import unittest
from unittest.mock import patch

import solana_onchain as chain


class MoonitDirectQuoteTests(unittest.TestCase):
    def setUp(self):
        self.pool = "moonit-curve"
        self.mint = chain._b58encode(bytes([31]) * 32)
        raw = bytearray(409)
        raw[:8] = bytes([8, 91, 83, 28, 132, 216, 248, 22])
        raw[8:16] = (1_000_000_000_000_000_000).to_bytes(8, "little")
        raw[16:24] = (800_000_000_000_000_000).to_bytes(8, "little")
        raw[24:56] = bytes([31]) * 32
        raw[56] = 9
        raw[57] = 0
        raw[58] = 1
        self.pool_data = bytes(raw)

        mint = bytearray(82)
        mint[44] = 9
        config = bytearray(170)
        config[168:170] = (100).to_bytes(2, "little")
        self.accounts = [
            {"owner": chain.PROGRAMS["moonit_amm"], "data": self.pool_data},
            {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint)},
            {"owner": chain.PROGRAMS["moonit_amm"], "data": bytes(config)},
        ]

    def quote(self, input_mint=None, **kwargs):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["moonit_amm"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 789)):
            return chain.quote_moonit_pool(
                self.pool, input_mint or chain.SOL_MINT, 100_000_000, **kwargs
            )

    def test_buy_quote_uses_fee_before_virtual_curve(self):
        result = self.quote()
        self.assertEqual(result["fee_bps"], 100)
        self.assertEqual(result["fee_amount"], 1_000_000)
        self.assertEqual(result["out_amount"], 2_337_643_212_039_504)
        self.assertEqual(result["minimum_out_amount"], 2_314_266_779_919_108)
        self.assertEqual(result["output_mint"], self.mint)
        self.assertEqual(result["slot"], 789)
        self.assertEqual(result["execution"], "DISABLED")
        self.assertEqual(result["signing"], "DISABLED")
        self.assertEqual(result["broadcast"], "DISABLED")

    def test_sell_quote_deducts_floor_rounded_fee_from_gross_sol_output(self):
        result = self.quote(input_mint=self.mint)
        self.assertEqual(result["output_mint"], chain.SOL_MINT)
        self.assertEqual(result["out_amount"], 41_766_732)
        self.assertEqual(result["fee_amount"], 421_886)

    def test_dispatch_uses_moonit_pool_owner(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["moonit_amm"], "data": self.pool_data
        }), patch.object(chain, "quote_moonit_pool", return_value={"protocol": "moonit_amm"}) as quote:
            result = chain.quote_direct_pool(self.pool, chain.SOL_MINT, 1000, 75)
        self.assertEqual(result["protocol"], "moonit_amm")
        quote.assert_called_once_with(self.pool, chain.SOL_MINT, 1000, 75)

    def test_rejects_wrong_pool_owner(self):
        with patch.object(chain, "_account", return_value={"owner": "wrong", "data": self.pool_data}):
            with self.assertRaisesRegex(ValueError, "not owned"):
                chain.quote_moonit_pool(self.pool, chain.SOL_MINT, 1000)

    def test_rejects_unverified_curve_type_and_collateral(self):
        raw = bytearray(self.pool_data)
        raw[58] = 0
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["moonit_amm"], "data": bytes(raw)
        }):
            with self.assertRaisesRegex(ValueError, "ConstantProductV1"):
                chain.quote_moonit_pool(self.pool, chain.SOL_MINT, 1000)
        raw = bytearray(self.pool_data)
        raw[57] = 1
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["moonit_amm"], "data": bytes(raw)
        }):
            with self.assertRaisesRegex(ValueError, "native-SOL"):
                chain.quote_moonit_pool(self.pool, chain.SOL_MINT, 1000)

    def test_rejects_token_2022_mint_and_wrong_config_owner(self):
        accounts = list(self.accounts)
        accounts[1] = dict(accounts[1], owner=chain.TOKEN_2022_PROGRAM)
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["moonit_amm"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 789)):
            with self.assertRaisesRegex(ValueError, "legacy SPL Token"):
                chain.quote_moonit_pool(self.pool, chain.SOL_MINT, 1000)
        accounts = list(self.accounts)
        accounts[2] = dict(accounts[2], owner="wrong")
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["moonit_amm"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 789)):
            with self.assertRaisesRegex(ValueError, "ConfigAccount"):
                chain.quote_moonit_pool(self.pool, chain.SOL_MINT, 1000)

    def test_rejects_invalid_fee_and_stale_snapshot(self):
        accounts = list(self.accounts)
        config = bytearray(accounts[2]["data"])
        config[168:170] = (10001).to_bytes(2, "little")
        accounts[2] = {"owner": chain.PROGRAMS["moonit_amm"], "data": bytes(config)}
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["moonit_amm"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 789)):
            with self.assertRaisesRegex(ValueError, "fee configuration"):
                chain.quote_moonit_pool(self.pool, chain.SOL_MINT, 1000)
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["moonit_amm"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 789)), \
             patch.object(chain.time, "time", side_effect=[100.0, 106.0]):
            with self.assertRaisesRegex(ValueError, "stale"):
                chain.quote_moonit_pool(self.pool, chain.SOL_MINT, 1000, max_snapshot_age=5.0)


if __name__ == "__main__":
    unittest.main()
