import os
import unittest
from unittest.mock import Mock, patch

import adaptive_learning
import rpc_transport
import strategy_rules
import onchain_stream


class RpcTransportTests(unittest.TestCase):
    def setUp(self):
        with rpc_transport._guard:
            rpc_transport._state.clear()
            rpc_transport._endpoint_locks.clear()
            rpc_transport._cursor = 0

    def test_429_on_primary_fails_over_without_exposing_urls(self):
        limited = Mock(status_code=429, headers={"Retry-After": "1"})
        healthy = Mock(status_code=200, headers={})
        healthy.json.return_value = {"jsonrpc": "2.0", "result": "ok"}
        healthy.raise_for_status.return_value = None
        with patch.dict(os.environ, {
            "SOLANA_RPC_URLS": "https://secret-primary.example/rpc?token=hidden,https://secondary.example/rpc",
        }, clear=True), patch.object(rpc_transport.requests, "post", side_effect=[limited, healthy]) as post:
            result = rpc_transport.rpc_call("getHealth", [])
            status = rpc_transport.get_rpc_status()
        self.assertEqual(result, "ok")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(status["endpoint_count"], 2)
        self.assertNotIn("hidden", str(status))
        self.assertEqual(status["endpoints"][0]["rate_limits"], 1)


class AdaptiveLearningTests(unittest.TestCase):
    def _report(self, rows):
        with patch.object(adaptive_learning.database, "get_recent_trades", return_value=rows), \
             patch.object(adaptive_learning, "_cache", {"updated_at": 0.0, "report": None}):
            return adaptive_learning.get_learning_report(force=True)

    def test_no_sizing_change_before_minimum_sample(self):
        rows = [{"action": "SELL", "strategy": "main", "profit_loss": 1.0}] * 5
        profile = self._report(rows)["strategies"][0]
        self.assertEqual(profile["size_multiplier"], 1.0)
        self.assertEqual(profile["status"], "collecting_sample")

    def test_positive_and_negative_samples_are_bounded(self):
        wins = [{"action": "SELL", "strategy": "main", "profit_loss": 2.0}] * 24
        losses = [{"action": "SELL", "strategy": "main", "profit_loss": -1.0}] * 6
        profile = self._report(wins + losses)["strategies"][0]
        self.assertEqual(profile["status"], "learning_active")
        self.assertGreaterEqual(profile["size_multiplier"], 0.80)
        self.assertLessEqual(profile["size_multiplier"], 1.15)

    def test_five_recent_losses_cap_multiplier_even_after_profitable_history(self):
        wins = [{"action": "SELL", "strategy": "main", "profit_loss": 2.0}] * 25
        losses = [{"action": "SELL", "strategy": "main", "profit_loss": -0.25}] * 5
        profile = self._report(list(reversed(wins + losses)))["strategies"][0]
        self.assertEqual(profile["current_losing_streak"], 5)
        self.assertEqual(profile["size_multiplier"], 0.8)

    def test_three_recent_losses_cap_multiplier_at_point_nine(self):
        wins = [{"action": "SELL", "strategy": "main", "profit_loss": 2.0}] * 27
        losses = [{"action": "SELL", "strategy": "main", "profit_loss": -0.25}] * 3
        profile = self._report(list(reversed(losses + wins)))["strategies"][0]
        self.assertEqual(profile["current_losing_streak"], 3)
        self.assertLessEqual(profile["size_multiplier"], 0.9)

    def test_non_finite_pnl_is_ignored_and_reported(self):
        rows = [{"action": "SELL", "strategy": "main", "profit_loss": float("nan")}]
        profile = self._report(rows)["strategies"][0]
        self.assertEqual(profile["closed_trades"], 0)
        self.assertEqual(profile["invalid_rows_ignored"], 1)
        self.assertEqual(profile["size_multiplier"], 1.0)

    def test_strategy_samples_are_isolated(self):
        rows = (
            [{"action": "SELL", "strategy": "main", "profit_loss": 1.0}] * 30
            + [{"action": "SELL", "strategy": "sniper", "profit_loss": -1.0}] * 5
        )
        report = self._report(rows)
        main, sniper = report["strategies"]
        self.assertEqual(main["closed_trades"], 30)
        self.assertEqual(sniper["closed_trades"], 5)
        self.assertEqual(sniper["status"], "collecting_sample")

    def test_invalid_environment_minimum_sample_falls_back_safely(self):
        with patch.dict(os.environ, {"TROJAN_ADAPTIVE_MIN_TRADES": "not-a-number"}):
            self.assertEqual(adaptive_learning._minimum_sample(), 30)


class PoolRefreshCoalescingTests(unittest.TestCase):
    def test_notifications_are_coalesced_per_pool(self):
        key = ("pool-address", "pumpswap", "mint-address")
        with onchain_stream._lock:
            onchain_stream._refreshing.discard(key)
            onchain_stream._last_refresh_at.pop(key, None)
        self.assertTrue(onchain_stream.should_refresh_pool(key, now=1000.0))
        self.assertFalse(onchain_stream.should_refresh_pool(key, now=1000.1))
        with onchain_stream._lock:
            onchain_stream._refreshing.discard(key)
        self.assertFalse(onchain_stream.should_refresh_pool(key, now=1000.3))
        self.assertTrue(onchain_stream.should_refresh_pool(key, now=1000.6))
        with onchain_stream._lock:
            onchain_stream._refreshing.discard(key)
            onchain_stream._last_refresh_at.pop(key, None)


class ExpandedStrategyConditionTests(unittest.TestCase):
    def test_candle_structure_conditions_are_valid(self):
        config = {
            "main": {"all": [
                {"field": "candle_body_pct", "op": "gt", "value": 0},
                {"field": "candle_range_pct", "op": "lt", "value": 10},
                {"field": "consecutive_green_candles", "op": "gte", "value": 2},
                {"field": "volume_spike", "op": "eq", "value": 1},
            ]}
        }
        strategy_rules.validate_rules_config(config)
        token = {
            "indicators": {
                "candle_body_pct": 1.5,
                "candle_range_pct": 3.0,
                "consecutive_green_candles": 3,
                "volume_spike": True,
            }
        }
        with patch.dict(os.environ, {"TROJAN_STRATEGY_RULES": ""}, clear=False), patch.object(strategy_rules, "get_config", return_value={"enabled": True, "valid": True, "rules": config, "error": None}):
            passed, reason = strategy_rules.evaluate_strategy_rules(token, 80, "main")
        self.assertTrue(passed, reason)


if __name__ == "__main__":
    unittest.main()
