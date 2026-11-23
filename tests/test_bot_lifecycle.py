"""Process-level smoke tests for the paper bot and authenticated dashboard."""

import base64
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


import rpc_transport


ROOT = Path(__file__).resolve().parents[1]
OFFLINE_ENTRYPOINT = """
import runpy
import sys
from unittest.mock import patch

script = sys.argv[1]
with (
    patch("sniper_scanner.start_listener"),
    patch("scanner.scan_tokens", return_value=[]),
    patch("discovery.token_aggregator.collect_tokens", return_value=[]),
):
    runpy.run_path(script, run_name="__main__")
"""


class BotLifecycleTests(unittest.TestCase):
    @patch("rpc_transport.requests.post")
    def test_rpc_never_sends_mutating_or_verified_simulation_calls(self, post):
        with self.assertRaisesRegex(ValueError, "read-only"):
            rpc_transport.rpc_call("sendTransaction", ["transaction"])
        with self.assertRaisesRegex(ValueError, "sigVerify=false"):
            rpc_transport.rpc_call("simulateTransaction", ["transaction", {"sigVerify": True}])
        post.assert_not_called()

    def test_paper_bot_starts_and_handles_sigterm(self):
        with TemporaryDirectory() as work_dir:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            token = secrets.token_urlsafe()
            env = {
                **os.environ,
                "BOT_DATA_DIR": work_dir,
                "DASHBOARD_HOST": "127.0.0.1",
                "PORT": str(port),
                "TROJAN_DASHBOARD_TOKEN": token,
            }
            for name in (
                "SOLANA_RPC_URL", "SOLANA_RPC_URLS", "SOLANA_WS_URL",
                "HELIUS_RPC_URL", "QUICKNODE_RPC_URL", "TRITON_RPC_URL", "ALCHEMY_RPC_URL",
                "TROJAN_COPY_WALLETS", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            ):
                env.pop(name, None)

            log_path = Path(work_dir) / "bot.log"
            with log_path.open("w") as log, subprocess.Popen(
                [sys.executable, "-u", "-c", OFFLINE_ENTRYPOINT, str(ROOT / "main.py")],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            ) as bot:
                try:
                    self.check_dashboard(port, token, bot, log_path)
                    bot.terminate()
                    self.assertEqual(bot.wait(timeout=8), 0, log_path.read_text())
                    self.assertIn("BOT SHUTTING DOWN", log_path.read_text())
                finally:
                    if bot.poll() is None:
                        bot.kill()
                        bot.wait(timeout=5)
            with sqlite3.connect(Path(work_dir) / "trading_history.db") as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone(), ("ok",))

    def test_rejected_discovery_candidate_does_not_block_next_candidate(self):
        import main

        candidates = [
            {"tokenAddress": "rejected", "candidate_quality": {
                "hard_reject": True, "reasons": ["invalid market data"],
            }},
            {"tokenAddress": "eligible"},
        ]
        with patch.object(main.paper, "get_positions", return_value={}), \
             patch.object(main, "schedule_token", return_value=True) as schedule:
            main.queue_discovery_candidates(candidates, scanned=2, invalid=0)
        schedule.assert_called_once_with("eligible", "main", candidates[1])

    def check_dashboard(self, port, token, bot, log_path):
        http = build_opener(ProxyHandler({}))
        base = f"http://127.0.0.1:{port}"
        credential = base64.b64encode(f"test:{token}".encode()).decode()
        headers = {"Authorization": f"Basic {credential}"}
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and bot.poll() is None:
            try:
                with http.open(Request(base + "/api/readiness", headers=headers), timeout=1) as response:
                    readiness = json.load(response)["readiness"]
                if readiness["paper_ready"]:
                    break
            except (URLError, TimeoutError):
                pass
            time.sleep(0.05)
        else:
            self.fail("Paper readiness did not pass:\n" + log_path.read_text())
        self.assertEqual(readiness["mode"], "PAPER")
        self.assertEqual(readiness["live_execution"], "LOCKED")
        self.assertFalse(readiness["live_ready"])
        with self.assertRaises(HTTPError) as rejected:
            http.open(base + "/api/health", timeout=1)
        self.assertEqual(rejected.exception.code, 401)
        rejected.exception.close()
