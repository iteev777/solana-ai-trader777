import io
import json
import threading
import unittest
from unittest.mock import Mock, patch

import rpc_transport


class FakeStdout:
    def __init__(self, line):
        self.line = line

    def fileno(self):
        return 42

    def readline(self):
        return self.line


class RustRpcTransportTests(unittest.TestCase):
    def setUp(self):
        self.sidecar_bin_patch = patch.object(rpc_transport, "_RUST_SIDECAR_BIN", "fake-rust-sidecar")
        self.sidecar_bin_patch.start()
        self.addCleanup(self.sidecar_bin_patch.stop)
        self.url = "https://rpc.example.invalid"
        self.sidecar_bin_patch = patch.object(
            rpc_transport, "_RUST_SIDECAR_BIN", "/tmp/trojan-rust-rpc-sidecar-test"
        )
        self.sidecar_bin_patch.start()
        self.original_processes = dict(rpc_transport._rust_sidecars)
        self.original_locks = dict(rpc_transport._rust_sidecar_locks)
        rpc_transport._rust_sidecars.clear()
        rpc_transport._rust_sidecar_locks.clear()

    def tearDown(self):
        self.sidecar_bin_patch.stop()
        rpc_transport._rust_sidecars.clear()
        rpc_transport._rust_sidecars.update(self.original_processes)
        rpc_transport._rust_sidecar_locks.clear()
        rpc_transport._rust_sidecar_locks.update(self.original_locks)

    def process(self, payload):
        proc = Mock()
        proc.poll.return_value = None
        proc.stdin = io.StringIO()
        proc.stdout = FakeStdout(json.dumps(payload) + "\n")
        return proc

    def test_sidecar_returns_only_complete_context_snapshot(self):
        payload = {
            "ok": True,
            "result": {
                "context": {"slot": 123},
                "value": [{"owner": "TokenProgram", "data": ["AA==", "base64"]}],
            },
        }
        proc = self.process(payload)
        rpc_transport._rust_sidecars[self.url] = proc
        rpc_transport._rust_sidecar_locks[self.url] = threading.Lock()
        with patch.object(rpc_transport.select, "select",
                          return_value=([proc.stdout], [], [])):
            result = rpc_transport._rust_rpc_call(
                self.url, "getMultipleAccounts", [["Mint111"], {"encoding": "base64"}]
            )
        self.assertEqual(result["context"]["slot"], 123)
        self.assertEqual(len(result["value"]), 1)
        self.assertIn('"method":"getMultipleAccounts"', proc.stdin.getvalue())

    def test_sidecar_timeout_kills_and_evicts_process(self):
        proc = self.process({"ok": True, "result": {}})
        rpc_transport._rust_sidecars[self.url] = proc
        rpc_transport._rust_sidecar_locks[self.url] = threading.Lock()
        with patch.object(rpc_transport.select, "select", return_value=([], [], [])):
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                rpc_transport._rust_rpc_call(
                    self.url, "getMultipleAccounts", [["Mint111"], {}]
                )
        proc.kill.assert_called_once()
        self.assertNotIn(self.url, rpc_transport._rust_sidecars)

    def test_sidecar_rejects_truncated_or_wrong_count_snapshots(self):
        proc = self.process({
            "ok": True,
            "result": {"context": {"slot": 123}, "value": []},
        })
        rpc_transport._rust_sidecars[self.url] = proc
        rpc_transport._rust_sidecar_locks[self.url] = threading.Lock()
        with patch.object(rpc_transport.select, "select",
                          return_value=([proc.stdout], [], [])):
            with self.assertRaisesRegex(RuntimeError, "invalid getMultipleAccounts"):
                rpc_transport._rust_rpc_call(
                    self.url, "getMultipleAccounts", [["Mint111"], {}]
                )


if __name__ == "__main__":
    unittest.main()
