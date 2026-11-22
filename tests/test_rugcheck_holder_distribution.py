import unittest
from unittest.mock import patch

import rugcheck


def safe_report(**updates):
    report = {
        "score_normalised": 5,
        "risks": [],
        "mintAuthority": None,
        "freezeAuthority": None,
        "topHolders": [
            {"pct": 10, "insider": False},
            {"pct": 8, "insider": False},
            {"pct": 6, "insider": False},
            {"pct": 4, "insider": False},
        ],
        "tokenType": "standard",
        "graphInsidersDetected": 1,
        "totalHolders": 100,
    }
    report.update(updates)
    return report


class RugCheckHolderDistributionTests(unittest.TestCase):
    def test_reports_top_three_holder_concentration(self):
        report = safe_report(topHolders=[
            {"pct": 40}, {"pct": 30}, {"pct": 20}, {"pct": 2}
        ])
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MAX_TOP3_HOLDERS_PCT": "80"}):
            result = rugcheck.analyze_rugcheck_report(report)
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["top_three_holders_pct"], 90.0)
        self.assertIn("Top three holder concentration exceeds configured maximum", result["reasons"])

    def test_top_ten_insider_count_and_total_percentage_are_gated(self):
        holders = [
            {"pct": 8, "insider": True},
            {"pct": 7, "insider": True},
            {"pct": 6, "insider": True},
            *[{"pct": 1, "insider": False} for _ in range(7)],
        ]
        with patch.dict("os.environ", {
            "TROJAN_RUGCHECK_MAX_INSIDERS_TOP10": "2",
            "TROJAN_RUGCHECK_MAX_INSIDER_TOTAL_PCT": "25",
        }):
            result = rugcheck.analyze_rugcheck_report(safe_report(topHolders=holders))
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["top_ten_insider_count"], 3)
        self.assertEqual(result["insider_total_pct"], 21.0)

    def test_graph_insider_share_is_calculated_and_gated(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MAX_GRAPH_INSIDER_SHARE_PCT": "10"}):
            result = rugcheck.analyze_rugcheck_report(
                safe_report(graphInsidersDetected=15, totalHolders=50)
            )
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["graph_insider_share_pct"], 30.0)

    def test_required_graph_insider_data_fails_closed_when_missing(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_REQUIRE_GRAPH_INSIDER_DATA": "true"}):
            result = rugcheck.analyze_rugcheck_report(
                safe_report(graphInsidersDetected=None, totalHolders=None)
            )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["safe"])

    def test_pump_token_lp_lock_is_required_only_when_configured(self):
        report = safe_report(tokenType="pump_fun", risks=[
            {"name": "LP Lock", "description": "Liquidity is locked 60%"}
        ])
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_LP_LOCK_PCT": "80"}):
            result = rugcheck.analyze_rugcheck_report(report)
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["lp_lock_pct"], 60.0)

    def test_missing_required_pump_lp_lock_data_is_unknown_not_pass(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_LP_LOCK_PCT": "80"}):
            result = rugcheck.analyze_rugcheck_report(safe_report(tokenType="pump_fun"))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["safe"])

    def test_non_pump_tokens_are_not_rejected_for_absent_pump_lp_lock_data(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_LP_LOCK_PCT": "80"}):
            result = rugcheck.analyze_rugcheck_report(safe_report(tokenType="standard"))
        self.assertEqual(result["status"], "PASS")

    def test_lp_lock_percentage_parser_handles_unlocked_and_locked_labels(self):
        self.assertEqual(rugcheck._percentage_from_text("LP unlocked"), 0.0)
        self.assertEqual(rugcheck._percentage_from_text("100% locked"), 100.0)
        self.assertEqual(rugcheck._percentage_from_text("partial lock"), None)

    def test_non_finite_holder_percentages_do_not_enter_top_three_sum(self):
        report = safe_report(topHolders=[
            {"pct": float("nan")}, {"pct": 10}, {"pct": 8}, {"pct": 6}
        ])
        result = rugcheck.analyze_rugcheck_report(report)
        self.assertEqual(result["top_three_holders_pct"], 24.0)


if __name__ == "__main__":
    unittest.main()
