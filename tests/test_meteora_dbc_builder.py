import base64
import unittest
from unittest.mock import patch

import meteora_dbc_builder as builder
import solana_onchain as chain


class MeteoraDbcBuilderTests(unittest.TestCase):
    def test_builder_and_simulator_entrypoints_exist(self):
        self.assertTrue(callable(builder.build_meteora_dbc_swap))
        self.assertTrue(callable(builder.simulate_meteora_dbc_transaction))

    def test_rejects_unconfirmed_or_wrong_protocol_quote(self):
        with self.assertRaisesRegex(ValueError, "Meteora DBC quote"):
            builder.build_meteora_dbc_swap({}, "wallet", "input", "output")
        quote = {"protocol": "meteora_dbc", "status": "UNCONFIRMED"}
        with self.assertRaisesRegex(ValueError, "confirmed Meteora DBC"):
            builder.build_meteora_dbc_swap(quote, "wallet", "input", "output")

    def test_unsigned_simulation_is_locked(self):
        transaction = base64.b64encode(bytes(120)).decode("ascii")
        with patch.object(chain, "_rpc", return_value={"value": {
            "err": None, "unitsConsumed": 90_000, "logs": ["meteora dbc ok"]
        }}):
            result = builder.simulate_meteora_dbc_transaction(transaction)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["signature_verification"], "DISABLED_UNSIGNED_SIMULATION")
        self.assertEqual(result["signing"], "NOT_PERFORMED")
        self.assertEqual(result["broadcast"], "DISABLED")
        self.assertEqual(result["execution"], "LOCKED")

    def test_rejects_malformed_simulation_transaction(self):
        with self.assertRaisesRegex(ValueError, "base64"):
            builder.simulate_meteora_dbc_transaction("bad base64")
        with self.assertRaisesRegex(ValueError, "packet limits"):
            builder.simulate_meteora_dbc_transaction(base64.b64encode(bytes(12)).decode())


if __name__ == "__main__":
    unittest.main()
