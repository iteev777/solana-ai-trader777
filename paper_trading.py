import threading
import os
import time
from datetime import datetime
import database
from position_policy import get_dca_steps, get_exit_steps
import exit_policy
import paper_fill_model


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




def _positive_finite(value):
    """Return a finite positive float, or None for unsafe market input."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    import math
    return number if math.isfinite(number) and number > 0 else None


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

    market_entry_price = _positive_finite(entry_price)
    if market_entry_price is None:
        return "Invalid price"
    position_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    try:
        entry_price, entry_slippage_bps = paper_fill_model.buy_fill_price(
            market_entry_price, position_metadata
        )
    except ValueError:
        return "Invalid paper fill price"

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

        # Re-apply hard risk limits after adaptive sizing. The adaptive
        # multiplier must never bypass the configured ticket or balance cap.
        import risk_manager
        position_size = min(
            round(position_size, 2),
            risk_manager.MAX_TRADE_SIZE,
            round(balance * risk_manager.MAX_TRADE_BALANCE_FRACTION, 2),
        )
        if position_size < risk_manager.MIN_TRADE_SIZE:
            return "Adaptive size is below the minimum safe trade size"

        main_count = sum(
            1 for item in positions.values()
            if isinstance(item, dict) and item.get("strategy", "main") == "main"
        )
        sniper_count = sum(
            1 for item in positions.values()
            if isinstance(item, dict) and item.get("strategy", "main") == "sniper"
        )
        approved, risk_reason = risk_manager.check_trade_risk(
            balance=balance,
            open_positions=len(positions),
            daily_loss=account.get("daily_loss", 0),
            trade_size=position_size,
            strategy=strategy,
            main_positions=main_count,
            sniper_positions=sniper_count,
        )
        if not approved:
            return "Risk limit after adaptive sizing: " + risk_reason

        if balance < position_size * (1 + database.PAPER_FEE_RATE):
            return "Insufficient balance including estimated entry fee"

        result = database.open_position_atomic(
            mode=MODE,
            token_address=token_address,
            entry_price=entry_price,
            invested=position_size,
            strategy=strategy,
            metadata={
                **position_metadata,
                "engine": "trojan_v2",
                "strategy": strategy,
                "adaptive_size_multiplier": adaptive_multiplier,
                "paper_entry_slippage_bps": entry_slippage_bps,
                "paper_fill_model": "CONSERVATIVE_MARKET_SLIPPAGE",
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
            "| MARKET PRICE:",
            market_entry_price,
            "| FILL PRICE:",
            entry_price,
            "| SLIPPAGE BPS:",
            entry_slippage_bps,
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

    current_price = _positive_finite(current_price)
    if current_price is None:
        return "Invalid price"

    with _lock:

        refresh_account()

        if token_address not in positions:
            return "Position not found"

        position = positions.get(token_address) or {}
        metadata = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
        try:
            fill_price, exit_slippage_bps = paper_fill_model.sell_fill_price(
                current_price, metadata
            )
        except ValueError:
            return "Invalid paper fill price"
        result = database.close_position_atomic(
            mode=MODE,
            token_address=token_address,
            current_price=fill_price,
            reason=reason
        )

        if not isinstance(result, dict):
            refresh_account()
            return result

        result["market_price"] = current_price
        result["effective_fill_price"] = fill_price
        result["slippage_bps"] = exit_slippage_bps
        result["fill_model"] = "CONSERVATIVE_MARKET_SLIPPAGE"
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

    current_price = _positive_finite(current_price)
    if current_price is None:
        return "Invalid price"

    with _lock:
        refresh_account()
        position = positions.get(token_address)

        if not position:
            return "Position not found"

        entry = _positive_finite(position.get("entry_price"))
        if entry is None:
            return "Invalid entry price"

        # Parse every configured policy before mutating position state.
        try:
            dca_steps = get_dca_steps()
            exit_config = exit_policy.get_exit_policy()
            exit_steps = get_exit_steps() if PARTIAL_TAKE_PROFIT_ENABLED else []
        except (TypeError, ValueError, OverflowError) as policy_error:
            print("PAPER POSITION POLICY INVALID:", repr(policy_error), flush=True)
            return "Invalid position policy configuration"

        change = (current_price / entry) - 1
        metadata = position.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        # Persist the best observed price so trailing protection survives
        # a process restart and does not reset on every monitor cycle.
        stored_peak = _positive_finite(metadata.get("peak_price", entry)) or entry
        peak_price = max(entry, stored_peak, current_price)
        if peak_price > stored_peak:
            metadata["peak_price"] = peak_price
            database.update_position_metadata(
                MODE, token_address, metadata
            )

        # Configurable DCA tiers. Every tier is one-shot, ordered, and
        # blocked at/beyond the hard stop. The default remains disabled.
        try:
            dca_count = max(0, int(metadata.get("dca_count", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            dca_count = 0
        if dca_count < len(dca_steps) and change > -(exit_config["stop_loss_pct"] / 100.0):
            step = dca_steps[dca_count]
            if change <= -(step["drop_pct"] / 100.0):
                try:
                    dca_fill_price, dca_slippage_bps = paper_fill_model.buy_fill_price(current_price)
                except ValueError:
                    return "Invalid paper DCA fill price"
                result = database.add_position_investment_atomic(
                    MODE, token_address, dca_fill_price, step["add_usd"],
                    metadata={
                        "dca_count": dca_count + 1,
                        "paper_dca_slippage_bps": dca_slippage_bps,
                        "paper_fill_model": "CONSERVATIVE_MARKET_SLIPPAGE",
                    }
                )
                if isinstance(result, dict):
                    result["market_price"] = current_price
                    result["effective_fill_price"] = dca_fill_price
                    result["slippage_bps"] = dca_slippage_bps
                    result["fill_model"] = "CONSERVATIVE_MARKET_SLIPPAGE"
                    refresh_account()
                    print("PAPER DCA TIER:", token_address, "|", result, flush=True)
                    return result

        # Configurable multi-step exit ladder. Each fraction applies to
        # the remaining position, so successive sells cannot over-close it.
        completed = metadata.get("exit_ladder_completed", [])
        if not isinstance(completed, list):
            completed = []
        for step_index, step in enumerate(exit_steps):
            if step_index in completed:
                continue
            if change >= step["profit_pct"] / 100.0:
                try:
                    exit_fill_price, exit_slippage_bps = paper_fill_model.sell_fill_price(
                        current_price, metadata
                    )
                except ValueError:
                    return "Invalid paper exit fill price"
                result = database.partial_close_position_atomic(
                    MODE, token_address, exit_fill_price,
                    fraction=step["sell_fraction"],
                    reason="EXIT LADDER TIER " + str(step_index + 1)
                )
                if isinstance(result, dict):
                    completed.append(step_index)
                    metadata["exit_ladder_completed"] = completed
                    metadata["partial_take_profit_done"] = True
                    metadata["partial_exit_count"] = int(metadata.get("partial_exit_count", 0) or 0) + 1
                    metadata["paper_exit_slippage_bps"] = exit_slippage_bps
                    metadata["paper_fill_model"] = "CONSERVATIVE_MARKET_SLIPPAGE"
                    database.update_position_metadata(MODE, token_address, metadata)
                    result["market_price"] = current_price
                    result["effective_fill_price"] = exit_fill_price
                    result["slippage_bps"] = exit_slippage_bps
                    result["fill_model"] = "CONSERVATIVE_MARKET_SLIPPAGE"
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
