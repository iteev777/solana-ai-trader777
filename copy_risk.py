"""Wallet-isolated paper copy-trading risk limits."""
import math
import os


def _positive_float(name, default):
    try:
        value = float(os.getenv(name, str(default)))
        if math.isfinite(value) and value > 0:
            return value
    except (TypeError, ValueError, OverflowError):
        pass
    return float(default)


def _positive_int(name, default):
    try:
        value = int(os.getenv(name, str(default)))
        if value > 0:
            return value
    except (TypeError, ValueError, OverflowError):
        pass
    return int(default)


def wallet_exposure(positions, wallet):
    count = 0
    exposure = 0.0
    mints = set()
    if not isinstance(positions, dict) or not isinstance(wallet, str) or not wallet:
        return {"open_positions": 0, "exposure_usd": 0.0, "mints": mints}
    for mint, position in positions.items():
        if not isinstance(position, dict):
            continue
        metadata = position.get("metadata", {})
        if not isinstance(metadata, dict) or metadata.get("copy_wallet") != wallet:
            continue
        count += 1
        mints.add(mint)
        amount = position.get("invested", position.get("position_size", 0))
        try:
            amount = float(amount)
        except (TypeError, ValueError, OverflowError):
            amount = 0.0
        if math.isfinite(amount) and amount > 0:
            exposure += amount
    return {"open_positions": count, "exposure_usd": exposure, "mints": mints}


def check_copy_buy(positions, wallet, token_mint, requested_size=None):
    if not isinstance(wallet, str) or not wallet.strip():
        return {"allowed": False, "reason": "COPY_WALLET_MISSING", "trade_size": 0.0}
    if not isinstance(token_mint, str) or not token_mint.strip():
        return {"allowed": False, "reason": "TOKEN_MINT_MISSING", "trade_size": 0.0}

    state = wallet_exposure(positions, wallet.strip())
    if token_mint in state["mints"]:
        return {**state, "allowed": False, "reason": "WALLET_ALREADY_HOLDS_TOKEN", "trade_size": 0.0}
    # The ledger is keyed by mint, so never merge two wallets' ownership or
    # attach a second wallet's sell event to an existing position.
    if isinstance(positions, dict) and token_mint in positions:
        return {**state, "allowed": False, "reason": "TOKEN_ALREADY_OPEN_UNDER_OTHER_OWNER", "trade_size": 0.0}

    max_positions = _positive_int("TROJAN_COPY_MAX_POSITIONS_PER_WALLET", 3)
    max_exposure = _positive_float("TROJAN_COPY_MAX_EXPOSURE_PER_WALLET_USD", 25.0)
    max_ticket = _positive_float("TROJAN_COPY_MAX_TRADE_SIZE_USD", 5.0)
    min_ticket = _positive_float("TROJAN_MIN_TRADE_SIZE_USD", 1.0)

    if state["open_positions"] >= max_positions:
        return {**state, "allowed": False, "reason": "COPY_WALLET_POSITION_LIMIT", "trade_size": 0.0}
    remaining = max(0.0, max_exposure - state["exposure_usd"])
    try:
        requested = max_ticket if requested_size is None else float(requested_size)
    except (TypeError, ValueError, OverflowError):
        requested = 0.0
    if not math.isfinite(requested) or requested <= 0:
        return {**state, "allowed": False, "reason": "INVALID_COPY_TRADE_SIZE", "trade_size": 0.0}
    trade_size = min(requested, max_ticket, remaining)
    if trade_size < min_ticket:
        return {**state, "allowed": False, "reason": "COPY_WALLET_EXPOSURE_LIMIT", "trade_size": 0.0}
    return {
        **state, "allowed": True, "reason": "COPY_WALLET_RISK_APPROVED",
        "remaining_budget_usd": remaining, "trade_size": round(trade_size, 2)
    }


def cap_copy_trade_size(positions, wallet, token_mint, proposed_size):
    decision = check_copy_buy(positions, wallet, token_mint, proposed_size)
    if not decision.get("allowed"):
        return 0.0, decision
    return decision["trade_size"], decision
