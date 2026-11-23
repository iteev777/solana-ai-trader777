"""Process-local emergency trading halt shared by entry and paper execution paths.

The halt is deliberately independent of live execution configuration: live execution
remains hard-locked, while this control stops paper entries and paper exits alike.
"""
import threading
from datetime import datetime, timezone

_lock = threading.RLock()
_stopped = False
_stopped_at = None
_reason = None


def is_force_stopped():
    with _lock:
        return _stopped


def set_force_stopped(stopped, reason=None):
    if not isinstance(stopped, bool):
        raise ValueError("stopped must be a boolean")
    global _stopped, _stopped_at, _reason
    normalized_reason = None
    if reason is not None:
        if not isinstance(reason, str):
            raise ValueError("reason must be text")
        normalized_reason = reason.strip()[:300].strip() or None
    with _lock:
        _stopped = stopped
        if stopped:
            _stopped_at = datetime.now(timezone.utc).isoformat()
            _reason = normalized_reason
        else:
            _stopped_at = None
            _reason = None
        return get_force_stop_status()


def get_force_stop_status():
    with _lock:
        return {
            "is_stopped": _stopped,
            "stopped_at": _stopped_at,
            "reason": _reason,
            "scope": "paper_entries_and_exits",
            "live_execution": "locked",
        }


def reset_for_tests():
    set_force_stopped(False)
