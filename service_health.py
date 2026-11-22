"""Runtime heartbeats and explicit paper/live readiness checks."""
import os
import time
import threading

_lock = threading.Lock()
_heartbeats = {}
_last_report = None


def heartbeat(service):
    with _lock:
        _heartbeats[str(service)] = time.time()


def get_readiness_report():
    checks = []
    now = time.time()
    with _lock:
        beats = dict(_heartbeats)
    for name in ("main_loop", "position_monitor"):
        age = now - beats.get(name, 0) if beats.get(name) else None
        checks.append({
            "name": name, "ok": age is not None and age < 30,
            "age_seconds": round(age, 1) if age is not None else None,
            "required": True
        })
    try:
        import database
        db = database.get_database_health()
        checks.append({"name": "database", "ok": db.get("integrity") == "ok",
                       "details": db.get("integrity"), "required": True})
    except Exception as error:
        checks.append({"name": "database", "ok": False, "details": str(error)[:150], "required": True})
    try:
        from solana_onchain import get_onchain_status
        rpc = get_onchain_status()
        checks.append({"name": "solana_rpc", "ok": bool(rpc.get("rpc_configured")),
                       "details": "configured" if rpc.get("rpc_configured") else "SOLANA_RPC_URL missing",
                       "required": False})
    except Exception as error:
        checks.append({"name": "solana_rpc", "ok": False, "details": str(error)[:150], "required": False})
    try:
        from api_client import get_api_status
        api = get_api_status()
        checks.append({"name": "market_api_client", "ok": True,
                       "details": str(len(api.get("hosts", {}))) + " hosts tracked", "required": True})
    except Exception as error:
        checks.append({"name": "market_api_client", "ok": False, "details": str(error)[:150], "required": True})
    try:
        from telegram_alerts import send_alert
        configured = bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
        checks.append({"name": "telegram", "ok": configured,
                       "details": "configured" if configured else "not configured", "required": False})
    except Exception:
        checks.append({"name": "telegram", "ok": False, "required": False})
    try:
        from copy_trade import get_copy_monitor_status
        copy_status = get_copy_monitor_status()
        checks.append({"name": "copy_wallet_monitor", "ok": copy_status["configured"],
                       "details": str(copy_status["wallet_count"]) + " wallets" if copy_status["configured"] else "not configured",
                       "required": False})
    except Exception as error:
        checks.append({"name": "copy_wallet_monitor", "ok": False, "details": str(error)[:150], "required": False})
    required_ok = all(item["ok"] for item in checks if item.get("required"))
    return {
        "timestamp": now,
        "mode": "PAPER",
        "paper_ready": required_ok,
        "live_ready": False,
        "live_execution": "LOCKED",
        "checks": checks
    }


def get_service_status():
    with _lock:
        return {name: {"last_heartbeat": stamp, "age_seconds": round(time.time()-stamp, 1)}
                for name, stamp in _heartbeats.items()}
