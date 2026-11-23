import unittest

from indicators import (
    calculate_indicators,
    calculate_rsi,
    ema_series,
    wilder_average,
)


class StandardIndicatorMathTests(unittest.TestCase):
    def test_ema_uses_simple_average_seed(self):
        self.assertEqual(ema_series([1, 2, 3, 4, 5], 3), [2.0, 3.0, 4.0])
        self.assertEqual(ema_series([1, 2], 3), [])

    def test_rsi_uses_wilder_smoothing(self):
        value = calculate_rsi([1, 2, 1, 2, 1, 2], period=3)
        self.assertAlmostEqual(value, 100.0 - 100.0 / (1.0 + 17.0 / 10.0), places=10)

    def test_rsi_flat_and_one_direction_series_have_defined_values(self):
        self.assertEqual(calculate_rsi([5] * 16), 50.0)
        self.assertEqual(calculate_rsi(list(range(1, 17))), 100.0)
        self.assertEqual(calculate_rsi(list(range(16, 0, -1))), 0.0)

    def test_wilder_average_uses_sma_seed_then_recursive_smoothing(self):
        self.assertAlmostEqual(wilder_average([1, 2, 3, 4, 5], 3), 31.0 / 9.0)
        self.assertIsNone(wilder_average([1, 2], 3))

    def test_indicator_output_declares_standard_calculation_methods(self):
        candles = []
        for index in range(50):
            close = 1.0 + index * 0.01
            candles.append([
                1700000000 + index * 60,
                close - 0.005,
                close + 0.01,
                close - 0.01,
                close,
                100 + index,
            ])
        result = calculate_indicators(candles)
        self.assertTrue(result["ready"])
        self.assertEqual(result["indicator_methods"]["rsi"], "WILDER")
        self.assertEqual(result["indicator_methods"]["ema"], "SMA_SEEDED_EXPONENTIAL")
        self.assertIsNotNone(result["macd_histogram"])
        self.assertIsNotNone(result["atr_14"])

    def test_macd_remains_unknown_when_signal_history_is_insufficient(self):
        candles = []
        for index in range(22):
            close = 1.0 + index * 0.01
            candles.append([
                1700000000 + index * 60,
                close - 0.005,
                close + 0.01,
                close - 0.01,
                close,
                100,
            ])
        result = calculate_indicators(candles)
        self.assertTrue(result["ready"])
        self.assertIsNone(result["macd_histogram"])
        self.assertIsNone(result["macd_bullish"])

    def test_malformed_ohlc_candle_is_excluded(self):
        candles = []
        for index in range(22):
            close = 1.0 + index * 0.01
            candles.append([
                1700000000 + index * 60,
                close - 0.005,
                close + 0.01,
                close - 0.01,
                close,
                100,
            ])
        candles.append([1700001320, 2.0, 1.0, 1.5, 1.8, 100])
        result = calculate_indicators(candles)
        self.assertEqual(result["candle_count"], 22)
        self.assertEqual(result["timestamp"], 1700001260)


if __name__ == "__main__":
    unittest.main()
