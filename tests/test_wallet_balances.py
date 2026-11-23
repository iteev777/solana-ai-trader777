import unittest
from unittest.mock import patch

import solana_onchain as chain
import wallet_balances


def key(byte):
    return chain._b58encode(bytes([byte]) * 32)


class WalletBalanceBatchTests(unittest.TestCase):
    def test_empty_input_does_not_call_rpc(self):
        with patch.object(chain, "_rpc") as rpc:
            self.assertEqual(wallet_balances.fetch_wallet_sol_balances([]), [])
            rpc.assert_not_called()

    def test_preserves_order_and_distinguishes_missing_from_unknown(self):
        addresses = [key(1), key(2), "invalid", key(3)]
        response = {
            "value": [
                {"lamports": 2_500_000_000},
                None,
                {"lamports": "bad"},
            ]
        }
        with patch.object(chain, "_rpc", return_value=response) as rpc:
            result = wallet_balances.fetch_wallet_sol_balances(addresses)
        self.assertEqual(result, [2.5, 0.0, None, None])
        rpc.assert_called_once_with(
            "getMultipleAccounts",
            [[addresses[0], addresses[1], addresses[3]],
             {"encoding": "base64", "commitment": "confirmed"}],
        )

    def test_batches_never_exceed_reference_limit(self):
        addresses = [key(i + 1) for i in range(117)]
        with patch.object(
            chain, "_rpc",
            side_effect=lambda method, params: {
                "value": [{"lamports": 1_000_000_000} for _ in params[0]]
            },
        ) as rpc:
            result = wallet_balances.fetch_wallet_sol_balances(addresses)
        self.assertEqual(result, [1.0] * len(addresses))
        sizes = [len(item.args[1][0]) for item in rpc.call_args_list]
        self.assertEqual(sizes, [50, 50, 17])

    def test_failed_batch_does_not_poison_later_batches(self):
        addresses = [key(i + 1) for i in range(51)]
        def rpc(method, params):
            if len(params[0]) == 50:
                raise RuntimeError("temporary RPC outage")
            return {"value": [{"lamports": 3_000_000_000}]}
        with patch.object(chain, "_rpc", side_effect=rpc):
            result = wallet_balances.fetch_wallet_sol_balances(addresses)
        self.assertEqual(result[:50], [None] * 50)
        self.assertEqual(result[50], 3.0)

    def test_malformed_batch_length_remains_unknown(self):
        addresses = [key(1), key(2)]
        with patch.object(chain, "_rpc", return_value={"value": [None]}):
            self.assertEqual(
                wallet_balances.fetch_wallet_sol_balances(addresses),
                [None, None],
            )

    def test_rejects_string_as_address_sequence(self):
        with self.assertRaises(TypeError):
            wallet_balances.fetch_wallet_sol_balances(key(1))


if __name__ == "__main__":
    unittest.main()
