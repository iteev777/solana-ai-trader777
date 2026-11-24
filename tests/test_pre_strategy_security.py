import os
import unittest
from unittest.mock import patch

import main


SAFE_REPORT = {
    "analysis": {
        "status": "PASS",
        "safe": True,
        "score": 4,
        "high_risks": 0,
        "reasons": [],
    }
}


class PreStrategySecurityTests(unittest.TestCase):
    @patch("main.get_rugcheck_report", return_value=SAFE_REPORT)
    @patch("main.check_rugcheck", return_value=False)
    @patch("main.get_onchain_status")
    def test_rugcheck_rejection_stops_before_rpc_security(self, rpc_status, validate, fetch):
        result = main.evaluate_pre_strategy_security("Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "RUGCHECK_FAILED")
        rpc_status.assert_not_called()

    @patch("main.get_rugcheck_report", return_value=SAFE_REPORT)
    @patch("main.check_rugcheck", return_value=True)
    @patch("main.get_onchain_status", return_value={"rpc_configured": False})
    def test_missing_rpc_is_reported_as_skipped_by_default(self, rpc_status, validate, fetch):
        with patch.dict(os.environ, {}, clear=True):
            result = main.evaluate_pre_strategy_security("Mint111")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["token_security"]["status"], "SKIPPED")
        self.assertEqual(result["token_security"]["reason"], "SOLANA_RPC_NOT_CONFIGURED")

    @patch("main.get_rugcheck_report", return_value=SAFE_REPORT)
    @patch("main.check_rugcheck", return_value=True)
    @patch("main.get_onchain_status", return_value={"rpc_configured": False})
    def test_missing_rpc_fails_closed_when_required(self, rpc_status, validate, fetch):
        with patch.dict(os.environ, {"TROJAN_REQUIRE_ONCHAIN_TOKEN_SECURITY": "true"}):
            result = main.evaluate_pre_strategy_security("Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(
            result["reason"],
            "ONCHAIN_TOKEN_SECURITY_REQUIRED_BUT_RPC_MISSING",
        )

    @patch("main.get_rugcheck_report", side_effect=TimeoutError("provider timeout"))
    def test_rugcheck_transport_error_fails_closed(self, fetch):
        result = main.evaluate_pre_strategy_security("Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "RUGCHECK_UNAVAILABLE")

    @patch("main.get_rugcheck_report", return_value=SAFE_REPORT)
    @patch("main.check_rugcheck", return_value=True)
    @patch("main.get_onchain_status", side_effect=RuntimeError("rpc status failed"))
    def test_rpc_status_error_fails_closed(self, rpc_status, validate, fetch):
        result = main.evaluate_pre_strategy_security("Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "ONCHAIN_TOKEN_SECURITY_UNAVAILABLE")

    @patch("main.get_rugcheck_report", return_value=SAFE_REPORT)
    @patch("main.check_rugcheck", return_value=True)
    @patch("main.get_onchain_status", return_value={"rpc_configured": True})
    @patch("token_security.get_token_security_report", return_value={
        "source": "solana_rpc",
        "mint_authority": "DISABLED",
        "freeze_authority": "DISABLED",
    })
    @patch("token_security.evaluate_token_security", return_value={
        "allowed": False,
        "reason": "MINT_AUTHORITY_ACTIVE_OR_UNKNOWN",
    })
    @patch("blacklist.match_token_security_accounts", return_value=[])
    def test_onchain_security_rejection_prevents_approval(
        self, blacklist_matches, evaluate, fetch_security, rpc_status, validate, fetch
    ):
        result = main.evaluate_pre_strategy_security("Mint111")
        self.assertEqual(result["status"], "REJECT")
        self.assertEqual(result["reason"], "MINT_AUTHORITY_ACTIVE_OR_UNKNOWN")
        fetch_security.assert_called_once_with("Mint111")

    @patch("main.get_rugcheck_report", return_value=SAFE_REPORT)
    @patch("main.check_rugcheck", return_value=True)
    @patch("main.get_onchain_status", return_value={"rpc_configured": True})
    @patch("token_security.get_token_security_report", return_value={
        "source": "solana_rpc",
        "mint_authority": "DISABLED",
        "freeze_authority": "DISABLED",
    })
    @patch("token_security.evaluate_token_security", return_value={
        "allowed": True,
        "reason": "ONCHAIN_AUTHORITIES_REVOKED",
    })
    @patch("blacklist.match_token_security_accounts", return_value=[])
    def test_all_configured_security_gates_pass(
        self, blacklist_matches, evaluate, fetch_security, rpc_status, validate, fetch
    ):
        result = main.evaluate_pre_strategy_security("Mint111")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["reason"], "PRE_STRATEGY_SECURITY_PASSED")
        self.assertEqual(result["token_security"]["source"], "solana_rpc")


if __name__ == "__main__":
    unittest.main()
