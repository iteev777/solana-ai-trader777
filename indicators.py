"""GeckoTerminal OHLCV retrieval and lightweight technical indicators."""
import math
import time
from api_client import get_json

_cache = {}
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
    rows = []
    for item in candles or []:
        if not isinstance(item, (list, tuple)) or len(item) < 6:
            continue
        ts = _finite(item[0])
        o, h, low, close, volume = (_finite(x) for x in item[1:6])
        if None in (ts, o, h, low, close, volume) or min(o, h, low, close) <= 0 or volume < 0:
            continue
        rows.append({"timestamp": int(ts), "open": o, "high": h, "low": low, "close": close, "volume": volume})
    rows.sort(key=lambda x: x["timestamp"])
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
    volume_base = sum(volumes[-21:-1]) / 20
    volume_spike = volumes[-1] / volume_base if volume_base > 0 else None
    current_close = closes[-1]
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
        "previous_high_20": previous_high,
        "volume_spike_ratio": round(volume_spike, 3) if volume_spike is not None else None,
        "volume_spike": volume_spike is not None and volume_spike >= 2.0,
        "trend": "bullish" if ema9 > ema21 else "bearish",
        "source": "geckoterminal_ohlcv"
    }


def fetch_pool_indicators(pool_address, force=False):
    if not pool_address:
        return {"ready": False, "reason": "Pool address unavailable"}
    key = str(pool_address)
    cached = _cache.get(key)
    if not force and cached and time.time() - cached["time"] < 25:
        return dict(cached["data"])
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
        indicators = calculate_indicators(raw)
        indicators["pool_address"] = key
        _cache[key] = {"time": time.time(), "data": indicators}
        if indicators.get("ready"):
            try:
                import database
                database.save_market_candles(key, "1m", [
                    {"timestamp": row["timestamp"], "open": row["open"], "high": row["high"],
                     "low": row["low"], "close": row["close"], "volume": row["volume"]}
                    for row in _normalize(raw)
                ])
            except Exception:
                pass
        return indicators
    except Exception as error:
        return {"ready": False, "pool_address": key, "reason": str(error)[:220]}


def _normalize(raw):
    rows = []
    for item in raw or []:
        if not isinstance(item, (list, tuple)) or len(item) < 6:
            continue
        values = [_finite(x) for x in item[:6]]
        if all(value is not None for value in values):
            rows.append(dict(zip(("timestamp", "open", "high", "low", "close", "volume"), values)))
    return rows


def get_indicator_status():
    ready = sum(1 for item in _cache.values() if item["data"].get("ready"))
    return {"cached_pools": len(_cache), "ready_pools": ready, "timeframe": "1m"}
