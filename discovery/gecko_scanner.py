import requests

API_URL = (
    "https://api.geckoterminal.com/api/v2/"
    "networks/solana/new_pools"
)

seen_pools = set()


def scan_new_pools():

    found_tokens = []

    try:
        response = requests.get(
            API_URL,
            headers={"Accept": "application/json"},
            timeout=15
        )

        response.raise_for_status()

        pools = response.json().get("data", [])

        for pool in pools:

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

            if not pool_address:
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

    except Exception as error:
        print("GECKO SCANNER ERROR:", repr(error))

    return found_tokens
