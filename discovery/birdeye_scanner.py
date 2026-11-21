# ==========================================
# TROJAN V2 - BIRDEYE TOKEN SCANNER
# ==========================================

import os
import requests
import math

from api_client import get_json


API_KEY = os.getenv("BIRDEYE_API_KEY")

API_URL = "https://public-api.birdeye.so/defi/tokenlist"

REQUEST_TIMEOUT = 15
CACHE_TTL = 60
MAX_RETRIES = 1
TOKEN_LIMIT = 50


# ==========================================
# VALIDATION
# ==========================================

def valid_token(token):

    if not isinstance(token, dict):
        return False

    address = token.get("address")

    if not isinstance(address, str):
        return False

    return bool(address.strip())


def safe_volume(value):

    try:

        volume = float(value)

        if not math.isfinite(volume) or volume < 0:
            return None

        return volume

    except (TypeError, ValueError):

        return None


# ==========================================
# BIRDEYE TOKEN SCANNER
# ==========================================

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
                "limit": TOKEN_LIMIT
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

            print("BIRDEYE INVALID RESPONSE")
            return found_tokens

        if data.get("success") is False:

            print("BIRDEYE API REPORTED FAILURE")
            return found_tokens

        result = data.get("data")

        if not isinstance(result, dict):

            print("BIRDEYE INVALID DATA")
            return found_tokens

        tokens = result.get("tokens")

        if not isinstance(tokens, list):

            print("BIRDEYE INVALID TOKEN LIST")
            return found_tokens

        seen_in_scan = set()

        for token in tokens:

            if not valid_token(token):
                continue

            address = token["address"].strip()

            if address in seen_in_scan:
                continue

            seen_in_scan.add(address)

            volume = safe_volume(
                token.get("v24hUSD")
            )

            found_tokens.append({
                "tokenAddress": address,
                "chainId": "solana",
                "name": token.get("name") or "Unknown",
                "symbol": token.get("symbol") or "Unknown",
                "volume_24h": volume,
                "discovery_sources": ["birdeye"]
            })

        print("================================")
        print("TROJAN V2 BIRDEYE SCANNER")
        print("TOKENS FOUND:", len(found_tokens))
        print("================================")

    except requests.RequestException as error:

        print("BIRDEYE REQUEST ERROR:", repr(error))

    except (ValueError, TypeError, AttributeError) as error:

        print("BIRDEYE DATA ERROR:", repr(error))

    except Exception as error:

        print("BIRDEYE SCANNER ERROR:", repr(error))

    return found_tokens


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    print("TROJAN V2 BIRDEYE SCANNER READY")

    scan_birdeye()
