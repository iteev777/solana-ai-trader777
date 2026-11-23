import json
import sys
import time
import types
import unittest
from unittest.mock import Mock, patch

import copy_trade


class FakeWebSocket:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        if not self.responses:
            raise copy_trade.websocket.WebSocketTimeoutException("timed out")
        return json.dumps(self.responses.pop(0))


class CopyWalletSubscriptionTests(unittest.TestCase):
    def setUp(self):
        self._old_catchup = dict(copy_trade._poll_catchup)
        copy_trade._poll_catchup.clear()
        copy_trade._stop_event.clear()
        copy_trade._active_ws = None
        copy_trade._thread = None

    def tearDown(self):
        copy_trade._poll_catchup.clear()
        copy_trade._poll_catchup.update(self._old_catchup)

    def test_stop_copy_monitor_closes_socket_and_joins_worker(self):
        thread = Mock()
        thread.is_alive.return_value = False
        ws = Mock()
        copy_trade._thread = thread
        copy_trade._active_ws = ws

        self.assertTrue(copy_trade.stop_copy_monitor(timeout=0.25))
        self.assertTrue(copy_trade._stop_event.is_set())
        ws.close.assert_called_once_with()
        thread.join.assert_called_once_with(0.25)
        self.assertIsNone(copy_trade._thread)
        self.assertFalse(copy_trade._status["running"])

    def test_stop_copy_monitor_reports_worker_that_does_not_exit(self):
        thread = Mock()
        thread.is_alive.return_value = True
        copy_trade._thread = thread
        self.assertFalse(copy_trade.stop_copy_monitor(timeout=0))
        self.assertIs(copy_trade._thread, thread)

    def test_stop_copy_monitor_rejects_invalid_timeout(self):
        for value in (True, -1, float("inf"), "5"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    copy_trade.stop_copy_monitor(value)

    def test_requires_every_wallet_and_preserves_early_notifications(self):
        notification = {
            "jsonrpc": "2.0",
            "method": "logsNotification",
            "params": {"subscription": 101, "result": {"value": {"signature": "sig-1"}}},
        }
        ws = FakeWebSocket([
            {"jsonrpc": "2.0", "id": 1, "result": 101},
            notification,
            {"jsonrpc": "2.0", "id": 2, "result": 102},
        ])
        subscriptions, buffered = copy_trade._subscribe_wallets(
            ws, ["wallet-A", "wallet-B"]
        )
        self.assertEqual(subscriptions, {"wallet-A": 101, "wallet-B": 102})
        self.assertEqual(buffered, [notification])
        self.assertEqual([item["method"] for item in ws.sent],
                         ["logsSubscribe", "logsSubscribe"])

    def test_fails_closed_when_any_wallet_subscription_is_missing(self):
        ws = FakeWebSocket([
            {"jsonrpc": "2.0", "id": 1, "result": 101},
        ])
        with self.assertRaisesRegex(RuntimeError, "Incomplete Solana wallet subscriptions"):
            copy_trade._subscribe_wallets(ws, ["wallet-A", "wallet-B"])

    def test_fails_closed_on_provider_subscription_error(self):
        ws = FakeWebSocket([
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "limit"}},
        ])
        with self.assertRaisesRegex(RuntimeError, "failed=wallet-A"):
            copy_trade._subscribe_wallets(ws, ["wallet-A"])

    def test_rejects_duplicate_subscription_ids(self):
        ws = FakeWebSocket([
            {"jsonrpc": "2.0", "id": 1, "result": 101},
            {"jsonrpc": "2.0", "id": 2, "result": 101},
        ])
        with self.assertRaisesRegex(RuntimeError, "failed=wallet-B"):
            copy_trade._subscribe_wallets(ws, ["wallet-A", "wallet-B"])

    def test_rejects_invalid_subscription_ids(self):
        ws = FakeWebSocket([
            {"jsonrpc": "2.0", "id": 1, "result": True},
        ])
        with self.assertRaisesRegex(RuntimeError, "failed=wallet-A"):
            copy_trade._subscribe_wallets(ws, ["wallet-A"])



    def test_fetch_wallet_signatures_uses_bounded_recent_page(self):
        class Response:
            def raise_for_status(self):
                return None
            def json(self):
                return {"result": [
                    {"signature": "sig-1", "err": None},
                    {"signature": "", "err": None},
                    {"bad": "shape"},
                ]}
        with patch.dict("os.environ", {"TROJAN_COPY_POLL_SIGNATURE_LIMIT": "10"}), \
             patch.object(copy_trade, "_rpc_url", return_value="https://rpc.example"), \
             patch.object(copy_trade.requests, "post", return_value=Response()) as post:
            rows = copy_trade._fetch_wallet_signatures("wallet-A")
        self.assertEqual(rows, [{"signature": "sig-1", "err": None}])
        self.assertEqual(post.call_args.kwargs["json"]["method"], "getSignaturesForAddress")
        self.assertEqual(post.call_args.kwargs["json"]["params"][1]["limit"], 10)

    def test_poll_wallet_activity_processes_oldest_first_and_skips_failed_signatures(self):
        rows = [
            {"signature": "newest", "err": None},
            {"signature": "failed", "err": {"InstructionError": [0, "Custom"]}},
            {"signature": "oldest", "err": None},
        ]
        processed = []
        with patch.object(copy_trade, "_fetch_wallet_signatures", return_value=rows), \
             patch.object(copy_trade, "_process_wallet_signature",
                          side_effect=lambda wallet, signature: processed.append(signature) or True):
            result = copy_trade._poll_wallet_activity(["wallet-A"])
        self.assertEqual(processed, ["oldest", "newest"])
        self.assertEqual(result, {"processed": 2, "wallets": 1, "errors": 0})

    def test_null_transaction_result_does_not_look_like_a_completed_decode(self):
        class Response:
            def raise_for_status(self):
                return None
            def json(self):
                return {"result": None}
        with patch.object(copy_trade, "_rpc_url", return_value="https://rpc.example"), \
             patch.object(copy_trade.requests, "post", return_value=Response()):
            with self.assertRaisesRegex(RuntimeError, "not indexed yet"):
                copy_trade._fetch_wallet_transaction("sig-pending", "wallet-A")

    def test_poll_wallet_activity_isolates_wallet_rpc_failures(self):
        with patch.object(copy_trade, "_fetch_wallet_signatures", side_effect=[RuntimeError("offline"), []]):
            result = copy_trade._poll_wallet_activity(["wallet-A", "wallet-B"])
        self.assertEqual(result, {"processed": 0, "wallets": 2, "errors": 1})
        self.assertIn("wallet-A", copy_trade._status["last_poll_error"])



    def _event(self, now, block_time):
        return {
            "wallet": "wallet-A", "signature": "sig-fresh" if block_time >= now - 30 else "sig-stale",
            "action": "BUY", "token_mint": "mint-A", "token_delta": 1.0,
            "sol_delta": -0.1, "economic_sol_delta": -0.1, "quote_delta": -0.1,
            "logs": [], "invoked_programs": ["dex-program"],
            "classification_reason": "TOKEN_IN_QUOTE_OUT",
            "block_time": block_time, "received_at": now,
        }

    def test_fresh_wallet_event_can_queue_paper_copy_and_alert(self):
        now = time.time()
        event = self._event(now, now - 5)
        fake_main = types.SimpleNamespace(queue_copy_signal=Mock())
        with patch.object(copy_trade, "_fetch_wallet_transaction", return_value=event), \
             patch.object(copy_trade.database, "record_copy_trade_event", return_value=True), \
             patch.object(copy_trade, "send_alert") as alert, \
             patch.dict("os.environ", {"TROJAN_COPY_PAPER_ENABLED": "true"}), \
             patch.dict(sys.modules, {"main": fake_main}):
            self.assertTrue(copy_trade._process_wallet_signature("wallet-A", "sig-fresh"))
        fake_main.queue_copy_signal.assert_called_once_with(event)
        alert.assert_called_once()

    def test_full_paper_queue_keeps_wallet_signature_retryable(self):
        now = time.time()
        event = self._event(now, now - 5)
        fake_main = types.SimpleNamespace(queue_copy_signal=Mock(side_effect=[False, True]))
        with patch.object(copy_trade, "_fetch_wallet_transaction", return_value=event), \
             patch.object(copy_trade.database, "record_copy_trade_event", side_effect=[True, False]) as record, \
             patch.object(copy_trade, "send_alert") as alert, \
             patch.dict("os.environ", {"TROJAN_COPY_PAPER_ENABLED": "true"}), \
             patch.dict(sys.modules, {"main": fake_main}):
            with self.assertRaisesRegex(RuntimeError, "dispatch is pending"):
                copy_trade._process_wallet_signature("wallet-A", "sig-fresh")
            self.assertTrue(copy_trade._process_wallet_signature("wallet-A", "sig-fresh"))
        self.assertEqual(record.call_count, 2)
        self.assertEqual(fake_main.queue_copy_signal.call_count, 2)
        alert.assert_called_once()

    def test_stale_recovered_wallet_event_is_recorded_but_never_copied_or_alerted(self):
        now = time.time()
        event = self._event(now, now - 600)
        fake_main = types.SimpleNamespace(queue_copy_signal=unittest.mock.Mock())
        with patch.object(copy_trade, "_fetch_wallet_transaction", return_value=event), \
             patch.object(copy_trade.database, "record_copy_trade_event", return_value=True) as record, \
             patch.object(copy_trade, "send_alert") as alert, \
             patch.dict("os.environ", {"TROJAN_COPY_PAPER_ENABLED": "true"}), \
             patch.dict(sys.modules, {"main": fake_main}):
            self.assertTrue(copy_trade._process_wallet_signature("wallet-A", "sig-stale"))
        record.assert_called_once()
        fake_main.queue_copy_signal.assert_not_called()
        alert.assert_not_called()

    def test_signature_fetch_includes_before_and_until_cursor_bounds(self):
        class Response:
            def raise_for_status(self):
                return None
            def json(self):
                return {"result": [{"signature": "sig-new", "err": None}]}
        with patch.dict("os.environ", {"TROJAN_COPY_POLL_SIGNATURE_LIMIT": "25"}), \
             patch.object(copy_trade, "_rpc_url", return_value="https://rpc.example"), \
             patch.object(copy_trade.requests, "post", return_value=Response()) as post:
            rows = copy_trade._fetch_wallet_signatures(
                "wallet-A", before="sig-before", until="sig-until"
            )
        options = post.call_args.kwargs["json"]["params"][1]
        self.assertEqual(options["limit"], 25)
        self.assertEqual(options["before"], "sig-before")
        self.assertEqual(options["until"], "sig-until")
        self.assertEqual(rows[0]["signature"], "sig-new")

    def test_catchup_accumulates_pages_then_replays_oldest_first_and_commits_cursor(self):
        first_page = [
            {"signature": "newest", "err": None},
            {"signature": "middle", "err": None},
        ]
        second_page = [{"signature": "oldest", "err": None}]
        processed = []
        cursor_writes = []
        with patch.dict("os.environ", {"TROJAN_COPY_POLL_SIGNATURE_LIMIT": "2"}), \
             patch.object(copy_trade, "_COPY_MAX_PAGES_PER_POLL", 1), \
             patch.object(copy_trade.database, "get_copy_watch_cursor", return_value="old-cursor"), \
             patch.object(copy_trade.database, "set_copy_watch_cursor", side_effect=lambda wallet, sig: cursor_writes.append((wallet, sig))), \
             patch.object(copy_trade, "_fetch_wallet_signatures", side_effect=[first_page, second_page]) as fetch, \
             patch.object(copy_trade, "_process_wallet_signature", side_effect=lambda wallet, sig: processed.append(sig) or True):
            first = copy_trade._poll_wallet_activity(["wallet-A"])
            self.assertEqual(first["processed"], 0)
            self.assertEqual(processed, [])
            second = copy_trade._poll_wallet_activity(["wallet-A"])

        self.assertEqual(second, {"processed": 3, "wallets": 1, "errors": 0})
        self.assertEqual(processed, ["oldest", "middle", "newest"])
        self.assertEqual(cursor_writes, [("wallet-A", "newest")])
        self.assertEqual(fetch.call_args_list[1].kwargs, {"limit": 2, "before": "middle", "until": "old-cursor"})
        self.assertNotIn("wallet-A", copy_trade._poll_catchup)

    def test_failed_replay_does_not_advance_durable_cursor(self):
        page = [{"signature": "newest", "err": None}]
        writes = []
        with patch.dict("os.environ", {"TROJAN_COPY_POLL_SIGNATURE_LIMIT": "2"}), \
             patch.object(copy_trade.database, "get_copy_watch_cursor", return_value="old-cursor"), \
             patch.object(copy_trade.database, "set_copy_watch_cursor", side_effect=lambda wallet, sig: writes.append(sig)), \
             patch.object(copy_trade, "_fetch_wallet_signatures", return_value=page), \
             patch.object(copy_trade, "_process_wallet_signature", side_effect=RuntimeError("decode unavailable")):
            result = copy_trade._poll_wallet_activity(["wallet-A"])
        self.assertEqual(result["errors"], 1)
        self.assertEqual(writes, [])
        self.assertTrue(copy_trade._poll_catchup["wallet-A"]["complete"])


    def test_catchup_at_memory_cap_skips_to_known_head_instead_of_sticking(self):
        page = [
            {"signature": "head-newest", "err": None},
            {"signature": "head-older", "err": None},
        ]
        writes = []
        processed = []
        before_skips = copy_trade._status.get("catchup_skips", 0)
        before_count = copy_trade._status.get("skipped_signatures", 0)
        with patch.dict("os.environ", {"TROJAN_COPY_POLL_SIGNATURE_LIMIT": "2"}), \
             patch.object(copy_trade, "_COPY_MAX_PAGES_PER_POLL", 1), \
             patch.object(copy_trade, "_COPY_MAX_CATCHUP_SIGNATURES", 2), \
             patch.object(copy_trade.database, "get_copy_watch_cursor", return_value="old-cursor"), \
             patch.object(copy_trade.database, "set_copy_watch_cursor", side_effect=lambda wallet, sig: writes.append((wallet, sig))), \
             patch.object(copy_trade, "_fetch_wallet_signatures", return_value=page), \
             patch.object(copy_trade, "_process_wallet_signature", side_effect=lambda wallet, sig: processed.append(sig) or True):
            result = copy_trade._poll_wallet_activity(["wallet-A"])
        self.assertEqual(result, {"processed": 0, "wallets": 1, "errors": 0})
        self.assertEqual(writes, [("wallet-A", "head-newest")])
        self.assertEqual(processed, [])
        self.assertNotIn("wallet-A", copy_trade._poll_catchup)
        self.assertEqual(copy_trade._status["catchup_skips"], before_skips + 1)
        self.assertEqual(copy_trade._status["skipped_signatures"], before_count + 2)

    def test_catchup_skips_after_bounded_number_of_full_page_polls(self):
        pages = [
            [{"signature": "head-newest", "err": None}, {"signature": "page1-old", "err": None}],
            [{"signature": "page2-new", "err": None}, {"signature": "page2-old", "err": None}],
        ]
        writes = []
        with patch.dict("os.environ", {"TROJAN_COPY_POLL_SIGNATURE_LIMIT": "2"}), \
             patch.object(copy_trade, "_COPY_MAX_PAGES_PER_POLL", 1), \
             patch.object(copy_trade, "_COPY_MAX_CATCHUP_SIGNATURES", 10), \
             patch.object(copy_trade, "_COPY_MAX_CATCHUP_POLLS", 2), \
             patch.object(copy_trade.database, "get_copy_watch_cursor", return_value="old-cursor"), \
             patch.object(copy_trade.database, "set_copy_watch_cursor", side_effect=lambda wallet, sig: writes.append((wallet, sig))), \
             patch.object(copy_trade, "_fetch_wallet_signatures", side_effect=pages):
            first = copy_trade._poll_wallet_activity(["wallet-A"])
            self.assertEqual(first["errors"], 0)
            self.assertIn("wallet-A", copy_trade._poll_catchup)
            second = copy_trade._poll_wallet_activity(["wallet-A"])
        self.assertEqual(second["errors"], 0)
        self.assertEqual(writes, [("wallet-A", "head-newest")])
        self.assertNotIn("wallet-A", copy_trade._poll_catchup)

if __name__ == "__main__":
    unittest.main()
