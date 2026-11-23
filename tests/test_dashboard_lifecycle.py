import unittest
from unittest.mock import Mock

import dashboard


class DashboardLifecycleTests(unittest.TestCase):
    def setUp(self):
        dashboard._server = None
        dashboard._server_thread = None

    def test_stop_dashboard_shuts_server_and_joins_thread(self):
        server = Mock()
        thread = Mock()
        thread.is_alive.return_value = False
        dashboard._server = server
        dashboard._server_thread = thread

        self.assertTrue(dashboard.stop_dashboard(timeout=0.3))
        server.shutdown.assert_called_once_with()
        server.server_close.assert_called_once_with()
        thread.join.assert_called_once_with(0.3)
        self.assertIsNone(dashboard._server)
        self.assertIsNone(dashboard._server_thread)

    def test_stop_dashboard_reports_thread_that_does_not_exit(self):
        server = Mock()
        thread = Mock()
        thread.is_alive.return_value = True
        dashboard._server = server
        dashboard._server_thread = thread

        self.assertFalse(dashboard.stop_dashboard(timeout=0))
        self.assertIs(dashboard._server, server)
        self.assertIs(dashboard._server_thread, thread)

    def test_stop_dashboard_is_idempotent_when_not_started(self):
        self.assertTrue(dashboard.stop_dashboard())

    def test_stop_dashboard_rejects_invalid_timeout(self):
        for value in (True, -1, float("inf"), "5"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    dashboard.stop_dashboard(value)


if __name__ == "__main__":
    unittest.main()
