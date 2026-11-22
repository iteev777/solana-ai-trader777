import threading
import os
import time
from datetime import datetime
import database
from position_policy import get_dca_steps, get_exit_steps
import exit_policy


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

# Partial exits are enabled in paper mode; DCA remains opt-in because
# averaging down increases exposure to a failing token.
PARTIAL_TAKE_PROFIT_ENABLED = os.getenv(
    "TROJAN_PARTIAL_TAKE_PROFIT_ENABLED", "true"
).lower() == "true"
PARTIAL_TAKE_PROFIT_TRIGGER = 0.08
PARTIAL_SELL_FRACTION = 0.50
DCA_ENABLED = os.getenv("TROJAN_DCA_ENABLED", "false").lower() == "true"
DCA_TRIGGER_DROP = 0.05
DCA_ADD_SIZE = 2.50
DCA_MAX_ADDS = 1

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
    metadata=None,
    trade_size=None
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

    try:
        position_size = float(TRADE_SIZE if trade_size is None else trade_size)
    except (TypeError, ValueError, OverflowError):
        return "Invalid trade size"
    if not __import__("math").isfinite(position_size) or position_size <= 0:
        return "Invalid trade size"

    # Adaptive sizing is paper-only and starts at 1.0x until enough closed
    # paper outcomes exist. The multiplier is bounded to avoid runaway risk.
    adaptive_multiplier = 1.0
    try:
        import adaptive_learning
        adaptive_multiplier = adaptive_learning.get_size_multiplier(strategy)
    except Exception as error:
        print("ADAPTIVE LEARNING SIZE FALLBACK:", type(error).__name__, flush=True)
    position_size = round(position_size * adaptive_multiplier, 2)
    if position_size <= 0:
        return "Invalid adaptive trade size"

    with _lock:

        refresh_account()

        if token_address in positions:
            return "Position already exists"

        if position_count(strategy) >= MAX_POSITIONS_PER_STRATEGY:
            return "Strategy position limit reached"

        if balance < position_size * (1 + database.PAPER_FEE_RATE):
            return "Insufficient balance including estimated entry fee"

        result = database.open_position_atomic(
            mode=MODE,
            token_address=token_address,
            entry_price=entry_price,
            invested=position_size,
            strategy=strategy,
            metadata={
                "engine": "trojan_v2",
                "strategy": strategy,
                "adaptive_size_multiplier": adaptive_multiplier,
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
            position_size,
            "| ADAPTIVE:",
            adaptive_multiplier,
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

        # Configurable DCA tiers. Every tier is one-shot, ordered, and
        # blocked at/beyond the hard stop. The default remains disabled.
        dca_steps = get_dca_steps()
        dca_count = int(metadata.get("dca_count", 0) or 0)
        exit_config = exit_policy.get_exit_policy()
        if dca_count < len(dca_steps) and change > -(exit_config["stop_loss_pct"] / 100.0):
            step = dca_steps[dca_count]
            if change <= -(step["drop_pct"] / 100.0):
                result = database.add_position_investment_atomic(
                    MODE, token_address, current_price, step["add_usd"],
                    metadata={"dca_count": dca_count + 1}
                )
                if isinstance(result, dict):
                    refresh_account()
                    print("PAPER DCA TIER:", token_address, "|", result, flush=True)
                    return result

        # Configurable multi-step exit ladder. Each fraction applies to
        # the remaining position, so successive sells cannot over-close it.
        exit_steps = get_exit_steps() if PARTIAL_TAKE_PROFIT_ENABLED else []
        completed = metadata.get("exit_ladder_completed", [])
        if not isinstance(completed, list):
            completed = []
        for step_index, step in enumerate(exit_steps):
            if step_index in completed:
                continue
            if change >= step["profit_pct"] / 100.0:
                result = database.partial_close_position_atomic(
                    MODE, token_address, current_price,
                    fraction=step["sell_fraction"],
                    reason="EXIT LADDER TIER " + str(step_index + 1)
                )
                if isinstance(result, dict):
                    completed.append(step_index)
                    metadata["exit_ladder_completed"] = completed
                    metadata["partial_take_profit_done"] = True
                    metadata["partial_exit_count"] = int(metadata.get("partial_exit_count", 0) or 0) + 1
                    database.update_position_metadata(MODE, token_address, metadata)
                    refresh_account()
                    print("PAPER EXIT LADDER:", token_address, "|", result, flush=True)
                    return result
                break

        exit_reason = exit_policy.evaluate_exit(
            position, current_price, policy=exit_config
        )
        if exit_reason:
            return close_position(token_address, current_price, exit_reason)

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
