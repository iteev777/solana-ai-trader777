"""Unsigned native Meteora DAMM v2 exact-input swap builder.

The adapter uses the verified 14-account cp-amm swap instruction and supports
legacy SPL Token plus the audited Token-2022 transfer-fee extension subset.
It re-quotes and verifies pool state, vaults, mint programs and canonical user
ATAs before returning an unsigned transaction. Transfer hooks remain blocked.
"""
import base64
import struct

import solana_onchain as chain
import meteora_damm_quote as damm_quote
import token2022_inspector as token2022
from raydium_cpmm_builder import _compile_legacy_message, _priority_fee_micro_lamports

PROGRAM_ID = chain.PROGRAMS["meteora_damm_v2"]
TOKEN_PROGRAM_ID = chain.TOKEN_PROGRAM
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
POOL_AUTHORITY = "HLnpSz9h2S4hiLQ43rnSD9XkcUThA7B8hQMKmDaiTLcC"
EVENT_AUTHORITY_SEED = b"__event_authority"
SWAP_DISCRIMINATOR = bytes([0xF8, 0xC6, 0x9E, 0x91, 0xE1, 0x75, 0x87, 0xC8])
MAX_QUOTE_SLOT_LAG = 32
COMPUTE_UNITS = 180_000


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


def _vault(account, token_program, mint, authority, label):
    amount = token2022.validate_token_account_for_program(
        account, token_program, mint, authority, label, 1
    )
    if amount <= 0:
        raise ValueError(label + " reserve is empty")
    return amount


def _ata(owner, token_program, mint):
    return chain._find_program_address(
        [_pubkey(owner, "ATA owner"), _pubkey(token_program, "ATA token program"),
         _pubkey(mint, "ATA mint")],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )[0]


def build_meteora_damm_v2_swap(quote, wallet, user_input_token_account,
                               user_output_token_account,
                               max_quote_slot_lag=MAX_QUOTE_SLOT_LAG):
    """Build an unsigned Meteora DAMM v2 Swap transaction."""
    if not isinstance(quote, dict) or quote.get("protocol") != "meteora_damm_v2":
        raise ValueError("A validated Meteora DAMM v2 quote is required")
    if quote.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_POOL_SNAPSHOT":
        raise ValueError("Quote is not a confirmed Meteora DAMM v2 quote")
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
    if user_input_token_account == user_output_token_account:
        raise ValueError("Meteora DAMM input and output token accounts must differ")
    slippage = quote.get("slippage_bps", 100)
    if isinstance(slippage, bool) or not isinstance(slippage, int) or not 0 <= slippage <= 300:
        raise ValueError("Quote slippage must be between 0 and 300 bps")

    fresh = damm_quote.quote_meteora_damm_pool(
        pool, input_mint, amount, slippage, output_mint=output_mint
    )
    if (
        not isinstance(fresh, dict)
        or fresh.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_POOL_SNAPSHOT"
        or fresh.get("pool_id") != pool
        or fresh.get("input_mint") != input_mint
        or fresh.get("output_mint") != output_mint
        or fresh.get("input_amount") != amount
    ):
        raise ValueError("Fresh Meteora DAMM quote does not validate pool, pair and amount")
    for field in ("token_program_input", "token_program_output"):
        if quote.get(field) != fresh.get(field):
            raise ValueError("Meteora DAMM token program state changed since the supplied quote")
    minimum_out = _u64(fresh.get("minimum_out_amount"), "fresh minimum output")
    fresh_out = _u64(fresh.get("out_amount"), "fresh quoted output")
    if minimum_out > fresh_out:
        raise ValueError("Fresh Meteora DAMM minimum output exceeds its quoted output")
    fresh_slot = fresh.get("slot")
    if isinstance(fresh_slot, bool) or not isinstance(fresh_slot, int) or fresh_slot < quote_slot:
        raise ValueError("Fresh Meteora DAMM quote is older than the supplied quote")

    discovered = chain._account(pool)
    if discovered.get("owner") != PROGRAM_ID:
        raise ValueError("Pool account is not owned by the Meteora DAMM v2 program")
    first = damm_quote._decode_pool(discovered.get("data"), chain)
    if first["pool_status"] != 0 or first["liquidity"] <= 0:
        raise ValueError("Meteora DAMM pool is disabled or has no active liquidity")
    for field in ("sqrt_price_before_x64", "liquidity", "fee_version",
                  "pool_status", "collect_fee_mode", "fee_rate_numerator",
                  "fee_cap_numerator"):
        if quote.get(field) != fresh.get(field):
            raise ValueError("Meteora DAMM state or fee schedule changed since the supplied quote")
    if (first["sqrt_price"] != fresh.get("sqrt_price_before_x64")
            or first["liquidity"] != fresh.get("liquidity")
            or first["fee_version"] != fresh.get("fee_version")
            or first["pool_status"] != fresh.get("pool_status")
            or first["collect_fee_mode"] != fresh.get("collect_fee_mode")
            or first["fee_numerator"] != fresh.get("fee_rate_numerator")
            or first["fee_cap"] != fresh.get("fee_cap_numerator")):
        raise ValueError("Meteora DAMM pool state changed after the fresh quote")

    pool_authority = POOL_AUTHORITY
    event_authority, _ = chain._find_program_address(
        [EVENT_AUTHORITY_SEED], PROGRAM_ID
    )
    keys = [
        pool, first["vault_a"], first["vault_b"],
        first["mint_a"], first["mint_b"],
        user_input_token_account, user_output_token_account,
    ]
    accounts, slot = chain._multiple_accounts_with_context(keys)
    if len(accounts) != len(keys) or any(item is None for item in accounts):
        raise ValueError("Meteora DAMM transaction snapshot is missing a pool, vault, mint or user account")
    pool_acc, vault_a, vault_b, mint_a, mint_b, user_input, user_output = accounts
    if pool_acc.get("owner") != PROGRAM_ID:
        raise ValueError("Meteora DAMM pool owner changed during transaction snapshot")
    current = damm_quote._decode_pool(pool_acc.get("data"), chain)
    for field in (
        "mint_a", "mint_b", "vault_a", "vault_b", "liquidity",
        "protocol_fee_a", "protocol_fee_b", "sqrt_min_price",
        "sqrt_max_price", "sqrt_price", "activation_point", "activation_type",
        "pool_status", "collect_fee_mode", "fee_version", "fee_numerator",
    ):
        if current[field] != first[field]:
            raise ValueError("Meteora DAMM pool state changed during transaction preparation")
    if slot < fresh_slot or slot - fresh_slot > max_quote_slot_lag:
        raise ValueError("Fresh Meteora DAMM quote is stale relative to transaction snapshot")
    token_program_a = mint_a.get("owner")
    token_program_b = mint_b.get("owner")
    supported_programs = (TOKEN_PROGRAM_ID, chain.TOKEN_2022_PROGRAM)
    if token_program_a not in supported_programs or token_program_b not in supported_programs:
        raise ValueError("Meteora DAMM mint uses an unsupported token program")
    if vault_a.get("owner") != token_program_a or vault_b.get("owner") != token_program_b:
        raise ValueError("Meteora DAMM vault token program does not match its mint")
    for mint_account, token_program in ((mint_a, token_program_a), (mint_b, token_program_b)):
        mint_data = mint_account.get("data", b"")
        if token_program == chain.TOKEN_2022_PROGRAM:
            token2022.validate_swap_compatible_mint_data(mint_data)
        elif not isinstance(mint_data, (bytes, bytearray)) or len(mint_data) != 82 or mint_data[45] != 1:
            raise ValueError("Meteora DAMM mint is not an initialized legacy SPL Token mint")
    _vault(vault_a, token_program_a, current["mint_a"], pool_authority, "Meteora DAMM vault A")
    _vault(vault_b, token_program_b, current["mint_b"], pool_authority, "Meteora DAMM vault B")
    if chain._mint_decimals(mint_a) is None or chain._mint_decimals(mint_b) is None:
        raise ValueError("Meteora DAMM mint decimals are invalid")
    token_program_input = token_program_a if input_mint == current["mint_a"] else token_program_b
    token_program_output = token_program_b if input_mint == current["mint_a"] else token_program_a
    if (fresh.get("token_program_input") != token_program_input
            or fresh.get("token_program_output") != token_program_output):
        raise ValueError("Meteora DAMM token program state changed during transaction preparation")

    input_balance = token2022.validate_token_account_for_program(
        user_input, token_program_input, input_mint, wallet,
        "Meteora DAMM input token account", amount,
    )
    token2022.validate_token_account_for_program(
        user_output, token_program_output, output_mint, wallet,
        "Meteora DAMM output token account",
    )
    if input_balance < amount:
        raise ValueError("Meteora DAMM wallet input token account has insufficient balance")
    for account, mint, token_program in (
        (user_input_token_account, input_mint, token_program_input),
        (user_output_token_account, output_mint, token_program_output),
    ):
        if account != _ata(wallet, token_program, mint):
            raise ValueError("Meteora DAMM wallet token account is not the canonical associated token account")

    instruction_data = SWAP_DISCRIMINATOR + struct.pack("<QQ", amount, minimum_out)
    metas = [
        {"pubkey": pool_authority, "is_signer": False, "is_writable": False},
        {"pubkey": pool, "is_signer": False, "is_writable": True},
        {"pubkey": user_input_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": user_output_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": current["vault_a"], "is_signer": False, "is_writable": True},
        {"pubkey": current["vault_b"], "is_signer": False, "is_writable": True},
        {"pubkey": current["mint_a"], "is_signer": False, "is_writable": False},
        {"pubkey": current["mint_b"], "is_signer": False, "is_writable": False},
        {"pubkey": wallet, "is_signer": True, "is_writable": False},
        {"pubkey": token_program_a, "is_signer": False, "is_writable": False},
        {"pubkey": token_program_b, "is_signer": False, "is_writable": False},
        {"pubkey": PROGRAM_ID, "is_signer": False, "is_writable": False},
        {"pubkey": event_authority, "is_signer": False, "is_writable": False},
        {"pubkey": PROGRAM_ID, "is_signer": False, "is_writable": False},
    ]
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
        "provider": "meteora_damm_v2_native_builder",
        "protocol": "meteora_damm_v2",
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
        "instruction": "swap",
        "instruction_data_base64": base64.b64encode(instruction_data).decode("ascii"),
        "instruction_accounts": [meta["pubkey"] for meta in metas],
        "account_keys": account_keys,
        "required_signers": [wallet],
        "transaction": base64.b64encode(transaction).decode("ascii"),
        "transaction_version": "legacy",
        "compute_unit_limit": 300_000,
        "priority_fee_micro_lamports": _priority_fee_micro_lamports(),
        "last_valid_block_height": last_valid,
        "compute_units": COMPUTE_UNITS,
        "signature_status": "UNSIGNED_EXTERNAL_WALLET_REQUIRED",
        "execution": "LOCKED",
        "signing": "DISABLED_ON_SERVER",
        "broadcast": "DISABLED",
        "live_trading_ready": False,
    }


def simulate_meteora_damm_v2_transaction(transaction_base64):
    """Simulate an unsigned DAMM v2 transaction; never sign or broadcast."""
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
    if not isinstance(result, dict) or not isinstance(result.get("value"), dict):
        raise ValueError("Solana RPC returned a malformed simulation result")
    value = result["value"]
    if "err" not in value:
        raise ValueError("Solana RPC simulation response is missing the error field")
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
