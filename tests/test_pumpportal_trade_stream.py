import json
import os
import unittest
from unittest.mock import Mock, patch

import sniper_scanner


class PumpPortalTradeStreamTests(unittest.TestCase):
    def setUp(self):
        with sniper_scanner._subscription_lock:
            sniper_scanner._active_ws = None
            sniper_scanner._watched_tokens.clear()
        with sniper_scanner.seen_lock:
            sniper_scanner.seen_tokens.clear()
        sniper_scanner._listener_stop_event.clear()
        sniper_scanner.listener_started = False
        sniper_scanner._listener_thread = None
        while True:
            try:
                sniper_scanner.new_token_queue.get_nowait()
            except Exception:
                break

    def test_trade_messages_are_forwarded_not_misclassified_as_new_tokens(self):
        event = {
            "txType": "buy",
            "mint": "So11111111111111111111111111111111111111112",
            "traderPublicKey": "buyer",
            "solAmount": 0.2,
            "tokenAmount": 1000,
        }
        with patch("candidate_observer.record_trade_event") as record:
            sniper_scanner.on_message(Mock(), json.dumps(event))
        record.assert_called_once_with(event)
        self.assertTrue(sniper_scanner.new_token_queue.empty())

    def test_create_event_still_enters_the_sniper_queue(self):
        event = {
            "txType": "create",
            "mint": "So11111111111111111111111111111111111111112",
            "name": "Test token",
            "symbol": "TEST",
            "traderPublicKey": "creator",
        }
        sniper_scanner.on_message(Mock(), json.dumps(event))
        token = sniper_scanner.new_token_queue.get_nowait()
        self.assertEqual(token["mint"], event["mint"])
        self.assertEqual(token["discovery_sources"], ["pumpportal"])

    def test_reconnect_resubscribes_active_tokens_on_same_socket(self):
        ws = Mock()
        with sniper_scanner._subscription_lock:
            sniper_scanner._watched_tokens.add("TokenMint")
        with patch.object(sniper_scanner, "API_KEY", "test-api-key"), patch.dict(
            os.environ, {"TROJAN_PUMPPORTAL_OBSERVATION_ENABLED": "true"}, clear=False
        ):
            sniper_scanner.on_open(ws)
        self.assertEqual(
            [json.loads(call.args[0]) for call in ws.send.call_args_list],
            [
                {"method": "subscribeNewToken"},
                {"method": "subscribeTokenTrade", "keys": ["TokenMint"]},
            ],
        )

    def test_trade_subscription_uses_existing_single_socket(self):
        ws = Mock()
        with sniper_scanner._subscription_lock:
            sniper_scanner._active_ws = ws
        with patch.object(sniper_scanner, "API_KEY", "test-api-key"), patch.dict(
            os.environ, {"TROJAN_PUMPPORTAL_OBSERVATION_ENABLED": "true"}, clear=False
        ):
            self.assertTrue(sniper_scanner.watch_token("TokenMint"))
            self.assertTrue(sniper_scanner.unwatch_token("TokenMint"))
        self.assertEqual(
            [json.loads(call.args[0]) for call in ws.send.call_args_list],
            [
                {"method": "subscribeTokenTrade", "keys": ["TokenMint"]},
                {"method": "unsubscribeTokenTrade", "keys": ["TokenMint"]},
            ],
        )

    def test_run_listener_does_not_reconnect_after_stop(self):
        sniper_scanner._listener_stop_event.set()
        with patch.object(sniper_scanner.websocket, "WebSocketApp") as websocket_app:
            sniper_scanner.run_sniper_listener()
        websocket_app.assert_not_called()

    def test_stop_listener_closes_socket_and_joins_worker(self):
        thread = Mock()
        thread.is_alive.return_value = False
        ws = Mock()
        with sniper_scanner.listener_lock:
            sniper_scanner._listener_thread = thread
            sniper_scanner.listener_started = True
        with sniper_scanner._subscription_lock:
            sniper_scanner._active_ws = ws

        self.assertTrue(sniper_scanner.stop_listener(timeout=0.2))
        self.assertTrue(sniper_scanner._listener_stop_event.is_set())
        ws.close.assert_called_once_with()
        thread.join.assert_called_once_with(0.2)
        self.assertFalse(sniper_scanner.listener_started)
        self.assertIsNone(sniper_scanner._listener_thread)

    def test_stop_listener_reports_worker_that_ignores_shutdown(self):
        thread = Mock()
        thread.is_alive.return_value = True
        with sniper_scanner.listener_lock:
            sniper_scanner._listener_thread = thread
            sniper_scanner.listener_started = True
        self.assertFalse(sniper_scanner.stop_listener(timeout=0))
        self.assertIs(sniper_scanner._listener_thread, thread)
        self.assertTrue(sniper_scanner.listener_started)

    def test_stop_listener_rejects_invalid_timeout(self):
        for value in (True, -1, float("inf"), "5"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    sniper_scanner.stop_listener(value)

    def test_trade_subscription_is_disabled_without_api_key(self):
        ws = Mock()
        with sniper_scanner._subscription_lock:
            sniper_scanner._active_ws = ws
        with patch.object(sniper_scanner, "API_KEY", None), patch.dict(
            os.environ, {"TROJAN_PUMPPORTAL_OBSERVATION_ENABLED": "true"}, clear=False
        ):
            self.assertFalse(sniper_scanner.watch_token("TokenMint"))
        ws.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
