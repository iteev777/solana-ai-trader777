"""Bounded batch SOL-balance reads for wallet lists.

Results preserve input order. Invalid addresses, unavailable batches and malformed
RPC responses are reported as None; a valid address whose account does not exist
is reported as 0.0 SOL. This deliberately keeps unknown distinct from zero.
"""
from typing import Optional, Sequence

import solana_onchain as chain

LAMPORTS_PER_SOL = 1_000_000_000
SOL_BALANCE_BATCH_SIZE = 50


def _valid_pubkey(address: object) -> bool:
    if not isinstance(address, str) or not address or len(address) > 44:
        return False
    try:
        raw = chain._b58decode(address)
    except (TypeError, ValueError, IndexError):
        return False
    return len(raw) == 32 and chain._b58encode(raw) == address


def fetch_wallet_sol_balances(addresses: Sequence[str]) -> list[Optional[float]]:
    """Fetch wallet SOL balances in batches of at most 50 accounts.

    A failed or malformed batch leaves only that batch's valid addresses unknown.
    Invalid addresses are never sent to RPC. Duplicate addresses and input ordering
    are preserved.
    """
    if isinstance(addresses, (str, bytes)) or not isinstance(addresses, Sequence):
        raise TypeError("addresses must be a sequence of public-key strings")
    results: list[Optional[float]] = [None] * len(addresses)
    valid = [
        (index, address)
        for index, address in enumerate(addresses)
        if _valid_pubkey(address)
    ]

    for offset in range(0, len(valid), SOL_BALANCE_BATCH_SIZE):
        batch = valid[offset:offset + SOL_BALANCE_BATCH_SIZE]
        keys = [address for _, address in batch]
        try:
            response = chain._rpc(
                "getMultipleAccounts",
                [keys, {"encoding": "base64", "commitment": "confirmed"}],
            )
            values = (response or {}).get("value")
            if not isinstance(values, list) or len(values) != len(batch):
                continue
            decoded: list[Optional[float]] = []
            for account in values:
                if account is None:
                    decoded.append(0.0)
                    continue
                lamports = account.get("lamports") if isinstance(account, dict) else None
                if (
                    isinstance(lamports, bool)
                    or not isinstance(lamports, int)
                    or lamports < 0
                ):
                    decoded.append(None)
                else:
                    decoded.append(lamports / LAMPORTS_PER_SOL)
            for (index, _), balance in zip(batch, decoded):
                results[index] = balance
        except Exception:
            # Keep an RPC outage scoped to this batch; other batches may still work.
            continue
    return results
