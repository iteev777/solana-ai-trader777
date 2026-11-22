import time
import math


# ==========================================
# TROJAN V2 - AI TOKEN ANALYZER
# ==========================================

MIN_LIQUIDITY = 10000
MIN_LIQUIDITY_BY_STRATEGY = {
    "main": 10000,
    "sniper": 2500
}
MIN_TOKEN_AGE_MINUTES = {
    "main": 10,
    "sniper": 0
}
MIN_TRANSACTIONS_BY_STRATEGY = {
    "main": 5,
    "sniper": 5
}
MIN_SCORE = 50

MAX_HOURLY_GAIN = 50
MAX_VOLUME_LIQUIDITY_RATIO = 10

MIN_TRANSACTIONS = 5


# ==========================================
# DATA VALIDATION
# ==========================================

def get_number(value):

    try:
        number = float(value)

        if not math.isfinite(number):
            return None

        return number

    except (ValueError, TypeError):
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


def get_discovery_sources(token):

    sources = token.get("discovery_sources", [])

    if not isinstance(sources, list):
        return []

    valid_sources = set()

    for source in sources:

        if isinstance(source, str):

            source = source.strip().lower()

            if source:
                valid_sources.add(source)

    return sorted(valid_sources)


# ==========================================
# MAIN ANALYSIS
# ==========================================

def analyze_token(token, strategy="main"):

    if strategy not in MIN_LIQUIDITY_BY_STRATEGY:
        return reject("Unknown strategy")

    if not isinstance(token, dict):
        return reject("Invalid token data")

    score = 0
    reasons = []

    # ======================================
    # MARKET DATA
    # ======================================

    liquidity = get_nested_number(
        token, "liquidity", "usd"
    )

    volume = get_nested_number(
        token, "volume", "h24"
    )

    price_h1 = get_nested_number(
        token, "priceChange", "h1"
    )

    price_h24 = get_nested_number(
        token, "priceChange", "h24"
    )

    buys = get_nested_number(
        token, "txns", "h1", "buys"
    )

    sells = get_nested_number(
        token, "txns", "h1", "sells"
    )

    if liquidity is None or volume is None:
        return reject("Missing liquidity or volume data")

    if liquidity <= 0 or volume < 0:
        return reject("Invalid market data")

    minimum_liquidity = MIN_LIQUIDITY_BY_STRATEGY[strategy]

    if liquidity < minimum_liquidity:
        return reject("Liquidity below minimum requirement")

    if price_h1 is None:
        return reject("Missing hourly price data")

    if price_h24 is None:
        return reject("Missing daily price data")

    # ======================================
    # TRANSACTION VALIDATION
    # ======================================

    if buys is None or sells is None:
        return reject("Missing transaction data")

    if buys < 0 or sells < 0:
        return reject("Invalid transaction data")

    total_transactions = buys + sells

    minimum_transactions = MIN_TRANSACTIONS_BY_STRATEGY[strategy]

    if total_transactions < minimum_transactions:
        return reject("Insufficient hourly transaction activity")

    # ======================================
    # DISCOVERY SOURCES
    # ======================================

    sources = get_discovery_sources(token)
    source_count = len(sources)

    # ======================================
    # TOKEN AGE VALIDATION
    # ======================================

    if "pairCreatedAt" in token:

        pair_created = get_number(
            token.get("pairCreatedAt")
        )

        if pair_created is None or pair_created <= 0:
            return reject("Invalid token creation timestamp")

        current_time_ms = time.time() * 1000

        if pair_created > current_time_ms + 300000:
            return reject("Token creation timestamp is in the future")

        age_minutes = (
            current_time_ms - pair_created
        ) / 60000

        minimum_age = MIN_TOKEN_AGE_MINUTES[strategy]

        if age_minutes < minimum_age:
            return reject("Trading pair too new")

    # ======================================
    # SAFETY CHECKS
    # ======================================

    if price_h1 > MAX_HOURLY_GAIN:
        return reject("Extreme hourly price increase")

    volume_ratio = volume / liquidity

    if volume_ratio > MAX_VOLUME_LIQUIDITY_RATIO:
        return reject("Suspicious volume to liquidity ratio")

    if total_transactions >= 10 and sells > buys * 1.5:
        return reject("Excessive selling pressure")

    # ======================================
    # LIQUIDITY SCORE: MAX 25
    # ======================================

    if liquidity >= 100000:
        score += 25
        reasons.append("Very strong liquidity")

    elif liquidity >= 50000:
        score += 22
        reasons.append("Strong liquidity")

    elif liquidity >= 25000:
        score += 18
        reasons.append("Good liquidity")

    else:
        score += 12
        reasons.append("Acceptable liquidity")

    # ======================================
    # VOLUME SCORE: MAX 20
    # ======================================

    if volume >= 100000:
        score += 20
        reasons.append("Very high trading volume")

    elif volume >= 50000:
        score += 17
        reasons.append("High trading volume")

    elif volume >= 10000:
        score += 13
        reasons.append("Good trading volume")

    elif volume >= 5000:
        score += 8
        reasons.append("Moderate trading volume")

    else:
        score += 3
        reasons.append("Low trading volume")

    # ======================================
    # BUYING PRESSURE: MAX 20
    # ======================================

    if total_transactions >= 20:

        score += 5
        reasons.append("Active trading")

    else:

        score += 3
        reasons.append("Some trading activity")

    buy_ratio = buys / total_transactions

    if buy_ratio >= 0.70:
        score += 15
        reasons.append("Very strong buying pressure")

    elif buy_ratio >= 0.60:
        score += 12
        reasons.append("Strong buying pressure")

    elif buy_ratio >= 0.52:
        score += 8
        reasons.append("Positive buying pressure")

    elif buy_ratio <= 0.35:
        score -= 10
        reasons.append("Strong selling pressure")

    elif buy_ratio < 0.45:
        score -= 5
        reasons.append("Selling pressure detected")

    # ======================================
    # SHORT-TERM MOMENTUM: MAX 15
    # ======================================

    if 0 < price_h1 <= 10:
        score += 15
        reasons.append("Healthy positive momentum")

    elif 10 < price_h1 <= 20:
        score += 8
        reasons.append("Strong short-term movement")

    elif price_h1 < -10:
        score -= 10
        reasons.append("Significant negative momentum")

    elif price_h1 < 0:
        score -= 5
        reasons.append("Negative short-term momentum")

    elif price_h1 == 0:
        reasons.append("No hourly price movement")

    # ======================================
    # DAILY MOMENTUM: MAX 10
    # ======================================

    if 0 < price_h24 <= 30:
        score += 10
        reasons.append("Positive daily momentum")

    elif price_h24 > 30:
        score += 5
        reasons.append("Strong daily price increase")

    elif price_h24 < -20:
        score -= 10
        reasons.append("Heavy daily price decline")

    elif price_h24 < 0:
        score -= 5
        reasons.append("Negative daily momentum")

    # ======================================
    # DISCOVERY SOURCE SCORE: MAX 10
    # ======================================

    if source_count >= 4:
        score += 10
        reasons.append("Detected by multiple discovery sources")

    elif source_count == 3:
        score += 7
        reasons.append("Detected by three discovery sources")

    elif source_count == 2:
        score += 4
        reasons.append("Detected by two discovery sources")

    else:
        reasons.append("Limited discovery confirmation")

    # ======================================
    # FINAL SCORE
    # ======================================

    score = max(0, min(100, round(score)))

    if score >= 70:

        decision = "APPROVE"
        reasons.append("High quality candidate")

    elif score >= MIN_SCORE:

        decision = "WATCH"
        reasons.append("Candidate requires further monitoring")

    else:

        decision = "REJECT"
        reasons.append("Insufficient quality score")

    return {
        "score": score,
        "decision": decision,
        "reasons": reasons,
        "source_count": source_count
    }


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    print("================================")
    print("TROJAN V2 AI ANALYZER")
    print("================================")

    print("MINIMUM LIQUIDITY:", MIN_LIQUIDITY)
    print("MINIMUM SCORE:", MIN_SCORE)
    print("MAX HOURLY GAIN:", MAX_HOURLY_GAIN)
    print("MINIMUM TRANSACTIONS:", MIN_TRANSACTIONS)

    print("AI ANALYZER READY")
