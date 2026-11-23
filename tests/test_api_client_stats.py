import unittest
from unittest.mock import patch

import api_client


class ApiClientStatsTests(unittest.TestCase):
    def setUp(self):
        with api_client.cache_lock:
            api_client.cache.clear()
        with api_client.state_lock:
            api_client._api_stats.clear()

    def test_records_success_and_cache_hit_per_host_and_endpoint(self):
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        payload = [{"tokenAddress": "Mint111"}]

        def fake_fetch(url, params=None, headers=None, timeout=15, cache_ttl=0, max_retries=1):
            key = api_client.cache_key(url, params, headers)
            api_client.save_cache(key, payload, cache_ttl)
            return payload

        with patch.object(api_client, "_get_json_impl", side_effect=fake_fetch):
            first = api_client.get_json(url, cache_ttl=60)
            second = api_client.get_json(url, cache_ttl=60)

        self.assertEqual(first, payload)
        self.assertEqual(second, payload)
        stats = api_client.get_api_status()["stats"]["api.dexscreener.com"]["dexscreener_profiles"]
        self.assertEqual(stats["total_requests"], 1)
        self.assertEqual(stats["successful_requests"], 1)
        self.assertEqual(stats["failed_requests"], 0)
        self.assertEqual(stats["cache_misses"], 1)
        self.assertEqual(stats["cache_hits"], 1)
        self.assertEqual(stats["success_rate"], 100.0)
        self.assertEqual(stats["cache_hit_rate"], 50.0)

    def test_records_failed_request_and_error_message(self):
        url = "https://api.rugcheck.xyz/v1/tokens/Mint111/report"
        with patch.object(api_client, "_get_json_impl", side_effect=TimeoutError("provider timeout")):
            with self.assertRaisesRegex(TimeoutError, "provider timeout"):
                api_client.get_json(url, cache_ttl=0)

        stats = api_client.get_api_status()["stats"]["api.rugcheck.xyz"]["default"]
        self.assertEqual(stats["total_requests"], 1)
        self.assertEqual(stats["successful_requests"], 0)
        self.assertEqual(stats["failed_requests"], 1)
        self.assertEqual(stats["cache_misses"], 0)
        self.assertIn("provider timeout", stats["last_error_message"])
        self.assertIsNotNone(stats["last_error_time"])

    def test_endpoint_stats_are_separated_for_one_provider(self):
        profile_url = "https://api.dexscreener.com/token-profiles/latest/v1"
        boost_url = "https://api.dexscreener.com/token-boosts/latest/v1"
        with patch.object(api_client, "_get_json_impl", return_value=[]):
            api_client.get_json(profile_url)
            api_client.get_json(boost_url)
        stats = api_client.get_api_status()["stats"]["api.dexscreener.com"]
        self.assertEqual(stats["dexscreener_profiles"]["total_requests"], 1)
        self.assertEqual(stats["dexscreener_boosts"]["total_requests"], 1)


if __name__ == "__main__":
    unittest.main()
