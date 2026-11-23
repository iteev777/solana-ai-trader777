"""Persistent, explicit Solana token/pool/account denylist helpers.

Entries are manually or programmatically supplied by trusted local workflows.
The module validates public keys and never treats provider metadata as a blacklist
instruction. All decisions are auditable through the database and dashboard.
"""
import database
from solana_onchain import _b58decode

ENTRY_TYPES = ("token", "pool", "account")


def _validate_public_key(value, field="address"):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + " is required")
    value = value.strip()
    try:
        decoded = _b58decode(value)
    except Exception as error:
        raise ValueError(field + " is not a valid base58 Solana public key") from error
    if len(decoded) != 32:
        raise ValueError(field + " must decode to a 32-byte Solana public key")
    return value


def add_entry(entry_type, address, reason, source="manual", token_mint=None):
    if entry_type not in ENTRY_TYPES:
        raise ValueError("entry_type must be token, pool or account")
    address = _validate_public_key(address)
    mint = _validate_public_key(token_mint, "token_mint") if token_mint else None
    return database.upsert_blacklist_entry(
        entry_type, address, reason, source=source, token_mint=mint
    )


def remove_entry(entry_type, address):
    if entry_type not in ENTRY_TYPES:
        raise ValueError("entry_type must be token, pool or account")
    address = _validate_public_key(address)
    return database.delete_blacklist_entry(entry_type, address)


def get_entries(entry_type=None, include_inactive=False, limit=500):
    return database.list_blacklist_entries(
        entry_type=entry_type, include_inactive=include_inactive, limit=limit
    )


def match_entries(entries):
    """Return active blacklist rows matching a list of typed public keys."""
    normalized = []
    for entry_type, address in entries or []:
        if entry_type not in ENTRY_TYPES:
            continue
        try:
            normalized.append((entry_type, _validate_public_key(address)))
        except ValueError:
            continue
    return database.match_blacklist_entries(normalized)


def match_token_security_accounts(report):
    if not isinstance(report, dict):
        return []
    entries = []
    details = report.get("largest_token_account_details")
    if isinstance(details, list):
        for detail in details:
            if isinstance(detail, dict):
                entries.append(("account", detail.get("address")))
                entries.append(("account", detail.get("authority")))
    return match_entries(entries)
