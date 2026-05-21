"""多源 router：按优先级链调 vendor，失败自动 fallback。"""
from __future__ import annotations

import logging
from typing import Any, Callable

from astockboard.data.base import VendorBase

logger = logging.getLogger(__name__)


class VendorRouter:
    """按方法路由到 vendor 链，第一个成功的返回。"""

    def __init__(self, vendors: list[VendorBase]):
        self.vendors = vendors

    def call(self, method: str, *args, **kwargs) -> Any:
        """按 vendor 链调用 method，第一个成功返回。

        Raises:
            RuntimeError: 所有 vendor 都失败
        """
        errors = []
        for v in self.vendors:
            fn = getattr(v, method, None)
            if fn is None:
                continue
            try:
                result = fn(*args, **kwargs)
                if result is not None:
                    return result
            except NotImplementedError:
                continue
            except Exception as e:
                logger.warning("[%s.%s] failed: %s", v.name, method, e)
                errors.append((v.name, str(e)[:200]))
                continue
        raise RuntimeError(
            f"All vendors failed for {method}: " + "; ".join(f"{n}:{e}" for n, e in errors)
        )

    def get_stock_list(self, **kw):
        return self.call("get_stock_list", **kw)

    def get_kline(self, *a, **kw):
        return self.call("get_kline", *a, **kw)

    def get_fundamentals(self, *a, **kw):
        return self.call("get_fundamentals", *a, **kw)

    def get_industry_map(self, **kw):
        return self.call("get_industry_map", **kw)

    def get_news(self, *a, **kw):
        return self.call("get_news", *a, **kw)

    def get_fund_flow(self, *a, **kw):
        return self.call("get_fund_flow", *a, **kw)


# 全局默认 router（懒加载）
_default_router: VendorRouter | None = None


def get_router() -> VendorRouter:
    """返回默认 router（lazy build）。"""
    global _default_router
    if _default_router is None:
        from astockboard.data.vendors.baostock_vendor import BaostockVendor
        from astockboard.data.vendors.mootdx_vendor import MootdxVendor
        from astockboard.data.vendors.tencent_vendor import TencentVendor

        # 顺序 = 优先级
        _default_router = VendorRouter([
            BaostockVendor(),
            MootdxVendor(),
            TencentVendor(),
        ])
    return _default_router
