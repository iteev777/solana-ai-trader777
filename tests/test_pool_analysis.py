import unittest
from unittest.mock import patch

import pool_analysis


class PoolAnalysisResilienceTests(unittest.TestCase):
    def setUp(self):
        pool_analysis.clear_pool_analysis_state()

    def test_rpc_and_rate_limit_errors_are_transient(self):
        self.assertEqual(
            pool_analysis.classify_pool_error(
                RuntimeError("All configured Solana RPC endpoints failed")
            ),
            "transient",
        )
        self.assertEqual(
            pool_analysis.classify_pool_error(
                RuntimeError("HTTP 429 rate limit")
            ),
            "transient",
        )

    def test_http_client_errors_are_transient_and_do_not_expose_urls(self):
        from requests.exceptions import HTTPError

        error = HTTPError("private provider URL")
        self.assertEqual(pool_analysis.classify_pool_error(error), "transient")
        pool_analysis.record_transient_failure("pool", error, now=10)
        self.assertEqual(pool_analysis.get_pool_analysis_status(now=11)["last_error"], "HTTPError")

    def test_polling_does_not_extend_expired_backoff_history(self):
        pool_analysis.record_transient_failure("pool", now=0)
        self.assertFalse(pool_analysis.should_skip_pool("pool", now=60))
        self.assertFalse(pool_analysis.should_skip_pool("pool", now=660))
        self.assertEqual(pool_analysis.record_transient_failure("pool", now=660)["failures"], 1)

    def test_missing_accounts_and_invalid_layouts_are_structural(self):
        self.assertEqual(
            pool_analysis.classify_pool_error(ValueError("on-chain account not found")),
            "structural",
        )
        self.assertEqual(
            pool_analysis.classify_pool_error(ValueError("invalid PumpSwap discriminator")),
            "structural",
        )

    def test_transient_backoff_doubles_only_after_a_real_retry(self):
        first = pool_analysis.record_transient_failure(
            "pool", RuntimeError("RPC timeout"), now=100.0
        )
        duplicate = pool_analysis.record_transient_failure(
            "pool", RuntimeError("RPC timeout"), now=101.0
        )
        self.assertEqual(first["retry_in_seconds"], 60.0)
        self.assertEqual(duplicate["retry_in_seconds"], 59.0)
        self.assertEqual(duplicate["failures"], 1)
        self.assertTrue(pool_analysis.should_skip_pool("pool", now=159.0))
        self.assertFalse(pool_analysis.should_skip_pool("pool", now=160.0))
        second = pool_analysis.record_transient_failure(
            "pool", RuntimeError("RPC timeout"), now=160.0
        )
        self.assertEqual(second["retry_in_seconds"], 120.0)

    def test_recent_verified_snapshot_suppresses_racing_transient_failure(self):
        with patch.object(pool_analysis.time, "monotonic", return_value=100.0):
            pool_analysis.record_success("pool")
        result = pool_analysis.record_transient_failure(
            "pool", RuntimeError("RPC timeout"), now=101.0
        )
        self.assertTrue(result["ignored_after_recent_success"])
        self.assertEqual(result["retry_in_seconds"], 0.0)
        self.assertEqual(
            pool_analysis.get_pool_analysis_status(now=102.0)["transient_backoff_pools"],
            0,
        )

    def test_structural_failures_are_quarantined_and_success_clears_them(self):
        state = pool_analysis.record_structural_failure(
            "pool", ValueError("invalid pool layout"), now=10.0
        )
        self.assertEqual(state["retry_in_seconds"], 3600.0)
        self.assertTrue(pool_analysis.should_skip_pool("pool", now=11.0))
        pool_analysis.record_success("pool")
        self.assertFalse(pool_analysis.should_skip_pool("pool", now=11.0))

    def test_protocol_validation_does_not_count_transient_rpc_failure_as_bad_pool(self):
        from unittest.mock import patch
        import protocol_validation
        with patch("database.record_protocol_validation") as record:
            result = protocol_validation.record_validation(
                "pumpswap",
                "pool",
                "mint",
                error=RuntimeError("All configured Solana RPC endpoints failed"),
            )
        self.assertEqual(result["failure_class"], "transient")
        self.assertFalse(result["recorded"])
        record.assert_not_called()
        self.assertEqual(
            pool_analysis.get_pool_analysis_status()["transient_backoff_pools"],
            1,
        )

    def test_pool_state_distinguishes_transient_from_structural_block(self):
        pool_analysis.record_transient_failure("rpc-pool", RuntimeError("timeout"), now=10.0)
        pool_analysis.record_structural_failure("bad-pool", ValueError("invalid layout"), now=10.0)
        self.assertEqual(
            pool_analysis.get_pool_analysis_state("rpc-pool", now=11.0)["state"],
            "transient_backoff",
        )
        self.assertEqual(
            pool_analysis.get_pool_analysis_state("bad-pool", now=11.0)["state"],
            "structural_quarantine",
        )
        self.assertTrue(pool_analysis.is_structurally_blocked("bad-pool", now=11.0))

    def test_unverified_snapshot_does_not_clear_active_backoff(self):
        import protocol_validation
        from solana_onchain import PROGRAMS
        pool_analysis.record_transient_failure("pool", RuntimeError("RPC timeout"))
        with patch("solana_onchain._account", return_value={"owner": PROGRAMS["pumpswap"]}), \
             patch("database.record_protocol_validation"):
            protocol_validation.record_validation(
                "pumpswap", "pool", "mint", snapshot={"onchainVerified": False}
            )
        self.assertTrue(pool_analysis.should_skip_pool("pool"))

    def test_status_reports_separate_transient_and_structural_states(self):
        pool_analysis.record_transient_failure("rpc-pool", RuntimeError("timeout"), now=1.0)
        pool_analysis.record_structural_failure("bad-pool", ValueError("invalid layout"), now=1.0)
        status = pool_analysis.get_pool_analysis_status(now=2.0)
        self.assertEqual(status["transient_backoff_pools"], 1)
        self.assertEqual(status["structural_blocked_pools"], 1)


if __name__ == "__main__":
    unittest.main()
