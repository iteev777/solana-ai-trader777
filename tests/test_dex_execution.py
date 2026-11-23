import base64
import time
import unittest
from unittest.mock import patch

import dex_execution


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class DexExecutionTests(unittest.TestCase):
    input_mint = "11111111111111111111111111111111"
    output_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    taker = "11111111111111111111111111111111"

    def setUp(self):
        dex_execution._simulated_orders.clear()
        self.transaction = base64.b64encode(bytes([0]) * 120).decode()

    def _order_payload(self, **overrides):
        payload = {
            "transaction": self.transaction,
            "requestId": "req-123",
            "inAmount": "1000",
            "outAmount": "2500",
            "otherAmountThreshold": "2400",
            "router": "metis",
            "transactionVersion": 0,
            "routePlan": [{"swapInfo": {"label": "Raydium"}}],
        }
        payload.update(overrides)
        return payload

    def _create_order(self, simulate=False):
        return dex_execution.create_order(
            self.input_mint, self.output_mint, 1000, self.taker,
            simulate=simulate,
        )

    def test_order_returns_unsigned_transaction_and_route(self):
        captured = {}

        def fake_get(url, params, headers, timeout):
            captured.update({"url": url, "params": params, "headers": headers})
            return FakeResponse(self._order_payload())

        with patch.object(dex_execution.requests, "get", side_effect=fake_get):
            result = self._create_order()

        self.assertEqual(result["request_id"], "req-123")
        self.assertEqual(result["output_amount"], 2500)
        self.assertEqual(result["minimum_output_amount"], 2400)
        self.assertEqual(result["signing"], "USER_WALLET_REQUIRED")
        self.assertEqual(result["execution"], "LOCKED")
        self.assertEqual(captured["params"]["taker"], self.taker)

    def test_order_rejects_missing_transaction(self):
        with patch.object(
            dex_execution.requests, "get",
            return_value=FakeResponse({"transaction": None, "errorMessage": "No route"}),
        ):
            with self.assertRaisesRegex(ValueError, "did not build"):
                self._create_order()

    def test_order_rejects_mismatched_input_amount(self):
        with patch.object(
            dex_execution.requests, "get",
            return_value=FakeResponse(self._order_payload(inAmount="999")),
        ):
            with self.assertRaisesRegex(ValueError, "does not match"):
                self._create_order()

    def test_execute_is_locked_by_default(self):
        with patch.object(dex_execution, "EXECUTE_ENABLED", False):
            with self.assertRaisesRegex(dex_execution.ExecutionLocked, "disabled"):
                dex_execution.execute_signed_order(self.transaction, "req-123", confirmed=True)

    def test_execute_requires_explicit_confirmation(self):
        with patch.object(dex_execution, "EXECUTE_ENABLED", True):
            with self.assertRaisesRegex(dex_execution.ExecutionLocked, "confirmation"):
                dex_execution.execute_signed_order(self.transaction, "req-123", confirmed=False)

    def test_execute_posts_only_signed_transaction_and_request_id(self):
        captured = {}

        def fake_post(url, json, headers, timeout):
            captured.update({"url": url, "json": json})
            return FakeResponse({
                "status": "Success", "signature": "signature123",
                "code": 0, "outputAmountResult": "2500",
            })

        dex_execution._simulated_orders["req-123"] = {
            "simulation_status": "PASS", "created_at": time.time()
        }
        with patch.object(dex_execution, "EXECUTE_ENABLED", True), patch.object(
            dex_execution.requests, "post", side_effect=fake_post
        ):
            result = dex_execution.execute_signed_order(
                self.transaction, "req-123", confirmed=True
            )

        self.assertEqual(captured["json"], {
            "signedTransaction": self.transaction, "requestId": "req-123"
        })
        self.assertEqual(result["status"], "Success")
        self.assertTrue(result["explorer_url"].endswith("signature123"))

    def test_execution_status_reports_locked_and_non_custodial(self):
        with patch.object(dex_execution, "EXECUTE_ENABLED", False):
            status = dex_execution.get_execution_status()
        self.assertTrue(status["execution_locked"])
        self.assertFalse(status["wallet_secret_required_on_server"])
        self.assertTrue(status["simulation_required_for_execution"])

    def test_order_simulation_pass_marks_order_execution_eligible(self):
        with patch.object(
            dex_execution.requests, "get",
            return_value=FakeResponse(self._order_payload()),
        ), patch.object(
            dex_execution, "_simulate_transaction",
            return_value={"status": "PASS", "signed": False, "broadcast": False},
        ):
            result = self._create_order(simulate=True)

        self.assertEqual(result["simulation"]["status"], "PASS")
        self.assertTrue(result["execution_eligible"])
        self.assertIn("req-123", dex_execution._simulated_orders)

    def test_execute_rejects_order_without_successful_simulation(self):
        with patch.object(dex_execution, "EXECUTE_ENABLED", True):
            with self.assertRaisesRegex(
                dex_execution.ExecutionLocked, "successful unsigned simulation"
            ):
                dex_execution.execute_signed_order(
                    self.transaction, "not-simulated", confirmed=True
                )


if __name__ == "__main__":
    unittest.main()
