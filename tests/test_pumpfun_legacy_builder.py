import base64
import struct
import unittest
from unittest.mock import patch

import pumpfun_legacy_builder as builder
import solana_onchain as chain


class PumpFunLegacyBuilderSmokeTests(unittest.TestCase):
    def test_builder_exports_registered_entrypoints(self):
        self.assertTrue(callable(builder.build_pumpfun_legacy_swap))
        self.assertTrue(callable(builder.simulate_pumpfun_legacy_transaction))

    def test_unsigned_simulation_never_signs_or_broadcasts(self):
        transaction = base64.b64encode(bytes(120)).decode("ascii")
        with patch.object(chain, "_rpc", return_value={"value": {
            "err": None, "unitsConsumed": 80_000, "logs": ["pump.fun ok"]
        }}):
            result = builder.simulate_pumpfun_legacy_transaction(transaction)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["signature_verification"], "DISABLED_UNSIGNED_SIMULATION")
        self.assertEqual(result["signing"], "NOT_PERFORMED")
        self.assertEqual(result["broadcast"], "DISABLED")
        self.assertEqual(result["execution"], "LOCKED")

    def test_rejects_malformed_unsigned_transaction(self):
        with self.assertRaisesRegex(ValueError, "base64"):
            builder.simulate_pumpfun_legacy_transaction("not base64")


if __name__ == "__main__":
    unittest.main()


def _pump_key(value):
    return chain._b58encode(bytes([value]) * 32)


def _pump_token_account(mint, owner, amount, token_program=chain.TOKEN_PROGRAM,
                        transfer_fee=False, immutable_owner=False):
    raw = bytearray(182 if immutable_owner else (178 if transfer_fee else 165))
    raw[:32] = chain._b58decode(mint)
    raw[32:64] = chain._b58decode(owner)
    raw[64:72] = amount.to_bytes(8, "little")
    raw[108] = 1
    if transfer_fee:
        raw[165] = 2
        raw[166:168] = (2).to_bytes(2, "little")
        raw[168:170] = (8).to_bytes(2, "little")
    if immutable_owner:
        raw[178:180] = (7).to_bytes(2, "little")
        raw[180:182] = (0).to_bytes(2, "little")
    return {"owner": token_program, "data": bytes(raw)}


def _pump_token2022_mint():
    from token2022_inspector import MINT_ACCOUNT_TYPE
    raw = bytearray(166)
    raw[44] = 6
    raw[45] = 1
    raw[165] = MINT_ACCOUNT_TYPE
    config = bytearray(108)
    config[80:88] = (10_000).to_bytes(8, "little")
    config[88:90] = (100).to_bytes(2, "little")
    config[98:106] = (10_000).to_bytes(8, "little")
    config[106:108] = (100).to_bytes(2, "little")
    raw.extend((1).to_bytes(2, "little") + (108).to_bytes(2, "little") + config)
    return {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw)}


def _pump_fee_config():
    fee = bytearray(65)
    fee[:8] = chain.PUMPSWAP_FEE_CONFIG_DISCRIMINATOR
    fee[8] = 1
    fee[41:49] = (0).to_bytes(8, "little")
    fee[49:57] = (100).to_bytes(8, "little")
    fee[57:65] = (30).to_bytes(8, "little")
    fee[65:69] = (2).to_bytes(4, "little")
    cursor = 69
    for threshold, rates in ((0, (0, 95, 30)), (3_000_000, (0, 20, 20))):
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
    return bytes(fee)


class PumpFunLegacyBuilderIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.wallet = _pump_key(1)
        self.mint = _pump_key(2)
        self.creator = _pump_key(7)
        self.fee_recipient = _pump_key(40)
        self.buyback_one = _pump_key(41)
        self.buyback_two = _pump_key(42)
        self.pool = chain._find_program_address(
            [builder.BONDING_CURVE_SEED, chain._b58decode(self.mint)],
            builder.PROGRAM_ID,
        )[0]
        self.global_address = chain._find_program_address(
            [builder.GLOBAL_SEED], builder.PROGRAM_ID
        )[0]
        self.fee_config_address = chain._find_program_address(
            [builder.FEE_CONFIG_SEED, chain._b58decode(builder.PROGRAM_ID)],
            builder.FEE_PROGRAM_ID,
        )[0]
        self.curve_ata = chain._find_program_address(
            [chain._b58decode(self.pool), chain._b58decode(chain.TOKEN_PROGRAM),
             chain._b58decode(self.mint)], builder.ASSOCIATED_TOKEN_PROGRAM_ID
        )[0]
        self.user_ata = chain._find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_PROGRAM),
             chain._b58decode(self.mint)], builder.ASSOCIATED_TOKEN_PROGRAM_ID
        )[0]
        self.curve_v2 = chain._find_program_address(
            [builder.BONDING_CURVE_V2_SEED, chain._b58decode(self.mint)],
            builder.PROGRAM_ID,
        )[0]
        self.creator_vault = chain._find_program_address(
            [builder.CREATOR_VAULT_SEED, chain._b58decode(self.creator)],
            builder.PROGRAM_ID,
        )[0]
        self.event_authority = chain._find_program_address(
            [builder.EVENT_AUTHORITY_SEED], builder.PROGRAM_ID
        )[0]
        curve = bytearray(162)
        curve[:8] = bytes([23, 183, 248, 55, 96, 216, 172, 96])
        curve[8:16] = (1_000_000).to_bytes(8, "little")
        curve[16:24] = (2_000_000).to_bytes(8, "little")
        curve[24:32] = (500_000).to_bytes(8, "little")
        curve[32:40] = (400_000).to_bytes(8, "little")
        curve[40:48] = (1_000_000).to_bytes(8, "little")
        curve[49:81] = chain._b58decode(self.creator)
        curve[83:115] = bytes(32)
        self.curve_data = bytes(curve)
        global_data = bytearray(997)
        global_data[:8] = chain.PUMP_GLOBAL_CONFIG_DISCRIMINATOR
        global_data[105:113] = (100).to_bytes(8, "little")
        global_data[154:162] = (30).to_bytes(8, "little")
        global_data[162:194] = chain._b58decode(self.fee_recipient)
        global_data[741:773] = chain._b58decode(self.buyback_one)
        global_data[773:805] = chain._b58decode(self.buyback_two)
        mint_data = bytearray(82)
        mint_data[44] = 6
        mint_data[45] = 1
        self.account_map = {
            self.pool: {"owner": builder.PROGRAM_ID, "data": self.curve_data},
            self.mint: {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint_data)},
            self.global_address: {"owner": builder.PROGRAM_ID, "data": bytes(global_data)},
            self.fee_config_address: {"owner": builder.FEE_PROGRAM_ID, "data": _pump_fee_config()},
            self.curve_ata: _pump_token_account(self.mint, self.pool, 500_000),
            self.user_ata: _pump_token_account(self.mint, self.wallet, 1_000_000),
        }
        self.blockhash = _pump_key(43)
        self.quote = {
            "protocol": "pumpfun_legacy",
            "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT",
            "pool_id": self.pool,
            "input_mint": chain.SOL_MINT,
            "output_mint": self.mint,
            "input_amount": 10_000,
            "out_amount": 4_913,
            "minimum_out_amount": 4_863,
            "slot": 654,
            "slippage_bps": 100,
            "fee_rates_bps": [0, 95, 30],
            "market_cap_lamports": 2_000_000,
            "creator": self.creator,
            "curve_complete": False,
            "is_mayhem_mode": False,
            "is_cashback_coin": False,
            "is_holder_reward": False,
            "creator_fee_bps": 0,
            "virtual_token_reserves": 1_000_000,
            "virtual_quote_reserves": 2_000_000,
            "real_token_reserves": 500_000,
            "real_quote_reserves": 400_000,
            "token_total_supply": 1_000_000,
            "quote_mint": chain.DEFAULT_PUBKEY,
            "token_decimals": 6,
            "token_program": chain.TOKEN_PROGRAM,
        }

    def build(self, quote=None, account_map=None, slot=657,
              user_input_override=None, user_output_override=None):
        account_map = account_map or self.account_map
        def fresh(pool, input_mint, output_mint, amount, slippage):
            result = dict(quote or self.quote)
            result.update({
                "input_mint": input_mint, "output_mint": output_mint,
                "input_amount": amount, "slippage_bps": slippage,
            })
            return result
        selected = quote or self.quote
        user_input = user_input_override or (
            _pump_key(50) if selected["input_mint"] == chain.SOL_MINT else self.user_ata
        )
        user_output = user_output_override or (
            self.user_ata if selected["input_mint"] == chain.SOL_MINT else _pump_key(51)
        )
        with (
            patch.object(chain, "quote_pumpfun_legacy_pool", side_effect=fresh),
            patch.object(chain, "_account", return_value=self.account_map[self.pool]),
            patch.object(chain, "_multiple_accounts_with_context",
                         side_effect=lambda keys: ([account_map.get(k) for k in keys], slot)),
            patch.object(chain, "_rpc", return_value={"value": {
                "blockhash": self.blockhash, "lastValidBlockHeight": 123456
            }}),
        ):
            return builder.build_pumpfun_legacy_swap(
                selected, self.wallet, user_input, user_output
            )

    def test_builds_buy_exact_sol_in_with_creator_v2_and_buyback_accounts(self):
        result = self.build()
        data = base64.b64decode(result["instruction_data_base64"])
        tx = base64.b64decode(result["transaction"])
        accounts = result["instruction_accounts"]
        self.assertEqual(data[:8], builder.BUY_EXACT_SOL_IN)
        self.assertEqual(struct.unpack("<QQ", data[8:]), (10_000, 4_863))
        self.assertEqual(len(accounts), 19)
        self.assertEqual(accounts[:8], [
            self.global_address, self.fee_recipient, self.mint, self.pool,
            self.curve_ata, self.user_ata, self.wallet, builder.SYSTEM_PROGRAM_ID,
        ])
        self.assertEqual(accounts[8], chain.TOKEN_PROGRAM)
        self.assertEqual(accounts[14], self.fee_config_address)
        self.assertEqual(accounts[15], builder.FEE_PROGRAM_ID)
        self.assertEqual(accounts[16:], [self.curve_v2, self.buyback_one, self.buyback_two])
        self.assertEqual(tx[0], 1)
        self.assertEqual(tx[1:65], bytes(64))
        self.assertEqual(result["execution"], "LOCKED")
        self.assertFalse(result["live_trading_ready"])

    def test_builds_token2022_buy_with_transfer_fee_aware_accounts(self):
        accounts = dict(self.account_map)
        self.curve_ata = chain._find_program_address(
            [chain._b58decode(self.pool), chain._b58decode(chain.TOKEN_2022_PROGRAM),
             chain._b58decode(self.mint)], builder.ASSOCIATED_TOKEN_PROGRAM_ID
        )[0]
        self.user_ata = chain._find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_2022_PROGRAM),
             chain._b58decode(self.mint)], builder.ASSOCIATED_TOKEN_PROGRAM_ID
        )[0]
        accounts.pop(next(k for k, v in accounts.items()
                          if isinstance(v, dict) and v.get("owner") == chain.TOKEN_PROGRAM
                          and v.get("data", b"")[:32] == chain._b58decode(self.mint)
                          and v.get("data", b"")[32:64] == chain._b58decode(self.pool)), None)
        accounts.pop(next(k for k, v in accounts.items()
                          if isinstance(v, dict) and v.get("owner") == chain.TOKEN_PROGRAM
                          and v.get("data", b"")[:32] == chain._b58decode(self.mint)
                          and v.get("data", b"")[32:64] == chain._b58decode(self.wallet)), None)
        accounts[self.mint] = _pump_token2022_mint()
        accounts[self.curve_ata] = _pump_token_account(
            self.mint, self.pool, 500_000, chain.TOKEN_2022_PROGRAM, transfer_fee=True
        )
        accounts[self.user_ata] = _pump_token_account(
            self.mint, self.wallet, 1_000_000, chain.TOKEN_2022_PROGRAM,
            transfer_fee=True, immutable_owner=True,
        )
        quote = dict(self.quote, token_program=chain.TOKEN_2022_PROGRAM)
        result = self.build(quote, account_map=accounts)
        self.assertEqual(result["token_program"], chain.TOKEN_2022_PROGRAM)
        self.assertTrue(result["token2022_fee_aware_supported"])
        self.assertEqual(result["instruction_accounts"][8], chain.TOKEN_2022_PROGRAM)

    def test_builds_sell_with_exact_token_input_and_sell_account_order(self):
        quote = dict(self.quote, input_mint=self.mint, output_mint=chain.SOL_MINT)
        result = self.build(quote)
        data = base64.b64decode(result["instruction_data_base64"])
        accounts = result["instruction_accounts"]
        self.assertEqual(data[:8], builder.SELL)
        self.assertEqual(struct.unpack("<QQ", data[8:]), (10_000, 4_863))
        self.assertEqual(len(accounts), 17)
        self.assertEqual(accounts[5], self.user_ata)
        self.assertEqual(accounts[8], self.creator_vault)
        self.assertEqual(accounts[9:14], [
            chain.TOKEN_PROGRAM, self.event_authority,
            builder.PROGRAM_ID, self.fee_config_address, builder.FEE_PROGRAM_ID,
        ])
        self.assertEqual(accounts[14:], [self.curve_v2, self.buyback_one, self.buyback_two])

    def test_rejects_noncanonical_user_ata(self):
        with self.assertRaisesRegex(ValueError, "canonical associated token account"):
            self.build(user_output_override=_pump_key(51))

    def test_rejects_uninitialized_legacy_mint(self):
        accounts = dict(self.account_map)
        mint = bytearray(accounts[self.mint]["data"])
        mint[45] = 0
        accounts[self.mint] = {"owner": chain.TOKEN_PROGRAM, "data": bytes(mint)}
        with self.assertRaisesRegex(ValueError, "not an initialized legacy SPL Token mint"):
            self.build(account_map=accounts)

    def test_rejects_mint_owner_mismatch_and_curve_balance_mismatch(self):
        accounts = dict(self.account_map)
        accounts[self.mint] = dict(accounts[self.mint], owner=chain.TOKEN_2022_PROGRAM)
        with self.assertRaisesRegex(ValueError, "mint owner changed"):
            self.build(account_map=accounts)
        accounts = dict(self.account_map)
        accounts[self.curve_ata] = _pump_token_account(self.mint, self.pool, 499_999)
        with self.assertRaisesRegex(ValueError, "does not match real token reserves"):
            self.build(account_map=accounts)

    def test_rejects_stale_snapshot_and_fee_tier_change(self):
        with self.assertRaisesRegex(ValueError, "stale relative"):
            self.build(slot=700)
        accounts = dict(self.account_map)
        fee = bytearray(accounts[self.fee_config_address]["data"])
        fee[65 + 4 + 16 + 8:65 + 4 + 16 + 16] = (200).to_bytes(8, "little")
        accounts[self.fee_config_address] = {"owner": builder.FEE_PROGRAM_ID, "data": bytes(fee)}
        with self.assertRaisesRegex(ValueError, "fee tier changed"):
            self.build(account_map=accounts)

