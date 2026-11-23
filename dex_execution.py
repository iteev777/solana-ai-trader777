"""Non-custodial Jupiter Swap API V2 order/execute adapter.

The server never receives or stores a wallet secret. /order returns an unsigned
transaction for the user's wallet to sign. /execute is disabled by default and
requires both an explicit per-call confirmation and an environment feature flag.
"""
import base64
import os
import time

import requests

BASE_URL = os.getenv("TROJAN_JUPITER_V2_BASE_URL", "https://api.jup.ag/swap/v2").rstrip("/")
API_KEY = os.getenv("JUPITER_API_KEY", "").strip()
TIMEOUT = min(15.0, max(2.0, float(os.getenv("TROJAN_JUPITER_EXECUTION_TIMEOUT", "8"))))
MAX_SLIPPAGE_BPS = min(300, max(1, int(os.getenv("TROJAN_MAX_SLIPPAGE_BPS", "100"))))
MAX_SIGNED_TRANSACTION_BYTES = 15_000
EXECUTE_ENABLED = os.getenv("TROJAN_JUPITER_EXECUTE_ENABLED", "false").strip().lower() in ("1", "true", "yes")


class ExecutionLocked(RuntimeError):
    pass


def _headers(json_body=False):
    headers = {"Accept": "application/json"}
    if json_body:
        headers["Content-Type"] = "application/json"
    if API_KEY:
        headers["x-api-key"] = API_KEY
    return headers


def _uint(value, name, minimum=1, maximum=2**64 - 1):
    if isinstance(value, bool):
        raise ValueError(name + " must be an integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        raise ValueError(name + " must be an integer")
    if number < minimum or number > maximum:
        raise ValueError(name + " is outside the allowed range")
    return number


def _base58_pubkey(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " is required")
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    try:
        for char in value.strip():
            number = number * 58 + alphabet.index(char)
    except ValueError:
        raise ValueError(name + " is not a valid base58 public key")
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    decoded = b"\x00" * (len(value.strip()) - len(value.strip().lstrip("1"))) + raw
    if len(decoded) != 32:
        raise ValueError(name + " must decode to a 32-byte Solana public key")
    return value.strip()


def _decode_transaction(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(name + " is missing")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        raise ValueError(name + " must be valid base64")
    if not raw or len(raw) > MAX_SIGNED_TRANSACTION_BYTES:
        raise ValueError(name + " has an invalid transaction size")
    return raw


def create_order(input_mint, output_mint, amount, taker, slippage_bps=None):
    """Request a Jupiter V2 best-route order and unsigned wallet transaction."""
    input_mint = _base58_pubkey(input_mint, "input_mint")
    output_mint = _base58_pubkey(output_mint, "output_mint")
    if input_mint == output_mint:
        raise ValueError("Input and output mints must differ")
    amount = _uint(amount, "amount")
    taker = _base58_pubkey(taker, "taker")
    slip = MAX_SLIPPAGE_BPS if slippage_bps is None else _uint(
        slippage_bps, "slippage_bps", minimum=0, maximum=MAX_SLIPPAGE_BPS
    )

    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "taker": taker,
        "slippageBps": str(slip),
        "maxSupportedTransactionVersion": "0",
    }
    started = time.perf_counter()
    response = requests.get(
        BASE_URL + "/order", params=params, headers=_headers(), timeout=TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Jupiter order response is not an object")

    if payload.get("transaction") in (None, ""):
        raise ValueError(
            "Jupiter did not build a signable transaction: "
            + str(payload.get("errorMessage") or payload.get("errorCode") or "unknown reason")[:160]
        )
    unsigned_bytes = _decode_transaction(payload["transaction"], "unsigned transaction")
    request_id = payload.get("requestId")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("Jupiter order response is missing requestId")

    in_amount = _uint(payload.get("inAmount"), "Jupiter inAmount")
    out_amount = _uint(payload.get("outAmount"), "Jupiter outAmount")
    if in_amount != amount:
        raise ValueError("Jupiter order input amount does not match request")
    minimum_out = payload.get("otherAmountThreshold")
    minimum_out = _uint(minimum_out, "Jupiter minimum output") if minimum_out not in (None, "") else None
    if minimum_out is not None and minimum_out > out_amount:
        raise ValueError("Jupiter minimum output exceeds quoted output")

    return {
        "provider": "jupiter",
        "api_version": "v2",
        "mode": payload.get("mode"),
        "router": payload.get("router"),
        "request_id": request_id,
        "transaction": payload["transaction"],
        "transaction_bytes": len(unsigned_bytes),
        "transaction_version": payload.get("transactionVersion", 0),
        "input_mint": input_mint,
        "output_mint": output_mint,
        "taker": taker,
        "input_amount": in_amount,
        "output_amount": out_amount,
        "minimum_output_amount": minimum_out,
        "price_impact_pct": payload.get("priceImpact"),
        "route_plan": payload.get("routePlan") or [],
        "fee_bps": payload.get("feeBps"),
        "quoted_at": time.time(),
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "signing": "USER_WALLET_REQUIRED",
        "execution": "LOCKED",
    }


def execute_signed_order(signed_transaction, request_id, confirmed=False):
    """Submit a wallet-signed order only after two independent live-trade gates."""
    if not EXECUTE_ENABLED:
        raise ExecutionLocked("Jupiter execution is disabled by TROJAN_JUPITER_EXECUTE_ENABLED")
    if confirmed is not True:
        raise ExecutionLocked("Explicit live execution confirmation is required")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise ValueError("request_id is invalid")
    raw = _decode_transaction(signed_transaction, "signed transaction")
    if len(raw) < 65:
        raise ValueError("signed transaction is too short to contain a signature")

    response = requests.post(
        BASE_URL + "/execute",
        json={"signedTransaction": signed_transaction, "requestId": request_id.strip()},
        headers=_headers(json_body=True),
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Jupiter execute response is not an object")
    status = payload.get("status")
    if status not in ("Success", "Failed"):
        raise ValueError("Jupiter returned an unknown execution status")
    return {
        "provider": "jupiter",
        "status": status,
        "signature": payload.get("signature"),
        "code": payload.get("code"),
        "input_amount_result": payload.get("inputAmountResult"),
        "output_amount_result": payload.get("outputAmountResult"),
        "error": str(payload.get("error") or "")[:240] or None,
        "explorer_url": (
            "https://solscan.io/tx/" + payload["signature"]
            if isinstance(payload.get("signature"), str) and payload["signature"]
            else None
        ),
    }


def get_execution_status():
    return {
        "provider": "Jupiter Swap API V2",
        "order_endpoint": BASE_URL + "/order",
        "execute_endpoint": BASE_URL + "/execute",
        "api_key_configured": bool(API_KEY),
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
        "execution_enabled": EXECUTE_ENABLED,
        "execution_locked": not EXECUTE_ENABLED,
        "wallet_secret_required_on_server": False,
        "signing": "external_user_wallet",
        "transaction_landing": "Jupiter managed /execute",
        "max_transaction_bytes": MAX_SIGNED_TRANSACTION_BYTES,
    }
