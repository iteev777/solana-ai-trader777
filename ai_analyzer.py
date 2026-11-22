import time
import math


# ==========================================
# TROJAN V2 - DIP REVERSAL / MOMENTUM ANALYZER
# ==========================================

MIN_LIQUIDITY_BY_STRATEGY = {
    "main": 8000,
    "sniper": 2500
}
MIN_TOKEN_AGE_MINUTES = {
    "main": 5,
    "sniper": 0
}
MIN_SCORE = 50
MINIMUM_APPROVAL_SCORE = {
    "main": 70,
    "sniper": 75
}

MAX_HOURLY_GAIN = 35
MAX_FIVE_MINUTE_GAIN = 8
MAX_VOLUME_LIQUIDITY_RATIO = 15


def get_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def get_nested_number(data, *keys):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return get_number(current)


def reject(reason):
    return {
        "score": 0,
        "decision": "REJECT",
        "reasons": [reason],
        "source_count": 0
    }


def watch(reason):
    return {
        "score": 0,
        "decision": "WATCH",
        "reasons": [reason],
        "source_count": 0
    }


def get_discovery_sources(token):
    sources = token.get("discovery_sources", [])
    if not isinstance(sources, list):
        return []
    return sorted({
        source.strip().lower()
        for source in sources
        if isinstance(source, str) and source.strip()
    })


def analyze_token(token, strategy="main"):
    if strategy not in MIN_LIQUIDITY_BY_STRATEGY:
        return reject("Unknown strategy")
    if not isinstance(token, dict):
        return reject("Invalid token data")

    reasons = []
    score = 0

    liquidity = get_nested_number(token, "liquidity", "usd")
    volume_h24 = get_nested_number(token, "volume", "h24")
    volume_m5 = get_nested_number(token, "volume", "m5")
    price_m5 = get_nested_number(token, "priceChange", "m5")
    price_h1 = get_nested_number(token, "priceChange", "h1")
    price_h24 = get_nested_number(token, "priceChange", "h24")

    txns_m5_buys = get_nested_number(token, "txns", "m5", "buys")
    txns_m5_sells = get_nested_number(token, "txns", "m5", "sells")
    txns_h1_buys = get_nested_number(token, "txns", "h1", "buys")
    txns_h1_sells = get_nested_number(token, "txns", "h1", "sells")

    if liquidity is None or volume_h24 is None:
        return watch("Waiting for liquidity or volume data")
    if liquidity < MIN_LIQUIDITY_BY_STRATEGY[strategy]:
        return watch("Liquidity below strategy minimum; keep watching")
    if volume_h24 < 0:
        return reject("Invalid volume data")

    # Prefer the 5-minute tape. Fall back to hourly transactions only
    # when the provider has not published short-window fields yet.
    if txns_m5_buys is not None and txns_m5_sells is not None:
        buys, sells = txns_m5_buys, txns_m5_sells
        window = "5m"
        minimum_transactions = 3 if strategy == "sniper" else 4
    else:
        buys, sells = txns_h1_buys, txns_h1_sells
        window = "1h"
        minimum_transactions = 5

    if buys is None or sells is None or buys < 0 or sells < 0:
        return watch("Waiting for valid recent buy/sell data")

    total_transactions = buys + sells
    if total_transactions < minimum_transactions:
        return watch("Waiting for more recent transactions")

    if price_m5 is None:
        # Without a short-window change we cannot verify a local rebound.
        return watch("Waiting for a 5-minute reversal signal")
    if price_h1 is None:
        return watch("Waiting for hourly price data")
    if price_h24 is None:
        return watch("Waiting for daily price data")

    if price_m5 <= 0:
        return watch("Waiting for buyers to turn the 5-minute candle positive")
    if price_m5 > MAX_FIVE_MINUTE_GAIN:
        return reject("Short-term move already extended; avoid chasing")
    if price_h1 < -25:
        return reject("Severe hourly downtrend")
    if price_h1 > MAX_HOURLY_GAIN:
        return reject("Hourly move too extended")
    if price_h24 < -70:
        return reject("Extreme daily collapse")
    if buys <= sells:
        return watch("Waiting for buyers to take control")

    volume_ratio = volume_h24 / max(liquidity, 1)
    if volume_ratio > MAX_VOLUME_LIQUIDITY_RATIO:
        return reject("Abnormal volume/liquidity ratio")

    pair_created = get_number(token.get("pairCreatedAt"))
    if pair_created is not None and pair_created > 0:
        current_ms = time.time() * 1000
        if pair_created > current_ms + 300000:
            return reject("Pair creation timestamp is in the future")
        age_minutes = (current_ms - pair_created) / 60000
        if age_minutes < MIN_TOKEN_AGE_MINUTES[strategy]:
            return watch("New pair; waiting until minimum age is reached")

    buy_ratio = buys / total_transactions
    sources = get_discovery_sources(token)
    source_count = len(sources)

    # Liquidity: 25 points.
    if liquidity >= 100000:
        score += 25
        reasons.append("Deep liquidity")
    elif liquidity >= 50000:
        score += 22
        reasons.append("Strong liquidity")
    elif liquidity >= 20000:
        score += 18
        reasons.append("Healthy liquidity")
    else:
        score += 12
        reasons.append("Minimum liquidity passed")

    # Recent activity: 15 points.
    recent_volume = volume_m5 if volume_m5 is not None else volume_h24
    if recent_volume >= 5000:
        score += 15
        reasons.append("Strong recent volume")
    elif recent_volume >= 1000:
        score += 11
        reasons.append("Good recent volume")
    elif recent_volume >= 100:
        score += 7
        reasons.append("Recent volume present")
    else:
        score += 3
        reasons.append("Thin recent volume")

    # Buy pressure: 25 points.
    if buy_ratio >= 0.75:
        score += 25
        reasons.append("Aggressive buyer dominance (" + window + ")")
    elif buy_ratio >= 0.65:
        score += 21
        reasons.append("Strong buyer dominance (" + window + ")")
    elif buy_ratio >= 0.58:
        score += 16
        reasons.append("Buyers in control (" + window + ")")
    elif buy_ratio >= 0.52:
        score += 10
        reasons.append("Slight positive buy pressure")
    else:
        return reject("Buy pressure too weak")

    # Reversal: prefer a small positive 5m turn while hourly movement
    # is still modest, rather than buying a vertical candle.
    if 0 < price_m5 <= 2:
        score += 20
        reasons.append("Early 5-minute rebound")
    elif 2 < price_m5 <= 5:
        score += 15
        reasons.append("Confirmed short-term rebound")
    else:
        score += 8
        reasons.append("Late but valid rebound")

    if -12 <= price_h1 <= 8:
        score += 10
        reasons.append("Hourly structure not overextended")
    elif price_h1 <= 15:
        score += 5
        reasons.append("Moderate hourly momentum")

    if source_count >= 3:
        score += 5
        reasons.append("Multi-source discovery confirmation")
    elif source_count == 2:
        score += 3
        reasons.append("Two discovery sources agree")

    score = max(0, min(100, round(score)))
    decision = (
        "APPROVE"
        if score >= MINIMUM_APPROVAL_SCORE[strategy]
        else "WATCH" if score >= MIN_SCORE else "REJECT"
    )

    reasons.append(
        "Dip-reversal candidate" if decision == "APPROVE"
        else "Waiting for a stronger entry" if decision == "WATCH"
        else "Entry quality too low"
    )

    return {
        "score": score,
        "decision": decision,
        "reasons": reasons,
        "source_count": source_count,
        "buy_ratio": round(buy_ratio, 3),
        "price_change_m5": price_m5,
        "price_change_h1": price_h1,
        "transaction_window": window
    }


if __name__ == "__main__":
    print("TROJAN V2 DIP-REVERSAL ANALYZER READY")
    print("STRATEGIES:", MINIMUM_APPROVAL_SCORE)
