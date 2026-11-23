import unittest
from unittest.mock import patch

import route_comparison


class PumpFunRouteComparisonTests(unittest.TestCase):
    def test_requested_output_mint_reaches_pumpfun_direct_quote(self):
        jupiter = {
            "provider": "jupiter", "out_amount": 1000,
            "minimum_out_amount": 990, "price_impact_percent": 0.1,
            "latency_ms": 10, "route": {}, "quoted_at": 1, "quality": None,
        }
        raptor = {
            "provider": "raptor", "status": "QUOTED",
            "out_amount": 900, "minimum_out_amount": 890,
            "price_impact_percent": 0.1, "latency_ms": 20,
            "route": {}, "quoted_at": 1, "quality": None,
        }
        direct = {
            "provider": "pumpfun_legacy_direct",
            "protocol": "pumpfun_legacy",
            "output_mint": "token-output",
            "out_amount": 1200,
            "minimum_out_amount": 1180,
            "price_impact_percent": 0.2,
            "slot": 777,
            "pool_id": "curve",
        }
        with (
            patch.object(route_comparison, "_jupiter", return_value=jupiter),
            patch.object(route_comparison, "_raptor", return_value=raptor),
            patch("solana_onchain.quote_direct_pool", return_value=direct) as direct_quote,
        ):
            result = route_comparison.compare_quotes(
                "So11111111111111111111111111111111111111112",
                "token-output",
                500,
                100,
                "curve",
            )
        self.assertEqual(result["best_provider"], "pumpfun_legacy_direct")
        direct_quote.assert_called_once_with(
            "curve",
            "So11111111111111111111111111111111111111112",
            500,
            100,
            output_mint="token-output",
        )

    def test_router_status_reports_pumpfun_quote_adapter(self):
        status = route_comparison.get_router_status()
        self.assertIn("Pump.fun bonding curve", status["direct_pool"]["quote_adapters"])


if __name__ == "__main__":
    unittest.main()
