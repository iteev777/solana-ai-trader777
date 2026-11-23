"""Unsigned native FluxBeam SwapV1 transaction builder.

FluxBeam uses the SPL Token Swap instruction format (tag 1), not Anchor.
The builder supports legacy and Token-2022 token-program account ordering, but
fails closed for transfer-hook mints and non-zero transfer-fee schedules until
the quote math can model those transfer effects. It never signs or broadcasts.
"""
import base64
import struct
import time

import solana_onchain as chain
from raydium_cpmm_builder import _compile_legacy_message, _priority_fee_micro_lamports
import token2022_inspector as token2022

PROGRAM_ID = chain.PROGRAMS["fluxbeam_amm"]
TOKEN_PROGRAM_ID = chain.TOKEN_PROGRAM
TOKEN_2022_PROGRAM_ID = chain.TOKEN_2022_PROGRAM
SWAP_TAG = 1
MAX_QUOTE_SLOT_LAG = 32
COMPUTE_UNITS = 160_000
UNSUPPORTED_MINT_EXTENSIONS = {4, 6, 9, 10, 12, 16, 24, 25, 26, 28}
SUPPORTED_TOKEN_ACCOUNT_EXTENSIONS = {2, 7}


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


def _u64(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 2**64 - 1:
        raise ValueError(label + " must be a positive u64 integer")
    return value


def _mint_program_and_validate(account, label):
    if not isinstance(account, dict):
        raise ValueError(label + " account is missing")
    owner = account.get("owner")
    if owner not in (TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID):
        raise ValueError(label + " is not owned by a supported SPL token program")
    raw = account.get("data", b"")
    if len(raw) < 82 or raw[45] != 1:
        raise ValueError(label + " mint layout is invalid or uninitialized")
    if owner == TOKEN_2022_PROGRAM_ID:
        try:
            token2022.validate_zero_fee_mint_data(raw)
        except ValueError as exc:
            raise ValueError(label + " is not a supported zero-fee Token-2022 mint: " + str(exc))
    return owner


def _token_account_amount(account, expected_program, expected_mint, expected_owner, label):
    if not isinstance(account, dict) or account.get("owner") != expected_program:
        raise ValueError(label + " token program does not match its mint")
    raw = account.get("data", b"")
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 165:
        raise ValueError(label + " token account layout is invalid")
    if chain._key(raw, 0) != expected_mint:
        raise ValueError(label + " token account mint does not match")
    if expected_owner is not None and chain._key(raw, 32) != expected_owner:
        raise ValueError(label + " token account authority does not match")
    if raw[108] != 1:
        raise ValueError(label + " token account is not initialized or is frozen")
    if expected_program == TOKEN_2022_PROGRAM_ID and len(raw) > 165:
        extensions = token2022.parse_tlv_extensions(raw, token2022.TOKEN_ACCOUNT_TYPE)
        unsupported = {item["type"] for item in extensions} - SUPPORTED_TOKEN_ACCOUNT_EXTENSIONS
        if unsupported:
            raise ValueError(label + " has unsupported Token-2022 account extensions")
    return int.from_bytes(raw[64:72], "little")


def build_fluxbeam_swap(quote, wallet, user_input_token_account,
                        user_output_token_account,
                        max_quote_slot_lag=MAX_QUOTE_SLOT_LAG):
    """Build an unsigned FluxBeam exact-input SwapV1 transaction."""
    if not isinstance(quote, dict) or quote.get("protocol") != "fluxbeam_amm":
        raise ValueError("A validated FluxBeam quote is required")
    if quote.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT":
        raise ValueError("Quote is not a confirmed FluxBeam quote")
    pool = quote.get("pool_id")
    input_mint, output_mint = quote.get("input_mint"), quote.get("output_mint")
    amount = _u64(quote.get("input_amount"), "quote input amount")
    quote_slot = quote.get("slot")
    if isinstance(quote_slot, bool) or not isinstance(quote_slot, int) or quote_slot <= 0:
        raise ValueError("Quote must include a positive confirmed slot")
    if isinstance(max_quote_slot_lag, bool) or not isinstance(max_quote_slot_lag, int) or not 0 <= max_quote_slot_lag <= 150:
        raise ValueError("Maximum quote slot lag is invalid")
    for value, label in (
        (pool, "pool"), (input_mint, "input mint"), (output_mint, "output mint"),
        (wallet, "wallet"), (user_input_token_account, "input token account"),
        (user_output_token_account, "output token account"),
    ):
        _pubkey(value, label)
    if input_mint == output_mint or user_input_token_account == user_output_token_account:
        raise ValueError("Swap mints and token accounts must be distinct")
    slippage = quote.get("slippage_bps", 100)
    if isinstance(slippage, bool) or not isinstance(slippage, int) or not 0 <= slippage <= 300:
        raise ValueError("Quote slippage must be between 0 and 300 bps")

    fresh = chain.quote_fluxbeam_pool(pool, input_mint, amount, slippage)
    if (
        not isinstance(fresh, dict)
        or fresh.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT"
        or fresh.get("pool_id") != pool
        or fresh.get("input_mint") != input_mint
        or fresh.get("output_mint") != output_mint
        or fresh.get("input_amount") != amount
    ):
        raise ValueError("Fresh FluxBeam quote does not validate pool, pair and amount")
    minimum_out = _u64(fresh.get("minimum_out_amount"), "fresh minimum output")
    fresh_slot = fresh.get("slot")
    if isinstance(fresh_slot, bool) or not isinstance(fresh_slot, int) or fresh_slot < quote_slot:
        raise ValueError("Fresh FluxBeam quote is older than the supplied quote")

    discovered = chain._account(pool)
    if discovered.get("owner") != PROGRAM_ID:
        raise ValueError("Pool account is not owned by the FluxBeam program")
    first = chain._decode_pool("fluxbeam_amm", discovered.get("data", b""))
    if not first.get("is_initialized") or first.get("curve_type") != 0:
        raise ValueError("FluxBeam pool is uninitialized or uses an unsupported curve")
    if input_mint not in (first["base_mint"], first["quote_mint"]):
        raise ValueError("Input mint does not belong to this FluxBeam pool")
    expected_output = first["quote_mint"] if input_mint == first["base_mint"] else first["base_mint"]
    if output_mint != expected_output:
        raise ValueError("Output mint does not match the opposite FluxBeam pool mint")

    authority, bump = chain._find_program_address([_pubkey(pool, "pool")], PROGRAM_ID)
    keys = [
        pool, first["vault_base"], first["vault_quote"],
        first["base_mint"], first["quote_mint"], first["pool_mint"],
        first["fee_account"], user_input_token_account, user_output_token_account,
    ]
    accounts, slot = chain._multiple_accounts_with_context(keys)
    if len(accounts) != len(keys) or any(item is None for item in accounts):
        raise ValueError("FluxBeam transaction snapshot is missing a required pool, vault, mint, fee or user account")
    (pool_acc, vault_base, vault_quote, mint_base, mint_quote,
     pool_mint, fee_account, user_input, user_output) = accounts
    if pool_acc.get("owner") != PROGRAM_ID:
        raise ValueError("FluxBeam pool owner changed during transaction snapshot")
    current = chain._decode_pool("fluxbeam_amm", pool_acc.get("data", b""))
    for field in ("vault_base", "vault_quote", "base_mint", "quote_mint",
                  "pool_mint", "fee_account", "trade_fee_numerator",
                  "trade_fee_denominator", "owner_fee_numerator",
                  "owner_fee_denominator", "curve_type", "is_initialized"):
        if current.get(field) != first.get(field):
            raise ValueError("FluxBeam pool state changed during transaction preparation")
    if slot < fresh_slot or slot - fresh_slot > max_quote_slot_lag:
        raise ValueError("Fresh FluxBeam quote is stale relative to transaction snapshot")

    program_base = _mint_program_and_validate(mint_base, "FluxBeam base")
    program_quote = _mint_program_and_validate(mint_quote, "FluxBeam quote")
    program_pool = _mint_program_and_validate(pool_mint, "FluxBeam LP")
    expected_base_decimals = fresh.get("base_decimals")
    expected_quote_decimals = fresh.get("quote_decimals")
    if (chain._mint_decimals(mint_base) != expected_base_decimals
            or chain._mint_decimals(mint_quote) != expected_quote_decimals):
        raise ValueError("FluxBeam mint decimals changed since the fresh quote")
    if vault_base.get("owner") != program_base or vault_quote.get("owner") != program_quote:
        raise ValueError("FluxBeam vault token program does not match its mint")
    if chain._key(vault_base.get("data", b""), 32) != authority or chain._key(vault_quote.get("data", b""), 32) != authority:
        raise ValueError("FluxBeam vault authority does not match the canonical pool PDA")
    _token_account_amount(vault_base, program_base, current["base_mint"], authority, "Base vault")
    _token_account_amount(vault_quote, program_quote, current["quote_mint"], authority, "Quote vault")
    _token_account_amount(fee_account, program_pool, current["pool_mint"], None, "LP fee account")
    input_program = program_base if input_mint == current["base_mint"] else program_quote
    output_program = program_quote if input_mint == current["base_mint"] else program_base
    input_balance = _token_account_amount(
        user_input, input_program, input_mint, wallet, "User input"
    )
    _token_account_amount(user_output, output_program, output_mint, wallet, "User output")
    if input_balance < amount:
        raise ValueError("Wallet input token account has insufficient balance")

    if input_mint == current["base_mint"]:
        source_vault, destination_vault = current["vault_base"], current["vault_quote"]
        source_mint, destination_mint = current["base_mint"], current["quote_mint"]
    else:
        source_vault, destination_vault = current["vault_quote"], current["vault_base"]
        source_mint, destination_mint = current["quote_mint"], current["base_mint"]
    source_program, destination_program = input_program, output_program
    metas = [
        {"pubkey": pool, "is_signer": False, "is_writable": True},
        {"pubkey": authority, "is_signer": False, "is_writable": False},
        {"pubkey": wallet, "is_signer": True, "is_writable": True},
        {"pubkey": user_input_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": source_vault, "is_signer": False, "is_writable": True},
        {"pubkey": destination_vault, "is_signer": False, "is_writable": True},
        {"pubkey": user_output_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": current["pool_mint"], "is_signer": False, "is_writable": True},
        {"pubkey": current["fee_account"], "is_signer": False, "is_writable": True},
        {"pubkey": source_mint, "is_signer": False, "is_writable": False},
        {"pubkey": destination_mint, "is_signer": False, "is_writable": False},
        {"pubkey": source_program, "is_signer": False, "is_writable": False},
        {"pubkey": destination_program, "is_signer": False, "is_writable": False},
        {"pubkey": program_pool, "is_signer": False, "is_writable": False},
    ]
    instruction_data = bytes([SWAP_TAG]) + struct.pack("<QQ", amount, minimum_out)
    latest = chain._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    value = (latest or {}).get("value") or {}
    blockhash, last_valid = value.get("blockhash"), value.get("lastValidBlockHeight")
    _pubkey(blockhash, "latest blockhash")
    if isinstance(last_valid, bool) or not isinstance(last_valid, int) or last_valid <= 0:
        raise ValueError("RPC latest blockhash response is incomplete")
    transaction, account_keys = _compile_legacy_message(
        wallet, blockhash, PROGRAM_ID, metas, instruction_data
    )
    return {
        "provider": "fluxbeam_native_builder",
        "protocol": "fluxbeam_amm",
        "pool_id": pool,
        "slot": slot,
        "quote_slot": quote_slot,
        "fresh_quote_slot": fresh_slot,
        "quote_slot_lag": slot - fresh_slot,
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount,
        "minimum_output_amount": minimum_out,
        "instruction": "Swap",
        "instruction_data_base64": base64.b64encode(instruction_data).decode("ascii"),
        "instruction_accounts": [meta["pubkey"] for meta in metas],
        "account_keys": account_keys,
        "required_signers": [wallet],
        "transaction": base64.b64encode(transaction).decode("ascii"),
        "transaction_version": "legacy",
        "compute_unit_limit": 300_000,
        "priority_fee_micro_lamports": _priority_fee_micro_lamports(),
        "last_valid_block_height": last_valid,
        "authority_bump": bump,
        "compute_units": COMPUTE_UNITS,
        "signature_status": "UNSIGNED_EXTERNAL_WALLET_REQUIRED",
        "execution": "LOCKED",
        "signing": "DISABLED_ON_SERVER",
        "broadcast": "DISABLED",
        "live_trading_ready": False,
    }


def simulate_fluxbeam_transaction(transaction_base64):
    """Simulate an unsigned FluxBeam transaction; never sign or broadcast."""
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
        {"encoding": "base64", "sigVerify": False,
         "replaceRecentBlockhash": True, "commitment": "confirmed"},
    ])
    value = (result or {}).get("value")
    if not isinstance(value, dict):
        raise ValueError("Solana RPC returned a malformed simulation result")
    logs = value.get("logs") or []
    if not isinstance(logs, list):
        raise ValueError("Solana RPC returned malformed simulation logs")
    return {
        "status": "PASS" if value.get("err") is None else "REJECT",
        "error": value.get("err"),
        "units_consumed": value.get("unitsConsumed"),
        "logs": [str(line)[:300] for line in logs[-30:]],
        "signature_verification": "DISABLED_UNSIGNED_SIMULATION",
        "signing": "NOT_PERFORMED",
        "broadcast": "DISABLED",
        "execution": "LOCKED",
    }
