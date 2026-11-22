"""Pool-analysis error classification and bounded retry backoff.

Transport/provider failures are transient and must not be recorded as bad pool
layouts. Structural account/layout failures are temporarily quarantined so the
same invalid pool is not fetched on every market-data heartbeat.
"""
import threading
import time

TRANSIENT_INITIAL_SECONDS = 60.0
TRANSIENT_MAX_SECONDS = 600.0
STRUCTURAL_BLOCK_SECONDS = 3600.0

_TRANSIENT_MARKERS = (
    "all configured solana rpc endpoints failed",
    "no solana rpc endpoint configured",
    "solana_rpc_url",
    "http 429",
    "rate limit",
    "timed out",
    "timeout",
    "connection",
    "network",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "rpc returned unexpected account count",
    "prefetched pool snapshot is missing",
    "required account",
)
_STRUCTURAL_MARKERS = (
    "on-chain account not found",
    "account not found",
    "unsupported solana dex program",
    "unsupported pool protocol",
    "invalid ",
    "discriminator",
    "layout",
    "not active",
    "mint does not match",
    "requested mint is not",
)

_lock = threading.RLock()
_transient = {}
_structural = {}
_total_transient_failures = 0
_total_structural_failures = 0
_last_error = None


def classify_pool_error(error):
    """Classify an error as transient transport/provider or structural pool data."""
    if isinstance(error, (TimeoutError, ConnectionError)):
        return "transient"
    message = str(error or "").lower()
    if any(marker in message for marker in _TRANSIENT_MARKERS):
        return "transient"
    if any(marker in message for marker in _STRUCTURAL_MARKERS):
        return "structural"
    if isinstance(error, RuntimeError):
        return "transient"
    return "structural"


def should_skip_pool(pool_address, now=None):
    if not isinstance(pool_address, str) or not pool_address.strip():
        return False
    now = time.monotonic() if now is None else float(now)
    key = pool_address.strip()
    with _lock:
        transient = _transient.get(key)
        if transient and now < transient["retry_at"]:
            return True
        structural = _structural.get(key)
        if structural and now < structural["retry_at"]:
            return True
        if transient and now >= transient["retry_at"]:
            transient["retry_at"] = now
        if structural and now >= structural["retry_at"]:
            _structural.pop(key, None)
        return False


def record_transient_failure(pool_address, error=None, now=None):
    global _total_transient_failures, _last_error
    if not isinstance(pool_address, str) or not pool_address.strip():
        return None
    now = time.monotonic() if now is None else float(now)
    key = pool_address.strip()
    with _lock:
        previous = _transient.get(key)
        delay = (
            min(TRANSIENT_MAX_SECONDS, previous["next_delay"])
            if previous else TRANSIENT_INITIAL_SECONDS
        )
        retry_at = now + delay
        _transient[key] = {
            "retry_at": retry_at,
            "next_delay": min(TRANSIENT_MAX_SECONDS, delay * 2),
            "failures": (previous["failures"] + 1) if previous else 1,
            "last_error": str(error)[:180] if error is not None else None,
        }
        _total_transient_failures += 1
        _last_error = str(error)[:180] if error is not None else _last_error
        return {"retry_at": retry_at, "retry_in_seconds": delay,
                "failures": _transient[key]["failures"]}


def record_structural_failure(pool_address, error=None, now=None):
    global _total_structural_failures, _last_error
    if not isinstance(pool_address, str) or not pool_address.strip():
        return None
    now = time.monotonic() if now is None else float(now)
    key = pool_address.strip()
    with _lock:
        _transient.pop(key, None)
        retry_at = now + STRUCTURAL_BLOCK_SECONDS
        _structural[key] = {
            "retry_at": retry_at,
            "last_error": str(error)[:180] if error is not None else None,
        }
        _total_structural_failures += 1
        _last_error = str(error)[:180] if error is not None else _last_error
        return {"retry_at": retry_at, "retry_in_seconds": STRUCTURAL_BLOCK_SECONDS}


def record_success(pool_address):
    if not isinstance(pool_address, str) or not pool_address.strip():
        return
    with _lock:
        _transient.pop(pool_address.strip(), None)
        _structural.pop(pool_address.strip(), None)


def get_pool_analysis_status(now=None):
    now = time.monotonic() if now is None else float(now)
    with _lock:
        active_transient = sum(
            1 for item in _transient.values() if item["retry_at"] > now
        )
        active_structural = sum(
            1 for item in _structural.values() if item["retry_at"] > now
        )
        return {
            "transient_backoff_pools": active_transient,
            "structural_blocked_pools": active_structural,
            "tracked_transient_pools": len(_transient),
            "tracked_structural_pools": len(_structural),
            "total_transient_failures": _total_transient_failures,
            "total_structural_failures": _total_structural_failures,
            "transient_initial_seconds": TRANSIENT_INITIAL_SECONDS,
            "transient_max_seconds": TRANSIENT_MAX_SECONDS,
            "structural_block_seconds": STRUCTURAL_BLOCK_SECONDS,
            "last_error": _last_error,
        }


def clear_pool_analysis_state():
    global _total_transient_failures, _total_structural_failures, _last_error
    with _lock:
        _transient.clear()
        _structural.clear()
        _total_transient_failures = 0
        _total_structural_failures = 0
        _last_error = None
