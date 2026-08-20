from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx

from .profile_art import (
    PROFILE_ART_PACKED_BYTES,
    compact_user_profile_fields,
    is_blank_profile_pixels,
    pack_profile_pixels,
    valid_profile_pixels,
)


SCHEMA_VERSION = 1

SUPABASE_HTTP_CLIENT = httpx.Client(
    timeout=httpx.Timeout(30.0, connect=10.0),
    limits=httpx.Limits(max_connections=64, max_keepalive_connections=24, keepalive_expiry=30.0),
    follow_redirects=True,
)


class SupabaseRequestError(RuntimeError):
    pass


class ConcurrentUpdateError(RuntimeError):
    pass


def split_legacy_profile_art(user: dict) -> tuple[dict, bytes | None, bool]:
    compact = compact_user_profile_fields(user)
    pixels = user.get("profile_pixels")
    if not valid_profile_pixels(pixels):
        return compact, None, False
    blank = is_blank_profile_pixels(pixels)
    compact["profile_pixels_blank"] = blank
    compact["profile_art_version"] = 0 if blank else max(1, int(user.get("profile_art_version", 1)))
    return compact, None if blank else pack_profile_pixels(pixels), True


class NormalizedSupabaseRepository:
    """PostgREST-backed implementation of the normalized repository contract."""

    def __init__(self, base_url: str, service_key: str, transport=None) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_key = service_key
        self.transport = transport or self._request

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload=None,
        prefer: str = "",
        headers: dict[str, str] | None = None,
    ):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request_headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            request_headers["Prefer"] = prefer
        for name, value in (headers or {}).items():
            request_headers[name] = value
        try:
            response = SUPABASE_HTTP_CLIENT.request(
                method,
                f"{self.base_url}{path}",
                headers=request_headers,
                content=data,
            )
            if response.is_error:
                raise SupabaseRequestError(response.text or f"HTTP {response.status_code}")
            return response.json() if response.content else {}
        except httpx.RequestError as error:
            raise ConnectionError(str(error)) from error

    def rows(self, table: str, query: dict[str, str] | None = None) -> list[dict]:
        suffix = f"?{urlencode(query or {}, safe=',.*()><{}')}" if query else ""
        result = self.transport(f"/rest/v1/{table}{suffix}")
        return result if isinstance(result, list) else []

    def all_rows(
        self,
        table: str,
        query: dict[str, str] | None = None,
        *,
        page_size: int = 1000,
    ) -> list[dict]:
        collected: list[dict] = []
        offset = 0
        while True:
            page_query = dict(query or {})
            page_query["limit"] = str(page_size)
            page_query["offset"] = str(offset)
            page = self.rows(table, page_query)
            collected.extend(page)
            if len(page) < page_size:
                return collected
            offset += page_size

    def upsert(self, table: str, rows: list[dict], conflict: str) -> None:
        if not rows:
            return
        query = urlencode({"on_conflict": conflict}, safe=",")
        self.transport(
            f"/rest/v1/{table}?{query}",
            method="POST",
            payload=rows,
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def rpc(self, name: str, payload: dict):
        return self.transport(f"/rest/v1/rpc/{name}", method="POST", payload=payload)

    def is_legacy_imported(self) -> bool:
        rows = self.rows("app_migrations", {"select": "version", "key": "eq.normalized_state", "limit": "1"})
        return bool(rows and int(rows[0].get("version", 0)) >= SCHEMA_VERSION)

    def import_legacy_state(self, state: dict) -> dict[str, int]:
        users = state.get("users", [])
        users_by_username = {user["username"]: user for user in users}
        self.upsert("accounts", [self.account_row(account) for account in state.get("accounts", [])], "id")
        compact_users = []
        profile_rows = []
        for user in users:
            compact, packed, migrated = split_legacy_profile_art(user)
            compact_users.append(compact)
            if migrated and packed is not None:
                profile_rows.append({
                    "user_id": user["id"],
                    "version": compact["profile_art_version"],
                    "pixels_rgb": f"\\x{packed.hex()}",
                    "updated_at": time.time(),
                })
        self.upsert("users", [self.user_row(user) for user in compact_users], "id")
        for offset in range(0, len(profile_rows), 500):
            self.upsert("profile_art", profile_rows[offset:offset + 500], "user_id")
        self.upsert(
            "social_accounts",
            [
                {"provider": user.get("auth_provider", "local"), "provider_user_id": user["provider_user_id"], "user_id": user["id"], "account_id": user["account_id"]}
                for user in users if user.get("provider_user_id")
            ],
            "provider,provider_user_id",
        )
        self.upsert(
            "friendships",
            [
                {"user_low_id": sorted(item["user_ids"])[0], "user_high_id": sorted(item["user_ids"])[1], "created_at": item.get("created_at", "")}
                for item in state.get("friendships", []) if len(item.get("user_ids", [])) == 2
            ],
            "user_low_id,user_high_id",
        )
        for room in state.get("rooms", []):
            self.sync_room(room)
        message_rows = []
        for messages in state.get("messages", {}).values():
            for message in messages:
                sender = users_by_username.get(message.get("username", ""))
                if sender:
                    message_rows.append(self.message_row(message, sender["id"]))
        for offset in range(0, len(message_rows), 500):
            self.upsert("messages", message_rows[offset:offset + 500], "id")
        for room in state.get("rooms", []):
            if room.get("last_read_by"):
                self.sync_room(room)
        for token_hash, session in state.get("sessions", {}).items():
            user = users_by_username.get(session.get("username", ""))
            if user:
                self.upsert("sessions", [{"token_hash": token_hash, "user_id": user["id"], "account_id": user["account_id"], "active_user_id": user["id"], "created_at": session["created_at"], "expires_at": session["expires_at"]}], "token_hash")
        for username, feed in state.get("shorts_feeds", {}).items():
            user = users_by_username.get(username)
            if user:
                self.save_shorts_feed(user["id"], feed.get("seen_ids", []), feed.get("next_cursor", ""))
        self.upsert("app_migrations", [{"key": "normalized_state", "version": SCHEMA_VERSION}], "key")
        return self.verify()

    def sync_user(self, user: dict) -> None:
        revision = self.rpc("colorless_sync_user", {"user_data": compact_user_profile_fields(user)})
        if not revision:
            raise ConcurrentUpdateError("user revision conflict")
        user["_revision"] = int(revision)

    def identity_by_id(self, account_id: str, user_id: str) -> dict | None:
        rows = self.rows(
            "users",
            {
                "select": "data,revision",
                "account_id": f"eq.{account_id}",
                "id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        if not rows:
            return None
        identity = dict(rows[0]["data"])
        identity["_revision"] = int(rows[0].get("revision", 0))
        return identity

    def sync_account(self, account: dict) -> None:
        self.upsert("accounts", [self.account_row(account)], "id")

    def load_profile_art(self, user_id: str) -> tuple[int, bytes] | None:
        rows = self.rows(
            "profile_art",
            {"select": "version,pixels_rgb", "user_id": f"eq.{user_id}", "limit": "1"},
        )
        if not rows:
            return None
        encoded = str(rows[0].get("pixels_rgb", ""))
        if not encoded.startswith("\\x"):
            raise SupabaseRequestError("profile art binary encoding is invalid")
        packed = bytes.fromhex(encoded[2:])
        if len(packed) != PROFILE_ART_PACKED_BYTES:
            raise SupabaseRequestError("profile art binary length is invalid")
        return int(rows[0].get("version", 0)), packed

    def save_profile_art(self, user_id: str, packed: bytes | None, version: int) -> None:
        if packed is None:
            self.transport(f"/rest/v1/profile_art?user_id=eq.{quote(user_id)}", method="DELETE")
            return
        if len(packed) != PROFILE_ART_PACKED_BYTES:
            raise ValueError("invalid packed profile art")
        self.upsert(
            "profile_art",
            [{
                "user_id": user_id,
                "version": int(version),
                "pixels_rgb": f"\\x{packed.hex()}",
                "updated_at": time.time(),
            }],
            "user_id",
        )

    def sync_friendship(self, first_user_id: str, second_user_id: str, created_at: str) -> None:
        low, high = sorted((first_user_id, second_user_id))
        self.upsert("friendships", [{"user_low_id": low, "user_high_id": high, "created_at": created_at}], "user_low_id,user_high_id")

    def sync_room(self, room: dict) -> None:
        revision = self.rpc("colorless_sync_room", {"room_data": room})
        if not revision:
            raise ConcurrentUpdateError("room revision conflict")
        room["_revision"] = int(revision)

    def sync_read_position(self, room_id: str, user_id: str, message_id: str) -> None:
        self.upsert(
            "read_positions",
            [{"room_id": room_id, "user_id": user_id, "message_id": message_id}],
            "room_id,user_id",
        )

    def insert_message(self, message: dict, sender_id: str, room: dict, keep: int) -> bool:
        result = self.rpc("colorless_insert_message", {"message_data": message, "sender_user_id": sender_id, "room_data": room, "keep_count": keep})
        if not result:
            return False
        room["_revision"] = int(result)
        return True

    def message_by_client_id(self, room_id: str, sender_id: str, client_message_id: str) -> dict | None:
        rows = self.rows("messages", {"select": "data", "room_id": f"eq.{room_id}", "sender_id": f"eq.{sender_id}", "client_message_id": f"eq.{client_message_id}", "limit": "1"})
        return dict(rows[0]["data"]) if rows else None

    def delete_message(self, room_id: str, message_id: str, sender_id: str) -> bool:
        query = urlencode(
            {
                "room_id": f"eq.{room_id}",
                "id": f"eq.{message_id}",
                "sender_id": f"eq.{sender_id}",
                "select": "id",
            }
        )
        deleted = self.transport(
            f"/rest/v1/messages?{query}",
            method="DELETE",
            prefer="return=representation",
        )
        return isinstance(deleted, list) and bool(deleted)

    def list_messages(self, room_id: str, *, limit: int = 200, before: str = "") -> list[dict]:
        return [
            {key: value for key, value in message.items() if key != "_sequence"}
            for message in self.list_messages_with_sequences(room_id, limit=limit, before=before)
        ]

    def list_messages_with_sequences(self, room_id: str, *, limit: int = 200, before: str = "") -> list[dict]:
        query = {"select": "sequence,data", "room_id": f"eq.{room_id}", "order": "sequence.desc", "limit": str(limit)}
        if before:
            cursor = self.rows("messages", {"select": "sequence", "room_id": f"eq.{room_id}", "id": f"eq.{before}", "limit": "1"})
            if not cursor:
                return []
            query["sequence"] = f"lt.{cursor[0]['sequence']}"
        rows = self.rows("messages", query)
        return [
            {**dict(row["data"]), "_sequence": int(row["sequence"])}
            for row in reversed(rows)
        ]

    def message_sequences(self, room_id: str, message_ids: list[str]) -> dict[str, int]:
        unique_ids = list(dict.fromkeys(message_id for message_id in message_ids if message_id))
        if not unique_ids:
            return {}
        rows = self.rows(
            "messages",
            {
                "select": "id,sequence",
                "room_id": f"eq.{room_id}",
                "id": f"in.({','.join(unique_ids)})",
                "limit": str(len(unique_ids)),
            },
        )
        return {str(row["id"]): int(row["sequence"]) for row in rows}

    def search_messages(self, room_ids: list[str], query: str, *, limit: int = 50) -> list[dict]:
        if not room_ids or not query:
            return []
        safe_query = query.replace("\\", " ").replace(",", " ").replace("*", " ").strip()
        if not safe_query:
            return []
        matches = []
        for offset in range(0, len(room_ids), 100):
            room_batch = room_ids[offset:offset + 100]
            rows = self.rows(
                "messages",
                {
                    "select": "data,created_at",
                    "room_id": f"in.({','.join(room_batch)})",
                    "data->>text": f"ilike.*{safe_query}*",
                    "order": "created_at.desc",
                    "limit": str(limit),
                },
            )
            matches.extend(
                dict(row["data"]) for row in rows if isinstance(row.get("data"), dict)
            )
        matches.sort(key=lambda message: str(message.get("timestamp", "")), reverse=True)
        return matches[:limit]

    def latest_message(self, room_id: str) -> dict | None:
        messages = self.list_messages(room_id, limit=1)
        return messages[-1] if messages else None

    def latest_messages_for_rooms(self, room_ids: list[str]) -> dict[str, dict]:
        if not room_ids:
            return {}
        result = self.rpc("colorless_latest_messages", {"room_ids": room_ids})
        return {
            str(row["room_id"]): dict(row["data"])
            for row in result if isinstance(row, dict) and isinstance(row.get("data"), dict)
        } if isinstance(result, list) else {}

    def attachment_room_ids(self, filename: str) -> set[str]:
        rows = self.all_rows(
            "messages",
            {
                "select": "room_id",
                "data->attachment->>url": f"eq./uploads/{filename}",
                "order": "sequence.asc",
            },
        )
        return {str(row["room_id"]) for row in rows}

    def create_session(self, token_hash: str, account_id: str, active_user_id: str, created_at: float, expires_at: float, max_sessions: int) -> None:
        self.rpc("colorless_create_account_session", {"session_token_hash": token_hash, "session_account_id": account_id, "session_active_user_id": active_user_id, "created_epoch": created_at, "expires_epoch": expires_at, "max_session_count": max_sessions})

    def session_username(self, token_hash: str, now: float) -> str | None:
        result = self.rpc("colorless_account_session_username", {"session_token_hash": token_hash, "now_epoch": now})
        return str(result) if result else None

    def switch_session_identity(self, token_hash: str, account_id: str, user_id: str) -> bool:
        return bool(self.rpc("colorless_switch_session_identity", {"session_token_hash": token_hash, "session_account_id": account_id, "target_user_id": user_id}))

    def refresh_session(self, token_hash: str, expires_at: float) -> None:
        self.transport(f"/rest/v1/sessions?token_hash=eq.{quote(token_hash, safe='')}", method="PATCH", payload={"expires_at": expires_at})

    def destroy_session(self, token_hash: str) -> None:
        self.transport(f"/rest/v1/sessions?token_hash=eq.{quote(token_hash, safe='')}", method="DELETE")

    def get_shorts_feed(self, user_id: str) -> tuple[list[str], str]:
        feed = self.rows("shorts_feeds", {"select": "next_cursor", "user_id": f"eq.{user_id}", "limit": "1"})
        seen = self.rows("shorts_seen", {"select": "video_id", "user_id": f"eq.{user_id}", "order": "seen_order.asc"})
        return [str(row["video_id"]) for row in seen], str(feed[0]["next_cursor"]) if feed else ""

    def save_shorts_feed(self, user_id: str, seen_ids: list[str], next_cursor: str) -> None:
        self.rpc("colorless_save_shorts_feed", {"feed_user_id": user_id, "seen_video_ids": seen_ids, "cursor_value": next_cursor})

    def list_shorts_catalog(self, *, limit: int, offset: int = 0) -> list[dict]:
        rows = self.rows(
            "shorts_catalog",
            {
                "select": "video_id,data,last_seen_at,expires_at,source,rank_score",
                "order": "expires_at.desc,rank_score.desc,last_seen_at.desc,video_id.asc",
                "limit": str(limit),
                "offset": str(offset),
            },
        )
        return [
            {
                **dict(row.get("data") or {}),
                "id": str(row["video_id"]),
                "last_seen_at": float(row.get("last_seen_at", 0)),
                "expires_at": float(row.get("expires_at", 0)),
                "source": str(row.get("source", "")),
                "rank_score": float(row.get("rank_score", 0)),
            }
            for row in rows
        ]

    def upsert_shorts_catalog(self, items: list[dict], source: str, now: float, ttl_seconds: int) -> None:
        self.upsert(
            "shorts_catalog",
            [
                {
                    "video_id": str(item["id"]),
                    "source": source,
                    "rank_score": float(item.get("rank_score", 0)),
                    "discovered_at": now,
                    "last_seen_at": now,
                    "expires_at": now + ttl_seconds,
                    "data": {
                        "id": str(item["id"]),
                        "title": str(item.get("title", "YouTube 쇼츠")),
                        "channel_title": str(item.get("channel_title", "YouTube")),
                    },
                }
                for item in items
            ],
            "video_id",
        )

    def acquire_shorts_collection_lease(
        self,
        owner: str,
        now: float,
        lease_seconds: int,
        quota_cost: int,
        daily_quota: int,
    ) -> dict | None:
        result = self.rpc(
            "colorless_acquire_shorts_collection",
            {
                "collector_owner": owner,
                "now_epoch": now,
                "lease_seconds": lease_seconds,
                "requested_quota": quota_cost,
                "daily_quota": daily_quota,
            },
        )
        return dict(result) if isinstance(result, dict) else None

    def finish_shorts_collection(
        self,
        owner: str,
        *,
        now: float,
        next_job: int,
        success: bool,
        error: str = "",
        circuit_seconds: int = 0,
    ) -> None:
        self.rpc(
            "colorless_finish_shorts_collection",
            {
                "collector_owner": owner,
                "now_epoch": now,
                "next_job_value": next_job,
                "was_successful": success,
                "error_code": error[:80],
                "circuit_seconds": circuit_seconds,
            },
        )

    def shorts_catalog_status(self, now: float) -> dict:
        catalog = self.rows(
            "shorts_catalog",
            {"select": "video_id,last_seen_at,expires_at", "order": "last_seen_at.desc", "limit": "1000"},
        )
        state_rows = self.rows("shorts_collection_state", {"select": "*", "source": "eq.youtube", "limit": "1"})
        state = dict(state_rows[0]) if state_rows else {}
        return {
            "items": len(catalog),
            "fresh_items": sum(float(row.get("expires_at", 0)) > now for row in catalog),
            "age_seconds": max(0.0, now - max((float(row.get("last_seen_at", 0)) for row in catalog), default=0.0)) if catalog else None,
            "quota_used": int(state.get("quota_used", 0)),
            "failure_count": int(state.get("failure_count", 0)),
            "circuit_open": float(state.get("circuit_open_until", 0)) > now,
            "last_success_at": float(state.get("last_success_at", 0)),
            "last_error": str(state.get("last_error", "")),
        }

    def prune_shorts_catalog(self, before: float) -> int:
        result = self.transport(
            f"/rest/v1/shorts_catalog?last_seen_at=lt.{before}",
            method="DELETE",
            prefer="return=representation",
        )
        return len(result) if isinstance(result, list) else 0

    def load_state(self) -> dict:
        accounts = [
            dict(row["data"])
            for row in self.all_rows("accounts", {"select": "data", "order": "id.asc"})
        ]
        users = []
        migrated_users = []
        migrated_profile_rows = []
        for row in self.all_rows("users", {"select": "id,username,friend_code,data,revision", "order": "id.asc"}):
            user, packed_art, migrated_art = split_legacy_profile_art(dict(row["data"]))
            user.setdefault("account_id", f"account_{user['id']}")
            if migrated_art:
                migrated_users.append(self.user_row(user))
                if packed_art is not None:
                    migrated_profile_rows.append({
                        "user_id": row["id"],
                        "version": user["profile_art_version"],
                        "pixels_rgb": f"\\x{packed_art.hex()}",
                        "updated_at": time.time(),
                    })
            user["_revision"] = int(row["revision"])
            users.append(user)
        for offset in range(0, len(migrated_profile_rows), 500):
            self.upsert("profile_art", migrated_profile_rows[offset:offset + 500], "user_id")
        for offset in range(0, len(migrated_users), 500):
            self.upsert("users", migrated_users[offset:offset + 500], "id")
        friendships = [
            {"user_ids": [row["user_low_id"], row["user_high_id"]], "created_at": row["created_at"]}
            for row in self.all_rows(
                "friendships",
                {"select": "user_low_id,user_high_id,created_at", "order": "user_low_id.asc,user_high_id.asc"},
            )
        ]
        rooms = []
        for row in self.all_rows("rooms", {"select": "data,revision,updated_at", "order": "id.asc"}):
            room = dict(row["data"])
            room["_revision"] = int(row["revision"])
            room["updated_at"] = str(row["updated_at"])
            rooms.append(room)
        members: dict[str, list[str]] = {}
        for row in self.all_rows(
            "room_members", {"select": "room_id,user_id", "order": "room_id.asc,user_id.asc"}
        ):
            members.setdefault(str(row["room_id"]), []).append(str(row["user_id"]))
        reads: dict[str, dict[str, str]] = {}
        for row in self.all_rows(
            "read_positions", {"select": "room_id,user_id,message_id", "order": "room_id.asc,user_id.asc"}
        ):
            reads.setdefault(str(row["room_id"]), {})[str(row["user_id"])] = str(row["message_id"])
        for room in rooms:
            room["participant_ids"] = members.get(room["id"], [])
            room["last_read_by"] = reads.get(room["id"], {})
        users_by_id = {user["id"]: user for user in users}
        sessions = {}
        for row in self.all_rows(
            "sessions",
            {"select": "token_hash,user_id,account_id,active_user_id,created_at,expires_at", "order": "token_hash.asc"},
        ):
            active_user_id = str(row.get("active_user_id") or row.get("user_id") or "")
            active_user = users_by_id.get(active_user_id)
            if active_user is None:
                continue
            sessions[str(row["token_hash"])] = {
                "username": active_user["username"],
                "account_id": str(row.get("account_id") or active_user["account_id"]),
                "active_user_id": active_user_id,
                "created_at": float(row["created_at"]),
                "expires_at": float(row["expires_at"]),
            }
        feed_cursors = {
            str(row["user_id"]): str(row.get("next_cursor", ""))
            for row in self.all_rows(
                "shorts_feeds", {"select": "user_id,next_cursor", "order": "user_id.asc"}
            )
        }
        seen_by_user: dict[str, list[str]] = {}
        for row in self.all_rows(
            "shorts_seen",
            {"select": "user_id,video_id,seen_order", "order": "user_id.asc,seen_order.asc,video_id.asc"},
        ):
            seen_by_user.setdefault(str(row["user_id"]), []).append(str(row["video_id"]))
        shorts_feeds = {}
        for user_id in feed_cursors.keys() | seen_by_user.keys():
            user = users_by_id.get(user_id)
            if user:
                shorts_feeds[user["username"]] = {
                    "seen_ids": seen_by_user.get(user_id, []),
                    "next_cursor": feed_cursors.get(user_id, ""),
                }
        if not accounts:
            derived_accounts = {
                user["account_id"]: {
                    "id": user["account_id"],
                    "auth_provider": user.get("auth_provider", "local"),
                    "provider_user_id": user.get("provider_user_id", ""),
                    "password_salt": user.get("password_salt", ""),
                    "password_hash": user.get("password_hash", ""),
                    "phone": user.get("phone", ""),
                    "age_group": user.get("age_group", ""),
                    "gender": user.get("gender", ""),
                    "created_at": user.get("created_at", ""),
                    "status": "active",
                }
                for user in users
            }
            accounts = list(derived_accounts.values())
        return {"accounts": accounts, "users": users, "friendships": friendships, "rooms": rooms, "messages": {}, "sessions": sessions, "shorts_feeds": shorts_feeds}

    def publish_event(
        self,
        event: dict,
        recipients: set[str],
        origin_instance_id: str,
        *,
        occurred_at: float | None = None,
        retention: int = 20_000,
    ) -> dict:
        event_data = {**event, "event_id": str(event.get("event_id") or uuid.uuid4().hex)}
        result = self.rpc(
            "colorless_publish_event",
            {
                "event_data": event_data,
                "recipient_usernames": sorted(recipients),
                "source_instance_id": origin_instance_id,
                "occurred_epoch": float(occurred_at if occurred_at is not None else time.time()),
                "retention_count": retention,
            },
        )
        if not isinstance(result, dict):
            raise SupabaseRequestError("event publish returned no durable event")
        return result

    def latest_event_sequence(self) -> int:
        rows = self.rows(
            "realtime_events",
            {"select": "sequence", "order": "sequence.desc", "limit": "1"},
        )
        return int(rows[0]["sequence"]) if rows else 0

    def list_events_after(self, sequence: int, *, limit: int = 500) -> list[tuple[dict, set[str]]]:
        rows = self.rows(
            "realtime_events",
            {
                "select": "sequence,data,recipients",
                "sequence": f"gt.{sequence}",
                "order": "sequence.asc",
                "limit": str(limit),
            },
        )
        return [(dict(row["data"]), {str(item) for item in row.get("recipients", [])}) for row in rows]

    def events_for_user_after(self, username: str, sequence: int, *, limit: int = 500) -> list[dict]:
        rows = self.rows(
            "realtime_events",
            {
                "select": "sequence,data",
                "sequence": f"gt.{sequence}",
                "recipients": f"cs.{{{username}}}",
                "order": "sequence.asc",
                "limit": str(limit),
            },
        )
        return [dict(row["data"]) for row in rows]

    def touch_presence(
        self,
        lease_id: str,
        instance_id: str,
        username: str,
        active_room_id: str,
        emoji: str,
        ttl_seconds: int,
    ) -> tuple[dict, bool]:
        result = self.rpc(
            "colorless_touch_presence",
            {
                "presence_lease_id": lease_id,
                "source_instance_id": instance_id,
                "presence_username": username,
                "room_id_value": active_room_id,
                "emoji_value": emoji,
                "ttl_seconds": ttl_seconds,
            },
        )
        return dict(result.get("presence", {})), bool(result.get("changed"))

    def disconnect_presence(self, lease_id: str, username: str) -> tuple[dict, bool]:
        result = self.rpc(
            "colorless_disconnect_presence",
            {"presence_lease_id": lease_id, "presence_username": username},
        )
        return dict(result.get("presence", {})), bool(result.get("changed"))

    def presence_for_user(self, username: str) -> dict:
        result = self.rpc("colorless_presence_for_user", {"presence_username": username})
        return dict(result) if isinstance(result, dict) else {"online": False, "active_room_ids": [], "emoji": ""}

    def presence_for_users(self, usernames: list[str]) -> dict[str, dict]:
        if not usernames:
            return {}
        result = self.rpc("colorless_presence_for_users", {"presence_usernames": usernames})
        return {
            str(row["username"]): dict(row["presence"])
            for row in result if isinstance(row, dict) and isinstance(row.get("presence"), dict)
        } if isinstance(result, list) else {}

    def cleanup_expired_presence(self) -> list[tuple[str, dict]]:
        result = self.rpc("colorless_cleanup_presence", {})
        return [
            (str(item["username"]), dict(item["presence"]))
            for item in result if isinstance(item, dict)
        ] if isinstance(result, list) else []

    def verify(self) -> dict[str, int]:
        result = self.rpc("colorless_storage_counts", {})
        return dict(result) if isinstance(result, dict) else {}

    @staticmethod
    def user_row(user: dict) -> dict:
        compact = compact_user_profile_fields(user)
        compact.setdefault("account_id", f"account_{compact['id']}")
        return {"id": compact["id"], "account_id": compact["account_id"], "username": compact["username"], "friend_code": compact["friend_code"], "data": compact}

    @staticmethod
    def account_row(account: dict) -> dict:
        return {"id": account["id"], "status": account.get("status", "active"), "data": dict(account)}

    @staticmethod
    def message_row(message: dict, sender_id: str) -> dict:
        return {"id": message["id"], "room_id": message["room_id"], "sender_id": sender_id, "sender_username": message.get("username", ""), "client_message_id": message.get("client_message_id") or None, "created_at": message.get("timestamp", ""), "data": message}


class NormalizedSqliteRepository:
    """Row-oriented persistence used by the application domain services."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=15)
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("PRAGMA synchronous=FULL")
        return database

    @contextmanager
    def connection(self):
        database = self.connect()
        try:
            with database:
                yield database
        finally:
            database.close()

    def initialize(self) -> None:
        with self.connection() as database:
            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'active',
                    data_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    username TEXT NOT NULL UNIQUE,
                    friend_code TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL DEFAULT 1,
                    data_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_art (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    pixels_rgb BLOB NOT NULL CHECK(length(pixels_rgb) = 3072),
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS social_accounts (
                    provider TEXT NOT NULL,
                    provider_user_id TEXT NOT NULL,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    PRIMARY KEY(provider, provider_user_id)
                );
                CREATE TABLE IF NOT EXISTS friendships (
                    user_low_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    user_high_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(user_low_id, user_high_id),
                    CHECK(user_low_id < user_high_id)
                );
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    direct_key TEXT,
                    data_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS room_members (
                    room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    PRIMARY KEY(room_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS room_members_user_room_idx
                    ON room_members(user_id, room_id);
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                    sender_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    sender_username TEXT NOT NULL,
                    client_message_id TEXT,
                    created_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    UNIQUE(room_id, sender_id, client_message_id)
                );
                CREATE INDEX IF NOT EXISTS messages_room_created_idx
                    ON messages(room_id, created_at DESC, id DESC);
                CREATE TABLE IF NOT EXISTS read_positions (
                    room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    PRIMARY KEY(room_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    active_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS shorts_feeds (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    next_cursor TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS shorts_seen (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    video_id TEXT NOT NULL,
                    seen_order INTEGER NOT NULL,
                    PRIMARY KEY(user_id, video_id)
                );
                CREATE INDEX IF NOT EXISTS shorts_seen_user_order_idx
                    ON shorts_seen(user_id, seen_order DESC);
                CREATE TABLE IF NOT EXISTS shorts_catalog (
                    video_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    rank_score REAL NOT NULL DEFAULT 0,
                    discovered_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS shorts_catalog_feed_idx
                    ON shorts_catalog(expires_at DESC, rank_score DESC, last_seen_at DESC, video_id);
                CREATE INDEX IF NOT EXISTS shorts_catalog_expiry_idx
                    ON shorts_catalog(expires_at);
                CREATE TABLE IF NOT EXISTS shorts_collection_state (
                    source TEXT PRIMARY KEY,
                    owner_instance_id TEXT NOT NULL DEFAULT '',
                    lease_until REAL NOT NULL DEFAULT 0,
                    next_job_index INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    circuit_open_until REAL NOT NULL DEFAULT 0,
                    last_success_at REAL NOT NULL DEFAULT 0,
                    last_attempt_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    quota_window_start REAL NOT NULL DEFAULT 0,
                    quota_used INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS realtime_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    room_id TEXT NOT NULL DEFAULT '',
                    occurred_at REAL NOT NULL,
                    origin_instance_id TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS realtime_events_occurred_idx
                    ON realtime_events(occurred_at);
                CREATE TABLE IF NOT EXISTS realtime_event_recipients (
                    sequence INTEGER NOT NULL REFERENCES realtime_events(sequence) ON DELETE CASCADE,
                    username TEXT NOT NULL,
                    PRIMARY KEY(sequence, username)
                );
                CREATE INDEX IF NOT EXISTS realtime_recipients_user_sequence_idx
                    ON realtime_event_recipients(username, sequence);
                CREATE TABLE IF NOT EXISTS presence_leases (
                    lease_id TEXT PRIMARY KEY,
                    instance_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    active_room_id TEXT NOT NULL DEFAULT '',
                    emoji TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS presence_leases_expiry_idx
                    ON presence_leases(expires_at);
                CREATE INDEX IF NOT EXISTS presence_leases_user_expiry_idx
                    ON presence_leases(username, expires_at);
                """
            )
            user_columns = {str(row[1]) for row in database.execute("PRAGMA table_info(users)")}
            if "account_id" not in user_columns:
                database.execute("ALTER TABLE users ADD COLUMN account_id TEXT")
            if "revision" not in user_columns:
                database.execute("ALTER TABLE users ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
            social_columns = {str(row[1]) for row in database.execute("PRAGMA table_info(social_accounts)")}
            if "account_id" not in social_columns:
                database.execute("ALTER TABLE social_accounts ADD COLUMN account_id TEXT")
            session_columns = {str(row[1]) for row in database.execute("PRAGMA table_info(sessions)")}
            if "account_id" not in session_columns:
                database.execute("ALTER TABLE sessions ADD COLUMN account_id TEXT")
            if "active_user_id" not in session_columns:
                database.execute("ALTER TABLE sessions ADD COLUMN active_user_id TEXT")
            account_migration = database.execute(
                "SELECT value FROM schema_meta WHERE key='account_identity_schema_version'"
            ).fetchone()
            if account_migration is None or int(account_migration[0]) < 1:
                for user_id, data_json, account_id in database.execute(
                    "SELECT id, data_json, account_id FROM users"
                ).fetchall():
                    user = self.decode(data_json)
                    resolved_account_id = str(account_id or user.get("account_id") or f"account_{user_id}")
                    account = {
                        "id": resolved_account_id,
                        "auth_provider": user.get("auth_provider", "local"),
                        "provider_user_id": user.get("provider_user_id", ""),
                        "password_salt": user.get("password_salt", ""),
                        "password_hash": user.get("password_hash", ""),
                        "phone": user.get("phone", ""),
                        "age_group": user.get("age_group", ""),
                        "gender": user.get("gender", ""),
                        "created_at": user.get("created_at", ""),
                        "status": "active",
                    }
                    for account_field in ("password_salt", "password_hash", "phone", "age_group", "gender"):
                        user.pop(account_field, None)
                    database.execute(
                        "INSERT OR IGNORE INTO accounts(id, status, data_json) VALUES(?, 'active', ?)",
                        (resolved_account_id, self.encode(account)),
                    )
                    user["account_id"] = resolved_account_id
                    database.execute(
                        "UPDATE users SET account_id=?, data_json=? WHERE id=?",
                        (resolved_account_id, self.encode(user), user_id),
                    )
                database.execute(
                    "UPDATE social_accounts SET account_id=(SELECT users.account_id FROM users WHERE users.id=social_accounts.user_id) "
                    "WHERE account_id IS NULL"
                )
                database.execute(
                    "UPDATE sessions SET account_id=(SELECT users.account_id FROM users WHERE users.id=sessions.user_id), "
                    "active_user_id=user_id WHERE account_id IS NULL OR active_user_id IS NULL"
                )
                database.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('account_identity_schema_version', '1')"
                )
            account_migration = database.execute(
                "SELECT value FROM schema_meta WHERE key='account_identity_schema_version'"
            ).fetchone()
            if account_migration is None or int(account_migration[0]) < 2:
                over_limit = database.execute(
                    "SELECT account_id FROM users GROUP BY account_id HAVING COUNT(*) > 3 LIMIT 1"
                ).fetchone()
                if over_limit is not None:
                    raise sqlite3.IntegrityError(
                        f"account identity limit already exceeded for account {over_limit[0]}"
                    )
                database.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('account_identity_schema_version', '2')"
                )
            database.execute("CREATE INDEX IF NOT EXISTS users_account_idx ON users(account_id, id)")
            database.execute(
                "CREATE TRIGGER IF NOT EXISTS users_account_identity_limit "
                "BEFORE INSERT ON users "
                "WHEN NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.id) "
                "AND (SELECT COUNT(*) FROM users WHERE account_id=NEW.account_id) >= 3 "
                "BEGIN SELECT RAISE(ABORT, 'account identity limit exceeded'); END"
            )
            database.execute(
                "CREATE TRIGGER IF NOT EXISTS users_account_identity_update_limit "
                "BEFORE UPDATE OF account_id ON users "
                "WHEN NEW.account_id <> OLD.account_id "
                "AND (SELECT COUNT(*) FROM users WHERE account_id=NEW.account_id AND id<>NEW.id) >= 3 "
                "BEGIN SELECT RAISE(ABORT, 'account identity limit exceeded'); END"
            )
            database.execute(
                "CREATE TRIGGER IF NOT EXISTS users_require_account "
                "BEFORE INSERT ON users WHEN NEW.account_id IS NULL "
                "BEGIN SELECT RAISE(ABORT, 'user account is required'); END"
            )
            database.execute(
                "CREATE TRIGGER IF NOT EXISTS sessions_require_account_identity "
                "BEFORE INSERT ON sessions WHEN NEW.account_id IS NULL OR NEW.active_user_id IS NULL "
                "BEGIN SELECT RAISE(ABORT, 'session account and identity are required'); END"
            )
            room_columns = {str(row[1]) for row in database.execute("PRAGMA table_info(rooms)")}
            if "revision" not in room_columns:
                database.execute("ALTER TABLE rooms ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
            if "direct_key" not in room_columns:
                database.execute("ALTER TABLE rooms ADD COLUMN direct_key TEXT")
            database.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS rooms_direct_key_unique_idx "
                "ON rooms(direct_key) WHERE direct_key IS NOT NULL"
            )
            existing_direct_keys = {
                str(row[0]) for row in database.execute("SELECT direct_key FROM rooms WHERE direct_key IS NOT NULL")
            }
            for room_id, data_json in database.execute(
                "SELECT id, data_json FROM rooms WHERE kind='direct' AND direct_key IS NULL ORDER BY id"
            ).fetchall():
                direct_key = self.direct_room_key(self.decode(data_json))
                if direct_key and direct_key not in existing_direct_keys:
                    database.execute("UPDATE rooms SET direct_key=? WHERE id=?", (direct_key, room_id))
                    existing_direct_keys.add(direct_key)

    def is_legacy_imported(self) -> bool:
        with self.connection() as database:
            row = database.execute(
                "SELECT value FROM schema_meta WHERE key='legacy_import_version'"
            ).fetchone()
        return bool(row and int(row[0]) >= SCHEMA_VERSION)

    def import_legacy_state(self, state: dict) -> dict[str, int]:
        users = state.get("users", [])
        rooms = state.get("rooms", [])
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            for account in state.get("accounts", []):
                database.execute(
                    "INSERT OR REPLACE INTO accounts(id, status, data_json) VALUES(?, ?, ?)",
                    (account["id"], account.get("status", "active"), self.encode(account)),
                )
            for user in users:
                compact_user, packed_art, migrated_art = split_legacy_profile_art(user)
                database.execute(
                    "INSERT INTO users(id, account_id, username, friend_code, data_json) VALUES(?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
                    "account_id=excluded.account_id, friend_code=excluded.friend_code, data_json=excluded.data_json",
                    (user["id"], user["account_id"], user["username"], user["friend_code"], self.encode(compact_user)),
                )
                if migrated_art:
                    if packed_art is None:
                        database.execute("DELETE FROM profile_art WHERE user_id=?", (user["id"],))
                    else:
                        database.execute(
                            "INSERT OR REPLACE INTO profile_art(user_id, version, pixels_rgb, updated_at) "
                            "VALUES(?, ?, ?, ?)",
                            (user["id"], compact_user["profile_art_version"], packed_art, time.time()),
                        )
                provider_user_id = str(user.get("provider_user_id", ""))
                if provider_user_id:
                    database.execute(
                        "INSERT OR REPLACE INTO social_accounts(provider, provider_user_id, user_id, account_id) VALUES(?, ?, ?, ?)",
                        (user.get("auth_provider", "local"), provider_user_id, user["id"], user["account_id"]),
                    )
            for friendship in state.get("friendships", []):
                user_ids = sorted(friendship.get("user_ids", []))
                if len(user_ids) == 2:
                    database.execute(
                        "INSERT OR IGNORE INTO friendships(user_low_id, user_high_id, created_at) VALUES(?, ?, ?)",
                        (user_ids[0], user_ids[1], friendship.get("created_at", "")),
                    )
            for room in rooms:
                self._upsert_room(database, room)
                for user_id in room.get("participant_ids", []):
                    database.execute(
                        "INSERT OR IGNORE INTO room_members(room_id, user_id) VALUES(?, ?)",
                        (room["id"], user_id),
                    )
                for user_id, message_id in room.get("last_read_by", {}).items():
                    # Imported after messages so the FK always resolves.
                    pass
            users_by_username = {user["username"]: user for user in users}
            for room_id, messages in state.get("messages", {}).items():
                for message in messages:
                    sender = users_by_username.get(message.get("username", ""))
                    if sender:
                        self._insert_message(database, message, sender["id"])
            for room in rooms:
                for user_id, message_id in room.get("last_read_by", {}).items():
                    database.execute(
                        "INSERT OR IGNORE INTO read_positions(room_id, user_id, message_id) "
                        "SELECT ?, ?, ? WHERE EXISTS(SELECT 1 FROM messages WHERE id=?)",
                        (room["id"], user_id, message_id, message_id),
                    )
            for token_hash, session in state.get("sessions", {}).items():
                user = users_by_username.get(session.get("username", ""))
                if user:
                    database.execute(
                        "INSERT OR REPLACE INTO sessions(token_hash, user_id, account_id, active_user_id, created_at, expires_at) VALUES(?, ?, ?, ?, ?, ?)",
                        (token_hash, user["id"], user["account_id"], user["id"], session["created_at"], session["expires_at"]),
                    )
            for username, feed in state.get("shorts_feeds", {}).items():
                user = users_by_username.get(username)
                if not user:
                    continue
                database.execute(
                    "INSERT OR REPLACE INTO shorts_feeds(user_id, next_cursor) VALUES(?, ?)",
                    (user["id"], feed.get("next_cursor", "")),
                )
                for order, video_id in enumerate(feed.get("seen_ids", [])):
                    database.execute(
                        "INSERT OR REPLACE INTO shorts_seen(user_id, video_id, seen_order) VALUES(?, ?, ?)",
                        (user["id"], video_id, order),
                    )
            database.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('legacy_import_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        return self.verify()

    def sync_user(self, user: dict) -> None:
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            expected_revision = int(user.get("_revision", 0))
            row = database.execute("SELECT revision FROM users WHERE id=?", (user["id"],)).fetchone()
            if row is None:
                if expected_revision != 0:
                    raise ConcurrentUpdateError("user was removed during update")
                new_revision = 1
                persisted_user = {**compact_user_profile_fields(user), "_revision": new_revision}
                database.execute(
                    "INSERT INTO users(id, account_id, username, friend_code, revision, data_json) VALUES(?, ?, ?, ?, ?, ?)",
                    (user["id"], user["account_id"], user["username"], user["friend_code"], new_revision, self.encode(persisted_user)),
                )
            else:
                if int(row[0]) != expected_revision:
                    raise ConcurrentUpdateError("user revision conflict")
                new_revision = expected_revision + 1
                persisted_user = {**compact_user_profile_fields(user), "_revision": new_revision}
                cursor = database.execute(
                    "UPDATE users SET account_id=?, username=?, friend_code=?, revision=?, data_json=? WHERE id=? AND revision=?",
                    (user["account_id"], user["username"], user["friend_code"], new_revision, self.encode(persisted_user), user["id"], expected_revision),
                )
                if cursor.rowcount != 1:
                    raise ConcurrentUpdateError("user revision conflict")
            database.execute("DELETE FROM social_accounts WHERE user_id=?", (user["id"],))
            provider_user_id = str(user.get("provider_user_id", ""))
            if provider_user_id:
                database.execute(
                    "INSERT INTO social_accounts(provider, provider_user_id, user_id, account_id) VALUES(?, ?, ?, ?)",
                    (user.get("auth_provider", "local"), provider_user_id, user["id"], user["account_id"]),
                )
        user["_revision"] = new_revision

    def identity_by_id(self, account_id: str, user_id: str) -> dict | None:
        with self.connection() as database:
            row = database.execute(
                "SELECT data_json, revision FROM users WHERE account_id=? AND id=?",
                (account_id, user_id),
            ).fetchone()
        if row is None:
            return None
        identity = self.decode(row[0])
        identity["_revision"] = int(row[1])
        return identity

    def sync_account(self, account: dict) -> None:
        with self.connection() as database:
            database.execute(
                "INSERT INTO accounts(id, status, data_json) VALUES(?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, data_json=excluded.data_json",
                (account["id"], account.get("status", "active"), self.encode(account)),
            )

    def load_profile_art(self, user_id: str) -> tuple[int, bytes] | None:
        with self.connection() as database:
            row = database.execute(
                "SELECT version, pixels_rgb FROM profile_art WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        packed = bytes(row[1])
        if len(packed) != PROFILE_ART_PACKED_BYTES:
            raise ValueError("invalid packed profile art")
        return int(row[0]), packed

    def save_profile_art(self, user_id: str, packed: bytes | None, version: int) -> None:
        with self.connection() as database:
            if packed is None:
                database.execute("DELETE FROM profile_art WHERE user_id=?", (user_id,))
                return
            if len(packed) != PROFILE_ART_PACKED_BYTES:
                raise ValueError("invalid packed profile art")
            database.execute(
                "INSERT INTO profile_art(user_id, version, pixels_rgb, updated_at) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET version=excluded.version, "
                "pixels_rgb=excluded.pixels_rgb, updated_at=excluded.updated_at",
                (user_id, int(version), packed, time.time()),
            )

    def sync_friendship(self, first_user_id: str, second_user_id: str, created_at: str) -> None:
        user_low_id, user_high_id = sorted((first_user_id, second_user_id))
        with self.connection() as database:
            database.execute(
                "INSERT OR IGNORE INTO friendships(user_low_id, user_high_id, created_at) VALUES(?, ?, ?)",
                (user_low_id, user_high_id, created_at),
            )

    def sync_room(self, room: dict) -> None:
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            expected_revision = int(room.get("_revision", 0))
            row = database.execute("SELECT revision FROM rooms WHERE id=?", (room["id"],)).fetchone()
            direct_key = self.direct_room_key(room)
            if row is None:
                if expected_revision != 0:
                    raise ConcurrentUpdateError("room was removed during update")
                new_revision = 1
                persisted_room = {**room, "_revision": new_revision}
                database.execute(
                    "INSERT INTO rooms(id, kind, created_by, updated_at, revision, direct_key, data_json) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (
                        room["id"], room.get("kind", "group"), room.get("created_by", ""),
                        room.get("updated_at", ""), new_revision, direct_key, self.encode(persisted_room),
                    ),
                )
            else:
                if int(row[0]) != expected_revision:
                    raise ConcurrentUpdateError("room revision conflict")
                new_revision = expected_revision + 1
                persisted_room = {**room, "_revision": new_revision}
                cursor = database.execute(
                    "UPDATE rooms SET kind=?, created_by=?, updated_at=?, revision=?, direct_key=?, data_json=? "
                    "WHERE id=? AND revision=?",
                    (
                        room.get("kind", "group"), room.get("created_by", ""), room.get("updated_at", ""),
                        new_revision, direct_key, self.encode(persisted_room), room["id"], expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrentUpdateError("room revision conflict")
            database.execute("DELETE FROM room_members WHERE room_id=?", (room["id"],))
            database.executemany(
                "INSERT INTO room_members(room_id, user_id) VALUES(?, ?)",
                [(room["id"], user_id) for user_id in room.get("participant_ids", [])],
            )
            database.execute("DELETE FROM read_positions WHERE room_id=?", (room["id"],))
            for user_id, message_id in room.get("last_read_by", {}).items():
                database.execute(
                    "INSERT INTO read_positions(room_id, user_id, message_id) "
                    "SELECT ?, ?, ? WHERE EXISTS(SELECT 1 FROM messages WHERE id=?)",
                    (room["id"], user_id, message_id, message_id),
                )
        room["_revision"] = new_revision

    def sync_read_position(self, room_id: str, user_id: str, message_id: str) -> None:
        with self.connection() as database:
            database.execute(
                "INSERT INTO read_positions(room_id, user_id, message_id) VALUES(?, ?, ?) "
                "ON CONFLICT(room_id, user_id) DO UPDATE SET message_id=excluded.message_id",
                (room_id, user_id, message_id),
            )

    def create_session(self, token_hash: str, account_id: str, active_user_id: str, created_at: float, expires_at: float, max_sessions: int) -> None:
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            database.execute("DELETE FROM sessions WHERE expires_at<=?", (created_at,))
            database.execute(
                "INSERT OR REPLACE INTO sessions(token_hash, user_id, account_id, active_user_id, created_at, expires_at) VALUES(?, ?, ?, ?, ?, ?)",
                (token_hash, active_user_id, account_id, active_user_id, created_at, expires_at),
            )
            session_count = int(database.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
            overflow = max(0, session_count - max_sessions)
            if overflow:
                database.execute(
                    "DELETE FROM sessions WHERE token_hash IN (SELECT token_hash FROM sessions ORDER BY created_at LIMIT ?)",
                    (overflow,),
                )

    def session_username(self, token_hash: str, now: float) -> str | None:
        with self.connection() as database:
            database.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
            row = database.execute(
                "SELECT users.username FROM sessions JOIN users ON users.id=sessions.active_user_id "
                "JOIN accounts ON accounts.id=sessions.account_id WHERE token_hash=? AND accounts.status='active'",
                (token_hash,),
            ).fetchone()
        return str(row[0]) if row else None

    def switch_session_identity(self, token_hash: str, account_id: str, user_id: str) -> bool:
        with self.connection() as database:
            cursor = database.execute(
                "UPDATE sessions SET active_user_id=?, user_id=? WHERE token_hash=? AND account_id=? "
                "AND EXISTS(SELECT 1 FROM users WHERE id=? AND account_id=?)",
                (user_id, user_id, token_hash, account_id, user_id, account_id),
            )
            return cursor.rowcount == 1

    def refresh_session(self, token_hash: str, expires_at: float) -> None:
        with self.connection() as database:
            database.execute("UPDATE sessions SET expires_at=? WHERE token_hash=?", (expires_at, token_hash))

    def destroy_session(self, token_hash: str) -> None:
        with self.connection() as database:
            database.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def get_shorts_feed(self, user_id: str) -> tuple[list[str], str]:
        with self.connection() as database:
            row = database.execute("SELECT next_cursor FROM shorts_feeds WHERE user_id=?", (user_id,)).fetchone()
            seen = database.execute(
                "SELECT video_id FROM shorts_seen WHERE user_id=? ORDER BY seen_order",
                (user_id,),
            ).fetchall()
        return [str(item[0]) for item in seen], str(row[0]) if row else ""

    def save_shorts_feed(self, user_id: str, seen_ids: list[str], next_cursor: str) -> None:
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            database.execute(
                "INSERT INTO shorts_feeds(user_id, next_cursor) VALUES(?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET next_cursor=excluded.next_cursor",
                (user_id, next_cursor),
            )
            database.execute("DELETE FROM shorts_seen WHERE user_id=?", (user_id,))
            database.executemany(
                "INSERT INTO shorts_seen(user_id, video_id, seen_order) VALUES(?, ?, ?)",
                [(user_id, video_id, order) for order, video_id in enumerate(seen_ids)],
            )

    def list_shorts_catalog(self, *, limit: int, offset: int = 0) -> list[dict]:
        with self.connection() as database:
            rows = database.execute(
                "SELECT video_id, source, rank_score, last_seen_at, expires_at, data_json "
                "FROM shorts_catalog ORDER BY expires_at DESC, rank_score DESC, last_seen_at DESC, video_id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [
            {
                **self.decode(row[5]),
                "id": str(row[0]),
                "source": str(row[1]),
                "rank_score": float(row[2]),
                "last_seen_at": float(row[3]),
                "expires_at": float(row[4]),
            }
            for row in rows
        ]

    def upsert_shorts_catalog(self, items: list[dict], source: str, now: float, ttl_seconds: int) -> None:
        if not items:
            return
        with self.connection() as database:
            database.executemany(
                "INSERT INTO shorts_catalog(video_id, source, rank_score, discovered_at, last_seen_at, expires_at, data_json) "
                "VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT(video_id) DO UPDATE SET "
                "source=excluded.source, rank_score=excluded.rank_score, last_seen_at=excluded.last_seen_at, "
                "expires_at=excluded.expires_at, data_json=excluded.data_json",
                [
                    (
                        str(item["id"]),
                        source,
                        float(item.get("rank_score", 0)),
                        now,
                        now,
                        now + ttl_seconds,
                        self.encode({
                            "id": str(item["id"]),
                            "title": str(item.get("title", "YouTube 쇼츠")),
                            "channel_title": str(item.get("channel_title", "YouTube")),
                        }),
                    )
                    for item in items
                ],
            )

    def acquire_shorts_collection_lease(
        self,
        owner: str,
        now: float,
        lease_seconds: int,
        quota_cost: int,
        daily_quota: int,
    ) -> dict | None:
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            database.execute(
                "INSERT OR IGNORE INTO shorts_collection_state(source, quota_window_start) VALUES('youtube', ?)",
                (now,),
            )
            row = database.execute(
                "SELECT owner_instance_id, lease_until, next_job_index, circuit_open_until, "
                "quota_window_start, quota_used FROM shorts_collection_state WHERE source='youtube'"
            ).fetchone()
            assert row is not None
            quota_window_start = float(row[4])
            quota_used = int(row[5])
            if now - quota_window_start >= 24 * 60 * 60:
                quota_window_start = now
                quota_used = 0
            if (
                (float(row[1]) > now and str(row[0]) != owner)
                or float(row[3]) > now
                or quota_used + quota_cost > daily_quota
            ):
                return None
            database.execute(
                "UPDATE shorts_collection_state SET owner_instance_id=?, lease_until=?, last_attempt_at=?, "
                "quota_window_start=?, quota_used=? WHERE source='youtube'",
                (owner, now + lease_seconds, now, quota_window_start, quota_used + quota_cost),
            )
            return {"next_job_index": int(row[2]), "quota_used": quota_used + quota_cost}

    def finish_shorts_collection(
        self,
        owner: str,
        *,
        now: float,
        next_job: int,
        success: bool,
        error: str = "",
        circuit_seconds: int = 0,
    ) -> None:
        with self.connection() as database:
            if success:
                database.execute(
                    "UPDATE shorts_collection_state SET lease_until=0, owner_instance_id='', next_job_index=?, "
                    "failure_count=0, circuit_open_until=0, last_success_at=?, last_error='' "
                    "WHERE source='youtube' AND owner_instance_id=?",
                    (next_job, now, owner),
                )
            else:
                database.execute(
                    "UPDATE shorts_collection_state SET lease_until=0, owner_instance_id='', failure_count=failure_count+1, "
                    "circuit_open_until=CASE WHEN failure_count+1>=3 OR ? LIKE 'http-429%' OR ? LIKE 'http-403%' "
                    "THEN ? ELSE circuit_open_until END, last_error=? "
                    "WHERE source='youtube' AND owner_instance_id=?",
                    (error, error, now + circuit_seconds, error[:80], owner),
                )

    def shorts_catalog_status(self, now: float) -> dict:
        with self.connection() as database:
            row = database.execute(
                "SELECT COUNT(*), SUM(CASE WHEN expires_at>? THEN 1 ELSE 0 END), MAX(last_seen_at) FROM shorts_catalog",
                (now,),
            ).fetchone()
            state = database.execute(
                "SELECT quota_used, failure_count, circuit_open_until, last_success_at, last_error "
                "FROM shorts_collection_state WHERE source='youtube'"
            ).fetchone()
        latest = float(row[2]) if row and row[2] is not None else 0.0
        return {
            "items": int(row[0]) if row else 0,
            "fresh_items": int(row[1] or 0) if row else 0,
            "age_seconds": max(0.0, now - latest) if latest else None,
            "quota_used": int(state[0]) if state else 0,
            "failure_count": int(state[1]) if state else 0,
            "circuit_open": float(state[2]) > now if state else False,
            "last_success_at": float(state[3]) if state else 0.0,
            "last_error": str(state[4]) if state else "",
        }

    def prune_shorts_catalog(self, before: float) -> int:
        with self.connection() as database:
            cursor = database.execute("DELETE FROM shorts_catalog WHERE last_seen_at<?", (before,))
            return cursor.rowcount

    def load_state(self) -> dict:
        with self.connection() as database:
            accounts = [
                self.decode(data_json)
                for (data_json,) in database.execute("SELECT data_json FROM accounts ORDER BY id")
            ]
            users = []
            for user_id, data_json, revision in database.execute("SELECT id, data_json, revision FROM users"):
                user, packed_art, migrated_art = split_legacy_profile_art(self.decode(data_json))
                if migrated_art:
                    if packed_art is None:
                        database.execute("DELETE FROM profile_art WHERE user_id=?", (user_id,))
                    else:
                        database.execute(
                            "INSERT OR IGNORE INTO profile_art(user_id, version, pixels_rgb, updated_at) "
                            "VALUES(?, ?, ?, ?)",
                            (user_id, user["profile_art_version"], packed_art, time.time()),
                        )
                    database.execute("UPDATE users SET data_json=? WHERE id=?", (self.encode(user), user_id))
                user["_revision"] = int(revision)
                users.append(user)
            friendships = [
                {"user_ids": [row[0], row[1]], "created_at": row[2]}
                for row in database.execute("SELECT user_low_id, user_high_id, created_at FROM friendships")
            ]
            rooms = []
            for data_json, revision, updated_at in database.execute(
                "SELECT data_json, revision, updated_at FROM rooms"
            ):
                room = self.decode(data_json)
                room["_revision"] = int(revision)
                room["updated_at"] = str(updated_at)
                rooms.append(room)
            members_by_room: dict[str, list[str]] = {}
            for room_id, user_id in database.execute("SELECT room_id, user_id FROM room_members ORDER BY rowid"):
                members_by_room.setdefault(str(room_id), []).append(str(user_id))
            reads_by_room: dict[str, dict[str, str]] = {}
            for room_id, user_id, message_id in database.execute("SELECT room_id, user_id, message_id FROM read_positions"):
                reads_by_room.setdefault(str(room_id), {})[str(user_id)] = str(message_id)
            users_by_id = {user["id"]: user for user in users}
            sessions = {
                str(row[0]): {
                    "username": users_by_id[str(row[2])]["username"],
                    "account_id": str(row[1]),
                    "active_user_id": str(row[2]),
                    "created_at": float(row[3]),
                    "expires_at": float(row[4]),
                }
                for row in database.execute("SELECT token_hash, account_id, active_user_id, created_at, expires_at FROM sessions")
                if row[1] is not None and str(row[2]) in users_by_id
            }
            seen_by_user: dict[str, list[str]] = {}
            for user_id, video_id in database.execute(
                "SELECT user_id, video_id FROM shorts_seen ORDER BY user_id, seen_order"
            ):
                seen_by_user.setdefault(str(user_id), []).append(str(video_id))
            shorts_feeds = {}
            for user_id, next_cursor in database.execute("SELECT user_id, next_cursor FROM shorts_feeds"):
                user = users_by_id.get(str(user_id))
                if not user:
                    continue
                shorts_feeds[user["username"]] = {
                    "seen_ids": seen_by_user.get(str(user_id), []),
                    "next_cursor": str(next_cursor),
                }
        for room in rooms:
            room["participant_ids"] = members_by_room.get(room["id"], [])
            room["last_read_by"] = reads_by_room.get(room["id"], {})
        return {
            "accounts": accounts,
            "users": users,
            "friendships": friendships,
            "rooms": rooms,
            "messages": {},
            "sessions": sessions,
            "shorts_feeds": shorts_feeds,
        }

    def insert_message(self, message: dict, sender_id: str, room: dict, keep: int) -> bool:
        try:
            with self.connection() as database:
                database.execute("BEGIN IMMEDIATE")
                stored_room = database.execute(
                    "SELECT revision, data_json FROM rooms WHERE id=?",
                    (message["room_id"],),
                ).fetchone()
                if stored_room is None:
                    return False
                inserted = self._insert_message(database, message, sender_id)
                if not inserted:
                    database.rollback()
                    return False
                new_revision = int(stored_room[0]) + 1
                room_data = self.decode(stored_room[1])
                room_data["updated_at"] = message.get("timestamp", room_data.get("updated_at", ""))
                room_data["_revision"] = new_revision
                database.execute(
                    "UPDATE rooms SET updated_at=?, revision=?, data_json=? WHERE id=?",
                    (room_data["updated_at"], new_revision, self.encode(room_data), message["room_id"]),
                )
                database.execute(
                    "DELETE FROM messages WHERE room_id=? AND id NOT IN ("
                    "SELECT id FROM messages WHERE room_id=? ORDER BY rowid DESC LIMIT ?)",
                    (message["room_id"], message["room_id"], keep),
                )
            room["_revision"] = new_revision
            return True
        except sqlite3.IntegrityError:
            return False

    def message_by_client_id(self, room_id: str, sender_id: str, client_message_id: str) -> dict | None:
        with self.connection() as database:
            row = database.execute(
                "SELECT data_json FROM messages WHERE room_id=? AND sender_id=? AND client_message_id=?",
                (room_id, sender_id, client_message_id),
            ).fetchone()
        return self.decode(row[0]) if row else None

    def delete_message(self, room_id: str, message_id: str, sender_id: str) -> bool:
        with self.connection() as database:
            cursor = database.execute(
                "DELETE FROM messages WHERE room_id=? AND id=? AND sender_id=?",
                (room_id, message_id, sender_id),
            )
        return cursor.rowcount == 1

    def list_messages(self, room_id: str, *, limit: int = 200, before: str = "") -> list[dict]:
        return [
            {key: value for key, value in message.items() if key != "_sequence"}
            for message in self.list_messages_with_sequences(room_id, limit=limit, before=before)
        ]

    def list_messages_with_sequences(self, room_id: str, *, limit: int = 200, before: str = "") -> list[dict]:
        with self.connection() as database:
            if before:
                cursor = database.execute(
                    "SELECT rowid FROM messages WHERE room_id=? AND id=?",
                    (room_id, before),
                ).fetchone()
                if not cursor:
                    return []
                rows = database.execute(
                    "SELECT rowid, data_json FROM messages WHERE room_id=? "
                    "AND rowid < ? ORDER BY rowid DESC LIMIT ?",
                    (room_id, cursor[0], limit),
                ).fetchall()
            else:
                rows = database.execute(
                    "SELECT rowid, data_json FROM messages WHERE room_id=? ORDER BY rowid DESC LIMIT ?",
                    (room_id, limit),
                ).fetchall()
        return [
            {**self.decode(row[1]), "_sequence": int(row[0])}
            for row in reversed(rows)
        ]

    def message_sequences(self, room_id: str, message_ids: list[str]) -> dict[str, int]:
        unique_ids = list(dict.fromkeys(message_id for message_id in message_ids if message_id))
        if not unique_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_ids)
        with self.connection() as database:
            rows = database.execute(
                f"SELECT id, rowid FROM messages WHERE room_id=? AND id IN ({placeholders})",
                (room_id, *unique_ids),
            ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def search_messages(self, room_ids: list[str], query: str, *, limit: int = 50) -> list[dict]:
        if not room_ids or not query:
            return []
        escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        matches = []
        with self.connection() as database:
            for offset in range(0, len(room_ids), 800):
                room_batch = room_ids[offset:offset + 800]
                placeholders = ",".join("?" for _ in room_batch)
                rows = database.execute(
                    "SELECT data_json FROM messages "
                    f"WHERE room_id IN ({placeholders}) "
                    "AND json_extract(data_json, '$.text') LIKE ? ESCAPE '\\' COLLATE NOCASE "
                    "ORDER BY created_at DESC LIMIT ?",
                    (*room_batch, f"%{escaped_query}%", limit),
                ).fetchall()
                matches.extend(self.decode(row[0]) for row in rows)
        matches.sort(key=lambda message: str(message.get("timestamp", "")), reverse=True)
        return matches[:limit]

    def latest_message(self, room_id: str) -> dict | None:
        messages = self.list_messages(room_id, limit=1)
        return messages[-1] if messages else None

    def latest_messages_for_rooms(self, room_ids: list[str]) -> dict[str, dict]:
        if not room_ids:
            return {}
        placeholders = ",".join("?" for _ in room_ids)
        with self.connection() as database:
            rows = database.execute(
                "SELECT room_id, data_json FROM messages WHERE rowid IN ("
                f"SELECT MAX(rowid) FROM messages WHERE room_id IN ({placeholders}) GROUP BY room_id"
                ")",
                tuple(room_ids),
            ).fetchall()
        return {str(row[0]): self.decode(row[1]) for row in rows}

    def attachment_room_ids(self, filename: str) -> set[str]:
        pattern = f'%"url":"/uploads/{filename}"%'
        with self.connection() as database:
            rows = database.execute(
                "SELECT DISTINCT room_id FROM messages WHERE data_json LIKE ?",
                (pattern,),
            ).fetchall()
        return {str(row[0]) for row in rows}

    def publish_event(
        self,
        event: dict,
        recipients: set[str],
        origin_instance_id: str,
        *,
        occurred_at: float | None = None,
        retention: int = 20_000,
    ) -> dict:
        event_id = str(event.get("event_id") or uuid.uuid4().hex)
        timestamp = float(occurred_at if occurred_at is not None else time.time())
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            cursor = database.execute(
                "INSERT OR IGNORE INTO realtime_events(event_id, event_type, room_id, occurred_at, origin_instance_id, data_json) "
                "VALUES(?, ?, ?, ?, ?, '{}')",
                (event_id, str(event.get("type", "")), str(event.get("roomId", "")), timestamp, origin_instance_id),
            )
            if cursor.rowcount == 0:
                row = database.execute(
                    "SELECT data_json FROM realtime_events WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                return self.decode(row[0])
            sequence = int(cursor.lastrowid)
            durable_event = {
                **event,
                "event_id": event_id,
                "revision": sequence,
                "occurred_at": event.get("occurred_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp)),
                "origin_instance_id": origin_instance_id,
            }
            database.execute(
                "UPDATE realtime_events SET data_json=? WHERE sequence=?",
                (self.encode(durable_event), sequence),
            )
            database.executemany(
                "INSERT INTO realtime_event_recipients(sequence, username) VALUES(?, ?)",
                [(sequence, username) for username in sorted(recipients)],
            )
            if retention > 0 and sequence % 100 == 0:
                database.execute("DELETE FROM realtime_events WHERE sequence<=?", (max(0, sequence - retention),))
        return durable_event

    def latest_event_sequence(self) -> int:
        with self.connection() as database:
            row = database.execute("SELECT COALESCE(MAX(sequence), 0) FROM realtime_events").fetchone()
        return int(row[0])

    def list_events_after(self, sequence: int, *, limit: int = 500) -> list[tuple[dict, set[str]]]:
        with self.connection() as database:
            rows = database.execute(
                "SELECT sequence, data_json FROM realtime_events WHERE sequence>? ORDER BY sequence LIMIT ?",
                (sequence, limit),
            ).fetchall()
            result = []
            for event_sequence, data_json in rows:
                recipients = {
                    str(row[0])
                    for row in database.execute(
                        "SELECT username FROM realtime_event_recipients WHERE sequence=?",
                        (event_sequence,),
                    )
                }
                result.append((self.decode(data_json), recipients))
        return result

    def events_for_user_after(self, username: str, sequence: int, *, limit: int = 500) -> list[dict]:
        with self.connection() as database:
            rows = database.execute(
                "SELECT events.data_json FROM realtime_events AS events "
                "JOIN realtime_event_recipients AS recipients ON recipients.sequence=events.sequence "
                "WHERE recipients.username=? AND events.sequence>? ORDER BY events.sequence LIMIT ?",
                (username, sequence, limit),
            ).fetchall()
        return [self.decode(row[0]) for row in rows]

    @staticmethod
    def _presence_for_user(database: sqlite3.Connection, username: str, now: float) -> dict:
        rows = database.execute(
            "SELECT active_room_id, emoji, updated_at FROM presence_leases "
            "WHERE username=? AND expires_at>? ORDER BY updated_at",
            (username, now),
        ).fetchall()
        active_room_ids = sorted({str(row[0]) for row in rows if row[0]})
        emoji_rows = [row for row in rows if row[1]]
        emoji = str(emoji_rows[-1][1]) if emoji_rows else ""
        return {"online": bool(rows), "active_room_ids": active_room_ids, "emoji": emoji}

    def touch_presence(
        self,
        lease_id: str,
        instance_id: str,
        username: str,
        active_room_id: str,
        emoji: str,
        ttl_seconds: int,
    ) -> tuple[dict, bool]:
        now = time.time()
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            before = self._presence_for_user(database, username, now)
            database.execute("DELETE FROM presence_leases WHERE expires_at<=?", (now,))
            database.execute(
                "INSERT INTO presence_leases(lease_id, instance_id, username, active_room_id, emoji, updated_at, expires_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT(lease_id) DO UPDATE SET "
                "instance_id=excluded.instance_id, username=excluded.username, active_room_id=excluded.active_room_id, "
                "emoji=excluded.emoji, updated_at=excluded.updated_at, expires_at=excluded.expires_at",
                (lease_id, instance_id, username, active_room_id, emoji, now, now + ttl_seconds),
            )
            current = self._presence_for_user(database, username, now)
        return current, current != before

    def disconnect_presence(self, lease_id: str, username: str) -> tuple[dict, bool]:
        now = time.time()
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            before = self._presence_for_user(database, username, now)
            database.execute("DELETE FROM presence_leases WHERE lease_id=?", (lease_id,))
            current = self._presence_for_user(database, username, now)
        return current, current != before

    def presence_for_user(self, username: str) -> dict:
        now = time.time()
        with self.connection() as database:
            return self._presence_for_user(database, username, now)

    def presence_for_users(self, usernames: list[str]) -> dict[str, dict]:
        if not usernames:
            return {}
        now = time.time()
        placeholders = ",".join("?" for _ in usernames)
        with self.connection() as database:
            rows = database.execute(
                "SELECT username, active_room_id, emoji, updated_at FROM presence_leases "
                f"WHERE username IN ({placeholders}) AND expires_at>? ORDER BY username, updated_at",
                (*usernames, now),
            ).fetchall()
        grouped: dict[str, list[tuple[str, str, float]]] = {username: [] for username in usernames}
        for username, active_room_id, emoji, updated_at in rows:
            grouped.setdefault(str(username), []).append((str(active_room_id), str(emoji), float(updated_at)))
        result = {}
        for username, leases in grouped.items():
            active_room_ids = sorted({lease[0] for lease in leases if lease[0]})
            emoji_rows = [lease for lease in leases if lease[1]]
            result[username] = {
                "online": bool(leases),
                "active_room_ids": active_room_ids,
                "emoji": emoji_rows[-1][1] if emoji_rows else "",
            }
        return result

    def cleanup_expired_presence(self) -> list[tuple[str, dict]]:
        now = time.time()
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            usernames = {
                str(row[0])
                for row in database.execute("SELECT DISTINCT username FROM presence_leases WHERE expires_at<=?", (now,))
            }
            database.execute("DELETE FROM presence_leases WHERE expires_at<=?", (now,))
            changes = [
                (username, self._presence_for_user(database, username, now))
                for username in sorted(usernames)
            ]
        return changes

    def verify(self) -> dict[str, int]:
        with self.connection() as database:
            counts = {
                table: int(database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in (
                    "users", "friendships", "rooms", "room_members", "messages",
                    "read_positions", "sessions", "shorts_catalog", "shorts_collection_state",
                    "realtime_events", "presence_leases",
                )
            }
            counts["foreign_key_errors"] = len(database.execute("PRAGMA foreign_key_check").fetchall())
        return counts

    @staticmethod
    def encode(value: dict) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def decode(value: str) -> dict:
        return json.loads(value)

    @staticmethod
    def direct_room_key(room: dict) -> str | None:
        participant_ids = sorted({str(item) for item in room.get("participant_ids", []) if item})
        if room.get("kind") != "direct" or len(participant_ids) != 2:
            return None
        return ":".join(participant_ids)

    def _upsert_room(self, database: sqlite3.Connection, room: dict) -> None:
        database.execute(
            "INSERT INTO rooms(id, kind, created_by, updated_at, direct_key, data_json) VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, created_by=excluded.created_by, "
            "updated_at=excluded.updated_at, direct_key=excluded.direct_key, data_json=excluded.data_json",
            (
                room["id"], room.get("kind", "group"), room.get("created_by", ""),
                room.get("updated_at", ""), self.direct_room_key(room), self.encode(room),
            ),
        )

    def _insert_message(self, database: sqlite3.Connection, message: dict, sender_id: str) -> bool:
        cursor = database.execute(
            "INSERT OR IGNORE INTO messages(id, room_id, sender_id, sender_username, client_message_id, created_at, data_json) "
            "VALUES(?, ?, ?, ?, NULLIF(?, ''), ?, ?)",
            (
                message["id"], message["room_id"], sender_id, message.get("username", ""),
                message.get("client_message_id", ""), message.get("timestamp", ""), self.encode(message),
            ),
        )
        return cursor.rowcount == 1
