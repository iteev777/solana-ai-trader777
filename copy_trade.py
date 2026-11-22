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
    if not isinstance(tx, dict) or not isinstance(tx.get("meta"), dict):
        return None
    meta = tx["meta"]
    message = tx.get("transaction", {}).get("message", {})
    keys = message.get("accountKeys", [])
    wallet_index = next((
        index for index, item in enumerate(keys)
        if (item.get("pubkey") if isinstance(item, dict) else item) == wallet
    ), None)
    sol_delta = None
    if wallet_index is not None:
        before, after = meta.get("preBalances", []), meta.get("postBalances", [])
        if wallet_index < len(before) and wallet_index < len(after):
            sol_delta = (float(after[wallet_index]) - float(before[wallet_index])) / 1e9
    token_deltas = {}
    for side, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for item in meta.get(side, []) or []:
            if item.get("owner") != wallet:
                continue
            mint = item.get("mint")
            amount = item.get("uiTokenAmount", {}).get("uiAmount")
            if mint and amount is not None:
                token_deltas[mint] = token_deltas.get(mint, 0.0) + sign * float(amount)
    token_deltas = {mint: delta for mint, delta in token_deltas.items() if abs(delta) > 1e-12}
    if not token_deltas:
        return None
    mint, delta = max(token_deltas.items(), key=lambda item: abs(item[1]))
    if delta > 0 and (sol_delta is None or sol_delta < 0):
        action = "BUY"
    elif delta < 0 and (sol_delta is None or sol_delta > 0):
        action = "SELL"
    else:
        action = "TOKEN TRANSFER / UNKNOWN"
    return {
        "wallet": wallet, "signature": signature, "action": action,
        "token_mint": mint, "token_delta": delta, "sol_delta": sol_delta,
        "logs": (meta.get("logMessages") or [])[-5:]
    }


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
                    json.dumps({"logs": event["logs"]})
                )
                if not inserted:
                    continue
                _status["events_seen"] += 1
                _status["last_event"] = time.time()
                send_alert(
                    "COPY WATCH " + event["action"]
                    + "\nWallet: " + wallet
                    + "\nMint: " + event["token_mint"]
                    + "\nToken delta: " + str(round(event["token_delta"], 6))
                    + "\nSOL delta: " + (
                        str(round(event["sol_delta"], 6))
                        if event["sol_delta"] is not None else "n/a"
                    )
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
    return dict(_status, configured=configured, wallet_count=len(wallets))
