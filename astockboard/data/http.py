"""反爬 HTTP 客户端：UA 池 + 令牌桶限流 + 指数退避重试。

用法：
    from astockboard.data.http import http_get

    resp = http_get("https://...", vendor="eastmoney", timeout=15)
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Optional

import requests

from astockboard.config import (
    HTTP_BACKOFF_BASE,
    HTTP_MAX_RETRIES,
    HTTP_TIMEOUT,
    RATE_LIMITS,
    USER_AGENTS,
)

logger = logging.getLogger(__name__)

# 全局 Session 池（每 vendor 一个 keep-alive）
_sessions: dict[str, requests.Session] = {}
_session_lock = threading.Lock()

# 限流状态：vendor -> (lock, last_request_time)
_rate_state: dict[str, tuple[threading.Lock, list[float]]] = {}
_rate_lock = threading.Lock()


def _get_session(vendor: str) -> requests.Session:
    with _session_lock:
        if vendor not in _sessions:
            s = requests.Session()
            _sessions[vendor] = s
        return _sessions[vendor]


def _rate_limit(vendor: str) -> None:
    """简单令牌桶：每秒最多 N 次请求。"""
    limit = RATE_LIMITS.get(vendor, 0)
    if limit <= 0:
        return

    with _rate_lock:
        if vendor not in _rate_state:
            _rate_state[vendor] = (threading.Lock(), [])
        lock, history = _rate_state[vendor]

    with lock:
        now = time.time()
        # 清理 1 秒前的记录
        history[:] = [t for t in history if now - t < 1.0]
        if len(history) >= limit:
            # 等到最早一次请求后 1 秒
            sleep_for = 1.0 - (now - history[0]) + 0.05
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.time()
            history[:] = [t for t in history if now - t < 1.0]
        history.append(now)


def http_get(
    url: str,
    vendor: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = HTTP_TIMEOUT,
    max_retries: int = HTTP_MAX_RETRIES,
    encoding: Optional[str] = None,
) -> requests.Response:
    """带反爬基建的 GET 请求。

    Args:
        vendor: 用于限流策略 + session 复用，e.g. "eastmoney" / "sina"
        encoding: 显式设置响应编码（GBK 站点必传）

    Raises:
        requests.RequestException: 重试用尽仍失败
    """
    sess = _get_session(vendor)
    headers = dict(headers or {})
    headers.setdefault("User-Agent", random.choice(USER_AGENTS))

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        _rate_limit(vendor)
        try:
            resp = sess.get(url, params=params, headers=headers, timeout=timeout)
            if encoding:
                resp.encoding = encoding
            if resp.status_code == 429:  # too many requests
                wait = HTTP_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1)
                logger.warning("[%s] 429 rate-limited, sleeping %.1fs", vendor, wait)
                time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600:  # server error
                wait = HTTP_BACKOFF_BASE * (2 ** attempt)
                logger.warning("[%s] HTTP %d, retry in %.1fs", vendor, resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            wait = HTTP_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 0.5)
            logger.warning("[%s] %s, retry in %.1fs", vendor, type(e).__name__, wait)
            time.sleep(wait)
        except requests.HTTPError as e:
            last_exc = e
            break  # 4xx 不重试

    raise requests.RequestException(
        f"[{vendor}] {url} failed after {max_retries} retries: {last_exc}"
    )


def http_json(url: str, vendor: str, **kwargs) -> Any:
    """GET + JSON 解析（自动处理 jsonp/var = ...）。"""
    resp = http_get(url, vendor, **kwargs)
    text = resp.text.strip()
    # jsonp: callback({...})
    if text.startswith(("var ", "/*")) or "(" in text[:20]:
        import re
        m = re.search(r"[=\(]\s*(\{.*\}|\[.*\])\s*\)?\s*;?\s*\*?/?$", text, re.DOTALL)
        if m:
            import json
            return json.loads(m.group(1))
    return resp.json()
