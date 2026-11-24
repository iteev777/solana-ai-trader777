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
_source_health_lock = threading.Lock()
_source_health = {}


def _record_source_health(name, status, count=0, error=None):
    with _source_health_lock:
        _source_health[name] = {
            "status": status,
            "count": int(count),
            "last_checked_at": time.time(),
            "last_error": str(error)[:240] if error else None,
        }


def get_scanner_status():
    with _source_health_lock:
        sources = {name: dict(value) for name, value in _source_health.items()}
    return {
        "running": _scan_lock.locked(),
        "last_scan_at": _last_scan_time or None,
        "sources": sources,
    }


def fetch_source(name, url):
    try:
        data = get_json(
            url,
            timeout=REQUEST_TIMEOUT,
            cache_ttl=CACHE_TTL,
            max_retries=MAX_RETRIES
        )

        if not isinstance(data, list):
            error = "INVALID_SOURCE_RESPONSE_SHAPE"
            _record_source_health(name, "ERROR", error=error)
            print("INVALID SOURCE DATA:", name)
            return []

        _record_source_health(name, "HEALTHY", count=len(data))
        print(
            "SOURCE:",
            name,
            "| FOUND:",
            len(data)
        )

        return data

    except requests.RequestException as error:
        _record_source_health(name, "ERROR", error=error)
        print(
            "SOURCE REQUEST ERROR:",
            name,
            repr(error)
        )
        return []

    except Exception as error:
        _record_source_health(name, "ERROR", error=error)
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

    if not isinstance(address, str) or address != address.strip():
        return None

    try:
        from market_data import valid_address
        if not valid_address(address):
            return None
    except Exception:
        return None

    return address


def name_slug(value):
    return "_".join(
        part for part in str(value).strip().lower().replace("-", " ").split()
        if part
    )


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
                        "discovery_feeds": [],
                        "discovery_timestamp": current_time
                    }

                candidate = discovered[address]
                # All four endpoints are DexScreener feeds, not four
                # independent providers. Preserve feed-level evidence while
                # counting the provider only once for consensus scoring.
                if "dexscreener" not in candidate["discovery_sources"]:
                    candidate["discovery_sources"].append("dexscreener")
                feed_key = "dexscreener_" + name_slug(source_name)
                if feed_key not in candidate["discovery_feeds"]:
                    candidate["discovery_feeds"].append(feed_key)

                for key, value in token.items():
                    existing = candidate.get(key)
                    missing = existing is None or (
                        isinstance(existing, str)
                        and existing.strip().lower() in {"", "unknown", "n/a", "none", "null"}
                    )
                    incoming_present = value is not None and not (
                        isinstance(value, str) and not value.strip()
                    )
                    if key not in candidate or (missing and incoming_present):
                        candidate[key] = value

        results = list(discovered.values())

        results.sort(
            key=lambda token: (
                len(token["discovery_feeds"]),
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
