# ==========================================
# TROJAN V2 - STRATEGY ENGINE
# ==========================================

from ai_analyzer import analyze_token
from strategy_rules import evaluate_strategy_rules
from risk_manager import check_trade_risk
import paper_trading as paper


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
        analysis = analyze_token(token, strategy=strategy)

        if not isinstance(analysis, dict):
            raise ValueError("Invalid AI analysis")

        score = float(analysis.get("score", 0))

    except Exception as error:
        print("TROJAN V2 STRATEGY ERROR:", repr(error))

        return {
            "action": "REJECT",
            "score": 0,
            "reason": "AI analysis failed"
        }

    if not 0 <= score <= 100:
        return {
            "action": "REJECT",
            "score": 0,
            "reason": "Invalid AI score"
        }

    decision = analysis.get("decision")

    if decision == "REJECT":
        return {
            "action": "REJECT",
            "score": score,
            "reason": "; ".join(analysis.get("reasons", [])) or "AI rejected token"
        }

    if decision != "APPROVE":
        return {
            "action": "WAIT",
            "score": score,
            "reason": "; ".join(analysis.get("reasons", [])) or "Waiting for stronger entry"
        }

    rules_passed, rules_reason = evaluate_strategy_rules(
        token, score, strategy
    )
    if not rules_passed:
        return {
            "action": "WAIT",
            "score": score,
            "reason": rules_reason
        }

    if score < MINIMUM_SCORES[strategy]:
        return {
            "action": "WAIT",
            "score": score,
            "reason": "Score below strategy threshold"
        }

    # Get separate strategy position counts.
    main_positions = paper.position_count("main")
    sniper_positions = paper.position_count("sniper")

    # TROJAN V2 centralized risk validation.
    allowed, risk_reason = check_trade_risk(
        balance,
        open_positions,
        daily_loss,
        TRADE_SIZE,
        strategy=strategy,
        main_positions=main_positions,
        sniper_positions=sniper_positions
    )

    if not allowed:
        return {
            "action": "REJECT",
            "score": score,
            "reason": risk_reason
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
