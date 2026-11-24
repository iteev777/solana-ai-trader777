import base64
import unittest
from unittest.mock import patch

import raydium_clmm_builder as builder
import solana_onchain as chain


class RaydiumClmmBuilderTests(unittest.TestCase):
    def test_builder_and_simulator_entrypoints_are_registered(self):
        self.assertTrue(callable(builder.build_raydium_clmm_swap))
        self.assertTrue(callable(builder.simulate_raydium_clmm_transaction))

    def test_rejects_quote_from_unconfirmed_or_other_protocol(self):
        with self.assertRaisesRegex(ValueError, "Raydium CLMM quote"):
            builder.build_raydium_clmm_swap({}, "wallet", "input", "output")
        quote = {"protocol": "raydium_clmm", "status": "UNCONFIRMED"}
        with self.assertRaisesRegex(ValueError, "confirmed Raydium CLMM"):
            builder.build_raydium_clmm_swap(quote, "wallet", "input", "output")

    def test_unsigned_simulation_never_signs_or_broadcasts(self):
        transaction = base64.b64encode(bytes(120)).decode("ascii")
        with patch.object(chain, "_rpc", return_value={"value": {
            "err": None, "unitsConsumed": 110_000, "logs": ["raydium clmm ok"]
        }}):
            result = builder.simulate_raydium_clmm_transaction(transaction)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["signature_verification"], "DISABLED_UNSIGNED_SIMULATION")
        self.assertEqual(result["signing"], "NOT_PERFORMED")
        self.assertEqual(result["broadcast"], "DISABLED")
        self.assertEqual(result["execution"], "LOCKED")

    def test_rejects_invalid_simulation_payloads(self):
        with self.assertRaisesRegex(ValueError, "base64"):
            builder.simulate_raydium_clmm_transaction("not base64")
        with self.assertRaisesRegex(ValueError, "packet limits"):
            builder.simulate_raydium_clmm_transaction(base64.b64encode(bytes(10)).decode())


if __name__ == "__main__":
    unittest.main()
