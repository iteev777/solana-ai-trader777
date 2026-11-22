# ==========================================
# TROJAN V2 - API CLIENT
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
    "api.dexscreener.com": 1.0,
    "public-api.birdeye.so": 3.0,
    "api.geckoterminal.com": 7.0,
    "api.rugcheck.xyz": 2.0,
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
host_next_request = {}
host_blocked_until = {}

cache = {}

cache_lock = threading.Lock()
locks_lock = threading.Lock()


# ==========================================
# HOST LOCK
# ==========================================

def get_host_lock(host):

    with locks_lock:

        if host not in host_locks:
            host_locks[host] = threading.Lock()

        return host_locks[host]


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

    retry_after = response.headers.get("Retry-After")

    if retry_after:

        try:

            delay = float(retry_after)

        except (ValueError, TypeError):

            try:

                retry_date = parsedate_to_datetime(retry_after)

                delay = retry_date.timestamp() - time.time()

            except (ValueError, TypeError, OverflowError):

                delay = DEFAULT_429_COOLDOWN * (2 ** attempt)

    else:

        delay = DEFAULT_429_COOLDOWN * (2 ** attempt)

    return min(
        max(delay, 1),
        MAX_429_COOLDOWN
    )


# ==========================================
# API REQUEST
# ==========================================

def get_json(
    url,
    params=None,
    headers=None,
    timeout=DEFAULT_TIMEOUT,
    cache_ttl=0,
    max_retries=1
):

    host = urlparse(url).hostname

    if not host:
        raise ValueError("Invalid API URL")

    host = host.lower()

    max_retries = max(0, int(max_retries))

    interval = HOST_INTERVALS.get(host, 2.0)

    key = cache_key(url, params, headers)

    # First cache check
    if cache_ttl > 0:

        cached_data = get_cached(key)

        if cached_data is not None:
            return cached_data

    lock = get_host_lock(host)

    with lock:

        # Second cache check
        if cache_ttl > 0:

            cached_data = get_cached(key)

            if cached_data is not None:
                return cached_data

        attempt = 0

        while True:

            now = time.time()

            blocked_until = host_blocked_until.get(host, 0)

            next_request = host_next_request.get(host, 0)

            wait_time = max(
                blocked_until - now,
                next_request - now,
                0
            )

            if wait_time > 0:

                print(
                    "API WAIT:",
                    host,
                    round(wait_time, 1),
                    "seconds"
                )

                time.sleep(wait_time)

            # Reserve next request slot
            host_next_request[host] = time.time() + interval

            request_started = time.monotonic()

            try:

                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout
                )

            except requests.RequestException as error:

                elapsed = time.monotonic() - request_started

                print(
                    "API CONNECTION ERROR:",
                    host,
                    "| TIME:",
                    round(elapsed, 2),
                    "s",
                    "| ERROR:",
                    repr(error)
                )

                if attempt >= max_retries:
                    raise

                delay = min(
                    2 ** (attempt + 1),
                    MAX_429_COOLDOWN
                )

                print(
                    "API RETRY:",
                    host,
                    "| WAIT:",
                    delay,
                    "seconds"
                )

                attempt += 1

                time.sleep(delay)

                continue

            elapsed = time.monotonic() - request_started

            # ----------------------------------
            # RATE LIMIT
            # ----------------------------------

            if response.status_code == 429:

                cooldown = get_retry_delay(
                    response,
                    attempt
                )

                host_blocked_until[host] = (
                    time.time() + cooldown
                )

                print(
                    "API RATE LIMIT:",
                    host,
                    "| WAIT:",
                    cooldown,
                    "seconds"
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
                    "s"
                )

                if attempt < max_retries:

                    delay = min(
                        2 ** (attempt + 1),
                        MAX_429_COOLDOWN
                    )

                    print(
                        "API RETRY:",
                        host,
                        "| WAIT:",
                        delay,
                        "seconds"
                    )

                    attempt += 1

                    time.sleep(delay)

                    continue

            # ----------------------------------
            # HTTP STATUS
            # ----------------------------------

            response.raise_for_status()

            # ----------------------------------
            # RESPONSE TIME DIAGNOSTICS
            # ----------------------------------

            if elapsed >= SLOW_REQUEST_THRESHOLD:

                print(
                    "API SLOW RESPONSE:",
                    host,
                    "| STATUS:",
                    response.status_code,
                    "| TIME:",
                    round(elapsed, 2),
                    "s"
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
                    "s"
                )

                raise

            save_cache(
                key,
                data,
                cache_ttl
            )

            return data


# ==========================================
# CACHE CONTROL
# ==========================================

def clear_cache():

    with cache_lock:
        cache.clear()

    print("TROJAN V2 API CACHE CLEARED")


# ==========================================
# API STATUS
# ==========================================

def get_api_status():

    now = time.time()

    with locks_lock:
        hosts = list(host_locks.keys())

    status = {}

    for host in hosts:

        lock = get_host_lock(host)

        with lock:

            status[host] = {
                "next_request_in": round(
                    max(
                        host_next_request.get(host, 0) - now,
                        0
                    ),
                    1
                ),
                "blocked_for": round(
                    max(
                        host_blocked_until.get(host, 0) - now,
                        0
                    ),
                    1
                )
            }

    with cache_lock:
        cache_entries = len(cache)

    return {
        "hosts": status,
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
