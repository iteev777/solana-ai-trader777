import base64
import os
import unittest
from unittest.mock import patch

import dashboard


class NativeDashboardExecutionApiTests(unittest.TestCase):
    def setUp(self):
        self.client = dashboard.app.test_client()
        self.headers = {
            "Authorization": "Basic " + base64.b64encode(b"trojan:secret").decode("ascii"),
            "X-Trojan-Action": "1",
            "Content-Type": "application/json",
        }
        self.simulation = {
            "status": "PASS",
            "signature_verification": "DISABLED_UNSIGNED_SIMULATION",
        }
        self.order = {
            "transaction": "dGVzdA==",
            "last_valid_block_height": 123,
            "protocol": "native",
        }

    def test_raydium_order_registers_only_raydium_approval(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch("raydium_cpmm_builder.build_raydium_cpmm_swap", return_value=dict(self.order)), \
             patch("raydium_cpmm_builder.simulate_raydium_cpmm_transaction", return_value=self.simulation), \
             patch("native_execution.register_native_approval", return_value={"request_id": "ray"}) as register:
            response = self.client.post(
                "/api/raydium-cpmm-order",
                json={"quote": {}, "wallet": "wallet",
                      "user_input_token_account": "input",
                      "user_output_token_account": "output"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200)
        register.assert_called_once_with("dGVzdA==", "raydium_cpmm", 123, self.simulation)
        self.assertEqual(response.get_json()["request_id"], "ray")

    def test_pumpswap_order_registers_only_pumpswap_approval(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch("pumpswap_v2_builder.build_pumpswap_v2_swap", return_value=dict(self.order)), \
             patch("pumpswap_v2_builder.simulate_pumpswap_v2_transaction", return_value=self.simulation), \
             patch("native_execution.register_native_approval", return_value={"request_id": "pump"}) as register:
            response = self.client.post(
                "/api/pumpswap-v2-order",
                json={"quote": {}, "wallet": "wallet",
                      "user_base_token_account": "base",
                      "user_quote_token_account": "quote"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200)
        register.assert_called_once_with("dGVzdA==", "pumpswap", 123, self.simulation)
        self.assertEqual(response.get_json()["request_id"], "pump")

    def test_failed_unsigned_simulation_does_not_register_approval(self):
        failed = {"status": "REJECT", "error": "simulation failed"}
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch("raydium_cpmm_builder.build_raydium_cpmm_swap", return_value=dict(self.order)), \
             patch("raydium_cpmm_builder.simulate_raydium_cpmm_transaction", return_value=failed), \
             patch("native_execution.register_native_approval") as register:
            response = self.client.post(
                "/api/raydium-cpmm-order",
                json={"quote": {}, "wallet": "wallet",
                      "user_input_token_account": "input",
                      "user_output_token_account": "output"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("transaction", response.get_json())
        register.assert_not_called()

    def test_native_execute_requires_confirmation_header(self):
        headers = dict(self.headers)
        headers.pop("X-Trojan-Confirm-Live", None)
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}):
            response = self.client.post(
                "/api/native-execute",
                json={"request_id": "id", "signed_transaction": "dGVzdA=="},
                headers=headers,
            )
        self.assertEqual(response.status_code, 403)

    def test_native_execute_passes_explicit_confirmation_to_gate(self):
        headers = dict(self.headers, **{"X-Trojan-Confirm-Live": "1"})
        result = {"submitted": True, "confirmed": False}
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch("native_execution.execute_native_signed_transaction", return_value=result) as execute:
            response = self.client.post(
                "/api/native-execute",
                json={"request_id": "id", "signed_transaction": "dGVzdA=="},
                headers=headers,
            )
        self.assertEqual(response.status_code, 200)
        execute.assert_called_once_with("dGVzdA==", "id", confirmed=True)

    def test_native_execution_status_is_authenticated(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret",
                                    "TROJAN_NATIVE_EXECUTE_ENABLED": "false"}):
            response = self.client.get(
                "/api/native-execution-status",
                headers={"Authorization": self.headers["Authorization"]},
            )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["enabled"])


if __name__ == "__main__":
    unittest.main()
