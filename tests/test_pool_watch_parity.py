import json
import unittest
from unittest.mock import Mock, patch

import onchain_stream as stream
import solana_onchain as onchain


def put_key(raw, offset, value):
    raw[offset:offset + 32] = onchain._b58decode(value)


class CandidatePoolWatchTests(unittest.TestCase):
    def setUp(self):
        with stream._lock:
            stream._watches.clear()
            stream._candidate_watches.clear()
            stream._snapshots.clear()
            stream._refreshing.clear()
            stream._last_refresh_at.clear()

    def test_candidate_watches_survive_position_watch_sync(self):
        candidate = ("pool-candidate", "pumpswap", "mint-candidate")
        position = ("pool-position", "raydium_cpmm", "mint-position")
        with patch.object(stream, "_ensure_thread_locked"):
            self.assertTrue(stream.watch_pool_price(*candidate))
            stream.sync_watched_pools([position])
        self.assertEqual(set(stream.get_watched_pools()), {candidate, position})
        status = stream.get_stream_status()
        self.assertEqual(status["candidate_watches"], 1)
        self.assertEqual(status["position_watches"], 1)

    def test_candidate_watch_expires_after_ttl(self):
        candidate = ("pool-expiring", "pumpswap", "mint-expiring")
        with patch.object(stream, "_ensure_thread_locked"), \
             patch.object(stream.time, "time", return_value=100.0):
            stream.watch_pool_price(*candidate)
        with patch.object(stream.time, "time",
                          return_value=100.0 + stream.CANDIDATE_WATCH_TTL + 1):
            self.assertNotIn(candidate, stream.get_watched_pools())
        self.assertEqual(stream.get_stream_status()["candidate_watches"], 0)

    def test_candidate_watch_registry_is_bounded_lru(self):
        with patch.object(stream, "_ensure_thread_locked"), \
             patch.object(stream, "MAX_CANDIDATE_WATCHES", 10), \
             patch.object(stream.time, "time", return_value=100.0):
            for index in range(11):
                stream.watch_pool_price("pool-" + str(index), "pumpswap", "mint-" + str(index))
            watched = stream.get_watched_pools()
        self.assertEqual(len(watched), 10)
        self.assertNotIn(("pool-0", "pumpswap", "mint-0"), watched)

    def test_stream_account_list_includes_pool_and_both_reserve_vaults(self):
        pool_id = onchain._b58encode(bytes([9]) * 32)
        base_mint = onchain._b58encode(bytes([1]) * 32)
        quote_mint = onchain._b58encode(bytes([2]) * 32)
        vault_base = onchain._b58encode(bytes([3]) * 32)
        vault_quote = onchain._b58encode(bytes([4]) * 32)
        raw = bytearray(300)
        raw[:8] = bytes([241, 154, 109, 4, 17, 177, 109, 188])
        put_key(raw, 43, base_mint)
        put_key(raw, 75, quote_mint)
        put_key(raw, 139, vault_base)
        put_key(raw, 171, vault_quote)
        account = {"owner": onchain.PROGRAMS["pumpswap"], "data": bytes(raw)}
        with patch.object(onchain, "_account", return_value=account):
            accounts = onchain.get_pool_watch_accounts(pool_id, "pumpfun_amm")
        self.assertEqual(accounts, [pool_id, vault_base, vault_quote])

    def test_unknown_pool_owner_is_never_subscribed(self):
        with patch.object(onchain, "_account",
                          return_value={"owner": "unknown-program", "data": b""}):
            with self.assertRaisesRegex(ValueError, "not a supported"):
                onchain.get_pool_watch_accounts("pool-id", "pumpswap")

    def test_shared_account_notification_refreshes_both_watches(self):
        pool_a = ("pool", "raydium_cpmm", "mint-a")
        pool_b = ("pool", "raydium_cpmm", "mint-b")
        ws = Mock()
        ws.recv.side_effect = [
            json.dumps({"id": 1, "result": 55}),
            json.dumps({"method": "accountNotification", "params": {
                "subscription": 55, "result": {}
            }}),
            EOFError,
        ]
        with patch.object(onchain, "get_pool_watch_accounts", return_value=["shared"]), \
             patch.object(stream, "_active_watch_keys_locked", return_value={pool_a, pool_b}), \
             patch.object(stream, "_batch_refresh_pool") as batch, \
             patch.object(stream, "_batch_refreshing", False), \
             patch.object(stream, "_dirty_pools", set()), \
             patch.object(stream, "_status", dict(stream._status)):
            with self.assertRaises(EOFError):
                stream._watch_connection(ws)
            self.assertEqual(stream._dirty_pools, {pool_a, pool_b})
            batch.submit.assert_called_once_with(stream._refresh_many, [pool_a, pool_b], True)
        self.assertEqual(ws.send.call_count, 1)

    def test_late_acknowledgement_is_released_after_watch_removed(self):
        pool = ("pool", "raydium_cpmm", "mint")
        ws = Mock()
        ws.recv.side_effect = [
            stream.websocket.WebSocketTimeoutException,
            json.dumps({"id": 1, "result": 55}),
            EOFError,
        ]
        with patch.object(onchain, "get_pool_watch_accounts", return_value=["shared"]), \
             patch.object(stream, "_active_watch_keys_locked", side_effect=[{pool}, set(), set()]), \
             patch.object(stream, "_batch_refreshing", True), \
             patch.object(stream, "_dirty_pools", set()), \
             patch.object(stream, "_status", dict(stream._status)):
            with self.assertRaises(EOFError):
                stream._watch_connection(ws)
        requests = [json.loads(call.args[0]) for call in ws.send.call_args_list]
        self.assertEqual([request["method"] for request in requests], [
            "accountSubscribe", "accountUnsubscribe"
        ])
        self.assertEqual(requests[1]["params"], [55])

    def test_late_snapshot_cannot_recreate_removed_watch(self):
        pool = ("pool", "raydium_cpmm", "mint")
        with patch.object(stream, "_ensure_thread_locked"), \
             patch.object(stream.time, "time", return_value=100):
            stream.sync_watched_pools([pool])
            verified = {"onchainVerified": True, "observedAt": 100}
            self.assertTrue(stream.store_pool_snapshot(pool, verified))
            self.assertFalse(stream.store_pool_snapshot(pool, dict(verified, observedAt=98)))
            self.assertEqual(stream.get_latest_snapshot(*pool)["observedAt"], 100)
            stream.sync_watched_pools([])
            self.assertFalse(stream.store_pool_snapshot(pool, verified))
            self.assertIsNone(stream.get_latest_snapshot(*pool))


if __name__ == "__main__":
    unittest.main()
