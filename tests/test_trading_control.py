import unittest
from unittest.mock import patch

import risk_manager
import trading_control


class TradingControlTests(unittest.TestCase):
    def setUp(self):
        trading_control.reset_for_tests()

    def tearDown(self):
        trading_control.reset_for_tests()

    def test_force_stop_status_records_reason_and_timestamp(self):
        status = trading_control.set_force_stopped(True, "Operator requested halt")
        self.assertTrue(status["is_stopped"])
        self.assertEqual(status["reason"], "Operator requested halt")
        self.assertIsNotNone(status["stopped_at"])
        self.assertEqual(status["scope"], "paper_entries_and_exits")
        self.assertEqual(status["live_execution"], "locked")

    def test_force_stop_reason_is_trimmed_and_bounded(self):
        status = trading_control.set_force_stopped(True, " x " * 200)
        self.assertLessEqual(len(status["reason"]), 300)
        self.assertEqual(status["reason"], status["reason"].strip())

    def test_resume_clears_force_stop_details(self):
        trading_control.set_force_stopped(True, "Emergency")
        status = trading_control.set_force_stopped(False)
        self.assertFalse(status["is_stopped"])
        self.assertIsNone(status["stopped_at"])
        self.assertIsNone(status["reason"])

    def test_non_boolean_stop_value_is_rejected(self):
        with self.assertRaises(ValueError):
            trading_control.set_force_stopped("true")

    def test_central_risk_admission_blocks_while_force_stopped(self):
        trading_control.set_force_stopped(True, "Unit test")
        allowed, reason = risk_manager.check_trade_risk(
            balance=100,
            open_positions=0,
            daily_loss=0,
            trade_size=5,
            strategy="main",
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "FORCE STOP ACTIVE")

    def test_central_risk_admission_resumes_after_force_stop_cleared(self):
        trading_control.set_force_stopped(True, "Unit test")
        trading_control.set_force_stopped(False)
        allowed, reason = risk_manager.check_trade_risk(
            balance=100,
            open_positions=0,
            daily_loss=0,
            trade_size=5,
            strategy="main",
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "TRADE APPROVED")


if __name__ == "__main__":
    unittest.main()
