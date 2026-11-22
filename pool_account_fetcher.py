"""Batched, coherent Solana pool-account refreshes.

Each pool's pool state, reserves and mint metadata are kept inside one confirmed
RPC context. Account sets are deduplicated across pools and packed into bounded
getMultipleAccounts calls, mirroring a shared reserve-account fetcher.
"""
import threading
import time

MAX_ACCOUNTS_PER_BATCH = 50
ACCOUNT_SET_TTL_SECONDS = 60.0

_lock = threading.RLock()
_pool_account_sets = {}
_pool_account_set_times = {}
_status = {
    "last_refresh_at": None,
    "last_refresh_pools": 0,
    "last_priced_pools": 0,
    "last_failed_pools": 0,
    "last_rpc_batches": 0,
    "last_accounts_requested": 0,
    "total_refreshes": 0,
    "total_rpc_batches": 0,
    "total_failures": 0,
}


def plan_account_batches(pool_account_sets, max_accounts=MAX_ACCOUNTS_PER_BATCH):
    """Greedily pack whole pool bundles without splitting any pool across slots."""
    if isinstance(max_accounts, bool) or not isinstance(max_accounts, int) or max_accounts < 1:
        raise ValueError("max_accounts must be a positive integer")
    batches = []
    current_pools = {}
    current_keys = []
    current_set = set()

    def flush():
        nonlocal current_pools, current_keys, current_set
        if current_pools:
            batches.append({
                "pool_accounts": dict(current_pools),
                "account_keys": list(current_keys),
            })
        current_pools = {}
        current_keys = []
        current_set = set()

    for pool_key, raw_accounts in pool_account_sets.items():
        accounts = list(dict.fromkeys(
            account.strip() for account in raw_accounts
            if isinstance(account, str) and account.strip()
        ))
        if not accounts:
            continue
        if len(accounts) > max_accounts:
            raise ValueError("one pool requires more accounts than a single RPC batch")
        additions = [account for account in accounts if account not in current_set]
        if current_pools and len(current_set) + len(additions) > max_accounts:
            flush()
            additions = accounts
        current_pools[pool_key] = accounts
        for account in additions:
            if account not in current_set:
                current_set.add(account)
                current_keys.append(account)
    flush()
    return batches


def _normalize_pool_key(item):
    if not isinstance(item, (tuple, list)) or len(item) != 3:
        return None
    if not all(isinstance(value, str) and value.strip() for value in item):
        return None
    return tuple(value.strip() for value in item)


def refresh_pools(pool_keys):
    """Refresh a group of pools with deduplicated, bounded RPC account batches."""
    from solana_onchain import (
        decode_pool_snapshot, fetch_pool_account_batch, get_pool_snapshot_accounts
    )

    keys = list(dict.fromkeys(
        key for key in (_normalize_pool_key(item) for item in (pool_keys or []))
        if key is not None
    ))
    now = time.time()
    with _lock:
        active = set(keys)
        for key in list(_pool_account_sets):
            if key not in active:
                _pool_account_sets.pop(key, None)
                _pool_account_set_times.pop(key, None)

    account_sets = {}
    discovery_failures = 0
    for key in keys:
        with _lock:
            cached = _pool_account_sets.get(key)
            cached_at = _pool_account_set_times.get(key, 0.0)
        if cached and now - cached_at < ACCOUNT_SET_TTL_SECONDS:
            account_sets[key] = list(cached)
            continue
        try:
            accounts = get_pool_snapshot_accounts(key[0], key[1], key[2])
            if not accounts:
                raise ValueError("pool returned no snapshot accounts")
            with _lock:
                _pool_account_sets[key] = list(accounts)
                _pool_account_set_times[key] = now
            account_sets[key] = list(accounts)
        except Exception:
            discovery_failures += 1

    batches = plan_account_batches(account_sets)
    priced = 0
    failed = discovery_failures
    requested = 0
    for batch in batches:
        account_keys = batch["account_keys"]
        requested += len(account_keys)
        try:
            account_map, slot, observed_at = fetch_pool_account_batch(account_keys)
        except Exception:
            failed += len(batch["pool_accounts"])
            continue
        for key, required_accounts in batch["pool_accounts"].items():
            subset = {account: account_map.get(account) for account in required_accounts}
            try:
                snapshot = decode_pool_snapshot(
                    key[0], key[1], key[2],
                    _force=True,
                    _register_watch=False,
                    _prefetched_accounts=subset,
                    _prefetched_slot=slot,
                    _prefetched_observed_at=observed_at,
                )
                if not isinstance(snapshot, dict):
                    failed += 1
                    continue
                from onchain_stream import store_pool_snapshot
                store_pool_snapshot(key, snapshot)
                priced += 1
            except Exception:
                failed += 1

    with _lock:
        _status.update({
            "last_refresh_at": time.time(),
            "last_refresh_pools": len(keys),
            "last_priced_pools": priced,
            "last_failed_pools": failed,
            "last_rpc_batches": len(batches),
            "last_accounts_requested": requested,
            "total_refreshes": _status["total_refreshes"] + 1,
            "total_rpc_batches": _status["total_rpc_batches"] + len(batches),
            "total_failures": _status["total_failures"] + failed,
        })
        return dict(_status)


def get_fetcher_status():
    with _lock:
        return dict(_status)


def clear_fetcher_state():
    """Test helper: clear cached account relationships and metrics."""
    with _lock:
        _pool_account_sets.clear()
        _pool_account_set_times.clear()
        for key in list(_status):
            _status[key] = 0 if key.startswith("total_") else None
