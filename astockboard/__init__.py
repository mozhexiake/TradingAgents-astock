"""astockboard — A 股股票评估调研平台.

模块组织:
    data/       数据采集（多源 vendor + 反爬基建 + 路由）
    storage/    持久化（SQLite + Parquet）
    monitor/    持仓 + watchlist 监控（评级降级告警）
    notify/     Bark/Lark 推送
    reports/    HTML 看板 / 日报
"""

__version__ = "0.1.0"
