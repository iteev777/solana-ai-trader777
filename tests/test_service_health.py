import unittest

import service_health


class ServiceHeartbeatTests(unittest.TestCase):
    def test_fresh_heartbeat_is_healthy(self):
        result = service_health._heartbeat_check("main_loop", {"main_loop": 95.0}, 100.0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["age_seconds"], 5.0)
        self.assertTrue(result["required"])

    def test_stale_heartbeat_is_unhealthy(self):
        result = service_health._heartbeat_check("main_loop", {"main_loop": 60.0}, 100.0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["age_seconds"], 40.0)

    def test_future_heartbeat_is_not_accepted_as_fresh(self):
        result = service_health._heartbeat_check("main_loop", {"main_loop": 101.0}, 100.0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["age_seconds"], -1.0)

    def test_missing_and_invalid_heartbeats_fail_closed(self):
        for value in (None, True, float("nan"), float("inf"), "not-a-time"):
            with self.subTest(value=value):
                result = service_health._heartbeat_check(
                    "main_loop", {"main_loop": value}, 100.0
                )
                self.assertFalse(result["ok"])
                self.assertIsNone(result["age_seconds"])

    def test_exact_staleness_boundary_is_not_healthy(self):
        result = service_health._heartbeat_check("main_loop", {"main_loop": 70.0}, 100.0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["age_seconds"], 30.0)


if __name__ == "__main__":
    unittest.main()
