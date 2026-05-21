"""SQLite 连接管理 + 初始化。

线程安全：每个线程独立连接（通过 threading.local）。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from astockboard.config import DB_PATH
from astockboard.storage.schema import SCHEMAS

logger = logging.getLogger(__name__)

_tl = threading.local()
_init_lock = threading.Lock()
_initialized = False


def get_db() -> sqlite3.Connection:
    """返回当前线程的连接（lazy create）。"""
    conn = getattr(_tl, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _tl.conn = conn
    return conn


def init_db() -> None:
    """创建所有表（幂等）。"""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        conn = get_db()
        for stmt in SCHEMAS:
            conn.execute(stmt)
        conn.commit()
        _initialized = True
        logger.info("DB initialized: %s", DB_PATH)


def close_db() -> None:
    conn = getattr(_tl, "conn", None)
    if conn is not None:
        conn.close()
        _tl.conn = None
