import unittest
from unittest.mock import patch
import requests

from discovery import gecko_scanner, birdeye_scanner, pumpfun_scanner


class DiscoverySourceFailureTests(unittest.TestCase):
    def test_gecko_transport_error_propagates_to_aggregator(self):
        with patch.object(gecko_scanner, "get_json", side_effect=requests.Timeout("timeout")):
            with self.assertRaises(requests.Timeout):
                gecko_scanner.scan_new_pools()

    def test_gecko_invalid_payload_is_a_source_failure(self):
        with patch.object(gecko_scanner, "get_json", return_value={"unexpected": []}):
            with self.assertRaisesRegex(ValueError, "invalid response shape"):
                gecko_scanner.scan_new_pools()

    def test_birdeye_transport_error_propagates_to_aggregator(self):
        with patch.object(birdeye_scanner, "API_KEY", "test-key"), \
             patch.object(birdeye_scanner, "_quota_disabled_until", 0), \
             patch.object(birdeye_scanner, "get_json", side_effect=requests.Timeout("timeout")):
            with self.assertRaises(requests.Timeout):
                birdeye_scanner.scan_birdeye()

    def test_birdeye_missing_key_is_reported_as_unavailable(self):
        with patch.object(birdeye_scanner, "API_KEY", None):
            with self.assertRaisesRegex(RuntimeError, "API_KEY"):
                birdeye_scanner.scan_birdeye()

    def test_meme_listing_transport_error_propagates_to_aggregator(self):
        with patch.object(pumpfun_scanner, "API_KEY", "test-key"), \
             patch.object(pumpfun_scanner, "_quota_disabled_until", 0), \
             patch.object(pumpfun_scanner, "get_json", side_effect=requests.Timeout("timeout")):
            with self.assertRaises(requests.Timeout):
                pumpfun_scanner.scan_pumpfun()

    def test_meme_listing_missing_key_is_reported_as_unavailable(self):
        with patch.object(pumpfun_scanner, "API_KEY", None):
            with self.assertRaisesRegex(RuntimeError, "API_KEY"):
                pumpfun_scanner.scan_pumpfun()


if __name__ == "__main__":
    unittest.main()