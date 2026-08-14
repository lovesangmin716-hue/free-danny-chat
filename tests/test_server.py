from __future__ import annotations

import importlib.util
import os
import queue
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path


TEST_DATA_DIR = tempfile.mkdtemp(prefix="colorless-tests-")
os.environ["DATA_DIR"] = TEST_DATA_DIR
os.environ["STATE_FILE"] = str(Path(TEST_DATA_DIR) / "global-state.json")
os.environ["UPLOADS_DIR"] = str(Path(TEST_DATA_DIR) / "uploads")

SERVER_PATH = Path(__file__).parents[1] / "outputs" / "chat-app" / "server.py"
SPEC = importlib.util.spec_from_file_location("colorless_server", SERVER_PATH)
assert SPEC is not None and SPEC.loader is not None
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class StateStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="colorless-store-")
        self.store = server.StateStore(Path(self.temp_dir.name) / "state.json")
        self.alice = self.store.create_or_update_social_user("demo", "alice-id", nickname="alice")
        self.bob = self.store.create_or_update_social_user("demo", "bob-id", nickname="bob")
        self.eve = self.store.create_or_update_social_user("demo", "eve-id", nickname="eve")
        self.store.add_friend("alice", self.bob["id"])
        room, _, error = self.store.create_or_get_direct_room("alice", self.bob["id"])
        self.assertIsNone(error)
        assert room is not None
        self.room_id = room["id"]

    def tearDown(self) -> None:
        self.assertTrue(self.store.close())
        self.temp_dir.cleanup()

    def test_room_events_only_reach_authorized_subscribers(self) -> None:
        recipients = self.store.room_event_recipients(self.room_id)
        self.assertEqual(recipients, {"alice", "bob"})

        subscribers = {name: queue.Queue(maxsize=2) for name in ("alice", "bob", "eve")}
        with server.SUBSCRIBERS_LOCK:
            server.SUBSCRIBERS.clear()
            server.SUBSCRIBERS.update({subscriber: name for name, subscriber in subscribers.items()})
        try:
            server.push_event({"type": "message_created"}, recipients)
            self.assertEqual(subscribers["alice"].get_nowait()["type"], "message_created")
            self.assertEqual(subscribers["bob"].get_nowait()["type"], "message_created")
            with self.assertRaises(queue.Empty):
                subscribers["eve"].get_nowait()
        finally:
            with server.SUBSCRIBERS_LOCK:
                server.SUBSCRIBERS.clear()

    def test_mark_room_read_only_changes_once_per_last_message(self) -> None:
        result = self.store.add_message(self.room_id, "bob", "hello")
        self.assertIsNotNone(result)

        room, changed = self.store.mark_room_read(self.room_id, "alice")
        self.assertIsNotNone(room)
        self.assertTrue(changed)

        _, changed_again = self.store.mark_room_read(self.room_id, "alice")
        self.assertFalse(changed_again)

    def test_attachment_access_follows_room_membership(self) -> None:
        attachment = {
            "url": "/uploads/upload_test.pdf",
            "name": "test.pdf",
            "type": "application/pdf",
            "size": 5,
        }
        self.store.add_message(self.room_id, "alice", "", attachment)

        self.assertTrue(self.store.can_access_attachment("upload_test.pdf", "alice"))
        self.assertTrue(self.store.can_access_attachment("upload_test.pdf", "bob"))
        self.assertFalse(self.store.can_access_attachment("upload_test.pdf", "eve"))

    def test_profile_image_is_available_to_authenticated_users(self) -> None:
        filename = server.profile_image_filename(self.alice["id"])
        thumbnail_filename = server.profile_image_filename(self.alice["id"], thumbnail=True)
        updated_user = self.store.update_profile_image(
            "alice",
            f"/uploads/{filename}",
            f"/uploads/{thumbnail_filename}",
        )

        self.assertIsNotNone(updated_user)
        assert updated_user is not None
        self.assertTrue(updated_user["profile_image_url"].startswith(f"/uploads/{filename}?v="))
        self.assertTrue(updated_user["profile_thumbnail_url"].startswith(f"/uploads/{thumbnail_filename}?v="))
        self.assertTrue(self.store.can_access_attachment(filename, "alice"))
        self.assertTrue(self.store.can_access_attachment(thumbnail_filename, "alice"))
        self.assertTrue(self.store.can_access_attachment(filename, "bob"))
        self.assertTrue(self.store.can_access_attachment(filename, "eve"))

        self.store.update_profile_image("alice", "")
        self.assertFalse(self.store.can_access_attachment(filename, "alice"))
        self.assertFalse(self.store.can_access_attachment(thumbnail_filename, "alice"))

    def test_message_persistence_only_snapshots_affected_parts(self) -> None:
        self.assertTrue(self.store.flush())
        written_part_sets = []
        original_write = self.store._write_state

        def capture_write(parts: dict) -> None:
            written_part_sets.append(set(parts))
            original_write(parts)

        self.store._write_state = capture_write
        self.store.add_message(self.room_id, "alice", "incremental")
        self.assertTrue(self.store.flush())

        self.assertEqual(written_part_sets, [{"rooms", f"messages:{self.room_id}"}])

    def test_incremental_message_survives_restart(self) -> None:
        saved = self.store.add_message(self.room_id, "alice", "persisted")
        self.assertIsNotNone(saved)
        self.assertTrue(self.store.flush())

        state_path = self.store.path
        self.assertTrue(self.store.close())
        self.store = server.StateStore(state_path)

        messages = self.store.get_messages(self.room_id, "bob")
        self.assertIsNotNone(messages)
        assert messages is not None
        self.assertEqual(messages[-1]["text"], "persisted")

    def test_shorts_history_is_bounded_and_persisted(self) -> None:
        seen_ids = [f"video-{index}" for index in range(600)]
        self.store.save_shorts_feed("alice", seen_ids, "next")

        saved_ids, cursor = self.store.get_shorts_feed("alice")
        self.assertEqual(len(saved_ids), server.MAX_SHORTS_SEEN_IDS)
        self.assertEqual(saved_ids[0], "video-100")
        self.assertEqual(cursor, "next")
        self.assertTrue(self.store.flush())

    def test_session_hash_survives_state_store_restart(self) -> None:
        sessions = server.SessionStore(state_store=self.store)
        token = sessions.create("alice")
        token_hash = server.hashlib.sha256(token.encode("utf-8")).hexdigest()

        self.assertNotIn(token, self.store.state["sessions"])
        self.assertIn(token_hash, self.store.state["sessions"])
        self.assertTrue(self.store.flush())

        state_path = self.store.path
        self.assertTrue(self.store.close())
        self.store = server.StateStore(state_path)
        restored_sessions = server.SessionStore(state_store=self.store)
        self.assertEqual(restored_sessions.get_username(token), "alice")

    def test_mutation_does_not_wait_for_slow_persistence(self) -> None:
        original_write = self.store._write_state

        def slow_write(state: dict) -> None:
            time.sleep(0.15)
            original_write(state)

        self.store._write_state = slow_write
        started = time.perf_counter()
        self.store.save_shorts_feed("alice", ["video"], "")
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.05)
        self.assertTrue(self.store.flush())


class BoundedStoresTestCase(unittest.TestCase):
    @staticmethod
    def make_vp8x(width: int, height: int) -> bytes:
        dimensions = (
            b"\x00\x00\x00\x00"
            + (width - 1).to_bytes(3, "little")
            + (height - 1).to_bytes(3, "little")
        )
        chunk = b"VP8X" + len(dimensions).to_bytes(4, "little") + dimensions
        payload = b"WEBP" + chunk
        return b"RIFF" + len(payload).to_bytes(4, "little") + payload

    def test_profile_webp_dimensions_are_validated(self) -> None:
        valid_image = self.make_vp8x(1024, 1024)
        wrong_size = self.make_vp8x(1024, 768)

        self.assertEqual(server.webp_dimensions(valid_image), (1024, 1024))
        self.assertEqual(server.webp_dimensions(wrong_size), (1024, 768))
        self.assertIsNone(server.webp_dimensions(valid_image + b"trailing"))
        self.assertIsNone(server.webp_dimensions(b"not-webp"))

    def test_sessions_expire_and_are_bounded(self) -> None:
        expired_store = server.SessionStore(ttl_seconds=-1, max_sessions=2)
        expired_token = expired_store.create("alice")
        self.assertIsNone(expired_store.get_username(expired_token))

        bounded_store = server.SessionStore(ttl_seconds=60, max_sessions=2)
        bounded_store.create("alice")
        bounded_store.create("bob")
        bounded_store.create("eve")
        self.assertLessEqual(len(bounded_store.sessions), 2)

    def test_rate_limiter_returns_retry_window(self) -> None:
        limiter = server.SlidingWindowRateLimiter()
        self.assertEqual(limiter.allow("login", 2, 60), (True, 0))
        self.assertEqual(limiter.allow("login", 2, 60), (True, 0))
        allowed, retry_after = limiter.allow("login", 2, 60)
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)

    def test_presence_uses_per_user_index(self) -> None:
        presence = server.PresenceStore()
        self.assertTrue(presence.connect("alice-1", "alice"))
        self.assertFalse(presence.connect("alice-2", "alice"))
        self.assertTrue(presence.connect("bob-1", "bob"))
        presence.update("alice-1", "alice", "room-1", "😀")

        self.assertEqual(presence.tokens_by_username["alice"], {"alice-1", "alice-2"})
        self.assertEqual(presence.for_user("alice")["active_room_ids"], ["room-1"])
        self.assertEqual(presence.for_user("alice")["emoji"], "😀")

    def test_ttl_cache_coalesces_concurrent_fetches(self) -> None:
        cache = server.BoundedTTLCache(max_entries=2, ttl_seconds=60)
        fetch_count = 0
        fetch_lock = threading.Lock()
        barrier = threading.Barrier(5)
        results = []

        def fetch() -> dict:
            nonlocal fetch_count
            with fetch_lock:
                fetch_count += 1
            time.sleep(0.03)
            return {"ok": True}

        def worker() -> None:
            barrier.wait()
            results.append(cache.get_or_fetch("same", fetch))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(fetch_count, 1)
        self.assertEqual(results, [{"ok": True}] * 5)


def tearDownModule() -> None:
    server.STORE.close()
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
