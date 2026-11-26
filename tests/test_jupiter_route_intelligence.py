import time
import unittest
from unittest.mock import Mock, patch

import live_execution


class JupiterRouteIntelligenceTests(unittest.TestCase):
    def test_route_diagnostics_summarize_hops_and_split(self):
        result = live_execution._route_diagnostics([
            {"percent": 60, "swapInfo": {"label": "Raydium"}},
            {"percent": 40, "swapInfo": {"label": "Orca"}},
        ])
        self.assertEqual(result["hop_count"], 2)
        self.assertEqual(result["route_labels"], ["Raydium", "Orca"])
        self.assertTrue(result["is_split_route"])

    def test_sequential_full_weight_hops_are_not_split_routes(self):
        result = live_execution._route_diagnostics([
            {"percent": 100, "swapInfo": {"label": "Raydium"}},
            {"percent": 100, "swapInfo": {"label": "Orca"}},
        ])
        self.assertFalse(result["is_split_route"])

    def test_base_unit_amount_preserves_large_integer_precision(self):
        amount = "18446744073709551615"
        self.assertEqual(live_execution._amount(amount, "amount"), 18446744073709551615)

    def test_route_diagnostics_reject_malformed_plan(self):
        with self.assertRaises(ValueError):
            live_execution._route_diagnostics(["not-a-step"])

    @patch.object(live_execution.requests, "get")
    def test_v2_quote_is_quote_only_and_contains_diagnostics(self, get):
        response = Mock()
        response.json.return_value = {
            "router": "metis",
            "inAmount": "1000",
            "outAmount": "2000",
            "otherAmountThreshold": "1980",
            "priceImpactPct": "0.001",
            "feeBps": 10,
            "routePlan": [
                {"percent": 100, "swapInfo": {"label": "Raydium", "ammKey": "pool"}}
            ],
            "transaction": None,
        }
        response.raise_for_status.return_value = None
        get.return_value = response
        quote = live_execution.get_jupiter_quote("mintA", "mintB", 1000)
        self.assertEqual(quote["api_version"], "v2")
        self.assertEqual(quote["minimum_out_amount"], 1980)
        self.assertEqual(quote["route_diagnostics"]["route_labels"], ["Raydium"])
        self.assertGreaterEqual(quote["quote_latency_ms"], 0)
        self.assertNotIn("taker", get.call_args.kwargs["params"])

    @patch.object(live_execution.requests, "get")
    def test_reject_transaction_bearing_response(self, get):
        response = Mock()
        response.json.return_value = {
            "inAmount": "1000", "outAmount": "2000",
            "transaction": "base64-transaction",
        }
        response.raise_for_status.return_value = None
        get.return_value = response
        with self.assertRaisesRegex(ValueError, "transaction-bearing"):
            live_execution.get_jupiter_quote("mintA", "mintB", 1000)

    def test_route_quality_rejects_excessive_price_impact(self):
        result = live_execution.evaluate_route_quality({
            "price_impact_percent": 4.0,
            "platform_fee_bps": 10,
            "route_diagnostics": {"hop_count": 1, "route_labels": ["Raydium"]}
        })
        self.assertEqual(result["status"], "REJECT")
        self.assertIn("PRICE_IMPACT_ABOVE_LIMIT", result["reasons"])

    @patch("solana_onchain._rpc")
    @patch.object(live_execution, "_jupiter_get")
    def test_unsigned_simulation_never_signs_or_broadcasts(self, get_order, rpc):
        get_order.return_value = {"transaction": "A" * 120, "router": "metis",
                                  "inAmount": "1000", "outAmount": "2000"}
        rpc.return_value = {"context": {"slot": 123}, "value": {
            "err": None, "unitsConsumed": 50000, "logs": ["Program success"]
        }}
        result = live_execution.simulate_unsigned_swap(
            "11111111111111111111111111111111",
            "SysvarRent111111111111111111111111111111111",
            1000, taker="11111111111111111111111111111111"
        )
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["signed"])
        self.assertFalse(result["broadcast"])
        self.assertEqual(rpc.call_args.args[0], "simulateTransaction")
        self.assertFalse(rpc.call_args.args[1][1]["sigVerify"])
        self.assertTrue(rpc.call_args.args[1][1]["replaceRecentBlockhash"])

    @patch("solana_onchain._rpc", return_value={"value": {}})
    @patch.object(live_execution, "_jupiter_get", return_value={"transaction": "A" * 120})
    def test_missing_simulation_outcome_cannot_pass(self, _get_order, _rpc):
        with self.assertRaisesRegex(ValueError, "valid simulation result"):
            live_execution.simulate_unsigned_swap(
                "11111111111111111111111111111111",
                "SysvarRent111111111111111111111111111111111",
                1000, taker="11111111111111111111111111111111",
            )

    def test_plan_keeps_execution_locked(self):
        quote = {
            "provider": "jupiter", "quoted_at": time.time(),
            "input_mint": "mintA", "output_mint": "mintB",
            "in_amount": 1000, "out_amount": 2000,
            "minimum_out_amount": 1980, "price_impact_percent": 0.1,
            "slippage_bps": 100, "route_plan": [],
        }
        plan = live_execution.build_execution_plan(quote, "BUY")
        self.assertTrue(plan["execution_locked"])
        self.assertFalse(plan["signing_available"])
        with self.assertRaises(live_execution.LiveExecutionLocked):
            live_execution.execute_plan(plan)


if __name__ == "__main__":
    unittest.main()
