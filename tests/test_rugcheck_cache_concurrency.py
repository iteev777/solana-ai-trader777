import threading
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import rugcheck
from requests import HTTPError


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "score_normalised": 2,
            "risks": [],
            "mintAuthority": None,
            "freezeAuthority": None,
            "topHolders": [{"address": "holder1", "pct": 5.0}],
            "rugged": False,
        }


class FakeRateLimitedResponse:
    status_code = 429

    def raise_for_status(self):
        raise AssertionError("429 must be handled before raise_for_status")


class RugCheckCacheConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_patch = patch.object(
            rugcheck.database, "DATABASE",
            os.path.join(self.temp_dir.name, "rugcheck-cache.sqlite3"),
        )
        self.database_patch.start()
        rugcheck.clear_rugcheck_cache()
        with rugcheck._cache_lock:
            rugcheck._inflight.clear()

    def tearDown(self):
        rugcheck.clear_rugcheck_cache()
        with rugcheck._cache_lock:
            rugcheck._inflight.clear()
        self.database_patch.stop()
        self.temp_dir.cleanup()

    @patch("rugcheck.get_json")
    def test_successful_report_is_cached(self, get):
        get.return_value = FakeResponse().json()
        first = rugcheck.get_rugcheck_report("MintCache1")
        second = rugcheck.get_rugcheck_report("MintCache1")
        self.assertIs(first, second)
        get.assert_called_once_with(
            "https://api.rugcheck.xyz/v1/tokens/MintCache1/report",
            headers={"Accept": "application/json"},
            timeout=15,
            cache_ttl=0,
            max_retries=1,
        )

    @patch("rugcheck.get_json")
    def test_report_survives_process_local_cache_reset_via_sqlite(self, get):
        get.return_value = FakeResponse().json()
        first = rugcheck.get_rugcheck_report("MintPersisted")
        with rugcheck._cache_lock:
            rugcheck._cache.clear()
        second = rugcheck.get_rugcheck_report("MintPersisted")
        self.assertEqual(first, second)
        get.assert_called_once()

    @patch("rugcheck.get_json")
    def test_concurrent_same_mint_requests_are_coalesced(self, get):
        entered = threading.Event()
        release = threading.Event()

        def delayed_get(*args, **kwargs):
            entered.set()
            release.wait(timeout=2)
            return FakeResponse().json()

        get.side_effect = delayed_get
        results = []
        errors = []

        def worker():
            try:
                results.append(rugcheck.get_rugcheck_report("MintConcurrent1"))
            except Exception as error:
                errors.append(error)

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        self.assertTrue(entered.wait(timeout=1))
        second.start()
        time.sleep(0.05)
        release.set()
        first.join(timeout=2)
        second.join(timeout=2)

        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertIs(results[0], results[1])
        get.assert_called_once()

    @patch("rugcheck.get_json")
    def test_rate_limit_is_not_cached_and_next_call_can_retry(self, get):
        get.side_effect = [HTTPError("429 Too Many Requests"), FakeResponse().json()]
        self.assertIsNone(rugcheck.get_rugcheck_report("Mint429"))
        result = rugcheck.get_rugcheck_report("Mint429")
        self.assertIsInstance(result, dict)
        self.assertEqual(get.call_count, 2)

    @patch.dict("os.environ", {"TROJAN_RUGCHECK_CACHE_SECONDS": "0"})
    @patch("rugcheck.get_json")
    def test_zero_ttl_disables_cache(self, get):
        get.return_value = FakeResponse().json()
        first = rugcheck.get_rugcheck_report("MintNoCache")
        second = rugcheck.get_rugcheck_report("MintNoCache")
        self.assertIsNot(first, second)
        self.assertEqual(get.call_count, 2)

    @patch("rugcheck.MAX_CACHE_ENTRIES", 2)
    def test_cache_evicts_oldest_entry_at_capacity(self):
        rugcheck._store_cached_report("MintA", {"mint": "A"})
        rugcheck._store_cached_report("MintB", {"mint": "B"})
        rugcheck._store_cached_report("MintC", {"mint": "C"})
        self.assertEqual(len(rugcheck._cache), 2)
        self.assertNotIn("MintA", rugcheck._cache)
        self.assertIn("MintB", rugcheck._cache)
        self.assertIn("MintC", rugcheck._cache)


if __name__ == "__main__":
    unittest.main()
