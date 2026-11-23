import os
import tempfile
import unittest
from unittest.mock import patch

from discovery import token_aggregator as aggregator


class TokenAggregatorMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_db.cleanup)
        self.db_patch = patch.object(
            aggregator.database, "DATABASE",
            os.path.join(self.temp_db.name, "discovery-health.sqlite3"),
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.old_scanners = aggregator.SCANNERS
        self.old_stats = aggregator._last_source_stats
        self.old_history = aggregator._source_history
        self.old_last_scan = aggregator._last_scan_at
        aggregator._source_history = {}
        aggregator._last_source_stats = {}
        aggregator._last_scan_at = None

    def tearDown(self):
        aggregator.SCANNERS = self.old_scanners
        aggregator._last_source_stats = self.old_stats
        aggregator._source_history = self.old_history
        aggregator._last_scan_at = self.old_last_scan

    def test_source_health_survives_process_local_state_reset(self):
        aggregator.SCANNERS = [
            ("geckoterminal_new_pools", lambda: []),
            ("pumpfun", lambda: []),
        ]
        aggregator.collect_tokens()
        aggregator._last_source_stats = {}
        aggregator._last_scan_at = None
        status = aggregator.get_discovery_status()
        self.assertEqual(status["health_model"], "persistent-sqlite-per-feed-success-failure-latency")
        self.assertIsNotNone(status["last_scan_at"])
        self.assertEqual({item["name"] for item in status["sources"]}, {
            "geckoterminal_new_pools", "pumpfun"
        })
        self.assertTrue(all("count" in item and "ok" in item for item in status["sources"]))

    def test_pumpfun_is_counted_as_its_own_provider_family(self):
        self.assertEqual(aggregator._provider_family("pumpfun"), "pumpfun")
        self.assertEqual(aggregator._provider_family("pumpfun_curve"), "pumpfun")

    def test_later_sources_fill_missing_and_zero_market_data_without_erasing_good_values(self):
        mint = "So11111111111111111111111111111111111111112"
        first = {
            "tokenAddress": mint, "chainId": "solana",
            "name": "Unknown", "symbol": "TEST",
            "priceUsd": 0, "liquidity": {"usd": 0},
            "volume": {"m5": 0, "h1": None},
            "discovery_sources": ["geckoterminal_new_pools"],
        }
        second = {
            "tokenAddress": mint, "chainId": "solana",
            "name": "Test Token", "symbol": "TEST",
            "priceUsd": 0.025, "liquidity": {"usd": 12_500},
            "volume": {"m5": 120, "h1": 850},
            "website": "https://example.invalid",
            "discovery_sources": ["pumpfun"],
        }
        aggregator.SCANNERS = [
            ("geckoterminal_new_pools", lambda: [first]),
            ("pumpfun", lambda: [second]),
        ]
        with patch.object(aggregator, "MAX_RESULTS", 10):
            results = aggregator.collect_tokens()
        self.assertEqual(len(results), 1)
        token = results[0]
        self.assertEqual(token["name"], "Test Token")
        self.assertEqual(token["priceUsd"], 0.025)
        self.assertEqual(token["liquidity"]["usd"], 12_500)
        self.assertEqual(token["volume"], {"m5": 120, "h1": 850})
        self.assertEqual(token["website"], "https://example.invalid")
        self.assertEqual(set(token["discovery_sources"]), {
            "geckoterminal_new_pools", "pumpfun"
        })
        status = aggregator.get_discovery_status()
        self.assertEqual(status["independent_provider_count"], 2)


if __name__ == "__main__":
    unittest.main()
