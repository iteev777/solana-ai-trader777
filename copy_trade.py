"""Read-only wallet activity monitor for paper research and Telegram alerts.

This module never signs transactions and never copies trades with real funds.
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
    value = os.getenv("SOLANA_WS_URL", "").strip()
    if value:
        return value
    rpc = _rpc_url()
    if rpc.startswith("https://"):
        return "wss://" + rpc[len("https://"):]
    if rpc.startswith("http://"):
        return "ws://" + rpc[len("http://"):]
    return ""


def _transaction(signature, wallet):
    response = requests.post(
        _rpc_url(),
        json={
            "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
            "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}]
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
    wallet_index = None
    for index, item in enumerate(keys):
        pubkey = item.get("pubkey") if isinstance(item, dict) else item
        if pubkey == wallet:
            wallet_index = index
            break
    sol_delta = None
    if wallet_index is not None:
        before = meta.get("preBalances", [])
        after = meta.get("postBalances", [])
        if wallet_index < len(before) and wallet_index < len(after):
            sol_delta = (float(after[wallet_index]) - float(before[wallet_index])) / 1_000_000_000
    deltas = {}
    for side, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for item in meta.get(side, []) or []:
            if item.get("owner") != wallet:
                continue
            mint = item.get("mint")
            amount = item.get("uiTokenAmount", {}).get("uiAmount")
            if not mint or amount is None:
                continue
            deltas[mint] = deltas.get(mint, 0.0) + sign * float(amount)
    deltas = {mint: amount for mint, amount in deltas.items() if abs(amount) > 1e-12}
    if not deltas:
        return None
    mint, amount = max(deltas.items(), key=lambda item: abs(item[1]))
    if amount > 0 and (sol_delta is None or sol_delta < 0):
        action = "BUY"
    elif amount < 0 and (sol_delta is None or sol_delta > 0):
        action = "SELL"
    else:
        action = "TOKEN TRANSFER / UNKNOWN"
    return {"wallet": wallet, "signature": signature, "action": action,
            "token_mint": mint, "token_delta": amount, "sol_delta": sol_delta}


def _run():
    wallets = _wallets()
    ws_url = _ws_url()
    if not wallets or not ws_url or not _rpc_url():
        _status.update({"running": False, "configured": False,
                        "last_error": "Set TROJAN_COPY_WALLETS, SOLANA_RPC_URL and SOLANA_WS_URL"})
        return
    _status.update({"configured": True, "wallet_count": len(wallets), "running": True})
    while True:
        try:
            ws = websocket.create_connection(ws_url, timeout=20, enable_multithread=True)
            ws.settimeout(25)
            for index, wallet in enumerate(wallets, 1):
                ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": index, "method": "logsSubscribe",
                    "params": [{"mentions": [wallet]}, {"commitment": "confirmed"}]
                }))
            last_ping = time.time()
            while True:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    if time.time() - last_ping > 20:
                        ws.ping()
                        last_ping = time.time()
                    continue
                payload = json.loads(raw)
                params = payload.get("params", {})
                value = params.get("result", {}).get("value", {})
                signature = value.get("signature")
                if not signature or value.get("err"):
                    continue
                # A notification is matched to the subscription ID returned by the server.
                sub_id = params.get("subscription")
                wallet = next((w for w, sid in subscriptions.items() if sid == sub_id), None)
                if not wallet:
                    continue
                event = _transaction(signature, wallet)
                if not event:
                    continue
                inserted = database.record_copy_trade_event(
                    event["wallet"], event["signature"], event["action"],
                    event["token_mint"], event["token_delta"], event["sol_delta"],
                    json.dumps({"logs": value.get("logs", [])[-4:]})
                )
                if inserted:
                    _status["events_seen"] += 1
                    _status["last_event"] = time.time()
                    summary = (
                        "COPY WATCH " + event["action"] + "\nWallet: " + wallet
                        + "\nMint: " + event["token_mint"]
                        + "\nToken delta: " + str(round(event["token_delta"], 6))
                        + "\nSOL delta: " + str(round(event["sol_delta"], 6) if event["sol_delta"] is not None else "n/a")
                        + "\nSignature: " + signature
                    )
                    send_alert(summary)
                    print("[TROJAN V2] COPY WATCH:", summary.replace("\n", " | "), flush=True)
        except Exception as error:
            _status.update({"running": False, "last_error": str(error)[:220]})
            print("[TROJAN V2] COPY WATCH RECONNECT:", repr(error), flush=True)
            time.sleep(5)
        finally:
            try:
                ws.close()
            except Exception:
                pass


def start_copy_monitor():
    global _thread, subscriptions
    with _lock:
        if _thread and _thread.is_alive():
            return True
        wallets = _wallets()
        if not wallets:
            _status.update({"configured": False, "running": False, "wallet_count": 0})
            return False
        subscriptions = {}
        # The subscription IDs are received asynchronously; map request IDs to wallets below.
        def runner():
            global subscriptions
            while True:
                try:
                    wallets_now = _wallets()
                    ws_url = _ws_url()
                    if not wallets_now or not ws_url or not _rpc_url():
                        _status.update({"running": False, "configured": False})
                        time.sleep(10)
                        continue
                    ws = websocket.create_connection(ws_url, timeout=20, enable_multithread=True)
                    ws.settimeout(25)
                    request_wallets = {}
                    for index, wallet in enumerate(wallets_now, 1):
                        request_wallets[index] = wallet
                        ws.send(json.dumps({
                            "jsonrpc": "2.0", "id": index, "method": "logsSubscribe",
                            "params": [{"mentions": [wallet]}, {"commitment": "confirmed"}]
                        }))
                    subscriptions = {}
                    # Consume subscription acknowledgements, then stream notifications.
                    pending = set(request_wallets)
                    started = time.time()
                    while pending and time.time() - started < 15:
                        message = json.loads(ws.recv())
                        if "id" in message and message.get("id") in request_wallets:
                            if "result" in message:
                                subscriptions[request_wallets[message["id"]]] = message["result"]
                            pending.discard(message["id"])
                    _status.update({"configured": True, "running": True, "wallet_count": len(subscriptions), "last_error": None})
                    while True:
                        try:
                            message = json.loads(ws.recv())
                        except websocket.WebSocketTimeoutException:
                            ws.ping()
                            continue
                        params = message.get("params", {})
                        value = params.get("result", {}).get("value", {})
                        signature = value.get("signature")
                        wallet = next((w for w, sid in subscriptions.items() if sid == params.get("subscription")), None)
                        if not signature or not wallet or value.get("err"):
                            continue
                        event = _transaction(signature, wallet)
                        if not event:
                            continue
                        inserted = database.record_copy_trade_event(
                            event["wallet"], event["signature"], event["action"],
                            event["token_mint"], event["token_delta"], event["sol_delta"],
                            json.dumps({"logs": value.get("logs", [])[-4:]})
                        )
                        if inserted:
                            _status["events_seen"] += 1
                            _status["last_event"] = time.time()
                            send_alert(
                                "COPY WATCH " + event["action"] + "\nWallet: " + wallet
                                + "\nMint: " + event["token_mint"]
                                + "\nToken delta: " + str(round(event["token_delta"], 6))
                                + "\nSOL delta: " + str(round(event["sol_delta"], 6) if event["sol_delta"] is not None else "n/a")
                                + "\nSignature: " + signature
                            )
                except Exception as error:
                    _status.update({"running": False, "last_error": str(error)[:220]})
                    time.sleep(5)
                finally:
                    try:
                        ws.close()
                    except Exception:
                        pass
        _thread = threading.Thread(target=runner, name="trojan-copy-watch", daemon=True)
        _thread.start()
        return True


def get_copy_monitor_status():
    return dict(_status, configured=bool(_wallets()) and bool(_rpc_url()) and bool(_ws_url()),
                wallet_count=len(_wallets()))
