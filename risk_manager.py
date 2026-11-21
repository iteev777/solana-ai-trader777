STARTING_BALANCE = 100.00

MAX_TRADE_SIZE = 5.00
MAX_OPEN_POSITIONS = 10

MAX_DAILY_LOSS = 50.00
STOP_LOSS_PERCENT = 20
TAKE_PROFIT_PERCENT = 35


def can_open_trade(balance, open_positions, daily_loss, trade_size):

    if balance < trade_size:
        return False

    if trade_size > MAX_TRADE_SIZE:
        return False

    if open_positions >= MAX_OPEN_POSITIONS:
        return False

    if daily_loss >= MAX_DAILY_LOSS:
        return False

    return True


def calculate_stop_loss(entry_price):
    return entry_price * (1 - STOP_LOSS_PERCENT / 100)


def calculate_take_profit(entry_price):
    return entry_price * (1 + TAKE_PROFIT_PERCENT / 100)


if __name__ == "__main__":
    print("RISK MANAGER 1.0 READY")
    print("Maximum trade:", MAX_TRADE_SIZE, "USD")
    print("Maximum daily loss:", MAX_DAILY_LOSS, "USD")
