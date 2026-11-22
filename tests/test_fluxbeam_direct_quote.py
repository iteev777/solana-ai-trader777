import unittest
from unittest.mock import patch

import solana_onchain as chain


class FluxbeamDirectQuoteTests(unittest.TestCase):
    def setUp(self):
        self.pool = "flux-pool"
        self.base_mint = chain._b58encode(bytes([11]) * 32)
        self.quote_mint = chain._b58encode(bytes([12]) * 32)
        self.base_vault = chain._b58encode(bytes([13]) * 32)
        self.quote_vault = chain._b58encode(bytes([14]) * 32)
        raw = bytearray(324)
        raw[0] = 1
        raw[1] = 1
        raw[35:67] = bytes([13]) * 32
        raw[67:99] = bytes([14]) * 32
        raw[131:163] = bytes([11]) * 32
        raw[163:195] = bytes([12]) * 32
        raw[227:235] = (20).to_bytes(8, "little")
        raw[235:243] = (10000).to_bytes(8, "little")
        raw[243:251] = (99).to_bytes(8, "little")
        raw[251:259] = (10000).to_bytes(8, "little")
        raw[291] = 0
        self.pool_data = bytes(raw)

        def token_account(mint, amount):
            data = bytearray(165)
            data[:32] = chain._b58decode(mint)
            data[64:72] = amount.to_bytes(8, "little")
            return {"owner": chain.TOKEN_PROGRAM, "data": bytes(data)}

        def mint_account(decimals=6, owner=None):
            data = bytearray(82)
            data[44] = decimals
            return {"owner": owner or chain.TOKEN_PROGRAM, "data": bytes(data)}

        self.accounts = [
            {"owner": chain.PROGRAMS["fluxbeam_amm"], "data": self.pool_data},
            token_account(self.base_mint, 1_000_000),
            token_account(self.quote_mint, 2_000_000),
            mint_account(),
            mint_account(),
        ]

    def quote(self, input_mint=None, **kwargs):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["fluxbeam_amm"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 456)):
            return chain.quote_fluxbeam_pool(
                self.pool, input_mint or self.base_mint, 10_000, **kwargs
            )

    def test_exact_input_quote_uses_both_onchain_input_fees(self):
        result = self.quote()
        self.assertEqual(result["trade_fee_amount"], 20)
        self.assertEqual(result["owner_fee_amount"], 99)
        self.assertEqual(result["total_fee_amount"], 119)
        self.assertEqual(result["out_amount"], 19568)
        self.assertEqual(result["minimum_out_amount"], 19372)
        self.assertEqual(result["slot"], 456)
        self.assertEqual(result["execution"], "DISABLED")
        self.assertEqual(result["signing"], "DISABLED")
        self.assertEqual(result["broadcast"], "DISABLED")

    def test_reverse_direction_uses_quote_vault_as_input_reserve(self):
        result = self.quote(input_mint=self.quote_mint)
        self.assertEqual(result["output_mint"], self.base_mint)
        self.assertGreater(result["out_amount"], 0)

    def test_owner_dispatch_reaches_fluxbeam_adapter(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["fluxbeam_amm"], "data": self.pool_data
        }), patch.object(chain, "quote_fluxbeam_pool", return_value={"protocol": "fluxbeam_amm"}) as quote:
            result = chain.quote_direct_pool(self.pool, self.base_mint, 1000, 75)
        self.assertEqual(result["protocol"], "fluxbeam_amm")
        quote.assert_called_once_with(self.pool, self.base_mint, 1000, 75)

    def test_rejects_wrong_pool_owner(self):
        with patch.object(chain, "_account", return_value={"owner": "wrong", "data": self.pool_data}):
            with self.assertRaisesRegex(ValueError, "not owned"):
                chain.quote_fluxbeam_pool(self.pool, self.base_mint, 1000)

    def test_rejects_non_constant_product_curve(self):
        raw = bytearray(self.pool_data)
        raw[291] = 1
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["fluxbeam_amm"], "data": bytes(raw)
        }):
            with self.assertRaisesRegex(ValueError, "ConstantProduct"):
                chain.quote_fluxbeam_pool(self.pool, self.base_mint, 1000)

    def test_rejects_token_2022_mint(self):
        accounts = list(self.accounts)
        accounts[3] = dict(accounts[3], owner=chain.TOKEN_2022_PROGRAM)
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["fluxbeam_amm"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 456)):
            with self.assertRaisesRegex(ValueError, "Token-2022"):
                chain.quote_fluxbeam_pool(self.pool, self.base_mint, 1000)

    def test_rejects_invalid_fee_denominator(self):
        raw = bytearray(self.pool_data)
        raw[235:243] = (0).to_bytes(8, "little")
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["fluxbeam_amm"], "data": bytes(raw)
        }):
            with self.assertRaisesRegex(ValueError, "fee configuration"):
                chain.quote_fluxbeam_pool(self.pool, self.base_mint, 1000)

    def test_rejects_empty_reserves(self):
        accounts = list(self.accounts)
        vault = bytearray(accounts[1]["data"])
        vault[64:72] = (0).to_bytes(8, "little")
        accounts[1] = {"owner": chain.TOKEN_PROGRAM, "data": bytes(vault)}
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["fluxbeam_amm"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 456)):
            with self.assertRaisesRegex(ValueError, "reserves are empty"):
                chain.quote_fluxbeam_pool(self.pool, self.base_mint, 1000)


if __name__ == "__main__":
    unittest.main()
