from ai_analyzer import analyze_token
from risk_manager import can_open_trade

TRADE_SIZE = 5.00
MINIMUM_SCORE = 70


def evaluate_trade(token, balance, open_positions, daily_loss):

    analysis = analyze_token(token)

    score = analysis["score"]

    if score < MINIMUM_SCORE:
        return {
            "action": "WAIT",
            "score": score,
            "reason": "Score too low"
        }

    allowed = can_open_trade(
        balance,
        open_positions,
        daily_loss,
        TRADE_SIZE
    )

    if not allowed:
        return {
            "action": "REJECT",
            "score": score,
            "reason": "Risk limits reached"
        }

    return {
        "action": "BUY_SIGNAL",
        "score": score,
        "trade_size": TRADE_SIZE,
        "reasons": analysis["reasons"]
    }


if __name__ == "__main__":
    print("STRATEGY ENGINE 1.0 READY")
