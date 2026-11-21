import json
import time
import queue
import threading
from collections import OrderedDict
from urllib.parse import quote
import os

import websocket


API_KEY = os.getenv("PUMPPORTAL_API_KEY")
BASE_URL = "wss://pumpportal.fun/api/data"

new_token_queue = queue.Queue(maxsize=1000)
seen_tokens = OrderedDict()
MAX_SEEN_TOKENS = 10000


def get_new_tokens():
    tokens = []

    while True:
        try:
            tokens.append(new_token_queue.get_nowait())
        except queue.Empty:
            break

    return tokens


def on_open(ws):
    print("================================")
    print("LAUNCH SNIPER CONNECTED")
    print("MODE: PAPER TRADING ONLY")
    print("SUBSCRIPTION: NEW TOKENS")
    print("================================")

    ws.send(json.dumps({
        "method": "subscribeNewToken"
    }))

    print("NEW TOKEN LISTENER ACTIVE")


def on_message(ws, message):
    try:
        data = json.loads(message)

        if not isinstance(data, dict):
            return

        address = data.get("mint")

        if not address or address in seen_tokens:
            return

        seen_tokens[address] = True

        if len(seen_tokens) > MAX_SEEN_TOKENS:
            seen_tokens.popitem(last=False)

        token = {
            "address": address,
            "name": data.get("name", "Unknown"),
            "symbol": data.get("symbol", "Unknown"),
            "creator": data.get("traderPublicKey", "Unknown"),
            "detected_at": time.time()
        }

        try:
            new_token_queue.put_nowait(token)
        except queue.Full:
            print("SNIPER QUEUE FULL - TOKEN SKIPPED:", address)
            return

        print("================================")
        print("NEW TOKEN DETECTED")
        print("NAME:", token["name"])
        print("SYMBOL:", token["symbol"])
        print("ADDRESS:", address)
        print("STATUS: SENT TO MAIN BOT")
        print("================================")

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
                ping_interval=20,
                ping_timeout=10
            )

        except Exception as error:
            print("SNIPER ERROR:", repr(error))

        print("RECONNECTING IN 5 SECONDS...")
        time.sleep(5)


def start_sniper_listener():
    thread = threading.Thread(
        target=run_sniper_listener,
        daemon=True
    )

    thread.start()
    print("SNIPER THREAD STARTED")


if __name__ == "__main__":
    run_sniper_listener()
