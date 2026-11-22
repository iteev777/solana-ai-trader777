import unittest
from unittest.mock import patch
import candidate_quality


class CandidateQualityTests(unittest.TestCase):
    def test_scores_known_market_context_and_source_diversity(self):
        token = {
            "discovery_sources": ["dex", "gecko", "jupiter", "rugcheck"],
            "market_data": {
                "liquidity": {"usd": 60000},
                "volume": {"m5": 30000},
                "txns": {"m5": {"buys": 80, "sells": 20}},
                "priceChange": {"m5": 3.0},
                "pairCreatedAt": 1760000000000,
            },
        }
        with patch("candidate_quality.time.time", return_value=1760000100):
            result = candidate_quality.score_candidate(token)
        self.assertGreaterEqual(result["score"], 80)
        self.assertEqual(result["inputs"]["source_count"], 4)
        self.assertFalse(result["gating_enabled"])

    def test_missing_data_is_explicit_not_fabricated(self):
        result = candidate_quality.score_candidate({})
        self.assertEqual(result["score"], 0)
        self.assertIn("LIQUIDITY_UNKNOWN", result["reasons"])
        self.assertIsNone(result["inputs"]["buy_ratio"])


if __name__ == "__main__":
    unittest.main()
