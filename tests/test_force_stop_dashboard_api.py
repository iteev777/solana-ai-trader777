import os
import unittest
from unittest.mock import patch

import dashboard
import trading_control


class ForceStopDashboardApiTests(unittest.TestCase):
    def setUp(self):
        trading_control.reset_for_tests()
        self.auth = patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "test-dashboard-token"})
        self.auth.start()
        self.client = dashboard.app.test_client()

    def tearDown(self):
        trading_control.reset_for_tests()
        self.auth.stop()

    def test_force_stop_endpoint_requires_dashboard_auth(self):
        response = self.client.get("/api/force-stop")
        self.assertEqual(response.status_code, 401)

    def test_force_stop_endpoint_requires_action_header_for_mutation(self):
        response = self.client.post(
            "/api/force-stop",
            json={"stopped": True},
            headers={},
            auth=("operator", "test-dashboard-token"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(trading_control.is_force_stopped())

    def test_force_stop_can_be_enabled_and_resumed(self):
        enabled = self.client.post(
            "/api/force-stop",
            json={"stopped": True, "reason": "Operator requested halt"},
            headers={"X-Trojan-Action": "1"},
            auth=("operator", "test-dashboard-token"),
        )
        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(enabled.get_json()["is_stopped"])
        self.assertTrue(trading_control.is_force_stopped())

        status = self.client.get(
            "/api/force-stop",
            auth=("operator", "test-dashboard-token"),
        )
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.get_json()["is_stopped"])

        resumed = self.client.post(
            "/api/force-stop",
            json={"stopped": False},
            headers={"X-Trojan-Action": "1"},
            auth=("operator", "test-dashboard-token"),
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertFalse(resumed.get_json()["is_stopped"])
        self.assertFalse(trading_control.is_force_stopped())

    def test_force_stop_rejects_non_boolean_payload(self):
        response = self.client.post(
            "/api/force-stop",
            json={"stopped": "true"},
            headers={"X-Trojan-Action": "1"},
            auth=("operator", "test-dashboard-token"),
        )
        self.assertEqual(response.status_code, 400)

    def test_overview_exposes_force_stop_and_live_lock_status(self):
        trading_control.set_force_stopped(True, "Test")
        response = self.client.get(
            "/api/overview",
            auth=("operator", "test-dashboard-token"),
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["trading_control"]["is_stopped"])
        self.assertEqual(data["live_execution"], "LOCKED")


if __name__ == "__main__":
    unittest.main()
