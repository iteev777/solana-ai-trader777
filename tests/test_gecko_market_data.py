import unittest
from unittest.mock import patch

from market_data import get_gecko_pool_data_batch, normalize_gecko_pool


class GeckoPoolNormalizationTests(unittest.TestCase):
    def test_normalizes_gecko_pool_to_trojan_market_shape(self):
        pool = {
            "attributes": {
                "address": "POOL123",
                "base_token_price_usd": "0.00025",
                "reserve_in_usd": "42000",
                "volume_usd": {"m5": "900", "h6": "7000", "h24": "18000"},
                "price_change_percentage": {"m5": "1.2", "h1": "-2.0", "h6": "4.0", "h24": "5.0"},
                "transactions": {
                    "m5": {"buys": 12, "sells": 4},
                    "h1": {"buys": 40, "sells": 20},
                    "h6": {"buys": 120, "sells": 60}
                },
                "pool_created_at": "2026-11-22T12:00:00Z"
            },
            "relationships": {
                "base_token": {"data": {"id": "solana_MINT123"}}
            }
        }
        result = normalize_gecko_pool(pool)
        self.assertIsNotNone(result)
        self.assertEqual(result["baseToken"]["address"], "MINT123")
        self.assertEqual(float(result["priceUsd"]), 0.00025)
        self.assertEqual(result["liquidity"]["usd"], 42000)
        self.assertEqual(result["txns"]["m5"]["buys"], 12)
        self.assertEqual(result["volume"]["h6"], 7000)
        self.assertEqual(result["priceChange"]["h6"], 4)
        self.assertEqual(result["txns"]["h6"]["buys"], 120)
        self.assertEqual(result["marketSource"], "geckoterminal")

    def test_quote_side_omits_base_side_directional_metrics(self):
        pool = {
            "attributes": {"address": "POOL", "quote_token_price_usd": "2",
                           "reserve_in_usd": "100", "transactions": {"m5": {"buys": 5, "sells": 1}}},
            "relationships": {"base_token": {"data": {"id": "solana_BASE"}},
                              "quote_token": {"data": {"id": "solana_QUOTE"}}},
        }
        result = normalize_gecko_pool(pool, "QUOTE")
        self.assertEqual(result["priceUsd"], "2")
        self.assertEqual(result["txns"], {})
        self.assertEqual(result["priceChange"], {})

    def test_batch_ignores_pool_outside_requested_set(self):
        pool = {
            "attributes": {"address": "OTHER", "base_token_price_usd": "1", "reserve_in_usd": "100"},
            "relationships": {"base_token": {"data": {"id": "solana_BASE"}}},
        }
        with patch("market_data.get_json", return_value={"data": [pool]}):
            self.assertEqual(get_gecko_pool_data_batch(["EXPECTED"]), {})
            self.assertIn("BASE", get_gecko_pool_data_batch(["OTHER"]))

    def test_rejects_pool_without_base_token_relationship(self):
        result = normalize_gecko_pool({"attributes": {"base_token_price_usd": "1"}})
        self.assertIsNone(result)

    def test_rejects_invalid_or_zero_price(self):
        pool = {
            "attributes": {"base_token_price_usd": "0", "reserve_in_usd": "100"},
            "relationships": {"base_token": {"data": {"id": "solana_MINT123"}}}
        }
        self.assertIsNone(normalize_gecko_pool(pool))


if __name__ == "__main__":
    unittest.main()
