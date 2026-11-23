"""Bounded, single-flight cache for confirmed Solana mint decimals."""
import os
import threading
import time
from collections import OrderedDict


_LOCK = threading.RLock()
_CACHE = OrderedDict()
_IN_FLIGHT = {}
_STATS = {"hits": 0, "misses": 0, "errors": 0, "waits": 0, "evictions": 0}


def _bounded_env(name, default, minimum, maximum, integer=True):
    try:
        raw = os.getenv(name, str(default))
        value = int(raw) if integer else float(raw)
        if value < minimum or value > maximum:
            return default
        return value
    except (TypeError, ValueError, OverflowError):
        return default


def _valid_decimals(value):
    return (
        isinstance(value, int) and not isinstance(value, bool)
        and 0 <= value <= 18
    )


def get_token_decimals(mint_address):
    """Return decimals 0–18, or None when the confirmed mint account is unavailable."""
    if not isinstance(mint_address, str) or not mint_address.strip() or len(mint_address.strip()) > 64:
        return None
    mint = mint_address.strip()
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(mint)
        if cached is not None:
            expires_at, value = cached
            if expires_at > now:
                _CACHE.move_to_end(mint)
                _STATS["hits"] += 1
                return value
            _CACHE.pop(mint, None)
        event = _IN_FLIGHT.get(mint)
        owner = event is None
        if owner:
            event = threading.Event()
            _IN_FLIGHT[mint] = event
            _STATS["misses"] += 1
        else:
            _STATS["waits"] += 1

    if not owner:
        wait_seconds = _bounded_env(
            "TROJAN_TOKEN_DECIMALS_SINGLEFLIGHT_WAIT_SECONDS", 5.0, 0.1, 30.0, integer=False
        )
        if not event.wait(wait_seconds):
            return None
        with _LOCK:
            cached = _CACHE.get(mint)
            if cached is None or cached[0] <= time.monotonic():
                return None
            _CACHE.move_to_end(mint)
            return cached[1]

    value = None
    try:
        import solana_onchain
        resolved = solana_onchain.get_mint_decimals(mint)
        if _valid_decimals(resolved):
            value = resolved
        else:
            _STATS["errors"] += 1
    except Exception:
        with _LOCK:
            _STATS["errors"] += 1
    finally:
        success_ttl = _bounded_env(
            "TROJAN_TOKEN_DECIMALS_CACHE_TTL_SECONDS", 86400, 60, 604800
        )
        failure_ttl = _bounded_env(
            "TROJAN_TOKEN_DECIMALS_FAILURE_TTL_SECONDS", 60, 1, 3600
        )
        maximum = _bounded_env("TROJAN_TOKEN_DECIMALS_CACHE_MAX", 5000, 100, 50000)
        with _LOCK:
            _CACHE[mint] = (
                time.monotonic() + (success_ttl if value is not None else failure_ttl),
                value,
            )
            _CACHE.move_to_end(mint)
            while len(_CACHE) > maximum:
                _CACHE.popitem(last=False)
                _STATS["evictions"] += 1
            finished = _IN_FLIGHT.pop(mint, None)
            if finished is not None:
                finished.set()
    return value


def clear_cache(mint_address=None):
    with _LOCK:
        if mint_address is None:
            _CACHE.clear()
            return
        _CACHE.pop(str(mint_address).strip(), None)


def get_cache_status():
    with _LOCK:
        return {**_STATS, "size": len(_CACHE), "in_flight": len(_IN_FLIGHT)}
