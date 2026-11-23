"""Safety-gated multi-router DEX route selection for Trojan V2.

This module is deliberately non-custodial and read-only. It fans out to the
existing Jupiter, Raptor and verified direct-pool quote adapters, rejects
stale/unsafe/malformed candidates, and selects the highest minimum output in
the requested output mint's raw units. It never signs or broadcasts.
"""
import math
import os
import time


MAX_ROUTE_AGE_SECONDS = min(
    30.0, max(1.0, float(os.getenv("TROJAN_DEX_MAX_QUOTE_AGE_SECONDS", "8")))
)
MAX_PRICE_IMPACT_PERCENT = min(
    20.0, max(0.01, float(os.getenv("TROJAN_DEX_MAX_PRICE_IMPACT_PERCENT", "3")))
)
MAX_ALLOWED_HOPS = min(8, max(1, int(os.getenv("TROJAN_DEX_MAX_HOPS", "4"))))


def _positive_int(value, name):
    if isinstance(value, bool):
        raise ValueError(name + " must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(name + " must be a positive integer")
    if number <= 0:
        raise ValueError(name + " must be a positive integer")
    return number


def _finite(value, name):
    if isinstance(value, bool):
        raise ValueError(name + " must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(name + " must be finite")
    if not math.isfinite(number):
        raise ValueError(name + " must be finite")
    return number


def _quote_age(candidate, now):
    quoted_at = candidate.get("quoted_at")
    if quoted_at is not None:
        age = now - _finite(quoted_at, "quoted_at")
    elif candidate.get("snapshot_age_seconds") is not None:
        age = _finite(candidate["snapshot_age_seconds"], "snapshot_age_seconds")
    else:
        return None
    if age < 0:
        raise ValueError("QUOTE_TIMESTAMP_IN_FUTURE")
    return age


def _candidate(provider_key, candidate, output_mint, now):
    if not isinstance(candidate, dict):
        return None, ["QUOTE_NOT_AN_OBJECT"]
    reasons = []
    if candidate.get("status") != "QUOTED":
        return None, [str(candidate.get("reason") or candidate.get("status") or "QUOTE_UNAVAILABLE")[:160]]

    provider = str(candidate.get("provider") or provider_key)[:80]
    actual_output = candidate.get("output_mint")
    if actual_output is not None and actual_output != output_mint:
        return None, ["OUTPUT_MINT_MISMATCH"]

    try:
        out_amount = _positive_int(candidate.get("out_amount"), "out_amount")
        minimum_out = _positive_int(candidate.get("minimum_out_amount"), "minimum_out_amount")
        if minimum_out > out_amount:
            raise ValueError("MINIMUM_OUTPUT_EXCEEDS_QUOTE")
    except ValueError as error:
        return None, [str(error)[:160]]

    impact = candidate.get("price_impact_percent")
    if impact is None:
        return None, ["PRICE_IMPACT_UNAVAILABLE"]
    try:
        impact = _finite(impact, "price_impact_percent")
    except ValueError as error:
        return None, [str(error)[:160]]
    if impact < 0 or impact > MAX_PRICE_IMPACT_PERCENT:
        return None, ["PRICE_IMPACT_OUTSIDE_LIMIT"]

    quality = candidate.get("quality")
    if isinstance(quality, dict) and quality.get("status") == "REJECT":
        return None, ["ROUTE_QUALITY_REJECTED"]

    route = candidate.get("route") or {}
    if not isinstance(route, dict):
        route = {}
    hops = route.get("hops", route.get("hop_count"))
    if hops is not None:
        try:
            hops = int(hops)
        except (TypeError, ValueError, OverflowError):
            return None, ["INVALID_HOP_COUNT"]
        if hops < 0 or hops > MAX_ALLOWED_HOPS:
            return None, ["HOP_COUNT_OUTSIDE_LIMIT"]

    try:
        age = _quote_age(candidate, now)
    except ValueError as error:
        return None, [str(error)[:160]]
    if age is None:
        # A direct pool quote must include a confirmed snapshot age. Router
        # APIs must include a quote timestamp. Unknown freshness fails closed.
        return None, ["QUOTE_FRESHNESS_UNAVAILABLE"]
    if age > MAX_ROUTE_AGE_SECONDS:
        return None, ["QUOTE_STALE"]

    latency = candidate.get("latency_ms", candidate.get("quote_latency_ms"))
    if latency is not None:
        try:
            latency = max(0.0, _finite(latency, "latency_ms"))
        except ValueError:
            latency = None

    normalized = {
        "provider": provider,
        "provider_key": provider_key,
        "protocol": candidate.get("protocol"),
        "status": "ELIGIBLE",
        "input_mint": None,
        "output_mint": output_mint,
        "out_amount": out_amount,
        "minimum_out_amount": minimum_out,
        "price_impact_percent": impact,
        "quote_age_seconds": round(age, 3),
        "latency_ms": round(latency, 2) if latency is not None else None,
        "hops": hops,
        "route": route,
        "quality": quality,
        "pool_id": candidate.get("pool_id"),
        "slot": candidate.get("slot"),
        "fee_amounts": candidate.get("fee_amounts"),
        "fee_mint": candidate.get("fee_mint"),
    }
    return normalized, reasons


def build_route(input_mint, output_mint, amount, slippage_bps=None, pool_address=None):
    """Return a safety-gated route decision; this function never executes a swap."""
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Input mint is required")
    if not isinstance(output_mint, str) or not output_mint.strip():
        raise ValueError("Output mint is required")
    input_mint, output_mint = input_mint.strip(), output_mint.strip()
    if input_mint == output_mint:
        raise ValueError("Input and output mints must differ")
    amount = _positive_int(amount, "input amount")

    import route_comparison
    comparison = route_comparison.compare_quotes(
        input_mint, output_mint, amount, slippage_bps, pool_address
    )
    now = time.time()
    candidates, rejected = [], {}
    for key, value in (comparison.get("quotes") or {}).items():
        normalized, reasons = _candidate(key, value, output_mint, now)
        if normalized is None:
            rejected[key] = reasons
            continue
        normalized["input_mint"] = input_mint
        candidates.append(normalized)

    # All candidates produce the same output mint, so minimum raw output is
    # directly comparable. Network/priority fees are not subtracted here:
    # they are denominated in SOL and must not be mixed with token base units.
    candidates.sort(
        key=lambda item: (
            item["minimum_out_amount"],
            item["out_amount"],
            -(item["latency_ms"] if item["latency_ms"] is not None else 1e12),
        ),
        reverse=True,
    )
    selected = candidates[0] if candidates else None
    if selected:
        best_minimum = selected["minimum_out_amount"]
        for item in candidates:
            item["relative_minimum_output_percent"] = round(
                item["minimum_out_amount"] / best_minimum * 100.0, 4
            )
            item["selected"] = item is selected
    return {
        "engine": "trojan_dex_route_engine_v1",
        "mode": "READ_ONLY_ROUTE_SELECTION",
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount,
        "slippage_bps": comparison.get("slippage_bps"),
        "selected_route": selected,
        "eligible_routes": candidates,
        "rejected_routes": rejected,
        "best_provider": selected["provider"] if selected else None,
        "best_minimum_output": selected["minimum_out_amount"] if selected else None,
        "comparison_basis": "minimum_output_raw_units_same_output_mint",
        "max_quote_age_seconds": MAX_ROUTE_AGE_SECONDS,
        "max_price_impact_percent": MAX_PRICE_IMPACT_PERCENT,
        "max_hops": MAX_ALLOWED_HOPS,
        "execution": "LOCKED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
        "live_trading_ready": False,
        "quoted_at": now,
    }


def get_engine_status():
    import route_comparison
    routers = route_comparison.get_router_status()
    return {
        "engine": "trojan_dex_route_engine_v1",
        "mode": "READ_ONLY_ROUTE_SELECTION",
        "quote_sources": {
            "jupiter": "enabled",
            "raptor": "enabled",
            "verified_direct_pool": "enabled_when_pool_address_supplied",
        },
        "direct_pool": routers.get("direct_pool", {}),
        "safety": {
            "max_quote_age_seconds": MAX_ROUTE_AGE_SECONDS,
            "max_price_impact_percent": MAX_PRICE_IMPACT_PERCENT,
            "max_hops": MAX_ALLOWED_HOPS,
            "unknown_freshness": "REJECT",
            "unknown_price_impact": "REJECT",
        },
        "execution_locked": True,
        "signing_available": False,
        "broadcast_available": False,
        "live_trading_ready": False,
    }
