STARTING_BALANCE = 100.00
TRADE_SIZE = 5.00

STOP_LOSS = 0.20
TAKE_PROFIT = 0.30

balance = STARTING_BALANCE
positions = {}


def open_position(token_address, entry_price):

    global balance

    if token_address in positions:
        return "Position already exists"

    if balance < TRADE_SIZE:
        return "Insufficient balance"

    if entry_price <= 0:
        return "Invalid price"

    balance -= TRADE_SIZE

    positions[token_address] = {
        "entry_price": entry_price,
        "invested": TRADE_SIZE
    }

    return "PAPER BUY: " + token_address


def close_position(token_address, current_price, reason):

    global balance

    if token_address not in positions:
        return "Position not found"

    if current_price <= 0:
        return "Invalid price"

    position = positions[token_address]

    entry = position["entry_price"]
    invested = position["invested"]

    returned_amount = invested * (current_price / entry)

    balance += returned_amount

    profit_loss = round(returned_amount - invested, 2)

    del positions[token_address]

    return {
        "result": reason,
        "profit_loss": profit_loss,
        "balance": round(balance, 2)
    }


def emergency_sell(token_address, current_price):

    return close_position(
        token_address,
        current_price,
        "EMERGENCY LIQUIDITY EXIT"
    )


def update_position(token_address, current_price):

    if token_address not in positions:
        return "Position not found"

    if current_price <= 0:
        return "Invalid price"

    position = positions[token_address]

    entry = position["entry_price"]

    change = (current_price / entry) - 1

    if change <= -STOP_LOSS:

        return close_position(
            token_address,
            current_price,
            "STOP LOSS"
        )

    elif change >= TAKE_PROFIT:

        return close_position(
            token_address,
            current_price,
            "TAKE PROFIT"
        )

    return "Position still open"


if __name__ == "__main__":

    print("PAPER TRADING 3.0 READY")
    print("Emergency sell enabled")
