"""Read-only Jupiter route analysis and unsigned Solana transaction simulation.

This module never signs or broadcasts. Jupiter's /order endpoint is requested
without taker for quotes; unsigned simulation uses a configured public taker
address and Solana simulateTransaction with sigVerify=false.
"""
import math
import os
import threading
import time

import requests

JUPITER_ORDER_URL = os.getenv(
    "TROJAN_JUPITER_ORDER_URL", "https://api.jup.ag/swap/v2/order"
)
JUPITER_API_KEY = os.getenv("JUPITER_API_KEY", "").strip()
REQUEST_TIMEOUT = float(os.getenv("TROJAN_LIVE_QUOTE_TIMEOUT", "7"))
MAX_QUOTE_AGE_SECONDS = 8.0
MAX_PRICE_IMPACT_PERCENT = float(os.getenv("TROJAN_MAX_PRICE_IMPACT_PERCENT", "3"))
MAX_SLIPPAGE_BPS = int(os.getenv("TROJAN_MAX_SLIPPAGE_BPS", "100"))
MIN_ROUTE_QUALITY_SCORE = float(os.getenv("TROJAN_MIN_ROUTE_QUALITY_SCORE", "55"))
LIVE_EXECUTION_LOCKED = True
_request_lock = threading.Lock()
_last_jupiter_request = 0.0


class LiveExecutionLocked(RuntimeError):
    pass


def _finite(value, name, minimum=0.0):
    if isinstance(value, bool):
        raise ValueError(name + " must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise ValueError(name + " is invalid")
    return number


def _amount(value, name, minimum=1):
    if isinstance(value, bool):
        raise ValueError(name + " must be an integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        number = int(value)
    else:
        raise ValueError(name + " must be an integer base-unit amount")
    if number < minimum:
        raise ValueError(name + " is invalid")
    return number


def _headers():
    headers = {"Accept": "application/json"}
    if JUPITER_API_KEY:
        headers["x-api-key"] = JUPITER_API_KEY
    return headers


def _jupiter_get(params):
    global _last_jupiter_request
    # Jupiter's keyless public tier is deliberately rate-limited to 0.5 rps.
    with _request_lock:
        if not JUPITER_API_KEY:
            wait = 2.05 - (time.monotonic() - _last_jupiter_request)
            if wait > 0:
                time.sleep(wait)
        response = requests.get(
            JUPITER_ORDER_URL, params=params, headers=_headers(),
            timeout=REQUEST_TIMEOUT
        )
        _last_jupiter_request = time.monotonic()
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Jupiter returned an invalid response")
    return payload


def _route_diagnostics(route_plan):
    if route_plan is None:
        route_plan = []
    if not isinstance(route_plan, list):
        raise ValueError("Jupiter route plan is invalid")
    labels, split_percentages = [], []
    for step in route_plan:
        if not isinstance(step, dict):
            raise ValueError("Jupiter route step is invalid")
        info = step.get("swapInfo") or step.get("swap_info") or {}
        if not isinstance(info, dict):
            raise ValueError("Jupiter route swap info is invalid")
        label = info.get("label") or info.get("ammKey") or info.get("amm_key")
        if label:
            labels.append(str(label)[:80])
        if step.get("percent") is not None:
            split_percentages.append(_finite(step["percent"], "route percent", 0))
    unique_labels = list(dict.fromkeys(labels))
    return {
        "hop_count": len(route_plan),
        "route_labels": unique_labels,
        "unique_venues": len(unique_labels),
        "split_percentages": split_percentages,
        # Multiple sequential hops commonly each carry percent=100.
        # Any sub-100 branch percentage is the reliable split-route signal.
        "is_split_route": any(percent < 100 for percent in split_percentages),
    }


def evaluate_route_quality(quote):
    if not isinstance(quote, dict):
        return {"status": "REJECT", "score": 0.0, "reasons": ["QUOTE_INVALID"]}
    impact = quote.get("price_impact_percent")
    if impact is None:
        return {"status": "REJECT", "score": 0.0, "reasons": ["PRICE_IMPACT_UNAVAILABLE"]}
    impact = _finite(impact, "price impact")
    fee_bps = _finite(quote.get("platform_fee_bps") or 0, "platform fee")
    route = quote.get("route_diagnostics") or {}
    hops = int(route.get("hop_count", 0))
    score = max(0.0, min(100.0,
        100.0 - min(55.0, impact * 15.0)
        - min(20.0, hops * 4.0)
        - min(15.0, fee_bps * 0.5)
        - (2.0 if route.get("is_split_route") else 0.0)
    ))
    reasons = []
    if impact > MAX_PRICE_IMPACT_PERCENT:
        reasons.append("PRICE_IMPACT_ABOVE_LIMIT")
    if score < MIN_ROUTE_QUALITY_SCORE:
        reasons.append("ROUTE_QUALITY_BELOW_LIMIT")
    if not route.get("route_labels"):
        reasons.append("VENUE_BREAKDOWN_UNAVAILABLE")
    status = "REJECT" if (
        impact > MAX_PRICE_IMPACT_PERCENT or score < MIN_ROUTE_QUALITY_SCORE
    ) else "PASS"
    return {
        "status": status, "score": round(score, 2),
        "reasons": reasons, "price_impact_percent": impact,
        "platform_fee_bps": fee_bps, "hop_count": hops,
        "venues": list(route.get("route_labels") or []),
        "split_route": bool(route.get("is_split_route")),
    }


def get_execution_status():
    wallet = os.getenv("TROJAN_PUBLIC_WALLET_ADDRESS", "").strip()
    try:
        from solana_onchain import RPC_URL
        rpc_configured = bool(RPC_URL)
    except Exception:
        rpc_configured = bool(os.getenv("SOLANA_RPC_URL", "").strip())
    return {
        "mode": "READ_ONLY_ROUTE_AND_SIMULATION",
        "execution_locked": LIVE_EXECUTION_LOCKED,
        "signing_available": False,
        "broadcast_available": False,
        "simulation_available": bool(rpc_configured and wallet),
        "simulation_requires_public_wallet": not bool(wallet),
        "rpc_configured": rpc_configured,
        "quote_provider": "Jupiter Swap API v2",
        "quote_endpoint": JUPITER_ORDER_URL,
        "api_key_configured": bool(JUPITER_API_KEY),
        "keyless_rate_limit": "0.5 requests/second" if not JUPITER_API_KEY else None,
        "max_quote_age_seconds": MAX_QUOTE_AGE_SECONDS,
        "max_price_impact_percent": MAX_PRICE_IMPACT_PERCENT,
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
        "minimum_route_quality_score": MIN_ROUTE_QUALITY_SCORE,
        "reason": "Quote and unsigned simulation only; signing and broadcast are disabled.",
    }


def _validate_pair(input_mint, output_mint, amount, slippage_bps):
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Invalid input mint")
    if not isinstance(output_mint, str) or not output_mint.strip():
        raise ValueError("Invalid output mint")
    if input_mint.strip() == output_mint.strip():
        raise ValueError("Input and output mints must differ")
    raw_amount = _amount(amount, "amount")
    slip = MAX_SLIPPAGE_BPS if slippage_bps is None else _amount(
        slippage_bps, "slippage_bps", 0
    )
    if slip > MAX_SLIPPAGE_BPS:
        raise ValueError("Requested slippage exceeds Trojan safety cap")
    return input_mint.strip(), output_mint.strip(), raw_amount, slip


def _normalize_quote(quote, input_mint, output_mint, raw_amount, slip, elapsed_ms):
    if quote.get("transaction"):
        raise ValueError("Refusing an unexpected transaction-bearing quote response")
    in_amount = _amount(quote.get("inAmount", 0), "inAmount")
    out_amount = _amount(quote.get("outAmount", 0), "outAmount")
    if in_amount != raw_amount or out_amount <= 0:
        raise ValueError("Jupiter quote amount mismatch")
    impact_raw = quote.get("priceImpactPct", quote.get("priceImpact"))
    impact = None
    if impact_raw is not None:
        impact_value = _finite(impact_raw, "price impact")
        impact = impact_value * 100.0 if impact_value <= 1 else impact_value
    route_plan = quote.get("routePlan") or []
    diagnostics = _route_diagnostics(route_plan)
    threshold = quote.get("otherAmountThreshold")
    minimum_out = (
        _amount(threshold, "otherAmountThreshold")
        if threshold not in (None, "")
        else out_amount * (10000 - slip) // 10000
    )
    if minimum_out <= 0 or minimum_out > out_amount:
        raise ValueError("Jupiter minimum output is invalid")
    normalized = {
        "provider": "jupiter", "api_version": "v2",
        "router": quote.get("router"),
        "input_mint": input_mint, "output_mint": output_mint,
        "in_amount": in_amount, "out_amount": out_amount,
        "minimum_out_amount": minimum_out,
        "price_impact_percent": impact, "slippage_bps": slip,
        "route_plan": route_plan, "route_diagnostics": diagnostics,
        "platform_fee_bps": quote.get("feeBps"),
        "context_slot": quote.get("contextSlot"),
        "quote_latency_ms": round(elapsed_ms, 2),
        "quoted_at": time.time(),
    }
    normalized["route_quality"] = evaluate_route_quality(normalized)
    return normalized


def get_jupiter_quote(input_mint, output_mint, amount, slippage_bps=None):
    """Get a quote without taker: Jupiter returns transaction=null."""
    input_mint, output_mint, raw_amount, slip = _validate_pair(
        input_mint, output_mint, amount, slippage_bps
    )
    started = time.perf_counter()
    quote = _jupiter_get({
        "inputMint": input_mint, "outputMint": output_mint,
        "amount": raw_amount, "slippageBps": slip,
    })
    elapsed_ms = (time.perf_counter() - started) * 1000
    return _normalize_quote(quote, input_mint, output_mint, raw_amount, slip, elapsed_ms)


def simulate_unsigned_swap(input_mint, output_mint, amount, slippage_bps=None, taker=None):
    """Build a Jupiter order for a public wallet and simulate it without signatures."""
    input_mint, output_mint, raw_amount, slip = _validate_pair(
        input_mint, output_mint, amount, slippage_bps
    )
    taker = (taker or os.getenv("TROJAN_PUBLIC_WALLET_ADDRESS", "")).strip()
    if not taker:
        return {"status": "SKIPPED", "reason": "PUBLIC_WALLET_NOT_CONFIGURED"}
    import solana_onchain as chain
    if len(chain._b58decode(taker)) != 32:
        raise ValueError("Simulation taker is not a valid Solana public key")

    started = time.perf_counter()
    order = _jupiter_get({
        "inputMint": input_mint, "outputMint": output_mint,
        "amount": raw_amount, "slippageBps": slip, "taker": taker,
    })
    transaction = order.get("transaction")
    if not isinstance(transaction, str) or len(transaction) < 80:
        raise ValueError("Jupiter did not return an unsigned transaction for simulation")
    # This is strictly simulateTransaction. No sendTransaction/execute endpoint exists here.
    result = chain._rpc("simulateTransaction", [
        transaction,
        {
            "encoding": "base64", "commitment": "confirmed",
            "sigVerify": False, "replaceRecentBlockhash": True,
            "innerInstructions": False,
        }
    ])
    value = result.get("value") if isinstance(result, dict) else None
    if not isinstance(value, dict) or "err" not in value:
        raise ValueError("RPC did not return a valid simulation result")
    context = result.get("context")
    elapsed_ms = (time.perf_counter() - started) * 1000
    logs = value.get("logs")
    if not isinstance(logs, list):
        logs = []
    return {
        "status": "PASS" if value.get("err") is None else "FAIL",
        "simulated": True, "signed": False, "broadcast": False,
        "error": value.get("err"),
        "units_consumed": value.get("unitsConsumed"),
        "context_slot": context.get("slot") if isinstance(context, dict) else None,
        "log_tail": [str(line)[:240] for line in logs[-8:]],
        "latency_ms": round(elapsed_ms, 2),
        "router": order.get("router"),
        "input_amount": order.get("inAmount"),
        "output_amount": order.get("outAmount"),
        "taker": taker,
        "simulated_at": time.time(),
    }


def build_execution_plan(quote, side, max_quote_age=MAX_QUOTE_AGE_SECONDS):
    if not isinstance(quote, dict) or quote.get("provider") != "jupiter":
        raise ValueError("Unsupported quote provider")
    age = time.time() - float(quote.get("quoted_at", 0))
    if age < 0 or age > min(float(max_quote_age), MAX_QUOTE_AGE_SECONDS):
        raise ValueError("Quote is stale")
    if side not in ("BUY", "SELL"):
        raise ValueError("Invalid swap side")
    in_amount = _amount(quote.get("in_amount", 0), "in_amount")
    out_amount = _amount(quote.get("out_amount", 0), "out_amount")
    minimum_value = quote.get("minimum_out_amount")
    if minimum_value in (None, ""):
        minimum_value = out_amount * (
            10000 - _amount(quote.get("slippage_bps", MAX_SLIPPAGE_BPS), "slippage_bps", 0)
        ) // 10000
    minimum_out = _amount(minimum_value, "minimum_out_amount")
    if minimum_out > out_amount:
        raise ValueError("Minimum output exceeds quoted output")
    return {
        "status": "READY_FOR_REVIEW", "side": side,
        "provider": quote["provider"], "api_version": quote.get("api_version", "unknown"),
        "router": quote.get("router"), "input_mint": quote["input_mint"],
        "output_mint": quote["output_mint"], "in_amount": in_amount,
        "out_amount": out_amount, "minimum_out_amount": minimum_out,
        "price_impact_percent": quote.get("price_impact_percent"),
        "slippage_bps": int(quote["slippage_bps"]),
        "route_diagnostics": quote.get("route_diagnostics"),
        "route_quality": quote.get("route_quality"),
        "platform_fee_bps": quote.get("platform_fee_bps"),
        "quote_latency_ms": quote.get("quote_latency_ms"),
        "context_slot": quote.get("context_slot"),
        "quoted_at": quote["quoted_at"],
        "expires_at": quote["quoted_at"] + MAX_QUOTE_AGE_SECONDS,
        "execution_locked": True, "signing_available": False,
        "broadcast_available": False,
    }


def execute_plan(plan):
    if not isinstance(plan, dict) or not plan.get("execution_locked"):
        raise LiveExecutionLocked("LIVE EXECUTION LOCKED")
    raise LiveExecutionLocked(
        "LIVE EXECUTION LOCKED: quote and unsigned simulation only; signing and broadcast are disabled."
    )
