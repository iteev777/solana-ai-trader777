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
    def test_feed_failure_is_reported_to_aggregator(self, get_json):
        get_json.side_effect = RuntimeError("profiles unavailable")
        with self.assertRaisesRegex(RuntimeError, "profiles unavailable"):
            scanner.scan_profiles()

    @patch("discovery.dexscreener_scanner.get_json")
    def test_invalid_payload_is_reported_as_source_failure(self, get_json):
        get_json.return_value = {"unexpected": "object"}
        with self.assertRaisesRegex(ValueError, "JSON array"):
            scanner.scan_profiles()

    @patch("discovery.dexscreener_scanner.get_json")
    def test_legacy_wrapper_continues_after_one_feed_failure(self, get_json):
        get_json.side_effect = [
            RuntimeError("profiles unavailable"),
            [{"chainId": "solana", "tokenAddress": "MintLatest"}],
            [{"chainId": "solana", "tokenAddress": "MintTop"}],
        ]
        result = scanner.scan_dexscreener()
        self.assertEqual(
            {item["tokenAddress"] for item in result},
            {"MintLatest", "MintTop"},
        )

    @patch("discovery.dexscreener_scanner.get_json")
    def test_feed_does_not_truncate_provider_results_at_100(self, get_json):
        get_json.return_value = [
            {"chainId": "solana", "tokenAddress": f"Mint{i}"}
            for i in range(125)
        ]
        self.assertEqual(len(scanner.scan_profiles()), 125)

    def test_aggregator_registers_each_dexscreener_feed_as_own_task(self):
        registered = {
            name: function for name, function in token_aggregator.SCANNERS
            if name.startswith("dexscreener_")
        }
        self.assertEqual(
            set(registered),
            {"dexscreener_profiles", "dexscreener_latest_boosts", "dexscreener_top_boosts"},
        )
        self.assertIs(registered["dexscreener_profiles"], scanner.scan_profiles)
        self.assertIs(registered["dexscreener_latest_boosts"], scanner.scan_latest_boosts)
        self.assertIs(registered["dexscreener_top_boosts"], scanner.scan_top_boosts)


if __name__ == "__main__":
    unittest.main()
