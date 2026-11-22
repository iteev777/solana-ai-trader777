import unittest
from unittest.mock import patch

import pool_account_fetcher as fetcher
import onchain_stream
import pool_analysis


class PoolAccountBatchPlanningTests(unittest.TestCase):
    def test_pool_bundles_are_never_split_and_shared_accounts_are_deduplicated(self):
        batches = fetcher.plan_account_batches({
            ("pool-a", "pumpswap", "mint-a"): ["a", "b", "c"],
            ("pool-b", "pumpswap", "mint-b"): ["c", "d", "e"],
            ("pool-c", "pumpswap", "mint-c"): ["f", "g"],
        }, max_accounts=5)
        self.assertEqual(len(batches), 2)
        self.assertEqual(batches[0]["account_keys"], ["a", "b", "c", "d", "e"])
        self.assertEqual(
            set(batches[0]["pool_accounts"]),
            {("pool-a", "pumpswap", "mint-a"), ("pool-b", "pumpswap", "mint-b")}
        )
        self.assertEqual(batches[1]["account_keys"], ["f", "g"])

    def test_one_pool_larger_than_batch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "one pool"):
            fetcher.plan_account_batches({
                ("pool", "pumpswap", "mint"): ["a", "b", "c", "d"]
            }, max_accounts=3)

    def test_empty_and_duplicate_accounts_are_normalized(self):
        batches = fetcher.plan_account_batches({
            ("pool", "pumpswap", "mint"): ["a", "a", "", None, "b"]
        }, max_accounts=3)
        self.assertEqual(batches[0]["account_keys"], ["a", "b"])


class BatchedPoolRefreshTests(unittest.TestCase):
    def setUp(self):
        fetcher.clear_fetcher_state()
        pool_analysis.clear_pool_analysis_state()

    def test_multiple_pool_snapshots_share_one_confirmed_account_batch(self):
        key_a = ("pool-a", "pumpswap", "mint-a")
        key_b = ("pool-b", "pumpswap", "mint-b")
        account_sets = {
            key_a: ["pool-a", "vault-a", "mint-a", "shared"],
            key_b: ["pool-b", "vault-b", "mint-b", "shared"],
        }
        decoded = []
        stored = []
        account_map = {key: {"owner": "mock", "data": b"x"} for key in
                       ["pool-a", "vault-a", "mint-a", "pool-b", "vault-b", "mint-b", "shared"]}

        def fake_decode(pool, protocol, mint, **kwargs):
            decoded.append((pool, kwargs.get("_prefetched_slot"),
                            set(kwargs.get("_prefetched_accounts", {}))))
            return {"priceUsd": "1", "observedAt": 1234.5, "snapshotSlot": 987}

        with patch("solana_onchain.get_pool_snapshot_accounts",
                   side_effect=lambda p, proto, m: account_sets[(p, proto, m)]), \
             patch("solana_onchain.fetch_pool_account_batch",
                   return_value=(account_map, 987, 1234.5)) as batch_fetch, \
             patch("solana_onchain.decode_pool_snapshot", side_effect=fake_decode), \
             patch.object(onchain_stream, "store_pool_snapshot",
                          side_effect=lambda key, snap: stored.append((key, snap)) or True):
            result = fetcher.refresh_pools([key_a, key_b])

        self.assertEqual(result["last_priced_pools"], 2)
        self.assertEqual(result["last_rpc_batches"], 1)
        self.assertEqual(result["last_accounts_requested"], 7)
        self.assertEqual(batch_fetch.call_count, 1)
        self.assertEqual({item[1] for item in decoded}, {987})
        self.assertEqual(len(stored), 2)
        self.assertEqual({item[0] for item in stored}, {key_a, key_b})

    def test_rpc_batches_run_independently_and_transient_pool_is_backed_off(self):
        key_a = ("pool-a", "pumpswap", "mint-a")
        key_b = ("pool-b", "pumpswap", "mint-b")
        account_sets = {
            key_a: ["pool-a"] + ["a-" + str(i) for i in range(29)],
            key_b: ["pool-b"] + ["b-" + str(i) for i in range(29)],
        }

        def fetch_accounts(keys):
            if "pool-a" in keys:
                raise RuntimeError("All configured Solana RPC endpoints failed")
            return ({key: {"owner": "mock", "data": b"x"} for key in keys}, 999, 1234.5)

        with patch("solana_onchain.get_pool_snapshot_accounts",
                   side_effect=lambda p, proto, m: account_sets[(p, proto, m)]), \
             patch("solana_onchain.fetch_pool_account_batch",
                   side_effect=fetch_accounts) as batch_fetch, \
             patch("solana_onchain.decode_pool_snapshot",
                   return_value={"priceUsd": "1", "observedAt": 1234.5}), \
             patch.object(onchain_stream, "store_pool_snapshot", return_value=True):
            first = fetcher.refresh_pools([key_a, key_b])
            second = fetcher.refresh_pools([key_a, key_b])

        self.assertEqual(first["last_rpc_batches"], 2)
        self.assertEqual(first["last_priced_pools"], 1)
        self.assertEqual(first["last_failed_pools"], 1)
        self.assertEqual(second["last_skipped_pools"], 1)
        self.assertEqual(second["last_rpc_batches"], 1)
        self.assertEqual(second["last_priced_pools"], 1)
        self.assertEqual(batch_fetch.call_count, 3)
        self.assertEqual(pool_analysis.get_pool_analysis_status()["transient_backoff_pools"], 1)

    def test_failed_pool_discovery_does_not_prevent_other_pools_refreshing(self):
        good = ("pool-good", "pumpswap", "mint-good")
        bad = ("pool-bad", "pumpswap", "mint-bad")
        with patch("solana_onchain.get_pool_snapshot_accounts",
                   side_effect=lambda p, proto, m: ["pool-good", "vault", "mint"]
                   if p == "pool-good" else (_ for _ in ()).throw(ValueError("bad pool"))), \
             patch("solana_onchain.fetch_pool_account_batch",
                   return_value=({"pool-good": {}, "vault": {}, "mint": {}}, 123, 1234.0)), \
             patch("solana_onchain.decode_pool_snapshot",
                   return_value={"priceUsd": "1", "observedAt": 1234.0}), \
             patch.object(onchain_stream, "store_pool_snapshot", return_value=True):
            result = fetcher.refresh_pools([good, bad])
        self.assertEqual(result["last_priced_pools"], 1)
        self.assertEqual(result["last_failed_pools"], 1)


if __name__ == "__main__":
    unittest.main()
