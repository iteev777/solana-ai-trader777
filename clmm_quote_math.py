"""Integer-only Raydium CLMM tick and exact-input quote arithmetic.

Arithmetic follows the reference venue's bounded integer model: pool state and
amounts are width-checked, intermediate products are bounded to U256, and
instruction-sized output/fee amounts saturate at u64 rather than leaking
unbounded Python integers into a transaction quote.
"""
from dataclasses import dataclass

Q64 = 1 << 64
FEE_DENOMINATOR = 1_000_000
MIN_TICK = -443_636
MAX_TICK = 443_636
U64_MAX = (1 << 64) - 1
U128_MAX = (1 << 128) - 1
U256_MAX = (1 << 256) - 1

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


def _integer(value, name, minimum=0, maximum=U128_MAX):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(name + " is outside its supported integer range")
    return value


def _mul_div_floor(a, b, denominator):
    _integer(a, "multiplication operand")
    _integer(b, "multiplication operand")
    _integer(denominator, "division denominator", minimum=1)
    product = a * b
    if product > U256_MAX:
        raise ValueError("CLMM multiplication exceeds the U256 intermediate range")
    result = product // denominator
    if result > U128_MAX:
        raise ValueError("CLMM division result exceeds the u128 range")
    return result


def _mul_div_ceil(a, b, denominator):
    _integer(a, "multiplication operand")
    _integer(b, "multiplication operand")
    _integer(denominator, "division denominator", minimum=1)
    product = a * b
    if product > U256_MAX:
        raise ValueError("CLMM multiplication exceeds the U256 intermediate range")
    result = (product + denominator - 1) // denominator
    if result > U128_MAX:
        raise ValueError("CLMM division result exceeds the u128 range")
    return result


def _scaled_liquidity(liquidity):
    liquidity = _integer(liquidity, "liquidity", minimum=1)
    scaled = liquidity * Q64
    if scaled > U128_MAX:
        raise ValueError("CLMM liquidity cannot be represented in the Q64.64 u128 intermediate")
    return scaled


def ceil_div(numerator, denominator):
    _integer(numerator, "ceil division numerator")
    _integer(denominator, "ceil division denominator", minimum=1)
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
        ratio = U128_MAX // ratio
    if ratio <= 0 or ratio > U128_MAX:
        raise ValueError("invalid Q64.64 square-root price")
    return ratio


def amount_in_to_target(zero_for_one, liquidity, sqrt_from, sqrt_to):
    if not isinstance(zero_for_one, bool):
        raise ValueError("swap direction must be boolean")
    liquidity = _integer(liquidity, "liquidity", minimum=1)
    sqrt_from = _integer(sqrt_from, "starting square-root price", minimum=1)
    sqrt_to = _integer(sqrt_to, "target square-root price", minimum=1)
    if zero_for_one:
        if sqrt_from <= sqrt_to:
            return 0
        numerator = _scaled_liquidity(liquidity)
        step = _mul_div_ceil(numerator, sqrt_from - sqrt_to, sqrt_from)
        return ceil_div(step, sqrt_to)
    if sqrt_to <= sqrt_from:
        return 0
    return _mul_div_ceil(liquidity, sqrt_to - sqrt_from, Q64)


def amount_out_for_move(zero_for_one, liquidity, sqrt_from, sqrt_to):
    if not isinstance(zero_for_one, bool):
        raise ValueError("swap direction must be boolean")
    liquidity = _integer(liquidity, "liquidity", minimum=1)
    sqrt_from = _integer(sqrt_from, "starting square-root price", minimum=1)
    sqrt_to = _integer(sqrt_to, "target square-root price", minimum=1)
    if zero_for_one:
        if sqrt_to > sqrt_from:
            raise ValueError("zero-for-one price must not increase")
        output = _mul_div_floor(liquidity, sqrt_from - sqrt_to, Q64)
    else:
        if sqrt_to < sqrt_from:
            raise ValueError("one-for-zero price must not decrease")
        numerator = _scaled_liquidity(liquidity)
        intermediate = _mul_div_floor(numerator, sqrt_to - sqrt_from, sqrt_to)
        output = intermediate // sqrt_from
    return min(output, U64_MAX)


def next_sqrt_price_from_input(zero_for_one, liquidity, sqrt_price, net_input):
    if not isinstance(zero_for_one, bool):
        raise ValueError("swap direction must be boolean")
    liquidity = _integer(liquidity, "liquidity", minimum=1)
    sqrt_price = _integer(sqrt_price, "square-root price", minimum=1)
    net_input = _integer(net_input, "net input")
    if zero_for_one:
        numerator = _scaled_liquidity(liquidity)
        product = net_input * sqrt_price
        if product > U128_MAX or numerator + product > U128_MAX:
            raise ValueError("CLMM partial-step denominator exceeds the u128 range")
        return _mul_div_ceil(numerator, sqrt_price, numerator + product)
    delta = _mul_div_floor(net_input, Q64, liquidity)
    next_price = sqrt_price + delta
    if next_price > U128_MAX:
        raise ValueError("CLMM next square-root price exceeds the u128 range")
    return next_price


def _normalize_ticks(initialized_ticks):
    if not isinstance(initialized_ticks, (list, tuple)):
        raise ValueError("initialized ticks must be a list or tuple")
    normalized = []
    seen = set()
    for item in initialized_ticks:
        if isinstance(item, Tick):
            index, liquidity_net = item.index, item.liquidity_net
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            index, liquidity_net = item
        else:
            raise ValueError("initialized tick has an invalid shape")
        if isinstance(index, bool) or not isinstance(index, int) or not MIN_TICK <= index <= MAX_TICK:
            raise ValueError("initialized tick index is invalid")
        if isinstance(liquidity_net, bool) or not isinstance(liquidity_net, int) or not -(1 << 127) <= liquidity_net < (1 << 127):
            raise ValueError("initialized tick liquidity delta is outside i128")
        if index in seen:
            raise ValueError("initialized tick indexes must be unique")
        seen.add(index)
        normalized.append(Tick(index, liquidity_net))
    return normalized


def quote_exact_input(sqrt_price_x64, liquidity, tick_current, initialized_ticks,
                      zero_for_one, amount_in, fee_rate, coverage_tick):
    """Walk initialized ticks; refuse any input that exceeds loaded array coverage."""
    sqrt_price_x64 = _integer(sqrt_price_x64, "square-root price", minimum=1)
    liquidity = _integer(liquidity, "liquidity", minimum=1)
    amount_in = _integer(amount_in, "input amount", minimum=1, maximum=U64_MAX)
    if isinstance(fee_rate, bool) or not isinstance(fee_rate, int) or not 0 <= fee_rate < FEE_DENOMINATOR:
        raise ValueError("CLMM trade fee rate is invalid")
    if isinstance(tick_current, bool) or not isinstance(tick_current, int) or not MIN_TICK <= tick_current <= MAX_TICK:
        raise ValueError("current tick is invalid")
    if isinstance(coverage_tick, bool) or not isinstance(coverage_tick, int) or not MIN_TICK <= coverage_tick <= MAX_TICK:
        raise ValueError("tick-array coverage boundary is invalid")
    if not isinstance(zero_for_one, bool):
        raise ValueError("swap direction must be boolean")
    if zero_for_one and coverage_tick > tick_current:
        raise ValueError("zero-for-one coverage boundary is on the wrong side of the current tick")
    if not zero_for_one and coverage_tick <= tick_current:
        raise ValueError("one-for-zero coverage boundary is on the wrong side of the current tick")
    lower_tick_price = sqrt_price_at_tick(tick_current)
    if tick_current < MAX_TICK:
        upper_tick_price = sqrt_price_at_tick(tick_current + 1)
        if not lower_tick_price <= sqrt_price_x64 < upper_tick_price:
            raise ValueError("current tick and square-root price are inconsistent")
    elif sqrt_price_x64 < lower_tick_price:
        raise ValueError("current tick and square-root price are inconsistent")

    ordered = sorted(
        _normalize_ticks(initialized_ticks),
        key=lambda item: item.index,
        reverse=zero_for_one,
    )
    relevant = [item for item in ordered
                if (item.index <= tick_current if zero_for_one else item.index > tick_current)]
    steps = [(item.index, item.liquidity_net, sqrt_price_at_tick(item.index), True)
             for item in relevant]
    steps.append((coverage_tick, 0, sqrt_price_at_tick(coverage_tick), False))

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
        gross = _mul_div_ceil(needed_net, FEE_DENOMINATOR, FEE_DENOMINATOR - fee_rate) if needed_net else 0
        if gross <= remaining:
            total_fee += gross - needed_net
            total_out += amount_out_for_move(
                zero_for_one, current_liquidity, current_sqrt, target_sqrt
            )
            remaining -= gross
            current_sqrt = target_sqrt
            if is_initialized:
                current_liquidity += -liquidity_net if zero_for_one else liquidity_net
                if current_liquidity > U128_MAX:
                    raise ValueError("initialized tick increases active liquidity beyond u128")
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
            if net_input == 0:
                remaining = 0
                break
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
        "out_amount": min(total_out, U64_MAX),
        "fee_amount": min(total_fee, U64_MAX),
        "sqrt_price_after_x64": current_sqrt,
        "liquidity_after": current_liquidity,
        "tick_crossings": crossed,
    }
