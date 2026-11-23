# ==========================================
# TROJAN V2 - API CLIENT
# ENDPOINT RATE LIMITS + HOST LOCKING
# CACHE + RETRIES + COOLDOWN
# ==========================================

import time
import json
import copy
import hashlib
import threading
import requests

from urllib.parse import urlparse
from email.utils import parsedate_to_datetime


# ==========================================
# API RATE LIMITS
# ==========================================

HOST_INTERVALS = {
    "api.dexscreener.com": 1.1,
    "public-api.birdeye.so": 3.0,
    "api.geckoterminal.com": 7.0,
    "api.rugcheck.xyz": 1.0,  # ScreenerBot parity: 60 requests/minute
    "lite-api.jup.ag": 1.2,
    "api.jup.ag": 1.2,
    "api.coingecko.com": 1.5,
    "api.llama.fi": 2.0,
}

ENDPOINT_INTERVALS = {
    ("api.dexscreener.com", "tokens_batch"): 0.25,
    # Separate discovery budgets: profiles and boosts each have a 60/min lane.
    ("api.dexscreener.com", "dexscreener_profiles"): 1.1,
    ("api.dexscreener.com", "dexscreener_boosts"): 1.1,
}

DEFAULT_429_COOLDOWN = 30
MAX_429_COOLDOWN = 300

DEFAULT_TIMEOUT = 15
MAX_CACHE_ENTRIES = 1000

SLOW_REQUEST_THRESHOLD = 2.0


# ==========================================
# SHARED STATE
# ==========================================

host_locks = {}
bucket_next_request = {}
host_blocked_until = {}

cache = {}
_api_stats = {}

cache_lock = threading.Lock()
state_lock = threading.Lock()


# ==========================================
# ENDPOINT CLASSIFICATION
# ==========================================

def get_rate_bucket(url):

    parsed = urlparse(url)

    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()

    if host == "api.dexscreener.com" and path.startswith("/tokens/v1/"):
        endpoint = "tokens_batch"
    elif host == "api.dexscreener.com" and path.startswith("/token-profiles/latest/"):
        endpoint = "dexscreener_profiles"
    elif host == "api.dexscreener.com" and path.startswith("/token-boosts/"):
        endpoint = "dexscreener_boosts"
    else:
        endpoint = "default"

    interval = ENDPOINT_INTERVALS.get(
        (host, endpoint),
        HOST_INTERVALS.get(host, 2.0)
    )

    return host, endpoint, interval


# ==========================================
# HOST LOCK
# ==========================================

def get_host_lock(host, endpoint="default"):

    # Separate locks allow independent provider budgets to run concurrently.
    # Calls sharing the same endpoint bucket remain serialized and rate-limited.
    key = (host, endpoint)

    with state_lock:

        if key not in host_locks:
            host_locks[key] = threading.Lock()

        return host_locks[key]


# ==========================================
# CACHE
# ==========================================

def cache_key(url, params, headers):

    safe_headers = {}

    for key, value in (headers or {}).items():

        value_hash = hashlib.sha256(
            str(value).encode("utf-8")
        ).hexdigest()

        safe_headers[str(key).lower()] = value_hash

    return json.dumps(
        {
            "url": url,
            "params": params or {},
            "headers": safe_headers
        },
        sort_keys=True,
        default=str
    )


def get_cached(key):

    with cache_lock:

        cached = cache.get(key)

        if cached is None:
            return None

        if time.time() >= cached["expires"]:

            cache.pop(key, None)

            return None

        return copy.deepcopy(cached["data"])


def save_cache(key, data, cache_ttl):

    if cache_ttl <= 0:
        return

    with cache_lock:

        now = time.time()

        expired = [
            item_key
            for item_key, item in cache.items()
            if now >= item["expires"]
        ]

        for item_key in expired:
            cache.pop(item_key, None)

        while len(cache) >= MAX_CACHE_ENTRIES:

            oldest_key = next(iter(cache))

            cache.pop(oldest_key, None)

        cache[key] = {
            "data": copy.deepcopy(data),
            "expires": now + cache_ttl
        }


# ==========================================
# RETRY DELAY
# ==========================================

def get_retry_delay(response, attempt):

    retry_after = response.headers.get(
        "Retry-After"
    )

    if retry_after:

        try:

            delay = float(retry_after)

        except (ValueError, TypeError):

            try:

                retry_date = parsedate_to_datetime(
                    retry_after
                )

                delay = (
                    retry_date.timestamp()
                    - time.time()
                )

            except (
                ValueError,
                TypeError,
                OverflowError
            ):

                delay = (
                    DEFAULT_429_COOLDOWN
                    * (2 ** attempt)
                )

    else:

        delay = (
            DEFAULT_429_COOLDOWN
            * (2 ** attempt)
        )

    return min(
        max(delay, 1),
        MAX_429_COOLDOWN
    )


# ==========================================
# RATE LIMIT WAIT
# ==========================================

def wait_for_request_slot(
    host,
    endpoint,
    interval
):

    while True:

        with state_lock:

            now = time.time()

            blocked_until = host_blocked_until.get(
                host,
                0
            )

            next_request = bucket_next_request.get(
                (host, endpoint),
                0
            )

            wait_time = max(
                blocked_until - now,
                next_request - now,
                0
            )

            if wait_time <= 0:

                bucket_next_request[
                    (host, endpoint)
                ] = now + interval

                return

        print(
            "API WAIT:",
            host,
            "| ENDPOINT:",
            endpoint,
            "| TIME:",
            round(wait_time, 1),
            "seconds",
            flush=True
        )

        time.sleep(wait_time)


# ==========================================
# API REQUEST
# ==========================================

def _get_json_impl(
    url,
    params=None,
    headers=None,
    timeout=DEFAULT_TIMEOUT,
    cache_ttl=0,
    max_retries=1
):

    host, endpoint, interval = get_rate_bucket(
        url
    )

    if not host:
        raise ValueError("Invalid API URL")

    max_retries = max(
        0,
        int(max_retries)
    )

    key = cache_key(
        url,
        params,
        headers
    )

    # First cache check
    if cache_ttl > 0:

        cached_data = get_cached(key)

        if cached_data is not None:
            return cached_data

    # One request at a time per host.
    # This also makes host cooldown reliable
    # across different endpoint buckets.
    lock = get_host_lock(host, endpoint)

    with lock:

        # Second cache check
        if cache_ttl > 0:

            cached_data = get_cached(key)

            if cached_data is not None:
                return cached_data

        attempt = 0

        while True:

            # Always re-check cooldown and rate
            # limits immediately before a request.
            wait_for_request_slot(
                host,
                endpoint,
                interval
            )

            request_started = time.monotonic()

            try:

                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout
                )

            except requests.RequestException as error:

                elapsed = (
                    time.monotonic()
                    - request_started
                )

                print(
                    "API CONNECTION ERROR:",
                    host,
                    "| TIME:",
                    round(elapsed, 2),
                    "s",
                    "| ERROR:",
                    repr(error),
                    flush=True
                )

                if attempt >= max_retries:
                    raise

                delay = min(
                    2 ** (attempt + 1),
                    MAX_429_COOLDOWN
                )

                attempt += 1

                print(
                    "API RETRY:",
                    host,
                    "| WAIT:",
                    delay,
                    "seconds",
                    flush=True
                )

                time.sleep(delay)

                continue

            elapsed = (
                time.monotonic()
                - request_started
            )

            # ----------------------------------
            # RATE LIMIT
            # ----------------------------------

            if response.status_code == 429:

                cooldown = get_retry_delay(
                    response,
                    attempt
                )

                with state_lock:

                    host_blocked_until[host] = max(
                        host_blocked_until.get(host, 0),
                        time.time() + cooldown
                    )

                print(
                    "API RATE LIMIT:",
                    host,
                    "| WAIT:",
                    cooldown,
                    "seconds",
                    flush=True
                )

                if attempt < max_retries:

                    attempt += 1

                    continue

                response.raise_for_status()

            # ----------------------------------
            # SERVER ERRORS
            # ----------------------------------

            if response.status_code in (
                500,
                502,
                503,
                504
            ):

                print(
                    "API SERVER ERROR:",
                    host,
                    "| STATUS:",
                    response.status_code,
                    "| TIME:",
                    round(elapsed, 2),
                    "s",
                    flush=True
                )

                if attempt < max_retries:

                    delay = min(
                        2 ** (attempt + 1),
                        MAX_429_COOLDOWN
                    )

                    attempt += 1

                    print(
                        "API RETRY:",
                        host,
                        "| WAIT:",
                        delay,
                        "seconds",
                        flush=True
                    )

                    time.sleep(delay)

                    continue

            # ----------------------------------
            # HTTP STATUS
            # ----------------------------------

            response.raise_for_status()

            # ----------------------------------
            # RESPONSE TIME
            # ----------------------------------

            if elapsed >= SLOW_REQUEST_THRESHOLD:

                print(
                    "API SLOW RESPONSE:",
                    host,
                    "| STATUS:",
                    response.status_code,
                    "| TIME:",
                    round(elapsed, 2),
                    "s",
                    flush=True
                )

            # ----------------------------------
            # JSON
            # ----------------------------------

            try:

                data = response.json()

            except ValueError:

                print(
                    "API JSON ERROR:",
                    host,
                    "| TIME:",
                    round(elapsed, 2),
                    "s",
                    flush=True
                )

                raise

            save_cache(
                key,
                data,
                cache_ttl
            )

            return data


def _record_api_stat(host, endpoint, *, success=None, elapsed_ms=0.0, error=None, cache_hit=False, cache_miss=False):
    key = (host, endpoint)
    with state_lock:
        stat = _api_stats.setdefault(key, {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "last_request_time": None,
            "last_success_time": None,
            "last_error_time": None,
            "last_error_message": None,
            "average_response_time_ms": 0.0,
        })
        if cache_hit:
            stat["cache_hits"] += 1
            return
        stat["total_requests"] += 1
        if cache_miss:
            stat["cache_misses"] += 1
        stat["last_request_time"] = time.time()
        total = stat["total_requests"]
        stat["average_response_time_ms"] += (
            elapsed_ms - stat["average_response_time_ms"]
        ) / total
        if success:
            stat["successful_requests"] += 1
            stat["last_success_time"] = time.time()
        else:
            stat["failed_requests"] += 1
            stat["last_error_time"] = time.time()
            stat["last_error_message"] = str(error)[:300] if error else "Unknown API error"


def get_json(
    url,
    params=None,
    headers=None,
    timeout=DEFAULT_TIMEOUT,
    cache_ttl=0,
    max_retries=1
):
    """Fetch JSON and expose thread-safe per-host/endpoint request statistics."""
    host, endpoint, _interval = get_rate_bucket(url)
    if not host:
        raise ValueError("Invalid API URL")

    key = cache_key(url, params, headers)
    if cache_ttl > 0:
        cached_data = get_cached(key)
        if cached_data is not None:
            _record_api_stat(host, endpoint, cache_hit=True)
            return cached_data

    started = time.perf_counter()
    try:
        result = _get_json_impl(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            cache_ttl=cache_ttl,
            max_retries=max_retries,
        )
    except Exception as error:
        _record_api_stat(
            host, endpoint, success=False,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            error=error,
            cache_miss=cache_ttl > 0,
        )
        raise

    _record_api_stat(
        host, endpoint, success=True,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        cache_miss=cache_ttl > 0,
    )
    return result


# ==========================================
# CACHE CONTROL
# ==========================================

def clear_cache():

    with cache_lock:
        cache.clear()

    print(
        "TROJAN V2 API CACHE CLEARED",
        flush=True
    )


# ==========================================
# API STATUS
# ==========================================

def get_api_status():

    now = time.time()

    with state_lock:

        hosts = sorted({host for host, _endpoint in host_locks})

        bucket_data = dict(
            bucket_next_request
        )

        blocked_data = dict(
            host_blocked_until
        )

    status = {}

    for host in hosts:

        host_buckets = {}

        for (bucket_host, endpoint), next_time in (
            bucket_data.items()
        ):

            if bucket_host != host:
                continue

            host_buckets[endpoint] = {
                "next_request_in": round(
                    max(next_time - now, 0),
                    1
                )
            }

        status[host] = {
            "next_request_in": max(
                (
                    item["next_request_in"]
                    for item in host_buckets.values()
                ),
                default=0
            ),
            "blocked_for": round(
                max(
                    blocked_data.get(host, 0) - now,
                    0
                ),
                1
            ),
            "buckets": host_buckets
        }

    with cache_lock:
        cache_entries = len(cache)

    with state_lock:
        stats = {}
        for key, value in _api_stats.items():
            host, endpoint = key
            item = dict(value)
            total = item["total_requests"]
            item["success_rate"] = (
                round(item["successful_requests"] / total * 100.0, 2)
                if total else 0.0
            )
            cache_total = item["cache_hits"] + item["cache_misses"]
            item["cache_hit_rate"] = (
                round(item["cache_hits"] / cache_total * 100.0, 2)
                if cache_total else 0.0
            )
            stats.setdefault(host, {})[endpoint] = item

    return {
        "hosts": status,
        "stats": stats,
        "cache_entries": cache_entries
    }


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    print("================================")
    print("TROJAN V2 API CLIENT READY")
    print("================================")

    for host, interval in HOST_INTERVALS.items():

        print(
            "API INTERVAL:",
            host,
            interval,
            "seconds"
        )

    for (host, endpoint), interval in (
        ENDPOINT_INTERVALS.items()
    ):

        print(
            "API ENDPOINT INTERVAL:",
            host,
            endpoint,
            interval,
            "seconds"
        )

    print(
        "SLOW REQUEST THRESHOLD:",
        SLOW_REQUEST_THRESHOLD,
        "seconds"
    )

    print(
        "429 COOLDOWN:",
        DEFAULT_429_COOLDOWN
    )

    print(
        "MAX CACHE ENTRIES:",
        MAX_CACHE_ENTRIES
    )
