import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from discovery.gecko_scanner import scan_new_pools, scan_recent_updates, scan_trending_pools
from discovery.pumpfun_scanner import scan_pumpfun
from discovery.birdeye_scanner import scan_birdeye
from discovery.dexscreener_scanner import scan_dexscreener
from discovery.screenerbot_sources import scan_rugcheck, scan_jupiter, scan_coingecko, scan_defillama

SCANNERS = [
    ("geckoterminal_new_pools", scan_new_pools),
    ("geckoterminal_trending_pools", scan_trending_pools),
    ("geckoterminal_recent_updates", scan_recent_updates),
    ("birdeye_meme_listing", scan_pumpfun),
    ("birdeye", scan_birdeye),
    ("dexscreener", scan_dexscreener),
    ("rugcheck", scan_rugcheck),
    ("jupiter", scan_jupiter),
    ("coingecko", scan_coingecko),
    ("defillama", scan_defillama),
]
MAX_RESULTS = 500
MAX_WORKERS = min(9, len(SCANNERS))
_aggregator_lock = threading.Lock()
_last_source_stats = {}
_last_scan_at = None


def normalize_token(token):
    if not isinstance(token, dict):
        return None
    address = token.get("tokenAddress")
    chain = token.get("chainId")
    if not isinstance(address, str) or not address.strip():
        return None
    if not isinstance(chain, str) or chain.lower().strip() != "solana":
        return None
    return address.strip()


def get_discovery_status():
    return {
        "source_count": len(SCANNERS),
        "sources": list(_last_source_stats.values()),
        "last_scan_at": _last_scan_at,
        "last_scan_age_seconds": max(0, round(time.time() - _last_scan_at, 1)) if _last_scan_at else None,
        "max_results": MAX_RESULTS,
    }


def collect_tokens():
    global _last_source_stats, _last_scan_at
    if not _aggregator_lock.acquire(blocking=False):
        print("TROJAN V2 AGGREGATOR ALREADY RUNNING", flush=True)
        return []
    try:
        discovered = {}
        source_stats = {}
        current_time = time.time()
        source_results = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(scanner): name for name, scanner in SCANNERS}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                    if not isinstance(result, list):
                        raise ValueError("Discovery scanner returned an invalid result")
                    source_results[name] = result
                    source_stats[name] = {"name": name, "count": len(source_results[name]), "ok": True, "error": None}
                except Exception as error:
                    print("TOKEN SOURCE WORKER ERROR:", name, repr(error), flush=True)
                    source_results[name] = []
                    source_stats[name] = {"name": name, "count": 0, "ok": False, "error": str(error)[:180]}

        for name, _scanner in SCANNERS:
            for token in source_results.get(name, []):
                address = normalize_token(token)
                if not address:
                    continue
                if address not in discovered:
                    discovered[address] = {
                        "tokenAddress": address, "chainId": "solana",
                        "discovery_sources": [], "discovery_timestamp": current_time,
                    }
                candidate = discovered[address]
                sources = token.get("discovery_sources", [])
                if isinstance(sources, str):
                    sources = [sources]
                if not isinstance(sources, list):
                    sources = []
                valid_sources = [s.strip().lower() for s in sources if isinstance(s, str) and s.strip()] or [name]
                for source in valid_sources:
                    if source not in candidate["discovery_sources"]:
                        candidate["discovery_sources"].append(source)
                for key, value in token.items():
                    if key not in candidate and value is not None:
                        candidate[key] = value

        results = list(discovered.values())
        results.sort(key=lambda t: (len(t["discovery_sources"]), t["discovery_timestamp"]), reverse=True)
        results = results[:MAX_RESULTS]
        for name, _scanner in SCANNERS:
            source_stats.setdefault(name, {"name": name, "count": 0, "ok": True, "error": None})
        _last_source_stats = source_stats
        _last_scan_at = current_time
        print("TROJAN V2 TOKEN AGGREGATOR | SOURCES:", len(SCANNERS), "| UNIQUE:", len(results), flush=True)
        for name, _scanner in SCANNERS:
            stat = source_stats[name]
            print("SCANNER:", name, "| FOUND:", stat["count"], "| OK:", stat["ok"], flush=True)
        return results
    finally:
        _aggregator_lock.release()


if __name__ == "__main__":
    print("TROJAN V2 TOKEN AGGREGATOR READY", flush=True)
    collect_tokens()
