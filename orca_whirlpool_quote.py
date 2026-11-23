"""Read-only exact-input quotes for classic Orca Whirlpool pools.

The adapter uses static TickArray accounts and the pool's base fee. It supports
the audited Token-2022 transfer-fee extension subset with conservative input
and output fee treatment. Adaptive-fee oracles, DynamicTickArray layouts and
unsupported Token-2022 extensions remain fail-closed.
"""
import math
import time

import clmm_quote_math as mathx

PROGRAM = "orca_whirlpool"
TICK_ARRAY_SIZE = 88
TICK_ARRAY_OFFSET = 12
TICK_STATE_SIZE = 113
FIXED_ARRAY_LENGTH = TICK_ARRAY_OFFSET + TICK_ARRAY_SIZE * TICK_STATE_SIZE + 32
MAX_SNAPSHOT_AGE_SECONDS = 5.0
MAX_SLIPPAGE_BPS = 300


def _decode_whirlpool(raw, chain):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 653:
        raise ValueError("Orca Whirlpool account is truncated")
    if raw[:8] != chain._discriminator("Whirlpool"):
        raise ValueError("Orca Whirlpool discriminator is invalid")
    state = {
        "tick_spacing": chain._u16(raw, 41),
        "fee_tier_index": chain._u16(raw, 43),
        "fee_rate": chain._u16(raw, 45),
        "protocol_fee_rate": chain._u16(raw, 47),
        "protocol_fee_owed_a": chain._u64(raw, 85),
        "protocol_fee_owed_b": chain._u64(raw, 93),
        "liquidity": chain._u128(raw, 49),
        "sqrt_price_x64": chain._u128(raw, 65),
        "tick_current": chain._i32(raw, 81),
        "mint_a": chain._key(raw, 101),
        "vault_a": chain._key(raw, 133),
        "mint_b": chain._key(raw, 181),
        "vault_b": chain._key(raw, 213),
    }
    if state["tick_spacing"] <= 0:
        raise ValueError("Orca Whirlpool tick spacing is zero")
    if state["mint_a"] == state["mint_b"]:
        raise ValueError("Orca Whirlpool pool mints must differ")
    if state["liquidity"] <= 0:
        raise ValueError("Orca Whirlpool has no active liquidity")
    if state["fee_tier_index"] != state["tick_spacing"]:
        raise ValueError("Orca adaptive-fee tier is not supported")
    if state["fee_rate"] > 60_000:
        raise ValueError("Orca Whirlpool fee rate exceeds the static fee cap")
    if state["protocol_fee_rate"] > 2_500:
        raise ValueError("Orca Whirlpool protocol fee rate exceeds the protocol cap")
    if not mathx.MIN_TICK <= state["tick_current"] < mathx.MAX_TICK:
        raise ValueError("Orca Whirlpool current tick is outside supported range")
    lower = mathx.sqrt_price_at_tick(state["tick_current"])
    upper = mathx.sqrt_price_at_tick(state["tick_current"] + 1)
    if not lower <= state["sqrt_price_x64"] < upper:
        raise ValueError("Orca Whirlpool tick and square-root price are inconsistent")
    return state


def _tick_array_starts(tick_current, spacing, zero_for_one):
    span = TICK_ARRAY_SIZE * spacing
    base = (tick_current // span) * span
    if zero_for_one:
        offsets = (0, -1, -2)
    elif tick_current + spacing >= base + span:
        offsets = (1, 2, 3)
    else:
        offsets = (0, 1, 2)
    lowest = (mathx.MIN_TICK // span) * span
    return [
        base + offset * span for offset in offsets
        if lowest <= base + offset * span <= mathx.MAX_TICK
    ]


def _derive_address(seeds, chain):
    return chain._find_program_address(
        seeds, chain.PROGRAMS[PROGRAM]
    )[0]


def _decode_fixed_tick_array(account, pool_address, start, spacing, chain):
    if not isinstance(account, dict) or account.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Orca Whirlpool tick array owner is invalid")
    raw = account.get("data")
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("Orca Whirlpool tick array data is missing")
    if len(raw) != FIXED_ARRAY_LENGTH:
        raise ValueError("Orca DynamicTickArray or unknown tick-array layout is not supported")
    if raw[:8] != chain._discriminator("TickArray"):
        raise ValueError("Orca Whirlpool TickArray discriminator is invalid")
    if chain._i32(raw, 8) != start:
        raise ValueError("Orca Whirlpool tick-array start index does not match its PDA")
    if chain._key(raw, len(raw) - 32) != pool_address:
        raise ValueError("Orca Whirlpool tick array belongs to a different pool")
    ticks = []
    for index in range(TICK_ARRAY_SIZE):
        offset = TICK_ARRAY_OFFSET + index * TICK_STATE_SIZE
        initialized = raw[offset]
        if initialized not in (0, 1):
            raise ValueError("Orca Whirlpool tick initialized flag is invalid")
        if not initialized:
            continue
        liquidity_net = int.from_bytes(raw[offset + 1:offset + 17], "little", signed=True)
        liquidity_gross = int.from_bytes(raw[offset + 17:offset + 33], "little")
        if liquidity_gross <= 0 or liquidity_gross < abs(liquidity_net):
            raise ValueError("Orca Whirlpool initialized tick liquidity is invalid")
        ticks.append((start + index * spacing, liquidity_net))
    return ticks


def quote_orca_whirlpool_pool(pool_address, input_mint, amount_in, slippage_bps=100,
                              output_mint=None, max_snapshot_age=MAX_SNAPSHOT_AGE_SECONDS):
    import solana_onchain as chain

    if not isinstance(pool_address, str) or not pool_address.strip():
        raise ValueError("Orca Whirlpool pool address is required")
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
        raise ValueError("Pool account is not owned by the Orca Whirlpool program")
    first = _decode_whirlpool(discovered.get("data"), chain)
    if input_mint == first["mint_a"]:
        zero_for_one = True
        expected_output = first["mint_b"]
        vault_a, vault_b = first["vault_a"], first["vault_b"]
    elif input_mint == first["mint_b"]:
        zero_for_one = False
        expected_output = first["mint_a"]
        vault_a, vault_b = first["vault_a"], first["vault_b"]
    else:
        raise ValueError("Input mint does not belong to this Orca Whirlpool pool")
    if output_mint is not None and output_mint != expected_output:
        raise ValueError("Requested output mint does not match the Orca Whirlpool pool")

    starts = _tick_array_starts(
        first["tick_current"], first["tick_spacing"], zero_for_one
    )
    pool_bytes = chain._b58decode(pool_address)
    tick_addresses = [
        _derive_address([b"tick_array", pool_bytes, str(start).encode("ascii")], chain)
        for start in starts
    ]
    oracle_address = _derive_address([b"oracle", pool_bytes], chain)
    addresses = [
        pool_address, vault_a, vault_b, first["mint_a"], first["mint_b"],
        oracle_address,
    ] + tick_addresses
    accounts, slot = chain._multiple_accounts_with_context(addresses)
    if len(accounts) != len(addresses):
        raise ValueError("Orca Whirlpool confirmed snapshot has an incomplete account list")
    pool_acc = accounts[0]
    if pool_acc is None or pool_acc.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Orca Whirlpool pool account is unavailable or has an invalid owner")
    state = _decode_whirlpool(pool_acc.get("data"), chain)
    for field in ("tick_spacing", "fee_tier_index", "fee_rate", "protocol_fee_rate",
                  "protocol_fee_owed_a", "protocol_fee_owed_b", "liquidity",
                  "sqrt_price_x64", "tick_current", "mint_a", "mint_b",
                  "vault_a", "vault_b"):
        if state[field] != first[field]:
            raise ValueError("Orca Whirlpool state changed between discovery and confirmed snapshot")

    vault_a_acc, vault_b_acc, mint_a_acc, mint_b_acc = accounts[1:5]
    if any(item is None for item in (vault_a_acc, vault_b_acc, mint_a_acc, mint_b_acc)):
        raise ValueError("Orca Whirlpool confirmed snapshot is missing a required mint or vault")
    import token2022_inspector as token2022
    token_program_a = mint_a_acc.get("owner")
    token_program_b = mint_b_acc.get("owner")
    supported_programs = (chain.TOKEN_PROGRAM, chain.TOKEN_2022_PROGRAM)
    if token_program_a not in supported_programs or token_program_b not in supported_programs:
        raise ValueError("Orca Whirlpool mint uses an unsupported token program")
    if vault_a_acc.get("owner") != token_program_a or vault_b_acc.get("owner") != token_program_b:
        raise ValueError("Orca Whirlpool vault token program does not match its mint")
    mint_a_token2022 = (
        token2022.validate_swap_compatible_mint_data(mint_a_acc.get("data", b""))
        if token_program_a == chain.TOKEN_2022_PROGRAM else None
    )
    mint_b_token2022 = (
        token2022.validate_swap_compatible_mint_data(mint_b_acc.get("data", b""))
        if token_program_b == chain.TOKEN_2022_PROGRAM else None
    )
    vault_balance_a = token2022.validate_token_account_for_program(
        vault_a_acc, token_program_a, state["mint_a"], pool_address,
        "Orca Whirlpool vault A",
    )
    vault_balance_b = token2022.validate_token_account_for_program(
        vault_b_acc, token_program_b, state["mint_b"], pool_address,
        "Orca Whirlpool vault B",
    )
    # Protocol fees owed are held in the vaults but are not spendable swap
    # liquidity. Subtract them before applying the output-reserve guard.
    reserve_a = vault_balance_a - state["protocol_fee_owed_a"]
    reserve_b = vault_balance_b - state["protocol_fee_owed_b"]
    if reserve_a <= 0 or reserve_b <= 0:
        raise ValueError("Orca Whirlpool spendable vault reserves are empty")

    oracle_acc = accounts[5]
    if oracle_acc is not None:
        if oracle_acc.get("owner") != chain.PROGRAMS[PROGRAM]:
            raise ValueError("Orca Whirlpool oracle owner is invalid")
        oracle_data = oracle_acc.get("data", b"")
        if (not isinstance(oracle_data, (bytes, bytearray))
                or len(oracle_data) < 58
                or oracle_data[:8] != chain._discriminator("Oracle")):
            raise ValueError("Orca Whirlpool oracle discriminator/layout is invalid; adaptive fee state cannot be verified")
        adaptive_fee_control = int.from_bytes(oracle_data[54:58], "little")
        if adaptive_fee_control != 0:
            raise ValueError("Orca Whirlpool adaptive-fee pools are not supported")

    loaded_starts = []
    decoded_ticks = []
    # Orca accepts the derived PDA for an uncreated array as an empty array.
    # Preserve the full three-array sequence; only existing accounts contribute
    # initialized ticks, while the program-defined sequence edge bounds the quote.
    for offset, start in enumerate(starts):
        account = accounts[6 + offset]
        if account is None:
            continue
        decoded_ticks.extend(_decode_fixed_tick_array(
            account, pool_address, start, state["tick_spacing"], chain
        ))
        loaded_starts.append(start)
    span = TICK_ARRAY_SIZE * state["tick_spacing"]
    last_start = starts[-1]
    if zero_for_one:
        coverage_tick = max(mathx.MIN_TICK, last_start)
    else:
        coverage_tick = min(mathx.MAX_TICK, last_start + span - 1)

    input_token2022 = mint_a_token2022 if zero_for_one else mint_b_token2022
    output_token2022 = mint_b_token2022 if zero_for_one else mint_a_token2022
    received_by_pool = token2022.conservative_net_amount(
        (input_token2022 or {}).get("transfer_fee_config"), amount_in
    )
    if received_by_pool <= 0:
        raise ValueError("Token-2022 input transfer fee consumes the entire input amount")
    quote = mathx.quote_exact_input(
        state["sqrt_price_x64"], state["liquidity"], state["tick_current"],
        sorted(set(decoded_ticks)), zero_for_one, received_by_pool, state["fee_rate"],
        coverage_tick,
    )
    output_reserve = reserve_b if zero_for_one else reserve_a
    if quote["out_amount"] > output_reserve:
        raise ValueError("Orca Whirlpool quote exceeds the output vault reserve")
    gross_output = quote["out_amount"]
    expected_output_amount = token2022.conservative_net_amount(
        (output_token2022 or {}).get("transfer_fee_config"), gross_output
    )
    if expected_output_amount <= 0:
        raise ValueError("Token-2022 output transfer fee consumes the entire output amount")

    sqrt_after_x64 = quote.get("sqrt_price_after_x64")
    if (
        isinstance(sqrt_after_x64, bool)
        or not isinstance(sqrt_after_x64, int)
        or not 0 < sqrt_after_x64 <= 2**128 - 1
    ):
        raise ValueError("Orca Whirlpool post-swap square-root price is invalid")
    sqrt_before = state["sqrt_price_x64"] / mathx.Q64
    sqrt_after = sqrt_after_x64 / mathx.Q64
    price_before = sqrt_before * sqrt_before
    price_after = sqrt_after * sqrt_after
    if not math.isfinite(price_before) or not math.isfinite(price_after) or price_before <= 0:
        raise ValueError("Orca Whirlpool price impact inputs are invalid")
    # Match ScreenerBot: impact is the movement in the squared spot price,
    # not an average execution-rate metric that also folds fees into impact.
    impact = abs(price_before - price_after) / price_before * 100.0
    if not math.isfinite(impact):
        raise ValueError("Orca Whirlpool price impact is invalid")
    impact = min(100.0, max(0.0, impact))
    block_time = chain._rpc("getBlockTime", [slot])
    if (isinstance(block_time, bool)
            or not isinstance(block_time, (int, float))
            or not math.isfinite(float(block_time))):
        raise ValueError("Orca Whirlpool confirmed slot block time is unavailable")
    age = time.time() - float(block_time)
    if age < 0 or age > max_snapshot_age:
        raise ValueError("Orca Whirlpool confirmed snapshot is stale")

    minimum_out = expected_output_amount * (10_000 - slippage_bps) // 10_000
    return {
        "provider": "orca_whirlpool_direct",
        "protocol": PROGRAM,
        "pool_id": pool_address,
        "slot": slot,
        "snapshot_age_seconds": round(age, 3),
        "input_mint": input_mint,
        "output_mint": expected_output,
        "input_amount": amount_in,
        "slippage_bps": slippage_bps,
        "out_amount": expected_output_amount,
        "gross_out_amount": gross_output,
        "token2022_input_transfer_fee": received_by_pool < amount_in,
        "token2022_input_received_by_pool": received_by_pool,
        "token2022_output_transfer_fee": expected_output_amount < gross_output,
        "token2022_output_gross_amount": gross_output,
        "token_program_input": token_program_a if zero_for_one else token_program_b,
        "token_program_output": token_program_b if zero_for_one else token_program_a,
        "minimum_out_amount": minimum_out,
        "fee_mint": input_mint,
        "fee_amount": quote["fee_amount"],
        "trade_fee_amount": quote["fee_amount"],
        "total_fee_amount": quote["fee_amount"],
        "fee_rate": state["fee_rate"],
        "price_impact_percent": round(impact, 6),
        "tick_crossings": quote["tick_crossings"],
        "sqrt_price_after_x64": quote["sqrt_price_after_x64"],
        "liquidity_after": quote["liquidity_after"],
        "loaded_tick_arrays": len(loaded_starts),
        "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_TICK_SNAPSHOT",
        "execution": "DISABLED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
    }
