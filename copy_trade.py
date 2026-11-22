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


def _wallets():
    return list(dict.fromkeys(
        item.strip() for item in os.getenv("TROJAN_COPY_WALLETS", "").split(",")
        if item.strip()
    ))[:20]


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
            request_wallets = {}
            for request_id, wallet in enumerate(wallets, 1):
                request_wallets[request_id] = wallet
                ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": request_id, "method": "logsSubscribe",
                    "params": [{"mentions": [wallet]}, {"commitment": "confirmed"}]
                }))
            subscriptions = {}
            pending = set(request_wallets)
            deadline = time.time() + 15
            while pending and time.time() < deadline:
                payload = json.loads(ws.recv())
                if payload.get("id") in request_wallets and "result" in payload:
                    subscriptions[request_wallets[payload["id"]]] = payload["result"]
                    pending.discard(payload["id"])
            if not subscriptions:
                raise RuntimeError("Solana websocket returned no wallet subscriptions")
            _status.update({
                "running": True, "configured": True,
                "wallet_count": len(subscriptions), "last_error": None
            })
            while True:
                try:
                    payload = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    ws.ping()
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
        paper_copy_enabled=os.getenv("TROJAN_COPY_PAPER_ENABLED", "false").lower() == "true"
    )
