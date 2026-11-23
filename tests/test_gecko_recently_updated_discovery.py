import unittest
from unittest.mock import patch

from discovery import gecko_scanner


class GeckoRecentlyUpdatedDiscoveryTests(unittest.TestCase):
    @patch("discovery.gecko_scanner.get_json")
    def test_fetches_solana_recently_updated_tokens_and_normalizes_metadata(self, get_json):
        get_json.return_value = {
            "data": [
                {
                    "id": "solana_token-a",
                    "type": "token",
                    "attributes": {
                        "address": "MintA",
                        "name": "Token A",
                        "symbol": "TKA",
                        "image_url": "https://example.com/a.png",
                        "gt_score": 82.5,
                        "metadata_updated_at": "2026-11-23T10:00:00Z",
                    },
                },
                {
                    "id": "solana_token-a-duplicate",
                    "type": "token",
                    "attributes": {"address": "MintA", "name": "Duplicate"},
                },
                {"id": "solana_missing", "type": "token", "attributes": {"name": "No address"}},
                {"id": "bad", "type": "token", "attributes": None},
            ]
        }

        result = gecko_scanner.scan_recently_updated_tokens()

        get_json.assert_called_once()
        args, kwargs = get_json.call_args
        self.assertEqual(args[0], gecko_scanner.RECENTLY_UPDATED_URL)
        self.assertEqual(kwargs["params"], {"network": "solana"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tokenAddress"], "MintA")
        self.assertEqual(result[0]["chainId"], "solana")
        self.assertEqual(result[0]["name"], "Token A")
        self.assertEqual(result[0]["symbol"], "TKA")
        self.assertEqual(result[0]["icon"], "https://example.com/a.png")
        self.assertEqual(result[0]["geckoterminal_score"], 82.5)
        self.assertEqual(result[0]["discovery_sources"], ["geckoterminal_recently_updated"])

    @patch("discovery.gecko_scanner.get_json")
    def test_invalid_response_returns_empty_list(self, get_json):
        get_json.return_value = {"data": {"items": []}}
        self.assertEqual(gecko_scanner.scan_recently_updated_tokens(), [])

    @patch("discovery.gecko_scanner.get_json", side_effect=RuntimeError("provider unavailable"))
    def test_provider_failure_is_isolated(self, _get_json):
        self.assertEqual(gecko_scanner.scan_recently_updated_tokens(), [])


if __name__ == "__main__":
    unittest.main()
