import os
import time
import unittest
from unittest.mock import patch

import live_execution
import position_policy
import indicators



class MultiTimeframeIndicatorTests(unittest.TestCase):
    def _candles(self, count=120):
        rows = []
        price = 1.0
        for index in range(count):
            next_price = price * (1.001 if index % 9 else 0.998)
            rows.append([
                1_700_000_000 + index * 60,
                price, max(price, next_price) * 1.002,
                min(price, next_price) * 0.998,
                next_price, 100 + index
            ])
            price = next_price
        return rows

    def test_aggregation_preserves_ohlcv_semantics(self):
        rows = self._candles(6)
        aggregated = indicators.aggregate_candles(rows, 180)
        self.assertEqual(len(aggregated), 2)
        self.assertEqual(aggregated[0][1], rows[0][1])
        self.assertEqual(aggregated[0][4], rows[2][4])
        self.assertAlmostEqual(aggregated[0][5], sum(row[5] for row in rows[:3]))

    def test_multi_timeframe_marks_insufficient_history_not_ready(self):
        result = indicators.calculate_multi_timeframe_indicators(self._candles(120))
        self.assertEqual(result["timeframe_count"], 7)
        self.assertTrue(result["timeframes"]["1m"]["ready"])
        self.assertFalse(result["timeframes"]["4h"]["ready"])
        self.assertIn(result["trend_consensus"], {"bullish", "bearish", "mixed"})

    def test_aggregation_rejects_invalid_interval(self):
        with self.assertRaises(ValueError):
            indicators.aggregate_candles(self._candles(), 90)


class PositionPolicyTests(unittest.TestCase):
    def test_dca_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(position_policy.get_dca_steps(), [])

    def test_multi_tier_dca_policy(self):
        raw = '[{"drop_pct":5,"add_usd":2},{"drop_pct":10,"add_usd":3}]'
        with patch.dict(os.environ, {
            "TROJAN_DCA_ENABLED": "true",
            "TROJAN_DCA_STEPS_JSON": raw
        }, clear=True):
            self.assertEqual(
                position_policy.get_dca_steps(),
                [{"drop_pct": 5.0, "add_usd": 2.0},
                 {"drop_pct": 10.0, "add_usd": 3.0}]
            )

    def test_exit_ladder_validates_remaining_position_fractions(self):
        raw = '[{"profit_pct":8,"sell_fraction":0.35},{"profit_pct":15,"sell_fraction":0.5}]'
        with patch.dict(os.environ, {"TROJAN_EXIT_LADDER_JSON": raw}, clear=True):
            steps = position_policy.get_exit_steps()
            self.assertEqual(len(steps), 2)
            self.assertEqual(steps[1]["sell_fraction"], 0.5)

    def test_exit_ladder_rejects_non_increasing_tiers(self):
        raw = '[{"profit_pct":15,"sell_fraction":0.5},{"profit_pct":10,"sell_fraction":0.2}]'
        with patch.dict(os.environ, {"TROJAN_EXIT_LADDER_JSON": raw}, clear=True):
            with self.assertRaises(ValueError):
                position_policy.get_exit_steps()


class LiveExecutionSafetyTests(unittest.TestCase):
    def test_live_execution_is_hard_locked(self):
        self.assertTrue(live_execution.get_execution_status()["execution_locked"])
        with self.assertRaises(live_execution.LiveExecutionLocked):
            live_execution.execute_plan({"execution_locked": True})

    def test_plan_rejects_stale_quote(self):
        quote = {
            "provider": "jupiter",
            "input_mint": "So11111111111111111111111111111111111111112",
            "output_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "in_amount": 1000,
            "out_amount": 2000,
            "slippage_bps": 100,
            "price_impact_percent": 0.1,
            "quoted_at": time.time() - 30
        }
        with self.assertRaises(ValueError):
            live_execution.build_execution_plan(quote, "BUY")

    def test_plan_computes_minimum_output(self):
        quote = {
            "provider": "jupiter",
            "input_mint": "So11111111111111111111111111111111111111112",
            "output_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "in_amount": 1000,
            "out_amount": 2000,
            "slippage_bps": 100,
            "price_impact_percent": 0.1,
            "quoted_at": time.time()
        }
        plan = live_execution.build_execution_plan(quote, "BUY")
        self.assertEqual(plan["minimum_out_amount"], 1980)
        self.assertTrue(plan["execution_locked"])


if __name__ == "__main__":
    unittest.main()
