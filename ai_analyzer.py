def analyze_token(token):
    score = 0
    reasons = []

    liquidity = token.get("liquidity", {}).get("usd", 0) or 0
    volume = token.get("volume", {}).get("h24", 0) or 0
    price_change = token.get("priceChange", {}).get("h24", 0) or 0

    if liquidity >= 25000:
        score += 30
        reasons.append("Good liquidity")

    if volume >= 10000:
        score += 30
        reasons.append("Active trading")

    if price_change > 0:
        score += 20
        reasons.append("Positive price movement")

    if price_change > 10:
        score += 20
        reasons.append("Strong momentum")

    return {
        "score": score,
        "decision": "WATCH" if score >= 50 else "REJECT",
        "reasons": reasons
    }


if __name__ == "__main__":
    print("AI ANALYZER 1.0 READY")
