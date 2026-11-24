import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import database
import risk_manager


class RiskGuardExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_database = database.DATABASE
        database.DATABASE = os.path.join(self.temp.name, "risk.sqlite")
        database.initialize_database()
        database.initialize_account("paper", 100.0)
        self.settings = patch.multiple(
            risk_manager,
            MAX_TRADE_SIZE=5.0,
            MIN_TRADE_SIZE=1.0,
            MAX_DAILY_LOSS=50.0,
            MAX_PERIOD_LOSS=10.0,
            PERIOD_LOSS_WINDOW_HOURS=24.0,
            REENTRY_COOLDOWN_MINUTES=15,
            MAX_TRADE_BALANCE_FRACTION=0.1,
            MAX_TOTAL_POSITIONS=15,
            MAX_MAIN_POSITIONS=10,
            MAX_SNIPER_POSITIONS=10,
        )
        self.settings.start()

    def tearDown(self):
        self.settings.stop()
        database.DATABASE = self.previous_database
        self.temp.cleanup()

    def risk(self, token="MintA", mode="paper", strategy="main"):
        return risk_manager.check_trade_risk(
            balance=100,
            open_positions=0,
            daily_loss=0,
            trade_size=5,
            strategy=strategy,
            main_positions=0,
            sniper_positions=0,
            token_address=token,
            mode=mode,
        )

    def test_period_loss_counts_only_realized_sell_losses_in_requested_mode(self):
        database.save_trade("MintA", "SELL", 1, 5, -7.5, mode="paper")
        database.save_trade("MintB", "SELL", 1, 5, 3.0, mode="paper")
        database.save_trade("MintC", "SELL", 1, 5, -99.0, mode="live")
        self.assertEqual(database.get_period_realized_loss("paper", 24), 7.5)
        self.assertEqual(database.get_period_realized_loss("live", 24), 99.0)

    def test_rolling_period_loss_blocks_new_entries(self):
        database.save_trade("MintA", "SELL", 1, 5, -10.0, mode="paper")
        allowed, reason = self.risk()
        self.assertFalse(allowed)
        self.assertEqual(reason, "ROLLING PERIOD LOSS LIMIT REACHED")

    def test_recent_exit_blocks_same_token_but_not_another_token(self):
        database.save_trade("MintA", "SELL", 1, 5, 0.0, mode="paper")
        allowed, reason = self.risk("MintA")
        self.assertFalse(allowed)
        self.assertEqual(reason, "RE-ENTRY COOLDOWN ACTIVE")
        other_allowed, other_reason = self.risk("MintB")
        self.assertTrue(other_allowed, other_reason)

    def test_exit_older_than_cooldown_allows_reentry(self):
        database.save_trade("MintA", "SELL", 1, 5, 0.0, mode="paper")
        old = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
        with database.connect() as connection:
            connection.execute(
                "UPDATE trades SET timestamp = ? WHERE token_address = 'MintA'",
                (old,),
            )
        allowed, reason = self.risk("MintA")
        self.assertTrue(allowed, reason)

    def test_reentry_cooldown_is_scoped_to_strategy(self):
        database.save_trade("MintA", "SELL", 1, 5, 0.0, mode="paper", strategy="main")
        main_allowed, main_reason = self.risk("MintA", strategy="main")
        sniper_allowed, sniper_reason = self.risk("MintA", strategy="sniper")
        self.assertFalse(main_allowed)
        self.assertEqual(main_reason, "RE-ENTRY COOLDOWN ACTIVE")
        self.assertTrue(sniper_allowed, sniper_reason)
        self.assertIsNone(database.get_last_exit_timestamp("MintA", "paper", strategy="sniper"))

    def test_period_loss_guard_applies_without_token_context(self):
        database.save_trade("MintA", "SELL", 1, 5, -10.0, mode="paper")
        allowed, reason = self.risk(token=None)
        self.assertFalse(allowed)
        self.assertEqual(reason, "ROLLING PERIOD LOSS LIMIT REACHED")

    def test_invalid_mode_is_rejected_without_token_context(self):
        allowed, reason = self.risk(token=None, mode="invalid")
        self.assertFalse(allowed)
        self.assertEqual(reason, "INVALID TRADING MODE FOR HISTORY GUARDS")

    def test_period_loss_guard_fails_closed_when_history_is_unavailable(self):
        with patch.object(database, "get_period_realized_loss", side_effect=RuntimeError("db offline")):
            allowed, reason = self.risk()
        self.assertFalse(allowed)
        self.assertEqual(reason, "PERIOD LOSS HISTORY UNAVAILABLE")

    def test_reentry_guard_fails_closed_when_exit_history_is_unavailable(self):
        with patch.object(database, "get_last_exit_timestamp", side_effect=RuntimeError("db offline")):
            allowed, reason = self.risk()
        self.assertFalse(allowed)
        self.assertEqual(reason, "RE-ENTRY COOLDOWN HISTORY UNAVAILABLE")

    def test_zero_cooldown_disables_only_reentry_guard(self):
        with patch.object(risk_manager, "REENTRY_COOLDOWN_MINUTES", 0):
            database.save_trade("MintA", "SELL", 1, 5, 0.0, mode="paper")
            allowed, reason = self.risk("MintA")
        self.assertTrue(allowed, reason)


if __name__ == "__main__":
    unittest.main()
