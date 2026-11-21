import requests

from api_client import get_json

API_URL = (
    "https://api.geckoterminal.com/api/v2/"
    "networks/solana/new_pools"
)

seen_pools = set()


def scan_new_pools():

    found_tokens = []

    try:
        data = get_json(
            API_URL,
            headers={"Accept": "application/json"},
            timeout=15,
            cache_ttl=60,
            max_retries=1
        )

        pools = data.get("data", [])

        for pool in pools:

            if not isinstance(pool, dict):
                continue

            attributes = pool.get("attributes", {})
            relationships = pool.get("relationships", {})

            base_token = relationships.get(
                "base_token", {}
            ).get("data", {})

            token_id = base_token.get("id", "")

            if not token_id:
                continue

            token_address = token_id.split("_")[-1]
            pool_address = attributes.get("address")

            if not pool_address or not token_address:
                continue

            if pool_address in seen_pools:
                continue

            seen_pools.add(pool_address)

            found_tokens.append({
                "tokenAddress": token_address,
                "poolAddress": pool_address,
                "discovery_sources": ["geckoterminal"]
            })

        print("GECKO NEW TOKENS:", len(found_tokens))

    except (requests.RequestException, ValueError, TypeError, AttributeError) as error:
        print("GECKO SCANNER ERROR:", repr(error))

    return found_tokens
