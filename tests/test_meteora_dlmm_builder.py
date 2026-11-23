import base64
import unittest
from unittest.mock import patch

import meteora_dlmm_builder as builder
import solana_onchain as chain


def key(value):
    return chain._b58encode(bytes([value]) * 32)


class MeteoraDlmmBuilderTests(unittest.TestCase):
    def test_compiler_builds_unsigned_swap2_with_official_account_order(self):
        wallet = key(1)
        blockhash = key(2)
        accounts = [key(i) for i in range(3, 18)]
        metas = [
            {"pubkey": accounts[0], "is_signer": False, "is_writable": True},
            {"pubkey": accounts[1], "is_signer": False, "is_writable": False},
            {"pubkey": accounts[2], "is_signer": False, "is_writable": True},
            {"pubkey": accounts[3], "is_signer": False, "is_writable": True},
            {"pubkey": accounts[4], "is_signer": False, "is_writable": True},
            {"pubkey": accounts[5], "is_signer": False, "is_writable": True},
            {"pubkey": accounts[6], "is_signer": False, "is_writable": False},
            {"pubkey": accounts[7], "is_signer": False, "is_writable": False},
            {"pubkey": accounts[8], "is_signer": False, "is_writable": True},
            {"pubkey": builder.DLMM_PROGRAM_ID, "is_signer": False, "is_writable": False},
            {"pubkey": wallet, "is_signer": True, "is_writable": False},
            {"pubkey": chain.TOKEN_PROGRAM, "is_signer": False, "is_writable": False},
            {"pubkey": chain.TOKEN_PROGRAM, "is_signer": False, "is_writable": False},
            {"pubkey": builder.MEMO_PROGRAM_ID, "is_signer": False, "is_writable": False},
            {"pubkey": accounts[9], "is_signer": False, "is_writable": False},
            {"pubkey": builder.DLMM_PROGRAM_ID, "is_signer": False, "is_writable": False},
            {"pubkey": accounts[10], "is_signer": False, "is_writable": True},
            {"pubkey": accounts[11], "is_signer": False, "is_writable": True},
        ]
        data = builder.SWAP2_DISCRIMINATOR + (100).to_bytes(8, "little") + (90).to_bytes(8, "little") + bytes(4)
        raw, ordered = builder._compile_message(wallet, blockhash, metas, data)
        self.assertEqual(raw[0], 1)
        self.assertEqual(raw[1:65], bytes(64))
        self.assertEqual(ordered[0], wallet)
        self.assertIn(builder.DLMM_PROGRAM_ID, ordered)
        self.assertEqual(builder.SWAP2_DISCRIMINATOR, bytes([65, 75, 63, 76, 235, 91, 91, 136]))

    def test_builder_rejects_non_dlmm_or_unconfirmed_quote_before_rpc(self):
        with self.assertRaisesRegex(ValueError, "validated Meteora DLMM"):
            builder.build_meteora_dlmm_swap({}, key(1), key(2), key(3))
        with self.assertRaisesRegex(ValueError, "confirmed Meteora DLMM"):
            builder.build_meteora_dlmm_swap({
                "protocol": "meteora_dlmm", "status": "STALE",
            }, key(1), key(2), key(3))

    def test_simulation_rejects_invalid_base64_and_packet_size(self):
        with self.assertRaisesRegex(ValueError, "base64"):
            builder.simulate_meteora_dlmm_transaction("not-base64")
        with self.assertRaisesRegex(ValueError, "packet limits"):
            builder.simulate_meteora_dlmm_transaction(
                base64.b64encode(bytes(1300)).decode("ascii")
            )

    def test_simulation_is_unsigned_and_reports_rpc_result(self):
        transaction = base64.b64encode(bytes(100)).decode("ascii")
        with patch.object(chain, "_rpc", return_value={
            "value": {"err": {"InstructionError": [0, "Custom"]}, "logs": []}
        }):
            with self.assertRaisesRegex(ValueError, "simulation failed"):
                builder.simulate_meteora_dlmm_transaction(transaction)
        with patch.object(chain, "_rpc", return_value={
            "value": {"err": None, "logs": ["ok"], "unitsConsumed": 123}
        }) as rpc:
            result = builder.simulate_meteora_dlmm_transaction(transaction)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["units_consumed"], 123)
        self.assertEqual(result["signing"], "NOT_PERFORMED")
        self.assertEqual(result["broadcast"], "NOT_PERFORMED")
        self.assertEqual(rpc.call_args.args[0], "simulateTransaction")


if __name__ == "__main__":
    unittest.main()
