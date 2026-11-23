import unittest
from unittest.mock import patch

import clmm_quote_math as mathx
import solana_onchain as chain
from orca_whirlpool_quote import quote_orca_whirlpool_pool
import orca_whirlpool_builder as orca_builder


class OrcaWhirlpoolDirectQuoteTests(unittest.TestCase):
    def setUp(self):
        self.pool = chain._b58encode(bytes([21]) * 32)
        self.mint_a = chain._b58encode(bytes([22]) * 32)
        self.mint_b = chain._b58encode(bytes([23]) * 32)
        self.vault_a = chain._b58encode(bytes([24]) * 32)
        self.vault_b = chain._b58encode(bytes([25]) * 32)
        self.oracle = chain._b58encode(bytes([26]) * 32)
        self.tick_addresses = {
            0: chain._b58encode(bytes([27]) * 32),
            88: chain._b58encode(bytes([28]) * 32),
            176: chain._b58encode(bytes([29]) * 32),
            -88: chain._b58encode(bytes([30]) * 32),
            -176: chain._b58encode(bytes([31]) * 32),
        }

        state = bytearray(653)
        state[:8] = chain._discriminator("Whirlpool")
        state[41:43] = (1).to_bytes(2, "little")
        state[43:45] = (1).to_bytes(2, "little")
        state[45:47] = (400).to_bytes(2, "little")
        state[47:49] = (300).to_bytes(2, "little")
        state[49:65] = (1_000_000_000).to_bytes(16, "little")
        state[65:81] = (mathx.sqrt_price_at_tick(10) + 1000).to_bytes(16, "little")
        state[81:85] = (10).to_bytes(4, "little", signed=True)
        state[101:133] = chain._b58decode(self.mint_a)
        state[133:165] = chain._b58decode(self.vault_a)
        state[181:213] = chain._b58decode(self.mint_b)
        state[213:245] = chain._b58decode(self.vault_b)
        self.state = bytes(state)

        def token_account(mint, amount, token_program=chain.TOKEN_PROGRAM,
                          transfer_fee=False, immutable_owner=False):
            raw = bytearray(182 if immutable_owner else (178 if transfer_fee else 165))
            raw[:32] = chain._b58decode(mint)
            raw[32:64] = chain._b58decode(self.pool)
            raw[64:72] = amount.to_bytes(8, "little")
            raw[108] = 1
            if transfer_fee:
                raw[165] = 2  # Token account type
                raw[166:168] = (2).to_bytes(2, "little")
                raw[168:170] = (8).to_bytes(2, "little")
            if immutable_owner:
                raw[178:180] = (7).to_bytes(2, "little")
                raw[180:182] = (0).to_bytes(2, "little")
            return {"owner": token_program, "data": bytes(raw)}

        self.token_account = token_account
        self.vault_a_account = token_account(self.mint_a, 10**12)
        self.vault_b_account = token_account(self.mint_b, 10**12)
        mint_a = bytearray(82)
        mint_a[44] = 6
        mint_a[45] = 1
        mint_b = bytearray(82)
        mint_b[44] = 9
        mint_b[45] = 1
        self.mint_a_account = {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint_a)}
        self.mint_b_account = {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint_b)}

        tick = bytearray(9988)
        tick[:8] = chain._discriminator("TickArray")
        tick[8:12] = (0).to_bytes(4, "little", signed=True)
        tick[-32:] = chain._b58decode(self.pool)
        self.tick_account = {"owner": chain.PROGRAMS["orca_whirlpool"], "data": bytes(tick)}
        self.accounts = [
            {"owner": chain.PROGRAMS["orca_whirlpool"], "data": self.state},
            self.vault_a_account, self.vault_b_account,
            self.mint_a_account, self.mint_b_account,
            None,
            self.tick_account, None, None,
        ]

    def _fake_pda(self, seeds, program):
        if seeds[0] == b"oracle":
            return self.oracle, 255
        start = int(seeds[2].decode("ascii"))
        return self.tick_addresses[start], 255

    def quote(self, input_mint=None, output_mint=None, amount=10_000, accounts=None):
        with (
            patch.object(chain, "_account", return_value={
                "owner": chain.PROGRAMS["orca_whirlpool"], "data": self.state
            }),
            patch.object(chain, "_find_program_address", side_effect=self._fake_pda),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(
                self.accounts if accounts is None else accounts, 98765
            )),
        ):
            return quote_orca_whirlpool_pool(
                self.pool, input_mint or self.mint_b, amount,
                output_mint=output_mint or self.mint_a,
            )

    def test_confirmed_snapshot_quotes_both_directions_read_only(self):
        buy = self.quote()
        sell = self.quote(input_mint=self.mint_a, output_mint=self.mint_b)
        self.assertEqual(buy["protocol"], "orca_whirlpool")
        self.assertEqual(buy["slot"], 98765)
        self.assertEqual(buy["output_mint"], self.mint_a)
        self.assertGreater(buy["out_amount"], 0)
        self.assertGreater(buy["fee_amount"], 0)
        self.assertEqual(buy["execution"], "DISABLED")
        self.assertEqual(sell["output_mint"], self.mint_b)
        self.assertGreater(sell["out_amount"], 0)

    def test_requested_output_mint_mismatch_is_rejected(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["orca_whirlpool"], "data": self.state
        }):
            with self.assertRaisesRegex(ValueError, "Requested output mint"):
                quote_orca_whirlpool_pool(self.pool, self.mint_b, 1000, output_mint="wrong")

    def test_non_whirlpool_owner_is_rejected(self):
        with patch.object(chain, "_account", return_value={
            "owner": chain.PROGRAMS["raydium_cpmm"], "data": self.state
        }):
            with self.assertRaisesRegex(ValueError, "not owned"):
                quote_orca_whirlpool_pool(self.pool, self.mint_b, 1000, output_mint=self.mint_a)

    def test_adaptive_fee_tier_index_mismatch_is_rejected(self):
        state = bytearray(self.state)
        state[43:45] = (64).to_bytes(2, "little")
        self.state = bytes(state)
        self.accounts[0] = {
            "owner": chain.PROGRAMS["orca_whirlpool"], "data": self.state
        }
        with self.assertRaisesRegex(ValueError, "adaptive-fee tier"):
            self.quote()

    def test_static_fee_rate_cap_is_enforced(self):
        state = bytearray(self.state)
        state[45:47] = (60_001).to_bytes(2, "little")
        self.state = bytes(state)
        self.accounts[0] = {
            "owner": chain.PROGRAMS["orca_whirlpool"], "data": self.state
        }
        with self.assertRaisesRegex(ValueError, "static fee cap"):
            self.quote()

    def test_protocol_fee_rate_cap_is_enforced(self):
        state = bytearray(self.state)
        state[47:49] = (2_501).to_bytes(2, "little")
        self.state = bytes(state)
        self.accounts[0] = {
            "owner": chain.PROGRAMS["orca_whirlpool"], "data": self.state
        }
        with self.assertRaisesRegex(ValueError, "protocol fee rate"):
            self.quote()

    def test_protocol_fees_are_subtracted_from_spendable_vault_reserves(self):
        state = bytearray(self.state)
        state[85:93] = (10**12 + 1).to_bytes(8, "little")
        self.state = bytes(state)
        self.accounts[0] = {
            "owner": chain.PROGRAMS["orca_whirlpool"], "data": self.state
        }
        with self.assertRaisesRegex(ValueError, "spendable vault reserves"):
            self.quote()

    def test_active_adaptive_fee_oracle_is_rejected(self):
        accounts = list(self.accounts)
        oracle = bytearray(64)
        oracle[54:58] = (1).to_bytes(4, "little")
        accounts[5] = {"owner": chain.PROGRAMS["orca_whirlpool"], "data": bytes(oracle)}
        with self.assertRaisesRegex(ValueError, "adaptive-fee"):
            self.quote(accounts=accounts)

    def test_unknown_oracle_layout_is_rejected(self):
        accounts = list(self.accounts)
        accounts[5] = {"owner": chain.PROGRAMS["orca_whirlpool"], "data": bytes(20)}
        with self.assertRaisesRegex(ValueError, "oracle layout"):
            self.quote(accounts=accounts)

    def test_token2022_zero_fee_mint_is_accepted_with_matching_vault_program(self):
        accounts = list(self.accounts)
        mint = bytearray(82)
        mint[44] = 6
        mint[45] = 1
        accounts[4] = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(mint)}
        accounts[1] = self.token_account(
            self.mint_a, 10**12, token_program=chain.TOKEN_2022_PROGRAM
        )
        result = self.quote(accounts=accounts)
        self.assertEqual(result["token_program_output"], chain.TOKEN_2022_PROGRAM)
        self.assertFalse(result["token2022_output_transfer_fee"])
        self.assertGreater(result["out_amount"], 0)

    def test_token2022_transfer_fees_are_applied_to_both_whirlpool_legs(self):
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
        accounts[3] = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(mint)}
        accounts[4] = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(mint)}
        accounts[1] = self.token_account(
            self.mint_a, 10**12, token_program=chain.TOKEN_2022_PROGRAM,
            transfer_fee=True,
        )
        accounts[2] = self.token_account(
            self.mint_b, 10**12, token_program=chain.TOKEN_2022_PROGRAM,
            transfer_fee=True,
        )
        result = self.quote(accounts=accounts)
        self.assertTrue(result["token2022_input_transfer_fee"])
        self.assertEqual(result["token2022_input_received_by_pool"], 9_900)
        self.assertTrue(result["token2022_output_transfer_fee"])
        self.assertLess(result["out_amount"], result["token2022_output_gross_amount"])

    def test_token2022_transfer_hook_mint_is_rejected(self):
        from token2022_inspector import MINT_ACCOUNT_TYPE
        mint = bytearray(166)
        mint[44] = 6
        mint[45] = 1
        mint[165] = MINT_ACCOUNT_TYPE
        mint.extend((14).to_bytes(2, "little") + (64).to_bytes(2, "little") + bytes(64))
        accounts = list(self.accounts)
        accounts[4] = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(mint)}
        accounts[1] = self.token_account(
            self.mint_a, 10**12, token_program=chain.TOKEN_2022_PROGRAM
        )
        with self.assertRaisesRegex(ValueError, "unsupported by native swap builders"):
            self.quote(accounts=accounts)

    def test_token2022_fee_bearing_swap_uses_swap_v2_and_correct_program_accounts(self):
        from token2022_inspector import MINT_ACCOUNT_TYPE
        quote = dict(
            self.quote(),
            token_program_input=chain.TOKEN_2022_PROGRAM,
            token_program_output=chain.TOKEN_PROGRAM,
        )
        wallet = chain._b58encode(bytes([41]) * 32)
        user_a = self.token_account(self.mint_a, 0)
        user_a["data"] = bytearray(user_a["data"])
        user_a["data"][32:64] = chain._b58decode(wallet)
        user_a["data"] = bytes(user_a["data"])

        config = bytearray(108)
        config[80:88] = (10_000).to_bytes(8, "little")
        config[88:90] = (100).to_bytes(2, "little")
        config[98:106] = (10_000).to_bytes(8, "little")
        config[106:108] = (100).to_bytes(2, "little")
        mint_b = bytearray(166)
        mint_b[44] = 9
        mint_b[45] = 1
        mint_b[165] = MINT_ACCOUNT_TYPE
        mint_b.extend((1).to_bytes(2, "little") + (108).to_bytes(2, "little") + config)
        mint_b_account = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(mint_b)}

        def token2022_account(mint, owner, amount, immutable_owner=False):
            raw = bytearray(182 if immutable_owner else 178)
            raw[:32] = chain._b58decode(mint)
            raw[32:64] = chain._b58decode(owner)
            raw[64:72] = amount.to_bytes(8, "little")
            raw[108] = 1
            raw[165] = 2
            raw[166:168] = (2).to_bytes(2, "little")
            raw[168:170] = (8).to_bytes(2, "little")
            if immutable_owner:
                raw[178:180] = (7).to_bytes(2, "little")
                raw[180:182] = (0).to_bytes(2, "little")
            return {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw)}

        vault_b = token2022_account(self.mint_b, self.pool, 10**12)
        user_b = token2022_account(self.mint_b, wallet, 10**9, immutable_owner=True)
        oracle = None
        snapshot = [
            self.accounts[0], self.vault_a_account, vault_b,
            self.mint_a_account, mint_b_account, user_a, user_b,
            None, None, None, oracle,
        ]
        with (
            patch.object(orca_builder, "quote_orca_whirlpool_pool", return_value=quote),
            patch.object(chain, "_account", return_value=self.accounts[0]),
            patch.object(chain, "_find_program_address", side_effect=self._fake_pda),
            patch.object(chain, "_multiple_accounts_with_context", return_value=(snapshot, 98765)),
            patch.object(chain, "_rpc", return_value={"value": {
                "blockhash": chain._b58encode(bytes([42]) * 32),
                "lastValidBlockHeight": 999999,
            }}),
        ):
            result = orca_builder.build_orca_whirlpool_swap(
                quote, wallet, chain._b58encode(bytes([43]) * 32),
                chain._b58encode(bytes([44]) * 32),
            )
        data = __import__("base64").b64decode(result["transaction"])
        self.assertEqual(result["instruction"], "SwapV2")
        self.assertEqual(result["instruction_discriminator"],
                         list(orca_builder.SWAP_V2_DISCRIMINATOR))
        self.assertEqual(result["token_program_input"], chain.TOKEN_2022_PROGRAM)
        self.assertEqual(result["token_program_output"], chain.TOKEN_PROGRAM)
        self.assertTrue(result["token2022_fee_aware_supported"])
        self.assertEqual(data[0], 1)  # one required signer

    def test_dynamic_tick_array_layout_is_rejected(self):
        accounts = list(self.accounts)
        accounts[6] = {
            "owner": chain.PROGRAMS["orca_whirlpool"],
            "data": bytes(8548),
        }
        with self.assertRaisesRegex(ValueError, "DynamicTickArray"):
            self.quote(accounts=accounts)

    def test_uninitialized_tick_array_pdas_are_treated_as_empty_arrays(self):
        accounts = list(self.accounts)
        accounts[6] = None
        accounts[7] = None
        accounts[8] = None
        result = self.quote(accounts=accounts)
        self.assertGreater(result["out_amount"], 0)
        self.assertEqual(result["loaded_tick_arrays"], 0)


if __name__ == "__main__":
    unittest.main()
