import base64
import os
import unittest
from unittest.mock import patch

import dashboard


class DatabaseDashboardApiTests(unittest.TestCase):
    def setUp(self):
        self.client = dashboard.app.test_client()
        self.headers = {
            "Authorization": "Basic " + base64.b64encode(b"trojan:secret").decode()
        }

    def test_database_health_requires_dashboard_authentication(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}, clear=False):
            response = self.client.get("/api/database")
        self.assertEqual(response.status_code, 401)

    def test_database_health_returns_storage_diagnostics(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}, clear=False), \
             patch.object(dashboard.database, "get_database_health", return_value={
                 "integrity": "ok", "storage_backend": "railway_volume",
                 "persistent_storage_configured": True, "backup_count": 2
             }):
            response = self.client.get("/api/database", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["persistent_storage_configured"])

    def test_backup_endpoint_uses_verified_backup_service(self):
        expected = {"created": True, "backup": "trojan-test.sqlite3", "integrity": "ok", "retained": 1}
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}, clear=False), \
             patch.object(dashboard.database, "backup_database", return_value=expected) as backup:
            response = self.client.post(
                "/api/database/backup", json={"keep": 3}, headers=self.headers
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), expected)
        backup.assert_called_once_with(keep=3)

    def test_invalid_backup_retention_returns_bad_request(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}, clear=False), \
             patch.object(dashboard.database, "backup_database", side_effect=ValueError("keep invalid")):
            response = self.client.post(
                "/api/database/backup", json={"keep": 0}, headers=self.headers
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "keep invalid")


if __name__ == "__main__":
    unittest.main()
