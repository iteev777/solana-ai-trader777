# TROJAN V2 - STRATEGY ENGINE

from ai_analyzer import analyze_token
from risk_manager import check_trade_risk

TRADE_SIZE = 5.00

MINIMUM_SCORES = {
    "main": 70,
    "sniper": 75
}

VALID_STRATEGIES = ("main", "sniper")


def evaluate_trade(
    token,
    balance,
    open_positions,
    daily_loss,
    strategy="main"
):
    if strategy not in VALID_STRATEGIES:
        return {
            "action": "REJECT",
            "score": 0,
            "reason": "Unknown strategy"
        }

    if not isinstance(token, dict):
        return {
            "action": "REJECT",
            "score": 0,
            "reason": "Invalid token data"
        }

    try:
        analysis = analyze_token(token)
        score = float(analysis.get("score", 0))
    except Exception as error:
        print("TROJAN V2 STRATEGY ERROR:", repr(error))
        return {
            "action": "REJECT",
            "score": 0,
            "reason": "AI analysis failed"
        }

    if analysis.get("decision") != "APPROVE":
        return {
            "action": "WAIT",
            "score": score,
            "reason": "AI did not approve token"
        }

    if score < MINIMUM_SCORES[strategy]:
        return {
            "action": "WAIT",
            "score": score,
            "reason": "Score below strategy threshold"
        }

    risk_result = check_trade_risk(
        balance,
        open_positions,
        daily_loss,
        TRADE_SIZE,
        strategy=strategy
    )

    if isinstance(risk_result, tuple):
        allowed, reason = risk_result
    else:
        allowed = bool(risk_result)
        reason = "Risk check rejected trade"

    if not allowed:
        return {
            "action": "REJECT",
            "score": score,
            "reason": reason
        }

    return {
        "action": "BUY_SIGNAL",
        "score": score,
        "strategy": strategy,
        "trade_size": TRADE_SIZE,
        "reasons": analysis.get("reasons", [])
    }


if __name__ == "__main__":
    print("TROJAN V2 STRATEGY ENGINE READY")
