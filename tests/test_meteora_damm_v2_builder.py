import base64
import struct
import unittest
from unittest.mock import patch

import meteora_damm_quote as damm_quote
import meteora_damm_v2_builder as builder
import solana_onchain as chain


def key(value):
    return chain._b58encode(bytes([value]) * 32)


def token_account(mint, authority, amount=1_000_000):
    raw = bytearray(165)
    raw[:32] = chain._b58decode(mint)
    raw[32:64] = chain._b58decode(authority)
    raw[64:72] = amount.to_bytes(8, "little")
    raw[108] = 1
    return {"owner": chain.TOKEN_PROGRAM, "data": bytes(raw)}


class MeteoraDammV2BuilderTests(unittest.TestCase):
    def setUp(self):
        self.pool = key(41)
        self.mint_a = key(42)
        self.mint_b = key(43)
        self.vault_a = key(44)
        self.vault_b = key(45)
        self.wallet = key(46)
        self.user_in = chain._find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_PROGRAM),
             chain._b58decode(self.mint_a)], builder.ASSOCIATED_TOKEN_PROGRAM_ID
        )[0]
        self.user_out = chain._find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_PROGRAM),
             chain._b58decode(self.mint_b)], builder.ASSOCIATED_TOKEN_PROGRAM_ID
        )[0]
        self.event_authority = chain._find_program_address(
            [builder.EVENT_AUTHORITY_SEED], builder.PROGRAM_ID
        )[0]
        self.blockhash = key(47)
        raw = bytearray(1112)
        raw[:8] = chain._discriminator("Pool")
        raw[8:16] = (3_000_000).to_bytes(8, "little")
        raw[16] = 0
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
        raw[480] = 1
        raw[481] = 0
        raw[484] = 0
        raw[486] = 1
        self.pool_data = bytes(raw)
        self.quote = {
            "protocol": "meteora_damm_v2",
            "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_POOL_SNAPSHOT",
            "pool_id": self.pool,
            "input_mint": self.mint_a,
            "output_mint": self.mint_b,
            "input_amount": 1_000_000,
            "out_amount": 900_000,
            "minimum_out_amount": 891_000,
            "slot": 765432,
            "slippage_bps": 100,
            "sqrt_price_before_x64": 1 << 64,
            "sqrt_price_after_x64": (1 << 64) - 1,
            "liquidity": 1 << 100,
            "fee_version": 1,
            "pool_status": 0,
            "collect_fee_mode": 0,
            "fee_rate_numerator": 3_000_000,
            "fee_cap_numerator": 990_000_000,
        }
        mint_a = bytearray(82)
        mint_a[44] = 6
        mint_a[45] = 1
        mint_b = bytearray(82)
        mint_b[44] = 9
        mint_b[45] = 1
        self.account_map = {
            self.pool: {"owner": builder.PROGRAM_ID, "data": self.pool_data},
            self.vault_a: token_account(self.mint_a, builder.POOL_AUTHORITY),
            self.vault_b: token_account(self.mint_b, builder.POOL_AUTHORITY),
            self.mint_a: {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint_a)},
            self.mint_b: {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint_b)},
            self.user_in: token_account(self.mint_a, self.wallet),
            self.user_out: token_account(self.mint_b, self.wallet),
        }

    def build(self, quote=None, accounts=None, slot=765434):
        accounts = accounts or self.account_map
        def fresh(pool, input_mint, amount, slippage, output_mint=None):
            result = dict(self.quote)
            result.update({
                "input_mint": input_mint,
                "output_mint": self.mint_b if input_mint == self.mint_a else self.mint_a,
                "input_amount": amount,
                "slippage_bps": slippage,
            })
            return result
        selected = quote or self.quote
        input_account = self.user_in if selected["input_mint"] == self.mint_a else self.user_out
        output_account = self.user_out if selected["input_mint"] == self.mint_a else self.user_in
        with (
            patch.object(damm_quote, "quote_meteora_damm_pool", side_effect=fresh),
            patch.object(chain, "_account", return_value=self.account_map[self.pool]),
            patch.object(chain, "_multiple_accounts_with_context",
                         side_effect=lambda keys: ([accounts.get(k) for k in keys], slot)),
            patch.object(chain, "_rpc", return_value={"value": {
                "blockhash": self.blockhash, "lastValidBlockHeight": 123456
            }}),
        ):
            return builder.build_meteora_damm_v2_swap(
                selected, self.wallet, input_account, output_account
            )

    def test_builds_swap_with_pool_order_14_account_shape(self):
        result = self.build()
        data = base64.b64decode(result["instruction_data_base64"])
        tx = base64.b64decode(result["transaction"])
        self.assertEqual(data[:8], builder.SWAP_DISCRIMINATOR)
        self.assertEqual(struct.unpack("<QQ", data[8:]), (1_000_000, 891_000))
        self.assertEqual(result["instruction_accounts"], [
            builder.POOL_AUTHORITY, self.pool, self.user_in, self.user_out,
            self.vault_a, self.vault_b, self.mint_a, self.mint_b,
            self.wallet, chain.TOKEN_PROGRAM, chain.TOKEN_PROGRAM,
            builder.PROGRAM_ID, self.event_authority, builder.PROGRAM_ID,
        ])
        self.assertEqual(tx[0], 1)
        self.assertEqual(tx[1:65], bytes(64))
        self.assertEqual(result["execution"], "LOCKED")
        self.assertFalse(result["live_trading_ready"])

    def test_reverse_direction_keeps_vaults_and_mints_in_pool_order(self):
        quote = dict(self.quote, input_mint=self.mint_b, output_mint=self.mint_a)
        result = self.build(quote)
        accounts = result["instruction_accounts"]
        self.assertEqual(accounts[2:4], [self.user_out, self.user_in])
        self.assertEqual(accounts[4:8], [self.vault_a, self.vault_b, self.mint_a, self.mint_b])

    def test_rejects_token2022_mint_and_wrong_vault_authority(self):
        accounts = dict(self.account_map)
        accounts[self.mint_b] = dict(accounts[self.mint_b], owner=chain.TOKEN_2022_PROGRAM)
        with self.assertRaisesRegex(ValueError, "legacy SPL Token mints"):
            self.build(accounts=accounts)
        accounts = dict(self.account_map)
        accounts[self.vault_a] = token_account(self.mint_a, key(99))
        with self.assertRaisesRegex(ValueError, "pool authority"):
            self.build(accounts=accounts)

    def test_rejects_stale_snapshot_and_changed_fee_state(self):
        with self.assertRaisesRegex(ValueError, "stale relative"):
            self.build(slot=765500)
        changed = dict(self.quote, fee_rate_numerator=4_000_000)
        with self.assertRaisesRegex(ValueError, "fee schedule changed"):
            self.build(quote=changed)

    def test_simulation_is_unsigned_and_never_broadcasts(self):
        tx = base64.b64encode(bytes(120)).decode("ascii")
        with patch.object(chain, "_rpc", return_value={"value": {
            "err": None, "unitsConsumed": 90_000, "logs": ["ok"]
        }}):
            result = builder.simulate_meteora_damm_v2_transaction(tx)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["signing"], "NOT_PERFORMED")
        self.assertEqual(result["broadcast"], "DISABLED")


if __name__ == "__main__":
    unittest.main()
