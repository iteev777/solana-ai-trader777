import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import database


class DatabaseBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_database = database.DATABASE
        database.DATABASE = os.path.join(self.temp.name, "trading_history.db")
        database.initialize_database()
        database.initialize_account("paper", 100.0)
        self.backup_dir = os.path.join(self.temp.name, "backups")

    def tearDown(self):
        database.DATABASE = self.old_database
        self.temp.cleanup()

    def test_online_backup_is_integrity_checked_and_restorable(self):
        with database.connect() as connection:
            connection.execute(
                "UPDATE account_state SET balance = ? WHERE mode = 'paper'",
                (77.5,)
            )
        result = database.backup_database(self.backup_dir)
        self.assertTrue(result["created"])
        self.assertEqual(result["integrity"], "ok")
        backup_path = os.path.join(self.backup_dir, result["backup"])
        self.assertTrue(os.path.isfile(backup_path))

        with database.connect() as connection:
            connection.execute(
                "UPDATE account_state SET balance = ? WHERE mode = 'paper'",
                (12.0,)
            )
        snapshot = sqlite3.connect(backup_path)
        try:
            balance = snapshot.execute(
                "SELECT balance FROM account_state WHERE mode = 'paper'"
            ).fetchone()[0]
            self.assertEqual(balance, 77.5)
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            snapshot.close()

    def test_restore_replaces_database_only_in_maintenance_mode_and_creates_emergency_backup(self):
        with database.connect() as connection:
            connection.execute(
                "UPDATE account_state SET balance = ? WHERE mode = 'paper'",
                (77.5,)
            )
        backup = database.backup_database(self.backup_dir)["backup"]
        with database.connect() as connection:
            connection.execute(
                "UPDATE account_state SET balance = ? WHERE mode = 'paper'",
                (12.0,)
            )

        with patch.dict(os.environ, {"TROJAN_RESTORE_MAINTENANCE_MODE": "true"}):
            result = database.restore_database(backup, self.backup_dir)

        self.assertTrue(result["restored"])
        self.assertEqual(result["integrity"], "ok")
        self.assertTrue(result["requires_application_restart"])
        self.assertNotEqual(result["backup"], result["emergency_backup"])
        with database.connect() as connection:
            balance = connection.execute(
                "SELECT balance FROM account_state WHERE mode = 'paper'"
            ).fetchone()[0]
        self.assertEqual(balance, 77.5)
        self.assertTrue(os.path.isfile(os.path.join(self.backup_dir, result["emergency_backup"])))

    def test_restore_requires_offline_maintenance_mode(self):
        backup = database.backup_database(self.backup_dir)["backup"]
        with patch.dict(os.environ, {"TROJAN_RESTORE_MAINTENANCE_MODE": "false"}):
            with self.assertRaises(RuntimeError):
                database.restore_database(backup, self.backup_dir)

    def test_restore_rejects_path_traversal(self):
        with patch.dict(os.environ, {"TROJAN_RESTORE_MAINTENANCE_MODE": "true"}):
            with self.assertRaises(ValueError):
                database.restore_database("../outside.sqlite3", self.backup_dir)

    def test_retention_keeps_only_requested_number_of_verified_backups(self):
        for balance in (91.0, 92.0, 93.0):
            with database.connect() as connection:
                connection.execute(
                    "UPDATE account_state SET balance = ? WHERE mode = 'paper'",
                    (balance,)
                )
            database.backup_database(self.backup_dir, keep=2)
        backups = database.list_database_backups(self.backup_dir)
        self.assertEqual(len(backups), 2)
        self.assertTrue(all(item["size_bytes"] > 0 for item in backups))

    def test_invalid_retention_is_rejected(self):
        with self.assertRaises(ValueError):
            database.backup_database(self.backup_dir, keep=0)
        with self.assertRaises(ValueError):
            database.backup_database(self.backup_dir, keep=True)

    def test_health_reports_storage_and_backup_state(self):
        database.backup_database(self.backup_dir)
        with patch.dict(os.environ, {"TROJAN_BACKUP_DIR": self.backup_dir}, clear=True):
            health = database.get_database_health()
        self.assertEqual(health["integrity"], "ok")
        self.assertEqual(health["journal_mode"], "wal")
        self.assertEqual(health["storage_backend"], "ephemeral_default")
        self.assertFalse(health["persistent_storage_configured"])
        self.assertEqual(health["backup_count"], 1)
        self.assertGreater(health["database_size_bytes"], 0)

    def test_initialize_refuses_future_schema_without_modifying_database(self):
        with database.connect() as connection:
            connection.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                ("999",),
            )
            connection.execute(
                "CREATE TABLE future_only (value TEXT NOT NULL)"
            )
        with self.assertRaisesRegex(RuntimeError, "newer than this Trojan build"):
            database.initialize_database()
        with database.connect() as connection:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()["value"]
            future_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='future_only'"
            ).fetchone()
        self.assertEqual(version, "999")
        self.assertIsNotNone(future_table)


if __name__ == "__main__":
    unittest.main()
