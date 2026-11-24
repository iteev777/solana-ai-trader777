import base64
import struct
import unittest
from unittest.mock import patch

import moonit_builder as builder
import solana_onchain as chain


def key(value):
    return chain._b58encode(bytes([value]) * 32)


def token_account(mint, owner, amount=1000):
    raw = bytearray(165)
    raw[:32] = chain._b58decode(mint)
    raw[32:64] = chain._b58decode(owner)
    raw[64:72] = amount.to_bytes(8, "little")
    raw[108] = 1
    return {"owner": chain.TOKEN_PROGRAM, "data": bytes(raw)}


class MoonitBuilderTests(unittest.TestCase):
    def setUp(self):
        self.wallet = key(1)
        self.pool = key(2)
        self.mint = key(3)
        self.config = key(4)
        self.curve_ata = key(5)
        self.dex_fee = key(6)
        self.helio_fee = key(7)
        self.user_in = key(8)
        self.user_out = key(9)
        self.blockhash = key(10)
        self.total_supply = 1_000_000_000_000_000_000
        self.curve_amount = 900_000_000_000_000_000
        self.fee_bps = 100
        self.quote = {
            "protocol": "moonit_amm",
            "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT",
            "pool_id": self.pool,
            "input_mint": chain.SOL_MINT,
            "output_mint": self.mint,
            "input_amount": 100,
            "out_amount": 100,
            "minimum_out_amount": 80,
            "slot": 500,
            "slippage_bps": 100,
            "curve_amount": self.curve_amount,
            "total_supply": self.total_supply,
            "fee_bps": self.fee_bps,
        }
        curve = bytearray(125)
        curve[:8] = builder.CURVE_DISCRIMINATOR
        curve[8:16] = self.total_supply.to_bytes(8, "little")
        curve[16:24] = self.curve_amount.to_bytes(8, "little")
        curve[24:56] = chain._b58decode(self.mint)
        curve[56] = 9
        curve[57] = 0
        curve[58] = 1
        self.curve_data = bytes(curve)

        config = bytearray(170)
        config[:8] = builder.CONFIG_DISCRIMINATOR
        config[104:136] = chain._b58decode(self.helio_fee)
        config[136:168] = chain._b58decode(self.dex_fee)
        config[168:170] = self.fee_bps.to_bytes(2, "little")
        self.config_data = bytes(config)

        mint = bytearray(82)
        mint[44] = 9
        mint[45] = 1
        self.account_map = {
            self.pool: {"owner": builder.PROGRAM_ID, "data": self.curve_data},
            self.mint: {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint)},
            self.config: {"owner": builder.PROGRAM_ID, "data": self.config_data},
            self.curve_ata: token_account(self.mint, self.pool, self.curve_amount),
            self.user_in: token_account(self.mint, self.wallet, 1000),
            self.user_out: token_account(self.mint, self.wallet, 0),
        }

    def call_builder(self, quote=None, account_map=None, slot=503):
        account_map = account_map or self.account_map
        def fresh_quote(pool, input_mint, amount, slippage):
            result = dict(self.quote)
            result["input_mint"] = input_mint
            result["output_mint"] = self.mint if input_mint == chain.SOL_MINT else chain.SOL_MINT
            result["input_amount"] = amount
            result["slippage_bps"] = slippage
            return result
        with (
            patch.object(chain, "quote_moonit_pool", side_effect=fresh_quote),
            patch.object(chain, "_account", return_value=self.account_map[self.pool]),
            patch.object(chain, "_find_program_address",
                         side_effect=[(self.config, 251), (self.curve_ata, 255),
                                      (self.user_out if (quote or self.quote).get("input_mint") == chain.SOL_MINT else self.user_in, 253)]),
            patch.object(chain, "_multiple_accounts_with_context",
                         side_effect=lambda keys: ([account_map.get(k) for k in keys], slot)),
            patch.object(chain, "_rpc", return_value={"value": {
                "blockhash": self.blockhash, "lastValidBlockHeight": 123456
            }}),
        ):
            selected_quote = quote or self.quote
            return builder.build_moonit_swap(
                selected_quote, self.wallet, self.user_in, self.user_out
            )

    def test_builds_native_sol_buy_with_verified_11_account_order(self):
        decoded = chain._decode_pool("moonit_amm", self.curve_data)
        self.assertEqual(decoded["base_mint"], self.mint)
        self.assertEqual(self.quote["output_mint"], self.mint)
        result = self.call_builder()
        data = base64.b64decode(result["instruction_data_base64"])
        transaction = base64.b64decode(result["transaction"])
        self.assertEqual(data[:8], builder.BUY_DISCRIMINATOR)
        self.assertEqual(struct.unpack("<QQBQ", data[8:]), (80, 100, 0, 0))
        self.assertEqual(len(result["instruction_accounts"]), 11)
        self.assertEqual(result["instruction_accounts"], [
            self.wallet, self.user_out, self.pool, self.curve_ata,
            self.dex_fee, self.helio_fee, self.mint, self.config,
            chain.TOKEN_PROGRAM, builder.ASSOCIATED_TOKEN_PROGRAM_ID,
            builder.SYSTEM_PROGRAM_ID,
        ])
        self.assertEqual(transaction[0], 1)
        self.assertEqual(transaction[1:65], bytes(64))
        self.assertEqual(result["execution"], "LOCKED")
        self.assertFalse(result["live_trading_ready"])

    def test_builds_native_sol_sell_with_exact_token_input(self):
        quote = dict(self.quote, input_mint=self.mint, output_mint=chain.SOL_MINT)
        result = self.call_builder(quote=quote)
        data = base64.b64decode(result["instruction_data_base64"])
        self.assertEqual(data[:8], builder.SELL_DISCRIMINATOR)
        self.assertEqual(struct.unpack("<QQBQ", data[8:]), (100, 80, 0, 0))
        self.assertEqual(result["instruction_accounts"][1], self.user_in)

    def test_rejects_non_sol_pair_and_unconfirmed_quote(self):
        with self.assertRaisesRegex(ValueError, "validated Moonit"):
            self.call_builder(dict(self.quote, protocol="raydium_cpmm"))
        with self.assertRaisesRegex(ValueError, "confirmed Moonit"):
            self.call_builder(dict(self.quote, status="STALE"))
        with self.assertRaisesRegex(ValueError, "Fresh Moonit quote"):
            self.call_builder(dict(self.quote, output_mint=key(55)))

    def test_rejects_noncanonical_wallet_ata(self):
        wrong_ata = key(55)
        with self.assertRaisesRegex(ValueError, "canonical associated token account"):
            with (
                patch.object(chain, "quote_moonit_pool", side_effect=lambda pool, mint, amount, slip: dict(
                    self.quote, input_mint=mint, output_mint=self.mint,
                    input_amount=amount, slippage_bps=slip
                )),
                patch.object(chain, "_account", return_value=self.account_map[self.pool]),
                patch.object(chain, "_find_program_address",
                             side_effect=[(self.config, 251), (self.curve_ata, 255), (wrong_ata, 253)]),
            ):
                builder.build_moonit_swap(
                    self.quote, self.wallet, self.user_in, self.user_out
                )

    def test_rejects_token2022_curve_mint(self):
        accounts = dict(self.account_map)
        accounts[self.mint] = dict(accounts[self.mint], owner=chain.TOKEN_2022_PROGRAM)
        with self.assertRaisesRegex(ValueError, "legacy SPL Token mints only"):
            self.call_builder(account_map=accounts)

    def test_rejects_curve_token_balance_mismatch(self):
        accounts = dict(self.account_map)
        accounts[self.curve_ata] = token_account(self.mint, self.pool, self.curve_amount - 1)
        with self.assertRaisesRegex(ValueError, "does not match curve state"):
            self.call_builder(account_map=accounts)

    def test_rejects_wrong_user_owner_and_insufficient_sell_balance(self):
        accounts = dict(self.account_map)
        accounts[self.user_in] = token_account(self.mint, key(55), 1000)
        quote = dict(self.quote, input_mint=self.mint, output_mint=chain.SOL_MINT)
        with self.assertRaisesRegex(ValueError, "authority does not match"):
            self.call_builder(quote=quote, account_map=accounts)
        accounts = dict(self.account_map)
        accounts[self.user_in] = token_account(self.mint, self.wallet, 99)
        with self.assertRaisesRegex(ValueError, "insufficient token balance"):
            self.call_builder(quote=quote, account_map=accounts)

    def test_rejects_fee_config_change_and_stale_snapshot(self):
        changed = dict(self.account_map)
        raw = bytearray(self.config_data)
        raw[168:170] = (200).to_bytes(2, "little")
        changed[self.config] = {"owner": builder.PROGRAM_ID, "data": bytes(raw)}
        with self.assertRaisesRegex(ValueError, "fee configuration changed"):
            self.call_builder(account_map=changed)
        with self.assertRaisesRegex(ValueError, "stale relative"):
            self.call_builder(slot=700)

    def test_simulation_is_read_only(self):
        transaction = base64.b64encode(bytes(120)).decode("ascii")
        with patch.object(chain, "_rpc", return_value={"value": {
            "err": None, "unitsConsumed": 30_000, "logs": ["ok"]
        }}):
            result = builder.simulate_moonit_transaction(transaction)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["signing"], "NOT_PERFORMED")
        self.assertEqual(result["broadcast"], "DISABLED")


if __name__ == "__main__":
    unittest.main()
