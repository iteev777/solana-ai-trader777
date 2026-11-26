# ==========================================
# TROJAN V2 - BIRDEYE TOKEN SCANNER
# V3 TOKEN LIST WITH TRENDING FALLBACK
# ==========================================

import os
import math
import time
import requests

from api_client import get_json


API_KEY = os.getenv("BIRDEYE_API_KEY")

API_URL = "https://public-api.birdeye.so/defi/v3/token/list"
TRENDING_URL = "https://public-api.birdeye.so/defi/token_trending"

REQUEST_TIMEOUT = 15
CACHE_TTL = 60
MAX_RETRIES = 1
TOKEN_LIMIT = 100

# Birdeye reports exhausted monthly/plan credits as HTTP 400.
QUOTA_COOLDOWN_SECONDS = 3600
_quota_disabled_until = 0


# ==========================================
# VALIDATION
# ==========================================

def valid_token(token):

    if not isinstance(token, dict):
        return False

    address = token.get("address") or token.get("tokenAddress")

    return isinstance(address, str) and bool(address.strip())


def safe_volume(value):

    try:
        volume = float(value)

        if not math.isfinite(volume) or volume < 0:
            return None

        return volume

    except (TypeError, ValueError, OverflowError):
        return None


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

    volume = safe_volume(
        token.get("volume_24h_usd", token.get("v24hUSD"))
    )

    return {
        "tokenAddress": address,
        "chainId": "solana",
        "name": token.get("name") or "Unknown",
        "symbol": token.get("symbol") or "Unknown",
        "volume_24h": volume,
        "discovery_sources": ["birdeye"]
    }


# ==========================================
# BIRDEYE TOKEN SCANNER
# ==========================================

def scan_birdeye():

    global _quota_disabled_until

    found_tokens = []

    if not API_KEY:
        raise RuntimeError("Birdeye: BIRDEYE_API_KEY is missing")
    if time.monotonic() < _quota_disabled_until:
        raise RuntimeError("Birdeye: quota cooldown active")

    headers = {
        "X-API-KEY": API_KEY,
        "x-chain": "solana",
        "accept": "application/json"
    }

    data = None
    endpoint_used = "token_list_v3"

    try:

        # V3 is Birdeye's current bulk token-list endpoint.
        # Both sorting parameters are explicitly supplied.
        try:
            data = get_json(
                API_URL,
                params={
                    "sort_by": "volume_24h_usd",
                    "sort_type": "desc",
                    "offset": 0,
                    "limit": TOKEN_LIMIT
                },
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                cache_ttl=CACHE_TTL,
                max_retries=MAX_RETRIES
            )

        except requests.HTTPError as error:

            response = getattr(error, "response", None)
            status_code = getattr(response, "status_code", None)

            # A plan/endpoint compatibility issue should not disable
            # the entire Birdeye source: fall back to trending tokens.
            if status_code not in (400, 404, 422):
                raise

            print(
                "BIRDEYE V3 UNAVAILABLE; USING TRENDING FALLBACK:",
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
            raise RuntimeError("Birdeye: provider reported failure")

        tokens = extract_items(data)
        if tokens is None:
            raise ValueError(f"Birdeye {endpoint_used}: invalid response shape")

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

        print("================================", flush=True)
        print("TROJAN V2 BIRDEYE SCANNER", flush=True)
        print("ENDPOINT:", endpoint_used, flush=True)
        print("TOKENS FOUND:", len(found_tokens), flush=True)
        print("================================", flush=True)

    except requests.HTTPError as error:
        response_text = getattr(error.response, "text", None) or ""
        if "compute units usage limit exceeded" in response_text[:300].lower():
            _quota_disabled_until = time.monotonic() + QUOTA_COOLDOWN_SECONDS
            raise RuntimeError("Birdeye: quota exhausted; paused for 1 hour") from None
        raise

    return found_tokens


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    print("TROJAN V2 BIRDEYE SCANNER READY", flush=True)

    scan_birdeye()
