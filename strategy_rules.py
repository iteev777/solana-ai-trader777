"""Configurable, safe strategy rule trees for Trojan V2.

Set TROJAN_STRATEGY_RULES to JSON, for example:
{
  "main": {"all": [
    {"field": "liquidity_usd", "op": "gte", "value": 12000},
    {"field": "buy_ratio", "op": "gte", "value": 0.58},
    {"field": "price_change_m5", "op": "between", "value": [0.1, 4.0]}
  ]},
  "sniper": {"all": [
    {"field": "liquidity_usd", "op": "gte", "value": 3000},
    {"field": "buy_ratio", "op": "gte", "value": 0.55}
  ]}
}

No configured rules means the existing strategy remains unchanged.
Expressions support all / any / not and numeric comparisons only.
"""
import json
import os
import time
import math

ALLOWED_FIELDS = {
    "liquidity_usd", "volume_m5", "volume_h24",
    "price_change_m5", "price_change_h1", "price_change_h24",
    "buy_ratio", "score", "source_count", "txns_m5_total",
    "pair_age_minutes", "rsi_14", "ema_9", "ema_21",
    "ema_cross_bullish", "breakout_20", "volume_spike_ratio"
}
ALLOWED_OPERATORS = {"gt", "gte", "lt", "lte", "eq", "between"}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _context(token, score):
    token = token if isinstance(token, dict) else {}
    liquidity = token.get("liquidity")
    volume = token.get("volume")
    changes = token.get("priceChange")
    txns = token.get("txns")
    liquidity = liquidity if isinstance(liquidity, dict) else {}
    volume = volume if isinstance(volume, dict) else {}
    changes = changes if isinstance(changes, dict) else {}
    txns = txns if isinstance(txns, dict) else {}
    m5 = txns.get("m5") if isinstance(txns.get("m5"), dict) else {}
    buys = _number(m5.get("buys"))
    sells = _number(m5.get("sells"))
    total = buys + sells if buys is not None and sells is not None else None
    pair_created = _number(token.get("pairCreatedAt"))
    age = max(0, (time.time() * 1000 - pair_created) / 60000) if pair_created else None
    sources = token.get("discovery_sources", [])
    indicators = token.get("indicators") if isinstance(token.get("indicators"), dict) else {}
    return {
        "liquidity_usd": _number(liquidity.get("usd")),
        "volume_m5": _number(volume.get("m5")),
        "volume_h24": _number(volume.get("h24")),
        "price_change_m5": _number(changes.get("m5")),
        "price_change_h1": _number(changes.get("h1")),
        "price_change_h24": _number(changes.get("h24")),
        "buy_ratio": buys / total if total and buys is not None else None,
        "score": _number(score),
        "source_count": len(sources) if isinstance(sources, list) else 0,
        "txns_m5_total": total,
        "pair_age_minutes": age,
        "rsi_14": _number(indicators.get("rsi_14")),
        "ema_9": _number(indicators.get("ema_9")),
        "ema_21": _number(indicators.get("ema_21")),
        "ema_cross_bullish": 1.0 if indicators.get("ema_cross_bullish") is True else 0.0 if indicators.get("ema_cross_bullish") is False else None,
        "breakout_20": 1.0 if indicators.get("breakout_20") is True else 0.0 if indicators.get("breakout_20") is False else None,
        "volume_spike_ratio": _number(indicators.get("volume_spike_ratio")),\n        "timeframe_ready_count": _number(indicators.get("timeframe_ready_count")),\n        "bullish_timeframes": _number(indicators.get("bullish_timeframes")),\n        "bearish_timeframes": _number(indicators.get("bearish_timeframes"))
    }


def _evaluate(node, context, path="root"):
    if not isinstance(node, dict):
        return False, [path + ": invalid rule node"]
    groups = [key for key in ("all", "any", "not") if key in node]
    if groups:
        if len(groups) != 1:
            return False, [path + ": exactly one group operator is required"]
        key = groups[0]
        children = node[key]
        if not isinstance(children, list) or not children:
            return False, [path + ": rule group must contain conditions"]
        results = [_evaluate(child, context, path + "." + key + "[" + str(i) + "]") for i, child in enumerate(children)]
        if key == "all":
            passed = all(item[0] for item in results)
        elif key == "any":
            passed = any(item[0] for item in results)
        else:
            if len(results) != 1:
                return False, [path + ": not requires exactly one condition"]
            passed = not results[0][0]
        reasons = [reason for ok, messages in results if not ok for reason in messages]
        return passed, ([] if passed else reasons or [path + ": group failed"])
    field = node.get("field")
    op = node.get("op")
    if field not in ALLOWED_FIELDS or op not in ALLOWED_OPERATORS:
        return False, [path + ": unsupported field or operator"]
    actual = context.get(field)
    if actual is None:
        return False, [field + ": data unavailable"]
    expected = node.get("value")
    if op == "between":
        if not isinstance(expected, list) or len(expected) != 2:
            return False, [field + ": between expects [min, max]"]
        low, high = _number(expected[0]), _number(expected[1])
        if low is None or high is None or low > high:
            return False, [field + ": invalid range"]
        passed = low <= actual <= high
        description = str(low) + "–" + str(high)
    else:
        target = _number(expected)
        if target is None:
            return False, [field + ": invalid numeric threshold"]
        passed = {
            "gt": actual > target, "gte": actual >= target,
            "lt": actual < target, "lte": actual <= target,
            "eq": actual == target
        }[op]
        description = op + " " + str(target)
    return passed, ([] if passed else [field + " failed (" + str(actual) + " " + description + ")"])


def _validate_node(node, path="root"):
    if not isinstance(node, dict):
        raise ValueError(path + ": rule must be an object")
    groups = [key for key in ("all", "any", "not") if key in node]
    if groups:
        if len(groups) != 1 or len(node) != 1:
            raise ValueError(path + ": use exactly one group operator")
        key = groups[0]
        children = node[key]
        if not isinstance(children, list) or not children or len(children) > 30:
            raise ValueError(path + ": group must contain 1-30 rules")
        if key == "not" and len(children) != 1:
            raise ValueError(path + ": not requires one rule")
        for index, child in enumerate(children):
            _validate_node(child, path + "." + key + "[" + str(index) + "]")
        return
    if set(node) != {"field", "op", "value"}:
        raise ValueError(path + ": condition needs field, op and value")
    if node["field"] not in ALLOWED_FIELDS or node["op"] not in ALLOWED_OPERATORS:
        raise ValueError(path + ": unsupported field or operator")
    if node["op"] == "between":
        value = node["value"]
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError(path + ": between needs two numeric values")
        low, high = _number(value[0]), _number(value[1])
        if low is None or high is None or low > high:
            raise ValueError(path + ": invalid between range")
    elif _number(node["value"]) is None:
        raise ValueError(path + ": value must be finite numeric")


def validate_rules_config(config):
    if not isinstance(config, dict):
        raise ValueError("Top-level JSON must be an object")
    unknown = set(config) - {"main", "sniper"}
    if unknown:
        raise ValueError("Unknown strategy names: " + ", ".join(sorted(unknown)))
    for name, rule in config.items():
        _validate_node(rule, name)
    return config


def get_config():
    raw = os.getenv("TROJAN_STRATEGY_RULES", "").strip()
    try:
        config = json.loads(raw) if raw else {}
        if not isinstance(config, dict):
            raise ValueError("Top-level JSON must be an object")
        # Dashboard-saved rules override environment rules per strategy.
        try:
            import database
            saved = database.get_strategy_configs()
            for name, item in saved.items():
                saved_rule = item.get("config")
                if saved_rule == {}:
                    config.pop(name, None)
                else:
                    config[name] = saved_rule
        except Exception:
            pass
        validate_rules_config(config)
        return {"enabled": bool(config), "valid": True, "rules": config, "error": None}
    except (ValueError, TypeError) as error:
        return {"enabled": True, "valid": False, "rules": {}, "error": str(error)[:300]}


def evaluate_strategy_rules(token, score, strategy):
    config = get_config()
    if not config["enabled"]:
        return True, "No custom strategy rules configured"
    if not config["valid"]:
        return False, "Custom strategy rules invalid: " + str(config["error"])
    rule = config["rules"].get(strategy)
    if rule is None:
        return True, "No custom rules for " + strategy
    passed, reasons = _evaluate(rule, _context(token, score))
    return passed, "Custom rules passed" if passed else "; ".join(reasons)


def get_strategy_rules_status():
    config = get_config()
    saved = {}
    try:
        import database
        saved = database.get_strategy_configs()
    except Exception:
        pass
    return {
        "enabled": config["enabled"],
        "valid": config["valid"],
        "strategies_configured": sorted(config["rules"].keys()),
        "rules": config["rules"],
        "updated_at": {name: item.get("updated_at") for name, item in saved.items()},
        "error": config["error"]
    }
