import math
import database
import paper_trading as paper
import risk_manager as risk


# ==========================================
# TROJAN V2 - TRADING MANAGER
# ==========================================

LIVE_TRADING_ENABLED = False
LIVE_EXECUTION_HARD_LOCK = True

TRADING_MODES = {
    "paper": {
        "enabled": True,
        "description": "Virtual paper trading"
    },
    "live": {
        "enabled": True,
        "execution_enabled": False,
        "execution_locked": True,
        "description": "Shadow signals only - execution hard-locked"
    }
}


# ==========================================
# VALIDATION
# ==========================================

def valid_price(price):

    if isinstance(price, bool):
        return False

    try:
        value = float(price)
        return math.isfinite(value) and value > 0
    except (ValueError, TypeError, OverflowError):
        return False


def valid_token_address(token_address):
    """Require a canonical 32-byte Solana mint before any risk or DB lookup."""
    try:
        from market_data import valid_address
        return valid_address(token_address)
    except Exception:
        return False


# ==========================================
# MODES
# ==========================================

def get_available_modes():

    return {
        mode: settings.copy()
        for mode, settings in TRADING_MODES.items()
    }


def is_mode_enabled(mode):

    if mode not in TRADING_MODES:
        return False

    return TRADING_MODES[mode]["enabled"]


def is_live_execution_enabled():
    # The manager cannot unlock real execution during the current project phase.
    return bool(LIVE_TRADING_ENABLED) and not LIVE_EXECUTION_HARD_LOCK


def get_engine(mode):

    if mode == "paper":
        return paper

    if mode == "live":
        raise RuntimeError(
            "LIVE TRADING IS LOCKED - SHADOW MODE ONLY"
        )

    raise ValueError("Unknown trading mode: " + str(mode))


# ==========================================
# ACCOUNT DATA
# ==========================================

def get_balance(mode):

    summary = get_account_summary(mode)

    return summary["balance"]


def get_positions(mode):

    if mode == "paper":
        return paper.get_positions()

    if mode == "live":
        return database.get_positions("live")

    raise ValueError("Unknown trading mode: " + str(mode))


def position_count(mode, strategy=None):

    positions = get_positions(mode)

    if strategy is None:
        return len(positions)

    return sum(
        1
        for position in positions.values()
        if isinstance(position, dict)
        and position.get("strategy", "main") == strategy
    )


def get_account_summary(mode):

    if mode == "paper":

        summary = paper.get_account_summary()

        summary["execution_enabled"] = True
        summary["status"] = "PAPER TRADING"

        return summary

    if mode == "live":

        database.initialize_account("live", 0.00)

        summary = database.get_account_summary("live")

        summary["execution_enabled"] = False
        summary["status"] = "SHADOW MODE - LOCKED"

        return summary

    raise ValueError("Unknown trading mode: " + str(mode))


# ==========================================
# RISK CHECK
# ==========================================

def check_trade_approval(
    token_address,
    trade_size,
    strategy="main"
):

    if not valid_token_address(token_address):
        return False, "INVALID TOKEN ADDRESS"

    if strategy not in ("main", "sniper"):
        return False, "INVALID STRATEGY"

    account = paper.get_account_summary()
    positions = paper.get_positions()

    main_count = sum(
        1
        for position in positions.values()
        if isinstance(position, dict)
        and position.get("strategy", "main") == "main"
    )

    sniper_count = sum(
        1
        for position in positions.values()
        if isinstance(position, dict)
        and position.get("strategy", "main") == "sniper"
    )

    return risk.check_trade_risk(
        balance=account["balance"],
        open_positions=len(positions),
        daily_loss=account["daily_loss"],
        trade_size=trade_size,
        strategy=strategy,
        main_positions=main_count,
        sniper_positions=sniper_count,
        token_address=token_address,
        mode="paper"
    )


# ==========================================
# APPROVED SIGNAL
# ==========================================

def process_approved_signal(
    token_address,
    price,
    strategy="main",
    trade_size=None,
    metadata=None
):

    if trade_size is None:
        trade_size = paper.TRADE_SIZE

    if not valid_token_address(token_address):
        return {
            "paper": "INVALID TOKEN ADDRESS",
            "live": "LIVE EXECUTION LOCKED"
        }

    if not valid_price(price):
        return {
            "paper": "INVALID PRICE",
            "live": "LIVE EXECUTION LOCKED"
        }

    if not risk.valid_number(trade_size):
        return {
            "paper": "INVALID TRADE SIZE",
            "live": "LIVE EXECUTION LOCKED"
        }

    approved, reason = check_trade_approval(
        token_address,
        trade_size,
        strategy
    )

    if not approved:

        print(
            "TRADE REJECTED:",
            token_address,
            "| REASON:",
            reason
        )

        return {
            "paper": reason,
            "live": "LIVE EXECUTION LOCKED"
        }

    results = {}

    if is_mode_enabled("paper"):

        results["paper"] = paper.open_position(
            token_address,
            float(price),
            strategy=strategy,
            metadata=metadata,
            trade_size=float(trade_size)
        )

    # Shadow signals never execute real trades.
    if is_mode_enabled("live"):

        results["live"] = (
            "SIGNAL APPROVED - LIVE EXECUTION LOCKED"
        )

        print(
            "LIVE SHADOW SIGNAL:",
            token_address,
            "| PRICE:",
            price,
            "| STRATEGY:",
            strategy
        )

    return results


# ==========================================
# MANUAL POSITION OPERATIONS
# ==========================================

def open_position(
    mode,
    token_address,
    price,
    strategy="main"
):

    if mode == "live":
        return "LIVE TRADING LOCKED"

    if mode != "paper":
        return "UNKNOWN TRADING MODE"

    if not valid_token_address(token_address):
        return "INVALID TOKEN ADDRESS"

    if not valid_price(price):
        return "INVALID PRICE"

    approved, reason = check_trade_approval(
        token_address,
        paper.TRADE_SIZE,
        strategy
    )

    if not approved:
        return reason

    return paper.open_position(
        token_address,
        float(price),
        strategy=strategy
    )


def close_position(
    mode,
    token_address,
    price,
    reason="MANUAL EXIT"
):

    if mode == "live":
        return "LIVE TRADING LOCKED"

    if mode != "paper":
        return "UNKNOWN TRADING MODE"

    if not valid_token_address(token_address):
        return "INVALID TOKEN ADDRESS"

    if not valid_price(price):
        return "INVALID PRICE"

    return paper.close_position(
        token_address,
        float(price),
        reason
    )


def update_position(
    mode,
    token_address,
    price
):

    if mode == "live":
        return "LIVE TRADING LOCKED"

    if mode != "paper":
        return "UNKNOWN TRADING MODE"

    if not valid_token_address(token_address):
        return "INVALID TOKEN ADDRESS"

    if not valid_price(price):
        return "INVALID PRICE"

    return paper.update_position(
        token_address,
        float(price)
    )


# ==========================================
# STRATEGY STATISTICS
# ==========================================

def get_strategy_stats(strategy):

    if strategy not in ("main", "sniper"):
        raise ValueError("Invalid strategy")

    return database.get_trade_stats(
        mode="paper",
        strategy=strategy
    )


def get_all_stats():

    return {
        "total": database.get_trade_stats("paper"),
        "main": get_strategy_stats("main"),
        "sniper": get_strategy_stats("sniper")
    }


# ==========================================
# STATUS
# ==========================================

def get_execution_status():
    return {
        "mode": "SHADOW_ONLY",
        "execution_enabled": is_live_execution_enabled(),
        "hard_lock": LIVE_EXECUTION_HARD_LOCK,
        "unlock_policy": "CODE_CHANGE_AFTER_EXPLICIT_PHASE_5_APPROVAL",
    }


def get_manager_status():
    return {
        "modes": get_available_modes(),
        "paper": get_account_summary("paper"),
        "live": get_account_summary("live"),
        "risk": risk.get_risk_summary(),
        "live_execution": is_live_execution_enabled(),
        "live_execution_hard_lock": LIVE_EXECUTION_HARD_LOCK,
        "execution": get_execution_status(),
    }


# ==========================================
# TEST
# ==========================================

if __name__ == "__main__":

    database.initialize_database()

    print("================================")
    print("TROJAN V2 TRADING MANAGER")
    print("================================")

    print("STATUS:", get_manager_status())

    print("STRATEGY STATS:", get_all_stats())

    print("LIVE EXECUTION:", is_live_execution_enabled())

    print("TRADING MANAGER READY")
