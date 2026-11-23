"""Non-custodial legacy SPL Associated Token Account manager.

Builds and simulates an unsigned idempotent ATA-create transaction. The
server never signs or broadcasts; the user's wallet pays rent and fees.
Token-2022 is deliberately rejected until extension-aware support is complete.
"""
import base64
import os

import solana_onchain as chain

SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
TOKEN_ACCOUNT_SIZE = 165
LAMPORTS_PER_SOL = 1_000_000_000
MAX_TRANSACTION_BYTES = 1232


def _pubkey(value, label):
    if not isinstance(value, str) or not value or len(value) > 44:
        raise ValueError(label + " must be a valid Solana public key")
    try:
        raw = chain._b58decode(value)
    except (ValueError, TypeError, IndexError):
        raise ValueError(label + " must be a valid Solana public key")
    if len(raw) != 32 or chain._b58encode(raw) != value:
        raise ValueError(label + " must be a valid Solana public key")
    return raw


def _shortvec(value):
    result = bytearray()
    while True:
        item = value & 0x7f
        value >>= 7
        if value:
            item |= 0x80
        result.append(item)
        if not value:
            return bytes(result)


def _compile_create_message(payer, ata, owner, mint, token_program, blockhash):
    # Associated Token Program CreateIdempotent instruction: discriminator 1.
    program = ASSOCIATED_TOKEN_PROGRAM_ID
    metas = [
        (payer, True, True),
        (ata, False, True),
        (owner, False, False),
        (mint, False, False),
        (SYSTEM_PROGRAM_ID, False, False),
        (token_program, False, False),
    ]
    merged = {payer: (True, True)}
    for key, signer, writable in metas:
        old = merged.get(key, (False, False))
        merged[key] = (old[0] or signer, old[1] or writable)
    merged[program] = (False, False)
    ordered = [payer]
    ordered += [k for k, v in merged.items() if k != payer and v == (True, True)]
    ordered += [k for k, v in merged.items() if k != payer and v == (True, False)]
    ordered += [k for k, v in merged.items() if k != payer and v == (False, True)]
    ordered += [k for k, v in merged.items() if k != payer and v == (False, False)]
    if len(ordered) > 256:
        raise ValueError("Legacy transaction account key limit exceeded")
    index = {key: i for i, key in enumerate(ordered)}
    readonly_signed = sum(1 for signer, writable in merged.values() if signer and not writable)
    readonly_unsigned = sum(1 for signer, writable in merged.values() if not signer and not writable)
    account_indexes = bytes(index[key] for key, _, _ in metas)
    instruction = (
        bytes([index[program]]) + _shortvec(len(account_indexes)) + account_indexes
        + _shortvec(1) + bytes([1])
    )
    message = (
        bytes([1, readonly_signed, readonly_unsigned])
        + _shortvec(len(ordered))
        + b"".join(_pubkey(key, "Account key") for key in ordered)
        + _pubkey(blockhash, "Recent blockhash")
        + _shortvec(1) + instruction
    )
    transaction = _shortvec(1) + (bytes(64)) + message
    if len(transaction) > MAX_TRANSACTION_BYTES:
        raise ValueError("Unsigned ATA transaction exceeds Solana packet size")
    return message, transaction, ordered


def _rpc_account(pubkey):
    result = chain._rpc("getAccountInfo", [
        pubkey, {"encoding": "base64", "commitment": "confirmed"}
    ])
    value = (result or {}).get("value")
    if value is None:
        return None
    try:
        raw = base64.b64decode(value["data"][0], validate=True)
    except Exception:
        raise ValueError("RPC returned malformed account data")
    return {"owner": value.get("owner"), "data": raw, "lamports": value.get("lamports")}


def _integer_rpc(method, params, label):
    value = chain._rpc(method, params)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("RPC returned an invalid " + label)
    return value


def create_ata_order(wallet, mint, min_sol_reserve_lamports=None):
    """Return an unsigned, simulated ATA-create order; never signs or broadcasts."""
    _pubkey(wallet, "Wallet")
    _pubkey(mint, "Mint")
    if min_sol_reserve_lamports is None:
        raw = os.getenv("TROJAN_MIN_SOL_RESERVE_LAMPORTS", "10000000")
        if not raw.isdigit():
            raise ValueError("TROJAN_MIN_SOL_RESERVE_LAMPORTS must be a non-negative integer")
        reserve = int(raw)
    elif isinstance(min_sol_reserve_lamports, bool) or not isinstance(min_sol_reserve_lamports, int) or min_sol_reserve_lamports < 0:
        raise ValueError("Minimum SOL reserve must be a non-negative integer")
    else:
        reserve = min_sol_reserve_lamports

    mint_account = _rpc_account(mint)
    if mint_account is None:
        raise ValueError("Mint account does not exist")
    if mint_account["owner"] != chain.TOKEN_PROGRAM:
        raise ValueError("Only legacy SPL Token mints are supported; Token-2022 is rejected")
    if len(mint_account["data"]) != 82 or mint_account["data"][45] != 1:
        raise ValueError("Mint account has an invalid or uninitialized legacy SPL layout")

    ata = chain._find_program_address(
        [_pubkey(wallet, "Wallet"), _pubkey(chain.TOKEN_PROGRAM, "Token program"),
         _pubkey(mint, "Mint")],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )[0]
    existing = _rpc_account(ata)
    if existing is not None:
        if existing["owner"] != chain.TOKEN_PROGRAM or len(existing["data"]) != TOKEN_ACCOUNT_SIZE:
            raise ValueError("Canonical ATA address exists with an unexpected owner or layout")
        raw = existing["data"]
        if chain._key(raw, 0) != mint or chain._key(raw, 32) != wallet or raw[108] != 1:
            raise ValueError("Existing canonical ATA does not match the wallet, mint and initialized state")
        return {
            "status": "ATA_ALREADY_EXISTS", "created": False, "ata": ata,
            "wallet": wallet, "mint": mint, "rent_lamports": 0,
            "fee_lamports": 0, "transaction": None,
            "signing": "NOT_PERFORMED", "broadcast": "NOT_PERFORMED",
            "side_effects": "READ_ONLY_RPC_ONLY",
        }

    wallet_balance_result = chain._rpc("getBalance", [wallet, {"commitment": "confirmed"}])
    wallet_balance = (wallet_balance_result or {}).get("value")
    if isinstance(wallet_balance, bool) or not isinstance(wallet_balance, int) or wallet_balance < 0:
        raise ValueError("RPC returned an invalid wallet SOL balance")
    rent = _integer_rpc("getMinimumBalanceForRentExemption", [
        TOKEN_ACCOUNT_SIZE, {"commitment": "confirmed"}
    ], "ATA rent exemption")
    latest = chain._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    latest_value = (latest or {}).get("value") or {}
    blockhash = latest_value.get("blockhash")
    last_valid_height = latest_value.get("lastValidBlockHeight")
    _pubkey(blockhash, "Latest blockhash")
    if isinstance(last_valid_height, bool) or not isinstance(last_valid_height, int) or last_valid_height <= 0:
        raise ValueError("RPC latest blockhash response is missing lastValidBlockHeight")

    message, transaction, account_keys = _compile_create_message(
        wallet, ata, wallet, mint, chain.TOKEN_PROGRAM, blockhash
    )
    message_b64 = base64.b64encode(message).decode("ascii")
    fee_result = chain._rpc("getFeeForMessage", [
        message_b64, {"commitment": "confirmed"}
    ])
    fee = (fee_result or {}).get("value")
    if isinstance(fee, bool) or not isinstance(fee, int) or fee < 0:
        raise ValueError("RPC could not provide a reliable transaction fee estimate")
    required = rent + fee + reserve
    if wallet_balance < required:
        raise ValueError("Wallet SOL balance is insufficient for ATA rent, network fee and configured reserve")

    transaction_b64 = base64.b64encode(transaction).decode("ascii")
    simulation = chain._rpc("simulateTransaction", [
        transaction_b64,
        {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True,
         "commitment": "confirmed", "innerInstructions": False},
    ])
    sim_value = (simulation or {}).get("value")
    if not isinstance(sim_value, dict):
        raise ValueError("RPC returned a malformed ATA transaction simulation")
    if sim_value.get("err") is not None:
        raise ValueError("ATA transaction simulation failed: " + str(sim_value.get("err"))[:160])
    return {
        "status": "UNSIGNED_ATA_ORDER_SIMULATED",
        "created": False,
        "wallet": wallet,
        "mint": mint,
        "ata": ata,
        "token_program": chain.TOKEN_PROGRAM,
        "associated_token_program": ASSOCIATED_TOKEN_PROGRAM_ID,
        "instruction": "CreateIdempotent",
        "transaction": transaction_b64,
        "transaction_version": "legacy",
        "required_signers": [wallet],
        "account_keys": account_keys,
        "wallet_sol_balance_lamports": wallet_balance,
        "rent_lamports": rent,
        "network_fee_lamports": fee,
        "minimum_sol_reserve_lamports": reserve,
        "required_total_lamports": required,
        "last_valid_block_height": last_valid_height,
        "simulation": {"status": "PASS", "units_consumed": sim_value.get("unitsConsumed"),
                       "logs": [str(line)[:300] for line in (sim_value.get("logs") or [])[-20:]]},
        "signing": "EXTERNAL_WALLET_REQUIRED",
        "broadcast": "DISABLED",
        "side_effects": "RPC_READ_AND_UNSIGNED_TRANSACTION_CONSTRUCTION_ONLY",
        "live_trading_ready": False,
    }
