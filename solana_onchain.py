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
import requests

RPC_URL = os.getenv("SOLANA_RPC_URL", "").strip()
TIMEOUT = float(os.getenv("SOLANA_RPC_TIMEOUT", "8"))
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
PROGRAMS = {
    "pumpswap": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
    "raydium_cpmm": "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",
    "raydium_clmm": "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",
    "orca_whirlpool": "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
    "meteora_dlmm": "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
}
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


def _i32(data, offset):
    return struct.unpack_from("<i", data, offset)[0]


def _u128(data, offset):
    return int.from_bytes(data[offset:offset + 16], "little")


def _discriminator(name):
    return hashlib.sha256(("account:" + name).encode()).digest()[:8]


def _rpc(method, params):
    if not RPC_URL:
        raise RuntimeError("SOLANA_RPC_URL is not configured")
    response = requests.post(
        RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(str(payload["error"])[:300])
    return payload.get("result")


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
        if len(raw) < 333 or raw[:8] != _discriminator("PoolState"):
            raise ValueError("invalid Raydium CPMM PoolState discriminator/layout")
        status = raw[329]
        return {
            "vault_base": _key(raw, 72), "vault_quote": _key(raw, 104),
            "base_mint": _key(raw, 168), "quote_mint": _key(raw, 200),
            "status": "disabled" if status & 4 else "active",
            "price_mode": "reserves"
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
    raise ValueError("unsupported Solana pool protocol")


def _token_amount(account):
    if not account or len(account["data"]) < 72:
        raise ValueError("invalid SPL token vault account")
    raw = account["data"]
    return int.from_bytes(raw[64:72], "little")


def _mint_decimals(account):
    if not account or len(account["data"]) < 45:
        raise ValueError("invalid SPL mint account")
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


def decode_pool_snapshot(pool_address, protocol, token_mint):
    """Return a verified pool snapshot in USD, or None if data is unavailable."""
    protocol = str(protocol or "").lower().replace("-", "_")
    aliases = {
        "pump_swap": "pumpswap", "pumpfun_amm": "pumpswap", "pump_amm": "pumpswap", "pump.fun": "pumpswap",
        "raydium": "raydium_auto", "raydium_cp_swap": "raydium_cpmm",
        "raydium_clmm": "raydium_clmm",
        "orca": "orca_whirlpool", "whirlpool": "orca_whirlpool",
        "meteora": "meteora_dlmm", "dlmm": "meteora_dlmm"
    }
    protocol = aliases.get(protocol, protocol)
    if protocol == "raydium_auto":
        protocol = "raydium_auto"
    elif protocol not in PROGRAMS or not pool_address or not token_mint:
        return None
    if not pool_address or not token_mint:
        return None
    key = (pool_address, protocol, token_mint)
    cached = _cache.get(key)
    if cached and time.time() - cached["time"] < 3:
        return dict(cached["data"])
    pool = _account(pool_address)
    if protocol == "raydium_auto":
        protocol = next((name for name in ("raydium_cpmm", "raydium_clmm")
                         if pool["owner"] == PROGRAMS[name]), None)
        if protocol is None:
            raise ValueError("Raydium pool owner is not a supported CPMM or CLMM program")
    if pool["owner"] != PROGRAMS[protocol]:
        raise ValueError("pool account owner does not match " + protocol)
    decoded = _decode_pool(protocol, pool["data"])
    if decoded["status"] != "active":
        raise ValueError("pool is not swap-active")
    if token_mint not in (decoded["base_mint"], decoded["quote_mint"]):
        raise ValueError("requested mint is not part of decoded pool")
    vaults = _multiple_accounts([
        decoded["vault_base"], decoded["vault_quote"],
        decoded["base_mint"], decoded["quote_mint"]
    ])
    base_amount = _token_amount(vaults[0]) / (10 ** _mint_decimals(vaults[2]))
    quote_amount = _token_amount(vaults[1]) / (10 ** _mint_decimals(vaults[3]))
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
    quote_usd = _quote_usd(decoded["quote_mint"])
    if quote_usd is None:
        return None
    token_price = ratio * quote_usd if token_mint == decoded["base_mint"] else quote_usd / ratio
    if not math.isfinite(token_price) or token_price <= 0:
        raise ValueError("decoded on-chain price is invalid")
    result = {
        "priceUsd": str(token_price),
        "liquidity": {"usd": (base_amount * token_price + quote_amount * quote_usd)},
        "baseToken": {"address": token_mint},
        "quoteToken": {"address": decoded["quote_mint"] if token_mint == decoded["base_mint"] else decoded["base_mint"]},
        "marketSource": "solana_rpc_" + protocol,
        "poolAddress": pool_address,
        "onchainVerified": True,
        "observedAt": time.time()
    }
    _cache[key] = {"time": time.time(), "data": result}
    _last_status.update({"configured": True, "last_success": time.time(), "last_error": None})
    return result


def record_onchain_error(error):
    _last_status["last_error"] = str(error)[:250]


def get_onchain_status():
    return {
        **_last_status,
        "supported_protocols": sorted(list(PROGRAMS.keys()) + ["raydium_auto"]),
        "rpc_configured": bool(RPC_URL)
    }


def _record_error(error):
    _last_status["last_error"] = str(error)[:250]
