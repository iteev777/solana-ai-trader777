import os
import tempfile
import unittest
from unittest.mock import patch

import database


class CopyPerformanceAndProtocolTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "test.db")
        self.patcher = patch.object(database, "DATABASE", self.db_path)
        self.patcher.start()
        database.initialize_database()
        database.initialize_account("paper", 100)

    def tearDown(self):
        self.patcher.stop()
        self.tempdir.cleanup()

    def test_copy_wallet_pnl_is_attributed_from_open_to_close(self):
        result = database.open_position_atomic(
            "paper", "MintA", 1.0, 10.0, "sniper",
            {"copy_wallet": "WalletA"}
        )
        self.assertEqual(result, "POSITION OPENED")
        closed = database.close_position_atomic("paper", "MintA", 1.2)
        self.assertIsInstance(closed, dict)
        rows = database.get_copy_wallet_performance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["wallet"], "WalletA")
        self.assertEqual(rows[0]["closed_trades"], 1)
        self.assertEqual(rows[0]["wins"], 1)
        self.assertGreater(rows[0]["realized_pnl"], 0)

    def test_open_copy_position_counts_exposure(self):
        database.open_position_atomic(
            "paper", "MintB", 1.0, 7.0, "sniper",
            {"copy_wallet": "WalletB"}
        )
        row = database.get_copy_wallet_performance()[0]
        self.assertEqual(row["open_positions"], 1)
        self.assertEqual(row["open_exposure_usd"], 7.0)

    def test_protocol_validation_telemetry_aggregates(self):
        database.record_protocol_validation(
            "pumpswap", "PoolA", "MintA", True, observed_at=123.0
        )
        database.record_protocol_validation(
            "pumpswap", "PoolB", "MintB", False, reason="bad discriminator"
        )
        summary = database.get_protocol_validation_summary()
        pumpswap = next(item for item in summary["protocols"]
                        if item["protocol"] == "pumpswap")
        self.assertEqual(pumpswap["attempts"], 2)
        self.assertEqual(pumpswap["successes"], 1)
        self.assertEqual(pumpswap["failures"], 1)
        self.assertEqual(summary["validated_protocols"], 1)


if __name__ == "__main__":
    unittest.main()
