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
# Solana's serialized transaction packet ceiling. Reject oversized data before parsing.
MAX_SIGNED_TRANSACTION_BYTES = 1232
# Phase 5 live execution is hard-locked in code; environment variables cannot unlock it.
EXECUTE_ENABLED = False
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


def _inspect_order_mints(input_mint, output_mint):
    """Validate both mint owners and supported Token-2022 extensions in one RPC snapshot.

    Jupiter computes route-level transfer fees. Trojan independently checks
    that each mint is a supported standard SPL/Token-2022 mint before allowing
    an unsigned order to become eligible for simulation.
    """
    import solana_onchain
    if not solana_onchain.RPC_URL:
        return {"status": "NOT_CHECKED", "reason": "SOLANA_RPC_NOT_CONFIGURED"}
    accounts, slot = solana_onchain._multiple_accounts_with_context(
        [input_mint, output_mint]
    )
    if len(accounts) != 2 or any(account is None for account in accounts):
        raise ValueError("Jupiter order mint account is missing from the confirmed RPC snapshot")
    inspected = []
    for mint, account in zip((input_mint, output_mint), accounts):
        owner = account.get("owner")
        raw = account.get("data", b"")
        if owner == solana_onchain.TOKEN_PROGRAM:
            if not isinstance(raw, (bytes, bytearray)) or len(raw) != 82 or raw[45] != 1:
                raise ValueError("Jupiter order contains an invalid legacy SPL Token mint")
            inspected.append({"mint": mint, "token_program": owner, "extensions": []})
        elif owner == solana_onchain.TOKEN_2022_PROGRAM:
            import token2022_inspector
            parsed = token2022_inspector.validate_swap_compatible_mint_data(raw)
            inspected.append({
                "mint": mint,
                "token_program": owner,
                "extensions": [item["name"] for item in parsed["extensions"]],
                "transfer_fee_configured": parsed["transfer_fee_config"] is not None,
            })
        else:
            raise ValueError("Jupiter order mint is not owned by a supported SPL Token program")
    return {"status": "PASS", "context_slot": slot, "mints": inspected}


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
        if not isinstance(result, dict) or not isinstance(result.get("value"), dict):
            return {"status": "FAIL", "reason": "RPC_SIMULATION_RESPONSE_INVALID", "signed": False, "broadcast": False}
        value = result["value"]
        if "err" not in value:
            return {"status": "FAIL", "reason": "RPC_SIMULATION_ERROR_FIELD_MISSING", "signed": False, "broadcast": False}
        logs = value.get("logs") or []
        return {
            "status": "PASS" if value["err"] is None else "FAIL",
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
    if minimum_out in (None, ""):
        raise ValueError("Jupiter order response is missing its mandatory minimum output threshold")
    minimum_out = _uint(minimum_out, "Jupiter minimum output")
    if minimum_out > out_amount:
        raise ValueError("Jupiter minimum output exceeds quoted output")

    mint_validation = _inspect_order_mints(input_mint, output_mint)
    simulation = _simulate_transaction(payload["transaction"]) if simulate is True else {
        "status": "NOT_REQUESTED", "signed": False, "broadcast": False
    }
    if simulation.get("status") == "PASS" and mint_validation.get("status") != "PASS":
        simulation = {
            "status": "FAIL",
            "reason": "MINT_VALIDATION_NOT_CONFIRMED",
            "signed": False,
            "broadcast": False,
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
        "mint_validation": mint_validation,
        "price_impact_pct": payload.get("priceImpact"),
        "route_plan": payload.get("routePlan") or [],
        "fee_bps": payload.get("feeBps"),
        "simulation": simulation,
        "simulation_passed": simulation.get("status") == "PASS",
        "execution_eligible": False,
        "execution_lock_reason": "PHASE_5_LIVE_EXECUTION_HARD_LOCK",
        "quoted_at": time.time(),
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "signing": "USER_WALLET_REQUIRED",
        "execution": "LOCKED",
    }


def execute_signed_order(signed_transaction, request_id, confirmed=False):
    """Live Jupiter execution is intentionally unavailable in the current project phase."""
    raise ExecutionLocked(
        "Live trading is hard-locked in Trojan V2. Jupiter /execute cannot be enabled "
        "by environment variables or per-request confirmation."
    )


def get_execution_status():
    return {
        "provider": "Jupiter Swap API V2",
        "order_endpoint": BASE_URL + "/order",
        "execute_endpoint": BASE_URL + "/execute",
        "api_key_configured": bool(API_KEY),
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
        "execution_enabled": False,
        "execution_locked": True,
        "hard_lock": True,
        "unlock_policy": "CODE_CHANGE_AFTER_EXPLICIT_PHASE_5_APPROVAL",
        "wallet_secret_required_on_server": False,
        "signing": "external_user_wallet",
        "transaction_landing": "Jupiter managed /execute",
        "max_transaction_bytes": MAX_SIGNED_TRANSACTION_BYTES,
        "simulation_required_for_execution": True,
        "simulated_order_ttl_seconds": ORDER_TTL_SECONDS,
    }
