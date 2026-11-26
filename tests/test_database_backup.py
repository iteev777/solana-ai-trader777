import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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

    def test_concurrent_backup_retention_preserves_unrelated_files(self):
        os.makedirs(self.backup_dir)
        unrelated = Path(self.backup_dir, "external.sqlite3")
        unrelated.write_bytes(b"unrelated")
        with ThreadPoolExecutor(max_workers=4) as workers:
            backups = list(workers.map(
                lambda _: database.backup_database(self.backup_dir, keep=2),
                range(4)
            ))
        self.assertEqual(len({item["backup"] for item in backups}), 4)
        self.assertEqual(len(database.list_database_backups(self.backup_dir)), 2)
        self.assertEqual(unrelated.read_bytes(), b"unrelated")

    def test_restore_is_verified_and_preserves_the_active_database(self):
        result = database.backup_database(self.backup_dir)
        snapshot = Path(self.backup_dir, result["backup"])
        destination = Path(self.temp.name, "recovered.db")
        with database.connect() as connection:
            connection.execute("UPDATE account_state SET balance = 20 WHERE mode = 'paper'")
        database.restore_database_backup(snapshot, destination)
        with patch.object(database, "DATABASE", str(destination)), database.connect() as recovered:
            self.assertEqual(recovered.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(recovered.execute(
                "SELECT balance FROM account_state WHERE mode = 'paper'"
            ).fetchone()[0], 100)
        self.assertEqual(database.get_account_state("paper")["balance"], 20)
        with self.assertRaises(FileExistsError):
            database.restore_database_backup(snapshot, destination)
        with self.assertRaises(ValueError):
            database.restore_database_backup(snapshot, database.DATABASE)

    def test_restore_rejects_unrelated_schema_corruption_and_existing_sidecars(self):
        foreign = Path(self.temp.name, "foreign.db")
        connection = sqlite3.connect(foreign)
        connection.execute("CREATE TABLE unrelated (id integer)")
        connection.close()
        destination = Path(self.temp.name, "recovered.db")
        with self.assertRaisesRegex(sqlite3.DatabaseError, "missing core Trojan tables"):
            database.restore_database_backup(foreign, destination)
        self.assertFalse(destination.exists())
        foreign.write_bytes(b"not a database")
        with self.assertRaises(sqlite3.DatabaseError):
            database.restore_database_backup(foreign, destination)
        self.assertFalse(destination.exists())
        Path(str(destination) + "-wal").touch()
        with self.assertRaises(FileExistsError):
            database.restore_database_backup(foreign, destination)

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


if __name__ == "__main__":
    unittest.main()
