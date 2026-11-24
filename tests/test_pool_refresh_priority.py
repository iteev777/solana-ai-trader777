import unittest

from onchain_stream import partition_refresh_priority


class PoolRefreshPriorityTests(unittest.TestCase):
    def test_open_positions_are_first_and_duplicates_are_removed(self):
        position_a = ("position-a", "raydium", "mint-a")
        position_b = ("position-b", "orca", "mint-b")
        candidate = ("candidate", "pumpswap", "mint-c")
        positions, candidates = partition_refresh_priority(
            [position_b, position_a, position_b], [candidate, position_a]
        )
        self.assertEqual(positions, [position_b, position_a])
        self.assertEqual(candidates, [candidate])

    def test_dirty_refresh_only_returns_requested_watches(self):
        position = ("position", "raydium", "mint")
        candidate = ("candidate", "pumpswap", "mint")
        positions, candidates = partition_refresh_priority(
            [position], [candidate], {candidate}
        )
        self.assertEqual(positions, [])
        self.assertEqual(candidates, [candidate])

    def test_candidate_order_is_stable(self):
        position = ("position", "raydium", "mint")
        candidate_a = ("candidate-a", "pumpswap", "mint-a")
        candidate_b = ("candidate-b", "orca", "mint-b")
        positions, candidates = partition_refresh_priority(
            [position], [candidate_b, candidate_a]
        )
        self.assertEqual(positions, [position])
        self.assertEqual(candidates, [candidate_b, candidate_a])


if __name__ == "__main__":
    unittest.main()
