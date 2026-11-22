import unittest
from datetime import datetime, timezone

import exit_policy


class ExitPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = {
            "stop_loss_pct": 9.0,
            "take_profit_pct": 10.0,
            "trailing_activation_pct": 4.0,
            "trailing_distance_pct": 3.5,
            "max_hold_seconds": 900,
            "stagnation_seconds": 0,
            "stagnation_band_pct": 1.5,
            "break_even_activation_pct": 0.0,
            "break_even_offset_pct": 0.0,
        }
        self.position = {
            "entry_price": 100.0,
            "opened_at": datetime.fromtimestamp(1000, timezone.utc).isoformat(),
            "metadata": {"peak_price": 125.0},
        }

    def test_stop_loss_has_priority(self):
        self.assertEqual(
            exit_policy.evaluate_exit(self.position, 90.0, now=1100, policy=self.policy),
            "STOP LOSS",
        )

    def test_trailing_stop_uses_persisted_peak_not_current_price(self):
        self.assertEqual(
            exit_policy.evaluate_exit(self.position, 120.0, now=1100, policy=self.policy),
            "TRAILING PROFIT EXIT",
        )

    def test_trailing_does_not_fire_before_activation(self):
        position = {**self.position, "metadata": {"peak_price": 103.0}}
        self.assertIsNone(
            exit_policy.evaluate_exit(position, 99.5, now=1100, policy=self.policy)
        )

    def test_take_profit(self):
        position = {**self.position, "metadata": {"peak_price": 100.0}}
        self.assertEqual(
            exit_policy.evaluate_exit(position, 111.0, now=1100, policy=self.policy),
            "TAKE PROFIT",
        )

    def test_break_even_protection_arms_from_peak(self):
        policy = {**self.policy, "break_even_activation_pct": 8.0, "break_even_offset_pct": 1.0}
        position = {**self.position, "metadata": {"peak_price": 110.0}}
        self.assertEqual(
            exit_policy.evaluate_exit(position, 100.5, now=1100, policy=policy),
            "BREAK-EVEN PROTECTION",
        )

    def test_stagnation_exit_requires_duration_and_near_flat_pnl(self):
        policy = {**self.policy, "stagnation_seconds": 300, "max_hold_seconds": 0}
        self.assertEqual(
            exit_policy.evaluate_exit(self.position, 100.5, now=1400, policy=policy),
            "STAGNATION EXIT",
        )

    def test_max_hold_exit(self):
        policy = {**self.policy, "max_hold_seconds": 300}
        position = {**self.position, "metadata": {"peak_price": 100.0}}
        self.assertEqual(
            exit_policy.evaluate_exit(position, 102.0, now=1400, policy=policy),
            "MAX HOLD EXIT",
        )

    def test_missing_or_invalid_prices_do_not_trigger_exit(self):
        self.assertIsNone(exit_policy.evaluate_exit(self.position, float("nan"), now=1100, policy=self.policy))
        self.assertIsNone(exit_policy.evaluate_exit({}, 100.0, now=1100, policy=self.policy))

    def test_environment_settings_reject_nan_and_out_of_range(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {"TROJAN_STOP_LOSS_PCT": "nan"}):
            with self.assertRaises(ValueError):
                exit_policy.get_exit_policy()
        with patch.dict("os.environ", {"TROJAN_TRAILING_DISTANCE_PCT": "99"}):
            with self.assertRaises(ValueError):
                exit_policy.get_exit_policy()


if __name__ == "__main__":
    unittest.main()
