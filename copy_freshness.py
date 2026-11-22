"""Copy-trade freshness guard. Never treats an old replay as a live signal."""
import math
import os
import time


def evaluate_event_freshness(block_time, received_at=None, now=None):
    try:
        configured_max_age = float(os.getenv("TROJAN_COPY_MAX_EVENT_AGE_SECONDS", "30"))
    except (TypeError, ValueError, OverflowError):
        configured_max_age = 30.0
    if not math.isfinite(configured_max_age):
        configured_max_age = 30.0
    max_age = max(1.0, min(300.0, configured_max_age))
    current = time.time() if now is None else float(now)
    received = current if received_at is None else float(received_at)
    try:
        chain_time = float(block_time)
    except (TypeError, ValueError, OverflowError):
        return {"allowed": False, "reason": "COPY_EVENT_BLOCK_TIME_MISSING", "age_seconds": None}
    if not math.isfinite(chain_time) or not math.isfinite(received) or not math.isfinite(current):
        return {"allowed": False, "reason": "COPY_EVENT_TIMESTAMP_INVALID", "age_seconds": None}
    age = current - chain_time
    if age < -5:
        return {"allowed": False, "reason": "COPY_EVENT_FROM_FUTURE", "age_seconds": round(age, 3)}
    if age > max_age:
        return {"allowed": False, "reason": "COPY_EVENT_STALE", "age_seconds": round(age, 3), "max_age_seconds": max_age}
    return {"allowed": True, "reason": "COPY_EVENT_FRESH", "age_seconds": round(max(0, age), 3), "max_age_seconds": max_age}
