import os
import tempfile
import unittest
from unittest.mock import patch

import database
import paper_trading


class PositionManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_database = database.DATABASE
        database.DATABASE = os.path.join(self.temp_dir.name, "test.sqlite")
        database.initialize_database()
        database.initialize_account("paper", 100.0)

    def tearDown(self):
        database.DATABASE = self.previous_database
        self.temp_dir.cleanup()

    def test_dca_and_partial_exit_preserve_position_accounting(self):
        opened = database.open_position_atomic(
            "paper", "MINT_TEST", 1.0, 5.0, metadata={"paper_fee_rate": database.PAPER_FEE_RATE}
        )
        self.assertEqual(opened, "POSITION OPENED")

        dca = database.add_position_investment_atomic(
            "paper", "MINT_TEST", 0.9, 2.5, metadata={"dca_count": 1}
        )
        self.assertEqual(dca["result"], "DCA ADDED")
        position = database.get_positions("paper")["MINT_TEST"]
        self.assertAlmostEqual(position["invested"], 7.5)
        self.assertAlmostEqual(position["invested"] / position["entry_price"], 5.0 / 1.0 + 2.5 / 0.9)
        self.assertEqual(position["metadata"]["dca_count"], 1)

        partial = database.partial_close_position_atomic(
            "paper", "MINT_TEST", 1.1, fraction=0.5
        )
        self.assertTrue(partial["partial"])
        remaining = database.get_positions("paper")["MINT_TEST"]
        self.assertAlmostEqual(remaining["invested"], 3.75)
        self.assertEqual(remaining["metadata"]["partial_exit_count"], 1)

        closed = database.close_position_atomic("paper", "MINT_TEST", 1.2)
        self.assertIsInstance(closed, dict)
        self.assertNotIn("MINT_TEST", database.get_positions("paper"))
        stats = database.get_trade_stats("paper")
        self.assertEqual(stats["total_trades"], 2)

        database.record_filter_decision(
            "MINT_TEST", "STRATEGY", "REJECT", "Buy pressure too weak",
            score=42, liquidity=1200, source_count=2
        )
        summary = database.get_filtering_summary()
        self.assertEqual(summary["decisions"]["REJECT"], 1)
        self.assertEqual(database.get_recent_filter_events(1)[0]["reason"], "Buy pressure too weak")

    def test_exit_ladder_completion_is_atomic_and_retry_safe(self):
        database.open_position_atomic("paper", "MINT_TEST", 1, 5)
        first = database.partial_close_position_atomic(
            "paper", "MINT_TEST", 1.1, exit_tier=0, slippage_bps=25
        )
        duplicate = database.partial_close_position_atomic(
            "paper", "MINT_TEST", 1.1, exit_tier=0, slippage_bps=25
        )
        position = database.get_positions("paper")["MINT_TEST"]
        self.assertTrue(first["partial"])
        self.assertEqual(duplicate, "Exit ladder tier already completed")
        self.assertEqual(database.get_trade_stats("paper")["total_trades"], 1)
        self.assertEqual(position["invested"], 2.5)
        self.assertEqual(position["metadata"]["partial_exit_count"], 1)
        self.assertEqual(position["metadata"]["exit_ladder_completed"], [0])
        self.assertEqual(position["metadata"]["paper_exit_slippage_bps"], 25)

    def test_original_partial_exit_flag_prevents_repeating_first_tier(self):
        database.open_position_atomic(
            "paper", "MINT_TEST", 1, 5, metadata={"partial_take_profit_done": True}
        )
        result = database.partial_close_position_atomic("paper", "MINT_TEST", 1.1, exit_tier=0)
        self.assertEqual(result, "Exit ladder tier already completed")

    def test_dca_tier_conflict_does_not_spend_twice(self):
        database.open_position_atomic("paper", "MINT_TEST", 1, 5)
        first = database.add_position_investment_atomic("paper", "MINT_TEST", 0.95, 2.5, dca_tier=0)
        duplicate = database.add_position_investment_atomic("paper", "MINT_TEST", 0.95, 2.5, dca_tier=0)
        self.assertEqual(first["result"], "DCA ADDED")
        self.assertEqual(duplicate, "DCA tier changed")
        self.assertEqual(database.get_positions("paper")["MINT_TEST"]["invested"], 7.5)

    def test_time_loss_exit_has_priority_over_dca(self):
        database.open_position_atomic("paper", "MINT_TEST", 1, 5)
        with (
            patch.dict(os.environ, {
                "TROJAN_DCA_ENABLED": "true",
                "TROJAN_TIME_LOSS_EXIT_SECONDS": "1",
                "TROJAN_TIME_LOSS_THRESHOLD_PCT": "-5",
            }),
            patch("exit_policy.time.time", return_value=4_000_000_000),
        ):
            result = paper_trading.update_position("MINT_TEST", 0.94)
        self.assertNotIn("MINT_TEST", database.get_positions("paper"))
        self.assertEqual(result["result"], "TIME LOSS EXIT")


if __name__ == "__main__":
    unittest.main()
