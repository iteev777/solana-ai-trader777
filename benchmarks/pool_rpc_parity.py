#!/usr/bin/env python3
"""Read-only pool RPC parity benchmark against ScreenerBot's Rust fetch model.

Uses eight real Raydium CPMM pools listed in ScreenerBot's public dashboard
fixtures. The same derived account-key set is passed to both clients. No
wallet keys, signing, transactions, or trading are used.
"""
import json
import os
import statistics
import sys
import time

# Running a script by path puts benchmarks/ at sys.path[0]; add repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not os.getenv("SOLANA_RPC_URL", "").strip():
    os.environ["SOLANA_RPC_URL"] = "https://api.mainnet-beta.solana.com"
os.environ.setdefault("SOLANA_RPC_MIN_INTERVAL", "0.0")
os.environ.setdefault("SOLANA_RPC_TIMEOUT", "12")

DEX_PROTOCOLS = {
    "raydium": "raydium_auto",
    "raydium-cpmm": "raydium_cpmm",
    "raydium-clmm": "raydium_clmm",
    "orca": "orca_whirlpool",
    "meteora": "meteora_dlmm",
    "meteora-dlmm": "meteora_dlmm",
    "meteora-damm": "meteora_damm_v2",
    "meteora-dbc": "meteora_dbc",
    "pumpswap": "pumpswap",
    "pumpfun": "pumpfun_legacy",
    "moonit": "moonit_amm",
    "fluxbeam": "fluxbeam_amm",
}
SOL_SYMBOLS = {"SOL", "WSOL"}
ROUNDS = 12
INTERVAL_SECONDS = 0.5


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * pct + 0.999999)))
    return round(ordered[index], 2)


def stats(values):
    return {
        "samples": len(values),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "mean_ms": round(statistics.mean(values), 2) if values else None,
        "min_ms": round(min(values), 2) if values else None,
        "max_ms": round(max(values), 2) if values else None,
    }


def main():
    import requests
    from solana_onchain import get_pool_snapshot_accounts, fetch_pool_account_batch
    import pool_account_fetcher

    # Use live DexScreener discovery rather than synthetic fixture addresses.
    discovery = requests.get(
        "https://api.dexscreener.com/latest/dex/search",
        params={"q": "SOL"},
        timeout=15,
    )
    discovery.raise_for_status()
    pairs = discovery.json().get("pairs") or []
    candidates = []
    for pair in pairs:
        if pair.get("chainId") != "solana":
            continue
        protocol = DEX_PROTOCOLS.get((pair.get("dexId") or "").lower())
        if not protocol:
            continue
        base = pair.get("baseToken") or {}
        quote = pair.get("quoteToken") or {}
        base_symbol = (base.get("symbol") or "").upper()
        quote_symbol = (quote.get("symbol") or "").upper()
        if quote_symbol in SOL_SYMBOLS:
            mint = base.get("address")
        elif base_symbol in SOL_SYMBOLS:
            mint = quote.get("address")
        else:
            continue
        pool_address = pair.get("pairAddress")
        liquidity = (pair.get("liquidity") or {}).get("usd") or 0
        if pool_address and mint and mint != "So11111111111111111111111111111111111111112":
            candidates.append((float(liquidity), (pool_address, protocol, mint)))
    candidates.sort(key=lambda item: item[0], reverse=True)

    active_pools = []
    account_keys = []
    errors = []
    seen = set()
    for liquidity, pool in candidates:
        if pool[0] in seen:
            continue
        seen.add(pool[0])
        try:
            keys = get_pool_snapshot_accounts(*pool)
            active_pools.append(pool)
            account_keys.extend(keys)
        except Exception as exc:
            errors.append({"pool": pool[0], "protocol": pool[1],
                           "error": type(exc).__name__ + ": " + str(exc)[:160]})
        if len(active_pools) >= 8:
            break

    account_keys = list(dict.fromkeys(account_keys))
    # Keep the common workload within ScreenerBot's documented 50-account batch.
    while len(account_keys) > 50 and len(active_pools) > 1:
        active_pools.pop()
        account_keys = []
        for pool in active_pools:
            account_keys.extend(get_pool_snapshot_accounts(*pool))
        account_keys = list(dict.fromkeys(account_keys))
    if not active_pools or not account_keys:
        print(json.dumps({"benchmark": "pool-discovery-failed",
                          "dexscreener_candidates": len(candidates),
                          "pool_discovery_errors": errors[:12]}, indent=2))
        raise RuntimeError("No valid live benchmark pools were discovered from mainnet RPC")
    if len(account_keys) > 50:
        raise RuntimeError("Common workload exceeds the 50-account comparison batch")

    with open("benchmark_accounts.json", "w", encoding="utf-8") as handle:
        json.dump({"account_keys": account_keys}, handle)

    # Python raw-RPC transport: same getMultipleAccounts workload as Rust below.
    raw_times = []
    raw_success = 0
    raw_errors = []
    for index in range(ROUNDS + 2):
        started = time.perf_counter()
        try:
            account_map, slot, observed_at = fetch_pool_account_batch(account_keys)
            elapsed = (time.perf_counter() - started) * 1000
            present = sum(value is not None for value in account_map.values())
            if present == len(account_keys) and slot > 0:
                raw_success += 1
            if index >= 2:
                raw_times.append(elapsed)
        except Exception as exc:
            raw_errors.append(type(exc).__name__ + ": " + str(exc)[:160])
        if index < ROUNDS + 1:
            time.sleep(INTERVAL_SECONDS)

    # Trojan end-to-end pool path: account discovery/cache, batched fetch,
    # verified snapshot decode and snapshot publication.
    full_times = []
    full_success = []
    full_failures = []
    for index in range(ROUNDS + 1):
        started = time.perf_counter()
        result = pool_account_fetcher.refresh_pools(active_pools)
        elapsed = (time.perf_counter() - started) * 1000
        if index > 0:
            full_times.append(elapsed)
            full_success.append(int(result.get("last_priced_pools", 0) or 0))
            full_failures.append(int(result.get("last_failed_pools", 0) or 0))
        time.sleep(INTERVAL_SECONDS)

    output = {
        "benchmark": "trojan-vs-screenerbot-rpc-pool-parity",
        "rpc_host": __import__("urllib.parse", fromlist=["urlparse"]).urlparse(os.environ["SOLANA_RPC_URL"]).hostname,
        "commit": os.getenv("GITHUB_SHA"),
        "pool_protocol_scope": "live SOL-quoted pools from supported DEXes",
        "pool_count": len(active_pools),
        "protocols": sorted({pool[1] for pool in active_pools}),
        "account_count": len(account_keys),
        "batch_limit_accounts": 50,
        "rounds": ROUNDS,
        "interval_ms": int(INTERVAL_SECONDS * 1000),
        "trojan_python_getMultipleAccounts": {
            **stats(raw_times),
            "successful_full_account_responses": raw_success,
            "attempts_including_warmups": ROUNDS + 2,
            "errors": raw_errors[:5],
        },
        "trojan_full_pool_refresh": {
            **stats(full_times),
            "priced_pools_per_round": full_success,
            "failed_pools_per_round": full_failures,
        },
        "pool_discovery_errors": errors[:8],
        "account_keys_file": "benchmark_accounts.json",
        "note": "Rust result is emitted by the paired ScreenerBot-compatible solana-client benchmark. RPC transport timings are comparable; Trojan full-refresh timings include Python decoding/publication and are not directly comparable to raw Rust RPC timings.",
    }
    with open("python-pool-benchmark.json", "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2)
    print(json.dumps(output, indent=2))
    if not raw_times:
        raise RuntimeError("Python raw RPC benchmark produced no successful timed samples")


if __name__ == "__main__":
    main()
