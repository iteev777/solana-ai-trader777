import base64
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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


    def test_copy_watchlist_api_requires_authentication(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}, clear=False):
            response = self.client.get("/api/copy-watchlist")
        self.assertEqual(response.status_code, 401)

    def test_copy_watchlist_api_adds_wallet_and_returns_watchlist(self):
        expected = {"wallet": "Wallet", "label": "Alpha", "enabled": True}
        watchlist = {"wallets": [expected], "active_count": 1, "maximum": 20, "monitor": {}}
        with (
            patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}, clear=False),
            patch("copy_trade.add_copy_watch_wallet", return_value=expected),
            patch("copy_trade.get_copy_watchlist", return_value=watchlist),
        ):
            response = self.client.post(
                "/api/copy-watchlist",
                json={"wallet": "Wallet", "label": "Alpha"},
                headers=self.headers
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["watchlist"]["active_count"], 1)

    def test_copy_watchlist_api_remove_returns_not_found(self):
        with (
            patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}, clear=False),
            patch("copy_trade.remove_copy_watch_wallet", return_value=False),
        ):
            response = self.client.delete("/api/copy-watchlist/Wallet", headers=self.headers)
        self.assertEqual(response.status_code, 404)

    def test_invalid_backup_retention_returns_bad_request(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}, clear=False), \
             patch.object(dashboard.database, "backup_database", side_effect=ValueError("keep invalid")):
            response = self.client.post(
                "/api/database/backup", json={"keep": 0}, headers=self.headers
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "keep invalid")

    def test_unavailable_database_health_returns_service_unavailable(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch.object(dashboard.database, "get_database_health",
                          side_effect=sqlite3.DatabaseError("private path")):
            response = self.client.get("/api/database", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["integrity"], "unknown")
        self.assertNotIn("private path", response.get_data(as_text=True))

    def test_backup_rejects_non_object_json_and_form_requests(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch.object(dashboard.database, "backup_database") as backup:
            response = self.client.post("/api/database/backup", json=[1], headers=self.headers)
            self.assertEqual(response.status_code, 400)
            response = self.client.post("/api/database/backup", data={"keep": 1}, headers=self.headers)
            self.assertEqual(response.status_code, 400)
        backup.assert_not_called()

    def test_dashboard_can_stop_and_restart_on_the_same_port(self):
        with patch.dict(os.environ, {
            "TROJAN_DASHBOARD_TOKEN": "secret", "DASHBOARD_HOST": "127.0.0.1", "PORT": "0"
        }):
            self.addCleanup(dashboard.stop_dashboard)
            self.assertTrue(dashboard.start_dashboard())
            port = dashboard._server.server_port
            dashboard.stop_dashboard()
            self.assertIsNone(dashboard._server)
            os.environ["PORT"] = str(port)
            self.assertTrue(dashboard.start_dashboard())
            self.assertEqual(dashboard._server.server_port, port)

    def test_daily_performance_counts_only_recent_paper_exits(self):
        today = datetime.now(timezone.utc).date()
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(dashboard.database, "DATABASE", os.path.join(tmp, "trades.db")), \
             patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}):
            dashboard.database.initialize_database()
            rows = [
                ("SELL", "paper", 4, today.isoformat(), "main"),
                ("SELL", "paper", -1.5, today.isoformat(), "sniper"),
                ("BUY", "paper", 20, today.isoformat(), "main"),
                ("SELL", "live", 10, today.isoformat(), "main"),
                ("SELL", "paper", 10, (today - timedelta(days=30)).isoformat(), "main"),
            ]
            with dashboard.database.connect() as connection:
                connection.executemany("""
                    INSERT INTO trades (token_address, price, amount, action, mode,
                                        profit_loss, timestamp, strategy)
                    VALUES ('test', 1, 1, ?, ?, ?, ?, ?)
                """, rows)
            response = self.client.get("/api/performance", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["live_execution"], "locked")
        self.assertEqual(data["daily"], [{
            "day": today.isoformat(), "closed_trades": 2, "wins": 1, "realized_pnl": 2.5,
        }])


    def test_blacklist_api_requires_dashboard_authentication(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}, clear=False):
            response = self.client.get("/api/blacklist")
        self.assertEqual(response.status_code, 401)

    def test_blacklist_api_lists_entries(self):
        expected = [{"entry_type": "token", "entry_value": "111"}]
        with (
            patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}, clear=False),
            patch("blacklist.get_entries", return_value=expected),
        ):
            response = self.client.get("/api/blacklist", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["entries"], expected)

    def test_blacklist_api_add_requires_action_header(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}, clear=False):
            response = self.client.post(
                "/api/blacklist",
                json={"entry_type": "token", "address": "11111111111111111111111111111111", "reason": "test"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Missing action header")

    def test_blacklist_api_adds_entry_with_explicit_action(self):
        expected = {"entry_type": "token", "entry_value": "11111111111111111111111111111111"}
        with (
            patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}, clear=False),
            patch("blacklist.add_entry", return_value=expected) as add_entry,
        ):
            response = self.client.post(
                "/api/blacklist",
                json={"entry_type": "token", "address": expected["entry_value"], "reason": "manual review"},
                headers={**self.headers, "X-Trojan-Action": "1"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["saved"])
        add_entry.assert_called_once()

    def test_blacklist_api_removal_returns_not_found_when_absent(self):
        with (
            patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}, clear=False),
            patch("blacklist.remove_entry", return_value=False),
        ):
            response = self.client.delete(
                "/api/blacklist/token/11111111111111111111111111111111",
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
