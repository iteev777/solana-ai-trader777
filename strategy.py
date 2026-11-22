# ==========================================
# TROJAN V2 - STRATEGY ENGINE
# ==========================================

import os
from ai_analyzer import analyze_token
from strategy_rules import evaluate_strategy_rules
from risk_manager import (
    check_trade_risk,
    MAX_TRADE_SIZE,
    MIN_TRADE_SIZE,
    MAX_TRADE_BALANCE_FRACTION
)
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

    # Deterministic market-quality score is advisory by default. Operators may
    # opt into a minimum threshold after reviewing paper results.
    from candidate_quality import score_candidate
    candidate_quality = score_candidate(token)
    try:
        minimum_quality = float(os.getenv("TROJAN_MIN_CANDIDATE_QUALITY_SCORE", "0"))
    except (TypeError, ValueError, OverflowError):
        minimum_quality = 0.0
    if not math.isfinite(minimum_quality) or minimum_quality < 0 or minimum_quality > 100:
        minimum_quality = 0.0
    candidate_quality["gating_enabled"] = minimum_quality > 0
    candidate_quality["minimum_score"] = minimum_quality
    if minimum_quality > 0 and candidate_quality["score"] < minimum_quality:
        return {
            "action": "WAIT", "score": score,
            "reason": "Candidate quality below configured threshold",
            "candidate_quality": candidate_quality
        }
    if not rules_passed:
        return {
            "action": "WAIT",
            "score": score,
            "reason": rules_reason
        }

    # Technical confirmation is applied only when a sufficiently long
    # OHLCV series is available; missing candles never fabricate a signal.
    if os.getenv("TROJAN_TECHNICAL_FILTER_ENABLED", "true").lower() == "true":
        indicators = token.get("indicators") if isinstance(token.get("indicators"), dict) else {}
        if indicators.get("ready") is True:
            rsi = indicators.get("rsi_14")
            if isinstance(rsi, (int, float)) and (rsi < 25 or rsi > 80):
                return {
                    "action": "WAIT", "score": score,
                    "reason": "Technical filter: RSI outside 25–80 range"
                }
            bullish = indicators.get("ema_cross_bullish") is True
            breakout_volume = (
                indicators.get("breakout_20") is True
                and isinstance(indicators.get("volume_spike_ratio"), (int, float))
                and indicators.get("volume_spike_ratio") >= 1.5
            )
            if not bullish and not breakout_volume:
                return {
                    "action": "WAIT", "score": score,
                    "reason": "Technical filter: no bullish EMA or breakout-volume confirmation"
                }
            # When at least two independently aggregated timeframes have
            # enough candles, avoid entries against a bearish majority.
            # Unavailable higher timeframes are ignored, never treated as bearish.
            ready_frames = indicators.get("timeframe_ready_count", 0)
            bullish_frames = indicators.get("bullish_timeframes", 0)
            bearish_frames = indicators.get("bearish_timeframes", 0)
            if (
                isinstance(ready_frames, int) and ready_frames >= 2
                and isinstance(bullish_frames, int)
                and isinstance(bearish_frames, int)
                and bearish_frames > bullish_frames
            ):
                return {
                    "action": "WAIT", "score": score,
                    "reason": "Multi-timeframe filter: bearish trend majority"
                }

    if score < MINIMUM_SCORES[strategy]:
        return {
            "action": "WAIT",
            "score": score,
            "reason": "Score below strategy threshold"
        }

    # Scale the ticket down as the paper balance falls. Never exceed
    # either the strategy's normal ticket or the configured balance fraction.
    try:
        available_balance = float(balance)
    except (TypeError, ValueError, OverflowError):
        available_balance = 0.0
    trade_size = min(
        TRADE_SIZE,
        MAX_TRADE_SIZE,
        available_balance * MAX_TRADE_BALANCE_FRACTION
    )
    if trade_size < MIN_TRADE_SIZE:
        return {
            "action": "REJECT",
            "score": score,
            "reason": "BALANCE TOO LOW FOR MINIMUM SAFE TRADE"
        }

    # Get separate strategy position counts.
    main_positions = paper.position_count("main")
    sniper_positions = paper.position_count("sniper")

    # TROJAN V2 centralized risk validation.
    allowed, risk_reason = check_trade_risk(
        balance,
        open_positions,
        daily_loss,
        trade_size,
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
        "trade_size": round(trade_size, 2),
        "reasons": analysis.get("reasons", []),
        "candidate_quality": candidate_quality
    }


if __name__ == "__main__":
    print("TROJAN V2 STRATEGY ENGINE READY")
