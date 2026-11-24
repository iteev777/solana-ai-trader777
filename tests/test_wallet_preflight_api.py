import base64
import os
import unittest
from unittest.mock import patch

import dashboard


class WalletPreflightApiTests(unittest.TestCase):
    def setUp(self):
        self.client = dashboard.app.test_client()
        self.headers = {
            "Authorization": "Basic " + base64.b64encode(b"trojan:secret").decode("ascii"),
            "X-Trojan-Action": "1",
            "Content-Type": "application/json",
        }
        self.payload = {
            "wallet": "wallet",
            "input_mint": "input-mint",
            "output_mint": "output-mint",
            "input_token_account": "input-account",
            "output_token_account": "output-account",
            "input_amount": "100",
        }

    def test_preflight_requires_action_header(self):
        headers = dict(self.headers)
        headers.pop("X-Trojan-Action")
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}):
            response = self.client.post("/api/wallet-preflight", json=self.payload, headers=headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("action header", response.get_json()["error"])

    def test_preflight_returns_read_only_result(self):
        result = {
            "status": "READY_FOR_NATIVE_ORDER_PREPARATION",
            "ready": True,
            "side_effects": "NONE_READ_ONLY_RPC_ONLY",
            "signing": "NOT_PERFORMED",
            "broadcast": "NOT_PERFORMED",
        }
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch("wallet_preflight.preflight_wallet_swap", return_value=result) as preflight:
            response = self.client.post("/api/wallet-preflight", json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ready"])
        self.assertEqual(response.get_json()["broadcast"], "NOT_PERFORMED")
        preflight.assert_called_once_with(
            "wallet", "input-mint", "output-mint",
            "input-account", "output-account", "100", None,
        )

    def test_preflight_rejects_invalid_request(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch("wallet_preflight.preflight_wallet_swap", side_effect=ValueError("invalid wallet")):
            response = self.client.post("/api/wallet-preflight", json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid wallet")


if __name__ == "__main__":
    unittest.main()
