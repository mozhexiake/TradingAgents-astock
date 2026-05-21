"""持仓价格触线告警 —— 每小时跑：拉持仓当前价 → 对比最新 target/stop → 触发 Bark。

设计：
- 触线判定：
    止损: current <= stop_loss
    目标: current >= target_price
- 防重复推送：同一 ticker+date+kind 已推过就跳过（查 notification_log）
- 同一交易日内每 ticker 最多推 1 次止损 + 1 次目标
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from astockboard.data.router import get_router
from astockboard.notify.bark import send_bark
from astockboard.storage import init_db
from astockboard.storage.db import get_db
from astockboard.storage.repos import list_holdings, log_notification

logger = logging.getLogger(__name__)


def _latest_levels(ticker: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """最新 L2 分析的 target/stop/rating。"""
    row = get_db().execute(
        """SELECT target_price, stop_loss, rating FROM analysis_result
           WHERE ticker=? AND level='L2'
           ORDER BY date DESC LIMIT 1""",
        (ticker,),
    ).fetchone()
    if not row:
        return None, None, None
    return row["target_price"], row["stop_loss"], row["rating"]


def _already_pushed_today(ticker: str, kind: str) -> bool:
    """今天是否已经推过同 ticker+kind 告警。"""
    today = datetime.now().strftime("%Y-%m-%d")
    row = get_db().execute(
        """SELECT id FROM notification_log
           WHERE related_ticker=? AND related_date=? AND title LIKE ?
             AND success=1 LIMIT 1""",
        (ticker, today, f"%{kind}%"),
    ).fetchone()
    return row is not None


def run() -> dict:
    """跑一次价格告警检测。"""
    init_db()
    holdings = list_holdings()
    if not holdings:
        return {"checked": 0, "alerts": []}

    router = get_router()
    today = datetime.now().strftime("%Y-%m-%d")
    alerts = []

    for h in holdings:
        ticker = h["ticker"]
        name = h["name"]
        cost = h["cost_price"]
        qty = h["qty"]

        target, stop, rating = _latest_levels(ticker)
        if target is None and stop is None:
            continue  # 没有价格水平就不监控

        # 拉当前价（mootdx 实时性最好）
        try:
            fund = router.get_fundamentals(ticker, today)
            current = fund.get("close")
        except Exception as e:
            logger.warning("price fetch failed %s: %s", ticker, e)
            continue

        if not current:
            continue

        pnl_pct = (current - cost) / cost * 100 if cost > 0 else 0
        mv = qty * current

        # 触发止损
        if stop and current <= stop and not _already_pushed_today(ticker, "止损触线"):
            title = f"🚨 {name} 止损触线 ¥{stop:.2f}"
            body = (
                f"代码: {ticker} | 当前 ¥{current:.2f}（≤止损 ¥{stop:.2f}）\n"
                f"持仓盈亏 {pnl_pct:+.2f}% | 市值 ¥{mv:,.0f}\n"
                f"评级: {rating}\n"
                f"建议: 立即执行止损纪律"
            )
            ok = send_bark(title, body, level="critical", group="止损触线",
                          related_ticker=ticker, related_date=today)
            if ok:
                alerts.append({"ticker": ticker, "kind": "止损", "price": current, "stop": stop})

        # 触发目标
        if target and current >= target and not _already_pushed_today(ticker, "目标触达"):
            title = f"🎯 {name} 目标触达 ¥{target:.2f}"
            body = (
                f"代码: {ticker} | 当前 ¥{current:.2f}（≥目标 ¥{target:.2f}）\n"
                f"持仓盈亏 {pnl_pct:+.2f}% | 市值 ¥{mv:,.0f}\n"
                f"评级: {rating}\n"
                f"建议: 考虑分批止盈"
            )
            ok = send_bark(title, body, level="timeSensitive", group="目标触达",
                          related_ticker=ticker, related_date=today)
            if ok:
                alerts.append({"ticker": ticker, "kind": "目标", "price": current, "target": target})

    return {"checked": len(holdings), "alerts": alerts}


if __name__ == "__main__":
    res = run()
    print(f"\n检查 {res['checked']} 只持仓 | 触发告警 {len(res['alerts'])} 条")
    for a in res["alerts"]:
        print(f"  {a}")
