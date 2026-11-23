import unittest
from unittest.mock import patch

import filter_pipeline


class PoolMetricFilterTests(unittest.TestCase):
    def test_unconfigured_pool_metrics_do_not_block(self):
        result = filter_pipeline._pool_metrics_stage({})
        self.assertEqual(result["status"], "PASS")

    def test_minimum_pool_count_waits_when_registry_is_unknown(self):
        with patch.dict("os.environ", {"TROJAN_FILTER_MIN_POOL_COUNT": "2"}):
            result = filter_pipeline._pool_metrics_stage({})
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "POOL_COUNT_UNKNOWN")

    def test_pool_count_below_floor_is_rejected(self):
        with patch.dict("os.environ", {"TROJAN_FILTER_MIN_POOL_COUNT": "3"}):
            result = filter_pipeline._pool_metrics_stage({"pool_count": 2})
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "POOL_COUNT_BELOW_MINIMUM")

    def test_pool_count_above_ceiling_is_rejected(self):
        with patch.dict("os.environ", {"TROJAN_FILTER_MAX_POOL_COUNT": "3"}):
            result = filter_pipeline._pool_metrics_stage({"pool_count": 4})
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "POOL_COUNT_ABOVE_MAXIMUM")

    def test_fractional_pool_count_is_rejected(self):
        result = filter_pipeline._pool_metrics_stage({"pool_count": 1.5})
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "INVALID_POOL_COUNT")

    def test_minimum_reserve_waits_when_missing(self):
        with patch.dict("os.environ", {"TROJAN_FILTER_MIN_POOL_RESERVE_USD": "10000"}):
            result = filter_pipeline._pool_metrics_stage({})
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "POOL_RESERVE_UNKNOWN")

    def test_reserve_below_floor_is_rejected(self):
        with patch.dict("os.environ", {"TROJAN_FILTER_MIN_POOL_RESERVE_USD": "10000"}):
            result = filter_pipeline._pool_metrics_stage({"reserve_in_usd": 9999})
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "POOL_RESERVE_BELOW_MINIMUM")

    def test_pool_count_and_reserve_pass_when_both_meet_floors(self):
        with patch.dict("os.environ", {
            "TROJAN_FILTER_MIN_POOL_COUNT": "2",
            "TROJAN_FILTER_MIN_POOL_RESERVE_USD": "10000",
        }):
            result = filter_pipeline._pool_metrics_stage({
                "pool_count": 3,
                "reserve_in_usd": 25000,
            })
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["metrics"]["pool_count"], 3)
        self.assertEqual(result["metrics"]["reserve_in_usd"], 25000)

    def test_negative_reserve_is_rejected(self):
        result = filter_pipeline._pool_metrics_stage({"reserve_in_usd": -1})
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "INVALID_POOL_RESERVE")


if __name__ == "__main__":
    unittest.main()
