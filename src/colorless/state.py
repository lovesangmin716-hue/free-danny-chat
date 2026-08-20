from __future__ import annotations

import copy
import hmac
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from .config import (
    AGE_GROUPS,
    APP_NAME,
    CLIENT_MESSAGE_ID_PATTERN,
    FRIEND_CODE_PATTERN,
    GENDERS,
    MAX_GROUP_PARTICIPANTS,
    MAX_MESSAGES_PER_ROOM,
    MAX_SESSIONS,
    MAX_SHORTS_SEEN_IDS,
    MIN_GROUP_PARTICIPANTS,
    SESSION_CLEANUP_INTERVAL_SECONDS,
    SESSION_REFRESH_THRESHOLD_SECONDS,
    SESSION_VALIDATION_CACHE_SECONDS,
    SUPABASE_ENABLED,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_STATE_ID,
    SUPABASE_STATE_TABLE,
    SUPABASE_URL,
)
from .integrations import fetch_json, supabase_headers
from .persistence import NormalizedSqliteRepository, NormalizedSupabaseRepository
from .profile_art import (
    is_blank_profile_pixels,
    pack_profile_pixels,
    profile_art_png,
    unpack_profile_pixels,
)
from .utils import (
    blank_profile_pixels,
    build_status_message,
    decode_page_cursor,
    encode_page_cursor,
    hash_password,
    mask_phone,
    new_friend_code,
    new_id,
    normalize_custom_palette,
    normalize_friend_code,
    normalize_phone,
    normalize_profile_image_url,
    normalize_profile_pixels,
    normalize_room_image_url,
    sanitize_username_seed,
    saved_activity_emoji,
    utc_now_iso,
    valid_hex_color,
    valid_profile_pixels,
)


class StateStore:
    def __init__(self, path: Path, presence=None) -> None:
        self.path = path
        self.presence = presence
        self.database_path = path.with_suffix(f"{path.suffix}.sqlite3")
        self.repository = (
            NormalizedSupabaseRepository(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
            if SUPABASE_ENABLED
            else NormalizedSqliteRepository(self.database_path)
        )
        self._normalized_ready = self.repository.is_legacy_imported()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._persist_condition = threading.Condition(self.lock)
        self._persist_event = threading.Event()
        self._persist_stop = threading.Event()
        self._revision = 0
        self._persisted_revision = 0
        self._persist_error: Exception | None = None
        self._pending_parts: set[str] = set()
        self._session_cleanup_deadline = 0.0
        self._session_validation_cache: dict[str, tuple[str, float]] = {}
        self._session_validation_versions: dict[str, int] = {}
        self._supabase_legacy_mode = False
        self.state = self._load_state()
        self._rebuild_indexes_locked()
        self._persist_thread = threading.Thread(
            target=self._persist_worker,
            name="chat-state-writer",
            daemon=True,
        )
        self._persist_thread.start()

    def bind_presence(self, presence) -> None:
        self.presence = presence

    def _default_rooms(self) -> list[dict]:
        created_at = utc_now_iso()
        rooms = [
            self._new_room("lobby", "로비", "처음 인사를 나누는 기본 채팅방입니다.", "system", created_at),
            self._new_room("study", "스터디", "프로젝트와 공부 이야기를 모아두는 공간입니다.", "system", created_at),
            self._new_room("random", "자유", "가볍게 대화하고 근황을 나누는 공간입니다.", "system", created_at),
        ]
        for room in rooms:
            room["kind"] = "public"
            room["is_public"] = True
        return rooms

    def _default_state(self) -> dict:
        rooms = self._default_rooms()
        return {
            "users": [],
            "friendships": [],
            "rooms": rooms,
            "messages": {room["id"]: [] for room in rooms},
            "shorts_feeds": {},
            "sessions": {},
        }

    def _new_room(
        self,
        room_id: str,
        name: str,
        description: str,
        created_by: str,
        created_at: str | None = None,
    ) -> dict:
        timestamp = created_at or utc_now_iso()
        return {
            "id": room_id,
            "name": name,
            "description": description,
            "image_url": "",
            "image_thumbnail_url": "",
            "image_version": 0,
            "created_by": created_by,
            "created_at": timestamp,
            "updated_at": timestamp,
            "kind": "group",
            "participant_ids": [],
            "is_public": False,
            "last_read_by": {},
        }

    def _write_legacy_supabase_state(self, state: dict) -> None:
        payload = json.dumps(
            [{"id": SUPABASE_STATE_ID, "state": state}],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = supabase_headers("application/json")
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        fetch_json(
            f"{SUPABASE_URL}/rest/v1/{SUPABASE_STATE_TABLE}?on_conflict=id",
            method="POST",
            headers=headers,
            data=payload,
        )

    def _write_state(self, parts: dict[str, object], full_state: dict | None = None) -> None:
        if self._normalized_ready:
            return
        if SUPABASE_ENABLED:
            if full_state is None:
                with self.lock:
                    full_state = copy.deepcopy(self.state)
            if self._supabase_legacy_mode:
                self._write_legacy_supabase_state(full_state)
                return
            payload = json.dumps(
                [{"id": part_id, "state": value} for part_id, value in parts.items()],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            headers = supabase_headers("application/json")
            headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
            try:
                fetch_json(
                    f"{SUPABASE_URL}/rest/v1/{SUPABASE_STATE_TABLE}?on_conflict=id",
                    method="POST",
                    headers=headers,
                    data=payload,
                )
            except ValueError as error:
                if "app_state_id_check" not in str(error):
                    raise
                self._supabase_legacy_mode = True
                self._write_legacy_supabase_state(full_state)
            return

        database = sqlite3.connect(self.database_path, timeout=15)
        try:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("PRAGMA synchronous=NORMAL")
            database.execute(
                "CREATE TABLE IF NOT EXISTS state_parts (id TEXT PRIMARY KEY, state_json TEXT NOT NULL)"
            )
            with database:
                database.executemany(
                    "INSERT INTO state_parts(id, state_json) VALUES(?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET state_json=excluded.state_json",
                    [
                        (part_id, json.dumps(value, ensure_ascii=False, separators=(",", ":")))
                        for part_id, value in parts.items()
                    ],
                )
        finally:
            database.close()

    @staticmethod
    def _state_to_parts(state: dict) -> dict[str, object]:
        parts: dict[str, object] = {
            "users": state["users"],
            "friendships": state["friendships"],
            "rooms": state["rooms"],
            "sessions": state["sessions"],
        }
        for username, feed in state["shorts_feeds"].items():
            parts[f"shorts:{username}"] = feed
        for room_id, messages in state["messages"].items():
            parts[f"messages:{room_id}"] = messages
        return parts

    def _state_part_locked(self, part_id: str) -> object:
        if part_id.startswith("messages:"):
            return self.state["messages"].get(part_id.removeprefix("messages:"), [])
        if part_id.startswith("shorts:"):
            return self.state["shorts_feeds"].get(part_id.removeprefix("shorts:"), {})
        return self.state[part_id]

    def _register_user_locked(self, user: dict) -> None:
        self._users_by_username[user["username"]] = user
        self._users_by_id[user["id"]] = user
        if user.get("friend_code"):
            self._users_by_friend_code[user["friend_code"]] = user
        if user.get("provider_user_id"):
            self._users_by_social_key[(user.get("auth_provider", "local"), user["provider_user_id"])] = user

    def _register_friendship_locked(self, first_id: str, second_id: str) -> None:
        pair = tuple(sorted((first_id, second_id)))
        self._friendship_pairs.add(pair)
        self._friend_ids_by_user.setdefault(first_id, set()).add(second_id)
        self._friend_ids_by_user.setdefault(second_id, set()).add(first_id)

    def _register_room_locked(self, room: dict) -> None:
        self._rooms_by_id[room["id"]] = room
        participant_ids = room.get("participant_ids", [])
        for user_id in participant_ids:
            self._room_ids_by_user.setdefault(user_id, set()).add(room["id"])
        if room.get("kind") == "direct" and len(participant_ids) == 2:
            self._direct_rooms_by_pair[tuple(sorted(participant_ids))] = room

    def _rebuild_indexes_locked(self) -> None:
        self._users_by_username = {user["username"]: user for user in self.state["users"]}
        self._users_by_id = {user["id"]: user for user in self.state["users"]}
        self._users_by_friend_code = {
            user["friend_code"]: user for user in self.state["users"] if user.get("friend_code")
        }
        self._users_by_social_key = {
            (user.get("auth_provider", "local"), user.get("provider_user_id", "")): user
            for user in self.state["users"]
            if user.get("provider_user_id")
        }
        self._rooms_by_id = {room["id"]: room for room in self.state["rooms"]}
        self._friend_ids_by_user: dict[str, set[str]] = {}
        self._friendship_pairs: set[tuple[str, str]] = set()
        for friendship in self.state["friendships"]:
            user_ids = sorted(friendship.get("user_ids", []))
            if len(user_ids) != 2:
                continue
            first_id, second_id = user_ids
            self._friendship_pairs.add((first_id, second_id))
            self._friend_ids_by_user.setdefault(first_id, set()).add(second_id)
            self._friend_ids_by_user.setdefault(second_id, set()).add(first_id)

        self._room_ids_by_user: dict[str, set[str]] = {}
        self._direct_rooms_by_pair: dict[tuple[str, str], dict] = {}
        for room in self.state["rooms"]:
            participant_ids = room.get("participant_ids", [])
            for user_id in participant_ids:
                self._room_ids_by_user.setdefault(user_id, set()).add(room["id"])
            if room.get("kind") == "direct" and len(participant_ids) == 2:
                self._direct_rooms_by_pair[tuple(sorted(participant_ids))] = room
        self._profile_images = {
            Path(image_url).name
            for user in self.state["users"]
            for image_url in (
                normalize_profile_image_url(user.get("profile_image_url")),
                normalize_profile_image_url(user.get("profile_thumbnail_url")),
            )
            if image_url
        }
        self._room_images = {
            Path(image_url).name: room["id"]
            for room in self.state["rooms"]
            for image_url in (
                normalize_room_image_url(room.get("image_url")),
                normalize_room_image_url(room.get("image_thumbnail_url")),
            )
            if image_url
        }
        self._attachment_rooms: dict[str, set[str]] = {}
        self._messages_by_client_id: dict[tuple[str, str, str], dict] = {}
        for room_id, messages in self.state["messages"].items():
            for message in messages:
                client_message_id = str(message.get("client_message_id", ""))
                message_username = str(message.get("username", ""))
                if CLIENT_MESSAGE_ID_PATTERN.fullmatch(client_message_id) and message_username:
                    self._messages_by_client_id[(room_id, message_username, client_message_id)] = message
                attachment = message.get("attachment")
                if not isinstance(attachment, dict):
                    continue
                filename = Path(str(attachment.get("url", ""))).name
                if filename:
                    self._attachment_rooms.setdefault(filename, set()).add(room_id)

    def _persist_worker(self) -> None:
        while True:
            self._persist_event.wait()
            if not self._persist_stop.is_set():
                time.sleep(0.05)

            with self.lock:
                if self._persisted_revision >= self._revision:
                    self._persist_event.clear()
                    if self._persist_stop.is_set():
                        return
                    continue
                revision = self._revision
                pending_parts = self._pending_parts
                self._pending_parts = set()
                snapshot = {
                    part_id: copy.deepcopy(self._state_part_locked(part_id))
                    for part_id in pending_parts
                }

            try:
                self._write_state(snapshot)
            except Exception as error:
                with self.lock:
                    self._pending_parts.update(pending_parts)
                    self._persist_error = error
                    self._persist_condition.notify_all()
                if self._persist_stop.wait(0.5):
                    return
                continue

            with self.lock:
                self._persisted_revision = max(self._persisted_revision, revision)
                self._persist_error = None
                if self._persisted_revision >= self._revision:
                    self._persist_event.clear()
                self._persist_condition.notify_all()
                if self._persist_stop.is_set() and self._persisted_revision >= self._revision:
                    return

    def flush(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        with self.lock:
            target_revision = self._revision
            while self._persisted_revision < target_revision:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._persist_error is not None:
                    return False
                self._persist_condition.wait(remaining)
            return True

    def close(self, timeout: float = 5.0) -> bool:
        flushed = self.flush(timeout)
        self._persist_stop.set()
        self._persist_event.set()
        self._persist_thread.join(timeout)
        return flushed and not self._persist_thread.is_alive()

    def _migrate_state(self, state: dict) -> dict:
        state.setdefault("users", [])
        state.setdefault("friendships", [])
        state.setdefault("rooms", [])
        state.setdefault("messages", {})
        state.setdefault("shorts_feeds", {})
        state.setdefault("sessions", {})

        if (
            not isinstance(state["users"], list)
            or not isinstance(state["friendships"], list)
            or not isinstance(state["rooms"], list)
            or not isinstance(state["messages"], dict)
            or not isinstance(state["shorts_feeds"], dict)
            or not isinstance(state["sessions"], dict)
        ):
            return self._default_state()

        for user in state["users"]:
            user.setdefault("id", new_id("user"))
            user.setdefault("display_name", user.get("username", ""))
            user.setdefault("status_message", "")
            user.setdefault("phone", "")
            user.setdefault("auth_provider", "local")
            user.setdefault("provider_user_id", "")
            user.setdefault("created_at", utc_now_iso())
            user.setdefault("age_group", "")
            user.setdefault("gender", "")
            legacy_pixels = user.get("profile_pixels")
            if valid_profile_pixels(legacy_pixels) or isinstance(legacy_pixels, str):
                normalized_pixels = normalize_profile_pixels(legacy_pixels)
                user["profile_pixels_blank"] = is_blank_profile_pixels(normalized_pixels)
                legacy_art_version = user.get("profile_art_version", 1)
                user["profile_art_version"] = (
                    0
                    if user["profile_pixels_blank"]
                    else max(1, legacy_art_version if isinstance(legacy_art_version, int) else 1)
                )
                if self._normalized_ready:
                    user.pop("profile_pixels", None)
                else:
                    user["profile_pixels"] = normalized_pixels
            else:
                user.pop("profile_pixels", None)
                user["profile_pixels_blank"] = bool(user.get("profile_pixels_blank", True))
                profile_art_version = user.get("profile_art_version", 0)
                user["profile_art_version"] = (
                    profile_art_version
                    if isinstance(profile_art_version, int) and profile_art_version >= 0
                    else 0
                )
            user["profile_image_url"] = normalize_profile_image_url(user.get("profile_image_url"))
            user["profile_thumbnail_url"] = normalize_profile_image_url(user.get("profile_thumbnail_url"))
            profile_image_version = user.get("profile_image_version", 0)
            user["profile_image_version"] = profile_image_version if isinstance(profile_image_version, int) and profile_image_version >= 0 else 0
            user["custom_palette"] = normalize_custom_palette(user.get("custom_palette"))

        used_friend_codes: set[str] = set()
        for user in state["users"]:
            raw_friend_code = str(user.get("friend_code", "")).strip()
            friend_code = raw_friend_code.upper() if raw_friend_code.upper().startswith("CL-") else normalize_friend_code(raw_friend_code)
            while not FRIEND_CODE_PATTERN.fullmatch(friend_code) or friend_code in used_friend_codes:
                friend_code = new_friend_code()
            user["friend_code"] = friend_code
            used_friend_codes.add(friend_code)

        user_ids = {user["id"] for user in state["users"]}
        state["friendships"] = [
            friendship
            for friendship in state["friendships"]
            if isinstance(friendship, dict)
            and isinstance(friendship.get("user_ids"), list)
            and len(friendship["user_ids"]) == 2
            and all(user_id in user_ids for user_id in friendship["user_ids"])
        ]

        defaults_by_id = {room["id"]: room for room in self._default_rooms()}
        users_by_name = {user["username"]: user for user in state["users"]}
        for room in state["rooms"]:
            room.setdefault("description", "")
            room["image_url"] = normalize_room_image_url(room.get("image_url"))
            room["image_thumbnail_url"] = normalize_room_image_url(room.get("image_thumbnail_url"))
            image_version = room.get("image_version", 0)
            room["image_version"] = image_version if isinstance(image_version, int) and image_version >= 0 else 0
            room.setdefault("created_by", "system")
            room.setdefault("created_at", utc_now_iso())
            room.setdefault("updated_at", room["created_at"])
            room.setdefault("kind", "public" if room["created_by"] == "system" else "group")
            room.setdefault("is_public", room["created_by"] == "system")
            room.setdefault("last_read_by", {})
            if not isinstance(room.get("last_read_by"), dict):
                room["last_read_by"] = {}
            if not isinstance(room.get("participant_ids"), list):
                room["participant_ids"] = []
            room["participant_ids"] = list(dict.fromkeys(
                user_id
                for user_id in room["participant_ids"]
                if isinstance(user_id, str) and user_id in user_ids
            ))[:MAX_GROUP_PARTICIPANTS]
            if not room["participant_ids"] and not room["is_public"] and not room.get("archived_at"):
                creator = users_by_name.get(room["created_by"])
                room["participant_ids"] = [creator["id"]] if creator else []
            room["last_read_by"] = {
                user_id: message_id
                for user_id, message_id in room["last_read_by"].items()
                if user_id in room["participant_ids"] and isinstance(message_id, str)
            }
            state["messages"].setdefault(room["id"], [])
            default_room = defaults_by_id.get(room.get("id"))
            if default_room and room.get("created_by") == "system":
                room["name"] = default_room["name"]
                room["description"] = default_room["description"]
                room["kind"] = "public"
                room["is_public"] = True

        if not state["rooms"]:
            return self._default_state()

        for room_id, messages in list(state["messages"].items()):
            if not isinstance(messages, list):
                state["messages"][room_id] = []
                continue
            state["messages"][room_id] = [
                message for message in messages if isinstance(message, dict)
            ][-MAX_MESSAGES_PER_ROOM:]
            for message in state["messages"][room_id]:
                client_message_id = str(message.get("client_message_id", ""))
                if client_message_id and not CLIENT_MESSAGE_ID_PATTERN.fullmatch(client_message_id):
                    message.pop("client_message_id", None)

        valid_usernames = {user["username"] for user in state["users"]}
        now = time.time()
        sessions = {
            token_hash: {
                "username": str(session["username"]),
                "created_at": float(session["created_at"]),
                "expires_at": float(session["expires_at"]),
            }
            for token_hash, session in state["sessions"].items()
            if isinstance(token_hash, str)
            and re.fullmatch(r"[0-9a-f]{64}", token_hash)
            and isinstance(session, dict)
            and session.get("username") in valid_usernames
            and isinstance(session.get("created_at"), (int, float))
            and isinstance(session.get("expires_at"), (int, float))
            and float(session["expires_at"]) > now
        }
        state["sessions"] = dict(
            sorted(sessions.items(), key=lambda item: item[1]["created_at"])[-MAX_SESSIONS:]
        )
        state["shorts_feeds"] = {
            username: {
                "next_cursor": str(feed.get("next_cursor", ""))[:200],
                "seen_ids": [
                    video_id
                    for video_id in feed.get("seen_ids", [])
                    if isinstance(video_id, str) and 1 <= len(video_id) <= 64
                ][-MAX_SHORTS_SEEN_IDS:],
            }
            for username, feed in state["shorts_feeds"].items()
            if username in valid_usernames and isinstance(feed, dict) and isinstance(feed.get("seen_ids", []), list)
        }

        return state

    def _state_from_parts(self, parts: dict[str, object]) -> dict | None:
        if not {"users", "friendships", "rooms", "sessions"}.issubset(parts):
            return None
        state = {
            "users": parts["users"],
            "friendships": parts["friendships"],
            "rooms": parts["rooms"],
            "messages": {},
            "shorts_feeds": {},
            "sessions": parts["sessions"],
        }
        for part_id, value in parts.items():
            if part_id.startswith("messages:"):
                state["messages"][part_id.removeprefix("messages:")] = value
            elif part_id.startswith("shorts:"):
                state["shorts_feeds"][part_id.removeprefix("shorts:")] = value
        return self._migrate_state(state)

    def _load_persisted_parts(self) -> tuple[dict[str, object], dict | None]:
        if SUPABASE_ENABLED:
            rows = fetch_json(
                f"{SUPABASE_URL}/rest/v1/{SUPABASE_STATE_TABLE}?select=id,state",
                headers=supabase_headers(),
            )
            if not isinstance(rows, list):
                return {}, None
            parts: dict[str, object] = {}
            legacy_state = None
            for row in rows:
                if not isinstance(row, dict):
                    continue
                part_id = row.get("id")
                value = row.get("state")
                if part_id == SUPABASE_STATE_ID and isinstance(value, dict):
                    legacy_state = value
                elif isinstance(part_id, str):
                    parts[part_id] = value
            return parts, legacy_state

        if not self.database_path.exists():
            return {}, None
        try:
            database = sqlite3.connect(self.database_path, timeout=15)
            try:
                if self._normalized_ready:
                    rows = database.execute(
                        "SELECT id, state_json FROM state_parts WHERE id NOT LIKE 'messages:%'"
                    ).fetchall()
                else:
                    rows = database.execute("SELECT id, state_json FROM state_parts").fetchall()
            finally:
                database.close()
        except (sqlite3.Error, OSError):
            return {}, None
        parts = {}
        for part_id, state_json in rows:
            try:
                parts[str(part_id)] = json.loads(state_json)
            except (TypeError, json.JSONDecodeError):
                continue
        return parts, None

    def _load_legacy_state(self, legacy_state: dict | None) -> dict:
        if legacy_state is not None:
            return self._migrate_state(legacy_state)
        if not self.path.exists():
            return self._default_state()
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self._default_state()
        return self._migrate_state(state) if isinstance(state, dict) else self._default_state()

    def _load_state(self) -> dict:
        if self._normalized_ready:
            state = self._migrate_state(self.repository.load_state())
            state["messages"] = {}
            return state
        parts, legacy_state = self._load_persisted_parts()
        state = self._state_from_parts(parts)
        if state is not None:
            self.repository.import_legacy_state(state)
            self._normalized_ready = True
            imported_state = self._migrate_state(self.repository.load_state())
            imported_state["messages"] = {}
            return imported_state

        state = self._load_legacy_state(legacy_state)
        self._write_state(self._state_to_parts(state), state)
        self.repository.import_legacy_state(state)
        self._normalized_ready = True
        imported_state = self._migrate_state(self.repository.load_state())
        imported_state["messages"] = {}
        return imported_state

    def refresh_from_repository(self) -> None:
        refreshed = self._migrate_state(self.repository.load_state())
        refreshed["messages"] = {}
        with self.lock:
            self.state = refreshed
            self._rebuild_indexes_locked()

    def _room_messages_locked(self, room_id: str, *, limit: int = MAX_MESSAGES_PER_ROOM, before: str = "") -> list[dict]:
        if self.repository is not None:
            return self.repository.list_messages(room_id, limit=limit, before=before)
        messages = self.state["messages"].get(room_id, [])
        if before:
            end = next((index for index, message in enumerate(messages) if message.get("id") == before), 0)
            return messages[max(0, end - limit):end]
        return messages[-limit:]

    def _save_locked(self, *part_ids: str) -> None:
        if not part_ids:
            part_ids = tuple(self._state_to_parts(self.state))
        self._pending_parts.update(part_ids)
        self._revision += 1
        self._persist_event.set()

    def _cleanup_sessions_locked(self, now: float, max_sessions: int) -> bool:
        sessions = self.state["sessions"]
        expired = [
            token_hash
            for token_hash, session in sessions.items()
            if not isinstance(session, dict) or float(session.get("expires_at", 0)) <= now
        ]
        for token_hash in expired:
            sessions.pop(token_hash, None)
            self._session_validation_cache.pop(token_hash, None)

        overflow = len(sessions) - max_sessions
        if overflow > 0:
            oldest = sorted(sessions, key=lambda token_hash: float(sessions[token_hash].get("created_at", 0)))[:overflow]
            for token_hash in oldest:
                sessions.pop(token_hash, None)
                self._session_validation_cache.pop(token_hash, None)
        self._session_cleanup_deadline = now + SESSION_CLEANUP_INTERVAL_SECONDS
        return bool(expired or overflow > 0)

    def create_session(self, token_hash: str, username: str, ttl_seconds: int, max_sessions: int) -> None:
        now = time.time()
        with self.lock:
            changed = self._cleanup_sessions_locked(now, max_sessions) if now >= self._session_cleanup_deadline else False
            self.state["sessions"][token_hash] = {
                "username": username,
                "created_at": now,
                "expires_at": now + ttl_seconds,
            }
            self._session_validation_versions[token_hash] = self._session_validation_versions.get(token_hash, 0) + 1
            self._session_validation_cache[token_hash] = (username, now + SESSION_VALIDATION_CACHE_SECONDS)
            if len(self.state["sessions"]) > max_sessions:
                changed = self._cleanup_sessions_locked(now, max_sessions) or changed
            user = self._users_by_username.get(username)
            if self.repository is not None and user is not None:
                self.repository.create_session(token_hash, user["id"], now, now + ttl_seconds, max_sessions)
            self._save_locked("sessions")

    def get_session_username(self, token_hash: str, ttl_seconds: int) -> str | None:
        now = time.time()
        repository = self.repository
        with self.lock:
            if repository is not None:
                cached = self._session_validation_cache.get(token_hash)
                if cached is not None and cached[1] > now:
                    return cached[0]
                validation_version = self._session_validation_versions.get(token_hash, 0)
            else:
                validation_version = 0

        if repository is not None:
            username = repository.session_username(token_hash, now)
            with self.lock:
                if self._session_validation_versions.get(token_hash, 0) != validation_version:
                    cached = self._session_validation_cache.get(token_hash)
                    return cached[0] if cached is not None and cached[1] > now else None
                if username is None:
                    self.state["sessions"].pop(token_hash, None)
                    self._session_validation_cache.pop(token_hash, None)
                    return None
                self._session_validation_cache[token_hash] = (
                    username,
                    now + SESSION_VALIDATION_CACHE_SECONDS,
                )
                session = self.state["sessions"].get(token_hash)
                if session is None:
                    return username
                refresh_threshold = min(SESSION_REFRESH_THRESHOLD_SECONDS, max(1, ttl_seconds // 2))
                if float(session["expires_at"]) - now <= refresh_threshold:
                    session["expires_at"] = now + ttl_seconds
                    repository.refresh_session(token_hash, session["expires_at"])
                return username

        with self.lock:
            changed = self._cleanup_sessions_locked(now, MAX_SESSIONS) if now >= self._session_cleanup_deadline else False
            session = self.state["sessions"].get(token_hash)
            if session is None or float(session.get("expires_at", 0)) <= now:
                if session is not None:
                    self.state["sessions"].pop(token_hash, None)
                    changed = True
                if changed:
                    self._save_locked("sessions")
                return None
            username = str(session.get("username", ""))
            if username not in self._users_by_username:
                self.state["sessions"].pop(token_hash, None)
                self._save_locked("sessions")
                return None
            refresh_threshold = min(SESSION_REFRESH_THRESHOLD_SECONDS, max(1, ttl_seconds // 2))
            if float(session["expires_at"]) - now <= refresh_threshold:
                session["expires_at"] = now + ttl_seconds
                changed = True
            if changed:
                self._save_locked("sessions")
            return username

    def destroy_session(self, token_hash: str) -> None:
        with self.lock:
            self._session_validation_versions[token_hash] = self._session_validation_versions.get(token_hash, 0) + 1
            self._session_validation_cache.pop(token_hash, None)
            if self.repository is not None:
                self.repository.destroy_session(token_hash)
            if self.state["sessions"].pop(token_hash, None) is not None:
                self._save_locked("sessions")

    def get_shorts_feed(self, username: str) -> tuple[list[str], str]:
        with self.lock:
            user = self._users_by_username.get(username)
            if self.repository is not None and user is not None:
                return self.repository.get_shorts_feed(user["id"])
            feed = self.state["shorts_feeds"].get(username, {})
            return list(feed.get("seen_ids", [])), str(feed.get("next_cursor", ""))

    def save_shorts_feed(self, username: str, seen_ids: list[str], next_cursor: str) -> None:
        bounded_seen_ids = list(dict.fromkeys(seen_ids))[-MAX_SHORTS_SEEN_IDS:]
        with self.lock:
            self.state["shorts_feeds"][username] = {
                "seen_ids": bounded_seen_ids,
                "next_cursor": next_cursor[:200],
            }
            user = self._users_by_username.get(username)
            if self.repository is not None and user is not None:
                self.repository.save_shorts_feed(user["id"], bounded_seen_ids, next_cursor[:200])
            self._save_locked(f"shorts:{username}")

    def _user_public(self, user: dict) -> dict:
        provider = user.get("auth_provider", "local")
        profile_image_url = normalize_profile_image_url(user.get("profile_image_url"))
        profile_thumbnail_url = normalize_profile_image_url(user.get("profile_thumbnail_url"))
        profile_image_version = user.get("profile_image_version", 0)
        profile_art_version = int(user.get("profile_art_version", 0))
        art_thumbnail_url = (
            f"/profile-art/{user['id']}/thumbnail?v={profile_art_version}"
            if profile_art_version > 0 and not user.get("profile_pixels_blank", True)
            else ""
        )
        return {
            "id": user["id"],
            "username": user["username"],
            "friend_code": user["friend_code"],
            "display_name": user.get("display_name") or user["username"],
            "status_message": user.get("status_message", ""),
            "phone_masked": mask_phone(user.get("phone", "")),
            "auth_provider": provider,
            "auth_provider_label": {
                "local": "비밀번호 계정",
                "kakao": "카카오",
                "google": "구글",
                "demo": "개발용 SNS",
            }.get(provider, provider),
            "created_at": user["created_at"],
            "profile_image_url": f"{profile_image_url}?v={profile_image_version}" if profile_image_url else "",
            "profile_thumbnail_url": (
                f"{profile_thumbnail_url}?v={profile_image_version}" if profile_thumbnail_url else art_thumbnail_url
            ),
            "profile_art_version": profile_art_version,
            "custom_palette": user.get("custom_palette", []),
        }

    def _user_list_summary(self, user: dict) -> dict:
        public = self._user_public(user)
        for field in ("profile_pixels", "custom_palette", "phone_masked", "auth_provider", "auth_provider_label"):
            public.pop(field, None)
        public["revision"] = int(user.get("_revision", 0))
        return public

    def _presence_for_user(self, user: dict) -> dict:
        presence = (
            self.presence.for_user(user["username"])
            if self.presence is not None
            else self.repository.presence_for_users([user["username"]]).get(
                user["username"], {"online": False, "active_room_ids": [], "emoji": ""}
            )
        )
        saved_emoji = saved_activity_emoji(user.get("status_message"))
        if presence["online"] and saved_emoji:
            presence["emoji"] = saved_emoji
        return presence

    def _presences_for_users(self, users: list[dict]) -> dict[str, dict]:
        if not users:
            return {}
        presences = self.repository.presence_for_users([user["username"] for user in users])
        for user in users:
            presence = presences.setdefault(
                user["username"], {"online": False, "active_room_ids": [], "emoji": ""}
            )
            saved_emoji = saved_activity_emoji(user.get("status_message"))
            if presence.get("online") and saved_emoji:
                presence["emoji"] = saved_emoji
        return presences

    def _room_summary(
        self,
        room: dict,
        viewer: dict | None = None,
        *,
        include_members: bool = True,
        latest_message: dict | None = None,
        latest_message_loaded: bool = False,
    ) -> dict:
        messages = [latest_message] if latest_message_loaded and latest_message is not None else (
            [] if latest_message_loaded else self._room_messages_locked(room["id"], limit=1)
        )
        image_url = normalize_room_image_url(room.get("image_url"))
        image_thumbnail_url = normalize_room_image_url(room.get("image_thumbnail_url"))
        image_version = room.get("image_version", 0)
        participant_count = len(room.get("participant_ids", []))
        if not participant_count:
            participants = {message["username"] for message in messages if message.get("username")}
            if room.get("created_by") and room["created_by"] != "system":
                participants.add(room["created_by"])
            participant_count = len(participants)
        last_message = messages[-1] if messages else None
        summary = {
            "id": room["id"],
            "name": room["name"],
            "description": room["description"],
            "image_url": f"{image_url}?v={image_version}" if image_url else "",
            "image_thumbnail_url": f"{image_thumbnail_url}?v={image_version}" if image_thumbnail_url else "",
            "created_by": room["created_by"],
            "created_at": room["created_at"],
            "updated_at": room["updated_at"],
            "kind": room.get("kind", "group"),
            "participant_count": participant_count,
            "message_count": len(messages),
            "last_message": last_message,
            "revision": int(room.get("_revision", 0)),
            "unread_count": 1 if (
                viewer is not None
                and last_message is not None
                and last_message.get("username") != viewer.get("username")
                and room.get("last_read_by", {}).get(viewer.get("id")) != last_message.get("id")
            ) else 0,
        }
        if room.get("kind") == "direct" and viewer is not None:
            peer_id = next((user_id for user_id in room.get("participant_ids", []) if user_id != viewer["id"]), "")
            peer = self._users_by_id.get(peer_id)
            if peer is not None:
                summary["name"] = peer.get("display_name") or peer["username"]
                peer_public = self._user_public(peer)
                peer_public.pop("profile_pixels", None)
                peer_public.pop("custom_palette", None)
                summary["peer"] = peer_public
                summary["peer"]["presence"] = self._presence_for_user(peer)
        elif room.get("kind") == "group" and viewer is not None and include_members:
            summary["participants"] = [
                {
                    "id": participant["id"],
                    "username": participant["username"],
                    "display_name": participant.get("display_name") or participant["username"],
                    "profile_thumbnail_url": self._user_public(participant)["profile_thumbnail_url"],
                }
                for user_id in room.get("participant_ids", [])
                if (participant := self._users_by_id.get(user_id)) is not None
            ]
        return summary

    def get_user_record(self, username: str) -> dict | None:
        with self.lock:
            user = self._users_by_username.get(username)
            return dict(user) if user is not None else None

    def get_user_public(self, username: str) -> dict | None:
        with self.lock:
            user = self._users_by_username.get(username)
            return self._user_public(user) if user is not None else None

    def get_profile_pixels(self, username: str) -> dict | None:
        with self.lock:
            user = self._users_by_username.get(username)
            if user is None:
                return None
            user_id = user["id"]
        stored = self.repository.load_profile_art(user_id)
        if stored is None:
            return {"pixels": blank_profile_pixels(), "version": 0}
        version, packed = stored
        return {"pixels": unpack_profile_pixels(packed), "version": version}

    def get_profile_art_thumbnail(self, user_id: str) -> tuple[int, bytes] | None:
        with self.lock:
            if user_id not in self._users_by_id:
                return None
        stored = self.repository.load_profile_art(user_id)
        if stored is None:
            return None
        version, packed = stored
        return version, profile_art_png(packed)

    def _save_profile_art_locked(self, user: dict, pixels: object) -> None:
        normalized = normalize_profile_pixels(pixels)
        blank = is_blank_profile_pixels(normalized)
        version = 0 if blank else time.time_ns()
        self.repository.save_profile_art(
            user["id"],
            None if blank else pack_profile_pixels(normalized),
            version,
        )
        user.pop("profile_pixels", None)
        user["profile_pixels_blank"] = blank
        user["profile_art_version"] = version

    def update_profile_pixels(self, username: str, pixels: object) -> dict | None:
        if not valid_profile_pixels(pixels):
            return None
        with self.lock:
            user = self._users_by_username.get(username)
            if user is None:
                return None
            self._save_profile_art_locked(user, pixels)
            if self.repository is not None:
                self.repository.sync_user(user)
            self._save_locked("users")
            return self._user_public(user)

    def update_profile_image(self, username: str, image_url: str, thumbnail_url: str = "") -> dict | None:
        normalized_url = normalize_profile_image_url(image_url)
        normalized_thumbnail_url = normalize_profile_image_url(thumbnail_url)
        if image_url and not normalized_url:
            return None
        if thumbnail_url and not normalized_thumbnail_url:
            return None
        with self.lock:
            user = self._users_by_username.get(username)
            if user is None:
                return None
            previous_filename = Path(normalize_profile_image_url(user.get("profile_image_url"))).name
            previous_thumbnail_filename = Path(normalize_profile_image_url(user.get("profile_thumbnail_url"))).name
            user["profile_image_url"] = normalized_url
            user["profile_thumbnail_url"] = normalized_thumbnail_url
            user["profile_image_version"] = time.time_ns() if normalized_url else 0
            if previous_filename:
                self._profile_images.discard(previous_filename)
            if previous_thumbnail_filename:
                self._profile_images.discard(previous_thumbnail_filename)
            if normalized_url:
                self._profile_images.add(Path(normalized_url).name)
            if normalized_thumbnail_url:
                self._profile_images.add(Path(normalized_thumbnail_url).name)
            if self.repository is not None:
                self.repository.sync_user(user)
            self._save_locked("users")
            return self._user_public(user)

    def update_profile(self, username: str, display_name: str, status_message: str, friend_code: str, pixels: object) -> tuple[dict | None, str | None]:
        normalized_display_name = display_name.strip()[:24]
        normalized_status_message = status_message.strip()[:40]
        normalized_friend_code = normalize_friend_code(friend_code)
        if len(normalized_display_name) < 2:
            return None, "이름은 2자 이상이어야 합니다."
        if pixels is not None and not valid_profile_pixels(pixels):
            return None, "프로필 픽셀 데이터가 올바르지 않습니다."
        if not FRIEND_CODE_PATTERN.fullmatch(normalized_friend_code):
            return None, "친구 ID는 영문 소문자, 숫자, 밑줄로 4~20자여야 합니다."

        with self.lock:
            user = self._users_by_username.get(username)
            if user is None:
                return None, "사용자를 찾을 수 없습니다."
            code_owner = self._users_by_friend_code.get(normalized_friend_code)
            if code_owner is not None and code_owner["username"] != username:
                return None, "이미 사용 중인 친구 ID입니다."
            previous_friend_code = user.get("friend_code", "")
            user["display_name"] = normalized_display_name
            user["status_message"] = normalized_status_message
            user["friend_code"] = normalized_friend_code
            if pixels is not None:
                self._save_profile_art_locked(user, pixels)
            if previous_friend_code != normalized_friend_code:
                self._users_by_friend_code.pop(previous_friend_code, None)
                self._users_by_friend_code[normalized_friend_code] = user
            if self.repository is not None:
                self.repository.sync_user(user)
            self._save_locked("users")
            return self._user_public(user), None

    def update_custom_palette(self, username: str, colors: object) -> tuple[dict | None, str | None]:
        if not isinstance(colors, list) or len(colors) > 10 or any(not valid_hex_color(color) for color in colors):
            return None, "나만의 팔레트는 올바른 색상 10개까지 저장할 수 있습니다."

        with self.lock:
            user = self._users_by_username.get(username)
            if user is None:
                return None, "사용자를 찾을 수 없습니다."
            user["custom_palette"] = normalize_custom_palette(colors)
            if self.repository is not None:
                self.repository.sync_user(user)
            self._save_locked("users")
            return self._user_public(user), None

    def find_social_user(self, provider: str, provider_user_id: str) -> dict | None:
        with self.lock:
            return self._users_by_social_key.get((provider, provider_user_id))

    def _unique_username_locked(self, seed: str, provider: str, provider_user_id: str) -> str:
        base_seed = sanitize_username_seed(seed) or f"{provider}_{provider_user_id[-6:]}"
        base = base_seed[:18]
        if len(base) < 2:
            base = f"{provider}_{provider_user_id[-4:]}"

        candidate = base
        index = 1
        while candidate in self._users_by_username:
            suffix = f"_{index}"
            candidate = f"{base[: max(2, 24 - len(suffix))]}{suffix}"
            index += 1
        return candidate[:24]

    def _new_friend_code_locked(self) -> str:
        friend_code = new_friend_code()
        while friend_code in self._users_by_friend_code:
            friend_code = new_friend_code()
        return friend_code

    def _user_by_id_locked(self, user_id: str) -> dict | None:
        return self._users_by_id.get(user_id)

    def _friend_ids_locked(self, user_id: str) -> set[str]:
        return set(self._friend_ids_by_user.get(user_id, set()))

    def _can_access_room_locked(self, room: dict, user: dict) -> bool:
        return bool(room.get("is_public")) or user["id"] in room.get("participant_ids", [])

    def can_access_room(self, room_id: str, username: str) -> bool:
        with self.lock:
            room = self._rooms_by_id.get(room_id)
            user = self._users_by_username.get(username)
            return room is not None and user is not None and self._can_access_room_locked(room, user)

    def list_rooms(self, viewer: dict | None = None) -> list[dict]:
        with self.lock:
            rooms = [
                self._room_summary(room, viewer)
                for room in self.state["rooms"]
                if viewer is None or self._can_access_room_locked(room, viewer)
            ]
        return sorted(rooms, key=lambda room: room["updated_at"], reverse=True)

    def get_bootstrap(self, user: dict) -> dict:
        rooms = self.list_rooms(user)
        return {
            "app_name": APP_NAME,
            "user": self._user_public(user),
            "rooms": rooms,
            "selected_room_id": rooms[0]["id"] if rooms else None,
        }

    def get_messenger_bootstrap(self, user: dict) -> dict:
        if user.get("auth_provider") == "demo":
            self.seed_demo_network(user["username"])
        with self.lock:
            friend_ids = self._friend_ids_locked(user["id"])
            raw_friends = [friend for friend_id in friend_ids if (friend := self._users_by_id.get(friend_id)) is not None]
            friends = [self._user_public(friend) for friend in raw_friends]
            for friend, raw_friend in zip(friends, raw_friends):
                friend["presence"] = self._presence_for_user(raw_friend)
            rooms = []
            for room_id in self._room_ids_by_user.get(user["id"], set()):
                room = self._rooms_by_id.get(room_id)
                if room is not None and room.get("kind") in {"direct", "group"}:
                    rooms.append(self._room_summary(room, user))
        return {
            "app_name": APP_NAME,
            "user": self._user_public(user),
            "friends": sorted(friends, key=lambda friend: friend["username"].lower()),
            "discoverable_users": [],
            "rooms": sorted(rooms, key=lambda room: room["updated_at"], reverse=True),
        }

    def current_sync_revision(self) -> int:
        return int(self.repository.latest_event_sequence()) if self.repository is not None else 0

    def get_me_summary(self, user: dict) -> dict:
        return {
            "app_name": APP_NAME,
            "user": self._user_list_summary(user),
            "revision": self.current_sync_revision(),
        }

    def get_friends_page(self, user: dict, *, limit: int, cursor: str = "") -> dict:
        cursor_key = decode_page_cursor(cursor, 2) if cursor else ()
        with self.lock:
            raw_friends = [
                friend
                for friend_id in self._friend_ids_locked(user["id"])
                if (friend := self._users_by_id.get(friend_id)) is not None
            ]
            raw_friends.sort(key=lambda friend: (friend["username"].casefold(), friend["id"]))
            if cursor_key:
                raw_friends = [
                    friend for friend in raw_friends
                    if (friend["username"].casefold(), friend["id"]) > cursor_key
                ]
            page = raw_friends[:limit + 1]
            has_more = len(page) > limit
            page = page[:limit]
            presences = self._presences_for_users(page)
            items = []
            for friend in page:
                summary = self._user_list_summary(friend)
                summary["presence"] = presences[friend["username"]]
                items.append(summary)
            next_cursor = (
                encode_page_cursor(page[-1]["username"].casefold(), page[-1]["id"])
                if has_more and page else ""
            )
        return {"items": items, "next_cursor": next_cursor, "has_more": has_more}

    def get_rooms_page(
        self,
        user: dict,
        *,
        limit: int,
        cursor: str = "",
        updated_since: str = "",
    ) -> dict:
        cursor_key = decode_page_cursor(cursor, 2) if cursor else ()
        with self.lock:
            rooms = [
                room
                for room_id in self._room_ids_by_user.get(user["id"], set())
                if (room := self._rooms_by_id.get(room_id)) is not None
                and room.get("kind") in {"direct", "group"}
                and (not updated_since or str(room.get("updated_at", "")) > updated_since)
            ]
            rooms.sort(key=lambda room: (str(room.get("updated_at", "")), room["id"]), reverse=True)
            if cursor_key:
                rooms = [
                    room for room in rooms
                    if (str(room.get("updated_at", "")), room["id"]) < cursor_key
                ]
            page = rooms[:limit + 1]
            has_more = len(page) > limit
            page = page[:limit]
            latest_messages = self.repository.latest_messages_for_rooms([room["id"] for room in page])
            items = [
                self._room_summary(
                    room,
                    user,
                    include_members=False,
                    latest_message=latest_messages.get(room["id"]),
                    latest_message_loaded=True,
                )
                for room in page
            ]
            next_cursor = (
                encode_page_cursor(str(page[-1].get("updated_at", "")), page[-1]["id"])
                if has_more and page else ""
            )
        return {"items": items, "next_cursor": next_cursor, "has_more": has_more}

    def get_room_members_page(
        self,
        room_id: str,
        user: dict,
        *,
        limit: int,
        cursor: str = "",
    ) -> dict | None:
        cursor_key = decode_page_cursor(cursor, 2) if cursor else ()
        with self.lock:
            room = self._rooms_by_id.get(room_id)
            if room is None or not self._can_access_room_locked(room, user):
                return None
            members = [
                member
                for user_id in room.get("participant_ids", [])
                if (member := self._users_by_id.get(user_id)) is not None
            ]
            members.sort(key=lambda member: (member["username"].casefold(), member["id"]))
            if cursor_key:
                members = [
                    member for member in members
                    if (member["username"].casefold(), member["id"]) > cursor_key
                ]
            page = members[:limit + 1]
            has_more = len(page) > limit
            page = page[:limit]
            items = [self._user_list_summary(member) for member in page]
            next_cursor = (
                encode_page_cursor(page[-1]["username"].casefold(), page[-1]["id"])
                if has_more and page else ""
            )
        return {"items": items, "next_cursor": next_cursor, "has_more": has_more}

    @staticmethod
    def _compact_sync_event(event: dict) -> dict:
        compact = dict(event)
        room = compact.get("room")
        if isinstance(room, dict):
            compact_room = dict(room)
            compact_room.pop("participants", None)
            peer = compact_room.get("peer")
            if isinstance(peer, dict):
                compact_peer = dict(peer)
                compact_peer.pop("profile_pixels", None)
                compact_peer.pop("custom_palette", None)
                compact_room["peer"] = compact_peer
            compact["room"] = compact_room
        return compact

    def get_sync_page(self, username: str, *, after_revision: int, limit: int) -> dict:
        current_revision = self.current_sync_revision()
        events = (
            self.repository.events_for_user_after(username, after_revision, limit=limit + 1)
            if self.repository is not None else []
        )
        has_more = len(events) > limit
        events = events[:limit]
        compact_events = [self._compact_sync_event(event) for event in events]
        last_event_revision = (
            int(compact_events[-1].get("revision", after_revision)) if compact_events else after_revision
        )
        next_revision = last_event_revision if has_more else max(current_revision, last_event_revision)
        return {
            "events": compact_events,
            "revision": max(after_revision, next_revision),
            "has_more": has_more,
        }

    def _messages_with_read_state_locked(
        self,
        room: dict,
        user: dict,
        messages: list[dict],
        *,
        all_messages: list[dict] | None = None,
    ) -> list[dict]:
        participant_ids = list(room.get("participant_ids", []))
        last_read_by = room.get("last_read_by", {})
        if all_messages is not None or self.repository is None:
            if all_messages is None:
                all_messages = self._room_messages_locked(room["id"])
            message_positions = {
                str(message["id"]): index
                for index, message in enumerate(all_messages)
            }
            reader_positions = {
                reader_id: message_positions.get(str(last_read_by.get(reader_id, "")), -1)
                for reader_id in participant_ids
            }

            def reader_has_read(reader_id: str, message: dict) -> bool:
                message_position = message_positions.get(str(message.get("id", "")), -1)
                return message_position >= 0 and reader_positions.get(reader_id, -1) >= message_position
        else:
            reader_cursor_ids = {
                reader_id: str(last_read_by.get(reader_id, ""))
                for reader_id in participant_ids
            }
            cursor_sequences = self.repository.message_sequences(
                room["id"],
                list(reader_cursor_ids.values()),
            )
            reader_sequences = {
                reader_id: cursor_sequences.get(cursor_id, -1)
                for reader_id, cursor_id in reader_cursor_ids.items()
            }

            def reader_has_read(reader_id: str, message: dict) -> bool:
                message_sequence = int(message.get("_sequence", -1))
                return message_sequence >= 0 and reader_sequences.get(reader_id, -1) >= message_sequence

        response_messages: list[dict] = []
        for message in messages:
            mine = message.get("username") == user["username"]
            sender = self._users_by_username.get(str(message.get("username", "")))
            sender_id = str(sender.get("id", "")) if sender is not None else ""
            eligible_reader_ids = [
                reader_id
                for reader_id in participant_ids
                if reader_id != sender_id
            ]
            read_by = [
                {
                    "id": reader["id"],
                    "username": reader["username"],
                    "display_name": reader.get("display_name") or reader["username"],
                }
                for reader_id in eligible_reader_ids
                if reader_has_read(reader_id, message)
                if (reader := self._users_by_id.get(reader_id)) is not None
            ]
            unread_by = [
                {
                    "id": reader["id"],
                    "username": reader["username"],
                    "display_name": reader.get("display_name") or reader["username"],
                }
                for reader_id in eligible_reader_ids
                if not reader_has_read(reader_id, message)
                if (reader := self._users_by_id.get(reader_id)) is not None
            ]
            response_message = {
                **{key: value for key, value in message.items() if key != "_sequence"},
                "read": mine and not unread_by,
                "read_by": read_by,
                "unread_by": unread_by,
            }
            response_messages.append(response_message)
        return response_messages

    def get_messages(self, room_id: str, username: str) -> list[dict] | None:
        with self.lock:
            user = self._users_by_username.get(username)
            room = self._rooms_by_id.get(room_id)
            if user is None or room is None or not self._can_access_room_locked(room, user):
                return None
            messages = self._room_messages_locked(room_id)
            return self._messages_with_read_state_locked(
                room,
                user,
                messages,
                all_messages=messages,
            )

    def sent_message_with_read_state(
        self,
        room_id: str,
        username: str,
        message: dict,
        *,
        created: bool,
    ) -> dict:
        """Build the sender response without reloading the room after a new insert."""
        if not created:
            messages = self.get_messages(room_id, username) or []
            return next(
                (candidate for candidate in reversed(messages) if candidate.get("id") == message.get("id")),
                message,
            )

        with self.lock:
            user = self._users_by_username.get(username)
            room = self._rooms_by_id.get(room_id)
            if user is None or room is None or not self._can_access_room_locked(room, user):
                return message

            response_message = {**message, "read": False}
            if room.get("kind") == "group":
                unread_by = [
                    {
                        "id": reader["id"],
                        "username": reader["username"],
                        "display_name": reader.get("display_name") or reader["username"],
                    }
                    for reader_id in room.get("participant_ids", [])
                    if reader_id != user["id"]
                    if (reader := self._users_by_id.get(reader_id)) is not None
                ]
                response_message["unread_by"] = unread_by
                response_message["read"] = not unread_by
            return response_message

    def get_messages_page(
        self,
        room_id: str,
        username: str,
        *,
        limit: int,
        before: str = "",
        around: str = "",
    ) -> dict | None:
        with self.lock:
            user = self._users_by_username.get(username)
            room = self._rooms_by_id.get(room_id)
            if user is None or room is None or not self._can_access_room_locked(room, user):
                return None
            if around:
                all_messages = self._room_messages_locked(room_id)
                target_index = next(
                    (index for index, message in enumerate(all_messages) if message.get("id") == around),
                    -1,
                )
                if target_index >= 0:
                    page_start = max(0, target_index - (limit // 2))
                    page_end = min(len(all_messages), page_start + limit)
                    page_start = max(0, page_end - limit)
                    messages = all_messages[page_start:page_end]
                    page_messages = self._messages_with_read_state_locked(
                        room,
                        user,
                        messages,
                        all_messages=all_messages,
                    )
                    return {
                        "items": page_messages,
                        "next_cursor": messages[0]["id"] if page_start > 0 and page_messages else "",
                        "around": around,
                    }
            messages = (
                self.repository.list_messages_with_sequences(room_id, limit=limit + 1, before=before)
                if self.repository is not None
                else self._room_messages_locked(room_id, limit=limit + 1, before=before)
            )
            has_more = len(messages) > limit
            if has_more:
                messages = messages[-limit:]
            page_messages = self._messages_with_read_state_locked(room, user, messages)
            return {
                "items": page_messages,
                "next_cursor": messages[0]["id"] if has_more and page_messages else "",
            }

    def search_messages(self, username: str, query: str, *, limit: int = 50) -> dict | None:
        normalized_query = query.strip().casefold()
        if not normalized_query:
            return {"items": []}
        with self.lock:
            user = self._users_by_username.get(username)
            if user is None:
                return None
            rooms = [
                room
                for room_id in self._room_ids_by_user.get(user["id"], set())
                if (room := self._rooms_by_id.get(room_id)) is not None
                and room.get("kind") in {"direct", "group"}
                and self._can_access_room_locked(room, user)
            ]
            rooms_by_id = {room["id"]: room for room in rooms}

            def searchable_room_copy(room: dict) -> str:
                values = [room.get("name", "")]
                for participant_id in room.get("participant_ids", []):
                    participant = self._users_by_id.get(participant_id)
                    if participant is None:
                        continue
                    values.extend((
                        participant.get("display_name", ""),
                        participant.get("username", ""),
                        participant.get("friend_code", ""),
                    ))
                return " ".join(str(value) for value in values if value).casefold()

            matching_room_ids = [
                room["id"] for room in rooms if normalized_query in searchable_room_copy(room)
            ][:limit]
            accessible_room_ids = list(rooms_by_id)
        message_limit = max(0, limit - len(matching_room_ids))
        matching_messages = self.repository.search_messages(
            accessible_room_ids,
            query.strip(),
            limit=message_limit,
        ) if message_limit else []
        result_room_ids = list(dict.fromkeys(
            matching_room_ids + [str(message.get("room_id", "")) for message in matching_messages]
        ))
        latest_messages = self.repository.latest_messages_for_rooms(result_room_ids)
        with self.lock:
            user = self._users_by_username.get(username)
            if user is None:
                return None
            rooms_by_id = {
                room_id: room
                for room_id in result_room_ids
                if (room := self._rooms_by_id.get(room_id)) is not None
                and self._can_access_room_locked(room, user)
            }
            room_summaries = {
                room_id: self._room_summary(
                    rooms_by_id[room_id],
                    user,
                    latest_message=latest_messages.get(room_id),
                    latest_message_loaded=True,
                )
                for room_id in result_room_ids
                if room_id in rooms_by_id
            }
        items = [
            {"kind": "room", "room": room_summaries[room_id]}
            for room_id in matching_room_ids
            if room_id in room_summaries
        ]
        items.extend(
            {"kind": "message", "room": room_summaries[room_id], "message": message}
            for message in matching_messages
            if (room_id := str(message.get("room_id", ""))) in room_summaries
        )
        return {"items": items[:limit]}

    def initial_message_read_state(self, room_id: str, username: str, message: dict) -> dict:
        with self.lock:
            user = self._users_by_username.get(username)
            room = self._rooms_by_id.get(room_id)
            if user is None or room is None or message.get("username") != username:
                return message

            response_message = {**message, "read": False}
            reader_ids = [
                user_id
                for user_id in room.get("participant_ids", [])
                if user_id != user["id"]
            ]
            last_read_by = room.get("last_read_by", {})
            response_message["read_by"] = [
                {
                    "id": reader["id"],
                    "username": reader["username"],
                    "display_name": reader.get("display_name") or reader["username"],
                }
                for reader_id in reader_ids
                if str(last_read_by.get(reader_id, "")) == str(message.get("id", ""))
                if (reader := self._users_by_id.get(reader_id)) is not None
            ]
            response_message["unread_by"] = [
                {
                    "id": reader["id"],
                    "username": reader["username"],
                    "display_name": reader.get("display_name") or reader["username"],
                }
                for reader_id in reader_ids
                if str(last_read_by.get(reader_id, "")) != str(message.get("id", ""))
                if (reader := self._users_by_id.get(reader_id)) is not None
            ]
            response_message["read"] = not response_message["unread_by"]
            return response_message

    def mark_room_read(self, room_id: str, username: str) -> tuple[dict | None, bool]:
        with self.lock:
            user = self._users_by_username.get(username)
            room = self._rooms_by_id.get(room_id)
            if user is None or room is None or not self._can_access_room_locked(room, user):
                return None, False
            messages = self._room_messages_locked(room_id, limit=1)
            if not messages:
                return self._room_summary(room, user), False
            last_read_by = room.setdefault("last_read_by", {})
            last_message_id = messages[-1]["id"]
            if last_read_by.get(user["id"]) == last_message_id:
                return self._room_summary(room, user), False
            last_read_by[user["id"]] = last_message_id
            if self.repository is not None:
                self.repository.sync_read_position(room_id, user["id"], last_message_id)
            self._save_locked("rooms")
            return self._room_summary(room, user), True

    def room_event_recipients(self, room_id: str) -> set[str]:
        with self.lock:
            room = self._rooms_by_id.get(room_id)
            if room is None:
                return set()
            if room.get("is_public"):
                return set(self._users_by_username)
            return {
                user["username"]
                for user_id in room.get("participant_ids", [])
                if (user := self._users_by_id.get(user_id)) is not None
            }

    def room_event_summaries(self, room_id: str) -> dict[str, dict]:
        with self.lock:
            room = self._rooms_by_id.get(room_id)
            if room is None:
                return {}
            return {
                user["username"]: self._room_summary(room, user)
                for user_id in room.get("participant_ids", [])
                if (user := self._users_by_id.get(user_id)) is not None
            }

    def presence_event_recipients(self, username: str) -> set[str]:
        with self.lock:
            user = self._users_by_username.get(username)
            if user is None:
                return set()
            recipient_ids = self._friend_ids_locked(user["id"])
            recipient_ids.add(user["id"])
            return {
                candidate["username"]
                for user_id in recipient_ids
                if (candidate := self._users_by_id.get(user_id)) is not None
            }

    def can_access_attachment(self, filename: str, username: str) -> bool:
        with self.lock:
            user = self._users_by_username.get(username)
            if user is None:
                return False
            if filename in self._profile_images:
                return True
            room_image_id = self._room_images.get(filename)
            if room_image_id:
                room = self._rooms_by_id.get(room_image_id)
                return room is not None and self._can_access_room_locked(room, user)
            attachment_room_ids = (
                self.repository.attachment_room_ids(filename)
                if self.repository is not None
                else self._attachment_rooms.get(filename, set())
            )
            return any(
                (room := self._rooms_by_id.get(room_id)) is not None and self._can_access_room_locked(room, user)
                for room_id in attachment_room_ids
            )

    def add_friend(self, username: str, friend_user_id: str) -> tuple[dict | None, str | None]:
        with self.lock:
            user = self._users_by_username.get(username)
            friend = self._user_by_id_locked(friend_user_id)
            if user is None or friend is None:
                return None, "사용자를 찾을 수 없습니다."
            if user["id"] == friend["id"]:
                return None, "자기 자신은 친구로 추가할 수 없습니다."

            user_ids = sorted([user["id"], friend["id"]])
            if tuple(user_ids) not in self._friendship_pairs:
                created_at = utc_now_iso()
                self.state["friendships"].append({"user_ids": user_ids, "created_at": created_at})
                self._register_friendship_locked(user_ids[0], user_ids[1])
                if self.repository is not None:
                    self.repository.sync_friendship(user_ids[0], user_ids[1], created_at)
                self._save_locked("friendships")
            return self._user_public(friend), None

    def add_friend_by_code(self, username: str, friend_code: str) -> tuple[dict | None, str | None]:
        normalized_friend_code = normalize_friend_code(friend_code)
        if not FRIEND_CODE_PATTERN.fullmatch(normalized_friend_code):
            return None, "올바른 친구 ID를 입력해 주세요."

        with self.lock:
            user = self._users_by_username.get(username)
            friend = self._users_by_friend_code.get(normalized_friend_code)
            if user is None or friend is None:
                return None, "해당 친구 ID의 사용자를 찾을 수 없습니다."
            if user["id"] == friend["id"]:
                return None, "자기 자신은 친구로 추가할 수 없습니다."

            user_ids = sorted([user["id"], friend["id"]])
            if tuple(user_ids) not in self._friendship_pairs:
                created_at = utc_now_iso()
                self.state["friendships"].append({"user_ids": user_ids, "created_at": created_at})
                self._register_friendship_locked(user_ids[0], user_ids[1])
                if self.repository is not None:
                    self.repository.sync_friendship(user_ids[0], user_ids[1], created_at)
                self._save_locked("friendships")
            return self._user_public(friend), None

    def create_or_get_direct_room(self, username: str, friend_user_id: str) -> tuple[dict | None, bool, str | None]:
        with self.lock:
            user = self._users_by_username.get(username)
            friend = self._user_by_id_locked(friend_user_id)
            if user is None or friend is None:
                return None, False, "사용자를 찾을 수 없습니다."

            participant_ids = sorted([user["id"], friend["id"]])
            if friend["id"] not in self._friend_ids_locked(user["id"]):
                return None, False, "먼저 친구로 추가해 주세요."

            room = self._direct_rooms_by_pair.get(tuple(participant_ids))
            if room is not None:
                return self._room_summary(room, user), False, None

            room = self._new_room(new_id("room"), friend["username"], "", username)
            room["kind"] = "direct"
            room["participant_ids"] = participant_ids
            self.state["rooms"].append(room)
            self._register_room_locked(room)
            self.state["messages"][room["id"]] = []
            if self.repository is not None:
                self.repository.sync_room(room)
            self._save_locked("rooms")
            return self._room_summary(room, user), True, None

    def create_group_room(
        self,
        username: str,
        name: str,
        member_user_ids: list[str],
    ) -> tuple[dict | None, str | None]:
        normalized_name = name.strip()
        if not 1 <= len(normalized_name) <= 32:
            return None, "그룹 이름은 1~32자로 입력해 주세요."
        if not MIN_GROUP_PARTICIPANTS - 1 <= len(member_user_ids) <= MAX_GROUP_PARTICIPANTS - 1:
            return None, "친구를 2명 이상 49명 이하로 선택해 주세요."
        if len(set(member_user_ids)) != len(member_user_ids):
            return None, "같은 친구를 중복해서 선택할 수 없습니다."

        with self.lock:
            creator = self._users_by_username.get(username)
            if creator is None:
                return None, "사용자를 찾을 수 없습니다."
            if creator["id"] in member_user_ids:
                return None, "자기 자신은 그룹 멤버로 선택할 수 없습니다."
            friend_ids = self._friend_ids_locked(creator["id"])
            if any(member_id not in friend_ids for member_id in member_user_ids):
                return None, "친구로 추가된 사용자만 그룹에 초대할 수 있습니다."
            if any(member_id not in self._users_by_id for member_id in member_user_ids):
                return None, "초대할 사용자를 찾을 수 없습니다."

            room = self._new_room(new_id("room"), normalized_name, "", username)
            room["kind"] = "group"
            room["participant_ids"] = [creator["id"], *member_user_ids]
            self.state["rooms"].append(room)
            self._register_room_locked(room)
            self.state["messages"][room["id"]] = []
            if self.repository is not None:
                self.repository.sync_room(room)
            self._save_locked("rooms")
            return self._room_summary(room, creator), None

    def group_room_access(self, username: str, room_id: str) -> str:
        with self.lock:
            user = self._users_by_username.get(username)
            room = self._rooms_by_id.get(room_id)
            if (
                user is None
                or room is None
                or room.get("kind") != "group"
                or not self._can_access_room_locked(room, user)
            ):
                return "not_found"
            return "owner" if room.get("created_by") == username else "member"

    def update_group_room_name(
        self,
        username: str,
        room_id: str,
        name: str,
    ) -> tuple[dict | None, str | None]:
        normalized_name = name.strip()
        if not 1 <= len(normalized_name) <= 32:
            return None, "invalid_name"
        with self.lock:
            user = self._users_by_username.get(username)
            room = self._rooms_by_id.get(room_id)
            if (
                user is None
                or room is None
                or room.get("kind") != "group"
                or not self._can_access_room_locked(room, user)
            ):
                return None, "not_found"
            if room.get("created_by") != username:
                return None, "forbidden"
            room["name"] = normalized_name
            room["updated_at"] = utc_now_iso()
            if self.repository is not None:
                self.repository.sync_room(room)
            self._save_locked("rooms")
            return self._room_summary(room, user), None

    def update_group_room_image(
        self,
        username: str,
        room_id: str,
        image_url: str,
        thumbnail_url: str = "",
    ) -> tuple[dict | None, str | None]:
        normalized_url = normalize_room_image_url(image_url)
        normalized_thumbnail_url = normalize_room_image_url(thumbnail_url)
        if image_url and not normalized_url:
            return None, "invalid_image"
        if thumbnail_url and not normalized_thumbnail_url:
            return None, "invalid_image"
        with self.lock:
            user = self._users_by_username.get(username)
            room = self._rooms_by_id.get(room_id)
            if (
                user is None
                or room is None
                or room.get("kind") != "group"
                or not self._can_access_room_locked(room, user)
            ):
                return None, "not_found"
            if room.get("created_by") != username:
                return None, "forbidden"

            previous_filenames = {
                Path(url).name
                for url in (
                    normalize_room_image_url(room.get("image_url")),
                    normalize_room_image_url(room.get("image_thumbnail_url")),
                )
                if url
            }
            for filename in previous_filenames:
                self._room_images.pop(filename, None)
            room["image_url"] = normalized_url
            room["image_thumbnail_url"] = normalized_thumbnail_url
            room["image_version"] = time.time_ns() if normalized_url else 0
            room["updated_at"] = utc_now_iso()
            for url in (normalized_url, normalized_thumbnail_url):
                if url:
                    self._room_images[Path(url).name] = room_id
            if self.repository is not None:
                self.repository.sync_room(room)
            self._save_locked("rooms")
            return self._room_summary(room, user), None

    def leave_group_room(
        self,
        username: str,
        room_id: str,
    ) -> tuple[dict | None, set[str], str | None]:
        with self.lock:
            user = self._users_by_username.get(username)
            room = self._rooms_by_id.get(room_id)
            if (
                user is None
                or room is None
                or room.get("kind") != "group"
                or user["id"] not in room.get("participant_ids", [])
            ):
                return None, set(), "not_found"

            recipients = {
                participant["username"]
                for user_id in room.get("participant_ids", [])
                if (participant := self._users_by_id.get(user_id)) is not None
            }
            remaining_ids = [
                user_id for user_id in room.get("participant_ids", [])
                if user_id != user["id"]
            ]
            room["participant_ids"] = remaining_ids
            room.setdefault("last_read_by", {}).pop(user["id"], None)
            self._room_ids_by_user.get(user["id"], set()).discard(room_id)
            if room.get("created_by") == username:
                next_creator = self._users_by_id.get(remaining_ids[0]) if remaining_ids else None
                room["created_by"] = next_creator["username"] if next_creator else ""
            room["updated_at"] = utc_now_iso()

            if not remaining_ids:
                room["archived_at"] = room["updated_at"]
                summary = None
            else:
                room.pop("archived_at", None)
                viewer = self._users_by_id.get(remaining_ids[0])
                summary = self._room_summary(room, viewer)
            if self.repository is not None:
                self.repository.sync_room(room)
            self._save_locked("rooms")
            return summary, recipients, None

    def create_local_user(
        self,
        username: str,
        friend_code: str,
        password: str,
        status_message: str,
        phone: str,
        age_group: str,
        gender: str,
    ) -> tuple[dict | None, str | None]:
        normalized_username = username.strip()[:24]
        normalized_friend_code = normalize_friend_code(friend_code)
        normalized_phone = normalize_phone(phone)

        if len(normalized_username) < 2:
            return None, "사용자 이름은 2자 이상이어야 합니다."
        if len(password) < 4:
            return None, "비밀번호는 4자 이상이어야 합니다."
        if not FRIEND_CODE_PATTERN.fullmatch(normalized_friend_code):
            return None, "친구 ID는 영문 소문자, 숫자, 밑줄로 4~20자여야 합니다."
        if not normalized_phone:
            return None, "휴대폰 인증을 완료해 주세요."
        if age_group not in AGE_GROUPS:
            return None, "연령대를 선택해 주세요."
        if gender not in GENDERS:
            return None, "성별을 선택해 주세요."

        with self.lock:
            if normalized_username in self._users_by_username:
                return None, "이미 존재하는 사용자 이름입니다."
            if normalized_friend_code in self._users_by_friend_code:
                return None, "이미 사용 중인 친구 ID입니다."

        salt_hex, digest = hash_password(password)
        with self.lock:
            if normalized_username in self._users_by_username:
                return None, "이미 존재하는 사용자 이름입니다."
            if normalized_friend_code in self._users_by_friend_code:
                return None, "이미 사용 중인 친구 ID입니다."
            user = {
                "id": new_id("user"),
                "username": normalized_username,
                "friend_code": normalized_friend_code,
                "display_name": normalized_username,
                "status_message": status_message.strip()[:40] or build_status_message("local"),
                "phone": normalized_phone,
                "auth_provider": "local",
                "provider_user_id": "",
                "password_salt": salt_hex,
                "password_hash": digest,
                "created_at": utc_now_iso(),
                "profile_pixels_blank": True,
                "profile_art_version": 0,
                "profile_image_url": "",
                "profile_thumbnail_url": "",
                "profile_image_version": 0,
                "custom_palette": [],
                "age_group": age_group,
                "gender": gender,
            }
            self.state["users"].append(user)
            self._register_user_locked(user)
            if self.repository is not None:
                self.repository.sync_user(user)
            self._save_locked("users")
            return self._user_public(user), None

    def create_or_update_social_user(
        self,
        provider: str,
        provider_user_id: str,
        *,
        nickname: str,
        status_message: str = "",
    ) -> dict:
        with self.lock:
            user = self._users_by_social_key.get((provider, provider_user_id))
            if user is None:
                username = self._unique_username_locked(nickname, provider, provider_user_id)
                user = {
                "id": new_id("user"),
                "username": username,
                "friend_code": self._new_friend_code_locked(),
                    "display_name": nickname.strip()[:24] or username,
                    "status_message": (status_message or build_status_message(provider))[:40],
                    "phone": "",
                    "auth_provider": provider,
                    "provider_user_id": provider_user_id,
                    "password_salt": "",
                "password_hash": "",
                "created_at": utc_now_iso(),
                "profile_pixels_blank": True,
                "profile_art_version": 0,
                "profile_image_url": "",
                "profile_thumbnail_url": "",
                "profile_image_version": 0,
                "custom_palette": [],
                "age_group": "",
                "gender": "",
                }
                self.state["users"].append(user)
                self._register_user_locked(user)
            else:
                if status_message:
                    user["status_message"] = status_message[:40]
            if self.repository is not None:
                self.repository.sync_user(user)
            self._save_locked("users")
            return self._user_public(user)

    def authenticate_user(self, username: str, password: str) -> dict | None:
        normalized_username = username.strip()
        with self.lock:
            user = self._users_by_username.get(normalized_username)
            if user is None or not user.get("password_hash"):
                return None
            password_salt = str(user["password_salt"])
            password_hash = str(user["password_hash"])
        _, digest = hash_password(password, password_salt)
        if not hmac.compare_digest(digest, password_hash):
            return None
        with self.lock:
            user = self._users_by_username.get(normalized_username)
            if user is None or not hmac.compare_digest(str(user.get("password_hash", "")), password_hash):
                return None
            return self._user_public(user)

    def seed_demo_network(self, username: str) -> None:
        with self.lock:
            user = self._users_by_username.get(username)
            if user is None or user.get("auth_provider") != "demo":
                return

            changed = False
            created_room_ids: list[str] = []
            contacts: list[dict] = []
            active_emojis = ["\U0001F600", "\U0001F60E", "\U0001F970", "\U0001F622", "\U0001F620"]
            for index in range(1, 21):
                provider_user_id = f"demo-contact-{index:02d}"
                contact = self._users_by_social_key.get(("demo", provider_user_id))
                if contact is None:
                    contact = {
                        "id": new_id("user"),
                        "username": self._unique_username_locked(f"test_{index:02d}", "demo", provider_user_id),
                        "friend_code": self._new_friend_code_locked(),
                        "display_name": f"Test {index:02d}",
                        "status_message": "test account",
                        "phone": "",
                        "auth_provider": "demo",
                        "provider_user_id": provider_user_id,
                        "password_salt": "",
                        "password_hash": "",
                        "created_at": utc_now_iso(),
                        "profile_pixels_blank": True,
                        "profile_art_version": 0,
                        "profile_image_url": "",
                        "profile_thumbnail_url": "",
                        "profile_image_version": 0,
                        "custom_palette": [],
                        "age_group": "",
                        "gender": "",
                    }
                    self.state["users"].append(contact)
                    self._register_user_locked(contact)
                    if self.repository is not None:
                        self.repository.sync_user(contact)
                    changed = True
                if index <= len(active_emojis):
                    emoji = active_emojis[index - 1]
                    if contact.get("status_message") != emoji:
                        contact["status_message"] = emoji
                        if self.repository is not None:
                            self.repository.sync_user(contact)
                        changed = True
                    if self.presence is not None:
                        self.presence.set_demo_active(contact["username"], active_emojis[index - 1])
                contacts.append(contact)

            for contact in contacts:
                user_ids = sorted([user["id"], contact["id"]])
                pair = tuple(user_ids)
                if pair not in self._friendship_pairs:
                    created_at = utc_now_iso()
                    self.state["friendships"].append({"user_ids": user_ids, "created_at": created_at})
                    self._register_friendship_locked(user_ids[0], user_ids[1])
                    if self.repository is not None:
                        self.repository.sync_friendship(user_ids[0], user_ids[1], created_at)
                    changed = True
                room = self._direct_rooms_by_pair.get(pair)
                if room is None:
                    room = self._new_room(new_id("room"), contact["username"], "", username)
                    room["kind"] = "direct"
                    room["participant_ids"] = user_ids
                    self.state["rooms"].append(room)
                    self._register_room_locked(room)
                    self.state["messages"][room["id"]] = []
                    created_room_ids.append(room["id"])
                    if self.repository is not None:
                        self.repository.sync_room(room)
                    changed = True
            if changed:
                self._save_locked(
                    "users",
                    "friendships",
                    "rooms",
                    *(f"messages:{room_id}" for room_id in created_room_ids),
                )

    def add_message(
        self,
        room_id: str,
        username: str,
        text: str,
        attachment: dict | None = None,
        client_message_id: str = "",
    ) -> tuple[dict, dict, bool] | None:
        with self.lock:
            room = self._rooms_by_id.get(room_id)
            user = self._users_by_username.get(username)
            if room is None or user is None or not self._can_access_room_locked(room, user):
                return None

            idempotency_key = (room_id, username, client_message_id)
            existing_message = self._messages_by_client_id.get(idempotency_key) if client_message_id else None
            if existing_message is None and client_message_id and self.repository is not None:
                existing_message = self.repository.message_by_client_id(room_id, user["id"], client_message_id)
            if existing_message is not None:
                if (
                    existing_message.get("text", "") != text[:300]
                    or existing_message.get("attachment") != attachment
                ):
                    raise ValueError("client message id was reused with different content")
                return existing_message, self._room_summary(
                    room,
                    latest_message=existing_message,
                    latest_message_loaded=True,
                ), False

            message = {
                "id": new_id("msg"),
                "room_id": room_id,
                "username": username[:24],
                "text": text[:300],
                "timestamp": utc_now_iso(),
            }
            if client_message_id:
                message["client_message_id"] = client_message_id
            if attachment is not None:
                message["attachment"] = attachment
                filename = Path(str(attachment.get("url", ""))).name
                if filename:
                    self._attachment_rooms.setdefault(filename, set()).add(room_id)
            room_messages = self.state["messages"].setdefault(room_id, [])
            room_messages.append(message)
            if client_message_id:
                self._messages_by_client_id[idempotency_key] = message
            if len(room_messages) > MAX_MESSAGES_PER_ROOM:
                removed_messages = room_messages[:-MAX_MESSAGES_PER_ROOM]
                del room_messages[:-MAX_MESSAGES_PER_ROOM]
                for removed_message in removed_messages:
                    removed_client_message_id = str(removed_message.get("client_message_id", ""))
                    if removed_client_message_id:
                        self._messages_by_client_id.pop(
                            (room_id, str(removed_message.get("username", "")), removed_client_message_id),
                            None,
                        )
                removed_filenames = {
                    Path(str(candidate.get("attachment", {}).get("url", ""))).name
                    for candidate in removed_messages
                    if isinstance(candidate.get("attachment"), dict)
                }
                remaining_filenames = {
                    Path(str(candidate.get("attachment", {}).get("url", ""))).name
                    for candidate in room_messages
                    if isinstance(candidate.get("attachment"), dict)
                }
                for removed_filename in removed_filenames - remaining_filenames:
                    rooms = self._attachment_rooms.get(removed_filename)
                    if rooms is not None:
                        rooms.discard(room_id)
                        if not rooms:
                            self._attachment_rooms.pop(removed_filename, None)
            room["updated_at"] = message["timestamp"]
            if self.repository is not None:
                if not self.repository.insert_message(message, user["id"], room, MAX_MESSAGES_PER_ROOM):
                    room_messages.pop()
                    existing_message = self.repository.message_by_client_id(room_id, user["id"], client_message_id) if client_message_id else None
                    if existing_message is not None:
                        return existing_message, self._room_summary(
                            room,
                            latest_message=existing_message,
                            latest_message_loaded=True,
                        ), False
                    raise ValueError("message persistence constraint failed")
                room_messages.clear()
            if self.repository is not None:
                self._save_locked("rooms")
            else:
                self._save_locked("rooms", f"messages:{room_id}")
            return message, self._room_summary(
                room,
                latest_message=message,
                latest_message_loaded=True,
            ), True

    def delete_message(
        self,
        room_id: str,
        username: str,
        message_id: str,
    ) -> tuple[dict | None, dict | None, str | None]:
        with self.lock:
            room = self._rooms_by_id.get(room_id)
            user = self._users_by_username.get(username)
            if room is None or user is None or not self._can_access_room_locked(room, user):
                return None, None, "not_found"

            messages = self._room_messages_locked(room_id)
            message_index = next(
                (index for index, message in enumerate(messages) if message.get("id") == message_id),
                -1,
            )
            if message_index < 0:
                return None, None, "not_found"
            message = messages[message_index]
            if message.get("username") != username:
                return None, None, "forbidden"

            remaining_messages = messages[:message_index] + messages[message_index + 1:]
            previous_message_id = (
                str(remaining_messages[message_index - 1].get("id", ""))
                if message_index > 0 else ""
            )
            last_read_by = room.setdefault("last_read_by", {})
            for reader_id, last_read_message_id in list(last_read_by.items()):
                if str(last_read_message_id) != message_id:
                    continue
                if previous_message_id:
                    last_read_by[reader_id] = previous_message_id
                else:
                    last_read_by.pop(reader_id, None)

            if self.repository is not None:
                if not self.repository.delete_message(room_id, message_id, user["id"]):
                    return None, None, "not_found"
            else:
                stored_messages = self.state["messages"].setdefault(room_id, [])
                stored_messages[:] = [candidate for candidate in stored_messages if candidate.get("id") != message_id]

            client_message_id = str(message.get("client_message_id", ""))
            if client_message_id:
                self._messages_by_client_id.pop((room_id, username, client_message_id), None)

            attachment = message.get("attachment")
            if isinstance(attachment, dict):
                filename = Path(str(attachment.get("url", ""))).name
                if filename and not any(
                    Path(str(candidate.get("attachment", {}).get("url", ""))).name == filename
                    for candidate in remaining_messages
                    if isinstance(candidate.get("attachment"), dict)
                ):
                    rooms = self._attachment_rooms.get(filename)
                    if rooms is not None:
                        rooms.discard(room_id)
                        if not rooms:
                            self._attachment_rooms.pop(filename, None)

            latest_message = remaining_messages[-1] if remaining_messages else None
            room["updated_at"] = (
                str(latest_message.get("timestamp", ""))
                if latest_message is not None else str(room.get("created_at", ""))
            )
            if self.repository is not None:
                self.repository.sync_room(room)
                self._save_locked("rooms")
            else:
                self._save_locked("rooms", f"messages:{room_id}")
            return message, self._room_summary(room), None
