"""Named strategy conditions inspired by common technical and position signals.

All evaluations are deterministic and read-only. Missing inputs fail closed with
an explicit reason; no candle, position, or market values are synthesized.
"""
import math
import time
from datetime import datetime, timezone


TIMEFRAMES = ("m5", "h1", "h6", "h24")
CONDITION_TYPES = (
    "BollingerBands", "BuyPressure", "CandleSize", "ConsecutiveCandles",
    "DrawdownFromPeak", "LiquidityChangeSinceEntry", "LiquidityLevel",
    "LiquidityToSize", "MaCross", "PositionAdds", "PositionHoldingTime",
    "PositionProfit", "PriceBreakout", "PriceChangePercent", "PriceToMa",
    "ProfitGiveback", "PullbackFromHigh", "Rsi", "TimeOfDay", "TokenAge",
    "TransactionVelocity", "VolatilityRange", "VolumeSpike", "Vwap",
)

SCHEMAS = {
    "BollingerBands": {"category": "Technical Indicators", "params": {"comparison": ["BELOW", "ABOVE"], "threshold": [0.0, 1.0]}},
    "BuyPressure": {"category": "Market Context", "params": {"threshold": [0.0, 1.0]}},
    "CandleSize": {"category": "Price Analysis", "params": {"metric": ["body", "range"], "comparison": ["ABOVE", "BELOW"], "threshold_pct": [0.0, 1000.0]}},
    "ConsecutiveCandles": {"category": "Price Analysis", "params": {"direction": ["GREEN", "RED"], "minimum": [1.0, 100.0]}},
    "DrawdownFromPeak": {"category": "Price Analysis", "params": {"minimum_pct": [0.0, 100.0], "maximum_pct": [0.0, 100.0]}},
    "LiquidityChangeSinceEntry": {"category": "Position & Performance", "params": {"comparison": ["ABOVE", "BELOW"], "threshold_pct": [-100.0, 10000.0]}},
    "LiquidityLevel": {"category": "Market Context", "params": {"comparison": ["ABOVE", "BELOW"], "threshold_usd": [0.0, 1e12]}},
    "LiquidityToSize": {"category": "Market Context", "params": {"minimum_multiple": [0.1, 1e9], "trade_size_usd": [0.01, 1e9]}},
    "MaCross": {"category": "Technical Indicators", "params": {"direction": ["BULLISH", "BEARISH"]}},
    "PositionAdds": {"category": "Position & Performance", "params": {"comparison": ["AT_LEAST", "AT_MOST"], "count": [0.0, 100.0]}},
    "PositionHoldingTime": {"category": "Position & Performance", "params": {"comparison": ["ABOVE", "BELOW"], "minutes": [0.0, 1e7]}},
    "PositionProfit": {"category": "Position & Performance", "params": {"comparison": ["ABOVE", "BELOW"], "profit_pct": [-100.0, 1e6]}},
    "PriceBreakout": {"category": "Price Analysis", "params": {"direction": ["ABOVE", "BELOW"]}},
    "PriceChangePercent": {"category": "Price Analysis", "params": {"timeframe": list(TIMEFRAMES), "comparison": ["ABOVE", "BELOW"], "threshold_pct": [-100.0, 1e6]}},
    "PriceToMa": {"category": "Technical Indicators", "params": {"period": [9.0, 200.0], "comparison": ["ABOVE", "BELOW"], "threshold_pct": [-100.0, 1000.0]}},
    "ProfitGiveback": {"category": "Position & Performance", "params": {"comparison": ["ABOVE", "BELOW"], "giveback_pct": [0.0, 100.0]}},
    "PullbackFromHigh": {"category": "Price Analysis", "params": {"minimum_pct": [0.0, 100.0], "maximum_pct": [0.0, 100.0]}},
    "Rsi": {"category": "Technical Indicators", "params": {"comparison": ["BELOW", "ABOVE"], "threshold": [1.0, 99.0]}},
    "TimeOfDay": {"category": "Market Context", "params": {"start_hour_utc": [0.0, 23.0], "end_hour_utc": [0.0, 23.0]}},
    "TokenAge": {"category": "Market Context", "params": {"comparison": ["ABOVE", "BELOW"], "minutes": [0.0, 1e8]}},
    "TransactionVelocity": {"category": "Market Context", "params": {"window": ["5m_vs_1h", "1h_vs_6h", "6h_vs_24h"], "comparison": ["ABOVE", "BELOW"], "ratio": [0.1, 50.0], "min_transactions": [1.0, 100000.0]}},
    "VolatilityRange": {"category": "Technical Indicators", "params": {"comparison": ["ABOVE", "BELOW"], "threshold_pct": [0.1, 500.0]}},
    "VolumeSpike": {"category": "Volume Analysis", "params": {"multiplier": [1.0, 50.0]}},
    "Vwap": {"category": "Technical Indicators", "params": {"position": ["ABOVE", "BELOW"]}},
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
    if name == "TimeOfDay":
        start = _num(params.get("start_hour_utc", 0))
        end = _num(params.get("end_hour_utc", 23))
        if start is not None and end is not None and start == end:
            raise ValueError("TimeOfDay: start and end hours must differ")
    return True


def evaluate_condition(node, context):
    validate_condition(node)
    name, params = node["condition"], node["params"]
    indicators = context.get("indicators") if isinstance(context.get("indicators"), dict) else {}
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
        size = _num(params.get("trade_size_usd", context.get("trade_size_usd", 0)))
        minimum = _num(params.get("minimum_multiple", 100))
        result = liquidity is not None and size is not None and size > 0 and liquidity / size >= minimum
        detail = "liquidity / trade size"
    elif name == "MaCross":
        bullish = indicators.get("ema_cross_bullish")
        direction = params.get("direction", "BULLISH")
        result = (bullish is True) if direction == "BULLISH" else (bullish is False)
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
        value = indicators.get("breakout_20")
        direction = params.get("direction", "ABOVE")
        result = (value is True) if direction == "ABOVE" else (value is False)
        detail = "20-candle breakout"
    elif name == "PriceChangePercent":
        window = params.get("timeframe", "m5")
        field = {"m5": "m5", "h1": "h1", "h6": "h6", "h24": "h24"}[window]
        value = _metric(context, "price_change_" + field, "priceChange." + field)
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
                age = max(0, (time.time() - created) / 60)
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
        result = (value is True) if params.get("position", "ABOVE") == "ABOVE" else (value is False)
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
