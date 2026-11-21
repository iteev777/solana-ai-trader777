import database
import paper_trading as paper

# Oikeat kaupat pysyvät lukittuina.
LIVE_TRADING_ENABLED = False

TRADING_MODES = {
    "paper": {
        "enabled": True,
        "description": "Virtual paper trading"
    },
    "live": {
        "enabled": True,
        "description": "Shadow signals only - real trading locked"
    }
}


def get_available_modes():
    return {
        mode: settings.copy()
        for mode, settings in TRADING_MODES.items()
    }


def is_mode_enabled(mode):
    if mode not in TRADING_MODES:
        return False

    if mode == "live":
        return True

    return TRADING_MODES[mode]["enabled"]


def is_live_execution_enabled():
    return LIVE_TRADING_ENABLED


def get_engine(mode):
    if mode == "paper":
        return paper

    if mode == "live":
        raise RuntimeError(
            "LIVE TRADING IS LOCKED - SHADOW MODE ONLY"
        )

    raise ValueError("Unknown trading mode: " + str(mode))


def process_approved_signal(
    token_address,
    price,
    strategy="main"
):
    results = {}

    # PAPER: avaa virtuaalisen position.
    if is_mode_enabled("paper"):
        results["paper"] = paper.open_position(
            token_address,
            price,
            strategy=strategy
        )

    # LIVE: vastaanottaa signaalin, mutta ei tee oikeaa kauppaa.
    if is_mode_enabled("live"):
        if LIVE_TRADING_ENABLED:
            results["live"] = "LIVE EXECUTION NOT IMPLEMENTED"
        else:
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


def get_balance(mode):
    if mode == "paper":
        return paper.balance

    if mode == "live":
        return get_account_summary("live")["balance"]

    raise ValueError("Unknown trading mode: " + str(mode))


def get_positions(mode):
    if mode == "paper":
        return paper.positions

    if mode == "live":
        return database.get_positions("live")

    raise ValueError("Unknown trading mode: " + str(mode))


def position_count(mode, strategy):
    positions = get_positions(mode)

    return sum(
        1
        for position in positions.values()
        if position.get("strategy", "main") == strategy
    )


def open_position(
    mode,
    token_address,
    price,
    strategy="main"
):
    if mode == "live":
        return "LIVE TRADING LOCKED"

    return paper.open_position(
        token_address,
        price,
        strategy=strategy
    )


def close_position(mode, token_address, price, reason):
    if mode == "live":
        return "LIVE TRADING LOCKED"

    return paper.close_position(
        token_address,
        price,
        reason
    )


def update_position(mode, token_address, price):
    if mode == "live":
        return "LIVE TRADING LOCKED"

    return paper.update_position(
        token_address,
        price
    )


def get_account_summary(mode):
    if mode == "paper":
        return {
            "mode": "paper",
            "balance": round(paper.balance, 2),
            "positions": len(paper.positions),
            "invested": round(
                sum(
                    p["invested"]
                    for p in paper.positions.values()
                ),
                2
            ),
            "execution_enabled": True
        }

    if mode == "live":
        database.initialize_account("live", 0.00)

        account = database.get_account_state("live")
        positions = database.get_positions("live")

        return {
            "mode": "live",
            "balance": round(float(account["balance"]), 2),
            "positions": len(positions),
            "invested": round(
                sum(
                    p["invested"]
                    for p in positions.values()
                ),
                2
            ),
            "execution_enabled": False,
            "status": "SHADOW MODE - LOCKED"
        }

    raise ValueError("Unknown trading mode: " + str(mode))


if __name__ == "__main__":
    database.initialize_database()

    print("TRADING MANAGER READY")
    print("PAPER:", get_account_summary("paper"))
    print("LIVE:", get_account_summary("live"))
    print("LIVE EXECUTION:", is_live_execution_enabled())
