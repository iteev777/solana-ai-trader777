import os
import tempfile
import unittest
from unittest.mock import patch

import copy_trade
import database
from solana_onchain import _b58encode

ENV_WALLET = "So11111111111111111111111111111111111111112"
DASHBOARD_WALLET = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class CopyWalletWatchlistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_database = database.DATABASE
        database.DATABASE = os.path.join(self.temp.name, "watchlist.sqlite")
        database.initialize_database()

    def tearDown(self):
        database.DATABASE = self.old_database
        self.temp.cleanup()

    def test_add_list_and_remove_dashboard_wallet(self):
        with patch.dict(os.environ, {"TROJAN_COPY_WALLETS": ""}, clear=False):
            added = copy_trade.add_copy_watch_wallet(DASHBOARD_WALLET, "Alpha")
            self.assertEqual(added["wallet"], DASHBOARD_WALLET)
            self.assertEqual(added["label"], "Alpha")
            watchlist = copy_trade.get_copy_watchlist()
            self.assertEqual(watchlist["active_count"], 1)
            self.assertEqual(watchlist["wallets"][0]["source"], "dashboard")
            self.assertTrue(copy_trade.remove_copy_watch_wallet(DASHBOARD_WALLET))
            self.assertEqual(copy_trade.get_copy_watchlist()["active_count"], 0)

    def test_environment_wallets_are_merged_and_cannot_be_removed_from_dashboard(self):
        with patch.dict(os.environ, {"TROJAN_COPY_WALLETS": ENV_WALLET}, clear=False):
            copy_trade.add_copy_watch_wallet(DASHBOARD_WALLET, "Research")
            wallets = copy_trade._wallets()
            self.assertEqual(wallets, [ENV_WALLET, DASHBOARD_WALLET])
            with self.assertRaisesRegex(ValueError, "environment"):
                copy_trade.remove_copy_watch_wallet(ENV_WALLET)
            watchlist = copy_trade.get_copy_watchlist()
            self.assertEqual(watchlist["active_count"], 2)
            self.assertEqual(watchlist["wallets"][0]["source"], "environment")

    def test_invalid_base58_and_wrong_length_are_rejected(self):
        with self.assertRaises(ValueError):
            copy_trade.add_copy_watch_wallet("not a wallet")
        with self.assertRaises(ValueError):
            copy_trade.add_copy_watch_wallet("11111111")

    def test_total_watchlist_limit_is_20(self):
        addresses = [_b58encode(bytes([value]) * 32) for value in range(1, 21)]
        with patch.dict(os.environ, {"TROJAN_COPY_WALLETS": addresses[0]}, clear=False):
            for address in addresses[1:20]:
                copy_trade.add_copy_watch_wallet(address)
            with self.assertRaisesRegex(ValueError, "20"):
                copy_trade.add_copy_watch_wallet(_b58encode(bytes([21]) * 32))
            self.assertEqual(len(copy_trade._wallets()), 20)


    def test_reenabling_disabled_wallet_cannot_exceed_active_limit(self):
        addresses = [_b58encode(bytes([value]) * 32) for value in range(2, 21)]
        candidate = _b58encode(bytes([21]) * 32)
        with patch.dict(os.environ, {"TROJAN_COPY_WALLETS": ENV_WALLET}, clear=False):
            for address in addresses:
                database.upsert_copy_watch_wallet(address, enabled=True)
            database.upsert_copy_watch_wallet(candidate, enabled=False)
            self.assertEqual(len(copy_trade._wallets()), 20)
            with self.assertRaisesRegex(ValueError, "20"):
                copy_trade.add_copy_watch_wallet(candidate)
            self.assertFalse(next(
                item for item in database.list_copy_watch_wallets()
                if item["wallet"] == candidate
            )["enabled"])

    def test_database_upsert_can_pause_and_reenable_wallet(self):
        first = database.upsert_copy_watch_wallet(DASHBOARD_WALLET, "Test", enabled=False)
        self.assertFalse(first["enabled"])
        second = database.upsert_copy_watch_wallet(DASHBOARD_WALLET, "Renamed", enabled=True)
        self.assertTrue(second["enabled"])
        self.assertEqual(second["label"], "Renamed")
        self.assertEqual(len(database.list_copy_watch_wallets()), 1)


if __name__ == "__main__":
    unittest.main()
