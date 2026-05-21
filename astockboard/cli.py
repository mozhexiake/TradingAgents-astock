"""CLI 入口：日常运维 + 测试。

用法:
    .venv/bin/python -m astockboard.cli init-db
    .venv/bin/python -m astockboard.cli sync-industry
    .venv/bin/python -m astockboard.cli sync-portfolio
    .venv/bin/python -m astockboard.cli test 600519
    .venv/bin/python -m astockboard.cli health
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("cli")


def cmd_init_db(args):
    from astockboard.storage import init_db
    init_db()
    print("✅ DB initialized")


def cmd_health(args):
    """检查每个 vendor 的连通性。"""
    from astockboard.data.vendors.baostock_vendor import BaostockVendor
    from astockboard.data.vendors.mootdx_vendor import MootdxVendor
    from astockboard.data.vendors.tencent_vendor import TencentVendor

    vendors = [BaostockVendor(), MootdxVendor(), TencentVendor()]
    print(f"{'Vendor':<12} | {'Status':<8}")
    print("-" * 25)
    for v in vendors:
        ok = v.healthcheck()
        print(f"{v.name:<12} | {'✅ OK' if ok else '❌ FAIL'}")


def cmd_sync_industry(args):
    """从 baostock 拉行业映射写入 SQLite。"""
    from astockboard.data.vendors.baostock_vendor import BaostockVendor
    from astockboard.storage import init_db
    from astockboard.storage.repos import bulk_upsert_industry_map

    init_db()
    df = BaostockVendor().get_industry_map()
    n = bulk_upsert_industry_map(df)
    print(f"✅ 写入 {n} 只股票（{df['industry'].nunique()} 个行业）")


def cmd_sync_portfolio(args):
    """从 config.HOLDINGS 写入 portfolio 表。"""
    from astockboard.config import HOLDINGS
    from astockboard.storage import init_db
    from astockboard.storage.repos import upsert_holding

    init_db()
    for code, name, qty, cost in HOLDINGS:
        upsert_holding(code, name, qty, cost)
    print(f"✅ 持仓 {len(HOLDINGS)} 只入库")


def cmd_test(args):
    """跑通一只票的数据采集（用 router）。"""
    from astockboard.data.router import get_router
    from astockboard.storage import init_db
    from astockboard.storage.repos import get_kline as repo_get_kline
    from astockboard.storage.repos import upsert_kline_bulk

    init_db()
    ticker = args.ticker
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    router = get_router()

    print(f"\n=== 测试: {ticker} | {start} ~ {today} ===\n")

    # 1. K 线
    print(f"[1/3] get_kline...")
    df = router.get_kline(ticker, start, today)
    print(f"  → {len(df)} 行 | 列: {list(df.columns)}")
    if not df.empty:
        print(df.tail(3).to_string())
        # 写入 DB
        n = upsert_kline_bulk(ticker, df, "router")
        print(f"  → 写入 DB {n} 行")

    # 2. 基本面
    print(f"\n[2/3] get_fundamentals...")
    fund = router.get_fundamentals(ticker, today)
    for k, v in fund.items():
        print(f"  {k}: {v}")

    # 3. 验证 DB 读取
    print(f"\n[3/3] 从 DB 读 K 线...")
    df2 = repo_get_kline(ticker, start, today)
    print(f"  → DB 读到 {len(df2)} 行")
    if not df2.empty:
        print(df2.tail(2).to_string())


def main():
    ap = argparse.ArgumentParser(prog="astockboard")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="初始化 SQLite").set_defaults(func=cmd_init_db)
    sub.add_parser("health", help="检查 vendor 连通性").set_defaults(func=cmd_health)
    sub.add_parser("sync-industry", help="同步行业映射").set_defaults(func=cmd_sync_industry)
    sub.add_parser("sync-portfolio", help="同步持仓到 DB").set_defaults(func=cmd_sync_portfolio)

    p_test = sub.add_parser("test", help="测试单只票数据采集")
    p_test.add_argument("ticker", help="6 位股票代码")
    p_test.set_defaults(func=cmd_test)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
