import json
import time
import queue
import threading
import os

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

    print("================================")
    print("LAUNCH SNIPER CONNECTED")
    print("MODE: PAPER TRADING ONLY")
    print("SUBSCRIPTION: NEW TOKENS")
    print("================================")

    try:

        ws.send(json.dumps({
            "method": "subscribeNewToken"
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

        address = data.get("mint")

        if not isinstance(address, str):
            return

        address = address.strip()

        if not address:
            return

        if address in seen_tokens:
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

        try:

            new_token_queue.put_nowait(token)

        except queue.Full:

            print(
                "SNIPER QUEUE FULL - TOKEN SKIPPED:",
                address
            )

            return

        remember_token(address)

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

    print("SNIPER CONNECTION CLOSED")
    print("STATUS:", close_status_code, close_message)


def run_sniper_listener():

    if API_KEY:
        url = BASE_URL + "?api-key=" + quote(API_KEY, safe="")
    else:
        url = BASE_URL

    while True:

        try:

            print("CONNECTING TO PUMPPORTAL...")

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

        print(
            "RECONNECTING IN",
            RECONNECT_DELAY,
            "SECONDS..."
        )

        time.sleep(RECONNECT_DELAY)


def start_sniper_listener():

    global listener_started

    with listener_lock:

        if listener_started:
            print("SNIPER LISTENER ALREADY STARTED")
            return

        listener_started = True

    thread = threading.Thread(
        target=run_sniper_listener,
        daemon=True,
        name="TrojanSniperListener"
    )

    thread.start()

    print("SNIPER THREAD STARTED")


# Compatibility with main.py
def start_listener():
    return start_sniper_listener()


if __name__ == "__main__":
    run_sniper_listener()
