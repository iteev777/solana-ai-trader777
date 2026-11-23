import unittest
from unittest.mock import patch

import solana_onchain as chain
import meteora_dlmm_quote as dlmm
from meteora_dlmm_quote import quote_meteora_dlmm_pool


class MeteoraDlmmDirectQuoteTests(unittest.TestCase):
    def setUp(self):
        self.pool = chain._b58encode(bytes([61]) * 32)
        self.mint_x = chain._b58encode(bytes([62]) * 32)
        self.mint_y = chain._b58encode(bytes([63]) * 32)
        self.reserve_x = chain._b58encode(bytes([64]) * 32)
        self.reserve_y = chain._b58encode(bytes([65]) * 32)
        self.bitmap = chain._b58encode(bytes([66]) * 32)
        self.array_addresses = {
            index: chain._b58encode(bytes([70 + i]) * 32)
            for i, index in enumerate((1, 0, -1, 2, 3))
        }

        pair = bytearray(904)
        pair[:8] = bytes([33, 11, 49, 98, 181, 101, 177, 13])
        pair[8:10] = (10000).to_bytes(2, "little")
        pair[10:12] = (30).to_bytes(2, "little")
        pair[12:14] = (60).to_bytes(2, "little")
        pair[14:16] = (5000).to_bytes(2, "little")
        pair[16:20] = (100000).to_bytes(4, "little")
        pair[20:24] = (100000).to_bytes(4, "little")
        pair[24:28] = (-1000).to_bytes(4, "little", signed=True)
        pair[28:32] = (1000).to_bytes(4, "little", signed=True)
        pair[32:34] = (1000).to_bytes(2, "little")
        pair[34] = 0
        pair[35] = 0
        pair[36] = 0  # InputOnly fee mode
        pair[40:44] = (1000).to_bytes(4, "little")
        pair[44:48] = (500).to_bytes(4, "little")
        pair[48:52] = (100).to_bytes(4, "little", signed=True)
        pair[56:64] = (990).to_bytes(8, "little", signed=True)
        pair[76:80] = (100).to_bytes(4, "little", signed=True)
        pair[80:82] = (4).to_bytes(2, "little")
        pair[82] = 0
        pair[88:120] = chain._b58decode(self.mint_x)
        pair[120:152] = chain._b58decode(self.mint_y)
        pair[152:184] = chain._b58decode(self.reserve_x)
        pair[184:216] = chain._b58decode(self.reserve_y)
        pair[216:224] = (100).to_bytes(8, "little")
        pair[224:232] = (200).to_bytes(8, "little")
        pair[552:584] = bytes([67]) * 32
        self.pair_data = bytes(pair)

        def token_account(mint, amount):
            raw = bytearray(165)
            raw[:32] = chain._b58decode(mint)
            raw[64:72] = amount.to_bytes(8, "little")
            return {"owner": chain.TOKEN_PROGRAM, "data": bytes(raw)}

        self.reserve_x_account = token_account(self.mint_x, 10**12)
        self.reserve_y_account = token_account(self.mint_y, 10**12)
        self.mint_x_account = {"owner": chain.TOKEN_PROGRAM, "data": bytes(82)}
        self.mint_y_account = {"owner": chain.TOKEN_PROGRAM, "data": bytes(82)}

        self.bin_accounts = {}
        for array_index, address in self.array_addresses.items():
            raw = bytearray(10136)
            raw[:8] = chain._discriminator("BinArray")
            raw[8:16] = array_index.to_bytes(8, "little", signed=True)
            raw[16] = 0
            raw[24:56] = chain._b58decode(self.pool)
            if array_index == 1:
                # Active bin 100 is slot 30 in array index 1.
                offset = 56 + 30 * 144
                raw[offset:offset + 8] = (1_000_000_000).to_bytes(8, "little")
                raw[offset + 8:offset + 16] = (1_000_000_000).to_bytes(8, "little")
                raw[offset + 16:offset + 32] = (1 << 64).to_bytes(16, "little")
            self.bin_accounts[address] = {
                "owner": chain.PROGRAMS["meteora_dlmm"], "data": bytes(raw)
            }

        clock = bytearray(40)
        clock[32:40] = (1000).to_bytes(8, "little", signed=True)
        self.clock_account = {"owner": "Sysvar1111111111111111111111111111111111", "data": bytes(clock)}
        self.pool_account = {
            "owner": chain.PROGRAMS["meteora_dlmm"], "data": self.pair_data
        }

    def _fake_pda(self, seeds, program):
        if seeds[0] == b"bitmap":
            return self.bitmap, 255
        index = int.from_bytes(seeds[2], "little", signed=True)
        return self.array_addresses[index], 255

    def _accounts_for(self, keys, overrides=None):
        overrides = overrides or {}
        values = [
            self.pool_account,
            overrides.get("reserve_x", self.reserve_x_account),
            overrides.get("reserve_y", self.reserve_y_account),
            overrides.get("mint_x", self.mint_x_account),
            overrides.get("mint_y", self.mint_y_account),
            self.clock_account,
            overrides.get("bitmap"),
        ]
        for key in keys[7:]:
            values.append(overrides.get(key, self.bin_accounts.get(key)))
        return values

    def quote(self, input_mint=None, output_mint=None, amount=1_000_000,
              overrides=None, pair_data=None, slot=123987):
        pool_account = self.pool_account if pair_data is None else {
            "owner": chain.PROGRAMS["meteora_dlmm"], "data": pair_data
        }
        def snapshot(keys):
            values = self._accounts_for(keys, overrides)
            values[0] = pool_account
            return values, slot
        with (
            patch.object(chain, "_account", return_value=pool_account),
            patch.object(chain, "_find_program_address", side_effect=self._fake_pda),
            patch.object(chain, "_multiple_accounts_with_context", side_effect=snapshot),
        ):
            return quote_meteora_dlmm_pool(
                self.pool, input_mint or self.mint_x, amount,
                output_mint=output_mint or self.mint_y,
            )

    def test_confirmed_snapshot_quotes_both_directions(self):
        x_to_y = self.quote()
        y_to_x = self.quote(input_mint=self.mint_y, output_mint=self.mint_x)
        self.assertEqual(x_to_y["protocol"], "meteora_dlmm")
        self.assertEqual(x_to_y["slot"], 123987)
        self.assertEqual(x_to_y["output_mint"], self.mint_y)
        self.assertGreater(x_to_y["out_amount"], 0)
        self.assertGreater(x_to_y["fee_amount"], 0)
        self.assertEqual(x_to_y["execution"], "DISABLED")
        self.assertEqual(y_to_x["output_mint"], self.mint_x)
        self.assertGreater(y_to_x["out_amount"], 0)

    def test_only_y_mode_charges_output_for_x_input_and_input_for_y(self):
        pair = bytearray(self.pair_data)
        pair[36] = 1
        x_to_y = self.quote(pair_data=bytes(pair))
        y_to_x = self.quote(
            input_mint=self.mint_y, output_mint=self.mint_x, pair_data=bytes(pair)
        )
        self.assertFalse(x_to_y["fee_on_input"])
        self.assertEqual(x_to_y["fee_mint"], self.mint_y)
        self.assertTrue(y_to_x["fee_on_input"])
        self.assertEqual(y_to_x["fee_mint"], self.mint_y)

    def test_fee_updates_after_crossing_into_next_bin(self):
        pair = bytearray(self.pair_data)
        pair[16:20] = (100000).to_bytes(4, "little")
        self.pair_data = bytes(pair)
        self.pool_account = {"owner": chain.PROGRAMS["meteora_dlmm"], "data": self.pair_data}
        active = self.bin_accounts[self.array_addresses[1]]["data"]
        raw = bytearray(active)
        offset = 56 + 30 * 144
        raw[offset:offset + 8] = (1000).to_bytes(8, "little")
        raw[offset + 8:offset + 16] = (1000).to_bytes(8, "little")
        next_offset = 56 + 29 * 144
        raw[next_offset:next_offset + 8] = (1_000_000_000).to_bytes(8, "little")
        raw[next_offset + 8:next_offset + 16] = (1_000_000_000).to_bytes(8, "little")
        raw[next_offset + 16:next_offset + 32] = (1 << 64).to_bytes(16, "little")
        self.bin_accounts[self.array_addresses[1]] = {
            "owner": chain.PROGRAMS["meteora_dlmm"], "data": bytes(raw)
        }
        result = self.quote(amount=10_000)
        self.assertGreaterEqual(result["bins_crossed"], 1)
        self.assertGreater(result["max_fee_rate"], 400_004)

    def test_quote_refuses_when_amount_exceeds_loaded_bin_arrays(self):
        with self.assertRaisesRegex(ValueError, "coverage"):
            self.quote(amount=10**12)

    def test_missing_active_array_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "active bin array"):
            self.quote(overrides={self.array_addresses[1]: None})

    def test_token_2022_mints_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Token-2022"):
            self.quote(overrides={
                "mint_y": dict(self.mint_y_account, owner=chain.TOKEN_2022_PROGRAM)
            })

    def test_disabled_pair_is_rejected(self):
        pair = bytearray(self.pair_data)
        pair[82] = 1
        with self.assertRaisesRegex(ValueError, "not enabled"):
            self.quote(pair_data=bytes(pair))

    def test_invalid_fee_collection_mode_is_rejected(self):
        pair = bytearray(self.pair_data)
        pair[36] = 9
        with self.assertRaisesRegex(ValueError, "fee collection mode"):
            self.quote(pair_data=bytes(pair))

    def test_invalid_clock_owner_is_rejected(self):
        original = self.clock_account
        try:
            self.clock_account = dict(original, owner=chain.TOKEN_PROGRAM)
            with self.assertRaisesRegex(ValueError, "Clock sysvar owner"):
                self.quote()
        finally:
            self.clock_account = original

    def test_invalid_clock_data_type_is_rejected(self):
        original = self.clock_account
        try:
            self.clock_account = dict(original, data="not bytes")
            with self.assertRaisesRegex(ValueError, "Clock sysvar is truncated"):
                self.quote()
        finally:
            self.clock_account = original

    def test_invalid_confirmed_slot_is_rejected(self):
        for slot in (0, -1, True, "123"):
            with self.subTest(slot=slot):
                with self.assertRaisesRegex(ValueError, "snapshot slot is invalid"):
                    self.quote(slot=slot)

    def test_aliased_reserve_and_mint_are_rejected(self):
        pair = bytearray(self.pair_data)
        pair[152:184] = chain._b58decode(self.mint_x)
        with self.assertRaisesRegex(ValueError, "distinct addresses"):
            self.quote(pair_data=bytes(pair))

    def test_wrong_output_mint_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Requested output mint"):
            self.quote(output_mint="wrong")

    def test_protocol_fee_is_subtracted_from_spendable_output_reserve(self):
        pair = bytearray(self.pair_data)
        pair[224:232] = (10**12).to_bytes(8, "little")
        with self.assertRaisesRegex(ValueError, "spendable output reserve"):
            self.quote(pair_data=bytes(pair))

    def test_bad_bin_array_owner_or_pool_link_is_rejected(self):
        account = dict(self.bin_accounts[self.array_addresses[1]], owner=chain.PROGRAMS["raydium_cpmm"])
        with self.assertRaisesRegex(ValueError, "bin-array owner"):
            self.quote(overrides={self.array_addresses[1]: account})


if __name__ == "__main__":
    unittest.main()
