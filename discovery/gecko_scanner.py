import requests

from api_client import get_json


API_URL = (
    "https://api.geckoterminal.com/api/v2/"
    "networks/solana/new_pools"
)

REQUEST_TIMEOUT = 15
CACHE_TTL = 60
MAX_RETRIES = 1


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

        pools = data.get("data", [])

        if not isinstance(pools, list):
            print("GECKO INVALID POOL LIST")
            return found_tokens

        for pool in pools:

            if not isinstance(pool, dict):
                continue

            attributes = pool.get("attributes") or {}
            relationships = pool.get("relationships") or {}

            if not isinstance(attributes, dict):
                continue

            if not isinstance(relationships, dict):
                continue

            base_relationship = relationships.get(
                "base_token"
            ) or {}

            if not isinstance(base_relationship, dict):
                continue

            base_data = base_relationship.get("data") or {}

            if not isinstance(base_data, dict):
                continue

            token_id = base_data.get("id", "")

            if not isinstance(token_id, str) or not token_id:
                continue

            token_address = token_id.split("_")[-1].strip()

            pool_address = attributes.get("address")

            if not isinstance(pool_address, str):
                continue

            pool_address = pool_address.strip()

            if not token_address or not pool_address:
                continue

            if token_address in seen_in_scan:
                continue

            seen_in_scan.add(token_address)

            found_tokens.append({
                "tokenAddress": token_address,
                "chainId": "solana",
                "poolAddress": pool_address,
                "poolName": attributes.get("name"),
                "discovery_sources": ["geckoterminal"]
            })

        print("GECKO NEW TOKENS:", len(found_tokens))

    except requests.RequestException as error:
        print("GECKO REQUEST ERROR:", repr(error))

    except (ValueError, TypeError, AttributeError) as error:
        print("GECKO DATA ERROR:", repr(error))

    except Exception as error:
        print("GECKO SCANNER ERROR:", repr(error))

    return found_tokens


if __name__ == "__main__":
    scan_new_pools()
