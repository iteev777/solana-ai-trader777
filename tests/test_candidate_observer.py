import os
import unittest
from unittest.mock import patch

import candidate_observer


class CandidateObserverTests(unittest.TestCase):
    def setUp(self):
        with candidate_observer._LOCK:
            candidate_observer._SESSIONS.clear()
            candidate_observer._STATS.update({
                "sessions_started": 0,
                "sessions_completed": 0,
                "sessions_passed": 0,
                "sessions_rejected": 0,
                "events_received": 0,
                "events_ignored": 0,
                "last_completed_at": None,
                "last_result": None,
                "last_error": None,
            })

    def _event(self, tx_type, trader, sol, price, signature):
        return {
            "txType": tx_type,
            "mint": "So11111111111111111111111111111111111111112",
            "traderPublicKey": trader,
            "solAmount": sol,
            "tokenAmount": 1000,
            "marketCapSol": price,
            "signature": signature,
        }

    def test_invalid_and_unwatched_events_are_ignored(self):
        self.assertFalse(candidate_observer.record_trade_event({"txType": "buy"}))
        self.assertEqual(candidate_observer.get_status()["stats"]["events_ignored"], 1)

    def test_events_are_validated_and_deduplicated(self):
        mint = "So11111111111111111111111111111111111111112"
        with candidate_observer._LOCK:
            candidate_observer._SESSIONS[mint] = {
                "events": [], "signatures": set(), "overflow": False, "started_at": 1
            }
        event = self._event("buy", "buyer-1", 0.2, 10, "signature-1")
        self.assertTrue(candidate_observer.record_trade_event(event))
        self.assertFalse(candidate_observer.record_trade_event(event))
        self.assertEqual(len(candidate_observer._SESSIONS[mint]["events"]), 1)
        self.assertEqual(candidate_observer.get_status()["stats"]["events_received"], 1)

    def test_observation_scoring_passes_broad_buy_pressure(self):
        events = []
        for index, (kind, buyer, sol, price) in enumerate([
            ("buy", "a", 0.2, 10.0),
            ("buy", "b", 0.2, 10.1),
            ("buy", "c", 0.2, 10.2),
            ("sell", "d", 0.1, 10.3),
            ("buy", "a", 0.1, 10.4),
        ]):
            events.append({
                "tx_type": kind, "trader": buyer, "sol_amount": sol,
                "token_amount": 1000, "price": price, "signature": str(index),
            })
        report = candidate_observer.evaluate_events(events)
        self.assertTrue(report["ok"], report["reasons"])
        self.assertEqual(report["trades"], 5)
        self.assertEqual(report["unique_buyers"], 3)
        self.assertGreater(report["buy_sell_ratio"], 1.25)
        self.assertLess(report["largest_buyer_pct"], 60)
        self.assertEqual(report["status"], "PASS")

    def test_observation_rejects_single_buyer_concentration(self):
        events = [
            {"tx_type": "buy", "trader": "same", "sol_amount": 1.0, "price": 10 + i / 10}
            for i in range(5)
        ]
        report = candidate_observer.evaluate_events(events)
        self.assertFalse(report["ok"])
        self.assertIn("OBSERVATION_UNIQUE_BUYERS_BELOW_MINIMUM", report["reasons"])
        self.assertIn("OBSERVATION_BUYER_CONCENTRATION_TOO_HIGH", report["reasons"])

    def test_observer_is_disabled_by_default(self):
        with patch.object(candidate_observer, "ENABLED", False):
            report = candidate_observer.observe_candidate(
                "So11111111111111111111111111111111111111112", ["pumpportal"]
            )
        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "DISABLED")

    def test_metered_feed_requires_explicit_api_key(self):
        with patch.object(candidate_observer, "ENABLED", True), patch.dict(
            os.environ, {"PUMPPORTAL_API_KEY": ""}, clear=False
        ):
            report = candidate_observer.observe_candidate(
                "So11111111111111111111111111111111111111112", ["pumpportal"]
            )
        self.assertFalse(report["ok"])
        self.assertEqual(
            report["reason"], "PUMPPORTAL_API_KEY_REQUIRED_FOR_METERED_TRADE_FEED"
        )

    def test_observer_uses_shared_websocket_and_records_window(self):
        mint = "So11111111111111111111111111111111111111112"
        from unittest.mock import Mock
        watcher = Mock(return_value=True)
        unwatcher = Mock(return_value=True)

        def collect_window(_seconds):
            rows = [
                ("buy", "a", 0.2, 10.0, "1"),
                ("buy", "b", 0.2, 10.1, "2"),
                ("buy", "c", 0.2, 10.2, "3"),
                ("sell", "d", 0.1, 10.3, "4"),
                ("buy", "a", 0.1, 10.4, "5"),
            ]
            for kind, trader, sol, price, signature in rows:
                candidate_observer.record_trade_event({
                    "txType": kind, "mint": mint, "traderPublicKey": trader,
                    "solAmount": sol, "tokenAmount": 1000,
                    "marketCapSol": price, "signature": signature,
                })

        with patch.object(candidate_observer, "ENABLED", True), \
             patch.object(candidate_observer, "DURATION_SECONDS", 5), \
             patch.dict(os.environ, {"PUMPPORTAL_API_KEY": "test-key"}, clear=False), \
             patch("sniper_scanner.watch_token", watcher), \
             patch("sniper_scanner.unwatch_token", unwatcher), \
             patch.object(candidate_observer.time, "sleep", side_effect=collect_window):
            report = candidate_observer.observe_candidate(mint, ["pumpportal"])

        self.assertTrue(report["ok"], report["reasons"])
        watcher.assert_called_once_with(mint)
        unwatcher.assert_called_once_with(mint)
        self.assertEqual(report["trades"], 5)


if __name__ == "__main__":
    unittest.main()
