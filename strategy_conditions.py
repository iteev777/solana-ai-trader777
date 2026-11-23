"""Named strategy conditions inspired by common technical and position signals.

All evaluations are deterministic and read-only. Missing inputs fail closed with
an explicit reason; no candle, position, or market values are synthesized.
"""
import math
import os
import time
from datetime import datetime, timezone


TIMEFRAMES = ("m5", "h1", "h6", "h24")
INDICATOR_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "12h", "1d")
INDICATOR_TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "12h": 43200, "1d": 86400}
CONDITION_TYPES = (
    "BollingerBands", "BuyPressure", "CandleSize", "ConsecutiveCandles",
    "DrawdownFromPeak", "LiquidityChangeSinceEntry", "LiquidityLevel",
    "LiquidityToSize", "MaCross", "PositionAdds", "PositionHoldingTime",
    "PositionProfit", "PriceBreakout", "PriceChangePercent", "PriceToMa",
    "ProfitGiveback", "PullbackFromHigh", "Rsi", "TimeOfDay", "TokenAge",
    "TransactionVelocity", "VolatilityRange", "VolumeSpike", "Vwap",
)

SCHEMAS = {
    "BollingerBands": {"category": "Technical Indicators", "params": {"timeframe": list(INDICATOR_TIMEFRAMES), "comparison": ["BELOW", "ABOVE"], "threshold": [0.0, 1.0]}},
    "BuyPressure": {"category": "Market Context", "params": {"threshold": [0.0, 1.0]}},
    "CandleSize": {"category": "Price Analysis", "params": {"timeframe": list(INDICATOR_TIMEFRAMES), "metric": ["body", "range"], "comparison": ["ABOVE", "BELOW"], "threshold_pct": [0.0, 1000.0]}},
    "ConsecutiveCandles": {"category": "Price Analysis", "params": {"timeframe": list(INDICATOR_TIMEFRAMES), "direction": ["GREEN", "RED"], "minimum": [1.0, 100.0]}},
    "DrawdownFromPeak": {"category": "Price Analysis", "params": {"timeframe": list(INDICATOR_TIMEFRAMES), "minimum_pct": [0.0, 100.0], "maximum_pct": [0.0, 100.0]}},
    "LiquidityChangeSinceEntry": {"category": "Position & Performance", "params": {"comparison": ["ABOVE", "BELOW"], "threshold_pct": [-100.0, 10000.0]}},
    "LiquidityLevel": {"category": "Market Context", "params": {"comparison": ["ABOVE", "BELOW"], "threshold_usd": [0.0, 1e12]}},
    "LiquidityToSize": {"category": "Market Context", "params": {"minimum_multiple": [0.1, 1e9], "trade_size_usd": [0.01, 1e9]}},
    "MaCross": {"category": "Technical Indicators", "params": {"timeframe": list(INDICATOR_TIMEFRAMES), "direction": ["BULLISH", "BEARISH"]}},
    "PositionAdds": {"category": "Position & Performance", "params": {"comparison": ["AT_LEAST", "AT_MOST"], "count": [0.0, 100.0]}},
    "PositionHoldingTime": {"category": "Position & Performance", "params": {"comparison": ["ABOVE", "BELOW"], "minutes": [0.0, 1e7]}},
    "PositionProfit": {"category": "Position & Performance", "params": {"comparison": ["ABOVE", "BELOW"], "profit_pct": [-100.0, 1e6]}},
    "PriceBreakout": {"category": "Price Analysis", "params": {"timeframe": list(INDICATOR_TIMEFRAMES), "direction": ["ABOVE", "BELOW"]}},
    "PriceChangePercent": {"category": "Price Analysis", "params": {"timeframe": list(TIMEFRAMES), "comparison": ["ABOVE", "BELOW"], "threshold_pct": [-100.0, 1e6]}},
    "PriceToMa": {"category": "Technical Indicators", "params": {"timeframe": list(INDICATOR_TIMEFRAMES), "period": [9.0, 200.0], "comparison": ["ABOVE", "BELOW"], "threshold_pct": [-100.0, 1000.0]}},
    "ProfitGiveback": {"category": "Position & Performance", "params": {"comparison": ["ABOVE", "BELOW"], "giveback_pct": [0.0, 100.0]}},
    "PullbackFromHigh": {"category": "Price Analysis", "params": {"timeframe": list(INDICATOR_TIMEFRAMES), "minimum_pct": [0.0, 100.0], "maximum_pct": [0.0, 100.0]}},
    "Rsi": {"category": "Technical Indicators", "params": {"timeframe": list(INDICATOR_TIMEFRAMES), "comparison": ["BELOW", "ABOVE"], "threshold": [1.0, 99.0]}},
    "TimeOfDay": {"category": "Market Context", "params": {"start_hour_utc": [0.0, 23.0], "end_hour_utc": [0.0, 23.0]}},
    "TokenAge": {"category": "Market Context", "params": {"comparison": ["ABOVE", "BELOW"], "minutes": [0.0, 1e8]}},
    "TransactionVelocity": {"category": "Market Context", "params": {"window": ["5m_vs_1h", "1h_vs_6h", "6h_vs_24h"], "comparison": ["ABOVE", "BELOW"], "ratio": [0.1, 50.0], "min_transactions": [1.0, 100000.0]}},
    "VolatilityRange": {"category": "Technical Indicators", "params": {"timeframe": list(INDICATOR_TIMEFRAMES), "comparison": ["ABOVE", "BELOW"], "threshold_pct": [0.1, 500.0]}},
    "VolumeSpike": {"category": "Volume Analysis", "params": {"timeframe": list(INDICATOR_TIMEFRAMES), "multiplier": [1.0, 50.0]}},
    "Vwap": {"category": "Technical Indicators", "params": {"timeframe": list(INDICATOR_TIMEFRAMES), "position": ["ABOVE", "BELOW"]}},
}


def _num(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _path(data, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _metric(context, *paths):
    for path in paths:
        value = _path(context, *path.split("."))
        number = _num(value)
        if number is not None:
            return number
    return None


def _compare(actual, comparison, threshold):
    if actual is None:
        return None
    return actual >= threshold if comparison in ("ABOVE", "AT_LEAST") else actual <= threshold


def validate_condition(node):
    if not isinstance(node, dict) or set(node) != {"condition", "params"}:
        raise ValueError("Named condition needs exactly condition and params")
    name = node.get("condition")
    if name not in SCHEMAS:
        raise ValueError("Unknown named condition: " + str(name))
    params = node.get("params")
    if not isinstance(params, dict):
        raise ValueError(name + ": params must be an object")
    schema = SCHEMAS[name]["params"]
    unknown = set(params) - set(schema)
    if unknown:
        raise ValueError(name + ": unknown parameters: " + ", ".join(sorted(unknown)))
    for key, rule in schema.items():
        if key not in params:
            continue
        value = params[key]
        if isinstance(rule, list) and rule and isinstance(rule[0], str):
            if value not in rule:
                raise ValueError(name + "." + key + ": unsupported choice")
        else:
            number = _num(value)
            if number is None or number < rule[0] or number > rule[1]:
                raise ValueError(name + "." + key + ": value outside allowed range")
    if name in ("DrawdownFromPeak", "PullbackFromHigh"):
        low = _num(params.get("minimum_pct", 0))
        high = _num(params.get("maximum_pct", 100))
        if low is not None and high is not None and low > high:
            raise ValueError(name + ": minimum_pct exceeds maximum_pct")
    if name == "PriceToMa":
        period = _num(params.get("period", 9))
        if period not in (9, 21):
            raise ValueError("PriceToMa.period must be 9 or 21")
    if name == "TimeOfDay":
        start = _num(params.get("start_hour_utc", 0))
        end = _num(params.get("end_hour_utc", 23))
        if start is not None and end is not None and start == end:
            raise ValueError("TimeOfDay: start and end hours must differ")
    return True


def _indicator_freshness(name, context, indicators, timeframe="1m"):
    """Require fresh, explicitly ready OHLCV-derived indicators for technical rules."""
    direct_metrics = {
        "DrawdownFromPeak": ("drawdown_from_peak_pct", "pullback_from_high_pct"),
        "PullbackFromHigh": ("pullback_from_high_pct", "drawdown_from_peak_pct"),
    }
    if name in direct_metrics and any(
        _metric(context, field) is not None for field in direct_metrics[name]
    ):
        return True, None

    indicator_conditions = {
        "BollingerBands", "CandleSize", "ConsecutiveCandles", "MaCross",
        "PriceBreakout", "PriceToMa", "Rsi", "VolatilityRange",
        "VolumeSpike", "Vwap", "DrawdownFromPeak", "PullbackFromHigh",
    }
    if name not in indicator_conditions:
        return True, None

    if indicators.get("ready") is not True:
        return False, "indicator series unavailable or not ready"
    timestamp = _num(indicators.get("timestamp"))
    if timestamp is None or timestamp <= 0:
        return False, "indicator timestamp is missing"
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0

    try:
        maximum_age = float(os.getenv(
            "TROJAN_STRATEGY_MAX_INDICATOR_AGE_SECONDS", "180"
        ))
    except (TypeError, ValueError, OverflowError):
        maximum_age = 180.0
    if not math.isfinite(maximum_age) or not 30.0 <= maximum_age <= 3600.0:
        maximum_age = 180.0
    # Candle timestamps identify the start of their bucket. Allow up to three
    # selected-timeframe buckets, while the nested frame readiness still depends
    # on the live 1m source-quality gate.
    maximum_age = max(maximum_age, INDICATOR_TIMEFRAME_SECONDS.get(timeframe, 60) * 3)

    evaluated_at = _num(context.get("evaluated_at"))
    now = evaluated_at if evaluated_at is not None else time.time()
    age = now - timestamp
    if age < -5.0:
        return False, "indicator timestamp is in the future"
    if age > maximum_age:
        return False, "indicator series is stale"
    return True, None


def evaluate_condition(node, context):
    validate_condition(node)
    name, params = node["condition"], node["params"]
    indicators = context.get("indicators") if isinstance(context.get("indicators"), dict) else {}
    technical = {
        "BollingerBands", "CandleSize", "ConsecutiveCandles", "DrawdownFromPeak",
        "MaCross", "PriceBreakout", "PriceToMa", "PullbackFromHigh", "Rsi",
        "VolatilityRange", "VolumeSpike", "Vwap",
    }
    timeframe = params.get("timeframe", "1m") if name in technical else "1m"
    direct_position_metric = (
        name in ("DrawdownFromPeak", "PullbackFromHigh")
        and any(_metric(context, field) is not None for field in (
            "drawdown_from_peak_pct", "pullback_from_high_pct"
        ))
    )
    if name in technical and not direct_position_metric:
        timeframe_map = indicators.get("timeframes")
        if isinstance(timeframe_map, dict):
            selected = timeframe_map.get(timeframe)
            if not isinstance(selected, dict):
                return False, [name + ": timeframe data unavailable (" + timeframe + ")"]
            indicators = selected
            context = dict(context)
            context["indicators"] = indicators
        elif timeframe != "1m":
            return False, [name + ": timeframe data unavailable (" + timeframe + ")"]
    fresh, freshness_reason = _indicator_freshness(name, context, indicators, timeframe)
    if not fresh:
        return False, [name + ": " + freshness_reason]
    token = context.get("token") if isinstance(context.get("token"), dict) else {}
    liquidity = _metric(context, "liquidity_usd", "liquidity.usd")
    current_price = _metric(context, "price_usd", "priceUsd")
    result = None
    detail = ""

    if name == "Rsi":
        value = _metric(context, "rsi_14", "indicators.rsi_14")
        result = _compare(value, params.get("comparison", "BELOW"), _num(params.get("threshold", 30)))
        detail = "RSI"
    elif name == "BollingerBands":
        value = _metric(context, "bollinger_percent_b", "indicators.bollinger_percent_b")
        result = _compare(value, params.get("comparison", "BELOW"), _num(params.get("threshold", 0.2)))
        detail = "Bollinger %B"
    elif name == "BuyPressure":
        value = _metric(context, "buy_ratio")
        result = value is not None and value >= _num(params.get("threshold", 0.6))
        detail = "buy ratio"
    elif name == "CandleSize":
        field = "candle_body_pct" if params.get("metric", "body") == "body" else "candle_range_pct"
        value = _metric(context, field, "indicators." + field)
        result = _compare(value, params.get("comparison", "ABOVE"), _num(params.get("threshold_pct", 1)))
        detail = field
    elif name == "ConsecutiveCandles":
        field = "consecutive_green_candles" if params.get("direction", "GREEN") == "GREEN" else "consecutive_red_candles"
        value = _metric(context, field, "indicators." + field)
        result = value is not None and value >= _num(params.get("minimum", 2))
        detail = field
    elif name in ("DrawdownFromPeak", "PullbackFromHigh"):
        value = _metric(context, "drawdown_from_peak_pct", "pullback_from_high_pct", "indicators.pullback_from_high_pct")
        if value is None and current_price is not None:
            peak = _metric(context, "previous_high_20", "indicators.previous_high_20")
            if peak and peak > 0:
                value = max(0.0, (peak - current_price) / peak * 100.0)
        low = _num(params.get("minimum_pct", 5))
        high = _num(params.get("maximum_pct", 30))
        result = value is not None and low <= value <= high
        detail = "drawdown/pullback percent"
    elif name == "LiquidityChangeSinceEntry":
        value = _metric(context, "liquidity_change_since_entry_pct")
        result = _compare(value, params.get("comparison", "ABOVE"), _num(params.get("threshold_pct", 0)))
        detail = "liquidity change"
    elif name == "LiquidityLevel":
        result = _compare(liquidity, params.get("comparison", "ABOVE"), _num(params.get("threshold_usd", 10000)))
        detail = "liquidity USD"
    elif name == "LiquidityToSize":
        size = _num(params.get("trade_size_usd", context.get("trade_size_usd")))
        minimum = _num(params.get("minimum_multiple", 100))
        result = None if liquidity is None or size is None or size <= 0 else liquidity / size >= minimum
        detail = "liquidity / trade size"
    elif name == "MaCross":
        bullish = indicators.get("ema_cross_bullish")
        direction = params.get("direction", "BULLISH")
        result = None if not isinstance(bullish, bool) else (bullish is True) if direction == "BULLISH" else (bullish is False)
        detail = "EMA cross"
    elif name == "PositionAdds":
        value = _metric(context, "position_add_count", "position_adds")
        result = _compare(value, params.get("comparison", "AT_LEAST"), _num(params.get("count", 1)))
        detail = "position adds"
    elif name == "PositionHoldingTime":
        value = _metric(context, "position_holding_minutes")
        result = _compare(value, params.get("comparison", "ABOVE"), _num(params.get("minutes", 30)))
        detail = "holding minutes"
    elif name == "PositionProfit":
        value = _metric(context, "position_profit_pct", "profit_pct")
        result = _compare(value, params.get("comparison", "ABOVE"), _num(params.get("profit_pct", 5)))
        detail = "position profit"
    elif name == "PriceBreakout":
        value = indicators.get("breakout_20") if params.get("direction", "ABOVE") == "ABOVE" else indicators.get("breakdown_20")
        result = value if isinstance(value, bool) else None
        detail = "20-candle high/low breakout"
    elif name == "PriceChangePercent":
        window = params.get("timeframe", "m5")
        field = {"m5": "m5", "h1": "h1", "h6": "h6", "h24": "h24"}[window]
        value = _metric(context, "price_change_" + field, "priceChange." + field, "token.priceChange." + field)
        result = _compare(value, params.get("comparison", "ABOVE"), _num(params.get("threshold_pct", 0)))
        detail = "price change " + window
    elif name == "PriceToMa":
        period = int(_num(params.get("period", 9)))
        ema = _metric(context, "ema_" + str(period), "indicators.ema_" + str(period))
        value = (current_price / ema - 1) * 100 if current_price is not None and ema and ema > 0 else None
        result = _compare(value, params.get("comparison", "ABOVE"), _num(params.get("threshold_pct", 0)))
        detail = "price to EMA" + str(period)
    elif name == "ProfitGiveback":
        value = _metric(context, "profit_giveback_pct")
        result = _compare(value, params.get("comparison", "ABOVE"), _num(params.get("giveback_pct", 25)))
        detail = "profit giveback"
    elif name == "TimeOfDay":
        now = _num(context.get("evaluated_at")) or time.time()
        hour = datetime.fromtimestamp(now, timezone.utc).hour
        start = int(_num(params.get("start_hour_utc", 0)))
        end = int(_num(params.get("end_hour_utc", 23)))
        result = start <= hour < end if start < end else hour >= start or hour < end
        detail = "UTC hour"
    elif name == "TokenAge":
        age = _metric(context, "pair_age_minutes")
        if age is None:
            created = _metric(context, "pairCreatedAt", "token.pairCreatedAt")
            if created is not None:
                created = created / 1000 if created > 10_000_000_000 else created
                evaluated_at = _num(context.get("evaluated_at")) or time.time()
                age = max(0, (evaluated_at - created) / 60)
        result = _compare(age, params.get("comparison", "ABOVE"), _num(params.get("minutes", 60)))
        detail = "token age minutes"
    elif name == "TransactionVelocity":
        windows = params.get("window", "5m_vs_1h")
        short, long, short_seconds, long_seconds = {
            "5m_vs_1h": ("m5", "h1", 300.0, 3600.0),
            "1h_vs_6h": ("h1", "h6", 3600.0, 21600.0),
            "6h_vs_24h": ("h6", "h24", 21600.0, 86400.0),
        }[windows]
        txns = _path(token, "txns") or context.get("txns") or {}
        def total(window):
            row = txns.get(window) if isinstance(txns, dict) else None
            buys = _num(_path(row, "buys"))
            sells = _num(_path(row, "sells"))
            return buys + sells if buys is not None and sells is not None else None
        short_count, long_count = total(short), total(long)
        minimum = _num(params.get("min_transactions", 30))
        if short_count is not None and long_count is not None and short_count >= minimum and long_count > 0:
            ratio = (short_count / short_seconds) / (long_count / long_seconds)
            result = _compare(ratio, params.get("comparison", "ABOVE"), _num(params.get("ratio", 2)))
        else:
            result = None
        detail = "transaction velocity " + windows
    elif name == "VolatilityRange":
        value = _metric(context, "atr_percent", "indicators.atr_percent")
        result = _compare(value, params.get("comparison", "ABOVE"), _num(params.get("threshold_pct", 5)))
        detail = "ATR percent"
    elif name == "VolumeSpike":
        value = _metric(context, "volume_spike_ratio", "indicators.volume_spike_ratio")
        result = value is not None and value >= _num(params.get("multiplier", 2))
        detail = "volume spike ratio"
    elif name == "Vwap":
        value = indicators.get("above_vwap")
        result = None if not isinstance(value, bool) else (value is True) if params.get("position", "ABOVE") == "ABOVE" else (value is False)
        detail = "VWAP position"

    if result is None:
        return False, [name + ": required data unavailable"]
    return bool(result), ([] if result else [name + ": " + detail + " condition not met"])


def get_condition_catalog():
    return [
        {
            "type": name,
            "category": SCHEMAS[name]["category"],
            "parameters": SCHEMAS[name]["params"],
            "reads": "market/indicators/position",
        }
        for name in CONDITION_TYPES
    ]
