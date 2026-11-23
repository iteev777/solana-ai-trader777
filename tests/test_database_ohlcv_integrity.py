import os
import tempfile
import time
import unittest

import database


class DatabaseOhlcvIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_database = database.DATABASE
        database.DATABASE = os.path.join(self.temp.name, "market.sqlite")
        database.initialize_database()

    def tearDown(self):
        database.DATABASE = self.old_database
        self.temp.cleanup()

    def test_save_rejects_invalid_geometry_and_deduplicates_timestamps(self):
        now = int(time.time())
        saved = database.save_market_candles("pool-a", "1m", [
            {"timestamp": now - 60, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
            {"timestamp": now - 60, "open": 1, "high": 3, "low": 0.5, "close": 2.5, "volume": 20},
            {"timestamp": now, "open": 1, "high": 0.5, "low": 2, "close": 1.5, "volume": 10},
        ])
        self.assertEqual(saved, 1)
        candles = database.get_market_candles("pool-a", "1m")
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0]["close"], 2.5)

    def test_retention_prunes_only_candles_older_than_cutoff(self):
        now = int(time.time())
        with database.connect() as connection:
            connection.execute("""
                INSERT INTO market_candles
                (pool_address, timeframe, open_time, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, ("pool-a", "1m", now - 31 * 86400, 1, 2, 0.5, 1.5, 10))
            connection.execute("""
                INSERT INTO market_candles
                (pool_address, timeframe, open_time, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, ("pool-a", "1m", now - 60, 1, 2, 0.5, 1.5, 10))
        removed = database.prune_market_candles(retention_days=30)
        self.assertEqual(removed, 1)
        candles = database.get_market_candles("pool-a", "1m")
        self.assertEqual(len(candles), 1)
        self.assertGreater(candles[0]["timestamp"], now - 86400)

    def test_invalid_retention_is_rejected(self):
        with self.assertRaises(ValueError):
            database.prune_market_candles(retention_days=0)
        with self.assertRaises(ValueError):
            database.prune_market_candles(retention_days="invalid")

    def test_unknown_timeframe_is_not_returned(self):
        self.assertEqual(database.get_market_candles("pool-a", "1w"), [])


if __name__ == "__main__":
    unittest.main()
