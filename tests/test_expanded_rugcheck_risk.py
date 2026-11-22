import unittest
from unittest.mock import patch

from rugcheck import analyze_rugcheck_report


def safe_report(**overrides):
    report = {
        "score_normalised": 5,
        "risks": [],
        "mintAuthority": None,
        "freezeAuthority": None,
        "topHolders": [
            {"address": "holder-a", "pct": 10.0},
            {"address": "holder-b", "pct": 8.0},
            {"address": "holder-c", "pct": 6.0},
        ],
        "rugged": False,
        "token": {"mutable": True},
        "totalLPProviders": 25,
        "totalMarketLiquidity": 25000,
        "graphInsidersDetected": 0,
    }
    report.update(overrides)
    return report


class ExpandedRugCheckRiskTests(unittest.TestCase):
    def test_baseline_report_passes_and_exposes_structured_signals(self):
        result = analyze_rugcheck_report(safe_report())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["signals"]["top10_holder_pct"], 24.0)
        self.assertEqual(result["signals"]["total_lp_providers"], 25)

    def test_rugged_flag_is_hard_rejection(self):
        result = analyze_rugcheck_report(safe_report(rugged=True))
        self.assertEqual(result["status"], "REJECT")
        self.assertTrue(result["signals"]["rugged"])

    def test_immutable_token_with_active_freeze_authority_is_rejected(self):
        report = safe_report(freezeAuthority="FreezeAuthority111")
        report["token"] = {"mutable": False}
        result = analyze_rugcheck_report(report)
        self.assertEqual(result["status"], "REJECT")
        self.assertIn("Immutable token still has an active freeze authority", result["reasons"])

    def test_top_holder_percentages_are_sorted_before_top_ten_sum(self):
        holders = [{"address": f"h{i}", "pct": value} for i, value in enumerate(
            [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 40]
        )]
        result = analyze_rugcheck_report(safe_report(topHolders=holders))
        self.assertEqual(result["signals"]["top10_holder_pct"], 49.0)
        self.assertEqual(result["signals"]["top_holder_pct"], 40.0)

    def test_extreme_top_ten_concentration_rejected(self):
        holders = [{"address": f"h{i}", "pct": 9.0} for i in range(9)]
        holders.append({"address": "h9", "pct": 10.0})
        result = analyze_rugcheck_report(safe_report(topHolders=holders))
        self.assertEqual(result["status"], "REJECT")
        self.assertIn("Extreme top-ten holder concentration", result["reasons"])

    def test_creator_share_derived_from_raw_balance_and_supply(self):
        result = analyze_rugcheck_report(safe_report(
            creator="creator111",
            creatorBalance=15,
            tokenSupply="100",
        ))
        self.assertEqual(result["signals"]["creator_pct"], 15.0)
        self.assertGreaterEqual(result["medium_risks"], 1)

    def test_creator_share_above_critical_threshold_rejected(self):
        result = analyze_rugcheck_report(safe_report(
            creator="creator111",
            creatorBalance=25,
            tokenSupply="100",
        ))
        self.assertEqual(result["status"], "REJECT")
        self.assertIn("Creator holds a critical share of supply", result["reasons"])

    def test_transfer_fee_and_active_fee_authority_are_counted(self):
        result = analyze_rugcheck_report(safe_report(
            transferFee={"pct": 2.0, "authority": "FeeAuthority111"}
        ))
        self.assertEqual(result["signals"]["transfer_fee_pct"], 2.0)
        self.assertTrue(result["signals"]["transfer_fee_authority_active"])
        self.assertGreaterEqual(result["medium_risks"], 2)

    def test_excessive_transfer_fee_rejected(self):
        result = analyze_rugcheck_report(safe_report(transferFeePct=15.0))
        self.assertEqual(result["status"], "REJECT")

    def test_graph_insiders_and_low_lp_provider_count_raise_risk(self):
        result = analyze_rugcheck_report(safe_report(
            graphInsidersDetected=3,
            totalLPProviders=1,
        ))
        self.assertEqual(result["status"], "REJECT")
        self.assertGreaterEqual(result["high_risks"], 1)
        self.assertGreaterEqual(result["medium_risks"], 1)

    def test_active_update_authority_is_reported_as_medium_risk(self):
        result = analyze_rugcheck_report(safe_report(
            tokenUpdateAuthority="UpdateAuthority111"
        ))
        self.assertEqual(result["update_authority"], "ACTIVE")
        self.assertGreaterEqual(result["medium_risks"], 1)

    def test_missing_essential_fields_remain_unknown(self):
        report = safe_report()
        report.pop("topHolders")
        result = analyze_rugcheck_report(report)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["safe"])

    def test_thresholds_can_be_tuned_without_code_changes(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MAX_TRANSFER_FEE_PCT": "5"}):
            result = analyze_rugcheck_report(safe_report(transferFeePct=6))
        self.assertEqual(result["status"], "REJECT")


if __name__ == "__main__":
    unittest.main()
