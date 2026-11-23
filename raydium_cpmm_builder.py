"""Unsigned native Raydium CPMM SwapBaseInput transaction builder.

The builder returns a legacy Solana transaction with an empty signature slot.
It never signs, stores keys, broadcasts, or enables live execution. Only legacy
SPL Token pools are accepted until Token-2022 transfer-fee and extension
handling is fully integrated into both quote and execution paths.
"""
import base64
import hashlib
import struct
import time

import solana_onchain as chain


CPMM_PROGRAM_ID = chain.PROGRAMS["raydium_cpmm"]
TOKEN_PROGRAM_ID = chain.TOKEN_PROGRAM
OBSERVATION_SEED = b"observation"
AUTHORITY_SEED = b"vault_and_lp_mint_auth_seed"
MAX_QUOTE_SLOT_LAG = 32


def _u64(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 2**64 - 1:
        raise ValueError(label + " must be a positive u64 integer")
    return value


def _pubkey(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(label + " is required")
    try:
        raw = chain._b58decode(value)
    except (ValueError, IndexError):
        raise ValueError(label + " must be a valid 32-byte Solana public key")
    if len(raw) != 32:
        raise ValueError(label + " must be a valid 32-byte Solana public key")
    return raw


def _shortvec(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("shortvec length must be a non-negative integer")
    encoded = bytearray()
    while True:
        element = value & 0x7F
        value >>= 7
        if value:
            element |= 0x80
        encoded.append(element)
        if not value:
            return bytes(encoded)


def _compile_legacy_message(payer, recent_blockhash, program_id, metas, data):
    """Compile one instruction into a canonical legacy message."""
    _pubkey(payer, "wallet")
    blockhash = _pubkey(recent_blockhash, "recent blockhash")
    _pubkey(program_id, "program id")
    merged = {}

    def add(key, signer=False, writable=False):
        _pubkey(key, "account key")
        current = merged.get(key)
        if current is None:
            merged[key] = {"signer": bool(signer), "writable": bool(writable)}
        else:
            current["signer"] = current["signer"] or bool(signer)
            current["writable"] = current["writable"] or bool(writable)

    add(payer, signer=True, writable=True)
    for meta in metas:
        if not isinstance(meta, dict):
            raise ValueError("instruction account meta must be an object")
        add(meta.get("pubkey"), meta.get("is_signer", False), meta.get("is_writable", False))
    add(program_id, signer=False, writable=False)

    ordered = [payer]
    ordered.extend(key for key, flags in merged.items()
                   if key != payer and flags["signer"] and flags["writable"])
    ordered.extend(key for key, flags in merged.items()
                   if key != payer and flags["signer"] and not flags["writable"])
    ordered.extend(key for key, flags in merged.items()
                   if key != payer and not flags["signer"] and flags["writable"])
    ordered.extend(key for key, flags in merged.items()
                   if key != payer and not flags["signer"] and not flags["writable"])
    if len(ordered) > 256:
        raise ValueError("legacy transaction account key limit exceeded")
    index = {key: position for position, key in enumerate(ordered)}
    required_signatures = sum(1 for flags in merged.values() if flags["signer"])
    readonly_signed = sum(1 for flags in merged.values() if flags["signer"] and not flags["writable"])
    readonly_unsigned = sum(1 for flags in merged.values() if not flags["signer"] and not flags["writable"])
    if required_signatures != 1:
        raise ValueError("Raydium CPMM swap must have exactly one external wallet signer")

    instruction_accounts = bytes(index[meta["pubkey"]] for meta in metas)
    compiled_instruction = (
        bytes([index[program_id]])
        + _shortvec(len(instruction_accounts))
        + instruction_accounts
        + _shortvec(len(data))
        + data
    )
    message = (
        bytes([required_signatures, readonly_signed, readonly_unsigned])
        + _shortvec(len(ordered))
        + b"".join(_pubkey(key, "account key") for key in ordered)
        + blockhash
        + _shortvec(1)
        + compiled_instruction
    )
    transaction = _shortvec(required_signatures) + (b"\x00" * 64) + message
    return transaction, ordered


def _decode_user_token_account(account, expected_owner, expected_mint, label):
    if not isinstance(account, dict) or account.get("owner") != TOKEN_PROGRAM_ID:
        raise ValueError(label + " must be owned by the legacy SPL Token program")
    raw = account.get("data", b"")
    if len(raw) < 165:
        raise ValueError(label + " is missing or has an invalid SPL token account layout")
    if chain._key(raw, 0) != expected_mint:
        raise ValueError(label + " mint does not match the requested swap mint")
    if chain._key(raw, 32) != expected_owner:
        raise ValueError(label + " is not owned by the signing wallet")
    if raw[108] != 1:
        raise ValueError(label + " is not initialized or is frozen")
    return int.from_bytes(raw[64:72], "little")


def build_raydium_cpmm_swap(quote, wallet, user_input_token_account,
                            user_output_token_account, max_quote_slot_lag=MAX_QUOTE_SLOT_LAG):
    """Build, but do not sign or send, a native Raydium CPMM exact-input swap.

    quote must be the complete output from quote_raydium_cpmm_pool. The builder
    re-reads pool/config/vault/mint/user accounts at one confirmed context slot
    and verifies the quote slot has not aged beyond the configured limit.
    Existing user token accounts are required; this function never creates
    ATAs or silently spends SOL to create accounts.
    """
    if not isinstance(quote, dict):
        raise ValueError("A validated Raydium CPMM quote object is required")
    if quote.get("protocol") != "raydium_cpmm" or quote.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT":
        raise ValueError("Quote is not a confirmed Raydium CPMM quote")
    pool_id = quote.get("pool_id")
    input_mint = quote.get("input_mint")
    output_mint = quote.get("output_mint")
    amount_in = _u64(quote.get("input_amount"), "quote input amount")
    requested_slippage = quote.get("slippage_bps", 100)
    if isinstance(requested_slippage, bool) or not isinstance(requested_slippage, int) or not 0 <= requested_slippage <= 300:
        raise ValueError("Quote slippage must be an integer between 0 and 300 bps")
    quote_slot = quote.get("slot")
    if isinstance(quote_slot, bool) or not isinstance(quote_slot, int) or quote_slot <= 0:
        raise ValueError("Quote must include a confirmed positive slot")
    if isinstance(max_quote_slot_lag, bool) or not isinstance(max_quote_slot_lag, int) or not 0 <= max_quote_slot_lag <= 150:
        raise ValueError("Maximum quote slot lag is outside the supported range")

    for key, label in ((pool_id, "pool"), (input_mint, "input mint"),
                       (output_mint, "output mint"), (wallet, "wallet"),
                       (user_input_token_account, "input token account"),
                       (user_output_token_account, "output token account")):
        _pubkey(key, label)
    if input_mint == output_mint:
        raise ValueError("Input and output mints must differ")
    if user_input_token_account == user_output_token_account:
        raise ValueError("Input and output token accounts must differ")

    # Never trust a client-supplied minimum output. Recompute it from a fresh,
    # confirmed on-chain CPMM quote using the same pool, mint, amount and bounded
    # user-selected slippage. The fresh quote supplies the min-out instruction arg.
    fresh_quote = chain.quote_raydium_cpmm_pool(
        pool_id, input_mint, amount_in, requested_slippage
    )
    if (not isinstance(fresh_quote, dict)
            or fresh_quote.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT"
            or fresh_quote.get("output_mint") != output_mint):
        raise ValueError("Fresh Raydium CPMM quote did not validate the requested pair")
    minimum_out = _u64(fresh_quote.get("minimum_out_amount"), "fresh quote minimum output")
    fresh_quote_slot = fresh_quote.get("slot")
    if isinstance(fresh_quote_slot, bool) or not isinstance(fresh_quote_slot, int) or fresh_quote_slot < quote_slot:
        raise ValueError("Fresh Raydium CPMM quote is older than the supplied quote")

    first = chain._account(pool_id)
    if first.get("owner") != CPMM_PROGRAM_ID:
        raise ValueError("Pool account is not owned by the Raydium CPMM program")
    discovered = chain._decode_pool("raydium_cpmm", first["data"])
    if input_mint not in (discovered["base_mint"], discovered["quote_mint"]):
        raise ValueError("Quote input mint does not belong to this CPMM pool")
    expected_output = discovered["quote_mint"] if input_mint == discovered["base_mint"] else discovered["base_mint"]
    if output_mint != expected_output:
        raise ValueError("Quote output mint does not match the pool's opposite mint")
    if discovered["status"] != "active" or discovered["open_time"] > int(time.time()):
        raise ValueError("Raydium CPMM pool is paused or not open")

    authority, _ = chain._find_program_address([AUTHORITY_SEED], CPMM_PROGRAM_ID)
    observation, _ = chain._find_program_address([OBSERVATION_SEED, _pubkey(pool_id, "pool")], CPMM_PROGRAM_ID)
    keys = [
        pool_id, discovered["amm_config"], discovered["vault_base"],
        discovered["vault_quote"], discovered["base_mint"], discovered["quote_mint"],
        user_input_token_account, user_output_token_account, observation,
    ]
    accounts, slot = chain._multiple_accounts_with_context(keys)
    if any(account is None for account in accounts):
        raise ValueError("Confirmed CPMM transaction snapshot is missing a required account")
    (pool_acc, config_acc, vault_base_acc, vault_quote_acc, mint_base_acc,
     mint_quote_acc, user_input_acc, user_output_acc, observation_acc) = accounts

    if pool_acc.get("owner") != CPMM_PROGRAM_ID:
        raise ValueError("Raydium CPMM pool owner changed during transaction snapshot")
    current = chain._decode_pool("raydium_cpmm", pool_acc["data"])
    for field in ("amm_config", "vault_base", "vault_quote", "base_mint",
                  "quote_mint", "open_time", "status"):
        if current.get(field) != discovered.get(field):
            raise ValueError("Raydium CPMM pool relationships changed during snapshot")
    if slot < fresh_quote_slot or slot - fresh_quote_slot > max_quote_slot_lag:
        raise ValueError("Fresh Raydium CPMM quote is stale relative to the transaction snapshot")
    if current["status"] != "active" or current["open_time"] > int(time.time()):
        raise ValueError("Raydium CPMM pool is paused or not yet open")

    if config_acc.get("owner") != CPMM_PROGRAM_ID:
        raise ValueError("AmmConfig is not owned by the Raydium CPMM program")
    chain._decode_raydium_amm_config(config_acc["data"])
    for mint_acc in (mint_base_acc, mint_quote_acc):
        if mint_acc.get("owner") != TOKEN_PROGRAM_ID:
            raise ValueError("Raydium CPMM native builder currently accepts legacy SPL Token mints only")
    if current["token_program_base"] != TOKEN_PROGRAM_ID or current["token_program_quote"] != TOKEN_PROGRAM_ID:
        raise ValueError("Pool token program does not match the supported legacy SPL Token program")
    if vault_base_acc.get("owner") != TOKEN_PROGRAM_ID or vault_quote_acc.get("owner") != TOKEN_PROGRAM_ID:
        raise ValueError("Raydium CPMM vaults must be owned by the legacy SPL Token program")
    chain._token_amount(vault_base_acc, current["base_mint"])
    chain._token_amount(vault_quote_acc, current["quote_mint"])

    input_balance = _decode_user_token_account(user_input_acc, wallet, input_mint, "Input token account")
    _decode_user_token_account(user_output_acc, wallet, output_mint, "Output token account")
    if input_balance < amount_in:
        raise ValueError("Wallet input token account has insufficient balance")
    if observation_acc.get("owner") != CPMM_PROGRAM_ID:
        raise ValueError("Observation account is not owned by Raydium CPMM")
    observation_raw = observation_acc.get("data", b"")
    if len(observation_raw) < 40 or observation_raw[:8] != chain._discriminator("ObservationState"):
        raise ValueError("Raydium CPMM observation account discriminator/layout is invalid")
    if chain._key(observation_raw, 8) != pool_id:
        raise ValueError("Observation account belongs to a different CPMM pool")

    if input_mint == current["base_mint"]:
        input_vault, output_vault = current["vault_base"], current["vault_quote"]
    else:
        input_vault, output_vault = current["vault_quote"], current["vault_base"]

    instruction_data = (
        hashlib.sha256(b"global:swap_base_input").digest()[:8]
        + struct.pack("<Q", amount_in)
        + struct.pack("<Q", minimum_out)
    )
    metas = [
        {"pubkey": wallet, "is_signer": True, "is_writable": True},
        {"pubkey": authority, "is_signer": False, "is_writable": False},
        {"pubkey": current["amm_config"], "is_signer": False, "is_writable": False},
        {"pubkey": pool_id, "is_signer": False, "is_writable": True},
        {"pubkey": user_input_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": user_output_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": input_vault, "is_signer": False, "is_writable": True},
        {"pubkey": output_vault, "is_signer": False, "is_writable": True},
        {"pubkey": TOKEN_PROGRAM_ID, "is_signer": False, "is_writable": False},
        {"pubkey": TOKEN_PROGRAM_ID, "is_signer": False, "is_writable": False},
        {"pubkey": input_mint, "is_signer": False, "is_writable": False},
        {"pubkey": output_mint, "is_signer": False, "is_writable": False},
        {"pubkey": observation, "is_signer": False, "is_writable": True},
    ]
    latest = chain._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    blockhash_value = ((latest or {}).get("value") or {}).get("blockhash")
    last_valid_height = ((latest or {}).get("value") or {}).get("lastValidBlockHeight")
    _pubkey(blockhash_value, "latest blockhash")
    if isinstance(last_valid_height, bool) or not isinstance(last_valid_height, int) or last_valid_height <= 0:
        raise ValueError("RPC latest blockhash response is missing lastValidBlockHeight")
    transaction, account_keys = _compile_legacy_message(
        wallet, blockhash_value, CPMM_PROGRAM_ID, metas, instruction_data
    )
    return {
        "provider": "raydium_cpmm_native_builder",
        "protocol": "raydium_cpmm",
        "pool_id": pool_id,
        "slot": slot,
        "quote_slot": quote_slot,
        "fresh_quote_slot": fresh_quote_slot,
        "quote_slot_lag": slot - fresh_quote_slot,
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount_in,
        "minimum_output_amount": minimum_out,
        "instruction": "SwapBaseInput",
        "instruction_data_base64": base64.b64encode(instruction_data).decode("ascii"),
        "account_keys": account_keys,
        "required_signers": [wallet],
        "transaction": base64.b64encode(transaction).decode("ascii"),
        "transaction_version": "legacy",
        "last_valid_block_height": last_valid_height,
        "signature_status": "UNSIGNED_EXTERNAL_WALLET_REQUIRED",
        "execution": "LOCKED",
        "signing": "DISABLED_ON_SERVER",
        "broadcast": "DISABLED",
        "live_trading_ready": False,
    }


def simulate_raydium_cpmm_transaction(transaction_base64):
    """Simulate an unsigned native transaction; never sign or broadcast it."""
    if not isinstance(transaction_base64, str) or not transaction_base64:
        raise ValueError("Unsigned transaction base64 is required")
    try:
        raw = base64.b64decode(transaction_base64, validate=True)
    except (ValueError, TypeError):
        raise ValueError("Unsigned transaction is not valid base64")
    if len(raw) < 100 or len(raw) > 1232:
        raise ValueError("Unsigned transaction size is outside Solana packet limits")
    result = chain._rpc("simulateTransaction", [
        transaction_base64,
        {
            "encoding": "base64",
            "sigVerify": False,
            "replaceRecentBlockhash": True,
            "commitment": "confirmed",
            "innerInstructions": False,
        },
    ])
    value = (result or {}).get("value")
    if not isinstance(value, dict):
        raise ValueError("Solana RPC returned a malformed simulation result")
    error = value.get("err")
    logs = value.get("logs")
    if logs is not None and not isinstance(logs, list):
        raise ValueError("Solana RPC returned malformed simulation logs")
    return {
        "status": "PASS" if error is None else "REJECT",
        "error": error,
        "units_consumed": value.get("unitsConsumed"),
        "logs": [str(line)[:300] for line in (logs or [])[-30:]],
        "signature_verification": "DISABLED_UNSIGNED_SIMULATION",
        "broadcast": "DISABLED",
    }
