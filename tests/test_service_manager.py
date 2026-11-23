import threading
import time
import unittest

import service_manager


class ServiceManagerTests(unittest.TestCase):
    def manager(self, **kwargs):
        return service_manager.ServiceManager(**kwargs)

    def test_startup_order_is_dependency_first_and_deterministic(self):
        manager = self.manager()
        started = []
        manager.register(service_manager.ServiceSpec(
            "worker", start=lambda: started.append("worker"), dependencies=("base",), priority=1
        ))
        manager.register(service_manager.ServiceSpec(
            "zeta", start=lambda: started.append("zeta"), priority=20
        ))
        manager.register(service_manager.ServiceSpec(
            "base", start=lambda: started.append("base"), priority=50
        ))
        manager.register(service_manager.ServiceSpec(
            "alpha", start=lambda: started.append("alpha"), priority=20
        ))
        self.assertEqual(
            manager.resolve_startup_order(),
            ["base", "worker", "alpha", "zeta"],
        )
        report = manager.start_all()
        self.assertEqual(started, ["base", "worker", "alpha", "zeta"])
        self.assertEqual(report["started"], ["base", "worker", "alpha", "zeta"])

    def test_missing_dependency_and_cycle_are_rejected(self):
        missing = self.manager()
        missing.register(service_manager.ServiceSpec(
            "worker", start=lambda: None, dependencies=("absent",)
        ))
        with self.assertRaises(service_manager.DependencyError):
            missing.resolve_startup_order()

        cyclic = self.manager()
        cyclic.register(service_manager.ServiceSpec(
            "a", start=lambda: None, dependencies=("b",)
        ))
        cyclic.register(service_manager.ServiceSpec(
            "b", start=lambda: None, dependencies=("a",)
        ))
        with self.assertRaisesRegex(service_manager.DependencyError, "cycle"):
            cyclic.resolve_startup_order()

    def test_disabled_dependency_blocks_dependent_without_starting_it(self):
        manager = self.manager()
        started = []
        manager.register(service_manager.ServiceSpec(
            "disabled", start=lambda: started.append("disabled"), enabled=False
        ))
        manager.register(service_manager.ServiceSpec(
            "dependent", start=lambda: started.append("dependent"), dependencies=("disabled",)
        ))
        report = manager.start_all()
        self.assertEqual(started, [])
        self.assertEqual(report["disabled"], ["disabled"])
        self.assertEqual(report["blocked"][0]["name"], "dependent")
        self.assertEqual(manager.get_status("dependent")["state"], "blocked")

    def test_nonessential_failure_does_not_stop_independent_services(self):
        manager = self.manager()
        started = []
        manager.register(service_manager.ServiceSpec(
            "broken", start=lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        ))
        manager.register(service_manager.ServiceSpec(
            "dependent", start=lambda: started.append("dependent"), dependencies=("broken",)
        ))
        manager.register(service_manager.ServiceSpec(
            "healthy", start=lambda: started.append("healthy")
        ))
        report = manager.start_all()
        self.assertEqual(started, ["healthy"])
        self.assertEqual(manager.get_status("broken")["state"], "failed")
        self.assertEqual(manager.get_status("dependent")["state"], "blocked")
        self.assertEqual(report["failures"][0]["name"], "broken")

    def test_essential_failure_rolls_back_previously_started_services(self):
        manager = self.manager()
        stopped = []
        manager.register(service_manager.ServiceSpec(
            "first", start=lambda: "handle", stop=lambda handle: stopped.append(handle), priority=1
        ))
        manager.register(service_manager.ServiceSpec(
            "essential", start=lambda: (_ for _ in ()).throw(RuntimeError("fatal")),
            essential=True, priority=2
        ))
        with self.assertRaises(service_manager.EssentialServiceError):
            manager.start_all()
        self.assertEqual(stopped, ["handle"])
        self.assertEqual(manager.get_status("first")["state"], "stopped")

    def test_single_service_stop_refuses_running_dependents(self):
        manager = self.manager()
        manager.register(service_manager.ServiceSpec(
            "base", start=lambda: None, stop=lambda handle: None, stops_alone=True
        ))
        manager.register(service_manager.ServiceSpec(
            "worker", start=lambda: None, dependencies=("base",)
        ))
        manager.start_all()
        result = manager.stop_service("base")
        self.assertFalse(result["stopped"])
        self.assertIn("running dependents", result["reason"])

    def test_single_service_stop_uses_own_signal_and_waits_for_thread(self):
        manager = self.manager(stop_grace_seconds=1)
        finished = threading.Event()

        def start():
            thread = threading.Thread(target=finished.wait, daemon=True)
            thread.start()
            return thread

        manager.register(service_manager.ServiceSpec(
            "worker", start=start, stop=lambda handle: finished.set(), stops_alone=True
        ))
        manager.start_all()
        result = manager.stop_service("worker")
        self.assertTrue(result["stopped"], result)
        self.assertEqual(manager.get_status("worker")["state"], "stopped")

    def test_thread_that_ignores_stop_is_not_force_killed(self):
        manager = self.manager(stop_grace_seconds=0.01)
        release = threading.Event()

        def start():
            thread = threading.Thread(target=release.wait, daemon=True)
            thread.start()
            return thread

        manager.register(service_manager.ServiceSpec(
            "stuck", start=start, stop=lambda handle: None, stops_alone=True
        ))
        manager.start_all()
        result = manager.stop_service("stuck")
        self.assertFalse(result["stopped"])
        self.assertEqual(manager.get_status("stuck")["state"], "degraded")
        release.set()

    def test_health_states_and_cache_are_normalized(self):
        manager = self.manager(cache_ttl_seconds=30)
        calls = []
        manager.register(service_manager.ServiceSpec(
            "api", start=lambda: None,
            health=lambda: calls.append(1) or {"status": "degraded", "message": "slow"}
        ))
        manager.start_all()
        first = manager.get_health()
        second = manager.get_health()
        self.assertEqual(first["api"]["status"], "degraded")
        self.assertEqual(second["api"]["message"], "slow")
        self.assertEqual(len(calls), 1)
        manager.get_health(refresh=True)
        self.assertEqual(len(calls), 2)

    def test_health_callback_failure_is_unhealthy(self):
        manager = self.manager()
        manager.register(service_manager.ServiceSpec(
            "broken-health", start=lambda: None,
            health=lambda: (_ for _ in ()).throw(RuntimeError("probe failed"))
        ))
        manager.start_all()
        self.assertEqual(manager.get_health()["broken-health"]["status"], "unhealthy")

    def test_enable_check_exception_is_not_misreported_as_disabled(self):
        manager = self.manager()
        manager.register(service_manager.ServiceSpec(
            "unstable", start=lambda: None,
            enabled=lambda: (_ for _ in ()).throw(RuntimeError("config unavailable")),
        ))
        report = manager.start_all()
        self.assertEqual(report["disabled"], ["unstable"])
        self.assertEqual(manager.get_status("unstable")["state"], "failed")

    def test_independent_stop_requires_a_stop_callback(self):
        manager = self.manager()
        manager.register(service_manager.ServiceSpec(
            "cannot-stop", start=lambda: None, stops_alone=True
        ))
        manager.start_all()
        result = manager.stop_service("cannot-stop")
        self.assertFalse(result["stopped"])
        self.assertIn("no independent stop callback", result["reason"])

    def test_unexpected_worker_exit_is_reported_as_failed(self):
        manager = self.manager()
        release = threading.Event()
        manager.register(service_manager.ServiceSpec(
            "worker",
            start=lambda: threading.Thread(target=lambda: None, daemon=True),
        ))
        manager.start_all()
        status = manager.get_status("worker")
        self.assertEqual(status["state"], "failed")
        self.assertIn("exited unexpectedly", status["last_error"])

    def test_global_manager_access_is_thread_safe_and_type_checked(self):
        manager = self.manager()
        service_manager.clear_global_service_manager()
        service_manager.set_global_service_manager(manager)
        self.assertIs(service_manager.get_global_service_manager(), manager)
        with self.assertRaises(TypeError):
            service_manager.set_global_service_manager(object())
        service_manager.clear_global_service_manager()
        self.assertIsNone(service_manager.get_global_service_manager())


if __name__ == "__main__":
    unittest.main()
