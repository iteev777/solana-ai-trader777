"""Typed classification for quote failures.

Provider text is retained for diagnostics only. Retryability and response status
are derived from exception type and HTTP status, not downstream string matching.
"""
import re

import requests


def _detail(error):
    message = str(error) or type(error).__name__
    response = getattr(error, "response", None)
    body = getattr(response, "text", None)
    if isinstance(body, str) and body.strip():
        message += ": " + body.strip()
    return message[:220]


def _retry_after(response):
    headers = getattr(response, "headers", None)
    try:
        raw = headers.get("Retry-After") if headers is not None else None
    except Exception:
        raw = None
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if value < 0:
        return None
    return min(value, 300.0)


def classify_quote_error(provider, error):
    """Return a stable failure kind and retry policy for a router exception."""
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    detail = _detail(error)
    lowered = detail.lower()

    if isinstance(error, (requests.Timeout, TimeoutError)) or status in (408, 504):
        kind, retryable, http_status = "TIMEOUT", True, 504
        retry_after = 0.5
    elif status == 429:
        kind, retryable, http_status = "RATE_LIMITED", True, 429
        retry_after = _retry_after(response) or 2.0
    elif isinstance(status, int) and status >= 500:
        kind, retryable, http_status = "UNAVAILABLE", True, 503
        retry_after = 0.5
    elif any(term in lowered for term in (
        "not tradable", "untradable", "token has no market", "no market exists"
    )):
        kind, retryable, http_status = "NOT_TRADABLE", False, 422
        retry_after = None
    elif any(term in lowered for term in (
        "no route", "no pool", "no liquidity", "insufficient liquidity",
        "pair not found", "market not found", "route not found"
    )):
        kind, retryable, http_status = "NO_ROUTE", False, 422
        retry_after = None
    elif isinstance(error, ValueError) or status in (400, 404, 422):
        kind, retryable, http_status = "ROUTER_REJECTED", False, 502
        retry_after = None
    elif isinstance(error, (requests.ConnectionError, OSError)):
        kind, retryable, http_status = "UNAVAILABLE", True, 503
        retry_after = 0.5
    else:
        kind, retryable, http_status = "UNAVAILABLE", False, 503
        retry_after = None

    return {
        "provider": str(provider)[:64],
        "kind": kind,
        "detail": detail,
        "retryable": retryable,
        "retry_after_seconds": retry_after,
        "http_status": http_status,
    }
