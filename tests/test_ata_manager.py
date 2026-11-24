import base64
import unittest
from unittest.mock import patch

import ata_manager
import solana_onchain as chain


def key(byte):
    return chain._b58encode(bytes([byte]) * 32)


def token2022_mint_data(with_transfer_fee=False):
    if not with_transfer_fee:
        raw = bytearray(82)
        raw[44] = 6
        raw[45] = 1
        return bytes(raw)
    raw = bytearray(166)
    raw[44] = 6
    raw[45] = 1
    raw[165] = 1  # Mint account type
    config = bytearray(108)
    config[80:88] = (10_000).to_bytes(8, "little")
    config[88:90] = (100).to_bytes(2, "little")
    config[98:106] = (10_000).to_bytes(8, "little")
    config[106:108] = (100).to_bytes(2, "little")
    raw.extend((1).to_bytes(2, "little") + (108).to_bytes(2, "little") + config)
    return bytes(raw)


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
        self.wallet_account = {
            "owner": ata_manager.SYSTEM_PROGRAM_ID, "data": b"", "lamports": 100_000_000
        }
        self.mint_account = {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint_data)}

    @patch("ata_manager.chain._rpc")
    @patch("ata_manager._rpc_account")
    def test_builds_simulates_and_returns_unsigned_idempotent_transaction(self, account, rpc):
        account.side_effect = [self.wallet_account, self.mint_account, None]
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

    def test_compiled_instruction_uses_expected_program_accounts_and_discriminator(self):
        blockhash = key(3)
        message, transaction, keys = ata_manager._compile_create_message(
            self.wallet, self.ata, self.wallet, self.mint,
            chain.TOKEN_PROGRAM, blockhash,
        )
        self.assertEqual(message[:3], bytes([1, 0, 4]))
        self.assertEqual(keys[0], self.wallet)
        self.assertEqual(keys[1], self.ata)
        self.assertEqual(keys[2], self.mint)
        self.assertEqual(keys[-1], ata_manager.ASSOCIATED_TOKEN_PROGRAM_ID)
        self.assertEqual(transaction[0], 1)
        self.assertEqual(transaction[1:65], bytes(64))
        # One compiled instruction; the final instruction data byte is CreateIdempotent.
        self.assertEqual(message[-1], 1)

    @patch("ata_manager.chain._rpc")
    @patch("ata_manager._rpc_account")
    def test_builds_token2022_ata_with_immutable_owner_account_size(self, account, rpc):
        mint_data = token2022_mint_data()
        ata = chain._find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_2022_PROGRAM),
             chain._b58decode(self.mint)], ata_manager.ASSOCIATED_TOKEN_PROGRAM_ID,
        )[0]
        account.side_effect = [
            self.wallet_account,
            {"owner": chain.TOKEN_2022_PROGRAM, "data": mint_data},
            None,
        ]
        rpc.side_effect = [
            {"value": 100_000_000},
            1_513_840,
            {"value": {"blockhash": key(3), "lastValidBlockHeight": 999}},
            {"value": 5_000},
            {"value": {"err": None, "unitsConsumed": 22_000, "logs": []}},
        ]
        result = ata_manager.create_ata_order(self.wallet, self.mint, 1_000_000)
        self.assertEqual(result["ata"], ata)
        self.assertEqual(result["token_program"], chain.TOKEN_2022_PROGRAM)
        self.assertEqual(result["token_account_size"], 170)
        self.assertEqual(rpc.call_args_list[1].args[0], "getMinimumBalanceForRentExemption")
        self.assertEqual(rpc.call_args_list[1].args[1][0], 170)

    @patch("ata_manager.chain._rpc")
    @patch("ata_manager._rpc_account")
    def test_transfer_fee_mint_uses_larger_ata_size_and_rent(self, account, rpc):
        mint_data = token2022_mint_data(with_transfer_fee=True)
        account.side_effect = [
            self.wallet_account,
            {"owner": chain.TOKEN_2022_PROGRAM, "data": mint_data},
            None,
        ]
        rpc.side_effect = [
            {"value": 100_000_000},
            1_574_800,
            {"value": {"blockhash": key(3), "lastValidBlockHeight": 999}},
            {"value": 5_000},
            {"value": {"err": None, "unitsConsumed": 22_000, "logs": []}},
        ]
        result = ata_manager.create_ata_order(self.wallet, self.mint, 1_000_000)
        self.assertEqual(result["token_account_size"], 182)
        self.assertEqual(result["rent_lamports"], 1_574_800)
        self.assertEqual(rpc.call_args_list[1].args[1][0], 182)

    @patch("ata_manager._rpc_account")
    def test_token2022_existing_ata_with_immutable_owner_is_recognized(self, account):
        raw = bytearray(170)
        raw[:32] = chain._b58decode(self.mint)
        raw[32:64] = chain._b58decode(self.wallet)
        raw[108] = 1
        raw[165] = 2  # Token account type
        raw[166:168] = (7).to_bytes(2, "little")  # ImmutableOwner
        raw[168:170] = (0).to_bytes(2, "little")
        account.side_effect = [
            self.wallet_account,
            {"owner": chain.TOKEN_2022_PROGRAM, "data": token2022_mint_data()},
            {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw)},
        ]
        result = ata_manager.create_ata_order(self.wallet, self.mint)
        self.assertEqual(result["status"], "ATA_ALREADY_EXISTS")
        self.assertEqual(result["ata"], chain._find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_2022_PROGRAM),
             chain._b58decode(self.mint)], ata_manager.ASSOCIATED_TOKEN_PROGRAM_ID,
        )[0])

    @patch("ata_manager._rpc_account")
    def test_rejects_token2022_transfer_hook_mint_for_ata_order(self, account):
        raw = bytearray(166)
        raw[44] = 6
        raw[45] = 1
        raw[165] = 1
        raw.extend((14).to_bytes(2, "little") + (64).to_bytes(2, "little") + bytes(64))
        account.side_effect = [
            self.wallet_account,
            {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw)},
        ]
        with self.assertRaisesRegex(ValueError, "unsupported by native swap builders"):
            ata_manager.create_ata_order(self.wallet, self.mint)

    @patch("ata_manager._rpc_account")
    def test_existing_canonical_ata_is_reported_without_transaction(self, account):
        raw = bytearray(165)
        raw[:32] = chain._b58decode(self.mint)
        raw[32:64] = chain._b58decode(self.wallet)
        raw[108] = 1
        account.side_effect = [
            self.wallet_account,
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
        account.side_effect = [self.wallet_account, self.mint_account, None]
        rpc.side_effect = [
            {"value": 1_000},
            2_039_280,
            {"value": {"blockhash": key(3), "lastValidBlockHeight": 999}},
            {"value": 5_000},
        ]
        with self.assertRaisesRegex(ValueError, "insufficient"):
            ata_manager.create_ata_order(self.wallet, self.mint, 1_000_000)
        self.assertEqual(rpc.call_count, 4)

    @patch("ata_manager._rpc_account")
    def test_rejects_non_system_owned_wallet_before_mint_or_transaction_calls(self, account):
        account.return_value = {
            "owner": chain.TOKEN_PROGRAM, "data": bytes(165), "lamports": 100_000_000
        }
        with self.assertRaisesRegex(ValueError, "system-owned wallet"):
            ata_manager.create_ata_order(self.wallet, self.mint)
        account.assert_called_once_with(self.wallet)

    @patch("ata_manager._rpc_account")
    def test_rejects_system_wallet_with_account_data(self, account):
        account.return_value = {
            "owner": ata_manager.SYSTEM_PROGRAM_ID, "data": b"not-empty",
            "lamports": 100_000_000,
        }
        with self.assertRaisesRegex(ValueError, "no account data"):
            ata_manager.create_ata_order(self.wallet, self.mint)
        account.assert_called_once_with(self.wallet)

    @patch("ata_manager._rpc_account")
    def test_rejects_wallet_that_does_not_exist(self, account):
        account.return_value = None
        with self.assertRaisesRegex(ValueError, "Wallet account does not exist"):
            ata_manager.create_ata_order(self.wallet, self.mint)
        account.assert_called_once_with(self.wallet)

    @patch("ata_manager.chain._rpc")
    @patch("ata_manager._rpc_account")
    def test_simulation_failure_never_returns_unsigned_order(self, account, rpc):
        account.side_effect = [self.wallet_account, self.mint_account, None]
        rpc.side_effect = [
            {"value": 100_000_000},
            2_039_280,
            {"value": {"blockhash": key(3), "lastValidBlockHeight": 999}},
            {"value": 5_000},
            {"value": {"err": {"InstructionError": [0, "InsufficientFunds"]}, "logs": []}},
        ]
        with self.assertRaisesRegex(ValueError, "simulation failed"):
            ata_manager.create_ata_order(self.wallet, self.mint, 1_000_000)
        self.assertEqual(rpc.call_count, 5)

    @patch("ata_manager.chain._rpc")
    @patch("ata_manager._rpc_account")
    def test_malformed_simulation_is_rejected(self, account, rpc):
        account.side_effect = [self.wallet_account, self.mint_account, None]
        rpc.side_effect = [
            {"value": 100_000_000},
            2_039_280,
            {"value": {"blockhash": key(3), "lastValidBlockHeight": 999}},
            {"value": 5_000},
            {"value": None},
        ]
        with self.assertRaisesRegex(ValueError, "malformed ATA transaction simulation"):
            ata_manager.create_ata_order(self.wallet, self.mint, 1_000_000)


if __name__ == "__main__":
    unittest.main()
