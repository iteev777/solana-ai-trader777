import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from solana_onchain import SOL_MINT, STABLES, _b58decode

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
_QUOTE_ASSETS = frozenset({SOL_MINT, "11111111111111111111111111111111", *STABLES})
_last_scan = (None, 0.0, ())


def normalize_token(token):
    if not isinstance(token, dict):
        return None
    address = token.get("tokenAddress")
    chain = token.get("chainId")
    if not isinstance(address, str) or not address.strip():
        return None
    if not isinstance(chain, str) or chain.lower().strip() != "solana":
        return None
    address = address.strip()
    if not 32 <= len(address) <= 44 or address in _QUOTE_ASSETS:
        return None
    try:
        return address if len(_b58decode(address)) == 32 else None
    except ValueError:
        return None


def get_discovery_status():
    scan_at, duration, sources = _last_scan
    return {
        "source_count": len(SCANNERS),
        "sources": [dict(source) for source in sources],
        "last_scan_at": scan_at,
        "last_scan_age_seconds": max(0, round(time.time() - scan_at, 1)) if scan_at is not None else None,
        "last_scan_duration_ms": round(duration * 1000) if scan_at is not None else None,
        "max_results": MAX_RESULTS,
    }


def collect_tokens():
    global _last_scan
    if not _aggregator_lock.acquire(blocking=False):
        print("TROJAN V2 AGGREGATOR ALREADY RUNNING", flush=True)
        return []
    try:
        discovered = {}
        source_stats = {}
        started = time.monotonic()
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
                    error_type = type(error).__name__
                    print("TOKEN SOURCE WORKER ERROR:", name, error_type, flush=True)
                    source_results[name] = []
                    source_stats[name] = {"name": name, "count": 0, "ok": False, "error": error_type}

        for name, _scanner in SCANNERS:
            invalid = 0
            for token in source_results.get(name, []):
                address = normalize_token(token)
                if not address:
                    invalid += 1
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
            source_stats[name]["invalid_count"] = invalid
            source_stats[name]["valid_count"] = source_stats[name]["count"] - invalid

        results = list(discovered.values())
        results.sort(key=lambda t: (len(t["discovery_sources"]), t["discovery_timestamp"]), reverse=True)
        results = results[:MAX_RESULTS]
        _last_scan = (current_time, time.monotonic() - started,
                      tuple(source_stats[name] for name, _scanner in SCANNERS))
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
