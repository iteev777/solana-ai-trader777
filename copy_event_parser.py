"""Conservative subject-wallet transaction classification for copy research.

A BUY/SELL requires a recognised DEX/aggregator program and one unambiguous
non-quote token leg opposed by exactly one quote leg. Incomplete balance
metadata, mixed quote legs and multi-token swaps remain UNKNOWN.
"""
from decimal import Decimal

SOL_MINT = "So11111111111111111111111111111111111111112"
QUOTE_MINTS = {
    SOL_MINT,
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}
JUPITER_V6 = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
# Rent, account creation/closure and ordinary fee residue must not be mistaken
# for a SOL quote leg. This intentionally rejects very small SOL-quoted copies.
NATIVE_QUOTE_MIN_LAMPORTS = 50_000


def _account_keys(message):
    keys = message.get("accountKeys")
    if not isinstance(keys, list):
        return []
    result = []
    for item in keys:
        value = item.get("pubkey") if isinstance(item, dict) else item
        result.append(value if isinstance(value, str) else "")
    return result


def _program_ids(tx, keys):
    message = tx.get("transaction", {}).get("message", {})
    meta = tx.get("meta", {})
    if not isinstance(message, dict) or not isinstance(meta, dict):
        return set()
    indexed_keys = list(keys)
    loaded = meta.get("loadedAddresses") or {}
    if isinstance(loaded, dict):
        for kind in ("writable", "readonly"):
            values = loaded.get(kind) or []
            if isinstance(values, list):
                indexed_keys.extend(item for item in values if isinstance(item, str))
    ids = set()
    instructions = message.get("instructions") or []
    if not isinstance(instructions, list):
        instructions = []
    for group in meta.get("innerInstructions") or []:
        if isinstance(group, dict) and isinstance(group.get("instructions"), list):
            instructions.extend(group["instructions"])
    for instruction in instructions:
        if not isinstance(instruction, dict):
            continue
        program = instruction.get("programId")
        if not program:
            index = instruction.get("programIdIndex")
            if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(indexed_keys):
                program = indexed_keys[index]
        if isinstance(program, str) and program:
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
    """Return per-mint raw totals; reject malformed subject-owned balance rows."""
    if not isinstance(rows, list):
        return None
    totals = {}
    decimals_by_mint = {}
    seen_accounts = set()
    account_mints = {}
    for item in rows:
        if not isinstance(item, dict):
            return None
        if item.get("owner") != wallet:
            continue
        mint = item.get("mint")
        amount = (item.get("uiTokenAmount") or {}).get("amount")
        decimals = (item.get("uiTokenAmount") or {}).get("decimals")
        account_index = item.get("accountIndex")
        if (
            not isinstance(mint, str) or not mint
            or not isinstance(amount, str) or not amount.isdigit()
            or isinstance(decimals, bool) or not isinstance(decimals, int)
            or not 0 <= decimals <= 18
            or isinstance(account_index, bool) or not isinstance(account_index, int)
            or account_index < 0
        ):
            return None
        if account_index in seen_accounts:
            return None
        seen_accounts.add(account_index)
        previous_mint = account_mints.get(account_index)
        if previous_mint is not None and previous_mint != mint:
            return None
        account_mints[account_index] = mint
        if mint in decimals_by_mint and decimals_by_mint[mint] != decimals:
            return None
        decimals_by_mint[mint] = decimals
        totals[mint] = totals.get(mint, 0) + int(amount)
    return totals, decimals_by_mint, account_mints


def _balance_delta(before, after):
    if (
        not isinstance(before, list) or not isinstance(after, list)
        or len(before) != len(after)
    ):
        return None
    result = []
    for pre, post in zip(before, after):
        if (
            isinstance(pre, bool) or not isinstance(pre, int) or pre < 0
            or isinstance(post, bool) or not isinstance(post, int) or post < 0
        ):
            return None
        result.append(post - pre)
    return result


def _subject_native_delta(meta, wallet_index, owned_token_indices):
    """Correct wallet SOL movement for rent held by subject-owned token accounts."""
    deltas = _balance_delta(meta.get("preBalances"), meta.get("postBalances"))
    if deltas is None or wallet_index >= len(deltas):
        return None
    fee = meta.get("fee", 0)
    if isinstance(fee, bool) or not isinstance(fee, int) or fee < 0:
        return None
    result = deltas[wallet_index] + (fee if wallet_index == 0 else 0)
    seen = {wallet_index}
    for index in owned_token_indices:
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            return None
        if index in seen:
            continue
        if index >= len(deltas):
            return None
        seen.add(index)
        result += deltas[index]
    return result


def parse_wallet_transaction(tx, wallet, signature=None, received_at=None):
    """Return a conservative wallet event, or None for incomplete/failed tx data."""
    import time
    if not isinstance(tx, dict) or not isinstance(wallet, str) or not wallet.strip():
        return None
    meta = tx.get("meta")
    if not isinstance(meta, dict) or meta.get("err") is not None:
        return None
    transaction = tx.get("transaction")
    message = transaction.get("message") if isinstance(transaction, dict) else None
    if not isinstance(message, dict):
        return None
    keys = _account_keys(message)
    wallet_indices = [index for index, key in enumerate(keys) if key == wallet]
    if len(wallet_indices) != 1:
        return None
    wallet_index = wallet_indices[0]

    sol_deltas = _balance_delta(meta.get("preBalances"), meta.get("postBalances"))
    if sol_deltas is None or wallet_index >= len(sol_deltas):
        return None
    sol_delta_lamports = sol_deltas[wallet_index]
    sol_delta = sol_delta_lamports / 1_000_000_000

    pre_result = _raw_token_totals(meta.get("preTokenBalances"), wallet)
    post_result = _raw_token_totals(meta.get("postTokenBalances"), wallet)
    if pre_result is None or post_result is None:
        return None
    pre, pre_decimals, pre_accounts = pre_result
    post, post_decimals, post_accounts = post_result
    owned_account_mints = dict(pre_accounts)
    for account_index, account_mint in post_accounts.items():
        if account_index in owned_account_mints and owned_account_mints[account_index] != account_mint:
            return None
        owned_account_mints[account_index] = account_mint
    if wallet_index in owned_account_mints:
        return None
    owned_non_wrapped_sol_accounts = [
        index for index, account_mint in owned_account_mints.items()
        if account_mint != SOL_MINT
    ]
    economic_sol_lamports = _subject_native_delta(
        meta, wallet_index, owned_non_wrapped_sol_accounts
    )
    if economic_sol_lamports is None:
        return None
    economic_sol_delta = economic_sol_lamports / 1_000_000_000
    deltas = {}
    for mint in set(pre) | set(post):
        decimals = post_decimals.get(mint, pre_decimals.get(mint))
        if decimals is None or (
            mint in pre_decimals and mint in post_decimals
            and pre_decimals[mint] != post_decimals[mint]
        ):
            return None
        raw_delta = post.get(mint, 0) - pre.get(mint, 0)
        if raw_delta:
            deltas[mint] = Decimal(raw_delta) / (Decimal(10) ** decimals)
    if not deltas:
        return None

    traded = {mint: amount for mint, amount in deltas.items() if mint not in QUOTE_MINTS}
    if not traded:
        return None
    mint, token_delta = max(traded.items(), key=lambda item: abs(item[1]))
    quote_flows = {mint: amount for mint, amount in deltas.items()
                   if mint in QUOTE_MINTS and amount != 0}
    quote_delta = sum(quote_flows.values(), Decimal(0))

    invoked = _program_ids(tx, keys)
    is_swap = bool(invoked & _swap_program_allowlist())
    action = "TOKEN TRANSFER / UNKNOWN"
    reason = "NO_RECOGNISED_SWAP_PROGRAM"
    if is_swap:
        if len(traded) > 1:
            reason = "MULTIPLE_TOKEN_LEGS"
        elif len(quote_flows) > 1:
            reason = "MULTIPLE_QUOTE_LEGS"
        else:
            stable_flows = {
                quote_mint: amount for quote_mint, amount in quote_flows.items()
                if quote_mint != SOL_MINT
            }
            native_is_material = abs(economic_sol_lamports) > NATIVE_QUOTE_MIN_LAMPORTS
            if stable_flows and native_is_material:
                reason = "MIXED_NATIVE_AND_STABLE_QUOTE"
            elif stable_flows:
                stable_delta = next(iter(stable_flows.values()))
                quote_direction = -1 if stable_delta < 0 else 1
                if token_delta > 0 and quote_direction < 0:
                    action, reason = "BUY", "TOKEN_IN_QUOTE_OUT"
                elif token_delta < 0 and quote_direction > 0:
                    action, reason = "SELL", "TOKEN_OUT_QUOTE_IN"
                else:
                    reason = "SWAP_PROGRAM_WITH_AMBIGUOUS_BALANCE_FLOW"
            elif SOL_MINT in quote_flows or native_is_material:
                native_direction = (
                    -1 if economic_sol_lamports < 0
                    else 1 if economic_sol_lamports > 0 else 0
                )
                if token_delta > 0 and native_direction < 0:
                    action, reason = "BUY", "TOKEN_IN_QUOTE_OUT"
                elif token_delta < 0 and native_direction > 0:
                    action, reason = "SELL", "TOKEN_OUT_QUOTE_IN"
                else:
                    reason = "SWAP_PROGRAM_WITH_AMBIGUOUS_BALANCE_FLOW"
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
