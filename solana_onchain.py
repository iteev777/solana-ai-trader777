"""Read-only Solana pool decoders for Trojan V2.

Supported layouts: PumpSwap Pool, Raydium CPMM PoolState, Orca Whirlpool,
Meteora DLMM LbPair. All account owners and account discriminators are checked.
No transaction signing or wallet keys are used.
"""
import base64
import hashlib
import math
import os
import struct
import time
import threading
import requests

RPC_URL = os.getenv("SOLANA_RPC_URL", "").strip() or os.getenv("SOLANA_RPC_URLS", "").split(",")[0].strip()
_rpc_semaphore = threading.BoundedSemaphore(4)
TIMEOUT = float(os.getenv("SOLANA_RPC_TIMEOUT", "8"))
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN_PROGRAMS = {TOKEN_PROGRAM, TOKEN_2022_PROGRAM}
PROGRAMS = {
    "pumpswap": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
    "raydium_cpmm": "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",
    "raydium_clmm": "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",
    "raydium_amm_v4": "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "orca_whirlpool": "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
    "meteora_dlmm": "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
    "meteora_damm_v2": "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG",
    "meteora_dbc": "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN",
    "fluxbeam_amm": "FLUXubRmkEi2q6K3Y9kBPg9248ggaZVsoSFhtJHSrm1X",
    "pumpfun_legacy": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "moonit_amm": "MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG",
}

PROTOCOL_ALIASES = {
    "pump_swap": "pumpswap", "pumpfun_amm": "pumpswap", "pump_amm": "pumpswap",
    "pumpfun": "pumpfun_legacy", "pump.fun": "pumpfun_legacy",
    "pumpfun_legacy": "pumpfun_legacy", "pump_fun": "pumpfun_legacy",
    "raydium": "raydium_auto", "raydium_amm": "raydium_auto",
    "raydium_amm_v4": "raydium_amm_v4", "raydium_cp_swap": "raydium_cpmm",
    "raydium_clmm": "raydium_clmm",
    "orca": "orca_whirlpool", "orca_whirlpools": "orca_whirlpool", "whirlpool": "orca_whirlpool",
    "meteora": "meteora_dlmm", "dlmm": "meteora_dlmm",
    "meteora_damm": "meteora_damm_v2", "meteora_damm_v2": "meteora_damm_v2",
    "damm_v2": "meteora_damm_v2", "meteora_dbc": "meteora_dbc", "dbc": "meteora_dbc",
    "fluxbeam": "fluxbeam_amm", "fluxbeam_amm": "fluxbeam_amm",
    "moonit": "moonit_amm", "moonit_amm": "moonit_amm",
    "moonshot": "moonit_amm", "moonshot_amm": "moonit_amm"
}


def normalize_protocol(protocol):
    value = str(protocol or "").strip().lower().replace("-", "_")
    return PROTOCOL_ALIASES.get(value, value)


STABLES = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}
_cache = {}
_last_status = {"configured": bool(RPC_URL), "last_success": None, "last_error": None}


def _b58decode(value):
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    for char in value:
        number = number * 58 + alphabet.index(char)
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\x00" * (len(value) - len(value.lstrip("1"))) + raw


def _b58encode(raw):
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    zeroes = len(raw) - len(raw.lstrip(b"\x00"))
    number = int.from_bytes(raw, "big")
    result = ""
    while number:
        number, remainder = divmod(number, 58)
        result = alphabet[remainder] + result
    return "1" * zeroes + (result or ("" if zeroes else "1"))


def _key(data, offset):
    if offset < 0 or offset + 32 > len(data):
        raise ValueError("pool account key offset out of range")
    return _b58encode(data[offset:offset + 32])


def _u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def _u64(data, offset):
    return struct.unpack_from("<Q", data, offset)[0]


def _i32(data, offset):
    return struct.unpack_from("<i", data, offset)[0]


def _u128(data, offset):
    return int.from_bytes(data[offset:offset + 16], "little")


def _discriminator(name):
    return hashlib.sha256(("account:" + name).encode()).digest()[:8]


def _rpc(method, params):
    if not RPC_URL:
        raise RuntimeError("SOLANA_RPC_URL or SOLANA_RPC_URLS is not configured")
    import rpc_transport
    with _rpc_semaphore:
        result = rpc_transport.rpc_call(method, params)
    return result


def _account(pubkey):
    result = _rpc("getAccountInfo", [pubkey, {"encoding": "base64", "commitment": "confirmed"}])
    value = (result or {}).get("value")
    if not value:
        raise ValueError("on-chain account not found")
    raw = base64.b64decode(value["data"][0])
    return {"owner": value.get("owner"), "data": raw, "lamports": value.get("lamports")}


def _multiple_accounts(keys):
    result = _rpc("getMultipleAccounts", [keys, {"encoding": "base64", "commitment": "confirmed"}])
    values = (result or {}).get("value") or []
    if len(values) != len(keys):
        raise ValueError("RPC returned unexpected account count")
    decoded = []
    for value in values:
        if not value:
            decoded.append(None)
        else:
            decoded.append({
                "owner": value.get("owner"),
                "data": base64.b64decode(value["data"][0])
            })
    return decoded


def _decode_pool(protocol, raw):
    if protocol == "pumpswap":
        if len(raw) < 244 or raw[:8] != bytes([241,154,109,4,17,177,109,188]):
            raise ValueError("invalid PumpSwap Pool discriminator/layout")
        return {
            "base_mint": _key(raw, 43), "quote_mint": _key(raw, 75),
            "vault_base": _key(raw, 139), "vault_quote": _key(raw, 171),
            "status": "active", "price_mode": "reserves"
        }
    if protocol == "raydium_cpmm":
        if len(raw) < 413 or raw[:8] != _discriminator("PoolState"):
            raise ValueError("invalid Raydium CPMM PoolState discriminator/layout")
        status = raw[329]
        return {
            "amm_config": _key(raw, 8),
            "vault_base": _key(raw, 72), "vault_quote": _key(raw, 104),
            "base_mint": _key(raw, 168), "quote_mint": _key(raw, 200),
            "token_program_base": _key(raw, 232), "token_program_quote": _key(raw, 264),
            "protocol_fees_base": _u64(raw, 341), "protocol_fees_quote": _u64(raw, 349),
            "fund_fees_base": _u64(raw, 357), "fund_fees_quote": _u64(raw, 365),
            "open_time": _u64(raw, 373), "creator_fee_on": raw[389],
            "enable_creator_fee": bool(raw[390]),
            "creator_fees_base": _u64(raw, 397), "creator_fees_quote": _u64(raw, 405),
            "status": "disabled" if status & 4 else "active",
            "price_mode": "reserves"
        }
    if protocol == "raydium_amm_v4":
        # Legacy AmmInfo is a non-Anchor account; its fields begin at byte 0.
        if len(raw) < 464:
            raise ValueError("invalid Raydium AMM v4 AmmInfo layout")
        status = _u64(raw, 0)
        if status not in (1, 6, 7):
            raise ValueError("Raydium AMM v4 pool status does not allow swaps")
        swap_fee_numerator = _u64(raw, 176)
        swap_fee_denominator = _u64(raw, 184)
        if swap_fee_denominator <= 0 or swap_fee_numerator >= swap_fee_denominator:
            raise ValueError("Raydium AMM v4 swap fee configuration is invalid")
        return {
            "vault_base": _key(raw, 336), "vault_quote": _key(raw, 368),
            "base_mint": _key(raw, 400), "quote_mint": _key(raw, 432),
            "swap_fee_numerator": swap_fee_numerator,
            "swap_fee_denominator": swap_fee_denominator,
            "need_take_pnl_base": _u64(raw, 192),
            "need_take_pnl_quote": _u64(raw, 200),
            "pool_open_time": _u64(raw, 224),
            "status": "active", "price_mode": "reserves"
        }
    if protocol == "raydium_clmm":
        if len(raw) < 269 or raw[:8] != _discriminator("PoolState"):
            raise ValueError("invalid Raydium CLMM PoolState discriminator/layout")
        sqrt_price = _u128(raw, 253)
        if sqrt_price <= 0:
            raise ValueError("Raydium CLMM sqrt price is zero")
        return {
            "base_mint": _key(raw, 73), "quote_mint": _key(raw, 105),
            "vault_base": _key(raw, 137), "vault_quote": _key(raw, 169),
            "sqrt_price_x64": sqrt_price,
            "status": "active", "price_mode": "sqrt_price"
        }
    if protocol == "orca_whirlpool":
        if len(raw) < 245 or raw[:8] != _discriminator("Whirlpool"):
            raise ValueError("invalid Orca Whirlpool discriminator/layout")
        sqrt_price = _u128(raw, 65)
        if sqrt_price <= 0:
            raise ValueError("Orca sqrt price is zero")
        return {
            "base_mint": _key(raw, 101), "vault_base": _key(raw, 133),
            "quote_mint": _key(raw, 181), "vault_quote": _key(raw, 213),
            "sqrt_price_x64": sqrt_price,
            "status": "active", "price_mode": "sqrt_price"
        }
    if protocol == "meteora_dlmm":
        if len(raw) < 216 or raw[:8] != bytes([33, 11, 49, 98, 181, 101, 177, 13]):
            raise ValueError("invalid Meteora LbPair discriminator/layout")
        # StaticParameters and VariableParameters are each 32-byte Borsh structs.
        active_id = _i32(raw, 76)
        bin_step = _u16(raw, 80)
        status = raw[82]
        if bin_step <= 0:
            raise ValueError("Meteora bin step invalid")
        return {
            "base_mint": _key(raw, 88), "quote_mint": _key(raw, 120),
            "vault_base": _key(raw, 152), "vault_quote": _key(raw, 184),
            "active_id": active_id, "bin_step": bin_step,
            "status": "disabled" if status != 0 else "active",
            "price_mode": "active_bin"
        }
    if protocol == "meteora_damm_v2":
        if len(raw) < 488 or raw[:8] != bytes([241, 154, 109, 4, 17, 177, 109, 188]):
            raise ValueError("invalid Meteora DAMM v2 Pool discriminator/layout")
        sqrt_price = _u128(raw, 456)
        if sqrt_price <= 0:
            raise ValueError("Meteora DAMM v2 sqrt price is zero")
        return {
            "base_mint": _key(raw, 168), "quote_mint": _key(raw, 200),
            "vault_base": _key(raw, 232), "vault_quote": _key(raw, 264),
            "sqrt_price_x64": sqrt_price,
            "status": "active" if raw[481] == 0 else "disabled",
            "price_mode": "sqrt_price"
        }
    if protocol == "meteora_dbc":
        if len(raw) < 306 or raw[:8] != bytes([213, 224, 5, 209, 98, 69, 119, 92]):
            raise ValueError("invalid Meteora DBC VirtualPool discriminator/layout")
        return {
            "config": _key(raw, 72), "base_mint": _key(raw, 136),
            "vault_base": _key(raw, 168), "vault_quote": _key(raw, 200),
            "base_reserve": _u64(raw, 232), "quote_reserve": _u64(raw, 240),
            "sqrt_price_x64": _u128(raw, 280),
            "status": "disabled" if raw[305] != 0 else "active",
            "price_mode": "dbc_curve"
        }
    if protocol == "fluxbeam_amm":
        if len(raw) != 324 or raw[1] != 1:
            raise ValueError("invalid FluxBeam SwapV1 layout or uninitialized pool")
        return {
            "base_mint": _key(raw, 131), "quote_mint": _key(raw, 163),
            "vault_base": _key(raw, 35), "vault_quote": _key(raw, 67),
            "status": "active", "price_mode": "reserves"
        }
    if protocol == "pumpfun_legacy":
        if len(raw) < 49 or raw[:8] != bytes([23, 183, 248, 55, 96, 216, 172, 96]):
            raise ValueError("invalid Pump.fun bonding curve discriminator/layout")
        if raw[48] != 0:
            raise ValueError("Pump.fun bonding curve is complete/migrated")
        return {
            "virtual_token_reserves": _u64(raw, 8),
            "virtual_quote_reserves": _u64(raw, 16),
            "real_token_reserves": _u64(raw, 24),
            "real_quote_reserves": _u64(raw, 32),
            "token_total_supply": _u64(raw, 40),
            "status": "active", "price_mode": "pump_curve"
        }
    if protocol == "moonit_amm":
        if len(raw) < 82 or raw[:8] != bytes([8, 91, 83, 28, 132, 216, 248, 22]):
            raise ValueError("invalid Moonit CurveAccount discriminator/layout")
        return {
            "total_supply": _u64(raw, 8), "curve_amount": _u64(raw, 16),
            "base_mint": _key(raw, 24), "token_decimals": raw[56],
            "collateral_currency": raw[57], "curve_type": raw[58],
            "status": "active", "price_mode": "moonit_curve"
        }
    raise ValueError("unsupported Solana pool protocol")



def _decode_raydium_amm_config(raw):
    """Decode the Raydium CPMM AmmConfig account using its Anchor layout."""
    if len(raw) < 116 or raw[:8] != _discriminator("AmmConfig"):
        raise ValueError("invalid Raydium CPMM AmmConfig discriminator/layout")
    trade_fee_rate = _u64(raw, 12)
    protocol_fee_rate = _u64(raw, 20)
    fund_fee_rate = _u64(raw, 28)
    creator_fee_rate = _u64(raw, 108)
    if (trade_fee_rate + creator_fee_rate >= 1_000_000
            or protocol_fee_rate > 1_000_000
            or fund_fee_rate > 1_000_000
            or protocol_fee_rate + fund_fee_rate > 1_000_000):
        raise ValueError("Raydium CPMM fee configuration is invalid")
    return {
        "trade_fee_rate": trade_fee_rate,
        "protocol_fee_rate": protocol_fee_rate,
        "fund_fee_rate": fund_fee_rate,
        "creator_fee_rate": creator_fee_rate,
    }


def _multiple_accounts_with_context(keys):
    result = _rpc("getMultipleAccounts", [
        keys, {"encoding": "base64", "commitment": "confirmed"}
    ])
    values = (result or {}).get("value") or []
    if len(values) != len(keys):
        raise ValueError("RPC returned unexpected account count")
    accounts = []
    for value in values:
        if not value:
            accounts.append(None)
        else:
            accounts.append({
                "owner": value.get("owner"),
                "data": base64.b64decode(value["data"][0]),
                "lamports": value.get("lamports"),
            })
    context = (result or {}).get("context") or {}
    slot = context.get("slot")
    if not isinstance(slot, int) or slot <= 0:
        raise ValueError("RPC snapshot is missing a confirmed context slot")
    return accounts, slot


def quote_raydium_cpmm_pool(pool_address, input_mint, amount_in,
                            slippage_bps=100, max_snapshot_age=5.0):
    """Read-only exact-input quote from a single confirmed Raydium CPMM snapshot.

    The first pool read discovers dependent account addresses. A second
    getMultipleAccounts call fetches pool/config/vaults/mints together; all
    quote inputs are decoded from that same RPC context slot.
    """
    if not isinstance(pool_address, str) or not pool_address.strip():
        raise ValueError("Raydium CPMM pool address is required")
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Input mint is required")
    if isinstance(amount_in, bool) or not isinstance(amount_in, int) or amount_in <= 0:
        raise ValueError("Input amount must be a positive integer")
    if isinstance(slippage_bps, bool) or not isinstance(slippage_bps, int) or not 0 <= slippage_bps <= 300:
        raise ValueError("Slippage must be an integer between 0 and 300 bps")

    discovered = _account(pool_address)
    if discovered.get("owner") != PROGRAMS["raydium_cpmm"]:
        raise ValueError("Pool account is not owned by the Raydium CPMM program")
    first = _decode_pool("raydium_cpmm", discovered["data"])
    keys = [
        pool_address, first["amm_config"], first["vault_base"], first["vault_quote"],
        first["base_mint"], first["quote_mint"]
    ]
    accounts, slot = _multiple_accounts_with_context(keys)
    pool_acc, config_acc, vault_base_acc, vault_quote_acc, mint_base_acc, mint_quote_acc = accounts
    if any(item is None for item in accounts):
        raise ValueError("Raydium CPMM snapshot is missing one or more accounts")
    if pool_acc["owner"] != PROGRAMS["raydium_cpmm"]:
        raise ValueError("Raydium CPMM pool owner changed during snapshot")
    decoded = _decode_pool("raydium_cpmm", pool_acc["data"])
    if decoded["amm_config"] != first["amm_config"] or decoded["vault_base"] != first["vault_base"] or decoded["vault_quote"] != first["vault_quote"]:
        raise ValueError("Raydium CPMM pool accounts changed during snapshot")
    if config_acc["owner"] != PROGRAMS["raydium_cpmm"]:
        raise ValueError("AmmConfig is not owned by the Raydium CPMM program")
    config = _decode_raydium_amm_config(config_acc["data"])
    if decoded["status"] != "active":
        raise ValueError("Raydium CPMM pool swaps are disabled")
    now = time.time()
    if decoded["open_time"] > int(now):
        raise ValueError("Raydium CPMM pool is not open for swaps yet")
    if mint_base_acc["owner"] != TOKEN_PROGRAM or mint_quote_acc["owner"] != TOKEN_PROGRAM:
        raise ValueError("Token-2022 or unsupported token program requires a dedicated transfer-fee quote adapter")
    if decoded["token_program_base"] != TOKEN_PROGRAM or decoded["token_program_quote"] != TOKEN_PROGRAM:
        raise ValueError("Pool token program does not match the supported SPL Token program")
    base_decimals = _mint_decimals(mint_base_acc)
    quote_decimals = _mint_decimals(mint_quote_acc)
    base_vault = _token_amount(vault_base_acc, decoded["base_mint"])
    quote_vault = _token_amount(vault_quote_acc, decoded["quote_mint"])
    base_deduct = decoded["protocol_fees_base"] + decoded["fund_fees_base"] + decoded["creator_fees_base"]
    quote_deduct = decoded["protocol_fees_quote"] + decoded["fund_fees_quote"] + decoded["creator_fees_quote"]
    if base_vault <= base_deduct or quote_vault <= quote_deduct:
        raise ValueError("Raydium CPMM vault reserves are empty after accrued fees")
    reserve_base, reserve_quote = base_vault - base_deduct, quote_vault - quote_deduct

    if input_mint == decoded["base_mint"]:
        reserve_in, reserve_out = reserve_base, reserve_quote
        output_mint = decoded["quote_mint"]
        input_is_token0 = True
    elif input_mint == decoded["quote_mint"]:
        reserve_in, reserve_out = reserve_quote, reserve_base
        output_mint = decoded["base_mint"]
        input_is_token0 = False
    else:
        raise ValueError("Input mint does not belong to this Raydium CPMM pool")

    fee_on = decoded["creator_fee_on"]
    if fee_on not in (0, 1, 2):
        raise ValueError("Raydium CPMM creator fee mode is invalid")
    creator_rate = config["creator_fee_rate"] if decoded["enable_creator_fee"] else 0
    creator_on_input = (
        creator_rate > 0 and (
            fee_on == 0 or (fee_on == 1 and input_is_token0)
            or (fee_on == 2 and not input_is_token0)
        )
    )
    trade_fee = (amount_in * config["trade_fee_rate"] + 999_999) // 1_000_000
    creator_fee_input = (
        (amount_in * creator_rate + 999_999) // 1_000_000
        if creator_on_input else 0
    )
    net_input = amount_in - trade_fee - creator_fee_input
    if net_input <= 0:
        raise ValueError("Input is fully consumed by Raydium CPMM fees")
    raw_output = (reserve_out * net_input) // (reserve_in + net_input)
    creator_fee_output = (
        (raw_output * creator_rate + 999_999) // 1_000_000
        if creator_rate and not creator_on_input else 0
    )
    output_amount = raw_output - creator_fee_output
    if output_amount <= 0 or output_amount >= reserve_out:
        raise ValueError("Raydium CPMM quote rounds to zero or drains the pool")
    minimum_out = output_amount * (10_000 - slippage_bps) // 10_000
    impact = 1.0 - ((reserve_out - raw_output) / (reserve_in + net_input)) / (reserve_out / reserve_in)
    if not math.isfinite(impact) or impact < 0:
        raise ValueError("Raydium CPMM price impact is invalid")
    age = time.time() - now
    if age < 0 or age > max_snapshot_age:
        raise ValueError("Raydium CPMM snapshot is stale")

    return {
        "provider": "raydium_cpmm_direct",
        "protocol": "raydium_cpmm",
        "pool_id": pool_address,
        "slot": slot,
        "snapshot_age_seconds": round(age, 3),
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount_in,
        "trade_fee_rate": config["trade_fee_rate"],
        "creator_fee_rate": creator_rate,
        "creator_fee_on_input": creator_on_input,
        "trade_fee_amount": trade_fee,
        "creator_fee_input_amount": creator_fee_input,
        "creator_fee_output_amount": creator_fee_output,
        "out_amount": output_amount,
        "minimum_out_amount": minimum_out,
        "price_impact_percent": round(impact * 100, 6),
        "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT",
        "execution": "DISABLED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
    }


def quote_raydium_amm_v4_pool(pool_address, input_mint, amount_in,
                                    slippage_bps=100, max_snapshot_age=5.0):
    """Read-only exact-input quote from a confirmed Raydium AMM v4 snapshot."""
    if not isinstance(pool_address, str) or not pool_address.strip():
        raise ValueError("Raydium AMM v4 pool address is required")
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Input mint is required")
    if isinstance(amount_in, bool) or not isinstance(amount_in, int) or amount_in <= 0:
        raise ValueError("Input amount must be a positive integer")
    if isinstance(slippage_bps, bool) or not isinstance(slippage_bps, int) or not 0 <= slippage_bps <= 300:
        raise ValueError("Slippage must be an integer between 0 and 300 bps")
    if not isinstance(max_snapshot_age, (int, float)) or not math.isfinite(max_snapshot_age) or max_snapshot_age <= 0:
        raise ValueError("Maximum snapshot age must be positive and finite")

    discovered = _account(pool_address)
    if discovered.get("owner") != PROGRAMS["raydium_amm_v4"]:
        raise ValueError("Pool account is not owned by the Raydium AMM v4 program")
    first = _decode_pool("raydium_amm_v4", discovered["data"])
    keys = [pool_address, first["vault_base"], first["vault_quote"],
            first["base_mint"], first["quote_mint"]]
    accounts, slot = _multiple_accounts_with_context(keys)
    if any(item is None for item in accounts):
        raise ValueError("Raydium AMM v4 snapshot is missing a required account")
    pool_acc, vault_base_acc, vault_quote_acc, mint_base_acc, mint_quote_acc = accounts
    if pool_acc.get("owner") != PROGRAMS["raydium_amm_v4"]:
        raise ValueError("Raydium AMM v4 pool owner changed during snapshot")
    decoded = _decode_pool("raydium_amm_v4", pool_acc["data"])
    for field in ("vault_base", "vault_quote", "base_mint", "quote_mint"):
        if decoded.get(field) != first.get(field):
            raise ValueError("Raydium AMM v4 pool relationships changed during snapshot")
    if decoded["pool_open_time"] > int(time.time()) and decoded["status"] == "active":
        raise ValueError("Raydium AMM v4 pool is not open for swaps yet")
    if mint_base_acc.get("owner") != TOKEN_PROGRAM or mint_quote_acc.get("owner") != TOKEN_PROGRAM:
        raise ValueError("Token-2022 or unsupported token program requires a dedicated transfer-fee quote adapter")

    base_decimals = _mint_decimals(mint_base_acc)
    quote_decimals = _mint_decimals(mint_quote_acc)
    base_vault = _token_amount(vault_base_acc, decoded["base_mint"])
    quote_vault = _token_amount(vault_quote_acc, decoded["quote_mint"])
    reserve_base = base_vault - decoded["need_take_pnl_base"]
    reserve_quote = quote_vault - decoded["need_take_pnl_quote"]
    if reserve_base <= 0 or reserve_quote <= 0:
        raise ValueError("Raydium AMM v4 tradable reserves are empty after accrued PnL")

    if input_mint == decoded["base_mint"]:
        reserve_in, reserve_out = reserve_base, reserve_quote
        output_mint = decoded["quote_mint"]
    elif input_mint == decoded["quote_mint"]:
        reserve_in, reserve_out = reserve_quote, reserve_base
        output_mint = decoded["base_mint"]
    else:
        raise ValueError("Input mint does not belong to this Raydium AMM v4 pool")

    fee = (amount_in * decoded["swap_fee_numerator"] + decoded["swap_fee_denominator"] - 1) // decoded["swap_fee_denominator"]
    net_input = amount_in - fee
    if net_input <= 0:
        raise ValueError("Input is fully consumed by Raydium AMM v4 fees")
    output_amount = (reserve_out * net_input) // (reserve_in + net_input)
    if output_amount <= 0 or output_amount >= reserve_out:
        raise ValueError("Raydium AMM v4 quote rounds to zero or drains the pool")
    minimum_out = output_amount * (10_000 - slippage_bps) // 10_000
    spot_price = reserve_out / reserve_in
    execution_price = output_amount / amount_in
    impact = max(0.0, (1.0 - execution_price / spot_price) * 100.0)
    age = time.time() - time.time()
    if not math.isfinite(impact):
        raise ValueError("Raydium AMM v4 price impact is invalid")

    return {
        "provider": "raydium_amm_v4_direct",
        "protocol": "raydium_amm_v4",
        "pool_id": pool_address,
        "slot": slot,
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount_in,
        "trade_fee_numerator": decoded["swap_fee_numerator"],
        "trade_fee_denominator": decoded["swap_fee_denominator"],
        "trade_fee_amount": fee,
        "out_amount": output_amount,
        "minimum_out_amount": minimum_out,
        "price_impact_percent": round(impact, 6),
        "base_decimals": base_decimals,
        "quote_decimals": quote_decimals,
        "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT",
        "execution": "DISABLED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
    }


def _token_amount(account, expected_mint):
    if not account or len(account["data"]) < 72:
        raise ValueError("invalid SPL token vault account")
    if account.get("owner") not in TOKEN_PROGRAMS:
        raise ValueError("pool vault is not owned by an SPL token program")
    raw = account["data"]
    if _key(raw, 0) != expected_mint:
        raise ValueError("pool vault mint does not match pool state")
    return int.from_bytes(raw[64:72], "little")


def _mint_decimals(account):
    if not account or len(account["data"]) < 45:
        raise ValueError("invalid SPL mint account")
    if account.get("owner") not in TOKEN_PROGRAMS:
        raise ValueError("pool mint is not owned by an SPL token program")
    return account["data"][44]


def _quote_usd(mint):
    if mint in STABLES:
        return 1.0
    # SOL and other quote tokens are valued from the existing public market feed.
    try:
        from market_data import get_market_data
        pair = get_market_data(mint)
        price = float(pair.get("priceUsd")) if pair else 0
        return price if math.isfinite(price) and price > 0 else None
    except Exception:
        return None


def _register_pool_price_watch(pool_address, protocol, token_mint):
    """Register a successfully decoded pool for reserve-account notifications."""
    try:
        from onchain_stream import watch_pool_price
        watch_pool_price(pool_address, protocol, token_mint)
    except Exception as error:
        print("[TROJAN V2] POOL WATCH REGISTRATION FAILED:", str(error)[:180], flush=True)


def get_pool_watch_accounts(pool_address, protocol, token_mint=None):
    """Return pool state and reserve vault accounts for WebSocket subscriptions."""
    if not isinstance(pool_address, str) or not pool_address.strip():
        raise ValueError("pool address is required for account watching")
    account = _account(pool_address.strip())
    owner_protocol = next(
        (name for name, program_id in PROGRAMS.items()
         if account.get("owner") == program_id),
        None
    )
    if owner_protocol is None:
        raise ValueError("pool owner is not a supported Solana DEX program")
    decoded = _decode_pool(owner_protocol, account["data"])
    if decoded.get("status") != "active":
        raise ValueError("pool is not active for account watching")
    accounts = [pool_address.strip()]
    for field in ("vault_base", "vault_quote"):
        address = decoded.get(field)
        if isinstance(address, str) and address and address not in accounts:
            accounts.append(address)
    return accounts


def get_pool_snapshot_accounts(pool_address, protocol, token_mint):
    """Return the complete account set needed for a coherent pool price snapshot."""
    if not isinstance(pool_address, str) or not pool_address.strip():
        raise ValueError("pool address is required for snapshot fetching")
    if not isinstance(token_mint, str) or not token_mint.strip():
        raise ValueError("token mint is required for snapshot fetching")
    account = _account(pool_address.strip())
    owner_protocol = next(
        (name for name, program_id in PROGRAMS.items()
         if account.get("owner") == program_id),
        None
    )
    if owner_protocol is None:
        raise ValueError("pool owner is not a supported Solana DEX program")
    decoded = _decode_pool(owner_protocol, account["data"])
    if decoded.get("status") != "active":
        raise ValueError("pool is not active for snapshot fetching")
    accounts = [pool_address.strip()]

    def add(address):
        if isinstance(address, str) and address and address not in accounts:
            accounts.append(address)

    if owner_protocol in ("pumpfun_legacy", "moonit_amm"):
        add(token_mint.strip())
        return accounts
    if owner_protocol == "meteora_dbc":
        if decoded.get("base_mint") != token_mint:
            raise ValueError("requested mint is not the Meteora DBC base mint")
        config = _account(decoded["config"])
        if (config.get("owner") != PROGRAMS["meteora_dbc"]
                or len(config.get("data", b"")) < 40
                or config["data"][:8] != bytes([26, 108, 14, 123, 116, 230, 129, 43])):
            raise ValueError("invalid Meteora DBC config for snapshot fetching")
        add(decoded["config"])
        add(decoded["base_mint"])
        add(_key(config["data"], 8))
    else:
        add(decoded.get("amm_config"))
        add(decoded.get("base_mint"))
        add(decoded.get("quote_mint"))
    add(decoded.get("vault_base"))
    add(decoded.get("vault_quote"))
    return accounts


def _accounts_for_snapshot(keys, prefetched_accounts=None, prefetched_slot=None):
    """Resolve a complete per-pool account bundle from one confirmed RPC slot."""
    if prefetched_accounts is None:
        return _multiple_accounts_with_context(keys)
    if not isinstance(prefetched_slot, int) or prefetched_slot <= 0:
        raise ValueError("prefetched pool snapshot is missing its confirmed slot")
    if not isinstance(prefetched_accounts, dict):
        raise ValueError("prefetched pool account bundle must be a mapping")
    accounts = [prefetched_accounts.get(key) for key in keys]
    if any(account is None for account in accounts):
        raise ValueError("prefetched pool snapshot is missing a required account")
    return accounts, prefetched_slot


def fetch_pool_account_batch(account_keys):
    """Fetch up to 100 unique pool accounts in one confirmed RPC context."""
    if not isinstance(account_keys, (list, tuple)):
        raise ValueError("account_keys must be a list or tuple")
    keys = list(dict.fromkeys(
        key.strip() for key in account_keys
        if isinstance(key, str) and key.strip()
    ))
    if not keys or len(keys) > 100:
        raise ValueError("pool account batch must contain between 1 and 100 unique keys")
    accounts, slot = _multiple_accounts_with_context(keys)
    return dict(zip(keys, accounts)), slot, time.time()


def decode_pool_snapshot(pool_address, protocol, token_mint, _force=False,
                         _register_watch=True, _prefetched_accounts=None,
                         _prefetched_slot=None, _prefetched_observed_at=None):
    """Return a verified pool snapshot in USD, or None if data is unavailable."""
    protocol = normalize_protocol(protocol)
    if protocol == "raydium_auto":
        protocol = "raydium_auto"
    elif protocol not in PROGRAMS or not pool_address or not token_mint:
        return None
    if not pool_address or not token_mint:
        return None
    key = (pool_address, protocol, token_mint)
    cached = _cache.get(key)
    if not _force and cached and time.time() - cached["time"] < 0.5:
        cached_data = dict(cached["data"])
        if cached_data.get("observedAt") is not None:
            cached_data["snapshotAgeSeconds"] = round(
                max(0.0, time.time() - cached_data["observedAt"]), 3
            )
        if _register_watch:
            _register_pool_price_watch(pool_address, protocol, token_mint)
        return cached_data
    if _prefetched_accounts is not None:
        pool = _prefetched_accounts.get(pool_address)
        if pool is None:
            raise ValueError("prefetched pool snapshot is missing the pool account")
    else:
        pool = _account(pool_address)

    # The account owner is the authoritative protocol identifier.
    # DEX labels from discovery APIs are often broad or stale (for example,
    # "Meteora" can refer to more than one pool family). Never select a binary
    # decoder from a display label when the on-chain owner can identify it.
    owner_protocol = next(
        (name for name, program_id in PROGRAMS.items()
         if pool["owner"] == program_id),
        None
    )
    if owner_protocol is None:
        raise ValueError(
            "pool account owner is not a supported Solana DEX program: "
            + str(pool["owner"])
        )

    if protocol != owner_protocol:
        print(
            "[TROJAN V2] ONCHAIN PROTOCOL LABEL OVERRIDE",
            "| DECLARED:", protocol,
            "| ACCOUNT_OWNER_PROTOCOL:", owner_protocol,
            "| POOL:", pool_address,
            flush=True
        )

    protocol = owner_protocol
    decoded = _decode_pool(protocol, pool["data"])
    if decoded["status"] != "active":
        raise ValueError("pool is not swap-active")

    # Launch curves have no conventional two-vault AMM layout.
    if protocol in ("pumpfun_legacy", "moonit_amm"):
        # Re-read the curve and mint together. The initial account read only
        # discovers the protocol; the published quote is tied to this one
        # confirmed RPC context and never combines independently-timed reads.
        curve_accounts, snapshot_slot = _accounts_for_snapshot(
            [pool_address, token_mint], _prefetched_accounts, _prefetched_slot
        )
        curve_account, mint_account = curve_accounts
        if curve_account is None or mint_account is None:
            raise ValueError("curve snapshot is missing its pool or mint account")
        if curve_account.get("owner") != PROGRAMS[protocol]:
            raise ValueError("curve account owner changed during snapshot")
        decoded = _decode_pool(protocol, curve_account["data"])
        if decoded["status"] != "active":
            raise ValueError("curve is no longer active in the confirmed snapshot")
        snapshot_observed_at = (
            _prefetched_observed_at
            if isinstance(_prefetched_observed_at, (int, float)) and _prefetched_observed_at > 0
            else time.time()
        )
        decimals = _mint_decimals(mint_account)
        if protocol == "pumpfun_legacy":
            virtual_tokens = decoded["virtual_token_reserves"] / (10 ** decimals)
            virtual_quote = decoded["virtual_quote_reserves"] / 1_000_000_000
            real_tokens = decoded["real_token_reserves"] / (10 ** decimals)
            real_quote = decoded["real_quote_reserves"] / 1_000_000_000
            if virtual_tokens <= 0 or virtual_quote <= 0:
                raise ValueError("Pump.fun virtual reserves are empty")
            price_sol = virtual_quote / virtual_tokens
            liquidity_sol = real_quote + real_tokens * price_sol
        else:
            if decoded["base_mint"] != token_mint:
                raise ValueError("requested mint does not match Moonit curve mint")
            if decoded["curve_type"] != 1 or decoded["collateral_currency"] != 0:
                raise ValueError("unsupported Moonit curve type or collateral currency")
            if decimals != decoded["token_decimals"]:
                raise ValueError("Moonit curve decimals disagree with mint account")
            total_supply, curve_amount = decoded["total_supply"], decoded["curve_amount"]
            if curve_amount <= 0 or total_supply <= curve_amount:
                raise ValueError("Moonit curve has no remaining virtual supply")
            initial_tokens, initial_sol = 1_073_000_000_000_000_000, 30_000_000_000
            virtual_tokens = initial_tokens - (total_supply - curve_amount)
            if virtual_tokens <= 0:
                raise ValueError("Moonit virtual token reserve is empty")
            virtual_sol = (initial_tokens * initial_sol) // virtual_tokens
            price_sol = (virtual_sol / 1_000_000_000) / (virtual_tokens / (10 ** decimals))
            liquidity_sol = (
                max(0, virtual_sol - initial_sol) / 1_000_000_000
                + curve_amount / (10 ** decimals) * price_sol
            )
        sol_usd = _quote_usd("So11111111111111111111111111111111111111112")
        if sol_usd is None or not math.isfinite(price_sol) or price_sol <= 0:
            return None
        result = {
            "priceUsd": str(price_sol * sol_usd),
            "liquidity": {"usd": liquidity_sol * sol_usd},
            "baseToken": {"address": token_mint},
            "quoteToken": {"address": "So11111111111111111111111111111111111111112"},
            "marketSource": "solana_rpc_" + protocol,
            "poolAddress": pool_address, "onchainVerified": True,
            "snapshotSlot": snapshot_slot,
            "observedAt": snapshot_observed_at,
            "snapshotAgeSeconds": round(max(0.0, time.time() - snapshot_observed_at), 3)
        }
        _cache[key] = {"time": snapshot_observed_at, "data": result}
        _last_status.update({"configured": True, "last_success": snapshot_observed_at, "last_error": None})
        if _register_watch:
            _register_pool_price_watch(pool_address, protocol, token_mint)
        return result

    if protocol == "meteora_dbc":
        if decoded["base_mint"] != token_mint:
            raise ValueError("requested mint is not the Meteora DBC base mint")
        config_address = decoded["config"]
        config_first = (
            _prefetched_accounts.get(config_address)
            if isinstance(_prefetched_accounts, dict) else None
        )
        if config_first is None:
            config_first = _account(config_address)
        if config_first["owner"] != PROGRAMS["meteora_dbc"] or len(config_first["data"]) < 40:
            raise ValueError("invalid Meteora DBC PoolConfig account")
        if config_first["data"][:8] != bytes([26, 108, 14, 123, 116, 230, 129, 43]):
            raise ValueError("invalid Meteora DBC PoolConfig discriminator")
        quote_mint = _key(config_first["data"], 8)
        # Pool, config and both mints are read in one confirmed context.
        dbc_accounts, snapshot_slot = _accounts_for_snapshot(
            [pool_address, config_address, decoded["base_mint"], quote_mint],
            _prefetched_accounts, _prefetched_slot
        )
        pool_acc, config_acc, base_mint_acc, quote_mint_acc = dbc_accounts
        if any(item is None for item in dbc_accounts):
            raise ValueError("Meteora DBC snapshot is missing a required account")
        if pool_acc["owner"] != PROGRAMS["meteora_dbc"]:
            raise ValueError("Meteora DBC pool owner changed during snapshot")
        if config_acc["owner"] != PROGRAMS["meteora_dbc"]:
            raise ValueError("Meteora DBC config owner changed during snapshot")
        decoded = _decode_pool("meteora_dbc", pool_acc["data"])
        if decoded["status"] != "active":
            raise ValueError("Meteora DBC pool is no longer active")
        if decoded["config"] != config_address:
            raise ValueError("Meteora DBC config relationship changed during snapshot")
        if config_acc["data"][:8] != bytes([26, 108, 14, 123, 116, 230, 129, 43]):
            raise ValueError("Meteora DBC config changed to an unsupported layout")
        if _key(config_acc["data"], 8) != quote_mint or decoded["base_mint"] != token_mint:
            raise ValueError("Meteora DBC pool/config mint relationship changed")
        snapshot_observed_at = (
            _prefetched_observed_at
            if isinstance(_prefetched_observed_at, (int, float)) and _prefetched_observed_at > 0
            else time.time()
        )
        base_decimals = _mint_decimals(base_mint_acc)
        quote_decimals = _mint_decimals(quote_mint_acc)
        quote_usd = _quote_usd(quote_mint)
        if quote_usd is None:
            return None
        ratio = (decoded["sqrt_price_x64"] / (2 ** 64)) ** 2
        ratio *= 10 ** (base_decimals - quote_decimals)
        token_price = ratio * quote_usd
        base_reserve = decoded["base_reserve"] / (10 ** base_decimals)
        quote_reserve = decoded["quote_reserve"] / (10 ** quote_decimals)
        liquidity_usd = base_reserve * token_price + quote_reserve * quote_usd
        if not math.isfinite(token_price) or token_price <= 0:
            raise ValueError("Meteora DBC price is invalid")
        result = {
            "priceUsd": str(token_price), "liquidity": {"usd": liquidity_usd},
            "baseToken": {"address": token_mint}, "quoteToken": {"address": quote_mint},
            "marketSource": "solana_rpc_meteora_dbc",
            "poolAddress": pool_address, "onchainVerified": True,
            "snapshotSlot": snapshot_slot,
            "observedAt": snapshot_observed_at,
            "snapshotAgeSeconds": round(max(0.0, time.time() - snapshot_observed_at), 3)
        }
        _cache[key] = {"time": snapshot_observed_at, "data": result}
        _last_status.update({"configured": True, "last_success": snapshot_observed_at, "last_error": None})
        if _register_watch:
            _register_pool_price_watch(pool_address, protocol, token_mint)
        return result

    if token_mint not in (decoded["base_mint"], decoded["quote_mint"]):
        raise ValueError("requested mint is not part of decoded pool")
    # Re-fetch the pool state, vaults and mints in a single RPC context. The
    # initial getAccountInfo is only for protocol/address discovery; it is
    # never mixed with the later reserve snapshot used to publish a price.
    snapshot_keys = [
        pool_address, decoded["vault_base"], decoded["vault_quote"],
        decoded["base_mint"], decoded["quote_mint"]
    ]
    snapshot_accounts, snapshot_slot = _accounts_for_snapshot(
        snapshot_keys, _prefetched_accounts, _prefetched_slot
    )
    pool_acc, vault_base_acc, vault_quote_acc, mint_base_acc, mint_quote_acc = snapshot_accounts
    if any(item is None for item in snapshot_accounts):
        raise ValueError("pool snapshot is missing its pool, vault or mint account")
    if pool_acc["owner"] != PROGRAMS[protocol]:
        raise ValueError("pool account owner changed during confirmed snapshot")
    snapshot_decoded = _decode_pool(protocol, pool_acc["data"])
    for field in ("base_mint", "quote_mint", "vault_base", "vault_quote"):
        if snapshot_decoded.get(field) != decoded.get(field):
            raise ValueError("pool account relationships changed during confirmed snapshot")
    if snapshot_decoded["status"] != "active":
        raise ValueError("pool swaps are disabled in the confirmed snapshot")
    decoded = snapshot_decoded
    snapshot_observed_at = (
        _prefetched_observed_at
        if isinstance(_prefetched_observed_at, (int, float)) and _prefetched_observed_at > 0
        else time.time()
    )
    vaults = [vault_base_acc, vault_quote_acc, mint_base_acc, mint_quote_acc]
    base_amount = _token_amount(vaults[0], decoded["base_mint"]) / (10 ** _mint_decimals(vaults[2]))
    quote_amount = _token_amount(vaults[1], decoded["quote_mint"]) / (10 ** _mint_decimals(vaults[3]))
    if base_amount <= 0 or quote_amount <= 0:
        raise ValueError("pool vault reserves are empty")
    if decoded["price_mode"] == "sqrt_price":
        ratio = (decoded["sqrt_price_x64"] / (2 ** 64)) ** 2
        ratio *= 10 ** (_mint_decimals(vaults[2]) - _mint_decimals(vaults[3]))
    elif decoded["price_mode"] == "active_bin":
        ratio = (1 + decoded["bin_step"] / 10000) ** decoded["active_id"]
        ratio *= 10 ** (_mint_decimals(vaults[2]) - _mint_decimals(vaults[3]))
    else:
        ratio = quote_amount / base_amount
    other_mint = decoded["quote_mint"] if token_mint == decoded["base_mint"] else decoded["base_mint"]
    other_usd = _quote_usd(other_mint)
    if other_usd is None:
        return None
    token_is_base = token_mint == decoded["base_mint"]
    token_price = ratio * other_usd if token_is_base else other_usd / ratio
    if not math.isfinite(token_price) or token_price <= 0:
        raise ValueError("decoded on-chain price is invalid")
    base_price_usd = token_price if token_is_base else other_usd
    quote_price_usd = other_usd if token_is_base else token_price
    result = {
        "priceUsd": str(token_price),
        "liquidity": {"usd": (base_amount * base_price_usd + quote_amount * quote_price_usd)},
        # Preserve the actual pool orientation; priceUsd still refers to token_mint.
        "baseToken": {"address": decoded["base_mint"]},
        "quoteToken": {"address": decoded["quote_mint"]},
        "marketSource": "solana_rpc_" + protocol,
        "poolAddress": pool_address,
        "onchainVerified": True,
        "snapshotSlot": snapshot_slot,
        "observedAt": snapshot_observed_at,
        "snapshotAgeSeconds": round(max(0.0, time.time() - snapshot_observed_at), 3)
    }
    _cache[key] = {"time": snapshot_observed_at, "data": result}
    _last_status.update({"configured": True, "last_success": snapshot_observed_at, "last_error": None})
    if _register_watch:
        _register_pool_price_watch(pool_address, protocol, token_mint)
    return result


def record_onchain_error(error):
    _last_status["last_error"] = str(error)[:250]


_rpc_probe = {"checked_at": 0.0, "healthy": False, "error": None}


def check_rpc_health(force=False):
    now = time.time()
    if not RPC_URL:
        return False, "SOLANA_RPC_URL / SOLANA_RPC_URLS missing"
    if not force and now - _rpc_probe["checked_at"] < 30:
        return _rpc_probe["healthy"], _rpc_probe["error"]
    try:
        result = _rpc("getHealth", [])
        healthy = result == "ok"
        _rpc_probe.update({"checked_at": now, "healthy": healthy,
                           "error": None if healthy else "RPC health returned " + str(result)[:100]})
    except Exception as error:
        _rpc_probe.update({"checked_at": now, "healthy": False, "error": str(error)[:180]})
    return _rpc_probe["healthy"], _rpc_probe["error"]


def get_onchain_status():
    healthy, error = check_rpc_health()
    try:
        from onchain_stream import get_stream_status
        stream = get_stream_status()
    except Exception as stream_error:
        stream = {"running": False, "last_error": str(stream_error)[:120]}
    try:
        import rpc_transport
        rpc = rpc_transport.get_rpc_status()
    except Exception as rpc_error:
        rpc = {"configured": bool(RPC_URL), "endpoint_count": 1 if RPC_URL else 0,
               "endpoints": [], "status_error": str(rpc_error)[:120]}
    return {
        **_last_status,
        "supported_protocols": sorted(list(PROGRAMS.keys()) + ["raydium_auto"]),
        "rpc_configured": bool(RPC_URL),
        "rpc_healthy": healthy,
        "rpc_error": error,
        "rpc_failover": rpc,
        "price_stream": stream
    }


def _record_error(error):
    _last_status["last_error"] = str(error)[:250]
