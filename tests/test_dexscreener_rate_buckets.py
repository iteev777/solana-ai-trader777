import unittest

import api_client


class DexScreenerRateBucketTests(unittest.TestCase):
    def test_profiles_have_dedicated_rate_limit_bucket(self):
        host, endpoint, interval = api_client.get_rate_bucket(
            "https://api.dexscreener.com/token-profiles/latest/v1"
        )
        self.assertEqual(host, "api.dexscreener.com")
        self.assertEqual(endpoint, "dexscreener_profiles")
        self.assertEqual(interval, 1.1)

    def test_both_boost_feeds_share_dedicated_boost_bucket(self):
        for path in ("token-boosts/latest/v1", "token-boosts/top/v1"):
            host, endpoint, interval = api_client.get_rate_bucket(
                f"https://api.dexscreener.com/{path}"
            )
            self.assertEqual(host, "api.dexscreener.com")
            self.assertEqual(endpoint, "dexscreener_boosts")
            self.assertEqual(interval, 1.1)

    def test_profiles_and_boosts_use_separate_concurrency_locks(self):
        profiles = api_client.get_host_lock("api.dexscreener.com", "dexscreener_profiles")
        boosts = api_client.get_host_lock("api.dexscreener.com", "dexscreener_boosts")
        same_profiles_bucket = api_client.get_host_lock("api.dexscreener.com", "dexscreener_profiles")
        self.assertIsNot(profiles, boosts)
        self.assertIs(profiles, same_profiles_bucket)


if __name__ == "__main__":
    unittest.main()


class RugcheckRateLimitParityTests(unittest.TestCase):
    def test_rugcheck_global_budget_matches_screenerbot_sixty_per_minute(self):
        from api_client import HOST_INTERVALS
        self.assertEqual(HOST_INTERVALS["api.rugcheck.xyz"], 1.0)
