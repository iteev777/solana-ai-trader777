from api_client import get_json
from market_data import normalize_gecko_pool
from solana_onchain import _b58decode

API_BASE = "https://api.geckoterminal.com/api/v2/networks/solana/"
API_URL = API_BASE + "new_pools"
TRENDING_URL = API_BASE + "trending_pools"
RECENT_UPDATES_URL = "https://api.geckoterminal.com/api/v2/tokens/info_recently_updated"
REQUEST_TIMEOUT = 15
CACHE_TTL = 60
MAX_RETRIES = 1
NETWORK_PREFIX = "solana_"


def extract_solana_address(token_id):
    if not isinstance(token_id, str):
        return None
    token_id = token_id.strip()
    if not token_id.startswith(NETWORK_PREFIX):
        return None
    address = token_id[len(NETWORK_PREFIX):].strip()
    return address if address and "_" not in address else None


def _fetch_records(url, source_name, *, cache_ttl=CACHE_TTL, params=None):
    data = get_json(url, params=params, headers={"Accept": "application/json"},
                    timeout=REQUEST_TIMEOUT, cache_ttl=cache_ttl, max_retries=MAX_RETRIES)
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError(f"{source_name}: invalid response shape")
    return data["data"]


def _scan_pools(url, source_name, cache_ttl=CACHE_TTL):
    found_tokens = []
    seen = set()
    for pool in _fetch_records(url, source_name, cache_ttl=cache_ttl):
        if not isinstance(pool, dict):
            continue
        attributes, relationships = pool.get("attributes"), pool.get("relationships")
        if not isinstance(attributes, dict) or not isinstance(relationships, dict):
            continue
        base = relationships.get("base_token")
        base_data = base.get("data") if isinstance(base, dict) else None
        token_address = extract_solana_address(base_data.get("id")) if isinstance(base_data, dict) else None
        pool_address = attributes.get("address")
        if not token_address or not isinstance(pool_address, str) or not pool_address.strip() or token_address in seen:
            continue
        seen.add(token_address)
        snapshot = normalize_gecko_pool(pool)
        item = {"tokenAddress": token_address, "chainId": "solana", "poolAddress": pool_address.strip(),
                "poolName": attributes.get("name"), "discovery_sources": [source_name]}
        if snapshot and snapshot.get("baseToken", {}).get("address") == token_address:
            item["market_data"] = snapshot
        found_tokens.append(item)
    print("GECKOTERMINAL", source_name, "TOKENS:", len(found_tokens), flush=True)
    return found_tokens


def scan_new_pools():
    return _scan_pools(API_URL, "geckoterminal_new_pools")


def scan_trending_pools():
    return _scan_pools(TRENDING_URL, "geckoterminal_trending_pools", cache_ttl=90)


def scan_recent_updates():
    source_name = "geckoterminal_recent_updates"
    records = _fetch_records(RECENT_UPDATES_URL, source_name, params={"network": "solana"})
    found = {}
    for record in records:
        attributes = record.get("attributes") if isinstance(record, dict) else None
        if not isinstance(attributes, dict):
            continue
        address = attributes.get("address")
        if not isinstance(address, str) or not 32 <= len(address) <= 44:
            continue
        try:
            if len(_b58decode(address)) != 32:
                continue
        except ValueError:
            continue
        if extract_solana_address(record.get("id")) != address:
            continue
        found.setdefault(address, {
            "tokenAddress": address,
            "chainId": "solana",
            "name": attributes.get("name"),
            "symbol": attributes.get("symbol"),
            "icon": attributes.get("image_url"),
            "discovery_sources": [source_name],
        })
    return list(found.values())


if __name__ == "__main__":
    print("TROJAN V2 GECKO SCANNER READY", flush=True)
    scan_new_pools()
    scan_trending_pools()
