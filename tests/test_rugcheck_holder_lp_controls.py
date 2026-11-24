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
        "tokenType": "regular",
    }
    data.update(overrides)
    return data


class RugCheckHolderAndLpControlTests(unittest.TestCase):
    def test_minimum_unique_holder_floor_passes_when_met(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_HOLDERS": "200"}):
            result = analyze_rugcheck_report(report())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["signals"]["unique_holders"], 250)

    def test_minimum_unique_holder_floor_rejects_low_count(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_HOLDERS": "300"}):
            result = analyze_rugcheck_report(report())
        self.assertEqual(result["status"], "REJECT")
        self.assertIn("Unique holder count is below configured minimum", result["reasons"])

    def test_minimum_unique_holder_floor_fails_closed_when_missing(self):
        data = report()
        data.pop("totalHolders")
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_HOLDERS": "100"}):
            result = analyze_rugcheck_report(data)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["safe"])
        self.assertIsNone(result["signals"]["unique_holders"])

    def test_invalid_fractional_holder_count_is_unknown(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_HOLDERS": "10"}):
            result = analyze_rugcheck_report(report(totalHolders=10.5))
        self.assertEqual(result["status"], "UNKNOWN")

    def test_regular_token_lp_lock_floor_rejects_low_lock(self):
        data = report(risks=[{
            "name": "LP Locked",
            "description": "Liquidity lock",
            "value": "50%",
            "level": "info",
        }])
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_REGULAR_LP_LOCK_PCT": "80"}):
            result = analyze_rugcheck_report(data)
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["signals"]["lp_lock_pct"], 50.0)
        self.assertEqual(result["signals"]["lp_lock_required_pct"], 80.0)

    def test_regular_token_missing_lp_lock_is_not_required(self):
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_REGULAR_LP_LOCK_PCT": "80"}):
            result = analyze_rugcheck_report(report())
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["safe"])

    def test_regular_token_with_sufficient_lp_lock_passes(self):
        data = report(risks=[{
            "name": "LP Locked",
            "description": "Liquidity lock",
            "value": "95%",
            "level": "info",
        }])
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_REGULAR_LP_LOCK_PCT": "80"}):
            result = analyze_rugcheck_report(data)
        self.assertEqual(result["status"], "PASS")

    def test_pump_legacy_lp_lock_variable_remains_compatible(self):
        data = report(tokenType="pumpfun", risks=[{
            "name": "LP Locked",
            "description": "Liquidity lock",
            "value": "90%",
            "level": "info",
        }])
        with patch.dict("os.environ", {"TROJAN_RUGCHECK_MIN_LP_LOCK_PCT": "80"}):
            result = analyze_rugcheck_report(data)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["signals"]["lp_lock_required_pct"], 80.0)

    def test_pump_specific_lp_lock_variable_overrides_legacy_value(self):
        data = report(tokenType="pumpfun", risks=[{
            "name": "LP Locked",
            "description": "Liquidity lock",
            "value": "70%",
            "level": "info",
        }])
        with patch.dict("os.environ", {
            "TROJAN_RUGCHECK_MIN_LP_LOCK_PCT": "60",
            "TROJAN_RUGCHECK_MIN_PUMPFUN_LP_LOCK_PCT": "80",
        }):
            result = analyze_rugcheck_report(data)
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["signals"]["lp_lock_required_pct"], 80.0)


if __name__ == "__main__":
    unittest.main()
