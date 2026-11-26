"""Asynchronous optional Telegram notifications; never block the position loop."""
import os
import queue
import threading
import requests

_queue = queue.Queue(maxsize=200)
_lock = threading.Lock()
_worker = None


def _sender():
    while True:
        message = _queue.get()
        try:
            token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
            if token and chat_id:
                response = requests.post(
                    "https://api.telegram.org/bot" + token + "/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": str(message)[:3800],
                        "disable_web_page_preview": True
                    },
                    timeout=8
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("ok"):
                    raise RuntimeError("Telegram API returned not-ok")
        except Exception as error:
            response = error.response if isinstance(error, requests.HTTPError) else None
            status = response.status_code if response is not None else None
            print("[TROJAN V2] TELEGRAM ALERT FAILED:", type(error).__name__, status, flush=True)
        finally:
            _queue.task_done()


def _start_worker():
    global _worker
    with _lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(
            target=_sender, name="trojan-telegram-alerts", daemon=True
        )
        _worker.start()


def send_alert(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return {"queued": False, "reason": "Telegram not configured"}
    _start_worker()
    try:
        _queue.put_nowait(str(message)[:3800])
        return {"queued": True}
    except queue.Full:
        print("[TROJAN V2] TELEGRAM ALERT QUEUE FULL", flush=True)
        return {"queued": False, "reason": "Alert queue full"}


def get_telegram_status():
    return {
        "configured": bool(
            os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            and os.getenv("TELEGRAM_CHAT_ID", "").strip()
        ),
        "queue_size": _queue.qsize(),
        "worker_running": bool(_worker and _worker.is_alive())
    }
