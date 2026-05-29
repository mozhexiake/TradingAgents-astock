"""rating_churn — 评级抖动平滑 3 件套（P1-6）。

诊断: 5 天同股 4-5 次翻牌（rock-ai-workspace/docs/optimization-2026-05-29.md §2.6）

3 件套:
  1. confidence 提取: LLM 报告里如有显式置信度数值，提为 [0,1] float
  2. 3 日多数决: 当日评级 = mode(t-2, t-1, t)，平局取 t-1 最近
  3. 反转触发 L3 建议: 评级方向反转（重点关注 ↔ 止损）→ 推 Bark 标记需要 L3 二审

L3 当前不自动跑（成本 ¥1/次 × 8 持仓 = ¥8/次），仅推送提醒由用户手动决策。
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from typing import Optional


# ─── 评级语义分层（驱动反转检测）───
RATING_BULL = {"重点关注"}        # 看好（积极）
RATING_NEUTRAL = {"中性观察"}     # 观望
RATING_BEAR = {"暂不参与", "止损"} # 看空 / 远离


def _level(rating: Optional[str]) -> Optional[int]:
    """评级语义层级。1=看好 / 0=中性 / -1=看空 / None=无效。"""
    if not rating:
        return None
    if rating in RATING_BULL:
        return 1
    if rating in RATING_NEUTRAL:
        return 0
    if rating in RATING_BEAR:
        return -1
    return None


# ─── confidence 提取 ─────────────────────────────────────

_CONFIDENCE_PATTERNS = [
    r"置信度[:：\s]*([01](?:\.\d+)?|\d+%)",
    r"信心[:：\s]*([01](?:\.\d+)?|\d+%)",
    r"confidence[:\s]*([01](?:\.\d+)?|\d+%)",
    r"概率[:：\s]*([01](?:\.\d+)?|\d+%)",
]


def extract_confidence(report: Optional[str]) -> Optional[float]:
    """从 LLM 报告找 confidence 数值（0.0-1.0）。

    支持: "置信度: 0.7" / "信心: 70%" / "confidence: 0.85"
    返回: 0.0-1.0 float，找不到 → None
    """
    if not report:
        return None
    for pat in _CONFIDENCE_PATTERNS:
        m = re.search(pat, report, re.IGNORECASE)
        if m:
            raw = m.group(1)
            if raw.endswith("%"):
                try:
                    return float(raw[:-1]) / 100.0
                except ValueError:
                    continue
            try:
                v = float(raw)
                if 0.0 <= v <= 1.0:
                    return v
                if 0 <= v <= 100:
                    return v / 100.0
            except ValueError:
                continue
    return None


# ─── 3 日多数决 ─────────────────────────────────────────

def smooth_rating(
    db: sqlite3.Connection,
    ticker: str,
    today: str,
    raw_rating: str,
    confidence: Optional[float] = None,
    window: int = 3,
    min_confidence_to_change: float = 0.6,
) -> tuple[str, str]:
    """3 日多数决平滑 + confidence 阈值兜底。

    逻辑:
      1. 拉过去 (window-1) 天的 L2 评级 + 今日 raw
      2. 如果 confidence < min_confidence_to_change → 维持上次评级
      3. 否则取多数决；平局时取最近一天

    Args:
        db: sqlite connection
        ticker: 股票代码
        today: YYYY-MM-DD
        raw_rating: 今日 LLM 原始评级
        confidence: 今日 confidence（None 时跳过 confidence 检查）
        window: 平滑窗口（默认 3 天）
        min_confidence_to_change: confidence 低于此阈值不接受新评级

    Returns:
        (smoothed_rating, smoothing_reason)
    """
    if not raw_rating:
        return raw_rating, "raw is empty"

    # 拉过去 window-1 天的最新评级（不含今日）
    cur = db.execute(
        """SELECT date, rating FROM rating_history
           WHERE ticker=? AND date<? AND source='L2'
           ORDER BY date DESC LIMIT ?""",
        (ticker, today, window - 1),
    )
    history = [r[1] for r in cur.fetchall() if r[1]]

    # 1) confidence 阈值
    if confidence is not None and confidence < min_confidence_to_change:
        if history:
            last = history[0]
            return last, f"low confidence {confidence:.2f}<{min_confidence_to_change}, kept '{last}'"
        # 无历史时只能用 raw
        return raw_rating, f"low confidence {confidence:.2f} but no history"

    # 2) 多数决
    all_ratings = [raw_rating] + history
    if len(all_ratings) < 2:
        return raw_rating, "no history, raw passes through"
    counter = Counter(all_ratings)
    top_rating, top_count = counter.most_common(1)[0]
    if top_count == 1:
        # 全部不同 → 平局，取今日 raw（保持响应性）
        return raw_rating, "tied (all distinct), raw kept"
    if top_count >= 2:
        # 有 >=2 票的同评级，取它
        if top_rating == raw_rating:
            return raw_rating, f"majority confirms ({top_count}/{len(all_ratings)})"
        else:
            return top_rating, (
                f"majority ({top_count}/{len(all_ratings)}) overrides "
                f"raw '{raw_rating}' → '{top_rating}'"
            )
    return raw_rating, "fallback to raw"


# ─── 反转检测（触发 L3 建议）───────────────────────────

def is_critical_reversal(old: Optional[str], new: Optional[str]) -> bool:
    """检测评级方向反转（看好 ↔ 看空）。

    True if:
      - 看好 → 看空 (e.g. 重点关注 → 止损)
      - 看空 → 看好 (e.g. 止损 → 重点关注)

    False if:
      - 平移到中性（视为正常 churn）
      - 同层级变化（重点关注 ↔ 中性观察）
    """
    lo = _level(old)
    ln = _level(new)
    if lo is None or ln is None:
        return False
    # 跨过中性的方向反转
    return (lo > 0 and ln < 0) or (lo < 0 and ln > 0)


# ─── 简易测试 ──────────────────────────────────────────

def _self_check():
    """smoke."""
    # confidence
    assert extract_confidence("...置信度: 0.7") == 0.7
    assert extract_confidence("...信心 75%") == 0.75
    assert extract_confidence("nothing") is None

    # reversal
    assert is_critical_reversal("重点关注", "止损") is True
    assert is_critical_reversal("止损", "重点关注") is True
    assert is_critical_reversal("重点关注", "中性观察") is False
    assert is_critical_reversal("中性观察", "止损") is False
    assert is_critical_reversal(None, "止损") is False

    print("✅ rating_churn self-check passed")


if __name__ == "__main__":
    _self_check()
