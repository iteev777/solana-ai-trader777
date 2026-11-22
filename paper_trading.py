import threading
import time
from datetime import datetime
import database


# ==========================================
# TROJAN V2 - PAPER TRADING ENGINE
# ==========================================

MODE = "paper"

STARTING_BALANCE = 100.00
TRADE_SIZE = 5.00

STOP_LOSS = 0.09
TAKE_PROFIT = 0.10
TRAILING_ACTIVATION = 0.04
TRAILING_DISTANCE = 0.035
MAX_HOLD_SECONDS = 900

MAX_POSITIONS_PER_STRATEGY = 10

VALID_STRATEGIES = ("main", "sniper")

_lock = threading.RLock()


# ==========================================
# INITIALIZATION
# ==========================================

database.initialize_database()
database.initialize_account(MODE, STARTING_BALANCE)

account = database.get_account_state(MODE)
balance = float(account["balance"])

positions = database.get_positions(MODE)


def refresh_account():
    global account, balance, positions

    account = database.get_account_state(MODE)

    if account is None:
        raise RuntimeError("Paper account could not be loaded")

    balance = float(account["balance"])
    positions = database.get_positions(MODE)

    return account


# ==========================================
# POSITION HELPERS
# ==========================================

def position_count(strategy):
    if strategy not in VALID_STRATEGIES:
        return 0

    with _lock:
        return sum(
            1
            for position in positions.values()
            if position.get("strategy", "main") == strategy
        )


def get_position(token_address):
    with _lock:
        return positions.get(token_address)


def get_positions():
    with _lock:
        return dict(positions)


# ==========================================
# OPEN POSITION
# ==========================================

def open_position(
    token_address,
    entry_price,
    strategy="main",
    metadata=None
):
    global balance, positions

    if not token_address:
        return "Invalid token address"

    try:
        entry_price = float(entry_price)
    except (ValueError, TypeError):
        return "Invalid price"

    if entry_price <= 0:
        return "Invalid price"

    if strategy not in VALID_STRATEGIES:
        return "Invalid strategy"

    with _lock:

        refresh_account()

        if token_address in positions:
            return "Position already exists"

        if position_count(strategy) >= MAX_POSITIONS_PER_STRATEGY:
            return "Strategy position limit reached"

        if balance < TRADE_SIZE:
            return "Insufficient balance"

        result = database.open_position_atomic(
            mode=MODE,
            token_address=token_address,
            entry_price=entry_price,
            invested=TRADE_SIZE,
            strategy=strategy,
            metadata={
                "engine": "trojan_v2",
                "strategy": strategy,
                **(metadata if isinstance(metadata, dict) else {})
            }
        )

        if result != "POSITION OPENED":
            refresh_account()
            return result

        refresh_account()

        print(
            "PAPER BUY:",
            token_address,
            "| STRATEGY:",
            strategy,
            "| PRICE:",
            entry_price,
            "| SIZE:",
            TRADE_SIZE,
            "| BALANCE:",
            round(balance, 2)
        )

        return "PAPER BUY: " + token_address


# ==========================================
# CLOSE POSITION
# ==========================================

def close_position(
    token_address,
    current_price,
    reason="MANUAL EXIT"
):
    global balance, positions

    try:
        current_price = float(current_price)
    except (ValueError, TypeError):
        return "Invalid price"

    if current_price <= 0:
        return "Invalid price"

    with _lock:

        refresh_account()

        if token_address not in positions:
            return "Position not found"

        result = database.close_position_atomic(
            mode=MODE,
            token_address=token_address,
            current_price=current_price,
            reason=reason
        )

        if not isinstance(result, dict):
            refresh_account()
            return result

        refresh_account()

        print(
            "PAPER SELL:",
            token_address,
            "| REASON:",
            reason,
            "| P/L:",
            result["profit_loss"],
            "| BALANCE:",
            result["balance"]
        )

        return result


# ==========================================
# EMERGENCY EXIT
# ==========================================

def emergency_sell(token_address, current_price):
    return close_position(
        token_address,
        current_price,
        "EMERGENCY LIQUIDITY EXIT"
    )


# ==========================================
# STOP LOSS / TAKE PROFIT
# ==========================================

def update_position(token_address, current_price):

    try:
        current_price = float(current_price)
    except (ValueError, TypeError):
        return "Invalid price"

    if current_price <= 0:
        return "Invalid price"

    with _lock:
        refresh_account()
        position = positions.get(token_address)

        if not position:
            return "Position not found"

        entry = float(position["entry_price"])
        if entry <= 0:
            return "Invalid entry price"

        change = (current_price / entry) - 1
        metadata = position.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        # Persist the best observed price so trailing protection survives
        # a process restart and does not reset on every monitor cycle.
        peak_price = max(
            float(metadata.get("peak_price", entry) or entry),
            current_price
        )
        if peak_price > float(metadata.get("peak_price", 0) or 0):
            metadata["peak_price"] = peak_price
            database.update_position_metadata(
                MODE, token_address, metadata
            )

        if change <= -STOP_LOSS:
            return close_position(token_address, current_price, "STOP LOSS")

        if change >= TAKE_PROFIT:
            return close_position(token_address, current_price, "SCALP TAKE PROFIT")

        peak_gain = (peak_price / entry) - 1
        if peak_gain >= TRAILING_ACTIVATION:
            trailing_floor = peak_price * (1 - TRAILING_DISTANCE)
            if current_price <= trailing_floor:
                return close_position(
                    token_address, current_price, "TRAILING PROFIT EXIT"
                )

        opened_at = position.get("opened_at")
        if opened_at:
            try:
                opened_timestamp = datetime.fromisoformat(
                    str(opened_at).replace("Z", "+00:00")
                ).timestamp()
                if time.time() - opened_timestamp >= MAX_HOLD_SECONDS:
                    return close_position(
                        token_address, current_price, "15 MINUTE ROTATION EXIT"
                    )
            except (ValueError, TypeError, OverflowError):
                pass

        return "Position still open"


# ==========================================
# ACCOUNT SUMMARY
# ==========================================

def get_account_summary():

    with _lock:

        refresh_account()

        invested = sum(
            float(position["invested"])
            for position in positions.values()
        )

        return {
            "mode": MODE,
            "balance": round(balance, 2),
            "positions": len(positions),
            "invested": round(invested, 2),
            "daily_loss": round(
                float(account["daily_loss"]), 2
            ),
            "daily_realized_pnl": round(
                float(account["daily_realized_pnl"]), 2
            )
        }


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    print("================================")
    print("TROJAN V2 PAPER ENGINE")
    print("================================")

    print("MODE:", MODE)
    print("ACCOUNT:", get_account_summary())

    print("MAIN POSITIONS:", position_count("main"))
    print("SNIPER POSITIONS:", position_count("sniper"))

    print("PAPER TRADING ENGINE READY")
