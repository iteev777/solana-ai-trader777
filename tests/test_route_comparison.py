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
        self.assertEqual(result["quotes"]["direct_pool"]["status"], "POOL_ADDRESS_REQUIRED")
        self.assertEqual(result["execution"], "DISABLED")


    def test_direct_pool_quote_is_included_when_pool_address_is_supplied(self):
        jupiter = {
            "provider": "jupiter", "out_amount": 1000,
            "minimum_out_amount": 990, "price_impact_percent": 0.1,
            "quote_latency_ms": 10, "route_diagnostics": {},
            "quoted_at": 1, "route_quality": {"status": "PASS"}
        }
        raptor = {
            "provider": "raptor", "status": "QUOTED",
            "out_amount": 900, "minimum_out_amount": 890,
            "price_impact_percent": 0.1, "latency_ms": 20,
            "route": {}, "quoted_at": 1, "quality": None
        }
        direct = {
            "provider": "raydium_cpmm_direct", "protocol": "raydium_cpmm",
            "output_mint": "out",
            "out_amount": 1200, "minimum_out_amount": 1180,
            "price_impact_percent": 0.2, "slot": 777, "pool_id": "pool",
            "creator_fee_on_input": True, "trade_fee_amount": 2,
            "creator_fee_input_amount": 1, "creator_fee_output_amount": 0,
        }
        with patch("live_execution.get_jupiter_quote", return_value=jupiter), \
             patch.object(route_comparison, "_raptor", return_value=raptor), \
             patch("solana_onchain.quote_direct_pool", return_value=direct):
            result = route_comparison.compare_quotes("in", "out", 500, 100, "pool")
        self.assertEqual(result["best_provider"], "raydium_cpmm_direct")
        self.assertEqual(result["best_minimum_output"], 1180)
        self.assertEqual(result["quotes"]["direct_pool"]["slot"], 777)



    def test_pumpswap_fee_breakdown_and_fee_mint_are_exposed(self):
        jupiter = {
            "provider": "jupiter", "out_amount": 1000,
            "minimum_out_amount": 990, "price_impact_percent": 0.1,
            "quote_latency_ms": 10, "route_diagnostics": {},
            "quoted_at": 1, "route_quality": {"status": "PASS"}
        }
        raptor = {
            "provider": "raptor", "status": "QUOTED",
            "out_amount": 900, "minimum_out_amount": 890,
            "price_impact_percent": 0.1, "latency_ms": 20,
            "route": {}, "quoted_at": 1, "quality": None
        }
        direct = {
            "provider": "pumpswap_direct", "protocol": "pumpswap",
            "output_mint": "out", "fee_mint": "quote",
            "out_amount": 1200, "minimum_out_amount": 1180,
            "price_impact_percent": 0.2, "slot": 777, "pool_id": "pool",
            "lp_fee_amount": 2, "protocol_fee_amount": 92,
            "creator_fee_amount": 30, "total_fee_amount": 124,
        }
        with (
            patch("live_execution.get_jupiter_quote", return_value=jupiter),
            patch.object(route_comparison, "_raptor", return_value=raptor),
            patch("solana_onchain.quote_direct_pool", return_value=direct),
        ):
            result = route_comparison.compare_quotes("in", "out", 500, 100, "pool")
        quote = result["quotes"]["direct_pool"]
        self.assertEqual(result["best_provider"], "pumpswap_direct")
        self.assertEqual(quote["fee_mint"], "quote")
        self.assertEqual(quote["fee_amounts"]["lp"], 2)
        self.assertEqual(quote["fee_amounts"]["protocol"], 92)
        self.assertEqual(quote["fee_amounts"]["creator"], 30)
        self.assertEqual(quote["fee_amounts"]["total"], 124)

    def test_router_status_lists_pumpswap_as_a_direct_quote_adapter(self):
        status = route_comparison.get_router_status()
        self.assertIn("PumpSwap", status["direct_pool"]["quote_adapters"])



    def test_requested_output_mint_is_forwarded_to_direct_adapter(self):
        jupiter = {
            "provider": "jupiter", "out_amount": 1000,
            "minimum_out_amount": 990, "price_impact_percent": 0.1,
            "quote_latency_ms": 10, "route_diagnostics": {},
            "quoted_at": 1, "route_quality": {"status": "PASS"}
        }
        raptor = {
            "provider": "raptor", "status": "QUOTED",
            "out_amount": 900, "minimum_out_amount": 890,
            "price_impact_percent": 0.1, "latency_ms": 20,
            "route": {}, "quoted_at": 1, "quality": None
        }
        direct = {
            "provider": "pumpfun_legacy_direct", "protocol": "pumpfun_legacy",
            "output_mint": "out", "out_amount": 1200,
            "minimum_out_amount": 1180, "price_impact_percent": 0.2,
            "slot": 777, "pool_id": "curve",
        }
        with (
            patch("live_execution.get_jupiter_quote", return_value=jupiter),
            patch.object(route_comparison, "_raptor", return_value=raptor),
            patch("solana_onchain.quote_direct_pool", return_value=direct) as direct_quote,
        ):
            result = route_comparison.compare_quotes("in", "out", 500, 100, "curve")
        self.assertEqual(result["best_provider"], "pumpfun_legacy_direct")
        direct_quote.assert_called_once_with(
            "curve", "in", 500, 100, output_mint="out"
        )

    def test_router_status_lists_pumpfun_bonding_curve_quote_adapter(self):
        status = route_comparison.get_router_status()
        self.assertIn("Pump.fun bonding curve", status["direct_pool"]["quote_adapters"])


    def test_direct_quote_with_wrong_output_mint_is_not_compared(self):
        jupiter = {
            "provider": "jupiter", "out_amount": 1000,
            "minimum_out_amount": 990, "price_impact_percent": 0.1,
            "quote_latency_ms": 10, "route_diagnostics": {},
            "quoted_at": 1, "route_quality": {"status": "PASS"}
        }
        raptor = {
            "provider": "raptor", "status": "QUOTED",
            "out_amount": 900, "minimum_out_amount": 890,
            "price_impact_percent": 0.1, "latency_ms": 20,
            "route": {}, "quoted_at": 1, "quality": None
        }
        direct = {
            "provider": "fluxbeam_direct", "protocol": "fluxbeam_amm",
            "output_mint": "wrong-output",
            "out_amount": 9000, "minimum_out_amount": 8900,
            "price_impact_percent": 0.2, "slot": 777, "pool_id": "pool",
        }
        with (
            patch("live_execution.get_jupiter_quote", return_value=jupiter),
            patch.object(route_comparison, "_raptor", return_value=raptor),
            patch("solana_onchain.quote_direct_pool", return_value=direct),
        ):
            result = route_comparison.compare_quotes("in", "out", 500, 100, "pool")
        self.assertEqual(result["best_provider"], "jupiter")
        self.assertEqual(result["quotes"]["direct_pool"]["status"], "UNAVAILABLE")

    def test_invalid_slippage_rejected(self):
        with self.assertRaises(ValueError):
            route_comparison.compare_quotes("in", "out", 500, 301)


if __name__ == "__main__":
    unittest.main()
