"""Conservative subject-wallet transaction classification for copy research.

All token movements are derived from the watched wallet's own balance rows.
A BUY/SELL label additionally requires a recognised swap program and an
opposing quote-asset flow; ambiguous transfers remain UNKNOWN.
"""
from decimal import Decimal, InvalidOperation

SOL_MINT = "So11111111111111111111111111111111111111112"
QUOTE_MINTS = {
    SOL_MINT,
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}
JUPITER_V6 = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"


def _account_keys(message):
    result = []
    for item in message.get("accountKeys", []) or []:
        value = item.get("pubkey") if isinstance(item, dict) else item
        result.append(value if isinstance(value, str) else "")
    return result


def _program_ids(tx, keys):
    message = tx.get("transaction", {}).get("message", {})
    meta = tx.get("meta", {})
    ids = set()
    instructions = list(message.get("instructions", []) or [])
    for group in meta.get("innerInstructions", []) or []:
        instructions.extend(group.get("instructions", []) or [])
    for instruction in instructions:
        if not isinstance(instruction, dict):
            continue
        program = instruction.get("programId")
        if not program:
            index = instruction.get("programIdIndex")
            if isinstance(index, int) and 0 <= index < len(keys):
                program = keys[index]
        if isinstance(program, str):
            ids.add(program)
    return ids


def _swap_program_allowlist():
    from solana_onchain import PROGRAMS
    allowed = set(PROGRAMS.values())
    allowed.add(JUPITER_V6)
    import os
    allowed.update(
        item.strip()
        for item in os.getenv("TROJAN_COPY_SWAP_PROGRAM_IDS", "").split(",")
        if item.strip()
    )
    return allowed


def _raw_token_totals(rows, wallet):
    totals = {}
    decimals_by_mint = {}
    for item in rows or []:
        if not isinstance(item, dict) or item.get("owner") != wallet:
            continue
        mint = item.get("mint")
        amount = (item.get("uiTokenAmount") or {}).get("amount")
        decimals = (item.get("uiTokenAmount") or {}).get("decimals")
        if not isinstance(mint, str) or not mint or amount is None:
            continue
        try:
            raw = int(str(amount))
            decimals = int(decimals)
        except (TypeError, ValueError, OverflowError):
            continue
        if raw < 0 or not 0 <= decimals <= 18:
            continue
        if mint in decimals_by_mint and decimals_by_mint[mint] != decimals:
            continue
        decimals_by_mint[mint] = decimals
        totals[mint] = totals.get(mint, 0) + raw
    return totals, decimals_by_mint


def parse_wallet_transaction(tx, wallet, signature=None, received_at=None):
    """Return a conservative wallet event, or None for incomplete/failed tx data."""
    import time
    if not isinstance(tx, dict) or not isinstance(wallet, str) or not wallet.strip():
        return None
    meta = tx.get("meta")
    if not isinstance(meta, dict) or meta.get("err") is not None:
        return None
    message = tx.get("transaction", {}).get("message", {})
    if not isinstance(message, dict):
        return None
    keys = _account_keys(message)
    if wallet not in keys:
        return None
    wallet_index = keys.index(wallet)
    before, after = meta.get("preBalances", []), meta.get("postBalances", [])
    if wallet_index >= len(before) or wallet_index >= len(after):
        return None

    try:
        sol_delta_lamports = int(after[wallet_index]) - int(before[wallet_index])
        fee_lamports = int(meta.get("fee", 0) or 0) if wallet_index == 0 else 0
    except (TypeError, ValueError, OverflowError):
        return None
    sol_delta = sol_delta_lamports / 1_000_000_000
    economic_sol_delta = (sol_delta_lamports + fee_lamports) / 1_000_000_000

    pre, pre_decimals = _raw_token_totals(meta.get("preTokenBalances"), wallet)
    post, post_decimals = _raw_token_totals(meta.get("postTokenBalances"), wallet)
    deltas = {}
    for mint in set(pre) | set(post):
        decimals = post_decimals.get(mint, pre_decimals.get(mint))
        if decimals is None or (mint in pre_decimals and mint in post_decimals and pre_decimals[mint] != post_decimals[mint]):
            continue
        raw_delta = post.get(mint, 0) - pre.get(mint, 0)
        if raw_delta:
            deltas[mint] = Decimal(raw_delta) / (Decimal(10) ** decimals)
    if not deltas:
        return None

    traded = {mint: amount for mint, amount in deltas.items() if mint not in QUOTE_MINTS}
    if not traded:
        return None
    mint, token_delta = max(traded.items(), key=lambda item: abs(item[1]))
    quote_flows = [amount for mint, amount in deltas.items() if mint in QUOTE_MINTS]
    quote_delta = sum(quote_flows, Decimal(0))

    invoked = _program_ids(tx, keys)
    is_swap = bool(invoked & _swap_program_allowlist())
    action = "TOKEN TRANSFER / UNKNOWN"
    reason = "NO_RECOGNISED_SWAP_PROGRAM"
    if wallet_index != 0:
        reason = "WATCHED_WALLET_NOT_FEE_PAYER"
    elif is_swap:
        if quote_flows and all(value < 0 for value in quote_flows):
            quote_direction = -1
        elif quote_flows and all(value > 0 for value in quote_flows):
            quote_direction = 1
        elif quote_flows:
            quote_direction = 0
        else:
            native_quote = Decimal(str(economic_sol_delta))
            quote_direction = -1 if native_quote < Decimal("-0.0001") else 1 if native_quote > Decimal("0.0001") else 0
        if token_delta > 0 and quote_direction < 0:
            action, reason = "BUY", "TOKEN_IN_QUOTE_OUT"
        elif token_delta < 0 and quote_direction > 0:
            action, reason = "SELL", "TOKEN_OUT_QUOTE_IN"
        else:
            reason = "SWAP_PROGRAM_WITH_AMBIGUOUS_BALANCE_FLOW"

    return {
        "wallet": wallet,
        "signature": signature,
        "action": action,
        "classification_reason": reason,
        "token_mint": mint,
        "token_delta": float(token_delta),
        "sol_delta": sol_delta,
        "economic_sol_delta": economic_sol_delta,
        "quote_delta": float(quote_delta),
        "block_time": tx.get("blockTime"),
        "received_at": time.time() if received_at is None else received_at,
        "logs": (meta.get("logMessages") or [])[-5:],
        "invoked_programs": sorted(invoked & _swap_program_allowlist()),
    }
