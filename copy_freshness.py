"""Copy-event arrival freshness guard aligned with observed chain latency.

Freshness is measured at receipt time, not when a downstream worker eventually
processes the event. This prevents queue/processing delay from changing whether
an event was fresh when it entered the copy pipeline.
"""
import math
import os
import time


def _max_arrival_age_seconds():
    try:
        configured = float(os.getenv("TROJAN_COPY_MAX_EVENT_AGE_SECONDS", "30"))
    except (TypeError, ValueError, OverflowError):
        configured = 30.0
    if not math.isfinite(configured):
        configured = 30.0
    return max(1.0, min(300.0, configured))


def evaluate_event_freshness(block_time, received_at=None, now=None):
    """Evaluate chain-to-observer arrival age; malformed/future values fail closed.

    received_at is the time the transaction was observed/fetched. now is used
    only to validate that receipt timestamp, never to penalize later queue delay.
    Timestamps are Unix seconds.
    """
    current = time.time() if now is None else now
    received = current if received_at is None else received_at
    try:
        chain_time = float(block_time)
        received = float(received)
        current = float(current)
    except (TypeError, ValueError, OverflowError):
        return {"allowed": False, "reason": "COPY_EVENT_TIMESTAMP_INVALID", "age_seconds": None}
    if not all(math.isfinite(value) for value in (chain_time, received, current)):
        return {"allowed": False, "reason": "COPY_EVENT_TIMESTAMP_INVALID", "age_seconds": None}
    if received > current + 5:
        return {
            "allowed": False, "reason": "COPY_EVENT_RECEIPT_FROM_FUTURE",
            "age_seconds": None,
        }

    # ScreenerBot's arrival-distance policy compares detected_at with the chain
    # block time. Use the captured receipt timestamp, not processing-time now.
    age = received - chain_time
    if age < -5:
        return {
            "allowed": False, "reason": "COPY_EVENT_FROM_FUTURE",
            "age_seconds": round(age, 3),
        }
    max_age = _max_arrival_age_seconds()
    if age > max_age:
        return {
            "allowed": False, "reason": "COPY_EVENT_STALE",
            "age_seconds": round(age, 3), "max_age_seconds": max_age,
        }
    return {
        "allowed": True, "reason": "COPY_EVENT_FRESH",
        "age_seconds": round(max(0.0, age), 3), "max_age_seconds": max_age,
    }
