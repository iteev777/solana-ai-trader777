import threading
import unittest
from unittest.mock import patch

import solana_onchain
import token_decimals


class TokenDecimalsCacheTests(unittest.TestCase):
    def setUp(self):
        token_decimals.clear_cache()
        with token_decimals._LOCK:
            for key in token_decimals._STATS:
                token_decimals._STATS[key] = 0

    def tearDown(self):
        token_decimals.clear_cache()

    def test_valid_decimals_are_cached(self):
        mint = solana_onchain._b58encode(bytes([1]) * 32)
        with patch.object(solana_onchain, "get_mint_decimals", return_value=9) as fetch:
            self.assertEqual(token_decimals.get_token_decimals(mint), 9)
            self.assertEqual(token_decimals.get_token_decimals(mint), 9)
        fetch.assert_called_once_with(mint)
        self.assertEqual(token_decimals.get_cache_status()["hits"], 1)

    def test_invalid_decimals_fail_closed_and_are_short_cached(self):
        mint = solana_onchain._b58encode(bytes([2]) * 32)
        with patch.object(solana_onchain, "get_mint_decimals", return_value=True) as fetch, \
             patch.dict("os.environ", {"TROJAN_TOKEN_DECIMALS_FAILURE_TTL_SECONDS": "60"}):
            self.assertIsNone(token_decimals.get_token_decimals(mint))
            self.assertIsNone(token_decimals.get_token_decimals(mint))
        fetch.assert_called_once_with(mint)
        self.assertEqual(token_decimals.get_cache_status()["errors"], 1)

    def test_rpc_exception_is_cached_as_unknown_not_zero(self):
        mint = solana_onchain._b58encode(bytes([3]) * 32)
        with patch.object(solana_onchain, "get_mint_decimals", side_effect=RuntimeError("RPC down")) as fetch:
            self.assertIsNone(token_decimals.get_token_decimals(mint))
            self.assertIsNone(token_decimals.get_token_decimals(mint))
        fetch.assert_called_once_with(mint)
        self.assertEqual(token_decimals.get_cache_status()["errors"], 1)

    def test_rejects_invalid_address_input_without_rpc(self):
        with patch.object(solana_onchain, "get_mint_decimals") as fetch:
            self.assertIsNone(token_decimals.get_token_decimals(None))
            self.assertIsNone(token_decimals.get_token_decimals(""))
            self.assertIsNone(token_decimals.get_token_decimals("x" * 65))
        fetch.assert_not_called()

    def test_singleflight_shares_one_rpc_request_between_threads(self):
        mint = solana_onchain._b58encode(bytes([4]) * 32)
        entered = threading.Event()
        release = threading.Event()
        results = []

        def slow_fetch(address):
            entered.set()
            self.assertTrue(release.wait(2))
            return 6

        with patch.object(solana_onchain, "get_mint_decimals", side_effect=slow_fetch) as fetch:
            first = threading.Thread(target=lambda: results.append(token_decimals.get_token_decimals(mint)))
            second = threading.Thread(target=lambda: results.append(token_decimals.get_token_decimals(mint)))
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            release.set()
            first.join(2)
            second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(results, [6, 6])
        fetch.assert_called_once_with(mint)
        self.assertEqual(token_decimals.get_cache_status()["waits"], 1)

    def test_cache_status_exposes_bounded_cache_metrics(self):
        status = token_decimals.get_cache_status()
        self.assertEqual(status["size"], 0)
        self.assertEqual(status["in_flight"], 0)
        self.assertIn("evictions", status)


if __name__ == "__main__":
    unittest.main()
