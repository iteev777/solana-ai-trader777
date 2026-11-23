"""Read-only Meteora Dynamic Bonding Curve exact-input quote adapter.

Only flat-fee, non-migrated VirtualPools with fully decoded piecewise liquidity
curves are supported. Scheduler/dynamic fees, transfer-hook pools and
Token-2022 mints fail closed rather than using guessed semantics.
"""
import math
import time

PROGRAM = "meteora_dbc"
FEE_DENOMINATOR = 1_000_000_000
MAX_SLIPPAGE_BPS = 300
MAX_SNAPSHOT_AGE_SECONDS = 5.0
MAX_CURVE_POINTS = 20
TRANSFER_HOOK_POOL_DISCRIMINATOR = bytes([237, 219, 184, 23, 42, 189, 169, 35])


def _ceil_div(n, d):
    if n < 0 or d <= 0:
        raise ValueError("invalid DBC ceil division operands")
    return (n + d - 1) // d


def _decode_virtual_pool(raw, chain):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 306:
        raise ValueError("Meteora DBC VirtualPool is truncated")
    if raw[:8] == TRANSFER_HOOK_POOL_DISCRIMINATOR:
        raise ValueError("Meteora DBC transfer-hook pools are not supported")
    if raw[:8] != chain._discriminator("VirtualPool"):
        raise ValueError("Meteora DBC VirtualPool discriminator is invalid")
    state = {
        "config": chain._key(raw, 72),
        "base_mint": chain._key(raw, 136),
        "base_vault": chain._key(raw, 168),
        "quote_vault": chain._key(raw, 200),
        "base_reserve": chain._u64(raw, 232),
        "quote_reserve": chain._u64(raw, 240),
        "protocol_base_fee": chain._u64(raw, 248),
        "protocol_quote_fee": chain._u64(raw, 256),
        "partner_base_fee": chain._u64(raw, 264),
        "partner_quote_fee": chain._u64(raw, 272),
        "sqrt_price": chain._u128(raw, 280),
        "activation_point": chain._u64(raw, 296),
        "pool_type": raw[304],
        "is_migrated": raw[305] != 0,
    }
    if state["is_migrated"]:
        raise ValueError("Meteora DBC curve has migrated; use its post-migration AMM")
    if state["sqrt_price"] <= 0:
        raise ValueError("Meteora DBC current square-root price is invalid")
    return state


def _decode_pool_config(raw, chain):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 1048:
        raise ValueError("Meteora DBC PoolConfig is truncated")
    if raw[:8] != chain._discriminator("PoolConfig"):
        raise ValueError("Meteora DBC PoolConfig discriminator is invalid")
    dynamic_flag = raw[136]
    if dynamic_flag not in (0, 1):
        raise ValueError("Meteora DBC dynamic-fee flag is invalid")
    second_factor = chain._u64(raw, 112)
    third_factor = chain._u64(raw, 120)
    first_factor = chain._u16(raw, 128)
    if second_factor or third_factor or first_factor:
        raise ValueError("Meteora DBC base-fee scheduler is active and not modelled")
    if dynamic_flag:
        raise ValueError("Meteora DBC volatility-based dynamic fees are not supported")
    cliff_fee = chain._u64(raw, 104)
    if not 0 <= cliff_fee < FEE_DENOMINATOR:
        raise ValueError("Meteora DBC flat fee numerator is invalid")
    collect_fee_mode = raw[232]
    if collect_fee_mode not in (0, 1):
        raise ValueError("Meteora DBC fee collection mode is unsupported")
    sqrt_start_price = chain._u128(raw, 392)
    if sqrt_start_price <= 0:
        raise ValueError("Meteora DBC curve start price is invalid")
    points = []
    previous = sqrt_start_price
    for index in range(MAX_CURVE_POINTS):
        offset = 408 + index * 32
        sqrt_price = chain._u128(raw, offset)
        liquidity = chain._u128(raw, offset + 16)
        if sqrt_price == 0:
            break
        if sqrt_price <= previous:
            raise ValueError("Meteora DBC curve price checkpoints are not strictly ascending")
        if liquidity <= 0:
            raise ValueError("Meteora DBC curve segment has zero liquidity")
        points.append((sqrt_price, liquidity))
        previous = sqrt_price
    if not points:
        raise ValueError("Meteora DBC PoolConfig has no curve segments")
    return {
        "quote_mint": chain._key(raw, 8),
        "cliff_fee_numerator": cliff_fee,
        "collect_fee_mode": collect_fee_mode,
        "sqrt_start_price": sqrt_start_price,
        "points": points,
    }


def _input_for_move(zero_for_one, liquidity, sqrt_from, sqrt_to):
    if zero_for_one:
        if sqrt_from <= sqrt_to:
            return 0
        spread = sqrt_from - sqrt_to
        first = _ceil_div(liquidity * spread, sqrt_from)
        return _ceil_div(first, sqrt_to)
    if sqrt_to <= sqrt_from:
        return 0
    return _ceil_div(liquidity * (sqrt_to - sqrt_from), 1 << 128)


def _output_for_move(zero_for_one, liquidity, sqrt_from, sqrt_to):
    if zero_for_one:
        if sqrt_to > sqrt_from:
            raise ValueError("Meteora DBC sell price direction is invalid")
        return (liquidity * (sqrt_from - sqrt_to)) >> 128
    if sqrt_to < sqrt_from:
        raise ValueError("Meteora DBC buy price direction is invalid")
    intermediate = (liquidity * (sqrt_to - sqrt_from)) // sqrt_to
    return intermediate // max(sqrt_from, 1)


def _next_sqrt_price(zero_for_one, liquidity, sqrt_price, amount_in):
    if zero_for_one:
        denominator = liquidity + amount_in * sqrt_price
        if denominator <= 0:
            raise ValueError("Meteora DBC partial-step denominator is invalid")
        return _ceil_div(liquidity * sqrt_price, denominator)
    return sqrt_price + (amount_in * (1 << 128)) // liquidity


def _walk_curve(state, config, zero_for_one, amount_in):
    boundaries = [config["sqrt_start_price"]] + [point[0] for point in config["points"]]
    liquidities = [point[1] for point in config["points"]]
    if not boundaries[0] <= state["sqrt_price"] <= boundaries[-1]:
        raise ValueError("Meteora DBC current price is outside configured curve segments")
    index = 0
    for i in range(len(liquidities)):
        if state["sqrt_price"] <= boundaries[i + 1]:
            index = i
            break
    current_sqrt = state["sqrt_price"]
    remaining = amount_in
    total_out = 0
    while remaining > 0:
        if index >= len(liquidities):
            raise ValueError("Meteora DBC quote exceeds loaded curve segments")
        liquidity = liquidities[index]
        if liquidity <= 0:
            raise ValueError("Meteora DBC active curve segment has zero liquidity")
        boundary = boundaries[index] if zero_for_one else boundaries[index + 1]
        needed = _input_for_move(zero_for_one, liquidity, current_sqrt, boundary)
        if remaining < needed:
            next_sqrt = _next_sqrt_price(zero_for_one, liquidity, current_sqrt, remaining)
            if (zero_for_one and next_sqrt < boundary) or (
                not zero_for_one and next_sqrt > boundary
            ):
                raise ValueError("Meteora DBC partial step exceeded segment boundary")
            total_out += _output_for_move(zero_for_one, liquidity, current_sqrt, next_sqrt)
            current_sqrt = next_sqrt
            remaining = 0
        else:
            total_out += _output_for_move(zero_for_one, liquidity, current_sqrt, boundary)
            remaining -= needed
            current_sqrt = boundary
            if remaining == 0:
                break
            if zero_for_one:
                if index == 0:
                    raise ValueError("Meteora DBC sell would move below configured starting price")
                index -= 1
            else:
                index += 1
    if total_out <= 0:
        raise ValueError("Meteora DBC quote rounds to zero output")
    if total_out > 2**64 - 1:
        raise ValueError("Meteora DBC output exceeds u64")
    return total_out, current_sqrt


def quote_meteora_dbc_pool(pool_address, input_mint, amount_in, slippage_bps=100,
                           output_mint=None, max_snapshot_age=MAX_SNAPSHOT_AGE_SECONDS):
    import solana_onchain as chain

    if not isinstance(pool_address, str) or not pool_address.strip():
        raise ValueError("Meteora DBC pool address is required")
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
        raise ValueError("Pool account is not owned by the Meteora DBC program")
    first = _decode_virtual_pool(discovered.get("data"), chain)
    config_discovery = chain._account(first["config"])
    if config_discovery.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Meteora DBC PoolConfig owner is invalid")
    config_first = _decode_pool_config(config_discovery.get("data"), chain)

    if (
        first["base_mint"] == config_first["quote_mint"]
        or first["base_vault"] == first["quote_vault"]
        or first["base_mint"] == first["base_vault"]
        or config_first["quote_mint"] == first["quote_vault"]
    ):
        raise ValueError("Meteora DBC mint/vault relationships are invalid")

    if input_mint == first["base_mint"]:
        input_is_base = True
        expected_output = config_first["quote_mint"]
    elif input_mint == config_first["quote_mint"]:
        input_is_base = False
        expected_output = first["base_mint"]
    else:
        raise ValueError("Input mint does not belong to this Meteora DBC pool")
    if output_mint is not None and output_mint != expected_output:
        raise ValueError("Requested output mint does not match the Meteora DBC pool")

    addresses = [
        pool_address, first["config"], first["base_mint"], config_first["quote_mint"],
        first["base_vault"], first["quote_vault"],
    ]
    snapshot_started = time.time()
    accounts, slot = chain._multiple_accounts_with_context(addresses)
    if len(accounts) != len(addresses) or any(item is None for item in accounts):
        raise ValueError("Meteora DBC confirmed snapshot is missing a required account")
    pool_acc, config_acc, base_mint_acc, quote_mint_acc, base_vault_acc, quote_vault_acc = accounts
    if pool_acc.get("owner") != chain.PROGRAMS[PROGRAM] or config_acc.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Meteora DBC pool/config owner changed during snapshot")
    state = _decode_virtual_pool(pool_acc.get("data"), chain)
    config = _decode_pool_config(config_acc.get("data"), chain)
    for field in ("config", "base_mint", "base_vault", "quote_vault", "sqrt_price", "is_migrated"):
        if state[field] != first[field]:
            raise ValueError("Meteora DBC VirtualPool changed between discovery and confirmed snapshot")
    for field in ("quote_mint", "cliff_fee_numerator", "collect_fee_mode",
                  "sqrt_start_price", "points"):
        if config[field] != config_first[field]:
            raise ValueError("Meteora DBC PoolConfig changed between discovery and confirmed snapshot")

    if base_mint_acc.get("owner") != chain.TOKEN_PROGRAM or quote_mint_acc.get("owner") != chain.TOKEN_PROGRAM:
        raise ValueError("Meteora DBC Token-2022 mints are refused until transfer fees and hooks are modelled")
    if base_vault_acc.get("owner") != chain.TOKEN_PROGRAM or quote_vault_acc.get("owner") != chain.TOKEN_PROGRAM:
        raise ValueError("Meteora DBC vaults must use the legacy SPL Token program")
    base_balance = chain._token_amount(base_vault_acc, state["base_mint"])
    quote_balance = chain._token_amount(quote_vault_acc, config["quote_mint"])
    if base_balance <= 0 or quote_balance <= 0:
        raise ValueError("Meteora DBC vault reserves are empty")

    zero_for_one = input_is_base
    fee_on_input = config["collect_fee_mode"] == 0 and not input_is_base
    if fee_on_input:
        fee_amount = _ceil_div(amount_in * config["cliff_fee_numerator"], FEE_DENOMINATOR)
        if fee_amount >= amount_in:
            raise ValueError("Meteora DBC fee consumes the entire input")
        curve_input = amount_in - fee_amount
        gross_output, sqrt_after = _walk_curve(state, config, zero_for_one, curve_input)
        output_amount = gross_output
        fee_mint = input_mint
    else:
        gross_output, sqrt_after = _walk_curve(state, config, zero_for_one, amount_in)
        fee_amount = _ceil_div(gross_output * config["cliff_fee_numerator"], FEE_DENOMINATOR)
        if fee_amount >= gross_output:
            raise ValueError("Meteora DBC output fee consumes the entire output")
        output_amount = gross_output - fee_amount
        fee_mint = expected_output

    output_reserve = quote_balance if input_is_base else base_balance
    if gross_output >= output_reserve:
        raise ValueError("Meteora DBC quote exceeds the output vault balance")
    if output_amount <= 0:
        raise ValueError("Meteora DBC quote returns zero output")

    before = state["sqrt_price"] / (1 << 64)
    after = sqrt_after / (1 << 64)
    before_price = before * before
    after_price = after * after
    impact = abs(after_price - before_price) / before_price * 100.0 if before_price > 0 else float("nan")
    if not math.isfinite(impact):
        raise ValueError("Meteora DBC price impact is invalid")
    age = time.time() - snapshot_started
    if age < 0 or age > max_snapshot_age:
        raise ValueError("Meteora DBC confirmed snapshot is stale")

    return {
        "provider": "meteora_dbc_direct",
        "protocol": PROGRAM,
        "pool_id": pool_address,
        "slot": slot,
        "snapshot_age_seconds": round(age, 3),
        "input_mint": input_mint,
        "output_mint": expected_output,
        "input_amount": amount_in,
        "output_amount_gross": gross_output,
        "out_amount": output_amount,
        "minimum_out_amount": output_amount * (10_000 - slippage_bps) // 10_000,
        "fee_mint": fee_mint,
        "fee_amount": fee_amount,
        "trade_fee_amount": fee_amount,
        "total_fee_amount": fee_amount,
        "fee_rate_numerator": config["cliff_fee_numerator"],
        "fee_denominator": FEE_DENOMINATOR,
        "fee_on_input": fee_on_input,
        "sqrt_price_after_x64": sqrt_after,
        "price_impact_percent": round(impact, 6),
        "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_CURVE_SNAPSHOT",
        "execution": "DISABLED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
    }
