import unittest
from unittest.mock import patch

import copy_freshness


class CopyFreshnessTests(unittest.TestCase):
    def test_fresh_event_passes(self):
        with patch.dict("os.environ", {"TROJAN_COPY_MAX_EVENT_AGE_SECONDS": "30"}):
            result = copy_freshness.evaluate_event_freshness(990, 999, now=1000)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["age_seconds"], 9)

    def test_old_event_is_rejected_using_receipt_time(self):
        result = copy_freshness.evaluate_event_freshness(900, 950, now=1000)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "COPY_EVENT_STALE")
        self.assertEqual(result["age_seconds"], 50)

    def test_processing_queue_delay_does_not_turn_fresh_event_stale(self):
        result = copy_freshness.evaluate_event_freshness(990, 999, now=1100)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["age_seconds"], 9)

    def test_missing_block_time_fails_closed(self):
        result = copy_freshness.evaluate_event_freshness(None, now=1000)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "COPY_EVENT_TIMESTAMP_INVALID")

    def test_future_block_time_is_rejected(self):
        result = copy_freshness.evaluate_event_freshness(1010, 1000, now=1000)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "COPY_EVENT_FROM_FUTURE")

    def test_future_receipt_timestamp_is_rejected(self):
        result = copy_freshness.evaluate_event_freshness(990, 1010, now=1000)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "COPY_EVENT_RECEIPT_FROM_FUTURE")

    def test_invalid_max_age_falls_back_to_safe_default(self):
        with patch.dict("os.environ", {"TROJAN_COPY_MAX_EVENT_AGE_SECONDS": "nan"}):
            result = copy_freshness.evaluate_event_freshness(960, 1000, now=1000)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["max_age_seconds"], 30.0)


if __name__ == "__main__":
    unittest.main()
