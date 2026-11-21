STARTING_BALANCE = 100.00

MAX_TRADE_SIZE = 5.00

MAX_MAIN_POSITIONS = 10
MAX_SNIPER_POSITIONS = 10

MAX_DAILY_LOSS = 50.00

STOP_LOSS_PERCENT = 20
TAKE_PROFIT_PERCENT = 30


def can_open_trade(
    balance,
    open_positions,
    daily_loss,
    trade_size,
    strategy="main"
):

    if balance < trade_size:
        return False

    if trade_size > MAX_TRADE_SIZE:
        return False

    if daily_loss >= MAX_DAILY_LOSS:
        return False

    if strategy == "main":
        position_limit = MAX_MAIN_POSITIONS

    elif strategy == "sniper":
        position_limit = MAX_SNIPER_POSITIONS

    else:
        return False

    if open_positions >= position_limit:
        return False

    return True


def calculate_stop_loss(entry_price):

    return entry_price * (
        1 - STOP_LOSS_PERCENT / 100
    )


def calculate_take_profit(entry_price):

    return entry_price * (
        1 + TAKE_PROFIT_PERCENT / 100
    )


if __name__ == "__main__":

    print("RISK MANAGER 2.0 READY")
    print("MAX MAIN POSITIONS:", MAX_MAIN_POSITIONS)
    print("MAX SNIPER POSITIONS:", MAX_SNIPER_POSITIONS)
    print("MAX TRADE SIZE:", MAX_TRADE_SIZE)
    print("MAX DAILY LOSS:", MAX_DAILY_LOSS)
