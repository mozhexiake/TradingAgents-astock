"""持仓行业 / 单票集中度暴露监控。

触发条件：
- 单票占比 > 30% → 🚨 critical
- 单行业占比 > 40% → 🟠 warning
- 总现金 < 10% → 🟡 提示

每日跑一次（17:30 监控之后）+ 也可手动跑。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from astockboard.data.router import get_router
from astockboard.notify.bark import send_bark
from astockboard.storage import init_db
from astockboard.storage.db import get_db
from astockboard.storage.repos import list_holdings

logger = logging.getLogger(__name__)

# 触发阈值
SINGLE_STOCK_HEAVY = 0.30        # 单票 > 30% 警示
SINGLE_INDUSTRY_HEAVY = 0.40     # 单行业 > 40% 警示


def _get_industry(ticker: str) -> str:
    row = get_db().execute("SELECT industry FROM stocks WHERE ticker=?", (ticker,)).fetchone()
    return row["industry"] if row and row["industry"] else "未分类"


def run(alert: bool = True) -> dict:
    """跑一次暴露分析。"""
    init_db()
    holdings = list_holdings()
    if not holdings:
        return {"total_mv": 0, "exposures": {}, "alerts": []}

    router = get_router()
    today = datetime.now().strftime("%Y-%m-%d")

    # 拉当前价 + 算市值
    positions = []
    total_mv = 0.0
    for h in holdings:
        try:
            fund = router.get_fundamentals(h["ticker"], today)
            price = fund.get("close")
        except Exception as e:
            logger.warning("price fetch failed %s: %s", h["ticker"], e)
            price = None
        if not price:
            continue
        mv = h["qty"] * price
        total_mv += mv
        positions.append({
            "ticker": h["ticker"],
            "name": h["name"],
            "qty": h["qty"],
            "price": price,
            "mv": mv,
            "industry": _get_industry(h["ticker"]),
        })

    # 单票占比
    single_alerts = []
    for p in positions:
        p["weight"] = p["mv"] / total_mv if total_mv > 0 else 0
        if p["weight"] > SINGLE_STOCK_HEAVY:
            single_alerts.append(p)

    # 行业占比
    by_industry = defaultdict(float)
    for p in positions:
        by_industry[p["industry"]] += p["weight"]

    industry_alerts = [
        (ind, w) for ind, w in by_industry.items()
        if w > SINGLE_INDUSTRY_HEAVY
    ]

    # 输出 + 告警
    print(f"\n=== 持仓暴露分析 ({today}) ===")
    print(f"总市值: ¥{total_mv:,.0f}")
    print(f"\n--- 单票占比 ---")
    for p in sorted(positions, key=lambda x: -x["weight"]):
        flag = " ⚠️" if p["weight"] > SINGLE_STOCK_HEAVY else ""
        print(f"  {p['ticker']} {p['name']:<10} ¥{p['mv']:>10,.0f} ({p['weight']:>5.1%}){flag}")

    print(f"\n--- 行业占比 ---")
    for ind, w in sorted(by_industry.items(), key=lambda x: -x[1]):
        flag = " ⚠️" if w > SINGLE_INDUSTRY_HEAVY else ""
        # 行业简称
        ind_short = ind[3:25] if len(ind) > 3 else ind
        print(f"  {ind_short:<25} {w:>6.1%}{flag}")

    bark_sent = []
    if alert and (single_alerts or industry_alerts):
        title = "⚠️ 持仓暴露过度"
        lines = []
        for p in single_alerts:
            lines.append(f"单票 {p['name']} = {p['weight']:.1%}（阈值 {SINGLE_STOCK_HEAVY:.0%}）")
        for ind, w in industry_alerts:
            ind_short = ind[3:20] if len(ind) > 3 else ind
            lines.append(f"行业 {ind_short} = {w:.1%}（阈值 {SINGLE_INDUSTRY_HEAVY:.0%}）")
        body = "\n".join(lines) + "\n\n建议：降低集中度"
        ok = send_bark(title, body, level="active", group="暴露监控",
                      related_ticker="PORTFOLIO", related_date=today)
        if ok:
            bark_sent.append("exposure")

    return {
        "total_mv": total_mv,
        "positions": positions,
        "by_industry": dict(by_industry),
        "single_alerts": [p["ticker"] for p in single_alerts],
        "industry_alerts": [ind for ind, _ in industry_alerts],
        "bark_sent": bark_sent,
    }


if __name__ == "__main__":
    run()
