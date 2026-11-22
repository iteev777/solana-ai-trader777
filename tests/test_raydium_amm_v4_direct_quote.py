import unittest
from unittest.mock import patch

import solana_onchain as chain


class RaydiumAmmV4DirectQuoteTests(unittest.TestCase):
    def setUp(self):
        self.pool = "pool-v4"
        self.base_mint = chain._b58encode(bytes([1]) * 32)
        self.quote_mint = chain._b58encode(bytes([2]) * 32)
        self.base_vault = chain._b58encode(bytes([3]) * 32)
        self.quote_vault = chain._b58encode(bytes([4]) * 32)
        raw = bytearray(752)
        raw[0:8] = (1).to_bytes(8, "little")       # swap-enabled status
        raw[176:184] = (25).to_bytes(8, "little") # 0.25% fee
        raw[184:192] = (10000).to_bytes(8, "little")
        raw[192:200] = (100).to_bytes(8, "little")
        raw[200:208] = (200).to_bytes(8, "little")
        raw[224:232] = (0).to_bytes(8, "little")
        raw[336:368] = bytes([3]) * 32
        raw[368:400] = bytes([4]) * 32
        raw[400:432] = bytes([1]) * 32
        raw[432:464] = bytes([2]) * 32
        self.pool_data = bytes(raw)

        def token_account(mint, amount):
            data = bytearray(165)
            data[:32] = chain._b58decode(mint)
            data[64:72] = amount.to_bytes(8, "little")
            return {"owner": chain.TOKEN_PROGRAM, "data": bytes(data)}

        def mint_account(decimals=6):
            data = bytearray(82)
            data[44] = decimals
            return {"owner": chain.TOKEN_PROGRAM, "data": bytes(data)}

        self.accounts = [
            {"owner": chain.PROGRAMS["raydium_amm_v4"], "data": self.pool_data},
            token_account(self.base_mint, 1_000_100),
            token_account(self.quote_mint, 2_000_200),
            mint_account(6),
            mint_account(6),
        ]

    def quote(self, **kwargs):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["raydium_amm_v4"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 123)):
            return chain.quote_raydium_amm_v4_pool(
                self.pool, self.base_mint, 1000, **kwargs
            )

    def test_exact_input_quote_uses_onchain_fee_and_pnl_adjusted_reserves(self):
        result = self.quote()
        self.assertEqual(result["trade_fee_amount"], 3)
        self.assertEqual(result["out_amount"], 1992)
        self.assertEqual(result["minimum_out_amount"], 1972)
        self.assertEqual(result["slot"], 123)
        self.assertEqual(result["execution"], "DISABLED")
        self.assertEqual(result["signing"], "DISABLED")
        self.assertEqual(result["broadcast"], "DISABLED")

    def test_reverse_direction(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["raydium_amm_v4"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 123)):
            result = chain.quote_raydium_amm_v4_pool(
                self.pool, self.quote_mint, 2000
            )
        self.assertEqual(result["output_mint"], self.base_mint)
        self.assertGreater(result["out_amount"], 0)


    def test_dispatch_uses_pool_owner_for_raydium_v4(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["raydium_amm_v4"], "data": self.pool_data
        }), patch.object(chain, "quote_raydium_amm_v4_pool", return_value={"protocol": "raydium_amm_v4"}) as quote:
            result = chain.quote_direct_pool(self.pool, self.base_mint, 1000, 75)
        self.assertEqual(result["protocol"], "raydium_amm_v4")
        quote.assert_called_once_with(self.pool, self.base_mint, 1000, 75)

    def test_rejects_wrong_pool_owner(self):
        with patch.object(chain, "_account", return_value={"owner": "wrong", "data": self.pool_data}):
            with self.assertRaisesRegex(ValueError, "not owned"):
                chain.quote_raydium_amm_v4_pool(self.pool, self.base_mint, 1000)

    def test_rejects_wrong_input_mint(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["raydium_amm_v4"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 123)):
            with self.assertRaisesRegex(ValueError, "does not belong"):
                chain.quote_raydium_amm_v4_pool(self.pool, "wrong-mint", 1000)

    def test_rejects_empty_reserves_after_pnl(self):
        accounts = list(self.accounts)
        vault = bytearray(accounts[1]["data"])
        vault[64:72] = (100).to_bytes(8, "little")
        accounts[1] = {"owner": chain.TOKEN_PROGRAM, "data": bytes(vault)}
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["raydium_amm_v4"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 123)):
            with self.assertRaisesRegex(ValueError, "reserves are empty"):
                chain.quote_raydium_amm_v4_pool(self.pool, self.base_mint, 1000)

    def test_rejects_token_2022_mint(self):
        accounts = list(self.accounts)
        accounts[3] = dict(accounts[3], owner=chain.TOKEN_2022_PROGRAM)
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["raydium_amm_v4"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 123)):
            with self.assertRaisesRegex(ValueError, "Token-2022"):
                chain.quote_raydium_amm_v4_pool(self.pool, self.base_mint, 1000)


if __name__ == "__main__":
    unittest.main()
