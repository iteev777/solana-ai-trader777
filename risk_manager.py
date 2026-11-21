import math


# ==========================================
# TROJAN V2 - RISK MANAGER
# ==========================================

STARTING_BALANCE = 100.00

MAX_TRADE_SIZE = 5.00
MIN_TRADE_SIZE = 1.00

MAX_MAIN_POSITIONS = 10
MAX_SNIPER_POSITIONS = 10
MAX_TOTAL_POSITIONS = 15

MAX_DAILY_LOSS = 50.00

STOP_LOSS_PERCENT = 20
TAKE_PROFIT_PERCENT = 30

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

def check_trade_risk(
    balance,
    open_positions,
    daily_loss,
    trade_size,
    strategy="main",
    main_positions=None,
    sniper_positions=None
):

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

    if daily_loss >= MAX_DAILY_LOSS:
        return False, "DAILY LOSS LIMIT REACHED"

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
    sniper_positions=None
):

    approved, _ = check_trade_risk(
        balance=balance,
        open_positions=open_positions,
        daily_loss=daily_loss,
        trade_size=trade_size,
        strategy=strategy,
        main_positions=main_positions,
        sniper_positions=sniper_positions
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
