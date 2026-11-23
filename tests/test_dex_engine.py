import time
import unittest
from unittest.mock import patch

import dex_engine


class DexEngineTests(unittest.TestCase):
    def _comparison(self, quotes):
        return {
            "input_mint": "input",
            "output_mint": "output",
            "input_amount": 1000,
            "slippage_bps": 100,
            "quotes": quotes,
        }

    def _quote(self, provider, minimum, **overrides):
        quote = {
            "provider": provider,
            "status": "QUOTED",
            "out_amount": minimum + 10,
            "minimum_out_amount": minimum,
            "price_impact_percent": 0.2,
            "quoted_at": time.time(),
            "latency_ms": 25,
            "route": {"hops": 1},
            "quality": {"status": "PASS"},
        }
        quote.update(overrides)
        return quote

    @patch("route_comparison.compare_quotes")
    def test_selects_highest_minimum_output(self, compare):
        compare.return_value = self._comparison({
            "jupiter": self._quote("jupiter", 900),
            "raptor": self._quote("raptor", 950),
        })
        result = dex_engine.build_route("input", "output", 1000)
        self.assertEqual(result["best_provider"], "raptor")
        self.assertEqual(result["best_minimum_output"], 950)
        self.assertTrue(result["selected_route"]["selected"])
        self.assertEqual(result["execution"], "LOCKED")
        self.assertFalse(result["live_trading_ready"])

    @patch("route_comparison.compare_quotes")
    def test_rejects_stale_quote(self, compare):
        compare.return_value = self._comparison({
            "jupiter": self._quote("jupiter", 900, quoted_at=time.time() - 60),
        })
        result = dex_engine.build_route("input", "output", 1000)
        self.assertIsNone(result["selected_route"])
        self.assertEqual(result["rejected_routes"]["jupiter"], ["QUOTE_STALE"])

    @patch("route_comparison.compare_quotes")
    def test_rejects_unknown_price_impact(self, compare):
        compare.return_value = self._comparison({
            "raptor": self._quote("raptor", 900, price_impact_percent=None),
        })
        result = dex_engine.build_route("input", "output", 1000)
        self.assertEqual(
            result["rejected_routes"]["raptor"], ["PRICE_IMPACT_UNAVAILABLE"]
        )

    @patch("route_comparison.compare_quotes")
    def test_rejects_route_quality_failure(self, compare):
        compare.return_value = self._comparison({
            "jupiter": self._quote(
                "jupiter", 900, quality={"status": "REJECT"}
            ),
        })
        result = dex_engine.build_route("input", "output", 1000)
        self.assertEqual(
            result["rejected_routes"]["jupiter"], ["ROUTE_QUALITY_REJECTED"]
        )

    @patch("route_comparison.compare_quotes")
    def test_rejects_wrong_output_mint(self, compare):
        compare.return_value = self._comparison({
            "direct_pool": self._quote(
                "raydium_direct", 900, output_mint="different-mint"
            ),
        })
        result = dex_engine.build_route("input", "output", 1000)
        self.assertEqual(
            result["rejected_routes"]["direct_pool"], ["OUTPUT_MINT_MISMATCH"]
        )

    @patch("route_comparison.compare_quotes")
    def test_rejects_excessive_hops(self, compare):
        compare.return_value = self._comparison({
            "jupiter": self._quote(
                "jupiter", 900, route={"hops": dex_engine.MAX_ALLOWED_HOPS + 1}
            ),
        })
        result = dex_engine.build_route("input", "output", 1000)
        self.assertEqual(
            result["rejected_routes"]["jupiter"], ["HOP_COUNT_OUTSIDE_LIMIT"]
        )

    def test_rejects_fractional_raw_amounts_and_hops(self):
        with self.assertRaises(ValueError):
            dex_engine._positive_int(1.5, "out_amount")
        candidate = self._quote("jupiter", 900, route={"hops": 1.5})
        normalized, reasons = dex_engine._candidate("jupiter", candidate, "output", time.time())
        self.assertIsNone(normalized)
        self.assertEqual(reasons, ["INVALID_HOP_COUNT"])

    def test_invalid_environment_values_fall_back_safely(self):
        with patch.dict("os.environ", {
            "TROJAN_DEX_MAX_QUOTE_AGE_SECONDS": "nan",
            "TROJAN_DEX_MAX_PRICE_IMPACT_PERCENT": "bad",
            "TROJAN_DEX_MAX_HOPS": "not-an-int",
        }):
            self.assertEqual(
                dex_engine._bounded_env_float("TROJAN_DEX_MAX_QUOTE_AGE_SECONDS", 8, 1, 30),
                8.0,
            )
            self.assertEqual(
                dex_engine._bounded_env_float("TROJAN_DEX_MAX_PRICE_IMPACT_PERCENT", 3, 0.01, 20),
                3.0,
            )
            self.assertEqual(
                dex_engine._bounded_env_int("TROJAN_DEX_MAX_HOPS", 4, 1, 8),
                4,
            )

    def test_rejects_invalid_pair_and_amount(self):
        with self.assertRaises(ValueError):
            dex_engine.build_route("same", "same", 1000)
        with self.assertRaises(ValueError):
            dex_engine.build_route("input", "output", 0)

    @patch("route_comparison.get_router_status")
    def test_engine_status_keeps_live_execution_locked(self, status):
        status.return_value = {"direct_pool": {"quote_adapters": ["PumpSwap"]}}
        result = dex_engine.get_engine_status()
        self.assertTrue(result["execution_locked"])
        self.assertFalse(result["signing_available"])
        self.assertFalse(result["broadcast_available"])
        self.assertIn("PumpSwap", result["direct_pool"]["quote_adapters"])


    def _aggregator_response(self):
        now = time.time()
        return self._comparison({
            "jupiter": self._quote("jupiter", 900, quoted_at=now),
            "raptor": self._quote("raptor", 880, quoted_at=now),
        })

    @patch("route_comparison.compare_quotes")
    @patch("solana_onchain.quote_direct_pool")
    def test_compares_multiple_direct_pools_with_aggregators(self, direct, compare):
        compare.return_value = self._aggregator_response()
        direct.side_effect = [
            {
                "provider": "raydium_cpmm", "protocol": "raydium_cpmm",
                "output_mint": "output", "out_amount": 1000,
                "minimum_out_amount": 990, "price_impact_percent": 0.4,
                "snapshot_age_seconds": 0.2, "pool_id": "pool-a", "slot": 123,
            },
            {
                "provider": "pumpswap", "protocol": "pumpswap",
                "output_mint": "output", "out_amount": 1100,
                "minimum_out_amount": 1090, "price_impact_percent": 0.5,
                "snapshot_age_seconds": 0.2, "pool_id": "pool-b", "slot": 124,
            },
        ]
        result = dex_engine.build_routes(
            "input", "output", 1000, pool_addresses=["pool-a", "pool-b"]
        )
        self.assertEqual(compare.call_count, 1)
        self.assertEqual(direct.call_count, 2)
        self.assertEqual(result["best_pool"], "pool-b")
        self.assertEqual(result["selected_route"]["net_minimum_output"], 1090)
        self.assertEqual(sum(x["selected"] for x in result["routes_eligible"]), 1)
        self.assertEqual(result["execution"], "LOCKED")

    @patch("route_comparison.compare_quotes")
    @patch("solana_onchain.quote_direct_pool")
    def test_rejects_stale_direct_pool_quote(self, direct, compare):
        compare.return_value = self._aggregator_response()
        direct.return_value = {
            "provider": "raydium_cpmm", "protocol": "raydium_cpmm",
            "output_mint": "output", "out_amount": 1000,
            "minimum_out_amount": 990, "price_impact_percent": 0.4,
            "snapshot_age_seconds": 30, "pool_id": "pool-a", "slot": 123,
        }
        result = dex_engine.build_routes(
            "input", "output", 1000, pool_addresses=["pool-a"]
        )
        self.assertEqual(result["routes_rejected"]["direct:pool-a"], "QUOTE_STALE")

    @patch("route_comparison.compare_quotes")
    @patch("solana_onchain.quote_direct_pool")
    def test_deducts_only_explicit_output_mint_network_fee(self, direct, compare):
        compare.return_value = self._aggregator_response()
        direct.return_value = {
            "provider": "raydium_cpmm", "protocol": "raydium_cpmm",
            "output_mint": "output", "out_amount": 1000,
            "minimum_out_amount": 990, "price_impact_percent": 0.4,
            "snapshot_age_seconds": 0.2, "pool_id": "pool-a", "slot": 123,
        }
        result = dex_engine.build_routes(
            "input", "output", 1000, pool_addresses=["pool-a"],
            network_fee_output_amounts={
                "direct:pool-a": 50, "jupiter": 0, "raptor": 0
            }
        )
        route = next(x for x in result["routes_eligible"] if x["pool_id"] == "pool-a")
        self.assertEqual(route["net_minimum_output"], 940)
        self.assertFalse(result["unpriced_network_costs_may_change_ranking"])

    @patch("dex_engine.build_routes")
    def test_dashboard_entrypoint_accepts_comma_separated_pool_addresses(self, build):
        build.return_value = {"execution": "LOCKED"}
        result = dex_engine.build_route("input", "output", "1000", 100, "pool-a,pool-b")
        self.assertEqual(result["execution"], "LOCKED")
        self.assertEqual(build.call_args.kwargs["pool_addresses"], ["pool-a", "pool-b"])


if __name__ == "__main__":
    unittest.main()
