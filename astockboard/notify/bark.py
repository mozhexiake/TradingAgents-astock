"""Bark 推送（个人私信通道）。"""
from __future__ import annotations

import logging
from typing import Optional

from astockboard.config import BARK_URL
from astockboard.data.http import http_get
from astockboard.storage.repos import log_notification

logger = logging.getLogger(__name__)


def send_bark(
    title: str,
    body: str,
    *,
    group: str = "astockboard",
    level: str = "active",       # active / timeSensitive / passive / critical
    sound: str = "default",
    url: Optional[str] = None,
    related_ticker: Optional[str] = None,
    related_date: Optional[str] = None,
) -> bool:
    """发送 Bark 推送。

    level:
        active        默认振动
        timeSensitive 时效性通知（专注模式仍提醒）
        critical      关键告警
    """
    params = {
        "group": group,
        "level": level,
        "sound": sound,
    }
    if url:
        params["url"] = url

    try:
        # Bark 用 URL path 传递 title + body
        import urllib.parse
        endpoint = f"{BARK_URL}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}"
        resp = http_get(endpoint, vendor="bark", params=params, timeout=10)
        data = resp.json()
        ok = data.get("code") == 200
        log_notification(
            channel="bark",
            title=title,
            body=body,
            success=ok,
            related_ticker=related_ticker,
            related_date=related_date,
        )
        if ok:
            logger.info("Bark sent: %s", title)
        else:
            logger.error("Bark failed: %s", data)
        return ok
    except Exception as e:
        logger.exception("Bark send error: %s", e)
        log_notification("bark", title, body, False, related_ticker, related_date)
        return False


def send_rating_alert(
    ticker: str,
    name: str,
    old_rating: str,
    new_rating: str,
    action: str,
    price: float,
    pnl_pct: float,
    report_snippet: str,
    date: str,
) -> bool:
    """评级变化告警 —— 这是 Day 2 的核心。"""
    # 评级排序，确定是降级还是升级
    order = {"Buy": 5, "Overweight": 4, "Hold": 3, "Underweight": 2, "Sell": 1,
             "重点关注": 5, "中性观察": 3, "暂不参与": 1, "止损": 0}
    o_old = order.get(old_rating, 3)
    o_new = order.get(new_rating, 3)
    is_downgrade = o_new < o_old

    arrow = "📉" if is_downgrade else "📈"
    title = f"{arrow} {name} 评级: {old_rating} → {new_rating}"
    body = (
        f"代码: {ticker} | 当前 ¥{price:.2f} | 浮盈亏 {pnl_pct:+.2f}%\n"
        f"建议: {action}\n\n"
        f"{report_snippet[:200]}"
    )
    return send_bark(
        title,
        body,
        level="timeSensitive" if is_downgrade else "active",
        group="评级变化",
        related_ticker=ticker,
        related_date=date,
    )


def send_daily_summary(date: str, holdings_summary: str, top_picks: str) -> bool:
    """每日运行总结。"""
    title = f"📊 {date} 日报"
    body = f"持仓:\n{holdings_summary}\n\n今日新增 Top:\n{top_picks}"
    return send_bark(title, body, group="日报", level="passive")
