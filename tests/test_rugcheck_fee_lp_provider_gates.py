import unittest
from unittest.mock import patch

from rugcheck import analyze_rugcheck_report


def safe_report(**overrides):
    data = {
        "score_normalised": 5,
        "risks": [],
        "mintAuthority": None,
        "freezeAuthority": None,
        "topHolders": [
            {"address": "holder-a", "pct": 10.0},
            {"address": "holder-b", "pct": 8.0},
            {"address": "holder-c", "pct": 6.0},
        ],
        "totalHolders": 250,
        "rugged": False,
        "token": {"mutable": True},
        "totalLPProviders": 25,
        "totalMarketLiquidity": 25000,
        "graphInsidersDetected": 0,
    }
    data.update(overrides)
    return data


class RugCheckFeeAndLpProviderTests(unittest.TestCase):
    def test_minimum_lp_provider_floor_passes_when_met(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_LP_PROVIDERS": "20"}):
            result = analyze_rugcheck_report(safe_report())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["signals"]["total_lp_providers"], 25)

    def test_minimum_lp_provider_floor_rejects_low_count(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_LP_PROVIDERS": "30"}):
            result = analyze_rugcheck_report(safe_report())
        self.assertEqual(result["status"], "REJECT")
        self.assertIn("LP provider count is below configured minimum", result["reasons"])

    def test_minimum_lp_provider_floor_waits_on_missing_count(self):
        data = safe_report()
        data.pop("totalLPProviders")
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_LP_PROVIDERS": "5"}):
            result = analyze_rugcheck_report(data)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(result["signals"]["total_lp_providers"])

    def test_negative_lp_provider_count_is_not_accepted(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_LP_PROVIDERS": "1"}):
            result = analyze_rugcheck_report(safe_report(totalLPProviders=-3))
        self.assertEqual(result["status"], "UNKNOWN")

    def test_block_transfer_fee_tokens_rejects_any_positive_fee(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_BLOCK_TRANSFER_FEE_TOKENS": "true"}):
            result = analyze_rugcheck_report(safe_report(transferFeePct=0.01))
        self.assertEqual(result["status"], "REJECT")
        self.assertIn("Token-2022 transfer-fee tokens are blocked by configuration", result["reasons"])
        self.assertTrue(result["signals"]["transfer_fee_block_enabled"])

    def test_zero_transfer_fee_is_allowed_when_block_is_enabled(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_BLOCK_TRANSFER_FEE_TOKENS": "true"}):
            result = analyze_rugcheck_report(safe_report(transferFeePct=0))
        self.assertEqual(result["status"], "PASS")

    def test_required_transfer_fee_data_holds_missing_report(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_REQUIRE_TRANSFER_FEE_DATA": "true"}):
            result = analyze_rugcheck_report(safe_report())
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertTrue(result["signals"]["transfer_fee_data_required"])

    def test_invalid_negative_transfer_fee_is_rejected(self):
        result = analyze_rugcheck_report(safe_report(transferFeePct=-1))
        self.assertEqual(result["status"], "REJECT")

    def test_invalid_transfer_fee_over_one_hundred_is_rejected(self):
        result = analyze_rugcheck_report(safe_report(transferFeePct=101))
        self.assertEqual(result["status"], "REJECT")

    def test_malformed_present_transfer_fee_data_is_unknown(self):
        result = analyze_rugcheck_report(safe_report(transferFeePct="not-a-number"))
        self.assertEqual(result["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
