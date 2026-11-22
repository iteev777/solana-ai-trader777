import os
import unittest
from unittest.mock import patch

import filter_pipeline


class ExtendedMarketBoundsTests(unittest.TestCase):
    def setUp(self):
        self.token = {
            "address": "Mint111",
            "symbol": "TROJ",
            "name": "Trojan",
            "priceUsd": "0.001",
            "liquidity": {"usd": 12000},
            "fdv": 100000,
            "marketCap": 80000,
            "volume": {"m5": 5000, "h1": 20000, "h6": 50000, "h24": 100000},
            "txns": {"m5": {"buys": 70, "sells": 30}},
            "priceChange": {"m5": 2.5},
        }

    def test_optional_metadata_assets_do_not_block_by_default(self):
        result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "PASS")

    def test_name_requirement_waits_for_missing_name(self):
        token = dict(self.token)
        token.pop("name")
        with patch.dict(os.environ, {"TROJAN_FILTER_REQUIRE_NAME_AND_SYMBOL": "true"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "NAME_UNKNOWN")

    def test_logo_requirement_waits_when_logo_missing_even_if_name_is_optional(self):
        with patch.dict(os.environ, {"TROJAN_FILTER_REQUIRE_LOGO_URL": "true"}):
            result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "LOGO_URL_MISSING")

    def test_logo_requirement_accepts_https_image_metadata(self):
        token = dict(self.token, info={"imageUrl": "https://example.com/logo.png"})
        with patch.dict(os.environ, {"TROJAN_FILTER_REQUIRE_LOGO_URL": "true"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "PASS")

    def test_website_requirement_accepts_provider_website_list(self):
        token = dict(self.token, websites=[{"url": "https://example.com"}])
        with patch.dict(os.environ, {"TROJAN_FILTER_REQUIRE_WEBSITE_URL": "true"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "PASS")

    def test_non_letter_single_character_symbol_rejected(self):
        token = dict(self.token, symbol="$")
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "SUSPICIOUS_SINGLE_CHARACTER_SYMBOL")

    def test_control_character_only_symbol_is_not_treated_as_valid(self):
        token = dict(self.token, symbol=" \x00 \x00 ")
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "SYMBOL_UNKNOWN")

    def test_liquidity_maximum_rejects_too_deep_pool_when_configured(self):
        with patch.dict(os.environ, {"TROJAN_FILTER_MAX_LIQUIDITY_USD": "10000"}):
            result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "LIQUIDITY_ABOVE_MAXIMUM")

    def test_fdv_minimum_waits_when_data_missing(self):
        token = dict(self.token)
        token.pop("fdv")
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_FDV_USD": "50000"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "FDV_UNKNOWN")

    def test_fdv_minimum_rejects_value_below_floor(self):
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_FDV_USD": "150000"}):
            result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "FDV_BELOW_MINIMUM")

    def test_fdv_maximum_rejects_value_above_ceiling(self):
        with patch.dict(os.environ, {"TROJAN_FILTER_MAX_FDV_USD": "90000"}):
            result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "FDV_ABOVE_MAXIMUM")

    def test_market_cap_minimum_and_maximum_are_independent(self):
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_MARKET_CAP_USD": "90000"}):
            result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "MARKET_CAP_BELOW_MINIMUM")
        with patch.dict(os.environ, {"TROJAN_FILTER_MAX_MARKET_CAP_USD": "70000"}):
            result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "MARKET_CAP_ABOVE_MAXIMUM")

    def test_missing_market_cap_waits_only_when_positive_floor_is_configured(self):
        token = dict(self.token)
        token.pop("marketCap")
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_MARKET_CAP_USD": "1000"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "MARKET_CAP_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
