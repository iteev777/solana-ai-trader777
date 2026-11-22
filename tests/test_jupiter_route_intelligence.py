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
