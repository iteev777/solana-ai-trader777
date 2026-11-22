"""Deterministic candidate quality score; advisory unless explicitly gated."""
import math
import time


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def score_candidate(token):
    token = token if isinstance(token, dict) else {}
    market = token.get("market_data")
    if not isinstance(market, dict):
        market = token
    liquidity = market.get("liquidity")
    volume = market.get("volume")
    txns = market.get("txns")
    changes = market.get("priceChange")
    liquidity = liquidity if isinstance(liquidity, dict) else {}
    volume = volume if isinstance(volume, dict) else {}
    txns = txns if isinstance(txns, dict) else {}
    changes = changes if isinstance(changes, dict) else {}
    m5 = txns.get("m5") if isinstance(txns.get("m5"), dict) else {}
    sources = token.get("discovery_sources")
    sources = list(dict.fromkeys(s for s in sources if isinstance(s, str))) if isinstance(sources, list) else []
    liq = _number(liquidity.get("usd"))
    vol = _number(volume.get("m5"))
    buys, sells = _number(m5.get("buys")), _number(m5.get("sells"))
    age_ms = _number(market.get("pairCreatedAt") or token.get("pairCreatedAt"))
    age_min = max(0.0, (time.time() * 1000 - age_ms) / 60000) if age_ms else None
    change = _number(changes.get("m5"))
    score, reasons = 0.0, []
    if liq is not None:
        if liq >= 50000: score += 25
        elif liq >= 15000: score += 20
        elif liq >= 5000: score += 12
        else: reasons.append("LOW_LIQUIDITY")
    else:
        reasons.append("LIQUIDITY_UNKNOWN")
    if vol is not None:
        if vol >= 25000: score += 20
        elif vol >= 5000: score += 15
        elif vol >= 1000: score += 8
        else: reasons.append("LOW_5M_VOLUME")
    else:
        reasons.append("VOLUME_UNKNOWN")
    if buys is not None and sells is not None and buys + sells > 0:
        ratio = buys / (buys + sells)
        score += min(20, max(0, ratio * 20))
        if ratio < 0.45: reasons.append("SELL_PRESSURE")
    else:
        reasons.append("TRANSACTION_FLOW_UNKNOWN")
    if len(sources) >= 4: score += 15
    elif len(sources) >= 2: score += 10
    elif len(sources) == 1: score += 5
    else: reasons.append("SOURCE_PROVENANCE_UNKNOWN")
    if age_min is not None:
        if 2 <= age_min <= 1440: score += 10
        elif age_min < 2: score += 4
        else: score += 6
    else:
        reasons.append("TOKEN_AGE_UNKNOWN")
    if change is not None:
        if 0 <= change <= 15: score += 10
        elif change > 15: score += 4
        else: reasons.append("NEGATIVE_SHORT_TERM_MOMENTUM")
    else:
        reasons.append("PRICE_CHANGE_UNKNOWN")
    return {
        "score": round(min(100.0, score), 2),
        "reasons": reasons,
        "inputs": {
            "liquidity_usd": liq, "volume_m5_usd": vol,
            "buy_ratio": round(buys / (buys + sells), 4)
            if buys is not None and sells is not None and buys + sells > 0 else None,
            "source_count": len(sources), "age_minutes": round(age_min, 2) if age_min is not None else None,
            "price_change_m5": change,
        },
        "gating_enabled": False,
    }
