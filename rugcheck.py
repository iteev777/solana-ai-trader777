import requests
import math
import time
import threading


# ==========================================
# TROJAN V2 - RUGCHECK SECURITY ANALYZER
# ==========================================

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

    value = data.get(authority_name)

    if value is None:
        return "UNKNOWN"

    if value is False or value == "":
        return "DISABLED"

    if value is True:
        return "ACTIVE"

    return "UNKNOWN"


def get_risk_level(risk):

    if not isinstance(risk, dict):
        return "UNKNOWN"

    level = str(
        risk.get("level", "")
    ).lower()

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

def analyze_rugcheck_report(data):

    if not isinstance(data, dict):

        return {
            "status": "UNKNOWN",
            "safe": False,
            "score": None,
            "risk_count": 0,
            "high_risks": 0,
            "medium_risks": 0,
            "low_risks": 0,
            "reasons": ["Missing RugCheck report"]
        }

    reasons = []

    raw_score = get_number(
        data.get("score_normalised")
    )

    if raw_score is None:

        raw_score = get_number(
            data.get("score")
        )

    risks = data.get("risks", [])

    if not isinstance(risks, list):
        risks = []

    high_risks = 0
    medium_risks = 0
    low_risks = 0

    for risk in risks:

        if not isinstance(risk, dict):
            continue

        level = get_risk_level(risk)

        name = risk.get(
            "name",
            "Unknown risk"
        )

        if level == "HIGH":

            high_risks += 1
            reasons.append("HIGH RISK: " + str(name))

        elif level == "MEDIUM":

            medium_risks += 1
            reasons.append("MEDIUM RISK: " + str(name))

        elif level == "LOW":

            low_risks += 1

    # Authority checks.
    mint_authority = get_authority_status(
        data,
        "mintAuthority"
    )

    freeze_authority = get_authority_status(
        data,
        "freezeAuthority"
    )

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

    # Top holder concentration.
    top_holders = data.get("topHolders", [])

    if isinstance(top_holders, list) and top_holders:

        holder_percentages = []

        for holder in top_holders:

            if not isinstance(holder, dict):
                continue

            percentage = get_number(
                holder.get("pct")
            )

            if percentage is not None:
                holder_percentages.append(percentage)

        if holder_percentages:

            top_holder = max(holder_percentages)

            if top_holder >= 50:

                high_risks += 1
                reasons.append("Extreme top holder concentration")

            elif top_holder >= 30:

                medium_risks += 1
                reasons.append("High top holder concentration")

            else:

                reasons.append("Top holder concentration acceptable")

    else:

        reasons.append("Top holder data unavailable")

    # Missing score means report cannot be fully evaluated.
    if raw_score is None:

        return {
            "status": "UNKNOWN",
            "safe": False,
            "score": None,
            "risk_count": len(risks),
            "high_risks": high_risks,
            "medium_risks": medium_risks,
            "low_risks": low_risks,
            "reasons": reasons
        }

    # RugCheck normalized score is generally a risk score:
    # lower is better.
    if raw_score > MAX_SAFE_SCORE:

        status = "REJECT"
        reasons.append("RugCheck risk score too high")

    elif high_risks > 0:

        status = "REJECT"
        reasons.append("High severity security risks detected")

    elif medium_risks >= 3:

        status = "REJECT"
        reasons.append("Multiple medium severity risks")

    elif mint_authority == "UNKNOWN" or freeze_authority == "UNKNOWN":

        status = "UNKNOWN"
        reasons.append("Authority information incomplete")

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
        "mint_authority": mint_authority,
        "freeze_authority": freeze_authority,
        "reasons": reasons
    }


# ==========================================
# FETCH REPORT
# ==========================================

def get_rugcheck_report(token_address):

    if not token_address:

        print("RUGCHECK ERROR: Missing token address")

        return None

    now = time.time()

    with _cache_lock:

        cached = _cache.get(token_address)

        if cached:

            cached_time, cached_data = cached

            if now - cached_time < CACHE_SECONDS:

                return cached_data

    url = API_URL + str(token_address) + "/report"

    try:

        response = requests.get(
            url,
            headers={
                "Accept": "application/json"
            },
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

            _cache[token_address] = (
                now,
                result
            )

        print("================================")
        print("TROJAN V2 RUGCHECK")
        print("TOKEN:", token_address)
        print("STATUS:", analysis["status"])
        print("SCORE:", analysis["score"])
        print("HIGH RISKS:", analysis["high_risks"])
        print("MEDIUM RISKS:", analysis["medium_risks"])
        print("LOW RISKS:", analysis["low_risks"])

        for reason in analysis["reasons"]:
            print("RISK:", reason)

        print("================================")

        return result

    except requests.RequestException as error:

        print("RUGCHECK CONNECTION ERROR:", error)

        return None

    except ValueError as error:

        print("RUGCHECK DATA ERROR:", error)

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
