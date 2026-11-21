import os
import time
import requests

API_KEY = os.getenv("BIRDEYE_API_KEY")

API_URL = "https://public-api.birdeye.so/defi/v2/tokens/new_listing"

seen_tokens = set()


def scan_pumpfun():

    found_tokens = []

    if not API_KEY:
        print("PUMPFUN SKIPPED: BIRDEYE API KEY MISSING")
        return found_tokens

    try:
        response = requests.get(
            API_URL,
            params={
                "time_to": int(time.time()),
                "limit": 50,
                "meme_platform_enabled": "true"
            },
            headers={
                "X-API-KEY": API_KEY,
                "x-chain": "solana",
                "accept": "application/json"
            },
            timeout=15
        )

        response.raise_for_status()

        result = response.json()
        data = result.get("data", {})
        tokens = data.get("items", [])

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

    except Exception as error:
        print("PUMPFUN SCANNER ERROR:", repr(error))

    return found_tokens
