import os
import tempfile
import unittest

import database


class NewPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous = database.DATABASE
        database.DATABASE = os.path.join(self.temp_dir.name, "trojan.sqlite")
        database.initialize_database()

    def tearDown(self):
        database.DATABASE = self.previous
        self.temp_dir.cleanup()

    def test_candle_upsert_and_order(self):
        candles = [
            {"timestamp": 120, "open": 1, "high": 2, "low": 0.9, "close": 1.5, "volume": 10},
            {"timestamp": 60, "open": 1, "high": 1.5, "low": 0.8, "close": 1.2, "volume": 8}
        ]
        self.assertEqual(database.save_market_candles("POOL", "1m", candles), 2)
        rows = database.get_market_candles("POOL", "1m")
        self.assertEqual([row["timestamp"] for row in rows], [60, 120])

    def test_strategy_config_save_and_delete(self):
        database.save_strategy_config("main", {"all": [
            {"field": "rsi_14", "op": "lt", "value": 80}
        ]})
        saved = database.get_strategy_configs()
        self.assertIn("main", saved)
        self.assertEqual(saved["main"]["config"]["all"][0]["field"], "rsi_14")
        database.delete_strategy_config("main")
        self.assertNotIn("main", database.get_strategy_configs())

    def test_copy_event_deduplicates_signatures(self):
        self.assertTrue(database.record_copy_trade_event(
            "WALLET", "SIGNATURE", "BUY", "MINT", 100, -0.5, "{}"
        ))
        self.assertFalse(database.record_copy_trade_event(
            "WALLET", "SIGNATURE", "BUY", "MINT", 100, -0.5, "{}"
        ))
        rows = database.get_recent_copy_trade_events()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "BUY")


if __name__ == "__main__":
    unittest.main()
