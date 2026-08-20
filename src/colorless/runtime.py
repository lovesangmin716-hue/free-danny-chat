from __future__ import annotations

import hashlib
import hmac
import mimetypes
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta

from .config import (
    MAX_SESSIONS,
    OAUTH_STATE_TTL_SECONDS,
    PHONE_CODE_TTL_SECONDS,
    PHONE_TOKEN_TTL_SECONDS,
    PRESENCE_TTL_SECONDS,
    SESSION_CLEANUP_INTERVAL_SECONDS,
    SESSION_TTL_SECONDS,
    UPLOAD_GRANT_TTL_SECONDS,
)
from .utils import mask_phone, normalize_phone, utc_now


class SessionStore:
    def __init__(
        self,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        max_sessions: int = MAX_SESSIONS,
        state_store: StateStore | None = None,
    ) -> None:
        self.lock = threading.Lock()
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.state_store = state_store
        self.sessions: dict[str, dict] = {}
        self.cleanup_deadline = 0.0

    @staticmethod
    def _token_hash(token: str) -> str:
        # Persist only a one-way digest so a leaked state file cannot be used as an auth cookie.
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _cleanup_locked(self, now: float) -> None:
        expired = [token for token, session in self.sessions.items() if session["expires_at"] <= now]
        for token in expired:
            self.sessions.pop(token, None)

        overflow = len(self.sessions) - self.max_sessions
        if overflow > 0:
            oldest = sorted(self.sessions, key=lambda token: self.sessions[token]["created_at"])[:overflow]
            for token in oldest:
                self.sessions.pop(token, None)
        self.cleanup_deadline = now + SESSION_CLEANUP_INTERVAL_SECONDS

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = self._token_hash(token)
        if self.state_store is not None:
            self.state_store.create_session(token_hash, username, self.ttl_seconds, self.max_sessions)
            return token

        now = time.time()
        with self.lock:
            self._cleanup_locked(now)
            self.sessions[token_hash] = {
                "username": username,
                "created_at": now,
                "expires_at": now + self.ttl_seconds,
            }
            self._cleanup_locked(now)
        return token

    def get_username(self, token: str | None) -> str | None:
        if not token:
            return None
        token_hash = self._token_hash(token)
        if self.state_store is not None:
            return self.state_store.get_session_username(token_hash, self.ttl_seconds)

        now = time.time()
        with self.lock:
            if now >= self.cleanup_deadline:
                self._cleanup_locked(now)
            session = self.sessions.get(token_hash)
            if session is None or session["expires_at"] <= now:
                self.sessions.pop(token_hash, None)
                return None
            session["expires_at"] = now + self.ttl_seconds
            return str(session["username"])

    def switch_identity(self, token: str | None, user_id: str) -> str | None:
        if not token or self.state_store is None:
            return None
        token_hash = self._token_hash(token)
        return self.state_store.switch_session_identity(token_hash, user_id)

    def destroy(self, token: str | None) -> None:
        if not token:
            return
        token_hash = self._token_hash(token)
        if self.state_store is not None:
            self.state_store.destroy_session(token_hash)
            return
        with self.lock:
            self.sessions.pop(token_hash, None)

class UploadGrantStore:
    def __init__(self, ttl_seconds: int = UPLOAD_GRANT_TTL_SECONDS) -> None:
        self.lock = threading.Lock()
        self.ttl_seconds = ttl_seconds
        self.grants: dict[str, dict] = {}

    def _cleanup_locked(self, now: float) -> list[dict]:
        expired: list[dict] = []
        for filename, grant in list(self.grants.items()):
            if float(grant["expires_at"]) <= now:
                expired.append(self.grants.pop(filename))
        return expired

    def create(self, filename: str, username: str) -> None:
        now = time.monotonic()
        with self.lock:
            self._cleanup_locked(now)
            self.grants[filename] = {
                "filename": filename,
                "username": username,
                "expires_at": now + self.ttl_seconds,
                "state": "completed",
                "token_hash": "",
                "name": filename,
                "type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
                "size": 0,
                "kind": "file",
                "duration_ms": 0,
            }

    def create_pending(
        self,
        filename: str,
        username: str,
        *,
        name: str,
        content_type: str,
        size: int,
        kind: str = "file",
        duration_ms: int = 0,
    ) -> str | None:
        now = time.monotonic()
        token = secrets.token_urlsafe(32)
        with self.lock:
            self._cleanup_locked(now)
            pending = [
                grant for grant in self.grants.values()
                if grant["username"] == username and grant["state"] == "pending"
            ]
            if len(pending) >= 10 or sum(int(grant["size"]) for grant in pending) + size > 64 * 1024 * 1024:
                return None
            self.grants[filename] = {
                "filename": filename,
                "username": username,
                "expires_at": now + self.ttl_seconds,
                "state": "pending",
                "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                "name": name,
                "type": content_type,
                "size": size,
                "kind": kind,
                "duration_ms": duration_ms,
            }
        return token

    def authorize_transfer(self, filename: str, username: str, token: str) -> dict | None:
        now = time.monotonic()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.lock:
            self._cleanup_locked(now)
            grant = self.grants.get(filename)
            if (
                grant is None
                or grant["state"] != "pending"
                or not hmac.compare_digest(str(grant["username"]), username)
                or not hmac.compare_digest(str(grant["token_hash"]), token_hash)
            ):
                return None
            return dict(grant)

    def complete(self, filename: str, username: str, *, size: int, content_type: str) -> dict | None:
        now = time.monotonic()
        with self.lock:
            self._cleanup_locked(now)
            grant = self.grants.get(filename)
            if (
                grant is None
                or grant["state"] not in {"pending", "completed"}
                or not hmac.compare_digest(str(grant["username"]), username)
                or int(grant["size"]) != size
                or str(grant["type"]) != content_type
            ):
                return None
            grant["state"] = "completed"
            grant["expires_at"] = now + self.ttl_seconds
            return dict(grant)

    def fail(self, filename: str, username: str) -> None:
        with self.lock:
            grant = self.grants.get(filename)
            if grant is not None and hmac.compare_digest(str(grant["username"]), username):
                grant["state"] = "failed"

    def get(self, filename: str, username: str) -> dict | None:
        now = time.monotonic()
        with self.lock:
            self._cleanup_locked(now)
            grant = self.grants.get(filename)
            if grant is None or not hmac.compare_digest(str(grant["username"]), username):
                return None
            return dict(grant)

    def pop_expired(self) -> list[dict]:
        with self.lock:
            return self._cleanup_locked(time.monotonic())

    def owns(self, filename: str, username: str) -> bool:
        now = time.monotonic()
        with self.lock:
            self._cleanup_locked(now)
            grant = self.grants.get(filename)
            return (
                grant is not None
                and grant["state"] == "completed"
                and hmac.compare_digest(str(grant["username"]), username)
            )

    def consume(self, filename: str, username: str) -> bool:
        now = time.monotonic()
        with self.lock:
            self._cleanup_locked(now)
            grant = self.grants.get(filename)
            if (
                grant is None
                or grant["state"] != "completed"
                or not hmac.compare_digest(str(grant["username"]), username)
            ):
                return False
            self.grants.pop(filename, None)
            return True

    def discard(self, filename: str, username: str) -> dict | None:
        now = time.monotonic()
        with self.lock:
            self._cleanup_locked(now)
            grant = self.grants.get(filename)
            if grant is None or not hmac.compare_digest(str(grant["username"]), username):
                return None
            return dict(self.grants.pop(filename))

    def restore(self, grant: dict) -> None:
        restored = dict(grant)
        restored["expires_at"] = time.monotonic() + self.ttl_seconds
        with self.lock:
            self.grants[str(restored["filename"])] = restored

class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.attempts: dict[str, deque[float]] = {}

    def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self.lock:
            if key not in self.attempts and len(self.attempts) >= 10_000:
                self.attempts.pop(next(iter(self.attempts)))
            timestamps = self.attempts.setdefault(key, deque())
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= limit:
                retry_after = max(1, int(window_seconds - (now - timestamps[0])))
                return False, retry_after
            timestamps.append(now)
            return True, 0

class PresenceStore:
    def __init__(self, repository, instance_id: str, ttl_seconds: int = PRESENCE_TTL_SECONDS) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, dict] = {}
        self.tokens_by_username: dict[str, set[str]] = {}
        self.repository = repository
        self.instance_id = instance_id
        self.ttl_seconds = ttl_seconds

    def _lease_id(self, token: str) -> str:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"{self.instance_id}:{digest}"

    def _remove_token_locked(self, token: str, username: str) -> None:
        tokens = self.tokens_by_username.get(username)
        if tokens is None:
            return
        tokens.discard(token)
        if not tokens:
            self.tokens_by_username.pop(username, None)

    def connect(self, token: str | None, username: str) -> bool:
        if not token:
            return False
        with self.lock:
            entry = self.sessions.get(token)
            if entry is None:
                entry = {"username": username, "connections": 0, "active_room_id": "", "emoji": "", "updated_at": 0.0}
                self.sessions[token] = entry
                self.tokens_by_username.setdefault(username, set()).add(token)
            elif entry["username"] != username:
                self._remove_token_locked(token, entry["username"])
                self.tokens_by_username.setdefault(username, set()).add(token)
            entry["username"] = username
            entry["connections"] += 1
            entry["updated_at"] = time.monotonic()
            active_room_id = entry["active_room_id"]
            emoji = entry["emoji"]
        _, changed = self.repository.touch_presence(
            self._lease_id(token), self.instance_id, username,
            active_room_id, emoji, self.ttl_seconds,
        )
        return changed

    def disconnect(self, token: str | None) -> tuple[str, bool]:
        if not token:
            return "", False
        with self.lock:
            entry = self.sessions.get(token)
            if entry is None:
                return "", False
            username = entry["username"]
            entry["connections"] = max(0, entry["connections"] - 1)
            remove_shared_lease = entry["connections"] == 0
            if remove_shared_lease:
                self.sessions.pop(token, None)
                self._remove_token_locked(token, username)
        if not remove_shared_lease:
            return username, False
        _, changed = self.repository.disconnect_presence(self._lease_id(token), username)
        return username, changed

    def update(self, token: str | None, username: str, active_room_id: str, emoji: str) -> bool:
        if not token:
            return False
        with self.lock:
            entry = self.sessions.get(token)
            if entry is None:
                entry = {"username": username, "connections": 0, "active_room_id": "", "emoji": "", "updated_at": 0.0}
                self.sessions[token] = entry
                self.tokens_by_username.setdefault(username, set()).add(token)
            elif entry["username"] != username:
                self._remove_token_locked(token, entry["username"])
                self.tokens_by_username.setdefault(username, set()).add(token)
            changed = entry["active_room_id"] != active_room_id or entry["emoji"] != emoji
            entry["username"] = username
            entry["active_room_id"] = active_room_id
            entry["emoji"] = emoji
            entry["updated_at"] = time.monotonic()
        _, shared_changed = self.repository.touch_presence(
            self._lease_id(token), self.instance_id, username,
            active_room_id, emoji, self.ttl_seconds,
        )
        return changed or shared_changed

    def heartbeat(self, token: str | None) -> None:
        if not token:
            return
        with self.lock:
            entry = self.sessions.get(token)
            if entry is None or entry["connections"] <= 0:
                return
            username = entry["username"]
            active_room_id = entry["active_room_id"]
            emoji = entry["emoji"]
        self.repository.touch_presence(
            self._lease_id(token), self.instance_id, username,
            active_room_id, emoji, self.ttl_seconds,
        )

    def for_user(self, username: str) -> dict:
        return self.repository.presence_for_user(username)

    def set_demo_active(self, username: str, emoji: str) -> None:
        token = f"demo:{username}"
        self.repository.touch_presence(
            self._lease_id(token), self.instance_id, username, "", emoji, self.ttl_seconds,
        )

class OAuthStateStore:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.states: dict[str, datetime] = {}

    def create(self) -> str:
        value = secrets.token_urlsafe(24)
        with self.lock:
            self._cleanup_locked()
            self.states[value] = utc_now() + timedelta(seconds=OAUTH_STATE_TTL_SECONDS)
        return value

    def consume(self, value: str) -> bool:
        with self.lock:
            self._cleanup_locked()
            expires_at = self.states.pop(value, None)
            return expires_at is not None

    def _cleanup_locked(self) -> None:
        now = utc_now()
        expired = [state for state, expires_at in self.states.items() if expires_at <= now]
        for state in expired:
            self.states.pop(state, None)

class PhoneVerificationStore:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: dict[str, dict] = {}
        self.verified_tokens: dict[str, dict] = {}

    def _cleanup_locked(self) -> None:
        now = utc_now()
        expired_pending = [phone for phone, item in self.pending.items() if item["expires_at"] <= now]
        for phone in expired_pending:
            self.pending.pop(phone, None)

        expired_tokens = [token for token, item in self.verified_tokens.items() if item["expires_at"] <= now]
        for token in expired_tokens:
            self.verified_tokens.pop(token, None)

    def request_code(self, phone: str) -> dict:
        normalized = normalize_phone(phone)
        if not normalized:
            raise ValueError("휴대폰 번호 형식이 올바르지 않습니다.")

        code = f"{secrets.randbelow(1_000_000):06d}"
        now = utc_now()
        expires_at = now + timedelta(seconds=PHONE_CODE_TTL_SECONDS)

        with self.lock:
            self._cleanup_locked()
            self.pending[normalized] = {
                "code": code,
                "created_at": now,
                "expires_at": expires_at,
            }

        return {
            "phone": normalized,
            "phone_masked": mask_phone(normalized),
            "code": code,
            "expires_in": PHONE_CODE_TTL_SECONDS,
        }

    def verify_code(self, phone: str, code: str) -> tuple[str | None, str | None]:
        normalized = normalize_phone(phone)
        if not normalized:
            return None, "휴대폰 번호 형식이 올바르지 않습니다."

        entered = "".join(character for character in code if character.isdigit())[:6]
        if len(entered) != 6:
            return None, "인증번호 6자리를 입력해 주세요."

        with self.lock:
            self._cleanup_locked()
            pending = self.pending.get(normalized)
            if pending is None:
                return None, "먼저 인증번호를 요청해 주세요."
            if pending["code"] != entered:
                return None, "인증번호가 일치하지 않습니다."

            self.pending.pop(normalized, None)
            token = secrets.token_urlsafe(24)
            self.verified_tokens[token] = {
                "phone": normalized,
                "expires_at": utc_now() + timedelta(seconds=PHONE_TOKEN_TTL_SECONDS),
            }
            return token, None

    def consume(self, phone: str, token: str) -> bool:
        normalized = normalize_phone(phone)
        if not normalized or not token:
            return False

        with self.lock:
            self._cleanup_locked()
            verified = self.verified_tokens.get(token)
            if verified is None or verified["phone"] != normalized:
                return False
            self.verified_tokens.pop(token, None)
            return True
