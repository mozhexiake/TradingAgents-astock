"""持仓监控：每日跑 8 只持仓 → DeepSeek L2 → 评级变化推送。"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from openai import OpenAI

from astockboard.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
from astockboard.data.router import get_router
from astockboard.notify.bark import send_rating_alert
from astockboard.storage import init_db
from astockboard.storage.repos import (
    get_latest_analysis,
    list_holdings,
    log_notification,
    save_analysis,
    update_holding_rating,
)

# 复用现有 a_stock.py 的数据采集（暂时；后续会迁到新数据层）
from tradingagents.dataflows.a_stock import (
    get_concept_blocks,
    get_fund_flow,
    get_fundamentals as old_get_fundamentals,
    get_news,
    get_profit_forecast,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────
# 评级提取（从 DeepSeek 报告文本里）
# ────────────────────────────────────────

RATING_KEYWORDS = [
    ("止损", "止损出局"),
    ("止损", "止损"),
    ("暂不参与", "暂不参与"),
    ("重点关注", "重点关注"),
    ("中性观察", "中性观察"),
    ("分批止盈", "分批止盈"),
    ("减仓", "减仓"),
    ("加仓", "加仓"),
]


def _first_price(text: str, patterns: list[str]) -> Optional[float]:
    """按 patterns 顺序匹配第一个合理价格（>0 且 < 10000）。"""
    for pat in patterns:
        for m in re.finditer(pat, text):
            try:
                p = float(m.group(1))
                if 0.5 < p < 10000:
                    return p
            except (ValueError, IndexError):
                continue
    return None


def extract_prices(report: str) -> tuple[Optional[float], Optional[float]]:
    """从 LLM 报告里提取 (target_price, stop_loss)。

    覆盖模式：
        止损: "止损位 29" / "止损设在 29 元" / "跌破 29 元止损" / "下方支撑 29 元"
        目标: "目标价 33.5" / "上方压力位 33.5" / "上涨至 33.5" / "反弹至 33.5"
    """
    if not report:
        return None, None

    # 优先匹配 【操作建议】段（精度最高）
    sec = re.search(r"【操作建议】(.+?)(?=【|$)", report, re.DOTALL)
    op_text = sec.group(1) if sec else ""

    # 止损相关
    stop_patterns = [
        r"止损位?[设于在为：:]*\s*[¥￥]?\s*(\d+\.?\d*)",
        r"止损线?[设于在为：:]*\s*[¥￥]?\s*(\d+\.?\d*)",
        r"硬性?止损位?[设于在为：:]*\s*[¥￥]?\s*(\d+\.?\d*)",
        r"跌破\s*[¥￥]?\s*(\d+\.?\d*)\s*元?[（(]?[^）)]{0,30}[）)]?\s*(?:则|止损|清仓|离场|出局|止损线)",
        r"下方支撑位?[在为：:]*\s*[¥￥]?\s*(\d+\.?\d*)",
    ]
    # 目标相关
    target_patterns = [
        r"目标价?[为是设于：:]*\s*[¥￥]?\s*(\d+\.?\d*)",
        r"上方压力位?[在为：:]*\s*[¥￥]?\s*(\d+\.?\d*)",
        r"上涨空间[^至]*至\s*[¥￥]?\s*(\d+\.?\d*)",
        r"反弹至\s*[¥￥]?\s*(\d+\.?\d*)",
        r"突破\s*[¥￥]?\s*(\d+\.?\d*)\s*元?[（(]?[^）)]{0,30}[）)]?\s*(?:加仓|跟进|确认|前高)",
        r"站稳\s*[¥￥]?\s*(\d+\.?\d*)",
    ]

    # 优先操作建议段，否则全文
    stop = _first_price(op_text, stop_patterns) or _first_price(report, stop_patterns)
    target = _first_price(op_text, target_patterns) or _first_price(report, target_patterns)

    # 防止"目标 = 止损"（regex 误吞）
    if stop is not None and target is not None and stop == target:
        target = None

    return target, stop


def extract_rating(report: str) -> tuple[Optional[str], Optional[str]]:
    """从 LLM 报告里提取 (rating, action) 。

    rating: 重点关注 / 中性观察 / 暂不参与 / 止损（4 档简化）
    action: 完整动作描述
    """
    if not report:
        return None, None

    # 优先匹配 【操作建议】部分
    sec = re.search(r"【操作建议】\s*\*?\*?(.+?)(?=\n\n|$)", report, re.DOTALL)
    text = sec.group(1) if sec else report

    # 按优先级匹配关键字
    if re.search(r"\*\*(止损出局|止损)\*\*", text) or "止损出局" in text:
        return "止损", "止损出局"
    if re.search(r"\*\*暂不参与\*\*", text) or "暂不参与" in text:
        return "暂不参与", "暂不参与"
    if re.search(r"\*\*重点关注\*\*", text) or "重点关注" in text:
        return "重点关注", "重点关注"
    if re.search(r"\*\*中性观察\*\*", text) or "中性观察" in text:
        return "中性观察", "中性观察"

    # Fallback
    if "分批止盈" in text or "减仓" in text:
        return "中性观察", "分批止盈/减仓"
    if "加仓" in text or "买入" in text:
        return "重点关注", "建议加仓/买入"

    return None, None


# ────────────────────────────────────────
# 数据采集 + LLM 分析
# ────────────────────────────────────────

PROMPT_TMPL = """你是 A 股资深卖方分析师，对**用户的持仓股**做一份 250-400 字的快速诊断。

## 持仓信息
- 代码: {ticker} {name}
- 行业: {industry}
- 当前价: ¥{price:.2f}
- 成本价: ¥{cost:.2f}
- 持仓盈亏: {pnl_pct:+.2f}%
- 持仓市值: ¥{market_value:,.0f}

## 数据快照（{date}）

### 基本面
{fund}

### 资金流向
{fund_flow}

### 概念板块
{concept}

### 利润预测
{forecast}

### 近期新闻
{news}

---

## 输出要求（250-400 字，用粗体+换行）

**【一句话定位】** 公司基本情况 + 当前阶段

**【基本面】** 财务+估值+行业景气

**【技术面】** 趋势+量价+关键位

**【催化/风险】** 1-3 月利好利空

**【操作建议】** 必须明确给出："**重点关注**"/"**中性观察**"/"**暂不参与**"/"**止损出局**" 四选一 + 具体止损位/加仓位。结合用户当前盈亏给建议（亏损深的更要明确止损）。
"""


def fetch_holding_data(ticker: str, end_date: str) -> dict:
    """从旧 a_stock 模块拉数据（之后会迁移）。"""
    def safe(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:
            return f"[采集失败: {type(e).__name__}]"

    return {
        "fund": safe(old_get_fundamentals, ticker, end_date)[:1500],
        "fund_flow": safe(get_fund_flow, ticker, end_date)[:600],
        "concept": safe(get_concept_blocks, ticker, end_date)[:500],
        "forecast": safe(get_profit_forecast, ticker, end_date)[:500],
        "news": safe(get_news, ticker, end_date)[:800],
    }


def analyze_one(client: OpenAI, holding: dict, current_price: float, end_date: str,
                use_cache: bool = True) -> dict:
    """对单只持仓票做一次 L2 分析。返回 {rating, action, report, ...}

    use_cache=True 时：当日同 ticker+level 已有结果直接返回（省 ¥）
    """
    ticker = holding["ticker"]
    name = holding["name"]
    cost = holding["cost_price"]
    qty = holding["qty"]
    pnl_pct = (current_price - cost) / cost * 100 if cost > 0 else 0
    market_value = qty * current_price

    # 缓存：当日已有 L2 结果直接返回
    if use_cache:
        from astockboard.storage.db import get_db
        row = get_db().execute(
            """SELECT rating, action, report, cost_yuan FROM analysis_result
               WHERE ticker=? AND date=? AND level='L2'""",
            (ticker, end_date),
        ).fetchone()
        if row and row["report"]:
            logger.info("cache hit for %s on %s (saved ¥%.4f)", ticker, end_date, row["cost_yuan"] or 0)
            return {
                "ticker": ticker, "name": name,
                "price": current_price, "pnl_pct": pnl_pct,
                "report": row["report"], "rating": row["rating"],
                "action": row["action"], "cost_yuan": 0.0,  # 0 = cache hit
                "cache_hit": True,
            }

    # 拉数据
    data = fetch_holding_data(ticker, end_date)

    prompt = PROMPT_TMPL.format(
        ticker=ticker, name=name, industry="", date=end_date,
        price=current_price, cost=cost, pnl_pct=pnl_pct,
        market_value=market_value,
        fund=data["fund"], fund_flow=data["fund_flow"],
        concept=data["concept"], forecast=data["forecast"],
        news=data["news"],
    )

    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=800,
    )
    report = resp.choices[0].message.content
    rating, action = extract_rating(report)
    target_price, stop_loss = extract_prices(report)

    # 估算成本（粗略）：input + output token，按 deepseek-chat 定价
    usage = resp.usage
    cost_yuan = (
        (usage.prompt_tokens / 1000) * 0.001 +
        (usage.completion_tokens / 1000) * 0.002
    )

    return {
        "ticker": ticker,
        "name": name,
        "price": current_price,
        "pnl_pct": pnl_pct,
        "report": report,
        "rating": rating,
        "action": action,
        "target_price": target_price,
        "stop_loss": stop_loss,
        "cost_yuan": cost_yuan,
    }


# ────────────────────────────────────────
# 主流程
# ────────────────────────────────────────

def run(end_date: Optional[str] = None, alert_on_change: bool = True) -> dict:
    """跑一次持仓监控。

    Returns:
        {"analyzed": N, "changes": [...], "total_cost": yuan}
    """
    init_db()
    end_date = end_date or datetime.now().strftime("%Y-%m-%d")
    holdings = list_holdings()
    if not holdings:
        print("⚠️ portfolio 表为空，先跑 sync-portfolio")
        return {"analyzed": 0, "changes": []}

    router = get_router()
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    changes: list[dict] = []
    total_cost = 0.0

    for i, h in enumerate(holdings, 1):
        ticker = h["ticker"]
        name = h["name"]
        print(f"\n[{i}/{len(holdings)}] {ticker} {name}", flush=True)

        # 拉当前价
        try:
            fund = router.get_fundamentals(ticker, end_date)
            current_price = fund.get("close")
            if not current_price:
                # fallback 近期 K 线
                start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
                kline = router.get_kline(ticker, start, end_date)
                if not kline.empty:
                    current_price = float(kline["close"].iloc[-1])
        except Exception as e:
            print(f"  ✗ 拉价格失败: {e}")
            continue
        if not current_price:
            print(f"  ✗ 无价格数据，跳过")
            continue
        print(f"  当前价 ¥{current_price:.2f}")

        # 分析
        try:
            result = analyze_one(client, h, current_price, end_date)
        except Exception as e:
            print(f"  ✗ LLM 分析失败: {e}")
            continue
        total_cost += result["cost_yuan"]
        print(f"  评级: {result['rating']} | 动作: {result['action']} | 成本 ¥{result['cost_yuan']:.4f}")

        # 写入 DB
        save_analysis(
            ticker=ticker, date=end_date, level="L2", model="deepseek-chat",
            rating=result["rating"], action=result["action"],
            target_price=result.get("target_price"),
            stop_loss=result.get("stop_loss"),
            report=result["report"],
            meta={"price": current_price, "pnl_pct": result["pnl_pct"]},
            cost_yuan=result["cost_yuan"],
        )

        # 评级变化检测
        old_rating = h.get("last_rating")
        new_rating = result["rating"]
        if alert_on_change and new_rating and old_rating and new_rating != old_rating:
            print(f"  🚨 评级变化: {old_rating} → {new_rating}")
            send_rating_alert(
                ticker=ticker, name=name,
                old_rating=old_rating, new_rating=new_rating,
                action=result["action"] or "-",
                price=current_price, pnl_pct=result["pnl_pct"],
                report_snippet=result["report"], date=end_date,
            )
            changes.append({
                "ticker": ticker, "name": name,
                "old": old_rating, "new": new_rating,
            })

        # 更新持仓表的最新评级
        if new_rating:
            update_holding_rating(ticker, new_rating)

    print(f"\n✅ 完成 {len(holdings)} 只 | 评级变化 {len(changes)} 只 | 总成本 ¥{total_cost:.3f}")
    return {"analyzed": len(holdings), "changes": changes, "total_cost": total_cost}


if __name__ == "__main__":
    run()
