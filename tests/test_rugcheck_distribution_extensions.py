import unittest
from unittest.mock import patch

from rugcheck import analyze_rugcheck_report


def report(**overrides):
    data = {
        "score_normalised": 5,
        "risks": [],
        "mintAuthority": None,
        "freezeAuthority": None,
        "topHolders": [
            {"address": "a", "pct": 10, "insider": False},
            {"address": "b", "pct": 8, "insider": False},
            {"address": "c", "pct": 6, "insider": False},
            {"address": "d", "pct": 4, "insider": False},
        ],
        "graphInsidersDetected": 0,
        "totalHolders": 100,
        "tokenType": "standard",
    }
    data.update(overrides)
    return data


class RugCheckDistributionExtensionTests(unittest.TestCase):
    def test_top_three_distribution_is_reported_and_configurable(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MAX_TOP3_HOLDERS_PCT": "70"}):
            result = analyze_rugcheck_report(report(topHolders=[
                {"pct": 30}, {"pct": 25}, {"pct": 20}, {"pct": 2}
            ]))
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["signals"]["top_three_holders_pct"], 75.0)

    def test_top_ten_insider_count_and_total_share_are_reported(self):
        holders = [
            {"pct": 9, "insider": True},
            {"pct": 8, "insider": True},
            {"pct": 7, "insider": True},
            *[{"pct": 2, "insider": False} for _ in range(7)],
        ]
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MAX_INSIDERS_TOP10": "2"}):
            result = analyze_rugcheck_report(report(topHolders=holders))
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["signals"]["top_ten_insider_count"], 3)
        self.assertEqual(result["signals"]["insider_total_pct"], 24.0)

    def test_graph_insider_share_percentage_is_gated(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MAX_GRAPH_INSIDER_SHARE_PCT": "20"}):
            result = analyze_rugcheck_report(report(
                graphInsidersDetected=2, totalHolders=5
            ))
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["signals"]["graph_insider_share_pct"], 40.0)

    def test_required_graph_insider_share_missing_is_unknown(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_REQUIRE_GRAPH_INSIDER_DATA": "true"}):
            result = analyze_rugcheck_report(report(totalHolders=None))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["safe"])

    def test_pump_lp_lock_below_minimum_is_rejected(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_LP_LOCK_PCT": "80"}):
            result = analyze_rugcheck_report(report(
                tokenType="pump_fun",
                risks=[{
                    "name": "LP Lock",
                    "level": "info",
                    "description": "Liquidity is locked 60%",
                }],
            ))
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["signals"]["lp_lock_pct"], 60.0)

    def test_missing_required_pump_lp_lock_is_unknown(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_LP_LOCK_PCT": "80"}):
            result = analyze_rugcheck_report(report(tokenType="pump_fun"))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["safe"])

    def test_non_pump_token_does_not_require_pump_lp_lock_field(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_LP_LOCK_PCT": "80"}):
            result = analyze_rugcheck_report(report(tokenType="standard"))
        self.assertEqual(result["status"], "PASS")

    def test_lp_lock_text_parser_recognizes_locked_and_unlocked(self):
        from rugcheck import _percentage_from_text
        self.assertEqual(_percentage_from_text("Liquidity locked 60%"), 60.0)
        self.assertEqual(_percentage_from_text("LP unlocked"), 0.0)
        self.assertIsNone(_percentage_from_text("partially locked"))

    def test_non_finite_holder_values_are_excluded_from_distribution(self):
        result = analyze_rugcheck_report(report(topHolders=[
            {"pct": float("nan")}, {"pct": 10}, {"pct": 8}, {"pct": 6}
        ]))
        self.assertEqual(result["signals"]["top_three_holders_pct"], 24.0)


if __name__ == "__main__":
    unittest.main()
