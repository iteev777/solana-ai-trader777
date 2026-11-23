"""Validated multi-step DCA and exit-ladder policies for Trojan V2."""
import json
import math
import os


def _number(value, label, minimum=0.0):
    if isinstance(value, bool):
        raise ValueError(label + " must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(label + " is invalid")
    return result


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
    """Returns ordered DCA tiers; disabled by default."""
    if os.getenv("TROJAN_DCA_ENABLED", "false").lower() != "true":
        return []
    default = [{"drop_pct": 5.0, "add_usd": 2.5}]
    rows = _load_json_env("TROJAN_DCA_STEPS_JSON", default)
    result = []
    previous_drop = 0.0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("DCA step must be an object")
        drop = _number(row.get("drop_pct"), "DCA drop", 0.1)
        amount = _number(row.get("add_usd"), "DCA add", 0.01)
        if drop <= previous_drop or drop >= 90:
            raise ValueError("DCA drop tiers must increase and stay below 90%")
        result.append({"drop_pct": drop, "add_usd": amount})
        previous_drop = drop
    return result


def get_exit_steps():
    """Fractions apply to the remaining position, not the original size."""
    default = [{"profit_pct": 8.0, "sell_fraction": 0.5}]
    rows = _load_json_env("TROJAN_EXIT_LADDER_JSON", default)
    result = []
    previous_profit = 0.0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Exit step must be an object")
        profit = _number(row.get("profit_pct"), "Exit profit", 0.1)
        fraction = _number(row.get("sell_fraction"), "Exit fraction", 0.01)
        if profit <= previous_profit or profit >= 10000:
            raise ValueError("Exit profit tiers must increase")
        if fraction >= 1:
            raise ValueError("Partial exit fraction must be below 1")
        result.append({"profit_pct": profit, "sell_fraction": fraction})
        previous_profit = profit
    return result


def completed_exit_steps(metadata):
    """Read ladder tiers while preserving the original one-shot completion flag."""
    completed = metadata.get("exit_ladder_completed")
    if isinstance(completed, list):
        return [tier for tier in completed if type(tier) is int and tier >= 0]
    return [0] if metadata.get("partial_take_profit_done") is True else []


def policy_status():
    return {
        "dca_enabled": os.getenv("TROJAN_DCA_ENABLED", "false").lower() == "true",
        "dca_steps": get_dca_steps(),
        "exit_steps": get_exit_steps(),
        "fraction_basis": "remaining_position"
    }
