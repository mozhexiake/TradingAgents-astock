"""L2: DeepSeek 候选股深度快评。

输入：screener MVP 输出的 CSV（含 ticker/name/industry/leader_rank 等）
输出：每只票一份 250-400 字的卖方分析师风格快评
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI

from tradingagents.dataflows.a_stock import (
    get_concept_blocks,
    get_fund_flow,
    get_fundamentals,
    get_news,
    get_profit_forecast,
    get_stock_data,
    _build_name_code_map,
)


PROMPT_TMPL = """你是 A 股资深卖方分析师，对以下入选**板块龙头**候选股做一份 250-400 字的快速诊断。

## 标的信息
- 代码: {code} {name}
- 行业: {industry}
- 板块内龙头排名: 龙{leader_rank}
- 当前价: ¥{close_today}
- 30 日涨幅: {ret_30d:+.1f}%
- 距 60 日高点回撤: {drawdown_from_high:+.1f}%
- 量比: {volume_ratio:.2f}
- 当日换手率: {turnover_today:.2f}%
- PE-TTM: {pe_ttm:.1f}
- PB: {pb_mrq:.2f}

## 数据快照（2026-05-19）

### 基本面
{fund}

### 资金流向
{fund_flow}

### 概念板块
{concept}

### 利润预测
{forecast}

### 近期新闻（精选）
{news}

---

## 输出要求（250-400 字，用粗体+换行，不用 markdown 标题）

**【一句话定位】** 这是什么公司、当前处于什么阶段

**【基本面】** 财务+估值+行业景气，2-3 句

**【技术面】** 趋势+量价+关键位，2 句

**【催化/风险】** 未来 1-3 月可能的利好或利空，2-3 个要点

**【入选逻辑】** 简述这只票为什么进入板块龙头池（你的视角下是否值得做？）

**【操作建议】** 明确给出"重点关注/中性观察/暂不参与"三选一 + 具体止损位/加仓位。不要"建议谨慎"这种废话。

要求：
1. 数据缺失时直说"数据缺失"，不要瞎编
2. 用 A 股语言，不要美股翻译腔
3. 如果有解禁/减持/政策利空必须提示
"""


def safe(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except Exception as e:
        return f"[采集失败: {type(e).__name__}]"


def analyze_one(client, row: dict, end_date: str) -> str:
    code = row["ticker"]
    name = row["name"]

    fund = safe(get_fundamentals, code, end_date)[:1500]
    concept = safe(get_concept_blocks, code, end_date)[:500]
    fund_flow = safe(get_fund_flow, code, end_date)[:600]
    forecast = safe(get_profit_forecast, code, end_date)[:500]
    news = safe(get_news, code, end_date)[:800]

    prompt = PROMPT_TMPL.format(
        code=code,
        name=name,
        industry=row.get("industry", ""),
        leader_rank=int(row.get("leader_rank", 0)) if pd.notna(row.get("leader_rank")) else 0,
        close_today=row["close_today"],
        ret_30d=row.get("ret_30d", 0),
        drawdown_from_high=row.get("drawdown_from_high", 0),
        volume_ratio=row.get("volume_ratio", 0),
        turnover_today=row.get("turnover_today", 0),
        pe_ttm=row.get("pe_ttm", 0),
        pb_mrq=row.get("pb_mrq", 0),
        fund=fund,
        fund_flow=fund_flow,
        concept=concept,
        forecast=forecast,
        news=news,
    )

    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=800,
    )
    return resp.choices[0].message.content


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="/tmp/screener_2026-05-19.csv", help="screener 输出 CSV")
    ap.add_argument("--date", default="2026-05-19")
    ap.add_argument("--top", type=int, default=50, help="最多分析多少只")
    ap.add_argument("--out", default="/tmp/screener_l2.json")
    args = ap.parse_args()

    df = pd.read_csv(args.csv, dtype={"ticker": str})
    df = df.head(args.top)
    print(f"=== 准备分析 {len(df)} 只候选 ===", flush=True)
    print("预热 name-code map...", flush=True)
    _build_name_code_map()

    client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
    )

    reports = {}
    for i, row in enumerate(df.to_dict("records"), 1):
        print(f"\n[{i}/{len(df)}] {row['ticker']} {row['name']} ({row.get('industry','')})", flush=True)
        try:
            report = analyze_one(client, row, args.date)
            reports[row["ticker"]] = {
                "name": row["name"],
                "industry": row.get("industry", ""),
                "leader_rank": int(row.get("leader_rank", 0)) if pd.notna(row.get("leader_rank")) else None,
                "close": row["close_today"],
                "ret_30d": row.get("ret_30d", 0),
                "leader_score": row.get("leader_score", 0),
                "report": report,
            }
            print(f"  ✓ {len(report)} chars", flush=True)
        except Exception as e:
            reports[row["ticker"]] = {"name": row["name"], "report": f"分析失败: {e}"}
            print(f"  ✗ {e}", flush=True)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 保存至 {args.out}", flush=True)

    # 打印所有报告
    print("\n" + "=" * 70)
    print("=== 所有快评 ===")
    print("=" * 70)
    for code, r in reports.items():
        print(f"\n\n━━━━━━━━ {code} {r['name']} | {r.get('industry','')} | 龙{r.get('leader_rank','-')} | 30日 {r.get('ret_30d',0):+.1f}% ━━━━━━━━\n")
        print(r["report"])


if __name__ == "__main__":
    main()
