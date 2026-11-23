"""Read-only wallet and token-account preflight for native DEX order preparation.

This module never signs, creates accounts, or submits transactions. It checks
one confirmed RPC snapshot and deliberately supports only the legacy SPL
Token program used by the current native Raydium CPMM and PumpSwap builders.
"""
import os

import solana_onchain as chain

SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
TOKEN_ACCOUNT_SIZE = 165
MINT_ACCOUNT_SIZE = 82
LAMPORTS_PER_SOL = 1_000_000_000


def _pubkey(value, label):
    if not isinstance(value, str) or not value or len(value) > 44:
        raise ValueError(label + " must be a valid Solana public key")
    try:
        raw = chain._b58decode(value)
    except (ValueError, TypeError):
        raise ValueError(label + " must be a valid Solana public key")
    if len(raw) != 32 or chain._b58encode(raw) != value:
        raise ValueError(label + " must be a valid Solana public key")
    return raw


def _positive_amount(value):
    if isinstance(value, bool):
        raise ValueError("Input amount must be a positive integer in token base units")
    if isinstance(value, int):
        amount = value
    elif isinstance(value, str) and value.isdigit():
        amount = int(value)
    else:
        raise ValueError("Input amount must be a positive integer in token base units")
    if amount <= 0 or amount > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("Input amount is outside the supported u64 range")
    return amount


def _decode_token_account(account, expected_mint, wallet, label):
    if account is None:
        return {"exists": False, "valid": False, "error": label + " does not exist"}
    if account.get("owner") != chain.TOKEN_PROGRAM:
        return {
            "exists": True, "valid": False,
            "error": label + " is not owned by the supported legacy SPL Token program",
        }
    raw = account.get("data", b"")
    if len(raw) != TOKEN_ACCOUNT_SIZE:
        return {"exists": True, "valid": False, "error": label + " has an unsupported token-account layout"}
    mint = chain._key(raw, 0)
    authority = chain._key(raw, 32)
    state = raw[108]
    if mint != expected_mint:
        return {"exists": True, "valid": False, "error": label + " mint does not match"}
    if authority != wallet:
        return {"exists": True, "valid": False, "error": label + " is not controlled by the supplied wallet"}
    if state != 1:
        return {"exists": True, "valid": False, "error": label + " is frozen or not initialized"}
    return {
        "exists": True, "valid": True,
        "raw_balance": int.from_bytes(raw[64:72], "little"),
        "state": "INITIALIZED",
    }


def _validate_mint(account, mint, label):
    if account is None:
        return {"valid": False, "error": label + " account does not exist"}
    if account.get("owner") != chain.TOKEN_PROGRAM:
        return {"valid": False, "error": label + " is not a legacy SPL Token mint"}
    raw = account.get("data", b"")
    if len(raw) != MINT_ACCOUNT_SIZE or raw[45] != 1:
        return {"valid": False, "error": label + " has an invalid or uninitialized mint layout"}
    return {"valid": True, "decimals": raw[44]}


def preflight_wallet_swap(wallet, input_mint, output_mint, input_token_account,
                          output_token_account, input_amount,
                          min_sol_reserve_lamports=None):
    """Check wallet funding and existing token accounts without side effects."""
    for value, label in (
        (wallet, "Wallet"), (input_mint, "Input mint"),
        (output_mint, "Output mint"), (input_token_account, "Input token account"),
        (output_token_account, "Output token account"),
    ):
        _pubkey(value, label)
    if input_mint == output_mint:
        raise ValueError("Input and output mints must differ")
    if input_token_account == output_token_account:
        raise ValueError("Input and output token accounts must differ")
    amount = _positive_amount(input_amount)

    if min_sol_reserve_lamports is None:
        raw_reserve = os.getenv("TROJAN_MIN_SOL_RESERVE_LAMPORTS", "10000000")
        if not raw_reserve.isdigit():
            raise ValueError("TROJAN_MIN_SOL_RESERVE_LAMPORTS must be a non-negative integer")
        reserve = int(raw_reserve)
    else:
        if isinstance(min_sol_reserve_lamports, bool) or not isinstance(min_sol_reserve_lamports, int) or min_sol_reserve_lamports < 0:
            raise ValueError("Minimum SOL reserve must be a non-negative integer")
        reserve = min_sol_reserve_lamports

    keys = [wallet, input_token_account, output_token_account, input_mint, output_mint]
    result = chain._rpc("getMultipleAccounts", [
        keys, {"encoding": "base64", "commitment": "confirmed"}
    ])
    values = (result or {}).get("value")
    context = (result or {}).get("context") or {}
    slot = context.get("slot")
    if not isinstance(values, list) or len(values) != len(keys):
        raise ValueError("RPC returned an incomplete wallet preflight snapshot")
    if isinstance(slot, bool) or not isinstance(slot, int) or slot <= 0:
        raise ValueError("RPC wallet preflight snapshot is missing its confirmed context slot")

    decoded = []
    for value in values:
        if value is None:
            decoded.append(None)
        else:
            import base64
            try:
                raw = base64.b64decode(value["data"][0], validate=True)
            except Exception:
                raise ValueError("RPC returned malformed account data")
            decoded.append({
                "owner": value.get("owner"),
                "data": raw,
                "lamports": value.get("lamports"),
            })

    wallet_account, input_account, output_account, input_mint_account, output_mint_account = decoded
    if wallet_account is not None and wallet_account.get("owner") != SYSTEM_PROGRAM_ID:
        raise ValueError("Wallet address is not a system-owned wallet account")
    wallet_lamports = 0 if wallet_account is None else wallet_account.get("lamports")
    if isinstance(wallet_lamports, bool) or not isinstance(wallet_lamports, int) or wallet_lamports < 0:
        raise ValueError("RPC wallet balance is invalid")

    input_info = _decode_token_account(input_account, input_mint, wallet, "Input token account")
    output_info = _decode_token_account(output_account, output_mint, wallet, "Output token account")
    input_mint_info = _validate_mint(input_mint_account, input_mint, "Input mint")
    output_mint_info = _validate_mint(output_mint_account, output_mint, "Output mint")

    checks = {
        "wallet_account_valid": wallet_account is not None and wallet_account.get("owner") == SYSTEM_PROGRAM_ID,
        "input_mint_valid": input_mint_info["valid"],
        "output_mint_valid": output_mint_info["valid"],
        "input_token_account_valid": input_info["valid"],
        "output_token_account_valid": output_info["valid"],
        "input_balance_sufficient": bool(input_info["valid"] and input_info["raw_balance"] >= amount),
        "sol_reserve_sufficient": wallet_lamports >= reserve,
    }
    ready = all(checks.values())
    if not output_info["exists"]:
        output_action = "CREATE_OUTPUT_TOKEN_ACCOUNT_AND_RETRY"
    elif not output_info["valid"]:
        output_action = "FIX_OUTPUT_TOKEN_ACCOUNT_AND_RETRY"
    else:
        output_action = "NONE"

    return {
        "status": "READY_FOR_NATIVE_ORDER_PREPARATION" if ready else "BLOCKED",
        "ready": ready,
        "wallet": wallet,
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount,
        "confirmed_context_slot": slot,
        "wallet_sol_balance_lamports": wallet_lamports,
        "wallet_sol_balance": wallet_lamports / LAMPORTS_PER_SOL,
        "minimum_sol_reserve_lamports": reserve,
        "sol_reserve_sufficient": checks["sol_reserve_sufficient"],
        "input_token_account": input_info,
        "output_token_account": output_info,
        "input_mint_info": input_mint_info,
        "output_mint_info": output_mint_info,
        "checks": checks,
        "output_account_action": output_action,
        "network_fee_estimate": "NOT_ESTIMATED",
        "network_fee_note": "This preflight checks a configurable SOL reserve; it does not estimate the exact transaction fee or rent.",
        "side_effects": "NONE_READ_ONLY_RPC_ONLY",
        "signing": "NOT_PERFORMED",
        "broadcast": "NOT_PERFORMED",
        "supported_token_program": chain.TOKEN_PROGRAM,
    }
