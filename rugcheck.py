# ==========================================
# TROJAN V2 - RUGCHECK SECURITY ANALYZER
# ==========================================

import requests
import math
import time
import threading


API_URL = "https://api.rugcheck.xyz/v1/tokens/"

REQUEST_TIMEOUT = 20
CACHE_SECONDS = 60

MAX_SAFE_SCORE = 40

_cache = {}
_cache_lock = threading.RLock()


# ==========================================
# HELPERS
# ==========================================

def get_number(value):

    try:
        number = float(value)

        if not math.isfinite(number):
            return None

        return number

    except (ValueError, TypeError):
        return None


def get_authority_status(data, authority_name):

    if not isinstance(data, dict):
        return "UNKNOWN"

    if authority_name not in data:
        return "UNKNOWN"

    value = data[authority_name]

    if value is None or value is False or value == "":
        return "DISABLED"

    if value is True:
        return "ACTIVE"

    if isinstance(value, str):
        return "ACTIVE"

    return "UNKNOWN"


def get_risk_level(risk):

    if not isinstance(risk, dict):
        return "UNKNOWN"

    level = str(risk.get("level", "")).lower().strip()

    if level in ("danger", "critical", "high"):
        return "HIGH"

    if level in ("warn", "warning", "medium"):
        return "MEDIUM"

    if level in ("info", "low"):
        return "LOW"

    return "UNKNOWN"


# ==========================================
# SECURITY ANALYSIS
# ==========================================

def _first_present(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _safe_ratio(numerator, denominator):
    numerator = get_number(numerator)
    denominator = get_number(denominator)
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator * 100.0


def _risk_threshold(name, default, minimum=0.0, maximum=100.0):
    try:
        value = float(__import__("os").getenv(name, str(default)))
        if not math.isfinite(value) or value < minimum or value > maximum:
            return float(default)
        return value
    except (TypeError, ValueError, OverflowError):
        return float(default)


def analyze_rugcheck_report(data):
    """Normalize RugCheck risk signals into one auditable, fail-closed decision."""
    empty = {
        "status": "UNKNOWN", "safe": False, "score": None,
        "risk_count": 0, "high_risks": 0, "medium_risks": 0,
        "low_risks": 0, "unknown_risks": 0,
        "mint_authority": "UNKNOWN", "freeze_authority": "UNKNOWN",
        "update_authority": "UNKNOWN", "reasons": ["Missing RugCheck report"],
        "signals": {},
    }
    if not isinstance(data, dict):
        return empty

    reasons = []
    high_risks = medium_risks = low_risks = unknown_risks = 0
    raw_score = get_number(data.get("score_normalised"))
    risks = data.get("risks")
    risks_valid = isinstance(risks, list)
    if not risks_valid:
        risks = []
        reasons.append("Risk list unavailable or invalid")

    for risk in risks:
        if not isinstance(risk, dict):
            unknown_risks += 1
            reasons.append("Invalid risk entry")
            continue
        level = get_risk_level(risk)
        name = str(risk.get("name", "Unknown risk"))
        if level == "HIGH":
            high_risks += 1
            reasons.append("HIGH RISK: " + name)
        elif level == "MEDIUM":
            medium_risks += 1
            reasons.append("MEDIUM RISK: " + name)
        elif level == "LOW":
            low_risks += 1
        else:
            unknown_risks += 1
            reasons.append("UNKNOWN RISK LEVEL: " + name)

    token = data.get("token") if isinstance(data.get("token"), dict) else {}
    token_meta = data.get("tokenMeta") if isinstance(data.get("tokenMeta"), dict) else {}
    mint_data = dict(data)
    mint_data.update(token)
    mint_authority = get_authority_status(mint_data, "mintAuthority")
    freeze_authority = get_authority_status(mint_data, "freezeAuthority")
    update_authority = get_authority_status({
        "updateAuthority": _first_present(
            data.get("tokenUpdateAuthority"), data.get("updateAuthority"),
            token.get("updateAuthority"), token_meta.get("updateAuthority")
        )
    }, "updateAuthority")

    if mint_authority == "ACTIVE":
        high_risks += 1
        reasons.append("Mint authority is active")
    elif mint_authority == "DISABLED":
        reasons.append("Mint authority disabled")
    else:
        reasons.append("Mint authority status unknown")

    if freeze_authority == "ACTIVE":
        high_risks += 1
        reasons.append("Freeze authority is active")
    elif freeze_authority == "DISABLED":
        reasons.append("Freeze authority disabled")
    else:
        reasons.append("Freeze authority status unknown")

    if update_authority == "ACTIVE":
        medium_risks += 1
        reasons.append("Token metadata update authority is active")

    top_holders = data.get("topHolders")
    holders_valid = isinstance(top_holders, list) and len(top_holders) > 0
    holder_percentages = []
    creator_pct = None
    creator = data.get("creator")
    if holders_valid:
        for holder in top_holders:
            if not isinstance(holder, dict):
                continue
            pct = get_number(holder.get("pct"))
            if pct is not None and 0 <= pct <= 100:
                holder_percentages.append(pct)
                owner = holder.get("owner")
                address = holder.get("address")
                if (isinstance(owner, str) and owner.lower() == "creator") or (
                    isinstance(creator, str) and isinstance(address, str)
                    and creator.lower() == address.lower()
                ):
                    creator_pct = pct
        if not holder_percentages:
            holders_valid = False
            reasons.append("Top holder percentages unavailable")
    else:
        reasons.append("Top holder data unavailable")

    top_holder_pct = max(holder_percentages) if holder_percentages else None
    top10_pct = min(100.0, sum(holder_percentages[:10])) if holder_percentages else None
    if top_holder_pct is not None:
        if top_holder_pct >= _risk_threshold("TROJAN_RUGCHECK_MAX_TOP_HOLDER_PCT", 50.0):
            high_risks += 1
            reasons.append("Extreme largest-holder concentration")
        elif top_holder_pct >= _risk_threshold("TROJAN_RUGCHECK_WARN_TOP_HOLDER_PCT", 30.0):
            medium_risks += 1
            reasons.append("Elevated largest-holder concentration")
    if top10_pct is not None:
        if top10_pct >= _risk_threshold("TROJAN_RUGCHECK_MAX_TOP10_PCT", 80.0):
            high_risks += 1
            reasons.append("Extreme top-ten holder concentration")
        elif top10_pct >= _risk_threshold("TROJAN_RUGCHECK_WARN_TOP10_PCT", 60.0):
            medium_risks += 1
            reasons.append("Elevated top-ten holder concentration")

    creator_balance = _first_present(data.get("creatorBalance"), token.get("creatorBalance"))
    token_supply = _first_present(data.get("tokenSupply"), token.get("supply"))
    if creator_pct is None:
        creator_pct = _safe_ratio(creator_balance, token_supply)
    if creator_pct is not None:
        if creator_pct >= _risk_threshold("TROJAN_RUGCHECK_MAX_CREATOR_PCT", 20.0):
            high_risks += 1
            reasons.append("Creator holds a critical share of supply")
        elif creator_pct >= _risk_threshold("TROJAN_RUGCHECK_WARN_CREATOR_PCT", 10.0):
            medium_risks += 1
            reasons.append("Creator holds an elevated share of supply")

    rugged = data.get("rugged") is True
    token_mutable = _first_present(
        token.get("mutable"), data.get("tokenMutable"), data.get("isMutable"),
        token_meta.get("mutable")
    )
    token_mutable = token_mutable if isinstance(token_mutable, bool) else None
    if rugged:
        high_risks += 1
        reasons.append("RugCheck marks token as rugged")
    if token_mutable is False and freeze_authority == "ACTIVE":
        high_risks += 1
        reasons.append("Immutable token still has an active freeze authority")

    transfer_fee = data.get("transferFee") if isinstance(data.get("transferFee"), dict) else {}
    transfer_fee_pct = get_number(_first_present(
        data.get("transferFeePct"), data.get("transfer_fee_pct"),
        transfer_fee.get("pct"), transfer_fee.get("transferFeePct")
    ))
    transfer_fee_authority = _first_present(
        data.get("transferFeeAuthority"), transfer_fee.get("authority")
    )
    if transfer_fee_pct is not None and transfer_fee_pct > 0:
        if transfer_fee_pct >= _risk_threshold("TROJAN_RUGCHECK_MAX_TRANSFER_FEE_PCT", 10.0):
            high_risks += 1
            reasons.append("Transfer fee exceeds the configured critical threshold")
        else:
            medium_risks += 1
            reasons.append("Token has a non-zero transfer fee")
    if transfer_fee_authority not in (None, "", False):
        medium_risks += 1
        reasons.append("Transfer fee authority remains active")

    insiders = get_number(data.get("graphInsidersDetected"))
    if insiders is not None and insiders > 0:
        if insiders >= _risk_threshold("TROJAN_RUGCHECK_MAX_GRAPH_INSIDERS", 3.0, 0.0, 100000.0):
            high_risks += 1
            reasons.append("Multiple graph-linked insiders detected")
        else:
            medium_risks += 1
            reasons.append("Graph-linked insiders detected")

    lp_providers = get_number(data.get("totalLPProviders"))
    if lp_providers is not None and lp_providers <= 1:
        medium_risks += 1
        reasons.append("Very low reported LP provider count")
    market_liquidity = get_number(data.get("totalMarketLiquidity"))
    if market_liquidity is not None and market_liquidity <= 0:
        high_risks += 1
        reasons.append("RugCheck reports no market liquidity")

    if raw_score is None:
        reasons.append("Normalized risk score unavailable")
    if not risks_valid:
        reasons.append("Risk information incomplete")
    if not holders_valid:
        reasons.append("Holder information incomplete")

    if (
        raw_score is None or not risks_valid or not holders_valid
        or mint_authority == "UNKNOWN" or freeze_authority == "UNKNOWN"
        or unknown_risks > 0
    ):
        status = "UNKNOWN"
    elif rugged or raw_score >= MAX_SAFE_SCORE or high_risks > 0:
        status = "REJECT"
        if raw_score >= MAX_SAFE_SCORE:
            reasons.append("RugCheck risk score too high")
        if high_risks > 0:
            reasons.append("High severity security risks detected")
    elif medium_risks >= _risk_threshold("TROJAN_RUGCHECK_MAX_MEDIUM_RISKS", 3.0, 1.0, 1000.0):
        status = "REJECT"
        reasons.append("Multiple medium severity risks")
    else:
        status = "PASS"
        reasons.append("No critical security risks detected")

    return {
        "status": status,
        "safe": status == "PASS",
        "score": raw_score,
        "risk_count": len(risks),
        "high_risks": high_risks,
        "medium_risks": medium_risks,
        "low_risks": low_risks,
        "unknown_risks": unknown_risks,
        "mint_authority": mint_authority,
        "freeze_authority": freeze_authority,
        "update_authority": update_authority,
        "reasons": reasons,
        "signals": {
            "rugged": rugged,
            "token_mutable": token_mutable,
            "top_holder_pct": round(top_holder_pct, 4) if top_holder_pct is not None else None,
            "top10_holder_pct": round(top10_pct, 4) if top10_pct is not None else None,
            "creator_pct": round(creator_pct, 4) if creator_pct is not None else None,
            "transfer_fee_pct": transfer_fee_pct,
            "transfer_fee_authority_active": transfer_fee_authority not in (None, "", False),
            "graph_insiders_detected": int(insiders) if insiders is not None and insiders.is_integer() else insiders,
            "total_lp_providers": int(lp_providers) if lp_providers is not None and lp_providers.is_integer() else lp_providers,
            "total_market_liquidity": market_liquidity,
        },
    }


# ==========================================
# FETCH REPORT
# ==========================================

def get_rugcheck_report(token_address):

    if not isinstance(token_address, str) or not token_address.strip():

        print("RUGCHECK ERROR: Missing token address")
        return None

    token_address = token_address.strip()

    now = time.time()

    with _cache_lock:

        cached = _cache.get(token_address)

        if cached:

            cached_time, cached_data = cached

            if now - cached_time < CACHE_SECONDS:
                return cached_data

            _cache.pop(token_address, None)

    url = API_URL + token_address + "/report"

    try:

        response = requests.get(
            url,
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 429:

            print("RUGCHECK: Rate limit reached")
            return None

        response.raise_for_status()

        data = response.json()

        if not isinstance(data, dict):

            print("RUGCHECK ERROR: Invalid response")
            return None

        analysis = analyze_rugcheck_report(data)

        result = {
            "token_address": token_address,
            "report": data,
            "analysis": analysis
        }

        with _cache_lock:
            _cache[token_address] = (time.time(), result)

        print("================================")
        print("TROJAN V2 RUGCHECK")
        print("TOKEN:", token_address)
        print("STATUS:", analysis["status"])
        print("SCORE:", analysis["score"])
        print("HIGH RISKS:", analysis["high_risks"])
        print("MEDIUM RISKS:", analysis["medium_risks"])
        print("LOW RISKS:", analysis["low_risks"])
        print("MINT AUTHORITY:", analysis["mint_authority"])
        print("FREEZE AUTHORITY:", analysis["freeze_authority"])

        for reason in analysis["reasons"]:
            print("RISK:", reason)

        print("================================")

        return result

    except requests.RequestException as error:

        print("RUGCHECK CONNECTION ERROR:", repr(error))
        return None

    except ValueError as error:

        print("RUGCHECK DATA ERROR:", repr(error))
        return None

    except Exception as error:

        print("RUGCHECK UNKNOWN ERROR:", repr(error))
        return None


# ==========================================
# CACHE MANAGEMENT
# ==========================================

def clear_rugcheck_cache(token_address=None):

    with _cache_lock:

        if token_address:
            _cache.pop(token_address, None)

        else:
            _cache.clear()


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    print("================================")
    print("TROJAN V2 RUGCHECK READY")
    print("================================")

    print("CACHE:", CACHE_SECONDS, "SECONDS")
    print("MAX SAFE SCORE:", MAX_SAFE_SCORE)
