"""Optional PumpPortal trade-window observer for ScreenerBot-style entry confirmation.

Trade subscriptions are metered by PumpPortal. This module is strictly opt-in:
no trade subscriptions are sent unless TROJAN_PUMPPORTAL_OBSERVATION_ENABLED=true
and PUMPPORTAL_API_KEY is configured. The observer never signs or submits trades.
"""
import math
import os
import threading
import time


def _bool_env(name, default=False):
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _number_env(name, default, minimum, maximum, integer=False):
    raw = os.getenv(name)
    try:
        value = default if raw is None else (int(raw) if integer else float(raw))
    except (TypeError, ValueError, OverflowError):
        value = default
    if isinstance(value, bool) or not math.isfinite(float(value)):
        value = default
    value = max(minimum, min(maximum, value))
    return int(value) if integer else float(value)


ENABLED = _bool_env("TROJAN_PUMPPORTAL_OBSERVATION_ENABLED", False)
DURATION_SECONDS = _number_env("TROJAN_OBSERVATION_DURATION_SECONDS", 20, 5, 180, True)
MAX_CONCURRENT = _number_env("TROJAN_OBSERVATION_MAX_CONCURRENT", 24, 1, 200, True)
MAX_EVENTS_PER_SESSION = _number_env("TROJAN_OBSERVATION_MAX_EVENTS", 3000, 50, 20000, True)
MIN_TRADES = _number_env("TROJAN_OBSERVATION_MIN_TRADES", 5, 1, 10000, True)
MIN_UNIQUE_BUYERS = _number_env("TROJAN_OBSERVATION_MIN_UNIQUE_BUYERS", 3, 1, 10000, True)
MIN_BUY_VOLUME_SOL = _number_env("TROJAN_OBSERVATION_MIN_BUY_VOLUME_SOL", 0.5, 0, 100000)
MIN_BUY_SELL_RATIO = _number_env("TROJAN_OBSERVATION_MIN_BUY_SELL_RATIO", 1.25, 0, 1000)
MAX_SINGLE_BUYER_PCT = _number_env("TROJAN_OBSERVATION_MAX_SINGLE_BUYER_PCT", 60, 0.01, 100)
MIN_PRICE_CHANGE_PCT = _number_env("TROJAN_OBSERVATION_MIN_PRICE_CHANGE_PCT", -2, -100, 10000)
MIN_SCORE = _number_env("TROJAN_OBSERVATION_MIN_SCORE", 55, 0, 100)

_LOCK = threading.RLock()
_SESSIONS = {}
_STATS = {
    "sessions_started": 0,
    "sessions_completed": 0,
    "sessions_passed": 0,
    "sessions_rejected": 0,
    "events_received": 0,
    "events_ignored": 0,
    "last_completed_at": None,
    "last_result": None,
    "last_error": None,
}


def _finite_positive(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _is_supported_source(sources):
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, (list, tuple, set)):
        return False
    accepted = {"pumpportal", "pumpfun", "pumpfun_migration", "pumpfun-migration"}
    return any(
        isinstance(source, str)
        and (source.strip().lower() in accepted
             or source.strip().lower().startswith("pumpfun_"))
        for source in sources
    )


def record_trade_event(event):
    """Append one validated PumpPortal buy/sell event to its active mint session."""
    if not isinstance(event, dict):
        with _LOCK:
            _STATS["events_ignored"] += 1
        return False
    tx_type = str(event.get("txType") or "").strip().lower()
    mint = event.get("mint")
    trader = event.get("traderPublicKey")
    sol_amount = _finite_positive(event.get("solAmount"))
    token_amount = _finite_positive(event.get("tokenAmount"))
    if (tx_type not in {"buy", "sell"} or not isinstance(mint, str)
            or not mint.strip() or not isinstance(trader, str) or not trader.strip()
            or sol_amount is None or token_amount is None):
        with _LOCK:
            _STATS["events_ignored"] += 1
        return False

    signature = event.get("signature")
    market_cap = _finite_positive(event.get("marketCapSol"))
    price = market_cap if market_cap is not None else sol_amount / token_amount
    if not math.isfinite(price) or price <= 0:
        with _LOCK:
            _STATS["events_ignored"] += 1
        return False

    normalized = {
        "tx_type": tx_type,
        "mint": mint.strip(),
        "trader": trader.strip(),
        "sol_amount": sol_amount,
        "token_amount": token_amount,
        "price": price,
        "signature": signature.strip() if isinstance(signature, str) and signature.strip() else None,
        "received_at": time.time(),
    }
    with _LOCK:
        session = _SESSIONS.get(normalized["mint"])
        if session is None:
            _STATS["events_ignored"] += 1
            return False
        if normalized["signature"] and normalized["signature"] in session["signatures"]:
            _STATS["events_ignored"] += 1
            return False
        if len(session["events"]) >= MAX_EVENTS_PER_SESSION:
            session["overflow"] = True
            _STATS["events_ignored"] += 1
            return False
        if normalized["signature"]:
            session["signatures"].add(normalized["signature"])
        session["events"].append(normalized)
        _STATS["events_received"] += 1
        return True


def _component(value, target, weight):
    if target <= 0:
        return float(weight)
    return min(float(weight), max(0.0, float(value) / float(target) * weight))


def evaluate_events(events, overflow=False):
    """Evaluate a captured event list; deterministic and independent of network IO."""
    buys = [event for event in events if event["tx_type"] == "buy"]
    sells = [event for event in events if event["tx_type"] == "sell"]
    buy_volume = sum(event["sol_amount"] for event in buys)
    sell_volume = sum(event["sol_amount"] for event in sells)
    ratio = buy_volume / sell_volume if sell_volume > 0 else (999.0 if buy_volume > 0 else 0.0)
    buyer_volumes = {}
    for event in buys:
        buyer_volumes[event["trader"]] = buyer_volumes.get(event["trader"], 0.0) + event["sol_amount"]
    largest_buyer = max(buyer_volumes.values(), default=0.0)
    largest_pct = largest_buyer / buy_volume * 100 if buy_volume > 0 else 100.0
    prices = [event["price"] for event in events if _finite_positive(event.get("price")) is not None]
    price_change = ((prices[-1] / prices[0]) - 1) * 100 if len(prices) >= 2 and prices[0] > 0 else 0.0

    reasons = []
    if overflow:
        reasons.append("OBSERVATION_EVENT_LIMIT_REACHED")
    if len(events) < MIN_TRADES:
        reasons.append("OBSERVATION_TRADES_BELOW_MINIMUM")
    if len(buyer_volumes) < MIN_UNIQUE_BUYERS:
        reasons.append("OBSERVATION_UNIQUE_BUYERS_BELOW_MINIMUM")
    if buy_volume < MIN_BUY_VOLUME_SOL:
        reasons.append("OBSERVATION_BUY_VOLUME_BELOW_MINIMUM")
    if ratio < MIN_BUY_SELL_RATIO:
        reasons.append("OBSERVATION_BUY_SELL_RATIO_BELOW_MINIMUM")
    if largest_pct > MAX_SINGLE_BUYER_PCT:
        reasons.append("OBSERVATION_BUYER_CONCENTRATION_TOO_HIGH")
    if price_change < MIN_PRICE_CHANGE_PCT:
        reasons.append("OBSERVATION_PRICE_CHANGE_BELOW_MINIMUM")

    score = round(
        _component(len(events), MIN_TRADES, 20)
        + _component(len(buyer_volumes), MIN_UNIQUE_BUYERS, 20)
        + _component(buy_volume, MIN_BUY_VOLUME_SOL, 20)
        + _component(ratio, MIN_BUY_SELL_RATIO, 20)
        + (10 if largest_pct <= MAX_SINGLE_BUYER_PCT else 0)
        + (10 if price_change >= MIN_PRICE_CHANGE_PCT else 0)
    )
    if score < MIN_SCORE:
        reasons.append("OBSERVATION_SCORE_BELOW_MINIMUM")

    return {
        "status": "PASS" if not reasons else "WAIT",
        "ok": not reasons,
        "reason": "OBSERVATION_PASSED" if not reasons else ";".join(reasons),
        "reasons": reasons,
        "score": score,
        "trades": len(events),
        "buy_trades": len(buys),
        "sell_trades": len(sells),
        "unique_buyers": len(buyer_volumes),
        "buy_volume_sol": round(buy_volume, 9),
        "sell_volume_sol": round(sell_volume, 9),
        "buy_sell_ratio": round(ratio, 4),
        "largest_buyer_pct": round(largest_pct, 3),
        "price_change_pct": round(price_change, 4),
        "event_overflow": bool(overflow),
    }


def observe_candidate(mint, sources):
    """Block a supported Pump.fun candidate for one configured live trade window."""
    if not ENABLED:
        return {"status": "DISABLED", "ok": True, "reason": "OBSERVATION_DISABLED"}
    if not _is_supported_source(sources):
        return {"status": "SKIPPED", "ok": True, "reason": "OBSERVATION_SOURCE_UNSUPPORTED"}
    if not os.getenv("PUMPPORTAL_API_KEY", "").strip():
        return {"status": "WAIT", "ok": False, "reason": "PUMPPORTAL_API_KEY_REQUIRED_FOR_METERED_TRADE_FEED"}

    with _LOCK:
        if mint in _SESSIONS:
            return {"status": "WAIT", "ok": False, "reason": "OBSERVATION_ALREADY_ACTIVE"}
        if len(_SESSIONS) >= MAX_CONCURRENT:
            return {"status": "WAIT", "ok": False, "reason": "OBSERVATION_CAPACITY_REACHED"}
        session = {"events": [], "signatures": set(), "overflow": False, "started_at": time.time()}
        _SESSIONS[mint] = session
        _STATS["sessions_started"] += 1

    try:
        import sniper_scanner
        if not sniper_scanner.watch_token(mint):
            return {"status": "WAIT", "ok": False, "reason": "PUMPPORTAL_TRADE_SUBSCRIPTION_UNAVAILABLE"}
        time.sleep(DURATION_SECONDS)
        with _LOCK:
            captured = list(session["events"])
            overflow = session["overflow"]
        report = evaluate_events(captured, overflow=overflow)
        with _LOCK:
            _STATS["sessions_completed"] += 1
            _STATS["sessions_passed" if report["ok"] else "sessions_rejected"] += 1
            _STATS["last_completed_at"] = time.time()
            _STATS["last_result"] = report["status"]
            _STATS["last_error"] = None
        return report
    except Exception as error:
        with _LOCK:
            _STATS["last_error"] = type(error).__name__ + ": " + str(error)[:160]
        return {"status": "WAIT", "ok": False, "reason": "OBSERVATION_ERROR"}
    finally:
        with _LOCK:
            _SESSIONS.pop(mint, None)
        try:
            import sniper_scanner
            sniper_scanner.unwatch_token(mint)
        except Exception:
            pass


def get_status():
    with _LOCK:
        active = [
            {"mint": mint, "event_count": len(session["events"]),
             "age_seconds": round(max(0.0, time.time() - session["started_at"]), 2)}
            for mint, session in _SESSIONS.items()
        ]
        stats = dict(_STATS)
    return {
        "enabled": ENABLED,
        "provider": "PumpPortal",
        "api_key_configured": bool(os.getenv("PUMPPORTAL_API_KEY", "").strip()),
        "metered": True,
        "duration_seconds": DURATION_SECONDS,
        "max_concurrent": MAX_CONCURRENT,
        "active_sessions": active,
        "active_count": len(active),
        "limits": {
            "min_trades": MIN_TRADES,
            "min_unique_buyers": MIN_UNIQUE_BUYERS,
            "min_buy_volume_sol": MIN_BUY_VOLUME_SOL,
            "min_buy_sell_ratio": MIN_BUY_SELL_RATIO,
            "max_single_buyer_pct": MAX_SINGLE_BUYER_PCT,
            "min_price_change_pct": MIN_PRICE_CHANGE_PCT,
            "min_score": MIN_SCORE,
        },
        "stats": stats,
        "live_trading": "LOCKED",
        "note": "Trade subscriptions are metered. This observer never signs or broadcasts transactions.",
    }
