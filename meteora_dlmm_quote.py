"""Read-only exact-input quotes for Meteora DLMM bin pools.

This adapter walks at most three consecutive bin arrays in the requested
direction. It models market-making bin liquidity and dynamic fees, but not
limit-order fills or Token-2022 transfer hooks; unsupported account modes fail
closed rather than assuming extra output.
"""
import math
import time

PROGRAM = "meteora_dlmm"
BINS_PER_ARRAY = 70
BIN_ARRAY_OFFSET = 56
BIN_SIZE = 144
BIN_ARRAY_LENGTH = BIN_ARRAY_OFFSET + BINS_PER_ARRAY * BIN_SIZE
FEE_PRECISION = 1_000_000_000
MAX_FEE_RATE = 100_000_000
BASIS_POINT_MAX = 10_000
MAX_SLIPPAGE_BPS = 300
MAX_SNAPSHOT_AGE_SECONDS = 5.0
CLOCK_SYSVAR = "SysvarC1ock11111111111111111111111111111111"


def _u32(raw, offset):
    return int.from_bytes(raw[offset:offset + 4], "little", signed=False)


def _i64(raw, offset):
    return int.from_bytes(raw[offset:offset + 8], "little", signed=True)


def _ceil_div(n, d):
    if n < 0 or d <= 0:
        raise ValueError("invalid DLMM ceil division operands")
    return (n + d - 1) // d


def _decode_pair(raw, chain):
    discriminator = bytes([33, 11, 49, 98, 181, 101, 177, 13])
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 904 or raw[:8] != discriminator:
        raise ValueError("Meteora DLMM LbPair discriminator or layout is invalid")
    state = {
        "base_factor": chain._u16(raw, 8),
        "filter_period": chain._u16(raw, 10),
        "decay_period": chain._u16(raw, 12),
        "reduction_factor": chain._u16(raw, 14),
        "variable_fee_control": _u32(raw, 16),
        "max_volatility_accumulator": _u32(raw, 20),
        "min_bin_id": chain._i32(raw, 24),
        "max_bin_id": chain._i32(raw, 28),
        "protocol_share": chain._u16(raw, 32),
        "base_fee_power_factor": raw[34],
        "function_type": raw[35],
        "collect_fee_mode": raw[36],
        "volatility_accumulator": _u32(raw, 40),
        "volatility_reference": _u32(raw, 44),
        "index_reference": chain._i32(raw, 48),
        "last_update_timestamp": _i64(raw, 56),
        "active_id": chain._i32(raw, 76),
        "bin_step": chain._u16(raw, 80),
        "status": raw[82],
        "mint_x": chain._key(raw, 88),
        "mint_y": chain._key(raw, 120),
        "reserve_x": chain._key(raw, 152),
        "reserve_y": chain._key(raw, 184),
        "protocol_fee_x": chain._u64(raw, 216),
        "protocol_fee_y": chain._u64(raw, 224),
        "oracle": chain._key(raw, 552),
    }
    if state["status"] != 0:
        raise ValueError("Meteora DLMM pair is not enabled")
    if state["function_type"] != 0:
        raise ValueError("Meteora DLMM fee function type is unsupported")
    if state["collect_fee_mode"] not in (0, 1):
        raise ValueError("Meteora DLMM fee collection mode is unsupported")
    if state["bin_step"] <= 0:
        raise ValueError("Meteora DLMM bin step is zero")
    if state["mint_x"] == state["mint_y"]:
        raise ValueError("Meteora DLMM pair mints must differ")
    if state["min_bin_id"] > state["max_bin_id"] or not state["min_bin_id"] <= state["active_id"] <= state["max_bin_id"]:
        raise ValueError("Meteora DLMM active bin is outside configured bin range")
    if state["protocol_share"] > BASIS_POINT_MAX:
        raise ValueError("Meteora DLMM protocol fee share is invalid")
    if state["base_fee_power_factor"] > 9:
        raise ValueError("Meteora DLMM base fee power factor is unsupported")
    if state["max_volatility_accumulator"] <= 0:
        raise ValueError("Meteora DLMM maximum volatility accumulator is invalid")
    if state["last_update_timestamp"] < 0:
        raise ValueError("Meteora DLMM fee timestamp is invalid")
    return state


def _update_references(state, now):
    elapsed = now - state["last_update_timestamp"]
    if elapsed < 0:
        raise ValueError("Meteora DLMM clock precedes pair fee timestamp")
    if elapsed >= state["filter_period"]:
        state["index_reference"] = state["active_id"]
        if elapsed < state["decay_period"]:
            state["volatility_reference"] = (
                state["volatility_accumulator"] * state["reduction_factor"]
                // BASIS_POINT_MAX
            )
        else:
            state["volatility_reference"] = 0
    state["volatility_accumulator"] = min(
        state["volatility_accumulator"], state["max_volatility_accumulator"]
    )


def _fee_rate(state, active_bin_id):
    delta = abs(state["index_reference"] - active_bin_id)
    volatility = min(
        state["volatility_reference"] + delta * BASIS_POINT_MAX,
        state["max_volatility_accumulator"],
    )
    base = (
        state["base_factor"] * state["bin_step"] * 10
        * (10 ** state["base_fee_power_factor"])
    )
    scaled = volatility * state["bin_step"]
    variable = _ceil_div(
        state["variable_fee_control"] * scaled * scaled,
        100_000_000_000,
    ) if state["variable_fee_control"] else 0
    return min(base + variable, MAX_FEE_RATE), volatility


def _array_indices(active_id, zero_for_one, min_bin_id, max_bin_id):
    active_index = active_id // BINS_PER_ARRAY
    direction = -1 if zero_for_one else 1
    indices = []
    for offset in range(3):
        index = active_index + direction * offset
        first_bin = index * BINS_PER_ARRAY
        last_bin = first_bin + BINS_PER_ARRAY - 1
        if last_bin < min_bin_id or first_bin > max_bin_id:
            continue
        indices.append(index)
    return indices


def _decode_bin_array(account, pool_address, expected_index, state, chain):
    if not isinstance(account, dict) or account.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Meteora DLMM bin-array owner is invalid")
    raw = account.get("data")
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != BIN_ARRAY_LENGTH:
        raise ValueError("Meteora DLMM BinArray has an invalid length")
    if raw[:8] != chain._discriminator("BinArray"):
        raise ValueError("Meteora DLMM BinArray discriminator is invalid")
    array_index = _i64(raw, 8)
    if array_index != expected_index:
        raise ValueError("Meteora DLMM BinArray index does not match its PDA")
    if chain._key(raw, 24) != pool_address:
        raise ValueError("Meteora DLMM BinArray belongs to a different LbPair")
    bins = []
    for offset_index in range(BINS_PER_ARRAY):
        offset = BIN_ARRAY_OFFSET + offset_index * BIN_SIZE
        bin_id = array_index * BINS_PER_ARRAY + offset_index
        if not state["min_bin_id"] <= bin_id <= state["max_bin_id"]:
            continue
        amount_x = chain._u64(raw, offset)
        amount_y = chain._u64(raw, offset + 8)
        price = chain._u128(raw, offset + 16)
        if (amount_x or amount_y) and price <= 0:
            raise ValueError("Meteora DLMM non-empty bin has an invalid price")
        bins.append((bin_id, amount_x, amount_y, price))
    return bins


def _input_fee_gross_for_net(net_needed, fee_rate):
    if fee_rate <= 0:
        return net_needed
    gross = _ceil_div(net_needed * FEE_PRECISION, FEE_PRECISION - fee_rate)
    # The programme rounds the fee upward. Adjust by raw units until the
    # resulting net amount really covers the bin's required input.
    while gross - _ceil_div(gross * fee_rate, FEE_PRECISION) < net_needed:
        gross += 1
    return gross


def _quote_bins(state, bins, input_is_x, amount_in):
    zero_for_one = input_is_x
    ordered = sorted(bins, key=lambda item: item[0], reverse=zero_for_one)
    ordered = [
        item for item in ordered
        if (item[0] <= state["active_id"] if zero_for_one else item[0] >= state["active_id"])
    ]
    if not ordered or ordered[0][0] != state["active_id"]:
        raise ValueError("Meteora DLMM active bin is not present in loaded bin arrays")

    fee_on_input = state["collect_fee_mode"] == 0 or input_is_x is False
    remaining = amount_in
    total_out = 0
    total_fee = 0
    bins_visited = 0
    bins_crossed = 0
    max_rate = 0
    active_id = state["active_id"]
    last_price = None

    for bin_id, amount_x, amount_y, price in ordered:
        if remaining <= 0:
            break
        if last_price is None and price > 0:
            last_price = price
        rate, volatility = _fee_rate(state, active_id)
        state["volatility_accumulator"] = volatility
        max_rate = max(max_rate, rate)
        bins_visited += 1
        if amount_x == 0 and amount_y == 0:
            active_id = bin_id - 1 if zero_for_one else bin_id + 1
            continue
        if price <= 0:
            raise ValueError("Meteora DLMM bin price is unavailable")

        available_output = amount_y if input_is_x else amount_x
        if available_output <= 0:
            active_id = bin_id - 1 if zero_for_one else bin_id + 1
            continue
        needed_input = (
            _ceil_div(available_output * (1 << 64), price)
            if input_is_x else
            _ceil_div(available_output * price, 1 << 64)
        )

        if fee_on_input:
            net_available = remaining - _ceil_div(remaining * rate, FEE_PRECISION)
            if net_available >= needed_input:
                gross_used = _input_fee_gross_for_net(needed_input, rate)
                if gross_used > remaining:
                    raise ValueError("Meteora DLMM input fee rounding exceeds remaining amount")
                fee = _ceil_div(gross_used * rate, FEE_PRECISION)
                remaining -= gross_used
                total_fee += fee
                total_out += available_output
                bins_crossed += 1
                active_id = bin_id - 1 if zero_for_one else bin_id + 1
            else:
                fee = _ceil_div(remaining * rate, FEE_PRECISION)
                net_input = remaining - fee
                partial = (
                    net_input * price // (1 << 64)
                    if input_is_x else
                    net_input * (1 << 64) // price
                )
                total_out += min(available_output, partial)
                total_fee += fee
                remaining = 0
                active_id = bin_id
        else:
            if remaining >= needed_input:
                gross_output = available_output
                fee = _ceil_div(gross_output * rate, FEE_PRECISION)
                total_out += gross_output - fee
                total_fee += fee
                remaining -= needed_input
                bins_crossed += 1
                active_id = bin_id - 1 if zero_for_one else bin_id + 1
            else:
                gross_output = (
                    remaining * price // (1 << 64)
                    if input_is_x else
                    remaining * (1 << 64) // price
                )
                gross_output = min(available_output, gross_output)
                fee = _ceil_div(gross_output * rate, FEE_PRECISION)
                total_out += gross_output - fee
                total_fee += fee
                remaining = 0
                active_id = bin_id

    if remaining > 0:
        raise ValueError("Meteora DLMM quote exceeds loaded consecutive bin-array coverage")
    if total_out <= 0:
        raise ValueError("Meteora DLMM quote rounds to zero output")
    return {
        "out_amount": total_out,
        "fee_amount": total_fee,
        "fee_on_input": fee_on_input,
        "bins_visited": bins_visited,
        "bins_crossed": bins_crossed,
        "max_fee_rate": max_rate,
        "active_id_after": active_id,
        "active_bin_price": last_price,
    }


def quote_meteora_dlmm_pool(pool_address, input_mint, amount_in, slippage_bps=100,
                            output_mint=None, max_snapshot_age=MAX_SNAPSHOT_AGE_SECONDS):
    import solana_onchain as chain

    if not isinstance(pool_address, str) or not pool_address.strip():
        raise ValueError("Meteora DLMM pool address is required")
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
        raise ValueError("Pool account is not owned by the Meteora DLMM program")
    first = _decode_pair(discovered.get("data"), chain)
    if input_mint == first["mint_x"]:
        input_is_x = True
        expected_output = first["mint_y"]
        output_reserve_key = first["reserve_y"]
    elif input_mint == first["mint_y"]:
        input_is_x = False
        expected_output = first["mint_x"]
        output_reserve_key = first["reserve_x"]
    else:
        raise ValueError("Input mint does not belong to this Meteora DLMM pool")
    if output_mint is not None and output_mint != expected_output:
        raise ValueError("Requested output mint does not match the Meteora DLMM pool")

    zero_for_one = input_is_x
    array_indices = _array_indices(
        first["active_id"], zero_for_one, first["min_bin_id"], first["max_bin_id"]
    )
    if not array_indices or array_indices[0] != first["active_id"] // BINS_PER_ARRAY:
        raise ValueError("Meteora DLMM active bin array is outside configured range")
    pool_bytes = chain._b58decode(pool_address)
    array_addresses = [
        chain._find_program_address(
            [b"bin_array", pool_bytes, index.to_bytes(8, "little", signed=True)],
            chain.PROGRAMS[PROGRAM],
        )[0]
        for index in array_indices
    ]
    bitmap_address = chain._find_program_address(
        [b"bitmap", pool_bytes], chain.PROGRAMS[PROGRAM]
    )[0]
    addresses = [
        pool_address, first["reserve_x"], first["reserve_y"],
        first["mint_x"], first["mint_y"], CLOCK_SYSVAR, bitmap_address,
    ] + array_addresses
    snapshot_started = time.time()
    accounts, slot = chain._multiple_accounts_with_context(addresses)
    if len(accounts) != len(addresses):
        raise ValueError("Meteora DLMM confirmed snapshot has an incomplete account list")
    pool_acc, reserve_x_acc, reserve_y_acc, mint_x_acc, mint_y_acc, clock_acc, bitmap_acc = accounts[:7]
    if pool_acc is None or pool_acc.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Meteora DLMM pool account is unavailable or has invalid owner")
    state = _decode_pair(pool_acc.get("data"), chain)
    account_keys = (pool_address, state["mint_x"], state["mint_y"], state["reserve_x"], state["reserve_y"])
    if len(set(account_keys)) != len(account_keys):
        raise ValueError("Meteora DLMM pool, mints and reserves must have distinct addresses")
    if isinstance(slot, bool) or not isinstance(slot, int) or slot <= 0:
        raise ValueError("Meteora DLMM confirmed snapshot slot is invalid")
    for field in (
        "base_factor", "filter_period", "decay_period", "reduction_factor",
        "variable_fee_control", "max_volatility_accumulator", "min_bin_id",
        "max_bin_id", "protocol_share", "base_fee_power_factor", "function_type",
        "collect_fee_mode", "volatility_accumulator", "volatility_reference",
        "index_reference", "last_update_timestamp", "active_id", "bin_step",
        "status", "mint_x", "mint_y", "reserve_x", "reserve_y",
        "protocol_fee_x", "protocol_fee_y", "oracle",
    ):
        if state[field] != first[field]:
            raise ValueError("Meteora DLMM LbPair changed between discovery and confirmed snapshot")

    if any(item is None for item in (reserve_x_acc, reserve_y_acc, mint_x_acc, mint_y_acc, clock_acc)):
        raise ValueError("Meteora DLMM confirmed snapshot is missing a mint, vault or Clock sysvar")
    if bitmap_acc is not None and bitmap_acc.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Meteora DLMM bitmap-extension owner is invalid")
    if mint_x_acc.get("owner") != chain.TOKEN_PROGRAM or mint_y_acc.get("owner") != chain.TOKEN_PROGRAM:
        raise ValueError("Meteora DLMM Token-2022 mints are refused until transfer hooks and fees are modelled")
    if reserve_x_acc.get("owner") != chain.TOKEN_PROGRAM or reserve_y_acc.get("owner") != chain.TOKEN_PROGRAM:
        raise ValueError("Meteora DLMM reserves must use the legacy SPL Token program")
    reserve_x = chain._token_amount(reserve_x_acc, state["mint_x"])
    reserve_y = chain._token_amount(reserve_y_acc, state["mint_y"])
    if reserve_x <= 0 or reserve_y <= 0:
        raise ValueError("Meteora DLMM reserve vaults are empty")

    clock_data = clock_acc.get("data", b"")
    if clock_acc.get("owner") != "Sysvar1111111111111111111111111111111111":
        raise ValueError("Meteora DLMM Clock sysvar owner is invalid")
    if not isinstance(clock_data, (bytes, bytearray)) or len(clock_data) < 40:
        raise ValueError("Meteora DLMM Clock sysvar is truncated")
    now = _i64(clock_data, 32)
    _update_references(state, now)

    bins = []
    loaded_indices = []
    for offset, array_index in enumerate(array_indices):
        account = accounts[7 + offset]
        if account is None:
            break
        decoded = _decode_bin_array(account, pool_address, array_index, state, chain)
        bins.extend(decoded)
        loaded_indices.append(array_index)
    if not loaded_indices or loaded_indices[0] != array_indices[0]:
        raise ValueError("Meteora DLMM active bin array is not initialized")

    quote = _quote_bins(state, bins, input_is_x, amount_in)
    protocol_fee = state["protocol_fee_y"] if input_is_x else state["protocol_fee_x"]
    output_reserve = reserve_y if input_is_x else reserve_x
    spendable_reserve = max(0, output_reserve - protocol_fee)
    if quote["out_amount"] >= spendable_reserve:
        raise ValueError("Meteora DLMM quote exceeds spendable output reserve")

    spot_price = quote["active_bin_price"]
    if spot_price is None or spot_price <= 0:
        raise ValueError("Meteora DLMM active bin price is unavailable")
    spot_ratio = spot_price / (1 << 64) if input_is_x else (1 << 64) / spot_price
    average_ratio = quote["out_amount"] / amount_in
    impact = max(0.0, (1.0 - average_ratio / spot_ratio) * 100.0)
    if not math.isfinite(impact):
        raise ValueError("Meteora DLMM price impact is invalid")
    age = time.time() - snapshot_started
    if age < 0 or age > max_snapshot_age:
        raise ValueError("Meteora DLMM confirmed snapshot is stale")

    fee_mint = input_mint if quote["fee_on_input"] else expected_output
    return {
        "provider": "meteora_dlmm_direct",
        "protocol": PROGRAM,
        "pool_id": pool_address,
        "slot": slot,
        "snapshot_age_seconds": round(age, 3),
        "input_mint": input_mint,
        "output_mint": expected_output,
        "input_amount": amount_in,
        "out_amount": quote["out_amount"],
        "minimum_out_amount": quote["out_amount"] * (10_000 - slippage_bps) // 10_000,
        "fee_mint": fee_mint,
        "fee_amount": quote["fee_amount"],
        "trade_fee_amount": quote["fee_amount"],
        "total_fee_amount": quote["fee_amount"],
        "max_fee_rate": quote["max_fee_rate"],
        "fee_denominator": FEE_PRECISION,
        "fee_on_input": quote["fee_on_input"],
        "bins_visited": quote["bins_visited"],
        "bins_crossed": quote["bins_crossed"],
        "active_id_after": quote["active_id_after"],
        "loaded_bin_arrays": len(loaded_indices),
        "volatility_accumulator_end": state["volatility_accumulator"],
        "price_impact_percent": round(impact, 6),
        "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_BIN_SNAPSHOT",
        "execution": "DISABLED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
    }
