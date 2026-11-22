"""Multi-source pool registry with rate-limited GeckoTerminal completion.

DexScreener is ingested in batches. When its per-token list is empty, lacks a
SOL-native pair, or reaches the provider's 30-pair cap, a background GeckoTerminal
token-pools request completes the registry without blocking the trading loop.
"""
import copy
import queue
import threading
import time
from collections import OrderedDict

from api_client import get_json

GECKO_TOKEN_POOLS_URL = (
    "https://api.geckoterminal.com/api/v2/networks/solana/tokens/"
)
DEXSCREENER_TOKEN_PAIRS_CAP = 30
MAX_TOKENS = 5000
MAX_POOLS_PER_TOKEN = 50
MAX_PENDING_GECKO_REQUESTS = 500
CACHE_TTL_SECONDS = 60

_lock = threading.RLock()
_candidates = OrderedDict()
_candidate_updated_at = {}
_pending = set()
_queue = queue.Queue(maxsize=MAX_PENDING_GECKO_REQUESTS)
_worker_thread = None
_status = {
    "queued": 0,
    "pending": 0,
    "completed": 0,
    "failed": 0,
    "pools_merged": 0,
    "last_request_at": None,
    "last_success_at": None,
    "last_error": None,
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


def merge_pool_candidates(mint, pairs, source):
    """Merge pool rows by pool address while preserving per-source provenance."""
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
            incoming_sources = set(incoming.get("marketSources", []))
            incoming_sources.add(source)
            if existing is None:
                incoming["marketSources"] = sorted(incoming_sources)
                incoming["poolSourceData"] = {source: copy.deepcopy(pair)}
                rows[address] = incoming
            else:
                current_sources = set(existing.get("marketSources", []))
                current_sources.add(source)
                source_data = dict(existing.get("poolSourceData") or {})
                source_data[source] = copy.deepcopy(pair)
                # DexScreener remains the primary live quote when both providers
                # describe the same pool; Gecko enriches provenance/alternatives.
                if source == "dexscreener" or "dexscreener" not in current_sources:
                    existing.update(incoming)
                existing["marketSources"] = sorted(current_sources)
                existing["poolSourceData"] = source_data
                rows.move_to_end(address)
            merged_count += 1

        ordered = sorted(
            rows.items(),
            key=lambda item: (_pool_liquidity(item[1]), item[0]),
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
    with _lock:
        rows = _candidates.get(mint.strip())
        return [copy.deepcopy(value) for value in rows.values()] if rows else []


def is_dexscreener_list_incomplete(pairs):
    if not isinstance(pairs, list):
        return True
    if len(pairs) >= DEXSCREENER_TOKEN_PAIRS_CAP:
        return True
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        base = pair.get("baseToken")
        quote = pair.get("quoteToken")
        if (isinstance(base, dict) and isinstance(quote, dict)
                and quote.get("address") == "So11111111111111111111111111111111111111112"):
            return False
    return True


def _ensure_worker():
    global _worker_thread
    with _lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(
                target=_worker, name="TrojanGeckoPoolRegistry", daemon=True
            )
            _worker_thread.start()


def enqueue_gecko_pool_fetch(mint):
    if not isinstance(mint, str) or not mint.strip():
        return False
    mint = mint.strip()
    with _lock:
        if mint in _pending:
            return False
        _pending.add(mint)
        _status["queued"] += 1
        _status["pending"] = len(_pending)
    try:
        _queue.put_nowait(mint)
    except queue.Full:
        with _lock:
            _pending.discard(mint)
            _status["pending"] = len(_pending)
            _status["failed"] += 1
            _status["last_error"] = "GeckoTerminal pool queue is full"
        return False
    _ensure_worker()
    return True


def _worker():
    while True:
        mint = _queue.get()
        try:
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
            merge_pool_candidates(mint, normalized, "geckoterminal")
            refreshed = refresh_market_data_from_pool_registry(mint)
            with _lock:
                _status["completed"] += 1
                _status["last_success_at"] = time.time()
                _status["last_error"] = None if normalized else "GeckoTerminal returned no usable pools"
        except Exception as error:
            with _lock:
                _status["failed"] += 1
                _status["last_error"] = str(error)[:200]
        finally:
            with _lock:
                _pending.discard(mint)
                _status["pending"] = len(_pending)
            _queue.task_done()


def get_pool_registry_status():
    with _lock:
        return {
            **_status,
            "token_count": len(_candidates),
            "queue_size": _queue.qsize(),
            "provider": "DexScreener + GeckoTerminal",
            "cache_ttl_seconds": CACHE_TTL_SECONDS,
            "max_pools_per_token": MAX_POOLS_PER_TOKEN,
        }


def clear_pool_registry_state():
    with _lock:
        _candidates.clear()
        _candidate_updated_at.clear()
        _pending.clear()
        for key in _status:
            _status[key] = 0 if key in ("queued", "pending", "completed", "failed", "pools_merged") else None
