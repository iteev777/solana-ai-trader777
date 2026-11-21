LIQUIDITY_DROP_LIMIT = 20.0

previous_liquidity = {}


def check_liquidity(token_address, current_liquidity):

    previous = previous_liquidity.get(token_address)

    if current_liquidity is None:
        print("LIQUIDITY UNKNOWN:", token_address)
        return "UNKNOWN"

    if current_liquidity < 0:
        print("INVALID LIQUIDITY:", token_address)
        return "UNKNOWN"

    if previous is None:
        previous_liquidity[token_address] = current_liquidity

        if current_liquidity == 0:
            return "COLLAPSE"

        return "NORMAL"

    if previous <= 0:
        return "COLLAPSE"

    drop_percent = (
        (previous - current_liquidity) / previous
    ) * 100

    print("LIQUIDITY:", token_address)
    print("PREVIOUS:", round(previous, 2), "USD")
    print("CURRENT:", round(current_liquidity, 2), "USD")
    print("DROP:", round(drop_percent, 2), "%")

    previous_liquidity[token_address] = current_liquidity

    if current_liquidity == 0 or drop_percent >= LIQUIDITY_DROP_LIMIT:

        print("CRITICAL: LIQUIDITY COLLAPSE DETECTED")

        return "COLLAPSE"

    return "NORMAL"


if __name__ == "__main__":

    print("LIQUIDITY MONITOR 2.0 READY")
    print("DROP LIMIT:", LIQUIDITY_DROP_LIMIT, "%")
