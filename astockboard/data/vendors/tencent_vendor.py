"""腾讯财经数据源：实时行情 + PE/PB/市值。GBK 编码。

接口：https://qt.gtimg.cn/q=sh600519
"""
from __future__ import annotations

import logging

from astockboard.data.base import VendorBase
from astockboard.data.http import http_get

logger = logging.getLogger(__name__)


def _market_prefix(ticker: str) -> str:
    return "sh" if ticker.startswith(("6", "9")) else "sz"


class TencentVendor(VendorBase):
    name = "tencent"

    def get_fundamentals(self, ticker: str, trade_date: str) -> dict:
        """腾讯单只票快照（实时）。"""
        symbol = f"{_market_prefix(ticker)}{ticker}"
        url = f"https://qt.gtimg.cn/q={symbol}"
        resp = http_get(url, vendor="tencent", encoding="gbk")
        text = resp.text

        # 格式: v_sh600519="1~贵州茅台~600519~1323.00~..."
        # 字段顺序详见腾讯文档，部分字段：
        # [1]name [2]code [3]close [4]prev_close [5]open [6]volume [10]bid1
        # [30]pe [33]turnover [38]float_market_cap [39]market_cap [44]pe_dynamic [45]pb
        m_start = text.find('"')
        m_end = text.rfind('"')
        if m_start < 0 or m_end <= m_start:
            return {}
        parts = text[m_start + 1:m_end].split("~")
        if len(parts) < 50:
            return {}

        def _safe_float(s: str) -> float | None:
            try:
                v = float(s)
                return v if v else None
            except (ValueError, TypeError):
                return None

        return {
            "name": parts[1],
            "close": _safe_float(parts[3]),
            "pct_change": _safe_float(parts[32]) if len(parts) > 32 else None,
            "turnover_rate": _safe_float(parts[38]) if len(parts) > 38 else None,
            "pe_ttm": _safe_float(parts[39]) if len(parts) > 39 else None,
            "float_market_cap": _safe_float(parts[44]) * 1e8 if len(parts) > 44 and _safe_float(parts[44]) else None,
            "market_cap": _safe_float(parts[45]) * 1e8 if len(parts) > 45 and _safe_float(parts[45]) else None,
            "pb_mrq": _safe_float(parts[46]) if len(parts) > 46 else None,
        }

    def healthcheck(self) -> bool:
        try:
            r = self.get_fundamentals("600519", "")
            return bool(r.get("close"))
        except Exception:
            return False
