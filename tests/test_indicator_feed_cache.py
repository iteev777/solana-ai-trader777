import unittest
from unittest.mock import patch

import indicators


class IndicatorFeedCacheTests(unittest.TestCase):
    def setUp(self):
        with indicators._cache_lock:
            indicators._cache.clear()

    def tearDown(self):
        with indicators._cache_lock:
            indicators._cache.clear()

    def test_fetch_uses_maximum_documented_candle_history(self):
        self.assertEqual(indicators.CANDLE_LIMIT, 1000)

    def test_cache_returns_copy_and_refreshes_lru_order(self):
        indicators._cache_put("pool-a", {"ready": True, "count": 1}, now=10.0)
        result = indicators._cache_get("pool-a", now=11.0)
        result["count"] = 999
        cached = indicators._cache_get("pool-a", now=12.0)
        self.assertEqual(cached["count"], 1)

    def test_cache_expires_using_monotonic_age(self):
        with patch.object(indicators, "CACHE_SECONDS", 5.0):
            indicators._cache_put("pool-expire", {"ready": True}, now=10.0)
            self.assertIsNotNone(indicators._cache_get("pool-expire", now=14.9))
            self.assertIsNone(indicators._cache_get("pool-expire", now=15.0))

    def test_cache_is_bounded_and_evicts_least_recently_used(self):
        with patch.object(indicators, "MAX_CACHE_ENTRIES", 2):
            indicators._cache_put("pool-a", {"n": 1}, now=1.0)
            indicators._cache_put("pool-b", {"n": 2}, now=2.0)
            indicators._cache_get("pool-a", now=3.0)
            indicators._cache_put("pool-c", {"n": 3}, now=4.0)
            self.assertIsNotNone(indicators._cache_get("pool-a", now=4.1))
            self.assertIsNone(indicators._cache_get("pool-b", now=4.1))
            self.assertIsNotNone(indicators._cache_get("pool-c", now=4.1))

    def test_public_api_rate_limit_cannot_be_configured_above_budget(self):
        with patch.dict("os.environ", {"TROJAN_OHLCV_MIN_INTERVAL": "0.1"}):
            self.assertEqual(indicators._minimum_fetch_interval(), 6.0)
        with patch.dict("os.environ", {"TROJAN_OHLCV_MIN_INTERVAL": "nan"}):
            self.assertEqual(indicators._minimum_fetch_interval(), 7.2)
        with patch.dict("os.environ", {"TROJAN_OHLCV_MIN_INTERVAL": "20"}):
            self.assertEqual(indicators._minimum_fetch_interval(), 20.0)


if __name__ == "__main__":
    unittest.main()
