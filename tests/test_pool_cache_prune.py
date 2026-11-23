import unittest

import pool_account_fetcher as fetcher


class PoolAccountCachePruningTests(unittest.TestCase):
    def setUp(self):
        fetcher.clear_fetcher_state()

    def test_pruning_uses_complete_position_and_candidate_watch_set(self):
        position = ("position", "raydium", "mint-a")
        candidate = ("candidate", "pumpswap", "mint-b")
        stale = ("stale", "orca", "mint-c")
        with fetcher._lock:
            for key in (position, candidate, stale):
                fetcher._pool_account_sets[key] = [key[0]]
                fetcher._pool_account_set_times[key] = 1.0
        removed = fetcher.prune_pool_account_cache([position, candidate])
        self.assertEqual(removed, 1)
        with fetcher._lock:
            self.assertIn(position, fetcher._pool_account_sets)
            self.assertIn(candidate, fetcher._pool_account_sets)
            self.assertNotIn(stale, fetcher._pool_account_sets)


if __name__ == "__main__":
    unittest.main()
