import unittest
from unittest.mock import patch
import copy_freshness


class CopyFreshnessTests(unittest.TestCase):
    def test_fresh_event_passes(self):
        with patch.dict("os.environ", {"TROJAN_COPY_MAX_EVENT_AGE_SECONDS": "30"}):
            result = copy_freshness.evaluate_event_freshness(990, 999, now=1000)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["age_seconds"], 10)

    def test_old_event_is_rejected(self):
        result = copy_freshness.evaluate_event_freshness(900, 999, now=1000)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "COPY_EVENT_STALE")

    def test_missing_block_time_fails_closed(self):
        result = copy_freshness.evaluate_event_freshness(None, now=1000)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "COPY_EVENT_BLOCK_TIME_MISSING")


if __name__ == "__main__":
    unittest.main()
