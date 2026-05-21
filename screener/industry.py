"""行业分类加载器：baostock 证监会行业分类（约 80 细分行业 / 5500 股票）。

数据通过 baostock 一次性下载并缓存到本地 CSV。
缓存有效期 7 天，过期自动重拉。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

CACHE_DIR = Path.home() / ".tradingagents" / "cache"
CACHE_FILE = CACHE_DIR / "industry_map.csv"
CACHE_TTL_DAYS = 7


def _is_cache_fresh() -> bool:
    if not CACHE_FILE.exists():
        return False
    mtime = datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
    return (datetime.now() - mtime) < timedelta(days=CACHE_TTL_DAYS)


def _fetch_from_baostock() -> pd.DataFrame:
    """从 baostock 拉取最新行业分类。"""
    import baostock as bs

    bs.login()
    try:
        rs = bs.query_stock_industry()
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        df = pd.DataFrame(rows, columns=rs.fields)
    finally:
        bs.logout()

    # 规范化：去掉 sh./sz. 前缀，只保留 6 位代码
    df["ticker"] = df["code"].str.split(".").str[1]
    df = df[df["industry"].str.strip() != ""].copy()  # 剔除无行业的
    return df[["ticker", "code_name", "industry"]].rename(
        columns={"code_name": "name"}
    )


def load_industry_map(refresh: bool = False) -> pd.DataFrame:
    """加载 ticker → industry 映射。

    Returns
    -------
    DataFrame with columns: ticker, name, industry
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not refresh and _is_cache_fresh():
        return pd.read_csv(CACHE_FILE, dtype={"ticker": str})

    df = _fetch_from_baostock()
    df.to_csv(CACHE_FILE, index=False)
    return df


def get_industries() -> list[str]:
    """所有行业名称列表。"""
    df = load_industry_map()
    return sorted(df["industry"].unique().tolist())


def get_stocks_by_industry(industry: str) -> list[tuple[str, str]]:
    """某行业的所有成分股 [(ticker, name), ...]。"""
    df = load_industry_map()
    sub = df[df["industry"] == industry]
    return list(zip(sub["ticker"], sub["name"]))


def get_industry_of(ticker: str) -> str | None:
    """单只票的行业归属。"""
    df = load_industry_map()
    row = df[df["ticker"] == ticker]
    if row.empty:
        return None
    return row.iloc[0]["industry"]


if __name__ == "__main__":
    df = load_industry_map(refresh=True)
    print(f"加载 {len(df)} 只股票，{df['industry'].nunique()} 个行业")
    print("Top 10 行业：")
    print(df["industry"].value_counts().head(10))
