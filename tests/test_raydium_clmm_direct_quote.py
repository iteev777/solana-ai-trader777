import unittest
from unittest.mock import patch

import clmm_quote_math as mathx
import solana_onchain as chain
from raydium_clmm_quote import quote_raydium_clmm_pool


class RaydiumClmmMathTests(unittest.TestCase):
    def test_tick_zero_is_exact_q64_one(self):
        self.assertEqual(mathx.sqrt_price_at_tick(0), mathx.Q64)

    def test_known_mainnet_tick_price_is_exact(self):
        self.assertEqual(
            mathx.sqrt_price_at_tick(-22882),
            5875816817492017904,
        )
        self.assertEqual(
            mathx.sqrt_price_at_tick(-22881),
            5876110600988489675,
        )

    def test_partial_step_quotes_both_directions_with_input_fee(self):
        sqrt_price = mathx.sqrt_price_at_tick(10) + 1000
        for zero_for_one in (True, False):
            result = mathx.quote_exact_input(
                sqrt_price, 1_000_000_000, 10, [],
                zero_for_one, 10_000, 3000, 0 if zero_for_one else 60,
            )
            self.assertGreater(result["out_amount"], 0)
            self.assertGreater(result["fee_amount"], 0)
            self.assertEqual(result["tick_crossings"], 0)

    def test_crossed_tick_updates_active_liquidity(self):
        start = mathx.sqrt_price_at_tick(10)
        target = mathx.sqrt_price_at_tick(20)
        net_to_tick = mathx.amount_in_to_target(False, 1_000_000_000, start, target)
        gross = mathx.ceil_div(net_to_tick * 1_000_000, 997_000) + 1000
        result = mathx.quote_exact_input(
            start, 1_000_000_000, 10, [(20, 500)],
            False, gross, 3000, 60,
        )
        self.assertEqual(result["tick_crossings"], 1)
        self.assertEqual(result["liquidity_after"], 1_000_000_500)

    def test_quote_refuses_amount_beyond_loaded_tick_array_coverage(self):
        with self.assertRaisesRegex(ValueError, "coverage"):
            mathx.quote_exact_input(
                mathx.sqrt_price_at_tick(10), 1_000_000_000, 10, [],
                False, 10**12, 3000, 60,
            )

    def test_zero_active_liquidity_after_tick_crossing_is_refused(self):
        start = mathx.sqrt_price_at_tick(10)
        target = mathx.sqrt_price_at_tick(20)
        net_to_tick = mathx.amount_in_to_target(False, 1_000_000_000, start, target)
        gross = mathx.ceil_div(net_to_tick * 1_000_000, 997_000) + 1000
        with self.assertRaisesRegex(ValueError, "removes all active liquidity"):
            mathx.quote_exact_input(
                start, 1_000_000_000, 10, [(20, -1_000_000_000)],
                False, gross, 3000, 60,
            )


class RaydiumClmmDirectQuoteTests(unittest.TestCase):
    def setUp(self):
        self.pool = chain._b58encode(bytes([11]) * 32)
        self.config = chain._b58encode(bytes([12]) * 32)
        self.mint0 = chain._b58encode(bytes([13]) * 32)
        self.mint1 = chain._b58encode(bytes([14]) * 32)
        self.vault0 = chain._b58encode(bytes([15]) * 32)
        self.vault1 = chain._b58encode(bytes([16]) * 32)
        self.tick_array = chain._b58encode(bytes([17]) * 32)

        state = bytearray(1544)
        state[:8] = chain._discriminator("PoolState")
        state[9:41] = chain._b58decode(self.config)
        state[73:105] = chain._b58decode(self.mint0)
        state[105:137] = chain._b58decode(self.mint1)
        state[137:169] = chain._b58decode(self.vault0)
        state[169:201] = chain._b58decode(self.vault1)
        state[235:237] = (1).to_bytes(2, "little")
        state[237:253] = (1_000_000_000).to_bytes(16, "little")
        state[253:269] = (mathx.sqrt_price_at_tick(10) + 1000).to_bytes(16, "little")
        state[269:273] = (10).to_bytes(4, "little", signed=True)
        state[389] = 0
        state[390] = 0
        # Bitmap array index zero is bit 512 (word 8, bit 0).
        state[904 + 8 * 8:904 + 9 * 8] = (1).to_bytes(8, "little")
        self.state = bytes(state)

        config = bytearray(117)
        config[47:51] = (3000).to_bytes(4, "little")
        config[51:53] = (1).to_bytes(2, "little")
        self.config_data = bytes(config)

        def token_account(mint, amount):
            data = bytearray(165)
            data[:32] = chain._b58decode(mint)
            data[64:72] = amount.to_bytes(8, "little")
            return {"owner": chain.TOKEN_PROGRAM, "data": bytes(data)}

        self.vault0_account = token_account(self.mint0, 10**12)
        self.vault1_account = token_account(self.mint1, 10**12)

        mint0 = bytearray(82)
        mint0[44] = 6
        mint1 = bytearray(82)
        mint1[44] = 9
        self.mint0_account = {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint0)}
        self.mint1_account = {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint1)}

        tick = bytearray(10240)
        tick[8:40] = chain._b58decode(self.pool)
        tick[40:44] = (0).to_bytes(4, "little", signed=True)
        self.tick_account = {"owner": chain.PROGRAMS["raydium_clmm"], "data": bytes(tick)}
        self.accounts = [
            {"owner": chain.PROGRAMS["raydium_clmm"], "data": self.state},
            {"owner": chain.PROGRAMS["raydium_clmm"], "data": self.config_data},
            self.vault0_account, self.vault1_account,
            self.mint0_account, self.mint1_account,
            self.tick_account,
        ]

    def quote(self, input_mint=None, output_mint=None, amount=10_000, **kwargs):
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["raydium_clmm"], "data": self.state
            }),
            patch.object(chain, "_find_program_address", return_value=(self.tick_array, 255)),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 123456)),
        ):
            return quote_raydium_clmm_pool(
                self.pool, input_mint or self.mint1, amount,
                output_mint=output_mint or self.mint0, **kwargs
            )

    def test_confirmed_snapshot_produces_read_only_quote_in_both_directions(self):
        buy = self.quote()
        sell = self.quote(input_mint=self.mint0, output_mint=self.mint1)
        self.assertEqual(buy["protocol"], "raydium_clmm")
        self.assertEqual(buy["output_mint"], self.mint0)
        self.assertGreater(buy["out_amount"], 0)
        self.assertGreater(buy["fee_amount"], 0)
        self.assertEqual(buy["slot"], 123456)
        self.assertEqual(buy["execution"], "DISABLED")
        self.assertEqual(sell["output_mint"], self.mint1)
        self.assertGreater(sell["out_amount"], 0)

    def test_wrong_requested_output_mint_is_rejected_before_rpc(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["raydium_clmm"], "data": self.state
        }):
            with self.assertRaisesRegex(ValueError, "Requested output mint"):
                quote_raydium_clmm_pool(self.pool, self.mint1, 1000, output_mint="wrong")

    def test_non_clmm_owner_is_rejected(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["raydium_cpmm"], "data": self.state
        }):
            with self.assertRaisesRegex(ValueError, "not owned"):
                quote_raydium_clmm_pool(self.pool, self.mint1, 1000, output_mint=self.mint0)

    def test_dynamic_fee_and_fee_on_modes_are_rejected(self):
        for offset, value in ((390, 1), (1096, 1)):
            state = bytearray(self.state)
            state[offset] = value
            with patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["raydium_clmm"], "data": bytes(state)
            }):
                with self.assertRaisesRegex(ValueError, "fee"):
                    quote_raydium_clmm_pool(self.pool, self.mint1, 1000, output_mint=self.mint0)

    def test_token_2022_mints_are_rejected(self):
        accounts = list(self.accounts)
        accounts[5] = dict(accounts[5], owner=chain.TOKEN_2022_PROGRAM)
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["raydium_clmm"], "data": self.state
            }),
            patch.object(chain, "_find_program_address", return_value=(self.tick_array, 255)),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 123456)),
        ):
            with self.assertRaisesRegex(ValueError, "Token-2022"):
                quote_raydium_clmm_pool(self.pool, self.mint1, 1000, output_mint=self.mint0)


if __name__ == "__main__":
    unittest.main()
