"""Dashboard-friendly strategy condition catalog and safe presets."""
import os
import math
import strategy_rules
import strategy_conditions

def _indicator_max_age():
    try:
        value = float(os.getenv("TROJAN_STRATEGY_MAX_INDICATOR_AGE_SECONDS", "180"))
    except (TypeError, ValueError, OverflowError):
        return 180.0
    return value if math.isfinite(value) and 30.0 <= value <= 3600.0 else 180.0

def _evaluation_timeout_ms():
    try:
        value = float(os.getenv("TROJAN_STRATEGY_EVALUATION_TIMEOUT_MS", "50"))
    except (TypeError, ValueError, OverflowError):
        return 50
    return int(value) if math.isfinite(value) and 1.0 <= value <= 1000.0 else 50


PRESETS = {
    "momentum_confirmation": {
        "all": [
            {"field": "rsi_14", "op": "between", "value": [35, 70]},
            {"field": "ema_cross_bullish", "op": "eq", "value": 1},
            {"field": "volume_spike_ratio", "op": "gte", "value": 1.5},
        ]
    },
    "breakout_with_flow": {
        "all": [
            {"field": "breakout_20", "op": "eq", "value": 1},
            {"field": "buy_ratio", "op": "gte", "value": 0.58},
            {"field": "liquidity_usd", "op": "gte", "value": 10000},
        ]
    },
    "risk_aware_scalp": {
        "all": [
            {"field": "liquidity_usd", "op": "gte", "value": 15000},
            {"field": "volume_m5", "op": "gte", "value": 5000},
            {"field": "atr_percent", "op": "lt", "value": 12},
            {"field": "consecutive_red_candles", "op": "lte", "value": 2},
        ]
    },
}


def get_strategy_catalog():
    fields = sorted(strategy_rules.ALLOWED_FIELDS)
    return {
        "fields": fields,
        "operators": sorted(strategy_rules.ALLOWED_OPERATORS),
        "conditions": strategy_conditions.get_condition_catalog(),
        "condition_types": list(strategy_conditions.CONDITION_TYPES),
        "presets": PRESETS,
        "timeframes": ["1m", "5m", "15m", "1h", "4h", "12h", "1d"],
        "logic": ["all", "any", "not"],
        "evaluation_policy": {
            "timeout_ms": _evaluation_timeout_ms(),
            "maximum_rule_depth": 4,
            "maximum_group_children": 30,
            "missing_data": "unknown_fail_closed",
        },
        "paper_validation_recommended": True,
        "indicator_freshness": {
            "enabled": True,
            "max_age_seconds": _indicator_max_age(),
            "missing_timestamp": "reject",
            "future_timestamp": "reject",
        },
        "live_execution": "locked",
    }
