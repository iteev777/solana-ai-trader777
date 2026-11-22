# ==========================================
# TROJAN V2 - MARKET DATA
# BATCH API + CACHE + REQUEST DEDUPLICATION
# ==========================================

import time
import threading
from collections import OrderedDict

from api_client import get_json


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
REQUEST_TIMEOUT = 15
MAX_RETRIES = 1


# ==========================================
# SHARED STATE
# ==========================================

_cache = OrderedDict()
_cache_lock = threading.Lock()

_batch_condition = threading.Condition()

_pending_addresses = OrderedDict()
_pending_results = {}

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

    except (ValueError, TypeError, OverflowError):

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

        return item["data"].copy()


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
            "data": pair.copy(),
            "expires": now + CACHE_TTL
        }

        _cache.move_to_end(address)

        while len(_cache) > MAX_CACHE_ENTRIES:

            _cache.popitem(last=False)


def clear_market_cache():

    with _cache_lock:
        _cache.clear()

    print("[TROJAN V2] MARKET CACHE CLEARED", flush=True)


# ==========================================
# PAIR VALIDATION
# ==========================================

def get_pair_address(pair):

    if not isinstance(pair, dict):
        return None

    base_token = pair.get("baseToken")
    quote_token = pair.get("quoteToken")

    addresses = []

    if isinstance(base_token, dict):

        addresses.append(
            base_token.get("address")
        )

    if isinstance(quote_token, dict):

        addresses.append(
            quote_token.get("address")
        )

    return addresses


def get_liquidity(pair):

    liquidity = pair.get("liquidity")

    if not isinstance(liquidity, dict):
        return -1

    value = safe_float(liquidity.get("usd"))

    if value is None or value < 0:
        return -1

    return value


def select_best_pairs(pairs, requested_addresses):

    if not isinstance(pairs, list):
        return {}

    requested = set(requested_addresses)

    best_pairs = {}
    best_liquidity = {}

    for pair in pairs:

        if not isinstance(pair, dict):
            continue

        price = safe_float(pair.get("priceUsd"))

        if price is None or price <= 0:
            continue

        pair_addresses = get_pair_address(pair)

        if not pair_addresses:
            continue

        liquidity = get_liquidity(pair)

        for address in pair_addresses:

            if address not in requested:
                continue

            previous = best_liquidity.get(address, -1)

            if liquidity > previous:

                best_liquidity[address] = liquidity
                best_pairs[address] = pair.copy()

    return best_pairs


# ==========================================
# BATCH API REQUEST
# ==========================================

def fetch_batch(addresses):

    addresses = list(dict.fromkeys(addresses))

    if not addresses:
        return {}

    results = {}

    for start in range(0, len(addresses), MAX_BATCH_SIZE):

        batch = addresses[
            start:start + MAX_BATCH_SIZE
        ]

        url = (
            DEXSCREENER_BATCH_URL
            + ",".join(batch)
        )

        try:

            pairs = get_json(
                url,
                timeout=REQUEST_TIMEOUT,
                cache_ttl=0,
                max_retries=MAX_RETRIES
            )

            selected = select_best_pairs(
                pairs,
                batch
            )

            results.update(selected)

            for address, pair in selected.items():
                save_cached(address, pair)

            print(
                "[TROJAN V2] MARKET BATCH COMPLETE",
                "| REQUESTED:",
                len(batch),
                "| FOUND:",
                len(selected),
                flush=True
            )

        except Exception as error:

            print(
                "[TROJAN V2] MARKET BATCH ERROR",
                "| SIZE:",
                len(batch),
                "| ERROR:",
                repr(error),
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

            _batch_condition.wait(
                timeout=BATCH_WAIT_SECONDS
            )

            addresses = list(
                _pending_addresses.keys()
            )[:MAX_BATCH_SIZE]

            waiters = {}

            for address in addresses:

                waiters[address] = (
                    _pending_addresses.pop(address)
                )

        results = fetch_batch(addresses)

        for address in addresses:

            result = results.get(address)

            for event, result_holder in waiters[address]:

                result_holder["data"] = (
                    result.copy()
                    if isinstance(result, dict)
                    else None
                )

                event.set()


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
# SINGLE TOKEN REQUEST
# ==========================================

def get_market_data(address):

    if not valid_address(address):
        return None

    address = address.strip()

    cached = get_cached(address)

    if cached is not None:
        return cached

    start_batch_worker()

    event = threading.Event()

    result_holder = {
        "data": None
    }

    with _batch_condition:

        if address in _pending_addresses:

            _pending_addresses[address].append(
                (event, result_holder)
            )

        else:

            _pending_addresses[address] = [
                (event, result_holder)
            ]

        _batch_condition.notify()

    event.wait(
        timeout=REQUEST_TIMEOUT + 10
    )

    if not event.is_set():

        print(
            "[TROJAN V2] MARKET DATA TIMEOUT",
            "| TOKEN:",
            address,
            flush=True
        )

        return None

    result = result_holder.get("data")

    if isinstance(result, dict):
        return result.copy()

    return None


# ==========================================
# MULTIPLE TOKEN REQUEST
# ==========================================

def get_market_data_batch(addresses):

    if not isinstance(addresses, (list, tuple, set)):
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

    results = {}
    missing = []

    for address in unique_addresses:

        cached = get_cached(address)

        if cached is not None:

            results[address] = cached

        else:

            missing.append(address)

    if missing:

        fetched = fetch_batch(missing)

        results.update(fetched)

    return results


# ==========================================
# STATUS
# ==========================================

def get_market_data_status():

    with _cache_lock:

        cache_entries = len(_cache)

    with _batch_condition:

        pending_count = len(_pending_addresses)

    return {
        "cache_entries": cache_entries,
        "pending_addresses": pending_count,
        "max_batch_size": MAX_BATCH_SIZE,
        "cache_ttl": CACHE_TTL,
        "worker_running": (
            _batch_thread is not None
            and _batch_thread.is_alive()
        )
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
    print("================================")
