"""Read-only Solana wallet activity monitor for paper research and Telegram alerts.

Tracks up to 20 public wallet addresses. It never signs, submits or copies
transactions with real funds.
"""
import json
import os
import threading
import math
import time
import requests
import websocket

import database
from telegram_alerts import send_alert
from copy_event_parser import parse_wallet_transaction
import copy_freshness

_lock = threading.Lock()
_thread = None
_stop_event = threading.Event()
_active_ws = None
# In-memory paging state is intentionally bounded. The durable cursor is advanced
# only after the complete catch-up range has been replayed oldest-first.
_poll_catchup = {}
_COPY_SIGNATURE_PAGE_SIZE = 100
_COPY_MAX_PAGES_PER_POLL = 5
_COPY_MAX_CATCHUP_SIGNATURES = 5000
_COPY_MAX_CATCHUP_POLLS = 20
_status = {
    "running": False, "configured": False, "wallet_count": 0,
    "last_event": None, "last_error": None, "events_seen": 0,
    "polls": 0, "last_poll": None, "last_poll_error": None,
    "stale_events_observed": 0, "catchup_skips": 0,
    "skipped_signatures": 0
}


def _environment_wallets():
    return list(dict.fromkeys(
        item.strip() for item in os.getenv("TROJAN_COPY_WALLETS", "").split(",")
        if item.strip()
    ))[:20]


def _wallets():
    configured = _environment_wallets()
    try:
        stored = [item["wallet"] for item in database.list_copy_watch_wallets()
                  if item.get("enabled") and item.get("wallet")]
    except Exception as error:
        _status["last_error"] = "watchlist database: " + type(error).__name__
        stored = []
    return list(dict.fromkeys(configured + stored))[:20]


def _validate_wallet_address(wallet):
    if not isinstance(wallet, str) or not wallet.strip():
        raise ValueError("Wallet address is required")
    address = wallet.strip()
    try:
        from solana_onchain import _b58decode
        decoded = _b58decode(address)
    except Exception as error:
        raise ValueError("Wallet address must be valid base58") from error
    if len(decoded) != 32:
        raise ValueError("Wallet address must decode to 32 bytes")
    return address


def get_copy_watchlist():
    env = _environment_wallets()
    result = [
        {"wallet": wallet, "label": "Environment variable", "enabled": True, "source": "environment"}
        for wallet in env
    ]
    for item in database.list_copy_watch_wallets():
        if item["wallet"] not in env:
            result.append({**item, "source": "dashboard"})
    return {"wallets": result, "active_count": len(_wallets()), "maximum": 20,
            "monitor": get_copy_monitor_status()}


def add_copy_watch_wallet(wallet, label=None):
    address = _validate_wallet_address(wallet)
    if address in _environment_wallets():
        raise ValueError("This wallet is already configured by TROJAN_COPY_WALLETS")
    current = database.list_copy_watch_wallets()
    existing = next((item for item in current if item["wallet"] == address), None)
    if (not existing or not existing.get("enabled")) and len(_wallets()) >= 20:
        raise ValueError("Maximum of 20 copy-watch wallets reached")
    return database.upsert_copy_watch_wallet(address, label=label, enabled=True)


def remove_copy_watch_wallet(wallet):
    address = _validate_wallet_address(wallet)
    if address in _environment_wallets():
        raise ValueError("environment-configured wallets must be removed from TROJAN_COPY_WALLETS")
    return database.delete_copy_watch_wallet(address)


def _rpc_url():
    return os.getenv("SOLANA_RPC_URL", "").strip()


def _ws_url():
    explicit = os.getenv("SOLANA_WS_URL", "").strip()
    if explicit:
        return explicit
    rpc = _rpc_url()
    if rpc.startswith("https://"):
        return "wss://" + rpc[len("https://"):]
    if rpc.startswith("http://"):
        return "ws://" + rpc[len("http://"):]
    return ""


def _fetch_wallet_transaction(signature, wallet):
    response = requests.post(
        _rpc_url(),
        json={
            "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
            "params": [signature, {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
                "commitment": "confirmed"
            }]
        },
        timeout=10
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(str(payload["error"])[:200])
    tx = payload.get("result")
    if tx is None:
        # Solana RPC can briefly return null while a confirmed transaction is
        # still being indexed. Treat it as retryable so the durable watch cursor
        # cannot move past a signature whose details were not yet available.
        raise RuntimeError("copy transaction is not indexed yet")
    if not isinstance(tx, dict):
        raise ValueError("copy transaction RPC returned an invalid result shape")
    return parse_wallet_transaction(
        tx, wallet, signature=signature, received_at=time.time()
    )


def _signature_page_size():
    try:
        configured = int(os.getenv("TROJAN_COPY_POLL_SIGNATURE_LIMIT", "100"))
    except (TypeError, ValueError, OverflowError):
        configured = 100
    return max(1, min(configured, 1000))


def _fetch_wallet_signatures(wallet, limit=None, before=None, until=None):
    """Fetch one bounded Solana signature page, newest first, with resumable bounds."""
    maximum = _signature_page_size()
    if limit is None:
        page_limit = maximum
    elif isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
        raise ValueError("signature page limit is outside the configured bound")
    else:
        page_limit = limit
    options = {"limit": page_limit, "commitment": "confirmed"}
    for key, value in (("before", before), ("until", until)):
        if value is not None:
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError("signature " + key + " cursor is invalid")
            options[key] = value.strip()
    response = requests.post(
        _rpc_url(),
        json={
            "jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
            "params": [wallet, options]
        },
        timeout=10
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("error"):
        error = payload.get("error") if isinstance(payload, dict) else "invalid JSON-RPC response"
        raise RuntimeError("signature page RPC failed: " + str(error)[:160])
    result = payload.get("result")
    if not isinstance(result, list):
        raise ValueError("signature page must be a list")
    rows = []
    for item in result:
        if not isinstance(item, dict) or not isinstance(item.get("signature"), str):
            continue
        signature = item["signature"].strip()
        if signature and len(signature) <= 128:
            rows.append({**item, "signature": signature})
    return rows


def _process_wallet_signature(wallet, signature):
    """Persist a deduplicated wallet event; keep cursor retryable under backpressure."""
    event = _fetch_wallet_transaction(signature, wallet)
    if not isinstance(event, dict):
        # A structurally valid transaction that cannot be classified is a
        # terminal non-signal; the RPC-null case raises above and remains retryable.
        return True
    inserted = database.record_copy_trade_event(
        event["wallet"], event["signature"], event["action"],
        event["token_mint"], event["token_delta"], event["sol_delta"],
        json.dumps({
            "logs": event["logs"],
            "classification_reason": event.get("classification_reason"),
            "economic_sol_delta": event.get("economic_sol_delta"),
            "quote_delta": event.get("quote_delta"),
            "invoked_programs": event.get("invoked_programs", [])
        }),
        asset_deltas=event.get("asset_deltas", []),
    )
    if inserted:
        _status["events_seen"] += 1
        _status["last_event"] = time.time()
    freshness = copy_freshness.evaluate_event_freshness(
        event.get("block_time"), received_at=event.get("received_at")
    )
    if not freshness.get("allowed"):
        if inserted:
            _status["stale_events_observed"] += 1
        return True
    if inserted:
        send_alert(
            "COPY WATCH " + event["action"]
            + "\nWallet: " + wallet
            + "\nMint: " + event["token_mint"]
            + "\nToken delta: " + str(round(event["token_delta"], 6))
            + "\nSOL delta: " + (
                str(round(event["sol_delta"], 6))
                if event["sol_delta"] is not None else "n/a"
            )
            + "\nClassification: " + event.get("classification_reason", "unknown")
            + "\nSignature: " + signature
        )
    if event["action"] in ("BUY", "SELL") and os.getenv("TROJAN_COPY_PAPER_ENABLED", "false").lower() == "true":
        try:
            import main
            queued = main.queue_copy_signal(event)
            if not queued:
                # Keep the durable wallet cursor behind this signature. The next
                # poll retries it even though copy_trade_events already deduped it.
                raise RuntimeError("paper copy signal queue is full")
        except Exception as copy_error:
            _status["last_error"] = "paper copy queue: " + str(copy_error)[:150]
            raise RuntimeError("paper copy dispatch is pending: " + str(copy_error)[:120]) from copy_error
    return True


def _skip_catchup_to_head(wallet, state):
    """Move a persistently oversized backlog to its newest known signature.

    Old transactions are intentionally not replayed as copy signals. The cursor
    is advanced only after the durable write succeeds, and the skipped count is
    exposed in monitor status for operator visibility.
    """
    newest = state.get("newest_signature") if isinstance(state, dict) else None
    if not isinstance(newest, str) or not newest:
        raise RuntimeError("cannot skip wallet catch-up without a known head signature")
    skipped = len(state.get("newest_first", []))
    database.set_copy_watch_cursor(wallet, newest)
    _status["catchup_skips"] = int(_status.get("catchup_skips", 0)) + 1
    _status["skipped_signatures"] = int(_status.get("skipped_signatures", 0)) + skipped
    _poll_catchup.pop(wallet, None)
    print(
        "COPY WATCH CATCH-UP SKIPPED:",
        wallet[:8], "| SIGNATURES:", skipped, "| CURSOR:", newest,
        flush=True,
    )
    return skipped


def _poll_wallet_activity(wallets):
    """Recover WS gaps with bounded paging and a durable per-wallet cursor.

    A range is accumulated newest-first until its old durable boundary is reached.
    Only then is it replayed oldest-first and the durable cursor advanced. A process
    restart may refetch an unfinished range, but event-signature dedupe makes replay
    safe and prevents an incomplete page from skipping unseen transactions.
    """
    processed = 0
    errors = []
    active_wallets = set(wallets)
    for stale_wallet in list(_poll_catchup):
        if stale_wallet not in active_wallets:
            _poll_catchup.pop(stale_wallet, None)
    for wallet in wallets:
        try:
            cursor = database.get_copy_watch_cursor(wallet)
            state = _poll_catchup.get(wallet)
            if state is None or state.get("until") != cursor:
                state = {
                    "until": cursor, "before": None, "newest_signature": None,
                    "newest_first": [], "seen": set(), "complete": False,
                    "initial_window": cursor is None, "polls": 0,
                }
                _poll_catchup[wallet] = state

            if not state["complete"]:
                page_size = _signature_page_size()
                for _ in range(_COPY_MAX_PAGES_PER_POLL):
                    page = _fetch_wallet_signatures(
                        wallet, limit=page_size,
                        before=state["before"], until=state["until"]
                    )
                    if not page:
                        state["complete"] = True
                        break
                    if state["newest_signature"] is None:
                        state["newest_signature"] = page[0]["signature"]
                    previous_before = state["before"]
                    page_last = page[-1]["signature"]
                    new_items = [
                        item for item in page
                        if item["signature"] not in state["seen"]
                    ]
                    if len(state["newest_first"]) + len(new_items) > _COPY_MAX_CATCHUP_SIGNATURES:
                        state["overflowed"] = True
                        break
                    for item in new_items:
                        state["seen"].add(item["signature"])
                        state["newest_first"].append(item)
                    added = len(new_items)
                    if added == 0 or (previous_before is not None and page_last == previous_before):
                        raise RuntimeError("signature pagination made no forward progress")
                    state["before"] = page_last

                    # First observation deliberately establishes a recent bounded
                    # baseline; it does not replay the wallet's entire lifetime.
                    if state["initial_window"] or len(page) < page_size:
                        state["complete"] = True
                        break
                    if len(state["newest_first"]) >= _COPY_MAX_CATCHUP_SIGNATURES:
                        state["overflowed"] = True
                        break

            if not state["complete"]:
                state["polls"] = int(state.get("polls", 0)) + 1
                if (
                    state.get("overflowed")
                    or len(state["newest_first"]) >= _COPY_MAX_CATCHUP_SIGNATURES
                    or state["polls"] >= _COPY_MAX_CATCHUP_POLLS
                ):
                    _skip_catchup_to_head(wallet, state)
                continue

            # Every signature in the fetched range has been collected. Failed
            # transactions are intentionally skipped, but still lie behind the
            # cursor boundary and therefore must not block forward progress.
            for item in reversed(state["newest_first"]):
                if item.get("err") is not None:
                    continue
                if _process_wallet_signature(wallet, item["signature"]):
                    processed += 1

            newest = state.get("newest_signature")
            if newest:
                database.set_copy_watch_cursor(wallet, newest)
            _poll_catchup.pop(wallet, None)

        except Exception as error:
            errors.append(wallet[:8] + ":" + (str(error) or type(error).__name__)[:100])

    _status["polls"] += 1
    _status["last_poll"] = time.time()
    _status["last_poll_error"] = "; ".join(errors[:5]) if errors else None
    if errors:
        _status["last_error"] = "wallet signature poll: " + _status["last_poll_error"]
    return {"processed": processed, "wallets": len(wallets), "errors": len(errors)}


def _poll_interval_seconds():
    try:
        value = float(os.getenv("TROJAN_COPY_POLL_INTERVAL_SECONDS", "30"))
    except (TypeError, ValueError, OverflowError):
        value = 30.0
    if not 5.0 <= value <= 300.0:
        value = 30.0
    return value


def _subscribe_wallets(ws, wallets, timeout_seconds=15):
    """Subscribe every configured wallet or fail closed; retain early notifications."""
    if not isinstance(wallets, list) or not wallets:
        raise ValueError("At least one copy-watch wallet is required")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ValueError("Subscription timeout must be positive")

    request_wallets = {}
    for request_id, wallet in enumerate(wallets, 1):
        request_wallets[request_id] = wallet
        ws.send(json.dumps({
            "jsonrpc": "2.0", "id": request_id, "method": "logsSubscribe",
            "params": [{"mentions": [wallet]}, {"commitment": "confirmed"}]
        }))

    subscriptions = {}
    subscription_ids = set()
    pending = set(request_wallets)
    early_notifications = []
    failures = {}
    deadline = time.monotonic() + timeout_seconds
    while pending and time.monotonic() < deadline:
        try:
            raw_payload = ws.recv()
        except websocket.WebSocketTimeoutException:
            break
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            raise RuntimeError("Solana websocket returned a malformed subscription response")
        request_id = payload.get("id")
        if request_id in pending:
            if payload.get("error") is not None:
                failures[request_id] = str(payload["error"])[:160]
                pending.discard(request_id)
                continue
            subscription_id = payload.get("result")
            if isinstance(subscription_id, bool) or not isinstance(subscription_id, int) or subscription_id < 0:
                failures[request_id] = "invalid subscription id"
                pending.discard(request_id)
                continue
            if subscription_id in subscription_ids:
                failures[request_id] = "duplicate subscription id"
                pending.discard(request_id)
                continue
            subscriptions[request_wallets[request_id]] = subscription_id
            subscription_ids.add(subscription_id)
            pending.discard(request_id)
        elif isinstance(payload.get("params"), dict):
            # A notification can arrive before the last subscribe acknowledgement.
            # Buffer it so the handshake does not silently drop a real wallet event.
            early_notifications.append(payload)

    if pending or failures or len(subscriptions) != len(wallets):
        missing = [request_wallets[item] for item in sorted(pending)]
        failed = [request_wallets[item] for item in sorted(failures)]
        raise RuntimeError(
            "Incomplete Solana wallet subscriptions; "
            + "missing=" + ",".join(missing[:10])
            + "; failed=" + ",".join(failed[:10])
        )
    return subscriptions, early_notifications


def _monitor_loop():
    global _active_ws
    while not _stop_event.is_set():
        ws = None
        try:
            wallets = _wallets()
            ws_url = _ws_url()
            if not wallets or not _rpc_url() or not ws_url:
                _status.update({
                    "running": False, "configured": False,
                    "wallet_count": len(wallets),
                    "last_error": "Set TROJAN_COPY_WALLETS and Solana RPC/WS URLs"
                })
                _stop_event.wait(10)
                continue
            ws = websocket.create_connection(ws_url, timeout=20, enable_multithread=True)
            with _lock:
                _active_ws = ws
            ws.settimeout(25)
            subscriptions, early_notifications = _subscribe_wallets(ws, wallets)
            notification_queue = list(early_notifications)
            _status.update({
                "running": True, "configured": True,
                "wallet_count": len(subscriptions), "last_error": None
            })
            poll_interval = _poll_interval_seconds()
            next_poll = time.monotonic()
            while not _stop_event.is_set():
                if _wallets() != wallets:
                    print("[TROJAN V2] COPY WATCHLIST CHANGED; RECONNECTING", flush=True)
                    break
                if time.monotonic() >= next_poll:
                    _poll_wallet_activity(wallets)
                    next_poll = time.monotonic() + poll_interval
                try:
                    payload = notification_queue.pop(0) if notification_queue else json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    ws.ping()
                    if _wallets() != wallets:
                        break
                    continue
                params = payload.get("params", {})
                value = params.get("result", {}).get("value", {})
                signature = value.get("signature")
                wallet = next((
                    address for address, subscription_id in subscriptions.items()
                    if subscription_id == params.get("subscription")
                ), None)
                if not signature or not wallet or value.get("err"):
                    continue
                _process_wallet_signature(wallet, signature)
        except Exception as error:
            _status.update({"running": False, "last_error": str(error)[:220]})
            if not _stop_event.is_set():
                print("[TROJAN V2] COPY WATCH RECONNECT:", repr(error), flush=True)
                _stop_event.wait(5)
        finally:
            if ws:
                with _lock:
                    if _active_ws is ws:
                        _active_ws = None
                try:
                    ws.close()
                except Exception:
                    pass
    _status.update({"running": False})


def start_copy_monitor():
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return _thread
        _stop_event.clear()
        _thread = threading.Thread(
            target=_monitor_loop, name="trojan-copy-watch", daemon=True
        )
        _thread.start()
        return _thread


def stop_copy_monitor(timeout=5.0):
    """Stop the wallet observer and close its socket to unblock recv()."""
    global _thread
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a finite non-negative number")
    if not math.isfinite(float(timeout)) or timeout < 0:
        raise ValueError("timeout must be a finite non-negative number")
    _stop_event.set()
    with _lock:
        ws = _active_ws
        thread = _thread
    if ws is not None:
        try:
            ws.close()
        except Exception as error:
            _status["last_error"] = "socket close: " + str(error)[:180]
    if thread is not None and thread is not threading.current_thread():
        thread.join(float(timeout))
        if thread.is_alive():
            return False
    with _lock:
        if _thread is thread:
            _thread = None
    _status["running"] = False
    return True


def get_copy_monitor_status():
    wallets = _wallets()
    configured = bool(wallets) and bool(_rpc_url()) and bool(_ws_url())
    return dict(
        _status, configured=configured, wallet_count=len(wallets),
        paper_copy_enabled=os.getenv("TROJAN_COPY_PAPER_ENABLED", "false").lower() == "true",
        watchlist_maximum=20,
        poll_interval_seconds=_poll_interval_seconds()
    )
