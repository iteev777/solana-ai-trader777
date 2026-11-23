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
import database

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


def _ensure_health_tables():
    with database.connect() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS discovery_source_health (
                source TEXT PRIMARY KEY,
                provider_family TEXT NOT NULL,
                total_runs INTEGER NOT NULL DEFAULT 0,
                successes INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_success_at REAL,
                last_error_at REAL,
                last_count INTEGER NOT NULL DEFAULT 0,
                last_ok INTEGER NOT NULL DEFAULT 0 CHECK(last_ok IN (0, 1)),
                last_error TEXT,
                latency_ms REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS discovery_scan_state (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                last_scan_at REAL,
                token_count INTEGER NOT NULL DEFAULT 0,
                source_count INTEGER NOT NULL DEFAULT 0,
                independent_provider_count INTEGER NOT NULL DEFAULT 0
            )
        """)


def _persist_source_stat(stat):
    try:
        _ensure_health_tables()
        with database.connect() as connection:
            connection.execute("""
                INSERT INTO discovery_source_health (
                    source, provider_family, total_runs, successes, failures,
                    consecutive_failures, last_success_at, last_error_at,
                    last_count, last_ok, last_error, latency_ms, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    provider_family = excluded.provider_family,
                    total_runs = excluded.total_runs,
                    successes = excluded.successes,
                    failures = excluded.failures,
                    consecutive_failures = excluded.consecutive_failures,
                    last_success_at = excluded.last_success_at,
                    last_error_at = excluded.last_error_at,
                    last_count = excluded.last_count,
                    last_ok = excluded.last_ok,
                    last_error = excluded.last_error,
                    latency_ms = excluded.latency_ms,
                    updated_at = excluded.updated_at
            """, (
                stat["name"], stat["provider_family"], stat["total_runs"],
                stat["successes"], stat["failures"], stat["consecutive_failures"],
                stat["last_success_at"], stat["last_error_at"], stat["count"],
                1 if stat["ok"] else 0, stat["error"], stat["latency_ms"],
                stat["completed_at"],
            ))
    except Exception as error:
        print("DISCOVERY HEALTH PERSISTENCE ERROR:", repr(error), flush=True)


def _persist_scan_state(last_scan_at, token_count):
    try:
        _ensure_health_tables()
        with database.connect() as connection:
            connection.execute("""
                INSERT INTO discovery_scan_state (
                    id, last_scan_at, token_count, source_count,
                    independent_provider_count
                ) VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_scan_at = excluded.last_scan_at,
                    token_count = excluded.token_count,
                    source_count = excluded.source_count,
                    independent_provider_count = excluded.independent_provider_count
            """, (
                last_scan_at, token_count, len(SCANNERS),
                len(set(_provider_family(name) for name, _ in SCANNERS)),
            ))
    except Exception as error:
        print("DISCOVERY SCAN STATE PERSISTENCE ERROR:", repr(error), flush=True)


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
    stat = {
        "name": name,
        "provider_family": _provider_family(name),
        "count": count,
        "ok": bool(ok),
        "error": (str(error)[:180] if error else None),
        "latency_ms": elapsed_ms,
        "completed_at": completed,
        **history,
    }
    _persist_source_stat(stat)
    return stat


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
    sources = [dict(value) for value in _last_source_stats.values()]
    last_scan_at = _last_scan_at
    if not sources or last_scan_at is None:
        try:
            _ensure_health_tables()
            with database.connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM discovery_source_health ORDER BY source"
                ).fetchall()
                state = connection.execute(
                    "SELECT last_scan_at FROM discovery_scan_state WHERE id = 1"
                ).fetchone()
            if not sources:
                sources = [dict(row) for row in rows]
            if last_scan_at is None and state is not None:
                last_scan_at = state["last_scan_at"]
        except Exception as error:
            print("DISCOVERY HEALTH READ ERROR:", repr(error), flush=True)
    return {
        "source_count": len(SCANNERS),
        "independent_provider_count": len(set(_provider_family(name) for name, _ in SCANNERS)),
        "sources": sources,
        "last_scan_at": last_scan_at,
        "last_scan_age_seconds": max(0, round(time.time() - last_scan_at, 1)) if last_scan_at else None,
        "max_results": MAX_RESULTS,
        "health_model": "persistent-sqlite-per-feed-success-failure-latency",
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
        _persist_scan_state(current_time, len(results))
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
