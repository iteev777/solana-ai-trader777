import base64
import hashlib
import struct
import unittest
from unittest.mock import patch

import raydium_cpmm_builder as builder
import solana_onchain as chain


def key(byte):
    return chain._b58encode(bytes([byte]) * 32)


def token_account(mint, owner, amount=1000, token_program=chain.TOKEN_PROGRAM):
    raw = bytearray(165)
    raw[0:32] = chain._b58decode(mint)
    raw[32:64] = chain._b58decode(owner)
    raw[64:72] = amount.to_bytes(8, "little")
    raw[108] = 1
    return {"owner": token_program, "data": bytes(raw)}


class RaydiumCpmmBuilderTests(unittest.TestCase):
    def setUp(self):
        self.wallet = key(1)
        self.pool = key(2)
        self.config = key(3)
        self.mint_a = key(4)
        self.mint_b = key(5)
        self.vault_a = key(6)
        self.vault_b = key(7)
        self.real_find_program_address = chain._find_program_address
        self.user_in = self.real_find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_PROGRAM),
             chain._b58decode(self.mint_a)], builder.ASSOCIATED_TOKEN_PROGRAM_ID
        )[0]
        self.user_out = self.real_find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_PROGRAM),
             chain._b58decode(self.mint_b)], builder.ASSOCIATED_TOKEN_PROGRAM_ID
        )[0]
        self.authority = key(10)
        self.observation = key(11)
        self.blockhash = key(12)
        self.quote = {
            "protocol": "raydium_cpmm",
            "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT",
            "pool_id": self.pool,
            "input_mint": self.mint_a,
            "output_mint": self.mint_b,
            "input_amount": 100,
            "out_amount": 100,
            "minimum_out_amount": 80,
            "slot": 500,
            "token_program_base": chain.TOKEN_PROGRAM,
            "token_program_quote": chain.TOKEN_PROGRAM,
        }
        pool_raw = bytearray(413)
        pool_raw[:8] = chain._discriminator("PoolState")
        pool_raw[8:40] = chain._b58decode(self.config)
        pool_raw[72:104] = chain._b58decode(self.vault_a)
        pool_raw[104:136] = chain._b58decode(self.vault_b)
        pool_raw[168:200] = chain._b58decode(self.mint_a)
        pool_raw[200:232] = chain._b58decode(self.mint_b)
        pool_raw[232:264] = chain._b58decode(chain.TOKEN_PROGRAM)
        pool_raw[264:296] = chain._b58decode(chain.TOKEN_PROGRAM)
        pool_raw[296:328] = chain._b58decode(self.observation)
        pool_raw[328] = 255
        pool_raw[329] = 0
        pool_raw[373:381] = (1).to_bytes(8, "little")
        self.pool_raw = bytes(pool_raw)

        config_raw = bytearray(116)
        config_raw[:8] = chain._discriminator("AmmConfig")
        config_raw[12:20] = (2500).to_bytes(8, "little")
        config_raw[20:28] = (1000).to_bytes(8, "little")
        config_raw[28:36] = (500).to_bytes(8, "little")
        self.config_raw = bytes(config_raw)

        observation_raw = bytearray(40)
        observation_raw[:8] = chain._discriminator("ObservationState")
        observation_raw[8:40] = chain._b58decode(self.pool)
        self.observation_raw = bytes(observation_raw)

        self.account_map = {
            self.pool: {"owner": builder.CPMM_PROGRAM_ID, "data": self.pool_raw},
            self.config: {"owner": builder.CPMM_PROGRAM_ID, "data": self.config_raw},
            self.vault_a: token_account(self.mint_a, self.authority),
            self.vault_b: token_account(self.mint_b, self.authority),
            self.mint_a: {"owner": chain.TOKEN_PROGRAM, "data": bytes(82)},
            self.mint_b: {"owner": chain.TOKEN_PROGRAM, "data": bytes(82)},
            self.user_in: token_account(self.mint_a, self.wallet, 1000),
            self.user_out: token_account(self.mint_b, self.wallet, 0),
            self.observation: {"owner": builder.CPMM_PROGRAM_ID, "data": self.observation_raw},
        }

    def call_builder(self, quote=None, account_map=None,
                     user_input_token_account=None, user_output_token_account=None):
        account_map = account_map or self.account_map
        selected_quote = quote or self.quote
        def pda(seeds, program):
            if seeds == [builder.AUTHORITY_SEED]:
                return self.authority, 255
            if seeds == [builder.OBSERVATION_SEED, chain._b58decode(self.pool)]:
                return self.observation, 254
            return self.real_find_program_address(seeds, program)
        with patch.object(chain, "quote_raydium_cpmm_pool", return_value=selected_quote), \
             patch.object(chain, "_account", side_effect=lambda address: account_map[address]), \
             patch.object(chain, "_decode_pool", wraps=chain._decode_pool), \
             patch.object(chain, "_find_program_address", side_effect=pda), \
             patch.object(chain, "_multiple_accounts_with_context",
                          side_effect=lambda keys: ([account_map.get(k) for k in keys], 503)), \
             patch.object(chain, "_rpc", return_value={"value": {
                 "blockhash": self.blockhash, "lastValidBlockHeight": 123456
             }}):
            return builder.build_raydium_cpmm_swap(
                selected_quote, self.wallet,
                user_input_token_account or self.user_in,
                user_output_token_account or self.user_out,
            )

    def test_builds_unsigned_legacy_transaction_with_correct_swap_data(self):
        result = self.call_builder()
        tx = base64.b64decode(result["transaction"])
        instruction_data = base64.b64decode(result["instruction_data_base64"])
        expected_discriminator = hashlib.sha256(b"global:swap_base_input").digest()[:8]
        self.assertEqual(instruction_data[:8], expected_discriminator)
        self.assertEqual(struct.unpack("<QQ", instruction_data[8:]), (100, 80))
        self.assertEqual(tx[0], 1)  # one signature slot
        self.assertEqual(tx[1:65], bytes(64))  # no server-side signature
        self.assertEqual(result["transaction_version"], "legacy")
        self.assertEqual(result["required_signers"], [self.wallet])
        self.assertEqual(result["signature_status"], "UNSIGNED_EXTERNAL_WALLET_REQUIRED")
        self.assertEqual(result["execution"], "LOCKED")
        self.assertFalse(result["live_trading_ready"])
        self.assertEqual(result["quote_slot_lag"], 3)

    def test_builds_zero_fee_token2022_input_with_matching_program_accounts(self):
        token2022_mint = bytearray(82)
        token2022_mint[44] = 6
        token2022_mint[45] = 1
        pool_raw = bytearray(self.pool_raw)
        pool_raw[232:264] = chain._b58decode(chain.TOKEN_2022_PROGRAM)
        accounts = dict(self.account_map)
        accounts[self.pool] = {"owner": builder.CPMM_PROGRAM_ID, "data": bytes(pool_raw)}
        accounts[self.mint_a] = {
            "owner": chain.TOKEN_2022_PROGRAM, "data": bytes(token2022_mint)
        }
        accounts[self.vault_a] = token_account(
            self.mint_a, self.authority, token_program=chain.TOKEN_2022_PROGRAM
        )
        token2022_user_ata = self.real_find_program_address(
            [chain._b58decode(self.wallet), chain._b58decode(chain.TOKEN_2022_PROGRAM),
             chain._b58decode(self.mint_a)], builder.ASSOCIATED_TOKEN_PROGRAM_ID
        )[0]
        accounts[token2022_user_ata] = token_account(
            self.mint_a, self.wallet, 1000, chain.TOKEN_2022_PROGRAM
        )
        quote = dict(
            self.quote,
            token_program_base=chain.TOKEN_2022_PROGRAM,
        )
        result = self.call_builder(
            quote=quote, account_map=accounts,
            user_input_token_account=token2022_user_ata,
        )
        self.assertTrue(result["token2022_zero_fee_supported"])
        self.assertEqual(result["token_program_input"], chain.TOKEN_2022_PROGRAM)

    def test_rejects_nonzero_token2022_transfer_fee(self):
        from token2022_inspector import MINT_ACCOUNT_TYPE
        config = bytearray(108)
        config[88:90] = (100).to_bytes(2, "little")
        tlv = (1).to_bytes(2, "little") + (108).to_bytes(2, "little") + bytes(config)
        mint_data = bytearray(166)
        mint_data[44] = 6
        mint_data[45] = 1
        mint_data[165] = MINT_ACCOUNT_TYPE
        mint_data.extend(tlv)
        pool_raw = bytearray(self.pool_raw)
        pool_raw[232:264] = chain._b58decode(chain.TOKEN_2022_PROGRAM)
        accounts = dict(self.account_map)
        accounts[self.pool] = {"owner": builder.CPMM_PROGRAM_ID, "data": bytes(pool_raw)}
        accounts[self.mint_a] = {
            "owner": chain.TOKEN_2022_PROGRAM, "data": bytes(mint_data)
        }
        quote = dict(self.quote, token_program_base=chain.TOKEN_2022_PROGRAM)
        with self.assertRaisesRegex(ValueError, "transfer fees must be zero"):
            self.call_builder(quote=quote, account_map=accounts)

    def test_rejects_quote_from_another_protocol(self):
        quote = dict(self.quote, protocol="raydium_clmm")
        with self.assertRaisesRegex(ValueError, "not a confirmed Raydium CPMM"):
            self.call_builder(quote)


    def test_rejects_fresh_quote_minimum_above_output(self):
        invalid = dict(self.quote, minimum_out_amount=101)
        with patch.object(chain, "quote_raydium_cpmm_pool", return_value=invalid):
            with self.assertRaisesRegex(ValueError, "exceeds its quoted output"):
                builder.build_raydium_cpmm_swap(
                    self.quote, self.wallet, self.user_in, self.user_out
                )

    def test_rejects_stale_quote_slot(self):
        with patch.object(chain, "quote_raydium_cpmm_pool", return_value=self.quote), \
             patch.object(chain, "_account", return_value={"owner": builder.CPMM_PROGRAM_ID, "data": self.pool_raw}), \
             patch.object(chain, "_decode_pool", wraps=chain._decode_pool), \
             patch.object(chain, "_find_program_address", side_effect=[
                 (self.authority, 255), (self.observation, 254)
             ]), \
             patch.object(chain, "_multiple_accounts_with_context",
                          side_effect=lambda keys: ([self.account_map.get(k) for k in keys], 600)):
            with self.assertRaisesRegex(ValueError, "quote is stale"):
                builder.build_raydium_cpmm_swap(
                    self.quote, self.wallet, self.user_in, self.user_out
                )


    def test_rejects_noncanonical_observation_pda_stored_in_pool_state(self):
        bad_pool = bytearray(self.pool_raw)
        bad_pool[296:328] = chain._b58decode(key(45))
        bad_account = {"owner": builder.CPMM_PROGRAM_ID, "data": bytes(bad_pool)}
        with patch.object(chain, "quote_raydium_cpmm_pool", return_value=self.quote), \
             patch.object(chain, "_account", return_value=bad_account), \
             patch.object(chain, "_find_program_address", side_effect=[
                 (self.authority, 255), (self.observation, 254)
             ]):
            with self.assertRaisesRegex(ValueError, "observation PDA"):
                builder.build_raydium_cpmm_swap(
                    self.quote, self.wallet, self.user_in, self.user_out
                )


    def test_rejects_vault_not_owned_by_canonical_pool_authority(self):
        accounts = dict(self.account_map)
        accounts[self.vault_a] = token_account(self.mint_a, key(46), 1000)
        with self.assertRaisesRegex(ValueError, "vault token authority"):
            self.call_builder(account_map=accounts)

    def test_rejects_input_account_owned_by_another_wallet(self):
        accounts = dict(self.account_map)
        accounts[self.user_in] = token_account(self.mint_a, key(30), 1000)
        with self.assertRaisesRegex(ValueError, "not owned by the signing wallet"):
            self.call_builder(account_map=accounts)

    def test_rejects_wrong_mint_in_output_account(self):
        accounts = dict(self.account_map)
        accounts[self.user_out] = token_account(self.mint_a, self.wallet, 1000)
        with self.assertRaisesRegex(ValueError, "mint does not match"):
            self.call_builder(account_map=accounts)

    def test_rejects_insufficient_input_balance(self):
        accounts = dict(self.account_map)
        accounts[self.user_in] = token_account(self.mint_a, self.wallet, 99)
        with self.assertRaisesRegex(ValueError, "insufficient balance"):
            self.call_builder(account_map=accounts)

    def test_rejects_missing_observation_or_wrong_pool_observation(self):
        accounts = dict(self.account_map)
        wrong = bytearray(self.observation_raw)
        wrong[8:40] = chain._b58decode(key(44))
        accounts[self.observation] = {
            "owner": builder.CPMM_PROGRAM_ID, "data": bytes(wrong)
        }
        with self.assertRaisesRegex(ValueError, "different CPMM pool"):
            self.call_builder(account_map=accounts)


    def test_ignores_client_tampered_minimum_output_and_requotes(self):
        tampered = dict(self.quote, minimum_out_amount=1)
        result = self.call_builder(quote=tampered)
        instruction_data = base64.b64decode(result["instruction_data_base64"])
        self.assertEqual(struct.unpack("<QQ", instruction_data[8:]), (100, 80))

    def test_simulation_requires_rpc_success(self):
        tx = base64.b64encode(b"x" * 120).decode("ascii")
        with patch.object(chain, "_rpc", return_value={"value": {
            "err": None, "unitsConsumed": 55000, "logs": ["ok"]
        }}) as rpc:
            result = builder.simulate_raydium_cpmm_transaction(tx)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["units_consumed"], 55000)
        self.assertEqual(rpc.call_args.args[0], "simulateTransaction")

    def test_simulation_rejects_program_error(self):
        tx = base64.b64encode(b"x" * 120).decode("ascii")
        with patch.object(chain, "_rpc", return_value={"value": {
            "err": {"InstructionError": [0, "Custom"]}, "logs": ["program failed"]
        }}):
            result = builder.simulate_raydium_cpmm_transaction(tx)
        self.assertEqual(result["status"], "REJECT")
        self.assertIsNotNone(result["error"])

    def test_simulation_rejects_malformed_rpc_response(self):
        tx = base64.b64encode(b"x" * 120).decode("ascii")
        with patch.object(chain, "_rpc", return_value={"value": None}):
            with self.assertRaisesRegex(ValueError, "malformed simulation"):
                builder.simulate_raydium_cpmm_transaction(tx)

    def test_legacy_message_compiler_rejects_invalid_blockhash(self):
        with self.assertRaisesRegex(ValueError, "recent blockhash"):
            builder._compile_legacy_message(
                self.wallet, "not-a-blockhash", builder.CPMM_PROGRAM_ID, [], b"x"
            )

    def test_legacy_compiler_prepends_compute_budget_and_priority_fee(self):
        program = builder.CPMM_PROGRAM_ID
        metas = [{"pubkey": self.pool, "is_signer": False, "is_writable": True}]
        tx, keys = builder._compile_legacy_message(
            self.wallet, key(88), program, metas, b"swap",
            compute_unit_limit=180_000, compute_unit_price=12_345,
        )
        self.assertIn(builder.COMPUTE_BUDGET_PROGRAM_ID, keys)
        offset = 65
        offset += 3  # message header
        key_count = tx[offset]
        offset += 1 + key_count * 32 + 32
        instruction_count = tx[offset]
        offset += 1
        self.assertEqual(instruction_count, 3)
        decoded = []
        for _ in range(instruction_count):
            program_index = tx[offset]
            account_count = tx[offset + 1]
            offset += 2 + account_count
            data_length = tx[offset]
            offset += 1
            data = tx[offset:offset + data_length]
            offset += data_length
            decoded.append((keys[program_index], data))
        self.assertEqual(decoded[0][0], builder.COMPUTE_BUDGET_PROGRAM_ID)
        self.assertEqual(decoded[0][1], bytes([2]) + struct.pack("<I", 180_000))
        self.assertEqual(decoded[1][0], builder.COMPUTE_BUDGET_PROGRAM_ID)
        self.assertEqual(decoded[1][1], bytes([3]) + struct.pack("<Q", 12_345))
        self.assertEqual(decoded[2][0], program)
        self.assertEqual(decoded[2][1], b"swap")

    def test_compact_shortvec_encoding(self):
        self.assertEqual(builder._shortvec(0), b"\x00")
        self.assertEqual(builder._shortvec(127), b"\x7f")
        self.assertEqual(builder._shortvec(128), b"\x80\x01")


if __name__ == "__main__":
    unittest.main()
