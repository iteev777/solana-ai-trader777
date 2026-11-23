"""GeckoTerminal discovery feeds for new, trending and recently updated Solana tokens."""
import requests

from api_client import get_json
from market_data import normalize_gecko_pool

API_BASE = "https://api.geckoterminal.com/api/v2/networks/solana/"
API_URL = API_BASE + "new_pools"
TRENDING_URL = API_BASE + "trending_pools"
RECENTLY_UPDATED_URL = "https://api.geckoterminal.com/api/v2/tokens/info_recently_updated"
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


def _scan_pools(url, source_name, cache_ttl=CACHE_TTL):
    found_tokens = []
    seen = set()
    try:
        data = get_json(url, headers={"Accept": "application/json"}, timeout=REQUEST_TIMEOUT,
                        cache_ttl=cache_ttl, max_retries=MAX_RETRIES)
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            print("GECKO INVALID RESPONSE:", source_name, flush=True)
            return found_tokens
        for pool in data["data"]:
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
    except requests.RequestException as error:
        print("GECKO REQUEST ERROR:", source_name, repr(error), flush=True)
    except (ValueError, TypeError, AttributeError) as error:
        print("GECKO DATA ERROR:", source_name, repr(error), flush=True)
    except Exception as error:
        print("GECKO SCANNER ERROR:", source_name, repr(error), flush=True)
    return found_tokens


def scan_new_pools():
    return _scan_pools(API_URL, "geckoterminal_new_pools")


def scan_trending_pools():
    return _scan_pools(TRENDING_URL, "geckoterminal_trending_pools", cache_ttl=90)


def scan_recently_updated_tokens():
    """Discover Solana tokens from GeckoTerminal's global recently-updated feed."""
    found_tokens = {}
    source_name = "geckoterminal_recently_updated"
    try:
        payload = get_json(
            RECENTLY_UPDATED_URL,
            params={"network": "solana"},
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
            cache_ttl=CACHE_TTL,
            max_retries=MAX_RETRIES,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            print("GECKO RECENTLY UPDATED INVALID RESPONSE", flush=True)
            return []

        for record in payload["data"]:
            if not isinstance(record, dict):
                continue
            attributes = record.get("attributes")
            if not isinstance(attributes, dict):
                continue
            address = attributes.get("address")
            if not isinstance(address, str) or not address.strip():
                continue
            address = address.strip()
            if address in found_tokens:
                continue

            token = {
                "tokenAddress": address,
                "chainId": "solana",
                "name": attributes.get("name") or "Unknown",
                "symbol": attributes.get("symbol") or "Unknown",
                "discovery_sources": [source_name],
            }
            image_url = attributes.get("image_url")
            if isinstance(image_url, str) and image_url.strip():
                token["icon"] = image_url.strip()
            score = attributes.get("gt_score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                token["geckoterminal_score"] = score
            updated_at = attributes.get("metadata_updated_at")
            if isinstance(updated_at, str) and updated_at.strip():
                token["metadata_updated_at"] = updated_at.strip()
            found_tokens[address] = token

        print("GECKOTERMINAL RECENTLY UPDATED TOKENS:", len(found_tokens), flush=True)
    except requests.RequestException as error:
        print("GECKO REQUEST ERROR:", source_name, repr(error), flush=True)
    except (ValueError, TypeError, AttributeError) as error:
        print("GECKO DATA ERROR:", source_name, repr(error), flush=True)
    except Exception as error:
        print("GECKO SCANNER ERROR:", source_name, repr(error), flush=True)

    return list(found_tokens.values())


if __name__ == "__main__":
    print("TROJAN V2 GECKO SCANNER READY", flush=True)
    scan_new_pools()
    scan_trending_pools()
    scan_recently_updated_tokens()
