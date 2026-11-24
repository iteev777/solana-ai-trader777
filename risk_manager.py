import math
import os
from datetime import datetime, timezone


# ==========================================
# TROJAN V2 - RISK MANAGER
# ==========================================

STARTING_BALANCE = 100.00


def _env_number(name, default, minimum=0.0):
    try:
        value = float(os.getenv(name, str(default)))
        if math.isfinite(value) and value >= minimum:
            return value
    except (TypeError, ValueError, OverflowError):
        pass
    return float(default)


def _env_count(name, default, minimum=1):
    try:
        value = int(os.getenv(name, str(default)))
        if value >= minimum:
            return value
    except (TypeError, ValueError, OverflowError):
        pass
    return int(default)


MAX_TRADE_SIZE = _env_number("TROJAN_MAX_TRADE_SIZE_USD", 5.00, 1.00)
MIN_TRADE_SIZE = _env_number("TROJAN_MIN_TRADE_SIZE_USD", 1.00, 0.01)

MAX_MAIN_POSITIONS = _env_count("TROJAN_MAX_MAIN_POSITIONS", 10)
MAX_SNIPER_POSITIONS = _env_count("TROJAN_MAX_SNIPER_POSITIONS", 10)
MAX_TOTAL_POSITIONS = _env_count("TROJAN_MAX_TOTAL_POSITIONS", 15)

# Default daily loss cap is $50 for paper trading. It can be
# overridden explicitly in Railway without editing the source.
MAX_DAILY_LOSS = _env_number(
    "TROJAN_MAX_DAILY_LOSS_USD",
    50.00,
    0.01
)

PERIOD_LOSS_WINDOW_HOURS = _env_number(
    "TROJAN_PERIOD_LOSS_WINDOW_HOURS", 24.0, 1.0
)
MAX_PERIOD_LOSS = _env_number(
    "TROJAN_MAX_PERIOD_LOSS_USD", MAX_DAILY_LOSS, 0.01
)


def _env_nonnegative_count(name, default):
    try:
        value = int(os.getenv(name, str(default)))
        if value >= 0:
            return value
    except (TypeError, ValueError, OverflowError):
        pass
    return int(default)


REENTRY_COOLDOWN_MINUTES = _env_nonnegative_count(
    "TROJAN_REENTRY_COOLDOWN_MINUTES", 15
)
MAX_TRADE_BALANCE_FRACTION = min(
    1.0,
    _env_number("TROJAN_MAX_TRADE_BALANCE_FRACTION", 0.10, 0.01)
)

STOP_LOSS_PERCENT = min(95.0, _env_number("TROJAN_STOP_LOSS_PERCENT", 20.0, 0.1))
TAKE_PROFIT_PERCENT = min(10000.0, _env_number("TROJAN_TAKE_PROFIT_PERCENT", 30.0, 0.1))

VALID_STRATEGIES = ("main", "sniper")


# ==========================================
# VALIDATION
# ==========================================

def valid_number(value):

    if isinstance(value, bool):
        return False

    try:
        return math.isfinite(float(value))
    except (ValueError, TypeError, OverflowError):
        return False


def valid_count(value):

    if not valid_number(value):
        return False

    number = float(value)

    return number >= 0 and number.is_integer()


# ==========================================
# POSITION LIMITS
# ==========================================

def get_position_limit(strategy):

    if strategy == "main":
        return MAX_MAIN_POSITIONS

    if strategy == "sniper":
        return MAX_SNIPER_POSITIONS

    return 0


# ==========================================
# TRADE APPROVAL WITH REASON
# ==========================================

def _check_history_guards(token_address, mode, strategy):
    """Fail closed on rolling realized-loss and per-token re-entry guards.

    The portfolio-wide loss guard must run even when a caller has no mint
    context. Only the token-specific cooldown may be skipped in that case.
    """
    if mode not in ("paper", "live"):
        return False, "INVALID TRADING MODE FOR HISTORY GUARDS"
    try:
        import database
        period_loss = database.get_period_realized_loss(
            mode, PERIOD_LOSS_WINDOW_HOURS
        )
    except Exception:
        return False, "PERIOD LOSS HISTORY UNAVAILABLE"
    if not valid_number(period_loss) or float(period_loss) < 0:
        return False, "PERIOD LOSS HISTORY INVALID"
    if float(period_loss) >= MAX_PERIOD_LOSS:
        return False, "ROLLING PERIOD LOSS LIMIT REACHED"

    # Per-token re-entry protection is mandatory for every entry decision.
    # A missing mint must never silently bypass the cooldown check.
    if not isinstance(token_address, str) or not token_address.strip():
        return False, "TOKEN ADDRESS REQUIRED FOR HISTORY GUARDS"
    token_address = token_address.strip()

    if REENTRY_COOLDOWN_MINUTES > 0:
        try:
            last_exit = database.get_last_exit_timestamp(token_address, mode, strategy=strategy)
        except Exception:
            return False, "RE-ENTRY COOLDOWN HISTORY UNAVAILABLE"
        if last_exit:
            try:
                exited_at = datetime.fromisoformat(last_exit)
                if exited_at.tzinfo is None:
                    exited_at = exited_at.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - exited_at.astimezone(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return False, "RE-ENTRY COOLDOWN TIMESTAMP INVALID"
            if elapsed < 0:
                return False, "RE-ENTRY COOLDOWN TIMESTAMP IS IN THE FUTURE"
            if elapsed < REENTRY_COOLDOWN_MINUTES * 60:
                return False, "RE-ENTRY COOLDOWN ACTIVE"

    return True, None


def _force_stop_reason():
    try:
        import trading_control
        return "FORCE STOP ACTIVE" if trading_control.is_force_stopped() else None
    except Exception:
        return "TRADING CONTROL UNAVAILABLE"


def check_trade_risk(
    balance,
    open_positions,
    daily_loss,
    trade_size,
    strategy="main",
    main_positions=None,
    sniper_positions=None,
    token_address=None,
    mode="paper"
):

    force_stop_reason = _force_stop_reason()
    if force_stop_reason:
        return False, force_stop_reason

    if strategy not in VALID_STRATEGIES:
        return False, "INVALID STRATEGY"

    if not all(valid_number(value) for value in (
        balance,
        daily_loss,
        trade_size
    )):
        return False, "INVALID NUMERIC VALUE"

    if not valid_count(open_positions):
        return False, "INVALID OPEN POSITION COUNT"

    if main_positions is not None:
        if not valid_count(main_positions):
            return False, "INVALID MAIN POSITION COUNT"

    if sniper_positions is not None:
        if not valid_count(sniper_positions):
            return False, "INVALID SNIPER POSITION COUNT"

    if main_positions is not None and sniper_positions is not None:
        if int(main_positions) + int(sniper_positions) != int(open_positions):
            return False, "INCONSISTENT TOTAL AND STRATEGY POSITION COUNTS"

    balance = float(balance)
    daily_loss = float(daily_loss)
    trade_size = float(trade_size)
    open_positions = int(open_positions)

    if balance < 0:
        return False, "INVALID BALANCE"

    if daily_loss < 0:
        return False, "INVALID DAILY LOSS"

    if trade_size < MIN_TRADE_SIZE:
        return False, "TRADE SIZE BELOW MINIMUM"

    if trade_size > MAX_TRADE_SIZE:
        return False, "TRADE SIZE ABOVE MAXIMUM"

    if balance < trade_size:
        return False, "INSUFFICIENT BALANCE"

    # Position sizing must shrink with the remaining account balance.
    # This prevents the fixed default ticket from becoming oversized after
    # a drawdown or a series of open positions.
    max_balance_risk = balance * MAX_TRADE_BALANCE_FRACTION
    if trade_size > max_balance_risk:
        return False, "TRADE SIZE EXCEEDS BALANCE RISK FRACTION"

    if daily_loss >= MAX_DAILY_LOSS:
        return False, "DAILY LOSS LIMIT REACHED"

    history_allowed, history_reason = _check_history_guards(token_address, mode, strategy)
    if not history_allowed:
        return False, history_reason

    if open_positions >= MAX_TOTAL_POSITIONS:
        return False, "TOTAL POSITION LIMIT REACHED"

    # Legacy compatibility:
    # If neither strategy count is supplied,
    # open_positions is treated as this strategy's count.

    if main_positions is None and sniper_positions is None:
        strategy_positions = open_positions

    elif strategy == "main":
        if main_positions is None:
            return False, "MAIN POSITION COUNT MISSING"

        strategy_positions = int(main_positions)

    else:
        if sniper_positions is None:
            return False, "SNIPER POSITION COUNT MISSING"

        strategy_positions = int(sniper_positions)

    if strategy_positions >= get_position_limit(strategy):
        if strategy == "main":
            return False, "MAIN POSITION LIMIT REACHED"

        return False, "SNIPER POSITION LIMIT REACHED"

    return True, "TRADE APPROVED"


# ==========================================
# TRADE APPROVAL - BOOLEAN
# ==========================================

def can_open_trade(
    balance,
    open_positions,
    daily_loss,
    trade_size,
    strategy="main",
    main_positions=None,
    sniper_positions=None,
    token_address=None,
    mode="paper"
):

    approved, _ = check_trade_risk(
        balance=balance,
        open_positions=open_positions,
        daily_loss=daily_loss,
        trade_size=trade_size,
        strategy=strategy,
        main_positions=main_positions,
        sniper_positions=sniper_positions,
        token_address=token_address,
        mode=mode
    )

    return approved


# ==========================================
# STOP LOSS / TAKE PROFIT
# ==========================================

def calculate_stop_loss(entry_price):

    if not valid_number(entry_price):
        return None

    entry_price = float(entry_price)

    if entry_price <= 0:
        return None

    return entry_price * (
        1 - STOP_LOSS_PERCENT / 100
    )


def calculate_take_profit(entry_price):

    if not valid_number(entry_price):
        return None

    entry_price = float(entry_price)

    if entry_price <= 0:
        return None

    return entry_price * (
        1 + TAKE_PROFIT_PERCENT / 100
    )


def calculate_position_change(entry_price, current_price):

    if not valid_number(entry_price):
        return None

    if not valid_number(current_price):
        return None

    entry_price = float(entry_price)
    current_price = float(current_price)

    if entry_price <= 0 or current_price <= 0:
        return None

    return round(
        ((current_price / entry_price) - 1) * 100,
        2
    )


# ==========================================
# RISK SUMMARY
# ==========================================

def get_risk_summary():

    return {
        "max_trade_size": MAX_TRADE_SIZE,
        "min_trade_size": MIN_TRADE_SIZE,
        "max_main_positions": MAX_MAIN_POSITIONS,
        "max_sniper_positions": MAX_SNIPER_POSITIONS,
        "max_total_positions": MAX_TOTAL_POSITIONS,
        "max_daily_loss": MAX_DAILY_LOSS,
        "period_loss_window_hours": PERIOD_LOSS_WINDOW_HOURS,
        "max_period_loss": MAX_PERIOD_LOSS,
        "reentry_cooldown_minutes": REENTRY_COOLDOWN_MINUTES,
        "max_trade_balance_fraction": MAX_TRADE_BALANCE_FRACTION,
        "stop_loss_percent": STOP_LOSS_PERCENT,
        "take_profit_percent": TAKE_PROFIT_PERCENT
    }


# ==========================================
# TEST
# ==========================================

if __name__ == "__main__":

    print("================================")
    print("TROJAN V2 RISK MANAGER")
    print("================================")

    print("RISK SETTINGS:", get_risk_summary())

    approved, reason = check_trade_risk(
        balance=100,
        open_positions=0,
        daily_loss=0,
        trade_size=5,
        strategy="main",
        main_positions=0,
        sniper_positions=0
    )

    print("TEST RESULT:", approved)
    print("TEST REASON:", reason)

    print("STOP LOSS:", calculate_stop_loss(1.0))
    print("TAKE PROFIT:", calculate_take_profit(1.0))

    print("RISK MANAGER READY")
