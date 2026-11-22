"""Shared Solana accountSubscribe feed for active candidate and position pool prices.

Pool state and reserve vault accounts are subscribed together. Candidate watches
expire automatically; open-position watches remain until the position monitor
removes them. All RPC reads remain read-only.
"""
import json
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import websocket

_lock = threading.RLock()
# Position watches are authoritative and persist until sync_watched_pools replaces them.
_watches = {}
# Discovery candidates are a bounded, expiring LRU so the subscription set cannot grow forever.
_candidate_watches = OrderedDict()
_snapshots = {}
_refreshing = set()
_last_refresh_at = {}
MIN_POOL_REFRESH_INTERVAL = max(0.25, float(os.getenv("TROJAN_POOL_REFRESH_INTERVAL", "0.5")))
POOL_BATCH_POLL_INTERVAL = max(0.5, float(os.getenv("TROJAN_POOL_BATCH_POLL_INTERVAL", "1.5")))
CANDIDATE_WATCH_TTL = max(30.0, float(os.getenv("TROJAN_CANDIDATE_WATCH_TTL", "300")))
MAX_CANDIDATE_WATCHES = max(10, int(os.getenv("TROJAN_MAX_CANDIDATE_WATCHES", "100")))
_thread = None
_status = {
    "configured": False, "running": False, "subscriptions": 0,
    "notifications": 0, "coalesced_notifications": 0, "refresh_errors": 0,
    "last_update": None, "last_error": None, "last_latency_ms": None
}
_refresh_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="onchain-price")
_batch_refresh_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pool-account-batch")
_dirty_pools = set()
_batch_refreshing = False
_batch_status = {"last_refresh_pools": 0, "last_priced_pools": 0,
                 "last_failed_pools": 0, "last_rpc_batches": 0,
                 "last_accounts_requested": 0}


def _rpc_ws_url():
    explicit = os.getenv("SOLANA_WS_URL", "").strip()
    if explicit:
        return explicit
    rpc = os.getenv("SOLANA_RPC_URL", "").strip()
    if rpc.startswith("https://"):
        return "wss://" + rpc[len("https://"):]
    if rpc.startswith("http://"):
        return "ws://" + rpc[len("http://"):]
    return ""


def _remove_snapshot_if_unwatched_locked(key):
    if key not in _watches and key not in _candidate_watches:
        _snapshots.pop(key, None)
        _last_refresh_at.pop(key, None)
        _dirty_pools.discard(key)


def _prune_candidate_watches_locked(now=None):
    now = time.time() if now is None else float(now)
    expired = [
        key for key, seen_at in _candidate_watches.items()
        if now - seen_at > CANDIDATE_WATCH_TTL
    ]
    for key in expired:
        _candidate_watches.pop(key, None)
        _remove_snapshot_if_unwatched_locked(key)
    while len(_candidate_watches) > MAX_CANDIDATE_WATCHES:
        key, _ = _candidate_watches.popitem(last=False)
        _remove_snapshot_if_unwatched_locked(key)
    return len(expired)


def _active_watch_keys_locked():
    _prune_candidate_watches_locked()
    return set(_watches) | set(_candidate_watches)


def _ensure_thread_locked():
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(
            target=_run, name="trojan-onchain-price-stream", daemon=True
        )
        _thread.start()


def sync_watched_pools(items):
    """Replace the open-position watch set without deleting discovery candidates."""
    normalized = set()
    for item in items or []:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            continue
        if not all(isinstance(x, str) and x.strip() for x in item):
            continue
        normalized.add(tuple(x.strip() for x in item))
    with _lock:
        removed = set(_watches) - normalized
        _watches.clear()
        _watches.update({key: True for key in normalized})
        for key in removed:
            _remove_snapshot_if_unwatched_locked(key)
        _status["configured"] = bool(_rpc_ws_url())
        if _active_watch_keys_locked():
            _ensure_thread_locked()
    return len(normalized)


def watch_pool_price(pool_address, protocol, token_mint):
    """Add or refresh a short-lived discovery-candidate watch."""
    if not all(isinstance(x, str) and x.strip()
               for x in (pool_address, protocol, token_mint)):
        return False
    key = (pool_address.strip(), protocol.strip(), token_mint.strip())
    with _lock:
        _candidate_watches[key] = time.time()
        _candidate_watches.move_to_end(key)
        _prune_candidate_watches_locked()
        _status["configured"] = bool(_rpc_ws_url())
        _ensure_thread_locked()
    return True


def get_watched_pools():
    with _lock:
        return sorted(_active_watch_keys_locked())


def get_latest_snapshot(pool_address, protocol, token_mint, max_age=1.0):
    key = (pool_address, protocol, token_mint)
    with _lock:
        item = _snapshots.get(key)
        if not item:
            return None
        age = time.time() - item["observed_at"]
        if age < 0 or age > max_age:
            return None
        result = dict(item["data"])
        result["streamAgeMs"] = round(age * 1000, 1)
        result["snapshotAgeSeconds"] = round(age, 3)
        return result


def get_stream_status():
    with _lock:
        _prune_candidate_watches_locked()
        return {
            **_status,
            **_batch_status,
            "batch_poll_interval_seconds": POOL_BATCH_POLL_INTERVAL,
            "configured": bool(_rpc_ws_url()),
            "watched_pools": len(set(_watches) | set(_candidate_watches)),
            "position_watches": len(_watches),
            "candidate_watches": len(_candidate_watches),
            "candidate_watch_ttl_seconds": CANDIDATE_WATCH_TTL,
            "max_candidate_watches": MAX_CANDIDATE_WATCHES,
        }


def should_refresh_pool(key, now=None):
    """Coalesce notification bursts to at most one snapshot refresh per interval."""
    now = time.monotonic() if now is None else float(now)
    with _lock:
        last = _last_refresh_at.get(key, 0.0)
        if key in _refreshing or now - last < MIN_POOL_REFRESH_INTERVAL:
            _status["coalesced_notifications"] += 1
            return False
        _refreshing.add(key)
        _last_refresh_at[key] = now
        return True


def store_pool_snapshot(key, snapshot):
    """Publish a verified pool snapshot for WebSocket and dashboard readers."""
    if not isinstance(key, (tuple, list)) or len(key) != 3 or not isinstance(snapshot, dict):
        return False
    observed = snapshot.get("observedAt")
    if not isinstance(observed, (int, float)) or observed <= 0:
        observed = time.time()
    normalized_key = tuple(key)
    with _lock:
        _snapshots[normalized_key] = {"data": dict(snapshot), "observed_at": observed}
        _status["last_update"] = observed
        _status["last_error"] = None
    return True


def _refresh(key):
    started = time.perf_counter()
    try:
        from solana_onchain import decode_pool_snapshot
        pool, protocol, mint = key
        snapshot = decode_pool_snapshot(
            pool, protocol, mint, _force=True, _register_watch=False
        )
        if isinstance(snapshot, dict):
            store_pool_snapshot(key, snapshot)
            with _lock:
                _status["last_latency_ms"] = round(
                    (time.perf_counter() - started) * 1000, 1
                )
    except Exception as error:
        with _lock:
            _status["refresh_errors"] += 1
            _status["last_error"] = str(error)[:200]
    finally:
        with _lock:
            _refreshing.discard(key)


def _refresh_many(keys):
    global _batch_refreshing
    started = time.perf_counter()
    try:
        import pool_account_fetcher
        result = pool_account_fetcher.refresh_pools(keys)
        with _lock:
            for name in _batch_status:
                _batch_status[name] = result.get(name, _batch_status[name])
            _status["last_latency_ms"] = round(
                (time.perf_counter() - started) * 1000, 1
            )
            _status["last_error"] = None if result.get("last_failed_pools", 0) == 0 else (
                "BATCH POOL REFRESH HAD " + str(result.get("last_failed_pools")) + " FAILURES"
            )
    except Exception as error:
        with _lock:
            _status["refresh_errors"] += 1
            _status["last_error"] = "BATCH POOL REFRESH: " + str(error)[:180]
    finally:
        with _lock:
            _batch_refreshing = False


def _run():
    delay = 1
    while True:
        url = _rpc_ws_url()
        if not url:
            with _lock:
                _status.update({
                    "running": False, "configured": False,
                    "last_error": "SOLANA_WS_URL or SOLANA_RPC_URL is required"
                })
            time.sleep(10)
            continue
        ws = None
        try:
            ws = websocket.create_connection(url, timeout=8, enable_multithread=True)
            ws.settimeout(1)
            # subscription id -> (pool key, account); request id -> same tuple.
            subscriptions = {}
            sent = {}
            account_cache = {}
            subscription_retry_after = {}
            request_id = 1
            next_batch_poll = 0.0
            with _lock:
                _status.update({"running": True, "configured": True, "last_error": None})
            while True:
                with _lock:
                    current = _active_watch_keys_locked()
                    now_mono = time.monotonic()
                    poll_due = bool(current) and now_mono >= next_batch_poll
                    batch_keys = None
                    if current and not _batch_refreshing and (_dirty_pools or poll_due):
                        batch_keys = sorted(set(current) if poll_due else set(_dirty_pools))
                        _dirty_pools.difference_update(batch_keys)
                        _batch_refreshing = True
                        next_batch_poll = now_mono + POOL_BATCH_POLL_INTERVAL
                if batch_keys:
                    _batch_refresh_pool.submit(_refresh_many, batch_keys)

                # Remove every pool's state and reserve subscriptions when it leaves
                # both the position set and the candidate LRU.
                removed_keys = set(account_cache) - current
                for key in removed_keys:
                    account_cache.pop(key, None)
                for pair in list(subscription_retry_after):
                    if pair[0] not in current:
                        subscription_retry_after.pop(pair, None)
                for pair in list(sent):
                    if pair[0] not in current:
                        subscription_id = next(
                            (sid for sid, active_pair in subscriptions.items()
                             if active_pair == pair), None
                        )
                        if subscription_id is not None:
                            ws.send(json.dumps({
                                "jsonrpc": "2.0", "id": request_id,
                                "method": "accountUnsubscribe",
                                "params": [subscription_id]
                            }))
                            request_id += 1
                            subscriptions.pop(subscription_id, None)
                        sent.pop(pair, None)
                with _lock:
                    _status["subscriptions"] = len(subscriptions)

                # Discover each pool's actual reserve accounts once per connection.
                from solana_onchain import get_pool_watch_accounts
                for key in current:
                    if key not in account_cache:
                        try:
                            accounts = get_pool_watch_accounts(key[0], key[1], key[2])
                            if not accounts:
                                raise ValueError("pool returned no watchable accounts")
                            account_cache[key] = accounts
                        except Exception as error:
                            with _lock:
                                _status["last_error"] = (
                                    "POOL WATCH ACCOUNT DISCOVERY: " + str(error)[:150]
                                )
                            continue
                    for account in account_cache.get(key, []):
                        pair = (key, account)
                        if pair in sent or time.monotonic() < subscription_retry_after.get(pair, 0.0):
                            continue
                        ws.send(json.dumps({
                            "jsonrpc": "2.0", "id": request_id,
                            "method": "accountSubscribe",
                            "params": [account, {
                                "encoding": "base64", "commitment": "processed"
                            }]
                        }))
                        sent[pair] = request_id
                        request_id += 1

                try:
                    payload = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue

                if "id" in payload:
                    pair = next(
                        (item for item, rid in sent.items() if rid == payload["id"]),
                        None
                    )
                    if pair and "result" in payload:
                        subscriptions[payload["result"]] = pair
                        subscription_retry_after.pop(pair, None)
                    elif pair and "error" in payload:
                        sent.pop(pair, None)
                        subscription_retry_after[pair] = time.monotonic() + 5.0
                        with _lock:
                            _status["last_error"] = (
                                "ACCOUNT SUBSCRIPTION REJECTED: "
                                + str(payload["error"])[:150]
                            )
                    with _lock:
                        _status["subscriptions"] = len(subscriptions)
                    continue

                params = payload.get("params") or {}
                pair = subscriptions.get(params.get("subscription"))
                if pair and isinstance(params.get("result"), dict):
                    key = pair[0]
                    with _lock:
                        _status["notifications"] += 1
                        if key in _dirty_pools:
                            _status["coalesced_notifications"] += 1
                        _dirty_pools.add(key)
            delay = 1
        except Exception as error:
            with _lock:
                _status.update({
                    "running": False, "subscriptions": 0,
                    "last_error": str(error)[:200]
                })
            time.sleep(delay)
            delay = min(delay * 2, 30)
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
