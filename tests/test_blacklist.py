import os
import tempfile
import unittest
from unittest.mock import patch

import blacklist
import database


SYSTEM = "11111111111111111111111111111111"
VOTE = "Vote111111111111111111111111111111111111111"


class PersistentBlacklistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "blacklist.db")
        self.db_patch = patch.object(database, "DATABASE", self.path)
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_add_list_match_and_soft_remove_token_pool_account(self):
        blacklist.add_entry("token", SYSTEM, "known malicious mint")
        blacklist.add_entry("pool", VOTE, "compromised pool")
        blacklist.add_entry("account", SYSTEM, "known scam authority", token_mint=VOTE)

        matches = blacklist.match_entries([
            ("token", SYSTEM), ("pool", VOTE), ("account", SYSTEM)
        ])
        self.assertEqual({row["entry_type"] for row in matches}, {"token", "pool", "account"})
        self.assertEqual(len(blacklist.get_entries()), 3)

        self.assertTrue(blacklist.remove_entry("pool", VOTE))
        self.assertFalse(blacklist.remove_entry("pool", VOTE))
        self.assertEqual(len(blacklist.get_entries("pool")), 0)
        self.assertEqual(len(blacklist.get_entries("pool", include_inactive=True)), 1)

    def test_upsert_reactivates_and_updates_existing_entry(self):
        first = blacklist.add_entry("token", SYSTEM, "first reason")
        second = blacklist.add_entry("token", SYSTEM, "updated reason", source="operator")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["reason"], "updated reason")
        self.assertEqual(second["source"], "operator")
        self.assertEqual(second["active"], 1)

    def test_invalid_public_keys_and_types_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "base58"):
            blacklist.add_entry("token", "not-a-solana-key", "reason")
        with self.assertRaisesRegex(ValueError, "entry_type"):
            blacklist.add_entry("wallet", SYSTEM, "reason")
        with self.assertRaisesRegex(ValueError, "32-byte"):
            blacklist.add_entry("token", "111", "reason")

    def test_match_ignores_inactive_and_unknown_entries(self):
        blacklist.add_entry("token", SYSTEM, "blocked")
        blacklist.remove_entry("token", SYSTEM)
        self.assertEqual(blacklist.match_entries([
            ("token", SYSTEM), ("unknown", SYSTEM), ("pool", "")
        ]), [])

    def test_token_account_and_authority_blacklist_matches_security_report(self):
        blacklist.add_entry("account", SYSTEM, "known hostile authority")
        report = {
            "largest_token_account_details": [
                {"address": VOTE, "authority": SYSTEM, "amount": 500}
            ]
        }
        matches = blacklist.match_token_security_accounts(report)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["entry_value"], SYSTEM)

    def test_missing_account_details_do_not_create_fake_matches(self):
        blacklist.add_entry("account", SYSTEM, "known hostile authority")
        self.assertEqual(blacklist.match_token_security_accounts({}), [])


    def test_initialize_creates_blacklist_table_and_schema_version(self):
        with database.connect() as connection:
            tables = {
                row["name"] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()["value"]
        self.assertIn("blacklist_entries", tables)
        self.assertEqual(version, str(database.SCHEMA_VERSION))


if __name__ == "__main__":
    unittest.main()
