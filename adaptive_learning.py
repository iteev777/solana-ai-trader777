"""Bounded adaptive learning from closed paper trades.

This module never reads live-mode trades and never modifies entry rules.
It only adjusts future paper position sizing after a meaningful sample exists.
"""
import threading
import time

import database

MIN_CLOSED_TRADES = max(10, int(__import__("os").getenv("TROJAN_ADAPTIVE_MIN_TRADES", "30")))
CACHE_TTL = 30
MIN_MULTIPLIER = 0.80
MAX_MULTIPLIER = 1.15

_lock = threading.Lock()
_cache = {"updated_at": 0.0, "report": None}


def _profile(strategy, rows):
    closed = [
        row for row in rows
        if row.get("action") == "SELL" and row.get("strategy", "main") == strategy
    ]
    values = []
    for row in reversed(closed):
        try:
            value = float(row.get("profit_loss") or 0)
        except (TypeError, ValueError, OverflowError):
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

    if count < MIN_CLOSED_TRADES:
        multiplier = 1.0
        status = "collecting_sample"
    else:
        multiplier = 1.0 + ((win_rate - 50.0) / 250.0) + ((min(profit_factor, 2.0) - 1.0) * 0.05)
        if profit_factor < 1.0 or total_pnl <= 0:
            multiplier = min(multiplier, 0.90)
        multiplier = max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, multiplier))
        status = "learning_active"

    return {
        "strategy": strategy,
        "closed_trades": count,
        "minimum_sample": MIN_CLOSED_TRADES,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 3),
        "realized_pnl": round(total_pnl, 4),
        "average_pnl": round(total_pnl / count, 4) if count else 0.0,
        "size_multiplier": round(multiplier, 3),
        "status": status,
        "paper_only": True,
    }


def get_learning_report(force=False):
    now = time.monotonic()
    with _lock:
        if not force and _cache["report"] is not None and now - _cache["updated_at"] < CACHE_TTL:
            return dict(_cache["report"])
    rows = database.get_recent_trades(mode="paper", limit=500)
    report = {
        "enabled": True,
        "mode": "paper",
        "live_execution": "locked",
        "lookback_trades": len(rows),
        "minimum_sample": MIN_CLOSED_TRADES,
        "strategies": [
            _profile(strategy, rows)
            for strategy in ("main", "sniper")
        ],
        "updated_at": time.time(),
        "policy": "Closed paper-trade outcomes adjust only future paper position size within 0.80x–1.15x.",
    }
    with _lock:
        _cache.update({"updated_at": now, "report": report})
    return dict(report)


def get_size_multiplier(strategy):
    if strategy not in ("main", "sniper"):
        return 1.0
    profile = next(
        (item for item in get_learning_report()["strategies"] if item["strategy"] == strategy),
        None,
    )
    return float(profile.get("size_multiplier", 1.0)) if profile else 1.0
