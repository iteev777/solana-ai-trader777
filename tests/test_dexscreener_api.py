import unittest
from unittest.mock import patch

import dexscreener_api


class DexScreenerApiTests(unittest.TestCase):
    @patch("dexscreener_api._get", return_value=[{"pairAddress": "P1"}, {"bad": None}])
    def test_fetch_token_pools_returns_all_pool_objects(self, get):
        result = dexscreener_api.fetch_token_pools("Mint111")
        self.assertEqual(result, [{"pairAddress": "P1"}, {"bad": None}])
        get.assert_called_once_with("/token-pairs/v1/solana/Mint111")

    @patch("dexscreener_api._get", return_value=[{"pairAddress": "P1"}])
    def test_batch_deduplicates_addresses(self, get):
        result = dexscreener_api.fetch_token_batch(["Mint111", "Mint111"])
        self.assertEqual(result, [{"pairAddress": "P1"}])
        self.assertEqual(
            get.call_args.args[0],
            "/tokens/v1/solana/Mint111",
        )

    def test_batch_rejects_more_than_provider_limit(self):
        addresses = [f"Mint{i}" for i in range(31)]
        with self.assertRaisesRegex(ValueError, "at most 30"):
            dexscreener_api.fetch_token_batch(addresses)

    @patch("dexscreener_api._get", return_value={"pairs": [{"pairAddress": "P1"}]})
    def test_pair_lookup_accepts_pairs_response_shape(self, get):
        self.assertEqual(
            dexscreener_api.fetch_pair("solana", "Pair111"),
            {"pairAddress": "P1"},
        )

    @patch("dexscreener_api._get", return_value={"pair": None})
    def test_pair_lookup_returns_none_when_not_found(self, get):
        self.assertIsNone(dexscreener_api.fetch_pair("solana", "Pair111"))

    @patch("dexscreener_api._get", return_value={"pairs": [{"pairAddress": "P1"}]})
    def test_search_passes_trimmed_query_and_extracts_pairs(self, get):
        result = dexscreener_api.search_pairs("  token symbol  ")
        self.assertEqual(result, [{"pairAddress": "P1"}])
        get.assert_called_once_with(
            "/latest/dex/search",
            params={"q": "token symbol"},
        )

    @patch("dexscreener_api._get", return_value=[{"type": "tokenProfile"}])
    def test_orders_endpoint_returns_valid_order_records(self, get):
        result = dexscreener_api.fetch_token_orders("Mint111")
        self.assertEqual(result, [{"type": "tokenProfile"}])
        get.assert_called_once_with("/orders/v1/solana/Mint111")

    @patch("dexscreener_api._get", return_value=[{"chainId": "solana"}])
    def test_supported_chains_endpoint_returns_chain_records(self, get):
        self.assertEqual(dexscreener_api.fetch_supported_chains(), [{"chainId": "solana"}])
        get.assert_called_once_with("/chains", cache_ttl=300)

    def test_invalid_inputs_are_rejected_before_network_access(self):
        with patch("dexscreener_api._get") as get:
            with self.assertRaises(ValueError):
                dexscreener_api.fetch_token_pools("bad/mint")
            with self.assertRaises(ValueError):
                dexscreener_api.search_pairs("   ")
            get.assert_not_called()

    def test_invalid_provider_response_shapes_raise(self):
        with patch("dexscreener_api._get", return_value={"unexpected": []}):
            with self.assertRaisesRegex(ValueError, "JSON array"):
                dexscreener_api.fetch_token_pools("Mint111")
        with patch("dexscreener_api._get", return_value={"unexpected": []}):
            with self.assertRaisesRegex(ValueError, "pairs array"):
                dexscreener_api.search_pairs("token")


if __name__ == "__main__":
    unittest.main()
