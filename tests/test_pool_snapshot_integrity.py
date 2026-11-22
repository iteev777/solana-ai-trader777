import unittest
from unittest.mock import patch

import solana_onchain as onchain


def _key_bytes(value):
    return onchain._b58decode(value)


def _put_key(raw, offset, value):
    raw[offset:offset + 32] = _key_bytes(value)


def _vault(mint, amount):
    raw = bytearray(165)
    _put_key(raw, 0, mint)
    raw[64:72] = int(amount).to_bytes(8, "little")
    return {"owner": onchain.TOKEN_PROGRAM, "data": bytes(raw)}


def _mint(decimals):
    raw = bytearray(82)
    raw[44] = decimals
    return {"owner": onchain.TOKEN_PROGRAM, "data": bytes(raw)}


class ConfirmedPoolSnapshotTests(unittest.TestCase):
    def setUp(self):
        onchain._cache.clear()
        self.pool_id = onchain._b58encode(bytes([9]) * 32)
        self.base_mint = onchain._b58encode(bytes([1]) * 32)
        self.quote_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        self.vault_base = onchain._b58encode(bytes([3]) * 32)
        self.vault_quote = onchain._b58encode(bytes([4]) * 32)
        self.pool_data = bytearray(300)
        self.pool_data[:8] = bytes([241, 154, 109, 4, 17, 177, 109, 188])
        _put_key(self.pool_data, 43, self.base_mint)
        _put_key(self.pool_data, 75, self.quote_mint)
        _put_key(self.pool_data, 139, self.vault_base)
        _put_key(self.pool_data, 171, self.vault_quote)
        self.pool_account = {
            "owner": onchain.PROGRAMS["pumpswap"],
            "data": bytes(self.pool_data),
        }
        self.accounts = [
            self.pool_account,
            _vault(self.base_mint, 10_000_000),
            _vault(self.quote_mint, 100_000_000),
            _mint(6),
            _mint(6),
        ]

    def test_price_uses_one_confirmed_pool_vault_and_mint_snapshot(self):
        with patch.object(onchain, "_account", return_value=self.pool_account), \
             patch.object(onchain, "_multiple_accounts_with_context",
                          return_value=(self.accounts, 987654)):
            result = onchain.decode_pool_snapshot(
                self.pool_id, "pumpswap", self.base_mint, _force=True, _register_watch=False
            )
        self.assertEqual(result["snapshotSlot"], 987654)
        self.assertGreaterEqual(result["snapshotAgeSeconds"], 0)
        self.assertEqual(result["priceUsd"], "10.0")
        self.assertTrue(result["onchainVerified"])

    def test_rejects_pool_owner_change_inside_confirmed_snapshot(self):
        changed = [dict(self.accounts[0]), *self.accounts[1:]]
        changed[0]["owner"] = onchain.PROGRAMS["raydium_cpmm"]
        with patch.object(onchain, "_account", return_value=self.pool_account), \
             patch.object(onchain, "_multiple_accounts_with_context",
                          return_value=(changed, 987655)):
            with self.assertRaisesRegex(ValueError, "owner changed"):
                onchain.decode_pool_snapshot(
                    self.pool_id, "pumpswap", self.base_mint, _force=True, _register_watch=False
                )

    def test_rejects_pool_relationship_change_inside_confirmed_snapshot(self):
        changed_pool = bytearray(self.pool_data)
        _put_key(changed_pool, 139, onchain._b58encode(bytes([7]) * 32))
        changed = [dict(self.pool_account, data=bytes(changed_pool)), *self.accounts[1:]]
        with patch.object(onchain, "_account", return_value=self.pool_account), \
             patch.object(onchain, "_multiple_accounts_with_context",
                          return_value=(changed, 987656)):
            with self.assertRaisesRegex(ValueError, "relationships changed"):
                onchain.decode_pool_snapshot(
                    self.pool_id, "pumpswap", self.base_mint, _force=True, _register_watch=False
                )

    def test_cache_reports_actual_age_since_confirmed_snapshot(self):
        observed = 1_700_000_000.0
        onchain._cache[(self.pool_id, "pumpswap", self.base_mint)] = {
            "time": observed,
            "data": {"observedAt": observed, "snapshotAgeSeconds": 0.0,
                     "snapshotSlot": 123, "priceUsd": "1"}
        }
        with patch.object(onchain.time, "time", return_value=observed + 0.37):
            result = onchain.decode_pool_snapshot(
                self.pool_id, "pumpswap", self.base_mint
            )
        self.assertAlmostEqual(result["snapshotAgeSeconds"], 0.37, places=2)


if __name__ == "__main__":
    unittest.main()
