"""Regression checks for iOS installed-dashboard relaunch behavior."""
import unittest

try:
    from dashboard import app
except ImportError:
    app = None


@unittest.skipIf(app is None, "Dashboard dependencies are unavailable")
class DashboardPwaRelaunchTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_dashboard_html_is_not_cached(self):
        with app.test_request_context("/"):
            from dashboard import dashboard_home
            response = dashboard_home()
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("private", response.headers["Cache-Control"])

    def test_service_worker_does_not_cache_authenticated_navigation(self):
        with app.test_request_context("/sw.js"):
            from dashboard import dashboard_service_worker
            response = dashboard_service_worker()
        script = response.get_data(as_text=True)
        self.assertIn('event.request.mode === "navigate"', script)
        self.assertIn('cache: "no-store"', script)
        self.assertIn("caches.delete(key)", script)
        self.assertNotIn("cache.addAll", script)
        self.assertNotIn("caches.match(event.request)", script)
        self.assertIn("no-store", response.headers["Cache-Control"])


if __name__ == "__main__":
    unittest.main()
