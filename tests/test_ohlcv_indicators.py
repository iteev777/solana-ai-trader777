import time
import unittest
from indicators import calculate_multi_timeframe_indicators

class OhlcvIndicatorFreshnessTests(unittest.TestCase):
    def candles(self,latest):
        return [[latest-(119-i)*60,1+i*.001,1.01+i*.001,.99+i*.001,1.005+i*.001,100+i] for i in range(120)]
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
