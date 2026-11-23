import os
import unittest
from unittest.mock import patch

import position_policy


class DcaPolicyTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def position(self, **overrides):
        value = {
            "entry_price": 1.0,
            "opened_at": 900.0,
            "metadata": {"dca_count": 0, "first_entry_price": 1.0, "first_investment": 10.0},
        }
        value.update(overrides)
        return value

    def policy(self, **overrides):
        value = {
            "enabled": True,
            "measure_from": "average",
            "cooldown_minutes": 60,
            "min_hold_minutes": 0,
            "price_max_age_seconds": 30,
        }
        value.update(overrides)
        return value

    def evaluate(self, **kwargs):
        defaults = {
            "position": self.position(),
            "current_price": 0.94,
            "price_timestamp": 1000.0,
            "step": {"drop_pct": 5.0, "add_usd": 2.5},
            "policy": self.policy(),
            "stop_loss_pct": 9.0,
            "now": 1000.0,
        }
        defaults.update(kwargs)
        return position_policy.evaluate_dca_step(**defaults)

    def test_dca_is_disabled_by_default(self):
        self.assertFalse(position_policy.get_dca_policy()["enabled"])
        self.assertEqual(position_policy.get_dca_steps(), [])

    def test_direct_evaluator_rejects_disabled_policy(self):
        result = self.evaluate(policy=self.policy(enabled=False))
        self.assertFalse(result["allowed"])
        self.assertIn("disabled", result["reason"])

    def test_legacy_fixed_usd_steps_remain_supported(self):
        with patch.dict(os.environ, {
            "TROJAN_DCA_ENABLED": "true",
            "TROJAN_DCA_STEPS_JSON": '[{"drop_pct":5,"add_usd":2.5}]',
        }):
            self.assertEqual(
                position_policy.get_dca_steps(),
                [{"drop_pct": 5.0, "add_usd": 2.5}],
            )

    def test_first_buy_multiple_steps_are_supported(self):
        with patch.dict(os.environ, {
            "TROJAN_DCA_ENABLED": "true",
            "TROJAN_DCA_STEPS_JSON": '[{"drop_pct":5,"add_multiple":0.5}]',
            "TROJAN_DCA_MEASURE_FROM": "first_entry",
            "TROJAN_DCA_COOLDOWN_MINUTES": "45",
            "TROJAN_DCA_MIN_HOLD_MINUTES": "10",
        }):
            config = position_policy.get_dca_policy()
        self.assertEqual(config["steps"], [{"drop_pct": 5.0, "add_multiple": 0.5}])
        self.assertEqual(config["measure_from"], "first_entry")
        self.assertEqual(config["cooldown_minutes"], 45)
        self.assertEqual(config["min_hold_minutes"], 10)

    def test_step_must_define_exactly_one_sizing_method(self):
        for row in (
            {"drop_pct": 5, "add_usd": 2, "add_multiple": 1},
            {"drop_pct": 5},
        ):
            with patch.dict(os.environ, {
                "TROJAN_DCA_ENABLED": "true",
                "TROJAN_DCA_STEPS_JSON": __import__("json").dumps([row]),
            }):
                with self.assertRaises(ValueError):
                    position_policy.get_dca_steps()

    def test_steps_must_have_increasing_drop_levels(self):
        with patch.dict(os.environ, {
            "TROJAN_DCA_ENABLED": "true",
            "TROJAN_DCA_STEPS_JSON": '[{"drop_pct":5,"add_usd":2},{"drop_pct":4,"add_usd":3}]',
        }):
            with self.assertRaises(ValueError):
                position_policy.get_dca_steps()

    def test_fresh_price_and_passed_drop_approve_legacy_add(self):
        result = self.evaluate()
        self.assertTrue(result["allowed"], result)
        self.assertEqual(result["amount_usd"], 2.5)
        self.assertAlmostEqual(result["drop_pct"], -6.0)

    def test_missing_stale_and_future_price_timestamps_fail_closed(self):
        for timestamp, expected in (
            (None, "missing or invalid"),
            (969.0, "stale"),
            (1001.0, "future"),
        ):
            result = self.evaluate(price_timestamp=timestamp)
            self.assertFalse(result["allowed"])
            self.assertIn(expected, result["reason"])

    def test_minimum_holding_period_is_enforced(self):
        result = self.evaluate(policy=self.policy(min_hold_minutes=2))
        self.assertFalse(result["allowed"])
        self.assertIn("holding period", result["reason"])

    def test_cooldown_is_enforced_and_expires_at_boundary(self):
        position = self.position(metadata={
            "dca_count": 1, "first_entry_price": 1.0,
            "first_investment": 10.0, "last_dca_at": 99970.0,
        })
        blocked = self.evaluate(position=position, price_timestamp=100000.0, now=100000.0)
        self.assertFalse(blocked["allowed"])
        self.assertIn("cooldown", blocked["reason"])
        position["metadata"]["last_dca_at"] = 96300.0
        passed = self.evaluate(position=position, price_timestamp=100000.0, now=100000.0)
        self.assertTrue(passed["allowed"], passed)

    def test_first_entry_basis_and_first_buy_multiple_are_used(self):
        position = self.position(
            entry_price=0.9,
            metadata={"dca_count": 0, "first_entry_price": 1.0, "first_investment": 10.0},
        )
        result = self.evaluate(
            position=position,
            current_price=0.93,
            step={"drop_pct": 5.0, "add_multiple": 0.5},
            policy=self.policy(measure_from="first_entry"),
        )
        self.assertTrue(result["allowed"], result)
        self.assertEqual(result["basis_price"], 1.0)
        self.assertEqual(result["amount_usd"], 5.0)

    def test_first_entry_basis_does_not_fall_back_after_a_prior_add(self):
        position = self.position(
            metadata={"dca_count": 1, "first_investment": 10.0, "last_dca_at": 900.0}
        )
        result = self.evaluate(
            position=position,
            policy=self.policy(measure_from="first_entry", cooldown_minutes=0),
        )
        self.assertFalse(result["allowed"])
        self.assertIn("first-entry", result["reason"])

    def test_hard_stop_blocks_dca_even_when_ladder_drop_passes(self):
        result = self.evaluate(current_price=0.90)
        self.assertFalse(result["allowed"])
        self.assertIn("hard stop", result["reason"])

    def test_invalid_dca_count_and_missing_multiple_base_fail_closed(self):
        invalid = self.evaluate(position=self.position(metadata={"dca_count": True}))
        self.assertFalse(invalid["allowed"])
        self.assertIn("DCA count", invalid["reason"])
        no_base = self.evaluate(
            position=self.position(metadata={"dca_count": 0, "first_entry_price": 1.0}),
            step={"drop_pct": 5.0, "add_multiple": 1.0},
        )
        self.assertFalse(no_base["allowed"])
        self.assertIn("First investment", no_base["reason"])


class ExitLadderPolicyTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_legacy_remaining_fraction_configuration_is_unchanged(self):
        with patch.dict(os.environ, {
            "TROJAN_EXIT_LADDER_JSON": '[{"profit_pct":8,"sell_fraction":0.5}]',
        }):
            self.assertEqual(
                position_policy.get_exit_steps(),
                [{"profit_pct": 8.0, "sell_fraction": 0.5}],
            )

    def test_original_position_percentages_normalize_to_remaining_shares(self):
        with patch.dict(os.environ, {
            "TROJAN_EXIT_LADDER_JSON": (
                '[{"profit_pct":20,"sell_pct":30},'
                '{"profit_pct":50,"sell_pct":30},'
                '{"profit_pct":100,"sell_pct":40}]'
            ),
        }):
            steps = position_policy.get_exit_steps()
        self.assertAlmostEqual(steps[0]["sell_fraction"], 0.30)
        self.assertAlmostEqual(steps[1]["sell_fraction"], 30.0 / 70.0)
        self.assertEqual(steps[2]["sell_fraction"], 1.0)

    def test_original_position_percentages_cannot_overallocate(self):
        for raw in (
            '[{"profit_pct":10,"sell_pct":60},{"profit_pct":20,"sell_pct":50}]',
            '[{"profit_pct":10,"sell_pct":100},{"profit_pct":20,"sell_pct":1}]',
        ):
            with patch.dict(os.environ, {"TROJAN_EXIT_LADDER_JSON": raw}):
                with self.assertRaises(ValueError):
                    position_policy.get_exit_steps()

    def test_exit_ladder_rejects_mixed_sizing_bases(self):
        raw = '[{"profit_pct":10,"sell_pct":30},{"profit_pct":20,"sell_fraction":0.5}]'
        with patch.dict(os.environ, {"TROJAN_EXIT_LADDER_JSON": raw}):
            with self.assertRaises(ValueError):
                position_policy.get_exit_steps()

    def test_exit_step_requires_exactly_one_sizing_field(self):
        for row in (
            '{"profit_pct":10,"sell_pct":30,"sell_fraction":0.3}',
            '{"profit_pct":10}',
        ):
            with patch.dict(os.environ, {"TROJAN_EXIT_LADDER_JSON": "[" + row + "]"}):
                with self.assertRaises(ValueError):
                    position_policy.get_exit_steps()


if __name__ == "__main__":
    unittest.main()
