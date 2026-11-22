import unittest
from unittest.mock import patch

import token_security
import protocol_validation


class TokenSecurityTests(unittest.TestCase):
    def test_active_authority_rejected(self):
        report = {"source": "solana_rpc", "mint_authority": "ACTIVE",
                  "freeze_authority": "DISABLED", "warnings": []}
        self.assertFalse(token_security.evaluate_token_security(report)["allowed"])

    def test_revoked_authorities_accepted_with_concentration_warning(self):
        report = {"source": "solana_rpc", "mint_authority": "DISABLED",
                  "freeze_authority": "DISABLED",
                  "warnings": ["TOP10_TOKEN_ACCOUNTS_HIGH_CONCENTRATION"]}
        result = token_security.evaluate_token_security(report)
        self.assertTrue(result["allowed"])
        self.assertTrue(result["warnings"])

    @patch("solana_onchain._rpc")
    def test_reads_mint_authorities_and_largest_account_concentration(self, rpc):
        rpc.side_effect = [
            {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                       "data": {"parsed": {"info": {
                           "isInitialized": True, "decimals": 6,
                           "mintAuthority": None, "freezeAuthority": None
                       }}}}},
            {"value": {"amount": "100000000", "decimals": 6}},
            {"value": {"value": [
                {"address": "11111111111111111111111111111111", "amount": "30000000"}
            ]}},
            {"value": [{"data": {"parsed": {"info": {"owner": "11111111111111111111111111111111"}}}}]},
        ]
        with patch("solana_onchain._b58decode", return_value=bytes(32)):
            report = token_security.get_token_security_report("MintAddress")
        self.assertEqual(report["mint_authority"], "DISABLED")
        self.assertEqual(report["freeze_authority"], "DISABLED")
        self.assertEqual(report["top10_token_account_pct"], 30.0)
        self.assertTrue(token_security.evaluate_token_security(report)["allowed"])

    def test_missing_report_fails_closed(self):
        self.assertFalse(token_security.evaluate_token_security(None)["allowed"])


class ProtocolValidationTests(unittest.TestCase):
    def test_protocol_list_contains_eleven_families(self):
        self.assertEqual(len(protocol_validation.SUPPORTED), 11)

    def test_alias_normalization_is_applied(self):
        with patch("solana_onchain.normalize_protocol", return_value="pumpswap"):
            self.assertEqual(protocol_validation._normalize("pumpfun-amm"), "pumpswap")


if __name__ == "__main__":
    unittest.main()
