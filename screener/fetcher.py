"""并发批量从 baostock 拉每只票的 60 日 K 线快照。

⚠️ 关键：baostock 是单连接 RPC，**不支持线程并发**（多线程会互相阻塞死锁）。
本模块用 multiprocessing.Pool —— 每个 worker process 独立 login。
"""
from __future__ import annotations

import multiprocessing as mp
from datetime import datetime, timedelta
from typing import Iterable

import pandas as pd


def _to_bs_code(ticker: str) -> str:
    """6 位代码 → baostock 格式 sh.600xxx / sz.000xxx。"""
    if ticker.startswith(("6", "9")):
        return f"sh.{ticker}"
    return f"sz.{ticker}"


# === Worker process: each owns its own baostock connection ===

_worker_bs = None


def _worker_init():
    """每个 worker process 启动时 login 一次 baostock。"""
    global _worker_bs
    import baostock as bs
    bs.login()
    _worker_bs = bs


def _fetch_one(args: tuple[str, str]) -> tuple[str, dict | None]:
    """单只票快照。"""
    ticker, end_date = args
    global _worker_bs
    if _worker_bs is None:
        _worker_init()
    bs = _worker_bs

    try:
        code = _to_bs_code(ticker)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=100)

        rs = bs.query_history_k_data_plus(
            code,
            "date,close,volume,amount,turn,pctChg,peTTM,pbMRQ",
            start_date=start_dt.strftime("%Y-%m-%d"),
            end_date=end_date,
            frequency="d",
            adjustflag="2",
        )
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if len(rows) < 30:
            return ticker, None

        df = pd.DataFrame(rows, columns=rs.fields)
        for col in ("close", "volume", "amount", "turn", "pctChg", "peTTM", "pbMRQ"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"]).reset_index(drop=True)
        if len(df) < 30:
            return ticker, None

        last = df.iloc[-1]
        closes = df["close"].values
        volumes = df["volume"].values

        snap = {
            "ticker": ticker,
            "close_today": float(last["close"]),
            "turnover_today": float(last["turn"]) if pd.notna(last["turn"]) else 0.0,
            "pct_today": float(last["pctChg"]) if pd.notna(last["pctChg"]) else 0.0,
            "pe_ttm": float(last["peTTM"]) if pd.notna(last["peTTM"]) else None,
            "pb_mrq": float(last["pbMRQ"]) if pd.notna(last["pbMRQ"]) else None,
            "amount_today": float(last["amount"]) if pd.notna(last["amount"]) else 0.0,
        }
        if len(closes) >= 30:
            snap["ret_30d"] = float((closes[-1] - closes[-30]) / closes[-30] * 100)
        if len(closes) >= 60:
            snap["ret_60d"] = float((closes[-1] - closes[-60]) / closes[-60] * 100)

        avg5 = float(volumes[-5:].mean())
        avg20 = float(volumes[-20:].mean()) if len(volumes) >= 20 else float(volumes.mean())
        snap["volume_ratio"] = avg5 / avg20 if avg20 > 0 else 0.0

        high60 = float(closes[-60:].max()) if len(closes) >= 60 else float(closes.max())
        low60 = float(closes[-60:].min()) if len(closes) >= 60 else float(closes.min())
        snap["high_60d"] = high60
        snap["low_60d"] = low60
        snap["drawdown_from_high"] = (closes[-1] - high60) / high60 * 100

        if len(closes) >= 20:
            snap["ma20"] = float(closes[-20:].mean())
        if len(closes) >= 60:
            snap["ma60"] = float(closes[-60:].mean())

        snap["bullish_alignment"] = (
            snap.get("ma20") is not None
            and snap.get("ma60") is not None
            and snap["close_today"] > snap["ma20"] > snap["ma60"]
        )
        snap["avg_amount_20d"] = float(df["amount"].iloc[-20:].mean()) if len(df) >= 20 else 0.0
        return ticker, snap
    except Exception as e:
        return ticker, None


def fetch_snapshots_batch(
    tickers: Iterable[str],
    end_date: str,
    max_workers: int = 6,
    progress_every: int = 200,
) -> dict[str, dict]:
    """multiprocessing.Pool 并发拉。每个 process 独立 baostock。"""
    tickers = list(tickers)
    total = len(tickers)
    args_list = [(t, end_date) for t in tickers]

    results: dict[str, dict] = {}
    failed_count = 0

    with mp.Pool(processes=max_workers, initializer=_worker_init) as pool:
        for i, (t, snap) in enumerate(pool.imap_unordered(_fetch_one, args_list, chunksize=10), 1):
            if snap:
                results[t] = snap
            else:
                failed_count += 1
            if i % progress_every == 0 or i == total:
                print(
                    f"  [{i}/{total}] ok={len(results)} fail={failed_count}",
                    flush=True,
                )
    return results
