"""Validated DCA and exit-ladder policies for Trojan V2.

DCA is opt-in. Legacy fixed-USD steps remain supported, while new steps may size
adds as a multiple of the first buy. Automatic adds fail closed on missing or
stale price timestamps, invalid position clocks, and cooldown/holding gates.
"""
import json
import math
import os
import time
from datetime import datetime, timezone


def _number(value, label, minimum=0.0):
    if isinstance(value, bool):
        raise ValueError(label + " must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(label + " must be numeric") from error
    if not math.isfinite(result) or result < minimum:
        raise ValueError(label + " is invalid")
    return result


def _bounded_env_number(name, default, minimum, maximum):
    raw = os.getenv(name, "").strip()
    if not raw:
        return float(default)
    value = _number(raw, name, minimum)
    if value > maximum:
        raise ValueError(name + " is outside the allowed range")
    return value


def _enabled():
    return os.getenv("TROJAN_DCA_ENABLED", "false").strip().lower() in (
        "1", "true", "yes", "on"
    )


def _load_json_env(name, default):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(name + " must contain valid JSON") from error
    if not isinstance(value, list) or len(value) > 10:
        raise ValueError(name + " must be a list of at most 10 steps")
    return value


def get_dca_steps():
    """Return ordered DCA tiers; disabled by default.

    Existing add_usd configuration remains valid. New configurations may use
    add_multiple, a multiplier of the original first investment.
    """
    if not _enabled():
        return []
    default = [{"drop_pct": 5.0, "add_usd": 2.5}]
    rows = _load_json_env("TROJAN_DCA_STEPS_JSON", default)
    result = []
    previous_drop = 0.0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("DCA step must be an object")
        drop = _number(row.get("drop_pct"), "DCA drop", 0.1)
        if drop <= previous_drop or drop >= 90:
            raise ValueError("DCA drop tiers must increase and stay below 90")
        has_usd = "add_usd" in row
        has_multiple = "add_multiple" in row
        if has_usd == has_multiple:
            raise ValueError("DCA step must define exactly one of add_usd or add_multiple")
        if has_usd:
            result.append({"drop_pct": drop, "add_usd": _number(row.get("add_usd"), "DCA add", 0.01)})
        else:
            multiple = _number(row.get("add_multiple"), "DCA add multiple", 0.01)
            if multiple > 100:
                raise ValueError("DCA add multiple must not exceed 100")
            result.append({"drop_pct": drop, "add_multiple": multiple})
        previous_drop = drop
    return result


def _validate_dca_measure_from():
    value = os.getenv("TROJAN_DCA_MEASURE_FROM", "average").strip().lower()
    if value not in ("average", "first_entry"):
        raise ValueError("TROJAN_DCA_MEASURE_FROM must be average or first_entry")
    return value


def get_dca_policy():
    """Return the validated automatic-DCA policy snapshot."""
    return {
        "enabled": _enabled(),
        "steps": get_dca_steps(),
        "measure_from": _validate_dca_measure_from(),
        "cooldown_minutes": int(_bounded_env_number("TROJAN_DCA_COOLDOWN_MINUTES", 60, 0, 10080)),
        "min_hold_minutes": int(_bounded_env_number("TROJAN_DCA_MIN_HOLD_MINUTES", 0, 0, 10080)),
        "price_max_age_seconds": int(_bounded_env_number("TROJAN_DCA_PRICE_MAX_AGE_SECONDS", 30, 1, 300)),
    }


def _timestamp_seconds(value):
    """Accept Unix seconds/milliseconds, datetime, or ISO-8601 timestamps."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, datetime):
        parsed = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            return None
        return number / 1000.0 if number > 100000000000 else number
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            number = float(raw)
            if math.isfinite(number) and number > 0:
                return number / 1000.0 if number > 100000000000 else number
        except (TypeError, ValueError, OverflowError):
            pass
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            parsed = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def evaluate_dca_step(position, current_price, price_timestamp, step, policy, stop_loss_pct, now=None):
    """Pure fail-closed gate for one automatic paper-DCA step."""
    result = {
        "allowed": False, "reason": "DCA input unavailable", "amount_usd": None,
        "basis_price": None, "drop_pct": None, "price_age_seconds": None,
    }
    if not isinstance(position, dict) or not isinstance(step, dict) or not isinstance(policy, dict):
        return result
    if policy.get("enabled", True) is not True:
        result["reason"] = "DCA disabled in policy"
        return result
    try:
        price = _number(current_price, "current price", 0.000000000001)
        average = _number(position.get("entry_price"), "average entry", 0.000000000001)
        stop = _number(stop_loss_pct, "stop loss", 0.1)
        clock = time.time() if now is None else _number(now, "current time", 0.000000001)
        max_age = _number(policy.get("price_max_age_seconds", 30), "price max age", 1)
        cooldown = _number(policy.get("cooldown_minutes", 60), "DCA cooldown", 0)
        min_hold = _number(policy.get("min_hold_minutes", 0), "DCA minimum hold", 0)
        drop_threshold = _number(step.get("drop_pct"), "DCA drop", 0.1)
    except (TypeError, ValueError, OverflowError):
        result["reason"] = "Invalid DCA policy or position values"
        return result

    metadata = position.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    count = metadata.get("dca_count", 0)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        result["reason"] = "Invalid DCA count"
        return result

    if policy.get("measure_from", "average") == "average":
        basis = average
    elif policy.get("measure_from") == "first_entry":
        first_entry = metadata.get("first_entry_price")
        if first_entry is None and count == 0:
            first_entry = average
        try:
            basis = _number(first_entry, "first entry price", 0.000000000001)
        except (TypeError, ValueError, OverflowError):
            result["reason"] = "No valid first-entry price for DCA basis"
            return result
    else:
        result["reason"] = "Invalid DCA measurement basis"
        return result

    price_time = _timestamp_seconds(price_timestamp)
    if price_time is None:
        result["reason"] = "DCA price timestamp missing or invalid"
        return result
    age = clock - price_time
    result["price_age_seconds"] = age
    if age < 0:
        result["reason"] = "DCA price timestamp is in the future"
        return result
    if age > max_age:
        result["reason"] = "DCA price is stale"
        return result

    opened_at = _timestamp_seconds(position.get("opened_at"))
    held_minutes = max(0.0, (clock - opened_at) / 60.0) if opened_at is not None else None
    # ScreenerBot applies the minimum hold only before the first add. Later
    # adds are governed by the per-add cooldown.
    if count == 0 and min_hold > 0 and (held_minutes is None or held_minutes < min_hold):
        result["reason"] = "DCA minimum holding period not reached"
        return result

    if count > 0 and cooldown > 0:
        last_add = _timestamp_seconds(metadata.get("last_dca_at"))
        if last_add is None:
            result["reason"] = "Previous DCA time missing; cooldown cannot be verified"
            return result
        minutes_since_last = (clock - last_add) / 60.0
        if minutes_since_last < 0:
            result["reason"] = "Previous DCA timestamp is in the future"
            return result
        if minutes_since_last < cooldown:
            result["reason"] = "DCA cooldown active"
            return result

    # The hard stop always uses average entry, regardless of the ladder basis.
    if price <= average * (1.0 - stop / 100.0):
        result["reason"] = "DCA blocked at or beyond hard stop loss"
        return result

    drop_pct = ((price - basis) / basis) * 100.0
    result["basis_price"] = basis
    result["drop_pct"] = drop_pct
    if drop_pct >= -drop_threshold:
        result["reason"] = "Price drop has not passed the configured DCA level"
        return result

    if "add_multiple" in step:
        try:
            first_investment = _number(metadata.get("first_investment"), "first investment", 0.01)
            amount = first_investment * _number(step.get("add_multiple"), "DCA add multiple", 0.01)
        except (TypeError, ValueError, OverflowError):
            result["reason"] = "First investment unavailable for DCA sizing"
            return result
    else:
        try:
            amount = _number(step.get("add_usd"), "DCA add", 0.01)
        except (TypeError, ValueError, OverflowError):
            result["reason"] = "Invalid fixed DCA amount"
            return result

    if not math.isfinite(amount) or amount <= 0:
        result["reason"] = "Calculated DCA amount is invalid"
        return result
    result.update({"allowed": True, "reason": "DCA step approved", "amount_usd": amount})
    return result


def get_exit_steps():
    """Normalize exit tiers to shares of the remaining position.

    Legacy sell_fraction remains a fraction of the remaining position.
    ScreenerBot-style sell_pct is a percentage of the original position;
    it is converted to the equivalent remaining-position fraction. A final
    sell_pct tier may therefore normalize to 1.0 and close the runner.
    """
    default = [{"profit_pct": 8.0, "sell_fraction": 0.5}]
    rows = _load_json_env("TROJAN_EXIT_LADDER_JSON", default)
    result = []
    previous_profit = 0.0
    previous_mode = None
    sold_pct = 0.0
    tolerance = 1e-9

    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Exit step must be an object")
        profit = _number(row.get("profit_pct"), "Exit profit", 0.1)
        has_fraction = "sell_fraction" in row
        has_pct = "sell_pct" in row
        if has_fraction == has_pct:
            raise ValueError("Exit step must define exactly one of sell_fraction or sell_pct")
        mode = "remaining" if has_fraction else "original"
        if previous_mode is not None and mode != previous_mode:
            raise ValueError("Exit ladder cannot mix sell_fraction and sell_pct sizing")
        previous_mode = mode

        if profit <= previous_profit or profit >= 10000:
            raise ValueError("Exit profit tiers must increase")

        if has_fraction:
            fraction = _number(row.get("sell_fraction"), "Exit fraction", 0.01)
            if fraction >= 1:
                raise ValueError("Legacy sell_fraction must be below 1")
        else:
            sell_pct = _number(row.get("sell_pct"), "Exit percentage", 0.01)
            if sell_pct > 100:
                raise ValueError("Exit percentage cannot exceed 100")
            remaining_pct = 100.0 - sold_pct
            if remaining_pct <= tolerance or sell_pct > remaining_pct + tolerance:
                raise ValueError("Exit ladder sells more than the original position")
            fraction = sell_pct / remaining_pct
            if fraction > 1.0 + tolerance:
                raise ValueError("Exit ladder step exceeds the remaining position")
            if abs(fraction - 1.0) <= tolerance:
                fraction = 1.0
            sold_pct += sell_pct
            if sold_pct > 100.0 + tolerance:
                raise ValueError("Exit ladder sells more than 100 percent")

        result.append({"profit_pct": profit, "sell_fraction": fraction})
        previous_profit = profit
    return result


def policy_status():
    dca = get_dca_policy()
    return {
        "dca_enabled": dca["enabled"], "dca_steps": dca["steps"],
        "dca_measure_from": dca["measure_from"],
        "dca_cooldown_minutes": dca["cooldown_minutes"],
        "dca_min_hold_minutes": dca["min_hold_minutes"],
        "dca_price_max_age_seconds": dca["price_max_age_seconds"],
        "exit_steps": get_exit_steps(), "fraction_basis": "remaining_position",
    }
