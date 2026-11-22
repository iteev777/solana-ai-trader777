# ==========================================
# TROJAN V2 - GECKOTERMINAL SCANNER
# ==========================================

import requests

from api_client import get_json
from market_data import normalize_gecko_pool


API_URL = (
    "https://api.geckoterminal.com/api/v2/"
    "networks/solana/new_pools"
)

REQUEST_TIMEOUT = 15
CACHE_TTL = 60
MAX_RETRIES = 1

NETWORK_PREFIX = "solana_"


# ==========================================
# TOKEN VALIDATION
# ==========================================

def extract_solana_address(token_id):

    if not isinstance(token_id, str):
        return None

    token_id = token_id.strip()

    if not token_id.startswith(NETWORK_PREFIX):
        return None

    address = token_id[len(NETWORK_PREFIX):].strip()

    if not address:
        return None

    if "_" in address:
        return None

    return address


# ==========================================
# SCAN NEW POOLS
# ==========================================

def scan_new_pools():

    found_tokens = []
    seen_in_scan = set()

    try:

        data = get_json(
            API_URL,
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
            cache_ttl=CACHE_TTL,
            max_retries=MAX_RETRIES
        )

        if not isinstance(data, dict):

            print("GECKO INVALID RESPONSE")
            return found_tokens

        pools = data.get("data")

        if not isinstance(pools, list):

            print("GECKO INVALID POOL LIST")
            return found_tokens

        for pool in pools:

            if not isinstance(pool, dict):
                continue

            attributes = pool.get("attributes")
            relationships = pool.get("relationships")

            if not isinstance(attributes, dict):
                continue

            if not isinstance(relationships, dict):
                continue

            base_relationship = relationships.get("base_token")

            if not isinstance(base_relationship, dict):
                continue

            base_data = base_relationship.get("data")

            if not isinstance(base_data, dict):
                continue

            token_address = extract_solana_address(
                base_data.get("id")
            )

            if not token_address:
                continue

            pool_address = attributes.get("address")

            if not isinstance(pool_address, str):
                continue

            pool_address = pool_address.strip()

            if not pool_address:
                continue

            if token_address in seen_in_scan:
                continue

            seen_in_scan.add(token_address)

            market_snapshot = normalize_gecko_pool(pool)
            candidate = {
                "tokenAddress": token_address,
                "chainId": "solana",
                "poolAddress": pool_address,
                "poolName": attributes.get("name"),
                "discovery_sources": ["geckoterminal"]
            }
            if market_snapshot and market_snapshot.get("baseToken", {}).get("address") == token_address:
                candidate["market_data"] = market_snapshot
            found_tokens.append(candidate)

        print("================================")
        print("TROJAN V2 GECKOTERMINAL")
        print("NEW UNIQUE TOKENS:", len(found_tokens))
        print("================================")

    except requests.RequestException as error:

        print("GECKO REQUEST ERROR:", repr(error))

    except (ValueError, TypeError, AttributeError) as error:

        print("GECKO DATA ERROR:", repr(error))

    except Exception as error:

        print("GECKO SCANNER ERROR:", repr(error))

    return found_tokens


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    print("TROJAN V2 GECKO SCANNER READY")

    scan_new_pools()
