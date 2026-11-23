import unittest
from unittest.mock import patch

from discovery import screenerbot_sources as sources
from discovery import token_aggregator as aggregator


MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


class RugcheckFeedParityTests(unittest.TestCase):
    def test_each_rugcheck_endpoint_is_exposed_as_its_own_scanner(self):
        cases = (
            (sources.scan_rugcheck_new, "new_tokens", "rugcheck_new"),
            (sources.scan_rugcheck_recent, "recent", "rugcheck_recent"),
            (sources.scan_rugcheck_trending, "trending", "rugcheck_trending"),
            (sources.scan_rugcheck_verified, "verified", "rugcheck_verified"),
        )
        for scanner, endpoint, label in cases:
            with self.subTest(endpoint=endpoint):
                with patch.object(sources, "get_json", return_value=[
                    {"mint": MINT, "tokenMeta": {"name": "Token", "symbol": "TKN"}}
                ]) as fetch:
                    result = scanner()
                self.assertEqual(fetch.call_args.args[0], f"{sources.RUGCHECK_BASE}/{endpoint}")
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["tokenAddress"], MINT)
                self.assertEqual(result[0]["discovery_sources"], [label])
                self.assertEqual(result[0]["name"], "Token")
                self.assertEqual(result[0]["symbol"], "TKN")

    def test_null_stats_response_is_a_valid_empty_feed(self):
        with patch.object(sources, "get_json", return_value=None):
            self.assertEqual(sources.scan_rugcheck_trending(), [])

    def test_invalid_stats_payload_is_a_source_failure_not_empty_success(self):
        with patch.object(sources, "get_json", return_value={"unexpected": []}):
            with self.assertRaisesRegex(ValueError, "invalid stats-list payload"):
                sources.scan_rugcheck_verified()

    def test_transport_failure_propagates_to_source_health(self):
        with patch.object(sources, "get_json", side_effect=TimeoutError("RugCheck timeout")):
            with self.assertRaisesRegex(TimeoutError, "RugCheck timeout"):
                sources.scan_rugcheck_recent()

    def test_provider_and_each_feed_have_independent_environment_switches(self):
        with patch.dict("os.environ", {}, clear=True):
            enabled = aggregator._rugcheck_scanners_from_env()
        self.assertEqual([name for name, _ in enabled], [
            "rugcheck_new", "rugcheck_recent", "rugcheck_trending", "rugcheck_verified"
        ])

        with patch.dict("os.environ", {"TROJAN_RUGCHECK_TRENDING_ENABLED": "false"}, clear=True):
            enabled = aggregator._rugcheck_scanners_from_env()
        self.assertEqual([name for name, _ in enabled], [
            "rugcheck_new", "rugcheck_recent", "rugcheck_verified"
        ])

        with patch.dict("os.environ", {"TROJAN_RUGCHECK_ENABLED": "0"}, clear=True):
            self.assertEqual(aggregator._rugcheck_scanners_from_env(), [])

    def test_individual_feed_failure_does_not_disable_other_feeds(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_RECENT_ENABLED": "false"}, clear=True):
            scanners = aggregator._rugcheck_scanners_from_env()
        self.assertEqual(len(scanners), 3)
        self.assertNotIn("rugcheck_recent", [name for name, _ in scanners])


if __name__ == "__main__":
    unittest.main()
