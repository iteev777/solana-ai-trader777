import unittest
from unittest.mock import patch

from discovery import dexscreener_scanner as scanner
from discovery import token_aggregator


class DexScreenerDiscoveryTests(unittest.TestCase):
    @patch("discovery.dexscreener_scanner.get_json")
    def test_profiles_feed_filters_solana_and_deduplicates(self, get_json):
        get_json.return_value = [
            {"chainId": "solana", "tokenAddress": "MintA", "url": "https://dexscreener.com/solana/a"},
            {"chainId": "ethereum", "tokenAddress": "EthMint"},
            {"chainId": "solana", "tokenAddress": "MintA", "icon": "duplicate"},
            {"chainId": "solana", "tokenAddress": "MintB", "description": "Token B"},
        ]
        result = scanner.scan_profiles()
        self.assertEqual([x["tokenAddress"] for x in result], ["MintA", "MintB"])
        self.assertEqual(result[0]["discovery_sources"], ["dexscreener_profiles"])
        self.assertEqual(result[0]["dexscreener_url"], "https://dexscreener.com/solana/a")
        self.assertEqual(result[1]["name"], "Token B")

    @patch("discovery.dexscreener_scanner.get_json")
    def test_latest_boosts_feed_is_independent_and_preserves_amount(self, get_json):
        get_json.return_value = [
            {"chainId": "solana", "tokenAddress": "MintA", "totalAmount": 25},
            {"chainId": "ethereum", "tokenAddress": "EthMint", "totalAmount": 99},
        ]
        result = scanner.scan_latest_boosts()
        self.assertEqual([x["tokenAddress"] for x in result], ["MintA"])
        self.assertEqual(result[0]["boost_amount"], 25)
        self.assertEqual(result[0]["discovery_sources"], ["dexscreener_latest_boosts"])
        get_json.assert_called_once_with(
            scanner.LATEST_BOOSTS_URL, timeout=10, cache_ttl=20, max_retries=1
        )

    @patch("discovery.dexscreener_scanner.get_json")
    def test_top_boosts_requests_solana_filter_from_api(self, get_json):
        get_json.return_value = [
            {"chainId": "solana", "tokenAddress": "MintTop", "amount": 8}
        ]
        result = scanner.scan_top_boosts()
        self.assertEqual([x["tokenAddress"] for x in result], ["MintTop"])
        self.assertEqual(result[0]["boost_amount"], 8)
        get_json.assert_called_once_with(
            scanner.TOP_BOOSTS_URL,
            timeout=10,
            cache_ttl=20,
            max_retries=1,
            params={"chainId": "solana"},
        )

    @patch("discovery.dexscreener_scanner.get_json")
    def test_feed_failure_isolated_from_other_feed(self, get_json):
        get_json.side_effect = RuntimeError("profiles unavailable")
        self.assertEqual(scanner.scan_profiles(), [])

    @patch("discovery.dexscreener_scanner.get_json")
    def test_feed_does_not_truncate_valid_provider_results_at_100(self, get_json):
        get_json.return_value = [
            {"chainId": "solana", "tokenAddress": f"Mint{i}"}
            for i in range(125)
        ]
        self.assertEqual(len(scanner.scan_profiles()), 125)

    @patch("discovery.dexscreener_scanner.get_json")
    def test_legacy_combined_wrapper_merges_all_three_sources(self, get_json):
        get_json.side_effect = [
            [{"chainId": "solana", "tokenAddress": "MintA", "url": "profile"}],
            [{"chainId": "solana", "tokenAddress": "MintA", "totalAmount": 5}],
            [{"chainId": "solana", "tokenAddress": "MintB", "amount": 2}],
        ]
        result = scanner.scan_dexscreener()
        by_address = {item["tokenAddress"]: item for item in result}
        self.assertEqual(set(by_address), {"MintA", "MintB"})
        self.assertEqual(
            set(by_address["MintA"]["discovery_sources"]),
            {"dexscreener_profiles", "dexscreener_latest_boosts"},
        )

    def test_aggregator_registers_each_dexscreener_feed_as_own_task(self):
        registered = {
            name: function for name, function in token_aggregator.SCANNERS
            if name.startswith("dexscreener_")
        }
        self.assertEqual(
            set(registered),
            {
                "dexscreener_profiles",
                "dexscreener_latest_boosts",
                "dexscreener_top_boosts",
            },
        )
        self.assertIs(registered["dexscreener_profiles"], scanner.scan_profiles)
        self.assertIs(registered["dexscreener_latest_boosts"], scanner.scan_latest_boosts)
        self.assertIs(registered["dexscreener_top_boosts"], scanner.scan_top_boosts)


if __name__ == "__main__":
    unittest.main()
