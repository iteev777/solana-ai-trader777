"""Canonical pool selection for token market data.

Ranks the candidate's actual base-side pools in one liquidity unit, prefers
native-SOL pairs, and uses incumbent hysteresis to prevent price-source churn.
"""
import copy
import math
import threading
from collections import OrderedDict

WSOL_MINT = "So11111111111111111111111111111111111111112"
CANONICAL_KEEP_RATIO = 0.80
MAX_CANDIDATE_SUMMARIES = 5
MAX_CANONICAL_TOKENS = 5000

_lock = threading.RLock()
_canonical_pool_by_token = OrderedDict()


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _address(token):
    return token.get("address") if isinstance(token, dict) else None


def _pair_identity(pair):
    value = pair.get("pairAddress") or pair.get("poolAddress") or pair.get("address")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _derive_native_price(pairs):
    observations = []
    for pair in pairs:
        base = _address(pair.get("baseToken"))
        quote = _address(pair.get("quoteToken"))
        price_usd = _number(pair.get("priceUsd"))
        price_native = _number(pair.get("priceNative"))
        if base == WSOL_MINT and price_usd and price_usd > 0:
            observations.append(price_usd)
        elif quote == WSOL_MINT and price_usd and price_usd > 0 and price_native and price_native > 0:
            implied_price = price_usd / price_native
            if math.isfinite(implied_price) and implied_price > 0:
                observations.append(implied_price)
    if not observations:
        return None
    observations.sort()
    middle = len(observations) // 2
    if len(observations) % 2:
        return observations[middle]
    return observations[middle - 1] / 2 + observations[middle] / 2


def _candidate(pair, native_price_usd):
    pool_address = _pair_identity(pair)
    if pool_address:
        import pool_analysis
        if pool_analysis.should_skip_pool(pool_address):
            return None
    base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
    quote = pair.get("quoteToken") if isinstance(pair.get("quoteToken"), dict) else {}
    base_address = _address(base)
    quote_address = _address(quote)
    liquidity = pair.get("liquidity") if isinstance(pair.get("liquidity"), dict) else {}
    volume = pair.get("volume") if isinstance(pair.get("volume"), dict) else {}
    liquidity_usd = _number(liquidity.get("usd"))
    volume_h24 = _number(volume.get("h24"))
    is_native = quote_address == WSOL_MINT
    native_reserve = _number(liquidity.get("quote")) if is_native else None
    if is_native and (native_reserve is None or native_reserve <= 0):
        if liquidity_usd and native_price_usd and native_price_usd > 0:
            native_reserve = _number((liquidity_usd / 2.0) / native_price_usd)
    metric = (
        max(0.0, native_reserve or 0.0)
        if is_native
        else max(0.0, liquidity_usd or 0.0)
    )
    return {
        "pair": pair,
        "address": _pair_identity(pair),
        "base_address": base_address,
        "quote_address": quote_address,
        "liquidity_usd": max(0.0, liquidity_usd or 0.0),
        "volume_h24": max(0.0, volume_h24 or 0.0),
        "is_native_pair": is_native,
        "native_liquidity": max(0.0, native_reserve or 0.0),
        "metric": metric,
    }


def _rank_key(candidate):
    return (candidate["metric"], candidate["volume_h24"], candidate["liquidity_usd"])


def _summary(candidate):
    return {
        "pairAddress": candidate["address"],
        "dexId": candidate["pair"].get("dexId"),
        "quoteToken": candidate["quote_address"],
        "liquidityUsd": candidate["liquidity_usd"],
        "nativeLiquidity": candidate["native_liquidity"],
        "volume24h": candidate["volume_h24"],
        "isNativePair": candidate["is_native_pair"],
        "metric": candidate["metric"],
        "metricUnit": "SOL" if candidate["is_native_pair"] else "USD",
    }


def select_best_pairs(pairs, requested_addresses):
    """Return one canonical, correctly base-priced pool for each requested mint."""
    if not isinstance(pairs, list) or requested_addresses is None:
        return {}
    requested = {
        value.strip() for value in requested_addresses
        if isinstance(value, str) and value.strip()
    }
    pairs = [
        pair for pair in pairs
        if isinstance(pair, dict) and pair.get("chainId", "solana") == "solana"
    ]
    native_price = _derive_native_price(pairs)
    grouped = {}
    for pair in pairs:
        price = _number(pair.get("priceUsd"))
        if price is None or price <= 0:
            continue
        base = pair.get("baseToken")
        if not isinstance(base, dict):
            continue
        address = base.get("address")
        # DexScreener priceUsd is the BASE token price. Never attribute it
        # to a requested quote token without an independent conversion path.
        if not isinstance(address, str) or address not in requested:
            continue
        candidate = _candidate(pair, native_price)
        if candidate is None:
            continue
        grouped.setdefault(address, []).append(candidate)

    selected = {}
    with _lock:
        for mint, candidates in grouped.items():
            native = [item for item in candidates if item["is_native_pair"]]
            ranked = native if native else candidates
            if not ranked:
                continue
            best = max(ranked, key=_rank_key)
            previous_address = _canonical_pool_by_token.get(mint)
            incumbent = next(
                (item for item in ranked if item["address"] and item["address"] == previous_address),
                None
            )
            retained = bool(
                incumbent
                and best["metric"] > 0
                and incumbent["metric"] >= CANONICAL_KEEP_RATIO * best["metric"]
            )
            chosen = incumbent if retained else best
            if chosen["address"]:
                _canonical_pool_by_token[mint] = chosen["address"]
                _canonical_pool_by_token.move_to_end(mint)
                while len(_canonical_pool_by_token) > MAX_CANONICAL_TOKENS:
                    _canonical_pool_by_token.popitem(last=False)
            result = copy.deepcopy(chosen["pair"])
            ordered = sorted(candidates, key=lambda item: (item["is_native_pair"], *_rank_key(item)), reverse=True)
            result["poolSelection"] = {
                "candidateCount": len(candidates),
                "nativePairCount": len(native),
                "metricUnit": "SOL" if native else "USD",
                "metric": round(chosen["metric"], 8),
                "reason": "native_pair_liquidity" if native else "usd_liquidity_fallback",
                "incumbentRetained": retained,
                "keepRatio": CANONICAL_KEEP_RATIO,
            }
            result["poolCandidates"] = [
                _summary(item) for item in ordered[:MAX_CANDIDATE_SUMMARIES]
            ]
            selected[mint] = result
    return selected


def reset_canonical_pools():
    """Clear incumbent state for deterministic tests and controlled resets."""
    with _lock:
        _canonical_pool_by_token.clear()


def get_canonical_pool(mint):
    with _lock:
        return _canonical_pool_by_token.get(mint)
