import base64
import unittest
from unittest.mock import patch

import solana_onchain as chain
import wallet_preflight


def key(byte):
    return chain._b58encode(bytes([byte]) * 32)


def account(owner, raw, lamports=0):
    return {
        "owner": owner,
        "data": [base64.b64encode(raw).decode("ascii"), "base64"],
        "lamports": lamports,
    }


class WalletPreflightTests(unittest.TestCase):
    def setUp(self):
        self.wallet = key(1)
        self.input_mint = key(2)
        self.output_mint = key(3)
        self.input_account = wallet_preflight._associated_token_address(self.wallet, self.input_mint)
        self.output_account = wallet_preflight._associated_token_address(self.wallet, self.output_mint)

        input_raw = bytearray(165)
        input_raw[0:32] = chain._b58decode(self.input_mint)
        input_raw[32:64] = chain._b58decode(self.wallet)
        input_raw[64:72] = (500).to_bytes(8, "little")
        input_raw[108] = 1

        output_raw = bytearray(165)
        output_raw[0:32] = chain._b58decode(self.output_mint)
        output_raw[32:64] = chain._b58decode(self.wallet)
        output_raw[108] = 1

        input_mint_raw = bytearray(82)
        input_mint_raw[44] = 6
        input_mint_raw[45] = 1
        output_mint_raw = bytearray(82)
        output_mint_raw[44] = 9
        output_mint_raw[45] = 1

        self.values = [
            account(wallet_preflight.SYSTEM_PROGRAM_ID, b"", 100_000_000),
            account(chain.TOKEN_PROGRAM, bytes(input_raw)),
            account(chain.TOKEN_PROGRAM, bytes(output_raw)),
            account(chain.TOKEN_PROGRAM, bytes(input_mint_raw)),
            account(chain.TOKEN_PROGRAM, bytes(output_mint_raw)),
        ]

    def call_preflight(self, amount=100, reserve=10_000_000):
        return wallet_preflight.preflight_wallet_swap(
            self.wallet, self.input_mint, self.output_mint,
            self.input_account, self.output_account, amount, reserve,
        )

    @patch("solana_onchain._rpc")
    def test_valid_wallet_is_ready_from_one_confirmed_snapshot(self, rpc):
        rpc.return_value = {"context": {"slot": 123}, "value": self.values}
        result = self.call_preflight()
        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], "READY_FOR_NATIVE_ORDER_PREPARATION")
        self.assertEqual(result["confirmed_context_slot"], 123)
        self.assertEqual(result["input_token_account"]["raw_balance"], 500)
        self.assertEqual(result["input_mint_info"]["decimals"], 6)
        self.assertEqual(result["output_mint_info"]["decimals"], 9)
        self.assertTrue(result["input_token_account"]["is_associated_token_account"])
        self.assertTrue(result["output_token_account"]["is_associated_token_account"])
        self.assertEqual(result["expected_input_associated_token_account"], self.input_account)
        self.assertEqual(result["expected_output_associated_token_account"], self.output_account)
        self.assertEqual(result["side_effects"], "NONE_READ_ONLY_RPC_ONLY")
        self.assertEqual(result["network_fee_estimate"], "NOT_ESTIMATED")
        rpc.assert_called_once()

    @patch("solana_onchain._rpc")
    def test_valid_non_ata_token_account_is_reported_without_being_rejected(self, rpc):
        rpc.return_value = {"context": {"slot": 123}, "value": self.values}
        non_ata = key(9)
        result = wallet_preflight.preflight_wallet_swap(
            self.wallet, self.input_mint, self.output_mint,
            non_ata, self.output_account, 100, 10_000_000,
        )
        self.assertTrue(result["input_token_account"]["valid"])
        self.assertFalse(result["input_token_account"]["is_associated_token_account"])
        self.assertEqual(result["expected_input_associated_token_account"], self.input_account)

    @patch("solana_onchain._rpc")
    def test_insufficient_token_balance_blocks_preflight(self, rpc):
        rpc.return_value = {"context": {"slot": 123}, "value": self.values}
        result = self.call_preflight(amount=501)
        self.assertFalse(result["ready"])
        self.assertFalse(result["checks"]["input_balance_sufficient"])

    @patch("solana_onchain._rpc")
    def test_insufficient_sol_reserve_blocks_preflight(self, rpc):
        rpc.return_value = {"context": {"slot": 123}, "value": self.values}
        result = self.call_preflight(reserve=100_000_001)
        self.assertFalse(result["ready"])
        self.assertFalse(result["sol_reserve_sufficient"])

    @patch("solana_onchain._rpc")
    def test_missing_output_account_is_reported_without_creating_it(self, rpc):
        values = list(self.values)
        values[2] = None
        rpc.return_value = {"context": {"slot": 123}, "value": values}
        result = self.call_preflight()
        self.assertFalse(result["ready"])
        self.assertEqual(result["output_account_action"], "CREATE_OUTPUT_TOKEN_ACCOUNT_AND_RETRY")
        self.assertEqual(result["side_effects"], "NONE_READ_ONLY_RPC_ONLY")

    @patch("solana_onchain._rpc")
    def test_input_account_owned_by_another_wallet_is_rejected(self, rpc):
        values = list(self.values)
        raw = bytearray(base64.b64decode(values[1]["data"][0]))
        raw[32:64] = chain._b58decode(key(8))
        values[1] = account(chain.TOKEN_PROGRAM, bytes(raw))
        rpc.return_value = {"context": {"slot": 123}, "value": values}
        result = self.call_preflight()
        self.assertFalse(result["ready"])
        self.assertIn("not controlled", result["input_token_account"]["error"])

    def test_rejects_boolean_amount_and_invalid_public_key(self):
        with self.assertRaisesRegex(ValueError, "positive integer"):
            wallet_preflight._positive_amount(True)
        with self.assertRaisesRegex(ValueError, "public key"):
            self.call_with_bad_wallet()

    def call_with_bad_wallet(self):
        return wallet_preflight.preflight_wallet_swap(
            "not-a-key", self.input_mint, self.output_mint,
            self.input_account, self.output_account, 100,
        )

    @patch("solana_onchain._rpc")
    def test_rejects_snapshot_without_context_slot(self, rpc):
        rpc.return_value = {"value": self.values}
        with self.assertRaisesRegex(ValueError, "context slot"):
            self.call_preflight()


if __name__ == "__main__":
    unittest.main()
