"""MVP 主入口：板块龙头选股漏斗。

跑法：
    .venv/bin/python -m screener.run_mvp           # 全 A
    .venv/bin/python -m screener.run_mvp --quick   # 仅沪深 300+中证 500（快速验证）

输出：
- /tmp/screener_<date>.csv  完整候选池
- /tmp/screener_top50_<date>.csv  最终 Top 50
- 控制台打印 Top 50
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from screener.industry import load_industry_map
from screener.fetcher import fetch_snapshots_batch


# ────────────────────────────────────────────────────────────
# 过滤器
# ────────────────────────────────────────────────────────────

def l0_filter(snap: dict, name: str) -> bool:
    """L0 排除：ST / 微盘 / 亏损 / 流动性差。"""
    if not snap:
        return False
    # ST/退市
    if "ST" in name or "*" in name or "退" in name:
        return False
    # 流动性：20 日均成交额 > 5000 万
    if snap.get("avg_amount_20d", 0) < 5e7:
        return False
    # 价格合理：1 < close < 500
    if not (1 < snap["close_today"] < 500):
        return False
    # PE 合理：盈利 + PE 5-100（亏损排除）
    pe = snap.get("pe_ttm")
    if pe is None or pe <= 0 or pe > 100:
        return False
    return True


def price_position_ok(snap: dict) -> bool:
    """价格位置：距 60 日高点 -30% ~ -3% 且多头排列。"""
    dd = snap.get("drawdown_from_high", 0)
    if not (-30 <= dd <= -3):
        return False
    return snap.get("bullish_alignment", False)


def volume_ratio_ok(snap: dict) -> bool:
    """换手 + 量比过滤。"""
    vr = snap.get("volume_ratio", 0)
    turn = snap.get("turnover_today", 0)
    return vr > 1.2 and 1.5 <= turn <= 12.0


# ────────────────────────────────────────────────────────────
# 龙头评分
# ────────────────────────────────────────────────────────────

def score_industry_leaders(industry_snaps: list[dict]) -> list[dict]:
    """对一个行业内的所有股票打龙头分，返回 top 3。"""
    if len(industry_snaps) < 3:
        return industry_snaps[:3]  # 太小的行业全部入选

    df = pd.DataFrame(industry_snaps)

    # 用市值代理：amount_today 当日成交额（缺市值数据，用成交额近似）
    # 实战中应该用真实流通市值；这里简化
    df["mv_proxy"] = df["amount_today"]

    # 分位分数（越大越好的因子用 rank pct）
    df["rank_mv"] = df["mv_proxy"].rank(pct=True) * 100
    df["rank_ret30"] = df["ret_30d"].rank(pct=True) * 100
    df["rank_vr"] = df["volume_ratio"].rank(pct=True) * 100
    df["rank_amt"] = df["avg_amount_20d"].rank(pct=True) * 100

    # 综合分（MVP 简化版：没有北向/龙虎榜数据，权重重分配到其他维度）
    df["leader_score"] = (
        0.40 * df["rank_mv"]      # 市值龙头权重最高
        + 0.30 * df["rank_ret30"]  # 30日动量
        + 0.20 * df["rank_vr"]     # 量比（资金近期流入）
        + 0.10 * df["rank_amt"]    # 流动性
    )

    df = df.sort_values("leader_score", ascending=False).reset_index(drop=True)
    df["leader_rank"] = df.index + 1
    return df.head(3).to_dict("records")


# ────────────────────────────────────────────────────────────
# Pipeline
# ────────────────────────────────────────────────────────────

def run(end_date: str, quick: bool = False, max_workers: int = 8) -> pd.DataFrame:
    print(f"\n{'='*60}", flush=True)
    print(f"📊 板块龙头选股 MVP | 日期: {end_date} | quick={quick}", flush=True)
    print(f"{'='*60}\n", flush=True)
    t0 = time.time()

    # === Step 1: 加载行业 ===
    print("Step 1: 加载行业分类...", flush=True)
    industry_df = load_industry_map()
    print(f"  → {len(industry_df)} 只股票，{industry_df['industry'].nunique()} 个行业", flush=True)

    universe = industry_df.copy()
    if quick:
        # 快速版：随机抽 1/4
        universe = universe.sample(n=min(1500, len(universe)), random_state=42)
        print(f"  → quick 模式抽样 {len(universe)} 只", flush=True)

    # === Step 2: 并发拉快照 ===
    print(f"\nStep 2: 并发拉取 {len(universe)} 只票的 60 日 K 线（{max_workers} 并发）...", flush=True)
    snaps = fetch_snapshots_batch(
        universe["ticker"].tolist(), end_date, max_workers=max_workers
    )
    print(f"  → 成功 {len(snaps)} 只", flush=True)

    # === Step 3: 合并行业 + L0 过滤 ===
    print(f"\nStep 3: L0 过滤（ST/微盘/亏损/流动性）...", flush=True)
    rows = []
    name_map = dict(zip(universe["ticker"], universe["name"]))
    industry_map = dict(zip(universe["ticker"], universe["industry"]))
    for t, snap in snaps.items():
        if l0_filter(snap, name_map.get(t, "")):
            snap["name"] = name_map.get(t, "")
            snap["industry"] = industry_map.get(t, "")
            rows.append(snap)
    print(f"  → 通过 {len(rows)} 只（淘汰 {len(snaps)-len(rows)}）", flush=True)

    # === Step 4: 行业内取龙 1-3 ===
    print(f"\nStep 4: 行业内打分取龙 1-3...", flush=True)
    leader_pool = []
    by_industry: dict[str, list[dict]] = {}
    for r in rows:
        by_industry.setdefault(r["industry"], []).append(r)

    for ind, stocks in by_industry.items():
        leaders = score_industry_leaders(stocks)
        for lead in leaders:
            leader_pool.append(lead)
    print(f"  → 龙头池 {len(leader_pool)} 只（{len(by_industry)} 个行业）", flush=True)

    # === Step 5: 价格位置 + 量比过滤 ===
    print(f"\nStep 5: 价格位置 + 量比过滤...", flush=True)
    filtered = [
        r for r in leader_pool
        if price_position_ok(r) and volume_ratio_ok(r)
    ]
    print(f"  → 通过 {len(filtered)} 只", flush=True)

    # === Step 6: 最终排序输出 ===
    print(f"\nStep 6: 最终排序...", flush=True)
    df_final = pd.DataFrame(filtered).sort_values("leader_score", ascending=False)

    elapsed = time.time() - t0
    print(f"\n✅ 完成 | 总耗时 {elapsed:.1f}s", flush=True)
    return df_final


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-05-19", help="分析日期 YYYY-MM-DD")
    parser.add_argument("--quick", action="store_true", help="快速版（采样 1500 只）")
    parser.add_argument("--workers", type=int, default=8, help="并发数")
    parser.add_argument("--top", type=int, default=50, help="输出 Top N")
    args = parser.parse_args()

    df = run(args.date, quick=args.quick, max_workers=args.workers)

    # 保存
    out_full = Path(f"/tmp/screener_{args.date}.csv")
    out_top = Path(f"/tmp/screener_top{args.top}_{args.date}.csv")
    df.to_csv(out_full, index=False)
    top = df.head(args.top).copy()
    top.to_csv(out_top, index=False)

    print(f"\n{'='*60}")
    print(f"📋 完整候选池: {out_full} ({len(df)} 只)")
    print(f"📋 Top {args.top}: {out_top}")
    print(f"{'='*60}\n")

    # 打印 Top
    print(f"=== Top {args.top} ===")
    cols = [
        "ticker", "name", "industry", "leader_rank", "leader_score",
        "close_today", "ret_30d", "drawdown_from_high",
        "volume_ratio", "turnover_today", "pe_ttm", "pb_mrq",
    ]
    cols = [c for c in cols if c in top.columns]
    print(top[cols].to_string(index=False))


if __name__ == "__main__":
    main()
