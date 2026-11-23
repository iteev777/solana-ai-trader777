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
            report = token_security.get_token_security_report("1" * 32)
        self.assertEqual(report["mint_authority"], "DISABLED")
        self.assertEqual(report["freeze_authority"], "DISABLED")
        self.assertEqual(report["top10_token_account_pct"], 30.0)
        self.assertEqual(report["largest_token_account_details"][0]["address"], "11111111111111111111111111111111")
        self.assertEqual(report["largest_token_account_details"][0]["authority"], "11111111111111111111111111111111")
        self.assertEqual(report["top_owner_account_authorities"], ["11111111111111111111111111111111"])
        self.assertTrue(token_security.evaluate_token_security(report)["allowed"])

    @patch("solana_onchain._rpc")
    def test_exact_raw_amount_and_present_blank_authority_fail_closed(self, rpc):
        raw_amount = 2**53 + 1
        rpc.side_effect = [
            {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                       "data": {"parsed": {"info": {
                           "isInitialized": True, "decimals": 6,
                           "mintAuthority": "", "freezeAuthority": None
                       }}}}},
            {"value": {"amount": str(raw_amount), "decimals": 6}},
            {"value": [{"address": "11111111111111111111111111111111",
                        "amount": str(raw_amount)}]},
            {"value": []},
        ]
        report = token_security.get_token_security_report("11111111111111111111111111111111")
        self.assertEqual(report["largest_token_account_details"][0]["amount"], raw_amount)
        self.assertFalse(token_security.evaluate_token_security(report)["allowed"])

    @patch("solana_onchain._rpc")
    def test_mint_snapshot_supplies_exact_amount_without_second_lookup(self, rpc):
        mint = "1" * 32
        rpc.side_effect = [
            {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                       "data": {"parsed": {"type": "mint", "info": {
                           "isInitialized": True, "decimals": 6, "supply": str(2**53 + 1),
                           "mintAuthority": None, "freezeAuthority": None
                       }}}}},
            {"value": [{"address": mint, "amount": "1"}]},
            {"value": []},
        ]
        report = token_security.get_token_security_report(mint)
        self.assertEqual(report["supply_raw"], 2**53 + 1)
        self.assertEqual([call.args[0] for call in rpc.call_args_list],
                         ["getAccountInfo", "getTokenLargestAccounts", "getMultipleAccounts"])

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
            report = token_security.get_token_security_report("1" * 32)
        self.assertEqual(len(report["largest_token_account_details"]), 1)
        self.assertIsNone(report["largest_token_account_details"][0]["authority"])
        self.assertEqual(report["top_owner_account_authorities"], [])



class ProtocolValidationTests(unittest.TestCase):
    def test_failed_observation_is_not_counted_as_verified(self):
        with patch("solana_onchain._account", return_value={}), \
             patch("pool_analysis.record_structural_failure"), \
             patch("database.record_protocol_validation") as record:
            result = protocol_validation.record_validation(
                "pumpswap", "pool", "mint", snapshot={"onchainVerified": True},
                error=ValueError("invalid layout with private details"),
            )
        self.assertFalse(result["success"])
        self.assertTrue(result["recorded"])
        self.assertEqual(record.call_args.args[3:], (False, "ValueError", None))

    def test_protocol_list_contains_eleven_families(self):
        self.assertEqual(len(protocol_validation.SUPPORTED), 11)

    def test_alias_normalization_is_applied(self):
        with patch("solana_onchain.normalize_protocol", return_value="pumpswap"):
            self.assertEqual(protocol_validation._normalize("pumpfun-amm"), "pumpswap")


if __name__ == "__main__":
    unittest.main()
