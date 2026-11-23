import time
import unittest
from unittest.mock import patch

import indicators
from indicators import calculate_multi_timeframe_indicators

class OhlcvIndicatorFreshnessTests(unittest.TestCase):
    def candles(self,latest):
        return [[latest-(119-i)*60,1+i*.001,1.01+i*.001,.99+i*.001,1.005+i*.001,100+i] for i in range(120)]
    def test_timeframe_keys_match_reference_seven_timeframes(self):
        expected = {"1m", "5m", "15m", "1h", "4h", "12h", "1d"}
        result = calculate_multi_timeframe_indicators(self.candles(int(time.time()) - 10))
        self.assertEqual(set(result["timeframes"]), expected)
        self.assertEqual(result["timeframe_count"], 7)
        self.assertFalse(result["timeframes"]["12h"]["ready"])
        self.assertFalse(result["timeframes"]["1d"]["ready"])


    def test_fetch_uses_persisted_history_to_make_daily_timeframe_ready(self):
        # A 1m provider response is capped at 1,000 candles (~16.7 hours), but
        # the database retains 30 days. The higher timeframes must use that
        # retained history rather than remaining permanently not-ready.
        now = (int(time.time()) // 60) * 60 - 60
        count = 23 * 24 * 60
        history = []
        for index in range(count):
            timestamp = now - (count - 1 - index) * 60
            price = 1.0 + index * 0.00001
            history.append({
                "timestamp": timestamp,
                "open": price,
                "high": price + 0.01,
                "low": price - 0.01,
                "close": price + 0.005,
                "volume": 100.0,
            })
        raw = [
            [row["timestamp"], row["open"], row["high"], row["low"], row["close"], row["volume"]]
            for row in history[-indicators.CANDLE_LIMIT:]
        ]
        payload = {"data": {"attributes": {"ohlcv_list": raw}}}

        with patch.object(indicators, "_last_fetch", 0.0), \
             patch.object(indicators, "_last_prune", time.monotonic()), \
             patch.object(indicators, "_cache", {}), \
             patch.object(indicators, "get_json", return_value=payload), \
             patch("database.save_market_candles", return_value=len(raw)), \
             patch("database.get_market_candles", return_value=history) as get_history:
            result = indicators.fetch_pool_indicators("Pool111", force=True)

        get_history.assert_called_once_with(
            "Pool111", "1m", limit=indicators.MAX_HISTORY_CANDLES
        )
        self.assertIn("timeframes", result, msg="OHLCV fetch failed: " + repr(result))
        self.assertTrue(result["timeframes"]["1d"]["ready"])
        self.assertTrue(result["timeframes"]["12h"]["ready"])

    def test_recent_series_ready(self):
        result=calculate_multi_timeframe_indicators(self.candles(int(time.time())-10))
        self.assertTrue(result["ready"]); self.assertTrue(result["ohlcv_quality"]["ready"])
        self.assertEqual(result["ohlcv_quality"]["valid_count"],120)
    def test_stale_series_fails_closed(self):
        result=calculate_multi_timeframe_indicators(self.candles(int(time.time())-600))
        self.assertFalse(result["ready"]); self.assertTrue(result["ohlcv_quality"]["stale"])
        self.assertFalse(result["timeframes"]["1m"]["ready"])
    def test_invalid_candles_are_counted(self):
        latest=int(time.time())-10; rows=self.candles(latest)
        rows[40]=[latest-79*60,1,.5,1.1,1,100]
        result=calculate_multi_timeframe_indicators(rows)
        self.assertEqual(result["ohlcv_quality"]["invalid_count"],1)
        self.assertEqual(result["ohlcv_quality"]["valid_count"],119)

if __name__=="__main__": unittest.main()
