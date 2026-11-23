"""Subject-relative Solana asset deltas for Trojan's transaction history.

This is the Solana implementation of the chain-neutral asset-delta contract:
it preserves every mint movement in raw base units rather than reducing a
transaction to the single dominant token leg used by copy-trade classification.
"""
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional


NATIVE_SOL_SENTINEL = "native"
CHAIN_ID = "solana"
MAX_U128 = (1 << 128) - 1
MIN_I128 = -(1 << 127)
MAX_I128 = (1 << 127) - 1


class DeltaKind(str, Enum):
    TRADE = "trade"
    TRANSFER = "transfer"
    DEFI = "defi"
    OTHER = "other"

    @classmethod
    def parse(cls, value):
        try:
            return cls(value)
        except (TypeError, ValueError) as error:
            raise ValueError("unknown asset delta kind: " + str(value)) from error


@dataclass(frozen=True)
class SubjectAssetDelta:
    chain: str
    wallet_address: str
    signature: str
    mint: str
    slot: Optional[int]
    block_time: Optional[int]
    tx_index: int
    delta_raw: int
    before_raw: Optional[int]
    after_raw: Optional[int]
    decimals: int
    kind: DeltaKind
    venue: Optional[str]
    fee_native_raw: Optional[int]
    success: bool

    def as_dict(self):
        value = asdict(self)
        value["kind"] = self.kind.value
        return value


def _valid_optional_int(value, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _venue_for(program_ids):
    try:
        from solana_onchain import PROGRAMS
    except Exception:
        PROGRAMS = {}
    reverse = {program_id: name for name, program_id in PROGRAMS.items()}
    try:
        from copy_event_parser import JUPITER_V6
    except Exception:
        JUPITER_V6 = None
    for program_id in sorted(program_ids):
        if program_id == JUPITER_V6:
            return "jupiter"
        if program_id in reverse:
            return str(reverse[program_id])
    return None


def extract_subject_deltas(subject_address, transaction, signature=None):
    """Extract every subject-owned token delta and material native SOL delta.

    Malformed, failed, unrelated or ambiguous transactions return an empty list.
    Raw integer values are retained; no floating-point amount conversion occurs.
    """
    if (
        not isinstance(subject_address, str) or not subject_address.strip()
        or not isinstance(transaction, dict)
    ):
        return []
    meta = transaction.get("meta")
    tx_body = transaction.get("transaction")
    message = tx_body.get("message") if isinstance(tx_body, dict) else None
    if not isinstance(meta, dict) or meta.get("err") is not None or not isinstance(message, dict):
        return []

    # Shared Solana parsing primitives keep account ownership and rent correction
    # identical to the conservative copy-event parser.
    from copy_event_parser import (
        SOL_MINT, NATIVE_QUOTE_MIN_LAMPORTS, _account_keys, _program_ids,
        _raw_token_totals, _balance_delta, _subject_native_delta,
    )

    keys = _account_keys(message)
    indices = [index for index, key in enumerate(keys) if key == subject_address]
    if len(indices) != 1:
        return []
    subject_index = indices[0]

    pre_balances = meta.get("preBalances")
    post_balances = meta.get("postBalances")
    native_deltas = _balance_delta(pre_balances, post_balances)
    if native_deltas is None or subject_index >= len(native_deltas):
        return []

    pre_rows = meta.get("preTokenBalances")
    post_rows = meta.get("postTokenBalances")
    if pre_rows is None:
        pre_rows = []
    if post_rows is None:
        post_rows = []
    pre_result = _raw_token_totals(pre_rows, subject_address)
    post_result = _raw_token_totals(post_rows, subject_address)
    if pre_result is None or post_result is None:
        return []
    pre, pre_decimals, pre_accounts = pre_result
    post, post_decimals, post_accounts = post_result

    account_mints = dict(pre_accounts)
    for account_index, mint in post_accounts.items():
        if account_index in account_mints and account_mints[account_index] != mint:
            return []
        account_mints[account_index] = mint
    if subject_index in account_mints:
        return []

    token_deltas = []
    for mint in sorted(set(pre) | set(post)):
        before = pre.get(mint, 0)
        after = post.get(mint, 0)
        decimals = post_decimals.get(mint, pre_decimals.get(mint))
        if decimals is None or not 0 <= decimals <= 18:
            return []
        delta = after - before
        if not MIN_I128 <= delta <= MAX_I128:
            return []
        if delta:
            token_deltas.append((mint, delta, before, after, decimals))

    owned_non_wrapped = [
        index for index, mint in account_mints.items() if mint != SOL_MINT
    ]
    economic_native_delta = _subject_native_delta(
        meta, subject_index, owned_non_wrapped
    )
    if economic_native_delta is None or not MIN_I128 <= economic_native_delta <= MAX_I128:
        return []

    fee = meta.get("fee", 0)
    fee = _valid_optional_int(fee, 0, MAX_U128)
    if fee is None:
        return []

    # Only attribute the network fee to the watched subject when it is the
    # transaction fee payer (account index zero).
    subject_fee = fee if subject_index == 0 else 0

    program_ids = _program_ids(transaction, keys)
    venue = _venue_for(program_ids)
    system_ids = {
        "11111111111111111111111111111111",
        "ComputeBudget111111111111111111111111111111",
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
        "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
        "Sysvar1nstructions1111111111111111111111111",
    }
    native_material = abs(economic_native_delta) > NATIVE_QUOTE_MIN_LAMPORTS
    if venue:
        kind = DeltaKind.TRADE
    elif token_deltas or native_material:
        kind = DeltaKind.TRANSFER
    elif any(program_id not in system_ids for program_id in program_ids):
        kind = DeltaKind.DEFI
    else:
        kind = DeltaKind.OTHER

    slot = _valid_optional_int(transaction.get("slot"), 1, MAX_U128)
    block_time = transaction.get("blockTime")
    if isinstance(block_time, bool) or not isinstance(block_time, int):
        block_time = None
    tx_index = transaction.get("transactionIndex", 0)
    if isinstance(tx_index, bool) or not isinstance(tx_index, int) or tx_index < 0:
        tx_index = 0
    tx_signature = signature if isinstance(signature, str) else transaction.get("signature", "")
    if not isinstance(tx_signature, str):
        tx_signature = ""

    result = []
    for mint, delta, before, after, decimals in token_deltas:
        result.append(SubjectAssetDelta(
            chain=CHAIN_ID, wallet_address=subject_address, signature=tx_signature,
            mint=mint, slot=slot, block_time=block_time, tx_index=tx_index,
            delta_raw=delta,
            before_raw=before if 0 <= before <= MAX_U128 else None,
            after_raw=after if 0 <= after <= MAX_U128 else None,
            decimals=decimals, kind=kind, venue=venue,
            fee_native_raw=subject_fee, success=True,
        ))

    if native_material:
        subject_pre = pre_balances[subject_index]
        subject_post = post_balances[subject_index]
        result.append(SubjectAssetDelta(
            chain=CHAIN_ID, wallet_address=subject_address, signature=tx_signature,
            mint=NATIVE_SOL_SENTINEL, slot=slot, block_time=block_time,
            tx_index=tx_index, delta_raw=economic_native_delta,
            before_raw=subject_pre if 0 <= subject_pre <= MAX_U128 else None,
            after_raw=subject_post if 0 <= subject_post <= MAX_U128 else None,
            decimals=9, kind=kind, venue=venue, fee_native_raw=fee, success=True,
        ))
    return result
