import unittest

from market_data import normalize_gecko_pool


class GeckoPoolNormalizationTests(unittest.TestCase):
    def test_normalizes_gecko_pool_to_trojan_market_shape(self):
        pool = {
            "attributes": {
                "address": "POOL123",
                "base_token_price_usd": "0.00025",
                "reserve_in_usd": "42000",
                "volume_usd": {"m5": "900", "h24": "18000"},
                "price_change_percentage": {"m5": "1.2", "h1": "-2.0", "h24": "5.0"},
                "transactions": {
                    "m5": {"buys": 12, "sells": 4},
                    "h1": {"buys": 40, "sells": 20}
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
        self.assertEqual(result["marketSource"], "geckoterminal")

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
