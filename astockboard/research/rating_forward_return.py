"""rating_forward_return — 验证 astockboard L2 评级是否有 alpha（P3-3）。

假设：
- 「重点关注」N 天 forward return 应显著为正
- 「止损」N 天 forward return 应显著为负

不验证则说明 astockboard L2 评级是噪音，仅作数据沉淀无 actionable alpha。

实施:
1. 拉每条 rating_history 记录的 (ticker, date, rating)
2. 用 mootdx 拉 ticker 在 date 起 N 天的 close 价
3. 算 forward_return_N = (close[t+N] - close[t]) / close[t]
4. 按 rating 分组算 mean / median / std
5. 评估是否显著（t-test）

用法:
  python3 -m astockboard.research.rating_forward_return --horizon 5
  python3 -m astockboard.research.rating_forward_return --horizons 1,3,5,10
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from astockboard.storage.db import get_db


def _fetch_close_prices(ticker: str, start_date: str,
                        horizon_days: int) -> list[tuple[str, float]]:
    """拉 ticker 在 start_date 起的 horizon_days+ buffer 天 close 价。"""
    try:
        from tradingagents.dataflows.a_stock import (
            get_stock_data as _get_stock,
        )
    except Exception:
        # 退化：直接从 mootdx 拉
        try:
            from mootdx.quotes import Quotes
            client = Quotes.factory(market="std")
            df = client.bars(symbol=ticker, frequency=9,
                              offset=horizon_days * 3 + 10)
            if df is None or df.empty:
                return []
            out = []
            for _, row in df.iterrows():
                date_str = str(row.get("datetime", ""))[:10]
                if date_str >= start_date:
                    out.append((date_str, float(row.get("close", 0))))
            return sorted(out)
        except Exception as e:
            print(f"  mootdx fail {ticker}: {e}")
            return []
    # 走 tradingagents 接口
    try:
        df = _get_stock(ticker, start_date,
                        (datetime.fromisoformat(start_date) +
                         timedelta(days=horizon_days * 2 + 10)).strftime("%Y-%m-%d"))
        out = []
        if hasattr(df, "iterrows"):
            for _, row in df.iterrows():
                date_str = str(row.get("date") or row.get("trade_date") or "")[:10]
                close = row.get("close") or row.get("收盘")
                if date_str and close is not None:
                    out.append((date_str, float(close)))
        return sorted(out)
    except Exception as e:
        print(f"  tradingagents fail {ticker}: {e}")
        return []


def compute_forward_return(ticker: str, rating_date: str,
                            horizon_days: int) -> float | None:
    """从 rating_date 后第 1 个交易日到第 horizon_days 个交易日的收益率。"""
    prices = _fetch_close_prices(ticker, rating_date, horizon_days)
    if len(prices) < horizon_days + 1:
        return None
    # 找 rating_date 后第一个交易日
    after = [p for p in prices if p[0] > rating_date]
    if len(after) < horizon_days:
        return None
    t0_price = after[0][1]
    if t0_price <= 0:
        return None
    t_n_price = after[min(horizon_days - 1, len(after) - 1)][1]
    return (t_n_price - t0_price) / t0_price


def analyze(horizons: list[int]) -> dict:
    db = get_db()
    cur = db.execute(
        """SELECT ticker, date, rating, source FROM rating_history
           WHERE source='L2' AND rating IS NOT NULL
           ORDER BY date"""
    )
    rating_records = [dict(zip(["ticker", "date", "rating", "source"], r)) for r in cur.fetchall()]
    print(f"📊 共 {len(rating_records)} 条评级记录")

    results: dict[int, dict[str, list[float]]] = {h: defaultdict(list) for h in horizons}

    for rec in rating_records:
        ticker = rec["ticker"]
        date = rec["date"]
        rating = rec["rating"]
        for h in horizons:
            fr = compute_forward_return(ticker, date, h)
            if fr is not None:
                results[h][rating].append(fr)

    # 聚合
    summary: dict = {"horizons": {}}
    for h in horizons:
        per_rating: dict = {}
        for rating, returns in results[h].items():
            if not returns:
                per_rating[rating] = {"n": 0}
                continue
            mean_r = statistics.mean(returns) * 100
            median_r = statistics.median(returns) * 100
            std_r = statistics.pstdev(returns) * 100 if len(returns) > 1 else 0
            # 简易 t-stat (mean / SE)
            se = std_r / (len(returns) ** 0.5) if std_r > 0 else 0
            t_stat = mean_r / se if se > 0 else 0
            per_rating[rating] = {
                "n": len(returns),
                "mean_return_pct": round(mean_r, 3),
                "median_return_pct": round(median_r, 3),
                "std_pct": round(std_r, 3),
                "t_stat": round(t_stat, 2),
                "significant_at_95": abs(t_stat) >= 2.0,
            }
        summary["horizons"][h] = per_rating
    return summary


def render_report(summary: dict) -> str:
    out = ["# Rating Forward Return Alpha Analysis", "",
           "## 假设检验",
           "- H0: 评级与 forward return 无关（评级是噪音）",
           "- H1: 「重点关注」mean > 0；「止损」mean < 0",
           ""]
    for h, per_rating in summary["horizons"].items():
        out.append(f"## Horizon = {h} 天 forward return")
        out.append("")
        out.append("| rating | n | mean % | median % | std % | t-stat | 显著 95%? |")
        out.append("|---|---:|---:|---:|---:|---:|:-:|")
        order = ["重点关注", "中性观察", "暂不参与", "止损"]
        for r in order:
            d = per_rating.get(r, {"n": 0})
            if d["n"] == 0:
                out.append(f"| {r} | 0 | - | - | - | - | - |")
            else:
                sig = "✅" if d.get("significant_at_95") else "—"
                out.append(
                    f"| {r} | {d['n']} | {d['mean_return_pct']:+.2f} | "
                    f"{d['median_return_pct']:+.2f} | {d['std_pct']:.2f} | "
                    f"{d['t_stat']:+.2f} | {sig} |"
                )
        out.append("")
        # 评判
        bull = per_rating.get("重点关注", {})
        bear = per_rating.get("止损", {})
        if bull.get("n", 0) >= 5 and bear.get("n", 0) >= 5:
            diff = bull.get("mean_return_pct", 0) - bear.get("mean_return_pct", 0)
            out.append(f"**长短组合 spread (重点关注 - 止损)**: {diff:+.2f}%")
            if diff > 0.5:
                out.append("→ 🟢 评级有方向性 alpha（重点关注跑赢止损）")
            elif diff < -0.5:
                out.append("→ 🔴 评级方向反了（止损反而跑赢重点关注）")
            else:
                out.append("→ ⚠️ 评级与价格无显著相关（视为噪音）")
        else:
            out.append("→ ⚠️ 样本不足，结论需累积更多数据")
        out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizons", default="1,3,5,10",
                        help="逗号分隔的 forward return 天数")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args()
    horizons = [int(x) for x in args.horizons.split(",")]
    summary = analyze(horizons)
    if args.markdown:
        print(render_report(summary))
    else:
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
