from __future__ import annotations

import importlib.util
import io
import gzip
import http.client
import inspect
import json
import os
import queue
import re
import shutil
import socket
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlparse


TEST_DATA_DIR = tempfile.mkdtemp(prefix="colorless-tests-")
os.environ["DATA_DIR"] = TEST_DATA_DIR
os.environ["STATE_FILE"] = str(Path(TEST_DATA_DIR) / "global-state.json")
os.environ["UPLOADS_DIR"] = str(Path(TEST_DATA_DIR) / "uploads")
os.environ["STRUCTURED_LOGS_ENABLED"] = "false"

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
                "assets/js/platform/store.js",
                "assets/js/platform/http.js",
                "assets/js/platform/pipeline.js",
                "assets/js/platform/events.js",
                "assets/js/platform/icons.js",
                "assets/js/platform/image-processing.js",
                "assets/js/core.js",
                "assets/js/profile.js",
                "assets/js/messenger.js",
                "assets/js/attachments.js",
                "assets/js/room-settings.js",
                "assets/js/chat.js",
                "assets/js/shorts.js",
                "assets/js/action-bar.js",
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

    def test_production_font_artifact_uses_one_preloaded_woff2(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        font_files = [path for path in (server.ASSETS_DIR / "fonts").iterdir() if path.is_file()]

        self.assertEqual([path.suffix.lower() for path in font_files], [".woff2"])
        self.assertLessEqual(sum(path.stat().st_size for path in font_files), 250 * 1024)
        self.assertEqual(index_html.count("@font-face"), 1)
        self.assertEqual(index_html.count('rel="preload"'), 1)
        self.assertIn('type="font/woff2"', index_html)
        self.assertIn("font-display: swap", index_html)
        self.assertNotIn(".ttf", index_html.lower())

    def test_html_and_fingerprinted_assets_have_separate_cache_and_encoding_policy(self) -> None:
        self.assertIsNotNone(server.brotli)
        core_path = (server.ASSETS_DIR / "js" / "core.js").resolve()
        core_hash = server.ASSET_FINGERPRINTS[core_path]
        font_path = next((server.ASSETS_DIR / "fonts").glob("*.woff2")).resolve()
        font_hash = server.ASSET_FINGERPRINTS[font_path]
        http_server = server.ChatServer(("127.0.0.1", 0), server.ChatHandler)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", http_server.server_address[1], timeout=5)
        try:
            connection.request("GET", "/", headers={"Accept-Encoding": "br, gzip"})
            index_response = connection.getresponse()
            index_body = index_response.read()
            index_etag = index_response.getheader("ETag")
            self.assertEqual(index_response.status, 200)
            self.assertEqual(index_response.getheader("Content-Encoding"), "br")
            self.assertEqual(index_response.getheader("Cache-Control"), "no-cache, max-age=0")
            self.assertEqual(server.brotli.decompress(index_body), server.INDEX_CONTENT)

            connection.request("GET", "/", headers={"If-None-Match": index_etag})
            not_modified = connection.getresponse()
            self.assertEqual(not_modified.status, 304)
            self.assertEqual(not_modified.read(), b"")

            connection.request("GET", f"/assets/js/core.js?v={core_hash}", headers={"Accept-Encoding": "br, gzip"})
            immutable_asset = connection.getresponse()
            immutable_body = immutable_asset.read()
            self.assertEqual(immutable_asset.status, 200)
            self.assertEqual(immutable_asset.getheader("Content-Encoding"), "br")
            self.assertEqual(immutable_asset.getheader("Cache-Control"), "public, max-age=31536000, immutable")
            self.assertIn("javascript", immutable_asset.getheader("Content-Type", ""))
            self.assertEqual(server.brotli.decompress(immutable_body), core_path.read_bytes())

            connection.request("GET", "/assets/js/core.js?v=stale-version", headers={"Accept-Encoding": "gzip"})
            stale_asset = connection.getresponse()
            stale_body = stale_asset.read()
            self.assertEqual(stale_asset.getheader("Content-Encoding"), "gzip")
            self.assertEqual(stale_asset.getheader("Cache-Control"), "public, no-cache, max-age=0")
            self.assertEqual(gzip.decompress(stale_body), core_path.read_bytes())

            font_url = "/assets/fonts/" + font_path.name.replace(" ", "%20") + f"?v={font_hash}"
            connection.request("GET", font_url, headers={"Accept-Encoding": "br, gzip"})
            font_response = connection.getresponse()
            font_body = font_response.read()
            self.assertEqual(font_response.status, 200)
            self.assertEqual(font_response.getheader("Content-Type"), "font/woff2")
            self.assertIsNone(font_response.getheader("Content-Encoding"))
            self.assertEqual(font_response.getheader("Cache-Control"), "public, max-age=31536000, immutable")
            self.assertEqual(font_body, font_path.read_bytes())
        finally:
            connection.close()
            http_server.shutdown()
            http_server.server_close()
            server_thread.join(timeout=5)

    def test_new_chat_uses_one_shared_member_selector(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        app_script = (server.ASSETS_DIR / "js" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="open-new-chat-button"', index_html)
        self.assertIn('id="new-chat-sheet"', index_html)
        self.assertIn('id="new-chat-search"', index_html)
        self.assertIn('id="new-chat-member-list"', index_html)
        self.assertIn('id="new-chat-group-name-field"', index_html)
        self.assertIn("friend.friend_code", app_script)
        self.assertIn("updateNewChatMemberSelection", app_script)
        self.assertIn("검색 결과가 없어요.", app_script)
        self.assertNotIn('id="group-room-name"', index_html)
        self.assertNotIn('id="group-member-list"', index_html)

    def test_attachment_picker_and_paste_have_desktop_and_room_contracts(self) -> None:
        attachment_script = (server.ASSETS_DIR / "js" / "attachments.js").read_text(encoding="utf-8")
        bootstrap_script = (server.ASSETS_DIR / "js" / "bootstrap.js").read_text(encoding="utf-8")

        self.assertIn('window.matchMedia?.("(pointer: fine)").matches', attachment_script)
        self.assertIn('openAttachmentPicker(kind = "all")', attachment_script)
        self.assertIn('"image/*,application/pdf"', attachment_script)
        self.assertIn('chatRoom.addEventListener("paste", handlePastedChatAttachment)', bootstrap_script)
        self.assertNotIn('chatMessageInput.addEventListener("paste"', bootstrap_script)

    def test_message_non_readers_are_shown_only_during_left_swipe(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        chat_script = (server.ASSETS_DIR / "js" / "chat.js").read_text(encoding="utf-8")
        messenger_script = (server.ASSETS_DIR / "js" / "messenger.js").read_text(encoding="utf-8")
        bootstrap_script = (server.ASSETS_DIR / "js" / "bootstrap.js").read_text(encoding="utf-8")

        self.assertIn('id="message-read-menu"', index_html)
        self.assertIn("function beginMessageReadSwipe(event)", chat_script)
        self.assertIn("function updateMessageReadSwipe(event)", chat_script)
        self.assertIn("function finishMessageReadSwipe(event)", chat_script)
        self.assertIn("const unreadNames = (message.unread_by || [])", chat_script)
        self.assertIn("`안 읽은 사람 ${unreadNames.length}명`", chat_script)
        self.assertIn('applyMessageReaderToCurrentMessages(payload.username, "realtime.room-read")', messenger_script)
        self.assertIn("function applyMessageReaderToCurrentMessages", chat_script)
        self.assertIn('chatMessageList.addEventListener("pointerdown", beginMessageReadSwipe)', bootstrap_script)
        self.assertIn('chatMessageList.addEventListener("pointermove", updateMessageReadSwipe)', bootstrap_script)
        self.assertIn('chatMessageList.addEventListener("pointerup", finishMessageReadSwipe)', bootstrap_script)
        self.assertNotIn("showMessageReadMenuFromContext", chat_script)
        self.assertNotRegex(
            messenger_script,
            r'if \(currentRoom\(\)\?\.kind === "group"\) \{\s+void loadChatMessages',
        )

    def test_signup_is_a_separate_responsive_document(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        signup_html = server.SIGNUP_FILE.read_text(encoding="utf-8")
        signup_css = (server.ASSETS_DIR / "css" / "signup.css").read_text(encoding="utf-8")

        self.assertIn('href="/signup"', index_html)
        self.assertNotIn('id="signup-form"', index_html)
        self.assertIn('id="signup-form"', signup_html)
        self.assertRegex(signup_html, r'src="/assets/js/signup\.js\?v=[0-9a-f]{12}"')
        self.assertIn('@media (min-width: 768px)', signup_css)
        self.assertIn('@media (max-width: 767px)', signup_css)
        self.assertIn('@media (min-width: 768px)', index_html)
        self.assertIn('@media (max-width: 767px)', index_html)

    def test_responsive_messenger_uses_desktop_split_and_mobile_single_pane(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        signup_css = (server.ASSETS_DIR / "css" / "signup.css").read_text(encoding="utf-8")

        desktop = re.search(r'@media \(min-width: 768px\) \{(.*?)\n\s*\}\n\s*@media \(max-width: 767px\)', index_html, re.DOTALL)
        self.assertIsNotNone(desktop)
        self.assertIn('"header chat"', desktop.group(1))
        self.assertIn('grid-template-columns: minmax(340px, 430px) minmax(0, 1fr)', desktop.group(1))
        self.assertIn('grid-area: chat', desktop.group(1))
        self.assertIn('position: relative', desktop.group(1))
        self.assertIn('class="desktop-chat-empty"', index_html)

        mobile = re.search(r'@media \(max-width: 767px\) \{(.*?)\n\s*\}\n\s*</style>', index_html, re.DOTALL)
        self.assertIsNotNone(mobile)
        self.assertIn('height: 100dvh', mobile.group(1))
        self.assertIn('width: 100%', mobile.group(1))
        self.assertIn('max-width: none', mobile.group(1))
        self.assertIn('height: 100dvh', signup_css)

    def test_render_requires_supabase_persistence(self) -> None:
        render_config = (SERVER_PATH.parents[2] / "render.yaml").read_text(encoding="utf-8")

        self.assertIn("key: REQUIRE_SUPABASE", render_config)
        self.assertIn("key: SUPABASE_URL", render_config)
        self.assertIn("key: SUPABASE_SERVICE_ROLE_KEY", render_config)

    def test_normalized_storage_schema_has_row_constraints_and_indexes(self) -> None:
        schema = (server.BASE_DIR / "supabase-schema.sql").read_text(encoding="utf-8")
        persistence = (server.BASE_DIR / "persistence.py").read_text(encoding="utf-8")

        for table in (
            "app_migrations", "users", "profile_art", "social_accounts", "friendships", "rooms", "room_members",
            "messages", "read_positions", "sessions", "shorts_feeds", "shorts_seen",
            "shorts_catalog", "shorts_collection_state", "realtime_events", "presence_leases",
        ):
            self.assertIn(f"create table if not exists public.{table}", schema.lower())
        for function in (
            "colorless_sync_user", "colorless_sync_room", "colorless_insert_message",
            "colorless_create_session", "colorless_session_username",
            "colorless_save_shorts_feed", "colorless_storage_counts",
            "colorless_acquire_shorts_collection", "colorless_finish_shorts_collection",
            "colorless_publish_event", "colorless_presence_for_user",
            "colorless_latest_messages", "colorless_presence_for_users",
            "colorless_touch_presence", "colorless_disconnect_presence", "colorless_cleanup_presence",
        ):
            self.assertIn(f"function public.{function}", schema.lower())
        self.assertIn("messages_room_sequence_idx", schema)
        self.assertIn("messages_client_id_unique_idx", schema)
        self.assertIn("room_members_user_room_idx", schema)
        self.assertIn("rooms_direct_key_unique_idx", schema)
        self.assertIn("realtime_events_recipients_idx", schema)
        self.assertIn("from public, anon, authenticated", schema.lower())
        self.assertIn("to service_role", schema.lower())
        self.assertIn("::timestamptz", schema.lower())
        self.assertIn("updated_at double precision not null default extract(epoch from now())", schema.lower())
        self.assertIn("alter column updated_at type double precision", schema.lower())
        self.assertNotIn("current_time timestamptz", schema.lower())
        self.assertGreaterEqual(schema.lower().count("now_value timestamptz"), 2)
        self.assertIn("BEGIN IMMEDIATE", persistence)
        self.assertIn("PRAGMA foreign_keys=ON", persistence)
        self.assertIn("ORDER BY rowid DESC", persistence)

    def test_realtime_client_persists_event_cursor_and_deduplicates_event_ids(self) -> None:
        events_script = (server.ASSETS_DIR / "js" / "platform" / "events.js").read_text(encoding="utf-8")

        self.assertIn("colorless-realtime-cursor", events_script)
        self.assertIn("message.lastEventId", events_script)
        self.assertIn("seenEventIds.has", events_script)

    def test_messenger_uses_paged_bootstrap_and_revision_sync(self) -> None:
        app_script = (server.ASSETS_DIR / "js" / "app.js").read_text(encoding="utf-8")
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        chat_script = (server.ASSETS_DIR / "js" / "chat.js").read_text(encoding="utf-8")
        bootstrap_script = (server.ASSETS_DIR / "js" / "bootstrap.js").read_text(encoding="utf-8")

        self.assertNotIn('requestAction("messenger.load", "/messenger"', app_script)
        self.assertIn('requestAction("messenger.me", "/me"', app_script)
        self.assertIn('"/friends?limit=30"', app_script)
        self.assertIn('"/rooms?limit=30"', app_script)
        self.assertIn('/sync?after_revision=${encodeURIComponent(state.syncRevision)}', app_script)
        self.assertIn('/rooms/${encodeURIComponent(roomId)}/members?limit=50', chat_script)
        self.assertIn('loadRoomsPage({ render: true })', bootstrap_script)
        self.assertIn('loadFriendsPage({ render: true })', bootstrap_script)

    def test_auth_client_omits_logout_body_and_reports_http_status(self) -> None:
        auth_script = (server.ASSETS_DIR / "js" / "auth.js").read_text(encoding="utf-8")
        http_script = (server.ASSETS_DIR / "js" / "platform" / "http.js").read_text(encoding="utf-8")

        self.assertIn('requestAction("auth.logout", "/logout", { method: "POST" })', auth_script)
        self.assertNotIn('requestAction("auth.logout", "/logout", { method: "POST", body:', auth_script)
        self.assertIn('${method} ${url} 요청 실패 (HTTP ${statusLabel})', http_script)

    def test_stale_session_check_cannot_overwrite_a_completed_login(self) -> None:
        core_script = (server.ASSETS_DIR / "js" / "core.js").read_text(encoding="utf-8")
        bootstrap_script = (server.ASSETS_DIR / "js" / "bootstrap.js").read_text(encoding="utf-8")

        self.assertIn("authEpoch: 0", core_script)
        self.assertIn("function advanceAuthEpoch()", core_script)
        self.assertIn("advanceAuthEpoch();\n  state.session = session;", core_script)
        self.assertIn("advanceAuthEpoch();\n  setAuthRequestBusy(true, message);", core_script)
        self.assertIn("const authEpoch = state.authEpoch;", bootstrap_script)
        self.assertEqual(bootstrap_script.count("if (authEpoch !== state.authEpoch) return;"), 2)

    def test_attachments_use_signed_grants_and_bounded_streaming(self) -> None:
        attachment_script = (server.ASSETS_DIR / "js" / "attachments.js").read_text(encoding="utf-8")
        server_script = SERVER_PATH.read_text(encoding="utf-8")

        self.assertIn('"/uploads/grant"', attachment_script)
        self.assertIn('"attachments.transfer"', attachment_script)
        self.assertIn('"/uploads/complete"', attachment_script)
        self.assertNotIn('requestAction("attachments.upload", "/uploads"', attachment_script)
        self.assertIn("def supabase_signed_upload_url", server_script)
        self.assertIn("def supabase_signed_download_url", server_script)
        self.assertIn("def stream_request_body_to_file", server_script)
        self.assertIn('self.send_header("Accept-Ranges", "bytes")', server_script)

    def test_shorts_players_wait_for_readiness_and_preload_adjacent_cards(self) -> None:
        shorts_script = (server.ASSETS_DIR / "js" / "shorts.js").read_text(encoding="utf-8")
        bootstrap_script = (server.ASSETS_DIR / "js" / "bootstrap.js").read_text(encoding="utf-8")
        server_script = SERVER_PATH.read_text(encoding="utf-8")

        self.assertIn("?autoplay=0&mute=1&playsinline=1", shorts_script)
        self.assertIn('payload?.event !== "onReady"', shorts_script)
        self.assertIn("if (distance > 1)", shorts_script)
        self.assertIn('window.addEventListener("message", handleShortPlayerMessage)', bootstrap_script)
        self.assertIn("payload.cycled", shorts_script)
        self.assertIn('"cycled": cycled', server_script)

    def test_desktop_headers_share_one_height_and_message_times_cluster_for_five_minutes(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        chat_script = (server.ASSETS_DIR / "js" / "chat.js").read_text(encoding="utf-8")

        self.assertIn("--desktop-header-height: 88px", index_html)
        self.assertIn("--chat-header-height: var(--desktop-header-height)", index_html)
        self.assertIn("const MESSAGE_TIME_CLUSTER_MS = 5 * 60 * 1000", chat_script)
        self.assertIn("nextMessage.username !== message.username", chat_script)
        self.assertIn("nextTimestamp - timestamp > MESSAGE_TIME_CLUSTER_MS", chat_script)
        self.assertIn('sender.textContent = messageSenderDisplayName(room, message)', chat_script)
        self.assertIn("if (!mine) {", chat_script)
        self.assertIn('row.querySelector(".message-sender")?.classList.toggle', chat_script)
        self.assertIn("syncMessageTimeVisibility(state.messages.length - 2)", chat_script)
        self.assertIn("function shouldShowMessageReadReceipt(message, messageIndex)", chat_script)
        self.assertIn("const nextIndex = nextOwnMessageIndex(messageIndex)", chat_script)
        self.assertIn('`${readerCount}명 읽음`', chat_script)

    def test_supabase_requests_use_persistent_connection_pools(self) -> None:
        server_script = SERVER_PATH.read_text(encoding="utf-8")
        persistence_script = (server.BASE_DIR / "persistence.py").read_text(encoding="utf-8")

        self.assertIn("OUTBOUND_HTTP_CLIENT = httpx.Client", server_script)
        self.assertIn("SUPABASE_HTTP_CLIENT = httpx.Client", persistence_script)
        self.assertNotIn("urlopen(request", persistence_script)

    def test_shared_pipeline_modules_define_one_feature_contract(self) -> None:
        platform_dir = server.ASSETS_DIR / "js" / "platform"
        store_script = (platform_dir / "store.js").read_text(encoding="utf-8")
        pipeline_script = (platform_dir / "pipeline.js").read_text(encoding="utf-8")
        events_script = (platform_dir / "events.js").read_text(encoding="utf-8")
        core_script = (server.ASSETS_DIR / "js" / "core.js").read_text(encoding="utf-8")

        self.assertIn("platform.createStore", store_script)
        self.assertIn("platform.createActionPipeline", pipeline_script)
        self.assertIn("platform.createEventRouter", events_script)
        self.assertIn("platform.createRealtimeClient", events_script)
        self.assertIn("function requestAction", core_script)

    def test_primary_actions_use_accessible_svg_icons(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        icons_script = (server.ASSETS_DIR / "js" / "platform" / "icons.js").read_text(encoding="utf-8")
        shorts_script = (server.ASSETS_DIR / "js" / "shorts.js").read_text(encoding="utf-8")

        self.assertIn("function decorateIconButton", icons_script)
        self.assertIn('classList.add("ui-icon")', icons_script)
        for button_id in (
            "open-list-search-button",
            "open-profile-button",
            "open-new-chat-button",
            "open-directory-button",
            "logout-button",
            "close-chat-room",
            "open-room-settings-button",
            "chat-attachment-button",
            "short-share-send",
        ):
            button = re.search(rf'<button(?=[^>]*id="{button_id}")[^>]*>', index_html)
            self.assertIsNotNone(button, button_id)
            self.assertIn("data-icon=", button.group(0))
            self.assertIn("aria-label=", button.group(0))
        self.assertNotIn('shortShareSend.textContent = ">"', shorts_script)

    def test_status_emoji_picker_is_mandatory_once_and_accepts_only_one_emoji(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        core_script = (server.ASSETS_DIR / "js" / "core.js").read_text(encoding="utf-8")
        app_script = (server.ASSETS_DIR / "js" / "app.js").read_text(encoding="utf-8")
        bootstrap_script = (server.ASSETS_DIR / "js" / "bootstrap.js").read_text(encoding="utf-8")
        chat_script = (server.ASSETS_DIR / "js" / "chat.js").read_text(encoding="utf-8")
        messenger_script = (server.ASSETS_DIR / "js" / "messenger.js").read_text(encoding="utf-8")
        server_script = SERVER_PATH.read_text(encoding="utf-8")

        start_app = re.search(r"async function startApp\(\) \{(.*?)\n\}", app_script, re.DOTALL)
        self.assertIsNotNone(start_app)
        self.assertIn("if (!state.statusPromptShown && !savedStatusEmoji())", start_app.group(1))
        self.assertIn("openStatusEmojiPicker(null)", start_app.group(1))
        self.assertIn('id="open-status-emoji-button"', index_html)
        self.assertIn('aria-labelledby="status-emoji-title"', index_html)
        self.assertIn('aria-describedby="status-emoji-description"', index_html)
        self.assertNotIn('id="close-status-emoji-button"', index_html)
        self.assertNotIn('id="skip-status-emoji-button"', index_html)
        self.assertIn('id="status-emoji-required"', index_html)
        self.assertIn('placeholder="이모티콘 1개"', index_html)
        self.assertIn("state.selectedStatusEmoji = savedStatusEmoji()", core_script)
        self.assertIn("openStatusEmojiButton.disabled = false", core_script)
        self.assertIn('openStatusEmojiButton.addEventListener("click"', bootstrap_script)
        self.assertIn("if (segments.length !== 1) return", core_script)
        self.assertIn("statusEmojiPicker.appendChild(profileStatusEmoji)", core_script)
        self.assertIn("function openCustomStatusEmojiInput()", core_script)
        self.assertIn("statusEmojiOnly: true", core_script)
        self.assertIn("openCustomStatusEmojiInput()", bootstrap_script)
        self.assertIn("saved_activity_emoji(status_message) != status_message.strip()", server_script)
        self.assertIn("state.statusPickerOpener", core_script)
        self.assertIn('event.key === "Escape"', bootstrap_script)
        self.assertIn('event.key !== "Tab"', bootstrap_script)
        self.assertNotIn("closeStatusEmojiPicker()", bootstrap_script)
        self.assertIn("normalizeStatusEmoji(presence.emoji)", core_script)
        self.assertIn("body: JSON.stringify({ activeRoomId: document.hidden ? \"\" : state.selectedRoomId, emoji })", chat_script)
        self.assertIn('realtimeEvents.register("presence_updated"', messenger_script)
        self.assertIn("schedulePresencePatch(payload.username)", messenger_script)

        self.assertEqual(server.saved_activity_emoji("😀"), "😀")
        self.assertEqual(server.saved_activity_emoji("👨‍👩‍👧‍👦"), "👨‍👩‍👧‍👦")
        self.assertEqual(server.saved_activity_emoji("🇰🇷"), "🇰🇷")
        self.assertEqual(server.saved_activity_emoji("hello 😀"), "")
        self.assertEqual(server.saved_activity_emoji("😀😃"), "")

    def test_mobile_header_keeps_title_on_one_line(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")

        self.assertRegex(index_html, r"\.app-header\s*>\s*div:first-child\s*\{[^}]*min-width:\s*0")
        title_rule = re.search(r"\.app-header h1\s*\{([^}]*)\}", index_html)
        self.assertIsNotNone(title_rule)
        self.assertIn("white-space: nowrap", title_rule.group(1))
        self.assertIn("text-overflow: ellipsis", title_rule.group(1))
        action_rule = re.search(r"\.app-actions button\s*\{([^}]*)\}", index_html)
        self.assertIsNotNone(action_rule)
        self.assertIn("min-height: 44px", action_rule.group(1))
        self.assertIn("min-width: 44px", action_rule.group(1))

    def test_context_action_bar_keeps_tab_state_and_room_creation_explicit(self) -> None:
        core_script = (server.ASSETS_DIR / "js" / "core.js").read_text(encoding="utf-8")
        action_bar_script = (server.ASSETS_DIR / "js" / "action-bar.js").read_text(encoding="utf-8")
        messenger_script = (server.ASSETS_DIR / "js" / "messenger.js").read_text(encoding="utf-8")
        shorts_script = (server.ASSETS_DIR / "js" / "shorts.js").read_text(encoding="utf-8")

        self.assertIn("actionBarByTab", core_script)
        self.assertIn("function renderChatActionBar", action_bar_script)
        self.assertIn("function renderFriendActionBar", action_bar_script)
        self.assertIn("function handleContextActionPrimary", action_bar_script)
        self.assertIn('item.addEventListener("click", () => selectFriendForActionBar(friend.id))', messenger_script)
        self.assertNotIn('item.addEventListener("click", () => openDirectChat(friend.id))', messenger_script)
        self.assertIn("selectedShareRoomIds", shorts_script)
        self.assertIn("Promise.all(rooms.map", shorts_script)
        self.assertIn('shortShareBar.setAttribute("aria-label", "새 메시지 빠른 답장")', shorts_script)

    def test_my_tab_owns_profile_status_and_logout_while_lists_own_search(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        app_script = (server.ASSETS_DIR / "js" / "app.js").read_text(encoding="utf-8")
        action_bar_script = (server.ASSETS_DIR / "js" / "action-bar.js").read_text(encoding="utf-8")
        messenger_script = (server.ASSETS_DIR / "js" / "messenger.js").read_text(encoding="utf-8")

        self.assertIn('id="my-tab"', index_html)
        self.assertIn('id="my-view"', index_html)
        my_view = re.search(r'<section class="my-view.*?</section>', index_html, re.DOTALL)
        self.assertIsNotNone(my_view)
        self.assertIn('id="open-status-emoji-button"', my_view.group(0))
        self.assertIn('id="open-profile-button"', my_view.group(0))
        self.assertIn('id="logout-button"', my_view.group(0))
        header = re.search(r'<header class="app-header">.*?</header>', index_html, re.DOTALL)
        self.assertIsNotNone(header)
        self.assertNotIn('id="logout-button"', header.group(0))
        self.assertIn('openDirectoryButton.classList.toggle("hidden", state.activeList !== "friends")', app_script)
        self.assertIn('id="open-list-search-button"', header.group(0))
        self.assertLess(header.group(0).index('id="open-list-search-button"'), header.group(0).index('id="open-new-chat-button"'))
        self.assertLess(header.group(0).index('id="open-list-search-button"'), header.group(0).index('id="open-directory-button"'))
        self.assertIn('? "사람 또는 주고받은 대화 검색"', action_bar_script)
        self.assertIn(': "친구 이름 또는 ID 검색"', action_bar_script)
        self.assertIn("scheduleChatSearch(headerSearchInput.value)", action_bar_script)
        self.assertIn("function renderChatSearchResults", messenger_script)
        self.assertIn("openChatRoomAtMessage", messenger_script)

    def test_desktop_sheets_stay_in_the_left_content_pane_and_actions_are_not_duplicated(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        action_bar_script = (server.ASSETS_DIR / "js" / "action-bar.js").read_text(encoding="utf-8")

        self.assertIn(".app > .sheet-backdrop", index_html)
        self.assertIn("grid-column: 1 / 2", index_html)
        self.assertIn("grid-row: 2 / 5", index_html)
        self.assertNotIn('createContextAction("새 채팅", "message-plus", openNewChat)', action_bar_script)
        self.assertNotIn('createContextAction("친구 추가", "user-plus", openDirectory)', action_bar_script)
        self.assertNotIn('createContextAction("검색", "search"', action_bar_script)
        self.assertIn('id="header-search-input"', index_html)

    def test_two_hundred_shorts_use_a_fixed_virtual_dom_window(self) -> None:
        core_script = (server.ASSETS_DIR / "js" / "core.js").read_text(encoding="utf-8")
        shorts_script = (server.ASSETS_DIR / "js" / "shorts.js").read_text(encoding="utf-8")
        bootstrap_script = (server.ASSETS_DIR / "js" / "bootstrap.js").read_text(encoding="utf-8")
        app_script = (server.ASSETS_DIR / "js" / "app.js").read_text(encoding="utf-8")
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")

        window_size = int(re.search(r"SHORTS_DOM_WINDOW_SIZE = (\d+)", core_script).group(1))
        self.assertLessEqual(window_size, 5)
        for active_index in range(200):
            start = min(max(0, active_index - window_size // 2), max(0, 200 - window_size))
            self.assertLessEqual(len(range(start, start + window_size)), 5)
        self.assertIn("function shortVirtualRange", shorts_script)
        self.assertIn("Math.round(shortsView.scrollTop / height)", shorts_script)
        self.assertNotIn("getBoundingClientRect", shorts_script)
        self.assertIn('card.style.position = "absolute"', shorts_script)
        self.assertIn("const preservedScrollTop = shortsView.scrollTop", shorts_script)
        self.assertIn("function scheduleShortScrollSnap", shorts_script)
        self.assertIn("frame.tabIndex = -1", shorts_script)
        self.assertIn("overflow-anchor: none", index_html)
        self.assertIn("const distance = Math.abs(cardIndex - state.youtube.activeIndex)", shorts_script)
        self.assertIn("if (distance > 1)", shorts_script)
        self.assertIn("releaseAllShortFrames", shorts_script)
        self.assertIn('document.addEventListener("visibilitychange", handleShortVisibilityChange)', bootstrap_script)
        self.assertIn('state.activeList === "shorts" && listName !== "shorts"', app_script)
        create_card = re.search(r"function createShortCard\(.*?\n\}", shorts_script, re.DOTALL)
        self.assertIsNotNone(create_card)
        self.assertNotIn("createShortFrame", create_card.group(0))

    def test_large_images_use_a_cancellable_worker_pipeline(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        worker_script = (server.ASSETS_DIR / "js" / "image-worker.js").read_text(encoding="utf-8")
        manager_script = (server.ASSETS_DIR / "js" / "platform" / "image-processing.js").read_text(encoding="utf-8")
        attachments_script = (server.ASSETS_DIR / "js" / "attachments.js").read_text(encoding="utf-8")
        profile_script = (server.ASSETS_DIR / "js" / "profile.js").read_text(encoding="utf-8")
        room_script = (server.ASSETS_DIR / "js" / "room-settings.js").read_text(encoding="utf-8")
        benchmark_html = (server.ASSETS_DIR / "image-worker-benchmark.html").read_text(encoding="utf-8")
        benchmark_script = (server.ASSETS_DIR / "image-worker-benchmark.js").read_text(encoding="utf-8")

        self.assertLess(index_html.index("platform/image-processing.js"), index_html.index("assets/js/core.js"))
        self.assertIn("new Worker(workerUrl)", manager_script)
        self.assertIn("job.worker.terminate()", manager_script)
        self.assertIn('new DOMException("Image processing was canceled", "AbortError")', manager_script)
        self.assertIn("OffscreenCanvas", worker_script)
        self.assertIn("createImageBitmap(file, bitmapOptions)", worker_script)
        self.assertIn("readImageMetadata(file)", worker_script)
        self.assertIn("imageOrientation: \"from-image\"", worker_script)
        self.assertIn("maxPixels", worker_script)
        self.assertIn('ColorlessImageProcessing.cancel("chat-attachment")', attachments_script)
        self.assertIn('ColorlessImageProcessing.cancel("profile-image")', profile_script)
        self.assertIn('ColorlessImageProcessing.cancel("room-image")', room_script)
        self.assertIn("IMAGE_FALLBACK_TOTAL_PIXELS_MAX", attachments_script)
        self.assertIn("4000×3000", benchmark_html)
        self.assertIn("longTasksOver100Ms", benchmark_script)
        self.assertIn('cancellationOutcome === "AbortError"', benchmark_script)
        self.assertIn('bombOutcome === "image-dimensions-too-large"', benchmark_script)

    def test_attachment_dimension_probe_rejects_pixel_bombs(self) -> None:
        def png(width: int, height: int) -> bytes:
            return b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")

        self.assertEqual(server.attachment_image_dimensions("image/png", png(4000, 3000)), (4000, 3000))
        self.assertTrue(server.safe_attachment_image_dimensions("image/png", png(4000, 3000)))
        self.assertFalse(server.safe_attachment_image_dimensions("image/png", png(20000, 20000)))
        jpeg = b"\xff\xd8\xff\xc0\x00\x07\x08" + (3000).to_bytes(2, "big") + (4000).to_bytes(2, "big")
        self.assertEqual(server.attachment_image_dimensions("image/jpeg", jpeg), (4000, 3000))
        webp = b"RIFF" + (22).to_bytes(4, "little") + b"WEBPVP8X" + (10).to_bytes(4, "little") + b"\x00\x00\x00\x00" + (3999).to_bytes(3, "little") + (2999).to_bytes(3, "little")
        self.assertEqual(server.attachment_image_dimensions("image/webp", webp), (4000, 3000))
        self.assertEqual(server.ATTACHMENT_IMAGE_PROBE_BYTES, 512 * 1024)

    def test_profile_pixel_editor_is_one_keyboard_accessible_canvas(self) -> None:
        index_html = server.INDEX_FILE.read_text(encoding="utf-8")
        profile_script = (server.ASSETS_DIR / "js" / "profile.js").read_text(encoding="utf-8")

        canvas = re.search(r'<canvas(?=[^>]*id="pixel-editor-grid")[^>]*>', index_html)
        self.assertIsNotNone(canvas)
        self.assertIn('tabindex="0"', canvas.group(0))
        self.assertIn('aria-describedby="profile-pixel-status"', canvas.group(0))
        self.assertIn('pixelEditorGrid.dataset.initialized === "true"', profile_script)
        self.assertIn('event.key === "ArrowRight"', profile_script)
        self.assertIn('event.key === " " || event.key === "Enter"', profile_script)
        self.assertIn('event.key === "Delete" || event.key === "Backspace"', profile_script)
        editor_builder = re.search(r"function buildProfileEditor\(\)\s*\{([^}]*)\}", profile_script)
        self.assertIsNotNone(editor_builder)
        self.assertNotIn("createElement", editor_builder.group(1))
        self.assertNotIn('className = "pixel-cell"', profile_script)

    def test_profile_pixel_original_is_loaded_only_when_editor_opens(self) -> None:
        profile_script = (server.ASSETS_DIR / "js" / "profile.js").read_text(encoding="utf-8")
        app_script = (server.ASSETS_DIR / "js" / "app.js").read_text(encoding="utf-8")
        bootstrap_script = (server.ASSETS_DIR / "js" / "bootstrap.js").read_text(encoding="utf-8")

        editor = re.search(r"async function openProfileEditor\(\) \{(.*?)\n\}", profile_script, re.DOTALL)
        self.assertIsNotNone(editor)
        self.assertIn('"/profile/pixels"', editor.group(1))
        self.assertIn("profile.load-pixels", editor.group(1))
        self.assertNotIn('"/profile/pixels"', app_script)
        self.assertIn("() => void openProfileEditor()", bootstrap_script)
        self.assertNotIn("pixels.join", (server.ASSETS_DIR / "js" / "core.js").read_text(encoding="utf-8"))

    def test_presence_events_patch_indexed_rows_on_one_animation_frame(self) -> None:
        messenger_script = (server.ASSETS_DIR / "js" / "messenger.js").read_text(encoding="utf-8")

        self.assertIn("function rebuildPresenceIndexes", messenger_script)
        self.assertIn("state.friendByUsername.get(payload.username)", messenger_script)
        self.assertIn("state.roomIdsByPeerUsername.get(payload.username)", messenger_script)
        self.assertIn("requestAnimationFrame(flushPresencePatches)", messenger_script)
        self.assertIn("state.friendNodes.get(username)", messenger_script)
        self.assertIn("state.roomNodes.get(room.id)", messenger_script)
        presence_handler = re.search(
            r'realtimeEvents\.register\("presence_updated",\s*\(payload\)\s*=>\s*\{(.*?)\n\s*\}\);',
            messenger_script,
            re.DOTALL,
        )
        self.assertIsNotNone(presence_handler)
        self.assertNotIn("renderMessenger", presence_handler.group(1))
        self.assertNotIn("replaceChildren", presence_handler.group(1))
        self.assertIn("schedulePresencePatch", presence_handler.group(1))
        feature_scripts = [
            path.read_text(encoding="utf-8")
            for path in (server.ASSETS_DIR / "js").glob("*.js")
            if path.name != "signup.js"
        ]
        self.assertTrue(all("fetch(" not in script for script in feature_scripts))
        self.assertTrue(all("new EventSource(" not in script for script in feature_scripts))


class AuthenticationHttpIntegrationTestCase(unittest.TestCase):
    def assert_oauth_state_is_browser_bound(self, provider: str) -> None:
        http_server = server.ChatServer(("127.0.0.1", 0), server.ChatHandler)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        browser_a = http.client.HTTPConnection("127.0.0.1", http_server.server_address[1], timeout=5)
        browser_b = http.client.HTTPConnection("127.0.0.1", http_server.server_address[1], timeout=5)

        def request(connection: http.client.HTTPConnection, path: str, cookie: str = "") -> tuple[int, str, list[tuple[str, str]]]:
            connection.request("GET", path, headers={"Cookie": cookie} if cookie else {})
            response = connection.getresponse()
            response.read()
            return response.status, response.getheader("Location", ""), response.getheaders()

        patches = [
            mock.patch.object(server, "PUBLIC_BASE_URL", f"http://127.0.0.1:{http_server.server_address[1]}"),
        ]
        if provider == "google":
            patches.extend([
                mock.patch.object(server, "GOOGLE_CLIENT_ID", "google-client"),
                mock.patch.object(server, "GOOGLE_CLIENT_SECRET", "google-secret"),
                mock.patch.object(server.ChatHandler, "request_google_token", return_value={"access_token": "mock-token"}),
                mock.patch.object(server.ChatHandler, "request_google_user_profile", return_value={"sub": "google-browser-binding", "name": "Google Mock"}),
            ])
        else:
            patches.extend([
                mock.patch.object(server, "KAKAO_REST_API_KEY", "kakao-key"),
                mock.patch.object(server.ChatHandler, "request_kakao_token", return_value={"access_token": "mock-token"}),
                mock.patch.object(server.ChatHandler, "request_kakao_user_profile", return_value={"id": "kakao-browser-binding", "kakao_account": {"profile": {"nickname": "Kakao Mock"}}}),
            ])

        try:
            for patcher in patches:
                patcher.start()

            status, location, headers = request(browser_a, f"/auth/{provider}/start")
            self.assertEqual(status, 302)
            state = parse_qs(urlparse(location).query)["state"][0]
            state_cookie = next(value.split(";", 1)[0] for key, value in headers if key.lower() == "set-cookie")
            self.assertTrue(state_cookie.startswith(f"{server.OAUTH_STATE_COOKIE_NAME}="))

            status, location, headers = request(
                browser_b,
                f"/auth/{provider}/callback?{urlencode({'code': 'cross-browser', 'state': state})}",
            )
            self.assertEqual(status, 302)
            self.assertIn("auth_error=oauth_state_invalid", location)
            self.assertTrue(any("Max-Age=0" in value for key, value in headers if key.lower() == "set-cookie"))

            status, location, headers = request(
                browser_a,
                f"/auth/{provider}/callback?{urlencode({'code': 'same-browser', 'state': state})}",
                state_cookie,
            )
            self.assertEqual(status, 302)
            self.assertEqual(location, "/")
            response_cookies = [value for key, value in headers if key.lower() == "set-cookie"]
            self.assertTrue(any(value.startswith(f"{server.SESSION_COOKIE_NAME}=") for value in response_cookies))
            self.assertTrue(any("Max-Age=0" in value for value in response_cookies))

            status, location, _ = request(
                browser_a,
                f"/auth/{provider}/callback?{urlencode({'code': 'replay', 'state': state})}",
                state_cookie,
            )
            self.assertEqual(status, 302)
            self.assertIn("auth_error=oauth_state_invalid", location)

            _, error_location, error_headers = request(browser_a, f"/auth/{provider}/start")
            error_state = parse_qs(urlparse(error_location).query)["state"][0]
            error_cookie = next(value.split(";", 1)[0] for key, value in error_headers if key.lower() == "set-cookie")
            status, location, headers = request(
                browser_a,
                f"/auth/{provider}/callback?{urlencode({'error': 'denied', 'state': error_state})}",
                error_cookie,
            )
            self.assertEqual(status, 302)
            self.assertIn(f"auth_error={provider}_access_denied", location)
            self.assertTrue(any("Max-Age=0" in value for key, value in headers if key.lower() == "set-cookie"))
            status, location, _ = request(
                browser_a,
                f"/auth/{provider}/callback?{urlencode({'code': 'after-error', 'state': error_state})}",
                error_cookie,
            )
            self.assertEqual(status, 302)
            self.assertIn("auth_error=oauth_state_invalid", location)
        finally:
            for patcher in reversed(patches):
                patcher.stop()
            browser_a.close()
            browser_b.close()
            http_server.shutdown()
            http_server.server_close()
            server_thread.join(timeout=5)

    def test_google_oauth_state_is_browser_bound_and_single_use(self) -> None:
        self.assert_oauth_state_is_browser_bound("google")

    def test_kakao_oauth_state_is_browser_bound_and_single_use(self) -> None:
        self.assert_oauth_state_is_browser_bound("kakao")

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

            connection.request("GET", "/metrics")
            metrics_response = connection.getresponse()
            self.assertEqual(metrics_response.status, 200)
            metrics = json.loads(metrics_response.read().decode("utf-8"))
            self.assertEqual(metrics["limits"]["max_sse_connections"], server.MAX_SSE_CONNECTIONS)
            self.assertIn("active", metrics["sse"])
            self.assertIn("rss_bytes", metrics["runtime"])
        finally:
            connection.close()
            http_server.shutdown()
            http_server.server_close()
            server_thread.join(timeout=5)

    def test_slow_headers_and_request_thread_capacity_are_bounded(self) -> None:
        with mock.patch.object(server, "HEADER_READ_TIMEOUT_SECONDS", 0.2):
            http_server = server.ChatServer(
                ("127.0.0.1", 0),
                server.ChatHandler,
                max_request_threads=2,
                max_body_readers=1,
            )
            server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
            server_thread.start()
            address = ("127.0.0.1", http_server.server_address[1])
            slow_clients = [socket.create_connection(address, timeout=2) for _ in range(2)]
            try:
                for client in slow_clients:
                    client.sendall(b"GET /health HTTP/1.1")
                deadline = time.monotonic() + 1
                while http_server.request_metrics.snapshot()["active"] < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(http_server.request_metrics.snapshot()["active"], 2)

                rejected = socket.create_connection(address, timeout=2)
                try:
                    response = rejected.recv(4096)
                    self.assertIn(b"503 Service Unavailable", response)
                finally:
                    rejected.close()

                for client in slow_clients:
                    client.settimeout(1)
                    self.assertEqual(client.recv(4096), b"")

                connection = http.client.HTTPConnection(*address, timeout=2)
                try:
                    connection.request("GET", "/metrics")
                    response = connection.getresponse()
                    metrics = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(metrics["limits"]["max_request_threads"], 2)
                    self.assertGreaterEqual(metrics["requests"]["rejected_total"], 1)
                    self.assertGreaterEqual(metrics["requests"]["header_timeouts_total"], 2)
                finally:
                    connection.close()
            finally:
                for client in slow_clients:
                    client.close()
                http_server.shutdown()
                http_server.server_close()
                server_thread.join(timeout=5)

    def test_slow_bodies_timeout_without_blocking_health_or_login(self) -> None:
        unique_suffix = str(time.time_ns())[-10:]
        username = f"slow{unique_suffix}"
        password = "test-password"
        user, error = server.STORE.create_local_user(
            username,
            f"slow_{unique_suffix}",
            password,
            "",
            f"010{unique_suffix[:8]}",
            "20대",
            "남성",
        )
        self.assertIsNone(error)
        self.assertIsNotNone(user)

        with mock.patch.object(server, "BODY_READ_TIMEOUT_SECONDS", 0.25):
            http_server = server.ChatServer(
                ("127.0.0.1", 0),
                server.ChatHandler,
                max_request_threads=6,
                max_body_readers=3,
            )
            server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
            server_thread.start()
            address = ("127.0.0.1", http_server.server_address[1])
            slow_clients = [socket.create_connection(address, timeout=2) for _ in range(2)]
            try:
                slow_request = (
                    b"POST /login HTTP/1.1\r\n"
                    b"Host: localhost\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: 4096\r\n\r\n{}"
                )
                for client in slow_clients:
                    client.sendall(slow_request)
                time.sleep(0.05)

                health = http.client.HTTPConnection(*address, timeout=2)
                health.request("GET", "/health")
                health_response = health.getresponse()
                self.assertEqual(health_response.status, 200)
                health_response.read()
                health.close()

                login_body = json.dumps({"username": username, "password": password})
                login = http.client.HTTPConnection(*address, timeout=2)
                login.request(
                    "POST",
                    "/login",
                    body=login_body,
                    headers={"Content-Type": "application/json"},
                )
                login_response = login.getresponse()
                self.assertEqual(login_response.status, 200)
                login_response.read()
                login.close()

                for client in slow_clients:
                    client.settimeout(1)
                    self.assertIn(b"408 Request Timeout", client.recv(4096))

                metrics_connection = http.client.HTTPConnection(*address, timeout=2)
                metrics_connection.request("GET", "/metrics")
                metrics_response = metrics_connection.getresponse()
                metrics = json.loads(metrics_response.read().decode("utf-8"))
                metrics_connection.close()
                self.assertGreaterEqual(metrics["requests"]["body_timeouts_total"], 2)
                self.assertIn("body_reader_rejections_total", metrics["requests"])
            finally:
                for client in slow_clients:
                    client.close()
                http_server.shutdown()
                http_server.server_close()
                server_thread.join(timeout=5)

    def test_common_security_headers_cover_documents_api_assets_and_uploads(self) -> None:
        unique_suffix = str(time.time_ns())[-10:]
        username = f"headers{unique_suffix}"
        user, error = server.STORE.create_local_user(
            username,
            f"headers_{unique_suffix}",
            "test-password",
            "",
            f"010{unique_suffix[:8]}",
            "20대",
            "남성",
        )
        self.assertIsNone(error)
        assert user is not None
        filename = server.profile_image_filename(user["id"])
        server.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        upload_path = server.UPLOADS_DIR / filename
        upload_path.write_bytes(b"test-webp-response")
        server.STORE.update_profile_image(username, f"/uploads/{filename}")
        session_token = server.SESSIONS.create(username)

        http_server = server.ChatServer(("127.0.0.1", 0), server.ChatHandler)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", http_server.server_address[1], timeout=5)
        try:
            requests = (
                ("/", {}),
                ("/health", {}),
                ("/assets/js/core.js", {}),
                (f"/uploads/{filename}", {"Cookie": f"{server.SESSION_COOKIE_NAME}={session_token}"}),
                ("/auth/providers", {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "chat.example.com"}),
            )
            for path, headers in requests:
                with self.subTest(path=path):
                    connection.request("GET", path, headers=headers)
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
                    self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
                    self.assertEqual(response.getheader("Referrer-Policy"), "strict-origin-when-cross-origin")
                    self.assertIn("camera=()", response.getheader("Permissions-Policy", ""))
                    csp = response.getheader("Content-Security-Policy", "")
                    self.assertIn("default-src 'self'", csp)
                    self.assertIn("frame-ancestors 'none'", csp)
                    self.assertIn("https://accounts.google.com", csp)
                    self.assertIn("https://www.youtube-nocookie.com", csp)
                    self.assertNotIn("*", csp)
                    if path == "/auth/providers":
                        self.assertEqual(response.getheader("Strict-Transport-Security"), "max-age=31536000")
        finally:
            connection.close()
            http_server.shutdown()
            http_server.server_close()
            server_thread.join(timeout=5)
            upload_path.unlink(missing_ok=True)


class OperationsObservabilityTestCase(unittest.TestCase):
    def start_server(self) -> tuple[server.ChatServer, threading.Thread]:
        http_server = server.ChatServer(("127.0.0.1", 0), server.ChatHandler)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        return http_server, server_thread

    def test_structured_request_logs_correlate_without_secrets(self) -> None:
        self.assertEqual(server.normalized_request_route("POST", "/uploads/grant"), "POST /uploads/grant")
        self.assertEqual(
            server.normalized_request_route("PUT", "/uploads/upload_" + "a" * 32 + ".pdf?grant=secret"),
            "PUT /uploads/:object",
        )
        unique_suffix = str(time.time_ns())[-10:]
        username = f"loguser{unique_suffix}"
        password = "never-log-this-password"
        user, error = server.STORE.create_local_user(
            username,
            f"log_{unique_suffix}",
            password,
            "",
            f"010{unique_suffix[:8]}",
            "20대",
            "남성",
        )
        self.assertIsNone(error)
        self.assertIsNotNone(user)
        captured = io.StringIO()
        with mock.patch.object(server, "STRUCTURED_LOGS_ENABLED", True), redirect_stdout(captured):
            http_server, server_thread = self.start_server()
            connection = http.client.HTTPConnection("127.0.0.1", http_server.server_address[1], timeout=5)
            try:
                login_body = json.dumps({"username": username, "password": password, "oauthCode": "secret-code"})
                connection.request(
                    "POST",
                    "/login?code=secret-code&signed_url=secret-url",
                    body=login_body,
                    headers={"Content-Type": "application/json", "X-Request-ID": "test-request-123456"},
                )
                login_response = connection.getresponse()
                login_response.read()
                self.assertEqual(login_response.status, 200)
                self.assertEqual(login_response.getheader("X-Request-ID"), "test-request-123456")
                cookie = login_response.getheader("Set-Cookie", "").split(";", 1)[0]

                connection.request("GET", "/session?token=secret-session-token", headers={"Cookie": cookie})
                session_response = connection.getresponse()
                session_response.read()
                self.assertEqual(session_response.status, 200)
                self.assertRegex(session_response.getheader("X-Request-ID", ""), r"^[0-9a-f]{24}$")
            finally:
                connection.close()
                http_server.shutdown()
                http_server.server_close()
                server_thread.join(timeout=5)

        log_text = captured.getvalue()
        records = [json.loads(line) for line in log_text.splitlines() if line.strip()]
        self.assertGreaterEqual(len(records), 2)
        login_record = next(record for record in records if record["request_id"] == "test-request-123456")
        self.assertEqual(login_record["route"], "/login")
        self.assertEqual(login_record["status"], 200)
        self.assertGreater(login_record["latency_ms"], 0)
        self.assertGreater(login_record["response_bytes"], 0)
        session_record = next(record for record in records if record["route"] == "/session")
        self.assertEqual(session_record["user_id"], server.safe_user_identifier(username))
        for secret in (password, username, "secret-code", "secret-url", "secret-session-token", "signed_url", "oauthCode"):
            self.assertNotIn(secret, log_text)

    def test_liveness_readiness_and_route_percentiles(self) -> None:
        http_server, server_thread = self.start_server()
        connection = http.client.HTTPConnection("127.0.0.1", http_server.server_address[1], timeout=5)
        try:
            connection.request("GET", "/live")
            live_response = connection.getresponse()
            live_payload = json.loads(live_response.read().decode("utf-8"))
            self.assertEqual(live_response.status, 200)
            self.assertEqual(live_payload["status"], "live")

            connection.request("GET", "/ready")
            ready_response = connection.getresponse()
            ready_payload = json.loads(ready_response.read().decode("utf-8"))
            self.assertEqual(ready_response.status, 200)
            self.assertTrue(ready_payload["ready"])
            self.assertTrue(all(ready_payload["checks"].values()))
            self.assertGreaterEqual(ready_payload["database"]["latency_ms"], 0)

            with mock.patch.object(server.STORE.repository, "is_legacy_imported", side_effect=ConnectionError("database unavailable")):
                connection.request("GET", "/ready")
                failed_response = connection.getresponse()
                failed_payload = json.loads(failed_response.read().decode("utf-8"))
            self.assertEqual(failed_response.status, 503)
            self.assertFalse(failed_payload["ready"])
            self.assertFalse(failed_payload["checks"]["database"])
            self.assertEqual(failed_payload["database"]["error"], "ConnectionError")

            connection.request("GET", "/metrics")
            metrics_response = connection.getresponse()
            metrics = json.loads(metrics_response.read().decode("utf-8"))
            self.assertEqual(metrics_response.status, 200)
            self.assertIn("GET /live", metrics["requests"]["routes"])
            self.assertIn("p50", metrics["requests"]["latency_ms"])
            self.assertIn("p95", metrics["requests"]["latency_ms"])
            self.assertIn("p99", metrics["requests"]["latency_ms"])
            self.assertGreaterEqual(metrics["requests"]["response_bytes_total"], 1)
            self.assertIn("active_body_readers", metrics["requests"])
            self.assertIn("open_file_descriptors", metrics["runtime"])
            self.assertIn("revision_lag", metrics["persistence"])
            self.assertEqual(metrics["readiness"]["status"], "not_ready")
        finally:
            connection.close()
            http_server.shutdown()
            http_server.server_close()
            server_thread.join(timeout=5)


class ShortsCatalogTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="colorless-shorts-catalog-")
        self.database_path = Path(self.temp_dir.name) / "catalog.sqlite3"
        self.repository = server.NormalizedSqliteRepository(self.database_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def item(video_id: str = "catalog-video", rank: float = 100) -> dict:
        return {
            "id": video_id,
            "title": f"Catalog {video_id}",
            "channel_title": "Catalog Channel",
            "rank_score": rank,
        }

    def test_catalog_is_shared_and_collection_lease_is_single_flight(self) -> None:
        now = time.time()
        self.repository.upsert_shorts_catalog([self.item()], "test", now, 60)
        second = server.NormalizedSqliteRepository(self.database_path)
        self.assertEqual(second.list_shorts_catalog(limit=10)[0]["id"], "catalog-video")

        first_lease = self.repository.acquire_shorts_collection_lease("instance-a", now, 60, 101, 5000)
        second_lease = second.acquire_shorts_collection_lease("instance-b", now, 60, 101, 5000)
        self.assertIsNotNone(first_lease)
        self.assertIsNone(second_lease)
        self.repository.finish_shorts_collection(
            "instance-a", now=now, next_job=1, success=True
        )
        next_lease = second.acquire_shorts_collection_lease("instance-b", now + 1, 60, 101, 5000)
        self.assertEqual(next_lease["next_job_index"], 1)

    def test_429_opens_circuit_and_stale_catalog_remains_available(self) -> None:
        now = time.time()
        self.repository.upsert_shorts_catalog([self.item("stale-video")], "test", now - 120, 1)
        collector = server.ShortsCatalogCollector(self.repository, "collector-a", start=False)
        try:
            with (
                mock.patch.object(server, "YOUTUBE_API_KEY", "test-key"),
                mock.patch.object(
                    server,
                    "collect_youtube_catalog_job",
                    side_effect=server.YoutubeCatalogError("http-429"),
                ),
            ):
                self.assertFalse(collector.run_once())
            status = self.repository.shorts_catalog_status(time.time())
            self.assertTrue(status["circuit_open"])
            self.assertEqual(status["failure_count"], 1)
            self.assertEqual(self.repository.list_shorts_catalog(limit=10)[0]["id"], "stale-video")
        finally:
            collector.close()

    def test_two_collectors_do_not_run_the_same_job_concurrently(self) -> None:
        first = server.ShortsCatalogCollector(self.repository, "collector-a", start=False)
        second = server.ShortsCatalogCollector(
            server.NormalizedSqliteRepository(self.database_path), "collector-b", start=False
        )
        started = threading.Event()
        release = threading.Event()

        def slow_collect(_job: dict) -> list[dict]:
            started.set()
            release.wait(3)
            return [self.item("single-flight")]

        try:
            with (
                mock.patch.object(server, "YOUTUBE_API_KEY", "test-key"),
                mock.patch.object(server, "collect_youtube_catalog_job", side_effect=slow_collect),
            ):
                thread = threading.Thread(target=first.run_once)
                thread.start()
                self.assertTrue(started.wait(2))
                self.assertFalse(second.run_once())
                release.set()
                thread.join(timeout=3)
            self.assertEqual(first.successes, 1)
            self.assertEqual(second.lease_skips, 1)
            self.assertEqual(self.repository.list_shorts_catalog(limit=10)[0]["id"], "single-flight")
        finally:
            release.set()
            first.close()
            second.close()

    def test_feed_handler_source_has_no_youtube_request(self) -> None:
        source = inspect.getsource(server.ChatHandler.serve_public_shorts)
        self.assertNotIn("fetch_youtube", source)
        self.assertNotIn("googleapis.com", source)
        self.assertIn("list_shorts_catalog", source)

    def test_catalog_read_p95_is_below_target(self) -> None:
        now = time.time()
        self.repository.upsert_shorts_catalog(
            [self.item(f"video-{index:04d}", 1000 - index) for index in range(1000)],
            "fixture",
            now,
            60,
        )
        timings = []
        for _ in range(30):
            started = time.perf_counter()
            page = self.repository.list_shorts_catalog(limit=100, offset=400)
            timings.append((time.perf_counter() - started) * 1000)
            self.assertEqual(len(page), 100)
        timings.sort()
        self.assertLess(timings[28], 300)

    def test_http_feed_uses_catalog_without_external_calls(self) -> None:
        suffix = str(time.time_ns())[-10:]
        user = server.STORE.create_or_update_social_user(
            "demo", f"catalog-http-{suffix}", nickname=f"catalog{suffix}"
        )
        session_token = server.SESSIONS.create(user["username"])
        video_id = f"http{suffix}"
        server.STORE.repository.upsert_shorts_catalog(
            [self.item(video_id, 1_000_000)],
            "http-fixture",
            time.time(),
            60,
        )
        http_server = server.ChatServer(("127.0.0.1", 0), server.ChatHandler)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        address = ("127.0.0.1", http_server.server_address[1])
        timings = []
        try:
            with (
                mock.patch.object(server, "fetch_youtube_catalog_json", side_effect=AssertionError("external call")) as catalog_fetch,
            ):
                for request_index in range(20):
                    connection = http.client.HTTPConnection(*address, timeout=5)
                    started = time.perf_counter()
                    connection.request(
                        "GET",
                        "/youtube/shorts",
                        headers={"Cookie": f"{server.SESSION_COOKIE_NAME}={session_token}"},
                    )
                    response = connection.getresponse()
                    payload = json.loads(response.read().decode("utf-8"))
                    timings.append((time.perf_counter() - started) * 1000)
                    connection.close()
                    self.assertEqual(response.status, 200)
                    self.assertIn("catalog", payload)
                    if request_index == 0:
                        self.assertIn(video_id, {item["id"] for item in payload["items"]})
                self.assertEqual(catalog_fetch.call_count, 0)
        finally:
            http_server.shutdown()
            http_server.server_close()
            server_thread.join(timeout=5)
        timings.sort()
        self.assertLess(timings[18], 300)


class AttachmentGrantContractTestCase(unittest.TestCase):
    def test_pending_upload_is_not_usable_until_verified(self) -> None:
        grants = server.UploadGrantStore(ttl_seconds=60)
        token = grants.create_pending(
            "upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf",
            "alice",
            name="report.pdf",
            content_type="application/pdf",
            size=10,
        )
        self.assertIsNotNone(token)
        assert token is not None
        self.assertFalse(grants.owns("upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf", "alice"))
        self.assertIsNone(grants.authorize_transfer("upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf", "alice", "wrong"))
        self.assertIsNotNone(grants.authorize_transfer("upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf", "alice", token))
        self.assertIsNone(
            grants.complete(
                "upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf",
                "alice",
                size=11,
                content_type="application/pdf",
            )
        )
        self.assertIsNotNone(
            grants.complete(
                "upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf",
                "alice",
                size=10,
                content_type="application/pdf",
            )
        )
        self.assertTrue(grants.owns("upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf", "alice"))

    def test_supabase_signed_urls_are_object_scoped_and_service_key_is_not_returned(self) -> None:
        filename = "upload_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.pdf"
        with (
            mock.patch.object(server, "SUPABASE_URL", "https://project.supabase.co"),
            mock.patch.object(server, "SUPABASE_SERVICE_ROLE_KEY", "service-secret"),
            mock.patch.object(
                server,
                "fetch_json",
                side_effect=[
                    {"url": f"/object/upload/sign/chat-uploads/{filename}?token=upload-token"},
                    {"signedURL": f"/object/sign/chat-uploads/{filename}?token=download-token"},
                ],
            ) as fetch,
        ):
            upload_url = server.supabase_signed_upload_url(filename)
            download_url = server.supabase_signed_download_url(filename, 60)

        self.assertEqual(
            upload_url,
            f"https://project.supabase.co/storage/v1/object/upload/sign/chat-uploads/{filename}?token=upload-token",
        )
        self.assertEqual(
            download_url,
            f"https://project.supabase.co/storage/v1/object/sign/chat-uploads/{filename}?token=download-token",
        )
        self.assertNotIn("service-secret", upload_url + download_url)
        self.assertIn("/object/upload/sign/chat-uploads/", fetch.call_args_list[0].args[0])
        self.assertEqual(json.loads(fetch.call_args_list[1].kwargs["data"]), {"expiresIn": 60})


class AttachmentTransferIntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="colorless-attachments-")
        self.patchers = [
            mock.patch.object(server, "UPLOADS_DIR", Path(self.temp_dir.name)),
            mock.patch.object(server, "UPLOAD_GRANTS", server.UploadGrantStore()),
            mock.patch.object(server, "SUPABASE_ENABLED", False),
        ]
        for patcher in self.patchers:
            patcher.start()

        suffix = str(time.time_ns())[-9:]
        self.username = f"upload{suffix}"
        peer_username = f"peer{suffix}"
        self.user, error = server.STORE.create_local_user(
            self.username,
            f"up{suffix}",
            "test-password",
            "",
            f"010{suffix[:8]}",
            "20대",
            "남성",
        )
        self.assertIsNone(error)
        self.peer, error = server.STORE.create_local_user(
            peer_username,
            f"pr{suffix}",
            "test-password",
            "",
            f"011{suffix[:8]}",
            "20대",
            "여성",
        )
        self.assertIsNone(error)
        assert self.user is not None and self.peer is not None
        _, error = server.STORE.add_friend_by_code(self.username, self.peer["friend_code"])
        self.assertIsNone(error)
        self.room, created, error = server.STORE.create_or_get_direct_room(self.username, self.peer["id"])
        self.assertIsNone(error)
        self.assertTrue(created)
        assert self.room is not None
        self.session_token = server.SESSIONS.create(self.username)
        self.cookie = f"{server.SESSION_COOKIE_NAME}={self.session_token}"

        self.http_server = server.ChatServer(("127.0.0.1", 0), server.ChatHandler)
        self.server_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        self.server_thread.start()
        self.address = ("127.0.0.1", self.http_server.server_address[1])

    def tearDown(self) -> None:
        self.http_server.shutdown()
        self.http_server.server_close()
        self.server_thread.join(timeout=5)
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(*self.address, timeout=10)
        request_headers = {"Cookie": self.cookie, **(headers or {})}
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            content = response.read()
            return response.status, dict(response.getheaders()), content
        finally:
            connection.close()

    def grant(self, payload: bytes, name: str = "report.pdf") -> dict:
        status, _, body = self.request(
            "POST",
            "/uploads/grant",
            json.dumps({"name": name, "type": "application/pdf", "size": len(payload)}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201, body)
        return json.loads(body.decode("utf-8"))["upload"]

    def test_streamed_upload_is_verified_before_message_and_supports_ranges(self) -> None:
        payload = b"%PDF-1.7\n" + (b"streamed-attachment\n" * 4096)
        upload = self.grant(payload)

        status, _, _ = self.request("GET", f"/uploads/{upload['id']}")
        self.assertEqual(status, 404)

        status, _, body = self.request(
            "PUT",
            upload["url"],
            payload,
            upload["headers"],
        )
        self.assertEqual(status, 200, body)
        self.assertEqual((Path(self.temp_dir.name) / upload["id"]).read_bytes(), payload)
        self.assertEqual(list(Path(self.temp_dir.name).glob("*.part")), [])

        status, _, body = self.request(
            "POST",
            "/uploads/complete",
            json.dumps({"id": upload["id"]}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201, body)
        attachment = json.loads(body.decode("utf-8"))["attachment"]

        message_payload = {
            "roomId": self.room["id"],
            "text": "range test",
            "attachment": attachment,
            "clientMessageId": f"upload_{str(time.time_ns())[-18:]}",
        }
        status, _, body = self.request(
            "POST",
            "/messages",
            json.dumps(message_payload).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201, body)
        self.assertFalse(server.UPLOAD_GRANTS.owns(upload["id"], self.username))

        status, headers, body = self.request(
            "GET",
            attachment["url"],
            headers={"Range": "bytes=5-19"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")
        self.assertEqual(headers.get("Content-Range"), f"bytes 5-19/{len(payload)}")
        self.assertEqual(body, payload[5:20])

        unauthorized = http.client.HTTPConnection(*self.address, timeout=5)
        try:
            unauthorized.request("GET", attachment["url"])
            response = unauthorized.getresponse()
            response.read()
            self.assertEqual(response.status, 401)
            self.assertIsNone(response.getheader("Location"))
        finally:
            unauthorized.close()

        def range_request(_: int) -> int:
            status_code, _, content = self.request(
                "GET",
                attachment["url"],
                headers={"Range": "bytes=0-65535"},
            )
            return status_code if content == payload[:65536] else 0

        with ThreadPoolExecutor(max_workers=32) as executor:
            statuses = list(executor.map(range_request, range(100)))
        self.assertEqual(statuses, [206] * 100)

        status, _, body = self.request("GET", f"/messages?room_id={self.room['id']}")
        self.assertEqual(status, 200, body)

    def test_wrong_magic_bytes_are_failed_and_never_published(self) -> None:
        payload = b"not-a-real-pdf"
        upload = self.grant(payload, "fake.pdf")
        status, _, _ = self.request("PUT", upload["url"], payload, upload["headers"])
        self.assertEqual(status, 400)
        self.assertFalse((Path(self.temp_dir.name) / upload["id"]).exists())

        status, _, _ = self.request(
            "POST",
            "/uploads/complete",
            json.dumps({"id": upload["id"]}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 409)


class SupabaseRepositoryContractTestCase(unittest.TestCase):
    def test_profile_art_uses_a_separate_fixed_size_binary_resource(self) -> None:
        requests = []
        packed = bytes(index % 256 for index in range(3072))

        def transport(path: str, **kwargs):
            requests.append((path, kwargs))
            if path.startswith("/rest/v1/profile_art?") and kwargs.get("method", "GET") == "GET":
                return [{"version": 7, "pixels_rgb": f"\\x{packed.hex()}"}]
            if path.endswith("/colorless_sync_user"):
                return 1
            return {}

        repository = server.NormalizedSupabaseRepository("https://example.test", "secret", transport)
        user = {
            "id": "u1",
            "username": "alice",
            "friend_code": "alice",
            "profile_pixels": ["#123456"] * 1024,
        }
        repository.sync_user(user)
        repository.save_profile_art("u1", packed, 7)
        self.assertEqual(repository.load_profile_art("u1"), (7, packed))

        sync_request = next(kwargs for path, kwargs in requests if path.endswith("/colorless_sync_user"))
        self.assertNotIn("profile_pixels", sync_request["payload"]["user_data"])
        art_request = next((path, kwargs) for path, kwargs in requests if "profile_art?on_conflict=" in path)
        self.assertEqual(len(bytes.fromhex(art_request[1]["payload"][0]["pixels_rgb"][2:])), 3072)
        self.assertIsInstance(art_request[1]["payload"][0]["updated_at"], float)

    def test_shorts_catalog_uses_shared_rows_and_atomic_collector_rpcs(self) -> None:
        requests = []

        def transport(path: str, **kwargs):
            requests.append((path, kwargs))
            if path.endswith("/colorless_acquire_shorts_collection"):
                return {"next_job_index": 2, "quota_used": 303}
            if path.startswith("/rest/v1/shorts_catalog?") and kwargs.get("method", "GET") == "GET":
                return [{
                    "video_id": "video-1",
                    "source": "search-0",
                    "rank_score": 100,
                    "last_seen_at": 10,
                    "expires_at": 20,
                    "data": {"title": "Shared", "channel_title": "Channel"},
                }]
            return {}

        repository = server.NormalizedSupabaseRepository("https://example.test", "secret", transport)
        lease = repository.acquire_shorts_collection_lease("instance-a", 10, 120, 101, 5000)
        repository.upsert_shorts_catalog(
            [{"id": "video-1", "title": "Shared", "channel_title": "Channel", "rank_score": 100}],
            "search-0",
            10,
            60,
        )
        rows = repository.list_shorts_catalog(limit=20)
        repository.finish_shorts_collection("instance-a", now=11, next_job=3, success=True)

        self.assertEqual(lease["next_job_index"], 2)
        self.assertEqual(rows[0]["id"], "video-1")
        rpc_names = {path.rsplit("/", 1)[-1] for path, _ in requests if "/rpc/" in path}
        self.assertEqual(
            rpc_names,
            {"colorless_acquire_shorts_collection", "colorless_finish_shorts_collection"},
        )
        upsert_request = next((path, kwargs) for path, kwargs in requests if path.startswith("/rest/v1/shorts_catalog?on_conflict="))
        self.assertEqual(upsert_request[1]["prefer"], "resolution=merge-duplicates,return=minimal")

    def test_all_rows_pages_past_postgrest_response_limit(self) -> None:
        dataset = [{"id": str(index)} for index in range(5)]
        requested_offsets = []

        def transport(path: str, **kwargs):
            query = parse_qs(urlparse(path).query)
            offset = int(query["offset"][0])
            limit = int(query["limit"][0])
            requested_offsets.append(offset)
            return dataset[offset:offset + limit]

        repository = server.NormalizedSupabaseRepository("https://example.test", "secret", transport)
        rows = repository.all_rows("users", {"select": "id", "order": "id.asc"}, page_size=2)

        self.assertEqual(rows, dataset)
        self.assertEqual(requested_offsets, [0, 2, 4])

    def test_load_state_uses_bounded_table_reads_without_per_user_queries(self) -> None:
        requests = []
        responses = {
            "users": [{"data": {"id": "u1", "username": "alice"}, "revision": 3}],
            "friendships": [],
            "rooms": [{"data": {"id": "r1", "name": "Room"}, "revision": 4, "updated_at": "2026-08-19T00:00:00Z"}],
            "room_members": [{"room_id": "r1", "user_id": "u1"}],
            "read_positions": [{"room_id": "r1", "user_id": "u1", "message_id": "m1"}],
            "sessions": [{"token_hash": "token", "user_id": "u1", "created_at": 1, "expires_at": 2}],
            "shorts_feeds": [{"user_id": "u1", "next_cursor": "next"}],
            "shorts_seen": [
                {"user_id": "u1", "video_id": "v1", "seen_order": 0},
                {"user_id": "u1", "video_id": "v2", "seen_order": 1},
            ],
        }

        def transport(path: str, **kwargs):
            requests.append((path, kwargs))
            table = path.removeprefix("/rest/v1/").split("?", 1)[0]
            return responses.get(table, [])

        repository = server.NormalizedSupabaseRepository("https://example.test", "secret", transport)
        state = repository.load_state()

        self.assertEqual(len(requests), 8)
        self.assertEqual(sum(path.startswith("/rest/v1/shorts_feeds?") for path, _ in requests), 1)
        self.assertEqual(sum(path.startswith("/rest/v1/shorts_seen?") for path, _ in requests), 1)
        self.assertEqual(state["rooms"][0]["participant_ids"], ["u1"])
        self.assertEqual(state["rooms"][0]["last_read_by"], {"u1": "m1"})
        self.assertEqual(state["sessions"]["token"]["username"], "alice")
        self.assertEqual(state["shorts_feeds"]["alice"], {"seen_ids": ["v1", "v2"], "next_cursor": "next"})

    def test_runtime_writes_use_transactional_rpcs(self) -> None:
        requests = []

        def transport(path: str, **kwargs):
            requests.append((path, kwargs))
            if path.endswith("/colorless_sync_user") or path.endswith("/colorless_sync_room"):
                return 1
            if path.endswith("/colorless_insert_message"):
                return 2
            if path.endswith("/colorless_session_username"):
                return "alice"
            return {}

        repository = server.NormalizedSupabaseRepository("https://example.test", "secret", transport)
        user = {"id": "u1", "username": "alice", "friend_code": "ABC123"}
        room = {"id": "r1", "participant_ids": ["u1"], "last_read_by": {}}
        message = {"id": "m1", "room_id": "r1", "username": "alice"}
        repository.sync_user(user)
        repository.sync_room(room)
        self.assertTrue(repository.insert_message(message, "u1", room, 200))
        repository.create_session("token", "u1", 1, 2, 5)
        self.assertEqual(repository.session_username("token", 1.5), "alice")
        repository.save_shorts_feed("u1", ["v1"], "next")

        rpc_names = {path.rsplit("/", 1)[-1] for path, _ in requests}
        self.assertEqual(
            rpc_names,
            {
                "colorless_sync_user", "colorless_sync_room", "colorless_insert_message",
                "colorless_create_session", "colorless_session_username", "colorless_save_shorts_feed",
            },
        )

    def test_shared_events_and_presence_use_durable_supabase_contract(self) -> None:
        requests = []

        def transport(path: str, **kwargs):
            requests.append((path, kwargs))
            if path.endswith("/colorless_publish_event"):
                payload = kwargs["payload"]["event_data"]
                return {**payload, "revision": 12, "occurred_at": "2026-08-19T00:00:00Z"}
            if path.endswith("/colorless_touch_presence"):
                return {"presence": {"online": True, "active_room_ids": ["r1"], "emoji": "😀"}, "changed": True}
            if path.endswith("/colorless_presence_for_user"):
                return {"online": True, "active_room_ids": ["r1"], "emoji": "😀"}
            if path.endswith("/colorless_presence_for_users"):
                return [{"username": "alice", "presence": {"online": True, "active_room_ids": ["r1"], "emoji": "😀"}}]
            if path.endswith("/colorless_latest_messages"):
                return [{"room_id": "r1", "data": {"id": "m1", "room_id": "r1"}}]
            if path.endswith("/colorless_cleanup_presence"):
                return [{"username": "alice", "presence": {"online": False, "active_room_ids": [], "emoji": ""}}]
            if path.startswith("/rest/v1/realtime_events?"):
                return [{"sequence": 12, "data": {"event_id": "event-1", "revision": 12}, "recipients": ["alice"]}]
            return {}

        repository = server.NormalizedSupabaseRepository("https://example.test", "secret", transport)
        published = repository.publish_event(
            {"event_id": "event-1", "type": "message_created", "roomId": "r1"},
            {"alice"},
            "instance-a",
            occurred_at=1,
        )
        current, changed = repository.touch_presence("lease", "instance-a", "alice", "r1", "😀", 45)
        events = repository.list_events_after(11)
        replay = repository.events_for_user_after("alice", 11)
        latest = repository.latest_messages_for_rooms(["r1"])
        presences = repository.presence_for_users(["alice"])

        self.assertEqual(published["revision"], 12)
        self.assertTrue(changed)
        self.assertTrue(current["online"])
        self.assertEqual(events[0][1], {"alice"})
        self.assertEqual(replay[0]["event_id"], "event-1")
        self.assertEqual(latest["r1"]["id"], "m1")
        self.assertTrue(presences["alice"]["online"])
        replay_path = next(path for path, _ in requests if "recipients=cs." in path)
        self.assertIn("recipients=cs.{alice}", replay_path)
        self.assertEqual(repository.cleanup_expired_presence()[0][0], "alice")


class MultiInstanceConsistencyTestCase(unittest.TestCase):
    @staticmethod
    def wait_until(predicate, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return bool(predicate())

    def test_two_brokers_fan_out_replay_and_survive_rolling_stop(self) -> None:
        with tempfile.TemporaryDirectory(prefix="colorless-broker-") as temp_dir:
            database_path = Path(temp_dir) / "shared.sqlite3"
            repository_a = server.NormalizedSqliteRepository(database_path)
            repository_b = server.NormalizedSqliteRepository(database_path)
            delivered_a = []
            delivered_b = []
            broker_a = server.DurableEventBroker(
                repository_a, "instance-a", lambda _: {"bob"}, lambda: None,
                deliver=lambda event, recipients: delivered_a.append((event, recipients)),
            )
            broker_b = server.DurableEventBroker(
                repository_b, "instance-b", lambda _: {"bob"}, lambda: None,
                deliver=lambda event, recipients: delivered_b.append((event, recipients)),
            )
            try:
                started = time.perf_counter()
                first = broker_a.publish(
                    {"type": "message_created", "roomId": "room-1", "message": {"id": "msg-1"}},
                    {"bob"},
                )
                second = broker_a.publish({"type": "friends_updated"}, {"bob"})
                third = broker_a.publish({"type": "room_created", "roomId": "room-2"}, {"bob"})
                expected_ids = {first["event_id"], second["event_id"], third["event_id"]}
                self.assertTrue(self.wait_until(
                    lambda: expected_ids.issubset({item[0].get("event_id") for item in delivered_b})
                ))
                self.assertLess(time.perf_counter() - started, 1.0)
                self.assertGreater(first["revision"], 0)
                self.assertIn("occurred_at", first)

                broker_a.close()
                after_rolling_stop = broker_b.publish(
                    {"type": "message_created", "roomId": "room-1", "message": {"id": "msg-2"}},
                    {"bob"},
                )
                self.assertTrue(self.wait_until(
                    lambda: any(item[0].get("event_id") == after_rolling_stop["event_id"] for item in delivered_b)
                ))
                replayed = broker_b.replay("bob", 0)
                self.assertEqual(
                    [event["event_id"] for event in replayed],
                    [first["event_id"], second["event_id"], third["event_id"], after_rolling_stop["event_id"]],
                )
            finally:
                broker_a.close()
                broker_b.close()

    def test_publish_failure_is_retried_from_local_outbox(self) -> None:
        with tempfile.TemporaryDirectory(prefix="colorless-outbox-") as temp_dir:
            repository = server.NormalizedSqliteRepository(Path(temp_dir) / "shared.sqlite3")

            class FlakyRepository:
                def __init__(self, delegate) -> None:
                    self.delegate = delegate
                    self.failures_remaining = 1

                def publish_event(self, *args, **kwargs):
                    if self.failures_remaining:
                        self.failures_remaining -= 1
                        raise ConnectionError("temporary broker failure")
                    return self.delegate.publish_event(*args, **kwargs)

                def __getattr__(self, name):
                    return getattr(self.delegate, name)

            delivered = []
            broker = server.DurableEventBroker(
                FlakyRepository(repository), "instance-a", lambda _: {"bob"}, lambda: None,
                deliver=lambda event, recipients: delivered.append((event, recipients)),
            )
            try:
                event = broker.publish({"type": "room_created"}, {"bob"})
                self.assertTrue(delivered)
                self.assertTrue(self.wait_until(lambda: repository.latest_event_sequence() == 1))
                persisted = repository.events_for_user_after("bob", 0)
                self.assertEqual(persisted[0]["event_id"], event["event_id"])
            finally:
                broker.close()

    def test_presence_lease_expires_and_is_cleaned(self) -> None:
        with tempfile.TemporaryDirectory(prefix="colorless-presence-ttl-") as temp_dir:
            repository = server.NormalizedSqliteRepository(Path(temp_dir) / "shared.sqlite3")
            current, changed = repository.touch_presence("lease", "instance-a", "alice", "room-1", "😀", 1)
            self.assertTrue(changed)
            self.assertTrue(current["online"])
            time.sleep(1.05)
            changes = dict(repository.cleanup_expired_presence())
            self.assertIn("alice", changes)
            self.assertFalse(changes["alice"]["online"])


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

    def test_full_sse_queue_drops_and_disconnects_slow_subscriber(self) -> None:
        slow_subscriber: queue.Queue = queue.Queue(maxsize=1)
        slow_subscriber.put_nowait({"type": "old"})
        before_drops = server.SSE_METRICS.snapshot()["queue_drops_total"]
        with server.SUBSCRIBERS_LOCK:
            server.SUBSCRIBERS[slow_subscriber] = "alice"
            server.SUBSCRIBERS_BY_USERNAME.setdefault("alice", set()).add(slow_subscriber)
        try:
            server.push_event({"type": "new"}, {"alice"})
            self.assertNotIn(slow_subscriber, server.SUBSCRIBERS)
            self.assertNotIn(slow_subscriber, server.SUBSCRIBERS_BY_USERNAME.get("alice", set()))
            self.assertIsNone(slow_subscriber.get_nowait())
            self.assertEqual(server.SSE_METRICS.snapshot()["queue_drops_total"], before_drops + 1)
        finally:
            with server.SUBSCRIBERS_LOCK:
                server.SUBSCRIBERS.pop(slow_subscriber, None)
                server.SUBSCRIBERS_BY_USERNAME.get("alice", set()).discard(slow_subscriber)

    def test_application_services_return_response_and_events(self) -> None:
        services = server.ApplicationServices(
            self.store,
            server.PresenceStore(self.store.repository, "test-application"),
        )
        friend_outcome = services.add_friend(
            self.alice,
            {"friendCode": self.eve["friend_code"]},
        )
        self.assertEqual(friend_outcome.status, server.HTTPStatus.CREATED)
        self.assertEqual(friend_outcome.data["friend"]["id"], self.eve["id"])
        self.assertEqual(friend_outcome.events[0][0]["type"], "friends_updated")

        group_outcome = services.create_group_room(
            self.alice,
            {"name": "Pipeline Team", "memberUserIds": [self.bob["id"], self.eve["id"]]},
        )
        self.assertEqual(group_outcome.status, server.HTTPStatus.CREATED)
        self.assertEqual(group_outcome.data["room"]["kind"], "group")
        self.assertTrue(all(event["type"] == "room_created" for event, _ in group_outcome.events))

        with mock.patch.object(
            self.store,
            "get_messages",
            side_effect=AssertionError("new messages must not be reloaded from storage"),
        ):
            message_outcome = services.create_message(
                self.alice,
                {
                    "roomId": group_outcome.data["room"]["id"],
                    "text": "who has not read this",
                    "clientMessageId": "application-read-state",
                },
                lambda _value, _username: None,
            )
        self.assertEqual(
            {reader["username"] for reader in message_outcome.data["unread_by"]},
            {"bob", "eve"},
        )

        presence_outcome = services.update_presence(
            "activity-token",
            self.alice,
            {"activeRoomId": group_outcome.data["room"]["id"], "emoji": "🧑‍💻"},
        )
        self.assertEqual(presence_outcome.data["presence"]["emoji"], "🧑‍💻")
        self.assertEqual(presence_outcome.events[0][0]["type"], "presence_updated")
        self.assertEqual(presence_outcome.events[0][0]["presence"]["emoji"], "🧑‍💻")

    def test_message_page_uses_one_repository_read_for_items_and_read_state(self) -> None:
        for index in range(5):
            self.store.add_message(self.room_id, "alice", f"message {index}")

        list_messages = self.store.repository.list_messages
        with mock.patch.object(
            self.store.repository,
            "list_messages",
            wraps=list_messages,
        ) as list_messages_spy:
            page = self.store.get_messages_page(self.room_id, "alice", limit=2)

        self.assertIsNotNone(page)
        assert page is not None
        self.assertEqual(len(page["items"]), 2)
        self.assertTrue(page["next_cursor"])
        self.assertEqual(list_messages_spy.call_count, 1)

    def test_profile_art_round_trip_is_lossless_and_not_in_user_rows(self) -> None:
        pixels = [f"#{(index * 2654435761 & 0xFFFFFF):06x}" for index in range(1024)]
        updated = self.store.update_profile_pixels("alice", pixels)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertNotIn("profile_pixels", updated)
        self.assertRegex(updated["profile_thumbnail_url"], r"^/profile-art/user_[0-9a-f]{8}/thumbnail\?v=\d+$")

        loaded = self.store.get_profile_pixels("alice")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["pixels"], pixels)

        database = server.sqlite3.connect(self.store.database_path)
        try:
            data_json = database.execute("SELECT data_json FROM users WHERE id=?", (self.alice["id"],)).fetchone()[0]
            packed_size = database.execute(
                "SELECT length(pixels_rgb) FROM profile_art WHERE user_id=?", (self.alice["id"],)
            ).fetchone()[0]
        finally:
            database.close()
        self.assertNotIn("profile_pixels", json.loads(data_json))
        self.assertEqual(packed_size, 3072)
        self.assertLessEqual(packed_size, 4 * 1024)

        thumbnail = self.store.get_profile_art_thumbnail(self.alice["id"])
        self.assertIsNotNone(thumbnail)
        assert thumbnail is not None
        self.assertTrue(thumbnail[1].startswith(b"\x89PNG\r\n\x1a\n"))

        state_path = self.store.path
        self.assertTrue(self.store.close())
        self.store = server.StateStore(state_path)
        restored_pixels = self.store.get_profile_pixels("alice")
        assert restored_pixels is not None
        self.assertEqual(restored_pixels["pixels"], pixels)

    def test_blank_profiles_store_no_pixel_array_or_art_row(self) -> None:
        record = self.store.get_user_record("eve")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertNotIn("profile_pixels", record)
        self.assertEqual(record["profile_art_version"], 0)
        self.assertTrue(record["profile_pixels_blank"])
        database = server.sqlite3.connect(self.store.database_path)
        try:
            count = database.execute(
                "SELECT COUNT(*) FROM profile_art WHERE user_id=?", (self.eve["id"],)
            ).fetchone()[0]
            data_json = database.execute("SELECT data_json FROM users WHERE id=?", (self.eve["id"],)).fetchone()[0]
        finally:
            database.close()
        self.assertEqual(count, 0)
        self.assertNotIn("profile_pixels", json.loads(data_json))
        self.assertEqual(self.store.get_profile_pixels("eve")["pixels"], ["#ffffff"] * 1024)

    def test_legacy_string_array_profile_art_migrates_losslessly(self) -> None:
        legacy_pixels = [f"#{(index * 97 & 0xFFFFFF):06x}" for index in range(1024)]
        bob_id = self.bob["id"]
        state_path = self.store.path
        database_path = self.store.database_path
        self.assertTrue(self.store.close())
        database = server.sqlite3.connect(database_path)
        try:
            stored = json.loads(database.execute("SELECT data_json FROM users WHERE id=?", (bob_id,)).fetchone()[0])
            stored["profile_pixels"] = legacy_pixels
            stored.pop("profile_art_version", None)
            database.execute(
                "UPDATE users SET data_json=? WHERE id=?",
                (json.dumps(stored, separators=(",", ":")), bob_id),
            )
            database.commit()
        finally:
            database.close()

        self.store = server.StateStore(state_path)
        migrated = self.store.get_profile_pixels("bob")
        assert migrated is not None
        self.assertEqual(migrated["pixels"], legacy_pixels)
        database = server.sqlite3.connect(database_path)
        try:
            compact = json.loads(database.execute("SELECT data_json FROM users WHERE id=?", (bob_id,)).fetchone()[0])
            packed_size = database.execute(
                "SELECT length(pixels_rgb) FROM profile_art WHERE user_id=?", (bob_id,)
            ).fetchone()[0]
        finally:
            database.close()
        self.assertNotIn("profile_pixels", compact)
        self.assertEqual(packed_size, 3072)

    def test_thousand_friend_summaries_do_not_scale_with_pixel_arrays(self) -> None:
        pixels = ["#123456"] * 1024
        summaries = []
        base_user = self.store.get_user_record("bob")
        assert base_user is not None
        for index in range(1000):
            user = {
                **base_user,
                "id": f"user_{index:08x}",
                "username": f"friend{index:04d}",
                "friend_code": f"friend{index:04d}",
                "display_name": f"Friend {index:04d}",
                "profile_pixels": pixels,
                "profile_pixels_blank": False,
                "profile_art_version": index + 1,
            }
            summaries.append(self.store._user_list_summary(user))
        encoded = json.dumps(summaries, separators=(",", ":")).encode("utf-8")
        self.assertTrue(all("profile_pixels" not in item for item in summaries))
        self.assertLess(len(encoded), 400 * 1024)
        avatar_metadata = json.dumps([
            {"profile_thumbnail_url": item["profile_thumbnail_url"], "profile_art_version": item["profile_art_version"]}
            for item in summaries
        ], separators=(",", ":")).encode("utf-8")
        self.assertLess(len(avatar_metadata) / len(summaries), 200)

    def test_entity_pages_are_stable_compact_and_recover_from_revision(self) -> None:
        self.store.add_friend("alice", self.eve["id"])
        group, error = self.store.create_group_room("alice", "Paged Team", [self.bob["id"], self.eve["id"]])
        self.assertIsNone(error)
        assert group is not None

        for index in range(18):
            friend = self.store.create_or_update_social_user(
                "demo", f"paged-friend-{index}", nickname=f"paged{index:02d}"
            )
            self.store.add_friend("alice", friend["id"])
            room, _, room_error = self.store.create_or_get_direct_room("alice", friend["id"])
            self.assertIsNone(room_error)
            self.assertIsNotNone(room)

        friend_ids: list[str] = []
        friend_cursor = ""
        while True:
            page = self.store.get_friends_page(self.alice, limit=7, cursor=friend_cursor)
            self.assertTrue(all("profile_pixels" not in item for item in page["items"]))
            friend_ids.extend(item["id"] for item in page["items"])
            friend_cursor = page["next_cursor"]
            if not friend_cursor:
                break
        self.assertEqual(len(friend_ids), len(set(friend_ids)))
        self.assertEqual(len(friend_ids), 20)

        room_ids: list[str] = []
        room_cursor = ""
        compact_group = None
        compressed_pages = 0
        while True:
            page = self.store.get_rooms_page(self.alice, limit=6, cursor=room_cursor)
            compressed_pages += len(gzip.compress(json.dumps(page).encode("utf-8")))
            for item in page["items"]:
                room_ids.append(item["id"])
                if item["id"] == group["id"]:
                    compact_group = item
            room_cursor = page["next_cursor"]
            if not room_cursor:
                break
        self.assertEqual(len(room_ids), len(set(room_ids)))
        self.assertEqual(len(room_ids), 20)
        self.assertIsNotNone(compact_group)
        self.assertNotIn("participants", compact_group)
        self.assertLess(compressed_pages, 100 * 1024)

        members = self.store.get_room_members_page(group["id"], self.alice, limit=2)
        self.assertIsNotNone(members)
        assert members is not None
        self.assertEqual(len(members["items"]), 2)
        self.assertTrue(members["next_cursor"])
        final_members = self.store.get_room_members_page(
            group["id"], self.alice, limit=2, cursor=members["next_cursor"]
        )
        self.assertEqual(len(final_members["items"]), 1)

        durable = self.store.repository.publish_event(
            {"type": "room_updated", "roomId": group["id"], "room": group},
            {"alice"},
            "test-bootstrap",
        )
        sync = self.store.get_sync_page("alice", after_revision=0, limit=10)
        self.assertEqual(sync["revision"], durable["revision"])
        self.assertEqual(sync["events"][0]["event_id"], durable["event_id"])
        self.assertNotIn("participants", sync["events"][0]["room"])
        unchanged = self.store.get_sync_page("alice", after_revision=sync["revision"], limit=10)
        self.assertEqual(unchanged["events"], [])

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

        received_messages = self.store.get_messages(self.room_id, "alice") or []
        self.assertEqual(
            [reader["username"] for reader in received_messages[-1]["read_by"]],
            ["alice"],
        )
        self.assertEqual(received_messages[-1]["unread_by"], [])

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

        alice_messages = self.store.get_messages(group["id"], "alice") or []
        self.assertEqual(
            {reader["username"] for reader in alice_messages[-1]["unread_by"]},
            {"bob", "eve"},
        )

        self.store.mark_room_read(group["id"], "bob")
        alice_messages = self.store.get_messages(group["id"], "alice") or []
        self.assertFalse(alice_messages[-1]["read"])
        self.assertEqual(
            [reader["username"] for reader in alice_messages[-1]["read_by"]],
            ["bob"],
        )
        self.assertEqual(
            [reader["username"] for reader in alice_messages[-1]["unread_by"]],
            ["eve"],
        )
        bob_messages = self.store.get_messages(group["id"], "bob") or []
        self.assertEqual(
            [reader["username"] for reader in bob_messages[-1]["read_by"]],
            ["bob"],
        )
        self.assertEqual(
            [reader["username"] for reader in bob_messages[-1]["unread_by"]],
            ["eve"],
        )

        self.store.mark_room_read(group["id"], "eve")
        alice_messages = self.store.get_messages(group["id"], "alice") or []
        self.assertTrue(alice_messages[-1]["read"])
        self.assertEqual(
            {reader["username"] for reader in alice_messages[-1]["read_by"]},
            {"bob", "eve"},
        )
        self.assertEqual(alice_messages[-1]["unread_by"], [])

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

        self.assertEqual(written_part_sets, [{"rooms"}])
        self.assertEqual(
            self.store.repository.list_messages(self.room_id, limit=1)[0]["text"],
            "incremental",
        )

    def test_incremental_message_survives_restart(self) -> None:
        saved = self.store.add_message(self.room_id, "alice", "persisted")
        self.assertIsNotNone(saved)
        self.assertEqual(self.store.state["messages"].get(self.room_id, []), [])
        self.assertTrue(self.store.flush())

        state_path = self.store.path
        self.assertTrue(self.store.close())
        self.store = server.StateStore(state_path)

        messages = self.store.get_messages(self.room_id, "bob")
        self.assertIsNotNone(messages)
        assert messages is not None
        self.assertEqual(messages[-1]["text"], "persisted")
        self.assertEqual(self.store.state["messages"].get(self.room_id, []), [])

    def test_message_commit_is_durable_before_async_snapshot_writer(self) -> None:
        original_save = self.store._save_locked
        self.store._save_locked = lambda *part_ids: None
        try:
            result = self.store.add_message(self.room_id, "alice", "durable-row")
        finally:
            self.store._save_locked = original_save
        self.assertIsNotNone(result)

        independent_repository = server.NormalizedSqliteRepository(self.store.database_path)
        messages = independent_repository.list_messages(self.room_id, limit=10)
        self.assertEqual(messages[-1]["text"], "durable-row")
        self.assertEqual(self.store.state["messages"].get(self.room_id, []), [])

    def test_normalized_rows_are_authoritative_for_all_domain_state(self) -> None:
        alice_record = self.store.get_user_record("alice")
        assert alice_record is not None
        alice_pixels = self.store.get_profile_pixels("alice")
        assert alice_pixels is not None
        updated, error = self.store.update_profile(
            "alice",
            "Alice Normalized",
            "😀",
            alice_record["friend_code"],
            alice_pixels["pixels"],
        )
        self.assertIsNone(error)
        self.assertIsNotNone(updated)
        self.store.save_shorts_feed("alice", ["video-a", "video-b"], "cursor-next")
        sessions = server.SessionStore(state_store=self.store)
        token = sessions.create("alice")
        token_hash = server.hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.assertTrue(self.store.flush())

        database = server.sqlite3.connect(self.store.database_path)
        try:
            normalized_counts = {
                table: database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("users", "friendships", "rooms", "room_members", "sessions", "shorts_seen")
            }
            legacy_users = json.loads(
                database.execute("SELECT state_json FROM state_parts WHERE id='users'").fetchone()[0]
            )
        finally:
            database.close()
        self.assertEqual(normalized_counts["users"], 3)
        self.assertEqual(normalized_counts["friendships"], 1)
        self.assertEqual(normalized_counts["room_members"], 2)
        self.assertEqual(normalized_counts["sessions"], 1)
        self.assertEqual(normalized_counts["shorts_seen"], 2)
        self.assertEqual(legacy_users, [])

        state_path = self.store.path
        self.assertTrue(self.store.close())
        self.store = server.StateStore(state_path)
        restored = self.store.get_user_record("alice")
        assert restored is not None
        self.assertEqual(restored["display_name"], "Alice Normalized")
        self.assertEqual(self.store.get_shorts_feed("alice"), (["video-a", "video-b"], "cursor-next"))
        self.assertEqual(self.store.get_session_username(token_hash, server.SESSION_TTL_SECONDS), "alice")
        self.assertIsNotNone(self.store.get_messages(self.room_id, "alice"))

    def test_normalized_multi_row_write_rolls_back_on_constraint_failure(self) -> None:
        room = json.loads(json.dumps(self.store._rooms_by_id[self.room_id]))
        room["name"] = "must-not-commit"
        room["participant_ids"].append("missing-user")

        with self.assertRaises(server.sqlite3.IntegrityError):
            self.store.repository.sync_room(room)

        database = server.sqlite3.connect(self.store.database_path)
        try:
            stored_room = json.loads(
                database.execute("SELECT data_json FROM rooms WHERE id=?", (self.room_id,)).fetchone()[0]
            )
            stored_members = {
                row[0]
                for row in database.execute("SELECT user_id FROM room_members WHERE room_id=?", (self.room_id,))
            }
        finally:
            database.close()
        self.assertNotEqual(stored_room["name"], "must-not-commit")
        self.assertEqual(stored_members, set(self.store._rooms_by_id[self.room_id]["participant_ids"]))

    def test_stale_room_revision_cannot_overwrite_another_instance(self) -> None:
        second_repository = server.NormalizedSqliteRepository(self.store.database_path)
        stale_room = next(room for room in second_repository.load_state()["rooms"] if room["id"] == self.room_id)
        current_room = self.store._rooms_by_id[self.room_id]
        current_room["name"] = "instance-a-wins"
        self.store.repository.sync_room(current_room)

        stale_room["name"] = "instance-b-stale"
        with self.assertRaises(server.ConcurrentUpdateError):
            second_repository.sync_room(stale_room)

        persisted_room = next(
            room for room in second_repository.load_state()["rooms"] if room["id"] == self.room_id
        )
        self.assertEqual(persisted_room["name"], "instance-a-wins")

    def test_stale_user_revision_and_duplicate_direct_room_are_rejected(self) -> None:
        second_repository = server.NormalizedSqliteRepository(self.store.database_path)
        stale_user = next(user for user in second_repository.load_state()["users"] if user["id"] == self.alice["id"])
        current_user = self.store.get_user_record("alice")
        assert current_user is not None
        current_user["display_name"] = "instance-a-profile"
        self.store.repository.sync_user(current_user)

        stale_user["display_name"] = "instance-b-stale-profile"
        with self.assertRaises(server.ConcurrentUpdateError):
            second_repository.sync_user(stale_user)

        duplicate_room = json.loads(json.dumps(self.store._rooms_by_id[self.room_id]))
        duplicate_room["id"] = "room_deadbeef"
        duplicate_room["_revision"] = 0
        with self.assertRaises(server.sqlite3.IntegrityError):
            second_repository.sync_room(duplicate_room)

    def test_independent_read_positions_merge_without_room_overwrite(self) -> None:
        message_result = self.store.add_message(self.room_id, "alice", "shared read cursor")
        assert message_result is not None
        message_id = message_result[0]["id"]
        second_repository = server.NormalizedSqliteRepository(self.store.database_path)
        self.store.repository.sync_read_position(self.room_id, self.alice["id"], message_id)
        second_repository.sync_read_position(self.room_id, self.bob["id"], message_id)

        persisted_room = next(
            room for room in second_repository.load_state()["rooms"] if room["id"] == self.room_id
        )
        self.assertEqual(
            persisted_room["last_read_by"],
            {self.alice["id"]: message_id, self.bob["id"]: message_id},
        )

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
        self.assertEqual(self.store.state["messages"].get(self.room_id, []), [])

    def test_message_search_finds_room_people_content_and_returns_target_window(self) -> None:
        self.store.update_profile("bob", "Robert", "😀", self.bob["friend_code"], None)
        messages = []
        for index in range(40):
            result = self.store.add_message(
                self.room_id,
                "alice" if index % 2 == 0 else "bob",
                "needle in history" if index == 7 else f"ordinary-{index}",
            )
            self.assertIsNotNone(result)
            assert result is not None
            messages.append(result[0])

        content_results = self.store.search_messages("alice", "needle", limit=10)
        self.assertIsNotNone(content_results)
        assert content_results is not None
        content_item = next(item for item in content_results["items"] if item["kind"] == "message")
        self.assertEqual(content_item["room"]["id"], self.room_id)
        self.assertEqual(content_item["message"]["id"], messages[7]["id"])

        person_results = self.store.search_messages("alice", "Robert", limit=10)
        self.assertIsNotNone(person_results)
        assert person_results is not None
        self.assertTrue(any(item["kind"] == "room" and item["room"]["id"] == self.room_id for item in person_results["items"]))
        self.assertEqual(self.store.search_messages("eve", "needle", limit=10), {"items": []})

        around = self.store.get_messages_page(
            self.room_id,
            "alice",
            limit=10,
            around=messages[7]["id"],
        )
        self.assertIsNotNone(around)
        assert around is not None
        self.assertIn(messages[7]["id"], {message["id"] for message in around["items"]})
        self.assertEqual(around["around"], messages[7]["id"])

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

    def test_session_validation_is_reused_for_bursty_authenticated_requests(self) -> None:
        sessions = server.SessionStore(state_store=self.store)
        token = sessions.create("alice")
        token_hash = server.hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.store._session_validation_cache.clear()

        original = self.store.repository.session_username
        with mock.patch.object(self.store.repository, "session_username", wraps=original) as lookup:
            self.assertEqual(self.store.get_session_username(token_hash, server.SESSION_TTL_SECONDS), "alice")
            self.assertEqual(self.store.get_session_username(token_hash, server.SESSION_TTL_SECONDS), "alice")

        self.assertEqual(lookup.call_count, 1)

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
        with tempfile.TemporaryDirectory(prefix="colorless-presence-") as temp_dir:
            repository = server.NormalizedSqliteRepository(Path(temp_dir) / "presence.sqlite3")
            presence = server.PresenceStore(repository, "test-presence")
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
