"""Additional ScreenerBot-parity Solana discovery feeds.

All feeds are read-only. Provider failures are isolated so one unavailable
source never prevents the remaining discovery sources from running.
"""
import os

from api_client import get_json
from solana_onchain import _b58decode

JUPITER_BASE = "https://lite-api.jup.ag/tokens/v2"
RUGCHECK_BASE = "https://api.rugcheck.xyz/v1/stats"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/list"
DEFILLAMA_URL = "https://api.llama.fi/protocols"

TIMEOUT = 18
_FETCH_FAILED = object()


def _is_solana_mint(value):
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not 32 <= len(value) <= 44:
        return False
    try:
        return len(_b58decode(value)) == 32
    except ValueError:
        return False


def _items(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and payload.get("success") is not False:
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "tokens", "token_list", "results"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
    return None


def _candidate(mint, source, item=None, name=None, symbol=None):
    if not _is_solana_mint(mint):
        return None
    item = item if isinstance(item, dict) else {}
    result = {
        "tokenAddress": mint.strip(),
        "chainId": "solana",
        "name": name or item.get("name") or "Unknown",
        "symbol": symbol or item.get("symbol") or "Unknown",
        "discovery_sources": [source],
    }
    decimals = item.get("decimals")
    if type(decimals) is int and 0 <= decimals <= 255:
        result["decimals"] = decimals
    for source_key, target_key in (
        ("icon", "icon"), ("logoURI", "icon"), ("logo", "icon"),
        ("usdPrice", "priceUsd"), ("liquidity", "liquidity_usd"),
        ("mcap", "marketCap"), ("fdv", "fdv"),
    ):
        value = item.get(source_key)
        if value is not None and target_key not in result:
            result[target_key] = value
    return result


def _safe_fetch(source, url, *, params=None, headers=None, ttl=60):
    try:
        return get_json(
            url, params=params, headers=headers, timeout=TIMEOUT,
            cache_ttl=ttl, max_retries=1
        )
    except Exception as error:
        print("DISCOVERY SOURCE ERROR:", source, type(error).__name__, flush=True)
        return _FETCH_FAILED


def _merge_candidate(found, token):
    mint = token["tokenAddress"]
    if mint not in found:
        found[mint] = token
        return
    existing = found[mint]
    source = token["discovery_sources"][0]
    if source not in existing["discovery_sources"]:
        existing["discovery_sources"].append(source)
    for key, value in token.items():
        if key != "discovery_sources" and existing.get(key) in (None, "", "Unknown"):
            existing[key] = value


def scan_rugcheck():
    """Rugcheck new, recent, trending and verified feeds."""
    feeds = (
        ("rugcheck_new", "new_tokens"),
        ("rugcheck_recent", "recent"),
        ("rugcheck_trending", "trending"),
        ("rugcheck_verified", "verified"),
    )
    found = {}
    available = False
    for source, endpoint in feeds:
        payload = _safe_fetch(source, f"{RUGCHECK_BASE}/{endpoint}", ttl=45)
        items = [] if payload is None else _items(payload)
        if items is None:
            continue
        available = True
        for item in items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                metadata = item.get("tokenMeta")
            metadata = metadata if isinstance(metadata, dict) else {}
            token = _candidate(
                item.get("mint") or item.get("address"),
                source, item,
                name=item.get("name") or metadata.get("name"),
                symbol=item.get("symbol") or metadata.get("symbol"),
            )
            if token:
                _merge_candidate(found, token)
    if not available:
        raise RuntimeError("RUGCHECK: all discovery feeds unavailable")
    print("RUGCHECK DISCOVERY TOKENS:", len(found), flush=True)
    return list(found.values())


def scan_jupiter():
    """Jupiter recent, organic-score, traded and trending token lists."""
    feeds = (
        ("jupiter_recent", f"{JUPITER_BASE}/recent"),
        ("jupiter_organic", f"{JUPITER_BASE}/toporganicscore/1h?limit=100"),
        ("jupiter_traded", f"{JUPITER_BASE}/toptraded/1h?limit=100"),
        ("jupiter_trending", f"{JUPITER_BASE}/toptrending/1h?limit=100"),
    )
    found = {}
    available = False
    for source, url in feeds:
        payload = _safe_fetch(source, url, ttl=30)
        items = _items(payload)
        if items is None:
            continue
        available = True
        for item in items:
            if not isinstance(item, dict):
                continue
            token = _candidate(
                item.get("id") or item.get("mint") or item.get("address"),
                source, item
            )
            if token:
                _merge_candidate(found, token)
    if not available:
        raise RuntimeError("JUPITER: all discovery feeds unavailable")
    print("JUPITER DISCOVERY TOKENS:", len(found), flush=True)
    return list(found.values())


def scan_coingecko():
    """CoinGecko coin list filtered to actual Solana platform addresses."""
    headers = {"Accept": "application/json"}
    api_key = os.getenv("COINGECKO_API_KEY", "").strip()
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    payload = _safe_fetch(
        "coingecko_solana", COINGECKO_URL,
        params={"include_platform": "true"}, headers=headers, ttl=3600
    )
    items = _items(payload)
    if items is None:
        raise RuntimeError("CoinGecko: discovery feed unavailable or invalid")
    found = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        platforms = item.get("platforms")
        if not isinstance(platforms, dict):
            continue
        mint = platforms.get("solana")
        token = _candidate(
            mint, "coingecko", item,
            name=item.get("name"), symbol=item.get("symbol")
        )
        if token:
            found[token["tokenAddress"]] = token
    print("COINGECKO SOLANA TOKENS:", len(found), flush=True)
    return list(found.values())


def scan_defillama():
    """DeFiLlama protocol registry filtered to Solana contract addresses."""
    payload = _safe_fetch("defillama_solana", DEFILLAMA_URL, ttl=3600)
    items = _items(payload)
    if items is None:
        raise RuntimeError("DeFiLlama: discovery feed unavailable or invalid")
    found = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        chains = item.get("chains")
        if not isinstance(chains, list) or not any(
            isinstance(chain, str) and chain.lower() == "solana"
            for chain in chains
        ):
            continue
        address = item.get("address")
        if isinstance(address, str) and address.lower().startswith("solana:"):
            address = address.split(":", 1)[1]
        token = _candidate(
            address, "defillama", item,
            name=item.get("name"), symbol=item.get("symbol")
        )
        if token:
            found[token["tokenAddress"]] = token
    print("DEFILLAMA SOLANA TOKENS:", len(found), flush=True)
    return list(found.values())
