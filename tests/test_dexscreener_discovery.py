import unittest
from unittest.mock import patch

import scanner
from discovery.dexscreener_scanner import scan_dexscreener


class DexScreenerDiscoveryTests(unittest.TestCase):
    def test_parallel_scanner_uses_canonical_sources_and_valid_mints(self):
        mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
        payload = {
            "dexscreener_profiles": [
                {"chainId": "solana", "tokenAddress": mint, "name": ""},
                {"chainId": "solana", "tokenAddress": "z" * 44},
            ],
            "dexscreener_latest_boosts": [
                {"chainId": "solana", "tokenAddress": mint, "name": "Candidate"},
            ],
        }
        with (
            patch.object(scanner, "_last_scan_time", 0),
            patch.object(scanner, "fetch_source", side_effect=lambda name, url: payload.get(name, [])),
        ):
            result = scanner.scan_tokens()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Candidate")
        self.assertEqual(result[0]["discovery_sources"],
                         ["dexscreener_profiles", "dexscreener_latest_boosts"])

    @patch("discovery.dexscreener_scanner.get_json")
    def test_only_solana_tokens_are_returned_and_sources_are_merged(self, get_json):
        get_json.side_effect = [
            [
                {"chainId": "solana", "tokenAddress": "MintA", "url": "https://dexscreener.com/solana/a"},
                {"chainId": "ethereum", "tokenAddress": "EthMint"}
            ],
            [
                {"chainId": "solana", "tokenAddress": "MintA", "totalAmount": 5},
                {"chainId": "solana", "tokenAddress": "MintB"}
            ],
            []
        ]
        result = scan_dexscreener()
        by_address = {item["tokenAddress"]: item for item in result}
        self.assertEqual(set(by_address), {"MintA", "MintB"})
        self.assertEqual(len(by_address["MintA"]["discovery_sources"]), 2)
        self.assertEqual(by_address["MintA"]["chainId"], "solana")

    @patch("discovery.dexscreener_scanner.get_json")
    def test_later_feed_fills_missing_metadata_and_preserves_zero(self, get_json):
        get_json.side_effect = [
            [{"chainId": "solana", "tokenAddress": "MintA"}],
            [{"chainId": "solana", "tokenAddress": "MintA", "symbol": "ABC",
              "totalAmount": 0, "amount": 5}],
            [{"chainId": "solana", "tokenAddress": "MintA", "totalAmount": 19}],
        ]
        token, = scan_dexscreener()
        self.assertEqual(token["symbol"], "ABC")
        self.assertEqual(token["boost_amount"], 0)

    @patch("discovery.dexscreener_scanner.get_json")
    def test_provider_failure_does_not_abort_other_feeds(self, get_json):
        get_json.side_effect = [
            RuntimeError("profiles unavailable"),
            [{"chainId": "solana", "tokenAddress": "MintC"}],
            []
        ]
        result = scan_dexscreener()
        self.assertEqual([item["tokenAddress"] for item in result], ["MintC"])


    @patch("discovery.dexscreener_scanner.get_json")
    def test_total_failure_is_distinct_from_healthy_empty_response(self, get_json):
        get_json.side_effect = [RuntimeError("unavailable"), {}, None]
        with self.assertRaisesRegex(RuntimeError, "all discovery feeds unavailable"):
            scan_dexscreener()
        get_json.side_effect = [RuntimeError("unavailable"), [], None]
        self.assertEqual(scan_dexscreener(), [])


if __name__ == "__main__":
    unittest.main()
