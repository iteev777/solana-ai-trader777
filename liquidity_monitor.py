
LIQUIDITY_DROP_LIMIT = 20.0

previous_liquidity = {}


def check_liquidity(token_address, current_liquidity):

    if current_liquidity <= 0:
        print("LIQUIDITY WARNING: No liquidity data")
        return "UNKNOWN"

    previous = previous_liquidity.get(token_address)

    previous_liquidity[token_address] = current_liquidity

    if previous is None or previous <= 0:
        return "NORMAL"

    drop_percent = (
        (previous - current_liquidity) / previous
    ) * 100

    print("LIQUIDITY:", token_address)
    print("PREVIOUS:", round(previous, 2), "USD")
    print("CURRENT:", round(current_liquidity, 2), "USD")
    print("DROP:", round(drop_percent, 2), "%")

    if drop_percent >= LIQUIDITY_DROP_LIMIT:

        print("CRITICAL: LIQUIDITY COLLAPSE DETECTED")

        return "COLLAPSE"

    return "NORMAL"


if __name__ == "__main__":

    print("LIQUIDITY MONITOR READY")
    print("DROP LIMIT:", LIQUIDITY_DROP_LIMIT, "%")
