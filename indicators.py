"""GeckoTerminal OHLCV retrieval and lightweight technical indicators."""
import math
import os
import time
import threading
from api_client import get_json
from ohlcv_integrity import normalize_ohlcv_rows

_cache = {}
_fetch_lock = threading.Lock()
_last_fetch = 0.0
_last_prune = 0.0
MIN_FETCH_INTERVAL = float(__import__('os').getenv('TROJAN_OHLCV_MIN_INTERVAL', '7.2'))
TIMEFRAME = "minute"
AGGREGATE = 1
CANDLE_LIMIT = 120


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def calculate_indicators(candles):
    rows, _quality = normalize_ohlcv_rows(candles)
    if len(rows) < 22:
        return {"ready": False, "candle_count": len(rows), "reason": "Need at least 22 valid candles"}
    closes = [row["close"] for row in rows]
    volumes = [row["volume"] for row in rows]

    def ema(values, period):
        alpha = 2 / (period + 1)
        result = values[0]
        for value in values[1:]:
            result = alpha * value + (1 - alpha) * result
        return result

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(value, 0) for value in changes[-14:]]
    losses = [max(-value, 0) for value in changes[-14:]]
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14
    if avg_loss == 0:
        rsi = 100.0 if avg_gain > 0 else 50.0
    else:
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))

    ema9 = ema(closes[-60:], 9)
    ema21 = ema(closes[-100:], 21)
    previous_high = max(row["high"] for row in rows[-21:-1])
    previous_low = min(row["low"] for row in rows[-21:-1])
    volume_base = sum(volumes[-21:-1]) / 20
    volume_spike = volumes[-1] / volume_base if volume_base > 0 else None
    current_close = closes[-1]

    # Momentum and volatility measurements used by the configurable rule engine.
    def ema_series(values, period):
        alpha = 2 / (period + 1)
        output = []
        value = values[0]
        for item in values:
            value = alpha * item + (1 - alpha) * value
            output.append(value)
        return output

    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    macd_signal = ema_series(macd_line, 9)
    macd_histogram = macd_line[-1] - macd_signal[-1]
    window = closes[-20:]
    mean20 = sum(window) / len(window)
    variance20 = sum((value - mean20) ** 2 for value in window) / len(window)
    std20 = math.sqrt(variance20)
    true_ranges = []
    for index in range(1, len(rows)):
        previous_close = closes[index - 1]
        true_ranges.append(max(
            rows[index]["high"] - rows[index]["low"],
            abs(rows[index]["high"] - previous_close),
            abs(rows[index]["low"] - previous_close)
        ))
    atr14 = sum(true_ranges[-14:]) / min(14, len(true_ranges))
    stochastic_low = min(row["low"] for row in rows[-14:])
    stochastic_high = max(row["high"] for row in rows[-14:])
    stochastic_k = (
        (current_close - stochastic_low) / (stochastic_high - stochastic_low) * 100
        if stochastic_high > stochastic_low else 50.0
    )
    typical_prices = [
        (row["high"] + row["low"] + row["close"]) / 3 for row in rows
    ]
    total_volume = sum(volumes)
    vwap = (
        sum(price * volume for price, volume in zip(typical_prices, volumes)) / total_volume
        if total_volume > 0 else None
    )
    obv = 0.0
    for index in range(1, len(closes)):
        if closes[index] > closes[index - 1]:
            obv += volumes[index]
        elif closes[index] < closes[index - 1]:
            obv -= volumes[index]

    last_candle = rows[-1]
    candle_body_pct = (last_candle["close"] - last_candle["open"]) / last_candle["open"] * 100
    candle_range_pct = (last_candle["high"] - last_candle["low"]) / last_candle["open"] * 100
    consecutive_green = 0
    consecutive_red = 0
    for candle in reversed(rows):
        if candle["close"] > candle["open"] and consecutive_red == 0:
            consecutive_green += 1
        elif candle["close"] < candle["open"] and consecutive_green == 0:
            consecutive_red += 1
        else:
            break

    return {
        "ready": True,
        "candle_count": len(rows),
        "timestamp": rows[-1]["timestamp"],
        "close": current_close,
        "rsi_14": round(rsi, 3),
        "ema_9": ema9,
        "ema_21": ema21,
        "ema_cross_bullish": ema9 > ema21,
        "breakout_20": current_close > previous_high,
        "breakdown_20": current_close < previous_low,
        "previous_high_20": previous_high,
        "previous_low_20": previous_low,
        "volume_spike_ratio": round(volume_spike, 3) if volume_spike is not None else None,
        "volume_spike": volume_spike is not None and volume_spike >= 2.0,
        "candle_body_pct": round(candle_body_pct, 5),
        "candle_range_pct": round(candle_range_pct, 5),
        "consecutive_green_candles": consecutive_green,
        "consecutive_red_candles": consecutive_red,
        "macd": round(macd_line[-1], 12),
        "macd_signal": round(macd_signal[-1], 12),
        "macd_histogram": round(macd_histogram, 12),
        "macd_bullish": macd_histogram > 0,
        "bollinger_middle": mean20,
        "bollinger_upper": mean20 + 2 * std20,
        "bollinger_lower": mean20 - 2 * std20,
        "bollinger_percent_b": (
            (current_close - (mean20 - 2 * std20)) / (4 * std20)
            if std20 > 0 else 0.5
        ),
        "atr_14": atr14,
        "atr_percent": atr14 / current_close * 100,
        "stochastic_k": round(stochastic_k, 3),
        "vwap": vwap,
        "above_vwap": current_close > vwap if vwap is not None else None,
        "obv": obv,
        "trend": "bullish" if ema9 > ema21 else "bearish",
        "source": "geckoterminal_ohlcv"
    }



def aggregate_candles(candles, interval_seconds):
    """Aggregate 1m OHLCV rows into a larger timeframe without look-ahead."""
    interval_seconds = int(interval_seconds)
    if interval_seconds < 60 or interval_seconds % 60:
        raise ValueError("Timeframe must be a whole number of minutes")
    buckets = {}
    for item in _normalize(candles):
        timestamp = int(item["timestamp"])
        bucket = timestamp - (timestamp % interval_seconds)
        current = buckets.get(bucket)
        if current is None:
            buckets[bucket] = {
                "timestamp": bucket, "open": item["open"],
                "high": item["high"], "low": item["low"],
                "close": item["close"], "volume": item["volume"]
            }
        else:
            current["high"] = max(current["high"], item["high"])
            current["low"] = min(current["low"], item["low"])
            current["close"] = item["close"]
            current["volume"] += item["volume"]
    return [
        [row["timestamp"], row["open"], row["high"], row["low"],
         row["close"], row["volume"]]
        for _, row in sorted(buckets.items())
    ]


TIMEFRAMES = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400
}


def calculate_multi_timeframe_indicators(candles):
    """Calculate seven timeframe views from the same ordered 1m source candles.

    Higher timeframes remain explicitly not-ready until enough complete
    aggregate candles exist; no synthetic history or forward filling is used.
    """
    normalized, quality = normalize_ohlcv_rows(candles)
    arrays = [
        [row["timestamp"], row["open"], row["high"], row["low"], row["close"], row["volume"]]
        for row in normalized
    ]
    frames = {}
    ready_trends = []
    for name, seconds in TIMEFRAMES.items():
        aggregated = arrays if name == "1m" else aggregate_candles(arrays, seconds)
        result = calculate_indicators(aggregated)
        result["timeframe"] = name
        if result.get("ready") and not quality["ready"]:
            result["ready"] = False
            result["reason"] = quality["reason"]
        frames[name] = result
        if result.get("ready"):
            ready_trends.append(result.get("trend"))
    bullish = sum(1 for trend in ready_trends if trend == "bullish")
    bearish = sum(1 for trend in ready_trends if trend == "bearish")
    consensus = (
        "bullish" if bullish > bearish else
        "bearish" if bearish > bullish else
        "mixed" if ready_trends else "unavailable"
    )
    primary = frames["1m"]
    return {
        **primary,
        "timeframes": frames,
        "timeframe_ready_count": len(ready_trends),
        "timeframe_count": len(TIMEFRAMES),
        "trend_consensus": consensus,
        "bullish_timeframes": bullish,
        "bearish_timeframes": bearish,
        "source": "geckoterminal_ohlcv_multi_timeframe",
        "ohlcv_quality": quality
    }


def fetch_pool_indicators(pool_address, force=False):
    global _last_fetch, _last_prune
    if not pool_address:
        return {"ready": False, "reason": "Pool address unavailable"}
    key = str(pool_address)
    cached = _cache.get(key)
    if not force and cached and time.time() - cached["time"] < 25:
        return dict(cached["data"])
    if not _fetch_lock.acquire(blocking=False):
        return {"ready": False, "pool_address": key, "reason": "OHLCV request already in progress"}
    if time.time() - _last_fetch < MIN_FETCH_INTERVAL:
        _fetch_lock.release()
        return {"ready": False, "pool_address": key, "reason": "OHLCV API cooldown"}
    _last_fetch = time.time()
    url = (
        "https://api.geckoterminal.com/api/v2/networks/solana/pools/"
        + key + "/ohlcv/" + TIMEFRAME
    )
    try:
        payload = get_json(
            url,
            params={
                "aggregate": AGGREGATE,
                "limit": CANDLE_LIMIT,
                "currency": "usd",
                "token": "base"
            },
            headers={"Accept": "application/json;version=20230203"},
            timeout=10,
            cache_ttl=20,
            max_retries=1
        )
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        attributes = data.get("attributes", {}) if isinstance(data, dict) else {}
        raw = attributes.get("ohlcv_list", [])
        normalized, _quality = normalize_ohlcv_rows(raw)
        indicators = calculate_multi_timeframe_indicators(normalized)
        indicators["pool_address"] = key
        _cache[key] = {"time": time.time(), "data": indicators}
        if normalized:
            try:
                import database
                database.save_market_candles(key, "1m", normalized)
                if time.time() - _last_prune >= 3600:
                    try:
                        removed = database.prune_market_candles()
                        if removed:
                            print("[TROJAN V2] OHLCV RETENTION PRUNED:", removed, flush=True)
                    except Exception as error:
                        print("[TROJAN V2] OHLCV RETENTION WARNING:", type(error).__name__, flush=True)
                    _last_prune = time.time()
            except Exception as error:
                print("[TROJAN V2] OHLCV STORAGE WARNING:", type(error).__name__, flush=True)
        return indicators
    except Exception as error:
        return {"ready": False, "pool_address": key, "reason": str(error)[:220]}
    finally:
        if _fetch_lock.locked():
            _fetch_lock.release()


def _normalize(raw):
    rows, _quality = normalize_ohlcv_rows(raw)
    return rows


def get_indicator_status():
    ready = sum(1 for item in _cache.values() if item["data"].get("ready"))
    stale = sum(1 for item in _cache.values()
                if (item["data"].get("ohlcv_quality") or {}).get("stale"))
    return {
        "cached_pools": len(_cache), "ready_pools": ready,
        "stale_pools": stale, "timeframe": "1m",
        "max_candle_age_seconds": int(os.getenv("TROJAN_OHLCV_MAX_AGE_SECONDS", "180")),
        "max_gap_seconds": int(os.getenv("TROJAN_OHLCV_MAX_GAP_SECONDS", "300"))
    }
