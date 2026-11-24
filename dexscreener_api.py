"""DexScreener API endpoint coverage missing from the batch market-data adapter.

This module is an independent Trojan implementation. It exposes raw validated
provider responses so pool selection, filtering and execution can consume the
specific endpoint they need without changing the existing batch quote path.
"""
from urllib.parse import quote

from api_client import get_json


BASE_URL = "https://api.dexscreener.com"
MAX_TOKENS_PER_REQUEST = 30
REQUEST_TIMEOUT = 10
CACHE_TTL = 5
MAX_RETRIES = 1


def _segment(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    value = value.strip()
    if "/" in value or "," in value or "?" in value or "#" in value:
        raise ValueError(f"{field} contains invalid URL characters")
    return quote(value, safe="-_.")


def _get(path, *, params=None, cache_ttl=CACHE_TTL):
    return get_json(
        BASE_URL + path,
        params=params,
        headers={"Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
        cache_ttl=cache_ttl,
        max_retries=MAX_RETRIES,
    )


def fetch_token_pools(token_address, chain_id="solana"):
    """Return all DexScreener pools for one mint (not only its best pair)."""
    chain = _segment(chain_id, "chain_id")
    mint = _segment(token_address, "token_address")
    data = _get(f"/token-pairs/v1/{chain}/{mint}")
    if not isinstance(data, list):
        raise ValueError("DexScreener token-pairs response must be a JSON array")
    return [item for item in data if isinstance(item, dict)]


def fetch_token_batch(token_addresses, chain_id="solana"):
    """Fetch best-pair market snapshots for up to 30 unique token mints."""
    if not isinstance(token_addresses, (list, tuple, set)):
        raise ValueError("token_addresses must be a list, tuple or set")
    addresses = list(dict.fromkeys(
        _segment(value, "token_address") for value in token_addresses
    ))
    if len(addresses) > MAX_TOKENS_PER_REQUEST:
        raise ValueError(f"at most {MAX_TOKENS_PER_REQUEST} token addresses are allowed")
    if not addresses:
        return []
    chain = _segment(chain_id, "chain_id")
    data = _get(
        f"/tokens/v1/{chain}/" + ",".join(addresses),
        cache_ttl=2,
    )
    if not isinstance(data, list):
        raise ValueError("DexScreener tokens batch response must be a JSON array")
    return [item for item in data if isinstance(item, dict)]


def fetch_pair(chain_id, pair_address):
    """Fetch one pair by chain and pair address; return None when not found."""
    chain = _segment(chain_id, "chain_id")
    pair = _segment(pair_address, "pair_address")
    data = _get(f"/latest/dex/pairs/{chain}/{pair}")
    if not isinstance(data, dict):
        raise ValueError("DexScreener pair lookup response must be an object")
    if isinstance(data.get("pair"), dict):
        return data["pair"]
    pairs = data.get("pairs")
    if isinstance(pairs, list):
        return next((item for item in pairs if isinstance(item, dict)), None)
    if data.get("pair") is None and "pair" in data:
        return None
    raise ValueError("DexScreener pair lookup response has no pair or pairs field")


def search_pairs(query):
    """Search DexScreener pairs by token name, symbol, address or pair text."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query cannot be empty")
    data = _get("/latest/dex/search", params={"q": query.strip()})
    if not isinstance(data, dict) or not isinstance(data.get("pairs"), list):
        raise ValueError("DexScreener search response must contain a pairs array")
    return [item for item in data["pairs"] if isinstance(item, dict)]


def fetch_token_orders(token_address, chain_id="solana"):
    """Fetch provider-reported token orders/boost placements."""
    chain = _segment(chain_id, "chain_id")
    mint = _segment(token_address, "token_address")
    data = _get(f"/orders/v1/{chain}/{mint}")
    if not isinstance(data, list):
        raise ValueError("DexScreener orders response must be a JSON array")
    return [item for item in data if isinstance(item, dict)]


def fetch_supported_chains():
    """Fetch the chains currently supported by DexScreener."""
    data = _get("/chains", cache_ttl=300)
    if not isinstance(data, list):
        raise ValueError("DexScreener supported chains response must be a JSON array")
    return [item for item in data if isinstance(item, dict)]
