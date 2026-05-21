"""数据表 repository 封装（按用例提供高层 API）。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from astockboard.storage.db import get_db

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────
# stocks
# ────────────────────────────────────────────

def upsert_stock(ticker: str, name: str, industry: Optional[str] = None,
                 market: Optional[str] = None, list_date: Optional[str] = None,
                 is_st: int = 0, is_active: int = 1) -> None:
    db = get_db()
    db.execute(
        """INSERT INTO stocks(ticker, name, industry, market, list_date, is_st, is_active, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(ticker) DO UPDATE SET
             name=excluded.name, industry=excluded.industry,
             is_st=excluded.is_st, is_active=excluded.is_active,
             updated_at=CURRENT_TIMESTAMP""",
        (ticker, name, industry, market, list_date, is_st, is_active),
    )
    db.commit()


def get_stock(ticker: str) -> Optional[dict]:
    row = get_db().execute("SELECT * FROM stocks WHERE ticker=?", (ticker,)).fetchone()
    return dict(row) if row else None


def list_stocks(industry: Optional[str] = None) -> list[dict]:
    sql = "SELECT * FROM stocks WHERE is_active=1"
    args = []
    if industry:
        sql += " AND industry=?"
        args.append(industry)
    return [dict(r) for r in get_db().execute(sql, args).fetchall()]


def bulk_upsert_industry_map(df: pd.DataFrame) -> int:
    """批量写入行业映射。df 必须有 ticker/name/industry 列。"""
    db = get_db()
    rows = [
        (r["ticker"], r["name"], r.get("industry"))
        for _, r in df.iterrows()
    ]
    db.executemany(
        """INSERT INTO stocks(ticker, name, industry, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(ticker) DO UPDATE SET
             name=excluded.name, industry=excluded.industry,
             updated_at=CURRENT_TIMESTAMP""",
        rows,
    )
    db.commit()
    return len(rows)


# ────────────────────────────────────────────
# daily_kline / daily_snapshot
# ────────────────────────────────────────────

def upsert_kline_bulk(ticker: str, df: pd.DataFrame, source: str) -> int:
    """批量写 K 线。df 列：date, open, high, low, close, volume, amount, turn, pct_change, pe_ttm, pb_mrq, ps_ttm"""
    if df.empty:
        return 0
    db = get_db()
    cols = ["date", "open", "high", "low", "close", "volume", "amount",
            "turn", "pct_change", "pe_ttm", "pb_mrq", "ps_ttm"]
    rows = []
    for _, r in df.iterrows():
        rows.append((
            ticker, r["date"],
            *[r.get(c) for c in cols[1:]],
            source,
        ))
    db.executemany(
        """INSERT OR REPLACE INTO daily_kline
           (ticker, date, open, high, low, close, volume, amount, turn, pct_change,
            pe_ttm, pb_mrq, ps_ttm, source, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        rows,
    )
    db.commit()
    return len(rows)


def get_kline(ticker: str, start: str, end: str) -> pd.DataFrame:
    sql = """SELECT date, open, high, low, close, volume, amount, turn, pct_change,
                    pe_ttm, pb_mrq, ps_ttm
             FROM daily_kline
             WHERE ticker=? AND date BETWEEN ? AND ?
             ORDER BY date"""
    rows = get_db().execute(sql, (ticker, start, end)).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def upsert_snapshot(snap: dict) -> None:
    """写当日快照。snap 必须含 ticker/date。"""
    db = get_db()
    db.execute(
        """INSERT OR REPLACE INTO daily_snapshot
           (ticker, date, close, pct_change, turnover_rate, pe_ttm, pb_mrq, ps_ttm,
            market_cap, float_market_cap, ret_30d, ret_60d, volume_ratio,
            drawdown_60d, ma20, ma60, bullish_align, source, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        (
            snap["ticker"], snap["date"],
            snap.get("close"), snap.get("pct_change"), snap.get("turnover_rate"),
            snap.get("pe_ttm"), snap.get("pb_mrq"), snap.get("ps_ttm"),
            snap.get("market_cap"), snap.get("float_market_cap"),
            snap.get("ret_30d"), snap.get("ret_60d"), snap.get("volume_ratio"),
            snap.get("drawdown_60d"), snap.get("ma20"), snap.get("ma60"),
            int(bool(snap.get("bullish_align"))) if snap.get("bullish_align") is not None else None,
            snap.get("source"),
        ),
    )
    db.commit()


# ────────────────────────────────────────────
# analysis_result + rating_history
# ────────────────────────────────────────────

def save_analysis(
    ticker: str, date: str, level: str, model: str,
    rating: Optional[str], action: Optional[str],
    target_price: Optional[float], stop_loss: Optional[float],
    report: str, meta: Optional[dict] = None, cost_yuan: float = 0.0,
) -> None:
    db = get_db()
    db.execute(
        """INSERT OR REPLACE INTO analysis_result
           (ticker, date, level, model, rating, action, target_price, stop_loss,
            report, meta, cost_yuan, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        (ticker, date, level, model, rating, action, target_price, stop_loss,
         report, json.dumps(meta or {}, ensure_ascii=False), cost_yuan),
    )

    # 同步写评级历史
    prev_row = db.execute(
        """SELECT rating FROM rating_history
           WHERE ticker=? AND source=?
           ORDER BY date DESC LIMIT 1""",
        (ticker, level),
    ).fetchone()
    prev_rating = prev_row["rating"] if prev_row else None
    changed = 1 if (prev_rating and rating and prev_rating != rating) else 0

    db.execute(
        """INSERT OR REPLACE INTO rating_history
           (ticker, date, rating, prev_rating, changed, source, meta, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        (ticker, date, rating, prev_rating, changed, level,
         json.dumps(meta or {}, ensure_ascii=False)),
    )
    db.commit()


def get_latest_analysis(ticker: str, level: str = "L2") -> Optional[dict]:
    row = get_db().execute(
        """SELECT * FROM analysis_result
           WHERE ticker=? AND level=?
           ORDER BY date DESC LIMIT 1""",
        (ticker, level),
    ).fetchone()
    return dict(row) if row else None


def get_rating_changes(date: str, source: str = "L2") -> list[dict]:
    """当日评级变化的票（用于推送）。"""
    rows = get_db().execute(
        """SELECT * FROM rating_history
           WHERE date=? AND source=? AND changed=1""",
        (date, source),
    ).fetchall()
    return [dict(r) for r in rows]


# ────────────────────────────────────────────
# portfolio
# ────────────────────────────────────────────

def upsert_holding(ticker: str, name: str, qty: int, cost_price: float,
                   last_rating: Optional[str] = None) -> None:
    get_db().execute(
        """INSERT INTO portfolio(ticker, name, qty, cost_price, last_rating, updated_at)
           VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(ticker) DO UPDATE SET
             qty=excluded.qty, cost_price=excluded.cost_price,
             updated_at=CURRENT_TIMESTAMP""",
        (ticker, name, qty, cost_price, last_rating),
    )
    get_db().commit()


def update_holding_rating(ticker: str, rating: str) -> None:
    get_db().execute(
        """UPDATE portfolio
           SET last_rating=?, last_rating_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
           WHERE ticker=?""",
        (rating, ticker),
    )
    get_db().commit()


def list_holdings() -> list[dict]:
    rows = get_db().execute("SELECT * FROM portfolio").fetchall()
    return [dict(r) for r in rows]


# ────────────────────────────────────────────
# notification_log
# ────────────────────────────────────────────

def log_notification(channel: str, title: str, body: str, success: bool,
                     related_ticker: Optional[str] = None,
                     related_date: Optional[str] = None) -> None:
    get_db().execute(
        """INSERT INTO notification_log
           (channel, title, body, related_ticker, related_date, success)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (channel, title, body, related_ticker, related_date, int(success)),
    )
    get_db().commit()


def already_notified_today(ticker: str, date: str) -> bool:
    """判断今天是否已经为该 ticker 推过告警（防止重复）。"""
    row = get_db().execute(
        """SELECT id FROM notification_log
           WHERE related_ticker=? AND related_date=? AND success=1
           LIMIT 1""",
        (ticker, date),
    ).fetchone()
    return row is not None
