import os
import json
import time
from collections import OrderedDict
from urllib.parse import quote

import websocket


API_KEY = os.getenv("PUMPPORTAL_API_KEY")

BASE_URL = "wss://pumpportal.fun/api/data"

seen_tokens = OrderedDict()
MAX_SEEN_TOKENS = 10000


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

        if not address:
            return

        if address in seen_tokens:
            return

        seen_tokens[address] = True

        if len(seen_tokens) > MAX_SEEN_TOKENS:
            seen_tokens.popitem(last=False)

        print("================================")
        print("NEW TOKEN DETECTED")
        print("NAME:", data.get("name", "Unknown"))
        print("SYMBOL:", data.get("symbol", "Unknown"))
        print("ADDRESS:", address)
        print("CREATOR:", data.get("traderPublicKey", "Unknown"))
        print("TIME:", time.strftime("%Y-%m-%d %H:%M:%S"))
        print("STATUS: WAITING FOR ANALYSIS")
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

    import threading

    thread = threading.Thread(
        target=run_sniper_listener,
        daemon=True
    )

    thread.start()

    print("SNIPER THREAD STARTED")


if __name__ == "__main__":

    run_sniper_listener()
