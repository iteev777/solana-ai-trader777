import unittest
from unittest.mock import patch

import solana_onchain as chain


class PumpSwapDirectQuoteTests(unittest.TestCase):
    def setUp(self):
        self.pool = "pump-pool"
        self.base_mint = chain._b58encode(bytes([1]) * 32)
        self.quote_mint = chain._b58encode(bytes([2]) * 32)
        self.base_vault = chain._b58encode(bytes([3]) * 32)
        self.quote_vault = chain._b58encode(bytes([4]) * 32)
        self.creator = chain._b58encode(bytes([5]) * 32)
        self.coin_creator = chain._b58encode(bytes([6]) * 32)

        pool = bytearray(271)
        pool[:8] = bytes([241, 154, 109, 4, 17, 177, 109, 188])
        pool[8] = 254
        pool[11:43] = chain._b58decode(self.creator)
        pool[43:75] = chain._b58decode(self.base_mint)
        pool[75:107] = chain._b58decode(self.quote_mint)
        pool[139:171] = chain._b58decode(self.base_vault)
        pool[171:203] = chain._b58decode(self.quote_vault)
        pool[211:243] = chain._b58decode(self.coin_creator)
        pool[243] = 0
        pool[244] = 0
        pool[245:261] = (-100).to_bytes(16, "little", signed=True)
        pool[261:269] = (30).to_bytes(8, "little")
        pool[270] = 0
        self.pool_data = bytes(pool)

        global_config = bytearray(321)
        global_config[:8] = chain.PUMPSWAP_GLOBAL_CONFIG_DISCRIMINATOR
        global_config[40:48] = (20).to_bytes(8, "little")
        global_config[48:56] = (5).to_bytes(8, "little")
        global_config[56] = 0
        global_config[313:321] = (30).to_bytes(8, "little")
        self.global_data = bytes(global_config)

        fee = bytearray(41)
        fee[:8] = chain.PUMPSWAP_FEE_CONFIG_DISCRIMINATOR
        fee[8] = 1
        fee[41:49] = (20).to_bytes(8, "little")
        fee[49:57] = (5).to_bytes(8, "little")
        fee[57:65] = (30).to_bytes(8, "little")
        cursor = 65
        fee[cursor:cursor + 4] = (2).to_bytes(4, "little")
        cursor += 4
        for threshold, rates in (
            (0, (2, 93, 30)),
            (1_000_000, (2, 50, 30)),
        ):
            fee[cursor:cursor + 16] = threshold.to_bytes(16, "little")
            cursor += 16
            for rate in rates:
                fee[cursor:cursor + 8] = rate.to_bytes(8, "little")
                cursor += 8
        fee[cursor:cursor + 4] = (1).to_bytes(4, "little")
        cursor += 4
        fee[cursor:cursor + 16] = (0).to_bytes(16, "little")
        cursor += 16
        for rate in (2, 50, 30):
            fee[cursor:cursor + 8] = rate.to_bytes(8, "little")
            cursor += 8
        for rate in (2, 10, 30):
            fee[cursor:cursor + 8] = rate.to_bytes(8, "little")
            cursor += 8
        self.fee_data = bytes(fee)

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
            {"owner": chain.PROGRAMS["pumpswap"], "data": self.pool_data},
            {"owner": chain.PROGRAMS["pumpswap"], "data": self.global_data},
            {"owner": chain.PUMP_FEES_PROGRAM, "data": self.fee_data},
            token_account(self.base_mint, 1_000_000),
            token_account(self.quote_mint, 2_000_000),
            mint_account(6),
            mint_account(9),
        ]

    def quote(self, input_mint=None, amount=10_000, **kwargs):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpswap"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 789)):
            return chain.quote_pumpswap_pool(
                self.pool, input_mint or self.quote_mint, amount, **kwargs
            )

    def test_quote_input_uses_effective_signed_quote_reserve_and_max_fee_tier(self):
        result = self.quote()
        self.assertEqual(result["fee_rates_bps"], [2, 93, 30])
        self.assertEqual(result["total_fee_bps"], 125)
        self.assertEqual(result["total_fee_amount"], 124)
        self.assertEqual(result["out_amount"], 4913)
        self.assertEqual(result["minimum_out_amount"], 4863)
        self.assertEqual(result["effective_quote_reserves"], 1_999_900)
        self.assertEqual(result["virtual_quote_reserves"], -100)
        self.assertEqual(result["slot"], 789)
        self.assertEqual(result["fee_policy"], "CONSERVATIVE_MAX_CONFIGURED_ONCHAIN_SCHEDULE")
        self.assertEqual(result["execution"], "DISABLED")
        self.assertEqual(result["signing"], "DISABLED")
        self.assertEqual(result["broadcast"], "DISABLED")

    def test_base_input_sell_charges_quote_output_fees(self):
        result = self.quote(input_mint=self.base_mint)
        self.assertEqual(result["output_mint"], self.quote_mint)
        self.assertEqual(result["output_amount_gross"], 19_800)
        self.assertEqual(result["total_fee_amount"], 249)
        self.assertEqual(result["out_amount"], 19_551)
        self.assertEqual(result["minimum_out_amount"], 19_355)

    def test_decoder_reads_signed_virtual_reserves_and_appended_pool_flags(self):
        decoded = chain._decode_pool("pumpswap", self.pool_data)
        self.assertEqual(decoded["creator"], self.creator)
        self.assertEqual(decoded["coin_creator"], self.coin_creator)
        self.assertEqual(decoded["virtual_quote_reserves"], -100)
        self.assertEqual(decoded["creator_fee_bps"], 30)
        self.assertFalse(decoded["is_cashback_coin"])
        self.assertFalse(decoded["is_holder_reward"])

    def test_fee_config_parser_reads_flat_and_both_tier_tables(self):
        parsed = chain._decode_pumpswap_fee_config(self.fee_data)
        self.assertEqual(len(parsed["fee_tiers"]), 2)
        self.assertEqual(len(parsed["stable_fee_tiers"]), 1)
        self.assertEqual(parsed["conservative_total_bps"], 125)

    def test_dispatch_uses_actual_pool_owner(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpswap"], "data": self.pool_data
        }), patch.object(chain, "quote_pumpswap_pool", return_value={"protocol": "pumpswap"}) as quote:
            result = chain.quote_direct_pool(self.pool, self.quote_mint, 1000, 75)
        self.assertEqual(result["protocol"], "pumpswap")
        quote.assert_called_once_with(self.pool, self.quote_mint, 1000, 75)

    def test_rejects_wrong_pool_owner(self):
        with patch.object(chain, "_account", return_value={"owner": "wrong", "data": self.pool_data}):
            with self.assertRaisesRegex(ValueError, "not owned"):
                chain.quote_pumpswap_pool(self.pool, self.quote_mint, 1000)

    def test_rejects_cashback_and_holder_reward_pools(self):
        for offset in (244, 270):
            raw = bytearray(self.pool_data)
            raw[offset] = 1
            with patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpswap"], "data": bytes(raw)
            }):
                with self.assertRaisesRegex(ValueError, "not supported|separate fee model"):
                    chain.quote_pumpswap_pool(self.pool, self.quote_mint, 1000)

    def test_rejects_wrong_fee_program_owner(self):
        accounts = list(self.accounts)
        accounts[2] = dict(accounts[2], owner="wrong")
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpswap"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 789)):
            with self.assertRaisesRegex(ValueError, "not owned by the Pump Fees"):
                chain.quote_pumpswap_pool(self.pool, self.quote_mint, 1000)

    def test_rejects_invalid_fee_config_discriminator(self):
        accounts = list(self.accounts)
        fee = bytearray(self.fee_data)
        fee[0] ^= 1
        accounts[2] = dict(accounts[2], data=bytes(fee))
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpswap"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 789)):
            with self.assertRaisesRegex(ValueError, "discriminator"):
                chain.quote_pumpswap_pool(self.pool, self.quote_mint, 1000)

    def test_rejects_token_2022_mints_until_extension_fees_are_supported(self):
        accounts = list(self.accounts)
        accounts[5] = dict(accounts[5], owner=chain.TOKEN_2022_PROGRAM)
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpswap"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 789)):
            with self.assertRaisesRegex(ValueError, "Token-2022"):
                chain.quote_pumpswap_pool(self.pool, self.quote_mint, 1000)

    def test_buy_and_sell_disable_flags_are_enforced(self):
        for input_mint, flag in ((self.quote_mint, 1 << 3), (self.base_mint, 1 << 4)):
            accounts = list(self.accounts)
            config = bytearray(self.global_data)
            config[56] = flag
            accounts[1] = dict(accounts[1], data=bytes(config))
            with patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["pumpswap"], "data": self.pool_data
            }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 789)):
                with self.assertRaisesRegex(ValueError, "disabled by GlobalConfig"):
                    chain.quote_pumpswap_pool(self.pool, input_mint, 1000)

    def test_rejects_wrong_input_mint(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpswap"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 789)):
            with self.assertRaisesRegex(ValueError, "does not belong"):
                chain.quote_pumpswap_pool(self.pool, "not-a-pool-mint", 1000)

    def test_rejects_empty_effective_quote_reserve(self):
        raw = bytearray(self.pool_data)
        raw[245:261] = (-3_000_000).to_bytes(16, "little", signed=True)
        accounts = list(self.accounts)
        accounts[0] = dict(accounts[0], data=bytes(raw))
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpswap"], "data": bytes(raw)
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 789)):
            with self.assertRaisesRegex(ValueError, "effective pool reserves"):
                chain.quote_pumpswap_pool(self.pool, self.quote_mint, 1000)

    def test_rejects_stale_confirmed_snapshot(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpswap"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 789)), patch.object(
            chain.time, "time", side_effect=[100.0, 110.0]
        ):
            with self.assertRaisesRegex(ValueError, "snapshot is stale"):
                chain.quote_pumpswap_pool(self.pool, self.quote_mint, 1000, max_snapshot_age=2.0)



    def test_buy_leaves_the_program_required_one_raw_unit_rounding_buffer(self):
        result = self.quote()
        self.assertEqual(result["unspent_input_amount"], 1)
        self.assertEqual(result["total_fee_amount"], 124)

    def test_third_party_pool_does_not_charge_creator_fee(self):
        raw = bytearray(self.pool_data)
        raw[211:243] = bytes(32)
        accounts = list(self.accounts)
        accounts[0] = dict(accounts[0], data=bytes(raw))
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpswap"], "data": bytes(raw)
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 789)):
            result = chain.quote_pumpswap_pool(self.pool, self.quote_mint, 10_000)
        self.assertEqual(result["fee_rates_bps"], [2, 93])
        self.assertEqual(result["creator_fee_amount"], 0)
        self.assertEqual(result["total_fee_bps"], 95)

    def test_global_config_flat_fee_can_be_more_conservative_than_fee_program_table(self):
        accounts = list(self.accounts)
        global_data = bytearray(self.global_data)
        global_data[40:48] = (200).to_bytes(8, "little")
        global_data[48:56] = (100).to_bytes(8, "little")
        global_data[313:321] = (50).to_bytes(8, "little")
        accounts[1] = dict(accounts[1], data=bytes(global_data))
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpswap"], "data": self.pool_data
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 789)):
            result = chain.quote_pumpswap_pool(self.pool, self.quote_mint, 10_000)
        self.assertEqual(result["fee_rates_bps"], [200, 100, 50])
        self.assertEqual(result["total_fee_bps"], 350)

    def test_sell_payout_is_capped_by_real_quote_vault_not_virtual_reserve(self):
        raw = bytearray(self.pool_data)
        raw[245:261] = (5_000_000).to_bytes(16, "little", signed=True)
        accounts = list(self.accounts)
        accounts[0] = dict(accounts[0], data=bytes(raw))
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpswap"], "data": bytes(raw)
        }), patch.object(chain, "_multiple_accounts_with_context", return_value=(accounts, 789)):
            result = chain.quote_pumpswap_pool(self.pool, self.base_mint, 1_000_000)
        self.assertEqual(result["effective_quote_reserves"], 7_000_000)
        self.assertEqual(result["output_amount_gross"], 2_000_000)
        self.assertLess(result["out_amount"], 2_000_000)

    def test_mayhem_pool_is_refused(self):
        raw = bytearray(self.pool_data)
        raw[243] = 1
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpswap"], "data": bytes(raw)
        }):
            with self.assertRaisesRegex(ValueError, "mayhem-mode"):
                chain.quote_pumpswap_pool(self.pool, self.quote_mint, 1000)

    def test_truncated_pool_layout_is_refused(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["pumpswap"], "data": self.pool_data[:244]
        }):
            with self.assertRaisesRegex(ValueError, "truncated"):
                chain.quote_pumpswap_pool(self.pool, self.quote_mint, 1000)

if __name__ == "__main__":
    unittest.main()
