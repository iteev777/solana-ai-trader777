import unittest

import ai_analyzer
import market_data
import risk_manager


class RiskManagerTests(unittest.TestCase):
    def test_default_daily_loss_cap_is_50_usd(self):
        self.assertEqual(risk_manager.MAX_DAILY_LOSS, 50.0)

    def test_normal_ticket_passes_with_full_balance(self):
        allowed, reason = risk_manager.check_trade_risk(
            balance=100, open_positions=0, daily_loss=0, trade_size=5,
            strategy="main", main_positions=0, sniper_positions=0
        )
        self.assertTrue(allowed, reason)

    def test_ticket_cannot_exceed_balance_fraction(self):
        allowed, reason = risk_manager.check_trade_risk(
            balance=40, open_positions=0, daily_loss=0, trade_size=5,
            strategy="main", main_positions=0, sniper_positions=0
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "TRADE SIZE EXCEEDS BALANCE RISK FRACTION")

    def test_daily_loss_cap_blocks_new_entries(self):
        allowed, reason = risk_manager.check_trade_risk(
            balance=100, open_positions=0,
            daily_loss=risk_manager.MAX_DAILY_LOSS, trade_size=5,
            strategy="main", main_positions=0, sniper_positions=0
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "DAILY LOSS LIMIT REACHED")

    def test_non_finite_inputs_are_rejected(self):
        allowed, _ = risk_manager.check_trade_risk(
            balance=float("nan"), open_positions=0, daily_loss=0,
            trade_size=5, strategy="main", main_positions=0,
            sniper_positions=0
        )
        self.assertFalse(allowed)


class MarketDataIntegrityTests(unittest.TestCase):
    def test_dexscreener_price_is_only_assigned_to_base_mint(self):
        pair = {
            "priceUsd": "0.001",
            "baseToken": {"address": "mint-base"},
            "quoteToken": {"address": "mint-quote"},
            "liquidity": {"usd": 25000}
        }
        selected = market_data.select_best_pairs(
            [pair], ["mint-base", "mint-quote"]
        )
        self.assertIn("mint-base", selected)
        self.assertNotIn("mint-quote", selected)

    def test_invalid_market_volume_is_rejected(self):
        token = {
            "liquidity": {"usd": 20000},
            "volume": {"h24": 1000, "m5": -1},
            "priceChange": {"m5": 1, "h1": 1, "h24": 5},
            "txns": {"m5": {"buys": 10, "sells": 2}}
        }
        result = ai_analyzer.analyze_token(token, strategy="main")
        self.assertEqual(result["decision"], "REJECT")
        self.assertIn("Invalid volume data", result["reasons"])


if __name__ == "__main__":
    unittest.main()
