"""Additional ScreenerBot-parity Solana discovery feeds.

All feeds are read-only. Provider failures are isolated so one unavailable
source never prevents the remaining discovery sources from running.
"""
import os
import re

from solders.pubkey import Pubkey

from api_client import get_json

JUPITER_BASE = "https://lite-api.jup.ag/tokens/v2"
RUGCHECK_BASE = "https://api.rugcheck.xyz/v1/stats"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/list"
DEFILLAMA_URL = "https://api.llama.fi/protocols"

TIMEOUT = 18
BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _is_solana_mint(value):
    """Match Solana Pubkey parsing, not merely a base58-looking string."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not BASE58_RE.fullmatch(value):
        return False
    try:
        return len(bytes(Pubkey.from_string(value))) == 32
    except (ValueError, TypeError):
        return False


def _items(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "tokens", "token_list", "results"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
    return []


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
    for source_key, target_key in (
        ("icon", "icon"), ("logoURI", "icon"), ("logo", "icon"),
        ("usdPrice", "priceUsd"), ("liquidity", "liquidity_usd"),
        ("mcap", "marketCap"), ("fdv", "fdv"),
        ("decimals", "decimals"),
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
        print("DISCOVERY SOURCE ERROR:", source, repr(error), flush=True)
        return None


def scan_rugcheck():
    """Rugcheck new, recent, trending and verified feeds."""
    feeds = (
        ("rugcheck_new", "new_tokens"),
        ("rugcheck_recent", "recent"),
        ("rugcheck_trending", "trending"),
        ("rugcheck_verified", "verified"),
    )
    found = {}
    for source, endpoint in feeds:
        payload = _safe_fetch(source, f"{RUGCHECK_BASE}/{endpoint}", ttl=45)
        for item in _items(payload):
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                metadata = item.get("tokenMeta")
            metadata = metadata if isinstance(metadata, dict) else {}
            if source == "rugcheck_recent" and not metadata:
                continue
            token = _candidate(
                item.get("mint") or item.get("address"),
                source, item,
                name=item.get("name") or metadata.get("name"),
                symbol=item.get("symbol") or metadata.get("symbol"),
            )
            if token:
                mint = token["tokenAddress"]
                if mint not in found:
                    found[mint] = token
                elif source not in found[mint]["discovery_sources"]:
                    found[mint]["discovery_sources"].append(source)
    print("RUGCHECK DISCOVERY TOKENS:", len(found), flush=True)
    return list(found.values())



def _scan_rugcheck_feed(source, endpoint):
    """Fetch one RugCheck stats feed; raise on transport/HTTP/JSON/schema errors.

    RugCheck's documented stats response is a list or JSON null (empty list).
    A failed feed must remain visible to the aggregator's per-source health.
    """
    payload = get_json(
        f"{RUGCHECK_BASE}/{endpoint}",
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
        cache_ttl=45,
        max_retries=1,
    )
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ValueError(f"{source} returned an invalid stats-list payload")
    found = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            metadata = item.get("tokenMeta")
        metadata = metadata if isinstance(metadata, dict) else {}
        # ScreenerBot's recent feed requires its metadata object; entries
        # without it are not discovery candidates.
        if source == "rugcheck_recent" and not metadata:
            continue
        token = _candidate(
            item.get("mint") or item.get("address"),
            source,
            item,
            name=item.get("name") or metadata.get("name"),
            symbol=item.get("symbol") or metadata.get("symbol"),
        )
        if token:
            mint = token["tokenAddress"]
            if mint not in found:
                found[mint] = token
            else:
                _merge_rugcheck_candidate(found[mint], token)
    return list(found.values())


def _merge_rugcheck_candidate(existing, incoming):
    """Keep first useful metadata and union independent RugCheck feed labels."""
    for key in ("name", "symbol", "icon", "priceUsd", "liquidity_usd", "marketCap", "fdv", "decimals"):
        current = existing.get(key)
        value = incoming.get(key)
        if current in (None, "", "Unknown", 0) and value not in (None, "", "Unknown", 0):
            existing[key] = value
    for source in incoming.get("discovery_sources", []):
        if source not in existing["discovery_sources"]:
            existing["discovery_sources"].append(source)


def scan_rugcheck_new():
    return _scan_rugcheck_feed("rugcheck_new", "new_tokens")


def scan_rugcheck_recent():
    return _scan_rugcheck_feed("rugcheck_recent", "recent")


def scan_rugcheck_trending():
    return _scan_rugcheck_feed("rugcheck_trending", "trending")


def scan_rugcheck_verified():
    return _scan_rugcheck_feed("rugcheck_verified", "verified")


def _scan_jupiter_feed(source, endpoint, *, interval=None, limit=100):
    """Fetch one Jupiter V2 list so failures are visible per endpoint."""
    url = f"{JUPITER_BASE}/{endpoint}"
    params = {"limit": limit} if endpoint != "recent" else None
    if interval:
        url = f"{url}/{interval}"
    payload = get_json(
        url, params=params, headers={"Accept": "application/json"},
        timeout=15, cache_ttl=30, max_retries=1
    )
    if not isinstance(payload, list):
        raise ValueError(f"{source} returned an invalid Jupiter token-list payload")
    found = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        token = _candidate(item.get("id") or item.get("mint") or item.get("address"), source, item)
        if token:
            found[token["tokenAddress"]] = token
    return list(found.values())


def scan_jupiter_recent():
    return _scan_jupiter_feed("jupiter_recent", "recent")


def scan_jupiter_organic():
    return _scan_jupiter_feed("jupiter_organic", "toporganicscore", interval="1h")


def scan_jupiter_traded():
    return _scan_jupiter_feed("jupiter_traded", "toptraded", interval="1h")


def scan_jupiter_trending():
    return _scan_jupiter_feed("jupiter_trending", "toptrending", interval="1h")


def scan_jupiter():
    """Backward-compatible combined view; aggregator schedules each feed alone."""
    found = {}
    for scanner in (scan_jupiter_recent, scan_jupiter_organic, scan_jupiter_traded, scan_jupiter_trending):
        try:
            for token in scanner():
                mint = token["tokenAddress"]
                if mint not in found:
                    found[mint] = token
                else:
                    _merge_rugcheck_candidate(found[mint], token)
        except Exception as error:
            print("JUPITER DISCOVERY FEED ERROR:", repr(error), flush=True)
    return list(found.values())

def scan_coingecko():
    """CoinGecko coin list filtered to actual Solana platform addresses."""
    headers = {"Accept": "application/json"}
    api_key = os.getenv("COINGECKO_API_KEY", "").strip()
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    payload = get_json(
        COINGECKO_URL, params={"include_platform": "true"}, headers=headers,
        timeout=20, cache_ttl=3600, max_retries=1
    )
    if not isinstance(payload, list):
        raise ValueError("CoinGecko returned an invalid coins/list payload")
    found = {}
    for item in _items(payload):
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
    payload = get_json(
        DEFILLAMA_URL, headers={"Accept": "application/json"},
        timeout=25, cache_ttl=3600, max_retries=1
    )
    if not isinstance(payload, list):
        raise ValueError("DeFiLlama returned an invalid protocols payload")
    found = {}
    for item in _items(payload):
        if not isinstance(item, dict):
            continue
        chains = item.get("chains")
        if not isinstance(chains, list) or not any(
            isinstance(chain, str) and "solana" in chain.lower()
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
