import base64
import struct
import unittest
from unittest.mock import patch

import raydium_amm_v4_builder as builder
import solana_onchain as chain


def key(value):
    return chain._b58encode(bytes([value]) * 32)


def token_account(mint, authority, amount=1000):
    raw = bytearray(165)
    raw[:32] = chain._b58decode(mint)
    raw[32:64] = chain._b58decode(authority)
    raw[64:72] = amount.to_bytes(8, "little")
    raw[108] = 1
    return {"owner": chain.TOKEN_PROGRAM, "data": bytes(raw)}


class RaydiumAmmV4BuilderTests(unittest.TestCase):
    def setUp(self):
        self.wallet = key(1)
        self.pool = key(2)
        self.mint_base = key(3)
        self.mint_quote = key(4)
        self.vault_base = key(5)
        self.vault_quote = key(6)
        self.user_in = key(7)
        self.user_out = key(8)
        self.authority = key(9)
        self.blockhash = key(10)
        self.quote = {
            "protocol": "raydium_amm_v4",
            "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT",
            "pool_id": self.pool,
            "input_mint": self.mint_base,
            "output_mint": self.mint_quote,
            "input_amount": 100,
            "out_amount": 100,
            "minimum_out_amount": 80,
            "slot": 500,
            "base_decimals": 9,
            "quote_decimals": 6,
        }
        raw = bytearray(752)
        raw[0:8] = (6).to_bytes(8, "little")
        raw[176:184] = (25).to_bytes(8, "little")
        raw[184:192] = (10_000).to_bytes(8, "little")
        raw[192:200] = (0).to_bytes(8, "little")
        raw[200:208] = (0).to_bytes(8, "little")
        raw[224:232] = (1).to_bytes(8, "little")
        raw[336:368] = chain._b58decode(self.vault_base)
        raw[368:400] = chain._b58decode(self.vault_quote)
        raw[400:432] = chain._b58decode(self.mint_base)
        raw[432:464] = chain._b58decode(self.mint_quote)
        self.pool_data = bytes(raw)
        mint_base_data = bytearray(82)
        mint_base_data[44] = 9
        mint_base_data[45] = 1
        mint_quote_data = bytearray(82)
        mint_quote_data[44] = 6
        mint_quote_data[45] = 1
        self.account_map = {
            self.pool: {"owner": builder.PROGRAM_ID, "data": self.pool_data},
            self.vault_base: token_account(self.mint_base, self.authority),
            self.vault_quote: token_account(self.mint_quote, self.authority),
            self.mint_base: {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint_base_data)},
            self.mint_quote: {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint_quote_data)},
            self.user_in: token_account(self.mint_base, self.wallet, 1000),
            self.user_out: token_account(self.mint_quote, self.wallet, 0),
        }

    def call_builder(self, quote=None, account_map=None, slot=503):
        account_map = account_map or self.account_map
        with (
            patch.object(chain, "quote_raydium_amm_v4_pool", return_value=self.quote),
            patch.object(chain, "_account", return_value=self.account_map[self.pool]),
            patch.object(chain, "_find_program_address",
                         return_value=(self.authority, 254)),
            patch.object(chain, "_multiple_accounts_with_context",
                         side_effect=lambda keys: ([account_map.get(k) for k in keys], slot)),
            patch.object(chain, "_rpc", return_value={"value": {
                "blockhash": self.blockhash, "lastValidBlockHeight": 123456
            }}),
        ):
            return builder.build_raydium_amm_v4_swap(
                quote or self.quote, self.wallet, self.user_in, self.user_out
            )

    def test_builds_unsigned_17_account_swap_base_in(self):
        result = self.call_builder()
        data = base64.b64decode(result["instruction_data_base64"])
        transaction = base64.b64decode(result["transaction"])
        self.assertEqual(data[0], builder.SWAP_BASE_IN_TAG)
        self.assertEqual(struct.unpack("<QQ", data[1:]), (100, 80))
        self.assertEqual(len(result["instruction_accounts"]), 17)
        self.assertEqual(result["instruction_accounts"][0], chain.TOKEN_PROGRAM)
        self.assertEqual(result["instruction_accounts"][1], self.pool)
        self.assertEqual(result["instruction_accounts"][2], self.authority)
        self.assertEqual(result["instruction_accounts"][4], self.vault_base)
        self.assertEqual(result["instruction_accounts"][5], self.vault_quote)
        self.assertEqual(result["instruction_accounts"][14:],
                         [self.user_in, self.user_out, self.wallet])
        self.assertEqual(transaction[0], 1)
        self.assertEqual(transaction[1:65], bytes(64))
        self.assertEqual(result["signature_status"], "UNSIGNED_EXTERNAL_WALLET_REQUIRED")
        self.assertEqual(result["execution"], "LOCKED")
        self.assertFalse(result["live_trading_ready"])

    def test_rejects_wrong_protocol_and_unconfirmed_quote(self):
        with self.assertRaisesRegex(ValueError, "validated Raydium AMM v4"):
            self.call_builder(dict(self.quote, protocol="raydium_cpmm"))
        with self.assertRaisesRegex(ValueError, "confirmed Raydium AMM v4"):
            self.call_builder(dict(self.quote, status="STALE"))

    def test_rejects_token2022_mints(self):
        accounts = dict(self.account_map)
        accounts[self.mint_base] = {
            "owner": chain.TOKEN_2022_PROGRAM, "data": bytes(166)
        }
        with self.assertRaisesRegex(ValueError, "legacy SPL Token mints"):
            self.call_builder(account_map=accounts)

    def test_rejects_wrong_vault_authority(self):
        accounts = dict(self.account_map)
        accounts[self.vault_base] = token_account(self.mint_base, key(44))
        with self.assertRaisesRegex(ValueError, "vault authority"):
            self.call_builder(account_map=accounts)

    def test_rejects_insufficient_wallet_balance(self):
        accounts = dict(self.account_map)
        accounts[self.user_in] = token_account(self.mint_base, self.wallet, 99)
        with self.assertRaisesRegex(ValueError, "insufficient balance"):
            self.call_builder(account_map=accounts)

    def test_rejects_stale_transaction_snapshot(self):
        with self.assertRaisesRegex(ValueError, "stale relative"):
            self.call_builder(slot=700)

    def test_simulation_is_read_only_and_reports_program_error(self):
        transaction = base64.b64encode(bytes(120)).decode("ascii")
        with patch.object(chain, "_rpc", return_value={"value": {
            "err": None, "unitsConsumed": 55_000, "logs": ["ok"]
        }}):
            result = builder.simulate_raydium_amm_v4_transaction(transaction)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["units_consumed"], 55_000)
        self.assertEqual(result["signing"], "NOT_PERFORMED")
        self.assertEqual(result["broadcast"], "DISABLED")
        with patch.object(chain, "_rpc", return_value={"value": {
            "err": {"InstructionError": [0, "Custom"]}, "logs": ["failed"]
        }}):
            rejected = builder.simulate_raydium_amm_v4_transaction(transaction)
        self.assertEqual(rejected["status"], "REJECT")


if __name__ == "__main__":
    unittest.main()
