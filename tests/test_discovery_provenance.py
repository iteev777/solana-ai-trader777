import unittest
from unittest.mock import patch

import candidate_quality


class IndependentDiscoveryEvidenceTests(unittest.TestCase):
    def test_multiple_feeds_from_one_provider_count_once(self):
        report = candidate_quality.score_candidate({
            "discovery_sources": [
                "geckoterminal_new_pools",
                "geckoterminal_trending_pools",
                "geckoterminal_token_pools",
            ]
        })
        self.assertEqual(report["inputs"]["source_count"], 3)
        self.assertEqual(report["inputs"]["independent_source_count"], 1)
        self.assertEqual(report["inputs"]["source_families"], ["geckoterminal"])
        self.assertIn("SINGLE_PROVIDER_ONLY", report["reasons"])

    def test_distinct_provider_families_receive_consensus_credit(self):
        report = candidate_quality.score_candidate({
            "discovery_sources": [
                "geckoterminal_new_pools",
                "dexscreener_boosts",
                "birdeye_meme_listing",
                "rugcheck_trending",
            ]
        })
        self.assertEqual(report["inputs"]["independent_source_count"], 4)
        self.assertNotIn("SINGLE_PROVIDER_ONLY", report["reasons"])

    def test_future_pool_timestamp_is_hard_reject(self):
        with patch("candidate_quality.time.time", return_value=1_800_000_000):
            report = candidate_quality.score_candidate({
                "market_data": {"pairCreatedAt": 1_800_000_000_000 + 120_000}
            })
        self.assertTrue(report["hard_reject"])
        self.assertEqual(report["tier"], "reject")
        self.assertIn("FUTURE_POOL_TIMESTAMP", report["reasons"])

    def test_negative_liquidity_and_volume_are_hard_rejects(self):
        report = candidate_quality.score_candidate({
            "market_data": {
                "liquidity": {"usd": -1},
                "volume": {"m5": -5},
            }
        })
        self.assertTrue(report["hard_reject"])
        self.assertIn("INVALID_LIQUIDITY", report["reasons"])
        self.assertIn("INVALID_VOLUME_5M", report["reasons"])

    def test_non_integer_transaction_counts_are_hard_rejects(self):
        report = candidate_quality.score_candidate({
            "market_data": {"txns": {"m5": {"buys": 1.5, "sells": 2}}}
        })
        self.assertTrue(report["hard_reject"])
        self.assertIn("NON_INTEGER_BUYS_5M", report["reasons"])

    def test_ranking_uses_independent_provider_count_as_tie_breaker(self):
        candidates = [
            {"tokenAddress": "same-provider", "discovery_sources": [
                "geckoterminal_new_pools", "geckoterminal_trending_pools"
            ]},
            {"tokenAddress": "independent", "discovery_sources": [
                "geckoterminal_new_pools", "dexscreener", "birdeye"
            ]},
        ]
        ranked = candidate_quality.rank_candidates(candidates)
        self.assertEqual(ranked[0]["tokenAddress"], "independent")


if __name__ == "__main__":
    unittest.main()
