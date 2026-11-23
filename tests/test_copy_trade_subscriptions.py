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


if __name__ == "__main__":
    unittest.main()
