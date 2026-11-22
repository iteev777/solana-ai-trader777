"""Independent multi-stage Solana candidate scoring for Trojan V2.

Scores are prioritisation signals, not buy instructions. Missing market data is
never fabricated and never treated as proof of safety. The discovery pipeline
keeps candidates with incomplete data in a watch tier so fast/new tokens are
not silently discarded.
"""
import math
import os
import time

_last_quality_status = {"candidate_count": 0, "tiers": {"strong": 0, "watch": 0, "weak": 0, "reject": 0}, "gating_enabled": False}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _nested(data, *path):
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _metric(market, token, key, *fallbacks):
    value = _nested(market, *key.split("."))
    if value is None:
        value = _nested(token, *key.split("."))
    if value is None:
        for path in fallbacks:
            value = _nested(market, *path.split("."))
            if value is None:
                value = _nested(token, *path.split("."))
            if value is not None:
                break
    return _number(value)


def score_candidate(token):
    token = token if isinstance(token, dict) else {}
    market = token.get("market_data")
    if not isinstance(market, dict):
        market = token

    liquidity = _metric(market, token, "liquidity.usd", "liquidity_usd")
    vol5 = _metric(market, token, "volume.m5", "volume_5m", "volume_5m_usd")
    vol1h = _metric(market, token, "volume.h1", "volume_1h", "volume_1h_usd")
    vol6h = _metric(market, token, "volume.h6", "volume_6h", "volume_6h_usd")
    vol24 = _metric(market, token, "volume.h24", "volume_24h", "volume_24h_usd")
    buys5 = _metric(market, token, "txns.m5.buys")
    sells5 = _metric(market, token, "txns.m5.sells")
    buys1h = _metric(market, token, "txns.h1.buys")
    sells1h = _metric(market, token, "txns.h1.sells")
    change5 = _metric(market, token, "priceChange.m5", "price_change_5m")
    change1h = _metric(market, token, "priceChange.h1", "price_change_1h")
    change6h = _metric(market, token, "priceChange.h6", "price_change_6h")
    change24 = _metric(market, token, "priceChange.h24", "price_change_24h")
    fdv = _metric(market, token, "fdv", "fdv_usd")
    mcap = _metric(market, token, "marketCap", "market_cap")
    price = _metric(market, token, "priceUsd", "price_usd")

    created = _metric(market, token, "pairCreatedAt", "created_at_ms")
    age_min = max(0.0, (time.time() * 1000 - created) / 60000) if created else None
    sources = token.get("discovery_sources")
    sources = list(dict.fromkeys(s.strip().lower() for s in sources if isinstance(s, str) and s.strip())) if isinstance(sources, list) else []
    reasons, hard_flags = [], []
    score = 0.0

    # Market depth: 25 points. Unknown values are explicitly marked, not zeroed.
    if liquidity is None:
        reasons.append("LIQUIDITY_UNKNOWN")
    elif liquidity <= 0:
        hard_flags.append("ZERO_LIQUIDITY")
    elif liquidity < 1500:
        score += 2
        reasons.append("LOW_LIQUIDITY")
    elif liquidity < 5000:
        score += 9
    elif liquidity < 15000:
        score += 17
    else:
        score += 25

    # Volume profile: 25 points, using multiple windows where available.
    known_volumes = [v for v in (vol5, vol1h, vol6h, vol24) if v is not None]
    if not known_volumes:
        reasons.append("VOLUME_UNKNOWN")
    else:
        if vol5 is not None:
            score += 10 if vol5 >= 5000 else 6 if vol5 >= 1000 else 2 if vol5 > 0 else 0
            if vol5 <= 0:
                reasons.append("NO_5M_VOLUME")
        if vol1h is not None:
            score += 7 if vol1h >= 20000 else 4 if vol1h >= 3000 else 1 if vol1h > 0 else 0
        if vol6h is not None:
            score += 4 if vol6h >= 50000 else 2 if vol6h > 0 else 0
        if vol24 is not None:
            score += 4 if vol24 >= 100000 else 2 if vol24 > 0 else 0

    # Transaction flow and sell pressure: 20 points.
    ratios = []
    for buys, sells in ((buys5, sells5), (buys1h, sells1h)):
        if buys is not None and sells is not None and buys >= 0 and sells >= 0 and buys + sells > 0:
            ratios.append(buys / (buys + sells))
    if ratios:
        ratio = sum(ratios) / len(ratios)
        score += min(20, ratio * 20)
        if ratio < 0.35:
            reasons.append("HEAVY_SELL_PRESSURE")
        elif ratio < 0.48:
            reasons.append("SELL_PRESSURE")
    else:
        ratio = None
        reasons.append("TRANSACTION_FLOW_UNKNOWN")

    # Source agreement: 10 points. One source is not treated as consensus.
    score += 10 if len(sources) >= 4 else 7 if len(sources) >= 2 else 3 if sources else 0
    if not sources:
        reasons.append("SOURCE_PROVENANCE_UNKNOWN")

    # Age: 10 points. Freshness is useful, but extremely new pools get less trust.
    if age_min is None:
        reasons.append("TOKEN_AGE_UNKNOWN")
    elif age_min < 2:
        score += 3
        reasons.append("VERY_NEW_POOL")
    elif age_min <= 1440:
        score += 10
    elif age_min <= 10080:
        score += 7
    else:
        score += 4

    # Momentum and anti-chase: 10 points.
    if change5 is None:
        reasons.append("SHORT_TERM_MOMENTUM_UNKNOWN")
    elif change5 < -15:
        reasons.append("NEGATIVE_SHORT_TERM_MOMENTUM")
    elif change5 > 40:
        score += 1
        reasons.append("SHORT_TERM_MOVE_EXTENDED")
    elif change5 > 0:
        score += 5
    else:
        score += 3
    if change1h is not None:
        score += 2 if -5 <= change1h <= 25 else 0
    if change6h is not None and change6h > 0:
        score += 1
    if change24 is not None and change24 > 0:
        score += 1

    if price is not None and price <= 0:
        hard_flags.append("INVALID_PRICE")
    if fdv is not None and fdv < 0 or mcap is not None and mcap < 0:
        hard_flags.append("INVALID_VALUATION")

    score = round(min(100.0, max(0.0, score)), 2)
    tier = "strong" if score >= 75 else "watch" if score >= 45 else "weak"
    if hard_flags:
        tier = "reject"
    return {
        "score": score,
        "tier": tier,
        "reasons": list(dict.fromkeys(reasons + hard_flags)),
        "hard_reject": bool(hard_flags),
        "gating_enabled": False,
        "inputs": {
            "liquidity_usd": liquidity, "volume_5m_usd": vol5,
            "volume_1h_usd": vol1h, "volume_6h_usd": vol6h,
            "volume_24h_usd": vol24, "buy_ratio": round(ratio, 4) if ratio is not None else None,
            "source_count": len(sources), "age_minutes": round(age_min, 2) if age_min is not None else None,
            "price_change_5m": change5, "price_change_1h": change1h,
            "price_change_6h": change6h, "price_change_24h": change24,
            "fdv_usd": fdv, "market_cap_usd": mcap,
        },
    }


def rank_candidates(tokens, market_data_by_mint=None):
    """Attach auditable scores and return deterministic best-first candidates."""
    global _last_quality_status
    market_data_by_mint = market_data_by_mint if isinstance(market_data_by_mint, dict) else {}
    ranked = []
    for token in tokens if isinstance(tokens, (list, tuple)) else []:
        if not isinstance(token, dict):
            continue
        item = dict(token)
        address = item.get("tokenAddress") or item.get("address") or item.get("mint")
        if not isinstance(item.get("market_data"), dict) and isinstance(market_data_by_mint.get(address), dict):
            item["market_data"] = market_data_by_mint[address]
        report = score_candidate(item)
        item["candidate_quality"] = report
        ranked.append(item)
    tier_order = {"strong": 0, "watch": 1, "weak": 2, "reject": 3}
    ranked.sort(key=lambda item: (
        tier_order.get(item["candidate_quality"]["tier"], 4),
        -item["candidate_quality"]["score"],
        -len(item.get("discovery_sources", [])) if isinstance(item.get("discovery_sources"), list) else 0,
        str(item.get("tokenAddress", "")),
    ))
    _last_quality_status = get_quality_status(ranked)
    return ranked


def get_quality_status(tokens=None):
    if tokens is None:
        return dict(_last_quality_status)
    counts = {"strong": 0, "watch": 0, "weak": 0, "reject": 0}
    for token in tokens if isinstance(tokens, (list, tuple)) else []:
        tier = _nested(token, "candidate_quality", "tier")
        if tier in counts:
            counts[tier] += 1
    return {"candidate_count": sum(counts.values()), "tiers": counts, "gating_enabled": False}
