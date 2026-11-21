import database

STARTING_BALANCE = 100.00
TRADE_SIZE = 5.00

STOP_LOSS = 0.20
TAKE_PROFIT = 0.30

MAX_POSITIONS_PER_STRATEGY = 10
MODE = "paper"

# Initialize and restore paper account
database.initialize_database()
database.initialize_account(MODE, STARTING_BALANCE)

account = database.get_account_state(MODE)

balance = account["balance"]
positions = database.get_positions(MODE)


def position_count(strategy):
    return sum(
        1
        for position in positions.values()
        if position.get("strategy", "main") == strategy
    )


def open_position(token_address, entry_price, strategy="main"):

    global balance

    if token_address in positions:
        return "Position already exists"

    if strategy not in ("main", "sniper"):
        return "Invalid strategy"

    if position_count(strategy) >= MAX_POSITIONS_PER_STRATEGY:
        return "Strategy position limit reached"

    if balance < TRADE_SIZE:
        return "Insufficient balance"

    if entry_price <= 0:
        return "Invalid price"

    new_balance = balance - TRADE_SIZE

    database.save_position(
        MODE,
        token_address,
        entry_price,
        TRADE_SIZE,
        strategy
    )

    database.save_account_state(
        MODE,
        new_balance,
        account["daily_loss"],
        account["daily_realized_pnl"]
    )

    balance = new_balance

    positions[token_address] = {
        "entry_price": entry_price,
        "invested": TRADE_SIZE,
        "strategy": strategy
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

    profit_loss = round(returned_amount - invested, 2)
    new_balance = balance + returned_amount

    strategy = position.get("strategy", "main")

    new_daily_loss = account["daily_loss"] + max(0, -profit_loss)
    new_daily_pnl = account["daily_realized_pnl"] + profit_loss

    database.delete_position(MODE, token_address)

    database.save_account_state(
        MODE,
        new_balance,
        new_daily_loss,
        new_daily_pnl
    )

    balance = new_balance

    account["balance"] = balance
    account["daily_loss"] = new_daily_loss
    account["daily_realized_pnl"] = new_daily_pnl

    del positions[token_address]

    return {
        "result": reason,
        "profit_loss": profit_loss,
        "balance": round(balance, 2),
        "strategy": strategy,
        "invested": invested,
        "returned_amount": round(returned_amount, 2)
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

    if change >= TAKE_PROFIT:
        return close_position(
            token_address,
            current_price,
            "TAKE PROFIT"
        )

    return "Position still open"


def get_account_summary():

    return {
        "mode": MODE,
        "balance": round(balance, 2),
        "positions": len(positions),
        "invested": round(
            sum(p["invested"] for p in positions.values()), 2
        ),
        "daily_loss": account["daily_loss"],
        "daily_realized_pnl": account["daily_realized_pnl"]
    }


if __name__ == "__main__":

    print("PAPER TRADING SYSTEM READY")
    print("MODE:", MODE)
    print("ACCOUNT:", get_account_summary())
