import base64
import struct
import unittest
from unittest.mock import patch

import fluxbeam_builder as builder
import solana_onchain as chain
import token2022_inspector as token2022


def key(value):
    return chain._b58encode(bytes([value]) * 32)


def mint_account(decimals=6, program=None, extensions=b""):
    raw = bytearray(166 if extensions else 82)
    raw[44] = decimals
    raw[45] = 1
    if extensions:
        raw[165] = token2022.MINT_ACCOUNT_TYPE
        raw.extend(extensions)
    return {"owner": program or chain.TOKEN_PROGRAM, "data": bytes(raw)}


def token_account(mint, authority, amount=1000, program=None, extended=False):
    raw = bytearray(166 if extended else 165)
    raw[:32] = chain._b58decode(mint)
    raw[32:64] = chain._b58decode(authority)
    raw[64:72] = amount.to_bytes(8, "little")
    raw[108] = 1
    if extended:
        raw[165] = token2022.TOKEN_ACCOUNT_TYPE
    return {"owner": program or chain.TOKEN_PROGRAM, "data": bytes(raw)}


def tlv(extension_type, value):
    return (extension_type.to_bytes(2, "little")
            + len(value).to_bytes(2, "little") + value)


class FluxbeamBuilderTests(unittest.TestCase):
    def setUp(self):
        self.wallet = key(1)
        self.pool = key(2)
        self.mint_a = key(3)
        self.mint_b = key(4)
        self.vault_a = key(5)
        self.vault_b = key(6)
        self.pool_mint = key(7)
        self.fee_account = key(8)
        self.user_in = key(9)
        self.user_out = key(10)
        self.authority = key(11)
        self.blockhash = key(12)
        self.quote = {
            "protocol": "fluxbeam_amm",
            "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT",
            "pool_id": self.pool,
            "input_mint": self.mint_a,
            "output_mint": self.mint_b,
            "input_amount": 100,
            "out_amount": 100,
            "minimum_out_amount": 80,
            "slot": 500,
            "base_decimals": 9,
            "quote_decimals": 6,
        }
        raw = bytearray(324)
        raw[0] = 1
        raw[1] = 1
        raw[2] = 254
        raw[35:67] = chain._b58decode(self.vault_a)
        raw[67:99] = chain._b58decode(self.vault_b)
        raw[99:131] = chain._b58decode(self.pool_mint)
        raw[131:163] = chain._b58decode(self.mint_a)
        raw[163:195] = chain._b58decode(self.mint_b)
        raw[195:227] = chain._b58decode(self.fee_account)
        raw[227:235] = (20).to_bytes(8, "little")
        raw[235:243] = (10_000).to_bytes(8, "little")
        raw[243:251] = (99).to_bytes(8, "little")
        raw[251:259] = (10_000).to_bytes(8, "little")
        raw[291] = 0
        self.pool_data = bytes(raw)
        self.account_map = {
            self.pool: {"owner": builder.PROGRAM_ID, "data": self.pool_data},
            self.vault_a: token_account(self.mint_a, self.authority),
            self.vault_b: token_account(self.mint_b, self.authority),
            self.mint_a: mint_account(9),
            self.mint_b: mint_account(6),
            self.pool_mint: mint_account(6),
            self.fee_account: token_account(self.pool_mint, key(55)),
            self.user_in: token_account(self.mint_a, self.wallet, 1000),
            self.user_out: token_account(self.mint_b, self.wallet, 0),
        }

    def call_builder(self, quote=None, account_map=None, slot=503):
        account_map = account_map or self.account_map
        def fresh_quote(pool, input_mint, amount, slippage):
            result = dict(self.quote)
            result["input_mint"] = input_mint
            result["output_mint"] = self.mint_b if input_mint == self.mint_a else self.mint_a
            result["input_amount"] = amount
            return result

        with (
            patch.object(chain, "quote_fluxbeam_pool", side_effect=fresh_quote),
            patch.object(chain, "_account", return_value=self.account_map[self.pool]),
            patch.object(chain, "_find_program_address",
                         return_value=(self.authority, 254)),
            patch.object(chain, "_multiple_accounts_with_context",
                         side_effect=lambda keys: ([account_map.get(k) for k in keys], slot)),
            patch.object(chain, "_rpc", return_value={"value": {
                "blockhash": self.blockhash, "lastValidBlockHeight": 123456
            }}),
        ):
            return builder.build_fluxbeam_swap(
                quote or self.quote, self.wallet, self.user_in, self.user_out
            )

    def test_builds_unsigned_swap_v1_with_swap_direction_account_order(self):
        result = self.call_builder()
        data = base64.b64decode(result["instruction_data_base64"])
        transaction = base64.b64decode(result["transaction"])
        self.assertEqual(data[0], builder.SWAP_TAG)
        self.assertEqual(struct.unpack("<QQ", data[1:]), (100, 80))
        self.assertEqual(len(result["instruction_accounts"]), 14)
        self.assertEqual(result["instruction_accounts"], [
            self.pool, self.authority, self.wallet, self.user_in,
            self.vault_a, self.vault_b, self.user_out, self.pool_mint,
            self.fee_account, self.mint_a, self.mint_b,
            chain.TOKEN_PROGRAM, chain.TOKEN_PROGRAM, chain.TOKEN_PROGRAM,
        ])
        self.assertEqual(transaction[0], 1)
        self.assertEqual(transaction[1:65], bytes(64))
        self.assertEqual(result["execution"], "LOCKED")
        self.assertFalse(result["live_trading_ready"])

    def test_reverse_direction_reorders_every_side_specific_account(self):
        reverse = dict(self.quote, input_mint=self.mint_b, output_mint=self.mint_a)
        accounts_map = dict(self.account_map)
        accounts_map[self.user_in] = token_account(self.mint_b, self.wallet, 1000)
        accounts_map[self.user_out] = token_account(self.mint_a, self.wallet, 0)
        result = self.call_builder(quote=reverse, account_map=accounts_map)
        accounts = result["instruction_accounts"]
        self.assertEqual(accounts[3], self.user_in)
        self.assertEqual(accounts[4:7], [self.vault_b, self.vault_a, self.user_out])
        self.assertEqual(accounts[9:11], [self.mint_b, self.mint_a])

    def test_supports_zero_fee_token2022_mint_and_account(self):
        accounts = dict(self.account_map)
        accounts[self.mint_b] = mint_account(
            6, chain.TOKEN_2022_PROGRAM
        )
        accounts[self.vault_b] = token_account(
            self.mint_b, self.authority, program=chain.TOKEN_2022_PROGRAM,
            extended=True,
        )
        accounts[self.user_out] = token_account(
            self.mint_b, self.wallet, 0, program=chain.TOKEN_2022_PROGRAM,
            extended=True,
        )
        result = self.call_builder(account_map=accounts)
        self.assertEqual(result["instruction_accounts"][12], chain.TOKEN_2022_PROGRAM)

    def test_refuses_token2022_transfer_hook_mint(self):
        accounts = dict(self.account_map)
        accounts[self.mint_b] = mint_account(
            6, chain.TOKEN_2022_PROGRAM, tlv(14, bytes(64))
        )
        accounts[self.vault_b] = token_account(
            self.mint_b, self.authority, program=chain.TOKEN_2022_PROGRAM,
            extended=True,
        )
        with self.assertRaisesRegex(ValueError, "unsupported by native swap builders"):
            self.call_builder(account_map=accounts)

    def test_refuses_nonzero_token2022_transfer_fee(self):
        config = bytearray(108)
        config[88:90] = (100).to_bytes(2, "little")
        accounts = dict(self.account_map)
        accounts[self.mint_b] = mint_account(
            6, chain.TOKEN_2022_PROGRAM, tlv(1, bytes(config))
        )
        accounts[self.vault_b] = token_account(
            self.mint_b, self.authority, program=chain.TOKEN_2022_PROGRAM,
            extended=True,
        )
        with self.assertRaisesRegex(ValueError, "transfer fees must be zero"):
            self.call_builder(account_map=accounts)

    def test_rejects_wrong_fee_account_mint(self):
        accounts = dict(self.account_map)
        accounts[self.fee_account] = token_account(key(45), key(55))
        with self.assertRaisesRegex(ValueError, "LP fee account token account mint"):
            self.call_builder(account_map=accounts)

    def test_rejects_wrong_vault_authority_and_insufficient_balance(self):
        accounts = dict(self.account_map)
        accounts[self.vault_a] = token_account(self.mint_a, key(45))
        with self.assertRaisesRegex(ValueError, "vault authority"):
            self.call_builder(account_map=accounts)
        accounts = dict(self.account_map)
        accounts[self.user_in] = token_account(self.mint_a, self.wallet, 99)
        with self.assertRaisesRegex(ValueError, "insufficient balance"):
            self.call_builder(account_map=accounts)

    def test_rejects_stale_snapshot(self):
        with self.assertRaisesRegex(ValueError, "stale relative"):
            self.call_builder(slot=700)

    def test_simulation_is_unsigned_and_never_broadcasts(self):
        transaction = base64.b64encode(bytes(120)).decode("ascii")
        with patch.object(chain, "_rpc", return_value={"value": {
            "err": None, "unitsConsumed": 70_000, "logs": ["ok"]
        }}):
            result = builder.simulate_fluxbeam_transaction(transaction)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["signing"], "NOT_PERFORMED")
        self.assertEqual(result["broadcast"], "DISABLED")


if __name__ == "__main__":
    unittest.main()
