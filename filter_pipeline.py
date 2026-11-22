"""Composable, auditable token-filter pipeline for Trojan V2.

The pipeline is deliberately independent of any third-party implementation. It
uses deterministic local checks, preserves WAIT vs REJECT semantics, and only
enforces optional market thresholds when explicitly configured.
"""
import math
import os
import re
import time


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _get(data, *path):
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _env_float(name, default, minimum=0.0, maximum=None):
    value = _number(os.getenv(name, str(default)))
    if value is None or value < minimum or (maximum is not None and value > maximum):
        return float(default)
    return value


def _env_int(name, default, minimum=0, maximum=None):
    value = _number(os.getenv(name, str(default)))
    if value is None or int(value) != value or value < minimum or (maximum is not None and value > maximum):
        return int(default)
    return int(value)


def _result(status, stage, reason, metrics=None):
    return {
        "status": status,
        "stage": stage,
        "reason": reason,
        "metrics": metrics or {},
    }


def _metadata_stage(token, mint):
    symbol = token.get("symbol") or _get(token, "baseToken", "symbol")
    name = token.get("name") or _get(token, "baseToken", "name")
    if not isinstance(mint, str) or not mint.strip():
        return _result("REJECT", "metadata", "MINT_MISSING")
    if not isinstance(symbol, str) or not symbol.strip():
        return _result("WAIT", "metadata", "SYMBOL_UNKNOWN")
    normalized = symbol.strip().strip("\x00").strip()
    if not normalized:
        return _result("WAIT", "metadata", "SYMBOL_UNKNOWN")
    if normalized.isdigit():
        return _result("REJECT", "metadata", "NUMERIC_SYMBOL")
    if len(normalized) == 1 and not normalized.isascii():
        return _result("REJECT", "metadata", "SUSPICIOUS_SINGLE_CHARACTER_SYMBOL")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        return _result("WAIT", "metadata", "NAME_INVALID")
    return _result("PASS", "metadata", "METADATA_VALID", {"symbol": normalized})


def _market_stage(token, strategy):
    price = _number(token.get("priceUsd"))
    liquidity = _number(_get(token, "liquidity", "usd"))
    if price is None:
        return _result("WAIT", "market", "PRICE_UNKNOWN")
    if price <= 0:
        return _result("REJECT", "market", "INVALID_PRICE", {"price_usd": price})
    if liquidity is None:
        return _result("WAIT", "market", "LIQUIDITY_UNKNOWN")
    if liquidity <= 0:
        return _result("REJECT", "market", "ZERO_LIQUIDITY", {"liquidity_usd": liquidity})

    min_liquidity = _env_float(
        "TROJAN_FILTER_MIN_LIQUIDITY_USD",
        2500.0 if strategy == "sniper" else 0.0,
        0.0,
        1_000_000_000.0,
    )
    if liquidity < min_liquidity:
        return _result("WAIT", "market", "LIQUIDITY_BELOW_MINIMUM", {
            "liquidity_usd": liquidity, "minimum_usd": min_liquidity
        })

    base_mint = _get(token, "baseToken", "address")
    if base_mint and token.get("address") and base_mint != token.get("address"):
        return _result("REJECT", "market", "BASE_MINT_MISMATCH", {
            "candidate_mint": token.get("address"), "base_mint": base_mint
        })
    return _result("PASS", "market", "MARKET_DATA_VALID", {
        "price_usd": price, "liquidity_usd": liquidity
    })


def _activity_stage(token):
    volume_5m = _number(_get(token, "volume", "m5"))
    volume_24h = _number(_get(token, "volume", "h24"))
    buys = _number(_get(token, "txns", "m5", "buys"))
    sells = _number(_get(token, "txns", "m5", "sells"))
    min_volume = _env_float("TROJAN_FILTER_MIN_VOLUME_5M_USD", 0.0, 0.0, 1_000_000_000.0)
    min_txns = _env_int("TROJAN_FILTER_MIN_TXNS_5M", 0, 0, 1_000_000)
    min_buy_ratio = _env_float("TROJAN_FILTER_MIN_BUY_RATIO", 0.0, 0.0, 1.0)

    if min_volume > 0:
        if volume_5m is None:
            return _result("WAIT", "activity", "VOLUME_5M_UNKNOWN")
        if volume_5m < min_volume:
            return _result("WAIT", "activity", "VOLUME_5M_BELOW_MINIMUM", {
                "volume_5m_usd": volume_5m, "minimum_usd": min_volume
            })

    total_txns = buys + sells if buys is not None and sells is not None else None
    if min_txns > 0:
        if total_txns is None:
            return _result("WAIT", "activity", "TRANSACTION_FLOW_UNKNOWN")
        if total_txns < min_txns:
            return _result("WAIT", "activity", "TRANSACTION_COUNT_BELOW_MINIMUM", {
                "transactions_5m": total_txns, "minimum": min_txns
            })

    buy_ratio = buys / total_txns if total_txns and buys is not None else None
    if min_buy_ratio > 0:
        if buy_ratio is None:
            return _result("WAIT", "activity", "BUY_RATIO_UNKNOWN")
        if buy_ratio < min_buy_ratio:
            return _result("REJECT", "activity", "BUY_PRESSURE_BELOW_MINIMUM", {
                "buy_ratio": round(buy_ratio, 4), "minimum": min_buy_ratio
            })

    if volume_5m is not None and volume_5m < 0 or volume_24h is not None and volume_24h < 0:
        return _result("REJECT", "activity", "NEGATIVE_VOLUME")
    if buys is not None and buys < 0 or sells is not None and sells < 0:
        return _result("REJECT", "activity", "NEGATIVE_TRANSACTION_COUNT")
    return _result("PASS", "activity", "ACTIVITY_VALID", {
        "volume_5m_usd": volume_5m,
        "volume_24h_usd": volume_24h,
        "transactions_5m": total_txns,
        "buy_ratio": round(buy_ratio, 4) if buy_ratio is not None else None,
    })


def _momentum_stage(token):
    change = _number(_get(token, "priceChange", "m5"))
    min_change = _env_float("TROJAN_FILTER_MIN_CHANGE_5M_PCT", -100.0, -100.0, 10000.0)
    max_change = _env_float("TROJAN_FILTER_MAX_CHANGE_5M_PCT", 10000.0, -100.0, 10000.0)
    if min_change > max_change:
        min_change, max_change = -100.0, 10000.0
    if change is None:
        if os.getenv("TROJAN_FILTER_REQUIRE_M5_CHANGE", "false").lower() == "true":
            return _result("WAIT", "momentum", "M5_MOMENTUM_UNKNOWN")
        return _result("PASS", "momentum", "MOMENTUM_NOT_CONFIGURED")
    if change < min_change:
        return _result("REJECT", "momentum", "M5_DOWNTREND_TOO_STRONG", {
            "change_5m_pct": change, "minimum_pct": min_change
        })
    if change > max_change:
        return _result("REJECT", "momentum", "M5_MOVE_EXTENDED", {
            "change_5m_pct": change, "maximum_pct": max_change
        })
    return _result("PASS", "momentum", "MOMENTUM_WITHIN_BOUNDS", {
        "change_5m_pct": change
    })


def evaluate_token(token, mint, strategy="main"):
    """Run the ordered local filter stages and return the first non-pass result.

    Missing optional market signals are WAIT only when their corresponding
    requirement is enabled. This avoids treating absent provider data as zero.
    """
    token = token if isinstance(token, dict) else {}
    stages = (
        _metadata_stage(token, mint),
        _market_stage(token, strategy),
        _activity_stage(token),
        _momentum_stage(token),
    )
    for item in stages:
        if item["status"] != "PASS":
            return {
                "status": item["status"],
                "reason": item["reason"],
                "stage": item["stage"],
                "decisions": list(stages[:stages.index(item) + 1]),
                "evaluated_at": time.time(),
            }
    return {
        "status": "PASS",
        "reason": "ALL_FILTER_STAGES_PASSED",
        "stage": "complete",
        "decisions": list(stages),
        "evaluated_at": time.time(),
    }
