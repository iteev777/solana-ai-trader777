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
SOL_MINT = "So11111111111111111111111111111111111111112"

PUMP_GLOBAL_CONFIG = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"
PUMP_FEE_CONFIG = "8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt"
PUMP_GLOBAL_CONFIG_DISCRIMINATOR = bytes([167, 232, 232, 177, 200, 108, 114, 127])
DEFAULT_PUBKEY = "11111111111111111111111111111111"
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

PUMPSWAP_GLOBAL_CONFIG = "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw"
PUMPSWAP_FEE_CONFIG = "5PHirr8joyTMp9JMm6nW7hNDVyEYdkzDqazxPD7RaTjx"
PUMP_FEES_PROGRAM = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"
PUMPSWAP_GLOBAL_CONFIG_DISCRIMINATOR = bytes([149, 8, 156, 202, 160, 252, 176, 217])
PUMPSWAP_FEE_CONFIG_DISCRIMINATOR = bytes([143, 52, 146, 187, 219, 123, 76, 155])

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


def _ed25519_is_on_curve(compressed):
    """Return whether a 32-byte compressed Ed25519 point decompresses."""
    if not isinstance(compressed, (bytes, bytearray)) or len(compressed) != 32:
        return False
    prime = 2 ** 255 - 19
    y = int.from_bytes(compressed, "little") & ((1 << 255) - 1)
    if y >= prime:
        return False
    d = (-121665 * pow(121666, prime - 2, prime)) % prime
    y2 = (y * y) % prime
    denominator = (d * y2 + 1) % prime
    if denominator == 0:
        return False
    x2 = ((y2 - 1) * pow(denominator, prime - 2, prime)) % prime
    x = pow(x2, (prime + 3) // 8, prime)
    if (x * x - x2) % prime:
        sqrt_minus_one = pow(2, (prime - 1) // 4, prime)
        x = (x * sqrt_minus_one) % prime
    if (x * x - x2) % prime:
        return False
    if x == 0 and (compressed[31] >> 7):
        return False
    return True


def _find_program_address(seeds, program_id):
    """Derive a Solana PDA with the canonical descending bump search."""
    if not isinstance(seeds, (list, tuple)) or len(seeds) >= 16:
        raise ValueError("PDA seed list is invalid")
    if any(not isinstance(seed, bytes) or len(seed) > 32 for seed in seeds):
        raise ValueError("PDA seeds must be byte strings of at most 32 bytes")
    program = _b58decode(program_id)
    if len(program) != 32:
        raise ValueError("PDA program id must decode to 32 bytes")
    for bump in range(255, -1, -1):
        digest = hashlib.sha256(
            b"".join(seeds) + bytes([bump]) + program + b"ProgramDerivedAddress"
        ).digest()
        if not _ed25519_is_on_curve(digest):
            return _b58encode(digest), bump
    raise ValueError("Unable to derive an off-curve Solana PDA")


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
        # The quote-critical extension reaches byte 270. Older/truncated
        # layouts cannot safely imply zero virtual reserves or disabled flags.
        if len(raw) < 271 or raw[:8] != bytes([241,154,109,4,17,177,109,188]):
            raise ValueError("invalid or truncated PumpSwap Pool discriminator/layout")
        return {
            "creator": _key(raw, 11),
            "base_mint": _key(raw, 43), "quote_mint": _key(raw, 75),
            "vault_base": _key(raw, 139), "vault_quote": _key(raw, 171),
            "coin_creator": _key(raw, 211),
            "is_mayhem_mode": bool(raw[243]),
            "is_cashback_coin": bool(raw[244]),
            "virtual_quote_reserves": int.from_bytes(raw[245:261], "little", signed=True),
            "creator_fee_bps": _u64(raw, 261),
            "is_holder_reward": bool(raw[270]),
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
        trade_fee_numerator, trade_fee_denominator = _u64(raw, 227), _u64(raw, 235)
        owner_fee_numerator, owner_fee_denominator = _u64(raw, 243), _u64(raw, 251)
        if (trade_fee_denominator <= 0 or owner_fee_denominator <= 0
                or trade_fee_numerator >= trade_fee_denominator
                or owner_fee_numerator >= owner_fee_denominator):
            raise ValueError("FluxBeam fee configuration is invalid")
        curve_type = raw[291]
        return {
            "base_mint": _key(raw, 131), "quote_mint": _key(raw, 163),
            "vault_base": _key(raw, 35), "vault_quote": _key(raw, 67),
            "pool_mint": _key(raw, 99), "fee_account": _key(raw, 195),
            "is_initialized": bool(raw[1]),
            "trade_fee_numerator": trade_fee_numerator,
            "trade_fee_denominator": trade_fee_denominator,
            "owner_fee_numerator": owner_fee_numerator,
            "owner_fee_denominator": owner_fee_denominator,
            "curve_type": curve_type,
            "status": "active", "price_mode": "reserves"
        }
    if protocol == "pumpfun_legacy":
        if len(raw) < 125 or raw[:8] != bytes([23, 183, 248, 55, 96, 216, 172, 96]):
            raise ValueError("invalid or truncated Pump.fun bonding curve discriminator/layout")
        if raw[48] != 0:
            raise ValueError("Pump.fun bonding curve is complete/migrated")
        return {
            "virtual_token_reserves": _u64(raw, 8),
            "virtual_quote_reserves": _u64(raw, 16),
            "real_token_reserves": _u64(raw, 24),
            "real_quote_reserves": _u64(raw, 32),
            "token_total_supply": _u64(raw, 40),
            "complete": bool(raw[48]),
            "creator": _key(raw, 49),
            "is_mayhem_mode": bool(raw[81]),
            "is_cashback_coin": bool(raw[82]),
            "quote_mint": _key(raw, 83),
            "creator_fee_bps": _u64(raw, 115),
            "is_holder_reward": bool(raw[124]),
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



def _decode_pumpswap_fee_config(raw):
    """Decode the public Pump Fees FeeConfig Borsh layout."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 69:
        raise ValueError("PumpSwap fee config is truncated")
    if raw[:8] != PUMPSWAP_FEE_CONFIG_DISCRIMINATOR:
        raise ValueError("PumpSwap fee config discriminator is invalid")

    def read_fees(offset):
        if offset < 0 or offset + 24 > len(raw):
            raise ValueError("PumpSwap fee schedule entry is truncated")
        values = tuple(_u64(raw, offset + i * 8) for i in range(3))
        if any(value > 10_000 for value in values):
            raise ValueError("PumpSwap fee schedule contains an invalid basis-point rate")
        return {"lp_fee_bps": values[0], "protocol_fee_bps": values[1], "creator_fee_bps": values[2]}

    cursor = 41
    flat_fees = read_fees(cursor)
    cursor += 24

    def read_tiers(position, label):
        if position + 4 > len(raw):
            raise ValueError("PumpSwap " + label + " fee tier count is missing")
        count = int.from_bytes(raw[position:position + 4], "little")
        position += 4
        if count > 128:
            raise ValueError("PumpSwap " + label + " fee tier count is unreasonable")
        tiers = []
        for _ in range(count):
            if position + 40 > len(raw):
                raise ValueError("PumpSwap " + label + " fee tier is truncated")
            threshold = int.from_bytes(raw[position:position + 16], "little")
            tiers.append({"threshold": threshold, **read_fees(position + 16)})
            position += 40
        return tiers, position

    fee_tiers, cursor = read_tiers(cursor, "market-cap")
    stable_tiers, cursor = read_tiers(cursor, "stable-market-cap")
    exotic_flat_fees = read_fees(cursor)
    candidates = [flat_fees, exotic_flat_fees]
    candidates.extend({k: v for k, v in tier.items() if k != "threshold"} for tier in fee_tiers)
    candidates.extend({k: v for k, v in tier.items() if k != "threshold"} for tier in stable_tiers)
    selected = max(candidates, key=lambda item: sum(item.values()))
    total_bps = sum(selected.values())
    if total_bps >= 10_000:
        raise ValueError("PumpSwap configured fee total consumes the entire trade")
    return {
        "flat_fees": flat_fees, "fee_tiers": fee_tiers,
        "stable_fee_tiers": stable_tiers, "exotic_flat_fees": exotic_flat_fees,
        "conservative_fees": selected, "conservative_total_bps": total_bps,
    }


def _fee_amount_ceil(amount, bps):
    return (amount * bps + 9_999) // 10_000 if amount > 0 and bps > 0 else 0


def _pumpswap_spendable_quote(amount_in, fee_rates):
    """Largest quote reserve input whose separately rounded fees fit the spend."""
    low, high = 0, amount_in
    while low < high:
        candidate = (low + high + 1) // 2
        if candidate + sum(_fee_amount_ceil(candidate, rate) for rate in fee_rates) <= amount_in:
            low = candidate
        else:
            high = candidate - 1
    fees = [_fee_amount_ceil(low, rate) for rate in fee_rates]
    return low, fees


def quote_pumpswap_pool(pool_address, input_mint, amount_in,
                        slippage_bps=100, max_snapshot_age=5.0):
    """Read-only PumpSwap quote using one confirmed pool/vault/mint/fee snapshot.

    Fee selection is conservative: use the highest configured total across the
    on-chain flat, standard-tier, stable-tier and exotic fee schedules. This
    avoids assuming a cheaper market-cap tier when pool classification is not
    proven and cannot overstate expected output.
    """
    if not isinstance(pool_address, str) or not pool_address.strip():
        raise ValueError("PumpSwap pool address is required")
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Input mint is required")
    if isinstance(amount_in, bool) or not isinstance(amount_in, int) or not 0 < amount_in <= 2**64 - 1:
        raise ValueError("Input amount must be a positive u64 integer")
    if isinstance(slippage_bps, bool) or not isinstance(slippage_bps, int) or not 0 <= slippage_bps <= 300:
        raise ValueError("Slippage must be an integer between 0 and 300 bps")
    if isinstance(max_snapshot_age, bool) or not isinstance(max_snapshot_age, (int, float)) or not math.isfinite(max_snapshot_age) or max_snapshot_age <= 0:
        raise ValueError("Maximum snapshot age must be positive and finite")

    discovered = _account(pool_address)
    if discovered.get("owner") != PROGRAMS["pumpswap"]:
        raise ValueError("Pool account is not owned by the PumpSwap AMM program")
    first = _decode_pool("pumpswap", discovered["data"])
    if first.get("is_mayhem_mode"):
        raise ValueError("PumpSwap mayhem-mode pools are not supported")
    if first.get("is_cashback_coin"):
        raise ValueError("PumpSwap cashback pools are not supported")
    if first.get("is_holder_reward"):
        raise ValueError("PumpSwap holder-reward pools require a separate fee model")

    keys = [
        pool_address, PUMPSWAP_GLOBAL_CONFIG, PUMPSWAP_FEE_CONFIG,
        first["vault_base"], first["vault_quote"], first["base_mint"], first["quote_mint"],
    ]
    snapshot_started = time.time()
    accounts, slot = _multiple_accounts_with_context(keys)
    if any(item is None for item in accounts):
        raise ValueError("PumpSwap confirmed snapshot is missing a required account")
    pool_acc, global_acc, fee_acc, vault_base_acc, vault_quote_acc, mint_base_acc, mint_quote_acc = accounts

    if pool_acc.get("owner") != PROGRAMS["pumpswap"]:
        raise ValueError("PumpSwap pool owner changed during snapshot")
    decoded = _decode_pool("pumpswap", pool_acc["data"])
    for field in (
        "creator", "base_mint", "quote_mint", "vault_base", "vault_quote",
        "coin_creator", "is_mayhem_mode", "is_cashback_coin", "is_holder_reward",
        "virtual_quote_reserves", "creator_fee_bps",
    ):
        if decoded.get(field) != first.get(field):
            raise ValueError("PumpSwap pool relationships or quote-reserve state changed during snapshot")
    if decoded.get("is_mayhem_mode") or decoded.get("is_cashback_coin") or decoded.get("is_holder_reward"):
        raise ValueError("PumpSwap pool has an unsupported mayhem, cashback or reward mode")

    if global_acc.get("owner") != PROGRAMS["pumpswap"]:
        raise ValueError("PumpSwap global config is not owned by the AMM program")
    global_raw = global_acc.get("data", b"")
    if len(global_raw) < 321 or global_raw[:8] != PUMPSWAP_GLOBAL_CONFIG_DISCRIMINATOR:
        raise ValueError("PumpSwap global config discriminator or fee layout is invalid")
    disable_flags = global_raw[56]
    global_flat_fees = {
        "lp_fee_bps": _u64(global_raw, 40),
        "protocol_fee_bps": _u64(global_raw, 48),
        "creator_fee_bps": _u64(global_raw, 313),
    }
    if any(value > 10_000 for value in global_flat_fees.values()):
        raise ValueError("PumpSwap GlobalConfig fee rate is invalid")
    is_buy = input_mint == decoded["quote_mint"]
    if is_buy and disable_flags & (1 << 3):
        raise ValueError("PumpSwap buy instructions are disabled by GlobalConfig")
    if input_mint == decoded["base_mint"] and disable_flags & (1 << 4):
        raise ValueError("PumpSwap sell instructions are disabled by GlobalConfig")

    if fee_acc.get("owner") != PUMP_FEES_PROGRAM:
        raise ValueError("PumpSwap fee config is not owned by the Pump Fees program")
    fee_config = _decode_pumpswap_fee_config(fee_acc.get("data", b""))
    if mint_base_acc.get("owner") != TOKEN_PROGRAM or mint_quote_acc.get("owner") != TOKEN_PROGRAM:
        raise ValueError("PumpSwap Token-2022 mints are refused until transfer-fee and hook behavior is verified")
    if vault_base_acc.get("owner") != TOKEN_PROGRAM or vault_quote_acc.get("owner") != TOKEN_PROGRAM:
        raise ValueError("PumpSwap vaults must use the legacy SPL Token program")

    base_decimals = _mint_decimals(mint_base_acc)
    quote_decimals = _mint_decimals(mint_quote_acc)
    reserve_base = _token_amount(vault_base_acc, decoded["base_mint"])
    raw_quote_reserve = _token_amount(vault_quote_acc, decoded["quote_mint"])
    reserve_quote = raw_quote_reserve + decoded["virtual_quote_reserves"]
    if reserve_base <= 0 or reserve_quote <= 0:
        raise ValueError("PumpSwap effective pool reserves are empty or invalid")
    if input_mint not in (decoded["base_mint"], decoded["quote_mint"]):
        raise ValueError("Input mint does not belong to this PumpSwap pool")

    creator_pool = decoded["coin_creator"] != "11111111111111111111111111111111"
    fee_candidates = [fee_config["conservative_fees"], global_flat_fees]
    if not creator_pool:
        fee_candidates = [
            {**candidate, "creator_fee_bps": 0}
            for candidate in fee_candidates
        ]
    fees = max(fee_candidates, key=lambda item: sum(item.values()))
    fee_rates = [fees["lp_fee_bps"], fees["protocol_fee_bps"]]
    if creator_pool:
        fee_rates.append(fees["creator_fee_bps"])
    total_fee_bps = sum(fee_rates)
    if total_fee_bps >= 10_000:
        raise ValueError("PumpSwap fee configuration is not tradable")

    if is_buy:
        net_input, _ = _pumpswap_spendable_quote(amount_in, fee_rates)
        # The deployed buy path uses one raw unit less than the mathematical
        # maximum affordable amount. Recompute each separately rounded fee
        # after that adjustment so the quote never overstates token output.
        net_input = max(0, net_input - 1)
        fee_amounts = [_fee_amount_ceil(net_input, rate) for rate in fee_rates]
        total_fee = sum(fee_amounts)
        if net_input <= 0:
            raise ValueError("PumpSwap quote input is consumed by fees")
        output_amount = (reserve_base * net_input) // (reserve_quote + net_input)
        impact_basis_input = net_input
        spot_price = reserve_base / reserve_quote
        output_mint = decoded["base_mint"]
        unspent_input = amount_in - net_input - total_fee
    else:
        gross_quote_out = min(
            (reserve_quote * amount_in) // (reserve_base + amount_in),
            raw_quote_reserve,
        )
        fee_amounts = [_fee_amount_ceil(gross_quote_out, rate) for rate in fee_rates]
        total_fee = sum(fee_amounts)
        output_amount = gross_quote_out - total_fee
        impact_basis_input = amount_in
        impact_output_amount = gross_quote_out
        spot_price = reserve_quote / reserve_base
        output_mint = decoded["quote_mint"]
        unspent_input = 0

    if is_buy:
        impact_output_amount = output_amount

    output_reserve = reserve_base if is_buy else raw_quote_reserve
    gross_output_amount = output_amount if is_buy else gross_quote_out
    if output_amount <= 0 or output_amount >= output_reserve:
        raise ValueError("PumpSwap quote rounds to zero or exceeds available pool reserves")
    minimum_out = output_amount * (10_000 - slippage_bps) // 10_000
    execution_price = impact_output_amount / impact_basis_input
    impact = max(0.0, (1.0 - execution_price / spot_price) * 100.0)
    age = time.time() - snapshot_started
    if age < 0 or age > max_snapshot_age:
        raise ValueError("PumpSwap account snapshot is stale")
    if not math.isfinite(impact):
        raise ValueError("PumpSwap price impact is invalid")

    return {
        "provider": "pumpswap_direct", "protocol": "pumpswap", "pool_id": pool_address,
        "slot": slot, "snapshot_age_seconds": round(age, 3),
        "input_mint": input_mint, "output_mint": output_mint, "input_amount": amount_in,
        "output_amount_gross": gross_output_amount, "out_amount": output_amount,
        "minimum_out_amount": minimum_out, "unspent_input_amount": unspent_input,
        "fee_policy": "CONSERVATIVE_MAX_CONFIGURED_ONCHAIN_SCHEDULE",
        "fee_mint": decoded["quote_mint"],
        "fee_bps": fees, "fee_rates_bps": fee_rates, "total_fee_bps": total_fee_bps,
        "lp_fee_amount": fee_amounts[0], "protocol_fee_amount": fee_amounts[1],
        "creator_fee_amount": fee_amounts[2] if len(fee_amounts) > 2 else 0, "total_fee_amount": total_fee,
        "effective_quote_reserves": reserve_quote, "raw_quote_vault_reserves": raw_quote_reserve,
        "virtual_quote_reserves": decoded["virtual_quote_reserves"],
        "price_impact_percent": round(impact, 6),
        "base_decimals": base_decimals, "quote_decimals": quote_decimals,
        "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT",
        "execution": "DISABLED", "signing": "DISABLED", "broadcast": "DISABLED",
    }


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
    import token2022_inspector as token2022
    token_program_base = mint_base_acc.get("owner")
    token_program_quote = mint_quote_acc.get("owner")
    supported_programs = (TOKEN_PROGRAM, TOKEN_2022_PROGRAM)
    if token_program_base not in supported_programs or token_program_quote not in supported_programs:
        raise ValueError("Raydium CPMM mint uses an unsupported token program")
    if (decoded["token_program_base"] != token_program_base
            or decoded["token_program_quote"] != token_program_quote):
        raise ValueError("Pool token program does not match its mint account owner")
    if token_program_base == TOKEN_2022_PROGRAM:
        token2022.validate_zero_fee_mint_data(mint_base_acc["data"])
    if token_program_quote == TOKEN_2022_PROGRAM:
        token2022.validate_zero_fee_mint_data(mint_quote_acc["data"])
    base_decimals = _mint_decimals(mint_base_acc)
    quote_decimals = _mint_decimals(mint_quote_acc)
    pool_authority, _ = _find_program_address(
        [b"vault_and_lp_mint_auth_seed"], PROGRAMS["raydium_cpmm"]
    )
    base_vault = token2022.validate_token_account_for_program(
        vault_base_acc, token_program_base, decoded["base_mint"], pool_authority,
        "Raydium CPMM base vault",
    )
    quote_vault = token2022.validate_token_account_for_program(
        vault_quote_acc, token_program_quote, decoded["quote_mint"], pool_authority,
        "Raydium CPMM quote vault",
    )
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
        "slippage_bps": slippage_bps,
        "token_program_base": token_program_base,
        "token_program_quote": token_program_quote,
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
    if isinstance(max_snapshot_age, bool) or not isinstance(max_snapshot_age, (int, float)) or not math.isfinite(max_snapshot_age) or max_snapshot_age <= 0:
        raise ValueError("Maximum snapshot age must be positive and finite")

    discovered = _account(pool_address)
    if discovered.get("owner") != PROGRAMS["raydium_amm_v4"]:
        raise ValueError("Pool account is not owned by the Raydium AMM v4 program")
    first = _decode_pool("raydium_amm_v4", discovered["data"])
    keys = [pool_address, first["vault_base"], first["vault_quote"],
            first["base_mint"], first["quote_mint"]]
    snapshot_started = time.time()
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
    execution_price = output_amount / net_input
    impact = max(0.0, (1.0 - execution_price / spot_price) * 100.0)
    age = time.time() - snapshot_started
    if age < 0 or age > max_snapshot_age:
        raise ValueError("Raydium AMM v4 snapshot is stale")
    if not math.isfinite(impact):
        raise ValueError("Raydium AMM v4 price impact is invalid")

    return {
        "provider": "raydium_amm_v4_direct",
        "protocol": "raydium_amm_v4",
        "pool_id": pool_address,
        "slot": slot,
        "snapshot_age_seconds": round(age, 3),
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount_in,
        "slippage_bps": slippage_bps,
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


def quote_fluxbeam_pool(pool_address, input_mint, amount_in,
                        slippage_bps=100, max_snapshot_age=5.0):
    """Read-only FluxBeam constant-product quote from a confirmed account snapshot."""
    if not isinstance(pool_address, str) or not pool_address.strip():
        raise ValueError("FluxBeam pool address is required")
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Input mint is required")
    if isinstance(amount_in, bool) or not isinstance(amount_in, int) or not 0 < amount_in <= 2**64 - 1:
        raise ValueError("Input amount must be a positive u64 integer")
    if isinstance(slippage_bps, bool) or not isinstance(slippage_bps, int) or not 0 <= slippage_bps <= 300:
        raise ValueError("Slippage must be an integer between 0 and 300 bps")
    if isinstance(max_snapshot_age, bool) or not isinstance(max_snapshot_age, (int, float)) or not math.isfinite(max_snapshot_age) or max_snapshot_age <= 0:
        raise ValueError("Maximum snapshot age must be positive and finite")

    discovered = _account(pool_address)
    if discovered.get("owner") != PROGRAMS["fluxbeam_amm"]:
        raise ValueError("Pool account is not owned by the FluxBeam AMM program")
    first = _decode_pool("fluxbeam_amm", discovered["data"])
    if first["curve_type"] != 0:
        raise ValueError("FluxBeam direct quotes support only verified ConstantProduct pools")
    keys = [pool_address, first["vault_base"], first["vault_quote"],
            first["base_mint"], first["quote_mint"]]
    snapshot_started = time.time()
    accounts, slot = _multiple_accounts_with_context(keys)
    if any(item is None for item in accounts):
        raise ValueError("FluxBeam snapshot is missing a required account")
    pool_acc, vault_base_acc, vault_quote_acc, mint_base_acc, mint_quote_acc = accounts
    if pool_acc.get("owner") != PROGRAMS["fluxbeam_amm"]:
        raise ValueError("FluxBeam pool owner changed during snapshot")
    decoded = _decode_pool("fluxbeam_amm", pool_acc["data"])
    for field in ("vault_base", "vault_quote", "base_mint", "quote_mint",
                  "trade_fee_numerator", "trade_fee_denominator",
                  "owner_fee_numerator", "owner_fee_denominator", "curve_type"):
        if decoded.get(field) != first.get(field):
            raise ValueError("FluxBeam pool relationships or fee configuration changed during snapshot")
    if decoded["curve_type"] != 0:
        raise ValueError("FluxBeam pool curve is not ConstantProduct")
    if mint_base_acc.get("owner") != TOKEN_PROGRAM or mint_quote_acc.get("owner") != TOKEN_PROGRAM:
        raise ValueError("FluxBeam Token-2022 mints are refused until transfer-fee behavior is verified")
    if vault_base_acc.get("owner") != TOKEN_PROGRAM or vault_quote_acc.get("owner") != TOKEN_PROGRAM:
        raise ValueError("FluxBeam vaults must use the legacy SPL Token program")

    base_decimals = _mint_decimals(mint_base_acc)
    quote_decimals = _mint_decimals(mint_quote_acc)
    reserve_base = _token_amount(vault_base_acc, decoded["base_mint"])
    reserve_quote = _token_amount(vault_quote_acc, decoded["quote_mint"])
    if reserve_base <= 0 or reserve_quote <= 0:
        raise ValueError("FluxBeam reserves are empty")

    if input_mint == decoded["base_mint"]:
        reserve_in, reserve_out = reserve_base, reserve_quote
        output_mint = decoded["quote_mint"]
    elif input_mint == decoded["quote_mint"]:
        reserve_in, reserve_out = reserve_quote, reserve_base
        output_mint = decoded["base_mint"]
    else:
        raise ValueError("Input mint does not belong to this FluxBeam pool")

    trade_fee = (amount_in * decoded["trade_fee_numerator"]
                 + decoded["trade_fee_denominator"] - 1) // decoded["trade_fee_denominator"]
    owner_fee = (amount_in * decoded["owner_fee_numerator"]
                 + decoded["owner_fee_denominator"] - 1) // decoded["owner_fee_denominator"]
    total_fee = trade_fee + owner_fee
    net_input = amount_in - total_fee
    if net_input <= 0:
        raise ValueError("Input is fully consumed by FluxBeam trading fees")
    new_source = reserve_in + net_input
    new_destination = (reserve_out * reserve_in + new_source - 1) // new_source
    if new_destination > reserve_out:
        raise ValueError("FluxBeam quote arithmetic exceeded the output reserve")
    output_amount = reserve_out - new_destination
    if output_amount <= 0 or output_amount >= reserve_out:
        raise ValueError("FluxBeam quote rounds to zero or drains the pool")
    minimum_out = output_amount * (10_000 - slippage_bps) // 10_000
    spot_price = reserve_out / reserve_in
    execution_price = output_amount / net_input
    impact = max(0.0, (1.0 - execution_price / spot_price) * 100.0)
    age = time.time() - snapshot_started
    if age < 0 or age > max_snapshot_age:
        raise ValueError("FluxBeam snapshot is stale")
    if not math.isfinite(impact):
        raise ValueError("FluxBeam price impact is invalid")

    return {
        "provider": "fluxbeam_direct",
        "protocol": "fluxbeam_amm",
        "pool_id": pool_address,
        "slot": slot,
        "snapshot_age_seconds": round(age, 3),
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount_in,
        "slippage_bps": slippage_bps,
        "trade_fee_numerator": decoded["trade_fee_numerator"],
        "trade_fee_denominator": decoded["trade_fee_denominator"],
        "owner_fee_numerator": decoded["owner_fee_numerator"],
        "owner_fee_denominator": decoded["owner_fee_denominator"],
        "trade_fee_amount": trade_fee,
        "owner_fee_amount": owner_fee,
        "total_fee_amount": total_fee,
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


def quote_moonit_pool(pool_address, input_mint, amount_in,
                     slippage_bps=100, max_snapshot_age=5.0):
    """Read-only Moonit ConstantProductV1 quote from confirmed curve/config state."""
    initial_token_reserves = 1_073_000_000_000_000_000
    initial_collateral_reserves = 30_000_000_000
    constant_product = initial_token_reserves * initial_collateral_reserves
    config_address = "36Eru7v11oU5Pfrojyn5oY3nETA1a1iqsw2WUu6afkM9"
    config_discriminator = bytes([189, 255, 97, 70, 186, 189, 24, 102])

    if not isinstance(pool_address, str) or not pool_address.strip():
        raise ValueError("Moonit curve address is required")
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Input mint is required")
    if isinstance(amount_in, bool) or not isinstance(amount_in, int) or not 0 < amount_in <= 2**64 - 1:
        raise ValueError("Input amount must be a positive u64 integer")
    if isinstance(slippage_bps, bool) or not isinstance(slippage_bps, int) or not 0 <= slippage_bps <= 300:
        raise ValueError("Slippage must be an integer between 0 and 300 bps")
    if isinstance(max_snapshot_age, bool) or not isinstance(max_snapshot_age, (int, float)) or not math.isfinite(max_snapshot_age) or max_snapshot_age <= 0:
        raise ValueError("Maximum snapshot age must be positive and finite")

    discovered = _account(pool_address)
    if discovered.get("owner") != PROGRAMS["moonit_amm"]:
        raise ValueError("Curve account is not owned by the Moonit program")
    first = _decode_pool("moonit_amm", discovered["data"])
    if first.get("curve_type") != 1:
        raise ValueError("Moonit direct quotes support only verified ConstantProductV1 curves")
    if first.get("collateral_currency") != 0:
        raise ValueError("Moonit direct quotes support only native-SOL collateral curves")
    if first.get("curve_amount", 0) <= 0 or first.get("curve_amount", 0) > first.get("total_supply", 0):
        raise ValueError("Moonit curve amount is empty or exceeds total supply")
    if first.get("total_supply", 0) >= initial_token_reserves:
        raise ValueError("Moonit total supply leaves no headroom under virtual token reserves")
    if first.get("token_decimals") != 9:
        raise ValueError("Moonit ConstantProductV1 direct quotes require the verified 9-decimal token format")

    keys = [pool_address, first["base_mint"], config_address]
    snapshot_started = time.time()
    accounts, slot = _multiple_accounts_with_context(keys)
    if any(item is None for item in accounts):
        raise ValueError("Moonit snapshot is missing a required account")
    pool_acc, mint_acc, config_acc = accounts
    if pool_acc.get("owner") != PROGRAMS["moonit_amm"]:
        raise ValueError("Moonit curve owner changed during snapshot")
    decoded = _decode_pool("moonit_amm", pool_acc["data"])
    for field in ("base_mint", "total_supply", "curve_amount",
                  "curve_type", "collateral_currency", "token_decimals"):
        if decoded.get(field) != first.get(field):
            raise ValueError("Moonit curve state changed during snapshot")
    if mint_acc.get("owner") != TOKEN_PROGRAM:
        raise ValueError("Moonit direct quotes require a legacy SPL Token mint")
    config_raw = config_acc.get("data", b"")
    if (config_acc.get("owner") != PROGRAMS["moonit_amm"]
            or len(config_raw) < 170
            or config_raw[:8] != config_discriminator):
        raise ValueError("Moonit ConfigAccount owner, discriminator or layout is invalid")
    fee_bps = int.from_bytes(config_raw[168:170], "little")
    if fee_bps < 0 or fee_bps > 10_000:
        raise ValueError("Moonit fee configuration is invalid")
    decimals = _mint_decimals(mint_acc)
    if decimals != decoded["token_decimals"]:
        raise ValueError("Moonit curve decimals do not match the mint account")

    curve_position = decoded["total_supply"] - decoded["curve_amount"]
    virtual_token = initial_token_reserves - curve_position
    if virtual_token <= 0:
        raise ValueError("Moonit virtual token reserve is empty")
    virtual_collateral = constant_product // virtual_token
    if virtual_collateral <= 0:
        raise ValueError("Moonit virtual collateral reserve is empty")

    token_mint = decoded["base_mint"]
    if input_mint == SOL_MINT:
        output_mint = token_mint
        fee = (amount_in * fee_bps) // 10_000
        net_collateral = amount_in - fee
        new_collateral = virtual_collateral + net_collateral
        if new_collateral <= 0:
            raise ValueError("Moonit buy collateral reserve is invalid")
        remaining_virtual_token = (constant_product + new_collateral - 1) // new_collateral
        gross_output = virtual_token - remaining_virtual_token
        output_amount = min(gross_output, decoded["curve_amount"])
        reserve_in, reserve_out = virtual_collateral, virtual_token
        fee_in_input = fee
    elif input_mint == token_mint:
        output_mint = SOL_MINT
        new_token = virtual_token + amount_in
        if new_token <= 0:
            raise ValueError("Moonit sell token reserve is invalid")
        remaining_virtual_collateral = (constant_product + new_token - 1) // new_token
        gross_output = virtual_collateral - remaining_virtual_collateral
        fee = (gross_output * fee_bps) // 10_000
        output_amount = gross_output - fee
        reserve_in, reserve_out = virtual_token, virtual_collateral
        fee_in_input = 0
    else:
        raise ValueError("Input mint does not belong to this Moonit SOL curve")

    if output_amount <= 0 or output_amount >= reserve_out or output_amount > 2**64 - 1:
        raise ValueError("Moonit quote rounds to zero, exceeds u64, or exceeds curve reserves")
    minimum_out = output_amount * (10_000 - slippage_bps) // 10_000
    net_for_impact = max(1, amount_in - fee_in_input)
    spot_price = reserve_out / reserve_in
    execution_price = output_amount / net_for_impact
    impact = max(0.0, (1.0 - execution_price / spot_price) * 100.0)
    age = time.time() - snapshot_started
    if age < 0 or age > max_snapshot_age:
        raise ValueError("Moonit snapshot is stale")
    if not math.isfinite(impact):
        raise ValueError("Moonit price impact is invalid")

    return {
        "provider": "moonit_direct",
        "protocol": "moonit_amm",
        "pool_id": pool_address,
        "slot": slot,
        "snapshot_age_seconds": round(age, 3),
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount_in,
        "slippage_bps": slippage_bps,
        "curve_amount": decoded["curve_amount"],
        "total_supply": decoded["total_supply"],
        "fee_bps": fee_bps,
        "fee_amount": fee,
        "out_amount": output_amount,
        "minimum_out_amount": minimum_out,
        "price_impact_percent": round(impact, 6),
        "token_decimals": decimals,
        "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT",
        "execution": "DISABLED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
    }








def quote_pumpfun_legacy_pool(pool_address, input_mint, output_mint, amount_in,
                              slippage_bps=100, max_snapshot_age=5.0):
    """Read-only Pump bonding-curve quote for SOL-paired legacy SPL mints."""
    if not isinstance(pool_address, str) or not pool_address.strip():
        raise ValueError("Pump.fun bonding curve address is required")
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Input mint is required")
    if not isinstance(output_mint, str) or not output_mint.strip():
        raise ValueError("Output mint is required")
    if isinstance(amount_in, bool) or not isinstance(amount_in, int) or not 0 < amount_in <= 2**64 - 1:
        raise ValueError("Input amount must be a positive u64 integer")
    if isinstance(slippage_bps, bool) or not isinstance(slippage_bps, int) or not 0 <= slippage_bps <= 300:
        raise ValueError("Slippage must be an integer between 0 and 300 bps")
    if isinstance(max_snapshot_age, bool) or not isinstance(max_snapshot_age, (int, float)) or not math.isfinite(max_snapshot_age) or max_snapshot_age <= 0:
        raise ValueError("Maximum snapshot age must be positive and finite")

    is_buy = input_mint == SOL_MINT and output_mint != SOL_MINT
    is_sell = output_mint == SOL_MINT and input_mint != SOL_MINT
    if not (is_buy or is_sell):
        raise ValueError("Pump.fun direct quotes currently support only SOL/token directions")
    token_mint = output_mint if is_buy else input_mint

    discovered = _account(pool_address)
    if discovered.get("owner") != PROGRAMS["pumpfun_legacy"]:
        raise ValueError("Bonding curve account is not owned by the Pump.fun program")
    first = _decode_pool("pumpfun_legacy", discovered["data"])
    token_mint_bytes = _b58decode(token_mint)
    if len(token_mint_bytes) != 32:
        raise ValueError("Pump.fun token mint must decode to 32 bytes")
    expected_curve, _ = _find_program_address(
        [b"bonding-curve", token_mint_bytes], PROGRAMS["pumpfun_legacy"]
    )
    if expected_curve != pool_address:
        raise ValueError("Pump.fun bonding curve PDA does not match the requested token mint")
    if len(discovered.get("data", b"")) < 125:
        raise ValueError("Pump.fun bonding curve extension layout is truncated")
    if first.get("is_mayhem_mode"):
        raise ValueError("Pump.fun mayhem-mode curves require a separate fee schedule")
    if first.get("is_cashback_coin"):
        raise ValueError("Pump.fun cashback curves are not supported")
    if first.get("is_holder_reward"):
        raise ValueError("Pump.fun holder-reward curves require a separate fee model")
    if first.get("quote_mint") not in (None, DEFAULT_PUBKEY):
        raise ValueError("Pump.fun token-paired curves are not supported by this SOL-only adapter")
    if first.get("creator_fee_bps", 0) > 0:
        raise ValueError("Custom creator-fee curves require a separate fee model")

    keys = [pool_address, PUMP_GLOBAL_CONFIG, PUMP_FEE_CONFIG, token_mint]
    snapshot_started = time.time()
    accounts, slot = _multiple_accounts_with_context(keys)
    if any(item is None for item in accounts):
        raise ValueError("Pump.fun confirmed snapshot is missing a required account")
    curve_acc, global_acc, fee_acc, mint_acc = accounts

    if curve_acc.get("owner") != PROGRAMS["pumpfun_legacy"]:
        raise ValueError("Pump.fun curve owner changed during snapshot")
    curve = _decode_pool("pumpfun_legacy", curve_acc["data"])
    for field in (
        "virtual_token_reserves", "virtual_quote_reserves",
        "real_token_reserves", "real_quote_reserves", "token_total_supply",
        "creator", "is_mayhem_mode", "is_cashback_coin", "quote_mint",
        "creator_fee_bps", "is_holder_reward",
    ):
        if curve.get(field) != first.get(field):
            raise ValueError("Pump.fun curve reserves or fee-mode state changed during snapshot")
    if curve.get("complete"):
        raise ValueError("Pump.fun bonding curve completed during quote snapshot")

    if global_acc.get("owner") != PROGRAMS["pumpfun_legacy"]:
        raise ValueError("Pump.fun Global account is not owned by the Pump program")
    global_raw = global_acc.get("data", b"")
    if len(global_raw) < 162 or global_raw[:8] != PUMP_GLOBAL_CONFIG_DISCRIMINATOR:
        raise ValueError("Pump.fun Global account discriminator or layout is invalid")
    if fee_acc.get("owner") != PUMP_FEES_PROGRAM:
        raise ValueError("Pump.fun FeeConfig is not owned by the Pump Fees program")
    fee_config = _decode_pumpswap_fee_config(fee_acc.get("data", b""))
    if mint_acc.get("owner") != TOKEN_PROGRAM:
        raise ValueError("Pump.fun Token-2022 mints are refused until transfer-fee and hook behavior is verified")

    token_decimals = _mint_decimals(mint_acc)
    virtual_token = curve["virtual_token_reserves"]
    virtual_quote = curve["virtual_quote_reserves"]
    real_token = curve["real_token_reserves"]
    real_quote = curve["real_quote_reserves"]
    supply = curve["token_total_supply"]
    if min(virtual_token, virtual_quote, real_token, supply) <= 0:
        raise ValueError("Pump.fun virtual reserves, real token reserve or supply are invalid")
    market_cap_lamports = (virtual_quote * supply) // virtual_token

    tiers = fee_config["fee_tiers"]
    if tiers:
        ordered = sorted(tiers, key=lambda item: item["threshold"])
        selected = ordered[0]
        for tier in ordered:
            if market_cap_lamports >= tier["threshold"]:
                selected = tier
            else:
                break
        fee_policy = "ONCHAIN_MARKET_CAP_FEE_TIER"
        fee_rates = [
            selected["lp_fee_bps"],
            selected["protocol_fee_bps"],
            selected["creator_fee_bps"],
        ]
    else:
        fee_policy = "GLOBAL_CONFIG_FALLBACK"
        fee_rates = [
            0,
            _u64(global_raw, 105),
            _u64(global_raw, 154),
        ]
    # The creator component is only charged when the curve names a creator.
    # The on-chain fee tier is shared across curves, but a default creator has
    # no creator vault to receive that component.
    if curve.get("creator") == DEFAULT_PUBKEY:
        fee_rates[2] = 0
    if any(rate > 10_000 for rate in fee_rates) or sum(fee_rates) >= 10_000:
        raise ValueError("Pump.fun fee schedule is invalid or consumes the full trade")

    if is_buy:
        net_quote, fee_amounts = _pumpswap_spendable_quote(amount_in, fee_rates)
        total_fee = sum(fee_amounts)
        if net_quote <= 0:
            raise ValueError("Pump.fun quote input is consumed by fees")
        output_amount = (virtual_token * net_quote) // (virtual_quote + net_quote)
        if output_amount <= 0 or output_amount >= real_token:
            raise ValueError("Pump.fun buy would drain or complete the bonding curve; migration quoting is not enabled")
        impact_input = net_quote
        impact_output = output_amount
        spot_price = virtual_token / virtual_quote
        output_mint = token_mint
        unspent_input = amount_in - net_quote - total_fee
        gross_output = output_amount
    else:
        if amount_in > supply:
            raise ValueError("Pump.fun sell input exceeds total token supply")
        gross_quote = (virtual_quote * amount_in) // (virtual_token + amount_in)
        if real_quote <= 0:
            raise ValueError("Pump.fun curve has no real SOL reserve for this sell")
        fee_amounts = [_fee_amount_ceil(gross_quote, rate) for rate in fee_rates]
        total_fee = sum(fee_amounts)
        output_amount = gross_quote - total_fee
        if output_amount <= 0:
            raise ValueError("Pump.fun sell output is consumed by fees")
        if gross_quote > real_quote:
            raise ValueError("Pump.fun curve has insufficient real SOL reserves for gross sell settlement")
        impact_input = amount_in
        impact_output = gross_quote
        spot_price = virtual_quote / virtual_token
        output_mint = SOL_MINT
        unspent_input = 0
        gross_output = gross_quote

    minimum_out = output_amount * (10_000 - slippage_bps) // 10_000
    execution_price = impact_output / impact_input
    impact = max(0.0, (1.0 - execution_price / spot_price) * 100.0)
    age = time.time() - snapshot_started
    if age < 0 or age > max_snapshot_age:
        raise ValueError("Pump.fun curve snapshot is stale")
    if not math.isfinite(impact):
        raise ValueError("Pump.fun price impact is invalid")

    return {
        "provider": "pumpfun_legacy_direct",
        "protocol": "pumpfun_legacy",
        "pool_id": pool_address,
        "slot": slot,
        "snapshot_age_seconds": round(age, 3),
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount_in,
        "slippage_bps": slippage_bps,
        "output_amount_gross": gross_output,
        "out_amount": output_amount,
        "minimum_out_amount": minimum_out,
        "unspent_input_amount": unspent_input,
        "fee_policy": fee_policy,
        "fee_mint": SOL_MINT,
        "fee_rates_bps": fee_rates,
        "creator": curve.get("creator"),
        "curve_complete": curve.get("complete"),
        "is_cashback_coin": curve.get("is_cashback_coin", False),
        "is_holder_reward": curve.get("is_holder_reward", False),
        "creator_fee_bps": curve.get("creator_fee_bps", 0),
        "lp_fee_amount": fee_amounts[0],
        "protocol_fee_amount": fee_amounts[1],
        "creator_fee_amount": fee_amounts[2],
        "total_fee_amount": total_fee,
        "market_cap_lamports": market_cap_lamports,
        "virtual_token_reserves": virtual_token,
        "virtual_quote_reserves": virtual_quote,
        "real_token_reserves": real_token,
        "real_quote_reserves": real_quote,
        "is_mayhem_mode": curve.get("is_mayhem_mode", False),
        "token_decimals": token_decimals,
        "price_impact_percent": round(impact, 6),
        "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT",
        "execution": "DISABLED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
    }




def quote_direct_pool(pool_address, input_mint, amount_in, slippage_bps=100, output_mint=None):
    """Dispatch read-only direct quotes from the actual pool account owner."""
    discovered = _account(pool_address)
    owner = discovered.get("owner")
    protocol = next((name for name, program in PROGRAMS.items() if program == owner), None)
    if protocol == "pumpfun_legacy":
        if not output_mint:
            raise ValueError("Pump.fun direct quotes require the requested output mint")
        return quote_pumpfun_legacy_pool(
            pool_address, input_mint, output_mint, amount_in, slippage_bps
        )
    if protocol == "pumpswap":
        return quote_pumpswap_pool(pool_address, input_mint, amount_in, slippage_bps)
    if protocol == "raydium_clmm":
        from raydium_clmm_quote import quote_raydium_clmm_pool
        return quote_raydium_clmm_pool(
            pool_address, input_mint, amount_in, slippage_bps,
            output_mint=output_mint,
        )
    if protocol == "orca_whirlpool":
        from orca_whirlpool_quote import quote_orca_whirlpool_pool
        return quote_orca_whirlpool_pool(
            pool_address, input_mint, amount_in, slippage_bps,
            output_mint=output_mint,
        )
    if protocol == "meteora_damm_v2":
        from meteora_damm_quote import quote_meteora_damm_pool
        return quote_meteora_damm_pool(
            pool_address, input_mint, amount_in, slippage_bps,
            output_mint=output_mint,
        )
    if protocol == "meteora_dbc":
        from meteora_dbc_quote import quote_meteora_dbc_pool
        return quote_meteora_dbc_pool(
            pool_address, input_mint, amount_in, slippage_bps,
            output_mint=output_mint,
        )
    if protocol == "meteora_dlmm":
        from meteora_dlmm_quote import quote_meteora_dlmm_pool
        return quote_meteora_dlmm_pool(
            pool_address, input_mint, amount_in, slippage_bps,
            output_mint=output_mint,
        )
    if protocol == "meteora_dlmm":
        from meteora_dlmm_quote import quote_meteora_dlmm_pool
        return quote_meteora_dlmm_pool(
            pool_address, input_mint, amount_in, slippage_bps,
            output_mint=output_mint,
        )
    if protocol == "raydium_cpmm":
        return quote_raydium_cpmm_pool(pool_address, input_mint, amount_in, slippage_bps)
    if protocol == "raydium_amm_v4":
        return quote_raydium_amm_v4_pool(pool_address, input_mint, amount_in, slippage_bps)
    if protocol == "fluxbeam_amm":
        return quote_fluxbeam_pool(pool_address, input_mint, amount_in, slippage_bps)
    if protocol == "moonit_amm":
        return quote_moonit_pool(pool_address, input_mint, amount_in, slippage_bps)
    if protocol is None:
        raise ValueError("Pool owner is not a supported Solana DEX program")
    raise ValueError("Direct quote adapter is not implemented for " + protocol)


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
