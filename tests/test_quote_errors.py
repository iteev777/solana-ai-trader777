import unittest
from unittest.mock import Mock

import requests

from quote_errors import classify_quote_error
import route_comparison


class QuoteErrorClassificationTests(unittest.TestCase):
    def test_timeout_is_retryable_with_gateway_timeout_status(self):
        result = classify_quote_error("jupiter", requests.Timeout("deadline exceeded"))
        self.assertEqual(result["kind"], "TIMEOUT")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["http_status"], 504)

    def test_rate_limit_preserves_bounded_retry_after(self):
        response = Mock(status_code=429, headers={"Retry-After": "8"}, text="slow down")
        result = classify_quote_error("raptor", requests.HTTPError("429", response=response))
        self.assertEqual(result["kind"], "RATE_LIMITED")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["retry_after_seconds"], 8.0)
        self.assertEqual(result["http_status"], 429)

    def test_no_route_is_not_retryable_and_does_not_mean_token_untradable(self):
        response = Mock(status_code=400, headers={}, text="No route found for this amount")
        result = classify_quote_error("raptor", requests.HTTPError("400", response=response))
        self.assertEqual(result["kind"], "NO_ROUTE")
        self.assertFalse(result["retryable"])
        self.assertEqual(result["http_status"], 422)

    def test_durable_not_tradable_verdict_is_separate_from_no_route(self):
        response = Mock(status_code=404, headers={}, text="Token is not tradable")
        result = classify_quote_error("jupiter", requests.HTTPError("404", response=response))
        self.assertEqual(result["kind"], "NOT_TRADABLE")
        self.assertFalse(result["retryable"])

    def test_invalid_quote_payload_is_router_rejected(self):
        result = classify_quote_error("direct_pool", ValueError("minimum output exceeds quoted output"))
        self.assertEqual(result["kind"], "ROUTER_REJECTED")
        self.assertFalse(result["retryable"])
        self.assertEqual(result["http_status"], 502)

    def test_provider_server_error_is_retryable_unavailable(self):
        response = Mock(status_code=503, headers={}, text="maintenance")
        result = classify_quote_error("raptor", requests.HTTPError("503", response=response))
        self.assertEqual(result["kind"], "UNAVAILABLE")
        self.assertTrue(result["retryable"])

    def test_route_failure_payload_exposes_typed_fields(self):
        result = route_comparison._failed("jupiter", requests.Timeout("slow"))
        self.assertEqual(result["status"], "TIMEOUT")
        self.assertEqual(result["error"]["kind"], "TIMEOUT")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["http_status"], 504)


if __name__ == "__main__":
    unittest.main()
