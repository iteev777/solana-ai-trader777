import os
import requests

from api_client import get_json

API_KEY = os.getenv("BIRDEYE_API_KEY")

API_URL = "https://public-api.birdeye.so/defi/tokenlist"

seen_tokens = set()


def scan_birdeye():

    found_tokens = []

    if not API_KEY:
        print("BIRDEYE SKIPPED: API KEY MISSING")
        return found_tokens

    try:
        data = get_json(
            API_URL,
            params={
                "sort_by": "v24hUSD",
                "sort_type": "desc",
                "offset": 0,
                "limit": 50
            },
            headers={
                "X-API-KEY": API_KEY,
                "x-chain": "solana"
            },
            timeout=15,
            cache_ttl=60,
            max_retries=1
        )

        result = data.get("data", {})
        tokens = result.get("tokens", [])

        for token in tokens:

            address = token.get("address")

            if not address or address in seen_tokens:
                continue

            seen_tokens.add(address)

            found_tokens.append({
                "tokenAddress": address,
                "name": token.get("name", "Unknown"),
                "symbol": token.get("symbol", "Unknown"),
                "discovery_sources": ["birdeye"]
            })

        print("BIRDEYE TOKENS:", len(found_tokens))

    except (requests.RequestException, ValueError, TypeError, AttributeError) as error:
        print("BIRDEYE SCANNER ERROR:", repr(error))

    return found_tokens
