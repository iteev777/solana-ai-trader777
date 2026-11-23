import unittest

from discovery import token_aggregator
from discovery.gecko_scanner import scan_recently_updated_tokens


class DiscoveryGeckoRegistrationTests(unittest.TestCase):
    def test_recently_updated_feed_is_registered_once(self):
        matches = [
            scanner for name, scanner in token_aggregator.SCANNERS
            if name == "geckoterminal_recently_updated"
        ]
        self.assertEqual(matches, [scan_recently_updated_tokens])

    def test_worker_capacity_covers_all_registered_sources(self):
        self.assertGreaterEqual(token_aggregator.MAX_WORKERS, len(token_aggregator.SCANNERS))


if __name__ == "__main__":
    unittest.main()
