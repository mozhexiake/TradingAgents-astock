"""SQLite schema 定义。

设计原则:
- 所有表加 created_at / updated_at
- ticker + date 是大多表的复合主键
- LLM 报告/原始 JSON 用 TEXT 存（SQLite 没有 JSON type 但内部支持 JSON1）
"""

SCHEMAS = [
    # ── 股票元数据（行业 + 名字） ──────────────────────
    """
    CREATE TABLE IF NOT EXISTS stocks (
        ticker      TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        industry    TEXT,
        market      TEXT,           -- sh/sz/bj
        list_date   TEXT,           -- 上市日期
        is_st       INTEGER DEFAULT 0,
        is_active   INTEGER DEFAULT 1,
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_stocks_industry ON stocks(industry)",

    # ── 日 K 线（最核心数据，每只票每日 1 行） ──────────
    """
    CREATE TABLE IF NOT EXISTS daily_kline (
        ticker      TEXT NOT NULL,
        date        TEXT NOT NULL,
        open        REAL,
        high        REAL,
        low         REAL,
        close       REAL,
        volume      REAL,
        amount      REAL,
        turn        REAL,
        pct_change  REAL,
        pe_ttm      REAL,
        pb_mrq      REAL,
        ps_ttm      REAL,
        source      TEXT,           -- vendor 名
        fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(ticker, date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_kline_date ON daily_kline(date)",

    # ── 当日快照（基本面+市值，每只票每日 1 行） ──────
    """
    CREATE TABLE IF NOT EXISTS daily_snapshot (
        ticker          TEXT NOT NULL,
        date            TEXT NOT NULL,
        close           REAL,
        pct_change      REAL,
        turnover_rate   REAL,
        pe_ttm          REAL,
        pb_mrq          REAL,
        ps_ttm          REAL,
        market_cap      REAL,
        float_market_cap REAL,
        ret_30d         REAL,
        ret_60d         REAL,
        volume_ratio    REAL,
        drawdown_60d    REAL,
        ma20            REAL,
        ma60            REAL,
        bullish_align   INTEGER,    -- close > ma20 > ma60 ? 1 : 0
        source          TEXT,
        fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(ticker, date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_snapshot_date ON daily_snapshot(date)",

    # ── 选股结果（每次 screener 跑一行/票/日） ────────
    """
    CREATE TABLE IF NOT EXISTS screening_result (
        date            TEXT NOT NULL,
        ticker          TEXT NOT NULL,
        strategy        TEXT NOT NULL,    -- 'sector_leader' / 'value' / ...
        industry        TEXT,
        leader_rank     INTEGER,
        leader_score    REAL,
        passed_l0       INTEGER,          -- 通过 L0 排除
        passed_position INTEGER,          -- 通过价格位置
        passed_volume   INTEGER,          -- 通过量比
        final_in_pool   INTEGER,          -- 最终入选
        meta            TEXT,             -- JSON 杂项
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(date, ticker, strategy)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_screening_date ON screening_result(date)",
    "CREATE INDEX IF NOT EXISTS idx_screening_ticker ON screening_result(ticker)",

    # ── LLM 分析结果（DeepSeek L2 / TradingAgents L3） ──
    """
    CREATE TABLE IF NOT EXISTS analysis_result (
        ticker          TEXT NOT NULL,
        date            TEXT NOT NULL,
        level           TEXT NOT NULL,    -- 'L2' / 'L3'
        model           TEXT,             -- 'deepseek-chat' / 'tradingagents'
        rating          TEXT,             -- Buy/Overweight/Hold/Underweight/Sell
        action          TEXT,             -- 重点关注/中性观察/暂不参与/止损 / Buy/Hold/Sell
        target_price    REAL,
        stop_loss       REAL,
        report          TEXT,             -- LLM 输出全文
        meta            TEXT,             -- 额外字段 JSON
        cost_yuan       REAL,             -- 该次 LLM 调用花了多少钱
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(ticker, date, level)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_analysis_date ON analysis_result(date)",

    # ── 持仓表 ───────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS portfolio (
        ticker          TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        qty             INTEGER NOT NULL,
        cost_price      REAL NOT NULL,
        last_rating     TEXT,             -- 上次评级 (用于变化检测)
        last_rating_at  TEXT,
        notes           TEXT,
        updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    # ── 评级历史（评级变化检测的依据） ────────────────
    """
    CREATE TABLE IF NOT EXISTS rating_history (
        ticker      TEXT NOT NULL,
        date        TEXT NOT NULL,
        rating      TEXT NOT NULL,
        prev_rating TEXT,                 -- 上次评级
        changed     INTEGER,              -- 变化标记
        source      TEXT,                 -- L2 / L3
        meta        TEXT,                 -- JSON
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(ticker, date, source)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rating_changed ON rating_history(changed) WHERE changed=1",

    # ── 反思（决策结果反馈，用于学习） ────────────────
    """
    CREATE TABLE IF NOT EXISTS reflection (
        ticker          TEXT NOT NULL,
        decision_date   TEXT NOT NULL,
        rating          TEXT,
        raw_return_5d   REAL,
        alpha_return_5d REAL,
        raw_return_20d  REAL,
        alpha_return_20d REAL,
        reflection_text TEXT,
        status          TEXT DEFAULT 'pending',  -- pending / resolved
        resolved_at     TIMESTAMP,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(ticker, decision_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_reflection_status ON reflection(status)",

    # ── 推送日志（避免重复推送同一告警） ──────────────
    """
    CREATE TABLE IF NOT EXISTS notification_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        channel         TEXT NOT NULL,    -- bark / lark
        title           TEXT,
        body            TEXT,
        related_ticker  TEXT,
        related_date    TEXT,
        success         INTEGER,
        sent_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_notif_ticker ON notification_log(related_ticker)",
]
