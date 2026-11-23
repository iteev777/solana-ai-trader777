import unittest
from unittest.mock import patch

import clmm_quote_math as mathx
import solana_onchain as chain
from orca_whirlpool_quote import quote_orca_whirlpool_pool


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
        state[45:47] = (400).to_bytes(2, "little")
        state[49:65] = (1_000_000_000).to_bytes(16, "little")
        state[65:81] = (mathx.sqrt_price_at_tick(10) + 1000).to_bytes(16, "little")
        state[81:85] = (10).to_bytes(4, "little", signed=True)
        state[101:133] = chain._b58decode(self.mint_a)
        state[133:165] = chain._b58decode(self.vault_a)
        state[181:213] = chain._b58decode(self.mint_b)
        state[213:245] = chain._b58decode(self.vault_b)
        self.state = bytes(state)

        def token_account(mint, amount):
            raw = bytearray(165)
            raw[:32] = chain._b58decode(mint)
            raw[64:72] = amount.to_bytes(8, "little")
            return {"owner": chain.TOKEN_PROGRAM, "data": bytes(raw)}

        self.vault_a_account = token_account(self.mint_a, 10**12)
        self.vault_b_account = token_account(self.mint_b, 10**12)
        self.mint_a_account = {"owner": chain.TOKEN_PROGRAM, "data": bytes(82)}
        self.mint_b_account = {"owner": chain.TOKEN_PROGRAM, "data": bytes(82)}

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

    def test_token_2022_mint_is_rejected(self):
        accounts = list(self.accounts)
        accounts[4] = dict(accounts[4], owner=chain.TOKEN_2022_PROGRAM)
        with self.assertRaisesRegex(ValueError, "Token-2022"):
            self.quote(accounts=accounts)

    def test_dynamic_tick_array_layout_is_rejected(self):
        accounts = list(self.accounts)
        accounts[6] = {
            "owner": chain.PROGRAMS["orca_whirlpool"],
            "data": bytes(8548),
        }
        with self.assertRaisesRegex(ValueError, "DynamicTickArray"):
            self.quote(accounts=accounts)

    def test_missing_current_tick_array_is_rejected(self):
        accounts = list(self.accounts)
        accounts[6] = None
        with self.assertRaisesRegex(ValueError, "current tick array"):
            self.quote(accounts=accounts)


if __name__ == "__main__":
    unittest.main()
