import hashlib
import struct
import unittest
from unittest import mock

from solana_onchain import (_decode_pool, _decode_raydium_amm_config, _discriminator,
                            quote_raydium_cpmm_pool, PROGRAMS, normalize_protocol,
                            TOKEN_PROGRAM)
from indicators import calculate_indicators
from strategy_rules import validate_rules_config
import onchain_stream


def put_key(data, offset, value):
    data[offset:offset + 32] = bytes([value]) * 32


class OnchainDecoderTests(unittest.TestCase):
    def test_pumpswap_pool_layout(self):
        raw = bytearray(300)
        raw[:8] = bytes([241, 154, 109, 4, 17, 177, 109, 188])
        put_key(raw, 43, 1)
        put_key(raw, 75, 2)
        put_key(raw, 139, 3)
        put_key(raw, 171, 4)
        result = _decode_pool("pumpswap", bytes(raw))
        self.assertEqual(result["base_mint"], _decode_pool("pumpswap", bytes(raw))["base_mint"])
        self.assertEqual(result["price_mode"], "reserves")


    def test_raydium_cpmm_fee_config_layout(self):
        raw = bytearray(236)
        raw[:8] = _discriminator("AmmConfig")
        struct.pack_into("<Q", raw, 12, 2500)
        struct.pack_into("<Q", raw, 20, 120000)
        struct.pack_into("<Q", raw, 28, 100000)
        struct.pack_into("<Q", raw, 108, 1000)
        decoded = _decode_raydium_amm_config(bytes(raw))
        self.assertEqual(decoded["trade_fee_rate"], 2500)
        self.assertEqual(decoded["creator_fee_rate"], 1000)

    def test_raydium_cpmm_quote_uses_confirmed_snapshot_and_fee_side(self):
        import solana_onchain
        pool = bytearray(400)
        pool[:8] = _discriminator("PoolState")
        put_key(pool, 8, 8)       # AmmConfig
        put_key(pool, 72, 3)      # vault token0
        put_key(pool, 104, 4)     # vault token1
        put_key(pool, 168, 1)     # mint token0
        put_key(pool, 200, 2)     # mint token1
        put_key(pool, 232, 9)     # token program token0
        put_key(pool, 264, 9)     # token program token1
        struct.pack_into("<Q", pool, 373, 0)
        pool[389] = 1             # creator fee only on token0
        pool[390] = 1             # creator fee enabled

        config = bytearray(236)
        config[:8] = _discriminator("AmmConfig")
        struct.pack_into("<Q", config, 12, 2500)
        struct.pack_into("<Q", config, 20, 120000)
        struct.pack_into("<Q", config, 28, 100000)
        struct.pack_into("<Q", config, 108, 1000)

        def token_account(mint_byte, amount):
            raw = bytearray(165)
            raw[:32] = bytes([mint_byte]) * 32
            struct.pack_into("<Q", raw, 64, amount)
            return {"owner": TOKEN_PROGRAM, "data": bytes(raw)}

        def mint_account(decimals):
            raw = bytearray(82)
            raw[44] = decimals
            return {"owner": TOKEN_PROGRAM, "data": bytes(raw)}

        pool_account = {"owner": PROGRAMS["raydium_cpmm"], "data": bytes(pool)}
        config_account = {"owner": PROGRAMS["raydium_cpmm"], "data": bytes(config)}
        accounts = [
            pool_account, config_account,
            token_account(3, 1_000_000), token_account(4, 2_000_000),
            mint_account(6), mint_account(9),
        ]
        with mock.patch.object(solana_onchain, "_account", return_value=pool_account), \
             mock.patch.object(solana_onchain, "_multiple_accounts_with_context",
                               return_value=(accounts, 777)):
            quote = quote_raydium_cpmm_pool(
                "pool", _decode_pool("raydium_cpmm", bytes(pool))["quote_mint"],
                1000, 100
            )
        self.assertEqual(quote["slot"], 777)
        self.assertFalse(quote["creator_fee_on_input"])
        self.assertEqual(quote["creator_fee_output_amount"], 1)
        self.assertEqual(quote["out_amount"], 497)
        self.assertEqual(quote["execution"], "DISABLED")

    def test_raydium_cpmm_quote_rejects_token_2022(self):
        import solana_onchain
        pool = bytearray(400)
        pool[:8] = _discriminator("PoolState")
        put_key(pool, 8, 8)
        put_key(pool, 72, 3)
        put_key(pool, 104, 4)
        put_key(pool, 168, 1)
        put_key(pool, 200, 2)
        pool[389] = 0
        config = bytearray(236)
        config[:8] = _discriminator("AmmConfig")
        struct.pack_into("<Q", config, 12, 2500)
        pool_account = {"owner": PROGRAMS["raydium_cpmm"], "data": bytes(pool)}
        accounts = [
            pool_account,
            {"owner": PROGRAMS["raydium_cpmm"], "data": bytes(config)},
            {"owner": TOKEN_PROGRAM, "data": bytes(165)},
            {"owner": TOKEN_PROGRAM, "data": bytes(165)},
            {"owner": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb", "data": bytes(82)},
            {"owner": TOKEN_PROGRAM, "data": bytes(82)},
        ]
        with mock.patch.object(solana_onchain, "_account", return_value=pool_account), \
             mock.patch.object(solana_onchain, "_multiple_accounts_with_context",
                               return_value=(accounts, 777)):
            with self.assertRaisesRegex(ValueError, "Token-2022"):
                quote_raydium_cpmm_pool("pool", "mint", 1000)

    def test_raydium_cpmm_pool_layout(self):
        raw = bytearray(400)
        raw[:8] = _discriminator("PoolState")
        put_key(raw, 72, 1)
        put_key(raw, 104, 2)
        put_key(raw, 168, 3)
        put_key(raw, 200, 4)
        result = _decode_pool("raydium_cpmm", bytes(raw))
        self.assertEqual(result["price_mode"], "reserves")

    def test_raydium_amm_v4_pool_layout(self):
        raw = bytearray(752)
        struct.pack_into("<Q", raw, 0, 6)
        put_key(raw, 336, 1)
        put_key(raw, 368, 2)
        put_key(raw, 400, 3)
        put_key(raw, 432, 4)
        result = _decode_pool("raydium_amm_v4", bytes(raw))
        self.assertEqual(result["price_mode"], "reserves")
        self.assertEqual(result["status"], "active")

    def test_raydium_clmm_pool_layout(self):
        raw = bytearray(500)
        raw[:8] = _discriminator("PoolState")
        raw[253:269] = (1 << 64).to_bytes(16, "little")
        put_key(raw, 73, 1)
        put_key(raw, 105, 2)
        put_key(raw, 137, 3)
        put_key(raw, 169, 4)
        result = _decode_pool("raydium_clmm", bytes(raw))
        self.assertEqual(result["price_mode"], "sqrt_price")

    def test_orca_whirlpool_layout(self):
        raw = bytearray(500)
        raw[:8] = _discriminator("Whirlpool")
        raw[65:81] = (1 << 64).to_bytes(16, "little")
        put_key(raw, 101, 1)
        put_key(raw, 133, 2)
        put_key(raw, 181, 3)
        put_key(raw, 213, 4)
        result = _decode_pool("orca_whirlpool", bytes(raw))
        self.assertEqual(result["sqrt_price_x64"], 1 << 64)

    def test_meteora_dlmm_layout(self):
        raw = bytearray(904)
        raw[:8] = bytes([33, 11, 49, 98, 181, 101, 177, 13])
        struct.pack_into("<i", raw, 76, 2)
        struct.pack_into("<H", raw, 80, 25)
        put_key(raw, 88, 1)
        put_key(raw, 120, 2)
        put_key(raw, 152, 3)
        put_key(raw, 184, 4)
        result = _decode_pool("meteora_dlmm", bytes(raw))
        self.assertEqual(result["active_id"], 2)
        self.assertEqual(result["bin_step"], 25)
        self.assertEqual(result["price_mode"], "active_bin")

    def test_invalid_discriminator_rejected(self):
        with self.assertRaises(ValueError):
            _decode_pool("pumpswap", bytes(400))

    def test_eleven_protocol_programs_are_registered(self):
        self.assertEqual(len(PROGRAMS), 11)
        self.assertIn("meteora_dbc", PROGRAMS)
        self.assertIn("moonit_amm", PROGRAMS)

    def test_common_dex_labels_map_to_their_actual_protocol_families(self):
        self.assertEqual(normalize_protocol("pumpfun"), "pumpfun_legacy")
        self.assertEqual(normalize_protocol("pumpfun-amm"), "pumpswap")
        self.assertEqual(normalize_protocol("moonshot"), "moonit_amm")
        self.assertEqual(normalize_protocol("meteora-damm-v2"), "meteora_damm_v2")

    def test_fluxbeam_amm_layout(self):
        raw = bytearray(324)
        raw[1] = 1
        put_key(raw, 35, 1)
        put_key(raw, 67, 2)
        put_key(raw, 131, 3)
        put_key(raw, 163, 4)
        result = _decode_pool("fluxbeam_amm", bytes(raw))
        self.assertEqual(result["vault_base"], _decode_pool("fluxbeam_amm", bytes(raw))["vault_base"])
        self.assertEqual(result["price_mode"], "reserves")

    def test_meteora_damm_v2_layout(self):
        raw = bytearray(1112)
        raw[:8] = bytes([241, 154, 109, 4, 17, 177, 109, 188])
        raw[456:472] = (1 << 64).to_bytes(16, "little")
        put_key(raw, 168, 1)
        put_key(raw, 200, 2)
        put_key(raw, 232, 3)
        put_key(raw, 264, 4)
        result = _decode_pool("meteora_damm_v2", bytes(raw))
        self.assertEqual(result["sqrt_price_x64"], 1 << 64)
        self.assertEqual(result["price_mode"], "sqrt_price")

    def test_meteora_dbc_layout(self):
        raw = bytearray(424)
        raw[:8] = bytes([213, 224, 5, 209, 98, 69, 119, 92])
        put_key(raw, 72, 1)
        put_key(raw, 136, 2)
        put_key(raw, 168, 3)
        put_key(raw, 200, 4)
        struct.pack_into("<Q", raw, 232, 1000)
        struct.pack_into("<Q", raw, 240, 2000)
        raw[280:296] = (1 << 64).to_bytes(16, "little")
        result = _decode_pool("meteora_dbc", bytes(raw))
        self.assertEqual(result["base_reserve"], 1000)
        self.assertEqual(result["price_mode"], "dbc_curve")

    def test_pumpfun_legacy_bonding_curve_layout(self):
        raw = bytearray(256)
        raw[:8] = bytes([23, 183, 248, 55, 96, 216, 172, 96])
        struct.pack_into("<QQQQQ", raw, 8, 10_000, 20_000, 8_000, 15_000, 1_000_000)
        result = _decode_pool("pumpfun_legacy", bytes(raw))
        self.assertEqual(result["virtual_quote_reserves"], 20_000)
        self.assertEqual(result["price_mode"], "pump_curve")
        raw[48] = 1
        with self.assertRaises(ValueError):
            _decode_pool("pumpfun_legacy", bytes(raw))

    def test_moonit_curve_account_layout(self):
        raw = bytearray(409)
        raw[:8] = bytes([8, 91, 83, 28, 132, 216, 248, 22])
        struct.pack_into("<QQ", raw, 8, 1_000_000, 900_000)
        put_key(raw, 24, 7)
        raw[56] = 9
        raw[57] = 0
        raw[58] = 1
        result = _decode_pool("moonit_amm", bytes(raw))
        self.assertEqual(result["base_mint"], _decode_pool("moonit_amm", bytes(raw))["base_mint"])
        self.assertEqual(result["token_decimals"], 9)
        self.assertEqual(result["price_mode"], "moonit_curve")



class OnchainStreamTests(unittest.TestCase):
    def test_stream_snapshot_age_gate(self):
        key = ("pool", "raydium_cpmm", "mint")
        with onchain_stream._lock:
            onchain_stream._snapshots[key] = {
                "data": {"priceUsd": "1.23"},
                "observed_at": 1000.0
            }
        with mock.patch("onchain_stream.time.time", return_value=1000.4):
            fresh = onchain_stream.get_latest_snapshot(*key, max_age=1.0)
            stale = onchain_stream.get_latest_snapshot(*key, max_age=0.2)
        self.assertEqual(fresh["priceUsd"], "1.23")
        self.assertIsNone(stale)
        with onchain_stream._lock:
            onchain_stream._snapshots.pop(key, None)

    def test_websocket_url_derives_from_rpc_url(self):
        with mock.patch.dict("os.environ", {"SOLANA_WS_URL": "", "SOLANA_RPC_URL": "https://rpc.example"}, clear=False):
            self.assertEqual(onchain_stream._rpc_ws_url(), "wss://rpc.example")

    def test_sync_removes_closed_position_pool_subscriptions_and_cache(self):
        key = ("pool-closed", "raydium_cpmm", "mint")
        with onchain_stream._lock:
            onchain_stream._watches[key] = True
            onchain_stream._snapshots[key] = {
                "data": {"priceUsd": "1"},
                "observed_at": 1000.0
            }
        self.assertEqual(onchain_stream.sync_watched_pools([]), 0)
        with onchain_stream._lock:
            self.assertNotIn(key, onchain_stream._watches)
            self.assertNotIn(key, onchain_stream._snapshots)

class IndicatorTests(unittest.TestCase):
    def test_ohlcv_indicators_return_technical_confirmation(self):
        candles = []
        for index in range(50):
            close = 1.0 + index * 0.01
            candles.append([1700000000 + index * 60, close - 0.005, close + 0.01,
                            close - 0.01, close, 100 + index])
        result = calculate_indicators(candles)
        self.assertTrue(result["ready"])
        self.assertIn("rsi_14", result)
        self.assertGreater(result["ema_9"], result["ema_21"])
        self.assertTrue(result["ema_cross_bullish"])
        self.assertIn("volume_spike_ratio", result)

    def test_insufficient_candles_are_not_fabricated(self):
        result = calculate_indicators([[1, 1, 1, 1, 1, 1]] * 10)
        self.assertFalse(result["ready"])


class StrategyRuleValidationTests(unittest.TestCase):
    def test_indicator_rule_is_valid(self):
        validate_rules_config({
            "main": {"all": [
                {"field": "rsi_14", "op": "between", "value": [30, 75]},
                {"field": "ema_cross_bullish", "op": "eq", "value": 1}
            ]}
        })

    def test_invalid_field_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_rules_config({"main": {"field": "wallet_secret", "op": "eq", "value": 1}})


if __name__ == "__main__":
    unittest.main()
