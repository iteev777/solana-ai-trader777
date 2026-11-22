# ==========================================
# TROJAN V2 - DEXSCREENER MULTI-SOURCE SCANNER
# ==========================================

import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from api_client import get_json


SOURCES = {
    "LATEST PROFILES":
        "https://api.dexscreener.com/token-profiles/latest/v1",

    "LATEST BOOSTS":
        "https://api.dexscreener.com/token-boosts/latest/v1",

    "TOP BOOSTS":
        "https://api.dexscreener.com/token-boosts/top/v1",

    "COMMUNITY TAKEOVERS":
        "https://api.dexscreener.com/community-takeovers/latest/v1"
}

REQUEST_TIMEOUT = 15
CACHE_TTL = 25
MAX_RETRIES = 1

MAX_RESULTS = 400
SCAN_COOLDOWN = 3

_last_scan_time = 0
_scan_lock = threading.Lock()


def fetch_source(name, url):
    try:
        data = get_json(
            url,
            timeout=REQUEST_TIMEOUT,
            cache_ttl=CACHE_TTL,
            max_retries=MAX_RETRIES
        )

        if not isinstance(data, list):
            print("INVALID SOURCE DATA:", name)
            return []

        print(
            "SOURCE:",
            name,
            "| FOUND:",
            len(data)
        )

        return data

    except requests.RequestException as error:
        print(
            "SOURCE REQUEST ERROR:",
            name,
            repr(error)
        )
        return []

    except Exception as error:
        print(
            "SOURCE ERROR:",
            name,
            repr(error)
        )
        return []


def normalize_token(token):
    if not isinstance(token, dict):
        return None

    chain = str(token.get("chainId", "")).lower()

    if chain != "solana":
        return None

    address = token.get("tokenAddress")

    if not isinstance(address, str):
        return None

    address = address.strip()

    if not address:
        return None

    return address


def scan_tokens():
    global _last_scan_time

    # Prevent simultaneous scans.
    if not _scan_lock.acquire(blocking=False):
        print("SCANNER ALREADY RUNNING")
        return []

    try:
        current_time = time.time()

        if current_time - _last_scan_time < SCAN_COOLDOWN:
            print("SCANNER COOLDOWN ACTIVE")
            return []

        _last_scan_time = current_time

        discovered = {}
        source_stats = {}

        # Fetch independent discovery endpoints concurrently.
        with ThreadPoolExecutor(max_workers=len(SOURCES)) as source_pool:
            futures = {
                source_pool.submit(fetch_source, name, url): name
                for name, url in SOURCES.items()
            }

            source_results = {}
            for future in as_completed(futures):
                source_name = futures[future]
                try:
                    source_results[source_name] = future.result()
                except Exception as error:
                    print("SOURCE WORKER ERROR:", source_name, repr(error))
                    source_results[source_name] = []

        # Merge deterministically after all source requests complete.
        for source_name in SOURCES:
            tokens = source_results.get(source_name, [])
            source_stats[source_name] = len(tokens)

            for token in tokens:
                address = normalize_token(token)
                if not address:
                    continue

                if address not in discovered:
                    discovered[address] = {
                        "tokenAddress": address,
                        "chainId": "solana",
                        "discovery_sources": [],
                        "discovery_timestamp": current_time
                    }

                candidate = discovered[address]
                if source_name not in candidate["discovery_sources"]:
                    candidate["discovery_sources"].append(source_name)

                for key, value in token.items():
                    if key not in candidate:
                        candidate[key] = value

        results = list(discovered.values())

        results.sort(
            key=lambda token: (
                len(token["discovery_sources"]),
                token["discovery_timestamp"]
            ),
            reverse=True
        )

        results = results[:MAX_RESULTS]

        print("================================")
        print("TROJAN V2 MULTI-SOURCE SCANNER")
        print("UNIQUE TOKENS:", len(results))
        print("================================")

        for source_name, count in source_stats.items():
            print(
                "SOURCE RESULT:",
                source_name,
                count
            )

        for token in results[:10]:
            print(
                "CANDIDATE:",
                token["tokenAddress"],
                "| SOURCES:",
                token["discovery_sources"]
            )

        return results

    finally:
        _scan_lock.release()


if __name__ == "__main__":
    scan_tokens()
