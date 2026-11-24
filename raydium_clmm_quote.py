"""Read-only Raydium CLMM exact-input quote adapter.

The adapter reads the pool, config, vaults, mints and initialized tick arrays
from one confirmed RPC context. It supports the audited Token-2022 transfer-fee
extension subset with conservative input/output fee treatment. Unmodelled fee
modes, unsupported extensions and trades beyond loaded tick-array coverage
remain fail-closed.
"""
import math
import time

import clmm_quote_math as mathx

PROGRAM = "raydium_clmm"
TICK_ARRAY_SIZE = 60
POOL_BITMAP_BITS = 1024
POOL_BITMAP_HALF = POOL_BITMAP_BITS // 2
TICK_STATE_OFFSET = 44
TICK_STATE_SIZE = 168
MIN_SLIPPAGE_BPS = 0
MAX_SLIPPAGE_BPS = 300
MAX_SNAPSHOT_AGE_SECONDS = 5.0


def _decode_state(raw, chain):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 1176:
        raise ValueError("Raydium CLMM PoolState is truncated")
    if raw[:8] != chain._discriminator("PoolState"):
        raise ValueError("Raydium CLMM PoolState discriminator is invalid")
    state = {
        "amm_config": chain._key(raw, 9),
        "mint_0": chain._key(raw, 73),
        "mint_1": chain._key(raw, 105),
        "decimals_0": raw[233],
        "decimals_1": raw[234],
        "vault_0": chain._key(raw, 137),
        "vault_1": chain._key(raw, 169),
        "tick_spacing": chain._u16(raw, 235),
        "liquidity": chain._u128(raw, 237),
        "sqrt_price_x64": chain._u128(raw, 253),
        "tick_current": chain._i32(raw, 269),
        "status": raw[389],
        "fee_on": raw[390],
        "dynamic_fee": any(raw[1096:1176]),
        "bitmap": [
            int.from_bytes(raw[904 + i * 8:912 + i * 8], "little")
            for i in range(16)
        ],
    }
    if not state["tick_spacing"]:
        raise ValueError("Raydium CLMM tick spacing is zero")
    if state["mint_0"] == state["mint_1"]:
        raise ValueError("Raydium CLMM pool mints must differ")
    if state["status"] & (1 << 2):
        raise ValueError("Raydium CLMM pool has swaps disabled")
    if state["fee_on"] != 0:
        raise ValueError("Raydium CLMM fee-on-token-side modes are not supported")
    if state["dynamic_fee"]:
        raise ValueError("Raydium CLMM dynamic volatility fees are not supported")
    if state["liquidity"] <= 0:
        raise ValueError("Raydium CLMM has no active liquidity")
    if not mathx.MIN_TICK <= state["tick_current"] <= mathx.MAX_TICK:
        raise ValueError("Raydium CLMM current tick is outside supported range")
    lower = mathx.sqrt_price_at_tick(state["tick_current"])
    upper = mathx.sqrt_price_at_tick(state["tick_current"] + 1)
    if not lower <= state["sqrt_price_x64"] < upper:
        raise ValueError("Raydium CLMM tick and square-root price are inconsistent")
    return state


BITMAP_EXTENSION_SEED = b"pool_tick_array_bitmap_extension"
BITMAP_EXTENSION_BLOCKS = 14
BITMAP_EXTENSION_BLOCK_BITS = 512
BITMAP_EXTENSION_BASE_OFFSET = 40


def _decode_bitmap_extension(account, pool_address, chain):
    """Decode the optional 14-block positive/negative tick-array bitmap extension."""
    if account is None:
        return [], []
    if not isinstance(account, dict) or account.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Raydium CLMM tick-array bitmap extension owner is invalid")
    raw = account.get("data", b"")
    expected_length = BITMAP_EXTENSION_BASE_OFFSET + 2 * BITMAP_EXTENSION_BLOCKS * 64
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < expected_length:
        raise ValueError("Raydium CLMM tick-array bitmap extension is truncated")
    if raw[:8] != chain._discriminator("TickArrayBitmapExtension"):
        raise ValueError("Raydium CLMM tick-array bitmap extension discriminator is invalid")
    if chain._key(raw, 8) != pool_address:
        raise ValueError("Raydium CLMM tick-array bitmap extension belongs to a different pool")
    positive, negative = [], []
    offset = BITMAP_EXTENSION_BASE_OFFSET
    for target in (positive, negative):
        for _ in range(BITMAP_EXTENSION_BLOCKS):
            target.append([
                int.from_bytes(raw[offset + word * 8:offset + word * 8 + 8], "little")
                for word in range(8)
            ])
            offset += 64
    return positive, negative


def _array_is_initialized(state, array_index, extension):
    positive, negative = extension
    if -POOL_BITMAP_HALF <= array_index < POOL_BITMAP_HALF:
        bit = array_index + POOL_BITMAP_HALF
        return bool(state["bitmap"][bit // 64] & (1 << (bit % 64)))
    if array_index >= POOL_BITMAP_HALF:
        distance = array_index - POOL_BITMAP_HALF
        blocks = positive
    else:
        distance = -array_index - POOL_BITMAP_HALF - 1
        blocks = negative
    block_index = distance // BITMAP_EXTENSION_BLOCK_BITS
    if block_index < 0 or block_index >= len(blocks):
        return False
    bit = distance % BITMAP_EXTENSION_BLOCK_BITS
    return bool(blocks[block_index][bit // 64] & (1 << (bit % 64)))


def _array_starts(state, pool_address, zero_for_one, chain, extension=((), ())):
    spacing = state["tick_spacing"]
    span = TICK_ARRAY_SIZE * spacing
    current_start = (state["tick_current"] // span) * span
    current_index = current_start // span
    direction = -1 if zero_for_one else 1
    min_index = -POOL_BITMAP_HALF - BITMAP_EXTENSION_BLOCKS * BITMAP_EXTENSION_BLOCK_BITS
    max_index = POOL_BITMAP_HALF - 1 + BITMAP_EXTENSION_BLOCKS * BITMAP_EXTENSION_BLOCK_BITS
    starts = []
    index = current_index
    while len(starts) < 3 and min_index <= index <= max_index:
        if _array_is_initialized(state, index, extension):
            start = index * span
            seed = start.to_bytes(4, "big", signed=True)
            address, _ = chain._find_program_address(
                [b"tick_array", chain._b58decode(pool_address), seed],
                chain.PROGRAMS[PROGRAM],
            )
            starts.append((start, address))
        index += direction
    if not starts or starts[0][0] != current_start:
        raise ValueError("Raydium CLMM current tick array is not initialized in the pool bitmap")
    return starts


def _decode_tick_array(account, pool_address, expected_start, spacing, chain):
    if not isinstance(account, dict) or account.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Raydium CLMM tick array owner is invalid")
    raw = account.get("data")
    required_length = TICK_STATE_OFFSET + TICK_ARRAY_SIZE * TICK_STATE_SIZE
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < required_length:
        raise ValueError("Raydium CLMM tick array is truncated")
    if chain._key(raw, 8) != pool_address:
        raise ValueError("Raydium CLMM tick array belongs to a different pool")
    start = chain._i32(raw, 40)
    if start != expected_start:
        raise ValueError("Raydium CLMM tick array start index does not match its PDA")
    ticks = []
    for index in range(TICK_ARRAY_SIZE):
        offset = TICK_STATE_OFFSET + index * TICK_STATE_SIZE
        tick_index = chain._i32(raw, offset)
        liquidity_net = int.from_bytes(raw[offset + 4:offset + 20], "little", signed=True)
        liquidity_gross = chain._u128(raw, offset + 20)
        if not liquidity_gross:
            continue
        expected_tick = start + index * spacing
        if tick_index != expected_tick:
            raise ValueError("Raydium CLMM tick array contains an invalid tick index")
        ticks.append((tick_index, liquidity_net))
    return ticks


def quote_raydium_clmm_pool(pool_address, input_mint, amount_in, slippage_bps=100,
                            output_mint=None, max_snapshot_age=MAX_SNAPSHOT_AGE_SECONDS):
    import solana_onchain as chain

    if not isinstance(pool_address, str) or not pool_address.strip():
        raise ValueError("Raydium CLMM pool address is required")
    if not isinstance(input_mint, str) or not input_mint.strip():
        raise ValueError("Input mint is required")
    if isinstance(amount_in, bool) or not isinstance(amount_in, int) or not 0 < amount_in <= 2**64 - 1:
        raise ValueError("Input amount must be a positive u64 integer")
    if isinstance(slippage_bps, bool) or not isinstance(slippage_bps, int) or not MIN_SLIPPAGE_BPS <= slippage_bps <= MAX_SLIPPAGE_BPS:
        raise ValueError("Slippage must be an integer between 0 and 300 bps")
    if isinstance(max_snapshot_age, bool) or not isinstance(max_snapshot_age, (int, float)) or not math.isfinite(max_snapshot_age) or max_snapshot_age <= 0:
        raise ValueError("Maximum snapshot age must be positive and finite")

    discovered = chain._account(pool_address)
    if discovered.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Pool account is not owned by the Raydium CLMM program")
    first = _decode_state(discovered.get("data"), chain)
    if input_mint == first["mint_0"]:
        zero_for_one = True
        expected_output = first["mint_1"]
        input_vault, output_vault = first["vault_0"], first["vault_1"]
        input_mint_account, output_mint_account = first["mint_0"], first["mint_1"]
    elif input_mint == first["mint_1"]:
        zero_for_one = False
        expected_output = first["mint_0"]
        input_vault, output_vault = first["vault_1"], first["vault_0"]
        input_mint_account, output_mint_account = first["mint_1"], first["mint_0"]
    else:
        raise ValueError("Input mint does not belong to this Raydium CLMM pool")
    if output_mint is not None and output_mint != expected_output:
        raise ValueError("Requested output mint does not match the Raydium CLMM pool")

    bitmap_extension_address, _ = chain._find_program_address(
        [BITMAP_EXTENSION_SEED, chain._b58decode(pool_address)],
        chain.PROGRAMS[PROGRAM],
    )
    bitmap_extension_account = chain._account(bitmap_extension_address)
    initial_extension = _decode_bitmap_extension(bitmap_extension_account, pool_address, chain)
    arrays = _array_starts(first, pool_address, zero_for_one, chain, initial_extension)
    addresses = [
        pool_address, first["amm_config"], first["vault_0"], first["vault_1"],
        first["mint_0"], first["mint_1"], bitmap_extension_address,
    ] + [address for _, address in arrays]
    accounts, slot = chain._multiple_accounts_with_context(addresses)
    if isinstance(slot, bool) or not isinstance(slot, int) or slot <= 0:
        raise ValueError("Raydium CLMM snapshot must include a positive confirmed slot")
    if (len(accounts) != len(addresses)
            or any(item is None for item in accounts[:6])
            or any(item is None for item in accounts[7:])):
        raise ValueError("Raydium CLMM confirmed snapshot is missing a required account")
    pool_acc, config_acc, vault0_acc, vault1_acc, mint0_acc, mint1_acc, bitmap_acc = accounts[:7]
    snapshot_extension = _decode_bitmap_extension(bitmap_acc, pool_address, chain)
    if snapshot_extension != initial_extension:
        raise ValueError("Raydium CLMM bitmap extension changed during confirmed snapshot")
    if pool_acc.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Raydium CLMM pool owner changed during snapshot")
    state = _decode_state(pool_acc.get("data"), chain)
    for field in ("amm_config", "mint_0", "mint_1", "vault_0", "vault_1",
                  "tick_spacing", "liquidity", "sqrt_price_x64", "tick_current",
                  "status", "fee_on", "dynamic_fee", "bitmap"):
        if state[field] != first[field]:
            raise ValueError("Raydium CLMM state changed between discovery and confirmed snapshot")

    if config_acc.get("owner") != chain.PROGRAMS[PROGRAM]:
        raise ValueError("Raydium CLMM AmmConfig owner is invalid")
    config_raw = config_acc.get("data", b"")
    if len(config_raw) < 57:
        raise ValueError("Raydium CLMM AmmConfig is truncated")
    fee_rate = int.from_bytes(config_raw[47:51], "little")
    config_spacing = int.from_bytes(config_raw[51:53], "little")
    if config_spacing != state["tick_spacing"]:
        raise ValueError("Raydium CLMM AmmConfig tick spacing does not match pool state")
    if fee_rate >= mathx.FEE_DENOMINATOR:
        raise ValueError("Raydium CLMM trade fee rate is invalid")

    import token2022_inspector as token2022
    token_program_0 = mint0_acc.get("owner")
    token_program_1 = mint1_acc.get("owner")
    supported_programs = (chain.TOKEN_PROGRAM, chain.TOKEN_2022_PROGRAM)
    if token_program_0 not in supported_programs or token_program_1 not in supported_programs:
        raise ValueError("Raydium CLMM mint uses an unsupported token program")
    if vault0_acc.get("owner") != token_program_0 or vault1_acc.get("owner") != token_program_1:
        raise ValueError("Raydium CLMM vault token program does not match its mint")
    if (chain._mint_decimals(mint0_acc) != state["decimals_0"]
            or chain._mint_decimals(mint1_acc) != state["decimals_1"]):
        raise ValueError("Raydium CLMM pool decimals do not match the mint accounts")
    mint0_token2022 = (
        token2022.validate_swap_compatible_mint_data(mint0_acc.get("data", b""))
        if token_program_0 == chain.TOKEN_2022_PROGRAM else None
    )
    mint1_token2022 = (
        token2022.validate_swap_compatible_mint_data(mint1_acc.get("data", b""))
        if token_program_1 == chain.TOKEN_2022_PROGRAM else None
    )
    reserve0 = token2022.validate_token_account_for_program(
        vault0_acc, token_program_0, state["mint_0"], pool_address,
        "Raydium CLMM vault 0",
    )
    reserve1 = token2022.validate_token_account_for_program(
        vault1_acc, token_program_1, state["mint_1"], pool_address,
        "Raydium CLMM vault 1",
    )
    if reserve0 <= 0 or reserve1 <= 0:
        raise ValueError("Raydium CLMM vault reserves are empty")

    decoded_ticks = []
    for offset, (start, _) in enumerate(arrays):
        decoded_ticks.extend(_decode_tick_array(
            accounts[7 + offset], pool_address, start, state["tick_spacing"], chain
        ))
    decoded_ticks = sorted(set(decoded_ticks), key=lambda item: item[0])
    span = TICK_ARRAY_SIZE * state["tick_spacing"]
    coverage_tick = arrays[-1][0] if zero_for_one else arrays[-1][0] + span
    input_token2022 = mint0_token2022 if zero_for_one else mint1_token2022
    output_token2022 = mint1_token2022 if zero_for_one else mint0_token2022
    received_by_pool = token2022.conservative_net_amount(
        (input_token2022 or {}).get("transfer_fee_config"), amount_in
    )
    if received_by_pool <= 0:
        raise ValueError("Token-2022 input transfer fee consumes the entire input amount")
    quote = mathx.quote_exact_input(
        state["sqrt_price_x64"], state["liquidity"], state["tick_current"],
        decoded_ticks, zero_for_one, received_by_pool, fee_rate, coverage_tick,
    )
    output_reserve = reserve1 if zero_for_one else reserve0
    if quote["out_amount"] >= output_reserve:
        raise ValueError("Raydium CLMM quote exceeds the output vault reserve")
    gross_output = quote["out_amount"]
    expected_output_amount = token2022.conservative_net_amount(
        (output_token2022 or {}).get("transfer_fee_config"), gross_output
    )
    if expected_output_amount <= 0:
        raise ValueError("Token-2022 output transfer fee consumes the entire output amount")

    spot_ratio = (
        (state["sqrt_price_x64"] / mathx.Q64) ** 2
        if zero_for_one else (mathx.Q64 / state["sqrt_price_x64"]) ** 2
    )
    average_ratio = expected_output_amount / amount_in
    impact = max(0.0, (1.0 - average_ratio / spot_ratio) * 100.0)
    if not math.isfinite(impact):
        raise ValueError("Raydium CLMM price impact is invalid")
    block_time = chain._rpc("getBlockTime", [slot])
    if isinstance(block_time, bool) or not isinstance(block_time, (int, float)) or not math.isfinite(float(block_time)):
        raise ValueError("Raydium CLMM confirmed slot block time is unavailable")
    age = time.time() - float(block_time)
    if not math.isfinite(age) or age < 0 or age > max_snapshot_age:
        raise ValueError("Raydium CLMM confirmed snapshot is stale")

    minimum_out = expected_output_amount * (10_000 - slippage_bps) // 10_000
    if minimum_out <= 0:
        raise ValueError("Raydium CLMM minimum output rounds to zero")
    return {
        "provider": "raydium_clmm_direct",
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
        "token_program_input": token_program_0 if zero_for_one else token_program_1,
        "token_program_output": token_program_1 if zero_for_one else token_program_0,
        "minimum_out_amount": minimum_out,
        "fee_mint": input_mint,
        "fee_amount": quote["fee_amount"],
        "trade_fee_amount": quote["fee_amount"],
        "total_fee_amount": quote["fee_amount"],
        "fee_rate": fee_rate,
        "price_impact_percent": round(impact, 6),
        "tick_crossings": quote["tick_crossings"],
        "sqrt_price_after_x64": quote["sqrt_price_after_x64"],
        "liquidity_after": quote["liquidity_after"],
        "loaded_tick_arrays": len(arrays),
        "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_TICK_SNAPSHOT",
        "execution": "DISABLED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
    }
