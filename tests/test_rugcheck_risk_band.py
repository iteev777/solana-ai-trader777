import unittest

import rugcheck


class RugCheckRiskBandTests(unittest.TestCase):
    def test_reference_band_boundaries(self):
        expected = {
            0: "low",
            30: "low",
            31: "medium",
            60: "medium",
            61: "high",
            100: "high",
        }
        for score, band in expected.items():
            with self.subTest(score=score):
                self.assertEqual(rugcheck.get_security_risk_band(score), band)

    def test_missing_or_invalid_scores_have_no_band(self):
        for score in (None, True, -1, 101, 30.5, "unknown", float("nan")):
            with self.subTest(score=score):
                self.assertIsNone(rugcheck.get_security_risk_band(score))

    def test_missing_report_exposes_no_risk_band(self):
        report = rugcheck.analyze_rugcheck_report(None)
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertIsNone(report["risk_band"])


if __name__ == "__main__":
    unittest.main()
