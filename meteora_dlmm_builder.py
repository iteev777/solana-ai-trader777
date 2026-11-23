"""Unsigned Meteora DLMM Swap2 transaction builder.

The first native path is deliberately limited to the existing read-only quote
adapter's classic SPL Token / function_type=0 pool mode. It builds Swap2 with
the official account order and empty RemainingAccountsInfo, then requires an
unsigned RPC simulation. It never signs or broadcasts.
"""
import base64
import hashlib
import struct

import solana_onchain as chain
import meteora_dlmm_quote as dlmm_quote

DLMM_PROGRAM_ID = chain.PROGRAMS["meteora_dlmm"]
SWAP2_DISCRIMINATOR = bytes([65, 75, 63, 76, 235, 91, 91, 136])
MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
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
    _pubkey(wallet, "wallet")
    _pubkey(blockhash, "recent blockhash")
    merged = {}

    def add(key, signer=False, writable=False):
        _pubkey(key, "instruction account")
        flags = merged.setdefault(key, {"signer": False, "writable": False})
        flags["signer"] = flags["signer"] or bool(signer)
        flags["writable"] = flags["writable"] or bool(writable)

    add(wallet, True, False)
    for meta in metas:
        add(meta["pubkey"], meta["is_signer"], meta["is_writable"])
    add(DLMM_PROGRAM_ID)
    ordered = [wallet]
    ordered += [k for k, v in merged.items() if k != wallet and v["signer"] and v["writable"]]
    ordered += [k for k, v in merged.items() if k != wallet and v["signer"] and not v["writable"]]
    ordered += [k for k, v in merged.items() if k != wallet and not v["signer"] and v["writable"]]
    ordered += [k for k, v in merged.items() if k != wallet and not v["signer"] and not v["writable"]]
    if len(ordered) > 256:
        raise ValueError("legacy transaction account key limit exceeded")
    if sum(1 for v in merged.values() if v["signer"]) != 1:
        raise ValueError("DLMM Swap2 must require exactly one external wallet signer")
    index = {key: i for i, key in enumerate(ordered)}
    compiled_accounts = bytes(index[m["pubkey"]] for m in metas)
    instruction = (
        bytes([index[DLMM_PROGRAM_ID]]) + _shortvec(len(compiled_accounts))
        + compiled_accounts + _shortvec(len(data)) + data
    )
    readonly_unsigned = sum(
        1 for v in merged.values() if not v["signer"] and not v["writable"]
    )
    message = (
        bytes([1, 0, readonly_unsigned]) + _shortvec(len(ordered))
        + b"".join(_pubkey(key, "account key") for key in ordered)
        + _pubkey(blockhash, "recent blockhash") + _shortvec(1) + instruction
    )
    return _shortvec(1) + bytes(64) + message, ordered


def _decode_user_token(account, wallet, mint, label):
    if not isinstance(account, dict) or account.get("owner") != chain.TOKEN_PROGRAM:
        raise ValueError(label + " must use legacy SPL Token")
    raw = account.get("data", b"")
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 165:
        raise ValueError(label + " has an invalid legacy token-account layout")
    if chain._key(raw, 0) != mint:
        raise ValueError(label + " mint does not match the requested swap")
    if chain._key(raw, 32) != wallet:
        raise ValueError(label + " is not owned by the signing wallet")
    if raw[108] != 1:
        raise ValueError(label + " is not initialized or is frozen")
    return chain._u64(raw, 64)


def build_meteora_dlmm_swap(quote, wallet, user_input_token_account,
                            user_output_token_account,
                            max_quote_slot_lag=MAX_QUOTE_SLOT_LAG):
    if not isinstance(quote, dict) or quote.get("protocol") != "meteora_dlmm":
        raise ValueError("A validated Meteora DLMM quote is required")
    if quote.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_BIN_SNAPSHOT":
        raise ValueError("Quote is not a confirmed Meteora DLMM quote")

    pool = quote.get("pool_id")
    input_mint, output_mint = quote.get("input_mint"), quote.get("output_mint")
    amount, quote_slot = quote.get("input_amount"), quote.get("slot")
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= 2**64 - 1:
        raise ValueError("Quote input amount must be a positive u64")
    if isinstance(quote_slot, bool) or not isinstance(quote_slot, int) or quote_slot <= 0:
        raise ValueError("Quote must include a positive confirmed slot")
    if isinstance(max_quote_slot_lag, bool) or not isinstance(max_quote_slot_lag, int) or not 0 <= max_quote_slot_lag <= 150:
        raise ValueError("Maximum quote slot lag is invalid")
    for value, label in (
        (pool, "pool"), (wallet, "wallet"), (input_mint, "input mint"),
        (output_mint, "output mint"),
        (user_input_token_account, "input token account"),
        (user_output_token_account, "output token account"),
    ):
        _pubkey(value, label)
    if input_mint == output_mint or user_input_token_account == user_output_token_account:
        raise ValueError("Swap mints and user token accounts must be distinct")

    slippage = quote.get("slippage_bps", 100)
    fresh = dlmm_quote.quote_meteora_dlmm_pool(
        pool, input_mint, amount, slippage, output_mint
    )
    if (
        fresh.get("pool_id") != pool or fresh.get("input_mint") != input_mint
        or fresh.get("output_mint") != output_mint
        or fresh.get("input_amount") != amount
        or fresh.get("slot", 0) < quote_slot
    ):
        raise ValueError("Fresh Meteora DLMM quote does not validate pool, pair and amount")
    minimum_out = fresh.get("minimum_out_amount")
    if isinstance(minimum_out, bool) or not isinstance(minimum_out, int) or not 0 < minimum_out <= 2**64 - 1:
        raise ValueError("Fresh Meteora DLMM minimum output is invalid")

    discovered = chain._account(pool)
    if discovered.get("owner") != DLMM_PROGRAM_ID:
        raise ValueError("Pool is not owned by the Meteora DLMM program")
    first = dlmm_quote._decode_pair(discovered.get("data"), chain)
    if input_mint == first["mint_x"] and output_mint == first["mint_y"]:
        input_is_x = True
    elif input_mint == first["mint_y"] and output_mint == first["mint_x"]:
        input_is_x = False
    else:
        raise ValueError("Quote mint pair does not match the Meteora DLMM pool")

    indices = fresh.get("loaded_bin_array_indices")
    addresses = fresh.get("loaded_bin_array_addresses")
    if (
        not isinstance(indices, list) or not indices
        or not isinstance(addresses, list) or len(indices) != len(addresses)
        or len(indices) > 3
    ):
        raise ValueError("Fresh DLMM quote does not expose a bounded initialized bin-array set")
    expected_indices = dlmm_quote._array_indices(
        first["active_id"], input_is_x, first["min_bin_id"], first["max_bin_id"]
    )
    if indices != expected_indices[:len(indices)]:
        raise ValueError("DLMM quote bin-array direction does not match the pool active bin")
    expected_addresses = [
        chain._find_program_address(
            [b"bin_array", chain._b58decode(pool),
             index.to_bytes(8, "little", signed=True)],
            DLMM_PROGRAM_ID,
        )[0]
        for index in indices
    ]
    if addresses != expected_addresses:
        raise ValueError("DLMM quote bin-array addresses do not match canonical PDAs")

    bitmap_address = chain._find_program_address(
        [b"bitmap", chain._b58decode(pool)], DLMM_PROGRAM_ID
    )[0]
    event_authority = chain._find_program_address(
        [b"__event_authority"], DLMM_PROGRAM_ID
    )[0]
    bitmap_present = fresh.get("bitmap_extension_present") is True
    bitmap_account_key = bitmap_address if bitmap_present else DLMM_PROGRAM_ID
    host_fee_account_key = DLMM_PROGRAM_ID  # Optional host fee account omitted.
    user_x = user_input_token_account if input_is_x else user_output_token_account
    user_y = user_output_token_account if input_is_x else user_input_token_account

    fetch_keys = [
        pool, first["reserve_x"], first["reserve_y"],
        first["mint_x"], first["mint_y"], first["oracle"],
        user_x, user_y, bitmap_address,
    ] + addresses
    accounts, slot = chain._multiple_accounts_with_context(fetch_keys)
    if len(accounts) != len(fetch_keys) or any(a is None for a in accounts[:8]):
        raise ValueError("DLMM transaction snapshot is missing a required pool, vault, mint, oracle or user account")
    (pool_acc, reserve_x, reserve_y, mint_x, mint_y, oracle_acc,
     user_x_acc, user_y_acc, bitmap_acc) = accounts[:9]
    if pool_acc.get("owner") != DLMM_PROGRAM_ID:
        raise ValueError("DLMM pool owner changed during transaction snapshot")
    state = dlmm_quote._decode_pair(pool_acc.get("data"), chain)
    if hashlib.sha256(pool_acc["data"]).hexdigest() != fresh.get("pool_state_sha256"):
        raise ValueError("Meteora DLMM pool state changed after the fresh quote")
    if slot < fresh.get("slot", 0) or slot - fresh.get("slot", 0) > max_quote_slot_lag:
        raise ValueError("Fresh DLMM quote is stale relative to transaction snapshot")
    if any(a.get("owner") != chain.TOKEN_PROGRAM for a in (mint_x, mint_y, reserve_x, reserve_y)):
        raise ValueError("Native DLMM builder supports legacy SPL Token mints and reserves only")
    for mint_account, label in ((mint_x, "Token X mint"), (mint_y, "Token Y mint")):
        mint_data = mint_account.get("data", b"")
        if len(mint_data) != 82 or mint_data[45] != 1:
            raise ValueError(label + " has an invalid or uninitialized legacy mint layout")
    if chain._key(reserve_x.get("data", b""), 32) != pool or chain._key(reserve_y.get("data", b""), 32) != pool:
        raise ValueError("DLMM reserve authority does not match the LbPair")
    chain._token_amount(reserve_x, state["mint_x"])
    chain._token_amount(reserve_y, state["mint_y"])
    expected_oracle = chain._find_program_address(
        [b"oracle", chain._b58decode(pool)], DLMM_PROGRAM_ID
    )[0]
    if state["oracle"] != expected_oracle:
        raise ValueError("DLMM oracle address is not the canonical pool PDA")
    if oracle_acc.get("owner") != DLMM_PROGRAM_ID:
        raise ValueError("DLMM oracle account owner is invalid")
    oracle_raw = oracle_acc.get("data", b"")
    if len(oracle_raw) < 16 or oracle_raw[:8] != chain._discriminator("Oracle"):
        raise ValueError("DLMM oracle discriminator or layout is invalid")
    if bitmap_present:
        if bitmap_acc is None or bitmap_acc.get("owner") != DLMM_PROGRAM_ID:
            raise ValueError("DLMM bitmap extension is missing or has invalid owner")
        if len(bitmap_acc.get("data", b"")) < 40 or chain._key(bitmap_acc["data"], 8) != pool:
            raise ValueError("DLMM bitmap extension does not belong to this LbPair")
        if hashlib.sha256(bitmap_acc["data"]).hexdigest() != fresh.get("bitmap_extension_sha256"):
            raise ValueError("DLMM bitmap extension changed after the fresh quote")
    elif bitmap_acc is not None:
        raise ValueError("DLMM bitmap extension appeared after the quote snapshot")

    balance_x = _decode_user_token(user_x_acc, wallet, state["mint_x"], "User token X account")
    balance_y = _decode_user_token(user_y_acc, wallet, state["mint_y"], "User token Y account")
    input_balance = balance_x if input_is_x else balance_y
    if input_balance < amount:
        raise ValueError("Wallet input token account has insufficient balance")
    expected_array_hashes = fresh.get("loaded_bin_array_sha256")
    if not isinstance(expected_array_hashes, list) or len(expected_array_hashes) != len(addresses):
        raise ValueError("Fresh DLMM quote is missing bin-array fingerprints")
    for address, index, account, expected_hash in zip(
        addresses, indices, accounts[9:], expected_array_hashes
    ):
        if account is None:
            raise ValueError("A quoted DLMM bin array disappeared before transaction preparation")
        if hashlib.sha256(account["data"]).hexdigest() != expected_hash:
            raise ValueError("DLMM bin array changed after the fresh quote")
        decoded = dlmm_quote._decode_bin_array(
            account, pool, index, state, chain
        )
        if not decoded:
            raise ValueError("DLMM bin array has no supported bin data")

    # Swap2: amount_in, min_amount_out, RemainingAccountsInfo { slices: [] }.
    data = SWAP2_DISCRIMINATOR + struct.pack("<QQI", amount, minimum_out, 0)
    metas = [
        {"pubkey": pool, "is_signer": False, "is_writable": True},
        {"pubkey": bitmap_account_key, "is_signer": False, "is_writable": bitmap_present},
        {"pubkey": state["reserve_x"], "is_signer": False, "is_writable": True},
        {"pubkey": state["reserve_y"], "is_signer": False, "is_writable": True},
        {"pubkey": user_input_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": user_output_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": state["mint_x"], "is_signer": False, "is_writable": False},
        {"pubkey": state["mint_y"], "is_signer": False, "is_writable": False},
        {"pubkey": state["oracle"], "is_signer": False, "is_writable": True},
        {"pubkey": host_fee_account_key, "is_signer": False, "is_writable": False},
        {"pubkey": wallet, "is_signer": True, "is_writable": False},
        {"pubkey": chain.TOKEN_PROGRAM, "is_signer": False, "is_writable": False},
        {"pubkey": chain.TOKEN_PROGRAM, "is_signer": False, "is_writable": False},
        {"pubkey": MEMO_PROGRAM_ID, "is_signer": False, "is_writable": False},
        {"pubkey": event_authority, "is_signer": False, "is_writable": False},
        {"pubkey": DLMM_PROGRAM_ID, "is_signer": False, "is_writable": False},
    ]
    metas.extend(
        {"pubkey": address, "is_signer": False, "is_writable": True}
        for address in addresses
    )
    latest = chain._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    value = (latest or {}).get("value") or {}
    blockhash, last_valid = value.get("blockhash"), value.get("lastValidBlockHeight")
    _pubkey(blockhash, "latest blockhash")
    if isinstance(last_valid, bool) or not isinstance(last_valid, int) or last_valid <= 0:
        raise ValueError("RPC latest blockhash response is incomplete")
    transaction, account_keys = _compile_message(wallet, blockhash, metas, data)
    return {
        "provider": "meteora_dlmm_native_builder", "protocol": "meteora_dlmm",
        "pool_id": pool, "slot": slot, "quote_slot": quote_slot,
        "fresh_quote_slot": fresh["slot"], "quote_slot_lag": slot - fresh["slot"],
        "input_mint": input_mint, "output_mint": output_mint,
        "input_amount": amount, "minimum_output_amount": minimum_out,
        "instruction": "Swap2", "instruction_discriminator": list(SWAP2_DISCRIMINATOR),
        "bin_array_indices": indices, "bin_array_addresses": addresses,
        "bitmap_extension": bitmap_address if bitmap_present else None,
        "account_keys": account_keys, "required_signers": [wallet],
        "transaction": base64.b64encode(transaction).decode("ascii"),
        "transaction_version": "legacy", "last_valid_block_height": last_valid,
        "signature_status": "UNSIGNED_EXTERNAL_WALLET_REQUIRED",
        "execution": "LOCKED", "signing": "DISABLED_ON_SERVER",
        "broadcast": "DISABLED", "live_trading_ready": False,
    }


def simulate_meteora_dlmm_transaction(transaction_base64):
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
    value = (result or {}).get("value") or {}
    if value.get("err") is not None:
        logs = value.get("logs") or []
        raise ValueError("Meteora DLMM unsigned simulation failed: " + " | ".join(logs[-4:])[:500])
    return {
        "status": "PASS", "success": True,
        "signature_verification": "DISABLED_UNSIGNED_SIMULATION",
        "units_consumed": value.get("unitsConsumed"),
        "logs": (value.get("logs") or [])[-8:],
        "signing": "NOT_PERFORMED", "broadcast": "NOT_PERFORMED",
        "execution": "LOCKED",
    }
