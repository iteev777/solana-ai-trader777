"""Validation and quality metrics for externally sourced OHLCV candle rows."""
import math
import time


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_ohlcv_rows(raw, now=None):
    """Accept Gecko-style arrays or candle dictionaries; reject malformed bars."""
    current_time = time.time() if now is None else float(now)
    source = list(raw or [])
    by_timestamp = {}
    invalid = 0
    duplicates = 0
    for item in source:
        if isinstance(item, dict):
            values = [item.get(key) for key in
                      ("timestamp", "open", "high", "low", "close", "volume")]
        elif isinstance(item, (list, tuple)) and len(item) >= 6:
            values = list(item[:6])
        else:
            invalid += 1
            continue
        parsed = [_finite(value) for value in values]
        if any(value is None for value in parsed):
            invalid += 1
            continue
        timestamp, open_price, high, low, close, volume = parsed
        timestamp = int(timestamp)
        if (timestamp <= 0 or timestamp > current_time + 60
                or min(open_price, high, low, close) <= 0 or volume < 0
                or high < low or high < max(open_price, close)
                or low > min(open_price, close)):
            invalid += 1
            continue
        row = {
            "timestamp": timestamp,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
        if timestamp in by_timestamp:
            duplicates += 1
        by_timestamp[timestamp] = row

    rows = [by_timestamp[key] for key in sorted(by_timestamp)]
    gaps = []
    for previous, current in zip(rows, rows[1:]):
        delta = current["timestamp"] - previous["timestamp"]
        if delta > 60:
            gaps.append(delta)
    latest_age = max(0.0, current_time - rows[-1]["timestamp"]) if rows else None
    largest_gap = max(gaps) if gaps else 0
    max_age = 180.0
    max_gap = 300
    quality = {
        "input_count": len(source),
        "valid_count": len(rows),
        "invalid_count": invalid,
        "duplicate_count": duplicates,
        "gap_count": sum(max(0, int(gap // 60) - 1) for gap in gaps),
        "largest_gap_seconds": largest_gap,
        "latest_age_seconds": round(latest_age, 2) if latest_age is not None else None,
        "stale": latest_age is None or latest_age > max_age,
        "discontinuous": largest_gap > max_gap,
    }
    quality["ready"] = bool(rows) and not quality["stale"] and not quality["discontinuous"]
    if not rows:
        quality["reason"] = "No valid OHLCV candles"
    elif quality["stale"]:
        quality["reason"] = "Latest OHLCV candle is stale"
    elif quality["discontinuous"]:
        quality["reason"] = "OHLCV history contains a gap larger than five minutes"
    elif invalid:
        quality["reason"] = "Invalid OHLCV rows were discarded"
    else:
        quality["reason"] = None
    return rows, quality
