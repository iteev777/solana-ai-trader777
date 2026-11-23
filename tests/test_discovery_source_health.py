import unittest
from unittest.mock import patch

import discovery.token_aggregator as aggregator


class DiscoverySourceHealthTests(unittest.TestCase):
    def setUp(self):
        aggregator._last_source_stats = {}
        aggregator._source_history.clear()
        aggregator._last_scan_at = None

    def test_reports_latency_and_success_failure_history(self):
        def good():
            return [{"tokenAddress": "Mint111111111111111111111111111111111", "chainId": "solana"}]

        def bad():
            raise RuntimeError("provider timeout")

        with patch.object(aggregator, "SCANNERS", [
            ("geckoterminal_new_pools", good),
            ("birdeye", bad),
        ]):
            aggregator.collect_tokens()
            status = aggregator.get_discovery_status()

        self.assertEqual(status["source_count"], 2)
        self.assertEqual(status["independent_provider_count"], 2)
        stats = {item["name"]: item for item in status["sources"]}
        self.assertTrue(stats["geckoterminal_new_pools"]["ok"])
        self.assertGreaterEqual(stats["geckoterminal_new_pools"]["latency_ms"], 0)
        self.assertEqual(stats["geckoterminal_new_pools"]["successes"], 1)
        self.assertFalse(stats["birdeye"]["ok"])
        self.assertEqual(stats["birdeye"]["consecutive_failures"], 1)
        self.assertIn("provider timeout", stats["birdeye"]["error"])

    def test_non_list_provider_payload_is_a_failure(self):
        with patch.object(aggregator, "SCANNERS", [("jupiter", lambda: {"bad": "shape"})]):
            aggregator.collect_tokens()
            status = aggregator.get_discovery_status()
        self.assertFalse(status["sources"][0]["ok"])
        self.assertIn("non-list", status["sources"][0]["error"])

    def test_duplicate_feeds_from_one_vendor_do_not_outrank_two_independent_vendors(self):
        shared = {"tokenAddress": "Shared1111111111111111111111111111111", "chainId": "solana"}
        independent = {"tokenAddress": "Indep11111111111111111111111111111111", "chainId": "solana"}
        with patch.object(aggregator, "SCANNERS", [
            ("geckoterminal_new_pools", lambda: [shared]),
            ("geckoterminal_trending_pools", lambda: [shared]),
            ("birdeye", lambda: [independent]),
            ("jupiter", lambda: [independent]),
        ]):
            result = aggregator.collect_tokens()
        self.assertEqual(result[0]["tokenAddress"], "Indep11111111111111111111111111111111")
        self.assertEqual(result[1]["tokenAddress"], "Shared1111111111111111111111111111111")


if __name__ == "__main__":
    unittest.main()
