"""Shared Solana accountSubscribe feed for live paper-position prices.

One WebSocket connection watches all active position pools. Account notifications
trigger verified read-only RPC snapshots; no signing or transaction broadcast.
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import websocket

_lock = threading.RLock()
_watches = {}
_snapshots = {}
_thread = None
_status = {
    "configured": False, "running": False, "subscriptions": 0,
    "notifications": 0, "refresh_errors": 0, "last_update": None,
    "last_error": None, "last_latency_ms": None
}
_refresh_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="onchain-price")


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


def watch_pool_price(pool_address, protocol, token_mint):
    global _thread
    if not all(isinstance(x, str) and x.strip() for x in (pool_address, protocol, token_mint)):
        return False
    key = (pool_address.strip(), protocol.strip(), token_mint.strip())
    with _lock:
        _watches[key] = True
        _status["configured"] = bool(_rpc_ws_url())
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, name="trojan-onchain-price-stream", daemon=True)
            _thread.start()
    return True


def get_latest_snapshot(pool_address, protocol, token_mint, max_age=1.0):
    key = (pool_address, protocol, token_mint)
    with _lock:
        item = _snapshots.get(key)
        if not item:
            return None
        age = time.time() - item["observed_at"]
        if age > max_age:
            return None
        result = dict(item["data"])
        result["streamAgeMs"] = round(age * 1000, 1)
        return result


def get_stream_status():
    with _lock:
        return {**_status, "configured": bool(_rpc_ws_url()),
                "watched_pools": len(_watches)}


def _refresh(key):
    started = time.perf_counter()
    try:
        from solana_onchain import decode_pool_snapshot
        pool, protocol, mint = key
        snapshot = decode_pool_snapshot(pool, protocol, mint, _force=True, _register_watch=False)
        if not isinstance(snapshot, dict):
            return
        observed = time.time()
        with _lock:
            _snapshots[key] = {"data": snapshot, "observed_at": observed}
            _status["last_update"] = observed
            _status["last_latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            _status["last_error"] = None
    except Exception as error:
        with _lock:
            _status["refresh_errors"] += 1
            _status["last_error"] = str(error)[:200]


def _run():
    delay = 1
    while True:
        url = _rpc_ws_url()
        if not url:
            with _lock:
                _status.update({"running": False, "configured": False,
                                "last_error": "SOLANA_WS_URL or SOLANA_RPC_URL is required"})
            time.sleep(10)
            continue
        ws = None
        try:
            ws = websocket.create_connection(url, timeout=8, enable_multithread=True)
            ws.settimeout(1)
            subscriptions = {}
            sent = {}
            request_id = 1
            with _lock:
                _status.update({"running": True, "configured": True, "last_error": None})
            while True:
                with _lock:
                    current = list(_watches)
                for key in current:
                    if key in sent:
                        continue
                    ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": request_id,
                        "method": "accountSubscribe",
                        "params": [key[0], {"encoding": "base64", "commitment": "processed"}]
                    }))
                    sent[key] = request_id
                    request_id += 1
                try:
                    payload = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue
                if "id" in payload and "result" in payload:
                    key = next((k for k, rid in sent.items() if rid == payload["id"]), None)
                    if key:
                        subscriptions[payload["result"]] = key
                        with _lock:
                            _status["subscriptions"] = len(subscriptions)
                    continue
                params = payload.get("params") or {}
                key = subscriptions.get(params.get("subscription"))
                if key and isinstance(params.get("result"), dict):
                    with _lock:
                        _status["notifications"] += 1
                    _refresh_pool.submit(_refresh, key)
            delay = 1
        except Exception as error:
            with _lock:
                _status.update({"running": False, "subscriptions": 0,
                                "last_error": str(error)[:200]})
            time.sleep(delay)
            delay = min(delay * 2, 30)
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
