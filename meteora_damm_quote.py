"""Read-only exact-input quotes for Meteora DAMM v2 (single-band liquidity)."""
import math
import time

PROGRAM = "meteora_damm_v2"
FEE_DENOMINATOR = 1_000_000_000
MAX_SNAPSHOT_AGE_SECONDS = 8.0
MAX_SLIPPAGE_BPS = 300


def _ceil_div(n, d):
    if n < 0 or d <= 0:
        raise ValueError("invalid fee division operands")
    return (n + d - 1) // d


def _fee_numerator(raw, fee_version):
    caps = {0: 500_000_000, 1: 990_000_000}
    cap = caps.get(fee_version)
    if cap is None:
        raise ValueError("Meteora DAMM fee version is unsupported")
    cliff = int.from_bytes(raw[8:16], "little")
    mode = raw[16]
    base = cliff
    # Rate limiter mode can raise the fee above the initial cliff. Using its
    # configured ceiling at all times is conservative, including outside the
    # limiter window.
    if mode == 2:
        max_fee_bps = int.from_bytes(raw[28:32], "little")
        limiter_ceiling = _ceil_div(max_fee_bps * FEE_DENOMINATOR, 10_000)
        base = max(base, limiter_ceiling)
    # Time-scheduler modes only reduce their cliff; unknown tags also fall back
    # to the cliff, the highest rate they can charge.
    dynamic_initialized = raw[56] != 0
    variable_control = int.from_bytes(raw[68:72], "little")
    bin_step = int.from_bytes(raw[72:74], "little")
    volatility = int.from_bytes(raw[120:136], "little")
    dynamic = 0
    if dynamic_initialized and variable_control:
        scaled = volatility * bin_step
        dynamic = _ceil_div(scaled * scaled * variable_control, 100_000_000_000)
    return min(cap, base + dynamic), cap


def _decode_pool(raw, chain):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 1112:
        raise ValueError("Meteora DAMM v2 pool state is truncated")
    if raw[:8] != chain._discriminator("Pool"):
        raise ValueError("Meteora DAMM v2 pool discriminator is invalid")
    state = {
        "mint_a": chain._key(raw, 168),
        "mint_b": chain._key(raw, 200),
        "vault_a": chain._key(raw, 232),
        "vault_b": chain._key(raw, 264),
        "liquidity": chain._u128(raw, 360),
        "protocol_fee_a": chain._u64(raw, 392),
        "protocol_fee_b": chain._u64(raw, 400),
        "sqrt_min_price": chain._u128(raw, 424),
        "sqrt_max_price": chain._u128(raw, 440),
        "sqrt_price": chain._u128(raw, 456),
        "activation_point": chain._u64(raw, 472),
        "activation_type": raw[480],
        "pool_status": raw[481],
        "collect_fee_mode": raw[484],
        "fee_version": raw[486],
    }
    if state["activation_type"] not in (0, 1):
        raise ValueError("Meteora DAMM activation type is unsupported")
    if state["pool_status"] != 0:
        raise ValueError("Meteora DAMM v2 pool is not enabled for trading")
    if state["collect_fee_mode"] not in (0, 1, 2):
        raise ValueError("Meteora DAMM v2 fee collection mode is unsupported")
    if state["mint_a"] == state["mint_b"]:
        raise ValueError("Meteora DAMM v2 pool mints must differ")
    if state["liquidity"] <= 0:
        raise ValueError("Meteora DAMM v2 has no active liquidity")
    if (
        state["sqrt_min_price"] <= 0
        or state["sqrt_max_price"] <= state["sqrt_min_price"]
        or not state["sqrt_min_price"] <= state["sqrt_price"] <= state["sqrt_max_price"]
    ):
        raise ValueError("Meteora DAMM v2 square-root price is outside its configured band")
    state["fee_numerator"], state["fee_cap"] = _fee_numerator(raw, state["fee_version"])
    return state


def _curve_step(state, input_is_a, amount_in):
    liquidity = state["liquidity"]
    sqrt_price = state["sqrt_price"]
    if input_is_a:
        product = amount_in * sqrt_price
        denominator = liquidity + product
        if denominator <= 0:
            raise ValueError("Meteora DAMM v2 input denominator is invalid")
        sqrt_next = _ceil_div(liquidity * sqrt_price, denominator)
        if sqrt_next < state["sqrt_min_price"]:
            raise ValueError("Meteora DAMM v2 trade exceeds minimum price boundary")
        delta = sqrt_price - sqrt_next
        gross_out = (liquidity * delta) // (1 << 128)
    else:
        delta = (amount_in * (1 << 128)) // liquidity
        sqrt_next = sqrt_price + delta
        if sqrt_next > state["sqrt_max_price"]:
            raise ValueError("Meteora DAMM v2 trade exceeds maximum price boundary")
        spread = sqrt_next - sqrt_price
        intermediate = (liquidity * spread) // sqrt_next
        gross_out = intermediate // sqrt_price
    if gross_out <= 0:
        raise ValueError("Meteora DAMM v2 quote rounds to zero output")
    return sqrt_next, gross_out


def quote_meteora_damm_pool(pool_address, input_mint, amount_in, slippage_bps=100,
                            output_mint=None, max_snapshot_age=MAX_SNAPSHOT_AGE_SECONDS):
    import solana_onchain as chain

    if not isinstance(pool_address, str) or not pool_address.strip():
        raise ValueError("Meteora DAMM pool address is required")
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Input mint is required")
    if isinstance(amount_in, bool) or not isinstance(amount_in, int) or not 0 < amount_in <= 2**64 - 1:
        raise ValueError("Input amount must be a positive u64 integer")
    if isinstance(slippage_bps, bool) or not isinstance(slippage_bps, int) or not 0 <= slippage_bps <= MAX_SLIPPAGE_BPS:
        raise ValueError("Slippage must be an integer between 0 and 300 bps")
    if isinstance(max_snapshot_age, bool) or not isinstance(max_snapshot_age, (int, float)) or not math.isfinite(max_snapshot_age) or max_snapshot_age <= 0:
        raise ValueError("Maximum snapshot age must be positive and finite")

    discovered = chain._account(pool_address)
    if discovered.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Pool account is not owned by the Meteora DAMM program")
    first = _decode_pool(discovered.get("data"), chain)
    if input_mint == first["mint_a"]:
        input_is_a = True
        expected_output = first["mint_b"]
    elif input_mint == first["mint_b"]:
        input_is_a = False
        expected_output = first["mint_a"]
    else:
        raise ValueError("Input mint does not belong to this Meteora DAMM pool")
    if output_mint is not None and output_mint != expected_output:
        raise ValueError("Requested output mint does not match the Meteora DAMM pool")

    addresses = [
        pool_address, first["vault_a"], first["vault_b"],
        first["mint_a"], first["mint_b"],
    ]
    accounts, slot = chain._multiple_accounts_with_context(addresses)
    if len(accounts) != len(addresses) or any(item is None for item in accounts):
        raise ValueError("Meteora DAMM confirmed snapshot is missing a required account")
    pool_acc, vault_a_acc, vault_b_acc, mint_a_acc, mint_b_acc = accounts
    if pool_acc.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Meteora DAMM pool owner changed during snapshot")
    state = _decode_pool(pool_acc.get("data"), chain)
    for field in (
        "mint_a", "mint_b", "vault_a", "vault_b", "liquidity",
        "protocol_fee_a", "protocol_fee_b", "sqrt_min_price",
        "sqrt_max_price", "sqrt_price", "activation_point", "activation_type",
        "pool_status", "collect_fee_mode",
        "fee_version", "fee_numerator",
    ):
        if state[field] != first[field]:
            raise ValueError("Meteora DAMM pool state changed between discovery and confirmed snapshot")

    if mint_a_acc.get("owner") != chain.TOKEN_PROGRAM or mint_b_acc.get("owner") != chain.TOKEN_PROGRAM:
        raise ValueError("Meteora DAMM Token-2022 mints are refused until transfer fees and hooks are modelled")
    if vault_a_acc.get("owner") != chain.TOKEN_PROGRAM or vault_b_acc.get("owner") != chain.TOKEN_PROGRAM:
        raise ValueError("Meteora DAMM vaults must use the legacy SPL Token program")
    reserve_a = chain._token_amount(vault_a_acc, state["mint_a"])
    reserve_b = chain._token_amount(vault_b_acc, state["mint_b"])
    if reserve_a <= 0 or reserve_b <= 0:
        raise ValueError("Meteora DAMM vault reserves are empty")

    # OnlyB (mode 1) charges token B when it is the input; other modes charge
    # the output token. Fees are rounded upward in the token they are collected.
    fee_on_input = state["collect_fee_mode"] in (1, 2) and not input_is_a
    if fee_on_input:
        fee_amount = _ceil_div(amount_in * state["fee_numerator"], FEE_DENOMINATOR)
        if fee_amount >= amount_in:
            raise ValueError("Meteora DAMM fee consumes the entire input")
        curve_input = amount_in - fee_amount
        sqrt_after, gross_output = _curve_step(state, input_is_a, curve_input)
        output_amount = gross_output
        fee_mint = input_mint
    else:
        sqrt_after, gross_output = _curve_step(state, input_is_a, amount_in)
        fee_amount = _ceil_div(gross_output * state["fee_numerator"], FEE_DENOMINATOR)
        if fee_amount >= gross_output:
            raise ValueError("Meteora DAMM output fee consumes the entire output")
        output_amount = gross_output - fee_amount
        fee_mint = expected_output

    output_reserve = (
        reserve_b - min(reserve_b, state["protocol_fee_b"])
        if input_is_a else reserve_a - min(reserve_a, state["protocol_fee_a"])
    )
    if output_amount >= output_reserve:
        raise ValueError("Meteora DAMM quote exceeds the swappable output vault balance")

    before = state["sqrt_price"]
    before_price = (before / (1 << 64)) ** 2
    after_price = (sqrt_after / (1 << 64)) ** 2
    impact = abs(after_price - before_price) / before_price * 100.0 if before_price > 0 else float("nan")
    if not math.isfinite(impact):
        raise ValueError("Meteora DAMM price impact is invalid")
    block_time = chain._rpc("getBlockTime", [slot])
    if isinstance(block_time, bool) or not isinstance(block_time, (int, float)) or not math.isfinite(float(block_time)):
        raise ValueError("Meteora DAMM confirmed slot block time is unavailable")
    age = time.time() - float(block_time)
    if age < 0 or age > max_snapshot_age:
        raise ValueError("Meteora DAMM confirmed snapshot is stale")

    if state["activation_type"] == 0:
        current_point = chain._rpc("getSlot", [])
    else:
        current_point = int(time.time())
    if (
        isinstance(current_point, bool)
        or not isinstance(current_point, (int, float))
        or not math.isfinite(float(current_point))
        or float(current_point) < state["activation_point"]
    ):
        raise ValueError("Meteora DAMM pool has not reached its activation point")

    minimum_out = output_amount * (10_000 - slippage_bps) // 10_000
    return {
        "provider": "meteora_damm_direct",
        "protocol": PROGRAM,
        "pool_id": pool_address,
        "slot": slot,
        "snapshot_age_seconds": round(age, 3),
        "input_mint": input_mint,
        "output_mint": expected_output,
        "input_amount": amount_in,
        "output_amount_gross": gross_output,
        "out_amount": output_amount,
        "minimum_out_amount": minimum_out,
        "fee_mint": fee_mint,
        "fee_amount": fee_amount,
        "trade_fee_amount": fee_amount,
        "total_fee_amount": fee_amount,
        "fee_rate_numerator": state["fee_numerator"],
        "fee_denominator": FEE_DENOMINATOR,
        "fee_cap_numerator": state["fee_cap"],
        "fee_on_input": fee_on_input,
        "sqrt_price_after_x64": sqrt_after,
        "price_impact_percent": round(impact, 6),
        "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_POOL_SNAPSHOT",
        "execution": "DISABLED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
    }
