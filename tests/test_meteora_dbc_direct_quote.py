import unittest
from unittest.mock import patch

import solana_onchain as chain
import meteora_dbc_quote as dbc
from meteora_dbc_quote import quote_meteora_dbc_pool


class MeteoraDbcDirectQuoteTests(unittest.TestCase):
    def setUp(self):
        self.pool = chain._b58encode(bytes([51]) * 32)
        self.config = chain._b58encode(bytes([52]) * 32)
        self.base_mint = chain._b58encode(bytes([53]) * 32)
        self.quote_mint = chain._b58encode(bytes([54]) * 32)
        self.base_vault = chain._b58encode(bytes([55]) * 32)
        self.quote_vault = chain._b58encode(bytes([56]) * 32)

        pool = bytearray(424)
        pool[:8] = chain._discriminator("VirtualPool")
        pool[72:104] = chain._b58decode(self.config)
        pool[136:168] = chain._b58decode(self.base_mint)
        pool[168:200] = chain._b58decode(self.base_vault)
        pool[200:232] = chain._b58decode(self.quote_vault)
        pool[232:240] = (500_000_000).to_bytes(8, "little")
        pool[240:248] = (700_000_000).to_bytes(8, "little")
        pool[248:256] = (100).to_bytes(8, "little")
        pool[256:264] = (200).to_bytes(8, "little")
        pool[264:272] = (300).to_bytes(8, "little")
        pool[272:280] = (400).to_bytes(8, "little")
        pool[280:296] = ((1 << 64) * 105 // 100).to_bytes(16, "little")
        pool[296:304] = (0).to_bytes(8, "little")
        pool[304] = 0
        pool[305] = 0
        self.pool_data = bytes(pool)

        config = bytearray(1048)
        config[:8] = chain._discriminator("PoolConfig")
        config[8:40] = chain._b58decode(self.quote_mint)
        config[104:112] = (10_000_000).to_bytes(8, "little")  # 1% flat fee
        config[112:120] = (0).to_bytes(8, "little")
        config[120:128] = (0).to_bytes(8, "little")
        config[128:130] = (0).to_bytes(2, "little")
        config[130] = 1  # scheduler tag, but all scheduler magnitudes are zero
        config[136] = 0
        config[232] = 0  # QuoteToken fee collection
        config[392:408] = (1 << 64).to_bytes(16, "little")
        config[408:424] = ((1 << 64) * 11 // 10).to_bytes(16, "little")
        config[424:440] = (1 << 105).to_bytes(16, "little")
        config[440:456] = ((1 << 64) * 12 // 10).to_bytes(16, "little")
        config[456:472] = (1 << 106).to_bytes(16, "little")
        self.config_data = bytes(config)

        def token_account(mint, amount):
            raw = bytearray(165)
            raw[:32] = chain._b58decode(mint)
            raw[64:72] = amount.to_bytes(8, "little")
            return {"owner": chain.TOKEN_PROGRAM, "data": bytes(raw)}

        self.accounts = [
            {"owner": chain.PROGRAMS["meteora_dbc"], "data": self.pool_data},
            {"owner": chain.PROGRAMS["meteora_dbc"], "data": self.config_data},
            {"owner": chain.TOKEN_PROGRAM, "data": bytes(82)},
            {"owner": chain.TOKEN_PROGRAM, "data": bytes(82)},
            token_account(self.base_mint, 10**12),
            token_account(self.quote_mint, 10**12),
        ]
        self.pool_account = {"owner": chain.PROGRAMS["meteora_dbc"], "data": self.pool_data}
        self.config_account = {"owner": chain.PROGRAMS["meteora_dbc"], "data": self.config_data}

    def quote(self, input_mint=None, output_mint=None, amount=1_000_000, accounts=None,
              config_data=None):
        config_account = self.config_account if config_data is None else {
            "owner": chain.PROGRAMS["meteora_dbc"], "data": config_data
        }
        with (
            patch.object(chain, "_account", side_effect=[self.pool_account, config_account]),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(
                self.accounts if accounts is None else accounts, 654321
            )),
        ):
            return quote_meteora_dbc_pool(
                self.pool, input_mint or self.base_mint, amount,
                output_mint=output_mint or self.quote_mint,
            )

    def test_confirmed_snapshot_quotes_base_and_quote_directions(self):
        base_sell = self.quote()
        quote_buy = self.quote(input_mint=self.quote_mint, output_mint=self.base_mint)
        self.assertEqual(base_sell["protocol"], "meteora_dbc")
        self.assertEqual(base_sell["slot"], 654321)
        self.assertEqual(base_sell["output_mint"], self.quote_mint)
        self.assertGreater(base_sell["out_amount"], 0)
        self.assertGreater(base_sell["fee_amount"], 0)
        self.assertEqual(base_sell["fee_mint"], self.quote_mint)
        self.assertEqual(base_sell["execution"], "DISABLED")
        self.assertEqual(quote_buy["output_mint"], self.base_mint)
        self.assertGreater(quote_buy["out_amount"], 0)
        self.assertEqual(quote_buy["fee_mint"], self.quote_mint)

    def test_quote_token_mode_charges_quote_input_for_buy(self):
        result = self.quote(input_mint=self.quote_mint, output_mint=self.base_mint)
        self.assertTrue(result["fee_on_input"])
        self.assertEqual(result["fee_mint"], self.quote_mint)

    def test_output_token_mode_charges_output_leg(self):
        config = bytearray(self.config_data)
        config[232] = 1
        result = self.quote(
            input_mint=self.quote_mint, output_mint=self.base_mint,
            config_data=bytes(config),
        )
        self.assertFalse(result["fee_on_input"])
        self.assertEqual(result["fee_mint"], self.base_mint)

    def test_curve_walk_crosses_into_next_liquidity_segment(self):
        config = dbc._decode_pool_config(self.config_data, chain)
        state = dbc._decode_virtual_pool(self.pool_data, chain)
        target = config["points"][0][0]
        needed = dbc._input_for_move(
            False, config["points"][0][1], state["sqrt_price"], target
        )
        config2 = bytearray(self.config_data)
        config2[232] = 1  # output fee, so full amount reaches curve
        result = self.quote(
            input_mint=self.quote_mint, output_mint=self.base_mint,
            amount=needed + 1000, config_data=bytes(config2),
        )
        self.assertGreater(result["sqrt_price_after_x64"], target)

    def test_quote_refuses_to_walk_beyond_configured_curve(self):
        with self.assertRaisesRegex(ValueError, "below configured starting price"):
            self.quote(amount=10**12)

    def test_scheduler_magnitudes_are_refused(self):
        config = bytearray(self.config_data)
        config[112:120] = (1).to_bytes(8, "little")
        with self.assertRaisesRegex(ValueError, "scheduler is active"):
            self.quote(config_data=bytes(config))

    def test_dynamic_fee_is_refused(self):
        config = bytearray(self.config_data)
        config[136] = 1
        with self.assertRaisesRegex(ValueError, "dynamic fees"):
            self.quote(config_data=bytes(config))

    def test_migrated_pool_is_refused(self):
        pool = bytearray(self.pool_data)
        pool[305] = 1
        pool_account = {"owner": chain.PROGRAMS["meteora_dbc"], "data": bytes(pool)}
        with patch.object(chain, "_account", return_value=pool_account):
            with self.assertRaisesRegex(ValueError, "has migrated"):
                quote_meteora_dbc_pool(self.pool, self.base_mint, 1000)

    def test_transfer_hook_pool_is_refused(self):
        pool = bytearray(self.pool_data)
        pool[:8] = dbc.TRANSFER_HOOK_POOL_DISCRIMINATOR
        pool_account = {"owner": chain.PROGRAMS["meteora_dbc"], "data": bytes(pool)}
        with patch.object(chain, "_account", return_value=pool_account):
            with self.assertRaisesRegex(ValueError, "transfer-hook"):
                quote_meteora_dbc_pool(self.pool, self.base_mint, 1000)

    def test_token_2022_mints_are_refused(self):
        accounts = list(self.accounts)
        accounts[3] = dict(accounts[3], owner=chain.TOKEN_2022_PROGRAM)
        with self.assertRaisesRegex(ValueError, "Token-2022"):
            self.quote(accounts=accounts)

    def test_wrong_output_mint_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Requested output mint"):
            self.quote(output_mint="wrong")

    def test_unrecognized_fee_mode_is_refused(self):
        config = bytearray(self.config_data)
        config[232] = 9
        with self.assertRaisesRegex(ValueError, "fee collection mode"):
            self.quote(config_data=bytes(config))

    def test_curve_checkpoints_must_be_increasing(self):
        config = bytearray(self.config_data)
        config[440:456] = ((1 << 64) * 10 // 10).to_bytes(16, "little")
        with self.assertRaisesRegex(ValueError, "strictly ascending"):
            self.quote(config_data=bytes(config))


if __name__ == "__main__":
    unittest.main()
