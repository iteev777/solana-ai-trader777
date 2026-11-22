import os
import tempfile
import unittest

import database


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
        self.assertAlmostEqual(position["entry_price"], (5.0 + 2.25) / 7.5)
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


if __name__ == "__main__":
    unittest.main()
