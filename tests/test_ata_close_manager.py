import base64
import unittest
from unittest.mock import patch

import ata_close_manager
import solana_onchain as chain


def key(byte):
    return chain._b58encode(bytes([byte]) * 32)


def token_account(wallet, mint, amount=0, close_authority=None):
    raw = bytearray(165)
    raw[:32] = chain._b58decode(mint)
    raw[32:64] = chain._b58decode(wallet)
    raw[64:72] = amount.to_bytes(8, "little")
    raw[108] = 1
    if close_authority is not None:
        raw[129:133] = (1).to_bytes(4, "little")
        raw[133:165] = chain._b58decode(close_authority)
    return bytes(raw)


class AtaCloseManagerTests(unittest.TestCase):
    def setUp(self):
        self.wallet = key(1)
        self.mint = key(2)
        self.ata = chain._find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_PROGRAM),
             chain._b58decode(self.mint)],
            ata_close_manager.ASSOCIATED_TOKEN_PROGRAM_ID,
        )[0]
        self.wallet_account = {
            "owner": ata_close_manager.SYSTEM_PROGRAM_ID, "data": b"", "lamports": 100_000_000
        }
        self.token_account = {
            "owner": chain.TOKEN_PROGRAM,
            "data": token_account(self.wallet, self.mint),
            "lamports": 2_039_280,
        }

    @patch("ata_close_manager.ata_manager._rpc_account")
    @patch("ata_close_manager.chain._rpc")
    def test_builds_unsigned_simulated_legacy_close_order(self, rpc, account):
        account.side_effect = [self.token_account, self.wallet_account]
        rpc.side_effect = [
            {"value": {"blockhash": key(3), "lastValidBlockHeight": 999}},
            {"value": 5_000},
            {"value": 100_000_000},
            {"value": {"err": None, "unitsConsumed": 20_000, "logs": []}},
        ]
        result = ata_close_manager.close_empty_ata_order(self.wallet, self.ata, 1_000_000)
        self.assertEqual(result["status"], "UNSIGNED_ATA_CLOSE_ORDER_SIMULATED")
        self.assertFalse(result["closed"])
        self.assertEqual(result["instruction"], "CloseAccount")
        self.assertEqual(result["rent_refund_lamports"], 2_039_280)
        self.assertEqual(result["signing"], "EXTERNAL_WALLET_REQUIRED")
        self.assertEqual(result["broadcast"], "DISABLED")
        raw = base64.b64decode(result["transaction"])
        self.assertEqual(raw[0], 1)
        self.assertEqual(raw[1:65], bytes(64))
        self.assertEqual(rpc.call_count, 4)

    @patch("ata_close_manager.ata_manager._rpc_account")
    def test_rejects_noncanonical_token_account(self, account):
        other = key(9)
        account.return_value = {
            "owner": chain.TOKEN_PROGRAM,
            "data": token_account(self.wallet, self.mint),
            "lamports": 2_039_280,
        }
        with self.assertRaisesRegex(ValueError, "canonical"):
            ata_close_manager.close_empty_ata_order(self.wallet, other)

    @patch("ata_close_manager.ata_manager._rpc_account")
    def test_rejects_nonempty_token_account(self, account):
        account.return_value = dict(self.token_account, data=token_account(self.wallet, self.mint, 1))
        with self.assertRaisesRegex(ValueError, "zero token balance"):
            ata_close_manager.close_empty_ata_order(self.wallet, self.ata)

    @patch("ata_close_manager.ata_manager._rpc_account")
    def test_rejects_foreign_close_authority(self, account):
        account.return_value = dict(
            self.token_account,
            data=token_account(self.wallet, self.mint, close_authority=key(8)),
        )
        with self.assertRaisesRegex(ValueError, "close authority"):
            ata_close_manager.close_empty_ata_order(self.wallet, self.ata)

    @patch("ata_close_manager.ata_manager._rpc_account")
    def test_rejects_token_account_not_owned_by_wallet(self, account):
        account.return_value = {
            "owner": chain.TOKEN_PROGRAM,
            "data": token_account(key(7), self.mint),
            "lamports": 2_039_280,
        }
        with self.assertRaisesRegex(ValueError, "not owned"):
            ata_close_manager.close_empty_ata_order(self.wallet, self.ata)

    @patch("ata_close_manager.ata_manager._rpc_account")
    @patch("ata_close_manager.chain._rpc")
    def test_rejects_simulation_without_explicit_error_field(self, rpc, account):
        account.side_effect = [self.token_account, self.wallet_account]
        rpc.side_effect = [
            {"value": {"blockhash": key(3), "lastValidBlockHeight": 999}},
            {"value": 5_000},
            {"value": 100_000_000},
            {"value": {"logs": []}},
        ]
        with self.assertRaisesRegex(ValueError, "malformed ATA close simulation"):
            ata_close_manager.close_empty_ata_order(self.wallet, self.ata)

    @patch("ata_close_manager.ata_manager._rpc_account")
    @patch("ata_close_manager.chain._rpc")
    def test_rejects_insufficient_wallet_balance_before_simulation(self, rpc, account):
        account.side_effect = [self.token_account, self.wallet_account]
        rpc.side_effect = [
            {"value": {"blockhash": key(3), "lastValidBlockHeight": 999}},
            {"value": 5_000},
            {"value": 1_000},
        ]
        with self.assertRaisesRegex(ValueError, "insufficient"):
            ata_close_manager.close_empty_ata_order(self.wallet, self.ata, 1_000_000)
        self.assertEqual(rpc.call_count, 3)

    @patch("ata_close_manager.ata_manager._rpc_account")
    @patch("ata_close_manager.chain._rpc")
    def test_rejects_token2022_ata_with_withheld_transfer_fees(self, rpc, account):
        raw = bytearray(182)
        raw[:32] = chain._b58decode(self.mint)
        raw[32:64] = chain._b58decode(self.wallet)
        raw[108] = 1
        raw[165] = token2022_inspector.TOKEN_ACCOUNT_TYPE
        raw[166:168] = (2).to_bytes(2, "little")
        raw[168:170] = (8).to_bytes(2, "little")
        raw[170:178] = (1).to_bytes(8, "little")
        raw[178:180] = (7).to_bytes(2, "little")
        raw[180:182] = (0).to_bytes(2, "little")
        ata = chain._find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_2022_PROGRAM),
             chain._b58decode(self.mint)], ata_close_manager.ASSOCIATED_TOKEN_PROGRAM_ID,
        )[0]
        account.return_value = {
            "owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw), "lamports": 1_574_800
        }
        with self.assertRaisesRegex(ValueError, "withheld transfer fees"):
            ata_close_manager.close_empty_ata_order(self.wallet, ata)

    @patch("ata_close_manager.ata_manager._rpc_account")
    @patch("ata_close_manager.chain._rpc")
    def test_token2022_empty_ata_with_zero_withheld_fees_is_closeable(self, rpc, account):
        raw = bytearray(182)
        raw[:32] = chain._b58decode(self.mint)
        raw[32:64] = chain._b58decode(self.wallet)
        raw[108] = 1
        raw[165] = 2
        raw[166:168] = (2).to_bytes(2, "little")
        raw[168:170] = (8).to_bytes(2, "little")
        raw[170:178] = (0).to_bytes(8, "little")
        raw[178:180] = (7).to_bytes(2, "little")
        raw[180:182] = (0).to_bytes(2, "little")
        ata = chain._find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_2022_PROGRAM),
             chain._b58decode(self.mint)], ata_close_manager.ASSOCIATED_TOKEN_PROGRAM_ID,
        )[0]
        account.side_effect = [
            {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw), "lamports": 1_574_800},
            self.wallet_account,
        ]
        rpc.side_effect = [
            {"value": {"blockhash": key(3), "lastValidBlockHeight": 999}},
            {"value": 5_000},
            {"value": 100_000_000},
            {"value": {"err": None, "unitsConsumed": 22_000, "logs": []}},
        ]
        result = ata_close_manager.close_empty_ata_order(self.wallet, ata, 1_000_000)
        self.assertEqual(result["token_program"], chain.TOKEN_2022_PROGRAM)
        self.assertEqual(result["rent_refund_lamports"], 1_574_800)


if __name__ == "__main__":
    unittest.main()
