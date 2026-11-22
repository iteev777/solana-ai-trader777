"""Multi-source pool registry with rate-limited provider completion.

The fast DexScreener batch endpoint supplies immediate pricing. A background
worker fetches the full token-pairs list per mint, then asks GeckoTerminal to
complete lists that are empty, capped, or lack a SOL-native pair.
"""
import copy
import queue
import threading
import time
from collections import OrderedDict

from api_client import get_json

GECKO_TOKEN_POOLS_URL = "https://api.geckoterminal.com/api/v2/networks/solana/tokens/"
DEXSCREENER_TOKEN_POOLS_URL = "https://api.dexscreener.com/token-pairs/v1/solana/"
WSOL_MINT = "So11111111111111111111111111111111111111112"
DEXSCREENER_TOKEN_PAIRS_CAP = 30
MAX_TOKENS = 5000
MAX_POOLS_PER_TOKEN = 50
MAX_PENDING_POOL_REQUESTS = 500
CACHE_TTL_SECONDS = 60
POOL_STALE_TTL_SECONDS = 300
FULL_POOL_REFRESH_TTL_SECONDS = 60
POOL_REFRESH_RETRY_SECONDS = 30

_lock = threading.RLock()
_candidates = OrderedDict()
_candidate_updated_at = {}
_full_refresh_at = {}
_full_refresh_retry_at = {}
_gecko_retry_at = {}
_pending = set()
_queue = queue.Queue(maxsize=MAX_PENDING_POOL_REQUESTS)
_worker_thread = None
_status = {
    "queued": 0, "pending": 0, "completed": 0, "failed": 0,
    "pools_merged": 0, "dex_pending": 0, "gecko_pending": 0,
    "last_request_at": None, "last_success_at": None, "last_error": None,
}


def _pool_address(pair):
    value = pair.get("pairAddress") or pair.get("poolAddress") or pair.get("address")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _pool_liquidity(pair):
    liquidity = pair.get("liquidity")
    if not isinstance(liquidity, dict):
        return 0.0
    try:
        value = float(liquidity.get("usd") or 0)
        return value if value >= 0 and value < float("inf") else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _pool_priority(pair):
    quote = pair.get("quoteToken")
    quote_address = quote.get("address") if isinstance(quote, dict) else None
    liquidity = pair.get("liquidity")
    liquidity = liquidity if isinstance(liquidity, dict) else {}
    if quote_address == WSOL_MINT:
        try:
            native_reserve = float(liquidity.get("quote") or 0)
        except (TypeError, ValueError, OverflowError):
            native_reserve = 0.0
        return (1, max(0.0, native_reserve), _pool_liquidity(pair))
    return (0, 0.0, _pool_liquidity(pair))


def merge_pool_candidates(mint, pairs, source):
    """Merge pool rows by pool address while preserving source provenance."""
    if not isinstance(mint, str) or not mint.strip() or not isinstance(pairs, list):
        return 0
    mint = mint.strip()
    now = time.time()
    merged_count = 0
    with _lock:
        rows = _candidates.get(mint, OrderedDict())
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            address = _pool_address(pair)
            base = pair.get("baseToken")
            if not address or not isinstance(base, dict) or base.get("address") != mint:
                continue
            existing = rows.get(address)
            incoming = copy.deepcopy(pair)
            if existing is None:
                incoming["marketSources"] = [source]
                incoming["poolSourceData"] = {source: copy.deepcopy(pair)}
                rows[address] = incoming
            else:
                sources = set(existing.get("marketSources") or [])
                sources.add(source)
                source_data = dict(existing.get("poolSourceData") or {})
                source_data[source] = copy.deepcopy(pair)
                # Keep DexScreener's quote as primary when both providers cover a pool.
                if source == "dexscreener" or "dexscreener" not in sources:
                    existing.update(incoming)
                existing["marketSources"] = sorted(sources)
                existing["poolSourceData"] = source_data
                rows.move_to_end(address)
            merged_count += 1

        ordered = sorted(
            rows.items(),
            key=lambda item: (_pool_priority(item[1]), item[0]),
            reverse=True
        )[:MAX_POOLS_PER_TOKEN]
        _candidates[mint] = OrderedDict(ordered)
        _candidate_updated_at[mint] = now
        _candidates.move_to_end(mint)
        while len(_candidates) > MAX_TOKENS:
            removed, _ = _candidates.popitem(last=False)
            _candidate_updated_at.pop(removed, None)
        _status["pools_merged"] += merged_count
    return merged_count


def get_pool_candidates(mint):
    if not isinstance(mint, str) or not mint.strip():
        return []
    normalized = mint.strip()
    with _lock:
        updated_at = _candidate_updated_at.get(normalized, 0.0)
        if updated_at and time.time() - updated_at > POOL_STALE_TTL_SECONDS:
            _candidates.pop(normalized, None)
            _candidate_updated_at.pop(normalized, None)
            return []
        rows = _candidates.get(normalized)
        return [copy.deepcopy(value) for value in rows.values()] if rows else []


def is_dexscreener_list_incomplete(pairs):
    if not isinstance(pairs, list) or len(pairs) >= DEXSCREENER_TOKEN_PAIRS_CAP:
        return True
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        base = pair.get("baseToken")
        quote = pair.get("quoteToken")
        if (isinstance(base, dict) and isinstance(quote, dict)
                and quote.get("address") == WSOL_MINT):
            return False
    return True


def _ensure_worker():
    global _worker_thread
    with _lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(
                target=_worker, name="TrojanPoolRegistryWorker", daemon=True
            )
            _worker_thread.start()


def _enqueue(source, mint):
    task = (source, mint)
    with _lock:
        if task in _pending:
            return False
        _pending.add(task)
        _status["queued"] += 1
        _status["pending"] = len(_pending)
        _status["dex_pending"] = sum(1 for item in _pending if item[0] == "dexscreener")
        _status["gecko_pending"] = sum(1 for item in _pending if item[0] == "geckoterminal")
    try:
        _queue.put_nowait(task)
    except queue.Full:
        with _lock:
            _pending.discard(task)
            _status["pending"] = len(_pending)
            _status["failed"] += 1
            _status["last_error"] = "Pool registry queue is full"
        return False
    _ensure_worker()
    return True


def needs_dexscreener_refresh(mint):
    if not isinstance(mint, str) or not mint.strip():
        return False
    mint = mint.strip()
    now = time.time()
    with _lock:
        refreshed = _full_refresh_at.get(mint, 0.0)
        retry_at = _full_refresh_retry_at.get(mint, 0.0)
        return now >= retry_at and now - refreshed >= FULL_POOL_REFRESH_TTL_SECONDS


def enqueue_dex_pool_refresh(mint):
    if not isinstance(mint, str) or not mint.strip():
        return False
    mint = mint.strip()
    if not needs_dexscreener_refresh(mint):
        return False
    return _enqueue("dexscreener", mint)


def enqueue_gecko_pool_fetch(mint):
    if not isinstance(mint, str) or not mint.strip():
        return False
    mint = mint.strip()
    with _lock:
        if time.time() < _gecko_retry_at.get(mint, 0.0):
            return False
    return _enqueue("geckoterminal", mint)


def fetch_dexscreener_token_pools(mint):
    """Fetch all known pools for a token from token-pairs/v1 (max 30)."""
    url = DEXSCREENER_TOKEN_POOLS_URL + mint
    with _lock:
        _status["last_request_at"] = time.time()
    response = get_json(
        url, timeout=10, cache_ttl=CACHE_TTL_SECONDS, max_retries=1
    )
    if not isinstance(response, list):
        raise ValueError("DexScreener token-pairs response is not a list")
    token_pairs = [
        pair for pair in response
        if isinstance(pair, dict)
        and isinstance(pair.get("baseToken"), dict)
        and pair["baseToken"].get("address") == mint
    ]
    merge_pool_candidates(mint, token_pairs, "dexscreener")
    with _lock:
        _full_refresh_at[mint] = time.time()
        _full_refresh_retry_at.pop(mint, None)
    incomplete = is_dexscreener_list_incomplete(token_pairs)
    if incomplete:
        enqueue_gecko_pool_fetch(mint)
    from market_data import refresh_market_data_from_pool_registry
    refresh_market_data_from_pool_registry(mint)
    return {"received": len(response), "usable": len(token_pairs), "incomplete": incomplete}


def fetch_gecko_pool_candidates(mint):
    """Fetch GeckoTerminal's complete token pool list and merge it by pool address."""
    url = GECKO_TOKEN_POOLS_URL + mint + "/pools?include=base_token,quote_token,dex"
    with _lock:
        _status["last_request_at"] = time.time()
    response = get_json(
        url,
        headers={"Accept": "application/json;version=20230203"},
        timeout=10,
        cache_ttl=CACHE_TTL_SECONDS,
        max_retries=1,
    )
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, list):
        raise ValueError("GeckoTerminal token pools response has no data list")
    from market_data import normalize_gecko_pool, refresh_market_data_from_pool_registry
    normalized = []
    for pool in data:
        row = normalize_gecko_pool(pool, requested_mint=mint)
        if row:
            normalized.append(row)
    merged = merge_pool_candidates(mint, normalized, "geckoterminal")
    refreshed = refresh_market_data_from_pool_registry(mint)
    with _lock:
        _status["last_success_at"] = time.time()
        _gecko_retry_at[mint] = time.time() + FULL_POOL_REFRESH_TTL_SECONDS
        _status["last_error"] = None if normalized else "GeckoTerminal returned no usable pools"
    return {"received": len(data), "normalized": len(normalized),
            "merged": merged, "selected": bool(refreshed)}


def _worker():
    while True:
        source, mint = _queue.get()
        try:
            if source == "dexscreener":
                fetch_dexscreener_token_pools(mint)
            else:
                fetch_gecko_pool_candidates(mint)
            with _lock:
                _status["completed"] += 1
        except Exception as error:
            with _lock:
                _status["failed"] += 1
                _status["last_error"] = str(error)[:200]
                if source == "dexscreener":
                    _full_refresh_retry_at[mint] = time.time() + POOL_REFRESH_RETRY_SECONDS
                else:
                    _gecko_retry_at[mint] = time.time() + FULL_POOL_REFRESH_TTL_SECONDS
        finally:
            with _lock:
                _pending.discard((source, mint))
                _status["pending"] = len(_pending)
                _status["dex_pending"] = sum(1 for item in _pending if item[0] == "dexscreener")
                _status["gecko_pending"] = sum(1 for item in _pending if item[0] == "geckoterminal")
            _queue.task_done()


def get_pool_registry_status():
    with _lock:
        return {
            **_status,
            "token_count": len(_candidates),
            "queue_size": _queue.qsize(),
            "provider": "DexScreener + GeckoTerminal",
            "cache_ttl_seconds": CACHE_TTL_SECONDS,
            "pool_stale_ttl_seconds": POOL_STALE_TTL_SECONDS,
            "max_pools_per_token": MAX_POOLS_PER_TOKEN,
        }


def clear_pool_registry_state():
    with _lock:
        _candidates.clear()
        _candidate_updated_at.clear()
        _full_refresh_at.clear()
        _full_refresh_retry_at.clear()
        _gecko_retry_at.clear()
        _pending.clear()
        for key in _status:
            _status[key] = 0 if key in (
                "queued", "pending", "completed", "failed", "pools_merged",
                "dex_pending", "gecko_pending"
            ) else None
