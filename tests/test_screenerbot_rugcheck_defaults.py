import unittest

from rugcheck import analyze_rugcheck_report


def safe_report(**overrides):
    data = {
        "score_normalised": 5,
        "risks": [],
        "mintAuthority": None,
        "freezeAuthority": None,
        "topHolders": [
            {"address": "holder-a", "pct": 10.0, "insider": False},
            {"address": "holder-b", "pct": 8.0, "insider": False},
            {"address": "holder-c", "pct": 6.0, "insider": False},
        ],
        "totalHolders": 250,
        "rugged": False,
        "token": {"mutable": True},
        "totalLPProviders": 25,
        "totalMarketLiquidity": 25000,
        "graphInsidersDetected": 0,
        "tokenType": "standard",
    }
    data.update(overrides)
    return data


class ScreenerBotRugCheckDefaultParityTests(unittest.TestCase):
    def test_default_minimum_holder_count_matches_reference(self):
        self.assertEqual(
            analyze_rugcheck_report(safe_report(totalHolders=149))["status"],
            "REJECT",
        )

    def test_default_top_holder_limit_matches_reference(self):
        result = analyze_rugcheck_report(safe_report(topHolders=[
            {"pct": 31.0}, {"pct": 8.0}, {"pct": 6.0}
        ]))
        self.assertEqual(result["status"], "REJECT")

    def test_default_top_three_limit_matches_reference(self):
        result = analyze_rugcheck_report(safe_report(topHolders=[
            {"pct": 20.0}, {"pct": 18.0}, {"pct": 13.0}
        ]))
        self.assertEqual(result["status"], "REJECT")

    def test_default_graph_insider_share_limit_matches_reference(self):
        result = analyze_rugcheck_report(safe_report(
            graphInsidersDetected=6, totalHolders=100
        ))
        self.assertEqual(result["status"], "REJECT")

    def test_default_top_ten_insider_count_matches_reference(self):
        holders = [
            {"pct": 2.0, "insider": True},
            {"pct": 2.0, "insider": True},
            {"pct": 2.0, "insider": True},
            *[{"pct": 1.0, "insider": False} for _ in range(7)],
        ]
        result = analyze_rugcheck_report(safe_report(topHolders=holders))
        self.assertEqual(result["status"], "REJECT")

    def test_default_creator_balance_limit_matches_reference(self):
        result = analyze_rugcheck_report(safe_report(
            creatorBalance=11, tokenSupply=100
        ))
        self.assertEqual(result["status"], "REJECT")

    def test_default_transfer_fee_limit_matches_reference(self):
        result = analyze_rugcheck_report(safe_report(transferFeePct=5.1))
        self.assertEqual(result["status"], "REJECT")

    def test_default_pumpfun_lp_lock_floor_and_missing_data(self):
        low = analyze_rugcheck_report(safe_report(
            tokenType="pumpfun",
            risks=[{"name": "LP Lock", "description": "Liquidity locked 49%", "level": "info"}],
        ))
        missing = analyze_rugcheck_report(safe_report(tokenType="pumpfun"))
        self.assertEqual(low["status"], "REJECT")
        self.assertEqual(missing["status"], "UNKNOWN")

    def test_default_regular_lp_lock_floor_applies_when_present(self):
        low = analyze_rugcheck_report(safe_report(
            risks=[{"name": "LP Lock", "description": "Liquidity locked 49%", "level": "info"}],
        ))
        self.assertEqual(low["status"], "REJECT")

    def test_missing_regular_lp_lock_is_not_a_missing_data_rejection(self):
        result = analyze_rugcheck_report(safe_report(tokenType="standard"))
        self.assertEqual(result["status"], "PASS")

    def test_default_insider_total_share_limit_matches_reference(self):
        holders = [
            {"pct": 11.0, "insider": True},
            {"pct": 10.0, "insider": True},
            *[{"pct": 1.0, "insider": False} for _ in range(8)],
        ]
        result = analyze_rugcheck_report(safe_report(topHolders=holders))
        self.assertEqual(result["status"], "REJECT")


if __name__ == "__main__":
    unittest.main()
