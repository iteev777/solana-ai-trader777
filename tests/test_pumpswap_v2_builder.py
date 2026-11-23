import base64
import hashlib
import struct
import unittest
from unittest.mock import patch

import pumpswap_v2_builder as builder
import solana_onchain as chain


def key(byte):
    return chain._b58encode(bytes([byte]) * 32)


def token_account(mint, owner, amount=1000):
    raw = bytearray(165)
    raw[0:32] = chain._b58decode(mint)
    raw[32:64] = chain._b58decode(owner)
    raw[64:72] = amount.to_bytes(8, "little")
    raw[108] = 1
    return {"owner": chain.TOKEN_PROGRAM, "data": bytes(raw)}


class PumpSwapV2BuilderTests(unittest.TestCase):
    def setUp(self):
        self.wallet = key(1)
        self.pool = key(2)
        self.creator = key(3)
        self.base_mint = key(4)
        self.quote_mint = chain.SOL_MINT
        self.vault_base = key(5)
        self.vault_quote = key(6)
        self.user_base = key(7)
        self.user_quote = key(8)
        self.user_volume = key(9)
        self.event_authority = key(10)
        self.blockhash = key(11)
        self.recipients = [key(20 + i) for i in range(8)]
        self.buyback_atas = [key(40 + i) for i in range(8)]
        self.quote = {
            "protocol": "pumpswap",
            "status": "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT",
            "pool_id": self.pool,
            "input_mint": self.quote_mint,
            "output_mint": self.base_mint,
            "input_amount": 100,
            "out_amount": 100,
            "minimum_out_amount": 80,
            "slot": 500,
        }

        pool_raw = bytearray(271)
        pool_raw[:8] = chain.PUMPSWAP_GLOBAL_CONFIG_DISCRIMINATOR[:0] + bytes([241,154,109,4,17,177,109,188])
        pool_raw[8] = 255
        pool_raw[9:11] = (0).to_bytes(2, "little")
        pool_raw[11:43] = chain._b58decode(self.creator)
        pool_raw[43:75] = chain._b58decode(self.base_mint)
        pool_raw[75:107] = chain._b58decode(self.quote_mint)
        pool_raw[139:171] = chain._b58decode(self.vault_base)
        pool_raw[171:203] = chain._b58decode(self.vault_quote)
        pool_raw[211:243] = chain._b58decode(key(12))
        pool_raw[243] = 0
        pool_raw[244] = 0
        pool_raw[245:261] = (0).to_bytes(16, "little", signed=True)
        pool_raw[261:269] = (0).to_bytes(8, "little")
        pool_raw[270] = 0
        self.pool_raw = bytes(pool_raw)

        global_raw = bytearray(907)
        global_raw[:8] = chain.PUMPSWAP_GLOBAL_CONFIG_DISCRIMINATOR
        global_raw[56] = 0
        for i, recipient in enumerate(self.recipients):
            global_raw[643 + i * 32:675 + i * 32] = chain._b58decode(recipient)
        global_raw[899:907] = (1000).to_bytes(8, "little")
        self.global_raw = bytes(global_raw)

        fee_raw = bytearray(97)
        fee_raw[:8] = chain.PUMPSWAP_FEE_CONFIG_DISCRIMINATOR
        for offset in (41, 73):
            fee_raw[offset:offset + 8] = (20).to_bytes(8, "little")
            fee_raw[offset + 8:offset + 16] = (5).to_bytes(8, "little")
            fee_raw[offset + 16:offset + 24] = (0).to_bytes(8, "little")
        fee_raw[65:69] = (0).to_bytes(4, "little")
        fee_raw[69:73] = (0).to_bytes(4, "little")
        self.fee_raw = bytes(fee_raw)

        self.account_map = {
            self.pool: {"owner": builder.PUMPSWAP_PROGRAM_ID, "data": self.pool_raw},
            builder.GLOBAL_CONFIG: {"owner": builder.PUMPSWAP_PROGRAM_ID, "data": self.global_raw},
            builder.FEE_CONFIG: {"owner": builder.PUMP_FEES_PROGRAM_ID, "data": self.fee_raw},
            self.vault_base: token_account(self.base_mint, self.pool),
            self.vault_quote: token_account(self.quote_mint, self.pool),
            self.base_mint: {"owner": chain.TOKEN_PROGRAM, "data": bytes(82)},
            self.quote_mint: {"owner": chain.TOKEN_PROGRAM, "data": bytes(82)},
            self.user_base: token_account(self.base_mint, self.wallet, 1000),
            self.user_quote: token_account(self.quote_mint, self.wallet, 1000),
            self.user_volume: None,
        }
        self.account_map[self.buyback_atas[0]] = token_account(
            self.quote_mint, self.recipients[0], 0
        )

    def find_pda(self, seeds, program):
        if seeds[0] == b"pool":
            return self.pool, 255
        if seeds[0] == b"global_config":
            return builder.GLOBAL_CONFIG, 254
        if seeds[0] == b"fee_config":
            return builder.FEE_CONFIG, 253
        if seeds[0] == b"user_volume_accumulator":
            return self.user_volume, 252
        if seeds[0] == b"__event_authority":
            return self.event_authority, 251
        if program == builder.ASSOCIATED_TOKEN_PROGRAM_ID:
            for recipient, ata in zip(self.recipients, self.buyback_atas):
                if seeds[0] == chain._b58decode(recipient):
                    return ata, 250
        raise AssertionError("unexpected PDA derivation")

    def call_builder(self, quote=None, account_map=None):
        account_map = account_map or self.account_map
        active_quote = quote or self.quote
        def multiple(keys):
            return [account_map.get(k) for k in keys], 503
        with patch.object(chain, "quote_pumpswap_pool", return_value=active_quote), \
             patch.object(chain, "_account", side_effect=lambda address: account_map[address]), \
             patch.object(chain, "_find_program_address", side_effect=self.find_pda), \
             patch.object(chain, "_multiple_accounts_with_context", side_effect=multiple), \
             patch.object(chain, "_rpc", return_value={"value": {
                 "blockhash": self.blockhash, "lastValidBlockHeight": 123456
             }}):
            return builder.build_pumpswap_v2_swap(
                active_quote, self.wallet, self.user_base, self.user_quote
            )

    def test_builds_unsigned_buy_exact_quote_in_v2_transaction(self):
        result = self.call_builder()
        data = base64.b64decode(result["transaction"])
        instruction_data = base64.b64decode(result["instruction_data_base64"])
        self.assertEqual(instruction_data[:8], builder.BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR)
        self.assertEqual(struct.unpack("<QQ", instruction_data[8:]), (100, 80))
        self.assertEqual(result["instruction"], "buy_exact_quote_in_v2")
        self.assertEqual(result["buyback_fee_recipient"], self.recipients[0])
        self.assertEqual(result["buyback_fee_recipient_ata"], self.buyback_atas[0])
        self.assertEqual(data[0], 1)
        self.assertEqual(data[1:65], bytes(64))
        self.assertEqual(result["signature_status"], "UNSIGNED_EXTERNAL_WALLET_REQUIRED")
        self.assertEqual(result["execution"], "LOCKED")
        self.assertFalse(result["live_trading_ready"])

    def test_builds_unsigned_sell_v2_transaction(self):
        quote = dict(self.quote, input_mint=self.base_mint,
                     output_mint=self.quote_mint)
        result = self.call_builder(quote=quote)
        instruction_data = base64.b64decode(result["instruction_data_base64"])
        self.assertEqual(instruction_data[:8], builder.SELL_V2_DISCRIMINATOR)
        self.assertEqual(struct.unpack("<QQ", instruction_data[8:]), (100, 80))
        self.assertEqual(result["instruction"], "sell_v2")


    def test_rejects_token_2022_mint_until_extension_aware_support_exists(self):
        accounts = dict(self.account_map)
        accounts[self.base_mint] = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(166)}
        with self.assertRaisesRegex(ValueError, "legacy SPL Token"):
            self.call_builder(account_map=accounts)

    def test_rejects_pool_state_with_wrong_pda_bump(self):
        bad = bytearray(self.pool_raw)
        bad[8] = 1
        accounts = dict(self.account_map)
        accounts[self.pool] = {"owner": builder.PUMPSWAP_PROGRAM_ID, "data": bytes(bad)}
        with patch.object(chain, "_account", side_effect=lambda address: accounts[address]), \
             patch.object(chain, "_find_program_address", side_effect=self.find_pda):
            with self.assertRaisesRegex(ValueError, "pool bump"):
                builder.build_pumpswap_v2_swap(
                    self.quote, self.wallet, self.user_base, self.user_quote
                )

    def test_rejects_cashback_pool(self):
        bad = bytearray(self.pool_raw)
        bad[244] = 1
        accounts = dict(self.account_map)
        accounts[self.pool] = {"owner": builder.PUMPSWAP_PROGRAM_ID, "data": bytes(bad)}
        with patch.object(chain, "_account", side_effect=lambda address: accounts[address]), \
             patch.object(chain, "_find_program_address", side_effect=self.find_pda):
            with self.assertRaisesRegex(ValueError, "cashback"):
                builder.build_pumpswap_v2_swap(
                    self.quote, self.wallet, self.user_base, self.user_quote
                )

    def test_rejects_stale_quote(self):
        with patch.object(chain, "_account", side_effect=lambda address: self.account_map[address]), \
             patch.object(chain, "_find_program_address", side_effect=self.find_pda), \
             patch.object(chain, "quote_pumpswap_pool", return_value=dict(self.quote, slot=499)):
            with self.assertRaisesRegex(ValueError, "older than the supplied quote"):
                builder.build_pumpswap_v2_swap(
                    self.quote, self.wallet, self.user_base, self.user_quote
                )

    def test_rejects_missing_buyback_ata(self):
        accounts = dict(self.account_map)
        accounts.pop(self.buyback_atas[0], None)
        with self.assertRaisesRegex(ValueError, "No configured PumpSwap buyback recipient"):
            self.call_builder(account_map=accounts)

    def test_rejects_insufficient_quote_balance(self):
        accounts = dict(self.account_map)
        accounts[self.user_quote] = token_account(self.quote_mint, self.wallet, 99)
        with self.assertRaisesRegex(ValueError, "insufficient balance"):
            self.call_builder(account_map=accounts)

    def test_rejects_wrong_vault_authority(self):
        accounts = dict(self.account_map)
        accounts[self.vault_base] = token_account(self.base_mint, key(55))
        with self.assertRaisesRegex(ValueError, "vault token authority"):
            self.call_builder(account_map=accounts)


if __name__ == "__main__":
    unittest.main()
