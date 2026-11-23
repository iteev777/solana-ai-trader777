import time
import unittest

from ohlcv_integrity import normalize_ohlcv_rows


class OhlcvIntegrityTests(unittest.TestCase):
    def test_sorts_deduplicates_and_rejects_invalid_candle_geometry(self):
        now = 10_000
        rows, quality = normalize_ohlcv_rows([
            [now - 60, 10, 12, 9, 11, 100],
            [now, 11, 13, 10, 12, 120],
            [now - 60, 10, 14, 9, 13, 150],
            [now - 120, 10, 9, 11, 10, 5],
        ], now=now)
        self.assertEqual([row["timestamp"] for row in rows], [now - 60, now])
        self.assertEqual(rows[0]["close"], 13)
        self.assertEqual(quality["duplicate_count"], 1)
        self.assertEqual(quality["invalid_count"], 1)
        self.assertTrue(quality["ready"])

    def test_stale_latest_candle_is_not_ready(self):
        rows, quality = normalize_ohlcv_rows([
            [1000 - 300, 10, 11, 9, 10, 1],
            [1000 - 240, 10, 11, 9, 10, 1],
        ], now=1000)
        self.assertEqual(len(rows), 2)
        self.assertTrue(quality["stale"])
        self.assertFalse(quality["ready"])
        self.assertEqual(quality["reason"], "Latest OHLCV candle is stale")

    def test_large_gap_is_not_filled_or_marked_ready(self):
        _, quality = normalize_ohlcv_rows([
            [400, 10, 11, 9, 10, 1],
            [460, 10, 11, 9, 10, 1],
            [820, 10, 11, 9, 10, 1],
            [880, 10, 11, 9, 10, 1],
        ], now=900)
        self.assertTrue(quality["discontinuous"])
        self.assertEqual(quality["gap_count"], 5)
        self.assertFalse(quality["ready"])

    def test_future_fractional_timestamp_and_nonfinite_values_are_rejected(self):
        rows, quality = normalize_ohlcv_rows([
            [1000.5, 10, 11, 9, 10, 1],
            [1100, 10, 11, 9, 10, 1],
            [900, 10, float("nan"), 9, 10, 1],
        ], now=1000)
        self.assertEqual(rows, [])
        self.assertEqual(quality["invalid_count"], 3)
        self.assertFalse(quality["ready"])

    def test_custom_freshness_thresholds_are_honored(self):
        _, quality = normalize_ohlcv_rows(
            [[1000 - 240, 10, 11, 9, 10, 1]],
            now=1000, max_age_seconds=300, max_gap_seconds=600
        )
        self.assertFalse(quality["stale"])
        self.assertTrue(quality["ready"])


if __name__ == "__main__":
    unittest.main()
