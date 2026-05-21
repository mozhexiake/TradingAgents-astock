"""全局配置。"""
from __future__ import annotations

import os
from pathlib import Path

# === 路径 ===
HOME = Path.home() / ".tradingagents"
DB_PATH = HOME / "db" / "astockboard.db"
CACHE_DIR = HOME / "cache"
LOG_DIR = HOME / "logs"

for p in (DB_PATH.parent, CACHE_DIR, LOG_DIR):
    p.mkdir(parents=True, exist_ok=True)

# === Bark 推送 ===
BARK_KEY = os.getenv("BARK_KEY", "eefbTRBG57AJzBFw9yJHkj")
BARK_URL = f"https://api.day.app/{BARK_KEY}"

# === LLM ===
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-742aad5667e442a9a3791c278806fad4")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# === 数据源限流（每秒最多 N 次请求） ===
RATE_LIMITS = {
    "baostock": 0,        # baostock 自己有连接限制，不额外限
    "mootdx": 0,          # TCP 协议，不限速
    "eastmoney": 5,       # 东财 push2/datacenter
    "sina": 5,
    "tencent": 10,
    "tonghuashun": 1,     # 反爬最严
    "cls": 5,
    "baidu": 5,
}

# === UA 池 ===
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# === HTTP 重试 ===
HTTP_TIMEOUT = 10
HTTP_MAX_RETRIES = 3
HTTP_BACKOFF_BASE = 1.0  # 指数退避 base，1, 2, 4 秒

# === 持仓配置（先 hard-code，后续迁数据库） ===
HOLDINGS = [
    # (code, name, qty, cost_price)
    ("603596", "伯特利", 1480, 36.15),     # cost = 当前 30.39 ÷ (1 - 0.1592)
    ("688126", "沪硅产业", 1000, 24.59),
    ("002185", "华天科技", 3000, 14.74),
    ("002312", "川发龙蟒", 8000, 12.83),
    ("300413", "芒果超媒", 1500, 25.03),
    ("300573", "兴齐眼药", 4400, 73.71),
    ("301555", "惠柏新材", 2500, 34.75),
    ("301628", "强达电路", 800, 80.65),
]
