"""Read-only multi-router quote comparison for Trojan V2.

Jupiter and Solana Tracker Raptor are queried independently. A quote is only
informational: this module never builds, signs, or broadcasts a transaction.
Native direct-pool quoting is reported as unavailable until a protocol-specific
math/account decoder is implemented and tested for that exact pool.
"""
import concurrent.futures
import math
import os
import time
from urllib.parse import urlencode

import requests

RAPTOR_BASE_URL = os.getenv(
    "TROJAN_RAPTOR_BASE_URL", "https://raptor.solanatracker.io"
).rstrip("/")
TIMEOUT = min(12.0, max(1.0, float(os.getenv("TROJAN_ROUTE_TIMEOUT", "7"))))
MAX_SLIPPAGE_BPS = min(300, max(1, int(os.getenv("TROJAN_MAX_SLIPPAGE_BPS", "100"))))


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


def _output_amount(payload):
    if not isinstance(payload, dict):
        raise ValueError("Router response is not an object")
    for key in ("outAmount", "amountOut", "outputAmount", "out_amount"):
        value = payload.get(key)
        if value is not None:
            return _positive_int(value, "output amount")
    nested = payload.get("quote")
    if isinstance(nested, dict):
        return _output_amount(nested)
    raise ValueError("Router did not provide a recognized output amount")


def _minimum_output(payload, output_amount, slippage_bps):
    for key in ("minAmountOut", "minimumOutAmount", "otherAmountThreshold",
                "minOutAmount", "minimum_out_amount"):
        value = payload.get(key) if isinstance(payload, dict) else None
        if value is not None:
            minimum = _positive_int(value, "minimum output")
            if minimum > output_amount:
                raise ValueError("Minimum output exceeds quoted output")
            return minimum
    return output_amount * (10000 - slippage_bps) // 10000


def _validate(input_mint, output_mint, amount, slippage_bps):
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Input mint is required")
    if not isinstance(output_mint, str) or not output_mint.strip():
        raise ValueError("Output mint is required")
    if input_mint.strip() == output_mint.strip():
        raise ValueError("Input and output mints must differ")
    amount = _positive_int(amount, "Input amount")
    slip = int(slippage_bps if slippage_bps is not None else MAX_SLIPPAGE_BPS)
    if slip < 0 or slip > MAX_SLIPPAGE_BPS:
        raise ValueError("Slippage exceeds configured safety cap")
    return input_mint.strip(), output_mint.strip(), amount, slip


def _jupiter(input_mint, output_mint, amount, slippage_bps):
    import live_execution
    quote = live_execution.get_jupiter_quote(
        input_mint, output_mint, amount, slippage_bps
    )
    output = _positive_int(quote.get("out_amount"), "Jupiter output")
    minimum = _minimum_output(
        {"minimum_out_amount": quote.get("minimum_out_amount")},
        output, slippage_bps
    )
    return {
        "provider": "jupiter",
        "status": "QUOTED",
        "out_amount": output,
        "minimum_out_amount": minimum,
        "price_impact_percent": quote.get("price_impact_percent"),
        "latency_ms": quote.get("quote_latency_ms"),
        "route": quote.get("route_diagnostics"),
        "quoted_at": quote.get("quoted_at"),
        "quality": quote.get("route_quality"),
    }


def _raptor(input_mint, output_mint, amount, slippage_bps):
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "slippageBps": slippage_bps,
        "jitRouting": "auto",
    }
    started = time.perf_counter()
    response = requests.get(
        RAPTOR_BASE_URL + "/quote",
        params=params,
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Raptor returned an invalid quote")
    output = _output_amount(payload)
    minimum = _minimum_output(payload, output, slippage_bps)
    impact = payload.get("priceImpactPct", payload.get("priceImpact"))
    if impact is not None:
        impact = float(impact)
        if not math.isfinite(impact) or impact < 0:
            raise ValueError("Raptor price impact is invalid")
        if impact <= 1:
            impact *= 100
    return {
        "provider": "raptor",
        "status": "QUOTED",
        "out_amount": output,
        "minimum_out_amount": minimum,
        "price_impact_percent": impact,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "route": {
            "dexes": payload.get("dexes") or payload.get("route") or [],
            "hops": payload.get("hopCount"),
            "jit_routing": payload.get("jitRouting"),
        },
        "quoted_at": time.time(),
        "quality": None,
    }


def _failed(provider, error):
    return {
        "provider": provider,
        "status": "UNAVAILABLE",
        "reason": (str(error) or type(error).__name__)[:220],
        "out_amount": None,
        "minimum_out_amount": None,
    }


def compare_quotes(input_mint, output_mint, amount, slippage_bps=None, pool_address=None):
    """Compare best available quotes; lower bound (min-out) wins.

    Direct pool routes are included only when a verified protocol-specific adapter exists.
    """
    input_mint, output_mint, amount, slip = _validate(
        input_mint, output_mint, amount, slippage_bps
    )
    tasks = {
        "jupiter": lambda: _jupiter(input_mint, output_mint, amount, slip),
        "raptor": lambda: _raptor(input_mint, output_mint, amount, slip),
    }
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as error:
                results[name] = _failed(name, error)

    if pool_address:
        try:
            import solana_onchain
            direct = solana_onchain.quote_direct_pool(
                pool_address, input_mint, amount, slip
            )
            if direct.get("output_mint") != output_mint:
                raise ValueError("Direct pool quote output mint does not match requested output mint")
            results["direct_pool"] = {
                "provider": direct["provider"],
                "protocol": direct["protocol"],
                "status": "QUOTED",
                "output_mint": direct["output_mint"],
                "out_amount": direct["out_amount"],
                "minimum_out_amount": direct["minimum_out_amount"],
                "price_impact_percent": direct["price_impact_percent"],
                "slot": direct["slot"],
                "snapshot_age_seconds": direct.get("snapshot_age_seconds"),
                "pool_id": direct["pool_id"],
                "creator_fee_on_input": direct.get("creator_fee_on_input"),
                "fee_mint": direct.get("fee_mint"),
                "fee_amounts": {
                    "lp": direct.get("lp_fee_amount", direct.get("trade_fee_amount", 0)),
                    "protocol": direct.get("protocol_fee_amount", direct.get("owner_fee_amount", 0)),
                    "creator": direct.get(
                        "creator_fee_amount",
                        (direct.get("creator_fee_input_amount", 0) or 0)
                        + (direct.get("creator_fee_output_amount", 0) or 0),
                    ),
                    "trade": direct.get("trade_fee_amount", 0),
                    "owner": direct.get("owner_fee_amount", 0),
                    "creator_input": direct.get("creator_fee_input_amount", 0),
                    "creator_output": direct.get("creator_fee_output_amount", 0),
                    "total": direct.get("total_fee_amount"),
                },
                "execution": "DISABLED",
            }
        except Exception as error:
            results["direct_pool"] = _failed("direct_pool", error)
    else:
        results["direct_pool"] = {
            "provider": "solana_direct_pool",
            "status": "POOL_ADDRESS_REQUIRED",
            "reason": "Pass pool_address to request a supported native Solana pool quote.",
            "out_amount": None,
            "minimum_out_amount": None,
        }
    available = [
        item for item in results.values()
        if item.get("status") == "QUOTED"
        and isinstance(item.get("minimum_out_amount"), int)
    ]
    best = max(available, key=lambda item: item["minimum_out_amount"]) if available else None
    return {
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount,
        "slippage_bps": slip,
        "quotes": results,
        "best_provider": best["provider"] if best else None,
        "best_minimum_output": best["minimum_out_amount"] if best else None,
        "comparison_basis": "minimum_out_amount_raw_units",
        "execution": "DISABLED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
        "quoted_at": time.time(),
    }


def get_router_status():
    return {
        "jupiter": {"available": True, "mode": "quote_only"},
        "raptor": {"available": True, "endpoint": RAPTOR_BASE_URL, "mode": "quote_only"},
        "direct_pool": {
            "available": True,
            "mode": "read_only_partial",
            "supported_protocol_decoders": [
                "Raydium CPMM", "Orca Whirlpool", "Meteora", "PumpSwap",
                "Pump.fun bonding curve", "FluxBeam", "Moonit"
            ],
            "quote_adapters": ["PumpSwap", "Raydium CPMM", "Raydium AMM v4", "FluxBeam", "Moonit ConstantProductV1"],
            "requires_pool_address": True,
        },
        "execution_locked": True,
        "signing_available": False,
        "broadcast_available": False,
    }
