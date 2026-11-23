"""Dependency-aware lifecycle manager for Trojan V2 background services.

The manager is intentionally framework-neutral: services are registered with Python
callbacks, so existing worker threads and loops can be adopted without replacing
their implementation. It provides deterministic startup, dependency failure
isolation, reverse-order shutdown, safe single-service stop, health snapshots and
cached status reporting. It never force-kills Python threads.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional


_LOG = logging.getLogger("trojan.services")


class ServiceState(str, Enum):
    REGISTERED = "registered"
    DISABLED = "disabled"
    INITIALIZING = "initializing"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    BLOCKED = "blocked"
    FAILED = "failed"


class ServiceManagerError(RuntimeError):
    """Base error for service registration and lifecycle failures."""


class DependencyError(ServiceManagerError):
    """A dependency is missing or the startup graph contains a cycle."""


class EssentialServiceError(ServiceManagerError):
    """An essential service failed to initialize or start."""


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    start: Callable[[], Any]
    stop: Optional[Callable[[Any], Any]] = None
    initialize: Optional[Callable[[], Any]] = None
    health: Optional[Callable[[], Any]] = None
    dependencies: tuple[str, ...] = ()
    priority: int = 100
    essential: bool = False
    enabled: bool | Callable[[], bool] = True
    stops_alone: bool = False


@dataclass
class _Runtime:
    state: ServiceState = ServiceState.REGISTERED
    handle: Any = None
    started_at: Optional[str] = None
    ready_at: Optional[str] = None
    start_duration_ms: Optional[float] = None
    stopped_at: Optional[str] = None
    message: Optional[str] = None
    last_error: Optional[str] = None
    error_count: int = 0
    restart_count: int = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_alive(handle: Any) -> Optional[bool]:
    check = getattr(handle, "is_alive", None)
    if callable(check):
        try:
            return bool(check())
        except Exception:
            return None
    done = getattr(handle, "done", None)
    if callable(done):
        try:
            return not bool(done())
        except Exception:
            return None
    return None


class ServiceManager:
    """Thread-safe lifecycle registry with deterministic dependency ordering."""

    def __init__(self, stop_grace_seconds: float = 5.0, cache_ttl_seconds: float = 5.0):
        for value, label in (
            (stop_grace_seconds, "stop_grace_seconds"),
            (cache_ttl_seconds, "cache_ttl_seconds"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(label + " must be numeric")
            if value < 0 or value != value or value == float("inf"):
                raise ValueError(label + " must be finite and non-negative")
        self.stop_grace_seconds = float(stop_grace_seconds)
        self.cache_ttl_seconds = float(cache_ttl_seconds)
        self._lock = threading.RLock()
        self._operation_lock = threading.RLock()
        self._specs: dict[str, ServiceSpec] = {}
        self._runtime: dict[str, _Runtime] = {}
        self._start_order: list[str] = []
        self._health_cache: dict[str, Any] = {}
        self._health_cache_at = 0.0

    def register(self, spec: ServiceSpec) -> None:
        if not isinstance(spec, ServiceSpec):
            raise TypeError("spec must be a ServiceSpec")
        name = spec.name.strip() if isinstance(spec.name, str) else ""
        if not name or name != spec.name:
            raise ValueError("service name must be non-empty and trimmed")
        if not callable(spec.start):
            raise ValueError("service start callback is required")
        if spec.stop is not None and not callable(spec.stop):
            raise ValueError("service stop callback must be callable")
        if spec.initialize is not None and not callable(spec.initialize):
            raise ValueError("service initialize callback must be callable")
        if spec.health is not None and not callable(spec.health):
            raise ValueError("service health callback must be callable")
        if not isinstance(spec.essential, bool) or not isinstance(spec.stops_alone, bool):
            raise ValueError("essential and stops_alone must be booleans")
        if not (isinstance(spec.enabled, bool) or callable(spec.enabled)):
            raise ValueError("enabled must be a boolean or callable")
        if isinstance(spec.priority, bool) or not isinstance(spec.priority, int):
            raise ValueError("service priority must be an integer")
        dependencies = tuple(spec.dependencies)
        if any(not isinstance(dep, str) or not dep.strip() for dep in dependencies):
            raise ValueError("service dependencies must be non-empty names")
        if len(set(dependencies)) != len(dependencies) or name in dependencies:
            raise ValueError("service dependencies must be unique and cannot include itself")
        normalized = ServiceSpec(
            name=name, start=spec.start, stop=spec.stop,
            initialize=spec.initialize, health=spec.health,
            dependencies=dependencies, priority=spec.priority,
            essential=spec.essential, enabled=spec.enabled,
            stops_alone=spec.stops_alone,
        )
        with self._lock:
            if name in self._specs:
                raise ValueError("service already registered: " + name)
            self._specs[name] = normalized
            self._runtime[name] = _Runtime()
            self._invalidate_cache()

    def registered_names(self) -> list[str]:
        with self._lock:
            return sorted(self._specs)

    def _enabled(self, spec: ServiceSpec) -> bool:
        try:
            value = spec.enabled() if callable(spec.enabled) else spec.enabled
            return value is True
        except Exception as error:
            _LOG.exception("service enable check failed: %s", spec.name)
            with self._lock:
                runtime = self._runtime[spec.name]
                runtime.state = ServiceState.FAILED
                runtime.last_error = "enable check failed: " + str(error)[:300]
                runtime.error_count += 1
            return False

    def resolve_startup_order(self, names: Optional[list[str]] = None) -> list[str]:
        with self._lock:
            selected = set(names if names is not None else [
                name for name, spec in self._specs.items() if self._enabled(spec)
            ])
            unknown = selected.difference(self._specs)
            if unknown:
                raise DependencyError("unknown services: " + ", ".join(sorted(unknown)))
            for name in selected:
                missing = set(self._specs[name].dependencies).difference(self._specs)
                if missing:
                    raise DependencyError(
                        name + " has unregistered dependencies: " + ", ".join(sorted(missing))
                    )
            indegree = {
                name: sum(1 for dep in self._specs[name].dependencies if dep in selected)
                for name in selected
            }
            dependents: dict[str, list[str]] = {name: [] for name in selected}
            for name in selected:
                for dependency in self._specs[name].dependencies:
                    if dependency in selected:
                        dependents[dependency].append(name)
            ready = sorted(
                (name for name, degree in indegree.items() if degree == 0),
                key=lambda name: (self._specs[name].priority, name),
            )
            ordered = []
            while ready:
                name = ready.pop(0)
                ordered.append(name)
                for dependent in dependents[name]:
                    indegree[dependent] -= 1
                    if indegree[dependent] == 0:
                        ready.append(dependent)
                ready.sort(key=lambda item: (self._specs[item].priority, item))
            if len(ordered) != len(selected):
                cyclic = sorted(name for name, degree in indegree.items() if degree > 0)
                raise DependencyError("service dependency cycle: " + ", ".join(cyclic))
            return ordered

    def start_all(self) -> dict[str, Any]:
        with self._operation_lock:
            with self._lock:
                enabled, disabled = [], []
                for name, spec in self._specs.items():
                    (enabled if self._enabled(spec) else disabled).append(name)
                for name in disabled:
                    runtime = self._runtime[name]
                    if runtime.state not in (
                        ServiceState.RUNNING, ServiceState.STARTING, ServiceState.FAILED
                    ):
                        runtime.state = ServiceState.DISABLED
                        runtime.message = "disabled by configuration"
                self._invalidate_cache()
            order = self.resolve_startup_order(enabled)
            attempted, started, failures, blocked = [], [], [], []
            for name in order:
                attempted.append(name)
                spec = self._specs[name]
                failed_dependencies = [
                    dep for dep in spec.dependencies
                    if self._runtime[dep].state != ServiceState.RUNNING
                ]
                if failed_dependencies:
                    reason = "dependencies unavailable: " + ", ".join(failed_dependencies)
                    with self._lock:
                        runtime = self._runtime[name]
                        runtime.state = ServiceState.BLOCKED
                        runtime.message = reason
                    blocked.append({"name": name, "reason": reason})
                    continue
                with self._lock:
                    if self._runtime[name].state == ServiceState.RUNNING:
                        started.append(name)
                        continue
                try:
                    self._start_one(name)
                    started.append(name)
                except Exception as error:
                    failures.append({"name": name, "error": str(error)[:500]})
                    if spec.essential:
                        self.stop_all()
                        raise EssentialServiceError(
                            "essential service failed: " + name
                        ) from error
            with self._lock:
                self._start_order = [name for name in order if self._runtime[name].state == ServiceState.RUNNING]
                self._invalidate_cache()
            return {
                "attempted": attempted,
                "started": started,
                "failures": failures,
                "blocked": blocked,
                "disabled": disabled,
                "total_enabled": len(enabled),
            }

    def _start_one(self, name: str) -> None:
        spec = self._specs[name]
        with self._lock:
            runtime = self._runtime[name]
            runtime.state = ServiceState.INITIALIZING
            runtime.message = None
            runtime.last_error = None
            started_clock = time.monotonic()
            runtime.started_at = _utc_now()
        try:
            if spec.initialize is not None:
                spec.initialize()
            with self._lock:
                runtime.state = ServiceState.STARTING
            handle = spec.start()
            ready_at = _utc_now()
            with self._lock:
                runtime.handle = handle
                runtime.state = ServiceState.RUNNING
                runtime.ready_at = ready_at
                runtime.start_duration_ms = round((time.monotonic() - started_clock) * 1000.0, 3)
                runtime.stopped_at = None
                runtime.restart_count += 1
                self._invalidate_cache()
            _LOG.info("service=%s event=start.complete", name)
        except Exception as error:
            with self._lock:
                runtime.state = ServiceState.FAILED
                runtime.last_error = str(error)[:500]
                runtime.message = "start failed"
                runtime.error_count += 1
                self._invalidate_cache()
            _LOG.exception("service=%s event=start.failed", name)
            raise

    def start_newly_enabled(self) -> dict[str, Any]:
        return self.start_all()

    def stop_service(self, name: str) -> dict[str, Any]:
        with self._operation_lock:
            with self._lock:
                if name not in self._specs:
                    raise KeyError("unknown service: " + str(name))
                spec = self._specs[name]
                runtime = self._runtime[name]
                if runtime.state != ServiceState.RUNNING:
                    return {"stopped": False, "reason": "service is not running"}
                if not spec.stops_alone:
                    return {"stopped": False, "reason": "service cannot stop independently"}
                if spec.stop is None:
                    return {"stopped": False, "reason": "service has no independent stop callback"}
                dependents = [
                    other for other, candidate in self._specs.items()
                    if name in candidate.dependencies
                    and self._runtime[other].state == ServiceState.RUNNING
                ]
                if dependents:
                    return {
                        "stopped": False,
                        "reason": "running dependents: " + ", ".join(sorted(dependents)),
                    }
                runtime.state = ServiceState.STOPPING
                handle = runtime.handle
            return self._stop_one(name, handle)

    def _stop_one(self, name: str, handle: Any) -> dict[str, Any]:
        spec = self._specs[name]
        try:
            if spec.stop is not None:
                spec.stop(handle)
            if handle is not None and callable(getattr(handle, "join", None)):
                handle.join(self.stop_grace_seconds)
                alive = _is_alive(handle)
                if alive is True:
                    raise TimeoutError("service thread did not stop within grace period")
            with self._lock:
                runtime = self._runtime[name]
                runtime.state = ServiceState.STOPPED
                runtime.handle = None
                runtime.stopped_at = _utc_now()
                runtime.message = None
                self._invalidate_cache()
            _LOG.info("service=%s event=stop.complete", name)
            return {"stopped": True, "reason": None}
        except Exception as error:
            with self._lock:
                runtime = self._runtime[name]
                runtime.state = ServiceState.DEGRADED
                runtime.last_error = str(error)[:500]
                runtime.message = "stop did not complete"
                runtime.error_count += 1
                self._invalidate_cache()
            _LOG.exception("service=%s event=stop.failed", name)
            return {"stopped": False, "reason": str(error)[:300]}

    def stop_all(self) -> dict[str, Any]:
        with self._operation_lock:
            with self._lock:
                order = list(self._start_order)
                for name, runtime in self._runtime.items():
                    if runtime.state == ServiceState.RUNNING and name not in order:
                        order.append(name)
            stopped, failures = [], []
            for name in reversed(order):
                with self._lock:
                    runtime = self._runtime[name]
                    if runtime.state != ServiceState.RUNNING:
                        continue
                    runtime.state = ServiceState.STOPPING
                    handle = runtime.handle
                result = self._stop_one(name, handle)
                if result["stopped"]:
                    stopped.append(name)
                else:
                    failures.append({"name": name, "reason": result["reason"]})
            with self._lock:
                self._start_order = [
                    name for name in self._start_order
                    if self._runtime[name].state == ServiceState.RUNNING
                ]
                self._invalidate_cache()
            return {"stopped": stopped, "failures": failures}

    def get_status(self, name: Optional[str] = None) -> Any:
        with self._lock:
            names = [name] if name is not None else sorted(self._specs)
            result = {}
            for item in names:
                if item not in self._specs:
                    raise KeyError("unknown service: " + str(item))
                spec = self._specs[item]
                runtime = self._runtime[item]
                alive = _is_alive(runtime.handle)
                state = runtime.state
                if state == ServiceState.RUNNING and alive is False:
                    runtime.state = ServiceState.FAILED
                    runtime.last_error = "service worker exited unexpectedly"
                    runtime.error_count += 1
                    state = ServiceState.FAILED
                result[item] = {
                    "name": item,
                    "state": state.value,
                    "enabled": self._enabled(spec),
                    "essential": spec.essential,
                    "priority": spec.priority,
                    "dependencies": list(spec.dependencies),
                    "started_at": runtime.started_at,
                    "ready_at": runtime.ready_at,
                    "stopped_at": runtime.stopped_at,
                    "start_duration_ms": runtime.start_duration_ms,
                    "uptime_seconds": (
                        max(0.0, time.time() - datetime.fromisoformat(runtime.ready_at).timestamp())
                        if runtime.ready_at and state == ServiceState.RUNNING else 0.0
                    ),
                    "message": runtime.message,
                    "last_error": runtime.last_error,
                    "error_count": runtime.error_count,
                    "restart_count": runtime.restart_count,
                }
            return result[name] if name is not None else result

    def get_health(self, refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if not refresh and self._health_cache and now - self._health_cache_at < self.cache_ttl_seconds:
                return {key: dict(value) for key, value in self._health_cache.items()}
            specs = dict(self._specs)
            runtime_states = {name: state.state for name, state in self._runtime.items()}
        result = {}
        for name, spec in specs.items():
            state = runtime_states[name]
            if state == ServiceState.DISABLED:
                result[name] = {"status": "disabled", "message": "disabled by configuration"}
                continue
            if state in (ServiceState.FAILED, ServiceState.BLOCKED):
                result[name] = {"status": "unhealthy", "message": self._runtime[name].last_error or self._runtime[name].message}
                continue
            if state in (ServiceState.REGISTERED, ServiceState.INITIALIZING, ServiceState.STARTING):
                result[name] = {"status": "starting", "message": None}
                continue
            if state in (ServiceState.STOPPING, ServiceState.STOPPED):
                result[name] = {"status": "stopping" if state == ServiceState.STOPPING else "disabled", "message": None}
                continue
            if spec.health is None:
                result[name] = {"status": "healthy" if state == ServiceState.RUNNING else "degraded", "message": None}
                continue
            try:
                raw = spec.health()
                if isinstance(raw, dict):
                    status = str(raw.get("status", "healthy")).lower()
                    message = raw.get("message")
                elif isinstance(raw, bool):
                    status, message = ("healthy", None) if raw else ("unhealthy", "health check returned false")
                else:
                    status, message = str(raw).lower(), None
                if status not in {"healthy", "degraded", "unhealthy", "starting", "stopping", "disabled"}:
                    status, message = "degraded", "unknown health status: " + status
                result[name] = {"status": status, "message": str(message)[:300] if message else None}
            except Exception as error:
                result[name] = {"status": "unhealthy", "message": str(error)[:300]}
        with self._lock:
            self._health_cache = dict(result)
            self._health_cache_at = now
        return result

    def get_metrics(self) -> dict[str, Any]:
        status = self.get_status()
        return {
            name: {
                "uptime_seconds": item["uptime_seconds"],
                "start_duration_ms": item["start_duration_ms"],
                "error_count": item["error_count"],
                "restart_count": item["restart_count"],
                "running": item["state"] == ServiceState.RUNNING.value,
            }
            for name, item in status.items()
        }

    def _invalidate_cache(self) -> None:
        self._health_cache = {}
        self._health_cache_at = 0.0


_GLOBAL_LOCK = threading.RLock()
_GLOBAL_MANAGER: Optional[ServiceManager] = None


def set_global_service_manager(manager: ServiceManager) -> None:
    if not isinstance(manager, ServiceManager):
        raise TypeError("manager must be a ServiceManager")
    global _GLOBAL_MANAGER
    with _GLOBAL_LOCK:
        _GLOBAL_MANAGER = manager


def get_global_service_manager() -> Optional[ServiceManager]:
    with _GLOBAL_LOCK:
        return _GLOBAL_MANAGER


def clear_global_service_manager() -> None:
    global _GLOBAL_MANAGER
    with _GLOBAL_LOCK:
        _GLOBAL_MANAGER = None
