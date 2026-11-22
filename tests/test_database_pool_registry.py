import json
import os
import tempfile
import unittest
from unittest.mock import patch

import database


class PersistentPoolRegistryDatabaseTests(unittest.TestCase):
    def test_pool_registry_round_trip_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "pool-registry-test.db")
            pools = [{
                "pairAddress": "pool-one",
                "baseToken": {"address": "mint-one"},
                "marketSources": ["dexscreener", "geckoterminal"],
            }]
            with patch.object(database, "DATABASE", path):
                self.assertTrue(database.save_pool_registry("mint-one", pools, updated_at=123.5))
                loaded = database.load_pool_registry("mint-one")
                self.assertEqual(loaded["pools"], pools)
                self.assertEqual(loaded["updated_at"], 123.5)
                database.delete_pool_registry("mint-one")
                self.assertIsNone(database.load_pool_registry("mint-one"))

    def test_pool_registry_rejects_non_finite_json_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "pool-registry-test.db")
            with patch.object(database, "DATABASE", path):
                with self.assertRaises(ValueError):
                    database.save_pool_registry("mint-one", [{"price": float("nan")}])

    def test_initialize_database_creates_pool_registry_table_and_schema_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "pool-registry-test.db")
            with patch.object(database, "DATABASE", path):
                database.initialize_database()
                with database.connect() as connection:
                    tables = {
                        row["name"] for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    version = connection.execute(
                        "SELECT value FROM schema_meta WHERE key='schema_version'"
                    ).fetchone()["value"]
                self.assertIn("pool_registry", tables)
                self.assertEqual(version, str(database.SCHEMA_VERSION))


if __name__ == "__main__":
    unittest.main()
