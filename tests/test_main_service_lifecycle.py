import os
import unittest
from unittest.mock import patch

import main


class MainServiceLifecycleTests(unittest.TestCase):
    def test_runtime_services_are_registered_and_started_through_manager(self):
        with patch.dict(os.environ, {
            "TROJAN_DASHBOARD_TOKEN": "test-token",
            "TROJAN_COPY_WALLETS": "wallet-A",
        }, clear=False), \
             patch("dashboard.start_dashboard", return_value=object()) as dashboard_start, \
             patch("dashboard.stop_dashboard", return_value=True) as dashboard_stop, \
             patch("copy_trade.get_copy_monitor_status", return_value={
                 "configured": True, "running": True, "wallet_count": 1
             }), \
             patch("copy_trade.start_copy_monitor", return_value=object()) as copy_start, \
             patch("copy_trade.stop_copy_monitor", return_value=True) as copy_stop, \
             patch.object(main.sniper, "start_listener", return_value=object()) as sniper_start, \
             patch.object(main.sniper, "stop_listener", return_value=True) as sniper_stop:
            manager = main._service_manager_health()
            report = manager.start_all()
            self.assertEqual(
                report["started"],
                ["dashboard", "sniper_listener", "copy_wallet_monitor"],
            )
            self.assertEqual(
                manager.registered_names(),
                ["copy_wallet_monitor", "dashboard", "sniper_listener"],
            )
            stopped = manager.stop_all()

        self.assertCountEqual(
            stopped["stopped"],
            ["dashboard", "sniper_listener", "copy_wallet_monitor"],
        )
        dashboard_start.assert_called_once()
        dashboard_stop.assert_called_once()
        copy_start.assert_called_once()
        copy_stop.assert_called_once()
        sniper_start.assert_called_once()
        sniper_stop.assert_called_once()

    def test_dashboard_and_copy_services_are_disabled_without_configuration(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("copy_trade.get_copy_monitor_status", return_value={
                 "configured": False, "running": False, "wallet_count": 0
             }):
            manager = main._service_manager_health()
            report = manager.start_all()
        self.assertEqual(report["disabled"], ["dashboard", "copy_wallet_monitor"])
        self.assertEqual(report["started"], ["sniper_listener"])
        manager.stop_all()


if __name__ == "__main__":
    unittest.main()
