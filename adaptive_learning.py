"""Risk-bounded adaptive sizing from completed paper-trade outcomes.

This module reads PAPER SELL rows only. It never changes entry signals, never
reads live-mode outcomes, and cannot enable or influence live execution.
"""
import os
import threading
import time

import database


def _minimum_sample():
    try:
        return max(10, min(500, int(os.getenv("TROJAN_ADAPTIVE_MIN_TRADES", "30"))))
    except (TypeError, ValueError, OverflowError):
        return 30


MIN_CLOSED_TRADES = _minimum_sample()
CACHE_TTL = 15
MIN_MULTIPLIER = 0.80
MAX_MULTIPLIER = 1.15
MAX_LOOKBACK = 500

_lock = threading.Lock()
_cache = {"updated_at": 0.0, "report": None}


def _finite_pnl(row):
    try:
        value = float(row.get("profit_loss") or 0)
    except (TypeError, ValueError, OverflowError, AttributeError):
        return None
    # bool is numeric in Python but is never a valid trade result.
    if isinstance(row.get("profit_loss"), bool):
        return None
    if not __import__("math").isfinite(value):
        return None
    return value


def _profile(strategy, rows):
    # get_recent_trades returns newest first. Reverse to calculate streaks and
    # equity path in chronological order, using completed SELL records only.
    closed = [
        row for row in rows
        if isinstance(row, dict)
        and row.get("action") == "SELL"
        and row.get("strategy", "main") == strategy
    ]
    values = []
    invalid_rows = 0
    for row in reversed(closed):
        value = _finite_pnl(row)
        if value is None:
            invalid_rows += 1
            continue
        values.append(value)

    count = len(values)
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    win_rate = (len(wins) / count * 100) if count else 0.0
    profit_factor = (
        gross_profit / gross_loss if gross_loss > 0
        else (3.0 if gross_profit > 0 else 0.0)
    )
    total_pnl = sum(values)
    average_pnl = total_pnl / count if count else 0.0
    average_win = gross_profit / len(wins) if wins else 0.0
    average_loss = gross_loss / len(losses) if losses else 0.0

    current_losing_streak = 0
    max_losing_streak = 0
    for value in values:
        if value < 0:
            current_losing_streak += 1
            max_losing_streak = max(max_losing_streak, current_losing_streak)
        elif value > 0:
            current_losing_streak = 0

    # Peak-to-trough drawdown of cumulative realized P/L. This is reported in
    # dollars, not mislabeled as account-equity percentage drawdown.
    equity = 0.0
    peak = 0.0
    max_drawdown_usd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown_usd = max(max_drawdown_usd, peak - equity)

    minimum_sample = _minimum_sample()
    if count < minimum_sample:
        multiplier = 1.0
        status = "collecting_sample"
    else:
        # Conservative baseline: win-rate and capped profit-factor contribution.
        multiplier = (
            1.0
            + ((win_rate - 50.0) / 250.0)
            + ((min(profit_factor, 2.0) - 1.0) * 0.05)
        )
        # A positive aggregate must not hide a recent run of losses.
        if current_losing_streak >= 5:
            multiplier = min(multiplier, 0.80)
        elif current_losing_streak >= 3:
            multiplier = min(multiplier, 0.90)
        if profit_factor < 1.0 or total_pnl <= 0 or average_pnl <= 0:
            multiplier = min(multiplier, 0.90)
        multiplier = max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, multiplier))
        status = "learning_active"

    return {
        "strategy": strategy,
        "closed_trades": count,
        "minimum_sample": minimum_sample,
        "invalid_rows_ignored": invalid_rows,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 3),
        "realized_pnl": round(total_pnl, 4),
        "average_pnl": round(average_pnl, 4),
        "average_win": round(average_win, 4),
        "average_loss": round(average_loss, 4),
        "max_losing_streak": max_losing_streak,
        "current_losing_streak": current_losing_streak,
        "max_drawdown_usd": round(max_drawdown_usd, 4),
        "size_multiplier": round(multiplier, 3),
        "status": status,
        "paper_only": True,
    }


def get_learning_report(force=False):
    now = time.monotonic()
    with _lock:
        if (
            not force
            and _cache["report"] is not None
            and now - _cache["updated_at"] < CACHE_TTL
        ):
            return dict(_cache["report"])

    rows = database.get_recent_trades(mode="paper", limit=MAX_LOOKBACK)
    if not isinstance(rows, list):
        rows = []
    report = {
        "enabled": True,
        "mode": "paper",
        "live_execution": "locked",
        "lookback_trades": len(rows),
        "minimum_sample": _minimum_sample(),
        "strategies": [
            _profile(strategy, rows)
            for strategy in ("main", "sniper")
        ],
        "updated_at": time.time(),
        "policy": (
            "Completed paper SELL outcomes adjust only future paper position "
            "size within 0.80x-1.15x. A three-trade losing streak caps sizing "
            "at 0.90x; five losses cap it at 0.80x. Entry signals and live "
            "execution are never changed."
        ),
    }
    with _lock:
        _cache.update({"updated_at": now, "report": report})
    return dict(report)


def get_size_multiplier(strategy):
    if strategy not in ("main", "sniper"):
        return 1.0
    profile = next(
        (
            item for item in get_learning_report()["strategies"]
            if item["strategy"] == strategy
        ),
        None,
    )
    if not profile or not profile.get("paper_only"):
        return 1.0
    try:
        multiplier = float(profile.get("size_multiplier", 1.0))
    except (TypeError, ValueError, OverflowError):
        return 1.0
    if not __import__("math").isfinite(multiplier):
        return 1.0
    return max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, multiplier))
