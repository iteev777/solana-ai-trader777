"""Unsigned native Meteora DBC exact-input swap builder.

Mirrors the direct DBC swap account order and supports the audited Token-2022
transfer-fee subset for flat-fee, non-migrated pools. Transfer-hook pools and
unsupported mint extensions remain blocked. Signing and broadcast are disabled.
"""
import base64
import struct

import solana_onchain as chain
import meteora_dbc_quote as dbc
import token2022_inspector as token2022
from raydium_cpmm_builder import _compile_legacy_message, _priority_fee_micro_lamports

PROGRAM_ID = chain.PROGRAMS["meteora_dbc"]
TOKEN_PROGRAM_ID = chain.TOKEN_PROGRAM
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
POOL_AUTHORITY = "FhVo3mqL8PW5pH5U2CN4XE33DokiyZnUwuGpH2hmHLuM"
EVENT_AUTHORITY_SEED = b"__event_authority"
SWAP = bytes([248, 198, 158, 145, 225, 117, 135, 200])
MAX_QUOTE_SLOT_LAG = 24


def _key(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(label + " is required")
    try:
        raw = chain._b58decode(value)
    except (ValueError, IndexError):
        raise ValueError(label + " must be a valid Solana public key")
    if len(raw) != 32:
        raise ValueError(label + " must be a valid Solana public key")
    return raw


def _token_account(account, token_program, owner, mint, label, minimum=0):
    return token2022.validate_token_account_for_program(
        account, token_program, mint, owner, label, minimum
    )


def build_meteora_dbc_swap(quote, wallet, user_input_token_account,
                           user_output_token_account,
                           max_quote_slot_lag=MAX_QUOTE_SLOT_LAG):
    if not isinstance(quote, dict) or quote.get("protocol") != "meteora_dbc":
        raise ValueError("A Meteora DBC quote is required")
    if quote.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_CURVE_SNAPSHOT":
        raise ValueError("Quote is not a confirmed Meteora DBC snapshot")
    pool = quote.get("pool_id")
    input_mint, output_mint = quote.get("input_mint"), quote.get("output_mint")
    amount, quote_slot = quote.get("input_amount"), quote.get("slot")
    slippage = quote.get("slippage_bps", 100)
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= 2**64 - 1:
        raise ValueError("Input amount must be a positive u64")
    if isinstance(quote_slot, bool) or not isinstance(quote_slot, int) or quote_slot <= 0:
        raise ValueError("Quote must contain a confirmed slot")
    if isinstance(slippage, bool) or not isinstance(slippage, int) or not 0 <= slippage <= 300:
        raise ValueError("Slippage must be between 0 and 300 bps")
    if isinstance(max_quote_slot_lag, bool) or not isinstance(max_quote_slot_lag, int) or not 0 <= max_quote_slot_lag <= 150:
        raise ValueError("Maximum quote slot lag is invalid")
    for value, label in ((pool, "pool"), (input_mint, "input mint"),
                         (output_mint, "output mint"), (wallet, "wallet"),
                         (user_input_token_account, "input token account"),
                         (user_output_token_account, "output token account")):
        _key(value, label)
    if input_mint == output_mint or user_input_token_account == user_output_token_account:
        raise ValueError("Swap mints and user token accounts must be distinct")

    fresh = dbc.quote_meteora_dbc_pool(pool, input_mint, amount, slippage, output_mint)
    if (not isinstance(fresh, dict)
            or fresh.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_CURVE_SNAPSHOT"
            or fresh.get("pool_id") != pool
            or fresh.get("input_mint") != input_mint
            or fresh.get("output_mint") != output_mint
            or fresh.get("input_amount") != amount):
        raise ValueError("Fresh Meteora DBC quote did not validate pool, pair and amount")
    for field in ("token_program_input", "token_program_output"):
        if quote.get(field) != fresh.get(field):
            raise ValueError("Meteora DBC token program state changed since the supplied quote")
    minimum_out = fresh.get("minimum_out_amount")
    if isinstance(minimum_out, bool) or not isinstance(minimum_out, int) or not 0 < minimum_out <= 2**64 - 1:
        raise ValueError("Fresh DBC minimum output must be a positive u64")
    if minimum_out > fresh.get("out_amount", 0):
        raise ValueError("Fresh DBC minimum output exceeds quoted output")
    fresh_slot = fresh.get("slot")
    if isinstance(fresh_slot, bool) or not isinstance(fresh_slot, int) or fresh_slot < quote_slot:
        raise ValueError("Fresh DBC quote is older than the supplied quote")

    discovered = chain._account(pool)
    if discovered.get("owner") != PROGRAM_ID:
        raise ValueError("VirtualPool is not owned by the Meteora DBC program")
    first = dbc._decode_virtual_pool(discovered.get("data"), chain)
    config_discovery = chain._account(first["config"])
    if config_discovery.get("owner") != PROGRAM_ID:
        raise ValueError("Meteora DBC PoolConfig owner is invalid")
    first_config = dbc._decode_pool_config(config_discovery.get("data"), chain)
    if input_mint == first["base_mint"]:
        input_is_base = True
        expected_output = first_config["quote_mint"]
    elif input_mint == first_config["quote_mint"]:
        input_is_base = False
        expected_output = first["base_mint"]
    else:
        raise ValueError("Input mint does not belong to this DBC pool")
    if output_mint != expected_output:
        raise ValueError("Output mint does not match the opposite DBC pool side")
    state_pairs = (
        ("config", first["config"]),
        ("base_mint", first["base_mint"]),
        ("quote_mint", first_config["quote_mint"]),
        ("base_vault", first["base_vault"]),
        ("quote_vault", first["quote_vault"]),
        ("base_reserve", first["base_reserve"]),
        ("quote_reserve", first["quote_reserve"]),
        ("protocol_base_fee", first["protocol_base_fee"]),
        ("protocol_quote_fee", first["protocol_quote_fee"]),
        ("partner_base_fee", first["partner_base_fee"]),
        ("partner_quote_fee", first["partner_quote_fee"]),
        ("sqrt_price_before_x64", first["sqrt_price"]),
        ("is_migrated", first["is_migrated"]),
        ("activation_point", first["activation_point"]),
        ("pool_type", first["pool_type"]),
        ("cliff_fee_numerator", first_config["cliff_fee_numerator"]),
        ("collect_fee_mode", first_config["collect_fee_mode"]),
        ("sqrt_start_price", first_config["sqrt_start_price"]),
        ("curve_points", first_config["points"]),
    )
    for field, expected in state_pairs:
        if quote.get(field) != fresh.get(field) or fresh.get(field) != expected:
            raise ValueError("Meteora DBC curve/config state changed since the supplied quote")

    keys = [pool, first["config"], first["base_mint"], first_config["quote_mint"],
            first["base_vault"], first["quote_vault"],
            user_input_token_account, user_output_token_account]
    accounts, slot = chain._multiple_accounts_with_context(keys)
    if len(accounts) != len(keys) or any(item is None for item in accounts):
        raise ValueError("Confirmed DBC transaction snapshot is missing a required account")
    (pool_acc, config_acc, base_mint_acc, quote_mint_acc, base_vault_acc,
     quote_vault_acc, user_in_acc, user_out_acc) = accounts
    if pool_acc.get("owner") != PROGRAM_ID or config_acc.get("owner") != PROGRAM_ID:
        raise ValueError("Meteora DBC pool/config owner changed during snapshot")
    current = dbc._decode_virtual_pool(pool_acc.get("data"), chain)
    config = dbc._decode_pool_config(config_acc.get("data"), chain)
    for field in ("config", "base_mint", "base_vault", "quote_vault", "sqrt_price",
                  "activation_point", "pool_type", "is_migrated"):
        if current[field] != first[field]:
            raise ValueError("Meteora DBC VirtualPool changed during transaction preparation")
    for field in ("quote_mint", "cliff_fee_numerator", "collect_fee_mode",
                  "sqrt_start_price", "points"):
        if config[field] != first_config[field]:
            raise ValueError("Meteora DBC PoolConfig changed during transaction preparation")
    if slot < fresh_slot or slot - fresh_slot > max_quote_slot_lag:
        raise ValueError("Fresh DBC quote is stale relative to transaction snapshot")
    base_token_program = base_mint_acc.get("owner")
    quote_token_program = quote_mint_acc.get("owner")
    supported_programs = (TOKEN_PROGRAM_ID, chain.TOKEN_2022_PROGRAM)
    if base_token_program not in supported_programs or quote_token_program not in supported_programs:
        raise ValueError("Meteora DBC mint uses an unsupported token program")
    if (base_vault_acc.get("owner") != base_token_program
            or quote_vault_acc.get("owner") != quote_token_program):
        raise ValueError("Meteora DBC vault token program does not match its mint")
    for mint_account, token_program, label in (
        (base_mint_acc, base_token_program, "base"),
        (quote_mint_acc, quote_token_program, "quote"),
    ):
        raw = mint_account.get("data", b"")
        if token_program == chain.TOKEN_2022_PROGRAM:
            token2022.validate_swap_compatible_mint_data(raw)
        elif not isinstance(raw, (bytes, bytearray)) or len(raw) != 82 or raw[45] != 1:
            raise ValueError("Meteora DBC " + label + " mint is not an initialized legacy SPL Token mint")
    base_decimals = chain._mint_decimals(base_mint_acc)
    quote_decimals = chain._mint_decimals(quote_mint_acc)
    if base_decimals is None or quote_decimals is None:
        raise ValueError("Meteora DBC mint account layout is invalid")

    pool_authority = POOL_AUTHORITY
    base_balance = _token_account(
        base_vault_acc, base_token_program, pool_authority, current["base_mint"], "DBC base vault", 1
    )
    quote_balance = _token_account(
        quote_vault_acc, quote_token_program, pool_authority, config["quote_mint"], "DBC quote vault", 1
    )
    output_reserve = quote_balance if input_is_base else base_balance
    if fresh.get("output_amount_gross", 0) >= output_reserve:
        raise ValueError("Meteora DBC fresh quote exceeds confirmed output vault balance")
    token_program_input = base_token_program if input_is_base else quote_token_program
    token_program_output = quote_token_program if input_is_base else base_token_program
    if (fresh.get("token_program_input") != token_program_input
            or fresh.get("token_program_output") != token_program_output):
        raise ValueError("Meteora DBC token program state changed during transaction preparation")
    _token_account(user_in_acc, token_program_input, wallet, input_mint,
                   "User input token account", amount)
    _token_account(user_out_acc, token_program_output, wallet, output_mint,
                   "User output token account")
    for mint, account_address, token_program in (
        (input_mint, user_input_token_account, token_program_input),
        (output_mint, user_output_token_account, token_program_output),
    ):
        expected_ata, _ = chain._find_program_address(
            [_key(wallet, "wallet"), _key(token_program, "token program"),
             _key(mint, "mint")],
            ASSOCIATED_TOKEN_PROGRAM_ID,
        )
        if account_address != expected_ata:
            raise ValueError("Meteora DBC user token account is not the canonical associated token account")
    if fresh.get("fee_rate_numerator") != config["cliff_fee_numerator"]:
        raise ValueError("Meteora DBC fee rate changed since the fresh quote")
    expected_fee_on_input = config["collect_fee_mode"] == 0 and not input_is_base
    if fresh.get("fee_on_input") != expected_fee_on_input:
        raise ValueError("Meteora DBC fee collection direction changed")
    event_authority, _ = chain._find_program_address([EVENT_AUTHORITY_SEED], PROGRAM_ID)

    data = SWAP + struct.pack("<QQ", amount, minimum_out)
    metas = [
        {"pubkey": pool_authority, "is_signer": False, "is_writable": False},
        {"pubkey": current["config"], "is_signer": False, "is_writable": False},
        {"pubkey": pool, "is_signer": False, "is_writable": True},
        {"pubkey": user_input_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": user_output_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": current["base_vault"], "is_signer": False, "is_writable": True},
        {"pubkey": current["quote_vault"], "is_signer": False, "is_writable": True},
        {"pubkey": current["base_mint"], "is_signer": False, "is_writable": False},
        {"pubkey": config["quote_mint"], "is_signer": False, "is_writable": False},
        {"pubkey": wallet, "is_signer": True, "is_writable": False},
        {"pubkey": base_token_program, "is_signer": False, "is_writable": False},
        {"pubkey": quote_token_program, "is_signer": False, "is_writable": False},
        {"pubkey": PROGRAM_ID, "is_signer": False, "is_writable": False},
        {"pubkey": event_authority, "is_signer": False, "is_writable": False},
        {"pubkey": PROGRAM_ID, "is_signer": False, "is_writable": False},
    ]
    latest = chain._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    value = (latest or {}).get("value") or {}
    blockhash, last_valid = value.get("blockhash"), value.get("lastValidBlockHeight")
    _key(blockhash, "latest blockhash")
    if isinstance(last_valid, bool) or not isinstance(last_valid, int) or last_valid <= 0:
        raise ValueError("RPC latest blockhash response is incomplete")
    transaction, account_keys = _compile_legacy_message(wallet, blockhash, PROGRAM_ID, metas, data)
    return {
        "provider": "meteora_dbc_native_builder",
        "protocol": "meteora_dbc",
        "pool_id": pool,
        "slot": slot,
        "quote_slot": quote_slot,
        "fresh_quote_slot": fresh_slot,
        "quote_slot_lag": slot - fresh_slot,
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount,
        "minimum_output_amount": minimum_out,
        "token_program_input": token_program_input,
        "token_program_output": token_program_output,
        "token2022_fee_aware_supported": chain.TOKEN_2022_PROGRAM in (token_program_input, token_program_output),
        "instruction": "swap_exact_input",
        "instruction_data_base64": base64.b64encode(data).decode("ascii"),
        "instruction_accounts": [meta["pubkey"] for meta in metas],
        "account_keys": account_keys,
        "required_signers": [wallet],
        "transaction": base64.b64encode(transaction).decode("ascii"),
        "transaction_version": "legacy",
        "compute_unit_limit": 300_000,
        "priority_fee_micro_lamports": _priority_fee_micro_lamports(),
        "last_valid_block_height": last_valid,
        "signature_status": "UNSIGNED_EXTERNAL_WALLET_REQUIRED",
        "execution": "LOCKED",
        "signing": "DISABLED_ON_SERVER",
        "broadcast": "DISABLED",
        "live_trading_ready": False,
    }


def simulate_meteora_dbc_transaction(transaction_base64):
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
