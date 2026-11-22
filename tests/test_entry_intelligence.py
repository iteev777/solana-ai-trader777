import unittest
from unittest.mock import patch

import entry_intelligence


class EntryIntelligenceTests(unittest.TestCase):
    @patch("market_data.get_market_data", return_value={"priceUsd": "100"})
    @patch("live_execution.get_execution_status", return_value={
        "simulation_available": False
    })
    @patch("live_execution.get_jupiter_quote")
    def test_route_passes_and_converts_usd_to_lamports(self, quote, status, market):
        quote.return_value = {
            "router": "metis", "route_quality": {"status": "PASS", "score": 95},
            "price_impact_percent": 0.1, "platform_fee_bps": 10,
            "minimum_out_amount": 100, "out_amount": 105,
            "quote_latency_ms": 12, "route_diagnostics": {"hop_count": 1},
        }
        result = entry_intelligence.analyze_entry_route("MintToken", 5.0)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["input_amount_lamports"], 50_000_000)
        quote.assert_called_once()

    @patch("market_data.get_market_data", return_value={"priceUsd": "100"})
    @patch("live_execution.get_execution_status", return_value={
        "simulation_available": False
    })
    @patch("live_execution.get_jupiter_quote")
    def test_bad_route_rejected(self, quote, status, market):
        quote.return_value = {"route_quality": {"status": "REJECT", "score": 20}}
        result = entry_intelligence.analyze_entry_route("MintToken", 5)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "JUPITER_ROUTE_REJECTED")

    @patch("market_data.get_market_data", return_value={"priceUsd": "0"})
    def test_missing_sol_price_fails_closed(self, market):
        result = entry_intelligence.analyze_entry_route("MintToken", 5)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "SOL_USD_PRICE_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
