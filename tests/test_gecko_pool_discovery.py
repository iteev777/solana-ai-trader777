import unittest
from unittest.mock import patch

from discovery import gecko_scanner


BASE_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
QUOTE_MINT = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"


class GeckoPoolDiscoveryTests(unittest.TestCase):
    def test_discovers_both_pool_sides_but_prices_only_the_base_token(self):
        pool = {
            "attributes": {"address": "POOL_ADDRESS", "name": "BASE / QUOTE"},
            "relationships": {
                "base_token": {"data": {"id": "solana_" + BASE_MINT}},
                "quote_token": {"data": {"id": "solana_" + QUOTE_MINT}},
            },
        }
        snapshot = {"baseToken": {"address": BASE_MINT}, "priceUsd": "0.25"}
        with patch.object(gecko_scanner, "get_json", return_value={"data": [pool]}), \
             patch.object(gecko_scanner, "normalize_gecko_pool", return_value=snapshot):
            result = gecko_scanner.scan_new_pools()

        by_mint = {item["tokenAddress"]: item for item in result}
        self.assertEqual(set(by_mint), {BASE_MINT, QUOTE_MINT})
        self.assertEqual(by_mint[BASE_MINT]["market_data"], snapshot)
        self.assertNotIn("market_data", by_mint[QUOTE_MINT])
        self.assertEqual(by_mint[BASE_MINT]["poolAddress"], "POOL_ADDRESS")
        self.assertEqual(by_mint[QUOTE_MINT]["poolAddress"], "POOL_ADDRESS")

    def test_deduplicates_token_that_appears_on_both_pool_sides(self):
        pool = {
            "attributes": {"address": "POOL_ADDRESS", "name": "SAME / SAME"},
            "relationships": {
                "base_token": {"data": {"id": "solana_" + BASE_MINT}},
                "quote_token": {"data": {"id": "solana_" + BASE_MINT}},
            },
        }
        with patch.object(gecko_scanner, "get_json", return_value={"data": [pool]}), \
             patch.object(gecko_scanner, "normalize_gecko_pool", return_value=None):
            result = gecko_scanner.scan_new_pools()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tokenAddress"], BASE_MINT)


if __name__ == "__main__":
    unittest.main()
