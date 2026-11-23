"""Unsigned Orca Whirlpool Swap / SwapV2 exact-input transaction builder.

Supports static-fee pools with legacy SPL Token or the audited Token-2022
transfer-fee extension subset. The builder re-quotes from confirmed on-chain
state, validates all supplied accounts, and simulates without signing or
broadcasting. Adaptive-fee pools and transfer-hook mints remain blocked.
"""
import base64
import struct
import time

import solana_onchain as chain
from raydium_cpmm_builder import _compile_legacy_message, _priority_fee_micro_lamports
from orca_whirlpool_quote import (
    _decode_whirlpool, _tick_array_starts, _decode_fixed_tick_array,
    _derive_address, quote_orca_whirlpool_pool, TICK_ARRAY_SIZE,
)
import clmm_quote_math as mathx
import token2022_inspector as token2022

ORCA_PROGRAM_ID = chain.PROGRAMS["orca_whirlpool"]
SWAP_DISCRIMINATOR = bytes([248, 198, 158, 145, 225, 117, 135, 200])
SWAP_V2_DISCRIMINATOR = bytes([43, 4, 237, 11, 26, 201, 30, 98])
MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
TOKEN_2022_PROGRAM_ID = chain.TOKEN_2022_PROGRAM
MIN_SQRT_PRICE = 4295048016
MAX_SQRT_PRICE = 79226673515401279992447579055
MAX_QUOTE_SLOT_LAG = 32


def _pubkey(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(label + " is required")
    try:
        raw = chain._b58decode(value)
    except (ValueError, IndexError):
        raise ValueError(label + " must be a valid Solana public key")
    if len(raw) != 32:
        raise ValueError(label + " must be a valid Solana public key")
    return raw


def _shortvec(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("shortvec length must be non-negative")
    out = bytearray()
    while True:
        item = value & 0x7f
        value >>= 7
        if value:
            item |= 0x80
        out.append(item)
        if not value:
            return bytes(out)


def _compile_message(wallet, blockhash, metas, data):
    """Use the shared compiler with compute-unit limit and priority fee."""
    return _compile_legacy_message(
        wallet, blockhash, ORCA_PROGRAM_ID, metas, data
    )


def _decode_user_token(account, token_program, wallet, mint, label, minimum=0):
    return token2022.validate_token_account_for_program(
        account, token_program, mint, wallet, label, minimum
    )


def build_orca_whirlpool_swap(quote, wallet, user_input_token_account,
                              user_output_token_account,
                              max_quote_slot_lag=MAX_QUOTE_SLOT_LAG):
    if not isinstance(quote, dict) or quote.get("protocol") != "orca_whirlpool":
        raise ValueError("A validated Orca Whirlpool quote is required")
    if quote.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_TICK_SNAPSHOT":
        raise ValueError("Quote is not a confirmed Orca Whirlpool quote")
    pool = quote.get("pool_id")
    input_mint, output_mint = quote.get("input_mint"), quote.get("output_mint")
    amount = quote.get("input_amount")
    quote_slot = quote.get("slot")
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= 2**64 - 1:
        raise ValueError("Quote input amount must be a positive u64")
    if isinstance(quote_slot, bool) or not isinstance(quote_slot, int) or quote_slot <= 0:
        raise ValueError("Quote must include a positive confirmed slot")
    if isinstance(max_quote_slot_lag, bool) or not isinstance(max_quote_slot_lag, int) or not 0 <= max_quote_slot_lag <= 150:
        raise ValueError("Maximum quote slot lag is invalid")
    for value, label in ((pool, "pool"), (wallet, "wallet"), (input_mint, "input mint"),
                         (output_mint, "output mint"),
                         (user_input_token_account, "input token account"),
                         (user_output_token_account, "output token account")):
        _pubkey(value, label)
    if input_mint == output_mint or user_input_token_account == user_output_token_account:
        raise ValueError("Swap mints and user token accounts must be distinct")

    slippage = quote.get("slippage_bps", 100)
    fresh = quote_orca_whirlpool_pool(pool, input_mint, amount, slippage, output_mint)
    if (fresh.get("pool_id") != pool or fresh.get("input_mint") != input_mint
            or fresh.get("output_mint") != output_mint or fresh.get("input_amount") != amount
            or fresh.get("slot", 0) < quote_slot):
        raise ValueError("Fresh Orca quote does not validate the requested pool, pair and amount")
    for field in ("token_program_input", "token_program_output"):
        if quote.get(field) != fresh.get(field):
            raise ValueError("Orca token program state changed since the supplied quote")
    minimum_out = fresh.get("minimum_out_amount")
    if isinstance(minimum_out, bool) or not isinstance(minimum_out, int) or not 0 < minimum_out <= 2**64 - 1:
        raise ValueError("Fresh Orca quote minimum output is invalid")

    discovered = chain._account(pool)
    if discovered.get("owner") != ORCA_PROGRAM_ID:
        raise ValueError("Pool is not owned by the Orca Whirlpool program")
    first = _decode_whirlpool(discovered.get("data"), chain)
    if input_mint == first["mint_a"] and output_mint == first["mint_b"]:
        a_to_b = True
    elif input_mint == first["mint_b"] and output_mint == first["mint_a"]:
        a_to_b = False
    else:
        raise ValueError("Quote mint pair does not match the Whirlpool")
    starts = _tick_array_starts(first["tick_current"], first["tick_spacing"], a_to_b)
    if len(starts) != 3:
        raise ValueError("Orca swap requires exactly three supported tick-array PDAs")
    pool_bytes = _pubkey(pool, "pool")
    tick_addresses = [
        _derive_address([b"tick_array", pool_bytes, str(start).encode("ascii")], chain)
        for start in starts
    ]
    oracle = _derive_address([b"oracle", pool_bytes], chain)

    user_a = user_input_token_account if a_to_b else user_output_token_account
    user_b = user_output_token_account if a_to_b else user_input_token_account
    addresses = [pool, first["vault_a"], first["vault_b"], first["mint_a"], first["mint_b"],
                 user_a, user_b] + tick_addresses + [oracle]
    accounts, slot = chain._multiple_accounts_with_context(addresses)
    if len(accounts) != len(addresses) or any(a is None for a in accounts[:7]):
        raise ValueError("Orca transaction snapshot is missing a required pool, vault, mint or user account")
    (pool_acc, vault_a, vault_b, mint_a, mint_b, user_a_acc, user_b_acc,
     tick0, tick1, tick2, oracle_acc) = accounts
    if pool_acc.get("owner") != ORCA_PROGRAM_ID:
        raise ValueError("Whirlpool owner changed during transaction snapshot")
    state = _decode_whirlpool(pool_acc.get("data"), chain)
    for field in ("tick_spacing", "fee_tier_index", "fee_rate", "protocol_fee_rate",
                  "liquidity", "sqrt_price_x64", "tick_current", "mint_a", "mint_b",
                  "vault_a", "vault_b"):
        if state[field] != first[field]:
            raise ValueError("Whirlpool state changed during transaction snapshot")
    if slot < fresh.get("slot", 0) or slot - fresh.get("slot", 0) > max_quote_slot_lag:
        raise ValueError("Fresh Orca quote is stale relative to transaction snapshot")
    token_program_a = mint_a.get("owner")
    token_program_b = mint_b.get("owner")
    supported_programs = (chain.TOKEN_PROGRAM, TOKEN_2022_PROGRAM_ID)
    if token_program_a not in supported_programs or token_program_b not in supported_programs:
        raise ValueError("Orca mint uses an unsupported token program")
    if vault_a.get("owner") != token_program_a or vault_b.get("owner") != token_program_b:
        raise ValueError("Orca vault token program does not match its mint")
    for mint_account, token_program in ((mint_a, token_program_a), (mint_b, token_program_b)):
        mint_raw = mint_account.get("data", b"")
        if token_program == TOKEN_2022_PROGRAM_ID:
            token2022.validate_swap_compatible_mint_data(mint_raw)
        elif not isinstance(mint_raw, (bytes, bytearray)) or len(mint_raw) != 82 or mint_raw[45] != 1:
            raise ValueError("Orca mint is not an initialized legacy SPL Token mint")
    token2022.validate_token_account_for_program(
        vault_a, token_program_a, state["mint_a"], pool, "Orca vault A", 1
    )
    token2022.validate_token_account_for_program(
        vault_b, token_program_b, state["mint_b"], pool, "Orca vault B", 1
    )
    token_program_input = token_program_a if a_to_b else token_program_b
    token_program_output = token_program_b if a_to_b else token_program_a
    if (fresh.get("token_program_input") != token_program_input
            or fresh.get("token_program_output") != token_program_output):
        raise ValueError("Orca token program state changed during transaction preparation")
    input_balance = _decode_user_token(
        user_input_token_account, token_program_input, wallet, input_mint,
        "Input token account", amount,
    )
    _decode_user_token(user_a_acc, token_program_a, wallet, state["mint_a"], "Token A account")
    _decode_user_token(user_b_acc, token_program_b, wallet, state["mint_b"], "Token B account")
    if input_balance < amount:
        raise ValueError("Wallet input token account has insufficient balance")

    for address, start, account in zip(tick_addresses, starts, (tick0, tick1, tick2)):
        if account is None:
            continue
        _decode_fixed_tick_array(account, pool, start, state["tick_spacing"], chain)
    if oracle_acc is not None:
        if oracle_acc.get("owner") != ORCA_PROGRAM_ID:
            raise ValueError("Orca oracle account owner is invalid")
        raw_oracle = oracle_acc.get("data", b"")
        if len(raw_oracle) < 58 or int.from_bytes(raw_oracle[54:58], "little") != 0:
            raise ValueError("Orca adaptive-fee oracle layout or state is unsupported")

    # Orca SwapV2 is required whenever either mint uses Token-2022.
    uses_token_2022 = TOKEN_2022_PROGRAM_ID in (token_program_a, token_program_b)
    sqrt_limit = MIN_SQRT_PRICE + 1 if a_to_b else MAX_SQRT_PRICE - 1
    discriminator = SWAP_V2_DISCRIMINATOR if uses_token_2022 else SWAP_DISCRIMINATOR
    data = (discriminator + struct.pack("<QQ", amount, minimum_out)
            + sqrt_limit.to_bytes(16, "little") + bytes([1, int(a_to_b)]))
    if uses_token_2022:
        # Option<RemainingAccountsInfo> = None; transfer hooks are refused.
        data += bytes([0])
        metas = [
            {"pubkey": token_program_a, "is_signer": False, "is_writable": False},
            {"pubkey": token_program_b, "is_signer": False, "is_writable": False},
            {"pubkey": MEMO_PROGRAM_ID, "is_signer": False, "is_writable": False},
            {"pubkey": wallet, "is_signer": True, "is_writable": False},
            {"pubkey": pool, "is_signer": False, "is_writable": True},
            {"pubkey": state["mint_a"], "is_signer": False, "is_writable": False},
            {"pubkey": state["mint_b"], "is_signer": False, "is_writable": False},
            {"pubkey": user_a, "is_signer": False, "is_writable": True},
            {"pubkey": state["vault_a"], "is_signer": False, "is_writable": True},
            {"pubkey": user_b, "is_signer": False, "is_writable": True},
            {"pubkey": state["vault_b"], "is_signer": False, "is_writable": True},
        ]
    else:
        metas = [
            {"pubkey": chain.TOKEN_PROGRAM, "is_signer": False, "is_writable": False},
            {"pubkey": wallet, "is_signer": True, "is_writable": False},
            {"pubkey": pool, "is_signer": False, "is_writable": True},
            {"pubkey": user_a, "is_signer": False, "is_writable": True},
            {"pubkey": state["vault_a"], "is_signer": False, "is_writable": True},
            {"pubkey": user_b, "is_signer": False, "is_writable": True},
            {"pubkey": state["vault_b"], "is_signer": False, "is_writable": True},
        ]
    metas.extend({"pubkey": address, "is_signer": False, "is_writable": True}
                 for address in tick_addresses)
    metas.append({"pubkey": oracle, "is_signer": False, "is_writable": False})
    latest = chain._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    value = (latest or {}).get("value") or {}
    blockhash, last_valid = value.get("blockhash"), value.get("lastValidBlockHeight")
    _pubkey(blockhash, "latest blockhash")
    if isinstance(last_valid, bool) or not isinstance(last_valid, int) or last_valid <= 0:
        raise ValueError("RPC latest blockhash response is incomplete")
    transaction, account_keys = _compile_message(wallet, blockhash, metas, data)
    return {
        "provider": "orca_whirlpool_native_builder", "protocol": "orca_whirlpool",
        "pool_id": pool, "slot": slot, "quote_slot": quote_slot,
        "fresh_quote_slot": fresh["slot"], "quote_slot_lag": slot - fresh["slot"],
        "input_mint": input_mint, "output_mint": output_mint,
        "input_amount": amount, "minimum_output_amount": minimum_out,
        "token_program_input": token_program_input,
        "token_program_output": token_program_output,
        "token2022_fee_aware_supported": uses_token_2022,
        "instruction": "SwapV2" if uses_token_2022 else "Swap",
        "instruction_discriminator": list(discriminator),
        "a_to_b": a_to_b, "sqrt_price_limit": str(sqrt_limit),
        "account_keys": account_keys,
        "required_signers": [wallet],
        "transaction": base64.b64encode(transaction).decode("ascii"),
        "transaction_version": "legacy",
        "compute_unit_limit": 300_000,
        "priority_fee_micro_lamports": _priority_fee_micro_lamports(), "last_valid_block_height": last_valid,
        "signature_status": "UNSIGNED_EXTERNAL_WALLET_REQUIRED",
        "execution": "LOCKED", "signing": "DISABLED_ON_SERVER",
        "broadcast": "DISABLED", "live_trading_ready": False,
    }


def simulate_orca_whirlpool_transaction(transaction_base64):
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
        {"encoding": "base64", "commitment": "confirmed",
         "sigVerify": False, "replaceRecentBlockhash": True},
    ])
    value = (result or {}).get("value")
    if not isinstance(value, dict):
        raise ValueError("Orca transaction simulation response is malformed")
    if value.get("err") is not None:
        raise ValueError("Orca transaction simulation failed: " + str(value["err"])[:200])
    return {"status": "PASS", "success": True, "err": None,
            "signature_verification": "DISABLED_UNSIGNED_SIMULATION",
            "logs": value.get("logs", [])[-20:],
            "units_consumed": value.get("unitsConsumed"),
            "execution": "LOCKED", "signing": "NOT_PERFORMED",
            "broadcast": "NOT_PERFORMED"}
