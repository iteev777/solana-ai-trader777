"""Unsigned native Pump.fun legacy bonding-curve buy/sell builder.

Implements the verified classic SOL-quoted curve instructions for legacy SPL
Token and the audited Token-2022 transfer-fee extension subset. Alternate quote
mints, mayhem, cashback and holder-reward modes remain fail-closed. The builder
re-quotes and re-reads curve/global/fee/mint/token accounts, derives canonical
PDAs/ATAs with the mint-specific token program, and returns an unsigned transaction.
"""
import base64
import struct

import solana_onchain as chain
import token2022_inspector as token2022
from raydium_cpmm_builder import _compile_legacy_message, _priority_fee_micro_lamports

PROGRAM_ID = chain.PROGRAMS["pumpfun_legacy"]
TOKEN_PROGRAM_ID = chain.TOKEN_PROGRAM
FEE_PROGRAM_ID = chain.PUMP_FEES_PROGRAM
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
BUY_EXACT_SOL_IN = bytes([56, 252, 116, 8, 158, 223, 205, 95])
SELL = bytes([51, 230, 133, 164, 1, 127, 131, 173])
GLOBAL_SEED = b"global"
BONDING_CURVE_SEED = b"bonding-curve"
CREATOR_VAULT_SEED = b"creator-vault"
BONDING_CURVE_V2_SEED = b"bonding-curve-v2"
EVENT_AUTHORITY_SEED = b"__event_authority"
GLOBAL_VOLUME_SEED = b"global_volume_accumulator"
USER_VOLUME_SEED = b"user_volume_accumulator"
FEE_CONFIG_SEED = b"fee_config"
GLOBAL_DISCRIMINATOR = chain.PUMP_GLOBAL_CONFIG_DISCRIMINATOR
MAX_QUOTE_SLOT_LAG = 32
COMPUTE_UNITS = 130_000


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


def _token_account(account, token_program, mint, authority, label, min_balance=0):
    return token2022.validate_token_account_for_program(
        account, token_program, mint, authority, label, min_balance
    )


def _fee_rates(curve, global_raw, fee_config_raw):
    decoded = chain._decode_pumpswap_fee_config(fee_config_raw)
    tiers = decoded.get("fee_tiers") or []
    market_cap = (
        curve["virtual_quote_reserves"] * curve["token_total_supply"]
        // curve["virtual_token_reserves"]
    )
    if tiers:
        ordered = sorted(tiers, key=lambda item: item["threshold"])
        selected = ordered[0]
        for tier in ordered:
            if market_cap >= tier["threshold"]:
                selected = tier
            else:
                break
        rates = [
            selected["lp_fee_bps"],
            selected["protocol_fee_bps"],
            selected["creator_fee_bps"],
        ]
    else:
        rates = [0, chain._u64(global_raw, 105), chain._u64(global_raw, 154)]
    if curve.get("creator") == chain.DEFAULT_PUBKEY:
        rates[2] = 0
    if any(isinstance(rate, bool) or not isinstance(rate, int) or rate < 0 or rate > 10_000 for rate in rates):
        raise ValueError("Pump.fun fee tier contains an invalid basis-point rate")
    if sum(rates) >= 10_000:
        raise ValueError("Pump.fun fee tier consumes the entire trade")
    return rates, market_cap


def _global_recipients(global_raw):
    if not isinstance(global_raw, (bytes, bytearray)) or len(global_raw) < 997:
        raise ValueError("Pump.fun Global recipient arrays are truncated")
    if global_raw[:8] != GLOBAL_DISCRIMINATOR:
        raise ValueError("Pump.fun Global discriminator is invalid")
    fee_recipient = None
    for offset in range(162, 162 + 7 * 32, 32):
        candidate = chain._b58encode(global_raw[offset:offset + 32])
        if candidate != chain.DEFAULT_PUBKEY:
            fee_recipient = candidate
            break
    buybacks = []
    for offset in range(741, 741 + 8 * 32, 32):
        candidate = chain._b58encode(global_raw[offset:offset + 32])
        if candidate != chain.DEFAULT_PUBKEY and candidate not in buybacks:
            buybacks.append(candidate)
        if len(buybacks) == 2:
            break
    if not fee_recipient or len(buybacks) != 2:
        raise ValueError("Pump.fun Global does not expose valid fee recipients")
    return fee_recipient, buybacks[0], buybacks[1]


def build_pumpfun_legacy_swap(quote, wallet, user_input_token_account,
                              user_output_token_account,
                              max_quote_slot_lag=MAX_QUOTE_SLOT_LAG):
    """Build a classic Pump.fun buy_exact_sol_in or sell transaction."""
    if not isinstance(quote, dict) or quote.get("protocol") != "pumpfun_legacy":
        raise ValueError("A validated Pump.fun legacy quote is required")
    if quote.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT":
        raise ValueError("Quote is not a confirmed Pump.fun legacy quote")
    pool = quote.get("pool_id")
    input_mint, output_mint = quote.get("input_mint"), quote.get("output_mint")
    amount = _u64(quote.get("input_amount"), "quote input amount")
    quote_slot = quote.get("slot")
    if isinstance(quote_slot, bool) or not isinstance(quote_slot, int) or quote_slot <= 0:
        raise ValueError("Quote must include a positive confirmed slot")
    if isinstance(max_quote_slot_lag, bool) or not isinstance(max_quote_slot_lag, int) or not 0 <= max_quote_slot_lag <= 150:
        raise ValueError("Maximum quote slot lag is invalid")
    for value, label in (
        (pool, "bonding curve"), (input_mint, "input mint"),
        (output_mint, "output mint"), (wallet, "wallet"),
        (user_input_token_account, "input token account"),
        (user_output_token_account, "output token account"),
    ):
        _pubkey(value, label)
    if user_input_token_account == user_output_token_account:
        raise ValueError("Pump.fun input and output token-account references must differ")
    buying = input_mint == chain.SOL_MINT and output_mint != chain.SOL_MINT
    selling = output_mint == chain.SOL_MINT and input_mint != chain.SOL_MINT
    if not (buying or selling):
        raise ValueError("Pump.fun legacy trades only support native SOL/token directions")
    token_mint = output_mint if buying else input_mint
    slippage = quote.get("slippage_bps", 100)
    if isinstance(slippage, bool) or not isinstance(slippage, int) or not 0 <= slippage <= 300:
        raise ValueError("Quote slippage must be between 0 and 300 bps")

    fresh = chain.quote_pumpfun_legacy_pool(pool, input_mint, output_mint, amount, slippage)
    if (
        not isinstance(fresh, dict)
        or fresh.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT"
        or fresh.get("pool_id") != pool
        or fresh.get("input_mint") != input_mint
        or fresh.get("output_mint") != output_mint
        or fresh.get("input_amount") != amount
    ):
        raise ValueError("Fresh Pump.fun quote does not validate curve, pair and amount")
    fresh_out = _u64(fresh.get("out_amount"), "fresh quote output")
    minimum_out = _u64(fresh.get("minimum_out_amount"), "fresh minimum output")
    if minimum_out > fresh_out:
        raise ValueError("Fresh Pump.fun minimum output exceeds its quoted output")
    token_program = fresh.get("token_program")
    if token_program not in (TOKEN_PROGRAM_ID, chain.TOKEN_2022_PROGRAM):
        raise ValueError("Fresh Pump.fun quote contains an unsupported token program")
    if quote.get("token_program") != token_program:
        raise ValueError("Pump.fun token program changed since the supplied quote")
    fresh_slot = fresh.get("slot")
    if isinstance(fresh_slot, bool) or not isinstance(fresh_slot, int) or fresh_slot < quote_slot:
        raise ValueError("Fresh Pump.fun quote is older than the supplied quote")

    discovered = chain._account(pool)
    if discovered.get("owner") != PROGRAM_ID:
        raise ValueError("Bonding curve is not owned by the Pump.fun program")
    first = chain._decode_pool("pumpfun_legacy", discovered.get("data", b""))
    if first.get("complete") or first.get("is_mayhem_mode") or first.get("is_cashback_coin") or first.get("is_holder_reward"):
        raise ValueError("Pump.fun curve is completed or uses an unsupported fee mode")
    if first.get("quote_mint") not in (None, chain.DEFAULT_PUBKEY):
        raise ValueError("Pump.fun alternate quote-mint curves are not supported")
    if first.get("creator_fee_bps", 0) > 0:
        raise ValueError("Pump.fun custom creator-fee curves are not supported")
    if first.get("creator") != quote.get("creator") or quote.get("creator") != fresh.get("creator"):
        raise ValueError("Pump.fun creator state changed since the supplied quote")
    curve_mint = token_mint
    curve_pda, _ = chain._find_program_address(
        [BONDING_CURVE_SEED, _pubkey(curve_mint, "token mint")], PROGRAM_ID
    )
    if curve_pda != pool:
        raise ValueError("Pump.fun bonding curve PDA does not match the requested token mint")
    for field in ("virtual_token_reserves", "virtual_quote_reserves",
                  "real_token_reserves", "real_quote_reserves",
                  "token_total_supply", "creator", "is_mayhem_mode",
                  "is_cashback_coin", "is_holder_reward", "quote_mint",
                  "creator_fee_bps"):
        if quote.get(field) != fresh.get(field):
            raise ValueError("Pump.fun curve state changed since the supplied quote")

    global_address, _ = chain._find_program_address([GLOBAL_SEED], PROGRAM_ID)
    if global_address != chain.PUMP_GLOBAL_CONFIG:
        raise ValueError("Pump.fun Global PDA does not match the canonical address")
    fee_config_address, _ = chain._find_program_address(
        [FEE_CONFIG_SEED, _pubkey(PROGRAM_ID, "Pump.fun program")],
        FEE_PROGRAM_ID,
    )
    if fee_config_address != chain.PUMP_FEE_CONFIG:
        raise ValueError("Pump.fun FeeConfig PDA does not match the canonical address")
    curve_token_account, _ = chain._find_program_address(
        [_pubkey(pool, "bonding curve"), _pubkey(token_program, "token program"),
         _pubkey(token_mint, "token mint")],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    user_token_account = user_output_token_account if buying else user_input_token_account
    user_ata, _ = chain._find_program_address(
        [_pubkey(wallet, "wallet"), _pubkey(token_program, "token program"),
         _pubkey(token_mint, "token mint")],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    if user_token_account != user_ata:
        raise ValueError("Pump.fun wallet token account is not the canonical associated token account")
    creator = first["creator"]
    creator_vault, _ = chain._find_program_address(
        [CREATOR_VAULT_SEED, _pubkey(creator, "curve creator")], PROGRAM_ID
    )
    event_authority, _ = chain._find_program_address([EVENT_AUTHORITY_SEED], PROGRAM_ID)
    global_volume, _ = chain._find_program_address([GLOBAL_VOLUME_SEED], PROGRAM_ID)
    user_volume, _ = chain._find_program_address(
        [USER_VOLUME_SEED, _pubkey(wallet, "wallet")], PROGRAM_ID
    )
    bonding_curve_v2 = None
    if creator != chain.DEFAULT_PUBKEY:
        bonding_curve_v2, _ = chain._find_program_address(
            [BONDING_CURVE_V2_SEED, _pubkey(token_mint, "token mint")], PROGRAM_ID
        )

    keys = [pool, token_mint, global_address, fee_config_address,
            curve_token_account, user_token_account]
    accounts, slot = chain._multiple_accounts_with_context(keys)
    if len(accounts) != len(keys) or any(item is None for item in accounts):
        raise ValueError("Pump.fun transaction snapshot is missing a required account")
    pool_acc, mint_acc, global_acc, fee_acc, curve_tokens, user_tokens = accounts
    if pool_acc.get("owner") != PROGRAM_ID:
        raise ValueError("Pump.fun curve owner changed during transaction snapshot")
    current = chain._decode_pool("pumpfun_legacy", pool_acc.get("data", b""))
    if current.get("complete") or current.get("creator") != creator:
        raise ValueError("Pump.fun curve completed or creator changed during transaction preparation")
    for field in ("virtual_token_reserves", "virtual_quote_reserves",
                  "real_token_reserves", "real_quote_reserves",
                  "token_total_supply", "creator", "is_mayhem_mode",
                  "is_cashback_coin", "is_holder_reward", "quote_mint",
                  "creator_fee_bps"):
        if current.get(field) != first.get(field):
            raise ValueError("Pump.fun curve state changed during transaction preparation")
    if slot < fresh_slot or slot - fresh_slot > max_quote_slot_lag:
        raise ValueError("Fresh Pump.fun quote is stale relative to transaction snapshot")
    if mint_acc.get("owner") != token_program:
        raise ValueError("Pump.fun mint owner changed during transaction preparation")
    if token_program == chain.TOKEN_2022_PROGRAM:
        token2022.validate_swap_compatible_mint_data(mint_acc.get("data", b""))
    else:
        mint_raw = mint_acc.get("data", b"")
        if not isinstance(mint_raw, (bytes, bytearray)) or len(mint_raw) != 82 or mint_raw[45] != 1:
            raise ValueError("Pump.fun mint is not an initialized legacy SPL Token mint")
    if chain._mint_decimals(mint_acc) != fresh.get("token_decimals"):
        raise ValueError("Pump.fun mint decimals changed since the fresh quote")
    global_raw = global_acc.get("data", b"")
    if global_acc.get("owner") != PROGRAM_ID:
        raise ValueError("Pump.fun Global account is not owned by the Pump program")
    fee_raw = fee_acc.get("data", b"")
    if fee_acc.get("owner") != FEE_PROGRAM_ID:
        raise ValueError("Pump.fun FeeConfig is not owned by the Pump Fees program")
    fee_recipient, buyback_wallet, buyback_paid = _global_recipients(global_raw)
    rates, market_cap = _fee_rates(current, global_raw, fee_raw)
    if rates != fresh.get("fee_rates_bps") or rates != quote.get("fee_rates_bps"):
        raise ValueError("Pump.fun fee tier changed since the supplied quote")
    if market_cap != fresh.get("market_cap_lamports"):
        raise ValueError("Pump.fun market cap changed since the fresh quote")

    curve_balance = _token_account(
        curve_tokens, token_program, token_mint, pool,
        "Pump.fun associated bonding curve token account"
    )
    if curve_balance != current["real_token_reserves"]:
        raise ValueError("Pump.fun curve token-account balance does not match real token reserves")
    _token_account(
        user_tokens, token_program, token_mint, wallet,
        "Pump.fun wallet token account", min_balance=amount if selling else 0,
    )

    metas = [
        {"pubkey": global_address, "is_signer": False, "is_writable": False},
        {"pubkey": fee_recipient, "is_signer": False, "is_writable": True},
        {"pubkey": token_mint, "is_signer": False, "is_writable": False},
        {"pubkey": pool, "is_signer": False, "is_writable": True},
        {"pubkey": curve_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": user_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": wallet, "is_signer": True, "is_writable": True},
        {"pubkey": SYSTEM_PROGRAM_ID, "is_signer": False, "is_writable": False},
    ]
    if buying:
        metas.extend([
            {"pubkey": token_program, "is_signer": False, "is_writable": False},
            {"pubkey": creator_vault, "is_signer": False, "is_writable": True},
            {"pubkey": event_authority, "is_signer": False, "is_writable": False},
            {"pubkey": PROGRAM_ID, "is_signer": False, "is_writable": False},
            {"pubkey": global_volume, "is_signer": False, "is_writable": True},
            {"pubkey": user_volume, "is_signer": False, "is_writable": True},
            {"pubkey": fee_config_address, "is_signer": False, "is_writable": False},
            {"pubkey": FEE_PROGRAM_ID, "is_signer": False, "is_writable": False},
        ])
        instruction_data = BUY_EXACT_SOL_IN + struct.pack("<QQ", amount, minimum_out)
    else:
        metas.extend([
            {"pubkey": creator_vault, "is_signer": False, "is_writable": True},
            {"pubkey": TOKEN_PROGRAM_ID, "is_signer": False, "is_writable": False},
            {"pubkey": event_authority, "is_signer": False, "is_writable": False},
            {"pubkey": PROGRAM_ID, "is_signer": False, "is_writable": False},
            {"pubkey": fee_config_address, "is_signer": False, "is_writable": False},
            {"pubkey": FEE_PROGRAM_ID, "is_signer": False, "is_writable": False},
        ])
        instruction_data = SELL + struct.pack("<QQ", amount, minimum_out)
    if bonding_curve_v2 is not None:
        metas.append({"pubkey": bonding_curve_v2, "is_signer": False, "is_writable": True})
    metas.extend([
        {"pubkey": buyback_wallet, "is_signer": False, "is_writable": True},
        {"pubkey": buyback_paid, "is_signer": False, "is_writable": True},
    ])

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
        "provider": "pumpfun_legacy_native_builder",
        "protocol": "pumpfun_legacy",
        "pool_id": pool,
        "slot": slot,
        "quote_slot": quote_slot,
        "fresh_quote_slot": fresh_slot,
        "quote_slot_lag": slot - fresh_slot,
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount,
        "minimum_output_amount": minimum_out,
        "token_program": token_program,
        "token2022_fee_aware_supported": token_program == chain.TOKEN_2022_PROGRAM,
        "instruction": "buy_exact_sol_in" if buying else "sell",
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


def simulate_pumpfun_legacy_transaction(transaction_base64):
    """Simulate an unsigned Pump.fun legacy transaction; never sign or send."""
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
