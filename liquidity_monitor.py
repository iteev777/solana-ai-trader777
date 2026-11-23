import math
import threading
from datetime import datetime, timezone


# ==========================================
# TROJAN V2 - LIQUIDITY MONITOR
# ==========================================

LIQUIDITY_DROP_LIMIT = 20.0

CONFIRMATION_COUNT = 2

previous_liquidity = {}
liquidity_states = {}

_lock = threading.RLock()


# ==========================================
# HELPERS
# ==========================================

def now():
    return datetime.now(timezone.utc).isoformat()


def valid_liquidity(value):

    if isinstance(value, bool):
        return None

    try:
        value = float(value)

        if not math.isfinite(value):
            return None

        if value < 0:
            return None

        return value

    except (ValueError, TypeError, OverflowError):
        return None


# ==========================================
# MAIN LIQUIDITY CHECK
# ==========================================

def check_liquidity(token_address, current_liquidity):

    if not isinstance(token_address, str) or not token_address.strip():
        return "UNKNOWN"

    current = valid_liquidity(current_liquidity)

    if current is None:

        print("LIQUIDITY UNKNOWN:", token_address)

        return "UNKNOWN"

    with _lock:

        state = liquidity_states.get(token_address)

        # First measurement.
        if state is None:

            state = {
                "peak": current,
                "previous": current,
                "drop_streak": 0,
                "status": "NORMAL",
                "last_update": now()
            }

            liquidity_states[token_address] = state
            previous_liquidity[token_address] = current

            if current == 0:

                state["status"] = "COLLAPSE"

                print(
                    "CRITICAL: ZERO LIQUIDITY:",
                    token_address
                )

                return "COLLAPSE"

            print(
                "LIQUIDITY INITIALIZED:",
                token_address,
                "|",
                round(current, 2),
                "USD"
            )

            return "NORMAL"

        previous = state["previous"]
        peak = state["peak"]

        # Zero liquidity is an immediate emergency.
        if current == 0:

            state["previous"] = current
            state["drop_streak"] = CONFIRMATION_COUNT
            state["status"] = "COLLAPSE"
            state["last_update"] = now()

            previous_liquidity[token_address] = current

            print(
                "CRITICAL: LIQUIDITY COLLAPSE:",
                token_address
            )

            return "COLLAPSE"

        if peak <= 0:

            state["peak"] = current
            state["previous"] = current
            state["drop_streak"] = 0
            state["status"] = "NORMAL"
            state["last_update"] = now()

            previous_liquidity[token_address] = current

            return "NORMAL"

        # Update the high-water mark.
        if current > peak:

            state["peak"] = current
            peak = current

        drop_percent = (
            (peak - current) / peak
        ) * 100

        print("LIQUIDITY:", token_address)
        print("PREVIOUS:", round(previous, 2), "USD")
        print("PEAK:", round(peak, 2), "USD")
        print("CURRENT:", round(current, 2), "USD")
        print("DROP FROM PEAK:", round(drop_percent, 2), "%")

        # Significant liquidity drop.
        if drop_percent >= LIQUIDITY_DROP_LIMIT:

            state["drop_streak"] += 1

            print(
                "LIQUIDITY DROP WARNING:",
                token_address,
                "| CONFIRMATION:",
                state["drop_streak"],
                "/",
                CONFIRMATION_COUNT
            )

            state["previous"] = current
            state["last_update"] = now()

            previous_liquidity[token_address] = current

            if state["drop_streak"] >= CONFIRMATION_COUNT:

                state["status"] = "COLLAPSE"

                print(
                    "CRITICAL: LIQUIDITY COLLAPSE CONFIRMED:",
                    token_address
                )

                return "COLLAPSE"

            state["status"] = "WARNING"

            return "WARNING"

        # Liquidity has recovered or remains within safe limits.
        if state["status"] in ("WARNING", "COLLAPSE"):

            print(
                "LIQUIDITY RECOVERY:",
                token_address,
                "| DROP:",
                round(drop_percent, 2),
                "%"
            )

        state["drop_streak"] = 0
        state["status"] = "NORMAL"
        state["previous"] = current
        state["last_update"] = now()

        previous_liquidity[token_address] = current

        return "NORMAL"


# ==========================================
# STATUS
# ==========================================

def get_liquidity_status(token_address):

    with _lock:

        state = liquidity_states.get(token_address)

        if state is None:
            return None

        return dict(state)


# ==========================================
# RESET TOKEN
# ==========================================

def reset_liquidity(token_address):

    with _lock:

        liquidity_states.pop(token_address, None)
        previous_liquidity.pop(token_address, None)

        print("LIQUIDITY STATE RESET:", token_address)


# ==========================================
# STATISTICS
# ==========================================

def get_monitor_summary():

    with _lock:

        normal = 0
        warning = 0
        collapse = 0

        for state in liquidity_states.values():

            status = state.get("status")

            if status == "NORMAL":
                normal += 1

            elif status == "WARNING":
                warning += 1

            elif status == "COLLAPSE":
                collapse += 1

        return {
            "tracked_tokens": len(liquidity_states),
            "normal": normal,
            "warning": warning,
            "collapse": collapse
        }


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    print("================================")
    print("TROJAN V2 LIQUIDITY MONITOR")
    print("================================")

    print("DROP LIMIT:", LIQUIDITY_DROP_LIMIT, "%")
    print("CONFIRMATIONS:", CONFIRMATION_COUNT)
    print("ZERO LIQUIDITY: IMMEDIATE EXIT")
    print("HIGH-WATER MARK: ENABLED")

    print("LIQUIDITY MONITOR READY")
