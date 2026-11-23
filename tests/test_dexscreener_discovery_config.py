import unittest
from unittest.mock import patch

from discovery import dexscreener_scanner as scanner
from discovery import token_aggregator


class DexScreenerDiscoveryConfigurationTests(unittest.TestCase):
    def test_all_three_feeds_enabled_by_default(self):
        self.assertEqual(
            [name for name, _ in token_aggregator._dexscreener_scanners_from_env()],
            [
                "dexscreener_profiles",
                "dexscreener_latest_boosts",
                "dexscreener_top_boosts",
            ],
        )

    def test_provider_switch_disables_all_dexscreener_feeds(self):
        with patch.dict("os.environ", {"TROJAN_DEXSCREENER_ENABLED": "false"}):
            self.assertEqual(token_aggregator._dexscreener_scanners_from_env(), [])

    def test_each_feed_can_be_disabled_independently(self):
        env = {
            "TROJAN_DEXSCREENER_PROFILES_ENABLED": "false",
            "TROJAN_DEXSCREENER_LATEST_BOOSTS_ENABLED": "true",
            "TROJAN_DEXSCREENER_TOP_BOOSTS_ENABLED": "0",
        }
        with patch.dict("os.environ", env, clear=True):
            configured = token_aggregator._dexscreener_scanners_from_env()
        self.assertEqual(
            configured,
            [("dexscreener_latest_boosts", scanner.scan_latest_boosts)],
        )


if __name__ == "__main__":
    unittest.main()
