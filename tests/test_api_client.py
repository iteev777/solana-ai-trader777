import unittest
from unittest.mock import Mock, patch

import api_client
import requests


class ApiClientTests(unittest.TestCase):
    def setUp(self):
        for name in ("cache", "_request_stats", "host_locks", "bucket_next_request", "host_blocked_until"):
            self.enterContext(patch.object(api_client, name, {}))

    def test_json_null_is_cached_without_a_second_request(self):
        response = Mock(status_code=200)
        response.json.return_value = None
        with patch.object(api_client.requests, "get", return_value=response) as fetch:
            for _ in range(2):
                self.assertIsNone(api_client.get_json("https://provider.example/stats", cache_ttl=60))
        fetch.assert_called_once()
        stats = api_client.get_api_status()["hosts"]["provider.example"]["requests"]
        self.assertEqual(stats["successful_requests"], 1)
        self.assertEqual(stats["failed_requests"], 0)

    def test_failed_attempt_records_error_class_without_request_details(self):
        with patch.object(api_client.requests, "get", side_effect=requests.Timeout("secret query")):
            with self.assertRaises(requests.Timeout):
                api_client.get_json("https://provider.example/stats", max_retries=0)
        stats = api_client.get_api_status()["hosts"]["provider.example"]["requests"]
        self.assertEqual(stats["failed_requests"], 1)
        self.assertEqual(stats["last_error"], "Timeout")

    def test_nonfinite_retry_after_uses_finite_backoff(self):
        for raw in ("NaN", "inf", "-inf"):
            self.assertEqual(api_client.get_retry_delay(Mock(headers={"Retry-After": raw}), 0),
                             api_client.DEFAULT_429_COOLDOWN)


if __name__ == "__main__":
    unittest.main()
