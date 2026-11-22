# ==========================================
# TROJAN V2 - TOKEN AGGREGATOR
# ==========================================

import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from discovery.gecko_scanner import scan_new_pools
from discovery.pumpfun_scanner import scan_pumpfun
from discovery.birdeye_scanner import scan_birdeye
from discovery.dexscreener_scanner import scan_dexscreener


# ==========================================
# SCANNER SOURCES
# ==========================================

SCANNERS = [
    ("geckoterminal", scan_new_pools),
    ("birdeye_meme_listing", scan_pumpfun),
    ("birdeye", scan_birdeye),
    ("dexscreener", scan_dexscreener)
]

MAX_RESULTS = 300

_aggregator_lock = threading.Lock()


# ==========================================
# TOKEN VALIDATION
# ==========================================

def normalize_token(token):

    if not isinstance(token, dict):
        return None

    address = token.get("tokenAddress")

    if not isinstance(address, str):
        return None

    address = address.strip()

    if not address:
        return None

    chain = token.get("chainId")

    if not isinstance(chain, str):
        return None

    if chain.lower().strip() != "solana":
        return None

    return address


# ==========================================
# TOKEN AGGREGATION
# ==========================================

def collect_tokens():

    if not _aggregator_lock.acquire(blocking=False):

        print("TROJAN V2 AGGREGATOR ALREADY RUNNING")
        return []

    try:

        discovered = {}
        source_stats = {}

        current_time = time.time()

        # Run independent providers concurrently, as ScreenerBot does.
        # Merge in configured source order so results stay deterministic.
        source_results = {}
        with ThreadPoolExecutor(max_workers=max(1, len(SCANNERS))) as pool:
            futures = {
                pool.submit(scanner): source_name
                for source_name, scanner in SCANNERS
            }
            for future in as_completed(futures):
                source_name = futures[future]
                try:
                    result = future.result()
                    source_results[source_name] = result if isinstance(result, list) else []
                except Exception as error:
                    print("TOKEN SOURCE WORKER ERROR:", source_name, repr(error), flush=True)
                    source_results[source_name] = []

        for source_name, scanner in SCANNERS:
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
                sources = token.get("discovery_sources", [])
                if isinstance(sources, str):
                    sources = [sources]
                if not isinstance(sources, list):
                    sources = []
                valid_sources = [
                    source.strip().lower()
                    for source in sources
                    if isinstance(source, str) and source.strip()
                ]
                if not valid_sources:
                    valid_sources = [source_name]
                for source in valid_sources:
                    if source not in candidate["discovery_sources"]:
                        candidate["discovery_sources"].append(source)
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
        print("TROJAN V2 TOKEN AGGREGATOR")
        print("UNIQUE TOKENS:", len(results))
        print("================================")

        for source_name, count in source_stats.items():

            print(
                "SCANNER:",
                source_name,
                "| FOUND:",
                count
            )

        for token in results[:10]:

            print(
                "AGGREGATED TOKEN:",
                token["tokenAddress"],
                "| SOURCES:",
                token["discovery_sources"]
            )

        return results

    finally:

        _aggregator_lock.release()


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    print("TROJAN V2 TOKEN AGGREGATOR READY")

    collect_tokens()
