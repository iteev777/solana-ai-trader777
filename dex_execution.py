"""Non-custodial Jupiter Swap API V2 order/execute adapter.

The server never receives or stores a wallet secret. /order returns an unsigned
transaction for the user's wallet to sign. /execute is disabled by default and
requires both an explicit per-call confirmation and an environment feature flag.
"""
import base64
import os
import time
import threading

import requests

BASE_URL = os.getenv("TROJAN_JUPITER_V2_BASE_URL", "https://api.jup.ag/swap/v2").rstrip("/")
API_KEY = os.getenv("JUPITER_API_KEY", "").strip()
TIMEOUT = min(15.0, max(2.0, float(os.getenv("TROJAN_JUPITER_EXECUTION_TIMEOUT", "8"))))
MAX_SLIPPAGE_BPS = min(300, max(1, int(os.getenv("TROJAN_MAX_SLIPPAGE_BPS", "100"))))
MAX_SIGNED_TRANSACTION_BYTES = 15_000
EXECUTE_ENABLED = os.getenv("TROJAN_JUPITER_EXECUTE_ENABLED", "false").strip().lower() in ("1", "true", "yes")
ORDER_TTL_SECONDS = 120
_order_lock = threading.Lock()
_simulated_orders = {}


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


def _simulate_transaction(transaction):
    """Simulate the unsigned order through Solana RPC; never broadcasts it."""
    try:
        import solana_onchain
        if not solana_onchain.RPC_URL:
            return {"status": "SKIPPED", "reason": "SOLANA_RPC_NOT_CONFIGURED"}
        result = solana_onchain._rpc("simulateTransaction", [
            transaction,
            {
                "encoding": "base64",
                "commitment": "confirmed",
                "sigVerify": False,
                "replaceRecentBlockhash": True,
                "innerInstructions": False,
            },
        ])
        value = (result or {}).get("value") or {}
        logs = value.get("logs") or []
        return {
            "status": "PASS" if value.get("err") is None else "FAIL",
            "error": value.get("err"),
            "units_consumed": value.get("unitsConsumed"),
            "context_slot": (result or {}).get("context", {}).get("slot"),
            "log_tail": [str(line)[:240] for line in logs[-8:]],
            "signed": False,
            "broadcast": False,
        }
    except Exception as error:
        return {"status": "FAIL", "reason": str(error)[:180], "signed": False, "broadcast": False}


def _remember_simulated_order(request_id, details):
    now = time.time()
    with _order_lock:
        expired = [
            key for key, item in _simulated_orders.items()
            if now - item.get("created_at", 0) > ORDER_TTL_SECONDS
        ]
        for key in expired:
            _simulated_orders.pop(key, None)
        if len(_simulated_orders) >= 256:
            oldest = min(_simulated_orders, key=lambda key: _simulated_orders[key].get("created_at", 0))
            _simulated_orders.pop(oldest, None)
        _simulated_orders[request_id] = {**details, "created_at": now}


def create_order(input_mint, output_mint, amount, taker, slippage_bps=None, simulate=False):
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

    simulation = _simulate_transaction(payload["transaction"]) if simulate is True else {
        "status": "NOT_REQUESTED", "signed": False, "broadcast": False
    }
    if simulation.get("status") == "PASS":
        _remember_simulated_order(request_id, {
            "taker": taker,
            "input_mint": input_mint,
            "output_mint": output_mint,
            "input_amount": in_amount,
            "minimum_output_amount": minimum_out,
            "simulation_status": "PASS",
        })

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
        "simulation": simulation,
        "execution_eligible": simulation.get("status") == "PASS",
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
    with _order_lock:
        approved = _simulated_orders.get(request_id.strip())
    if not approved or approved.get("simulation_status") != "PASS":
        raise ExecutionLocked("Order has no recent successful unsigned simulation on this Trojan instance")
    if time.time() - approved.get("created_at", 0) > ORDER_TTL_SECONDS:
        with _order_lock:
            _simulated_orders.pop(request_id.strip(), None)
        raise ExecutionLocked("Simulated order expired; request a fresh order")
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
        "simulation_required_for_execution": True,
        "simulated_order_ttl_seconds": ORDER_TTL_SECONDS,
    }
