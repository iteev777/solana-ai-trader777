import unittest
from unittest.mock import patch

import market_data
import pool_registry

TOKEN = "TokenMint111111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def gecko_pool(base, quote, address="pool-gecko"):
    return {
        "attributes": {
            "address": address,
            "name": "TOKEN / SOL",
            "base_token_price_usd": "100",
            "base_token_price_native_currency": "1",
            "quote_token_price_usd": "0.01",
            "quote_token_price_native_currency": "0.0001",
            "reserve_in_usd": "100000",
            "volume_usd": {"m5": "200", "h24": "50000"},
            "price_change_percentage": {"m5": "2", "h1": "3", "h24": "5"},
            "transactions": {"m5": {"buys": 10, "sells": 4}},
            "pool_created_at": "2026-11-22T12:00:00Z",
        },
        "relationships": {
            "base_token": {"data": {"id": "solana_" + base}},
            "quote_token": {"data": {"id": "solana_" + quote}},
            "dex": {"data": {"id": "raydium"}},
        },
    }


class GeckoPoolOrientationTests(unittest.TestCase):
    def test_base_side_pool_is_normalized_with_both_mints(self):
        result = market_data.normalize_gecko_pool(gecko_pool(TOKEN, WSOL), TOKEN)
        self.assertEqual(result["baseToken"]["address"], TOKEN)
        self.assertEqual(result["quoteToken"]["address"], WSOL)
        self.assertEqual(result["priceUsd"], "100")
        self.assertEqual(result["priceNative"], "1")

    def test_quote_side_pool_is_reoriented_using_quote_token_prices(self):
        result = market_data.normalize_gecko_pool(gecko_pool(WSOL, TOKEN), TOKEN)
        self.assertEqual(result["baseToken"]["address"], TOKEN)
        self.assertEqual(result["quoteToken"]["address"], WSOL)
        self.assertEqual(result["priceUsd"], "0.01")
        self.assertEqual(result["priceNative"], "0.0001")
        self.assertEqual(result["priceChange"], {})

    def test_unrelated_mint_is_rejected(self):
        self.assertIsNone(market_data.normalize_gecko_pool(
            gecko_pool("OtherMint", WSOL), TOKEN
        ))

    def test_legacy_base_only_gecko_payload_remains_supported(self):
        payload = {
            "attributes": {
                "address": "POOL123",
                "base_token_price_usd": "0.00025",
                "reserve_in_usd": "42000",
            },
            "relationships": {
                "base_token": {"data": {"id": "solana_MINT123"}}
            },
        }
        result = market_data.normalize_gecko_pool(payload)
        self.assertEqual(result["baseToken"]["address"], "MINT123")
        self.assertEqual(result["quoteToken"], {})


class MultiSourcePoolRegistryTests(unittest.TestCase):
    def setUp(self):
        pool_registry.clear_pool_registry_state(clear_persisted=True)

    def test_dexscreener_quote_is_preserved_when_gecko_enriches_same_pool(self):
        dex = {
            "pairAddress": "shared-pool",
            "baseToken": {"address": TOKEN},
            "quoteToken": {"address": WSOL},
            "priceUsd": "0.0101",
            "liquidity": {"usd": 100000},
            "marketSource": "dexscreener",
        }
        gecko = {
            "pairAddress": "shared-pool",
            "baseToken": {"address": TOKEN},
            "quoteToken": {"address": WSOL},
            "priceUsd": "0.0099",
            "liquidity": {"usd": 110000},
            "marketSource": "geckoterminal",
        }
        pool_registry.merge_pool_candidates(TOKEN, [dex], "dexscreener")
        pool_registry.merge_pool_candidates(TOKEN, [gecko], "geckoterminal")
        rows = pool_registry.get_pool_candidates(TOKEN)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["priceUsd"], "0.0101")
        self.assertEqual(rows[0]["marketSources"], ["dexscreener", "geckoterminal"])
        self.assertIn("geckoterminal", rows[0]["poolSourceData"])

    def test_empty_or_cross_chain_refresh_does_not_extend_cached_pool_age(self):
        row = {"pairAddress": "pool", "baseToken": {"address": TOKEN}}
        with patch.object(pool_registry.time, "time", return_value=100.0):
            pool_registry.merge_pool_candidates(TOKEN, [row], "dexscreener")
        with patch.object(pool_registry.time, "time", return_value=200.0):
            self.assertEqual(pool_registry.merge_pool_candidates(TOKEN, [], "dexscreener"), 0)
            self.assertEqual(pool_registry.merge_pool_candidates(
                TOKEN, [dict(row, chainId="ethereum")], "dexscreener"), 0)
        self.assertEqual(pool_registry._candidate_updated_at[TOKEN], 100.0)

    def test_registry_returns_deep_copies(self):
        row = {
            "pairAddress": "copy-pool",
            "baseToken": {"address": TOKEN},
            "liquidity": {"usd": 1},
        }
        pool_registry.merge_pool_candidates(TOKEN, [row], "dexscreener")
        result = pool_registry.get_pool_candidates(TOKEN)
        result[0]["liquidity"]["usd"] = 999
        self.assertEqual(pool_registry.get_pool_candidates(TOKEN)[0]["liquidity"]["usd"], 1)

    def test_stale_pool_snapshot_expires(self):
        row = {
            "pairAddress": "stale-pool",
            "baseToken": {"address": TOKEN},
            "liquidity": {"usd": 1},
        }
        with patch.object(pool_registry.time, "time", return_value=100.0):
            pool_registry.merge_pool_candidates(TOKEN, [row], "dexscreener")
        with patch.object(pool_registry.time, "time",
                          return_value=100.0 + pool_registry.POOL_STALE_TTL_SECONDS + 1):
            self.assertEqual(pool_registry.get_pool_candidates(TOKEN), [])

    def test_dexscreener_incomplete_list_detection(self):
        stable = {
            "baseToken": {"address": TOKEN},
            "quoteToken": {"address": USDC},
        }
        native = {
            "baseToken": {"address": TOKEN},
            "quoteToken": {"address": WSOL},
        }
        self.assertTrue(pool_registry.is_dexscreener_list_incomplete([]))
        self.assertTrue(pool_registry.is_dexscreener_list_incomplete([stable]))
        self.assertFalse(pool_registry.is_dexscreener_list_incomplete([stable, native]))
        self.assertTrue(pool_registry.is_dexscreener_list_incomplete(
            [native] * pool_registry.DEXSCREENER_TOKEN_PAIRS_CAP
        ))

    def test_full_dexscreener_pool_list_merges_all_pairs_and_skips_gecko_when_complete(self):
        native = {
            "pairAddress": "native-pool",
            "baseToken": {"address": TOKEN},
            "quoteToken": {"address": WSOL},
            "priceUsd": "0.01",
            "liquidity": {"usd": 100000, "quote": 500},
            "volume": {"h24": 50000},
        }
        stable = {
            "pairAddress": "stable-pool",
            "baseToken": {"address": TOKEN},
            "quoteToken": {"address": USDC},
            "priceUsd": "0.01",
            "liquidity": {"usd": 200000},
            "volume": {"h24": 70000},
        }
        with patch.object(pool_registry, "get_json", return_value=[native, stable]), \
             patch.object(pool_registry, "enqueue_gecko_pool_fetch") as gecko, \
             patch.object(market_data, "refresh_market_data_from_pool_registry",
                          return_value={"pairAddress": "native-pool"}):
            result = pool_registry.fetch_dexscreener_token_pools(TOKEN)
        self.assertEqual(result["received"], 2)
        self.assertEqual(result["usable"], 2)
        self.assertFalse(result["incomplete"])
        self.assertEqual(len(pool_registry.get_pool_candidates(TOKEN)), 2)
        gecko.assert_not_called()

    def test_full_dexscreener_list_without_native_pool_enqueues_gecko_completion(self):
        stable = {
            "pairAddress": "stable-only",
            "baseToken": {"address": TOKEN},
            "quoteToken": {"address": USDC},
            "priceUsd": "0.01",
            "liquidity": {"usd": 100000},
        }
        with patch.object(pool_registry, "get_json", return_value=[stable]), \
             patch.object(pool_registry, "enqueue_gecko_pool_fetch", return_value=True) as gecko, \
             patch.object(market_data, "refresh_market_data_from_pool_registry", return_value=None):
            result = pool_registry.fetch_dexscreener_token_pools(TOKEN)
        self.assertTrue(result["incomplete"])
        gecko.assert_called_once_with(TOKEN)

    def test_registry_hydrates_recent_candidates_from_sqlite(self):
        stored = {
            "pools": [{
                "pairAddress": "persisted-pool",
                "baseToken": {"address": TOKEN},
                "quoteToken": {"address": WSOL},
                "priceUsd": "0.01",
                "liquidity": {"usd": 100000},
            }],
            "updated_at": 100.0,
        }
        with patch.object(pool_registry.time, "time", return_value=110.0), \
             patch("database.load_pool_registry", return_value=stored) as load:
            rows = pool_registry.get_pool_candidates(TOKEN)
        load.assert_called_once_with(TOKEN)
        self.assertEqual(rows[0]["pairAddress"], "persisted-pool")
        self.assertEqual(pool_registry.get_pool_registry_status()["persisted_tokens"], 1)

    def test_new_pool_is_persisted_immediately(self):
        row = {
            "pairAddress": "persist-now",
            "baseToken": {"address": TOKEN},
            "quoteToken": {"address": WSOL},
            "liquidity": {"usd": 50000},
        }
        with patch("database.save_pool_registry", return_value=True) as save:
            pool_registry.merge_pool_candidates(TOKEN, [row], "dexscreener")
        save.assert_called_once()
        self.assertEqual(save.call_args.args[0], TOKEN)
        self.assertEqual(save.call_args.kwargs["updated_at"], pool_registry._candidate_updated_at[TOKEN])

    def test_dex_full_pool_refresh_has_a_sixty_second_ttl(self):
        with patch.object(pool_registry.time, "time", return_value=100.0):
            self.assertTrue(pool_registry.needs_dexscreener_refresh(TOKEN))
            pool_registry._full_refresh_at[TOKEN] = 100.0
            self.assertFalse(pool_registry.needs_dexscreener_refresh(TOKEN))
        with patch.object(pool_registry.time, "time", return_value=161.0):
            self.assertTrue(pool_registry.needs_dexscreener_refresh(TOKEN))

    def test_gecko_fetch_merges_candidates_and_refreshes_canonical_cache(self):
        payload = {"data": [gecko_pool(WSOL, TOKEN)]}
        with patch.object(pool_registry, "get_json", return_value=payload), \
             patch.object(market_data, "normalize_gecko_pool",
                          return_value={
                              "pairAddress": "pool-gecko",
                              "baseToken": {"address": TOKEN},
                              "quoteToken": {"address": WSOL},
                              "priceUsd": "0.01",
                              "liquidity": {"usd": 100000},
                              "marketSource": "geckoterminal",
                          }) as normalize, \
             patch.object(market_data, "refresh_market_data_from_pool_registry",
                          return_value={"pairAddress": "pool-gecko"}) as refresh:
            result = pool_registry.fetch_gecko_pool_candidates(TOKEN)
        self.assertEqual(result["normalized"], 1)
        normalize.assert_called_once()
        self.assertEqual(normalize.call_args.kwargs["requested_mint"], TOKEN)
        refresh.assert_called_once_with(TOKEN)
        self.assertEqual(pool_registry.get_pool_candidates(TOKEN)[0]["pairAddress"], "pool-gecko")


if __name__ == "__main__":
    unittest.main()
