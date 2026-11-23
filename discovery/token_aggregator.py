"""Concurrent multi-source Solana token discovery and source health telemetry."""
import os
import time
import json
import math
import re
import threading

from solders.pubkey import Pubkey
from concurrent.futures import ThreadPoolExecutor, as_completed

from discovery.gecko_scanner import scan_new_pools, scan_trending_pools, scan_recently_updated_tokens
from discovery.pumpfun_scanner import scan_pumpfun
from discovery.birdeye_scanner import scan_birdeye
from discovery.dexscreener_scanner import scan_profiles, scan_latest_boosts, scan_top_boosts
from discovery.screenerbot_sources import (
    scan_rugcheck_new, scan_rugcheck_recent, scan_rugcheck_trending,
    scan_rugcheck_verified, scan_jupiter_recent, scan_jupiter_organic,
    scan_jupiter_traded, scan_jupiter_trending, scan_coingecko, scan_defillama,
)
import database

def _env_enabled(name, default=True):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}



def _geckoterminal_scanners_from_env():
    """Expose provider-level and per-feed switches for GeckoTerminal."""
    if not _env_enabled("TROJAN_GECKOTERMINAL_ENABLED", True):
        return []
    configured = [
        ("TROJAN_GECKOTERMINAL_NEW_POOLS_ENABLED", "geckoterminal_new_pools", scan_new_pools),
        ("TROJAN_GECKOTERMINAL_TRENDING_ENABLED", "geckoterminal_trending_pools", scan_trending_pools),
        ("TROJAN_GECKOTERMINAL_RECENTLY_UPDATED_ENABLED", "geckoterminal_recently_updated", scan_recently_updated_tokens),
    ]
    return [(label, scanner) for env_name, label, scanner in configured
            if _env_enabled(env_name, True)]


def _pumpfun_scanners_from_env():
    return [("pumpfun", scan_pumpfun)] if _env_enabled("TROJAN_PUMPFUN_ENABLED", True) else []


def _birdeye_scanners_from_env():
    return [("birdeye", scan_birdeye)] if _env_enabled("TROJAN_BIRDEYE_ENABLED", True) else []


def _dexscreener_scanners_from_env():
    """Mirror ScreenerBot's provider and per-feed discovery switches."""
    if not _env_enabled("TROJAN_DEXSCREENER_ENABLED", True):
        return []
    configured = [
        ("TROJAN_DEXSCREENER_PROFILES_ENABLED", "dexscreener_profiles", scan_profiles),
        ("TROJAN_DEXSCREENER_LATEST_BOOSTS_ENABLED", "dexscreener_latest_boosts", scan_latest_boosts),
        ("TROJAN_DEXSCREENER_TOP_BOOSTS_ENABLED", "dexscreener_top_boosts", scan_top_boosts),
    ]
    return [
        (label, scanner)
        for env_name, label, scanner in configured
        if _env_enabled(env_name, True)
    ]


def _rugcheck_scanners_from_env():
    """Mirror ScreenerBot's independently configurable RugCheck stats feeds."""
    if not _env_enabled("TROJAN_RUGCHECK_ENABLED", True):
        return []
    configured = [
        ("TROJAN_RUGCHECK_NEW_ENABLED", "rugcheck_new", scan_rugcheck_new),
        ("TROJAN_RUGCHECK_RECENT_ENABLED", "rugcheck_recent", scan_rugcheck_recent),
        ("TROJAN_RUGCHECK_TRENDING_ENABLED", "rugcheck_trending", scan_rugcheck_trending),
        ("TROJAN_RUGCHECK_VERIFIED_ENABLED", "rugcheck_verified", scan_rugcheck_verified),
    ]
    return [
        (label, scanner)
        for env_name, label, scanner in configured
        if _env_enabled(env_name, True)
    ]


def _jupiter_scanners_from_env():
    if not _env_enabled("TROJAN_JUPITER_ENABLED", True):
        return []
    configured = [
        ("TROJAN_JUPITER_RECENT_ENABLED", "jupiter_recent", scan_jupiter_recent),
        ("TROJAN_JUPITER_ORGANIC_ENABLED", "jupiter_organic", scan_jupiter_organic),
        ("TROJAN_JUPITER_TRADED_ENABLED", "jupiter_traded", scan_jupiter_traded),
        ("TROJAN_JUPITER_TRENDING_ENABLED", "jupiter_trending", scan_jupiter_trending),
    ]
    return [(label, scanner) for env, label, scanner in configured if _env_enabled(env, True)]


def _coingecko_scanners_from_env():
    if not _env_enabled("TROJAN_COINGECKO_ENABLED", True) or not _env_enabled("TROJAN_COINGECKO_MARKETS_ENABLED", True):
        return []
    return [("coingecko_markets", scan_coingecko)]


def _defillama_scanners_from_env():
    if not _env_enabled("TROJAN_DEFILLAMA_ENABLED", True) or not _env_enabled("TROJAN_DEFILLAMA_PROTOCOLS_ENABLED", True):
        return []
    return [("defillama_protocols", scan_defillama)]

# Source enablement is read at process startup, matching the other provider switches.
SCANNERS = [
    *_geckoterminal_scanners_from_env(),
    *_pumpfun_scanners_from_env(),
    *_birdeye_scanners_from_env(),
    *_dexscreener_scanners_from_env(),
    *_rugcheck_scanners_from_env(),
    *_jupiter_scanners_from_env(),
    *_coingecko_scanners_from_env(),
    *_defillama_scanners_from_env(),
]
MAX_RESULTS = 500
MAX_WORKERS = max(1, len(SCANNERS))
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


def _persist_discovered_candidates(tokens):
    """Persist the deduplicated discovery output independently of the live queue."""
    if not tokens:
        return 0
    now = time.time()
    try:
        with database.connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS discovery_candidates (
                    token_address TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    name TEXT,
                    symbol TEXT,
                    decimals INTEGER,
                    discovery_sources TEXT NOT NULL,
                    discovery_timestamp REAL NOT NULL,
                    first_seen_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    discovery_count INTEGER NOT NULL DEFAULT 1,
                    payload_json TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_discovery_candidates_last_seen ON discovery_candidates(last_seen_at)")
            for token in tokens:
                mint = token["tokenAddress"]
                row = connection.execute(
                    "SELECT discovery_sources, first_seen_at, discovery_count, payload_json FROM discovery_candidates WHERE token_address = ?",
                    (mint,),
                ).fetchone()
                sources = list(token.get("discovery_sources", []))
                first_seen = now
                count = 1
                payload = dict(token)
                if row is not None:
                    try:
                        old_sources = json.loads(row["discovery_sources"] or "[]")
                    except (TypeError, ValueError):
                        old_sources = []
                    for source in old_sources if isinstance(old_sources, list) else []:
                        if isinstance(source, str) and source not in sources:
                            sources.append(source)
                    first_seen = float(row["first_seen_at"] or now)
                    count = int(row["discovery_count"] or 0) + 1
                    try:
                        old_payload = json.loads(row["payload_json"] or "{}")
                    except (TypeError, ValueError):
                        old_payload = {}
                    if isinstance(old_payload, dict):
                        for key, value in old_payload.items():
                            if key not in payload or _missing_value(payload.get(key)):
                                payload[key] = value
                payload["discovery_sources"] = sources
                connection.execute("""
                    INSERT INTO discovery_candidates (
                        token_address, chain_id, name, symbol, decimals,
                        discovery_sources, discovery_timestamp, first_seen_at,
                        last_seen_at, discovery_count, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(token_address) DO UPDATE SET
                        chain_id = excluded.chain_id,
                        name = COALESCE(NULLIF(excluded.name, 'Unknown'), discovery_candidates.name),
                        symbol = COALESCE(NULLIF(excluded.symbol, 'Unknown'), discovery_candidates.symbol),
                        decimals = COALESCE(excluded.decimals, discovery_candidates.decimals),
                        discovery_sources = excluded.discovery_sources,
                        discovery_timestamp = excluded.discovery_timestamp,
                        last_seen_at = excluded.last_seen_at,
                        discovery_count = excluded.discovery_count,
                        payload_json = excluded.payload_json
                """, (
                    mint, token.get("chainId", "solana"), token.get("name"),
                    token.get("symbol"), token.get("decimals"),
                    json.dumps(sources, separators=(",", ":")),
                    float(token.get("discovery_timestamp") or now), first_seen,
                    now, count, json.dumps(payload, separators=(",", ":"), default=str),
                ))
        return len(tokens)
    except Exception as error:
        print("DISCOVERY CANDIDATE PERSISTENCE ERROR:", repr(error), flush=True)
        return 0

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


# Match ScreenerBot's chain-adapter validation for Solana discovery:
# base58 account shape, followed by exclusion of native/quote assets.
BASE58_MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
SOLANA_NON_DISCOVERY_ASSETS = {
    "So11111111111111111111111111111111111111112",  # WSOL / native SOL
    "11111111111111111111111111111111",             # System Program alias
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", # USDT
}


def normalize_token(token):
    """Return only valid, non-quote Solana token mints, like ScreenerBot's adapter."""
    if not isinstance(token, dict):
        return None
    address = token.get("tokenAddress")
    chain = token.get("chainId")
    if not isinstance(address, str) or not isinstance(chain, str):
        return None
    address = address.strip()
    if chain.lower().strip() != "solana":
        return None
    if not BASE58_MINT_RE.fullmatch(address):
        return None
    try:
        if len(bytes(Pubkey.from_string(address))) != 32:
            return None
    except (ValueError, TypeError):
        return None
    if address in SOLANA_NON_DISCOVERY_ASSETS:
        return None
    return address


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
                sources = []
                for row in rows:
                    item = dict(row)
                    item["name"] = item.get("source")
                    item["count"] = item.get("last_count", 0)
                    item["ok"] = bool(item.get("last_ok", 0))
                    item["completed_at"] = item.get("updated_at")
                    sources.append(item)
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
    if not _env_enabled("TROJAN_DISCOVERY_ENABLED", True):
        print("TROJAN V2 DISCOVERY DISABLED BY CONFIGURATION", flush=True)
        return []
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
        persisted_candidates = _persist_discovered_candidates(results)
        print("DISCOVERY CANDIDATES PERSISTED:", persisted_candidates, flush=True)
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
