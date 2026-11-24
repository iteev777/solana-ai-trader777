"""Post-confirmation token-output accounting for native Solana swaps.

The swap instruction's minimum-output constraint protects execution. This
module measures the actual token amount for fills/P&L after confirmation.
Missing RPC metadata is treated as an accounting gap, never as a zero fill.
Native-SOL receipts are deliberately rejected here; they require WSOL inner
instruction accounting and must not be confused with wallet lamport deltas.
"""
from dataclasses import dataclass


class ReceiptError(ValueError):
    """A confirmed transaction contradicts its guaranteed token output."""


@dataclass(frozen=True)
class SwapReceipt:
    received: int
    exact: bool
    network_fee_lamports: int
    slot: int


def _uint(value, label, allow_zero=False):
    if isinstance(value, bool):
        raise ValueError(label + " must be an integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.isdigit():
        number = int(value)
    else:
        raise ValueError(label + " must be an integer")
    if number < (0 if allow_zero else 1) or number > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(label + " is outside the supported u64 range")
    return number


def _account_keys(transaction):
    message = ((transaction.get("transaction") or {}).get("message") or {})
    keys = message.get("accountKeys")
    if not isinstance(keys, list):
        return []
    result = []
    for item in keys:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and isinstance(item.get("pubkey"), str):
            result.append(item["pubkey"])
        else:
            result.append(None)
    return result


def _sum_balances(entries, matches):
    """Return (matched, raw total); absent metadata differs from a real zero."""
    if not isinstance(entries, list):
        return False, 0
    total = 0
    matched = False
    for entry in entries:
        if not isinstance(entry, dict) or not matches(entry):
            continue
        matched = True
        amount = ((entry.get("uiTokenAmount") or {}).get("amount"))
        total += _uint(amount, "token balance", allow_zero=True)
        if total > 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Aggregated token balance exceeds u64")
    return matched, total


def _token_delta(meta, transaction, owner, output_account, output_mint):
    """Measure the destination account by index first, then mint+owner."""
    keys = _account_keys(transaction)
    try:
        account_index = keys.index(output_account)
    except ValueError:
        account_index = None

    pre = meta.get("preTokenBalances")
    post = meta.get("postTokenBalances")
    if account_index is not None:
        by_index = lambda row: row.get("accountIndex") == account_index
        post_found, post_total = _sum_balances(post, by_index)
        if post_found:
            pre_found, pre_total = _sum_balances(pre, by_index)
            return max(0, post_total - (pre_total if pre_found else 0))

    if owner:
        by_owner = lambda row: (
            row.get("mint") == output_mint and row.get("owner") == owner
        )
        post_found, post_total = _sum_balances(post, by_owner)
        if post_found:
            pre_found, pre_total = _sum_balances(pre, by_owner)
            return max(0, post_total - (pre_total if pre_found else 0))
    return None


def receipt_from_transaction(signature, transaction, output_account, output_mint,
                             minimum_output, *, confirmed=False, owner=None,
                             output_is_native=False):
    """Return an exact token fill or a clearly labelled minimum-output estimate.

    Caller must establish successful confirmation before invoking this method.
    A grounded token-account delta below the on-chain minimum is a hard error.
    If the node omits metadata or the destination cannot be attributed, the
    guaranteed minimum is returned with exact=False rather than inventing zero.
    """
    if confirmed is not True:
        raise ValueError("A successfully confirmed transaction is required")
    if not isinstance(signature, str) or not signature.strip():
        raise ValueError("Transaction signature is required")
    if not isinstance(transaction, dict):
        raise ValueError("Transaction details must be an object")
    if output_is_native:
        raise ValueError(
            "Native SOL receipt measurement requires WSOL inner-instruction accounting"
        )
    if not isinstance(output_account, str) or not output_account:
        raise ValueError("Output token account is required")
    if not isinstance(output_mint, str) or not output_mint:
        raise ValueError("Output mint is required")
    minimum = _uint(minimum_output, "minimum_output")

    slot = transaction.get("slot", 0)
    slot = _uint(slot, "transaction slot", allow_zero=True)
    meta = transaction.get("meta")
    if meta is None:
        return SwapReceipt(minimum, False, 0, slot)
    if not isinstance(meta, dict):
        raise ValueError("Transaction metadata is malformed")
    if meta.get("err") is not None:
        raise ReceiptError("Confirmed transaction metadata contains an execution error")

    fee = _uint(meta.get("fee", 0), "network fee", allow_zero=True)
    received = _token_delta(meta, transaction, owner, output_account, output_mint)
    if received is None:
        return SwapReceipt(minimum, False, fee, slot)
    if received < minimum:
        raise ReceiptError(
            "Measured token output {} is below guaranteed minimum {}".format(
                received, minimum
            )
        )
    return SwapReceipt(received, True, fee, slot)
