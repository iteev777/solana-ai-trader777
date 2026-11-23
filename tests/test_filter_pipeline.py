import os
import unittest
from unittest.mock import patch

import filter_pipeline


class FilterPipelineTests(unittest.TestCase):
    def setUp(self):
        self.token = {
            "address": "Mint111",
            "symbol": "TROJ",
            "name": "Trojan",
            "priceUsd": "0.001",
            "liquidity": {"usd": 12000},
            "volume": {"m5": 5000, "h24": 100000},
            "txns": {"m5": {"buys": 70, "sells": 30}},
            "priceChange": {"m5": 2.5},
        }

    def test_all_stages_pass_with_valid_market_data(self):
        result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            [row["stage"] for row in result["decisions"]],
            ["metadata", "onchain", "market", "age", "valuation", "pool_metrics", "activity", "momentum", "provenance", "quality"],
        )

    def test_numeric_symbol_is_rejected(self):
        token = dict(self.token, symbol="12345")
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "NUMERIC_SYMBOL")

    def test_missing_symbol_is_rejected_like_screenerbot_symbol_stage(self):
        token = dict(self.token)
        token.pop("symbol")
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "EMPTY_SYMBOL")

    def test_empty_symbol_can_be_configured_to_wait(self):
        token = dict(self.token, symbol="   ")
        with patch.dict(os.environ, {"TROJAN_FILTER_REJECT_EMPTY_SYMBOLS": "false"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "SYMBOL_UNKNOWN")

    def test_single_character_ascii_letter_is_not_rejected_by_default(self):
        token = dict(self.token, symbol="A")
        self.assertEqual(filter_pipeline.evaluate_token(token, "Mint111")["status"], "PASS")

    def test_non_letter_single_character_can_be_rejected_when_enabled(self):
        token = dict(self.token, symbol="!")
        with patch.dict(os.environ, {"TROJAN_FILTER_REJECT_SINGLE_CHAR_SYMBOL": "true"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "SUSPICIOUS_SINGLE_CHARACTER_SYMBOL")

    def test_immutable_metadata_with_active_freeze_authority_is_rejected(self):
        token = dict(self.token, mutable=False, freezeAuthority="Authority111")
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "IMMUTABLE_WITH_ACTIVE_FREEZE_AUTHORITY")

    def test_known_scam_authority_flag_is_rejected(self):
        token = dict(self.token, known_scam_authority=True)
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "KNOWN_SCAM_AUTHORITY")

    def test_onchain_filter_can_be_disabled_explicitly(self):
        token = dict(self.token, mutable=False, freezeAuthority="Authority111")
        with patch.dict(os.environ, {"TROJAN_FILTER_ONCHAIN_ENABLED": "false"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "PASS")

    def test_onchain_filter_accepts_numeric_false_environment_value(self):
        token = dict(self.token, mutable=False, freezeAuthority="Authority111")
        with patch.dict(os.environ, {"TROJAN_FILTER_ONCHAIN_ENABLED": "0"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "PASS")

    def test_zero_liquidity_rejected_but_missing_liquidity_waits(self):
        zero = dict(self.token, liquidity={"usd": 0})
        missing = dict(self.token)
        missing.pop("liquidity")
        self.assertEqual(
            filter_pipeline.evaluate_token(zero, "Mint111")["reason"],
            "ZERO_LIQUIDITY",
        )
        self.assertEqual(
            filter_pipeline.evaluate_token(missing, "Mint111")["status"],
            "WAIT",
        )

    def test_base_mint_mismatch_is_rejected(self):
        token = dict(self.token, baseToken={"address": "OtherMint"})
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["reason"], "BASE_MINT_MISMATCH")

    def test_optional_volume_threshold_waits_on_missing_data(self):
        token = dict(self.token)
        token.pop("volume")
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_VOLUME_5M_USD": "1000"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "VOLUME_5M_UNKNOWN")

    def test_optional_volume_threshold_rejects_low_activity_as_wait(self):
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_VOLUME_5M_USD": "6000"}):
            result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "VOLUME_5M_BELOW_MINIMUM")

    def test_buy_ratio_gate_rejects_sell_dominated_flow(self):
        token = dict(self.token, txns={"m5": {"buys": 20, "sells": 80}})
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_BUY_RATIO": "0.55"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "BUY_PRESSURE_BELOW_MINIMUM")

    def test_momentum_cap_blocks_extended_pump(self):
        token = dict(self.token, priceChange={"m5": 75})
        with patch.dict(os.environ, {"TROJAN_FILTER_MAX_CHANGE_5M_PCT": "40"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "M5_MOVE_EXTENDED")


    def test_pool_age_minimum_holds_too_new_pool(self):
        import time
        token = dict(self.token, pairCreatedAt=(time.time() - 30) * 1000)
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_POOL_AGE_MINUTES": "2"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "POOL_TOO_NEW")

    def test_negative_valuation_is_rejected(self):
        token = dict(self.token, fdv=-1)
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "NEGATIVE_VALUATION")

    def test_source_agreement_threshold_waits_when_not_met(self):
        token = dict(self.token, discovery_sources=["dexscreener"])
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_DISCOVERY_SOURCES": "2"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "SOURCE_AGREEMENT_BELOW_MINIMUM")

    def test_quality_threshold_requires_a_real_quality_score(self):
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_QUALITY_SCORE": "60"}):
            result = filter_pipeline.evaluate_token(self.token, "Mint111")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "CANDIDATE_QUALITY_UNKNOWN")

    def test_quality_threshold_accepts_sufficient_score(self):
        token = dict(self.token, candidate_quality={"score": 82, "tier": "strong"})
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_QUALITY_SCORE": "60"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "PASS")

    def test_filter_stages_short_circuit_after_first_rejection(self):
        token = dict(self.token, symbol="")
        with patch.object(filter_pipeline, "_activity_stage", side_effect=AssertionError("later stage ran")):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["reason"], "EMPTY_SYMBOL")
        self.assertEqual([item["stage"] for item in result["decisions"]], ["metadata"])

    def test_invalid_boolean_environment_value_preserves_safe_default(self):
        token = dict(self.token, mutable=False, freezeAuthority="Authority111")
        with patch.dict(os.environ, {"TROJAN_FILTER_ONCHAIN_ENABLED": "flase"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["reason"], "IMMUTABLE_WITH_ACTIVE_FREEZE_AUTHORITY")

    def test_unknown_authority_is_not_treated_as_revoked(self):
        token = dict(self.token, mutable=False, freezeAuthority="unknown")
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["reason"], "IMMUTABLE_WITH_ACTIVE_FREEZE_AUTHORITY")

    def test_string_false_mutability_is_normalized(self):
        token = dict(self.token, mutable="false", freezeAuthority="Authority111")
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["reason"], "IMMUTABLE_WITH_ACTIVE_FREEZE_AUTHORITY")

    def test_metadata_name_falls_back_to_base_token_when_top_level_is_empty(self):
        token = dict(self.token, name=None, baseToken={"name": "Base Name", "address": "Mint111"})
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["decisions"][0]["metrics"]["name"], "Base Name")

    def test_token_mint_mismatch_is_rejected(self):
        token = dict(self.token, address="DifferentMint")
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["reason"], "TOKEN_MINT_MISMATCH")

    def test_quality_score_outside_unit_interval_is_rejected(self):
        token = dict(self.token, candidate_quality={"score": 101, "tier": "strong"})
        with patch.dict(os.environ, {"TROJAN_FILTER_MIN_QUALITY_SCORE": "60"}):
            result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["reason"], "INVALID_CANDIDATE_QUALITY_SCORE")

    def test_missing_optional_momentum_does_not_block_by_default(self):
        token = dict(self.token)
        token.pop("priceChange")
        result = filter_pipeline.evaluate_token(token, "Mint111")
        self.assertEqual(result["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
