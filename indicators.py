"""GeckoTerminal OHLCV retrieval and lightweight technical indicators."""
import math
import os
import time
import threading
import os
from collections import OrderedDict
from api_client import get_json
from ohlcv_integrity import normalize_ohlcv_rows

MAX_CACHE_ENTRIES = 100
CACHE_SECONDS = 60.0
_cache = OrderedDict()
_cache_lock = threading.RLock()
_fetch_lock = threading.Lock()
_last_fetch = 0.0
_last_prune = 0.0


def _minimum_fetch_interval():
    try:
        value = float(os.getenv("TROJAN_OHLCV_MIN_INTERVAL", "7.2"))
    except (TypeError, ValueError, OverflowError):
        return 7.2
    if not math.isfinite(value):
        return 7.2
    # GeckoTerminal's public API is approximately 10 calls/minute; retain
    # headroom instead of allowing configuration to exceed that shared budget.
    return max(6.0, min(value, 60.0))


MIN_FETCH_INTERVAL = _minimum_fetch_interval()
TIMEFRAME = "minute"
AGGREGATE = 1
CANDLE_LIMIT = 1000


def _cache_get(key, now=None):
    clock = time.monotonic() if now is None else now
    with _cache_lock:
        cached = _cache.get(key)
        if cached is None:
            return None
        if clock - cached["time"] >= CACHE_SECONDS:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return dict(cached["data"])


def _cache_put(key, data, now=None):
    clock = time.monotonic() if now is None else now
    with _cache_lock:
        _cache[key] = {"time": clock, "data": dict(data)}
        _cache.move_to_end(key)
        while len(_cache) > MAX_CACHE_ENTRIES:
            _cache.popitem(last=False)


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def ema_series(values, period):
    """SMA-seeded EMA series; oldest input first."""
    if period <= 0 or len(values) < period:
        return []
    alpha = 2.0 / (period + 1.0)
    average = sum(values[:period]) / period
    output = [average]
    for value in values[period:]:
        average = alpha * value + (1.0 - alpha) * average
        output.append(average)
    return output


def wilder_average(values, period):
    """Wilder smoothing seeded by the first-period simple average."""
    if period <= 0 or len(values) < period:
        return None
    average = sum(values[:period]) / period
    for value in values[period:]:
        average = (average * (period - 1) + value) / period
    return average


def calculate_rsi(closes, period=14):
    """Wilder RSI with an SMA seed, matching the conventional RSI definition."""
    if period <= 0 or len(closes) < period + 1:
        return None
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = wilder_average(gains, period)
    average_loss = wilder_average(losses, period)
    if average_gain is None or average_loss is None:
        return None
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    if average_gain == 0:
        return 0.0
    return 100.0 - (100.0 / (1.0 + average_gain / average_loss))


def calculate_indicators(candles):
    # Drop malformed OHLCV rows instead of letting inconsistent candles distort
    # technical confirmation. Input order is normalized before any calculations.
    by_timestamp = {}
    for item in candles or []:
        if not isinstance(item, (list, tuple)) or len(item) < 6:
            continue
        ts = _finite(item[0])
        o, h, low, close, volume = (_finite(x) for x in item[1:6])
        if None in (ts, o, h, low, close, volume):
            continue
        if min(o, h, low, close) <= 0 or volume < 0:
            continue
        if h < max(o, close, low) or low > min(o, close, h):
            continue
        by_timestamp[int(ts)] = {
            "timestamp": int(ts), "open": o, "high": h,
            "low": low, "close": close, "volume": volume
        }
    rows = [by_timestamp[key] for key in sorted(by_timestamp)]
    if len(rows) < 22:
        return {"ready": False, "candle_count": len(rows), "reason": "Need at least 22 valid candles"}
    closes = [row["close"] for row in rows]
    volumes = [row["volume"] for row in rows]

    ema9_series = ema_series(closes, 9)
    ema21_series = ema_series(closes, 21)
    ema9 = ema9_series[-1]
    ema21 = ema21_series[-1]
    rsi = calculate_rsi(closes, 14)

    previous_high = max(row["high"] for row in rows[-21:-1])
    previous_low = min(row["low"] for row in rows[-21:-1])
    volume_base = sum(volumes[-21:-1]) / 20
    volume_spike = volumes[-1] / volume_base if volume_base > 0 else None
    current_close = closes[-1]

    # Standard MACD(12,26,9): SMA-seeded EMA series are aligned at the
    # 26-period start before the signal EMA is calculated.
    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    macd_histogram = macd_value = macd_signal_value = None
    macd_bullish = None
    if ema26:
        aligned_ema12 = ema12[-len(ema26):]
        macd_line = [fast - slow for fast, slow in zip(aligned_ema12, ema26)]
        signal_line = ema_series(macd_line, 9)
        if signal_line:
            macd_value = macd_line[-1]
            macd_signal_value = signal_line[-1]
            macd_histogram = macd_value - macd_signal_value
            macd_bullish = macd_histogram > 0

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
    atr14 = wilder_average(true_ranges, 14)
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
        "rsi_14": round(rsi, 3) if rsi is not None else None,
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
        "macd": round(macd_value, 12) if macd_value is not None else None,
        "macd_signal": round(macd_signal_value, 12) if macd_signal_value is not None else None,
        "macd_histogram": round(macd_histogram, 12) if macd_histogram is not None else None,
        "macd_bullish": macd_bullish,
        "bollinger_middle": mean20,
        "bollinger_upper": mean20 + 2 * std20,
        "bollinger_lower": mean20 - 2 * std20,
        "bollinger_percent_b": (
            (current_close - (mean20 - 2 * std20)) / (4 * std20)
            if std20 > 0 else 0.5
        ),
        "atr_14": atr14,
        "atr_percent": atr14 / current_close * 100 if atr14 is not None else None,
        "stochastic_k": round(stochastic_k, 3),
        "vwap": vwap,
        "above_vwap": current_close > vwap if vwap is not None else None,
        "obv": obv,
        "trend": "bullish" if ema9 > ema21 else "bearish",
        "indicator_methods": {
            "ema": "SMA_SEEDED_EXPONENTIAL",
            "rsi": "WILDER",
            "atr": "WILDER",
            "macd": "SMA_SEEDED_EMA_12_26_9",
        },
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
    cached = _cache_get(key)
    if not force and cached is not None:
        return cached
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
        _cache_put(key, indicators)
        if normalized:
            try:
                import database
                database.save_market_candles(key, "1m", normalized)
                now = time.monotonic()
                if now - _last_prune >= 3600:
                    try:
                        removed = database.prune_market_candles()
                        if removed:
                            print("[TROJAN V2] OHLCV RETENTION PRUNED:", removed, flush=True)
                    except Exception as prune_error:
                        print("[TROJAN V2] OHLCV RETENTION WARNING:", type(prune_error).__name__, flush=True)
                    _last_prune = now
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


def _bounded_env_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(maximum, value))


def get_indicator_status():
    with _cache_lock:
        ready = sum(1 for item in _cache.values() if item["data"].get("ready"))
        stale = sum(1 for item in _cache.values()
                    if (item["data"].get("ohlcv_quality") or {}).get("stale"))
        cached_pools = len(_cache)
    return {
        "cached_pools": cached_pools,
        "ready_pools": ready,
        "stale_pools": stale,
        "cache_capacity": MAX_CACHE_ENTRIES,
        "cache_ttl_seconds": CACHE_SECONDS,
        "candles_per_fetch": CANDLE_LIMIT,
        "minimum_fetch_interval_seconds": MIN_FETCH_INTERVAL,
        "retention_days": _bounded_env_int("TROJAN_OHLCV_RETENTION_DAYS", 30, 1, 3650),
        "timeframe": "1m",
        "max_candle_age_seconds": _bounded_env_int("TROJAN_OHLCV_MAX_AGE_SECONDS", 180, 30, 3600),
        "max_gap_seconds": _bounded_env_int("TROJAN_OHLCV_MAX_GAP_SECONDS", 300, 60, 3600)
    }
