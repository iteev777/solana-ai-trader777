import json
import unittest

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

    def test_poll_wallet_activity_isolates_wallet_rpc_failures(self):
        with patch.object(copy_trade, "_fetch_wallet_signatures", side_effect=[RuntimeError("offline"), []]):
            result = copy_trade._poll_wallet_activity(["wallet-A", "wallet-B"])
        self.assertEqual(result, {"processed": 0, "wallets": 2, "errors": 1})
        self.assertIn("wallet-A", copy_trade._status["last_poll_error"])

if __name__ == "__main__":
    unittest.main()
