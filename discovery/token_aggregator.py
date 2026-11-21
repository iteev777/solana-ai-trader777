# ==========================================
# TROJAN V2 - TOKEN AGGREGATOR
# ==========================================

import time
import threading

from discovery.gecko_scanner import scan_new_pools
from discovery.pumpfun_scanner import scan_pumpfun
from discovery.birdeye_scanner import scan_birdeye


SCANNERS = [
    ("GECKO", scan_new_pools),
    ("PUMPFUN", scan_pumpfun),
    ("BIRDEYE", scan_birdeye)
]

MAX_RESULTS = 300

_aggregator_lock = threading.Lock()


def normalize_token(token):
    if not isinstance(token, dict):
        return None

    address = token.get("tokenAddress")

    if not isinstance(address, str):
        return None

    address = address.strip()

    if not address:
        return None

    chain = str(token.get("chainId", "solana")).lower()

    if chain != "solana":
        return None

    return address


def collect_tokens():

    if not _aggregator_lock.acquire(blocking=False):
        print("TROJAN V2 AGGREGATOR ALREADY RUNNING")
        return []

    try:
        discovered = {}
        source_stats = {}

        current_time = time.time()

        for source_name, scanner in SCANNERS:

            try:
                tokens = scanner()

                if not isinstance(tokens, list):
                    print(
                        "INVALID SCANNER RESULT:",
                        source_name
                    )

                    source_stats[source_name] = 0
                    continue

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

                    sources = token.get(
                        "discovery_sources",
                        []
                    )

                    if isinstance(sources, str):
                        sources = [sources]

                    if not isinstance(sources, list):
                        sources = []

                    # Add scanner name as an additional source.
                    sources = sources + [source_name]

                    for source in sources:

                        if not isinstance(source, str):
                            continue

                        source = source.strip()

                        if (
                            source
                            and source not in candidate["discovery_sources"]
                        ):
                            candidate["discovery_sources"].append(source)

                    # Preserve useful scanner information.
                    for key, value in token.items():

                        if key not in candidate:
                            candidate[key] = value

            except Exception as error:

                print(
                    "TOKEN AGGREGATOR ERROR:",
                    source_name,
                    repr(error)
                )

                source_stats[source_name] = 0

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


if __name__ == "__main__":
    collect_tokens()
