import os
import unittest
from unittest.mock import patch

import strategy_rules


class StrategyRulesTests(unittest.TestCase):
    def setUp(self):
        self.token = {
            "liquidity": {"usd": 20000},
            "volume": {"m5": 5000, "h24": 100000},
            "priceChange": {"m5": 1.5, "h1": 3, "h24": 12},
            "txns": {"m5": {"buys": 8, "sells": 2}},
            "discovery_sources": ["gecko", "dex"],
            "pairCreatedAt": 1
        }

    def test_no_config_preserves_existing_strategy(self):
        with patch.dict(os.environ, {"TROJAN_STRATEGY_RULES": ""}, clear=False):
            passed, reason = strategy_rules.evaluate_strategy_rules(self.token, 80, "main")
        self.assertTrue(passed)
        self.assertIn("No custom", reason)

    def test_all_conditions_pass(self):
        config = '{"main":{"all":[{"field":"liquidity_usd","op":"gte","value":10000},{"field":"buy_ratio","op":"gte","value":0.8}]}}'
        with patch.dict(os.environ, {"TROJAN_STRATEGY_RULES": config}, clear=False):
            passed, _ = strategy_rules.evaluate_strategy_rules(self.token, 80, "main")
        self.assertTrue(passed)

    def test_any_group_passes_one_condition(self):
        config = '{"main":{"any":[{"field":"liquidity_usd","op":"gt","value":50000},{"field":"score","op":"gte","value":70}]}}'
        with patch.dict(os.environ, {"TROJAN_STRATEGY_RULES": config}, clear=False):
            passed, _ = strategy_rules.evaluate_strategy_rules(self.token, 80, "main")
        self.assertTrue(passed)

    def test_not_group_inverts_condition(self):
        config = '{"main":{"not":[{"field":"liquidity_usd","op":"lt","value":10000}]}}'
        with patch.dict(os.environ, {"TROJAN_STRATEGY_RULES": config}, clear=False):
            passed, _ = strategy_rules.evaluate_strategy_rules(self.token, 80, "main")
        self.assertTrue(passed)

    def test_missing_market_data_fails_closed(self):
        config = '{"main":{"all":[{"field":"volume_m5","op":"gte","value":100}]}}'
        token = dict(self.token)
        token["volume"] = {"h24": 100000}
        with patch.dict(os.environ, {"TROJAN_STRATEGY_RULES": config}, clear=False):
            passed, reason = strategy_rules.evaluate_strategy_rules(token, 80, "main")
        self.assertFalse(passed)
        self.assertIn("unavailable", reason)

    def test_invalid_config_fails_closed(self):
        with patch.dict(os.environ, {"TROJAN_STRATEGY_RULES": "{broken"}, clear=False):
            passed, reason = strategy_rules.evaluate_strategy_rules(self.token, 80, "main")
        self.assertFalse(passed)
        self.assertIn("invalid", reason.lower())


if __name__ == "__main__":
    unittest.main()
