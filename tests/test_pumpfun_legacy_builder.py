import base64
import unittest
from unittest.mock import patch

import pumpfun_legacy_builder as builder
import solana_onchain as chain


class PumpFunLegacyBuilderSmokeTests(unittest.TestCase):
    def test_builder_exports_registered_entrypoints(self):
        self.assertTrue(callable(builder.build_pumpfun_legacy_swap))
        self.assertTrue(callable(builder.simulate_pumpfun_legacy_transaction))

    def test_unsigned_simulation_never_signs_or_broadcasts(self):
        transaction = base64.b64encode(bytes(120)).decode("ascii")
        with patch.object(chain, "_rpc", return_value={"value": {
            "err": None, "unitsConsumed": 80_000, "logs": ["pump.fun ok"]
        }}):
            result = builder.simulate_pumpfun_legacy_transaction(transaction)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["signature_verification"], "DISABLED_UNSIGNED_SIMULATION")
        self.assertEqual(result["signing"], "NOT_PERFORMED")
        self.assertEqual(result["broadcast"], "DISABLED")
        self.assertEqual(result["execution"], "LOCKED")

    def test_rejects_malformed_unsigned_transaction(self):
        with self.assertRaisesRegex(ValueError, "base64"):
            builder.simulate_pumpfun_legacy_transaction("not base64")


if __name__ == "__main__":
    unittest.main()
