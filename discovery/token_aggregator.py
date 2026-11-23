"""Concurrent multi-source Solana token discovery and source health telemetry."""
import time
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from discovery.gecko_scanner import scan_new_pools, scan_trending_pools
from discovery.pumpfun_scanner import scan_pumpfun
from discovery.birdeye_scanner import scan_birdeye
from discovery.dexscreener_scanner import scan_dexscreener
from discovery.screenerbot_sources import scan_rugcheck, scan_jupiter, scan_coingecko, scan_defillama

SCANNERS = [
    ("geckoterminal_new_pools", scan_new_pools),
    ("geckoterminal_trending_pools", scan_trending_pools),
    ("pumpfun", scan_pumpfun),
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
_source_history = {}
_last_scan_at = None


def _provider_family(source):
    value = str(source or "").strip().lower()
    for prefix in ("geckoterminal", "dexscreener", "birdeye", "pumpfun",
                   "pumpportal", "rugcheck", "jupiter", "coingecko", "defillama"):
        if value == prefix or value.startswith(prefix + "_"):
            return prefix
    return value.split("_", 1)[0] if value else "unknown"


def _source_result(name, ok, count, started, error=None):
    completed = time.time()
    elapsed_ms = round(max(0.0, time.perf_counter() - started) * 1000, 2)
    history = _source_history.setdefault(name, {
        "total_runs": 0, "successes": 0, "failures": 0,
        "consecutive_failures": 0, "last_success_at": None, "last_error_at": None,
    })
    history["total_runs"] += 1
    if ok:
        history["successes"] += 1
        history["consecutive_failures"] = 0
        history["last_success_at"] = completed
    else:
        history["failures"] += 1
        history["consecutive_failures"] += 1
        history["last_error_at"] = completed
    return {
        "name": name,
        "provider_family": _provider_family(name),
        "count": count,
        "ok": bool(ok),
        "error": (str(error)[:180] if error else None),
        "latency_ms": elapsed_ms,
        "completed_at": completed,
        **history,
    }


def _missing_value(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("", "unknown", "n/a", "none", "null")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return not math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            return True
    return False


def _merge_value(current, incoming):
    if _missing_value(current):
        return incoming
    if _missing_value(incoming):
        return current
    if isinstance(current, dict) and isinstance(incoming, dict):
        merged = dict(current)
        for key, value in incoming.items():
            if key not in merged:
                merged[key] = value
            else:
                merged[key] = _merge_value(merged[key], value)
        return merged
    if isinstance(current, list) and isinstance(incoming, list):
        return incoming if not current and incoming else current
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        if isinstance(incoming, (int, float)) and not isinstance(incoming, bool):
            try:
                if float(current) <= 0 < float(incoming):
                    return incoming
            except (TypeError, ValueError, OverflowError):
                pass
    return current


def _merge_candidate(candidate, token):
    """Fill gaps and replace invalid zero-valued market metrics, never erase good data."""
    for key, value in token.items():
        if key in ("tokenAddress", "chainId", "discovery_sources", "discovery_timestamp"):
            continue
        if key not in candidate:
            candidate[key] = value
        else:
            candidate[key] = _merge_value(candidate[key], value)


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
        "independent_provider_count": len(set(_provider_family(name) for name, _ in SCANNERS)),
        "sources": [dict(value) for value in _last_source_stats.values()],
        "last_scan_at": _last_scan_at,
        "last_scan_age_seconds": max(0, round(time.time() - _last_scan_at, 1)) if _last_scan_at else None,
        "max_results": MAX_RESULTS,
        "health_model": "per-feed-success-failure-latency-in-memory",
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
        started_at = {name: time.perf_counter() for name, _ in SCANNERS}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(scanner): name for name, scanner in SCANNERS}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                    if not isinstance(result, list):
                        raise TypeError("scanner returned a non-list payload")
                    source_results[name] = result
                    source_stats[name] = _source_result(
                        name, True, len(result), started_at[name]
                    )
                except Exception as error:
                    print("TOKEN SOURCE WORKER ERROR:", name, repr(error), flush=True)
                    source_results[name] = []
                    source_stats[name] = _source_result(
                        name, False, 0, started_at[name], error
                    )

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
                _merge_candidate(candidate, token)

        results = list(discovered.values())
        # Rank source corroboration by independent provider families, not by
        # the number of feed endpoints one vendor exposes.
        results.sort(key=lambda token: (
            len(set(_provider_family(source) for source in token["discovery_sources"])),
            len(token["discovery_sources"]),
            token["discovery_timestamp"],
        ), reverse=True)
        results = results[:MAX_RESULTS]
        for name, _scanner in SCANNERS:
            source_stats.setdefault(name, _source_result(
                name, False, 0, time.perf_counter(), "SOURCE_NOT_EXECUTED"
            ))
        _last_source_stats = source_stats
        _last_scan_at = current_time
        print("TROJAN V2 TOKEN AGGREGATOR | SOURCES:", len(SCANNERS), "| UNIQUE:", len(results), flush=True)
        for name, _scanner in SCANNERS:
            stat = source_stats[name]
            print("SCANNER:", name, "| FOUND:", stat["count"], "| OK:", stat["ok"],
                  "| LATENCY_MS:", stat["latency_ms"], flush=True)
        return results
    finally:
        _aggregator_lock.release()


if __name__ == "__main__":
    print("TROJAN V2 TOKEN AGGREGATOR READY", flush=True)
    collect_tokens()
