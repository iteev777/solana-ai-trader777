import unittest
from unittest.mock import patch

import candidate_quality


class CandidateQualityTests(unittest.TestCase):
    def test_scores_known_market_context_and_source_diversity(self):
        token = {
            "tokenAddress": "A" * 32,
            "discovery_sources": ["dex", "gecko", "jupiter", "rugcheck"],
            "market_data": {
                "liquidity": {"usd": 60000},
                "volume": {"m5": 30000, "h1": 50000, "h24": 200000},
                "txns": {"m5": {"buys": 80, "sells": 20}, "h1": {"buys": 400, "sells": 100}},
                "priceChange": {"m5": 3.0, "h1": 5.0, "h24": 12.0},
                "pairCreatedAt": 1760000000000,
            },
        }
        with patch("candidate_quality.time.time", return_value=1760000100):
            result = candidate_quality.score_candidate(token)
        self.assertGreaterEqual(result["score"], 75)
        self.assertEqual(result["inputs"]["source_count"], 4)
        self.assertEqual(result["tier"], "strong")
        self.assertFalse(result["gating_enabled"])

    def test_missing_data_is_explicit_not_fabricated(self):
        result = candidate_quality.score_candidate({})
        self.assertEqual(result["score"], 0)
        self.assertIn("LIQUIDITY_UNKNOWN", result["reasons"])
        self.assertIsNone(result["inputs"]["buy_ratio"])
        self.assertEqual(result["tier"], "weak")
        self.assertFalse(result["hard_reject"])

    def test_zero_liquidity_is_a_hard_reject_but_missing_is_not(self):
        bad = candidate_quality.score_candidate({"market_data": {"liquidity": {"usd": 0}}})
        missing = candidate_quality.score_candidate({})
        self.assertTrue(bad["hard_reject"])
        self.assertIn("ZERO_LIQUIDITY", bad["reasons"])
        self.assertFalse(missing["hard_reject"])

    def test_rank_candidates_prefers_strong_and_enriches_missing_market_data(self):
        tokens = [
            {"tokenAddress": "weak", "discovery_sources": ["dex"]},
            {"tokenAddress": "strong", "discovery_sources": ["dex", "gecko", "jupiter", "rugcheck"]},
        ]
        market = {
            "strong": {
                "liquidity": {"usd": 60000},
                "volume": {"m5": 30000, "h1": 50000, "h24": 200000},
                "txns": {"m5": {"buys": 80, "sells": 20}, "h1": {"buys": 400, "sells": 100}},
                "priceChange": {"m5": 3, "h1": 5},
                "pairCreatedAt": 1760000000000,
            }
        }
        with patch("candidate_quality.time.time", return_value=1760000100):
            ranked = candidate_quality.rank_candidates(tokens, market)
        self.assertEqual(ranked[0]["tokenAddress"], "strong")
        self.assertIn("market_data", ranked[0])
        self.assertIn("candidate_quality", ranked[0])


if __name__ == "__main__":
    unittest.main()
