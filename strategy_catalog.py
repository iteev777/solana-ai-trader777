"""Dashboard-friendly strategy condition catalog and safe presets."""
import strategy_rules

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
        "presets": PRESETS,
        "timeframes": ["1m", "5m", "15m", "1h", "4h", "12h", "1d"],
        "logic": ["all", "any", "not"],
        "paper_validation_recommended": True,
        "live_execution": "locked",
    }
