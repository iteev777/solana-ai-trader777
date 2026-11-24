"""Unsigned native Raydium CLMM swap builder.

Builds the SWAP_V2 exact-input instruction from a fresh confirmed tick quote.
Supports legacy SPL Token and the audited Token-2022 transfer-fee extension
subset, with mint-specific token-program accounts and extension-aware token
account validation. Unsupported extensions and unmodelled fee modes remain
fail-closed. Bitmap-extension tick arrays use the quote adapter's pool model.
No signing or broadcasting is performed.
"""
import base64
import struct

import solana_onchain as chain
import raydium_clmm_quote as clmm
import token2022_inspector as token2022
from raydium_cpmm_builder import _compile_legacy_message, _priority_fee_micro_lamports

PROGRAM_ID = chain.PROGRAMS["raydium_clmm"]
TOKEN_PROGRAM_ID = chain.TOKEN_PROGRAM
TOKEN_2022_PROGRAM_ID = chain.TOKEN_2022_PROGRAM
MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
SWAP_V2 = bytes([43, 4, 237, 11, 26, 201, 30, 98])
MAX_QUOTE_SLOT_LAG = 24
MAX_SLIPPAGE_BPS = 300


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


def build_raydium_clmm_swap(quote, wallet, user_input_token_account,
                            user_output_token_account,
                            max_quote_slot_lag=MAX_QUOTE_SLOT_LAG):
    if not isinstance(quote, dict) or quote.get("protocol") != "raydium_clmm":
        raise ValueError("A Raydium CLMM quote is required")
    if quote.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_TICK_SNAPSHOT":
        raise ValueError("Quote is not a confirmed Raydium CLMM tick snapshot")
    pool = quote.get("pool_id")
    input_mint, output_mint = quote.get("input_mint"), quote.get("output_mint")
    amount = quote.get("input_amount")
    quote_slot = quote.get("slot")
    slippage = quote.get("slippage_bps", 100)
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= 2**64 - 1:
        raise ValueError("Input amount must be a positive u64")
    if isinstance(quote_slot, bool) or not isinstance(quote_slot, int) or quote_slot <= 0:
        raise ValueError("Quote must contain a confirmed slot")
    if isinstance(slippage, bool) or not isinstance(slippage, int) or not 0 <= slippage <= MAX_SLIPPAGE_BPS:
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

    fresh = clmm.quote_raydium_clmm_pool(pool, input_mint, amount, slippage, output_mint)
    if (not isinstance(fresh, dict)
            or fresh.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_TICK_SNAPSHOT"
            or fresh.get("pool_id") != pool
            or fresh.get("input_mint") != input_mint
            or fresh.get("output_mint") != output_mint
            or fresh.get("input_amount") != amount):
        raise ValueError("Fresh Raydium CLMM quote did not validate pool, pair and amount")
    minimum_out = fresh.get("minimum_out_amount")
    if isinstance(minimum_out, bool) or not isinstance(minimum_out, int) or not 0 < minimum_out <= 2**64 - 1:
        raise ValueError("Fresh CLMM minimum output must be a positive u64")
    if minimum_out > fresh.get("out_amount", 0):
        raise ValueError("Fresh CLMM minimum output exceeds quoted output")
    for field in ("token_program_input", "token_program_output"):
        if quote.get(field) != fresh.get(field):
            raise ValueError("CLMM token program state changed since the supplied quote")
    fresh_slot = fresh.get("slot")
    if isinstance(fresh_slot, bool) or not isinstance(fresh_slot, int) or fresh_slot < quote_slot:
        raise ValueError("Fresh CLMM quote is older than the supplied quote")

    discovered_acc = chain._account(pool)
    if discovered_acc.get("owner") != PROGRAM_ID:
        raise ValueError("Pool account is not owned by Raydium CLMM")
    first = clmm._decode_state(discovered_acc.get("data"), chain)
    if input_mint == first["mint_0"]:
        zero_for_one = True
        input_vault, output_vault = first["vault_0"], first["vault_1"]
    elif input_mint == first["mint_1"]:
        zero_for_one = False
        input_vault, output_vault = first["vault_1"], first["vault_0"]
    else:
        raise ValueError("Input mint does not belong to this CLMM pool")
    if (output_mint != (first["mint_1"] if zero_for_one else first["mint_0"])):
        raise ValueError("Output mint does not match the opposite CLMM pool side")

    bitmap_extension, _ = chain._find_program_address(
        [clmm.BITMAP_EXTENSION_SEED, _key(pool, "pool")], PROGRAM_ID
    )
    bitmap_discovery = chain._account(bitmap_extension)
    initial_extension = clmm._decode_bitmap_extension(bitmap_discovery, pool, chain)
    arrays = clmm._array_starts(first, pool, zero_for_one, chain, initial_extension)
    observation, _ = chain._find_program_address(
        [b"observation", _key(pool, "pool")], PROGRAM_ID
    )
    keys = [pool, first["amm_config"], first["vault_0"], first["vault_1"],
            first["mint_0"], first["mint_1"], bitmap_extension, observation,
            user_input_token_account, user_output_token_account]
    keys.extend(address for _, address in arrays)
    accounts, slot = chain._multiple_accounts_with_context(keys)
    if len(accounts) != len(keys) or any(item is None for item in accounts[:6]):
        raise ValueError("CLMM transaction snapshot is missing pool, config, vault or mint accounts")
    (pool_acc, config_acc, vault0_acc, vault1_acc, mint0_acc, mint1_acc,
     bitmap_acc, observation_acc, user_in_acc, user_out_acc) = accounts[:10]
    snapshot_extension = clmm._decode_bitmap_extension(bitmap_acc, pool, chain)
    if snapshot_extension != initial_extension:
        raise ValueError("CLMM bitmap extension changed during transaction snapshot")
    if observation_acc is None:
        raise ValueError("CLMM observation account is missing from transaction snapshot")
    if pool_acc.get("owner") != PROGRAM_ID:
        raise ValueError("CLMM pool owner changed during transaction snapshot")
    current = clmm._decode_state(pool_acc.get("data"), chain)
    for field in ("amm_config", "mint_0", "mint_1", "vault_0", "vault_1",
                  "tick_spacing", "liquidity", "sqrt_price_x64", "tick_current",
                  "status", "fee_on", "dynamic_fee", "bitmap"):
        if current[field] != first[field]:
            raise ValueError("CLMM pool state changed during transaction preparation")
    if slot < fresh_slot or slot - fresh_slot > max_quote_slot_lag:
        raise ValueError("Fresh CLMM quote is stale relative to transaction snapshot")
    if config_acc.get("owner") != PROGRAM_ID or len(config_acc.get("data", b"")) < 57:
        raise ValueError("CLMM AmmConfig owner or layout is invalid")
    fee_rate = int.from_bytes(config_acc["data"][47:51], "little")
    config_spacing = int.from_bytes(config_acc["data"][51:53], "little")
    if fee_rate != fresh.get("fee_rate"):
        raise ValueError("CLMM fee rate changed since fresh quote")
    if config_spacing != current["tick_spacing"]:
        raise ValueError("CLMM AmmConfig tick spacing changed during transaction preparation")
    token_program_0 = mint0_acc.get("owner")
    token_program_1 = mint1_acc.get("owner")
    supported_programs = (TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID)
    if token_program_0 not in supported_programs or token_program_1 not in supported_programs:
        raise ValueError("CLMM mint uses an unsupported token program")
    if vault0_acc.get("owner") != token_program_0 or vault1_acc.get("owner") != token_program_1:
        raise ValueError("CLMM vault token program does not match its mint")
    for mint_account, token_program in ((mint0_acc, token_program_0), (mint1_acc, token_program_1)):
        mint_raw = mint_account.get("data", b"")
        if token_program == TOKEN_2022_PROGRAM_ID:
            token2022.validate_swap_compatible_mint_data(mint_raw)
        elif not isinstance(mint_raw, (bytes, bytearray)) or len(mint_raw) != 82 or mint_raw[45] != 1:
            raise ValueError("CLMM mint is not an initialized legacy SPL Token mint")
    if (chain._mint_decimals(mint0_acc) != current["decimals_0"]
            or chain._mint_decimals(mint1_acc) != current["decimals_1"]):
        raise ValueError("CLMM pool decimals do not match the confirmed mint account layouts")
    token_program_input = token_program_0 if input_mint == current["mint_0"] else token_program_1
    token_program_output = token_program_1 if input_mint == current["mint_0"] else token_program_0
    if (fresh.get("token_program_input") != token_program_input
            or fresh.get("token_program_output") != token_program_output):
        raise ValueError("CLMM token program state changed during transaction preparation: quote=" + str((fresh.get("token_program_input"), fresh.get("token_program_output"))) + " snapshot=" + str((token_program_input, token_program_output)))
    # Raydium CLMM vault authority is the pool address itself.
    authority = pool
    _token_account(vault0_acc, token_program_0, authority, current["mint_0"], "CLMM vault 0", 1)
    _token_account(vault1_acc, token_program_1, authority, current["mint_1"], "CLMM vault 1", 1)
    _token_account(user_in_acc, token_program_input, wallet, input_mint,
                   "User input token account", amount)
    _token_account(user_out_acc, token_program_output, wallet, output_mint,
                   "User output token account")
    if observation_acc.get("owner") != PROGRAM_ID:
        raise ValueError("CLMM observation account owner is invalid")
    if len(observation_acc.get("data", b"")) < 40:
        raise ValueError("CLMM observation account is truncated")
    if chain._key(observation_acc["data"], 8) != pool:
        raise ValueError("CLMM observation account belongs to another pool")

    tick_array_accounts = accounts[10:]
    if len(tick_array_accounts) != len(arrays) or any(item is None for item in tick_array_accounts):
        raise ValueError("CLMM tick array snapshot is incomplete")
    for account, (start, address) in zip(tick_array_accounts, arrays):
        if account.get("owner") != PROGRAM_ID:
            raise ValueError("CLMM tick array owner is invalid")
        clmm._decode_tick_array(account, pool, start, current["tick_spacing"], chain)

    data = (SWAP_V2 + struct.pack("<QQ", amount, minimum_out)
            + bytes(16) + b"\x01")
    metas = [
        {"pubkey": wallet, "is_signer": True, "is_writable": False},
        {"pubkey": current["amm_config"], "is_signer": False, "is_writable": False},
        {"pubkey": pool, "is_signer": False, "is_writable": True},
        {"pubkey": user_input_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": user_output_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": input_vault, "is_signer": False, "is_writable": True},
        {"pubkey": output_vault, "is_signer": False, "is_writable": True},
        {"pubkey": observation, "is_signer": False, "is_writable": True},
        # swap_v2 carries both canonical token-program accounts in fixed
        # order, regardless of which program owns either mint. The mint
        # ownership checks above still bind each side to its actual program.
        {"pubkey": TOKEN_PROGRAM_ID, "is_signer": False, "is_writable": False},
        {"pubkey": TOKEN_2022_PROGRAM_ID, "is_signer": False, "is_writable": False},
        {"pubkey": MEMO_PROGRAM_ID, "is_signer": False, "is_writable": False},
        {"pubkey": input_mint, "is_signer": False, "is_writable": False},
        {"pubkey": output_mint, "is_signer": False, "is_writable": False},
        {"pubkey": bitmap_extension, "is_signer": False, "is_writable": True},
    ]
    metas.extend({"pubkey": address, "is_signer": False, "is_writable": True}
                 for _, address in arrays[:3])
    latest = chain._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    value = (latest or {}).get("value") or {}
    blockhash, last_valid = value.get("blockhash"), value.get("lastValidBlockHeight")
    _key(blockhash, "latest blockhash")
    if isinstance(last_valid, bool) or not isinstance(last_valid, int) or last_valid <= 0:
        raise ValueError("RPC latest blockhash response is incomplete")
    transaction, account_keys = _compile_legacy_message(wallet, blockhash, PROGRAM_ID, metas, data)
    return {
        "provider": "raydium_clmm_native_builder",
        "protocol": "raydium_clmm",
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
        "token2022_fee_aware_supported": TOKEN_2022_PROGRAM_ID in (token_program_input, token_program_output),
        "instruction": "swap_v2_exact_input",
        "instruction_data_base64": base64.b64encode(data).decode("ascii"),
        "instruction_accounts": [item["pubkey"] for item in metas],
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


def simulate_raydium_clmm_transaction(transaction_base64):
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
        raise ValueError("Solana RPC simulation response is missing value.err")
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
