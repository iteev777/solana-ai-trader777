"""Conservative, configurable paper fill price model.

This model is an estimate, not an executable DEX quote. It worsens buy and sell
marks by a bounded slippage allowance and can incorporate the entry route's
reported price impact. Database fee accounting remains separate.
"""
import math
import os

DEFAULT_SLIPPAGE_BPS = 25
MAX_SLIPPAGE_BPS = 300


def configured_slippage_bps():
    raw = os.getenv("TROJAN_PAPER_SLIPPAGE_BPS", str(DEFAULT_SLIPPAGE_BPS))
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_SLIPPAGE_BPS
    if not math.isfinite(value):
        return DEFAULT_SLIPPAGE_BPS
    return int(max(0, min(round(value), MAX_SLIPPAGE_BPS)))


def _valid_price(value):
    if isinstance(value, bool):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def entry_slippage_bps(metadata=None):
    """Use the larger of configured slippage and approved route price impact."""
    base = configured_slippage_bps()
    metadata = metadata if isinstance(metadata, dict) else {}
    intelligence = metadata.get("entry_intelligence")
    quote = intelligence.get("quote") if isinstance(intelligence, dict) else None
    impact = quote.get("price_impact_percent") if isinstance(quote, dict) else None
    if isinstance(impact, bool):
        return base
    try:
        impact = float(impact)
    except (TypeError, ValueError, OverflowError):
        impact = None
    if impact is None or not math.isfinite(impact) or impact < 0:
        return base
    impact_bps = int(math.ceil(impact * 100.0))
    return max(base, min(impact_bps, MAX_SLIPPAGE_BPS))


def buy_fill_price(market_price, metadata=None):
    price = _valid_price(market_price)
    if price is None:
        raise ValueError("Paper buy mark must be finite and positive")
    bps = entry_slippage_bps(metadata)
    fill = price * (1.0 + bps / 10_000.0)
    if not math.isfinite(fill) or fill <= 0:
        raise ValueError("Paper buy fill price is invalid")
    return fill, bps


def sell_fill_price(market_price, metadata=None):
    price = _valid_price(market_price)
    if price is None:
        raise ValueError("Paper sell mark must be finite and positive")
    metadata = metadata if isinstance(metadata, dict) else {}
    stored = metadata.get("paper_exit_slippage_bps")
    if stored is None:
        bps = configured_slippage_bps()
    else:
        try:
            if isinstance(stored, bool):
                raise ValueError
            value = float(stored)
            if not math.isfinite(value):
                raise ValueError
            bps = int(max(0, min(round(value), MAX_SLIPPAGE_BPS)))
        except (TypeError, ValueError, OverflowError):
            bps = configured_slippage_bps()
    fill = price * (1.0 - bps / 10_000.0)
    if not math.isfinite(fill) or fill <= 0:
        raise ValueError("Paper sell fill price is invalid")
    return fill, bps
