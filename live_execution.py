"""Read-only Jupiter route intelligence for Trojan V2.

This module fetches quotes only. It never requests a transaction, loads wallet
secrets, signs, simulates, or broadcasts. Live execution is hard-locked.
"""
import math
import os
import time

import requests

JUPITER_ORDER_URL = os.getenv(
    "TROJAN_JUPITER_ORDER_URL",
    "https://api.jup.ag/swap/v2/order",
)
JUPITER_API_KEY = os.getenv("JUPITER_API_KEY", "").strip()
REQUEST_TIMEOUT = float(os.getenv("TROJAN_LIVE_QUOTE_TIMEOUT", "5"))
MAX_QUOTE_AGE_SECONDS = 8.0
MAX_PRICE_IMPACT_PERCENT = float(os.getenv("TROJAN_MAX_PRICE_IMPACT_PERCENT", "3"))
MAX_SLIPPAGE_BPS = int(os.getenv("TROJAN_MAX_SLIPPAGE_BPS", "100"))
LIVE_EXECUTION_LOCKED = True


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
    number = _finite(value, name, minimum)
    if not number.is_integer():
        raise ValueError(name + " must be an integer base-unit amount")
    return int(number)


def _route_diagnostics(route_plan):
    """Summarize route hops without trusting provider labels as executable data."""
    if not isinstance(route_plan, list):
        raise ValueError("Jupiter route plan is invalid")
    labels = []
    hop_count = 0
    split_percentages = []
    for step in route_plan:
        if not isinstance(step, dict):
            raise ValueError("Jupiter route step is invalid")
        info = step.get("swapInfo") or step.get("swap_info") or {}
        if not isinstance(info, dict):
            raise ValueError("Jupiter route swap info is invalid")
        label = info.get("label") or info.get("ammKey") or info.get("amm_key")
        if label:
            labels.append(str(label)[:80])
        hop_count += 1
        percent = step.get("percent")
        if percent is not None:
            split_percentages.append(_finite(percent, "route percent", 0))
    unique_labels = list(dict.fromkeys(labels))
    return {
        "hop_count": hop_count,
        "route_labels": unique_labels,
        "unique_venues": len(unique_labels),
        "split_percentages": split_percentages,
        "is_split_route": len(split_percentages) > 1 or (
            len(split_percentages) == 1 and split_percentages[0] < 100
        ),
    }


def get_execution_status():
    return {
        "mode": "PREPARED_ONLY",
        "execution_locked": LIVE_EXECUTION_LOCKED,
        "signing_available": False,
        "broadcast_available": False,
        "simulation_available": False,
        "quote_provider": "Jupiter Swap API v2",
        "quote_endpoint": JUPITER_ORDER_URL,
        "quote_endpoint_configured": bool(JUPITER_ORDER_URL),
        "api_key_configured": bool(JUPITER_API_KEY),
        "max_quote_age_seconds": MAX_QUOTE_AGE_SECONDS,
        "max_price_impact_percent": MAX_PRICE_IMPACT_PERCENT,
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
        "reason": "Read-only route analysis only; signing, simulation and broadcast are disabled.",
    }


def get_jupiter_quote(input_mint, output_mint, amount, slippage_bps=None):
    """Fetch a quote-only Jupiter v2 order; no taker means no transaction."""
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Invalid input mint")
    if not isinstance(output_mint, str) or not output_mint.strip():
        raise ValueError("Invalid output mint")
    if input_mint.strip() == output_mint.strip():
        raise ValueError("Input and output mints must differ")
    raw_amount = _amount(amount, "amount")
    slip = MAX_SLIPPAGE_BPS if slippage_bps is None else _amount(
        slippage_bps, "slippage_bps"
    )
    if slip > MAX_SLIPPAGE_BPS:
        raise ValueError("Requested slippage exceeds Trojan safety cap")

    headers = {"Accept": "application/json"}
    if JUPITER_API_KEY:
        headers["x-api-key"] = JUPITER_API_KEY
    started = time.perf_counter()
    response = requests.get(
        JUPITER_ORDER_URL,
        params={
            "inputMint": input_mint.strip(),
            "outputMint": output_mint.strip(),
            "amount": raw_amount,
            "slippageBps": slip,
        },
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    quote = response.json()
    elapsed_ms = (time.perf_counter() - started) * 1000
    if not isinstance(quote, dict):
        raise ValueError("Jupiter returned an invalid quote")
    # V2 quote-only /order responses must not contain a transaction.
    if quote.get("transaction"):
        raise ValueError("Refusing an unexpected transaction-bearing Jupiter response")

    in_amount = _amount(quote.get("inAmount", 0), "inAmount")
    out_amount = _amount(quote.get("outAmount", 0), "outAmount")
    impact_raw = quote.get("priceImpactPct", quote.get("priceImpact", 0))
    impact = _finite(impact_raw, "price impact") * (
        100.0 if abs(float(impact_raw)) <= 1 else 1.0
    )
    if in_amount != raw_amount or out_amount <= 0:
        raise ValueError("Jupiter quote amount mismatch")
    if impact > MAX_PRICE_IMPACT_PERCENT:
        raise ValueError("Price impact exceeds configured safety cap")

    route_plan = quote.get("routePlan", [])
    diagnostics = _route_diagnostics(route_plan)
    threshold = quote.get("otherAmountThreshold")
    minimum_out = (
        _amount(threshold, "otherAmountThreshold")
        if threshold not in (None, "")
        else out_amount * (10000 - slip) // 10000
    )
    if minimum_out <= 0 or minimum_out > out_amount:
        raise ValueError("Jupiter minimum output is invalid")

    return {
        "provider": "jupiter",
        "api_version": "v2",
        "router": quote.get("router"),
        "input_mint": input_mint.strip(),
        "output_mint": output_mint.strip(),
        "in_amount": in_amount,
        "out_amount": out_amount,
        "minimum_out_amount": minimum_out,
        "price_impact_percent": impact,
        "slippage_bps": slip,
        "route_plan": route_plan,
        "route_diagnostics": diagnostics,
        "platform_fee_bps": quote.get("feeBps"),
        "context_slot": quote.get("contextSlot"),
        "quote_latency_ms": round(elapsed_ms, 2),
        "quoted_at": time.time(),
        "raw_quote": quote,
    }


def build_execution_plan(quote, side, max_quote_age=MAX_QUOTE_AGE_SECONDS):
    """Validate a fresh route into an auditable, permanently non-executable plan."""
    if not isinstance(quote, dict) or quote.get("provider") != "jupiter":
        raise ValueError("Unsupported quote provider")
    age = time.time() - float(quote.get("quoted_at", 0))
    if age < 0 or age > min(float(max_quote_age), MAX_QUOTE_AGE_SECONDS):
        raise ValueError("Quote is stale")
    if side not in ("BUY", "SELL"):
        raise ValueError("Invalid swap side")
    in_amount = _amount(quote.get("in_amount", 0), "in_amount")
    out_amount = _amount(quote.get("out_amount", 0), "out_amount")
    minimum_out = _amount(
        quote.get("minimum_out_amount", 0), "minimum_out_amount"
    )
    if minimum_out > out_amount:
        raise ValueError("Minimum output exceeds quoted output")
    diagnostics = quote.get("route_diagnostics") or _route_diagnostics(
        quote.get("route_plan", [])
    )
    return {
        "status": "READY_FOR_REVIEW",
        "side": side,
        "provider": quote["provider"],
        "api_version": quote.get("api_version", "unknown"),
        "router": quote.get("router"),
        "input_mint": quote["input_mint"],
        "output_mint": quote["output_mint"],
        "in_amount": in_amount,
        "out_amount": out_amount,
        "minimum_out_amount": minimum_out,
        "price_impact_percent": float(quote["price_impact_percent"]),
        "slippage_bps": int(quote["slippage_bps"]),
        "route_diagnostics": diagnostics,
        "platform_fee_bps": quote.get("platform_fee_bps"),
        "quote_latency_ms": quote.get("quote_latency_ms"),
        "context_slot": quote.get("context_slot"),
        "quoted_at": quote["quoted_at"],
        "expires_at": quote["quoted_at"] + MAX_QUOTE_AGE_SECONDS,
        "execution_locked": True,
        "signing_available": False,
        "broadcast_available": False,
    }


def execute_plan(plan):
    """Fail closed. No runtime flag can unlock this path."""
    if not isinstance(plan, dict) or not plan.get("execution_locked"):
        raise LiveExecutionLocked("LIVE EXECUTION LOCKED")
    raise LiveExecutionLocked(
        "LIVE EXECUTION LOCKED: quote analysis is available; signing and broadcast are disabled."
    )
