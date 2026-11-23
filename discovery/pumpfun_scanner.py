# ==========================================
# TROJAN V2 - BIRDEYE MEME LISTING SCANNER
# RECENT LISTINGS + TRENDING FALLBACK
# ==========================================

import os
import requests

from api_client import get_json
from discovery.birdeye_scanner import (
    _quota_exhausted,
    check_quota,
    extract_items,
    pause_if_quota_exhausted,
    valid_token,
)


API_KEY = os.getenv("BIRDEYE_API_KEY")

API_URL = "https://public-api.birdeye.so/defi/v2/tokens/new_listing"
TRENDING_URL = "https://public-api.birdeye.so/defi/token_trending"

REQUEST_TIMEOUT = 15
CACHE_TTL = 30
MAX_RETRIES = 1
TOKEN_LIMIT = 20

def normalize_token(token):

    if not valid_token(token):
        return None

    address = (
        token.get("address")
        or token.get("tokenAddress")
    ).strip()

    return {
        "tokenAddress": address,
        "chainId": "solana",
        "name": token.get("name") or "Unknown",
        "symbol": token.get("symbol") or "Unknown",
        "discovery_sources": ["birdeye_meme_listing"]
    }


# ==========================================
# SCAN BIRDEYE MEME LISTINGS
# ==========================================

def scan_pumpfun():

    found_tokens = []

    if not API_KEY:
        raise RuntimeError("Birdeye meme listing: BIRDEYE_API_KEY is missing")
    check_quota("Birdeye meme listing")

    headers = {
        "X-API-KEY": API_KEY,
        "x-chain": "solana",
        "accept": "application/json"
    }

    endpoint_used = "new_listing"

    try:

        # time_to is intentionally omitted. It is optional and must
        # be within a rolling three-day window; omitting it avoids
        # clock-boundary and timestamp validation failures.
        try:
            data = get_json(
                API_URL,
                params={
                    "limit": TOKEN_LIMIT,
                    "meme_platform_enabled": "true"
                },
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                cache_ttl=CACHE_TTL,
                max_retries=MAX_RETRIES
            )

        except requests.HTTPError as error:

            response = getattr(error, "response", None)
            status_code = getattr(response, "status_code", None)

            if status_code not in (400, 404, 422) or _quota_exhausted(response):
                raise

            print(
                "BIRDEYE NEW LISTING UNAVAILABLE; USING TRENDING FALLBACK:",
                status_code,
                flush=True
            )

            endpoint_used = "token_trending"

            data = get_json(
                TRENDING_URL,
                params={
                    "sort_by": "rank",
                    "sort_type": "asc",
                    "interval": "1h",
                    "limit": TOKEN_LIMIT
                },
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                cache_ttl=CACHE_TTL,
                max_retries=MAX_RETRIES
            )

        if isinstance(data, dict) and data.get("success") is False:
            raise RuntimeError("Birdeye meme listing: provider reported failure")

        tokens = extract_items(data)
        if tokens is None:
            raise ValueError(f"Birdeye meme listing {endpoint_used}: invalid response shape")

        seen_in_scan = set()

        for token in tokens:

            normalized = normalize_token(token)

            if not normalized:
                continue

            address = normalized["tokenAddress"]

            if address in seen_in_scan:
                continue

            seen_in_scan.add(address)
            found_tokens.append(normalized)

        print(
            "TROJAN V2 BIRDEYE MEME LISTINGS:",
            len(found_tokens),
            "| ENDPOINT:",
            endpoint_used,
            flush=True
        )

    except requests.HTTPError as error:
        pause_if_quota_exhausted(error, "Birdeye meme listing")
        raise

    return found_tokens


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    print("TROJAN V2 BIRDEYE MEME LISTING READY", flush=True)

    scan_pumpfun()
