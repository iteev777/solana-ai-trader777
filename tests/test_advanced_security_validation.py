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
            {"value": [
                {"address": "11111111111111111111111111111111", "amount": "30000000"}
            ]},
            {"value": [{"data": {"parsed": {"info": {"owner": "11111111111111111111111111111111"}}}}]},
        ]
        with patch("solana_onchain._b58decode", return_value=bytes(32)):
            report = token_security.get_token_security_report("MintAddress")
        self.assertEqual(report["mint_authority"], "DISABLED")
        self.assertEqual(report["freeze_authority"], "DISABLED")
        self.assertEqual(report["top10_token_account_pct"], 30.0)
        self.assertEqual(report["largest_token_account_details"][0]["address"], "11111111111111111111111111111111")
        self.assertEqual(report["largest_token_account_details"][0]["authority"], "11111111111111111111111111111111")
        self.assertEqual(report["top_owner_account_authorities"], ["11111111111111111111111111111111"])
        self.assertTrue(token_security.evaluate_token_security(report)["allowed"])

    @patch("solana_onchain._rpc")
    def test_boolean_or_fractional_mint_decimals_are_rejected(self, rpc):
        rpc.return_value = {"value": {
            "owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            "data": {"parsed": {"info": {
                "isInitialized": True, "decimals": True,
                "mintAuthority": None, "freezeAuthority": None,
            }}},
        }}
        with patch("solana_onchain._b58decode", return_value=bytes(32)):
            with self.assertRaisesRegex(ValueError, "decimals"):
                token_security.get_token_security_report("MintAddress")

    @patch("solana_onchain._rpc")
    def test_supply_decimals_must_match_mint_account(self, rpc):
        rpc.side_effect = [
            {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                       "data": {"parsed": {"info": {
                           "isInitialized": True, "decimals": 6,
                           "mintAuthority": None, "freezeAuthority": None
                       }}}}},
            {"value": {"amount": "100000000", "decimals": 9}},
        ]
        with patch("solana_onchain._b58decode", return_value=bytes(32)):
            with self.assertRaisesRegex(ValueError, "do not match"):
                token_security.get_token_security_report("MintAddress")

    @patch("solana_onchain._rpc")
    def test_largest_account_raw_amount_must_be_integer_u64(self, rpc):
        rpc.side_effect = [
            {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                       "data": {"parsed": {"info": {
                           "isInitialized": True, "decimals": 6,
                           "mintAuthority": None, "freezeAuthority": None
                       }}}}},
            {"value": {"amount": "100000000", "decimals": 6}},
            {"value": [{"address": "11111111111111111111111111111111", "amount": True}]},
        ]
        with patch("solana_onchain._b58decode", return_value=bytes(32)):
            with self.assertRaisesRegex(ValueError, "raw u64"):
                token_security.get_token_security_report("MintAddress")

    @patch("solana_onchain._rpc")
    def test_incomplete_owner_lookup_does_not_report_partial_owner_concentration(self, rpc):
        rpc.side_effect = [
            {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                       "data": {"parsed": {"info": {
                           "isInitialized": True, "decimals": 6,
                           "mintAuthority": None, "freezeAuthority": None
                       }}}}},
            {"value": {"amount": "100000000", "decimals": 6}},
            {"value": [
                {"address": "11111111111111111111111111111111", "amount": "30000000"},
                {"address": "11111111111111111111111111111112", "amount": "20000000"},
            ]},
            {"value": [{"data": {"parsed": {"info": {"owner": "owner-a"}}}}]},
        ]
        with patch("solana_onchain._b58decode", return_value=bytes(32)):
            report = token_security.get_token_security_report("MintAddress")
        self.assertIsNone(report["top_owner_account_authority_pct"])
        self.assertEqual(report["top_owner_account_authorities"], [])

    def test_empty_authority_string_is_unknown_not_disabled(self):
        report = {
            "source": "solana_rpc", "mint_authority": "UNKNOWN",
            "freeze_authority": "DISABLED", "warnings": [],
        }
        self.assertFalse(token_security.evaluate_token_security(report)["allowed"])

    def test_missing_report_fails_closed(self):
        self.assertFalse(token_security.evaluate_token_security(None)["allowed"])


    @patch("solana_onchain._rpc")
    def test_owner_lookup_failure_keeps_account_addresses_and_returns_report(self, rpc):
        rpc.side_effect = [
            {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                       "data": {"parsed": {"info": {
                           "isInitialized": True, "decimals": 6,
                           "mintAuthority": None, "freezeAuthority": None
                       }}}}},
            {"value": {"amount": "100000000", "decimals": 6}},
            {"value": [
                {"address": "11111111111111111111111111111111", "amount": "30000000"}
            ]},
            RuntimeError("optional owner RPC unavailable"),
        ]
        with patch("solana_onchain._b58decode", return_value=bytes(32)):
            report = token_security.get_token_security_report("MintAddress")
        self.assertEqual(len(report["largest_token_account_details"]), 1)
        self.assertIsNone(report["largest_token_account_details"][0]["authority"])
        self.assertEqual(report["top_owner_account_authorities"], [])



class ProtocolValidationTests(unittest.TestCase):
    def test_protocol_list_contains_eleven_families(self):
        self.assertEqual(len(protocol_validation.SUPPORTED), 11)

    def test_alias_normalization_is_applied(self):
        with patch("solana_onchain.normalize_protocol", return_value="pumpswap"):
            self.assertEqual(protocol_validation._normalize("pumpfun-amm"), "pumpswap")


if __name__ == "__main__":
    unittest.main()
