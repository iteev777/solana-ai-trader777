import unittest
from unittest.mock import patch

import solana_onchain as chain


class PumpFunLegacyDirectQuoteTests(unittest.TestCase):
    def setUp(self):
        self.pool = "pump-curve"
        self.token_mint = chain._b58encode(bytes([11]) * 32)

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

    def test_completed_curve_is_decoded_but_not_marked_active(self):
        curve = bytearray(self.curve_data)
        curve[48] = 1
        decoded = chain._decode_pool("pumpfun_legacy", bytes(curve))
        self.assertTrue(decoded["complete"])
        self.assertEqual(decoded["status"], "complete")

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

    def test_mayhem_curve_uses_supported_fee_tier_without_crossing_curve(self):
        curve = bytearray(self.curve_data)
        curve[81] = 1
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
        self.assertTrue(result["is_mayhem_mode"])
        self.assertEqual(result["fee_rates_bps"], [0, 95, 30])

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

    def test_rejects_token_2022_until_mint_extensions_are_supported(self):
        accounts = list(self.accounts)
        accounts[3] = dict(accounts[3], owner=chain.TOKEN_2022_PROGRAM)
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpfun_legacy"], "data": self.curve_data
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 654)),
        ):
            with self.assertRaisesRegex(ValueError, "Token-2022"):
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


if __name__ == "__main__":
    unittest.main()
