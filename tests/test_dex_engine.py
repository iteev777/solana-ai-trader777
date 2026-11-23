import time
import unittest
from unittest.mock import patch

import dex_engine
import route_comparison


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
        direct.side_effect = lambda pool, *args, **kwargs: self.direct_quote(
            pool, 1100 if pool == "pool-a" else 1200
        )
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

    @patch("route_comparison.compare_quotes")
    @patch("solana_onchain.quote_direct_pool")
    @patch("pool_registry.get_pool_candidates")
    @patch("pool_registry.enqueue_dex_pool_refresh")
    def test_auto_discovers_only_matching_pools_from_both_token_indexes(
        self, enqueue, registry, direct, aggregate
    ):
        aggregate.return_value = self.aggregator_quotes()
        registry.side_effect = [
            [
                {"pairAddress": "pool-a", "baseToken": {"address": self.input_mint},
                 "quoteToken": {"address": self.output_mint}, "liquidity": {"usd": 5000}},
                {"pairAddress": "unrelated", "baseToken": {"address": self.input_mint},
                 "quoteToken": {"address": "other-mint"}, "liquidity": {"usd": 9000}},
            ],
            [
                {"pairAddress": "pool-b", "baseToken": {"address": self.output_mint},
                 "quoteToken": {"address": self.input_mint}, "liquidity": {"usd": 10000}},
            ],
        ]
        direct.side_effect = lambda pool, *args, **kwargs: self.direct_quote(
            pool, 1100 if pool == "pool-a" else 1200
        )
        result = dex_engine.build_routes(
            self.input_mint, self.output_mint, 100
        )
        self.assertEqual(registry.call_count, 2)
        self.assertEqual(enqueue.call_count, 2)
        self.assertEqual(direct.call_count, 2)
        self.assertEqual(result["pool_discovery"], "AUTOMATIC_REGISTRY_PAIR_MATCH")
        self.assertEqual(result["best_pool"], "pool-b")
        self.assertEqual(
            {route["pool_id"] for route in result["routes_eligible"] if route["pool_id"]},
            {"pool-a", "pool-b"},
        )

    def test_rejects_slippage_above_configured_cap_before_provider_calls(self):
        with patch("route_comparison.compare_quotes") as aggregate:
            with self.assertRaisesRegex(ValueError, "Slippage exceeds configured safety cap"):
                dex_engine.build_routes(
                    self.input_mint, self.output_mint, 100,
                    pool_addresses=[], slippage_bps=route_comparison.MAX_SLIPPAGE_BPS + 1
                )
        aggregate.assert_not_called()

    def test_rejects_boolean_slippage(self):
        with self.assertRaisesRegex(ValueError, "slippage_bps must be an integer"):
            dex_engine.build_routes(
                self.input_mint, self.output_mint, 100,
                pool_addresses=[], slippage_bps=True
            )

    def test_rejects_invalid_fee_estimate(self):
        with self.assertRaises(ValueError):
            dex_engine.build_routes(
                self.input_mint, self.output_mint, 100,
                network_fee_output_amounts={"jupiter": -1}
            )

    def test_rejects_invalid_quote_latency(self):
        item = self.aggregator_quotes()["quotes"]["jupiter"]
        item["latency_ms"] = float("nan")
        with self.assertRaisesRegex(ValueError, "latency_ms must be finite"):
            dex_engine._normalize_candidate(
                "jupiter", item, self.input_mint, self.output_mint, time.time()
            )

    def test_rejects_negative_quote_latency(self):
        item = self.aggregator_quotes()["quotes"]["jupiter"]
        item["latency_ms"] = -1
        with self.assertRaisesRegex(ValueError, "LATENCY_OUTSIDE_LIMIT"):
            dex_engine._normalize_candidate(
                "jupiter", item, self.input_mint, self.output_mint, time.time()
            )

    def test_rejects_unknown_network_fee_provider_key(self):
        with self.assertRaisesRegex(ValueError, "Unknown network fee provider key"):
            dex_engine.build_routes(
                self.input_mint, self.output_mint, 100,
                pool_addresses=[], network_fee_output_amounts={"jupter": 1}
            )

    def test_rejects_network_fee_for_unknown_direct_pool(self):
        with self.assertRaisesRegex(ValueError, "unknown route"):
            dex_engine.build_routes(
                self.input_mint, self.output_mint, 100,
                pool_addresses=["pool-a"],
                network_fee_output_amounts={"direct:pool-b": 1}
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
        self.assertTrue(status["pool_discovery"]["automatic_pair_matching"])
        self.assertEqual(status["pool_discovery"]["max_candidates_per_pair"], 20)
        self.assertTrue(status["native_transaction_builders"]["raydium_cpmm"]["available"])
        self.assertTrue(status["native_transaction_builders"]["raydium_cpmm"]["rpc_simulation_required"])
        self.assertFalse(status["native_transaction_builders"]["raydium_cpmm"]["live_execution"])
        self.assertTrue(status["native_transaction_builders"]["pumpswap_v2"]["available"])
        self.assertTrue(status["native_transaction_builders"]["pumpswap_v2"]["rpc_simulation_required"])
        self.assertFalse(status["native_transaction_builders"]["pumpswap_v2"]["live_execution"])
        self.assertFalse(status["native_transaction_builders"]["other_protocols"]["available"])

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
