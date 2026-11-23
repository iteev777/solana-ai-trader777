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

    def test_json_rpc_error_is_counted_and_falls_back_even_if_result_present(self):
        failed = Mock(status_code=200)
        failed.json.return_value = {"error": {"code": -32603, "message": "hidden"}, "result": "wrong"}
        healthy = Mock(status_code=200)
        healthy.json.return_value = {"result": "ok"}
        with (
            patch.dict(os.environ, {"SOLANA_RPC_URLS": "https://a.example,https://b.example"}, clear=True),
            patch.object(rpc_transport.requests, "post", side_effect=[failed, healthy]),
        ):
            self.assertEqual(rpc_transport.rpc_call("getHealth", []), "ok")
            primary = rpc_transport.get_rpc_status()["endpoints"][0]
        self.assertEqual(primary["failures"], 1)
        self.assertEqual(primary["successes"], 0)
        self.assertNotIn("hidden", str(primary))


class AdaptiveLearningTests(unittest.TestCase):
    def test_no_sizing_change_before_minimum_sample(self):
        rows = [{"action": "SELL", "strategy": "main", "profit_loss": 1.0}] * 5
        with patch.object(adaptive_learning.database, "get_recent_trades", return_value=rows), patch.object(adaptive_learning, "_cache", {"updated_at": 0.0, "report": None}):
            profile = adaptive_learning.get_learning_report(force=True)["strategies"][0]
        self.assertEqual(profile["size_multiplier"], 1.0)
        self.assertEqual(profile["status"], "collecting_sample")

    def test_positive_and_negative_samples_are_bounded(self):
        wins = [{"action": "SELL", "strategy": "main", "profit_loss": 2.0}] * 24
        losses = [{"action": "SELL", "strategy": "main", "profit_loss": -1.0}] * 6
        with patch.object(adaptive_learning.database, "get_recent_trades", return_value=wins + losses), patch.object(adaptive_learning, "_cache", {"updated_at": 0.0, "report": None}):
            profile = adaptive_learning.get_learning_report(force=True)["strategies"][0]
        self.assertEqual(profile["status"], "learning_active")
        self.assertGreaterEqual(profile["size_multiplier"], 0.80)
        self.assertLessEqual(profile["size_multiplier"], 1.15)

    def test_missing_and_nonfinite_pnl_do_not_count_as_evidence(self):
        rows = [{"action": "SELL", "profit_loss": value} for value in
                (None, float("nan"), float("inf"), float("-inf"), True, 0)]
        profile = adaptive_learning._profile("main", rows)
        self.assertEqual(profile["closed_trades"], 1)
        self.assertEqual(profile["size_multiplier"], 1.0)

    def test_mutating_report_cannot_change_cached_sizing(self):
        rows = [{"action": "SELL", "profit_loss": 1}]
        with patch.object(adaptive_learning.database, "get_recent_trades", return_value=rows), \
             patch.object(adaptive_learning, "_cache", {"updated_at": 0.0, "report": None}):
            report = adaptive_learning.get_learning_report(force=True)
            report["strategies"][0]["size_multiplier"] = 5.0
            self.assertEqual(adaptive_learning.get_size_multiplier("main"), 1.0)


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
