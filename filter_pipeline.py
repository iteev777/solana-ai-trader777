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


def _clean_metadata_text(value):
    if not isinstance(value, str):
        return ""
    return value.strip().strip("".join(chr(i) for i in range(0, 33)) + chr(127)).strip()


def _metadata_stage(token, mint):
    symbol = token.get("symbol") or _get(token, "baseToken", "symbol")
    name_raw = token.get("name") if "name" in token else _get(token, "baseToken", "name")
    name = _clean_metadata_text(name_raw)
    symbol = _clean_metadata_text(symbol)

    if not isinstance(mint, str) or not mint.strip():
        return _result("REJECT", "metadata", "MINT_MISSING")
    if not symbol:
        return _result("WAIT", "metadata", "SYMBOL_UNKNOWN")
    if symbol.isascii() and symbol.isdigit():
        return _result("REJECT", "metadata", "NUMERIC_SYMBOL")
    reject_single = os.getenv("TROJAN_FILTER_REJECT_SINGLE_CHAR_SYMBOL", "true").lower() == "true"
    if reject_single and len(symbol) == 1 and not (symbol.isascii() and symbol.isalpha()):
        return _result("REJECT", "metadata", "SUSPICIOUS_SINGLE_CHARACTER_SYMBOL")
    if not name and os.getenv("TROJAN_FILTER_REQUIRE_NAME_AND_SYMBOL", "false").lower() == "true":
        return _result("WAIT", "metadata", "NAME_UNKNOWN" if name_raw is None else "NAME_INVALID")

    info = token.get("info") if isinstance(token.get("info"), dict) else {}
    token_meta = token.get("tokenMeta") if isinstance(token.get("tokenMeta"), dict) else {}
    logo = (
        token.get("logoURI") or token.get("logo_uri") or token.get("logo_url")
        or token.get("imageUrl") or info.get("imageUrl") or token_meta.get("image")
    )
    require_logo = os.getenv("TROJAN_FILTER_REQUIRE_LOGO_URL", "false").lower() == "true"
    if require_logo and not (isinstance(logo, str) and logo.strip().startswith(("https://", "http://"))):
        return _result("WAIT", "metadata", "LOGO_URL_MISSING")

    websites = token.get("websites")
    if not isinstance(websites, list):
        websites = info.get("websites") if isinstance(info.get("websites"), list) else []
    has_website = any(
        isinstance(item, dict) and isinstance(item.get("url"), str)
        and item["url"].strip().startswith(("https://", "http://"))
        for item in websites
    )
    direct_website = token.get("website") or token.get("website_url")
    has_website = has_website or (
        isinstance(direct_website, str)
        and direct_website.strip().startswith(("https://", "http://"))
    )
    if os.getenv("TROJAN_FILTER_REQUIRE_WEBSITE_URL", "false").lower() == "true" and not has_website:
        return _result("WAIT", "metadata", "WEBSITE_URL_MISSING")

    return _result("PASS", "metadata", "METADATA_VALID", {
        "symbol": symbol,
        "name": name,
        "logo_available": isinstance(logo, str) and logo.strip().startswith(("https://", "http://")),
        "website_available": has_website,
    })


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
    max_liquidity = _env_float(
        "TROJAN_FILTER_MAX_LIQUIDITY_USD", 0.0, 0.0, 1_000_000_000.0
    )
    if liquidity < min_liquidity:
        return _result("WAIT", "market", "LIQUIDITY_BELOW_MINIMUM", {
            "liquidity_usd": liquidity, "minimum_usd": min_liquidity
        })
    if max_liquidity > 0 and liquidity > max_liquidity:
        return _result("REJECT", "market", "LIQUIDITY_ABOVE_MAXIMUM", {
            "liquidity_usd": liquidity, "maximum_usd": max_liquidity
        })

    base_mint = _get(token, "baseToken", "address")
    if base_mint and token.get("address") and base_mint != token.get("address"):
        return _result("REJECT", "market", "BASE_MINT_MISMATCH", {
            "candidate_mint": token.get("address"), "base_mint": base_mint
        })
    return _result("PASS", "market", "MARKET_DATA_VALID", {
        "price_usd": price, "liquidity_usd": liquidity
    })



def _pool_metrics_stage(token):
    """Apply optional pool-count and reserve floors to observed registry data."""
    raw_count = _first_value(token.get("pool_count"), token.get("poolCount"))
    count = _number(raw_count)
    min_count = _env_int("TROJAN_FILTER_MIN_POOL_COUNT", 0, 0, 100000)
    max_count = _env_int("TROJAN_FILTER_MAX_POOL_COUNT", 0, 0, 100000)

    if raw_count is not None and (
        count is None or count < 0 or int(count) != count
    ):
        return _result("REJECT", "pool_metrics", "INVALID_POOL_COUNT", {
            "pool_count": raw_count
        })
    if min_count > 0 and count is None:
        return _result("WAIT", "pool_metrics", "POOL_COUNT_UNKNOWN", {
            "minimum": min_count
        })
    if count is not None and min_count > 0 and count < min_count:
        return _result("REJECT", "pool_metrics", "POOL_COUNT_BELOW_MINIMUM", {
            "pool_count": int(count), "minimum": min_count
        })
    if count is not None and max_count > 0 and count > max_count:
        return _result("REJECT", "pool_metrics", "POOL_COUNT_ABOVE_MAXIMUM", {
            "pool_count": int(count), "maximum": max_count
        })

    raw_reserve = _first_value(
        token.get("reserve_in_usd"),
        token.get("reserveInUsd"),
        _get(token, "pool_metrics", "reserve_in_usd"),
    )
    reserve = _number(raw_reserve)
    min_reserve = _env_float(
        "TROJAN_FILTER_MIN_POOL_RESERVE_USD", 0.0, 0.0, 1e15
    )
    if raw_reserve is not None and (reserve is None or reserve < 0):
        return _result("REJECT", "pool_metrics", "INVALID_POOL_RESERVE", {
            "reserve_in_usd": raw_reserve
        })
    if min_reserve > 0 and reserve is None:
        return _result("WAIT", "pool_metrics", "POOL_RESERVE_UNKNOWN", {
            "minimum_usd": min_reserve
        })
    if reserve is not None and min_reserve > 0 and reserve < min_reserve:
        return _result("REJECT", "pool_metrics", "POOL_RESERVE_BELOW_MINIMUM", {
            "reserve_in_usd": reserve, "minimum_usd": min_reserve
        })

    return _result("PASS", "pool_metrics", "POOL_METRICS_WITHIN_BOUNDS", {
        "pool_count": int(count) if count is not None else None,
        "reserve_in_usd": reserve,
        "minimum_pool_count": min_count,
        "maximum_pool_count": max_count,
        "minimum_reserve_usd": min_reserve,
    })


def _activity_stage(token):
    """Validate independent activity windows without treating missing data as zero."""
    windows = (
        ("m5", "5M", "TROJAN_FILTER_MIN_VOLUME_5M_USD", "TROJAN_FILTER_MIN_TXNS_5M"),
        ("h1", "1H", "TROJAN_FILTER_MIN_VOLUME_1H_USD", "TROJAN_FILTER_MIN_TXNS_1H"),
        ("h6", "6H", "TROJAN_FILTER_MIN_VOLUME_6H_USD", "TROJAN_FILTER_MIN_TXNS_6H"),
        ("h24", "24H", "TROJAN_FILTER_MIN_VOLUME_24H_USD", "TROJAN_FILTER_MIN_TXNS_24H"),
    )
    volume = token.get("volume") if isinstance(token.get("volume"), dict) else {}
    txns = token.get("txns") if isinstance(token.get("txns"), dict) else {}
    metrics = {"windows": {}}

    for key, label, volume_env, txns_env in windows:
        volume_value = _number(volume.get(key))
        txn_data = txns.get(key) if isinstance(txns.get(key), dict) else {}
        buys = _number(txn_data.get("buys"))
        sells = _number(txn_data.get("sells"))
        if volume_value is not None and volume_value < 0:
            return _result("REJECT", "activity", "NEGATIVE_VOLUME", {
                "window": key, "volume_usd": volume_value
            })
        if any(value is not None and (value < 0 or int(value) != value)
               for value in (buys, sells)):
            return _result("REJECT", "activity", "INVALID_TRANSACTION_COUNT", {
                "window": key, "buys": buys, "sells": sells
            })

        legacy_volume_min = 0.0
        legacy_txns_min = 0
        if key == "m5":
            legacy_volume_min = _env_float("TROJAN_FILTER_MIN_VOLUME_5M_USD", 0.0, 0.0, 1_000_000_000.0)
            legacy_txns_min = _env_int("TROJAN_FILTER_MIN_TXNS_5M", 0, 0, 1_000_000)
        min_volume = _env_float(volume_env, legacy_volume_min, 0.0, 1_000_000_000.0)
        min_txns = _env_int(txns_env, legacy_txns_min, 0, 1_000_000)

        if min_volume > 0:
            if volume_value is None:
                return _result("WAIT", "activity", "VOLUME_" + label + "_UNKNOWN", {
                    "window": key, "minimum_usd": min_volume
                })
            if volume_value < min_volume:
                return _result("WAIT", "activity", "VOLUME_" + label + "_BELOW_MINIMUM", {
                    "window": key, "volume_usd": volume_value, "minimum_usd": min_volume
                })

        total = buys + sells if buys is not None and sells is not None else (
            buys if buys is not None else sells
        )
        if min_txns > 0:
            if total is None:
                reason = "TRANSACTION_FLOW_UNKNOWN" if key == "m5" else "TRANSACTION_FLOW_" + label + "_UNKNOWN"
                return _result("WAIT", "activity", reason, {
                    "window": key, "minimum": min_txns
                })
            if total < min_txns:
                reason = "TRANSACTION_COUNT_BELOW_MINIMUM" if key == "m5" else "TRANSACTION_COUNT_" + label + "_BELOW_MINIMUM"
                return _result("WAIT", "activity", reason, {
                    "window": key, "transactions": total, "minimum": min_txns
                })

        ratio_env = "TROJAN_FILTER_MIN_BUY_RATIO_" + label
        default_ratio = _env_float("TROJAN_FILTER_MIN_BUY_RATIO", 0.0, 0.0, 1.0) if key == "m5" else 0.0
        min_ratio = _env_float(ratio_env, default_ratio, 0.0, 1.0)
        buy_ratio = buys / (buys + sells) if buys is not None and sells is not None and (buys + sells) > 0 else None
        if min_ratio > 0:
            if buy_ratio is None:
                return _result("WAIT", "activity", "BUY_RATIO_" + label + "_UNKNOWN", {
                    "window": key, "minimum": min_ratio
                })
            if buy_ratio < min_ratio:
                reason = "BUY_PRESSURE_BELOW_MINIMUM" if key == "m5" else "BUY_PRESSURE_" + label + "_BELOW_MINIMUM"
                return _result("REJECT", "activity", reason, {
                    "window": key, "buy_ratio": round(buy_ratio, 4), "minimum": min_ratio
                })

        metrics["windows"][key] = {
            "volume_usd": volume_value,
            "buys": buys,
            "sells": sells,
            "transactions": total,
            "buy_ratio": round(buy_ratio, 4) if buy_ratio is not None else None,
        }

    return _result("PASS", "activity", "ACTIVITY_VALID", metrics)


def _momentum_stage(token):
    """Apply separate price-change bands to every market-data horizon."""
    changes = token.get("priceChange") if isinstance(token.get("priceChange"), dict) else {}
    windows = (
        ("m5", "5M", "TROJAN_FILTER_MIN_CHANGE_5M_PCT", "TROJAN_FILTER_MAX_CHANGE_5M_PCT"),
        ("h1", "1H", "TROJAN_FILTER_MIN_CHANGE_1H_PCT", "TROJAN_FILTER_MAX_CHANGE_1H_PCT"),
        ("h6", "6H", "TROJAN_FILTER_MIN_CHANGE_6H_PCT", "TROJAN_FILTER_MAX_CHANGE_6H_PCT"),
        ("h24", "24H", "TROJAN_FILTER_MIN_CHANGE_24H_PCT", "TROJAN_FILTER_MAX_CHANGE_24H_PCT"),
    )
    metrics = {}
    for key, label, min_env, max_env in windows:
        change = _number(changes.get(key))
        legacy_min = -100.0
        legacy_max = 10000.0
        if key == "m5":
            legacy_min = _env_float(min_env, -100.0, -100.0, 10000.0)
            legacy_max = _env_float(max_env, 10000.0, -100.0, 10000.0)
        minimum = _env_float(min_env, legacy_min, -100.0, 10000.0)
        maximum = _env_float(max_env, legacy_max, -100.0, 10000.0)
        if minimum > maximum:
            minimum, maximum = -100.0, 10000.0
        require_name = "TROJAN_FILTER_REQUIRE_" + label + "_CHANGE"
        required = os.getenv(require_name, "false").lower() == "true"
        if change is None:
            if required:
                reason = "M5_MOMENTUM_UNKNOWN" if key == "m5" else "MOMENTUM_" + label + "_UNKNOWN"
                return _result("WAIT", "momentum", reason, {
                    "window": key, "minimum_pct": minimum, "maximum_pct": maximum
                })
            metrics[key] = None
            continue
        if change < minimum:
            reason = "M5_DOWNTREND_TOO_STRONG" if key == "m5" else "MOMENTUM_" + label + "_BELOW_MINIMUM"
            return _result("REJECT", "momentum", reason, {
                "window": key, "change_pct": change, "minimum_pct": minimum
            })
        if change > maximum:
            reason = "M5_MOVE_EXTENDED" if key == "m5" else "MOMENTUM_" + label + "_ABOVE_MAXIMUM"
            return _result("REJECT", "momentum", reason, {
                "window": key, "change_pct": change, "maximum_pct": maximum
            })
        metrics[key] = change
    return _result("PASS", "momentum", "MOMENTUM_WITHIN_BOUNDS", {"windows": metrics})


def _age_stage(token):
    created = _number(token.get("pairCreatedAt"))
    if created is None:
        created = _number(token.get("created_at_ms"))
    if created is None:
        if os.getenv("TROJAN_FILTER_REQUIRE_POOL_AGE", "false").lower() == "true":
            return _result("WAIT", "age", "POOL_AGE_UNKNOWN")
        return _result("PASS", "age", "POOL_AGE_NOT_REQUIRED")
    # Providers normally expose milliseconds; tolerate Unix seconds explicitly.
    created_seconds = created / 1000.0 if created > 10_000_000_000 else created
    age_minutes = max(0.0, (time.time() - created_seconds) / 60.0)
    minimum = _env_float("TROJAN_FILTER_MIN_POOL_AGE_MINUTES", 0.0, 0.0, 10_000_000.0)
    maximum = _env_float("TROJAN_FILTER_MAX_POOL_AGE_MINUTES", 10_000_000.0, 0.0, 10_000_000.0)
    if age_minutes < minimum:
        return _result("WAIT", "age", "POOL_TOO_NEW", {
            "age_minutes": round(age_minutes, 2), "minimum_minutes": minimum
        })
    if age_minutes > maximum:
        return _result("REJECT", "age", "POOL_TOO_OLD", {
            "age_minutes": round(age_minutes, 2), "maximum_minutes": maximum
        })
    return _result("PASS", "age", "POOL_AGE_WITHIN_BOUNDS", {
        "age_minutes": round(age_minutes, 2)
    })


def _valuation_stage(token):
    fdv = _number(_first_value(token.get("fdv"), token.get("fullyDilutedValuation")))
    market_cap = _number(_first_value(token.get("marketCap"), token.get("market_cap")))
    liquidity = _number(_get(token, "liquidity", "usd"))
    if (fdv is not None and fdv < 0) or (market_cap is not None and market_cap < 0):
        return _result("REJECT", "valuation", "NEGATIVE_VALUATION")

    rules = (
        ("FDV", fdv, "TROJAN_FILTER_MIN_FDV_USD", "TROJAN_FILTER_MAX_FDV_USD"),
        ("MARKET_CAP", market_cap, "TROJAN_FILTER_MIN_MARKET_CAP_USD", "TROJAN_FILTER_MAX_MARKET_CAP_USD"),
    )
    for label, value, min_env, max_env in rules:
        minimum = _env_float(min_env, 0.0, 0.0, 1e18)
        maximum = _env_float(max_env, 1e15, 0.0, 1e18)
        if minimum > 0 and value is None:
            return _result("WAIT", "valuation", label + "_UNKNOWN", {
                "minimum_usd": minimum
            })
        if value is not None and minimum > 0 and value < minimum:
            return _result("REJECT", "valuation", label + "_BELOW_MINIMUM", {
                "value_usd": value, "minimum_usd": minimum
            })
        if value is not None and maximum > 0 and value > maximum:
            return _result("REJECT", "valuation", label + "_ABOVE_MAXIMUM", {
                "value_usd": value, "maximum_usd": maximum
            })

    min_liquidity_ratio = _env_float(
        "TROJAN_FILTER_MIN_LIQUIDITY_FDV_RATIO", 0.0, 0.0, 100.0
    )
    if min_liquidity_ratio > 0:
        valuation = fdv if fdv is not None else market_cap
        if valuation is None or valuation <= 0 or liquidity is None:
            return _result("WAIT", "valuation", "LIQUIDITY_VALUATION_RATIO_UNKNOWN")
        ratio = liquidity / valuation
        if ratio < min_liquidity_ratio:
            return _result("REJECT", "valuation", "LIQUIDITY_VALUATION_RATIO_TOO_LOW", {
                "liquidity_to_fdv_ratio": round(ratio, 6),
                "minimum_ratio": min_liquidity_ratio
            })
        return _result("PASS", "valuation", "VALUATION_WITHIN_BOUNDS", {
            "fdv_usd": fdv, "market_cap_usd": market_cap,
            "liquidity_to_fdv_ratio": round(ratio, 6)
        })
    return _result("PASS", "valuation", "VALUATION_WITHIN_BOUNDS", {
        "fdv_usd": fdv, "market_cap_usd": market_cap
    })


def _first_value(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _provenance_stage(token):
    sources = token.get("discovery_sources", token.get("sources", []))
    if not isinstance(sources, list):
        sources = []
    normalized = {
        value.strip().lower() for value in sources
        if isinstance(value, str) and value.strip()
    }
    minimum = _env_int("TROJAN_FILTER_MIN_DISCOVERY_SOURCES", 0, 0, 20)
    if len(normalized) < minimum:
        return _result("WAIT", "provenance", "SOURCE_AGREEMENT_BELOW_MINIMUM", {
            "source_count": len(normalized), "minimum": minimum
        })
    return _result("PASS", "provenance", "SOURCE_PROVENANCE_VALID", {
        "source_count": len(normalized), "sources": sorted(normalized)
    })


def _quality_stage(token):
    minimum = _env_float("TROJAN_FILTER_MIN_QUALITY_SCORE", 0.0, 0.0, 100.0)
    if minimum <= 0:
        return _result("PASS", "quality", "QUALITY_GATE_DISABLED")
    quality = token.get("candidate_quality")
    score = _number(quality.get("score")) if isinstance(quality, dict) else None
    tier = quality.get("tier") if isinstance(quality, dict) else None
    if score is None:
        return _result("WAIT", "quality", "CANDIDATE_QUALITY_UNKNOWN")
    if score < minimum:
        return _result("REJECT", "quality", "CANDIDATE_QUALITY_BELOW_MINIMUM", {
            "quality_score": score, "minimum": minimum, "tier": tier
        })
    return _result("PASS", "quality", "CANDIDATE_QUALITY_ACCEPTED", {
        "quality_score": score, "tier": tier
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
        _age_stage(token),
        _valuation_stage(token),
        _pool_metrics_stage(token),
        _activity_stage(token),
        _momentum_stage(token),
        _provenance_stage(token),
        _quality_stage(token),
    )
    decisions = []
    for item in stages:
        decisions.append(item)
        if item["status"] != "PASS":
            return {
                "status": item["status"],
                "reason": item["reason"],
                "stage": item["stage"],
                "decisions": decisions,
                "evaluated_at": time.time(),
            }
    return {
        "status": "PASS",
        "reason": "ALL_FILTER_STAGES_PASSED",
        "stage": "complete",
        "decisions": list(stages),
        "evaluated_at": time.time(),
    }
