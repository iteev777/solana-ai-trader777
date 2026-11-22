"""Live execution preparation for Trojan V2.

This module deliberately supports quote/readiness planning only. It never loads
wallet secrets, signs transactions, or broadcasts. The hard lock is code-level
and cannot be enabled by a Railway environment variable.
"""
import math
import os
import time
import requests

JUPITER_QUOTE_URL = os.getenv(
    "TROJAN_JUPITER_QUOTE_URL",
    "https://quote-api.jup.ag/v6/quote"
)
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


def get_execution_status():
    return {
        "mode": "PREPARED_ONLY",
        "execution_locked": LIVE_EXECUTION_LOCKED,
        "signing_available": False,
        "broadcast_available": False,
        "quote_provider": "Jupiter v6",
        "quote_endpoint_configured": bool(JUPITER_QUOTE_URL),
        "max_quote_age_seconds": MAX_QUOTE_AGE_SECONDS,
        "max_price_impact_percent": MAX_PRICE_IMPACT_PERCENT,
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
        "reason": "Live signing and transaction submission are intentionally disabled."
    }


def get_jupiter_quote(input_mint, output_mint, amount, slippage_bps=None):
    """Fetch a read-only Jupiter quote; amount is in input-token base units."""
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Invalid input mint")
    if not isinstance(output_mint, str) or not output_mint.strip():
        raise ValueError("Invalid output mint")
    raw_amount = int(_finite(amount, "amount", 1))
    slip = MAX_SLIPPAGE_BPS if slippage_bps is None else int(
        _finite(slippage_bps, "slippage_bps", 1)
    )
    if slip > MAX_SLIPPAGE_BPS:
        raise ValueError("Requested slippage exceeds Trojan safety cap")
    response = requests.get(
        JUPITER_QUOTE_URL,
        params={
            "inputMint": input_mint.strip(),
            "outputMint": output_mint.strip(),
            "amount": raw_amount,
            "slippageBps": slip,
            "swapMode": "ExactIn"
        },
        timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    quote = response.json()
    if not isinstance(quote, dict):
        raise ValueError("Jupiter returned an invalid quote")
    in_amount = int(quote.get("inAmount", 0))
    out_amount = int(quote.get("outAmount", 0))
    impact = _finite(quote.get("priceImpactPct", 0), "price impact")
    if in_amount != raw_amount or out_amount <= 0:
        raise ValueError("Jupiter quote amount mismatch")
    if impact > MAX_PRICE_IMPACT_PERCENT:
        raise ValueError("Price impact exceeds configured safety cap")
    return {
        "provider": "jupiter",
        "input_mint": input_mint.strip(),
        "output_mint": output_mint.strip(),
        "in_amount": in_amount,
        "out_amount": out_amount,
        "price_impact_percent": impact,
        "slippage_bps": slip,
        "route_plan": quote.get("routePlan", []),
        "context_slot": quote.get("contextSlot"),
        "quoted_at": time.time(),
        "raw_quote": quote
    }


def build_execution_plan(quote, side, max_quote_age=MAX_QUOTE_AGE_SECONDS):
    """Validate and freeze a quote into an auditable, non-executable plan."""
    if not isinstance(quote, dict) or quote.get("provider") != "jupiter":
        raise ValueError("Unsupported quote provider")
    age = time.time() - float(quote.get("quoted_at", 0))
    if age < 0 or age > min(float(max_quote_age), MAX_QUOTE_AGE_SECONDS):
        raise ValueError("Quote is stale")
    if side not in ("BUY", "SELL"):
        raise ValueError("Invalid swap side")
    if int(quote.get("in_amount", 0)) <= 0 or int(quote.get("out_amount", 0)) <= 0:
        raise ValueError("Invalid quote amounts")
    return {
        "status": "READY_FOR_REVIEW",
        "side": side,
        "provider": quote["provider"],
        "input_mint": quote["input_mint"],
        "output_mint": quote["output_mint"],
        "in_amount": int(quote["in_amount"]),
        "minimum_out_amount": int(quote["out_amount"]) * (10000 - int(quote["slippage_bps"])) // 10000,
        "price_impact_percent": float(quote["price_impact_percent"]),
        "context_slot": quote.get("context_slot"),
        "quoted_at": quote["quoted_at"],
        "expires_at": quote["quoted_at"] + MAX_QUOTE_AGE_SECONDS,
        "execution_locked": True
    }


def execute_plan(plan):
    """Fail closed. A future live adapter must replace this behind formal review."""
    if not isinstance(plan, dict) or not plan.get("execution_locked"):
        raise LiveExecutionLocked("LIVE EXECUTION LOCKED")
    raise LiveExecutionLocked(
        "LIVE EXECUTION LOCKED: quote preparation is available; signing and broadcast are disabled."
    )
