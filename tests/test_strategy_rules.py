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

    def test_negation_preserves_missing_evidence_in_nested_group(self):
        rule = {"not": [{"any": [
            {"field": "score", "op": "gt", "value": 100},
            {"field": "volume_m5", "op": "gt", "value": 100},
        ]}]}
        config = {"enabled": True, "valid": True, "rules": {"main": rule}}
        token = dict(self.token, volume={})
        with patch.object(strategy_rules, "get_config", return_value=config):
            passed, reason = strategy_rules.evaluate_strategy_rules(token, 80, "main")
        self.assertFalse(passed)
        self.assertIn("volume_m5: data unavailable", reason)

    def test_negated_named_rule_requires_usable_indicator_evidence(self):
        rule = {"not": [{"condition": "Rsi", "params": {"threshold": 30}}]}
        config = {"enabled": True, "valid": True, "rules": {"main": rule}}
        with patch.object(strategy_rules, "get_config", return_value=config):
            passed, reason = strategy_rules.evaluate_strategy_rules(self.token, 80, "main")
        self.assertFalse(passed)
        self.assertIn("indicator series unavailable", reason)

    def test_bounded_nesting_applies_to_numeric_rules(self):
        rule = {"field": "score", "op": "gt", "value": 1}
        for _ in range(strategy_rules.MAX_RULE_DEPTH + 1):
            rule = {"not": [rule]}
        with self.assertRaisesRegex(ValueError, "nesting is too deep"):
            strategy_rules.validate_rules_config({"main": rule})

    def test_invalid_config_fails_closed(self):
        with patch.dict(os.environ, {"TROJAN_STRATEGY_RULES": "{broken"}, clear=False):
            passed, reason = strategy_rules.evaluate_strategy_rules(self.token, 80, "main")
        self.assertFalse(passed)
        self.assertIn("invalid", reason.lower())


if __name__ == "__main__":
    unittest.main()
