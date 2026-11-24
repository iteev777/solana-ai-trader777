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

    latency_raw = item.get("latency_ms", item.get("quote_latency_ms"))
    if latency_raw is None:
        latency = None
    else:
        latency = _finite(latency_raw, "latency_ms")
        if latency < 0 or latency > 120_000:
            raise ValueError("LATENCY_OUTSIDE_LIMIT")

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
        "latency_ms": latency,
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


def discover_pool_addresses(input_mint, output_mint, limit=MAX_POOL_CANDIDATES):
    """Find fresh registry pools for the exact mint pair, from either token index."""
    import pool_registry

    discovered = {}
    for mint in (input_mint, output_mint):
        try:
            # Refresh is queued and rate-limited by the registry; the current
            # cached snapshot remains available for this quote request.
            pool_registry.enqueue_dex_pool_refresh(mint)
        except Exception:
            pass
        try:
            rows = pool_registry.get_pool_candidates(mint)
        except Exception:
            rows = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            base = row.get("baseToken")
            quote = row.get("quoteToken")
            if not isinstance(base, dict) or not isinstance(quote, dict):
                continue
            pair_mints = {base.get("address"), quote.get("address")}
            if pair_mints != {input_mint, output_mint}:
                continue
            address = row.get("pairAddress") or row.get("poolAddress") or row.get("address")
            if not isinstance(address, str) or not address.strip():
                continue
            liquidity = row.get("liquidity")
            liquidity_usd = 0.0
            if isinstance(liquidity, dict):
                try:
                    candidate = float(liquidity.get("usd") or 0)
                    if math.isfinite(candidate) and candidate > 0:
                        liquidity_usd = candidate
                except (TypeError, ValueError, OverflowError):
                    pass
            old = discovered.get(address.strip())
            if old is None or liquidity_usd > old:
                discovered[address.strip()] = liquidity_usd

    ordered = sorted(discovered.items(), key=lambda item: (-item[1], item[0]))
    return [address for address, _ in ordered[:max(0, min(int(limit), MAX_POOL_CANDIDATES))]]


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
    effective_slippage_bps = (
        route_comparison.MAX_SLIPPAGE_BPS
        if slippage_bps is None else _uint(slippage_bps, "slippage_bps", allow_zero=True)
    )
    if effective_slippage_bps > route_comparison.MAX_SLIPPAGE_BPS:
        raise ValueError("Slippage exceeds configured safety cap")
    if network_fee_output_amounts is None:
        network_fee_output_amounts = {}
    if not isinstance(network_fee_output_amounts, dict):
        raise ValueError("network_fee_output_amounts must be an object")

    # Validate every fee estimate before touching the pool registry or APIs.
    fee_estimates = {
        str(key): _uint(value, "network fee", allow_zero=True)
        for key, value in network_fee_output_amounts.items()
    }
    allowed_fee_keys = {"jupiter", "raptor"}
    if any(not (key in allowed_fee_keys or key.startswith("direct:") and len(key) > 7)
           for key in fee_estimates):
        raise ValueError("Unknown network fee provider key")
    auto_discovery = pool_addresses is None
    pools = (
        discover_pool_addresses(input_mint, output_mint)
        if auto_discovery else _validate_pool_addresses(pool_addresses)
    )
    valid_fee_keys = {"jupiter", "raptor"} | {"direct:" + address for address in pools}
    unknown_fee_keys = set(fee_estimates) - valid_fee_keys
    if unknown_fee_keys:
        raise ValueError("Network fee estimate references an unknown route: "
                         + sorted(unknown_fee_keys)[0][:100])
    started = time.perf_counter()
    raw_quotes = {}
    rejected = {}

    tasks = {
        "aggregators": lambda: route_comparison.compare_quotes(
            input_mint, output_mint, amount, effective_slippage_bps, pool_address=None
        )
    }
    for pool_address in pools:
        key = "direct:" + pool_address
        tasks[key] = lambda address=pool_address: _direct_pool_quote(
            address, input_mint, output_mint, amount,
            effective_slippage_bps
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
    aggregate = raw_quotes.get("aggregators")
    if not isinstance(aggregate, dict):
        rejected["aggregators"] = "INVALID_AGGREGATOR_RESPONSE"
        aggregate_quotes = {}
    else:
        aggregate_quotes = aggregate.get("quotes")
        if not isinstance(aggregate_quotes, dict):
            rejected["aggregators"] = "INVALID_AGGREGATOR_QUOTES_MAP"
            aggregate_quotes = {}

    for provider in ("jupiter", "raptor"):
        item = aggregate_quotes.get(provider)
        if item is None:
            rejected[provider] = rejected.get("aggregators", "QUOTE_UNAVAILABLE")
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
    # A route cannot be called definitively best when some eligible routes
    # lack comparable network-fee estimates in the output mint. Keep the
    # provisional selection visible, but label its confidence explicitly.
    unpriced_eligible_routes = [
        item["provider_key"] for item in candidates
        if not item["network_fee_priced"]
    ]
    ranking_confidence = (
        "COST_ADJUSTED"
        if candidates and not unpriced_eligible_routes
        else "PROVISIONAL_UNPRICED_NETWORK_COSTS"
        if candidates
        else "NO_ELIGIBLE_ROUTES"
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
        "slippage_bps": effective_slippage_bps,
        "pool_candidates_requested": len(pools),
        "pool_discovery": "AUTOMATIC_REGISTRY_PAIR_MATCH" if auto_discovery else "EXPLICIT_POOL_LIST",
        "pool_discovery_complete": not auto_discovery or len(pools) < MAX_POOL_CANDIDATES,
        "routes_eligible": candidates,
        "routes_rejected": rejected,
        "selected_route": selected,
        "best_provider": selected["provider"] if selected else None,
        "best_pool": selected["pool_id"] if selected else None,
        "comparison_basis": "net_minimum_output_same_output_mint_raw_units",
        "ranking_confidence": ranking_confidence,
        "ranking_is_provisional": bool(unpriced_eligible_routes),
        "unpriced_eligible_route_count": len(unpriced_eligible_routes),
        "unpriced_eligible_route_keys": unpriced_eligible_routes,
        "ranking_note": (
            "Selected route is provisional; missing network fees may change route order"
            if unpriced_eligible_routes else
            "All eligible routes include comparable output-mint network fee estimates"
            if candidates else
            "No route passed eligibility checks"
        ),
        "network_costs": "only explicit output-mint-denominated estimates are deducted",
        "unpriced_network_costs_may_change_ranking": bool(unpriced_eligible_routes),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "execution": "LOCKED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
        "live_trading_ready": False,
        "quoted_at": now,
    }


def get_engine_status():
    import solana_onchain
    import pool_registry
    routers = route_comparison.get_router_status()
    chain = solana_onchain.get_onchain_status()
    from native_execution import get_native_execution_status
    native_execution_status = get_native_execution_status()
    try:
        registry_status = pool_registry.get_pool_registry_status()
    except Exception as error:
        registry_status = {"available": False, "error": str(error)[:160]}
    import dex_capabilities
    capability_report = dex_capabilities.get_dex_capabilities(
        chain.get("supported_protocols", [])
    )
    protocol_capabilities = capability_report["protocols"]
    native_builders = {
        "raydium_cpmm": protocol_capabilities.get("raydium_cpmm", {}).get("transaction_builder", {}),
        "pumpswap_v2": protocol_capabilities.get("pumpswap", {}).get("transaction_builder", {}),
        "orca_whirlpool": protocol_capabilities.get("orca_whirlpool", {}).get("transaction_builder", {}),
        "meteora_dlmm": protocol_capabilities.get("meteora_dlmm", {}).get("transaction_builder", {}),
        "other_protocols": {
            "available": False,
            "status": "REGISTERED_BUT_NOT_PROMOTED_IN_LEGACY_SUMMARY",
            "registered_protocols": [
                name for name, capability in protocol_capabilities.items()
                if name not in {"raydium_cpmm", "pumpswap", "orca_whirlpool", "meteora_dlmm"}
                and capability.get("transaction_builder", {}).get("available")
            ],
            "reason": "Additional builders are listed in protocol_capabilities but are not promoted in this legacy dashboard summary",
        },
    }
    # Preserve dashboard fields while adding the full, explicit protocol matrix.
    for key in ("raydium_cpmm", "pumpswap_v2", "orca_whirlpool", "meteora_dlmm"):
        protocol_key = ("raydium_cpmm" if key == "raydium_cpmm"
                        else "pumpswap" if key == "pumpswap_v2"
                        else "orca_whirlpool" if key == "orca_whirlpool"
                        else "meteora_dlmm")
        simulation_capability = protocol_capabilities.get(protocol_key, {}).get(
            "simulation", {}
        )
        simulation_available = bool(
            isinstance(simulation_capability, dict)
            and simulation_capability.get("available")
        )
        native_builders[key] = {
            **native_builders[key],
            "instruction": ("SwapBaseInput" if key == "raydium_cpmm"
                            else "Swap" if key == "orca_whirlpool"
                            else "Swap2" if key == "meteora_dlmm" else None),
            "instructions": ["buy_exact_quote_in_v2", "sell_v2"] if key == "pumpswap_v2" else None,
            "transaction_version": "legacy",
            "fresh_requote_required": True,
            "rpc_simulation_required": True,
            "rpc_simulation_available": simulation_available,
            "token_program_support": ["legacy_spl_token"],
            "existing_user_token_accounts_required": True,
            "pool_modes": ["standard_non_cashback_non_mayhem"] if key == "pumpswap_v2" else None,
            "buyback_recipient_ata_required": True if key == "pumpswap_v2" else None,
            "signing": "external_wallet_only",
            "broadcast": False,
            "live_execution": False,
        }
    return {
        "engine": "trojan_multi_pool_dex_engine_v2",
        "mode": "READ_ONLY_ROUTE_SELECTION",
        "aggregators": {
            "jupiter": routers.get("jupiter"),
            "raptor": routers.get("raptor"),
        },
        "direct_pool": routers.get("direct_pool"),
        "pool_discovery": {
            "automatic_pair_matching": True,
            "max_candidates_per_pair": MAX_POOL_CANDIDATES,
            "registry": registry_status,
        },
        "onchain_supported_protocols": chain.get("supported_protocols", []),
        "protocol_capabilities": protocol_capabilities,
        "capability_summary": {
            "quote_adapters": capability_report["quote_adapter_count"],
            "native_builders": capability_report["native_builder_count"],
            "execution_policy": capability_report["execution_policy"],
        },
        "native_transaction_builders": native_builders,
        "native_execution": native_execution_status,
        "execution_locked": True,
        "signing_available": False,
        "broadcast_available": False,
        "live_trading_ready": False,
    }


def build_route(input_mint, output_mint, amount, slippage_bps=None, pool_address=None):
    """Backward-compatible dashboard entry point; accepts one or many pools."""
    if pool_address is None:
        pools = None
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
