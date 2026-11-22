import unittest
from unittest.mock import patch

import main


class RugCheckReportPropagationTests(unittest.TestCase):
    def setUp(self):
        self.safe = {
            "analysis": {
                "status": "PASS",
                "safe": True,
                "score": 5,
                "high_risks": 0,
                "reasons": ["Mint authority disabled"],
                "signals": {"top10_holder_pct": 42.0, "creator_pct": 3.0},
            },
            "report": {"score_normalised": 5},
        }

    @patch("main.get_rugcheck_report")
    def test_check_rugcheck_fetches_report_when_not_supplied(self, fetch):
        fetch.return_value = self.safe
        self.assertTrue(main.check_rugcheck("Mint111"))
        fetch.assert_called_once_with("Mint111")

    @patch("main.get_rugcheck_report")
    def test_supplied_report_is_reused_without_a_second_api_call(self, fetch):
        self.assertTrue(main.check_rugcheck("Mint111", self.safe))
        fetch.assert_not_called()

    @patch("main.get_rugcheck_report")
    def test_explicit_missing_report_fails_without_retry(self, fetch):
        self.assertFalse(main.check_rugcheck("Mint111", None))
        fetch.assert_not_called()

    @patch("main.get_rugcheck_report")
    def test_rejected_report_is_not_overridden_by_valid_score(self, fetch):
        report = {
            "analysis": {
                "status": "REJECT", "safe": False,
                "score": 1, "high_risks": 0, "reasons": ["RUGGED"]
            }
        }
        fetch.return_value = report
        self.assertFalse(main.check_rugcheck("Mint111"))
        fetch.assert_called_once_with("Mint111")

    @patch("main.get_rugcheck_report")
    def test_active_high_risk_signal_blocks_even_when_status_says_pass(self, fetch):
        report = {
            "analysis": {
                "status": "PASS", "safe": True,
                "score": 1, "high_risks": 1, "reasons": ["Top holder concentration"]
            }
        }
        fetch.return_value = report
        self.assertFalse(main.check_rugcheck("Mint111"))


if __name__ == "__main__":
    unittest.main()
