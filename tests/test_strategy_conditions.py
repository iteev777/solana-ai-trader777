import unittest
from unittest.mock import patch

import strategy_conditions
import strategy_rules


class StrategyConditionTests(unittest.TestCase):
    def setUp(self):
        self.token = {
            "symbol": "TEST",
            "priceUsd": 12.0,
            "liquidity": {"usd": 50000},
            "volume": {"m5": 8000, "h24": 200000},
            "priceChange": {"m5": 4.0, "h1": 8.0, "h6": 12.0, "h24": 20.0},
            "txns": {
                "m5": {"buys": 80, "sells": 20},
                "h1": {"buys": 400, "sells": 100},
                "h6": {"buys": 1500, "sells": 500},
                "h24": {"buys": 5000, "sells": 2000},
            },
            "pairCreatedAt": 1760000000000,
            "indicators": {
                "rsi_14": 42,
                "ema_9": 11,
                "ema_21": 10,
                "ema_cross_bullish": True,
                "breakout_20": True,
                "volume_spike_ratio": 2.5,
                "bollinger_percent_b": 0.7,
                "atr_percent": 4.0,
                "above_vwap": True,
                "candle_body_pct": 2.0,
                "candle_range_pct": 3.0,
                "consecutive_green_candles": 3,
                "consecutive_red_candles": 0,
                "previous_high_20": 15.0,
            },
            "position_holding_minutes": 45,
            "position_profit_pct": 12,
            "profit_giveback_pct": 20,
            "position_add_count": 2,
            "liquidity_change_since_entry_pct": 8,
            "trade_size_usd": 5,
        }
        self.context = strategy_rules._context(self.token, 80)

    def evaluate(self, name, params=None):
        return strategy_conditions.evaluate_condition(
            {"condition": name, "params": params or {}}, self.context
        )

    def test_catalog_exposes_twenty_four_named_conditions(self):
        self.assertEqual(len(strategy_conditions.CONDITION_TYPES), 24)
        self.assertEqual(len(strategy_conditions.get_condition_catalog()), 24)

    def test_rsi_and_bollinger_conditions(self):
        self.assertTrue(self.evaluate("Rsi", {"comparison": "ABOVE", "threshold": 30})[0])
        self.assertTrue(self.evaluate("BollingerBands", {"comparison": "ABOVE", "threshold": 0.5})[0])

    def test_volume_spike_and_buy_pressure(self):
        self.assertTrue(self.evaluate("VolumeSpike", {"multiplier": 2})[0])
        self.assertTrue(self.evaluate("BuyPressure", {"threshold": 0.7})[0])

    def test_transaction_velocity_compares_normalized_rates(self):
        passed, _ = self.evaluate("TransactionVelocity", {
            "window": "5m_vs_1h", "comparison": "ABOVE",
            "ratio": 1.5, "min_transactions": 10
        })
        self.assertTrue(passed)

    def test_breakout_ma_cross_and_vwap(self):
        self.assertTrue(self.evaluate("PriceBreakout", {"direction": "ABOVE"})[0])
        self.assertTrue(self.evaluate("MaCross", {"direction": "BULLISH"})[0])
        self.assertTrue(self.evaluate("Vwap", {"position": "ABOVE"})[0])

    def test_liquidity_and_market_conditions(self):
        self.assertTrue(self.evaluate("LiquidityLevel", {
            "comparison": "ABOVE", "threshold_usd": 10000
        })[0])
        self.assertTrue(self.evaluate("LiquidityToSize", {
            "minimum_multiple": 100, "trade_size_usd": 5
        })[0])
        self.assertTrue(self.evaluate("PriceChangePercent", {
            "timeframe": "h1", "comparison": "ABOVE", "threshold_pct": 5
        })[0])

    def test_position_conditions(self):
        self.assertTrue(self.evaluate("PositionAdds", {
            "comparison": "AT_LEAST", "count": 2
        })[0])
        self.assertTrue(self.evaluate("PositionHoldingTime", {
            "comparison": "ABOVE", "minutes": 30
        })[0])
        self.assertTrue(self.evaluate("PositionProfit", {
            "comparison": "ABOVE", "profit_pct": 5
        })[0])
        self.assertTrue(self.evaluate("ProfitGiveback", {
            "comparison": "BELOW", "giveback_pct": 25
        })[0])

    def test_price_structure_conditions(self):
        self.assertTrue(self.evaluate("CandleSize", {
            "metric": "body", "comparison": "ABOVE", "threshold_pct": 1
        })[0])
        self.assertTrue(self.evaluate("ConsecutiveCandles", {
            "direction": "GREEN", "minimum": 2
        })[0])
        self.assertTrue(self.evaluate("PullbackFromHigh", {
            "minimum_pct": 10, "maximum_pct": 30
        })[0])
        self.assertTrue(self.evaluate("DrawdownFromPeak", {
            "minimum_pct": 10, "maximum_pct": 30
        })[0])

    def test_liquidity_change_and_volatility(self):
        self.assertTrue(self.evaluate("LiquidityChangeSinceEntry", {
            "comparison": "ABOVE", "threshold_pct": 5
        })[0])
        self.assertTrue(self.evaluate("VolatilityRange", {
            "comparison": "BELOW", "threshold_pct": 10
        })[0])

    def test_missing_data_fails_closed(self):
        context = strategy_rules._context({"symbol": "X"}, 50)
        passed, reasons = strategy_conditions.evaluate_condition({
            "condition": "Rsi", "params": {"comparison": "BELOW", "threshold": 30}
        }, context)
        self.assertFalse(passed)
        self.assertIn("unavailable", reasons[0])

    def test_invalid_condition_parameters_rejected(self):
        with self.assertRaises(ValueError):
            strategy_conditions.validate_condition({
                "condition": "Rsi", "params": {"threshold": 120}
            })

    def test_named_condition_works_inside_strategy_rule_tree(self):
        config = '{"main":{"all":[{"condition":"BuyPressure","params":{"threshold":0.7}},{"condition":"VolumeSpike","params":{"multiplier":2}}]}}'
        with patch.dict("os.environ", {"TROJAN_STRATEGY_RULES": config}, clear=False):
            passed, reason = strategy_rules.evaluate_strategy_rules(self.token, 80, "main")
        self.assertTrue(passed, reason)

    def test_unknown_named_condition_fails_validation(self):
        with self.assertRaises(ValueError):
            strategy_rules.validate_rules_config({
                "main": {"condition": "NotARealCondition", "params": {}}
            })


if __name__ == "__main__":
    unittest.main()
