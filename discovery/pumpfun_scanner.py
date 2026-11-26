# ==========================================
# TROJAN V2 - BIRDEYE MEME LISTING SCANNER
# RECENT LISTINGS + TRENDING FALLBACK
# ==========================================

import os
import time
import requests

from api_client import get_json


API_KEY = os.getenv("BIRDEYE_API_KEY")

API_URL = "https://public-api.birdeye.so/defi/v2/tokens/new_listing"
TRENDING_URL = "https://public-api.birdeye.so/defi/token_trending"

REQUEST_TIMEOUT = 15
CACHE_TTL = 30
MAX_RETRIES = 1
TOKEN_LIMIT = 20

# Birdeye reports exhausted monthly/plan credits as HTTP 400.
QUOTA_COOLDOWN_SECONDS = 3600
_quota_disabled_until = 0


# ==========================================
# TOKEN VALIDATION
# ==========================================

def valid_token(token):

    if not isinstance(token, dict):
        return False

    address = token.get("address") or token.get("tokenAddress")

    return isinstance(address, str) and bool(address.strip())


def extract_items(data):

    if not isinstance(data, dict):
        return None

    result = data.get("data")

    if isinstance(result, list):
        return result

    if not isinstance(result, dict):
        return None

    for key in ("items", "tokens", "token_list"):
        items = result.get(key)

        if isinstance(items, list):
            return items

    return None


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

    global _quota_disabled_until

    found_tokens = []

    if not API_KEY:
        raise RuntimeError("Birdeye meme listing: BIRDEYE_API_KEY is missing")
    if time.monotonic() < _quota_disabled_until:
        raise RuntimeError("Birdeye meme listing: quota cooldown active")

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

            if status_code not in (400, 404, 422):
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
        response_text = getattr(error.response, "text", None) or ""
        if "compute units usage limit exceeded" in response_text[:300].lower():
            _quota_disabled_until = time.monotonic() + QUOTA_COOLDOWN_SECONDS
            raise RuntimeError("Birdeye meme listing: quota exhausted; paused for 1 hour") from None
        raise

    return found_tokens


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    print("TROJAN V2 BIRDEYE MEME LISTING READY", flush=True)

    scan_pumpfun()
