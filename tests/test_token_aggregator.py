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

    def test_jupiter_endpoints_are_independent_sources(self):
        names = [name for name, _ in aggregator.SCANNERS]
        self.assertEqual([name for name in names if name.startswith("jupiter_")], [
            "jupiter_recent", "jupiter_organic", "jupiter_traded", "jupiter_trending"
        ])

    def test_jupiter_provider_and_each_endpoint_can_be_disabled(self):
        with patch.dict(os.environ, {"TROJAN_JUPITER_ENABLED": "false"}, clear=True):
            self.assertEqual(aggregator._jupiter_scanners_from_env(), [])
        with patch.dict(os.environ, {"TROJAN_JUPITER_TRENDING_ENABLED": "0"}, clear=True):
            names = [name for name, _ in aggregator._jupiter_scanners_from_env()]
        self.assertEqual(names, ["jupiter_recent", "jupiter_organic", "jupiter_traded"])

    def test_coingecko_and_defillama_have_independent_switches(self):
        with patch.dict(os.environ, {"TROJAN_COINGECKO_ENABLED": "false"}, clear=True):
            self.assertEqual(aggregator._coingecko_scanners_from_env(), [])
        with patch.dict(os.environ, {"TROJAN_DEFILLAMA_PROTOCOLS_ENABLED": "false"}, clear=True):
            self.assertEqual(aggregator._defillama_scanners_from_env(), [])
    def test_pumpfun_is_counted_as_its_own_provider_family(self):
        self.assertEqual(aggregator._provider_family("pumpfun"), "pumpfun")
        self.assertEqual(aggregator._provider_family("pumpfun_curve"), "pumpfun")

    def test_discovered_candidates_are_persisted_with_source_provenance(self):
        mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
        token = {
            "tokenAddress": mint, "chainId": "solana",
            "name": "Persisted Token", "symbol": "PERSIST", "decimals": 9,
            "discovery_sources": ["jupiter_recent", "rugcheck_new"],
        }
        aggregator.SCANNERS = [("jupiter_recent", lambda: [token])]
        aggregator.collect_tokens()
        with aggregator.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM discovery_candidates WHERE token_address = ?", (mint,)
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "Persisted Token")
        self.assertEqual(row["decimals"], 9)
        self.assertEqual(set(__import__("json").loads(row["discovery_sources"])), {
            "jupiter_recent", "rugcheck_new"
        })
        self.assertEqual(__import__("json").loads(row["payload_json"])["symbol"], "PERSIST")

    def test_candidate_persistence_preserves_first_seen_and_increments_observations(self):
        mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
        token = {"tokenAddress": mint, "chainId": "solana", "symbol": "PERSIST",
                 "discovery_sources": ["jupiter_recent"]}
        aggregator._persist_discovered_candidates([token])
        with aggregator.database.connect() as connection:
            first = connection.execute(
                "SELECT first_seen_at, discovery_count FROM discovery_candidates WHERE token_address = ?", (mint,)
            ).fetchone()
        aggregator._persist_discovered_candidates([dict(token, discovery_sources=["rugcheck_new"])])
        with aggregator.database.connect() as connection:
            second = connection.execute(
                "SELECT first_seen_at, discovery_count, discovery_sources FROM discovery_candidates WHERE token_address = ?", (mint,)
            ).fetchone()
        self.assertEqual(first["first_seen_at"], second["first_seen_at"])
        self.assertEqual(second["discovery_count"], 2)
        self.assertEqual(set(__import__("json").loads(second["discovery_sources"])),
                         {"jupiter_recent", "rugcheck_new"})

    def test_source_health_resets_consecutive_failures_after_recovery(self):
        aggregator._source_history = {}
        aggregator._source_result("jupiter_recent", False, 0, __import__("time").perf_counter(), "timeout")
        recovered = aggregator._source_result("jupiter_recent", True, 1, __import__("time").perf_counter())
        self.assertEqual(recovered["consecutive_failures"], 0)
        self.assertEqual(recovered["successes"], 1)
        self.assertEqual(recovered["failures"], 1)
    def test_later_sources_fill_missing_and_zero_market_data_without_erasing_good_values(self):
        mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
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
