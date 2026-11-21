import time


def get_number(value):
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0


def analyze_token(token):

    score = 0
    reasons = []

    liquidity = get_number(
        token.get("liquidity", {}).get("usd")
    )

    volume = get_number(
        token.get("volume", {}).get("h24")
    )

    price_h1 = get_number(
        token.get("priceChange", {}).get("h1")
    )

    price_h24 = get_number(
        token.get("priceChange", {}).get("h24")
    )

    transactions = token.get("txns", {})
    hourly = transactions.get("h1", {})

    buys = get_number(hourly.get("buys"))
    sells = get_number(hourly.get("sells"))

    total_transactions = buys + sells

    # SAFETY CHECK 1: LIQUIDITY

    if liquidity < 10000:
        return {
            "score": 0,
            "decision": "REJECT",
            "reasons": ["Liquidity below 10000 USD"]
        }

    # SAFETY CHECK 2: TOKEN AGE

    pair_created = get_number(
        token.get("pairCreatedAt")
    )

    if pair_created > 0:

        age_minutes = (
            time.time() * 1000 - pair_created
        ) / 60000

        if age_minutes < 10:
            return {
                "score": 0,
                "decision": "REJECT",
                "reasons": ["Trading pair too new"]
            }

    # SAFETY CHECK 3: EXTREME PRICE PUMP

    if price_h1 > 50:
        return {
            "score": 0,
            "decision": "REJECT",
            "reasons": ["Extreme hourly price increase"]
        }

    # SAFETY CHECK 4: SUSPICIOUS VOLUME

    if liquidity > 0 and volume / liquidity > 10:
        return {
            "score": 0,
            "decision": "REJECT",
            "reasons": ["Suspicious volume to liquidity ratio"]
        }

    # SAFETY CHECK 5: SELLING PRESSURE

    if total_transactions >= 10 and sells > buys * 1.5:
        return {
            "score": 0,
            "decision": "REJECT",
            "reasons": ["Excessive selling pressure"]
        }

    # SCORING: LIQUIDITY

    if liquidity >= 50000:
        score += 30
        reasons.append("Excellent liquidity")

    elif liquidity >= 25000:
        score += 25
        reasons.append("Good liquidity")

    else:
        score += 15
        reasons.append("Acceptable liquidity")

    # SCORING: VOLUME

    if volume >= 50000:
        score += 25
        reasons.append("Very high volume")

    elif volume >= 10000:
        score += 20
        reasons.append("Good trading volume")

    elif volume >= 5000:
        score += 10
        reasons.append("Moderate volume")

    # SCORING: BUYING PRESSURE

    if total_transactions >= 20:
        score += 10
        reasons.append("Active trading")

    elif total_transactions >= 5:
        score += 5

    if total_transactions > 0:

        buy_ratio = buys / total_transactions

        if buy_ratio >= 0.65:
            score += 15
            reasons.append("Strong buying pressure")

        elif buy_ratio >= 0.52:
            score += 10
            reasons.append("More buyers than sellers")

    # SCORING: MOMENTUM

    if 0 < price_h1 <= 15:
        score += 10
        reasons.append("Positive short-term momentum")

    elif price_h1 > 15:
        score += 5
        reasons.append("Strong short-term movement")

    elif price_h1 < 0:
        score -= 5
        reasons.append("Negative short-term movement")

    if price_h24 > 0:
        score += 5
        reasons.append("Positive daily movement")

    score = max(0, min(100, score))

    decision = "WATCH" if score >= 50 else "REJECT"

    return {
        "score": score,
        "decision": decision,
        "reasons": reasons
    }


if __name__ == "__main__":
    print("ADVANCED TOKEN ANALYZER 2.0 READY")
