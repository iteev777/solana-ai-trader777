"""Validated, deterministic paper-position exit policy.

This module is deliberately independent of exchange/RPC I/O. It evaluates
freshly supplied marks against persisted position state and returns a reason;
the caller remains responsible for recording the close atomically.
"""
import math
import os
import time
from datetime import datetime


def _setting(name, default, minimum=0.0, maximum=None):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        value = float(default)
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(name + " must be numeric") from error
    if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(name + " is outside the allowed range")
    return value


def get_exit_policy():
    """Read bounded exit settings; defaults preserve Trojan's existing behavior."""
    return {
        "stop_loss_pct": _setting("TROJAN_STOP_LOSS_PCT", 9.0, 0.1, 95.0),
        "take_profit_pct": _setting("TROJAN_TAKE_PROFIT_PCT", 10.0, 0.1, 10000.0),
        "trailing_activation_pct": _setting("TROJAN_TRAILING_ACTIVATION_PCT", 4.0, 0.0, 10000.0),
        "trailing_distance_pct": _setting("TROJAN_TRAILING_DISTANCE_PCT", 3.5, 0.1, 95.0),
        "max_hold_seconds": int(_setting("TROJAN_MAX_HOLD_SECONDS", 900, 0, 31_536_000)),
        "stagnation_seconds": int(_setting("TROJAN_STAGNATION_SECONDS", 0, 0, 31_536_000)),
        "stagnation_band_pct": _setting("TROJAN_STAGNATION_BAND_PCT", 1.5, 0.0, 100.0),
        "break_even_activation_pct": _setting("TROJAN_BREAK_EVEN_ACTIVATION_PCT", 0.0, 0.0, 10000.0),
        "break_even_offset_pct": _setting("TROJAN_BREAK_EVEN_OFFSET_PCT", 0.0, 0.0, 50.0),
    }


def _opened_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def evaluate_exit(position, current_price, now=None, policy=None):
    """Return an exit reason or None. Trailing activation is measured from peak."""
    if not isinstance(position, dict):
        return None
    try:
        price = float(current_price)
        entry = float(position.get("entry_price"))
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(price) or not math.isfinite(entry) or price <= 0 or entry <= 0:
        return None
    cfg = get_exit_policy() if policy is None else policy
    clock = time.time() if now is None else float(now)
    if not math.isfinite(clock):
        return None

    change = price / entry - 1.0
    stop = float(cfg["stop_loss_pct"]) / 100.0
    take = float(cfg["take_profit_pct"]) / 100.0
    if change <= -stop:
        return "STOP LOSS"

    metadata = position.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    peak = metadata.get("peak_price", entry)
    try:
        peak = max(entry, float(peak))
    except (TypeError, ValueError, OverflowError):
        peak = entry
    peak_gain = peak / entry - 1.0
    activation = float(cfg["trailing_activation_pct"]) / 100.0
    distance = float(cfg["trailing_distance_pct"]) / 100.0
    if peak_gain >= activation and price <= peak * (1.0 - distance):
        return "TRAILING PROFIT EXIT"

    be_activation = float(cfg.get("break_even_activation_pct", 0.0)) / 100.0
    be_offset = float(cfg.get("break_even_offset_pct", 0.0)) / 100.0
    if be_activation > 0 and peak_gain >= be_activation:
        if price <= entry * (1.0 + be_offset):
            return "BREAK-EVEN PROTECTION"

    if change >= take:
        return "TAKE PROFIT"

    opened = _opened_timestamp(position.get("opened_at"))
    if opened is not None:
        held = max(0.0, clock - opened)
        stagnation_seconds = int(cfg.get("stagnation_seconds", 0))
        band = float(cfg.get("stagnation_band_pct", 1.5)) / 100.0
        if stagnation_seconds > 0 and held >= stagnation_seconds and abs(change) <= band:
            return "STAGNATION EXIT"
        max_hold = int(cfg["max_hold_seconds"])
        if max_hold > 0 and held >= max_hold:
            return "MAX HOLD EXIT"
    return None
