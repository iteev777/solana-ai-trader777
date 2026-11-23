"""Runtime heartbeats and explicit paper/live readiness checks."""
import os
import time
import threading
import math

_lock = threading.Lock()
_heartbeats = {}
_last_report = None

def _heartbeat_check(name, beats, now, max_age_seconds=30):
    """A heartbeat is healthy only when finite, present, non-future and fresh."""
    stamp = beats.get(name)
    if isinstance(stamp, bool):
        stamp = None
    try:
        stamp = float(stamp) if stamp is not None else None
    except (TypeError, ValueError, OverflowError):
        stamp = None
    if stamp is None or not math.isfinite(stamp):
        return {"name": name, "ok": False, "age_seconds": None, "required": True}
    age = now - stamp
    return {
        "name": name,
        "ok": 0.0 <= age < max_age_seconds,
        "age_seconds": round(age, 1),
        "required": True,
    }



def heartbeat(service):
    with _lock:
        _heartbeats[str(service)] = time.time()


def get_readiness_report():
    checks = []
    now = time.time()
    with _lock:
        beats = dict(_heartbeats)
    for name in ("main_loop", "position_monitor"):
        checks.append(_heartbeat_check(name, beats, now))
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
        checks.append({"name": "solana_rpc", "ok": bool(rpc.get("rpc_healthy")),
                       "details": "healthy" if rpc.get("rpc_healthy") else (rpc.get("rpc_error") or "RPC not configured"),
                       "required": False})
    except Exception as error:
        checks.append({"name": "solana_rpc", "ok": False, "details": str(error)[:150], "required": False})
    try:
        from api_client import get_api_status
        from market_data import get_market_data_status
        api = get_api_status()
        market = get_market_data_status()
        checks.append({"name": "market_api_client", "ok": True,
                       "details": str(len(api.get("hosts", {}))) + " hosts tracked", "required": True})
        checks.append({"name": "market_data_worker", "ok": bool(market.get("worker_running")),
                       "details": "running" if market.get("worker_running") else "waiting for first market request",
                       "required": True})
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
