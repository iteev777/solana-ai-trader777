import base64
import unittest
from unittest.mock import patch

import meteora_dbc_builder as builder
import meteora_dbc_quote as dbc
import solana_onchain as chain


class MeteoraDbcBuilderTests(unittest.TestCase):
    def test_builder_and_simulator_entrypoints_exist(self):
        self.assertTrue(callable(builder.build_meteora_dbc_swap))
        self.assertTrue(callable(builder.simulate_meteora_dbc_transaction))

    def test_rejects_unconfirmed_or_wrong_protocol_quote(self):
        with self.assertRaisesRegex(ValueError, "Meteora DBC quote"):
            builder.build_meteora_dbc_swap({}, "wallet", "input", "output")
        quote = {"protocol": "meteora_dbc", "status": "UNCONFIRMED"}
        with self.assertRaisesRegex(ValueError, "confirmed Meteora DBC"):
            builder.build_meteora_dbc_swap(quote, "wallet", "input", "output")

    def test_unsigned_simulation_is_locked(self):
        transaction = base64.b64encode(bytes(120)).decode("ascii")
        with patch.object(chain, "_rpc", return_value={"value": {
            "err": None, "unitsConsumed": 90_000, "logs": ["meteora dbc ok"]
        }}):
            result = builder.simulate_meteora_dbc_transaction(transaction)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["signature_verification"], "DISABLED_UNSIGNED_SIMULATION")
        self.assertEqual(result["signing"], "NOT_PERFORMED")
        self.assertEqual(result["broadcast"], "DISABLED")
        self.assertEqual(result["execution"], "LOCKED")

    def test_rejects_malformed_simulation_transaction(self):
        with self.assertRaisesRegex(ValueError, "base64"):
            builder.simulate_meteora_dbc_transaction("bad base64")
        with self.assertRaisesRegex(ValueError, "packet limits"):
            builder.simulate_meteora_dbc_transaction(base64.b64encode(bytes(12)).decode())



def _dbc_key(value):
    return chain._b58encode(bytes([value]) * 32)


def _dbc_token_account(mint, authority, amount, token_program=chain.TOKEN_PROGRAM,
                       transfer_fee=False, immutable_owner=False):
    raw = bytearray(182 if immutable_owner else (178 if transfer_fee else 165))
    raw[:32] = chain._b58decode(mint)
    raw[32:64] = chain._b58decode(authority)
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


def _dbc_token2022_mint():
    from token2022_inspector import MINT_ACCOUNT_TYPE
    raw = bytearray(166)
    raw[44] = 9
    raw[45] = 1
    raw[165] = MINT_ACCOUNT_TYPE
    config = bytearray(108)
    config[80:88] = (10_000).to_bytes(8, "little")
    config[88:90] = (100).to_bytes(2, "little")
    config[98:106] = (10_000).to_bytes(8, "little")
    config[106:108] = (100).to_bytes(2, "little")
    raw.extend((1).to_bytes(2, "little") + (108).to_bytes(2, "little") + config)
    return {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw)}


class MeteoraDbcBuilderIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.pool = _dbc_key(51)
        self.config = _dbc_key(52)
        self.base_mint = _dbc_key(53)
        self.quote_mint = _dbc_key(54)
        self.base_vault = _dbc_key(55)
        self.quote_vault = _dbc_key(56)
        self.wallet = _dbc_key(57)
        self.blockhash = _dbc_key(58)
        self.event_authority = _dbc_key(59)
        self.base_ata = chain._find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_PROGRAM),
             chain._b58decode(self.base_mint)],
            "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
        )[0]
        self.quote_ata = chain._find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_PROGRAM),
             chain._b58decode(self.quote_mint)],
            "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
        )[0]
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
        config[104:112] = (10_000_000).to_bytes(8, "little")
        config[112:120] = (0).to_bytes(8, "little")
        config[120:128] = (0).to_bytes(8, "little")
        config[128:130] = (0).to_bytes(2, "little")
        config[130] = 1
        config[136] = 0
        config[232] = 0
        config[392:408] = (1 << 64).to_bytes(16, "little")
        config[408:424] = ((1 << 64) * 11 // 10).to_bytes(16, "little")
        config[424:440] = (1 << 105).to_bytes(16, "little")
        config[440:456] = ((1 << 64) * 12 // 10).to_bytes(16, "little")
        config[456:472] = (1 << 106).to_bytes(16, "little")
        self.config_data = bytes(config)
        base_mint = bytearray(82)
        base_mint[44] = 6
        base_mint[45] = 1
        quote_mint = bytearray(82)
        quote_mint[44] = 9
        quote_mint[45] = 1
        self.base_mint_acc = {"owner": chain.TOKEN_PROGRAM, "data": bytes(base_mint)}
        self.quote_mint_acc = {"owner": chain.TOKEN_PROGRAM, "data": bytes(quote_mint)}
        self.pool_acc = {"owner": chain.PROGRAMS["meteora_dbc"], "data": self.pool_data}
        self.config_acc = {"owner": chain.PROGRAMS["meteora_dbc"], "data": self.config_data}
        self.quote_accounts = [
            self.pool_acc, self.config_acc, self.base_mint_acc, self.quote_mint_acc,
            _dbc_token_account(self.base_mint, builder.POOL_AUTHORITY, 10**12),
            _dbc_token_account(self.quote_mint, builder.POOL_AUTHORITY, 10**12),
        ]

    def _account(self, address):
        if address == self.pool:
            return self.pool_acc
        if address == self.config:
            return self.config_acc
        return None

    def _build(self, input_is_base=True, wrong_ata=False):
        input_mint = self.base_mint if input_is_base else self.quote_mint
        output_mint = self.quote_mint if input_is_base else self.base_mint
        input_ata = self.base_ata if input_is_base else self.quote_ata
        output_ata = self.quote_ata if input_is_base else self.base_ata
        amount = 1_000_000
        with (
            patch.object(chain, "_account", side_effect=self._account),
            patch.object(chain, "_multiple_accounts_with_context",
                         return_value=(self.quote_accounts, 654321)),
        ):
            quote = dbc.quote_meteora_dbc_pool(
                self.pool, input_mint, amount, output_mint=output_mint
            )
        user_in_address = _dbc_key(60) if wrong_ata else input_ata
        input_program = self.base_mint_acc["owner"] if input_is_base else self.quote_mint_acc["owner"]
        output_program = self.quote_mint_acc["owner"] if input_is_base else self.base_mint_acc["owner"]
        user_in = _dbc_token_account(
            input_mint, self.wallet, amount, token_program=input_program,
            transfer_fee=input_program == chain.TOKEN_2022_PROGRAM,
            immutable_owner=input_program == chain.TOKEN_2022_PROGRAM,
        )
        user_out = _dbc_token_account(
            output_mint, self.wallet, 0, token_program=output_program,
            transfer_fee=output_program == chain.TOKEN_2022_PROGRAM,
            immutable_owner=output_program == chain.TOKEN_2022_PROGRAM,
        )
        builder_accounts = [
            self.pool_acc, self.config_acc, self.base_mint_acc, self.quote_mint_acc,
            self.quote_accounts[4], self.quote_accounts[5], user_in, user_out,
        ]
        pda_values = [
            (input_ata, 255), (output_ata, 254), (self.event_authority, 253)
        ]
        with (
            patch.object(chain, "_account", side_effect=self._account),
            patch.object(chain, "_multiple_accounts_with_context",
                         side_effect=[(self.quote_accounts, 654321), (builder_accounts, 654321)]),
            patch.object(chain, "_find_program_address", side_effect=pda_values),
            patch.object(chain, "_rpc", return_value={"value": {
                "blockhash": self.blockhash, "lastValidBlockHeight": 123456
            }}),
        ):
            return builder.build_meteora_dbc_swap(
                quote, self.wallet, user_in_address, output_ata
            )

    def test_builds_verified_15_account_swap_in_both_directions(self):
        for input_is_base in (True, False):
            result = self._build(input_is_base=input_is_base)
            data = base64.b64decode(result["instruction_data_base64"])
            self.assertEqual(data[:8], builder.SWAP)
            self.assertEqual(len(data), 24)
            self.assertEqual(len(result["instruction_accounts"]), 15)
            self.assertEqual(result["instruction_accounts"][0], builder.POOL_AUTHORITY)
            self.assertEqual(result["instruction_accounts"][2], self.pool)
            self.assertEqual(result["instruction_accounts"][9], self.wallet)
            self.assertEqual(result["instruction_accounts"][12], builder.PROGRAM_ID)
            self.assertEqual(result["instruction_accounts"][14], builder.PROGRAM_ID)
            self.assertEqual(result["execution"], "LOCKED")
            self.assertFalse(result["live_trading_ready"])

    def test_builds_fee_bearing_token2022_dbc_swap_with_correct_program_accounts(self):
        self.quote_mint_acc = _dbc_token2022_mint()
        self.quote_accounts[3] = self.quote_mint_acc
        self.quote_accounts[5] = _dbc_token_account(
            self.quote_mint, builder.POOL_AUTHORITY, 10**12,
            token_program=chain.TOKEN_2022_PROGRAM, transfer_fee=True,
        )
        self.quote_ata = chain._find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_2022_PROGRAM),
             chain._b58decode(self.quote_mint)],
            builder.ASSOCIATED_TOKEN_PROGRAM_ID,
        )[0]
        result = self._build(input_is_base=True)
        self.assertEqual(result["token_program_input"], chain.TOKEN_PROGRAM)
        self.assertEqual(result["token_program_output"], chain.TOKEN_2022_PROGRAM)
        self.assertTrue(result["token2022_fee_aware_supported"])
        self.assertEqual(
            result["instruction_accounts"][10:12],
            [chain.TOKEN_PROGRAM, chain.TOKEN_2022_PROGRAM],
        )

    def test_rejects_noncanonical_user_ata(self):
        with self.assertRaisesRegex(ValueError, "canonical associated token account"):
            self._build(wrong_ata=True)


if __name__ == "__main__":
    unittest.main()
