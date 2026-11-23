"""Integer-only Raydium CLMM tick and exact-input quote arithmetic."""
from dataclasses import dataclass

Q64 = 1 << 64
FEE_DENOMINATOR = 1_000_000
MIN_TICK = -443_636
MAX_TICK = 443_636

_TICK_FACTORS = (
    (0x2, 0xFFF97272373D4000), (0x4, 0xFFF2E50F5F657000),
    (0x8, 0xFFE5CACA7E10F000), (0x10, 0xFFCB9843D60F7000),
    (0x20, 0xFF973B41FA98E800), (0x40, 0xFF2EA16466C9B000),
    (0x80, 0xFE5DEE046A9A3800), (0x100, 0xFCBE86C7900BB000),
    (0x200, 0xF987A7253AC65800), (0x400, 0xF3392B0822BB6000),
    (0x800, 0xE7159475A2CAF000), (0x1000, 0xD097F3BDFD2F2000),
    (0x2000, 0xA9F746462D9F8000), (0x4000, 0x70D869A156F31C00),
    (0x8000, 0x31BE135F97ED3200), (0x10000, 0x9AA508B5B85A500),
    (0x20000, 0x5D6AF8DEDC582C), (0x40000, 0x2216E584F5FA),
)


@dataclass(frozen=True)
class Tick:
    index: int
    liquidity_net: int


def ceil_div(numerator, denominator):
    if denominator <= 0 or numerator < 0:
        raise ValueError("invalid ceil division operands")
    return (numerator + denominator - 1) // denominator


def sqrt_price_at_tick(tick):
    if isinstance(tick, bool) or not isinstance(tick, int) or not MIN_TICK <= tick <= MAX_TICK:
        raise ValueError("tick is outside the supported Raydium range")
    absolute = abs(tick)
    ratio = 0xFFFcb933bd6fb800 if absolute & 1 else Q64
    for bit, factor in _TICK_FACTORS:
        if absolute & bit:
            ratio = (ratio * factor) >> 64
    if tick > 0:
        ratio = ((1 << 128) - 1) // ratio
    if ratio <= 0 or ratio >= 1 << 128:
        raise ValueError("invalid Q64.64 square-root price")
    return ratio


def amount_in_to_target(zero_for_one, liquidity, sqrt_from, sqrt_to):
    if liquidity <= 0 or sqrt_from <= 0 or sqrt_to <= 0:
        raise ValueError("invalid concentrated-liquidity step state")
    if zero_for_one:
        if sqrt_from <= sqrt_to:
            return 0
        numerator = liquidity * Q64
        return ceil_div(ceil_div(numerator * (sqrt_from - sqrt_to), sqrt_from), sqrt_to)
    if sqrt_to <= sqrt_from:
        return 0
    return ceil_div(liquidity * (sqrt_to - sqrt_from), Q64)


def amount_out_for_move(zero_for_one, liquidity, sqrt_from, sqrt_to):
    if liquidity <= 0:
        raise ValueError("pool liquidity is empty")
    if zero_for_one:
        if sqrt_to > sqrt_from:
            raise ValueError("zero-for-one price must not increase")
        return liquidity * (sqrt_from - sqrt_to) // Q64
    if sqrt_to < sqrt_from:
        raise ValueError("one-for-zero price must not decrease")
    intermediate = (liquidity * Q64 * (sqrt_to - sqrt_from)) // sqrt_to
    return intermediate // sqrt_from


def next_sqrt_price_from_input(zero_for_one, liquidity, sqrt_price, net_input):
    if liquidity <= 0 or sqrt_price <= 0 or net_input < 0:
        raise ValueError("invalid partial-step state")
    if zero_for_one:
        numerator = liquidity * Q64
        return ceil_div(numerator * sqrt_price, numerator + net_input * sqrt_price)
    return sqrt_price + net_input * Q64 // liquidity


def quote_exact_input(sqrt_price_x64, liquidity, tick_current, initialized_ticks,
                      zero_for_one, amount_in, fee_rate, coverage_tick):
    """Walk initialized ticks; refuse any input that exceeds loaded array coverage."""
    for value, name in ((sqrt_price_x64, "sqrt price"), (liquidity, "liquidity"),
                        (amount_in, "input amount")):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(name + " must be a positive integer")
    if type(fee_rate) is not int or not 0 <= fee_rate < FEE_DENOMINATOR:
        raise ValueError("CLMM trade fee rate is invalid")
    if type(tick_current) is not int or not MIN_TICK <= tick_current <= MAX_TICK:
        raise ValueError("current tick is invalid")

    coverage_sqrt = sqrt_price_at_tick(coverage_tick)
    if (zero_for_one and coverage_tick > tick_current) or (
        not zero_for_one and coverage_tick <= tick_current
    ):
        raise ValueError("tick-array coverage is in the wrong direction")

    ordered = sorted(
        (item if isinstance(item, Tick) else Tick(int(item[0]), int(item[1]))
         for item in initialized_ticks),
        key=lambda item: item.index,
        reverse=zero_for_one,
    )
    relevant = [item for item in ordered
                if (item.index <= tick_current if zero_for_one else item.index > tick_current)]
    indices = {item.index for item in relevant}
    if len(indices) != len(relevant):
        raise ValueError("duplicate initialized tick")
    if any(index < coverage_tick if zero_for_one else index > coverage_tick
           for index in indices):
        raise ValueError("initialized tick exceeds verified tick-array coverage")
    steps = [(item.index, item.liquidity_net, sqrt_price_at_tick(item.index), True)
             for item in relevant]
    steps.append((coverage_tick, 0, coverage_sqrt, False))

    current_sqrt = sqrt_price_x64
    current_liquidity = liquidity
    remaining = amount_in
    total_out = total_fee = crossed = 0

    for tick_index, liquidity_net, target_sqrt, is_initialized in steps:
        if (zero_for_one and target_sqrt > current_sqrt) or (
            not zero_for_one and target_sqrt <= current_sqrt
        ):
            continue
        if current_liquidity <= 0:
            raise ValueError("liquidity became zero while crossing an initialized tick")

        available_net = remaining * (FEE_DENOMINATOR - fee_rate) // FEE_DENOMINATOR
        needed_net = amount_in_to_target(zero_for_one, current_liquidity, current_sqrt, target_sqrt)
        if available_net >= needed_net:
            gross = ceil_div(needed_net * FEE_DENOMINATOR,
                             FEE_DENOMINATOR - fee_rate) if needed_net else 0
            if gross > remaining:
                raise ValueError("fee rounding exceeds remaining input")
            total_fee += gross - needed_net
            total_out += amount_out_for_move(
                zero_for_one, current_liquidity, current_sqrt, target_sqrt
            )
            remaining -= gross
            current_sqrt = target_sqrt
            if is_initialized:
                current_liquidity += -liquidity_net if zero_for_one else liquidity_net
                crossed += 1
                if current_liquidity <= 0:
                    raise ValueError("initialized tick removes all active liquidity")
            if remaining == 0:
                break
            if not is_initialized:
                raise ValueError("input exceeds verified tick-array coverage")
        else:
            net_input = available_net
            total_fee += remaining - net_input
            next_sqrt = next_sqrt_price_from_input(
                zero_for_one, current_liquidity, current_sqrt, net_input
            )
            if (zero_for_one and next_sqrt < target_sqrt) or (
                not zero_for_one and next_sqrt > target_sqrt
            ):
                raise ValueError("partial step exceeded its verified tick boundary")
            total_out += amount_out_for_move(
                zero_for_one, current_liquidity, current_sqrt, next_sqrt
            )
            remaining = 0
            current_sqrt = next_sqrt
            break

    if remaining > 0:
        raise ValueError("input exceeds verified tick-array coverage")
    if total_out <= 0:
        raise ValueError("CLMM quote rounds to zero output")
    return {
        "out_amount": total_out,
        "fee_amount": total_fee,
        "sqrt_price_after_x64": current_sqrt,
        "liquidity_after": current_liquidity,
        "tick_crossings": crossed,
    }
