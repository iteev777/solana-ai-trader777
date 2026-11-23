import base64
import unittest
from unittest.mock import patch

import ata_manager
import solana_onchain as chain


def key(byte):
    return chain._b58encode(bytes([byte]) * 32)


class AtaManagerTests(unittest.TestCase):
    def setUp(self):
        self.wallet = key(1)
        self.mint = key(2)
        self.ata = chain._find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_PROGRAM),
             chain._b58decode(self.mint)],
            ata_manager.ASSOCIATED_TOKEN_PROGRAM_ID,
        )[0]
        mint_data = bytearray(82)
        mint_data[45] = 1
        self.mint_account = {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint_data)}

    @patch("ata_manager.chain._rpc")
    @patch("ata_manager._rpc_account")
    def test_builds_simulates_and_returns_unsigned_idempotent_transaction(self, account, rpc):
        account.side_effect = [self.mint_account, None]
        rpc.side_effect = [
            {"value": 100_000_000},
            2_039_280,
            {"value": {"blockhash": key(3), "lastValidBlockHeight": 999}},
            {"value": 5_000},
            {"value": {"err": None, "unitsConsumed": 20_000, "logs": []}},
        ]
        result = ata_manager.create_ata_order(self.wallet, self.mint, 1_000_000)
        self.assertEqual(result["status"], "UNSIGNED_ATA_ORDER_SIMULATED")
        self.assertEqual(result["ata"], self.ata)
        self.assertEqual(result["instruction"], "CreateIdempotent")
        raw = base64.b64decode(result["transaction"])
        self.assertEqual(raw[0], 1)
        self.assertEqual(result["signing"], "EXTERNAL_WALLET_REQUIRED")
        self.assertEqual(result["broadcast"], "DISABLED")
        self.assertEqual(rpc.call_count, 5)

    @patch("ata_manager._rpc_account")
    def test_rejects_token_2022_mint(self, account):
        account.return_value = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(82)}
        with self.assertRaisesRegex(ValueError, "Token-2022"):
            ata_manager.create_ata_order(self.wallet, self.mint)

    @patch("ata_manager._rpc_account")
    def test_existing_canonical_ata_is_reported_without_transaction(self, account):
        raw = bytearray(165)
        raw[:32] = chain._b58decode(self.mint)
        raw[32:64] = chain._b58decode(self.wallet)
        raw[108] = 1
        account.side_effect = [
            self.mint_account,
            {"owner": chain.TOKEN_PROGRAM, "data": bytes(raw)},
        ]
        result = ata_manager.create_ata_order(self.wallet, self.mint)
        self.assertEqual(result["status"], "ATA_ALREADY_EXISTS")
        self.assertIsNone(result["transaction"])
        self.assertEqual(result["side_effects"], "READ_ONLY_RPC_ONLY")

    @patch("ata_manager.chain._rpc")
    @patch("ata_manager._rpc_account")
    def test_insufficient_wallet_sol_fails_before_simulation(self, account, rpc):
        account.side_effect = [self.mint_account, None]
        rpc.side_effect = [
            {"value": 1_000},
            2_039_280,
            {"value": {"blockhash": key(3), "lastValidBlockHeight": 999}},
            {"value": 5_000},
        ]
        with self.assertRaisesRegex(ValueError, "insufficient"):
            ata_manager.create_ata_order(self.wallet, self.mint, 1_000_000)
        self.assertEqual(rpc.call_count, 4)


if __name__ == "__main__":
    unittest.main()
