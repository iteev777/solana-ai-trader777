# ==========================================
# TROJAN V2 - MARKET DATA
# BATCH API + CACHE + REQUEST DEDUPLICATION
# ==========================================

import time
import copy
import threading
from datetime import datetime
from collections import OrderedDict

from api_client import get_json

GECKO_POOL_MULTI_URL = (
    "https://api.geckoterminal.com/api/v2/networks/solana/pools/multi/"
)


# ==========================================
# CONFIGURATION
# ==========================================

DEXSCREENER_BATCH_URL = (
    "https://api.dexscreener.com/tokens/v1/solana/"
)

MAX_BATCH_SIZE = 30

CACHE_TTL = 10
MAX_CACHE_ENTRIES = 2000

BATCH_WAIT_SECONDS = 0.15

REQUEST_TIMEOUT = 10
MAX_RETRIES = 1

# Do not let slow market endpoints freeze the rapid position monitor.
WAIT_TIMEOUT = 12


# ==========================================
# SHARED STATE
# ==========================================

_cache = OrderedDict()
_cache_lock = threading.Lock()

_batch_condition = threading.Condition()

_pending_addresses = OrderedDict()
_inflight_addresses = {}

_batch_thread = None
_batch_thread_lock = threading.Lock()


# ==========================================
# VALIDATION
# ==========================================

def valid_address(address):

    return (
        isinstance(address, str)
        and bool(address.strip())
        and "," not in address
    )


def safe_float(value):

    if isinstance(value, bool):
        return None

    try:

        number = float(value)

        if number != number or number in (
            float("inf"),
            float("-inf")
        ):
            return None

        return number

    except (
        ValueError,
        TypeError,
        OverflowError
    ):

        return None


# ==========================================
# CACHE
# ==========================================

def get_cached(address):

    with _cache_lock:

        item = _cache.get(address)

        if item is None:
            return None

        if time.monotonic() >= item["expires"]:

            _cache.pop(address, None)

            return None

        _cache.move_to_end(address)

        return copy.deepcopy(item["data"])


def save_cached(address, pair):

    if not valid_address(address):
        return

    if not isinstance(pair, dict):
        return

    with _cache_lock:

        now = time.monotonic()

        expired = [
            key
            for key, item in _cache.items()
            if now >= item["expires"]
        ]

        for key in expired:
            _cache.pop(key, None)

        _cache[address] = {
            "data": copy.deepcopy(pair),
            "expires": now + CACHE_TTL
        }

        _cache.move_to_end(address)

        while len(_cache) > MAX_CACHE_ENTRIES:

            _cache.popitem(last=False)


def clear_market_cache():

    with _cache_lock:
        _cache.clear()

    print(
        "[TROJAN V2] MARKET CACHE CLEARED",
        flush=True
    )


# ==========================================
# PAIR VALIDATION
# ==========================================

def get_pair_address(pair):

    if not isinstance(pair, dict):
        return []

    base_token = pair.get("baseToken")
    quote_token = pair.get("quoteToken")

    addresses = []

    if isinstance(base_token, dict):

        address = base_token.get("address")

        if valid_address(address):
            addresses.append(address)

    if isinstance(quote_token, dict):

        address = quote_token.get("address")

        if valid_address(address):
            addresses.append(address)

    return addresses


def get_liquidity(pair):

    liquidity = pair.get("liquidity")

    if not isinstance(liquidity, dict):
        return -1

    value = safe_float(
        liquidity.get("usd")
    )

    if value is None or value < 0:
        return -1

    return value


def select_best_pairs(pairs, requested_addresses):
    """Delegate to the canonical native-liquidity selector."""
    from pool_selection import select_best_pairs as select_canonical_pairs
    return select_canonical_pairs(pairs, requested_addresses)


# ==========================================
# GECKOTERMINAL POOL NORMALIZATION + FALLBACK
# ==========================================

def _gecko_token_address(token_id):
    if not isinstance(token_id, str):
        return None
    value = token_id.strip()
    if value.startswith("solana_"):
        value = value[len("solana_"):]
    elif value.startswith("solana:"):
        value = value[len("solana:"):]
    return value if valid_address(value) and "_" not in value else None


def normalize_gecko_pool(pool, requested_mint=None):
    """Normalize GeckoTerminal pool data, orienting it to the requested token."""
    if not isinstance(pool, dict):
        return None
    attributes = pool.get("attributes")
    relationships = pool.get("relationships")
    if not isinstance(attributes, dict) or not isinstance(relationships, dict):
        return None
    dex_relation = relationships.get("dex")
    dex_data = dex_relation.get("data") if isinstance(dex_relation, dict) else None
    dex_id = (
        (dex_data.get("id") if isinstance(dex_data, dict) else None)
        or attributes.get("dex_id") or attributes.get("dex")
    )
    base_relation = relationships.get("base_token")
    quote_relation = relationships.get("quote_token")
    base_data = base_relation.get("data") if isinstance(base_relation, dict) else None
    quote_data = quote_relation.get("data") if isinstance(quote_relation, dict) else None
    base_address = _gecko_token_address(base_data.get("id") if isinstance(base_data, dict) else None)
    quote_address = _gecko_token_address(quote_data.get("id") if isinstance(quote_data, dict) else None)
    if not base_address:
        return None

    token_is_base = True
    if requested_mint:
        if requested_mint == base_address:
            token_is_base = True
        elif quote_address and requested_mint == quote_address:
            token_is_base = False
        else:
            return None

    if token_is_base:
        token_address, counter_address = base_address, quote_address
        price = safe_float(attributes.get("base_token_price_usd"))
        price_native = safe_float(attributes.get("base_token_price_native_currency"))
    else:
        token_address, counter_address = quote_address, base_address
        price = safe_float(attributes.get("quote_token_price_usd"))
        price_native = safe_float(attributes.get("quote_token_price_native_currency"))

    reserve = safe_float(attributes.get("reserve_in_usd"))
    if price is None or price <= 0 or reserve is None or reserve < 0:
        return None

    volumes = attributes.get("volume_usd")
    changes = attributes.get("price_change_percentage")
    transactions = attributes.get("transactions")
    volumes = volumes if isinstance(volumes, dict) else {}
    changes = changes if isinstance(changes, dict) else {}
    transactions = transactions if isinstance(transactions, dict) else {}
    txns = {}
    for window in ("m5", "h1", "h24"):
        item = transactions.get(window)
        if isinstance(item, dict):
            txns[window] = {"buys": item.get("buys"), "sells": item.get("sells")}

    created = attributes.get("pool_created_at")
    created_ms = None
    if isinstance(created, str):
        try:
            created_ms = int(datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp() * 1000)
        except (ValueError, TypeError, OverflowError):
            created_ms = None

    return {
        "chainId": "solana",
        "dexId": dex_id or "geckoterminal",
        "poolName": attributes.get("name"),
        "pairAddress": attributes.get("address"),
        "baseToken": {"address": token_address, "name": "Unknown", "symbol": "Unknown"},
        "quoteToken": (
            {"address": counter_address, "name": "Unknown", "symbol": "Unknown"}
            if counter_address else {}
        ),
        "priceUsd": str(price),
        "priceNative": str(price_native) if price_native is not None and price_native > 0 else None,
        "liquidity": {"usd": reserve},
        "volume": {"m5": safe_float(volumes.get("m5")), "h24": safe_float(volumes.get("h24"))},
        "priceChange": {
            "m5": safe_float(changes.get("m5")),
            "h1": safe_float(changes.get("h1")),
            "h24": safe_float(changes.get("h24")),
        } if token_is_base else {},
        "txns": txns,
        "pairCreatedAt": created_ms,
        "fdv": safe_float(attributes.get("fdv_usd")) if token_is_base else None,
        "marketCap": safe_float(attributes.get("market_cap_usd")) if token_is_base else None,
        "marketSource": "geckoterminal",
    }


def get_gecko_pool_data_batch(pool_addresses):
    """Fetch fallback quotes for known GeckoTerminal pool addresses in batches."""
    if not isinstance(pool_addresses, (list, tuple, set)):
        return {}
    unique = list(dict.fromkeys(
        item.strip() for item in pool_addresses
        if isinstance(item, str) and item.strip() and "," not in item
    ))
    results = {}
    for start in range(0, len(unique), 30):
        batch = unique[start:start + 30]
        if not batch:
            continue
        url = GECKO_POOL_MULTI_URL + ",".join(batch)
        try:
            response = get_json(
                url,
                headers={"Accept": "application/json;version=20230203"},
                timeout=10,
                cache_ttl=10,
                max_retries=1
            )
            pools = response.get("data") if isinstance(response, dict) else None
            if not isinstance(pools, list):
                continue
            for pool in pools:
                normalized = normalize_gecko_pool(pool)
                if normalized:
                    results[normalized["baseToken"]["address"]] = normalized
        except Exception as error:
            print("[TROJAN V2] GECKO POOL FALLBACK ERROR:", repr(error), flush=True)
    return results


# ==========================================
# BATCH API REQUEST
# ==========================================

def refresh_market_data_from_pool_registry(address):
    """Re-select and cache a token's canonical pair after asynchronous source enrichment."""
    from pool_registry import get_pool_candidates
    candidates = get_pool_candidates(address)
    selected = select_best_pairs(candidates, [address]).get(address)
    if selected:
        save_cached(address, selected)
    return selected


def fetch_batch(addresses):
    addresses = list(dict.fromkeys(addresses))
    if not addresses:
        return {}

    results = {}
    from pool_registry import (
        enqueue_dex_pool_refresh, get_pool_candidates,
        merge_pool_candidates,
    )

    for start in range(0, len(addresses), MAX_BATCH_SIZE):
        batch = addresses[start:start + MAX_BATCH_SIZE]
        url = DEXSCREENER_BATCH_URL + ",".join(batch)
        try:
            pairs = get_json(
                url,
                timeout=REQUEST_TIMEOUT,
                cache_ttl=0,
                max_retries=MAX_RETRIES
            )
            if not isinstance(pairs, list):
                pairs = []

            # Keep every returned pair for each requested base mint, not just
            # the one chosen for this response. The registry merges sources.
            for address in batch:
                token_pairs = [
                    pair for pair in pairs
                    if isinstance(pair, dict)
                    and isinstance(pair.get("baseToken"), dict)
                    and pair["baseToken"].get("address") == address
                ]
                merge_pool_candidates(address, token_pairs, "dexscreener")
                # Fast batch quotes are supplemented asynchronously by the
                # full per-token token-pairs endpoint (up to 30 pools).
                enqueue_dex_pool_refresh(address)

            combined = []
            for address in batch:
                combined.extend(get_pool_candidates(address))
            selected = select_best_pairs(combined, batch)
            results.update(selected)

            for address, pair in selected.items():
                save_cached(address, pair)

            print(
                "[TROJAN V2] MARKET BATCH COMPLETE",
                "| REQUESTED:", len(batch),
                "| FOUND:", len(selected),
                "| POOL SOURCES: DEXSCREENER + GECKOTERMINAL",
                flush=True
            )
        except Exception as error:
            # If DexScreener is unavailable, still schedule a background
            # GeckoTerminal lookup; never block the trading loop on it.
            for address in batch:
                enqueue_dex_pool_refresh(address)
            print(
                "[TROJAN V2] MARKET BATCH ERROR",
                "| SIZE:", len(batch),
                "| ERROR:", repr(error),
                flush=True
            )
    return results


# ==========================================
# BACKGROUND BATCH WORKER
# ==========================================

def batch_worker():

    while True:

        with _batch_condition:

            while not _pending_addresses:

                _batch_condition.wait()

            deadline = (
                time.monotonic()
                + BATCH_WAIT_SECONDS
            )

            while (
                len(_pending_addresses) < MAX_BATCH_SIZE
            ):

                remaining = (
                    deadline - time.monotonic()
                )

                if remaining <= 0:
                    break

                _batch_condition.wait(
                    timeout=remaining
                )

                if not _pending_addresses:
                    break

            addresses = list(
                _pending_addresses.keys()
            )[:MAX_BATCH_SIZE]

            waiters = {}

            for address in addresses:

                address_waiters = (
                    _pending_addresses.pop(address)
                )

                waiters[address] = address_waiters

                _inflight_addresses[address] = (
                    address_waiters
                )

        # Network request outside the lock.
        try:

            results = fetch_batch(addresses)

        except Exception as error:

            print(
                "[TROJAN V2] BATCH WORKER ERROR:",
                repr(error),
                flush=True
            )

            results = {}

        # Resolve all callers, including callers
        # that joined while the request was running.
        with _batch_condition:

            for address in addresses:

                address_waiters = (
                    _inflight_addresses.pop(
                        address,
                        []
                    )
                )

                result = results.get(address)

                for event, result_holder in (
                    address_waiters
                ):

                    result_holder["data"] = (
                        copy.deepcopy(result)
                        if isinstance(result, dict)
                        else None
                    )

                    event.set()

            _batch_condition.notify_all()


# ==========================================
# WORKER STARTUP
# ==========================================

def start_batch_worker():

    global _batch_thread

    with _batch_thread_lock:

        if (
            _batch_thread is not None
            and _batch_thread.is_alive()
        ):
            return

        _batch_thread = threading.Thread(
            target=batch_worker,
            name="TrojanMarketBatchWorker",
            daemon=True
        )

        _batch_thread.start()

        print(
            "[TROJAN V2] MARKET BATCH WORKER STARTED",
            flush=True
        )


# ==========================================
# SHARED REQUEST QUEUE
# ==========================================

def request_addresses(addresses):

    if not addresses:
        return {}

    start_batch_worker()

    results = {}
    waiters = {}

    # Check cache first.
    missing = []

    for address in addresses:

        cached = get_cached(address)

        if cached is not None:

            results[address] = cached

        else:

            missing.append(address)

    if not missing:
        return results

    # Register all requests together.
    with _batch_condition:

        for address in missing:

            # Another request is already fetching
            # this token. Join that request.
            if address in _inflight_addresses:

                event = threading.Event()

                result_holder = {
                    "data": None
                }

                _inflight_addresses[address].append(
                    (event, result_holder)
                )

            else:

                event = threading.Event()

                result_holder = {
                    "data": None
                }

                if address in _pending_addresses:

                    _pending_addresses[address].append(
                        (event, result_holder)
                    )

                else:

                    _pending_addresses[address] = [
                        (event, result_holder)
                    ]

            waiters[address] = (
                event,
                result_holder
            )

        _batch_condition.notify_all()

    # Use one shared deadline for this call.
    deadline = (
        time.monotonic() + WAIT_TIMEOUT
    )

    for address, (event, result_holder) in (
        waiters.items()
    ):

        remaining = (
            deadline - time.monotonic()
        )

        if remaining > 0:
            event.wait(timeout=remaining)

        if not event.is_set():

            print(
                "[TROJAN V2] MARKET DATA TIMEOUT",
                "| TOKEN:",
                address,
                flush=True
            )

            continue

        result = result_holder.get("data")

        if isinstance(result, dict):
            results[address] = copy.deepcopy(
                result
            )

    return results


# ==========================================
# SINGLE TOKEN REQUEST
# ==========================================

def get_market_data(address):

    if not valid_address(address):
        return None

    address = address.strip()

    results = request_addresses([
        address
    ])

    return results.get(address)


# ==========================================
# MULTIPLE TOKEN REQUEST
# ==========================================

def get_market_data_batch(addresses):

    if not isinstance(
        addresses,
        (list, tuple, set)
    ):
        return {}

    unique_addresses = list(
        dict.fromkeys(
            address.strip()
            for address in addresses
            if valid_address(address)
        )
    )

    if not unique_addresses:
        return {}

    return request_addresses(
        unique_addresses
    )


# ==========================================
# STATUS
# ==========================================

def get_market_data_status():

    with _cache_lock:

        cache_entries = len(_cache)

    with _batch_condition:

        pending_count = len(
            _pending_addresses
        )

        inflight_count = len(
            _inflight_addresses
        )

    with _batch_thread_lock:

        worker_running = (
            _batch_thread is not None
            and _batch_thread.is_alive()
        )

    return {
        "cache_entries": cache_entries,
        "pending_addresses": pending_count,
        "inflight_addresses": inflight_count,
        "max_batch_size": MAX_BATCH_SIZE,
        "cache_ttl": CACHE_TTL,
        "batch_wait_seconds": BATCH_WAIT_SECONDS,
        "worker_running": worker_running,
        "pool_registry": __import__("pool_registry").get_pool_registry_status()
    }


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    start_batch_worker()

    print("================================")
    print("TROJAN V2 MARKET DATA READY")
    print("================================")
    print("BATCH SIZE:", MAX_BATCH_SIZE)
    print("CACHE TTL:", CACHE_TTL)
    print("BATCH WAIT:", BATCH_WAIT_SECONDS)
    print("WAIT TIMEOUT:", WAIT_TIMEOUT)
    print("================================")
