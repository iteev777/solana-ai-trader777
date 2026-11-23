"""Multi-pool DEX route selection for Trojan V2.

This module compares Jupiter, Raptor and every supplied verified direct pool
in parallel. It is quote-only: it never constructs, signs or broadcasts a swap.
All amounts are raw units of the same output mint. Protocol fees are expected
to be reflected in each adapter's out_amount; network fees are only deducted
when an explicit output-mint-denominated estimate is supplied.
"""
import concurrent.futures
import math
import time

import route_comparison


MAX_POOL_CANDIDATES = 20
MAX_QUOTE_AGE_SECONDS = 8.0
MAX_PRICE_IMPACT_PERCENT = 3.0
MAX_HOPS = 4


def _uint(value, name, allow_zero=False):
    if isinstance(value, bool):
        raise ValueError(name + " must be an integer")
    if isinstance(value, str):
        if not value.isdigit():
            raise ValueError(name + " must be an integer")
        number = int(value)
    elif isinstance(value, int):
        number = value
    else:
        raise ValueError(name + " must be an integer")
    if number < (0 if allow_zero else 1) or number > 2**64 - 1:
        raise ValueError(name + " is outside the supported u64 range")
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


def _validate_pool_addresses(pool_addresses):
    if pool_addresses is None:
        return []
    if not isinstance(pool_addresses, (list, tuple)):
        raise ValueError("pool_addresses must be a list")
    result = []
    for address in pool_addresses:
        if not isinstance(address, str) or not address.strip():
            raise ValueError("pool address must be a non-empty string")
        address = address.strip()
        if address not in result:
            result.append(address)
    if len(result) > MAX_POOL_CANDIDATES:
        raise ValueError("Too many direct pool candidates")
    return result


def _normalize_candidate(provider_key, item, input_mint, output_mint, now,
                         network_fee_output_amount=None):
    if not isinstance(item, dict) or item.get("status") != "QUOTED":
        raise ValueError("QUOTE_UNAVAILABLE")
    if item.get("output_mint") != output_mint:
        raise ValueError("OUTPUT_MINT_MISMATCH")

    out_amount = _uint(item.get("out_amount"), "out_amount")
    minimum_out = _uint(item.get("minimum_out_amount"), "minimum_out_amount")
    if minimum_out > out_amount:
        raise ValueError("MINIMUM_OUTPUT_EXCEEDS_QUOTE")

    impact_raw = item.get("price_impact_percent")
    if impact_raw is None:
        raise ValueError("PRICE_IMPACT_UNAVAILABLE")
    impact = _finite(impact_raw, "price_impact_percent")
    if impact < 0 or impact > MAX_PRICE_IMPACT_PERCENT:
        raise ValueError("PRICE_IMPACT_OUTSIDE_LIMIT")

    quality = item.get("quality")
    if isinstance(quality, dict) and quality.get("status") != "PASS":
        raise ValueError("ROUTE_QUALITY_NOT_PASSED")
    if quality is not None and not isinstance(quality, dict):
        raise ValueError("ROUTE_QUALITY_INVALID")

    route = item.get("route") or {}
    if not isinstance(route, dict):
        route = {}
    hops = route.get("hops", route.get("hop_count"))
    if hops is None:
        raise ValueError("HOP_COUNT_UNAVAILABLE")
    hops = _uint(hops, "hop_count")
    if hops > MAX_HOPS:
        raise ValueError("HOP_COUNT_OUTSIDE_LIMIT")

    if item.get("snapshot_age_seconds") is not None:
        age = _finite(item["snapshot_age_seconds"], "snapshot_age_seconds")
    elif item.get("quoted_at") is not None:
        age = now - _finite(item["quoted_at"], "quoted_at")
    else:
        raise ValueError("QUOTE_FRESHNESS_UNAVAILABLE")
    if age < 0:
        raise ValueError("QUOTE_TIMESTAMP_IN_FUTURE")
    if age > MAX_QUOTE_AGE_SECONDS:
        raise ValueError("QUOTE_STALE")

    network_cost = _uint(
        network_fee_output_amount if network_fee_output_amount is not None else 0,
        "network_fee_output_amount", allow_zero=True
    )
    if network_cost >= minimum_out:
        raise ValueError("NETWORK_COST_CONSUMES_OUTPUT")
    net_minimum = minimum_out - network_cost
    return {
        "provider": str(item.get("provider") or provider_key)[:80],
        "provider_key": provider_key,
        "protocol": item.get("protocol"),
        "pool_id": item.get("pool_id"),
        "input_mint": input_mint,
        "output_mint": output_mint,
        "out_amount": out_amount,
        "minimum_out_amount": minimum_out,
        "network_fee_output_amount": network_cost,
        "net_minimum_output": net_minimum,
        "network_fee_priced": network_fee_output_amount is not None,
        "price_impact_percent": impact,
        "quote_age_seconds": round(age, 3),
        "latency_ms": item.get("latency_ms", item.get("quote_latency_ms")),
        "hops": hops,
        "route": route,
        "slot": item.get("slot"),
        "fee_amounts": item.get("fee_amounts"),
        "quality": item.get("quality"),
        "selected": False,
    }


def _direct_pool_quote(pool_address, input_mint, output_mint, amount, slippage_bps):
    import solana_onchain
    quote = solana_onchain.quote_direct_pool(
        pool_address, input_mint, amount, slippage_bps, output_mint=output_mint
    )
    quote = dict(quote)
    quote["pool_id"] = pool_address
    quote["provider"] = quote.get("provider") or quote.get("protocol") or "direct_pool"
    quote["status"] = "QUOTED"
    quote["output_mint"] = quote.get("output_mint")
    quote["route"] = {
        "hops": 1,
        "venue": quote.get("protocol") or "verified_direct_pool",
    }
    return quote


def build_routes(input_mint, output_mint, amount, pool_addresses=None,
                 slippage_bps=None, network_fee_output_amounts=None):
    """Compare aggregator routes and multiple verified native pools.

    network_fee_output_amounts maps provider keys (jupiter, raptor, or
    direct:<pool-address>) to an estimate denominated in output-token raw units.
    Missing entries are not guessed or converted across incompatible units.
    """
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Input mint is required")
    if not isinstance(output_mint, str) or not output_mint.strip():
        raise ValueError("Output mint is required")
    input_mint, output_mint = input_mint.strip(), output_mint.strip()
    if input_mint == output_mint:
        raise ValueError("Input and output mints must differ")
    amount = _uint(amount, "amount")
    pools = _validate_pool_addresses(pool_addresses)
    network_fee_output_amounts = network_fee_output_amounts or {}
    if not isinstance(network_fee_output_amounts, dict):
        raise ValueError("network_fee_output_amounts must be an object")

    # Validate every fee estimate before making external calls.
    fee_estimates = {
        str(key): _uint(value, "network fee", allow_zero=True)
        for key, value in network_fee_output_amounts.items()
    }
    started = time.perf_counter()
    raw_quotes = {}
    rejected = {}

    tasks = {
        "aggregators": lambda: route_comparison.compare_quotes(
            input_mint, output_mint, amount, slippage_bps, pool_address=None
        )
    }
    for pool_address in pools:
        key = "direct:" + pool_address
        tasks[key] = lambda address=pool_address: _direct_pool_quote(
            address, input_mint, output_mint, amount,
            slippage_bps if slippage_bps is not None else route_comparison.MAX_SLIPPAGE_BPS
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(tasks))) as executor:
        futures = {executor.submit(fn): key for key, fn in tasks.items()}
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            try:
                raw_quotes[key] = future.result()
            except Exception as error:
                rejected[key] = (str(error) or type(error).__name__)[:180]

    now = time.time()
    candidates = []
    aggregate = raw_quotes.get("aggregators") or {}
    for provider in ("jupiter", "raptor"):
        item = (aggregate.get("quotes") or {}).get(provider)
        if item is None:
            rejected[provider] = "QUOTE_UNAVAILABLE"
            continue
        try:
            candidates.append(_normalize_candidate(
                provider, item, input_mint, output_mint, now,
                fee_estimates.get(provider)
            ))
        except Exception as error:
            rejected[provider] = (str(error) or type(error).__name__)[:180]

    for key, item in raw_quotes.items():
        if not key.startswith("direct:"):
            continue
        try:
            candidates.append(_normalize_candidate(
                key, item, input_mint, output_mint, now,
                fee_estimates.get(key)
            ))
        except Exception as error:
            rejected[key] = (str(error) or type(error).__name__)[:180]

    candidates.sort(
        key=lambda item: (
            item["net_minimum_output"],
            item["minimum_out_amount"],
            -(float(item["latency_ms"]) if item["latency_ms"] is not None else 1e12)
        ),
        reverse=True
    )
    if candidates:
        candidates[0]["selected"] = True
    selected = candidates[0] if candidates else None
    return {
        "engine": "trojan_multi_pool_dex_engine_v2",
        "mode": "READ_ONLY_ROUTE_SELECTION",
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount,
        "slippage_bps": aggregate.get("slippage_bps"),
        "pool_candidates_requested": len(pools),
        "routes_eligible": candidates,
        "routes_rejected": rejected,
        "selected_route": selected,
        "best_provider": selected["provider"] if selected else None,
        "best_pool": selected["pool_id"] if selected else None,
        "comparison_basis": "net_minimum_output_same_output_mint_raw_units",
        "network_costs": "only explicit output-mint-denominated estimates are deducted",
        "unpriced_network_costs_may_change_ranking": any(
            key not in fee_estimates for key in ["jupiter", "raptor"]
        ) or any("direct:" + address not in fee_estimates for address in pools),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "execution": "LOCKED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
        "live_trading_ready": False,
        "quoted_at": now,
    }


def get_engine_status():
    import solana_onchain
    routers = route_comparison.get_router_status()
    chain = solana_onchain.get_onchain_status()
    return {
        "engine": "trojan_multi_pool_dex_engine_v2",
        "mode": "READ_ONLY_ROUTE_SELECTION",
        "aggregators": {
            "jupiter": routers.get("jupiter"),
            "raptor": routers.get("raptor"),
        },
        "direct_pool": routers.get("direct_pool"),
        "onchain_supported_protocols": chain.get("supported_protocols", []),
        "execution_locked": True,
        "signing_available": False,
        "broadcast_available": False,
        "live_trading_ready": False,
    }



def build_route(input_mint, output_mint, amount, slippage_bps=None, pool_address=None):
    """Backward-compatible dashboard entry point; accepts one or many pools."""
    if pool_address is None:
        pools = []
    elif isinstance(pool_address, str):
        pools = [part.strip() for part in pool_address.split(",") if part.strip()]
    elif isinstance(pool_address, (list, tuple)):
        pools = list(pool_address)
    else:
        raise ValueError("pool_address must be a string or list")
    return build_routes(
        input_mint, output_mint, amount,
        pool_addresses=pools,
        slippage_bps=slippage_bps,
    )
