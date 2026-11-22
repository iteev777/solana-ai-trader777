"""Optional Telegram notifications. No credentials are stored in source."""
import os
import requests

def send_alert(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return {"sent": False, "reason": "Telegram not configured"}
    try:
        response = requests.post(
            "https://api.telegram.org/bot" + token + "/sendMessage",
            json={"chat_id": chat_id, "text": str(message)[:3800], "disable_web_page_preview": True},
            timeout=8
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram API returned not-ok")
        return {"sent": True}
    except Exception as error:
        print("[TROJAN V2] TELEGRAM ALERT FAILED:", repr(error), flush=True)
        return {"sent": False, "reason": str(error)[:180]}
