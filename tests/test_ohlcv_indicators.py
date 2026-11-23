import time
import unittest

from indicators import calculate_multi_timeframe_indicators


class OhlcvIndicatorFreshnessTests(unittest.TestCase):
    def candles(self, latest_timestamp):
        return [
            [latest_timestamp - (119 - index) * 60,
             1.0 + index * 0.001,
             1.01 + index * 0.001,
             0.99 + index * 0.001,
             1.005 + index * 0.001,
             100.0 + index]
            for index in range(120)
        ]

    def test_recent_valid_series_produces_ready_indicators_and_quality(self):
        latest = int(time.time()) - 10
        result = calculate_multi_timeframe_indicators(self.candles(latest))
        self.assertTrue(result["ready"])
        self.assertTrue(result["ohlcv_quality"]["ready"])
        self.assertEqual(result["ohlcv_quality"]["valid_count"], 120)
        self.assertTrue(result["timeframes"]["1m"]["ready"])

    def test_stale_series_fails_closed_across_timeframes(self):
        latest = int(time.time()) - 600
        result = calculate_multi_timeframe_indicators(self.candles(latest))
        self.assertFalse(result["ready"])
        self.assertTrue(result["ohlcv_quality"]["stale"])
        self.assertFalse(result["timeframes"]["1m"]["ready"])
        self.assertIn("stale", result["timeframes"]["1m"]["reason"].lower())

    def test_malformed_candles_are_discarded_and_counted(self):
        latest = int(time.time()) - 10
        rows = self.candles(latest)
        rows[40] = [latest - 79 * 60, 1, 0.5, 1.1, 1, 100]
        result = calculate_multi_timeframe_indicators(rows)
        self.assertEqual(result["ohlcv_quality"]["invalid_count"], 1)
        self.assertEqual(result["ohlcv_quality"]["valid_count"], 119)


if __name__ == "__main__":
    unittest.main()
