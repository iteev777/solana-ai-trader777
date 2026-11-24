import math
import threading
from collections import OrderedDict
from datetime import datetime, timezone


# ==========================================
# TROJAN V2 - LIQUIDITY MONITOR
# ==========================================

LIQUIDITY_DROP_LIMIT = 20.0

CONFIRMATION_COUNT = 2
MAX_TRACKED_TOKENS = 5000

previous_liquidity = {}
liquidity_states = OrderedDict()

_lock = threading.RLock()


# ==========================================
# HELPERS
# ==========================================

def now():
    return datetime.now(timezone.utc).isoformat()


def _valid_address(value):
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    try:
        import solana_onchain as chain
        decoded = chain._b58decode(value)
        return (
            isinstance(decoded, (bytes, bytearray))
            and len(decoded) == 32
            and chain._b58encode(decoded) == value
        )
    except Exception:
        return False


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

    except (ValueError, TypeError):
        return None


# ==========================================
# MAIN LIQUIDITY CHECK
# ==========================================

def check_liquidity(token_address, current_liquidity, pool_address=None):

    if not _valid_address(token_address):
        return "UNKNOWN"
    if pool_address is not None and not _valid_address(pool_address):
        return "UNKNOWN"

    current = valid_liquidity(current_liquidity)

    if current is None:

        print("LIQUIDITY UNKNOWN:", token_address)

        return "UNKNOWN"

    with _lock:

        state = liquidity_states.get(token_address)

        # A canonical-pool change resets the high-water mark. Comparing
        # liquidity from two different pools would create a false collapse.
        if state is not None and state.get("pool_address") != pool_address:
            state = None
            liquidity_states.pop(token_address, None)
            previous_liquidity.pop(token_address, None)

        # First measurement for this token/pool pair.
        if state is None:

            state = {
                "pool_address": pool_address,
                "peak": current,
                "previous": current,
                "drop_streak": 0,
                "status": "NORMAL",
                "last_update": now()
            }

            liquidity_states[token_address] = state
            liquidity_states.move_to_end(token_address)
            previous_liquidity[token_address] = current
            while len(liquidity_states) > MAX_TRACKED_TOKENS:
                expired_address, _ = liquidity_states.popitem(last=False)
                previous_liquidity.pop(expired_address, None)

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

        liquidity_states.move_to_end(token_address)
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
