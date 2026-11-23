import unittest
import time
from unittest.mock import patch

import solana_onchain as chain
from meteora_damm_quote import quote_meteora_damm_pool


class MeteoraDammDirectQuoteTests(unittest.TestCase):
    def setUp(self):
        self.pool = chain._b58encode(bytes([41]) * 32)
        self.mint_a = chain._b58encode(bytes([42]) * 32)
        self.mint_b = chain._b58encode(bytes([43]) * 32)
        self.vault_a = chain._b58encode(bytes([44]) * 32)
        self.vault_b = chain._b58encode(bytes([45]) * 32)

        raw = bytearray(1112)
        raw[:8] = chain._discriminator("Pool")
        raw[8:16] = (3_000_000).to_bytes(8, "little")  # 0.3% cliff
        raw[16] = 0  # time scheduler: cliff is a conservative upper bound
        raw[168:200] = chain._b58decode(self.mint_a)
        raw[200:232] = chain._b58decode(self.mint_b)
        raw[232:264] = chain._b58decode(self.vault_a)
        raw[264:296] = chain._b58decode(self.vault_b)
        raw[360:376] = (1 << 100).to_bytes(16, "little")
        raw[392:400] = (100).to_bytes(8, "little")
        raw[400:408] = (200).to_bytes(8, "little")
        raw[424:440] = ((1 << 64) // 2).to_bytes(16, "little")
        raw[440:456] = ((1 << 64) * 2).to_bytes(16, "little")
        raw[456:472] = (1 << 64).to_bytes(16, "little")
        raw[480] = 0
        raw[481] = 0
        raw[484] = 0  # both-token mode: fee is collected from output
        raw[486] = 1  # fee version 1, 99% ceiling
        self.state = bytes(raw)

        def token_account(mint, amount):
            data = bytearray(165)
            data[:32] = chain._b58decode(mint)
            data[64:72] = amount.to_bytes(8, "little")
            return {"owner": chain.TOKEN_PROGRAM, "data": bytes(data)}

        self.accounts = [
            {"owner": chain.PROGRAMS["meteora_damm_v2"], "data": self.state},
            token_account(self.mint_a, 10**12),
            token_account(self.mint_b, 10**12),
            {"owner": chain.TOKEN_PROGRAM, "data": bytes(82)},
            {"owner": chain.TOKEN_PROGRAM, "data": bytes(82)},
        ]

    def quote(self, input_mint=None, output_mint=None, amount=1_000_000, accounts=None):
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["meteora_damm_v2"], "data": self.state
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(
                self.accounts if accounts is None else accounts, 765432
            )),
            patch.object(chain, "_rpc", side_effect=lambda method, params: int(time.time()) if method == "getBlockTime" else 765433),
        ):
            return quote_meteora_damm_pool(
                self.pool, input_mint or self.mint_a, amount,
                output_mint=output_mint or self.mint_b,
            )

    def test_confirmed_snapshot_quotes_both_directions(self):
        a_to_b = self.quote()
        b_to_a = self.quote(input_mint=self.mint_b, output_mint=self.mint_a)
        self.assertEqual(a_to_b["protocol"], "meteora_damm_v2")
        self.assertEqual(a_to_b["slot"], 765432)
        self.assertEqual(a_to_b["output_mint"], self.mint_b)
        self.assertGreater(a_to_b["out_amount"], 0)
        self.assertGreater(a_to_b["fee_amount"], 0)
        self.assertEqual(a_to_b["fee_mint"], self.mint_b)
        self.assertEqual(a_to_b["execution"], "DISABLED")
        self.assertEqual(b_to_a["output_mint"], self.mint_a)
        self.assertGreater(b_to_a["out_amount"], 0)

    def test_only_b_mode_charges_input_when_token_b_is_spent(self):
        state = bytearray(self.state)
        state[484] = 1
        self.state = bytes(state)
        accounts = list(self.accounts)
        accounts[0] = {"owner": chain.PROGRAMS["meteora_damm_v2"], "data": self.state}
        result = self.quote(input_mint=self.mint_b, output_mint=self.mint_a, accounts=accounts)
        self.assertEqual(result["fee_mint"], self.mint_b)
        self.assertTrue(result["fee_on_input"])

    def test_rate_limiter_uses_configured_ceiling_conservatively(self):
        state = bytearray(self.state)
        state[16] = 2
        state[28:32] = (1000).to_bytes(4, "little")  # 10%
        self.state = bytes(state)
        accounts = list(self.accounts)
        accounts[0] = {"owner": chain.PROGRAMS["meteora_damm_v2"], "data": self.state}
        result = self.quote(accounts=accounts)
        self.assertEqual(result["fee_rate_numerator"], 100_000_000)

    def test_dynamic_fee_is_added_using_stored_volatility(self):
        state = bytearray(self.state)
        state[56] = 1
        state[68:72] = (1).to_bytes(4, "little")
        state[72:74] = (10).to_bytes(2, "little")
        state[120:136] = (100_000).to_bytes(16, "little")
        self.state = bytes(state)
        accounts = list(self.accounts)
        accounts[0] = {"owner": chain.PROGRAMS["meteora_damm_v2"], "data": self.state}
        result = self.quote(accounts=accounts)
        self.assertGreater(result["fee_rate_numerator"], 3_000_000)

    def test_fee_version_cap_is_applied(self):
        state = bytearray(self.state)
        state[8:16] = (800_000_000).to_bytes(8, "little")
        state[486] = 0
        self.state = bytes(state)
        accounts = list(self.accounts)
        accounts[0] = {"owner": chain.PROGRAMS["meteora_damm_v2"], "data": self.state}
        result = self.quote(accounts=accounts)
        self.assertEqual(result["fee_rate_numerator"], 500_000_000)

    def test_invalid_fee_version_is_rejected(self):
        state = bytearray(self.state)
        state[486] = 2
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["meteora_damm_v2"], "data": bytes(state)
        }):
            with self.assertRaisesRegex(ValueError, "fee version"):
                quote_meteora_damm_pool(self.pool, self.mint_a, 1000, output_mint=self.mint_b)

    def test_disabled_pool_is_rejected(self):
        state = bytearray(self.state)
        state[481] = 1
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["meteora_damm_v2"], "data": bytes(state)
        }):
            with self.assertRaisesRegex(ValueError, "not enabled"):
                quote_meteora_damm_pool(self.pool, self.mint_a, 1000, output_mint=self.mint_b)

    def test_price_band_is_enforced(self):
        with self.assertRaisesRegex(ValueError, "price boundary"):
            self.quote(amount=10**12)

    def test_token_2022_mints_are_rejected(self):
        accounts = list(self.accounts)
        accounts[4] = dict(accounts[4], owner=chain.TOKEN_2022_PROGRAM)
        with self.assertRaisesRegex(ValueError, "Token-2022"):
            self.quote(accounts=accounts)

    def test_program_owner_dispatches_to_meteora_damm_adapter(self):
        expected = {"protocol": "meteora_damm_v2", "out_amount": 123}
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["meteora_damm_v2"], "data": self.state
            }),
            patch("meteora_damm_quote.quote_meteora_damm_pool", return_value=expected) as quote,
        ):
            result = chain.quote_direct_pool(
                self.pool, self.mint_a, 1000, 75, output_mint=self.mint_b
            )
        self.assertEqual(result, expected)
        quote.assert_called_once_with(
            self.pool, self.mint_a, 1000, 75, output_mint=self.mint_b
        )

    def test_requested_output_mint_mismatch_is_rejected(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["meteora_damm_v2"], "data": self.state
        }):
            with self.assertRaisesRegex(ValueError, "Requested output mint"):
                quote_meteora_damm_pool(self.pool, self.mint_a, 1000, output_mint="wrong")


    def test_router_status_lists_meteora_damm_v2_adapter(self):
        import route_comparison
        status = route_comparison.get_router_status()
        self.assertIn("Meteora DAMM v2", status["direct_pool"]["quote_adapters"])


    def test_pool_activation_point_is_enforced(self):
        state = bytearray(self.state)
        state[472:480] = (765434).to_bytes(8, "little")
        self.state = bytes(state)
        accounts = list(self.accounts)
        accounts[0] = {"owner": chain.PROGRAMS["meteora_damm_v2"], "data": self.state}
        with self.assertRaisesRegex(ValueError, "activation point"):
            self.quote(accounts=accounts)

    def test_unknown_activation_type_is_rejected(self):
        state = bytearray(self.state)
        state[480] = 2
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["meteora_damm_v2"], "data": bytes(state)
        }):
            with self.assertRaisesRegex(ValueError, "activation type"):
                quote_meteora_damm_pool(self.pool, self.mint_a, 1000, output_mint=self.mint_b)

    def test_confirmed_slot_block_time_is_required(self):
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["meteora_damm_v2"], "data": self.state
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 765432)),
            patch.object(chain, "_rpc", return_value=None),
        ):
            with self.assertRaisesRegex(ValueError, "block time is unavailable"):
                quote_meteora_damm_pool(self.pool, self.mint_a, 1000, output_mint=self.mint_b)

    def test_stale_confirmed_slot_is_rejected(self):
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["meteora_damm_v2"], "data": self.state
            }),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(self.accounts, 765432)),
            patch.object(chain, "_rpc", side_effect=lambda method, params: int(time.time() - 30) if method == "getBlockTime" else 765433),
        ):
            with self.assertRaisesRegex(ValueError, "snapshot is stale"):
                quote_meteora_damm_pool(self.pool, self.mint_a, 1000, output_mint=self.mint_b)



if __name__ == "__main__":
    unittest.main()
