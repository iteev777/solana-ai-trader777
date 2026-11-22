import os
import unittest
from unittest.mock import patch

import filter_pipeline


class MultiWindowMarketFilterTests(unittest.TestCase):
    def setUp(self):
        self.token = {
            "address": "Mint111",
            "symbol": "TROJ",
            "name": "Trojan",
            "priceUsd": "0.001",
            "liquidity": {"usd": 12000},
            "volume": {"m5": 5000, "h1": 20000, "h6": 50000, "h24": 100000},
            "txns": {
                "m5": {"buys": 70, "sells": 30},
                "h1": {"buys": 120, "sells": 80},
                "h6": {"buys": 400, "sells": 300},
                "h24": {"buys": 1000, "sells": 900},
            },
            "priceChange": {"m5": 2.5, "h1": 8, "h6": 15, "h24": 35},
        }

    def test_all_windows_pass_and_are_exposed_in_auditable_metrics(self):
        result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "PASS")
        activity = next(x for x in result["decisions"] if x["stage"] == "activity")
        momentum = next(x for x in result["decisions"] if x["stage"] == "momentum")
        self.assertEqual(set(activity["metrics"]["windows"]), {"m5", "h1", "h6", "h24"})
        self.assertEqual(set(momentum["metrics"]["windows"]), {"m5", "h1", "h6", "h24"})
        self.assertEqual(activity["metrics"]["windows"]["h1"]["transactions"], 200)

    def test_h1_volume_floor_waits_when_missing(self):
        token = dict(self.token)
        token["volume"] = dict(self.token["volume"])
        token["volume"].pop("h1")
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_VOLUME_1H_USD": "1000"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "VOLUME_1H_UNKNOWN")

    def test_h6_volume_floor_waits_when_below_threshold(self):
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_VOLUME_6H_USD": "60000"}):
            result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "VOLUME_6H_BELOW_MINIMUM")

    def test_h1_transaction_floor_checks_its_own_window(self):
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_TXNS_1H": "250"}):
            result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "TRANSACTION_COUNT_1H_BELOW_MINIMUM")

    def test_one_sided_transaction_data_is_counted_without_fabricating_other_side(self):
        token = dict(self.token)
        token["txns"] = dict(self.token["txns"])
        token["txns"]["h1"] = {"buys": 40}
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_TXNS_1H": "30"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "PASS")

    def test_h1_buy_ratio_gate_uses_h1_not_m5(self):
        token = dict(self.token)
        token["txns"] = dict(self.token["txns"])
        token["txns"]["h1"] = {"buys": 20, "sells": 80}
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_BUY_RATIO_1H": "0.4"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "BUY_PRESSURE_1H_BELOW_MINIMUM")

    def test_h6_momentum_maximum_blocks_overextended_move(self):
        with patch.dict(os.environ, {"TROJAN_FILTER_MAX_CHANGE_6H_PCT": "12"}):
            result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "MOMENTUM_6H_ABOVE_MAXIMUM")

    def test_h24_momentum_minimum_blocks_deep_downtrend(self):
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_CHANGE_24H_PCT": "-10"}):
            token = dict(self.token)
            token["priceChange"] = dict(self.token["priceChange"], h24=-15)
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "MOMENTUM_24H_BELOW_MINIMUM")

    def test_required_h1_momentum_waits_on_missing_data(self):
        token = dict(self.token)
        token["priceChange"] = dict(self.token["priceChange"])
        token["priceChange"].pop("h1")
        with patch.dict(os.environ, {"TROJAN_FILTER_REQUIRE_1H_CHANGE": "true"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "MOMENTUM_1H_UNKNOWN")

    def test_nonfinite_and_negative_window_data_never_passes(self):
        token = dict(self.token)
        token["volume"] = dict(self.token["volume"], h6=float("nan"))
        token["txns"] = dict(self.token["txns"], h24={"buys": -1, "sells": 3})
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "INVALID_TRANSACTION_COUNT")

    def test_old_single_window_configuration_remains_compatible(self):
        with patch.dict(os.environ, {
            "TROJAN_FILTER_MIN_VOLUME_5M_USD": "6000",
            "TROJAN_FILTER_MAX_CHANGE_5M_PCT": "20",
        }):
            result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "VOLUME_5M_BELOW_MINIMUM")


if __name__ == "__main__":
    unittest.main()
