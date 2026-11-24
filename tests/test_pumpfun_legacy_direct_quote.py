import unittest
from unittest.mock import patch

import solana_onchain as chain


class PumpFunLegacyDirectQuoteTests(unittest.TestCase):
    def setUp(self):
        self.token_mint = chain._b58encode(bytes([11]) * 32)
        self.pool = chain._find_program_address(
            [b"bonding-curve", chain._b58decode(self.token_mint)],
            chain.PROGRAMS["pumpfun_legacy"],
        )[0]

        curve = bytearray(162)
        curve[:8] = bytes([23, 183, 248, 55, 96, 216, 172, 96])
        curve[8:16] = (1_000_000).to_bytes(8, "little")
        curve[16:24] = (2_000_000).to_bytes(8, "little")
        curve[24:32] = (500_000).to_bytes(8, "little")
        curve[32:40] = (400_000).to_bytes(8, "little")
        curve[40:48] = (1_000_000).to_bytes(8, "little")
        curve[48] = 0
        curve[49:81] = bytes([7]) * 32
        curve[81] = 0
        curve[82] = 0
        curve[83:115] = bytes(32)
        curve[115:123] = (0).to_bytes(8, "little")
        curve[124] = 0
        self.curve_data = bytes(curve)

        global_config = bytearray(162)
        global_config[:8] = chain.PUMP_GLOBAL_CONFIG_DISCRIMINATOR
        global_config[105:113] = (100).to_bytes(8, "little")
        global_config[154:162] = (30).to_bytes(8, "little")
        self.global_data = bytes(global_config)

        fee = bytearray(41)
        fee[:8] = chain.PUMPSWAP_FEE_CONFIG_DISCRIMINATOR
        fee[8] = 1
        fee[41:49] = (0).to_bytes(8, "little")
        fee[49:57] = (100).to_bytes(8, "little")
        fee[57:65] = (30).to_bytes(8, "little")
        cursor = 65
        fee[cursor:cursor + 4] = (2).to_bytes(4, "little")
        cursor += 4
        for threshold, rates in (
            (0, (0, 95, 30)),
            (3_000_000, (0, 20, 20)),
        ):
            fee[cursor:cursor + 16] = threshold.to_bytes(16, "little")
            cursor += 16
            for rate in rates:
                fee[cursor:cursor + 8] = rate.to_bytes(8, "little")
                cursor += 8
        fee[cursor:cursor + 4] = (0).to_bytes(4, "little")
        cursor += 4
        for rate in (0, 0, 0):
            fee[cursor:cursor + 8] = rate.to_bytes(8, "little")
            cursor += 8
        self.fee_data = bytes(fee)

        mint = bytearray(82)
        mint[44] = 6
        self.accounts = [
            {"owner": chain.PROGRAMS["pumpfun_legacy"], "data": self.curve_data},
            {"owner": chain.PROGRAMS["pumpfun_legacy"], "data": self.global_data},
            {"owner": chain.PUMP_FEES_PROGRAM, "data": self.fee_data},
            {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint)},
        ]

    def _token2022_mint(self):
        from token2022_inspector import MINT_ACCOUNT_TYPE
        raw = bytearray(166)
        raw[44] = 6
        raw[45] = 1
        raw[165] = MINT_ACCOUNT_TYPE
        config = bytearray(108)
        config[80:88] = (10_000).to_bytes(8, "little")
        config[88:90] = (100).to_bytes(2, "little")
        config[98:106] = (10_000).to_bytes(8, "little")
        config[106:108] = (100).to_bytes(2, "little")
        raw.extend((1).to_bytes(2, "little") + (108).to_bytes(2, "little") + config)
        return {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw)}

    def quote(self, input_mint=None, output_mint=None, amount=10_000, **kwargs):
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpfun_legacy"], "data": self.curve_data
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 654)),
        ):
            return chain.quote_pumpfun_legacy_pool(
                self.pool,
                input_mint or chain.SOL_MINT,
                output_mint or self.token_mint,
                amount,
                **kwargs,
            )

    def test_sol_in_buy_uses_market_cap_tier_and_exact_spend_budget(self):
        result = self.quote()
        self.assertEqual(result["fee_rates_bps"], [0, 95, 30])
        self.assertEqual(result["fee_policy"], "ONCHAIN_MARKET_CAP_FEE_TIER")
        self.assertEqual(result["market_cap_lamports"], 2_000_000)
        self.assertEqual(result["total_fee_amount"], 124)
        self.assertEqual(result["out_amount"], 4913)
        self.assertEqual(result["minimum_out_amount"], 4863)
        self.assertEqual(result["fee_mint"], chain.SOL_MINT)
        self.assertEqual(result["slot"], 654)
        self.assertEqual(result["execution"], "DISABLED")
        self.assertEqual(result["signing"], "DISABLED")
        self.assertEqual(result["broadcast"], "DISABLED")

    def test_token_in_sell_charges_quote_output_fees(self):
        result = self.quote(input_mint=self.token_mint, output_mint=chain.SOL_MINT)
        self.assertEqual(result["output_amount_gross"], 19_801)
        self.assertEqual(result["total_fee_amount"], 249)
        self.assertEqual(result["out_amount"], 19_552)
        self.assertEqual(result["minimum_out_amount"], 19_356)

    def test_market_cap_selects_later_fee_tier(self):
        curve = bytearray(self.curve_data)
        curve[16:24] = (4_000_000).to_bytes(8, "little")
        accounts = list(self.accounts)
        accounts[0] = dict(accounts[0], data=bytes(curve))
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpfun_legacy"], "data": bytes(curve)
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 654)),
        ):
            result = chain.quote_pumpfun_legacy_pool(
                self.pool, chain.SOL_MINT, self.token_mint, 10_000
            )
        self.assertEqual(result["market_cap_lamports"], 4_000_000)
        self.assertEqual(result["fee_rates_bps"], [0, 20, 20])

    def test_dispatch_uses_pool_owner_and_requested_output_mint(self):
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpfun_legacy"], "data": self.curve_data
            }),
            patch.object(chain, "quote_pumpfun_legacy_pool", return_value={
                "protocol": "pumpfun_legacy"
            }) as quote,
        ):
            result = chain.quote_direct_pool(
                self.pool, chain.SOL_MINT, 1000, 75, output_mint=self.token_mint
            )
        self.assertEqual(result["protocol"], "pumpfun_legacy")
        quote.assert_called_once_with(
            self.pool, chain.SOL_MINT, self.token_mint, 1000, 75
        )

    def test_decoder_exposes_extended_curve_flags_and_quote_mint(self):
        decoded = chain._decode_pool("pumpfun_legacy", self.curve_data)
        self.assertEqual(decoded["quote_mint"], chain.DEFAULT_PUBKEY)
        self.assertFalse(decoded["is_mayhem_mode"])
        self.assertFalse(decoded["is_cashback_coin"])
        self.assertFalse(decoded["is_holder_reward"])

    def test_rejects_non_sol_quote_mint(self):
        curve = bytearray(self.curve_data)
        curve[83:115] = bytes([9]) * 32
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpfun_legacy"], "data": bytes(curve)
        }):
            with self.assertRaisesRegex(ValueError, "token-paired"):
                chain.quote_pumpfun_legacy_pool(
                    self.pool, chain.SOL_MINT, self.token_mint, 1000
                )

    def test_rejects_mayhem_mode_until_its_fee_schedule_is_verified(self):
        curve = bytearray(self.curve_data)
        curve[81] = 1
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpfun_legacy"], "data": bytes(curve)
        }):
            with self.assertRaisesRegex(ValueError, "mayhem-mode"):
                chain.quote_pumpfun_legacy_pool(
                    self.pool, chain.SOL_MINT, self.token_mint, 1000
                )

    def test_rejects_cashback_and_holder_reward_modes(self):
        for offset in (82, 124):
            curve = bytearray(self.curve_data)
            curve[offset] = 1
            with patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpfun_legacy"], "data": bytes(curve)
            }):
                with self.assertRaisesRegex(ValueError, "not supported|separate fee model"):
                    chain.quote_pumpfun_legacy_pool(
                        self.pool, chain.SOL_MINT, self.token_mint, 1000
                    )

    def test_token2022_transfer_fee_is_applied_to_buy_output_and_sell_input(self):
        accounts = list(self.accounts)
        accounts[3] = self._token2022_mint()
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpfun_legacy"], "data": self.curve_data
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 654)),
        ):
            buy = chain.quote_pumpfun_legacy_pool(
                self.pool, chain.SOL_MINT, self.token_mint, 10_000
            )
            sell = chain.quote_pumpfun_legacy_pool(
                self.pool, self.token_mint, chain.SOL_MINT, 10_000
            )
        self.assertTrue(buy["token2022_output_transfer_fee"])
        self.assertLess(buy["out_amount"], buy["output_amount_gross"])
        self.assertTrue(sell["token2022_input_transfer_fee"])
        self.assertEqual(sell["token2022_input_received_by_curve"], 9_900)
        self.assertEqual(buy["token_program"], chain.TOKEN_2022_PROGRAM)

    def test_token2022_transfer_hook_mint_is_rejected(self):
        from token2022_inspector import MINT_ACCOUNT_TYPE
        raw = bytearray(166)
        raw[44] = 6
        raw[45] = 1
        raw[165] = MINT_ACCOUNT_TYPE
        raw.extend((14).to_bytes(2, "little") + (64).to_bytes(2, "little") + bytes(64))
        accounts = list(self.accounts)
        accounts[3] = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw)}
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpfun_legacy"], "data": self.curve_data
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 654)),
        ):
            with self.assertRaisesRegex(ValueError, "unsupported by native swap builders"):
                chain.quote_pumpfun_legacy_pool(
                    self.pool, chain.SOL_MINT, self.token_mint, 1000
                )

    def test_rejects_buy_that_requires_synthetic_migration(self):
        curve = bytearray(self.curve_data)
        curve[24:32] = (100).to_bytes(8, "little")
        accounts = list(self.accounts)
        accounts[0] = dict(accounts[0], data=bytes(curve))
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpfun_legacy"], "data": bytes(curve)
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 654)),
        ):
            with self.assertRaisesRegex(ValueError, "complete the bonding curve"):
                chain.quote_pumpfun_legacy_pool(
                    self.pool, chain.SOL_MINT, self.token_mint, 100_000
                )

    def test_rejects_sell_when_real_sol_reserve_is_insufficient(self):
        curve = bytearray(self.curve_data)
        curve[32:40] = (1).to_bytes(8, "little")
        accounts = list(self.accounts)
        accounts[0] = dict(accounts[0], data=bytes(curve))
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpfun_legacy"], "data": bytes(curve)
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 654)),
        ):
            with self.assertRaisesRegex(ValueError, "insufficient real SOL"):
                chain.quote_pumpfun_legacy_pool(
                    self.pool, self.token_mint, chain.SOL_MINT, 10_000
                )

    def test_rejects_wrong_input_output_pair(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpfun_legacy"], "data": self.curve_data
        }):
            with self.assertRaisesRegex(ValueError, "only SOL/token"):
                chain.quote_pumpfun_legacy_pool(
                    self.pool, "other", "another", 1000
                )

    def test_rejects_stale_curve_snapshot(self):
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpfun_legacy"], "data": self.curve_data
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 654)),
            patch.object(chain.time, "time", side_effect=[100.0, 110.0]),
        ):
            with self.assertRaisesRegex(ValueError, "snapshot is stale"):
                chain.quote_pumpfun_legacy_pool(
                    self.pool, chain.SOL_MINT, self.token_mint, 1000, max_snapshot_age=2.0
                )



    def test_pump_global_config_pda_matches_known_program_address(self):
        address, _ = chain._find_program_address(
            [b"global"], chain.PROGRAMS["pumpfun_legacy"]
        )
        self.assertEqual(address, chain.PUMP_GLOBAL_CONFIG)

    def test_curve_pda_must_match_the_requested_token_mint(self):
        wrong_pool = chain._b58encode(bytes([99]) * 32)
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpfun_legacy"], "data": self.curve_data
        }):
            with self.assertRaisesRegex(ValueError, "PDA does not match"):
                chain.quote_pumpfun_legacy_pool(
                    wrong_pool, chain.SOL_MINT, self.token_mint, 1000
                )

    def test_sell_gross_settlement_must_fit_real_sol_reserve(self):
        curve = bytearray(self.curve_data)
        curve[32:40] = (19_800).to_bytes(8, "little")
        accounts = list(self.accounts)
        accounts[0] = dict(accounts[0], data=bytes(curve))
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpfun_legacy"], "data": bytes(curve)
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 654)),
        ):
            with self.assertRaisesRegex(ValueError, "gross sell settlement"):
                chain.quote_pumpfun_legacy_pool(
                    self.pool, self.token_mint, chain.SOL_MINT, 10_000
                )

    def test_buy_does_not_require_nonzero_real_sol_reserve(self):
        curve = bytearray(self.curve_data)
        curve[32:40] = (0).to_bytes(8, "little")
        accounts = list(self.accounts)
        accounts[0] = dict(accounts[0], data=bytes(curve))
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpfun_legacy"], "data": bytes(curve)
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 654)),
        ):
            result = chain.quote_pumpfun_legacy_pool(
                self.pool, chain.SOL_MINT, self.token_mint, 1000
            )
        self.assertGreater(result["out_amount"], 0)

    def test_sell_input_cannot_exceed_total_supply(self):
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpfun_legacy"], "data": self.curve_data
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 654)),
        ):
            with self.assertRaisesRegex(ValueError, "exceeds total token supply"):
                chain.quote_pumpfun_legacy_pool(
                    self.pool, self.token_mint, chain.SOL_MINT, 1_000_001
                )

    def test_rejects_truncated_curve_extension(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpfun_legacy"], "data": self.curve_data[:82]
        }):
            with self.assertRaisesRegex(ValueError, "truncated"):
                chain.quote_pumpfun_legacy_pool(
                    self.pool, chain.SOL_MINT, self.token_mint, 1000
                )

if __name__ == "__main__":
    unittest.main()
