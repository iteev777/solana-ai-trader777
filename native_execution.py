"""Safety-gated broadcast of externally signed native Solana transactions.

No private key or signing implementation is accepted here. A transaction can
only be broadcast when its exact message matches a fresh, single-use approval
created after successful unsigned simulation, the external wallet signature is
present, the blockhash is still valid, signed simulation passes, and both an
environment gate and explicit request confirmation are enabled.
"""
import base64
import hashlib
import os
import secrets
import threading
import time

import solana_onchain as chain


APPROVAL_TTL_SECONDS = 120
MAX_APPROVALS = 512
MAX_TRANSACTION_BYTES = 1232
_lock = threading.Lock()
_approvals = {}


class NativeExecutionLocked(RuntimeError):
    pass


class NativeExecutionRejected(ValueError):
    pass


def _enabled():
    return os.getenv("TROJAN_NATIVE_EXECUTE_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _decode_transaction(value):
    if not isinstance(value, str) or not value:
        raise NativeExecutionRejected("Signed transaction base64 is required")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise NativeExecutionRejected("Signed transaction is not valid base64")
    if len(raw) < 100 or len(raw) > MAX_TRANSACTION_BYTES:
        raise NativeExecutionRejected("Signed transaction size is outside Solana packet limits")
    return raw


def _read_shortvec(raw, offset):
    value = 0
    shift = 0
    for _ in range(3):
        if offset >= len(raw):
            raise NativeExecutionRejected("Transaction shortvec is truncated")
        byte = raw[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise NativeExecutionRejected("Transaction shortvec is invalid or too large")


def _split_transaction(raw, require_signature):
    signature_count, offset = _read_shortvec(raw, 0)
    if signature_count != 1:
        raise NativeExecutionRejected("Native order must contain exactly one wallet signature")
    end = offset + signature_count * 64
    if end >= len(raw):
        raise NativeExecutionRejected("Transaction signature or message is truncated")
    signature = raw[offset:end]
    message = raw[end:]
    if require_signature and signature == bytes(64):
        raise NativeExecutionRejected("External wallet signature is missing")
    if not require_signature and signature != bytes(64):
        raise NativeExecutionRejected("Approval transaction must be unsigned")
    if not message or message[0] & 0x80:
        raise NativeExecutionRejected("Only legacy Solana messages are supported by native execution")
    return signature, message


def register_native_approval(unsigned_transaction, protocol, last_valid_block_height):
    """Cache exact unsigned message after caller has confirmed simulation PASS."""
    if protocol not in ("raydium_cpmm", "pumpswap"):
        raise NativeExecutionRejected("Unsupported native transaction protocol")
    if isinstance(last_valid_block_height, bool) or not isinstance(last_valid_block_height, int) or last_valid_block_height <= 0:
        raise NativeExecutionRejected("Valid block height is required")
    raw = _decode_transaction(unsigned_transaction)
    _, message = _split_transaction(raw, require_signature=False)
    now = time.monotonic()
    request_id = secrets.token_urlsafe(24)
    entry = {
        "message": message,
        "message_sha256": hashlib.sha256(message).hexdigest(),
        "protocol": protocol,
        "last_valid_block_height": last_valid_block_height,
        "expires_at": now + APPROVAL_TTL_SECONDS,
    }
    with _lock:
        expired = [key for key, value in _approvals.items() if value["expires_at"] <= now]
        for key in expired:
            _approvals.pop(key, None)
        if len(_approvals) >= MAX_APPROVALS:
            oldest = min(_approvals, key=lambda key: _approvals[key]["expires_at"])
            _approvals.pop(oldest, None)
        _approvals[request_id] = entry
    return {
        "request_id": request_id,
        "approval_ttl_seconds": APPROVAL_TTL_SECONDS,
        "message_sha256": entry["message_sha256"],
        "approval": "SIMULATED_SINGLE_USE",
    }


def get_native_execution_status():
    return {
        "enabled": _enabled(),
        "environment_variable": "TROJAN_NATIVE_EXECUTE_ENABLED",
        "default": False,
        "requires_external_wallet_signature": True,
        "requires_explicit_confirmation_header": True,
        "requires_exact_simulated_message_match": True,
        "requires_signed_rpc_simulation": True,
        "approval_ttl_seconds": APPROVAL_TTL_SECONDS,
        "approval_cache": "process_local_single_use",
        "supported_protocols": ["raydium_cpmm", "pumpswap"],
        "private_key_custody": False,
        "server_signing": False,
        "broadcast_available": _enabled(),
    }


def execute_native_signed_transaction(signed_transaction, request_id, confirmed=False):
    """Verify exact message and external signature, then broadcast once if gated."""
    if not _enabled():
        raise NativeExecutionLocked("Native live execution is disabled by TROJAN_NATIVE_EXECUTE_ENABLED")
    if confirmed is not True:
        raise NativeExecutionLocked("Explicit native live execution confirmation is required")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise NativeExecutionRejected("A valid single-use native request_id is required")

    raw = _decode_transaction(signed_transaction)
    signature, message = _split_transaction(raw, require_signature=True)
    now = time.monotonic()
    with _lock:
        approval = _approvals.pop(request_id, None)
    if approval is None:
        raise NativeExecutionRejected("Native transaction approval is missing, expired or already consumed")
    if approval["expires_at"] <= now:
        raise NativeExecutionRejected("Native transaction approval has expired")
    if not secrets.compare_digest(message, approval["message"]):
        raise NativeExecutionRejected("Signed transaction message differs from the simulated approval")

    try:
        current_height = chain._rpc("getBlockHeight", [{"commitment": "confirmed"}])
    except Exception as error:
        raise NativeExecutionRejected("Could not verify recent block height: " + str(error)[:160])
    if isinstance(current_height, bool) or not isinstance(current_height, int):
        raise NativeExecutionRejected("RPC returned an invalid block height")
    if current_height > approval["last_valid_block_height"]:
        raise NativeExecutionRejected("Approved transaction blockhash has expired")

    simulation = chain._rpc("simulateTransaction", [
        signed_transaction,
        {
            "encoding": "base64",
            "sigVerify": True,
            "replaceRecentBlockhash": False,
            "commitment": "confirmed",
            "innerInstructions": False,
        },
    ])
    value = (simulation or {}).get("value")
    if not isinstance(value, dict):
        raise NativeExecutionRejected("Signed transaction simulation returned a malformed result")
    if value.get("err") is not None:
        raise NativeExecutionRejected("Signed transaction simulation failed: " + str(value.get("err"))[:200])

    try:
        signature_result = chain._rpc("sendTransaction", [
            signed_transaction,
            {
                "encoding": "base64",
                "skipPreflight": False,
                "preflightCommitment": "confirmed",
                "maxRetries": 0,
            },
        ])
    except Exception as error:
        raise NativeExecutionRejected("Native transaction broadcast failed: " + str(error)[:200])

    if not isinstance(signature_result, str) or not signature_result:
        raise NativeExecutionRejected("RPC did not return a transaction signature")
    expected_signature = chain._b58encode(signature)
    if signature_result != expected_signature:
        raise NativeExecutionRejected("RPC returned a signature that does not match the externally signed transaction")
    return {
        "submitted": True,
        "confirmed": False,
        "status": "SUBMITTED_NOT_CONFIRMED",
        "signature": signature_result,
        "protocol": approval["protocol"],
        "message_sha256": approval["message_sha256"],
        "units_consumed": value.get("unitsConsumed"),
        "approval_consumed": True,
        "server_signed": False,
        "private_key_custody": False,
    }
