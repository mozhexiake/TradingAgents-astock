"""Baostock 数据源：免费免注册，覆盖 K 线/财务/行业。

⚠️ baostock 不支持线程并发，仅可用 multiprocessing。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from astockboard.data.base import VendorBase

logger = logging.getLogger(__name__)

_login_lock = threading.Lock()
_logged_in = False


def _ensure_login():
    """主进程内的 lazy login。multiprocessing worker 需自己 login。"""
    global _logged_in
    with _login_lock:
        if not _logged_in:
            import baostock as bs
            rs = bs.login()
            if rs.error_code != "0":
                raise RuntimeError(f"baostock login failed: {rs.error_msg}")
            _logged_in = True


def _to_bs_code(ticker: str) -> str:
    """6 位代码 → baostock 格式 sh./sz."""
    if ticker.startswith(("6", "9")):
        return f"sh.{ticker}"
    return f"sz.{ticker}"


class BaostockVendor(VendorBase):
    name = "baostock"

    def get_industry_map(self) -> pd.DataFrame:
        _ensure_login()
        import baostock as bs

        rs = bs.query_stock_industry()
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        df = pd.DataFrame(rows, columns=rs.fields)
        df["ticker"] = df["code"].str.split(".").str[1]
        df = df[df["industry"].str.strip() != ""].copy()
        return df[["ticker", "code_name", "industry"]].rename(
            columns={"code_name": "name"}
        )

    def get_kline(
        self,
        ticker: str,
        start: str,
        end: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        _ensure_login()
        import baostock as bs

        adjustflag = {"qfq": "2", "hfq": "1", "none": "3"}[adjust]
        code = _to_bs_code(ticker)
        rs = bs.query_history_k_data_plus(
            code,
            "date,open,high,low,close,volume,amount,turn,pctChg,peTTM,pbMRQ,psTTM",
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag=adjustflag,
        )
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=rs.fields)
        # 类型转换
        num_cols = ["open", "high", "low", "close", "volume", "amount", "turn",
                    "pctChg", "peTTM", "pbMRQ", "psTTM"]
        for c in num_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close"]).reset_index(drop=True)
        return df.rename(columns={
            "pctChg": "pct_change",
            "peTTM": "pe_ttm",
            "pbMRQ": "pb_mrq",
            "psTTM": "ps_ttm",
        })

    def get_fundamentals(self, ticker: str, trade_date: str) -> dict:
        """从 baostock 当日 K 线提取基本面。"""
        df = self.get_kline(ticker, trade_date, trade_date)
        if df.empty:
            return {}
        row = df.iloc[-1]
        return {
            "close": float(row["close"]),
            "pe_ttm": float(row["pe_ttm"]) if pd.notna(row["pe_ttm"]) else None,
            "pb_mrq": float(row["pb_mrq"]) if pd.notna(row["pb_mrq"]) else None,
            "ps_ttm": float(row["ps_ttm"]) if pd.notna(row["ps_ttm"]) else None,
            "turnover_rate": float(row["turn"]) if pd.notna(row["turn"]) else None,
            "pct_change": float(row["pct_change"]) if pd.notna(row["pct_change"]) else None,
            "volume": float(row["volume"]) if pd.notna(row["volume"]) else None,
            "amount": float(row["amount"]) if pd.notna(row["amount"]) else None,
        }

    def healthcheck(self) -> bool:
        try:
            _ensure_login()
            return True
        except Exception:
            return False
