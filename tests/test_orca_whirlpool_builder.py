import base64
import unittest
from unittest.mock import patch

import orca_whirlpool_builder as builder
import solana_onchain as chain


def key(value):
    return chain._b58encode(bytes([value]) * 32)


class OrcaWhirlpoolBuilderTests(unittest.TestCase):
    def test_compiler_builds_unsigned_legacy_swap_with_exact_official_account_order(self):
        wallet = key(1)
        blockhash = key(2)
        keys = [key(i) for i in range(3, 14)]
        metas = [
            {"pubkey": chain.TOKEN_PROGRAM, "is_signer": False, "is_writable": False},
            {"pubkey": wallet, "is_signer": True, "is_writable": False},
        ] + [
            {"pubkey": value, "is_signer": False, "is_writable": True}
            for value in keys[:7]
        ] + [
            {"pubkey": value, "is_signer": False, "is_writable": True}
            for value in keys[7:10]
        ] + [
            {"pubkey": keys[10], "is_signer": False, "is_writable": False}
        ]
        data = (builder.SWAP_DISCRIMINATOR + bytes(34))
        raw, ordered = builder._compile_message(wallet, blockhash, metas, data)
        self.assertEqual(raw[0], 1)
        self.assertEqual(raw[1:65], bytes(64))
        self.assertEqual(ordered[0], wallet)
        self.assertIn(builder.ORCA_PROGRAM_ID, ordered)
        self.assertEqual(builder.SWAP_DISCRIMINATOR,
                         bytes([248, 198, 158, 145, 225, 117, 135, 200]))

    def test_builder_rejects_non_orca_or_unconfirmed_quote_before_rpc(self):
        with self.assertRaisesRegex(ValueError, "validated Orca"):
            builder.build_orca_whirlpool_swap({}, key(1), key(2), key(3))
        with self.assertRaisesRegex(ValueError, "confirmed Orca"):
            builder.build_orca_whirlpool_swap({
                "protocol": "orca_whirlpool", "status": "STALE",
            }, key(1), key(2), key(3))

    def test_simulation_rejects_invalid_base64_and_packet_size(self):
        with self.assertRaisesRegex(ValueError, "base64"):
            builder.simulate_orca_whirlpool_transaction("not-base64")
        with self.assertRaisesRegex(ValueError, "packet limits"):
            builder.simulate_orca_whirlpool_transaction(
                base64.b64encode(bytes(1300)).decode("ascii")
            )

    def test_simulation_is_read_only_and_reports_rpc_error(self):
        transaction = base64.b64encode(bytes(100)).decode("ascii")
        with patch.object(chain, "_rpc", return_value={
            "value": {"err": {"InstructionError": [0, "Custom"]}, "logs": []}
        }):
            with self.assertRaisesRegex(ValueError, "simulation failed"):
                builder.simulate_orca_whirlpool_transaction(transaction)
        with patch.object(chain, "_rpc", return_value={
            "value": {"err": None, "logs": ["ok"], "unitsConsumed": 123}
        }) as rpc:
            result = builder.simulate_orca_whirlpool_transaction(transaction)
        self.assertTrue(result["success"])
        self.assertEqual(result["units_consumed"], 123)
        self.assertEqual(result["signing"], "NOT_PERFORMED")
        self.assertEqual(result["broadcast"], "NOT_PERFORMED")
        self.assertEqual(rpc.call_args.args[0], "simulateTransaction")


if __name__ == "__main__":
    unittest.main()
