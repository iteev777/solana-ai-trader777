import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import token_security
import protocol_validation
import solana_onchain

MINT = solana_onchain._b58encode(bytes([2]) * 32)
ACCOUNT_A = solana_onchain._b58encode(bytes([3]) * 32)
ACCOUNT_B = solana_onchain._b58encode(bytes([4]) * 32)


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
                {"address": ACCOUNT_A, "amount": "30000000"}
            ]},
            {"value": [{"data": {"parsed": {"info": {"owner": ACCOUNT_A}}}}]},
        ]
        with patch("solana_onchain._b58decode", wraps=solana_onchain._b58decode):
            report = token_security.get_token_security_report(MINT)
        self.assertEqual(report["mint_authority"], "DISABLED")
        self.assertEqual(report["freeze_authority"], "DISABLED")
        self.assertEqual(report["top10_token_account_pct"], 30.0)
        self.assertEqual(report["largest_token_account_details"][0]["address"], ACCOUNT_A)
        self.assertEqual(report["largest_token_account_details"][0]["authority"], ACCOUNT_A)
        self.assertEqual(report["top_owner_account_authorities"], [ACCOUNT_A])
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
        with patch("solana_onchain._b58decode", wraps=solana_onchain._b58decode):
            with self.assertRaisesRegex(ValueError, "decimals"):
                token_security.get_token_security_report(MINT)

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
        with patch("solana_onchain._b58decode", wraps=solana_onchain._b58decode):
            with self.assertRaisesRegex(ValueError, "do not match"):
                token_security.get_token_security_report(MINT)

    @patch("solana_onchain._rpc")
    def test_largest_account_raw_amount_must_be_integer_u64(self, rpc):
        rpc.side_effect = [
            {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                       "data": {"parsed": {"info": {
                           "isInitialized": True, "decimals": 6,
                           "mintAuthority": None, "freezeAuthority": None
                       }}}}},
            {"value": {"amount": "100000000", "decimals": 6}},
            {"value": [{"address": ACCOUNT_A, "amount": True}]},
        ]
        with patch("solana_onchain._b58decode", wraps=solana_onchain._b58decode):
            with self.assertRaisesRegex(ValueError, "raw u64"):
                token_security.get_token_security_report(MINT)

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
                {"address": ACCOUNT_A, "amount": "30000000"},
                {"address": ACCOUNT_B, "amount": "20000000"},
            ]},
            {"value": [{"data": {"parsed": {"info": {"owner": "owner-a"}}}}]},
        ]
        with patch("solana_onchain._b58decode", wraps=solana_onchain._b58decode):
            report = token_security.get_token_security_report(MINT)
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
                {"address": ACCOUNT_A, "amount": "30000000"}
            ]},
            RuntimeError("optional owner RPC unavailable"),
        ]
        with patch("solana_onchain._b58decode", wraps=solana_onchain._b58decode):
            report = token_security.get_token_security_report(MINT)
        self.assertEqual(len(report["largest_token_account_details"]), 1)
        self.assertIsNone(report["largest_token_account_details"][0]["authority"])
        self.assertEqual(report["top_owner_account_authorities"], [])



class ProtocolValidationTests(unittest.TestCase):
    def test_protocol_list_contains_eleven_families(self):
        self.assertEqual(len(protocol_validation.SUPPORTED), 11)

    def test_alias_normalization_is_applied(self):
        with patch("solana_onchain.normalize_protocol", return_value="pumpswap"):
            self.assertEqual(protocol_validation._normalize("pumpfun-amm"), "pumpswap")


class TokenSecurityCacheTests(unittest.TestCase):
    MINT = "11111111111111111111111111111111"

    def setUp(self):
        token_security.clear_cache()

    def tearDown(self):
        token_security.clear_cache()

    def test_successful_report_is_cached_and_returned_as_a_copy(self):
        report = {"source": "solana_rpc", "warnings": ["A"]}
        with patch("solana_onchain._b58decode", return_value=bytes(32)), \
             patch.object(token_security, "_fetch_token_security_report", return_value=report) as fetch:
            first = token_security.get_token_security_report(self.MINT)
            first["warnings"].append("caller mutation")
            second = token_security.get_token_security_report(self.MINT)
        fetch.assert_called_once_with(self.MINT, 20)
        self.assertEqual(second["warnings"], ["A"])
        self.assertEqual(token_security.get_cache_metrics()["entries"], 1)

    def test_largest_account_limit_is_part_of_cache_key(self):
        report = {"source": "solana_rpc"}
        with patch("solana_onchain._b58decode", return_value=bytes(32)), \
             patch.object(token_security, "_fetch_token_security_report", return_value=report) as fetch:
            token_security.get_token_security_report(self.MINT, 10)
            token_security.get_token_security_report(self.MINT, 20)
        self.assertEqual(fetch.call_count, 2)

    def test_failed_report_is_not_cached(self):
        report = {"source": "solana_rpc"}
        with patch("solana_onchain._b58decode", return_value=bytes(32)), \
             patch.object(
                 token_security, "_fetch_token_security_report",
                 side_effect=[RuntimeError("RPC offline"), report],
             ) as fetch:
            with self.assertRaisesRegex(RuntimeError, "RPC offline"):
                token_security.get_token_security_report(self.MINT)
            self.assertEqual(token_security.get_token_security_report(self.MINT), report)
        self.assertEqual(fetch.call_count, 2)

    def test_concurrent_requests_share_one_inflight_rpc_lookup(self):
        started = threading.Event()
        release = threading.Event()
        report = {"source": "solana_rpc"}

        def slow_fetch(mint, limit):
            started.set()
            self.assertTrue(release.wait(2))
            return report

        with patch("solana_onchain._b58decode", return_value=bytes(32)), \
             patch.object(token_security, "_fetch_token_security_report", side_effect=slow_fetch) as fetch:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(token_security.get_token_security_report, self.MINT)
                self.assertTrue(started.wait(1))
                second = pool.submit(token_security.get_token_security_report, self.MINT)
                release.set()
                self.assertEqual(first.result(timeout=3), report)
                self.assertEqual(second.result(timeout=3), report)
        fetch.assert_called_once_with(self.MINT, 20)




if __name__ == "__main__":
    unittest.main()
