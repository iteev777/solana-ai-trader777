import unittest
from unittest.mock import patch

import onchain_stream


class PoolRefreshRuntimeMetricsTests(unittest.TestCase):
    def setUp(self):
        with onchain_stream._lock:
            for key in onchain_stream._batch_status:
                onchain_stream._batch_status[key] = 0
            onchain_stream._batch_refreshing = False

    def test_position_and_candidate_refresh_metrics_are_reported_separately(self):
        position = ("position-pool", "raydium_cpmm", "mint-a")
        candidate = ("candidate-pool", "pumpswap", "mint-b")
        responses = [
            {"last_refresh_pools": 1, "last_priced_pools": 1,
             "last_failed_pools": 0, "last_skipped_pools": 0,
             "last_rpc_batches": 1, "last_accounts_requested": 5},
            {"last_refresh_pools": 1, "last_priced_pools": 0,
             "last_failed_pools": 1, "last_skipped_pools": 0,
             "last_rpc_batches": 1, "last_accounts_requested": 4},
        ]
        with patch("pool_account_fetcher.refresh_pools", side_effect=responses) as refresh, \
             patch("pool_account_fetcher.prune_pool_account_cache", return_value=0):
            onchain_stream._refresh_many([position], [candidate], prune_unwatched=True)

        self.assertEqual(refresh.call_count, 2)
        self.assertEqual(refresh.call_args_list[0].args[0], [position])
        self.assertEqual(refresh.call_args_list[1].args[0], [candidate])
        status = onchain_stream.get_stream_status()
        self.assertEqual(status["last_position_pools"], 1)
        self.assertEqual(status["last_position_priced_pools"], 1)
        self.assertEqual(status["last_candidate_pools"], 1)
        self.assertEqual(status["last_candidate_failed_pools"], 1)
        self.assertEqual(status["last_refresh_pools"], 2)
        self.assertEqual(status["last_priced_pools"], 1)
        self.assertEqual(status["last_failed_pools"], 1)
        self.assertGreaterEqual(status["last_position_refresh_ms"], 0)
        self.assertGreaterEqual(status["last_candidate_refresh_ms"], 0)
        self.assertGreaterEqual(status["last_total_refresh_ms"], 0)
        self.assertGreaterEqual(status["last_position_pools_per_second"], 0)
        self.assertGreaterEqual(status["last_candidate_pools_per_second"], 0)

    def test_empty_refresh_has_zero_per_class_metrics(self):
        with patch("pool_account_fetcher.prune_pool_account_cache", return_value=0):
            onchain_stream._refresh_many([], [], prune_unwatched=True)
        status = onchain_stream.get_stream_status()
        self.assertEqual(status["last_position_pools"], 0)
        self.assertEqual(status["last_candidate_pools"], 0)
        self.assertEqual(status["last_position_pools_per_second"], 0.0)
        self.assertEqual(status["last_candidate_pools_per_second"], 0.0)


if __name__ == "__main__":
    unittest.main()
