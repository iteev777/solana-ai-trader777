"""Protocol-specific, read-only pool quote math.

This module intentionally accepts an already-decoded, fresh pool snapshot.
It does NOT guess account layouts or fetch synthetic reserves. The caller must
supply vault reserves net of protocol/fund/creator fees and the on-chain fee
configuration from the same pool snapshot.
"""
from dataclasses import dataclass
import math

CPMM_FEE_DENOMINATOR = 1_000_000
BPS_DENOMINATOR = 10_000


@dataclass(frozen=True)
class CpmmSnapshot:
    protocol: str
    pool_id: str
    mint_a: str
    mint_b: str
    reserve_a: int
    reserve_b: int
    trade_fee_rate: int
    creator_fee_rate: int = 0
    creator_fee_on_input: bool = False
    slot: int = 0
    observed_at: float = 0.0
    transfer_fee_present: bool = False
    status_enabled: bool = True


def _positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(name + " must be a positive integer")
    return value


def validate_cpmm_snapshot(snapshot, max_age_seconds=5.0, now=None):
    if not isinstance(snapshot, CpmmSnapshot):
        raise ValueError("A decoded CpmmSnapshot is required")
    if snapshot.protocol != "raydium_cpmm":
        raise ValueError("Unsupported CPMM protocol")
    if not snapshot.pool_id or not snapshot.mint_a or not snapshot.mint_b:
        raise ValueError("Pool identity and both mints are required")
    if snapshot.mint_a == snapshot.mint_b:
        raise ValueError("Pool mints must differ")
    _positive_int(snapshot.reserve_a, "reserve_a")
    _positive_int(snapshot.reserve_b, "reserve_b")
    if snapshot.trade_fee_rate < 0 or snapshot.creator_fee_rate < 0:
        raise ValueError("Fee rates cannot be negative")
    if snapshot.trade_fee_rate + snapshot.creator_fee_rate >= CPMM_FEE_DENOMINATOR:
        raise ValueError("Combined fee rate is invalid")
    if snapshot.trade_fee_rate > CPMM_FEE_DENOMINATOR:
        raise ValueError("Trade fee rate is invalid")
    if snapshot.creator_fee_rate and not snapshot.creator_fee_on_input:
        raise ValueError("Creator fee mode is not supported by this quote adapter")
    if snapshot.transfer_fee_present:
        raise ValueError("Token-2022 transfer-fee mints require a dedicated fee decoder")
    if not snapshot.status_enabled:
        raise ValueError("Pool swaps are disabled")
    if snapshot.slot <= 0 or snapshot.observed_at <= 0:
        raise ValueError("Pool snapshot must include a confirmed slot and timestamp")
    current = now if now is not None else __import__("time").time()
    age = current - snapshot.observed_at
    if age < 0 or age > max_age_seconds:
        raise ValueError("Pool snapshot is stale or has a future timestamp")
    return age


def quote_raydium_cpmm_exact_in(snapshot, input_mint, amount_in,
                                slippage_bps=100, max_age_seconds=5.0,
                                now=None):
    """Quote an exact-input swap using Raydium CPMM's integer fee/curve math.

    Reserves must already exclude accrued protocol/fund/creator fee balances.
    Token-2022 transfer-fee mints and unsupported creator-fee modes fail closed.
    """
    age = validate_cpmm_snapshot(snapshot, max_age_seconds=max_age_seconds, now=now)
    _positive_int(amount_in, "amount_in")
    if isinstance(slippage_bps, bool) or not isinstance(slippage_bps, int):
        raise ValueError("slippage_bps must be an integer")
    if not 0 <= slippage_bps <= 300:
        raise ValueError("slippage_bps is outside the safe quote range")

    if input_mint == snapshot.mint_a:
        reserve_in, reserve_out = snapshot.reserve_a, snapshot.reserve_b
        output_mint = snapshot.mint_b
    elif input_mint == snapshot.mint_b:
        reserve_in, reserve_out = snapshot.reserve_b, snapshot.reserve_a
        output_mint = snapshot.mint_a
    else:
        raise ValueError("Input mint does not belong to this pool")

    fee_rate = snapshot.trade_fee_rate + snapshot.creator_fee_rate
    fee_amount = (amount_in * fee_rate + CPMM_FEE_DENOMINATOR - 1) // CPMM_FEE_DENOMINATOR
    net_input = amount_in - fee_amount
    if net_input <= 0:
        raise ValueError("Input is fully consumed by fees")

    amount_out = (reserve_out * net_input) // (reserve_in + net_input)
    if amount_out <= 0 or amount_out >= reserve_out:
        raise ValueError("Quote rounds to zero or drains the pool")
    minimum_out = amount_out * (BPS_DENOMINATOR - slippage_bps) // BPS_DENOMINATOR

    # Compare marginal spot with the post-trade marginal pool price.
    # The fee-inclusive amount enters the vault; fee balances are accounted separately.
    post_reserve_in = reserve_in + amount_in
    post_reserve_out = reserve_out - amount_out
    impact = 1.0 - ((post_reserve_out / post_reserve_in) / (reserve_out / reserve_in))
    if not math.isfinite(impact) or impact < 0:
        raise ValueError("Calculated price impact is invalid")

    return {
        "provider": "raydium_cpmm_direct_math",
        "protocol": "raydium_cpmm",
        "pool_id": snapshot.pool_id,
        "slot": snapshot.slot,
        "snapshot_age_seconds": round(age, 3),
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount_in,
        "fee_amount": fee_amount,
        "fee_rate": fee_rate,
        "out_amount": amount_out,
        "minimum_out_amount": minimum_out,
        "price_impact_percent": round(impact * 100, 6),
        "status": "QUOTED_FROM_VALIDATED_SNAPSHOT",
        "execution": "DISABLED",
        "signing": "DISABLED",
        "broadcast": "DISABLED",
    }
