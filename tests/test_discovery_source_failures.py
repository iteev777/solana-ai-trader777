import json
from collections import OrderedDict
from queue import Queue
import unittest
from unittest.mock import patch
import requests

from discovery import birdeye_scanner, gecko_scanner, pumpfun_scanner, token_aggregator


class DiscoverySourceFailureTests(unittest.TestCase):
    def test_pumpportal_validates_before_queueing_and_retries_full_queue(self):
        import sniper_scanner

        mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
        pending = Queue(maxsize=1)
        seen = OrderedDict()
        with (
            patch.object(sniper_scanner, "new_token_queue", pending),
            patch.object(sniper_scanner, "seen_tokens", seen),
        ):
            for invalid in ("z" * 44, "So11111111111111111111111111111111111111112"):
                sniper_scanner.on_message(None, json.dumps({"mint": invalid}))
            self.assertTrue(pending.empty())
            self.assertFalse(seen)
            pending.put("occupied")
            event = json.dumps({"mint": f" {mint} ", "name": "Fixture"})
            sniper_scanner.on_message(None, event)
            self.assertNotIn(mint, seen)
            self.assertEqual(pending.get_nowait(), "occupied")
            sniper_scanner.on_message(None, event)
            sniper_scanner.on_message(None, event)
            self.assertEqual(pending.qsize(), 1)
            self.assertEqual(pending.get_nowait()["tokenAddress"], mint)

    def test_gecko_transport_error_propagates_to_aggregator(self):
        with patch.object(gecko_scanner, "get_json", side_effect=requests.Timeout("timeout")):
            with self.assertRaises(requests.Timeout):
                gecko_scanner.scan_new_pools()

    def test_gecko_invalid_payload_is_a_source_failure(self):
        with patch.object(gecko_scanner, "get_json", return_value={"unexpected": []}):
            with self.assertRaisesRegex(ValueError, "invalid response shape"):
                gecko_scanner.scan_new_pools()

    def test_gecko_pool_deduplication_prefers_identity_matched_snapshot(self):
        mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

        def pool(address, price, resource_id):
            return {
                "id": resource_id,
                "attributes": {"address": address, "reserve_in_usd": "100", "base_token_price_usd": price},
                "relationships": {"base_token": {"data": {"id": f"solana_{mint}"}}},
            }

        records = [
            pool("wrong", "9", "ethereum_wrong"),
            pool("empty", None, "solana_empty"),
            pool("priced", "0.25", "solana_priced"),
        ]
        with patch.object(gecko_scanner, "get_json", return_value={"data": records}):
            token, = gecko_scanner.scan_new_pools()
        self.assertEqual(token["poolAddress"], "priced")
        self.assertEqual(token["market_data"]["priceUsd"], "0.25")

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
        mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
        scanners = [
            ("gecko", gecko_scanner.scan_new_pools),
            ("healthy", lambda: [{"tokenAddress": mint, "chainId": "solana"}]),
            ("malformed", lambda: None),
        ]
        with (
            patch.object(token_aggregator, "SCANNERS", scanners),
            patch.object(token_aggregator, "_last_scan", (None, 0.0, ())),
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

    def test_aggregator_validates_mints_and_publishes_isolated_status(self):
        mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
        addresses = [f" {mint} ", "z" * 44, "0" * 32, "1" * 32,
                     "So11111111111111111111111111111111111111112",
                     "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"]
        records = [{"chainId": "solana", "tokenAddress": address} for address in addresses]
        with (
            patch.object(token_aggregator, "SCANNERS", [("fixture", lambda: records)]),
            patch.object(token_aggregator, "_last_scan", (None, 0.0, ())),
        ):
            self.assertEqual([row["tokenAddress"] for row in token_aggregator.collect_tokens()], [mint])
            report = token_aggregator.get_discovery_status()
            self.assertEqual(report["sources"][0]["invalid_count"], 5)
            self.assertEqual(report["sources"][0]["valid_count"], 1)
            self.assertGreaterEqual(report["last_scan_duration_ms"], 0)
            report["sources"][0]["ok"] = False
            self.assertTrue(token_aggregator.get_discovery_status()["sources"][0]["ok"])

    def test_birdeye_transport_error_propagates_to_aggregator(self):
        with patch.object(birdeye_scanner, "API_KEY", "test-key"), \
             patch.object(birdeye_scanner, "_quota_disabled_until", 0), \
             patch.object(birdeye_scanner, "get_json", side_effect=requests.Timeout("timeout")):
            with self.assertRaises(requests.Timeout):
                birdeye_scanner.scan_birdeye()

    def test_birdeye_quota_error_does_not_call_trending(self):
        response = requests.Response()
        response.status_code = 400
        response._content = b"Compute units usage limit exceeded"
        with (
            patch.object(birdeye_scanner, "API_KEY", "test-key"),
            patch.object(birdeye_scanner, "_quota_disabled_until", 0),
            patch.object(birdeye_scanner, "get_json",
                         side_effect=requests.HTTPError(response=response)) as fetch,
        ):
            with self.assertRaisesRegex(RuntimeError, "quota exhausted"):
                birdeye_scanner.scan_birdeye()
            with self.assertRaisesRegex(RuntimeError, "cooldown"):
                birdeye_scanner.scan_birdeye()
        fetch.assert_called_once()
        self.assertIsNone(birdeye_scanner.safe_volume(True))

    def test_birdeye_missing_key_is_reported_as_unavailable(self):
        with patch.object(birdeye_scanner, "API_KEY", None):
            with self.assertRaisesRegex(RuntimeError, "API_KEY"):
                birdeye_scanner.scan_birdeye()

    def test_meme_listing_transport_error_propagates_to_aggregator(self):
        with patch.object(pumpfun_scanner, "API_KEY", "test-key"), \
             patch.object(birdeye_scanner, "_quota_disabled_until", 0), \
             patch.object(pumpfun_scanner, "get_json", side_effect=requests.Timeout("timeout")):
            with self.assertRaises(requests.Timeout):
                pumpfun_scanner.scan_pumpfun()

    def test_birdeye_quota_pause_applies_across_both_scanners(self):
        response = requests.Response()
        response.status_code = 400
        response._content = b"Compute units usage limit exceeded"
        with (
            patch.object(birdeye_scanner, "API_KEY", "test-key"),
            patch.object(pumpfun_scanner, "API_KEY", "test-key"),
            patch.object(birdeye_scanner, "_quota_disabled_until", 0),
            patch.object(pumpfun_scanner, "get_json",
                         side_effect=requests.HTTPError(response=response)) as listing,
            patch.object(birdeye_scanner, "get_json") as token_list,
        ):
            with self.assertRaisesRegex(RuntimeError, "quota exhausted"):
                pumpfun_scanner.scan_pumpfun()
            with self.assertRaisesRegex(RuntimeError, "cooldown"):
                birdeye_scanner.scan_birdeye()
        listing.assert_called_once()
        token_list.assert_not_called()

    def test_meme_listing_missing_key_is_reported_as_unavailable(self):
        with patch.object(pumpfun_scanner, "API_KEY", None):
            with self.assertRaisesRegex(RuntimeError, "API_KEY"):
                pumpfun_scanner.scan_pumpfun()


if __name__ == "__main__":
    unittest.main()
