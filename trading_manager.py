import paper_trading as paper


LIVE_TRADING_ENABLED = False

TRADING_MODES = {
    "paper": {
        "enabled": True,
        "description": "Virtual paper trading"
    },
    "live": {
        "enabled": False,
        "description": "Real Solana trading"
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
        return LIVE_TRADING_ENABLED

    return TRADING_MODES[mode]["enabled"]


def get_engine(mode):

    if mode == "paper":
        return paper

    if mode == "live":

        if not LIVE_TRADING_ENABLED:
            raise RuntimeError(
                "LIVE TRADING IS LOCKED"
            )

        from live_trading import live

        return live

    raise ValueError("Unknown trading mode: " + str(mode))


def get_balance(mode):

    engine = get_engine(mode)

    return engine.balance


def get_positions(mode):

    engine = get_engine(mode)

    return engine.positions


def position_count(mode, strategy):

    engine = get_engine(mode)

    return engine.position_count(strategy)


def open_position(mode, token_address, price, strategy="main"):

    engine = get_engine(mode)

    return engine.open_position(
        token_address,
        price,
        strategy=strategy
    )


def close_position(mode, token_address, price, reason):

    engine = get_engine(mode)

    return engine.close_position(
        token_address,
        price,
        reason
    )


def update_position(mode, token_address, price):

    engine = get_engine(mode)

    return engine.update_position(
        token_address,
        price
    )


def get_account_summary(mode):

    engine = get_engine(mode)

    return engine.get_account_summary()


if __name__ == "__main__":

    print("TRADING MANAGER READY")
    print("PAPER ENABLED:", is_mode_enabled("paper"))
    print("LIVE ENABLED:", is_mode_enabled("live"))

    print("PAPER ACCOUNT:")
    print(get_account_summary("paper"))

    print("LIVE ACCOUNT:")
    print("LOCKED - NOT YET CONFIGURED")
