import unittest
from unittest.mock import patch

import clmm_quote_math as mathx
import solana_onchain as chain
import raydium_clmm_quote as dlmm
import raydium_clmm_builder as clmm_builder
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

    def test_known_orca_mainnet_tick_prices_are_exact(self):
        self.assertEqual(
            mathx.sqrt_price_at_tick(-22315),
            6044771392447776916,
        )
        self.assertEqual(
            mathx.sqrt_price_at_tick(-22314),
            6045073623461811934,
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

    def test_rejects_boolean_and_out_of_range_quote_inputs(self):
        with self.assertRaisesRegex(ValueError, "fee rate"):
            mathx.quote_exact_input(
                mathx.sqrt_price_at_tick(10), 1_000_000_000, 10, [],
                False, 1000, True, 60,
            )
        with self.assertRaisesRegex(ValueError, "input amount"):
            mathx.quote_exact_input(
                mathx.sqrt_price_at_tick(10), 1_000_000_000, 10, [],
                False, 1 << 64, 3000, 60,
            )
        with self.assertRaisesRegex(ValueError, "current tick"):
            mathx.quote_exact_input(
                mathx.sqrt_price_at_tick(10), 1_000_000_000, True, [],
                False, 1000, 3000, 60,
            )

    def test_rejects_duplicate_or_malformed_initialized_ticks(self):
        sqrt_price = mathx.sqrt_price_at_tick(10)
        with self.assertRaisesRegex(ValueError, "unique"):
            mathx.quote_exact_input(
                sqrt_price, 1_000_000_000, 10, [(20, 1), (20, 2)],
                False, 1000, 3000, 60,
            )
        with self.assertRaisesRegex(ValueError, "liquidity delta"):
            mathx.quote_exact_input(
                sqrt_price, 1_000_000_000, 10, [(20, True)],
                False, 1000, 3000, 60,
            )

    def test_output_is_saturated_to_instruction_u64_width(self):
        output = mathx.amount_out_for_move(
            True, 1 << 100, 1 << 100, (1 << 100) - mathx.Q64
        )
        self.assertEqual(output, mathx.U64_MAX)

    def test_rejects_liquidity_that_cannot_be_scaled_into_q64_u128(self):
        with self.assertRaisesRegex(ValueError, "Q64.64 u128"):
            mathx.amount_in_to_target(
                True, 1 << 64, mathx.U128_MAX, 1
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
        self.bitmap_extension = chain._b58encode(bytes([18]) * 32)

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

        def token_account(mint, amount, authority=None):
            data = bytearray(165)
            data[:32] = chain._b58decode(mint)
            if authority is not None:
                data[32:64] = chain._b58decode(authority)
            data[64:72] = amount.to_bytes(8, "little")
            data[108] = 1
            return {"owner": chain.TOKEN_PROGRAM, "data": bytes(data)}

        self.token_account = token_account
        self.vault0_account = token_account(self.mint0, 10**12, self.pool)
        self.vault1_account = token_account(self.mint1, 10**12, self.pool)

        mint0 = bytearray(82)
        mint0[44] = 6
        mint0[45] = 1
        mint1 = bytearray(82)
        mint1[44] = 9
        mint1[45] = 1
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
            None, self.tick_account,
        ]

    def token_account(self, mint, amount, owner):
        data = bytearray(165)
        data[:32] = chain._b58decode(mint)
        data[32:64] = chain._b58decode(owner)
        data[64:72] = amount.to_bytes(8, "little")
        data[108] = 1
        return {"owner": chain.TOKEN_PROGRAM, "data": bytes(data)}

    def _pda(self, seeds, program):
        if seeds[0] == dlmm.BITMAP_EXTENSION_SEED:
            return self.bitmap_extension, 255
        return self.tick_array, 255

    def _account(self, address):
        if address == self.pool:
            return {"owner": chain.PROGRAMS["raydium_clmm"], "data": self.state}
        if address == self.bitmap_extension:
            return None
        return None

    def quote(self, input_mint=None, output_mint=None, amount=10_000, **kwargs):
        with (
            patch.object(chain, "_account", side_effect=self._account),
            patch.object(chain, "_find_program_address", side_effect=self._pda),
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

    def test_native_builder_builds_exact_input_swap_and_checks_pool_authority(self):
        quote = self.quote()
        wallet = chain._b58encode(bytes([21]) * 32)
        user_in = chain._b58encode(bytes([22]) * 32)
        user_out = chain._b58encode(bytes([23]) * 32)
        input_account = self.token_account(self.mint1, 10**9, wallet)
        output_account = self.token_account(self.mint0, 0, wallet)
        observation = chain._b58encode(bytes([24]) * 32)
        observation_raw = bytearray(40)
        observation_raw[8:40] = chain._b58decode(self.pool)
        observation_account = {
            "owner": chain.PROGRAMS["raydium_clmm"], "data": bytes(observation_raw)
        }
        builder_accounts = [
            self.accounts[0], self.accounts[1], self.accounts[2], self.accounts[3],
            self.accounts[4], self.accounts[5], None, observation_account,
            input_account, output_account, self.tick_account,
        ]

        def pda(seeds, program):
            if seeds[0] == dlmm.BITMAP_EXTENSION_SEED:
                return self.bitmap_extension, 255
            if seeds[0] == b"observation":
                return observation, 254
            return self.tick_array, 253

        def account(address):
            if address == self.pool:
                return {"owner": chain.PROGRAMS["raydium_clmm"], "data": self.state}
            return None

        with (
            patch.object(chain, "_account", side_effect=account),
            patch.object(chain, "_find_program_address", side_effect=pda),
            patch.object(chain, "_multiple_accounts_with_context",
                         side_effect=[(self.accounts, 123456), (builder_accounts, 123456)]),
            patch.object(chain, "_rpc", return_value={"value": {
                "blockhash": chain._b58encode(bytes([25]) * 32),
                "lastValidBlockHeight": 999999,
            }}),
        ):
            result = clmm_builder.build_raydium_clmm_swap(
                quote, wallet, user_in, user_out
            )
        data = __import__("base64").b64decode(result["instruction_data_base64"])
        self.assertEqual(data[:8], clmm_builder.SWAP_V2)
        self.assertEqual(len(data), 41)
        self.assertEqual(len(result["instruction_accounts"]), 15)
        self.assertEqual(result["instruction_accounts"][5:8],
                         [self.vault1, self.vault0, observation])
        self.assertEqual(result["instruction_accounts"][13:],
                         [self.bitmap_extension, self.tick_array])
        self.assertEqual(result["execution"], "LOCKED")

    def test_native_builder_binds_token_programs_to_each_token2022_mint(self):
        quote = dict(
            self.quote(),
            token_program_input=chain.TOKEN_2022_PROGRAM,
            token_program_output=chain.TOKEN_PROGRAM,
        )
        wallet = chain._b58encode(bytes([31]) * 32)
        user_in = chain._b58encode(bytes([32]) * 32)
        user_out = chain._b58encode(bytes([33]) * 32)
        from token2022_inspector import MINT_ACCOUNT_TYPE
        fee_config = bytearray(108)
        fee_config[80:88] = (10_000).to_bytes(8, "little")
        fee_config[88:90] = (100).to_bytes(2, "little")
        fee_config[98:106] = (10_000).to_bytes(8, "little")
        fee_config[106:108] = (100).to_bytes(2, "little")
        mint1 = bytearray(166)
        mint1[44] = 6
        mint1[45] = 1
        mint1[165] = MINT_ACCOUNT_TYPE
        mint1.extend((1).to_bytes(2, "little") + (108).to_bytes(2, "little") + fee_config)
        mint1_account = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(mint1)}

        def token2022_account(mint, owner, amount, immutable_owner=False):
            raw = bytearray(182 if immutable_owner else 178)
            raw[:32] = chain._b58decode(mint)
            raw[32:64] = chain._b58decode(owner)
            raw[64:72] = amount.to_bytes(8, "little")
            raw[108] = 1
            raw[165] = 2  # Token account type
            raw[166:168] = (2).to_bytes(2, "little")  # TransferFeeAmount
            raw[168:170] = (8).to_bytes(2, "little")
            if immutable_owner:
                raw[178:180] = (7).to_bytes(2, "little")
                raw[180:182] = (0).to_bytes(2, "little")
            return {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw)}

        vault1 = token2022_account(self.mint1, self.pool, 10**12)
        input_account = token2022_account(self.mint1, wallet, 10**9, immutable_owner=True)
        output_account = self.token_account(self.mint0, 0, wallet)
        observation = chain._b58encode(bytes([34]) * 32)
        observation_raw = bytearray(40)
        observation_raw[8:40] = chain._b58decode(self.pool)
        observation_account = {
            "owner": chain.PROGRAMS["raydium_clmm"], "data": bytes(observation_raw)
        }
        builder_accounts = [
            self.accounts[0], self.accounts[1], self.accounts[2], vault1,
            self.accounts[4], mint1_account, None, observation_account,
            input_account, output_account, self.tick_account,
        ]

        def pda(seeds, program):
            if seeds[0] == dlmm.BITMAP_EXTENSION_SEED:
                return self.bitmap_extension, 255
            if seeds[0] == b"observation":
                return observation, 254
            return self.tick_array, 253

        def account(address):
            if address == self.pool:
                return {"owner": chain.PROGRAMS["raydium_clmm"], "data": self.state}
            return None

        with (
            patch.object(clmm_builder.clmm, "quote_raydium_clmm_pool", return_value=quote),
            patch.object(chain, "_account", side_effect=account),
            patch.object(chain, "_find_program_address", side_effect=pda),
            patch.object(chain, "_multiple_accounts_with_context",
                         return_value=(builder_accounts, 123456)),
            patch.object(chain, "_rpc", return_value={"value": {
                "blockhash": chain._b58encode(bytes([35]) * 32),
                "lastValidBlockHeight": 999999,
            }}),
        ):
            result = clmm_builder.build_raydium_clmm_swap(
                quote, wallet, user_in, user_out
            )
        self.assertEqual(result["token_program_input"], chain.TOKEN_2022_PROGRAM)
        self.assertEqual(result["token_program_output"], chain.TOKEN_PROGRAM)
        self.assertTrue(result["token2022_fee_aware_supported"])
        self.assertEqual(
            result["instruction_accounts"][8:10],
            [chain.TOKEN_PROGRAM, chain.TOKEN_2022_PROGRAM],
        )

    def test_bitmap_extension_decodes_positive_and_negative_array_ranges(self):
        raw = bytearray(40 + 2 * dlmm.BITMAP_EXTENSION_BLOCKS * 64)
        raw[:8] = chain._discriminator("TickArrayBitmapExtension")
        raw[8:40] = chain._b58decode(self.pool)
        positive_start = dlmm.BITMAP_EXTENSION_BASE_OFFSET
        negative_start = positive_start + dlmm.BITMAP_EXTENSION_BLOCKS * 64
        raw[positive_start:positive_start + 8] = (1).to_bytes(8, "little")
        raw[negative_start:negative_start + 8] = (1).to_bytes(8, "little")
        account = {"owner": chain.PROGRAMS["raydium_clmm"], "data": bytes(raw)}
        extension = dlmm._decode_bitmap_extension(account, self.pool, chain)
        state = dlmm._decode_state(self.state, chain)
        self.assertTrue(dlmm._array_is_initialized(state, 512, extension))
        self.assertTrue(dlmm._array_is_initialized(state, -513, extension))
        self.assertFalse(dlmm._array_is_initialized(state, 513, extension))

    def test_bitmap_extension_array_search_reaches_past_pool_bitmap(self):
        raw = bytearray(40 + 2 * dlmm.BITMAP_EXTENSION_BLOCKS * 64)
        raw[:8] = chain._discriminator("TickArrayBitmapExtension")
        raw[8:40] = chain._b58decode(self.pool)
        raw[dlmm.BITMAP_EXTENSION_BASE_OFFSET:
            dlmm.BITMAP_EXTENSION_BASE_OFFSET + 8] = (1).to_bytes(8, "little")
        extension = dlmm._decode_bitmap_extension(
            {"owner": chain.PROGRAMS["raydium_clmm"], "data": bytes(raw)},
            self.pool, chain,
        )
        state = dlmm._decode_state(self.state, chain)
        state["tick_current"] = 512 * 60 + 10
        with patch.object(chain, "_find_program_address", return_value=(self.tick_array, 255)):
            arrays = dlmm._array_starts(state, self.pool, False, chain, extension)
        self.assertEqual(arrays, [(512 * 60, self.tick_array)])

    def test_missing_bitmap_extension_refuses_uncovered_outer_array(self):
        state = dlmm._decode_state(self.state, chain)
        state["tick_current"] = 512 * 60 + 10
        with self.assertRaisesRegex(ValueError, "current tick array is not initialized"):
            dlmm._array_starts(state, self.pool, False, chain)

    def test_token2022_zero_fee_mint_is_accepted_with_matching_vault_program(self):
        accounts = list(self.accounts)
        mint = bytearray(82)
        mint[44] = 6
        mint[45] = 1
        accounts[5] = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(mint)}
        accounts[3] = dict(accounts[3], owner=chain.TOKEN_2022_PROGRAM)
        with (
            patch.object(chain, "_account", side_effect=self._account),
            patch.object(chain, "_find_program_address", side_effect=self._pda),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 123456)),
        ):
            quote = quote_raydium_clmm_pool(
                self.pool, self.mint1, 1000, output_mint=self.mint0
            )
        self.assertEqual(quote["token_program_input"], chain.TOKEN_2022_PROGRAM)
        self.assertFalse(quote["token2022_input_transfer_fee"])
        self.assertGreater(quote["out_amount"], 0)

    def test_token2022_transfer_fees_are_applied_to_both_clmm_swap_legs(self):
        from token2022_inspector import MINT_ACCOUNT_TYPE
        config = bytearray(108)
        config[80:88] = (10_000).to_bytes(8, "little")
        config[88:90] = (100).to_bytes(2, "little")
        config[98:106] = (10_000).to_bytes(8, "little")
        config[106:108] = (100).to_bytes(2, "little")
        mint = bytearray(166)
        mint[44] = 6
        mint[45] = 1
        mint[165] = MINT_ACCOUNT_TYPE
        mint.extend((1).to_bytes(2, "little") + (108).to_bytes(2, "little") + config)
        accounts = list(self.accounts)
        accounts[4] = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(mint)}
        accounts[5] = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(mint)}
        accounts[2] = dict(accounts[2], owner=chain.TOKEN_2022_PROGRAM)
        accounts[3] = dict(accounts[3], owner=chain.TOKEN_2022_PROGRAM)
        with (
            patch.object(chain, "_account", side_effect=self._account),
            patch.object(chain, "_find_program_address", side_effect=self._pda),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 123456)),
        ):
            quote = quote_raydium_clmm_pool(
                self.pool, self.mint1, 10_000, output_mint=self.mint0
            )
        self.assertTrue(quote["token2022_input_transfer_fee"])
        self.assertEqual(quote["token2022_input_received_by_pool"], 9_900)
        self.assertTrue(quote["token2022_output_transfer_fee"])
        self.assertLess(quote["out_amount"], quote["token2022_output_gross_amount"])

    def test_token2022_transfer_hook_mint_is_rejected(self):
        from token2022_inspector import MINT_ACCOUNT_TYPE
        mint = bytearray(166)
        mint[44] = 6
        mint[45] = 1
        mint[165] = MINT_ACCOUNT_TYPE
        mint.extend((14).to_bytes(2, "little") + (64).to_bytes(2, "little") + bytes(64))
        accounts = list(self.accounts)
        accounts[5] = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(mint)}
        accounts[3] = dict(accounts[3], owner=chain.TOKEN_2022_PROGRAM)
        with (
            patch.object(chain, "_account", side_effect=self._account),
            patch.object(chain, "_find_program_address", side_effect=self._pda),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 123456)),
        ):
            with self.assertRaisesRegex(ValueError, "unsupported by native swap builders"):
                quote_raydium_clmm_pool(self.pool, self.mint1, 1000, output_mint=self.mint0)


if __name__ == "__main__":
    unittest.main()
