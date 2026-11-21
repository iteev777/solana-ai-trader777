import os
import time
import requests

from api_client import get_json

API_KEY = os.getenv("BIRDEYE_API_KEY")
API_URL = "https://public-api.birdeye.so/defi/v2/tokens/new_listing"

seen_tokens = set()


def scan_pumpfun():

    found_tokens = []

    if not API_KEY:
        print("PUMPFUN SKIPPED: API KEY MISSING")
        return found_tokens

    try:
        data = get_json(
            API_URL,
            params={
                "time_to": int(time.time()),
                "limit": 20,
                "meme_platform_enabled": "true"
            },
            headers={
                "X-API-KEY": API_KEY,
                "x-chain": "solana",
                "accept": "application/json"
            },
            timeout=15,
            cache_ttl=30,
            max_retries=1
        )

        result = data.get("data", {})
        tokens = result.get("items", [])

        for token in tokens:

            address = token.get("address")

            if not address or address in seen_tokens:
                continue

            seen_tokens.add(address)

            found_tokens.append({
                "tokenAddress": address,
                "name": token.get("name", "Unknown"),
                "symbol": token.get("symbol", "Unknown"),
                "discovery_sources": ["pumpfun"]
            })

        print("PUMPFUN/BIRDEYE NEW TOKENS:", len(found_tokens))

    except (requests.RequestException, ValueError, TypeError, AttributeError) as error:
        print("PUMPFUN SCANNER ERROR:", repr(error))

    return found_tokens
