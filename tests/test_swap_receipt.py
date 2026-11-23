import unittest

from swap_receipt import (
    ReceiptError,
    SwapReceipt,
    receipt_from_transaction,
)


class SwapReceiptTests(unittest.TestCase):
    def setUp(self):
        self.signature = "confirmed-signature"
        self.output_account = "destination-token-account"
        self.output_mint = "output-mint"
        self.transaction = {
            "slot": 123,
            "transaction": {
                "message": {
                    "accountKeys": [
                        "fee-payer",
                        self.output_account,
                        "pool-vault",
                    ]
                }
            },
            "meta": {
                "err": None,
                "fee": 5000,
                "preTokenBalances": [
                    {"accountIndex": 1, "uiTokenAmount": {"amount": "100"}}
                ],
                "postTokenBalances": [
                    {"accountIndex": 1, "uiTokenAmount": {"amount": "725"}},
                    {"accountIndex": 2, "uiTokenAmount": {"amount": "999999"}},
                ],
            },
        }

    def receipt(self, transaction=None, **kwargs):
        return receipt_from_transaction(
            self.signature,
            self.transaction if transaction is None else transaction,
            self.output_account,
            self.output_mint,
            600,
            confirmed=True,
            **kwargs
        )

    def test_measures_exact_destination_account_delta_by_account_index(self):
        result = self.receipt()
        self.assertEqual(result, SwapReceipt(625, True, 5000, 123))

    def test_does_not_count_pool_vault_balance_for_same_transaction(self):
        result = self.receipt()
        self.assertEqual(result.received, 625)
        self.assertNotEqual(result.received, 999999)

    def test_uses_mint_and_owner_when_account_index_is_unavailable(self):
        tx = {
            "slot": 124,
            "transaction": {"message": {"accountKeys": ["fee-payer"]}},
            "meta": {
                "err": None,
                "fee": 4000,
                "preTokenBalances": [
                    {"mint": self.output_mint, "owner": "wallet",
                     "uiTokenAmount": {"amount": "50"}}
                ],
                "postTokenBalances": [
                    {"mint": self.output_mint, "owner": "wallet",
                     "uiTokenAmount": {"amount": "700"}},
                    {"mint": self.output_mint, "owner": "pool",
                     "uiTokenAmount": {"amount": "900000"}},
                ],
            },
        }
        result = self.receipt(tx, owner="wallet")
        self.assertEqual(result, SwapReceipt(650, True, 4000, 124))

    def test_missing_metadata_is_an_estimate_not_zero(self):
        result = self.receipt({"slot": 125, "meta": None})
        self.assertEqual(result, SwapReceipt(600, False, 0, 125))

    def test_unattributable_destination_is_an_estimate_not_zero(self):
        tx = {
            "slot": 126,
            "transaction": {"message": {"accountKeys": ["fee-payer"]}},
            "meta": {"err": None, "fee": 3000,
                     "preTokenBalances": [], "postTokenBalances": []},
        }
        result = self.receipt(tx)
        self.assertEqual(result, SwapReceipt(600, False, 3000, 126))

    def test_newly_created_output_account_uses_zero_pre_balance(self):
        tx = {
            "slot": 127,
            "transaction": {"message": {"accountKeys": [self.output_account]}},
            "meta": {
                "err": None, "fee": 2000, "preTokenBalances": [],
                "postTokenBalances": [
                    {"accountIndex": 0, "uiTokenAmount": {"amount": "650"}}
                ],
            },
        }
        self.assertEqual(self.receipt(tx), SwapReceipt(650, True, 2000, 127))

    def test_grounded_output_below_minimum_is_a_hard_error(self):
        tx = self.transaction.copy()
        tx["meta"] = dict(self.transaction["meta"])
        tx["meta"]["postTokenBalances"] = [
            {"accountIndex": 1, "uiTokenAmount": {"amount": "500"}}
        ]
        with self.assertRaisesRegex(ReceiptError, "below guaranteed minimum"):
            self.receipt(tx)

    def test_failed_transaction_metadata_is_rejected(self):
        tx = {"slot": 1, "meta": {"err": {"InstructionError": [0, "Custom"]}}}
        with self.assertRaisesRegex(ReceiptError, "execution error"):
            self.receipt(tx)

    def test_confirmation_is_required(self):
        with self.assertRaisesRegex(ValueError, "successfully confirmed"):
            receipt_from_transaction(
                self.signature, self.transaction, self.output_account,
                self.output_mint, 600, confirmed=False
            )

    def test_native_sol_requires_dedicated_wsol_measurement(self):
        with self.assertRaisesRegex(ValueError, "WSOL inner-instruction"):
            self.receipt(output_is_native=True)

    def test_boolean_and_out_of_range_amounts_are_rejected(self):
        for amount in (True, 0, -1, 2**64):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    receipt_from_transaction(
                        self.signature, self.transaction, self.output_account,
                        self.output_mint, amount, confirmed=True
                    )


if __name__ == "__main__":
    unittest.main()


class NativeExecutionReceiptIntegrationTests(unittest.TestCase):
    def test_confirmed_receipt_fetches_parsed_transaction_and_returns_accounting(self):
        from unittest.mock import patch
        import native_execution

        details = {
            "slot": 900,
            "transaction": {"message": {"accountKeys": ["wallet", "out-account"]}},
            "meta": {
                "err": None,
                "fee": 5000,
                "preTokenBalances": [
                    {"accountIndex": 1, "uiTokenAmount": {"amount": "10"}}
                ],
                "postTokenBalances": [
                    {"accountIndex": 1, "uiTokenAmount": {"amount": "910"}}
                ],
            },
        }
        def rpc(method, params):
            self.assertEqual(method, "getTransaction")
            self.assertEqual(params[1]["encoding"], "jsonParsed")
            return details

        with patch.object(native_execution, "get_native_transaction_status",
                          return_value={"status": "CONFIRMED"}), \
             patch.object(native_execution.chain, "_rpc", side_effect=rpc):
            result = native_execution.get_native_swap_receipt(
                "sig", "out-account", "mint", 800
            )
        self.assertEqual(result["received_amount"], 900)
        self.assertTrue(result["received_amount_exact"])
        self.assertEqual(result["accounting"], "EXACT_ONCHAIN_TOKEN_DELTA")

    def test_receipt_integration_refuses_unconfirmed_transaction(self):
        from unittest.mock import patch
        import native_execution

        with patch.object(native_execution, "get_native_transaction_status",
                          return_value={"status": "PROCESSED"}):
            with self.assertRaisesRegex(native_execution.NativeExecutionRejected,
                                        "not yet confirmed"):
                native_execution.get_native_swap_receipt(
                    "sig", "out-account", "mint", 800
                )

    def test_receipt_integration_keeps_live_broadcast_locked(self):
        import native_execution
        self.assertTrue(native_execution.NATIVE_BROADCAST_HARD_LOCK)
        self.assertFalse(native_execution.get_native_execution_status()["broadcast_available"])
