import unittest
from unittest.mock import patch

from discovery import gecko_scanner


class GeckoRecentlyUpdatedDiscoveryTests(unittest.TestCase):
    def test_fetches_solana_scoped_recently_updated_tokens(self):
        payload = {
            "data": [
                {
                    "id": "solana_MINTA",
                    "attributes": {
                        "address": "MINTA",
                        "name": "Token A",
                        "symbol": "TKA",
                        "image_url": "https://example.com/a.png",
                        "gt_score": 72.5,
                        "metadata_updated_at": "2026-11-22T12:00:00Z",
                    },
                },
                {
                    "id": "solana_MINTA",
                    "attributes": {
                        "address": "MINTA",
                        "name": "Duplicate",
                        "symbol": "DUP",
                    },
                },
                {"id": "solana_MISSING_ATTRIBUTES"},
                {"id": "solana_MISSING_ADDRESS", "attributes": {"name": "No address"}},
                {
                    "id": "ethereum_WRONG_CHAIN",
                    "attributes": {
                        "address": "0x1234",
                        "name": "Wrong chain",
                        "symbol": "BAD",
                    },
                },
            ]
        }
        with patch.object(gecko_scanner, "get_json", return_value=payload) as fetch:
            result = gecko_scanner.scan_recently_updated_tokens()

        self.assertEqual(fetch.call_args.args[0], gecko_scanner.RECENTLY_UPDATED_URL)
        self.assertEqual(fetch.call_args.kwargs["params"], {"network": "solana"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tokenAddress"], "MINTA")
        self.assertEqual(result[0]["discovery_sources"], ["geckoterminal_recently_updated"])
        self.assertEqual(result[0]["name"], "Token A")
        self.assertEqual(result[0]["symbol"], "TKA")
        self.assertEqual(result[0]["icon"], "https://example.com/a.png")
        self.assertEqual(result[0]["gt_score"], 72.5)

    def test_invalid_top_level_payload_is_a_source_failure(self):
        with patch.object(gecko_scanner, "get_json", return_value={"unexpected": []}):
            with self.assertRaisesRegex(ValueError, "recently_updated.*invalid response shape"):
                gecko_scanner.scan_recently_updated_tokens()

    def test_transport_failure_propagates_to_source_health(self):
        with patch.object(gecko_scanner, "get_json", side_effect=TimeoutError("provider timeout")):
            with self.assertRaisesRegex(TimeoutError, "provider timeout"):
                gecko_scanner.scan_recently_updated_tokens()


if __name__ == "__main__":
    unittest.main()
