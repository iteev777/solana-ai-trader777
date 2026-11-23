"""Unsigned native Raydium AMM v4 swapBaseIn transaction builder.

Uses the 17-account live AMM v4 instruction shape documented by ScreenerBot.
Only legacy SPL Token pools are accepted. The builder re-quotes and re-reads
pool/vault/mint/user accounts at a confirmed slot, then returns an unsigned
transaction for an external wallet. It never signs or broadcasts.
"""
import base64
import struct
import time

import solana_onchain as chain
from raydium_cpmm_builder import _compile_legacy_message, _priority_fee_micro_lamports, _decode_user_token_account

PROGRAM_ID = chain.PROGRAMS["raydium_amm_v4"]
TOKEN_PROGRAM_ID = chain.TOKEN_PROGRAM
AUTHORITY_SEED = b"amm authority"
SWAP_BASE_IN_TAG = 9
MAX_QUOTE_SLOT_LAG = 32
COMPUTE_UNITS = 100_000


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


def build_raydium_amm_v4_swap(quote, wallet, user_input_token_account,
                              user_output_token_account,
                              max_quote_slot_lag=MAX_QUOTE_SLOT_LAG):
    """Build an unsigned AMM v4 swapBaseIn transaction from a confirmed quote."""
    if not isinstance(quote, dict) or quote.get("protocol") != "raydium_amm_v4":
        raise ValueError("A validated Raydium AMM v4 quote is required")
    if quote.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT":
        raise ValueError("Quote is not a confirmed Raydium AMM v4 quote")

    pool = quote.get("pool_id")
    input_mint = quote.get("input_mint")
    output_mint = quote.get("output_mint")
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
        raise ValueError("Swap mints and user token accounts must be distinct")

    slippage = quote.get("slippage_bps", 100)
    if isinstance(slippage, bool) or not isinstance(slippage, int) or not 0 <= slippage <= 300:
        raise ValueError("Quote slippage must be an integer between 0 and 300 bps")
    fresh = chain.quote_raydium_amm_v4_pool(pool, input_mint, amount, slippage)
    if (
        not isinstance(fresh, dict)
        or fresh.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT"
        or fresh.get("pool_id") != pool
        or fresh.get("input_mint") != input_mint
        or fresh.get("output_mint") != output_mint
        or fresh.get("input_amount") != amount
    ):
        raise ValueError("Fresh Raydium AMM v4 quote does not validate pool, pair and amount")
    minimum_out = _u64(fresh.get("minimum_out_amount"), "fresh minimum output")
    fresh_slot = fresh.get("slot")
    if isinstance(fresh_slot, bool) or not isinstance(fresh_slot, int) or fresh_slot < quote_slot:
        raise ValueError("Fresh Raydium AMM v4 quote is older than the supplied quote")

    discovered = chain._account(pool)
    if discovered.get("owner") != PROGRAM_ID:
        raise ValueError("Pool account is not owned by the Raydium AMM v4 program")
    first = chain._decode_pool("raydium_amm_v4", discovered.get("data", b""))
    if input_mint not in (first["base_mint"], first["quote_mint"]):
        raise ValueError("Input mint does not belong to this Raydium AMM v4 pool")
    expected_output = first["quote_mint"] if input_mint == first["base_mint"] else first["base_mint"]
    if output_mint != expected_output:
        raise ValueError("Output mint does not match the opposite pool mint")
    if first["status"] != "active" or first["pool_open_time"] > int(time.time()):
        raise ValueError("Raydium AMM v4 pool is paused or not open")

    authority, bump = chain._find_program_address([AUTHORITY_SEED], PROGRAM_ID)
    keys = [pool, first["vault_base"], first["vault_quote"],
            first["base_mint"], first["quote_mint"],
            user_input_token_account, user_output_token_account]
    accounts, slot = chain._multiple_accounts_with_context(keys)
    if len(accounts) != len(keys) or any(item is None for item in accounts):
        raise ValueError("AMM v4 transaction snapshot is missing a pool, vault, mint or user account")
    pool_acc, vault_base, vault_quote, mint_base, mint_quote, user_input, user_output = accounts
    if pool_acc.get("owner") != PROGRAM_ID:
        raise ValueError("AMM v4 pool owner changed during transaction snapshot")
    current = chain._decode_pool("raydium_amm_v4", pool_acc.get("data", b""))
    for field in ("vault_base", "vault_quote", "base_mint", "quote_mint",
                  "swap_fee_numerator", "swap_fee_denominator",
                  "need_take_pnl_base", "need_take_pnl_quote", "pool_open_time", "status"):
        if current.get(field) != first.get(field):
            raise ValueError("AMM v4 pool state changed during transaction preparation")
    if slot < fresh_slot or slot - fresh_slot > max_quote_slot_lag:
        raise ValueError("Fresh AMM v4 quote is stale relative to transaction snapshot")
    if current["status"] != "active" or current["pool_open_time"] > int(time.time()):
        raise ValueError("Raydium AMM v4 pool is paused or not yet open")

    if any(mint.get("owner") != TOKEN_PROGRAM_ID for mint in (mint_base, mint_quote)):
        raise ValueError("Raydium AMM v4 builder supports legacy SPL Token mints only")
    if any(vault.get("owner") != TOKEN_PROGRAM_ID for vault in (vault_base, vault_quote)):
        raise ValueError("Raydium AMM v4 vaults must use legacy SPL Token")
    if chain._key(vault_base.get("data", b""), 32) != authority or chain._key(vault_quote.get("data", b""), 32) != authority:
        raise ValueError("AMM v4 vault authority does not match the canonical Raydium authority PDA")
    if vault_base["data"][108] != 1 or vault_quote["data"][108] != 1:
        raise ValueError("AMM v4 vault is not initialized or is frozen")
    chain._token_amount(vault_base, current["base_mint"])
    chain._token_amount(vault_quote, current["quote_mint"])
    if (chain._mint_decimals(mint_base) != fresh.get("base_decimals")
            or chain._mint_decimals(mint_quote) != fresh.get("quote_decimals")):
        raise ValueError("AMM v4 mint decimals changed since the fresh quote")

    input_balance = _decode_user_token_account(user_input, wallet, input_mint, "Input token account")
    _decode_user_token_account(user_output, wallet, output_mint, "Output token account")
    if input_balance < amount:
        raise ValueError("Wallet input token account has insufficient balance")

    source = user_input_token_account
    destination = user_output_token_account
    metas = [
        {"pubkey": TOKEN_PROGRAM_ID, "is_signer": False, "is_writable": False},
        {"pubkey": pool, "is_signer": False, "is_writable": True},
        {"pubkey": authority, "is_signer": False, "is_writable": False},
        {"pubkey": pool, "is_signer": False, "is_writable": True},
        {"pubkey": current["vault_base"], "is_signer": False, "is_writable": True},
        {"pubkey": current["vault_quote"], "is_signer": False, "is_writable": True},
    ]
    # Modern AMM v4 routing uses the 17-account form: OpenBook slots are
    # populated with the pool id, as verified against ScreenerBot's live shape.
    metas.extend(
        {"pubkey": pool, "is_signer": False, "is_writable": True}
        for _ in range(8)
    )
    metas.extend([
        {"pubkey": source, "is_signer": False, "is_writable": True},
        {"pubkey": destination, "is_signer": False, "is_writable": True},
        {"pubkey": wallet, "is_signer": True, "is_writable": False},
    ])
    instruction_data = bytes([SWAP_BASE_IN_TAG]) + struct.pack("<QQ", amount, minimum_out)
    latest = chain._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    value = (latest or {}).get("value") or {}
    blockhash = value.get("blockhash")
    last_valid = value.get("lastValidBlockHeight")
    _pubkey(blockhash, "latest blockhash")
    if isinstance(last_valid, bool) or not isinstance(last_valid, int) or last_valid <= 0:
        raise ValueError("RPC latest blockhash response is incomplete")
    transaction, account_keys = _compile_legacy_message(
        wallet, blockhash, PROGRAM_ID, metas, instruction_data
    )
    return {
        "provider": "raydium_amm_v4_native_builder",
        "protocol": "raydium_amm_v4",
        "pool_id": pool,
        "slot": slot,
        "quote_slot": quote_slot,
        "fresh_quote_slot": fresh_slot,
        "quote_slot_lag": slot - fresh_slot,
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount,
        "minimum_output_amount": minimum_out,
        "instruction": "swapBaseIn",
        "instruction_data_base64": base64.b64encode(instruction_data).decode("ascii"),
        "account_keys": account_keys,
        "instruction_accounts": [meta["pubkey"] for meta in metas],
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


def simulate_raydium_amm_v4_transaction(transaction_base64):
    """Simulate an unsigned AMM v4 transaction without signing or broadcasting."""
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
