"""Asynchronous optional Telegram notifications; never block the position loop."""
import os
import queue
import threading
import requests

_queue = queue.Queue(maxsize=200)
_lock = threading.RLock()
_worker = None
_stopping = False
_STOP = object()


def _safe_error(error, token):
    message = repr(error)
    if token:
        message = message.replace(token, "[REDACTED_BOT_TOKEN]")
    return message[:400]


def _sender():
    global _worker, _stopping
    while True:
        message = _queue.get()
        if message is _STOP:
            _queue.task_done()
            break
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        try:
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
                if not isinstance(payload, dict) or payload.get("ok") is not True:
                    raise RuntimeError("Telegram API returned an invalid/not-ok response")
        except Exception as error:
            # Requests exceptions can include the full API URL, which contains
            # the bot token. Always redact it before writing to application logs.
            print("[TROJAN V2] TELEGRAM ALERT FAILED:", _safe_error(error, token), flush=True)
        finally:
            _queue.task_done()
    with _lock:
        if _worker is threading.current_thread():
            _worker = None
        _stopping = False


def _start_worker():
    global _worker
    with _lock:
        if _stopping:
            return False
        if _worker and _worker.is_alive():
            return True
        _worker = threading.Thread(
            target=_sender, name="trojan-telegram-alerts", daemon=True
        )
        _worker.start()
        return True


def start_telegram_alert_worker():
    """Start the managed optional notification worker."""
    if not (
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        and os.getenv("TELEGRAM_CHAT_ID", "").strip()
    ):
        return None
    _start_worker()
    with _lock:
        return _worker


def stop_telegram_alert_worker(timeout=5.0):
    """Drain queued notifications and stop the worker gracefully."""
    global _stopping
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
        raise ValueError("Telegram worker timeout must be non-negative")
    with _lock:
        worker = _worker
        if worker is None or not worker.is_alive():
            return True
        if _stopping:
            return False
        _stopping = True
    try:
        _queue.put(_STOP, timeout=float(timeout))
    except queue.Full:
        with _lock:
            _stopping = False
        return False
    if worker is not threading.current_thread():
        worker.join(float(timeout))
    return not worker.is_alive()


def send_alert(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return {"queued": False, "reason": "Telegram not configured"}
    with _lock:
        if _stopping:
            return {"queued": False, "reason": "Telegram worker stopping"}
        if not _start_worker():
            return {"queued": False, "reason": "Telegram worker unavailable"}
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
        "worker_running": bool(_worker and _worker.is_alive()),
        "worker_stopping": _stopping
    }
