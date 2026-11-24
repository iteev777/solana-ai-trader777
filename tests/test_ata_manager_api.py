import base64
import os
import unittest
from unittest.mock import patch

import dashboard


class AtaOrderApiTests(unittest.TestCase):
    def setUp(self):
        self.client = dashboard.app.test_client()
        self.headers = {
            "Authorization": "Basic " + base64.b64encode(b"trojan:secret").decode("ascii"),
            "X-Trojan-Action": "1",
            "Content-Type": "application/json",
        }
        self.payload = {"wallet": "wallet-public-key", "mint": "mint-public-key"}

    def test_requires_action_header(self):
        headers = dict(self.headers)
        headers.pop("X-Trojan-Action")
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}):
            response = self.client.post("/api/ata-order", json=self.payload, headers=headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("action header", response.get_json()["error"])

    def test_returns_unsigned_order(self):
        result = {
            "status": "UNSIGNED_ATA_ORDER_SIMULATED",
            "created": False,
            "signing": "EXTERNAL_WALLET_REQUIRED",
            "broadcast": "DISABLED",
        }
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch("ata_manager.create_ata_order", return_value=result) as create:
            response = self.client.post("/api/ata-order", json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["broadcast"], "DISABLED")
        create.assert_called_once_with("wallet-public-key", "mint-public-key", None)

    def test_close_order_requires_action_header(self):
        headers = dict(self.headers)
        headers.pop("X-Trojan-Action")
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}):
            response = self.client.post(
                "/api/ata-close-order",
                json={"wallet": "wallet", "token_account": "ata"},
                headers=headers,
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("action header", response.get_json()["error"])

    def test_returns_unsigned_close_order(self):
        result = {
            "status": "UNSIGNED_ATA_CLOSE_ORDER_SIMULATED",
            "closed": False,
            "signing": "EXTERNAL_WALLET_REQUIRED",
            "broadcast": "DISABLED",
        }
        payload = {"wallet": "wallet-public-key", "token_account": "ata-public-key"}
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch("ata_close_manager.close_empty_ata_order", return_value=result) as close:
            response = self.client.post("/api/ata-close-order", json=payload, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["broadcast"], "DISABLED")
        close.assert_called_once_with("wallet-public-key", "ata-public-key", None)

    def test_rejects_invalid_close_order(self):
        payload = {"wallet": "wallet-public-key", "token_account": "ata-public-key"}
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch("ata_close_manager.close_empty_ata_order", side_effect=ValueError("not empty")):
            response = self.client.post("/api/ata-close-order", json=payload, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "not empty")

    def test_rejects_invalid_order(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch("ata_manager.create_ata_order", side_effect=ValueError("invalid mint")):
            response = self.client.post("/api/ata-order", json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid mint")


if __name__ == "__main__":
    unittest.main()
