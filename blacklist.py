"""Persistent, explicit Solana token/pool/account denylist helpers.

Entries are manually or programmatically supplied by trusted local workflows.
The module validates public keys and never treats provider metadata as a blacklist
instruction. All decisions are auditable through the database and dashboard.
"""
import os
import time
import threading
from collections import OrderedDict

import database
from solana_onchain import _b58decode

ENTRY_TYPES = ("token", "pool", "account")

CACHE_TTL_SECONDS = 60.0
MAX_CACHE_ENTRIES = 100_000
_cache = OrderedDict()
_cache_lock = threading.RLock()


def _cache_ttl_seconds():
    try:
        value = float(os.getenv("TROJAN_BLACKLIST_CACHE_SECONDS", str(CACHE_TTL_SECONDS)))
    except (TypeError, ValueError, OverflowError):
        return CACHE_TTL_SECONDS
    return value if 1.0 <= value <= 3600.0 else CACHE_TTL_SECONDS


def clear_cache():
    """Clear the process-local match cache (used after bulk DB refresh and tests)."""
    with _cache_lock:
        _cache.clear()


def _invalidate_cache(key):
    with _cache_lock:
        _cache.pop(key, None)


def _cached_matches(key, now):
    with _cache_lock:
        item = _cache.get(key)
        if item is None:
            return None
        cached_at, matches = item
        if now - cached_at >= _cache_ttl_seconds():
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return [dict(row) for row in matches]


def _store_matches(key, matches, now):
    with _cache_lock:
        _cache[key] = (now, tuple(dict(row) for row in matches))
        _cache.move_to_end(key)
        while len(_cache) > MAX_CACHE_ENTRIES:
            _cache.popitem(last=False)


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
    result = database.upsert_blacklist_entry(
        entry_type, address, reason, source=source, token_mint=mint
    )
    _invalidate_cache((entry_type, address))
    return result


def remove_entry(entry_type, address):
    if entry_type not in ENTRY_TYPES:
        raise ValueError("entry_type must be token, pool or account")
    address = _validate_public_key(address)
    removed = database.delete_blacklist_entry(entry_type, address)
    _invalidate_cache((entry_type, address))
    return removed


def get_entries(entry_type=None, include_inactive=False, limit=500):
    return database.list_blacklist_entries(
        entry_type=entry_type, include_inactive=include_inactive, limit=limit
    )


def match_entries(entries):
    """Return active blacklist rows using a bounded, invalidation-aware hot-path cache."""
    normalized = []
    seen = set()
    for item in entries or []:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue
        entry_type, address = item
        if entry_type not in ENTRY_TYPES:
            continue
        try:
            key = (entry_type, _validate_public_key(address))
        except ValueError:
            continue
        if key not in seen:
            normalized.append(key)
            seen.add(key)
    if not normalized:
        return []

    now = time.monotonic()
    result = []
    missing = []
    for key in normalized:
        cached = _cached_matches(key, now)
        if cached is None:
            missing.append(key)
        else:
            result.extend(cached)

    if missing:
        rows = database.match_blacklist_entries(missing)
        grouped = {key: [] for key in missing}
        for row in rows:
            key = (row.get("entry_type"), row.get("entry_value"))
            if key in grouped:
                grouped[key].append(row)
        for key in missing:
            _store_matches(key, grouped[key], now)
            result.extend(dict(row) for row in grouped[key])
    return result


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
