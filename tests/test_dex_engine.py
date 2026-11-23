import time
import unittest
from unittest.mock import patch

import dex_engine


class MultiPoolDexEngineTests(unittest.TestCase):
    def setUp(self):
        self.input_mint = "input-mint"
        self.output_mint = "output-mint"

    def aggregator_quotes(self, jupiter_out=1000, raptor_out=980):
        now = time.time()

        def item(provider, output, impact=0.2):
            return {
                "provider": provider,
                "status": "QUOTED",
                "output_mint": self.output_mint,
                "out_amount": output,
                "minimum_out_amount": output - 10,
                "price_impact_percent": impact,
                "quoted_at": now,
                "latency_ms": 20,
                "route": {"hops": 1},
                "quality": {"status": "PASS"},
            }

        return {
            "slippage_bps": 100,
            "quotes": {
                "jupiter": item("jupiter", jupiter_out),
                "raptor": item("raptor", raptor_out),
            },
        }

    def direct_quote(self, pool, output, age=0.2, impact=0.4):
        return {
            "provider": "solana_direct_pool",
            "protocol": "raydium_cpmm",
            "status": "QUOTED",
            "output_mint": self.output_mint,
            "out_amount": output,
            "minimum_out_amount": output - 5,
            "price_impact_percent": impact,
            "snapshot_age_seconds": age,
            "pool_id": pool,
            "slot": 123,
            "fee_amounts": {"trade": 2},
        }

    @patch("route_comparison.compare_quotes")
    @patch("solana_onchain.quote_direct_pool")
    def test_compares_all_direct_pools_and_aggregators(self, direct, aggregate):
        aggregate.return_value = self.aggregator_quotes()
        direct.side_effect = [
            self.direct_quote("pool-a", 1100),
            self.direct_quote("pool-b", 1200),
        ]
        result = dex_engine.build_routes(
            self.input_mint, self.output_mint, 100,
            pool_addresses=["pool-a", "pool-b"]
        )
        self.assertEqual(direct.call_count, 2)
        self.assertEqual(aggregate.call_count, 1)
        self.assertEqual(result["best_pool"], "pool-b")
        self.assertEqual(result["selected_route"]["net_minimum_output"], 1195)
        self.assertEqual(sum(x["selected"] for x in result["routes_eligible"]), 1)
        self.assertEqual(result["execution"], "LOCKED")

    @patch("route_comparison.compare_quotes")
    @patch("solana_onchain.quote_direct_pool")
    def test_explicit_network_fee_is_subtracted_in_output_units(self, direct, aggregate):
        aggregate.return_value = self.aggregator_quotes()
        direct.return_value = self.direct_quote("pool-a", 1100)
        result = dex_engine.build_routes(
            self.input_mint, self.output_mint, 100,
            pool_addresses=["pool-a"],
            network_fee_output_amounts={
                "direct:pool-a": 100, "jupiter": 0, "raptor": 0
            }
        )
        direct_route = next(x for x in result["routes_eligible"] if x["pool_id"] == "pool-a")
        self.assertEqual(direct_route["net_minimum_output"], 995)
        self.assertFalse(result["unpriced_network_costs_may_change_ranking"])

    @patch("route_comparison.compare_quotes")
    @patch("solana_onchain.quote_direct_pool")
    def test_stale_direct_quote_is_rejected(self, direct, aggregate):
        aggregate.return_value = self.aggregator_quotes()
        direct.return_value = self.direct_quote("pool-a", 1500, age=20)
        result = dex_engine.build_routes(
            self.input_mint, self.output_mint, 100, pool_addresses=["pool-a"]
        )
        self.assertIn("direct:pool-a", result["routes_rejected"])
        self.assertEqual(result["routes_rejected"]["direct:pool-a"], "QUOTE_STALE")

    @patch("route_comparison.compare_quotes")
    @patch("solana_onchain.quote_direct_pool")
    def test_unknown_price_impact_is_rejected(self, direct, aggregate):
        aggregate.return_value = self.aggregator_quotes()
        direct.return_value = self.direct_quote("pool-a", 1500, impact=None)
        result = dex_engine.build_routes(
            self.input_mint, self.output_mint, 100, pool_addresses=["pool-a"]
        )
        self.assertIn("direct:pool-a", result["routes_rejected"])

    def test_deduplicates_repeated_and_rejects_excessive_unique_pool_addresses(self):
        self.assertEqual(
            dex_engine._validate_pool_addresses(["pool-a", "pool-a"]),
            ["pool-a"]
        )
        with self.assertRaises(ValueError):
            dex_engine._validate_pool_addresses(["pool-%d" % index for index in range(21)])

    def test_rejects_invalid_fee_estimate(self):
        with self.assertRaises(ValueError):
            dex_engine.build_routes(
                self.input_mint, self.output_mint, 100,
                network_fee_output_amounts={"jupiter": -1}
            )

    def test_status_keeps_live_execution_locked(self):
        with patch("solana_onchain.get_onchain_status", return_value={
            "supported_protocols": ["raydium_cpmm"]
        }), patch("route_comparison.get_router_status", return_value={
            "jupiter": {}, "raptor": {}, "direct_pool": {}
        }):
            status = dex_engine.get_engine_status()
        self.assertTrue(status["execution_locked"])
        self.assertFalse(status["signing_available"])
        self.assertFalse(status["broadcast_available"])
        self.assertFalse(status["live_trading_ready"])

    @patch("dex_engine.build_routes")
    def test_dashboard_compatibility_accepts_comma_separated_pool_list(self, build):
        build.return_value = {"execution": "LOCKED"}
        result = dex_engine.build_route(
            self.input_mint, self.output_mint, "100", 75, "pool-a,pool-b"
        )
        self.assertEqual(result["execution"], "LOCKED")
        self.assertEqual(build.call_args.kwargs["pool_addresses"], ["pool-a", "pool-b"])


    def test_missing_output_mint_is_rejected(self):
        item = self.aggregator_quotes()["quotes"]["jupiter"]
        item.pop("output_mint")
        with self.assertRaisesRegex(ValueError, "OUTPUT_MINT_MISMATCH"):
            dex_engine._normalize_candidate("jupiter", item, self.input_mint, self.output_mint, time.time())

    def test_missing_hop_count_is_rejected(self):
        item = self.aggregator_quotes()["quotes"]["jupiter"]
        item["route"] = {}
        with self.assertRaisesRegex(ValueError, "HOP_COUNT_UNAVAILABLE"):
            dex_engine._normalize_candidate("jupiter", item, self.input_mint, self.output_mint, time.time())

    def test_non_pass_route_quality_is_rejected(self):
        item = self.aggregator_quotes()["quotes"]["jupiter"]
        item["quality"] = {"status": "UNKNOWN"}
        with self.assertRaisesRegex(ValueError, "ROUTE_QUALITY_NOT_PASSED"):
            dex_engine._normalize_candidate("jupiter", item, self.input_mint, self.output_mint, time.time())



if __name__ == "__main__":
    unittest.main()
