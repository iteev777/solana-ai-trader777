import unittest

import pool_selection
import pool_analysis


WSOL = pool_selection.WSOL_MINT
TOKEN = "TokenMint111111111111111111111111111111111"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def pair(address, quote, liquidity_usd, native_reserve=None, volume=1000):
    liquidity = {"usd": liquidity_usd}
    if native_reserve is not None:
        liquidity["quote"] = native_reserve
    return {
        "pairAddress": address,
        "dexId": "test-dex",
        "baseToken": {"address": TOKEN},
        "quoteToken": {"address": quote},
        "priceUsd": "0.01",
        "priceNative": "0.0001" if quote == WSOL else "0.01",
        "liquidity": liquidity,
        "volume": {"h24": volume},
    }


class CanonicalPoolSelectionTests(unittest.TestCase):
    def setUp(self):
        pool_selection.reset_canonical_pools()
        pool_analysis.clear_pool_analysis_state()

    def test_native_pair_wins_over_a_larger_usdc_pool(self):
        stable = pair("stable", USDC, 2_000_000)
        native = pair("native", WSOL, 900_000, native_reserve=4_000)
        selected = pool_selection.select_best_pairs([stable, native], [TOKEN])[TOKEN]
        self.assertEqual(selected["pairAddress"], "native")
        self.assertEqual(selected["poolSelection"]["metricUnit"], "SOL")
        self.assertEqual(selected["poolSelection"]["nativePairCount"], 1)

    def test_incumbent_is_retained_within_eighty_percent_of_best(self):
        incumbent = pair("incumbent", WSOL, 900_000, native_reserve=80)
        best = pair("best", WSOL, 1_000_000, native_reserve=100)
        first = pool_selection.select_best_pairs([incumbent, best], [TOKEN])[TOKEN]
        self.assertEqual(first["pairAddress"], "best")
        retained = pool_selection.select_best_pairs(
            [incumbent, best], [TOKEN]
        )[TOKEN]
        self.assertEqual(retained["pairAddress"], "best")
        # Set incumbent explicitly through a first snapshot where it leads,
        # then let the new pool lead by less than the hysteresis threshold.
        pool_selection.reset_canonical_pools()
        pool_selection.select_best_pairs([incumbent], [TOKEN])
        retained = pool_selection.select_best_pairs([incumbent, best], [TOKEN])[TOKEN]
        self.assertEqual(retained["pairAddress"], "incumbent")
        self.assertTrue(retained["poolSelection"]["incumbentRetained"])

    def test_incumbent_is_replaced_when_it_falls_below_keep_ratio(self):
        old = pair("old", WSOL, 100_000, native_reserve=50)
        new = pair("new", WSOL, 1_000_000, native_reserve=100)
        pool_selection.select_best_pairs([old], [TOKEN])
        selected = pool_selection.select_best_pairs([old, new], [TOKEN])[TOKEN]
        self.assertEqual(selected["pairAddress"], "new")
        self.assertFalse(selected["poolSelection"]["incumbentRetained"])

    def test_stable_pool_is_fallback_when_no_native_pool_exists(self):
        low = pair("low", USDC, 100_000)
        high = pair("high", USDC, 500_000)
        selected = pool_selection.select_best_pairs([low, high], [TOKEN])[TOKEN]
        self.assertEqual(selected["pairAddress"], "high")
        self.assertEqual(selected["poolSelection"]["metricUnit"], "USD")
        self.assertEqual(selected["poolSelection"]["reason"], "usd_liquidity_fallback")

    def test_candidate_summary_exposes_alternatives(self):
        pools = [
            pair("sol-a", WSOL, 100_000, native_reserve=10),
            pair("sol-b", WSOL, 200_000, native_reserve=20),
            pair("usdc", USDC, 500_000),
        ]
        selected = pool_selection.select_best_pairs(pools, [TOKEN])[TOKEN]
        self.assertEqual(selected["poolSelection"]["candidateCount"], 3)
        self.assertEqual(len(selected["poolCandidates"]), 3)

    def test_quarantined_best_pool_is_skipped_for_next_supported_candidate(self):
        blocked = pair("unsupported-owner", WSOL, 1_000_000, native_reserve=500)
        healthy = pair("healthy", WSOL, 500_000, native_reserve=250)
        pool_analysis.record_structural_failure(
            "unsupported-owner", ValueError("unsupported pool layout")
        )
        selected = pool_selection.select_best_pairs([blocked, healthy], [TOKEN])[TOKEN]
        self.assertEqual(selected["pairAddress"], "healthy")
        self.assertEqual(selected["poolSelection"]["candidateCount"], 1)

    def test_transient_rpc_backoff_excludes_unverified_pool(self):
        blocked = pair("rpc-down", WSOL, 1_000_000, native_reserve=500)
        healthy = pair("healthy", WSOL, 500_000, native_reserve=250)
        pool_analysis.record_transient_failure(
            "rpc-down", RuntimeError("RPC timeout")
        )
        selected = pool_selection.select_best_pairs([blocked, healthy], [TOKEN])[TOKEN]
        self.assertEqual(selected["pairAddress"], "healthy")

    def test_malformed_rows_and_other_chains_cannot_distort_solana_ranking(self):
        valid = pair("solana", WSOL, 100_000)
        foreign = dict(pair("foreign", WSOL, 900_000, native_reserve=9999), chainId="ethereum")
        overflow = dict(pair("overflow", WSOL, 10), priceUsd="1e300", priceNative="1e-300")
        selected = pool_selection.select_best_pairs([None, foreign, overflow, valid], [TOKEN])[TOKEN]
        self.assertEqual(selected["pairAddress"], "solana")
        self.assertEqual(selected["poolSelection"]["candidateCount"], 2)
        self.assertEqual(selected["poolSelection"]["metric"], 500)

    def test_invalid_or_quote_side_prices_are_not_misattributed(self):
        quote_side = {
            "pairAddress": "quote-side",
            "baseToken": {"address": "some-other-token"},
            "quoteToken": {"address": TOKEN},
            "priceUsd": "0.001",
            "liquidity": {"usd": 100_000},
        }
        invalid = pair("invalid", WSOL, 100_000, native_reserve=10)
        invalid["priceUsd"] = "NaN"
        self.assertEqual(
            pool_selection.select_best_pairs([quote_side, invalid], [TOKEN]),
            {}
        )


if __name__ == "__main__":
    unittest.main()
