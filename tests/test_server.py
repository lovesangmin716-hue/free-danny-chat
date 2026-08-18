from __future__ import annotations

import importlib.util
import http.client
import os
import queue
import re
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


class StaticAppStructureTestCase(unittest.TestCase):
    def test_feature_scripts_are_loaded_in_dependency_order(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        script_sources = re.findall(r'<script defer src="([^"]+)"></script>', index_html)

        self.assertEqual(
            [source.split("?", 1)[0] for source in script_sources],
            [
                "assets/js/core.js",
                "assets/js/profile.js",
                "assets/js/messenger.js",
                "assets/js/attachments.js",
                "assets/js/room-settings.js",
                "assets/js/chat.js",
                "assets/js/shorts.js",
                "assets/js/app.js",
                "assets/js/auth.js",
                "assets/js/bootstrap.js",
            ],
        )
        self.assertNotIn("<script>", index_html)
        source_bytes = 0
        compressed_bytes = 0
        for source in script_sources:
            asset_path = server.BASE_DIR / source.split("?", 1)[0]
            self.assertTrue(asset_path.is_file(), source)
            self.assertLess(asset_path.stat().st_size, 32 * 1024, source)
            source_bytes += asset_path.stat().st_size
            compressed_content = server.ASSET_GZIP_CONTENT.get(asset_path.resolve(), b"")
            self.assertTrue(compressed_content, source)
            compressed_bytes += len(compressed_content)
        self.assertLess(compressed_bytes, source_bytes)

    def test_new_chat_uses_one_shared_member_selector(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")

        self.assertIn('id="open-new-chat-button"', index_html)
        self.assertIn('id="new-chat-sheet"', index_html)
        self.assertIn('id="new-chat-member-list"', index_html)
        self.assertIn('id="new-chat-group-name-field"', index_html)
        self.assertNotIn('id="group-room-name"', index_html)
        self.assertNotIn('id="group-member-list"', index_html)

    def test_signup_is_a_separate_responsive_document(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        signup_html = server.SIGNUP_FILE.read_text(encoding="utf-8")
        signup_css = (server.ASSETS_DIR / "css" / "signup.css").read_text(encoding="utf-8")

        self.assertIn('href="/signup"', index_html)
        self.assertNotIn('id="signup-form"', index_html)
        self.assertIn('id="signup-form"', signup_html)
        self.assertIn('src="/assets/js/signup.js"', signup_html)
        self.assertIn('@media (min-width: 768px)', signup_css)
        self.assertIn('@media (max-width: 767px)', signup_css)
        self.assertIn('@media (min-width: 768px)', index_html)
        self.assertIn('@media (max-width: 767px)', index_html)

    def test_render_requires_supabase_persistence(self) -> None:
        render_config = (SERVER_PATH.parents[2] / "render.yaml").read_text(encoding="utf-8")

        self.assertIn("key: REQUIRE_SUPABASE", render_config)
        self.assertIn("key: SUPABASE_URL", render_config)
        self.assertIn("key: SUPABASE_SERVICE_ROLE_KEY", render_config)

    def test_auth_client_omits_logout_body_and_reports_http_status(self) -> None:
        auth_script = (server.ASSETS_DIR / "js" / "auth.js").read_text(encoding="utf-8")
        core_script = (server.ASSETS_DIR / "js" / "core.js").read_text(encoding="utf-8")

        self.assertIn('await api("/logout", { method: "POST" });', auth_script)
        self.assertNotIn('api("/logout", { method: "POST", body:', auth_script)
        self.assertIn('${method} ${url} 요청 실패 (HTTP ${statusLabel})', core_script)


class AuthenticationHttpIntegrationTestCase(unittest.TestCase):
    def test_signup_page_is_served_separately_from_signup_api(self) -> None:
        http_server = server.ChatServer(("127.0.0.1", 0), server.ChatHandler)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", http_server.server_address[1], timeout=5)
        try:
            connection.request("GET", "/signup")
            response = connection.getresponse()
            content = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn('id="signup-form"', content)
            self.assertNotIn('id="login-form"', content)
        finally:
            connection.close()
            http_server.shutdown()
            http_server.server_close()
            server_thread.join(timeout=5)

    def test_logout_body_does_not_corrupt_next_login_on_keep_alive_connection(self) -> None:
        unique_suffix = str(time.time_ns())[-10:]
        username = f"http{unique_suffix}"
        password = "test-password"
        user, error = server.STORE.create_local_user(
            username,
            f"http_{unique_suffix}",
            password,
            "",
            f"010{unique_suffix[:8]}",
            "20대",
            "남성",
        )
        self.assertIsNone(error)
        self.assertIsNotNone(user)

        http_server = server.ChatServer(("127.0.0.1", 0), server.ChatHandler)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", http_server.server_address[1], timeout=5)
        try:
            login_body = f'{{"username":"{username}","password":"{password}"}}'
            connection.request(
                "POST",
                "/login",
                body=login_body,
                headers={"Content-Type": "application/json", "Content-Length": str(len(login_body))},
            )
            first_login_response = connection.getresponse()
            self.assertEqual(first_login_response.status, 200)
            first_cookie = first_login_response.getheader("Set-Cookie", "").split(";", 1)[0]
            self.assertIn("codex_talk_session=", first_cookie)
            first_login_response.read()

            connection.request(
                "POST",
                "/logout",
                body="{}",
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": "2",
                    "Cookie": first_cookie,
                },
            )
            logout_response = connection.getresponse()
            self.assertEqual(logout_response.status, 200)
            logout_response.read()

            connection.request(
                "POST",
                "/login",
                body=login_body,
                headers={"Content-Type": "application/json", "Content-Length": str(len(login_body))},
            )
            login_response = connection.getresponse()
            self.assertEqual(login_response.status, 200)
            second_cookie = login_response.getheader("Set-Cookie", "").split(";", 1)[0]
            self.assertIn("codex_talk_session=", second_cookie)
            login_response.read()

            connection.request("GET", "/messenger", headers={"Cookie": second_cookie})
            messenger_response = connection.getresponse()
            self.assertEqual(messenger_response.status, 200)
            messenger_response.read()
        finally:
            connection.close()
            http_server.shutdown()
            http_server.server_close()
            server_thread.join(timeout=5)

    def test_unhandled_post_body_does_not_corrupt_next_request(self) -> None:
        http_server = server.ChatServer(("127.0.0.1", 0), server.ChatHandler)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", http_server.server_address[1], timeout=5)
        try:
            connection.request(
                "POST",
                "/unknown-post",
                body="{}",
                headers={"Content-Type": "application/json", "Content-Length": "2"},
            )
            unknown_response = connection.getresponse()
            self.assertEqual(unknown_response.status, 404)
            unknown_response.read()

            connection.request("GET", "/health")
            health_response = connection.getresponse()
            self.assertEqual(health_response.status, 200)
            health_response.read()
        finally:
            connection.close()
            http_server.shutdown()
            http_server.server_close()
            server_thread.join(timeout=5)


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
            server.SUBSCRIBERS_BY_USERNAME.clear()
            for name, subscriber in subscribers.items():
                server.SUBSCRIBERS_BY_USERNAME.setdefault(name, set()).add(subscriber)
        try:
            server.push_event({"type": "message_created"}, recipients)
            self.assertEqual(subscribers["alice"].get_nowait()["type"], "message_created")
            self.assertEqual(subscribers["bob"].get_nowait()["type"], "message_created")
            with self.assertRaises(queue.Empty):
                subscribers["eve"].get_nowait()
        finally:
            with server.SUBSCRIBERS_LOCK:
                server.SUBSCRIBERS.clear()
                server.SUBSCRIBERS_BY_USERNAME.clear()

    def test_friend_add_does_not_create_room_and_direct_room_is_deduplicated(self) -> None:
        mallory = self.store.create_or_update_social_user("demo", "mallory-id", nickname="mallory")
        room_count_before = len(self.store.state["rooms"])

        friend, error = self.store.add_friend_by_code("alice", mallory["friend_code"])
        self.assertIsNone(error)
        self.assertIsNotNone(friend)
        self.assertEqual(len(self.store.state["rooms"]), room_count_before)

        room, created, error = self.store.create_or_get_direct_room("alice", mallory["id"])
        self.assertIsNone(error)
        self.assertTrue(created)
        self.assertIsNotNone(room)
        assert room is not None

        duplicate, created_again, error = self.store.create_or_get_direct_room("alice", mallory["id"])
        self.assertIsNone(error)
        self.assertFalse(created_again)
        self.assertIsNotNone(duplicate)
        assert duplicate is not None
        self.assertEqual(duplicate["id"], room["id"])
        self.assertEqual(len(self.store.state["rooms"]), room_count_before + 1)

        summaries = self.store.room_event_summaries(room["id"])
        self.assertEqual(set(summaries), {"alice", "mallory"})
        self.assertEqual(summaries["alice"]["peer"]["id"], mallory["id"])
        self.assertEqual(summaries["mallory"]["peer"]["id"], self.alice["id"])

    def test_mark_room_read_only_changes_once_per_last_message(self) -> None:
        result = self.store.add_message(self.room_id, "bob", "hello")
        self.assertIsNotNone(result)

        room, changed = self.store.mark_room_read(self.room_id, "alice")
        self.assertIsNotNone(room)
        self.assertTrue(changed)

        _, changed_again = self.store.mark_room_read(self.room_id, "alice")
        self.assertFalse(changed_again)

    def test_group_room_requires_friends_and_enforces_membership(self) -> None:
        group, error = self.store.create_group_room(
            "alice",
            "Project Team",
            [self.bob["id"], self.eve["id"]],
        )
        self.assertIsNone(group)
        self.assertIsNotNone(error)

        self.store.add_friend_by_code("alice", self.eve["friend_code"])
        group, error = self.store.create_group_room(
            "alice",
            "Project Team",
            [self.bob["id"], self.eve["id"]],
        )
        self.assertIsNone(error)
        self.assertIsNotNone(group)
        assert group is not None
        self.assertEqual(group["kind"], "group")
        self.assertEqual(group["participant_count"], 3)
        self.assertEqual(len(group["participants"]), 3)
        self.assertEqual(self.store.room_event_recipients(group["id"]), {"alice", "bob", "eve"})
        summaries = self.store.room_event_summaries(group["id"])
        self.assertEqual(set(summaries), {"alice", "bob", "eve"})
        self.assertTrue(all(summary["participant_count"] == 3 for summary in summaries.values()))

        bob_rooms = self.store.get_messenger_bootstrap(self.bob)["rooms"]
        self.assertIn(group["id"], {room["id"] for room in bob_rooms})
        self.store.add_message(group["id"], "eve", "hello group")
        self.assertEqual((self.store.get_messages(group["id"], "bob") or [])[-1]["text"], "hello group")

        outsider = self.store.create_or_update_social_user("demo", "mallory-id", nickname="mallory")
        self.assertIsNone(self.store.get_messages(group["id"], outsider["username"]))

    def test_group_message_is_read_after_every_other_member_reads(self) -> None:
        self.store.add_friend_by_code("alice", self.eve["friend_code"])
        group, error = self.store.create_group_room(
            "alice",
            "Read Team",
            [self.bob["id"], self.eve["id"]],
        )
        self.assertIsNone(error)
        assert group is not None
        self.store.add_message(group["id"], "alice", "check read state")

        self.store.mark_room_read(group["id"], "bob")
        alice_messages = self.store.get_messages(group["id"], "alice") or []
        self.assertFalse(alice_messages[-1]["read"])

        self.store.mark_room_read(group["id"], "eve")
        alice_messages = self.store.get_messages(group["id"], "alice") or []
        self.assertTrue(alice_messages[-1]["read"])

    def test_group_settings_and_leave_revoke_access_and_transfer_owner(self) -> None:
        self.store.add_friend_by_code("alice", self.eve["friend_code"])
        group, error = self.store.create_group_room(
            "alice",
            "Original Team",
            [self.bob["id"], self.eve["id"]],
        )
        self.assertIsNone(error)
        assert group is not None
        room_id = group["id"]

        self.assertEqual(self.store.group_room_access("alice", room_id), "owner")
        self.assertEqual(self.store.group_room_access("bob", room_id), "member")
        updated, error = self.store.update_group_room_name("bob", room_id, "Not Allowed")
        self.assertIsNone(updated)
        self.assertEqual(error, "forbidden")

        updated, error = self.store.update_group_room_name("alice", room_id, "Renamed Team")
        self.assertIsNone(error)
        assert updated is not None
        self.assertEqual(updated["name"], "Renamed Team")

        image_filename = server.room_image_filename(room_id)
        thumbnail_filename = server.room_image_filename(room_id, thumbnail=True)
        updated, error = self.store.update_group_room_image(
            "alice",
            room_id,
            f"/uploads/{image_filename}",
            f"/uploads/{thumbnail_filename}",
        )
        self.assertIsNone(error)
        assert updated is not None
        self.assertTrue(updated["image_url"].startswith(f"/uploads/{image_filename}?v="))
        self.assertTrue(self.store.can_access_attachment(image_filename, "bob"))

        attachment = {
            "url": "/uploads/upload_group_test.pdf",
            "name": "group-test.pdf",
            "type": "application/pdf",
            "size": 5,
        }
        self.store.add_message(room_id, "alice", "private", attachment)
        room_after_leave, recipients, error = self.store.leave_group_room("alice", room_id)
        self.assertIsNone(error)
        self.assertEqual(recipients, {"alice", "bob", "eve"})
        assert room_after_leave is not None
        self.assertEqual(room_after_leave["created_by"], "bob")
        self.assertEqual(room_after_leave["participant_count"], 2)
        self.assertIsNone(self.store.get_messages(room_id, "alice"))
        self.assertFalse(self.store.can_access_attachment(image_filename, "alice"))
        self.assertFalse(self.store.can_access_attachment("upload_group_test.pdf", "alice"))
        self.assertEqual(self.store.room_event_recipients(room_id), {"bob", "eve"})
        self.assertEqual(self.store.group_room_access("bob", room_id), "owner")

        direct_room, recipients, error = self.store.leave_group_room("bob", self.room_id)
        self.assertIsNone(direct_room)
        self.assertEqual(recipients, set())
        self.assertEqual(error, "not_found")
        self.assertIsNotNone(self.store.get_messages(self.room_id, "bob"))

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

    def test_client_message_id_is_idempotent(self) -> None:
        client_message_id = "message_retry_key_1234"
        first = self.store.add_message(
            self.room_id,
            "alice",
            "only once",
            client_message_id=client_message_id,
        )
        second = self.store.add_message(
            self.room_id,
            "alice",
            "only once",
            client_message_id=client_message_id,
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertTrue(first[2])
        self.assertFalse(second[2])
        self.assertEqual(first[0]["id"], second[0]["id"])
        self.assertEqual(len(self.store.get_messages(self.room_id, "alice") or []), 1)

        self.assertTrue(self.store.flush())
        state_path = self.store.path
        self.assertTrue(self.store.close())
        self.store = server.StateStore(state_path)
        after_restart = self.store.add_message(
            self.room_id,
            "alice",
            "only once",
            client_message_id=client_message_id,
        )
        self.assertIsNotNone(after_restart)
        assert after_restart is not None
        self.assertFalse(after_restart[2])

        with self.assertRaises(ValueError):
            self.store.add_message(
                self.room_id,
                "alice",
                "different content",
                client_message_id=client_message_id,
            )

    def test_message_pages_are_bounded_and_cursor_based(self) -> None:
        for index in range(45):
            self.store.add_message(self.room_id, "alice", f"message-{index}")

        latest = self.store.get_messages_page(self.room_id, "bob", limit=30)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(len(latest["items"]), 30)
        self.assertEqual(latest["items"][0]["text"], "message-15")
        self.assertTrue(latest["next_cursor"])

        older = self.store.get_messages_page(
            self.room_id,
            "bob",
            limit=30,
            before=latest["next_cursor"],
        )
        self.assertIsNotNone(older)
        assert older is not None
        self.assertEqual(len(older["items"]), 15)
        self.assertEqual(older["items"][0]["text"], "message-0")
        self.assertEqual(older["next_cursor"], "")

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

    def test_expired_upload_grant_cannot_be_consumed(self) -> None:
        grants = server.UploadGrantStore(ttl_seconds=-1)
        grants.create("upload_test.pdf", "alice")
        self.assertFalse(grants.consume("upload_test.pdf", "alice"))


def tearDownModule() -> None:
    server.STORE.close()
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
