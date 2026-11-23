# ==========================================
# TROJAN V2 - SNIPER SCANNER
# ==========================================

import json
import time
import queue
import threading
import os
import math

from collections import OrderedDict
from urllib.parse import quote

import websocket


API_KEY = os.getenv("PUMPPORTAL_API_KEY")
BASE_URL = "wss://pumpportal.fun/api/data"

RECONNECT_DELAY = 5
PING_INTERVAL = 20
PING_TIMEOUT = 10

QUEUE_SIZE = 1000
MAX_SEEN_TOKENS = 10000

new_token_queue = queue.Queue(maxsize=QUEUE_SIZE)

# Compatibility with main.py
token_queue = new_token_queue

seen_tokens = OrderedDict()
seen_lock = threading.Lock()

listener_lock = threading.Lock()
listener_started = False
_listener_thread = None
_listener_stop_event = threading.Event()
_subscription_lock = threading.RLock()
_active_ws = None
_watched_tokens = set()


def get_new_tokens():
    tokens = []

    while True:
        try:
            tokens.append(new_token_queue.get_nowait())
        except queue.Empty:
            break

    return tokens


def remember_token(address):
    with seen_lock:
        if address in seen_tokens:
            return False

        seen_tokens[address] = time.time()

        if len(seen_tokens) > MAX_SEEN_TOKENS:
            seen_tokens.popitem(last=False)

        return True


def on_open(ws):
    if _listener_stop_event.is_set():
        try:
            ws.close()
        except Exception:
            pass
        return

    print("================================")
    print("TROJAN V2 - LAUNCH SNIPER CONNECTED")
    print("MODE: PAPER TRADING ONLY")
    print("SUBSCRIPTION: NEW TOKENS")
    print("================================")

    global _active_ws
    with _subscription_lock:
        _active_ws = ws
        watched = list(_watched_tokens)

    try:
        ws.send(json.dumps({
            "method": "subscribeNewToken"
        }))
        if API_KEY and os.getenv("TROJAN_PUMPPORTAL_OBSERVATION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
            for start in range(0, len(watched), 100):
                ws.send(json.dumps({
                    "method": "subscribeTokenTrade",
                    "keys": watched[start:start + 100]
                }))

        print("NEW TOKEN LISTENER ACTIVE")

    except Exception as error:
        print("SNIPER SUBSCRIPTION ERROR:", repr(error))
        ws.close()


def on_message(ws, message):
    try:
        data = json.loads(message)

        if not isinstance(data, dict):
            return

        tx_type = str(data.get("txType") or "").strip().lower()
        if tx_type in {"buy", "sell"}:
            try:
                import candidate_observer
                candidate_observer.record_trade_event(data)
            except Exception as observer_error:
                print("SNIPER TRADE OBSERVATION ERROR:", repr(observer_error))
            return
        if tx_type and tx_type != "create":
            return

        address = data.get("mint")

        if not isinstance(address, str):
            return

        address = address.strip()

        if not address:
            return

        token = {
            "address": address,
            "mint": address,
            "tokenAddress": address,
            "chainId": "solana",
            "name": data.get("name") or "Unknown",
            "symbol": data.get("symbol") or "Unknown",
            "creator": data.get("traderPublicKey") or "Unknown",
            "discovery_sources": ["pumpportal"],
            "detected_at": time.time()
        }

        # Atomically reject duplicates and reserve the token.
        if not remember_token(address):
            return

        try:
            new_token_queue.put_nowait(token)

        except queue.Full:
            print(
                "SNIPER QUEUE FULL - TOKEN SKIPPED:",
                address
            )

            # Allow the token to be detected again later.
            with seen_lock:
                seen_tokens.pop(address, None)

            return

        print("================================")
        print("NEW TOKEN DETECTED")
        print("NAME:", token["name"])
        print("SYMBOL:", token["symbol"])
        print("ADDRESS:", address)
        print("STATUS: SENT TO MAIN BOT")
        print("================================")

    except json.JSONDecodeError:
        print("SNIPER INVALID JSON MESSAGE")

    except Exception as error:
        print("SNIPER MESSAGE ERROR:", repr(error))


def on_error(ws, error):
    print("SNIPER CONNECTION ERROR:", repr(error))


def on_close(ws, close_status_code, close_message):
    global _active_ws
    with _subscription_lock:
        if _active_ws is ws:
            _active_ws = None
    print("SNIPER CONNECTION CLOSED")
    print("STATUS:", close_status_code, close_message)


def watch_token(mint):
    """Subscribe to metered trade events on the existing single WebSocket."""
    if not API_KEY:
        return False
    if not isinstance(mint, str) or not mint.strip():
        return False
    if os.getenv("TROJAN_PUMPPORTAL_OBSERVATION_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    mint = mint.strip()
    with _subscription_lock:
        if mint in _watched_tokens:
            return True
        _watched_tokens.add(mint)
        ws = _active_ws
        if ws is not None:
            try:
                ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))
            except Exception as error:
                print("SNIPER TRADE SUBSCRIBE QUEUED FOR RECONNECT:", repr(error))
    return True


def unwatch_token(mint):
    """Remove a token subscription from the shared PumpPortal WebSocket."""
    if not isinstance(mint, str) or not mint.strip():
        return False
    mint = mint.strip()
    with _subscription_lock:
        existed = mint in _watched_tokens
        _watched_tokens.discard(mint)
        ws = _active_ws
        if existed and ws is not None and API_KEY:
            try:
                ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": [mint]}))
            except Exception as error:
                print("SNIPER TRADE UNSUBSCRIBE ERROR:", repr(error))
    return existed


def get_trade_subscription_status():
    with _subscription_lock:
        return {
            "connected": _active_ws is not None,
            "watching_count": len(_watched_tokens),
            "api_key_configured": bool(API_KEY),
            "enabled": os.getenv("TROJAN_PUMPPORTAL_OBSERVATION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        }


def run_sniper_listener():
    if API_KEY:
        url = BASE_URL + "?api-key=" + quote(API_KEY, safe="")
    else:
        url = BASE_URL

    while not _listener_stop_event.is_set():
        try:
            print("TROJAN V2 CONNECTING TO PUMPPORTAL...")

            ws = websocket.WebSocketApp(
                url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )

            ws.run_forever(
                ping_interval=PING_INTERVAL,
                ping_timeout=PING_TIMEOUT
            )

        except Exception as error:
            print("SNIPER ERROR:", repr(error))

        if not _listener_stop_event.is_set():
            print(
                "RECONNECTING IN",
                RECONNECT_DELAY,
                "SECONDS..."
            )
            _listener_stop_event.wait(RECONNECT_DELAY)


def start_sniper_listener():
    global listener_started, _listener_thread

    with listener_lock:
        if _listener_thread is not None and _listener_thread.is_alive():
            print("SNIPER LISTENER ALREADY STARTED")
            return _listener_thread
        _listener_stop_event.clear()
        listener_started = True

    def start_thread():
        global listener_started

        try:
            run_sniper_listener()
        finally:
            with listener_lock:
                listener_started = False

    try:
        thread = threading.Thread(
            target=start_thread,
            daemon=True,
            name="TrojanV2SniperListener"
        )

        with listener_lock:
            _listener_thread = thread
        thread.start()

        print("TROJAN V2 SNIPER THREAD STARTED")
        return thread

    except Exception as error:
        with listener_lock:
            listener_started = False

        print("SNIPER THREAD START ERROR:", repr(error))
        return None


def stop_listener(timeout=5.0):
    """Stop the reconnect loop, close the active socket and join its worker."""
    global listener_started, _listener_thread
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a finite non-negative number")
    if not math.isfinite(float(timeout)) or timeout < 0:
        raise ValueError("timeout must be a finite non-negative number")

    _listener_stop_event.set()
    with _subscription_lock:
        ws = _active_ws
    if ws is not None:
        try:
            ws.close()
        except Exception as error:
            print("SNIPER SOCKET CLOSE ERROR:", repr(error))

    with listener_lock:
        thread = _listener_thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(float(timeout))
        if thread.is_alive():
            return False
    with listener_lock:
        listener_started = False
        if _listener_thread is thread:
            _listener_thread = None
    return True


# Compatibility with main.py
def start_listener():
    return start_sniper_listener()


if __name__ == "__main__":
    run_sniper_listener()
