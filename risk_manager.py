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
    try:
        number = float(value)
        return math.isfinite(number)
    except (ValueError, TypeError):
        return False


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
# TRADE APPROVAL
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

    if not all(valid_number(value) for value in (
        balance,
        open_positions,
        daily_loss,
        trade_size
    )):
        return False

    balance = float(balance)
    open_positions = int(open_positions)
    daily_loss = float(daily_loss)
    trade_size = float(trade_size)

    if balance < 0:
        return False

    if open_positions < 0:
        return False

    if daily_loss < 0:
        return False

    if strategy not in VALID_STRATEGIES:
        return False

    if trade_size < MIN_TRADE_SIZE:
        return False

    if trade_size > MAX_TRADE_SIZE:
        return False

    if balance < trade_size:
        return False

    if daily_loss >= MAX_DAILY_LOSS:
        return False

    if open_positions >= MAX_TOTAL_POSITIONS:
        return False

    position_limit = get_position_limit(strategy)

    if strategy == "main" and main_positions is not None:
        if not valid_number(main_positions):
            return False

        if int(main_positions) >= position_limit:
            return False

    elif strategy == "sniper" and sniper_positions is not None:
        if not valid_number(sniper_positions):
            return False

        if int(sniper_positions) >= position_limit:
            return False

    elif main_positions is None and sniper_positions is None:
        # Legacy compatibility:
        # open_positions is interpreted as this strategy's count.
        if open_positions >= position_limit:
            return False

    return True


# ==========================================
# APPROVAL WITH REASON
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
        open_positions,
        daily_loss,
        trade_size
    )):
        return False, "INVALID NUMERIC VALUE"

    if float(balance) < 0:
        return False, "INVALID BALANCE"

    if float(trade_size) < MIN_TRADE_SIZE:
        return False, "TRADE SIZE BELOW MINIMUM"

    if float(trade_size) > MAX_TRADE_SIZE:
        return False, "TRADE SIZE ABOVE MAXIMUM"

    if float(balance) < float(trade_size):
        return False, "INSUFFICIENT BALANCE"

    if float(daily_loss) >= MAX_DAILY_LOSS:
        return False, "DAILY LOSS LIMIT REACHED"

    if int(open_positions) >= MAX_TOTAL_POSITIONS:
        return False, "TOTAL POSITION LIMIT REACHED"

    if strategy == "main" and main_positions is not None:
        if int(main_positions) >= MAX_MAIN_POSITIONS:
            return False, "MAIN POSITION LIMIT REACHED"

    if strategy == "sniper" and sniper_positions is not None:
        if int(sniper_positions) >= MAX_SNIPER_POSITIONS:
            return False, "SNIPER POSITION LIMIT REACHED"

    return True, "TRADE APPROVED"


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
        strategy="main"
    )

    print("TEST RESULT:", approved)
    print("TEST REASON:", reason)

    print("STOP LOSS:", calculate_stop_loss(1.0))
    print("TAKE PROFIT:", calculate_take_profit(1.0))

    print("RISK MANAGER READY")
