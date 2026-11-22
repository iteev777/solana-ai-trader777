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
