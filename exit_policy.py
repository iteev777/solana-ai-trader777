"""Validated, deterministic paper-position exit policy.

This module is deliberately independent of exchange/RPC I/O. It evaluates
freshly supplied marks against persisted position state and returns a reason;
the caller remains responsible for recording the close atomically.
"""
import math
import json
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


def _trailing_bands(base_activation):
    """Return ordered peak-profit bands, each armed above the base threshold."""
    raw = os.getenv("TROJAN_TRAILING_BANDS_JSON", "").strip()
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("TROJAN_TRAILING_BANDS_JSON must contain valid JSON") from error
    if not isinstance(rows, list) or len(rows) > 10:
        raise ValueError("TROJAN_TRAILING_BANDS_JSON must be a list of at most 10 bands")
    result = []
    previous_activation = base_activation
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Trailing band must be an object")
        activation = _setting_value(row.get("activation_pct"), "trailing activation", 0.0, 10000.0)
        distance = _setting_value(row.get("distance_pct"), "trailing distance", 0.1, 95.0)
        if activation <= previous_activation:
            raise ValueError("Trailing band activations must increase above the base activation")
        if distance >= activation:
            raise ValueError("Trailing band distance must be below its activation threshold")
        result.append({"activation_pct": activation, "distance_pct": distance})
        previous_activation = activation
    return result


def _setting_value(value, label, minimum, maximum):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(label + " must be numeric") from error
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise ValueError(label + " is outside the allowed range")
    return number


def get_exit_policy():
    """Read bounded exit settings and reject internally impossible rule policies."""
    trailing_activation = _setting(
        "TROJAN_TRAILING_ACTIVATION_PCT", 4.0, 0.0, 10000.0
    )
    trailing_distance = _setting(
        "TROJAN_TRAILING_DISTANCE_PCT", 3.5, 0.1, 95.0
    )
    if trailing_distance >= trailing_activation:
        raise ValueError("Trailing distance must be below its activation threshold")

    break_even_activation = _setting(
        "TROJAN_BREAK_EVEN_ACTIVATION_PCT", 0.0, 0.0, 10000.0
    )
    break_even_offset = _setting(
        "TROJAN_BREAK_EVEN_OFFSET_PCT", 0.0, 0.0, 50.0
    )
    if break_even_activation > 0 and break_even_offset >= break_even_activation:
        raise ValueError("Break-even offset must be below its activation threshold")

    stagnation_seconds = int(
        _setting("TROJAN_STAGNATION_SECONDS", 0, 0, 31_536_000)
    )
    stagnation_band = _setting(
        "TROJAN_STAGNATION_BAND_PCT", 1.5, 0.0, 100.0
    )
    if stagnation_seconds > 0 and stagnation_band <= 0:
        raise ValueError("Stagnation band must be positive when stagnation exit is enabled")

    return {
        "stop_loss_pct": _setting("TROJAN_STOP_LOSS_PCT", 9.0, 0.1, 95.0),
        "take_profit_pct": _setting("TROJAN_TAKE_PROFIT_PCT", 10.0, 0.1, 10000.0),
        "trailing_activation_pct": trailing_activation,
        "trailing_distance_pct": trailing_distance,
        "trailing_bands": _trailing_bands(trailing_activation),
        "time_loss_seconds": int(_setting("TROJAN_TIME_LOSS_EXIT_SECONDS", 0, 0, 31_536_000)),
        "time_loss_threshold_pct": _setting("TROJAN_TIME_LOSS_THRESHOLD_PCT", -10.0, -95.0, 0.0),
        "max_hold_seconds": int(_setting("TROJAN_MAX_HOLD_SECONDS", 900, 0, 31_536_000)),
        "stagnation_seconds": stagnation_seconds,
        "stagnation_band_pct": stagnation_band,
        "break_even_activation_pct": break_even_activation,
        "break_even_offset_pct": break_even_offset,
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
    distance_pct = float(cfg["trailing_distance_pct"])
    for band in cfg.get("trailing_bands", []) or []:
        band_activation = float(band["activation_pct"])
        if peak_gain * 100.0 >= band_activation:
            distance_pct = float(band["distance_pct"])
    distance = distance_pct / 100.0
    # A trailing stop protects already-earned profit; it must not convert a
    # retracement below entry into a trailing-profit exit.
    if peak_gain >= activation and change > 0 and price <= peak * (1.0 - distance):
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
        time_loss_seconds = int(cfg.get("time_loss_seconds", 0))
        time_loss_threshold = float(cfg.get("time_loss_threshold_pct", -10.0)) / 100.0
        if time_loss_seconds > 0 and held >= time_loss_seconds and change <= time_loss_threshold:
            return "TIME LOSS EXIT"
        if stagnation_seconds > 0 and held >= stagnation_seconds and abs(change) <= band:
            return "STAGNATION EXIT"
        max_hold = int(cfg["max_hold_seconds"])
        if max_hold > 0 and held >= max_hold:
            return "MAX HOLD EXIT"
    return None
