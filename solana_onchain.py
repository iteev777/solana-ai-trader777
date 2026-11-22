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

RPC_URL = os.getenv("SOLANA_RPC_URL", "").strip()
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
        raise RuntimeError("SOLANA_RPC_URL is not configured")
    with _rpc_semaphore:
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
    if protocol == "raydium_amm_v4":
        # Legacy AmmInfo is a non-Anchor account; its fields begin at byte 0.
        if len(raw) < 464:
            raise ValueError("invalid Raydium AMM v4 AmmInfo layout")
        status = _u64(raw, 0)
        if status not in (1, 6, 7):
            raise ValueError("Raydium AMM v4 pool status does not allow swaps")
        return {
            "vault_base": _key(raw, 336), "vault_quote": _key(raw, 368),
            "base_mint": _key(raw, 400), "quote_mint": _key(raw, 432),
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


def decode_pool_snapshot(pool_address, protocol, token_mint, _force=False, _register_watch=True):
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
        return dict(cached["data"])
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
        if protocol == "pumpfun_legacy":
            decimals = _mint_decimals(_account(token_mint))
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
            decimals = decoded["token_decimals"]
            if _mint_decimals(_account(token_mint)) != decimals:
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
            "observedAt": time.time()
        }
        _cache[key] = {"time": time.time(), "data": result}
        _last_status.update({"configured": True, "last_success": time.time(), "last_error": None})
        return result

    if protocol == "meteora_dbc":
        if decoded["base_mint"] != token_mint:
            raise ValueError("requested mint is not the Meteora DBC base mint")
        config = _account(decoded["config"])
        if config["owner"] != PROGRAMS["meteora_dbc"] or len(config["data"]) < 40:
            raise ValueError("invalid Meteora DBC PoolConfig account")
        if config["data"][:8] != bytes([26, 108, 14, 123, 116, 230, 129, 43]):
            raise ValueError("invalid Meteora DBC PoolConfig discriminator")
        quote_mint = _key(config["data"], 8)
        base_decimals = _mint_decimals(_account(decoded["base_mint"]))
        quote_decimals = _mint_decimals(_account(quote_mint))
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
            "observedAt": time.time()
        }
        _cache[key] = {"time": time.time(), "data": result}
        _last_status.update({"configured": True, "last_success": time.time(), "last_error": None})
        return result

    if token_mint not in (decoded["base_mint"], decoded["quote_mint"]):
        raise ValueError("requested mint is not part of decoded pool")
    vaults = _multiple_accounts([
        decoded["vault_base"], decoded["vault_quote"],
        decoded["base_mint"], decoded["quote_mint"]
    ])
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
        "baseToken": {"address": token_mint},
        "quoteToken": {"address": other_mint},
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


_rpc_probe = {"checked_at": 0.0, "healthy": False, "error": None}


def check_rpc_health(force=False):
    now = time.time()
    if not RPC_URL:
        return False, "SOLANA_RPC_URL missing"
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
    return {
        **_last_status,
        "supported_protocols": sorted(list(PROGRAMS.keys()) + ["raydium_auto"]),
        "rpc_configured": bool(RPC_URL),
        "rpc_healthy": healthy,
        "rpc_error": error,
        "price_stream": stream
    }


def _record_error(error):
    _last_status["last_error"] = str(error)[:250]
