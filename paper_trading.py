STARTING_BALANCE = 100.00
TRADE_SIZE = 5.00

STOP_LOSS = 0.20
TAKE_PROFIT = 0.35

balance = STARTING_BALANCE
positions = {}


def open_position(token_address, entry_price):

    global balance

    if token_address in positions:
        return "Position already exists"

    if balance < TRADE_SIZE:
        return "Insufficient balance"

    if entry_price <= 0:
        return "Invalid entry price"

    balance -= TRADE_SIZE

    positions[token_address] = {
        "entry_price": entry_price,
        "invested": TRADE_SIZE
    }

    return "PAPER BUY: " + token_address


def update_position(token_address, current_price):

    global balance

    if token_address not in positions:
        return "Position not found"

    if current_price <= 0:
        return "Invalid price"

    position = positions[token_address]

    entry = position["entry_price"]
    invested = position["invested"]

    change = (current_price / entry) - 1

    if change <= -STOP_LOSS:
        reason = "STOP LOSS"

    elif change >= TAKE_PROFIT:
        reason = "TAKE PROFIT"

    else:
        return "Position still open"

    returned_amount = invested * (1 + change)

    balance += returned_amount
    del positions[token_address]

    return {
        "result": reason,
        "profit_loss": round(returned_amount - invested, 2),
        "balance": round(balance, 2)
    }


if __name__ == "__main__":
    print("PAPER TRADING 1.0 READY")
    print("Starting balance:", balance, "USD")
