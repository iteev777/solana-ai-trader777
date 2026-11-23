"""Read-only Solana RPC transport with endpoint failover and 429 backoff.

Supports SOLANA_RPC_URL plus comma-separated SOLANA_RPC_URLS. Optional
provider-specific variables: HELIUS_RPC_URL, QUICKNODE_RPC_URL,
TRITON_RPC_URL and ALCHEMY_RPC_URL. Endpoint URLs are never logged or exposed.
"""
import os
import json
import subprocess
import threading
import time
from urllib.parse import urlparse

import requests

_TIMEOUT = float(os.getenv("SOLANA_RPC_TIMEOUT", "8"))
_MIN_INTERVAL = float(os.getenv("SOLANA_RPC_MIN_INTERVAL", "0.12"))
_MAX_COOLDOWN = 60.0
_guard = threading.RLock()
_endpoint_locks = {}
_state = {}
_cursor = 0

_rust_sidecars = {}
_rust_sidecar_locks = {}
_RUST_SIDECAR_BIN = os.getenv("TROJAN_RUST_RPC_BIN", "").strip()


def _rust_rpc_call(url, method, params):
    """Call the optional persistent Rust sidecar for batched account reads."""
    if not _RUST_SIDECAR_BIN or method != "getMultipleAccounts":
        raise RuntimeError("Rust RPC sidecar is not enabled for this method")
    with _guard:
        process = _rust_sidecars.get(url)
        lock = _rust_sidecar_locks.setdefault(url, threading.Lock())
        if process is None or process.poll() is not None:
            process = subprocess.Popen(
                [_RUST_SIDECAR_BIN], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1,
            )
            _rust_sidecars[url] = process
    with lock:
        if process.poll() is not None:
            raise RuntimeError("Rust RPC sidecar exited")
        request = json.dumps(
            {"url": url, "method": method, "params": params},
            separators=(",", ":"),
        )
        process.stdin.write(request + "\\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            raise RuntimeError("Rust RPC sidecar closed its output")
        payload = json.loads(line)
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            message = payload.get("error", "Rust RPC sidecar request failed")
            raise RuntimeError(str(message)[:200])
        return payload.get("result")


def _urls():
    raw = []
    raw.extend(os.getenv("SOLANA_RPC_URLS", "").split(","))
    raw.append(os.getenv("SOLANA_RPC_URL", ""))
    for name in ("HELIUS_RPC_URL", "QUICKNODE_RPC_URL", "TRITON_RPC_URL", "ALCHEMY_RPC_URL"):
        raw.append(os.getenv(name, ""))
    result, seen = [], set()
    for item in raw:
        url = item.strip()
        if url and url not in seen and url.startswith(("https://", "http://")):
            seen.add(url)
            result.append(url)
    return result


def _host(url):
    return (urlparse(url).hostname or "unknown").lower()


def _lock_for(url):
    with _guard:
        if url not in _endpoint_locks:
            _endpoint_locks[url] = threading.Lock()
        return _endpoint_locks[url]


def _entry(url):
    with _guard:
        return _state.setdefault(url, {
            "cooldown_until": 0.0,
            "next_request": 0.0,
            "successes": 0,
            "failures": 0,
            "rate_limits": 0,
            "last_latency_ms": None,
            "last_error": None,
        })


def _retry_after(response):
    value = response.headers.get("Retry-After")
    try:
        return min(_MAX_COOLDOWN, max(1.0, float(value)))
    except (TypeError, ValueError):
        return 5.0


def rpc_call(method, params):
    global _cursor
    endpoints = _urls()
    if not endpoints:
        raise RuntimeError("No Solana RPC endpoint configured; set SOLANA_RPC_URL or SOLANA_RPC_URLS")

    with _guard:
        start = _cursor % len(endpoints)
        _cursor = (_cursor + 1) % max(1, len(endpoints))
    ordered = endpoints[start:] + endpoints[:start]
    errors = []

    for url in ordered:
        state = _entry(url)
        with _lock_for(url):
            now = time.monotonic()
            cooldown = max(0.0, state["cooldown_until"] - now)
            if cooldown > 0:
                errors.append(_host(url) + " cooling down")
                continue
            wait = max(0.0, state["next_request"] - now)
            if wait:
                time.sleep(wait)
            started = time.monotonic()
            try:
                if _RUST_SIDECAR_BIN and method == "getMultipleAccounts":
                    try:
                        payload_result = _rust_rpc_call(url, method, params)
                        state["successes"] += 1
                        state["last_latency_ms"] = round((time.monotonic() - started) * 1000, 1)
                        state["last_error"] = None
                        state["next_request"] = time.monotonic() + _MIN_INTERVAL
                        return payload_result
                    except Exception as rust_error:
                        # Preserve the normal endpoint failover path if the helper is unhealthy.
                        state["last_error"] = "Rust sidecar fallback: " + type(rust_error).__name__
                response = requests.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    timeout=_TIMEOUT,
                )
                if response.status_code == 429:
                    delay = _retry_after(response)
                    state["cooldown_until"] = time.monotonic() + delay
                    state["rate_limits"] += 1
                    state["failures"] += 1
                    state["last_error"] = "HTTP 429; cooldown " + str(round(delay, 1)) + "s"
                    errors.append(_host(url) + " rate limited")
                    continue
                if response.status_code in (500, 502, 503, 504):
                    state["cooldown_until"] = time.monotonic() + 2.0
                    state["failures"] += 1
                    state["last_error"] = "HTTP " + str(response.status_code)
                    errors.append(_host(url) + " server error")
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Invalid JSON-RPC response")
                rpc_error = payload.get("error")
                if rpc_error:
                    code = rpc_error.get("code") if isinstance(rpc_error, dict) else None
                    message = str(rpc_error)[:180]
                    if code in (-32005, 429) or "rate limit" in message.lower():
                        state["cooldown_until"] = time.monotonic() + 5.0
                        state["rate_limits"] += 1
                    state["failures"] += 1
                    state["last_error"] = "JSON-RPC error " + str(code)
                    errors.append(_host(url) + " JSON-RPC error " + str(code))
                    continue
                state["successes"] += 1
                state["last_latency_ms"] = round((time.monotonic() - started) * 1000, 1)
                state["last_error"] = None
                state["next_request"] = time.monotonic() + _MIN_INTERVAL
                return payload.get("result")
            except Exception as error:
                state["failures"] += 1
                state["last_error"] = type(error).__name__
                state["next_request"] = time.monotonic() + _MIN_INTERVAL
                errors.append(_host(url) + " " + type(error).__name__)

    raise RuntimeError("All configured Solana RPC endpoints failed: " + "; ".join(errors[:6]))


def get_rpc_status():
    endpoints = _urls()
    rows = []
    now = time.monotonic()
    for url in endpoints:
        state = _entry(url)
        rows.append({
            "host": _host(url),
            "cooldown_seconds": round(max(0.0, state["cooldown_until"] - now), 1),
            "successes": state["successes"],
            "failures": state["failures"],
            "rate_limits": state["rate_limits"],
            "last_latency_ms": state["last_latency_ms"],
            "last_error": state["last_error"],
        })
    return {"configured": bool(endpoints), "endpoint_count": len(endpoints), "endpoints": rows}
