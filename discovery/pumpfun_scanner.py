import os
import time
import math
import requests

from api_client import get_json


API_KEY = os.getenv("BIRDEYE_API_KEY")

API_URL = "https://public-api.birdeye.so/defi/v2/tokens/new_listing"

REQUEST_TIMEOUT = 15
CACHE_TTL = 30
MAX_RETRIES = 1
TOKEN_LIMIT = 20


def valid_token(token):

    if not isinstance(token, dict):
        return False

    address = token.get("address")

    return isinstance(address, str) and bool(address.strip())


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
                "limit": TOKEN_LIMIT,
                "meme_platform_enabled": "true"
            },
            headers={
                "X-API-KEY": API_KEY,
                "x-chain": "solana",
                "accept": "application/json"
            },
            timeout=REQUEST_TIMEOUT,
            cache_ttl=CACHE_TTL,
            max_retries=MAX_RETRIES
        )

        if not isinstance(data, dict):
            print("PUMPFUN INVALID RESPONSE")
            return found_tokens

        result = data.get("data")

        if not isinstance(result, dict):
            print("PUMPFUN INVALID DATA")
            return found_tokens

        tokens = result.get("items", [])

        if not isinstance(tokens, list):
            print("PUMPFUN INVALID TOKEN LIST")
            return found_tokens

        seen_in_scan = set()

        for token in tokens:

            if not valid_token(token):
                continue

            address = token["address"].strip()

            if address in seen_in_scan:
                continue

            seen_in_scan.add(address)

            found_tokens.append({
                "tokenAddress": address,
                "chainId": "solana",
                "name": token.get("name") or "Unknown",
                "symbol": token.get("symbol") or "Unknown",
                "discovery_sources": ["pumpfun"]
            })

        print(
            "PUMPFUN/BIRDEYE NEW TOKENS:",
            len(found_tokens)
        )

    except requests.RequestException as error:
        print("PUMPFUN REQUEST ERROR:", repr(error))

    except (ValueError, TypeError, AttributeError) as error:
        print("PUMPFUN DATA ERROR:", repr(error))

    except Exception as error:
        print("PUMPFUN SCANNER ERROR:", repr(error))

    return found_tokens


if __name__ == "__main__":
    scan_pumpfun()
