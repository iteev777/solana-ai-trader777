import base64
import os
import unittest
from unittest.mock import patch

import native_execution as native
import raydium_cpmm_builder as builder
import solana_onchain as chain


def key(byte):
    return chain._b58encode(bytes([byte]) * 32)


class NativeExecutionTests(unittest.TestCase):
    def setUp(self):
        with native._lock:
            native._approvals.clear()
        self.wallet = key(1)
        self.program = key(2)
        self.account = key(3)
        self.blockhash = key(4)
        self.signature = bytes([7]) * 64
        unsigned, _ = builder._compile_legacy_message(
            self.wallet,
            self.blockhash,
            self.program,
            [{"pubkey": self.account, "is_writable": True}],
            b"test-instruction",
        )
        self.unsigned = base64.b64encode(unsigned).decode("ascii")
        signed = bytearray(unsigned)
        signed[1:65] = self.signature
        self.signed = base64.b64encode(bytes(signed)).decode("ascii")
        self.expected_signature = chain._b58encode(self.signature)

    def test_broadcast_requires_environment_gate(self):
        approval = native.register_native_approval(
            self.unsigned, "raydium_cpmm", 200
        )
        with patch.dict(os.environ, {"TROJAN_NATIVE_EXECUTE_ENABLED": "false"}):
            with self.assertRaises(native.NativeExecutionLocked):
                native.execute_native_signed_transaction(
                    self.signed, approval["request_id"], confirmed=True
                )

    def test_broadcast_requires_explicit_confirmation(self):
        approval = native.register_native_approval(
            self.unsigned, "raydium_cpmm", 200
        )
        with patch.dict(os.environ, {"TROJAN_NATIVE_EXECUTE_ENABLED": "true"}):
            with self.assertRaises(native.NativeExecutionLocked):
                native.execute_native_signed_transaction(
                    self.signed, approval["request_id"], confirmed=False
                )

    def test_successful_external_signature_is_simulated_then_broadcast_once(self):
        approval = native.register_native_approval(
            self.unsigned, "pumpswap", 200
        )
        def rpc(method, params):
            if method == "getBlockHeight":
                return 150
            if method == "simulateTransaction":
                self.assertTrue(params[1]["sigVerify"])
                return {"value": {"err": None, "unitsConsumed": 90000}}
            if method == "sendTransaction":
                self.assertFalse(params[1]["skipPreflight"])
                return self.expected_signature
            raise AssertionError(method)

        with patch.dict(os.environ, {"TROJAN_NATIVE_EXECUTE_ENABLED": "true"}), \
             patch.object(chain, "_rpc", side_effect=rpc) as mocked:
            result = native.execute_native_signed_transaction(
                self.signed, approval["request_id"], confirmed=True
            )
        self.assertTrue(result["submitted"])
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["status"], "SUBMITTED_NOT_CONFIRMED")
        self.assertEqual(result["protocol"], "pumpswap")
        self.assertEqual(mocked.call_count, 3)

    def test_approval_is_single_use(self):
        approval = native.register_native_approval(
            self.unsigned, "raydium_cpmm", 200
        )
        def rpc(method, params):
            if method == "getBlockHeight":
                return 150
            if method == "simulateTransaction":
                return {"value": {"err": None}}
            if method == "sendTransaction":
                return self.expected_signature
        with patch.dict(os.environ, {"TROJAN_NATIVE_EXECUTE_ENABLED": "true"}), \
             patch.object(chain, "_rpc", side_effect=rpc):
            native.execute_native_signed_transaction(
                self.signed, approval["request_id"], confirmed=True
            )
            with self.assertRaisesRegex(ValueError, "missing, expired or already consumed"):
                native.execute_native_signed_transaction(
                    self.signed, approval["request_id"], confirmed=True
                )

    def test_rejects_message_modified_after_simulation(self):
        approval = native.register_native_approval(
            self.unsigned, "raydium_cpmm", 200
        )
        changed = bytearray(base64.b64decode(self.signed))
        changed[-1] ^= 1
        with patch.dict(os.environ, {"TROJAN_NATIVE_EXECUTE_ENABLED": "true"}):
            with self.assertRaisesRegex(ValueError, "differs from the simulated approval"):
                native.execute_native_signed_transaction(
                    base64.b64encode(changed).decode("ascii"),
                    approval["request_id"],
                    confirmed=True,
                )

    def test_rejects_expired_blockhash(self):
        approval = native.register_native_approval(
            self.unsigned, "raydium_cpmm", 100
        )
        with patch.dict(os.environ, {"TROJAN_NATIVE_EXECUTE_ENABLED": "true"}), \
             patch.object(chain, "_rpc", return_value=101):
            with self.assertRaisesRegex(ValueError, "blockhash has expired"):
                native.execute_native_signed_transaction(
                    self.signed, approval["request_id"], confirmed=True
                )

    def test_signed_simulation_failure_never_broadcasts(self):
        approval = native.register_native_approval(
            self.unsigned, "pumpswap", 200
        )
        with patch.dict(os.environ, {"TROJAN_NATIVE_EXECUTE_ENABLED": "true"}), \
             patch.object(chain, "_rpc", side_effect=[
                 150, {"value": {"err": {"InstructionError": [0, "Custom"]}}}
             ]) as rpc:
            with self.assertRaisesRegex(ValueError, "simulation failed"):
                native.execute_native_signed_transaction(
                    self.signed, approval["request_id"], confirmed=True
                )
        self.assertEqual(rpc.call_count, 2)

    def test_rejects_zero_signature(self):
        approval = native.register_native_approval(
            self.unsigned, "raydium_cpmm", 200
        )
        with patch.dict(os.environ, {"TROJAN_NATIVE_EXECUTE_ENABLED": "true"}):
            with self.assertRaisesRegex(ValueError, "signature is missing"):
                native.execute_native_signed_transaction(
                    self.unsigned, approval["request_id"], confirmed=True
                )

    def test_status_reports_live_execution_locked_by_default(self):
        with patch.dict(os.environ, {"TROJAN_NATIVE_EXECUTE_ENABLED": "false"}):
            status = native.get_native_execution_status()
        self.assertFalse(status["enabled"])
        self.assertFalse(status["broadcast_available"])
        self.assertFalse(status["server_signing"])
        self.assertEqual(status["approval_ttl_seconds"], 120)


if __name__ == "__main__":
    unittest.main()
