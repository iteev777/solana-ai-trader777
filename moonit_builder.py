"""Unsigned native Moonit ConstantProductV1 transaction builder.

Moonit settles the collateral leg in native SOL, not WSOL. The builder uses
the verified 11-account buy/sell instruction, validates the global fee config
and the curve's associated token account, and requires the wallet's existing
base-token account. It never signs or broadcasts.
"""
import base64
import struct

import solana_onchain as chain
from raydium_cpmm_builder import _compile_legacy_message

PROGRAM_ID = chain.PROGRAMS["moonit_amm"]
TOKEN_PROGRAM_ID = chain.TOKEN_PROGRAM
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
CONFIG_SEED = b"config_account"
CONFIG_DISCRIMINATOR = bytes([189, 255, 97, 70, 186, 189, 24, 102])
CURVE_DISCRIMINATOR = bytes([8, 91, 83, 28, 132, 216, 248, 22])
BUY_DISCRIMINATOR = bytes([0x66, 0x06, 0x3d, 0x12, 0x01, 0xda, 0xeb, 0xea])
SELL_DISCRIMINATOR = bytes([0x33, 0xe6, 0x85, 0xa4, 0x01, 0x7f, 0x83, 0xad])
FIXED_SIDE_IN = 0
MAX_QUOTE_SLOT_LAG = 32
COMPUTE_UNITS = 90_000


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


def _token_account(account, mint, authority, label, min_balance=0):
    if not isinstance(account, dict) or account.get("owner") != TOKEN_PROGRAM_ID:
        raise ValueError(label + " must use the legacy SPL Token program")
    raw = account.get("data", b"")
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 165:
        raise ValueError(label + " has an invalid legacy token-account layout")
    if chain._key(raw, 0) != mint:
        raise ValueError(label + " mint does not match")
    if chain._key(raw, 32) != authority:
        raise ValueError(label + " authority does not match")
    if raw[108] != 1:
        raise ValueError(label + " is not initialized or is frozen")
    balance = int.from_bytes(raw[64:72], "little")
    if balance < min_balance:
        raise ValueError(label + " has insufficient token balance")
    return balance


def build_moonit_swap(quote, wallet, user_input_token_account,
                      user_output_token_account,
                      max_quote_slot_lag=MAX_QUOTE_SLOT_LAG):
    """Build an unsigned Moonit buy or sell using native SOL settlement."""
    if not isinstance(quote, dict) or quote.get("protocol") != "moonit_amm":
        raise ValueError("A validated Moonit quote is required")
    if quote.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT":
        raise ValueError("Quote is not a confirmed Moonit quote")
    pool = quote.get("pool_id")
    input_mint, output_mint = quote.get("input_mint"), quote.get("output_mint")
    amount = _u64(quote.get("input_amount"), "quote input amount")
    quote_slot = quote.get("slot")
    if isinstance(quote_slot, bool) or not isinstance(quote_slot, int) or quote_slot <= 0:
        raise ValueError("Quote must include a positive confirmed slot")
    if isinstance(max_quote_slot_lag, bool) or not isinstance(max_quote_slot_lag, int) or not 0 <= max_quote_slot_lag <= 150:
        raise ValueError("Maximum quote slot lag is invalid")
    for value, label in (
        (pool, "curve"), (input_mint, "input mint"), (output_mint, "output mint"),
        (wallet, "wallet"), (user_input_token_account, "input token account"),
        (user_output_token_account, "output token account"),
    ):
        _pubkey(value, label)
    if user_input_token_account == user_output_token_account:
        raise ValueError("Moonit input and output token-account references must be distinct")

    token_mint = quote.get("output_mint") if input_mint == chain.SOL_MINT else input_mint
    if token_mint == chain.SOL_MINT:
        raise ValueError("Moonit requires one non-SOL base mint")
    if input_mint == chain.SOL_MINT and output_mint == token_mint:
        buying = True
    elif input_mint == token_mint and output_mint == chain.SOL_MINT:
        buying = False
    else:
        raise ValueError("Moonit trades only between native SOL and the curve's base mint")

    slippage = quote.get("slippage_bps", 100)
    if isinstance(slippage, bool) or not isinstance(slippage, int) or not 0 <= slippage <= 300:
        raise ValueError("Quote slippage must be between 0 and 300 bps")
    fresh = chain.quote_moonit_pool(pool, input_mint, amount, slippage)
    if (
        not isinstance(fresh, dict)
        or fresh.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT"
        or fresh.get("pool_id") != pool
        or fresh.get("input_mint") != input_mint
        or fresh.get("output_mint") != output_mint
        or fresh.get("input_amount") != amount
    ):
        raise ValueError("Fresh Moonit quote does not validate curve, pair and amount")
    minimum_out = _u64(fresh.get("minimum_out_amount"), "fresh minimum output")
    fresh_slot = fresh.get("slot")
    if isinstance(fresh_slot, bool) or not isinstance(fresh_slot, int) or fresh_slot < quote_slot:
        raise ValueError("Fresh Moonit quote is older than the supplied quote")

    discovered = chain._account(pool)
    if discovered.get("owner") != PROGRAM_ID:
        raise ValueError("Curve account is not owned by the Moonit program")
    first = chain._decode_pool("moonit_amm", discovered.get("data", b""))
    if first.get("curve_type") != 1 or first.get("collateral_currency") != 0:
        raise ValueError("Moonit builder supports only native-SOL ConstantProductV1 curves")
    if first.get("curve_amount", 0) <= 0 or first.get("curve_amount", 0) > first.get("total_supply", 0):
        raise ValueError("Moonit curve amount is invalid")
    if first.get("token_decimals") != 9:
        raise ValueError("Moonit builder supports only the verified 9-decimal token format")
    if first.get("base_mint") != token_mint:
        raise ValueError("Quote base mint does not match the Moonit curve")
    if (fresh.get("curve_amount") != first["curve_amount"]
            or fresh.get("total_supply") != first["total_supply"]
            or quote.get("curve_amount") != fresh.get("curve_amount")
            or quote.get("total_supply") != fresh.get("total_supply")):
        raise ValueError("Moonit curve state changed since the supplied quote")

    config_address, config_bump = chain._find_program_address([CONFIG_SEED], PROGRAM_ID)
    curve_token_account, curve_token_bump = chain._find_program_address(
        [_pubkey(pool, "curve"), _pubkey(TOKEN_PROGRAM_ID, "token program"),
         _pubkey(token_mint, "base mint")],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    user_base_account = user_output_token_account if buying else user_input_token_account
    expected_user_base_account, user_ata_bump = chain._find_program_address(
        [_pubkey(wallet, "wallet"), _pubkey(TOKEN_PROGRAM_ID, "token program"),
         _pubkey(token_mint, "base mint")],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    if user_base_account != expected_user_base_account:
        raise ValueError("Moonit wallet base token account is not the canonical associated token account")

    keys = [pool, token_mint, config_address, curve_token_account, user_base_account]
    accounts, slot = chain._multiple_accounts_with_context(keys)
    if len(accounts) != len(keys) or any(item is None for item in accounts):
        raise ValueError("Moonit transaction snapshot is missing a curve, mint, config or token account")
    pool_acc, mint_acc, config_acc, curve_tokens, user_tokens = accounts
    if pool_acc.get("owner") != PROGRAM_ID:
        raise ValueError("Moonit curve owner changed during transaction snapshot")
    current = chain._decode_pool("moonit_amm", pool_acc.get("data", b""))
    for field in ("base_mint", "total_supply", "curve_amount",
                  "curve_type", "collateral_currency", "token_decimals"):
        if current.get(field) != first.get(field):
            raise ValueError("Moonit curve state changed during transaction preparation")
    if slot < fresh_slot or slot - fresh_slot > max_quote_slot_lag:
        raise ValueError("Fresh Moonit quote is stale relative to transaction snapshot")
    if mint_acc.get("owner") != TOKEN_PROGRAM_ID:
        raise ValueError("Moonit native builder supports legacy SPL Token mints only")
    if chain._mint_decimals(mint_acc) != current["token_decimals"]:
        raise ValueError("Moonit mint decimals changed since the fresh quote")

    mint_raw = mint_acc.get("data", b"")
    if len(mint_raw) != 82 or mint_raw[45] != 1:
        raise ValueError("Moonit mint has an invalid legacy mint layout")
    curve_balance = _token_account(curve_tokens, token_mint, pool, "Moonit curve token account")
    if curve_balance != current["curve_amount"]:
        raise ValueError("Moonit curve token account balance does not match curve state")

    config_raw = config_acc.get("data", b"")
    if (config_acc.get("owner") != PROGRAM_ID or len(config_raw) < 170
            or config_raw[:8] != CONFIG_DISCRIMINATOR):
        raise ValueError("Moonit ConfigAccount owner, discriminator or layout is invalid")
    helio_fee = chain._key(config_raw, 104)
    dex_fee = chain._key(config_raw, 136)
    fee_bps = chain._u16(config_raw, 168)
    if (fee_bps > 10_000 or fee_bps != fresh.get("fee_bps")
            or quote.get("fee_bps") != fresh.get("fee_bps")):
        raise ValueError("Moonit fee configuration changed or is invalid")
    _pubkey(helio_fee, "Helio fee recipient")
    _pubkey(dex_fee, "DEX fee recipient")

    _token_account(
        user_tokens, token_mint, wallet, "Moonit wallet base token account",
        min_balance=amount if not buying else 0,
    )

    if buying:
        discriminator = BUY_DISCRIMINATOR
        token_amount = minimum_out
        collateral_amount = amount
    else:
        discriminator = SELL_DISCRIMINATOR
        token_amount = amount
        collateral_amount = minimum_out
    instruction_data = (
        discriminator + struct.pack("<QQBQ", token_amount, collateral_amount,
                                     FIXED_SIDE_IN, 0)
    )
    metas = [
        {"pubkey": wallet, "is_signer": True, "is_writable": True},
        {"pubkey": user_base_account, "is_signer": False, "is_writable": True},
        {"pubkey": pool, "is_signer": False, "is_writable": True},
        {"pubkey": curve_token_account, "is_signer": False, "is_writable": True},
        {"pubkey": dex_fee, "is_signer": False, "is_writable": True},
        {"pubkey": helio_fee, "is_signer": False, "is_writable": True},
        {"pubkey": token_mint, "is_signer": False, "is_writable": False},
        {"pubkey": config_address, "is_signer": False, "is_writable": False},
        {"pubkey": TOKEN_PROGRAM_ID, "is_signer": False, "is_writable": False},
        {"pubkey": ASSOCIATED_TOKEN_PROGRAM_ID, "is_signer": False, "is_writable": False},
        {"pubkey": SYSTEM_PROGRAM_ID, "is_signer": False, "is_writable": False},
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
        "provider": "moonit_native_builder",
        "protocol": "moonit_amm",
        "pool_id": pool,
        "slot": slot,
        "quote_slot": quote_slot,
        "fresh_quote_slot": fresh_slot,
        "quote_slot_lag": slot - fresh_slot,
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount,
        "minimum_output_amount": minimum_out,
        "instruction": "buy" if buying else "sell",
        "instruction_data_base64": base64.b64encode(instruction_data).decode("ascii"),
        "instruction_accounts": [meta["pubkey"] for meta in metas],
        "account_keys": account_keys,
        "required_signers": [wallet],
        "transaction": base64.b64encode(transaction).decode("ascii"),
        "transaction_version": "legacy",
        "last_valid_block_height": last_valid,
        "config_bump": config_bump,
        "curve_token_bump": curve_token_bump,
        "user_ata_bump": user_ata_bump,
        "compute_units": COMPUTE_UNITS,
        "signature_status": "UNSIGNED_EXTERNAL_WALLET_REQUIRED",
        "execution": "LOCKED",
        "signing": "DISABLED_ON_SERVER",
        "broadcast": "DISABLED",
        "live_trading_ready": False,
    }


def simulate_moonit_transaction(transaction_base64):
    """Simulate an unsigned Moonit transaction; never sign or broadcast."""
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
