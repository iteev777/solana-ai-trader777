import unittest
from unittest.mock import patch, Mock

import route_comparison


class RouteComparisonTests(unittest.TestCase):
    def test_raptor_quote_normalizes_minimum_output(self):
        response = Mock()
        response.json.return_value = {
            "outAmount": "1000",
            "minAmountOut": "990",
            "priceImpactPct": 0.002,
            "dexes": ["Raydium"]
        }
        response.raise_for_status.return_value = None
        with patch.object(route_comparison.requests, "get", return_value=response):
            result = route_comparison._raptor("in", "out", 500, 100)
        self.assertEqual(result["out_amount"], 1000)
        self.assertEqual(result["minimum_out_amount"], 990)
        self.assertEqual(result["price_impact_percent"], 0.2)

    def test_comparison_never_fabricates_direct_pool_quote(self):
        jupiter = {
            "provider": "jupiter", "out_amount": 1000,
            "minimum_out_amount": 990, "price_impact_percent": 0.1,
            "quote_latency_ms": 10, "route_diagnostics": {},
            "quoted_at": 1, "route_quality": {"status": "PASS"}
        }
        raptor = {
            "provider": "raptor", "status": "QUOTED",
            "out_amount": 1100, "minimum_out_amount": 1080,
            "price_impact_percent": 0.1, "latency_ms": 20,
            "route": {}, "quoted_at": 1, "quality": None
        }
        with patch("live_execution.get_jupiter_quote", return_value=jupiter), \
             patch.object(route_comparison, "_raptor", return_value=raptor):
            result = route_comparison.compare_quotes("in", "out", 500)
        self.assertEqual(result["best_provider"], "raptor")
        self.assertEqual(result["best_minimum_output"], 1080)
        self.assertEqual(result["quotes"]["direct_pool"]["status"], "NOT_IMPLEMENTED")
        self.assertEqual(result["execution"], "DISABLED")

    def test_invalid_slippage_rejected(self):
        with self.assertRaises(ValueError):
            route_comparison.compare_quotes("in", "out", 500, 301)


if __name__ == "__main__":
    unittest.main()
