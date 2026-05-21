"""Vendor 抽象基类：所有数据源实现统一接口。

不强求 vendor 实现所有方法，没实现的方法抛 NotImplementedError，
由 router 决定 fallback 策略。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd


@dataclass
class StockSnapshot:
    """单只股票的快照数据（行业 + 当日 + 近期统计）。"""
    ticker: str
    name: str
    industry: str | None = None
    close: float | None = None
    pct_change: float | None = None
    turnover_rate: float | None = None
    volume: float | None = None
    amount: float | None = None
    pe_ttm: float | None = None
    pb_mrq: float | None = None
    ps_ttm: float | None = None
    market_cap: float | None = None       # 总市值（元）
    float_market_cap: float | None = None  # 流通市值（元）
    # 衍生指标（router 算）
    ret_30d: float | None = None
    ret_60d: float | None = None
    volume_ratio: float | None = None
    drawdown_60d: float | None = None
    high_60d: float | None = None
    low_60d: float | None = None
    ma20: float | None = None
    ma60: float | None = None


class VendorBase(ABC):
    """数据源抽象基类。"""

    name: str = "base"

    def get_stock_list(self) -> pd.DataFrame:
        """返回全市场股票列表。
        columns: ticker, name, industry (可选)
        """
        raise NotImplementedError(f"{self.name} 不支持 get_stock_list")

    def get_kline(
        self,
        ticker: str,
        start: str,
        end: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """日 K 线。
        columns: date, open, high, low, close, volume, amount, turn, pct_change
        """
        raise NotImplementedError(f"{self.name} 不支持 get_kline")

    def get_fundamentals(self, ticker: str, trade_date: str) -> dict:
        """当日基本面快照。
        keys: close, pe_ttm, pb_mrq, ps_ttm, market_cap, float_market_cap,
              turnover_rate, change
        """
        raise NotImplementedError(f"{self.name} 不支持 get_fundamentals")

    def get_industry_map(self) -> pd.DataFrame:
        """全市场行业映射。
        columns: ticker, name, industry
        """
        raise NotImplementedError(f"{self.name} 不支持 get_industry_map")

    def get_news(self, ticker: str, end_date: str, limit: int = 20) -> list[dict]:
        """近期新闻。
        each item: {date, title, source, url}
        """
        raise NotImplementedError(f"{self.name} 不支持 get_news")

    def get_fund_flow(self, ticker: str, end_date: str) -> dict:
        """资金流向（主力/超大/大/中/小单 + 北向）。"""
        raise NotImplementedError(f"{self.name} 不支持 get_fund_flow")

    def get_dragon_tiger(self, ticker: str, end_date: str, days: int = 90) -> list[dict]:
        """龙虎榜。"""
        raise NotImplementedError(f"{self.name} 不支持 get_dragon_tiger")

    def get_lockup_schedule(self, ticker: str, end_date: str) -> list[dict]:
        """解禁日历。"""
        raise NotImplementedError(f"{self.name} 不支持 get_lockup_schedule")

    def healthcheck(self) -> bool:
        """vendor 是否可用。"""
        try:
            self.get_industry_map().head(1)
            return True
        except Exception:
            return False
