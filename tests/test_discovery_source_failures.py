import unittest
from unittest.mock import patch
import requests

from discovery import birdeye_scanner, gecko_scanner, pumpfun_scanner, token_aggregator


class DiscoverySourceFailureTests(unittest.TestCase):
    def test_gecko_transport_error_propagates_to_aggregator(self):
        with patch.object(gecko_scanner, "get_json", side_effect=requests.Timeout("timeout")):
            with self.assertRaises(requests.Timeout):
                gecko_scanner.scan_new_pools()

    def test_gecko_invalid_payload_is_a_source_failure(self):
        with patch.object(gecko_scanner, "get_json", return_value={"unexpected": []}):
            with self.assertRaisesRegex(ValueError, "invalid response shape"):
                gecko_scanner.scan_new_pools()

    def test_gecko_recent_updates_only_return_verified_solana_metadata(self):
        mint = "So11111111111111111111111111111111111111112"
        other_mint = "11111111111111111111111111111111"
        payload = {"data": [
            {"id": f"solana_{mint}", "attributes": {
                "address": mint, "name": "Wrapped SOL", "symbol": "WSOL",
                "image_url": "https://example.com/wsol.png",
            }},
            {"id": f"solana_{mint}", "attributes": {"address": mint, "name": "Duplicate"}},
            {"id": f"ethereum_{other_mint}", "attributes": {"address": other_mint}},
            {"id": f"solana_{mint}", "attributes": {"address": other_mint}},
            {"id": f"solana_{'z' * 44}", "attributes": {"address": "z" * 44}},
            {"attributes": {"address": other_mint}},
        ]}
        with patch.object(gecko_scanner, "get_json", return_value=payload) as fetch:
            result = gecko_scanner.scan_recent_updates()
        self.assertEqual(fetch.call_args.kwargs["params"], {"network": "solana"})
        self.assertEqual(result, [{
            "tokenAddress": mint, "chainId": "solana", "name": "Wrapped SOL", "symbol": "WSOL",
            "icon": "https://example.com/wsol.png", "discovery_sources": ["geckoterminal_recent_updates"],
        }])

    def test_aggregator_keeps_healthy_sources_and_reports_recovery(self):
        mint = "So11111111111111111111111111111111111111112"
        scanners = [
            ("gecko", gecko_scanner.scan_new_pools),
            ("healthy", lambda: [{"tokenAddress": mint, "chainId": "solana"}]),
            ("malformed", lambda: None),
        ]
        with (
            patch.object(token_aggregator, "SCANNERS", scanners),
            patch.object(token_aggregator, "_last_source_stats", {}),
            patch.object(token_aggregator, "_last_scan_at", None),
            patch.object(gecko_scanner, "get_json", side_effect=[requests.Timeout("timed out"), {"data": []}]),
        ):
            for gecko_ok in (False, True):
                result = token_aggregator.collect_tokens()
                self.assertEqual([item["tokenAddress"] for item in result], [mint])
                status = {item["name"]: item for item in token_aggregator.get_discovery_status()["sources"]}
                self.assertEqual(status["gecko"]["ok"], gecko_ok)
                self.assertEqual(status["gecko"]["count"], 0)
                self.assertTrue(status["healthy"]["ok"])
                self.assertFalse(status["malformed"]["ok"])

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
