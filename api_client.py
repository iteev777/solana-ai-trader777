import time
import json
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

# Shared cooldown after HTTP 429.
DEFAULT_429_COOLDOWN = 30
MAX_429_COOLDOWN = 300

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

    return json.dumps(
        {
            "url": url,
            "params": params or {},
            "headers": headers or {}
        },
        sort_keys=True,
        default=str
    )


def get_json(
    url,
    params=None,
    headers=None,
    timeout=15,
    cache_ttl=0,
    max_retries=1
):

    host = urlparse(url).hostname

    if not host:
        raise ValueError("Invalid API URL")

    interval = HOST_INTERVALS.get(host, 2.0)

    key = cache_key(url, params, headers)

    # Return cached response if still valid.
    if cache_ttl > 0:

        with cache_lock:

            cached = cache.get(key)

            if cached and time.time() < cached["expires"]:
                return cached["data"]

    lock = get_host_lock(host)

    with lock:

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

                    retry_after = response.headers.get(
                        "Retry-After"
                    )

                    try:
                        cooldown = float(retry_after)
                    except (ValueError, TypeError):
                        cooldown = (
                            DEFAULT_429_COOLDOWN
                            * (2 ** attempt)
                        )

                    cooldown = min(
                        max(cooldown, DEFAULT_429_COOLDOWN),
                        MAX_429_COOLDOWN
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

                response.raise_for_status()

                data = response.json()

                if cache_ttl > 0:

                    with cache_lock:

                        cache[key] = {
                            "data": data,
                            "expires": time.time() + cache_ttl
                        }

                return data

            except requests.RequestException:
                raise

            except ValueError:
                print("API JSON ERROR:", host)
                raise


def clear_cache():

    with cache_lock:
        cache.clear()


if __name__ == "__main__":

    print("API CLIENT READY")
    print("DEXSCREENER INTERVAL:", HOST_INTERVALS[
        "api.dexscreener.com"
    ])
    print("BIRDEYE INTERVAL:", HOST_INTERVALS[
        "public-api.birdeye.so"
    ])
    print("GECKOTERMINAL INTERVAL:", HOST_INTERVALS[
        "api.geckoterminal.com"
    ])
