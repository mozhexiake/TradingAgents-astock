"""mootdx 数据源：TCP 通达信协议，K 线最稳。"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Optional

import pandas as pd

from astockboard.data.base import VendorBase

logger = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            from mootdx.quotes import Quotes
            _client = Quotes.factory(market="std")
        return _client


def _market_for(ticker: str) -> int:
    """0=SZ, 1=SH。"""
    return 1 if ticker.startswith(("6", "9")) else 0


class MootdxVendor(VendorBase):
    name = "mootdx"

    def get_kline(
        self,
        ticker: str,
        start: str,
        end: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        client = _get_client()
        # mootdx bars 拉最近 N 个交易日，需要手动截断
        bars = client.bars(symbol=ticker, frequency=9, offset=800)  # 9=日线，约 3 年
        if bars is None or bars.empty:
            return pd.DataFrame()

        # mootdx 字段：datetime, open, high, low, close, vol, amount
        bars = bars.copy()
        bars["date"] = pd.to_datetime(bars["datetime"]).dt.strftime("%Y-%m-%d")
        bars = bars[(bars["date"] >= start) & (bars["date"] <= end)].reset_index(drop=True)
        if bars.empty:
            return pd.DataFrame()

        # 计算 pct_change
        bars["pct_change"] = bars["close"].pct_change() * 100

        return bars.rename(columns={"vol": "volume"})[
            ["date", "open", "high", "low", "close", "volume", "amount", "pct_change"]
        ]

    def get_fundamentals(self, ticker: str, trade_date: str) -> dict:
        df = self.get_kline(ticker, trade_date, trade_date)
        if df.empty:
            return {}
        row = df.iloc[-1]
        return {
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "amount": float(row["amount"]),
            "pct_change": float(row["pct_change"]) if pd.notna(row["pct_change"]) else None,
        }

    def healthcheck(self) -> bool:
        try:
            _get_client()
            return True
        except Exception:
            return False
