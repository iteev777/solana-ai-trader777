"""Read-only Solana wallet activity monitor for paper research and Telegram alerts.

Tracks up to 20 public wallet addresses. It never signs, submits or copies
transactions with real funds.
"""
import json
import os
import threading
import time
import requests
import websocket

import database
from telegram_alerts import send_alert
from copy_event_parser import parse_wallet_transaction

_lock = threading.Lock()
_thread = None
_status = {
    "running": False, "configured": False, "wallet_count": 0,
    "last_event": None, "last_error": None, "events_seen": 0
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
    if not isinstance(tx, dict):
        return None
    return parse_wallet_transaction(
        tx, wallet, signature=signature, received_at=time.time()
    )


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
        payload = json.loads(ws.recv())
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
    while True:
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
                time.sleep(10)
                continue
            ws = websocket.create_connection(ws_url, timeout=20, enable_multithread=True)
            ws.settimeout(25)
            subscriptions, early_notifications = _subscribe_wallets(ws, wallets)
            notification_queue = list(early_notifications)
            _status.update({
                "running": True, "configured": True,
                "wallet_count": len(subscriptions), "last_error": None
            })
            while True:
                if _wallets() != wallets:
                    print("[TROJAN V2] COPY WATCHLIST CHANGED; RECONNECTING", flush=True)
                    break
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
                event = _fetch_wallet_transaction(signature, wallet)
                if not event:
                    continue
                inserted = database.record_copy_trade_event(
                    event["wallet"], event["signature"], event["action"],
                    event["token_mint"], event["token_delta"], event["sol_delta"],
                    json.dumps({
                        "logs": event["logs"],
                        "classification_reason": event.get("classification_reason"),
                        "economic_sol_delta": event.get("economic_sol_delta"),
                        "quote_delta": event.get("quote_delta"),
                        "invoked_programs": event.get("invoked_programs", [])
                    })
                )
                if not inserted:
                    continue
                _status["events_seen"] += 1
                _status["last_event"] = time.time()
                if event["action"] in ("BUY", "SELL") and os.getenv("TROJAN_COPY_PAPER_ENABLED", "false").lower() == "true":
                    try:
                        import main
                        main.queue_copy_signal(event)
                    except Exception as copy_error:
                        _status["last_error"] = "paper copy queue: " + str(copy_error)[:150]
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
        except Exception as error:
            _status.update({"running": False, "last_error": str(error)[:220]})
            print("[TROJAN V2] COPY WATCH RECONNECT:", repr(error), flush=True)
            time.sleep(5)
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass


def start_copy_monitor():
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return True
        _thread = threading.Thread(
            target=_monitor_loop, name="trojan-copy-watch", daemon=True
        )
        _thread.start()
        return True


def get_copy_monitor_status():
    wallets = _wallets()
    configured = bool(wallets) and bool(_rpc_url()) and bool(_ws_url())
    return dict(
        _status, configured=configured, wallet_count=len(wallets),
        paper_copy_enabled=os.getenv("TROJAN_COPY_PAPER_ENABLED", "false").lower() == "true",
        watchlist_maximum=20
    )
