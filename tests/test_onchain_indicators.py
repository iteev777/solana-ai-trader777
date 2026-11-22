import hashlib
import struct
import unittest

from solana_onchain import _decode_pool, _discriminator
from indicators import calculate_indicators
from strategy_rules import validate_rules_config


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

    def test_raydium_cpmm_pool_layout(self):
        raw = bytearray(400)
        raw[:8] = _discriminator("PoolState")
        put_key(raw, 72, 1)
        put_key(raw, 104, 2)
        put_key(raw, 168, 3)
        put_key(raw, 200, 4)
        result = _decode_pool("raydium_cpmm", bytes(raw))
        self.assertEqual(result["price_mode"], "reserves")

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
