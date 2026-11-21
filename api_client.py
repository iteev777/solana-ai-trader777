import time
import json
import copy
import threading
import requests

from urllib.parse import urlparse


# Minimum time between requests to each service.
HOST_INTERVALS = {
    "api.dexscreener.com": 1.5,
    "public-api.birdeye.so": 3.0,
    "api.geckoterminal.com": 7.0,
    "api.rugcheck.xyz": 2.0,
}

DEFAULT_429_COOLDOWN = 30
MAX_429_COOLDOWN = 300

DEFAULT_TIMEOUT = 15
MAX_CACHE_ENTRIES = 1000

host_locks = {}
host_next_request = {}
host_blocked_until = {}

cache = {}

cache_lock = threading.Lock()
locks_lock = threading.Lock()


def get_host_lock(host):

    with locks_lock:

        if host not in host_locks:
            host_locks[host] = threading.Lock()

        return host_locks[host]


def cache_key(url, params, headers):

    safe_headers = {}

    for key, value in (headers or {}).items():

        # Authentication headers are included in the key
        # so different API accounts never share cached responses.
        safe_headers[str(key).lower()] = str(value)

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

        if not cached:
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

        # Remove expired entries.
        expired = [
            item_key
            for item_key, item in cache.items()
            if now >= item["expires"]
        ]

        for item_key in expired:
            cache.pop(item_key, None)

        # Keep memory usage bounded.
        while len(cache) >= MAX_CACHE_ENTRIES:
            oldest_key = next(iter(cache))
            cache.pop(oldest_key, None)

        cache[key] = {
            "data": copy.deepcopy(data),
            "expires": now + cache_ttl
        }


def get_retry_delay(response, attempt):

    retry_after = response.headers.get("Retry-After")

    try:
        delay = float(retry_after)

    except (ValueError, TypeError):
        delay = DEFAULT_429_COOLDOWN * (2 ** attempt)

    return min(
        max(delay, 1),
        MAX_429_COOLDOWN
    )


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

    if max_retries < 0:
        max_retries = 0

    interval = HOST_INTERVALS.get(host, 2.0)

    key = cache_key(url, params, headers)

    # Check cache before making a request.
    if cache_ttl > 0:

        cached_data = get_cached(key)

        if cached_data is not None:
            return cached_data

    lock = get_host_lock(host)

    with lock:

        # Check cache again after acquiring the lock.
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

            host_next_request[host] = time.time() + interval

            try:

                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout
                )

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

                # Retry temporary server errors.
                if response.status_code in (500, 502, 503, 504):

                    if attempt < max_retries:

                        delay = min(
                            2 ** (attempt + 1),
                            MAX_429_COOLDOWN
                        )

                        print(
                            "API SERVER ERROR:",
                            host,
                            "| RETRY IN:",
                            delay,
                            "seconds"
                        )

                        attempt += 1
                        time.sleep(delay)
                        continue

                response.raise_for_status()

                data = response.json()

                save_cache(key, data, cache_ttl)

                return data

            except requests.RequestException as error:

                if attempt < max_retries:

                    delay = min(
                        2 ** (attempt + 1),
                        MAX_429_COOLDOWN
                    )

                    print(
                        "API REQUEST ERROR:",
                        host,
                        "| RETRY IN:",
                        delay,
                        "seconds",
                        "| ERROR:",
                        repr(error)
                    )

                    attempt += 1
                    time.sleep(delay)
                    continue

                raise

            except ValueError:

                print("API JSON ERROR:", host)
                raise


def clear_cache():

    with cache_lock:
        cache.clear()

    print("API CACHE CLEARED")


def get_api_status():

    now = time.time()

    with locks_lock:

        hosts = list(host_locks.keys())

    status = {}

    for host in hosts:

        status[host] = {
            "next_request_in": round(
                max(host_next_request.get(host, 0) - now, 0),
                1
            ),
            "blocked_for": round(
                max(host_blocked_until.get(host, 0) - now, 0),
                1
            )
        }

    with cache_lock:
        cache_entries = len(cache)

    return {
        "hosts": status,
        "cache_entries": cache_entries
    }


if __name__ == "__main__":

    print("TROJAN API CLIENT V2 READY")

    for host, interval in HOST_INTERVALS.items():
        print("API INTERVAL:", host, interval)

    print("429 COOLDOWN:", DEFAULT_429_COOLDOWN)
    print("MAX CACHE ENTRIES:", MAX_CACHE_ENTRIES)
