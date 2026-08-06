from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8765"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
STATE_FILE = Path(os.getenv("STATE_FILE", str(DATA_DIR / "chat_state.json")))
MAX_MESSAGES_PER_ROOM = 200
SESSION_COOKIE_NAME = "codex_talk_session"
PASSWORD_ITERATIONS = 100_000
PHONE_CODE_TTL_SECONDS = 180
PHONE_TOKEN_TTL_SECONDS = 900
OAUTH_STATE_TTL_SECONDS = 600
PHONE_VERIFICATION_MODE = os.getenv("PHONE_VERIFICATION_MODE", "dev")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "").strip()
KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET", "").strip()
KAKAO_REDIRECT_URI = os.getenv("KAKAO_REDIRECT_URI", "").strip()
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "").strip()
SOCIAL_DEMO_LOGIN_ENABLED = os.getenv("SOCIAL_DEMO_LOGIN_ENABLED", "true").lower() != "false"
SUBSCRIBERS: set[queue.Queue] = set()
SUBSCRIBERS_LOCK = threading.Lock()
APP_NAME = "FREE DANNY"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt_hex = salt_hex or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        PASSWORD_ITERATIONS,
    ).hex()
    return salt_hex, digest


def default_public_base_url() -> str:
    return f"http://localhost:{PORT}"


def make_cookie_header(token: str, *, max_age: int | None = None, secure: bool = False) -> str:
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE_NAME] = token
    cookie[SESSION_COOKIE_NAME]["path"] = "/"
    cookie[SESSION_COOKIE_NAME]["httponly"] = True
    cookie[SESSION_COOKIE_NAME]["samesite"] = "Lax"
    if secure:
        cookie[SESSION_COOKIE_NAME]["secure"] = True
    if max_age is not None:
        cookie[SESSION_COOKIE_NAME]["max-age"] = str(max_age)
    return cookie.output(header="").strip()


def normalize_phone(phone: str) -> str:
    digits = "".join(character for character in phone if character.isdigit())
    if len(digits) not in (10, 11):
        return ""
    if not digits.startswith("0"):
        return ""
    return digits


def mask_phone(phone: str) -> str:
    normalized = normalize_phone(phone)
    if not normalized:
        return ""
    if len(normalized) == 10:
        return f"{normalized[:3]}-***-{normalized[-3:]}"
    return f"{normalized[:3]}-****-{normalized[-4:]}"


def sanitize_username_seed(value: str) -> str:
    allowed: list[str] = []
    for character in value.strip():
        if character.isalnum() or character in {"_", "-", "."}:
            allowed.append(character)
        elif "\uac00" <= character <= "\ud7a3":
            allowed.append(character)
        if len(allowed) >= 18:
            break
    return "".join(allowed)


def build_status_message(provider: str) -> str:
    labels = {
        "local": "비밀번호 계정으로 접속 중",
        "kakao": "카카오로 접속 중",
        "google": "구글로 접속 중",
        "demo": "개발용 SNS로 접속 중",
    }
    return labels.get(provider, "메신저에 접속 중")


def fetch_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> dict:
    request = Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="ignore")
        raise ValueError(body or f"HTTP {error.code}") from error
    except URLError as error:
        raise ConnectionError(str(error.reason)) from error


class SessionStore:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, str] = {}

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions[token] = username
        return token

    def get_username(self, token: str | None) -> str | None:
        if not token:
            return None
        with self.lock:
            return self.sessions.get(token)

    def destroy(self, token: str | None) -> None:
        if not token:
            return
        with self.lock:
            self.sessions.pop(token, None)


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


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.state = self._load_state()

    def _default_rooms(self) -> list[dict]:
        created_at = utc_now_iso()
        return [
            self._new_room("lobby", "로비", "처음 인사를 나누는 기본 채팅방입니다.", "system", created_at),
            self._new_room("study", "스터디", "프로젝트와 공부 이야기를 모아두는 공간입니다.", "system", created_at),
            self._new_room("random", "자유", "가볍게 대화하고 근황을 나누는 공간입니다.", "system", created_at),
        ]

    def _default_state(self) -> dict:
        rooms = self._default_rooms()
        return {
            "users": [],
            "rooms": rooms,
            "messages": {room["id"]: [] for room in rooms},
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
            "created_by": created_by,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

    def _write_state(self, state: dict) -> None:
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.path)

    def _migrate_state(self, state: dict) -> dict:
        state.setdefault("users", [])
        state.setdefault("rooms", [])
        state.setdefault("messages", {})

        if not isinstance(state["users"], list) or not isinstance(state["rooms"], list) or not isinstance(state["messages"], dict):
            return self._default_state()

        for user in state["users"]:
            user.setdefault("id", new_id("user"))
            user.setdefault("status_message", "")
            user.setdefault("phone", "")
            user.setdefault("auth_provider", "local")
            user.setdefault("provider_user_id", "")
            user.setdefault("created_at", utc_now_iso())

        defaults_by_id = {room["id"]: room for room in self._default_rooms()}
        for room in state["rooms"]:
            room.setdefault("description", "")
            room.setdefault("created_by", "system")
            room.setdefault("created_at", utc_now_iso())
            room.setdefault("updated_at", room["created_at"])
            state["messages"].setdefault(room["id"], [])
            default_room = defaults_by_id.get(room.get("id"))
            if default_room and room.get("created_by") == "system":
                room["name"] = default_room["name"]
                room["description"] = default_room["description"]

        if not state["rooms"]:
            return self._default_state()

        for room_id, messages in list(state["messages"].items()):
            if not isinstance(messages, list):
                state["messages"][room_id] = []

        return state

    def _load_state(self) -> dict:
        if not self.path.exists():
            state = self._default_state()
            self._write_state(state)
            return state

        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = self._default_state()
            self._write_state(state)
            return state

        if not isinstance(state, dict):
            state = self._default_state()
        else:
            state = self._migrate_state(state)

        self._write_state(state)
        return state

    def _save_locked(self) -> None:
        self._write_state(self.state)

    def _user_public(self, user: dict) -> dict:
        provider = user.get("auth_provider", "local")
        return {
            "id": user["id"],
            "username": user["username"],
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
        }

    def _room_summary(self, room: dict) -> dict:
        messages = self.state["messages"].get(room["id"], [])
        participants = {message["username"] for message in messages if message.get("username")}
        if room.get("created_by") and room["created_by"] != "system":
            participants.add(room["created_by"])
        last_message = messages[-1] if messages else None
        return {
            "id": room["id"],
            "name": room["name"],
            "description": room["description"],
            "created_by": room["created_by"],
            "created_at": room["created_at"],
            "updated_at": room["updated_at"],
            "participant_count": len(participants),
            "message_count": len(messages),
            "last_message": last_message,
        }

    def get_user_record(self, username: str) -> dict | None:
        with self.lock:
            return next((item for item in self.state["users"] if item["username"] == username), None)

    def get_user_public(self, username: str) -> dict | None:
        user = self.get_user_record(username)
        if user is None:
            return None
        return self._user_public(user)

    def find_social_user(self, provider: str, provider_user_id: str) -> dict | None:
        with self.lock:
            return next(
                (
                    item
                    for item in self.state["users"]
                    if item.get("auth_provider") == provider and item.get("provider_user_id") == provider_user_id
                ),
                None,
            )

    def _unique_username_locked(self, seed: str, provider: str, provider_user_id: str) -> str:
        base_seed = sanitize_username_seed(seed) or f"{provider}_{provider_user_id[-6:]}"
        base = base_seed[:18]
        if len(base) < 2:
            base = f"{provider}_{provider_user_id[-4:]}"

        candidate = base
        index = 1
        existing_names = {user["username"] for user in self.state["users"]}
        while candidate in existing_names:
            suffix = f"_{index}"
            candidate = f"{base[: max(2, 24 - len(suffix))]}{suffix}"
            index += 1
        return candidate[:24]

    def list_rooms(self) -> list[dict]:
        with self.lock:
            rooms = [self._room_summary(room) for room in self.state["rooms"]]
        return sorted(rooms, key=lambda room: room["updated_at"], reverse=True)

    def get_bootstrap(self, user: dict) -> dict:
        rooms = self.list_rooms()
        return {
            "app_name": APP_NAME,
            "user": self._user_public(user),
            "rooms": rooms,
            "selected_room_id": rooms[0]["id"] if rooms else None,
        }

    def get_messages(self, room_id: str) -> list[dict] | None:
        with self.lock:
            if room_id not in self.state["messages"]:
                return None
            return list(self.state["messages"][room_id])

    def create_local_user(
        self,
        username: str,
        password: str,
        status_message: str,
        phone: str,
    ) -> tuple[dict | None, str | None]:
        normalized_username = username.strip()[:24]
        normalized_phone = normalize_phone(phone)

        if len(normalized_username) < 2:
            return None, "사용자 이름은 2자 이상이어야 합니다."
        if len(password) < 4:
            return None, "비밀번호는 4자 이상이어야 합니다."
        if not normalized_phone:
            return None, "휴대폰 인증을 완료해 주세요."

        with self.lock:
            existing = next((item for item in self.state["users"] if item["username"] == normalized_username), None)
            if existing is not None:
                return None, "이미 존재하는 사용자 이름입니다."

            salt_hex, digest = hash_password(password)
            user = {
                "id": new_id("user"),
                "username": normalized_username,
                "status_message": status_message.strip()[:40] or build_status_message("local"),
                "phone": normalized_phone,
                "auth_provider": "local",
                "provider_user_id": "",
                "password_salt": salt_hex,
                "password_hash": digest,
                "created_at": utc_now_iso(),
            }
            self.state["users"].append(user)
            self._save_locked()
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
            user = next(
                (
                    item
                    for item in self.state["users"]
                    if item.get("auth_provider") == provider and item.get("provider_user_id") == provider_user_id
                ),
                None,
            )
            if user is None:
                username = self._unique_username_locked(nickname, provider, provider_user_id)
                user = {
                    "id": new_id("user"),
                    "username": username,
                    "status_message": (status_message or build_status_message(provider))[:40],
                    "phone": "",
                    "auth_provider": provider,
                    "provider_user_id": provider_user_id,
                    "password_salt": "",
                    "password_hash": "",
                    "created_at": utc_now_iso(),
                }
                self.state["users"].append(user)
            else:
                if status_message:
                    user["status_message"] = status_message[:40]
            self._save_locked()
            return self._user_public(user)

    def authenticate_user(self, username: str, password: str) -> dict | None:
        normalized_username = username.strip()
        with self.lock:
            user = next((item for item in self.state["users"] if item["username"] == normalized_username), None)
            if user is None or not user.get("password_hash"):
                return None
            _, digest = hash_password(password, user["password_salt"])
            if not hmac.compare_digest(digest, user["password_hash"]):
                return None
            return self._user_public(user)

    def create_room(self, name: str, description: str, created_by: str) -> dict:
        with self.lock:
            room = self._new_room(new_id("room"), name[:32], description[:100], created_by[:24])
            self.state["rooms"].append(room)
            self.state["messages"][room["id"]] = []
            self._save_locked()
            return self._room_summary(room)

    def add_message(self, room_id: str, username: str, text: str) -> tuple[dict, dict] | None:
        with self.lock:
            room = next((item for item in self.state["rooms"] if item["id"] == room_id), None)
            if room is None:
                return None

            message = {
                "id": new_id("msg"),
                "room_id": room_id,
                "username": username[:24],
                "text": text[:300],
                "timestamp": utc_now_iso(),
            }
            room_messages = self.state["messages"][room_id]
            room_messages.append(message)
            if len(room_messages) > MAX_MESSAGES_PER_ROOM:
                del room_messages[:-MAX_MESSAGES_PER_ROOM]
            room["updated_at"] = message["timestamp"]
            self._save_locked()
            return message, self._room_summary(room)


def push_event(event: dict) -> None:
    with SUBSCRIBERS_LOCK:
        dead_subscribers = []
        for subscriber in SUBSCRIBERS:
            try:
                subscriber.put_nowait(event)
            except Exception:
                dead_subscribers.append(subscriber)
        for subscriber in dead_subscribers:
            SUBSCRIBERS.discard(subscriber)


def social_provider_config(google_redirect_uri: str, kakao_redirect_uri: str) -> dict:
    return {
        "google": {
            "name": "구글",
            "enabled": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
            "configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
            "login_url": "/auth/google/start",
            "redirect_uri": google_redirect_uri,
            "mode": "oauth",
        },
        "kakao": {
            "name": "카카오",
            "enabled": bool(KAKAO_REST_API_KEY),
            "configured": bool(KAKAO_REST_API_KEY),
            "login_url": "/auth/kakao/start",
            "redirect_uri": kakao_redirect_uri,
            "mode": "oauth",
        },
        "demo": {
            "name": "개발용 SNS",
            "enabled": SOCIAL_DEMO_LOGIN_ENABLED,
            "configured": SOCIAL_DEMO_LOGIN_ENABLED,
            "login_url": "/auth/demo-login",
            "mode": "local",
        },
    }


STORE = StateStore(STATE_FILE)
SESSIONS = SessionStore()
PHONE_VERIFICATIONS = PhoneVerificationStore()
OAUTH_STATES = OAuthStateStore()


class ChatHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def request_scheme(self) -> str:
        forwarded_proto = (self.headers.get("X-Forwarded-Proto", "") or "").split(",")[0].strip()
        if forwarded_proto in {"http", "https"}:
            return forwarded_proto
        return "http"

    def request_host(self) -> str:
        forwarded_host = (self.headers.get("X-Forwarded-Host", "") or "").split(",")[0].strip()
        return forwarded_host or (self.headers.get("Host", "") or "").strip()

    def public_base_url(self) -> str:
        if PUBLIC_BASE_URL:
            return PUBLIC_BASE_URL
        host = self.request_host()
        if host:
            return f"{self.request_scheme()}://{host}"
        return default_public_base_url()

    def provider_redirect_uri(self, provider: str) -> str:
        if provider == "google" and GOOGLE_REDIRECT_URI:
            return GOOGLE_REDIRECT_URI
        if provider == "kakao" and KAKAO_REDIRECT_URI:
            return KAKAO_REDIRECT_URI
        return f"{self.public_base_url()}/auth/{provider}/callback"

    def cookie_secure(self) -> bool:
        return self.request_scheme() == "https"

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        if path == "/":
            self.serve_index()
            return
        if path == "/health":
            self.send_json({"ok": True, "app_name": APP_NAME}, HTTPStatus.OK)
            return
        if path == "/session":
            self.serve_session()
            return
        if path == "/bootstrap":
            user = self.require_auth_record()
            if user is None:
                return
            self.send_json(STORE.get_bootstrap(user), HTTPStatus.OK)
            return
        if path == "/messages":
            user = self.require_auth()
            if user is None:
                return
            self.serve_messages(query)
            return
        if path == "/events":
            user = self.require_auth()
            if user is None:
                return
            self.serve_events(user)
            return
        if path == "/auth/providers":
            self.send_json(
                {
                    "providers": social_provider_config(
                        self.provider_redirect_uri("google"),
                        self.provider_redirect_uri("kakao"),
                    ),
                    "app_name": APP_NAME,
                    "public_base_url": self.public_base_url(),
                },
                HTTPStatus.OK,
            )
            return
        if path == "/auth/google/start":
            self.start_google_login()
            return
        if path == "/auth/google/callback":
            self.finish_google_login(query)
            return
        if path == "/auth/kakao/start":
            self.start_kakao_login()
            return
        if path == "/auth/kakao/callback":
            self.finish_kakao_login(query)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        if path == "/signup":
            self.signup()
            return
        if path == "/login":
            self.login()
            return
        if path == "/logout":
            self.logout()
            return
        if path == "/phone/request-code":
            self.request_phone_code()
            return
        if path == "/phone/verify-code":
            self.verify_phone_code()
            return
        if path == "/auth/demo-login":
            self.demo_social_login()
            return
        if path == "/rooms":
            user = self.require_auth()
            if user is None:
                return
            self.create_room(user)
            return
        if path == "/messages":
            user = self.require_auth()
            if user is None:
                return
            self.create_message(user)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def serve_index(self) -> None:
        content = INDEX_FILE.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def serve_session(self) -> None:
        user = self.current_user()
        if user is None:
            self.send_json({"authenticated": False}, HTTPStatus.OK)
            return
        self.send_json({"authenticated": True, "user": user}, HTTPStatus.OK)

    def serve_messages(self, query: dict[str, list[str]]) -> None:
        room_id = query.get("room_id", [""])[0]
        messages = STORE.get_messages(room_id)
        if messages is None:
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return
        self.send_json(messages, HTTPStatus.OK)

    def serve_events(self, user: dict) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        subscriber: queue.Queue = queue.Queue()
        with SUBSCRIBERS_LOCK:
            SUBSCRIBERS.add(subscriber)

        try:
            hello = {"type": "hello", "timestamp": utc_now_iso(), "username": user["username"]}
            self.wfile.write(f"data: {json.dumps(hello, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()

            while True:
                event = subscriber.get()
                payload = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
                self.wfile.write(payload)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with SUBSCRIBERS_LOCK:
                SUBSCRIBERS.discard(subscriber)

    def start_google_login(self) -> None:
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            self.redirect("/?auth_error=google_not_configured")
            return

        state = OAUTH_STATES.create()
        params = {
            "response_type": "code",
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": self.provider_redirect_uri("google"),
            "scope": "openid profile email",
            "state": state,
            "access_type": "online",
            "include_granted_scopes": "true",
            "prompt": "select_account",
        }
        self.redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}")

    def finish_google_login(self, query: dict[str, list[str]]) -> None:
        if "error" in query:
            self.redirect("/?auth_error=google_access_denied")
            return

        code = query.get("code", [""])[0].strip()
        state = query.get("state", [""])[0].strip()
        if not code or not state or not OAUTH_STATES.consume(state):
            self.redirect("/?auth_error=oauth_state_invalid")
            return

        try:
            token_payload = self.request_google_token(code)
            profile_payload = self.request_google_user_profile(token_payload["access_token"])
            google_sub = str(profile_payload.get("sub", "")).strip()
            if not google_sub:
                raise ValueError("구글 사용자 정보를 읽지 못했습니다.")

            nickname = (
                str(profile_payload.get("name", "")).strip()
                or str(profile_payload.get("email", "")).split("@")[0].strip()
                or f"google_{google_sub[-6:]}"
            )
            user = STORE.create_or_update_social_user(
                "google",
                google_sub,
                nickname=nickname,
                status_message="구글로 접속 중",
            )
        except Exception:
            self.redirect("/?auth_error=google_login_failed")
            return

        token = SESSIONS.create(user["username"])
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure()))
        self.end_headers()

    def start_kakao_login(self) -> None:
        if not KAKAO_REST_API_KEY:
            self.redirect("/?auth_error=kakao_not_configured")
            return

        state = OAUTH_STATES.create()
        params = {
            "response_type": "code",
            "client_id": KAKAO_REST_API_KEY,
            "redirect_uri": self.provider_redirect_uri("kakao"),
            "state": state,
        }
        self.redirect(f"https://kauth.kakao.com/oauth/authorize?{urlencode(params)}")

    def finish_kakao_login(self, query: dict[str, list[str]]) -> None:
        if "error" in query:
            self.redirect("/?auth_error=kakao_access_denied")
            return

        code = query.get("code", [""])[0].strip()
        state = query.get("state", [""])[0].strip()
        if not code or not state or not OAUTH_STATES.consume(state):
            self.redirect("/?auth_error=oauth_state_invalid")
            return

        try:
            token_payload = self.request_kakao_token(code)
            profile_payload = self.request_kakao_user_profile(token_payload["access_token"])
            kakao_id = str(profile_payload.get("id", "")).strip()
            if not kakao_id:
                raise ValueError("카카오 사용자 정보를 읽지 못했습니다.")

            kakao_account = profile_payload.get("kakao_account", {}) or {}
            profile = kakao_account.get("profile", {}) or {}
            nickname = str(profile.get("nickname", "")).strip() or f"kakao_{kakao_id[-6:]}"
            user = STORE.create_or_update_social_user(
                "kakao",
                kakao_id,
                nickname=nickname,
                status_message="카카오로 접속 중",
            )
        except Exception:
            self.redirect("/?auth_error=kakao_login_failed")
            return

        token = SESSIONS.create(user["username"])
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure()))
        self.end_headers()

    def request_google_token(self, code: str) -> dict:
        payload = {
            "grant_type": "authorization_code",
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": self.provider_redirect_uri("google"),
            "code": code,
        }
        return fetch_json(
            "https://oauth2.googleapis.com/token",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=urlencode(payload).encode("utf-8"),
        )

    def request_google_user_profile(self, access_token: str) -> dict:
        return fetch_json(
            "https://openidconnect.googleapis.com/v1/userinfo",
            method="GET",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    def request_kakao_token(self, code: str) -> dict:
        payload = {
            "grant_type": "authorization_code",
            "client_id": KAKAO_REST_API_KEY,
            "redirect_uri": self.provider_redirect_uri("kakao"),
            "code": code,
        }
        if KAKAO_CLIENT_SECRET:
            payload["client_secret"] = KAKAO_CLIENT_SECRET

        return fetch_json(
            "https://kauth.kakao.com/oauth/token",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
            data=urlencode(payload).encode("utf-8"),
        )

    def request_kakao_user_profile(self, access_token: str) -> dict:
        return fetch_json(
            "https://kapi.kakao.com/v2/user/me",
            method="GET",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    def demo_social_login(self) -> None:
        self.discard_request_body()
        if not SOCIAL_DEMO_LOGIN_ENABLED:
            self.send_json({"error": "개발용 SNS 로그인이 비활성화되어 있습니다."}, HTTPStatus.BAD_REQUEST)
            return

        suffix = secrets.token_hex(3)
        user = STORE.create_or_update_social_user(
            "demo",
            f"demo-{suffix}",
            nickname=f"demo_{suffix}",
            status_message="개발용 SNS로 접속 중",
        )
        token = SESSIONS.create(user["username"])
        self.send_json(
            {"authenticated": True, "user": user},
            HTTPStatus.OK,
            headers={"Set-Cookie": make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure())},
        )

    def signup(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", "")).strip()
        status_message = str(payload.get("statusMessage", "")).strip()
        phone = str(payload.get("phone", "")).strip()
        verification_token = str(payload.get("verificationToken", "")).strip()

        if not PHONE_VERIFICATIONS.consume(phone, verification_token):
            self.send_json({"error": "휴대폰 인증을 먼저 완료해 주세요."}, HTTPStatus.BAD_REQUEST)
            return

        user, error = STORE.create_local_user(username, password, status_message, phone)
        if error:
            self.send_json({"error": error}, HTTPStatus.BAD_REQUEST)
            return

        token = SESSIONS.create(user["username"])
        self.send_json(
            {"authenticated": True, "user": user},
            HTTPStatus.CREATED,
            headers={"Set-Cookie": make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure())},
        )

    def login(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", "")).strip()
        user = STORE.authenticate_user(username, password)
        if user is None:
            self.send_json({"error": "사용자 이름 또는 비밀번호가 올바르지 않습니다."}, HTTPStatus.UNAUTHORIZED)
            return

        token = SESSIONS.create(user["username"])
        self.send_json(
            {"authenticated": True, "user": user},
            HTTPStatus.OK,
            headers={"Set-Cookie": make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure())},
        )

    def logout(self) -> None:
        self.discard_request_body()
        token = self.read_session_token()
        SESSIONS.destroy(token)
        self.send_json(
            {"authenticated": False},
            HTTPStatus.OK,
            headers={"Set-Cookie": make_cookie_header("", max_age=0, secure=self.cookie_secure())},
        )

    def request_phone_code(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        phone = str(payload.get("phone", "")).strip()
        try:
            verification = PHONE_VERIFICATIONS.request_code(phone)
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return

        response = {
            "ok": True,
            "phoneMasked": verification["phone_masked"],
            "expiresIn": verification["expires_in"],
            "delivery": "dev-preview" if PHONE_VERIFICATION_MODE != "prod" else "sms",
        }
        if PHONE_VERIFICATION_MODE != "prod":
            response["devCode"] = verification["code"]
        self.send_json(response, HTTPStatus.OK)

    def verify_phone_code(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        phone = str(payload.get("phone", "")).strip()
        code = str(payload.get("code", "")).strip()
        token, error = PHONE_VERIFICATIONS.verify_code(phone, code)
        if error:
            self.send_json({"error": error}, HTTPStatus.BAD_REQUEST)
            return

        self.send_json({"ok": True, "phoneMasked": mask_phone(phone), "verificationToken": token}, HTTPStatus.OK)

    def create_room(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        name = str(payload.get("name", "")).strip()
        description = str(payload.get("description", "")).strip()
        if not name:
            self.send_json({"error": "채팅방 이름을 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
            return

        room = STORE.create_room(name, description, user["username"])
        push_event({"type": "room_created", "room": room})
        self.send_json(room, HTTPStatus.CREATED)

    def create_message(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        room_id = str(payload.get("roomId", "")).strip()
        text = str(payload.get("text", "")).strip()
        if not room_id or not text:
            self.send_json({"error": "roomId와 text는 필수입니다."}, HTTPStatus.BAD_REQUEST)
            return

        result = STORE.add_message(room_id, user["username"], text)
        if result is None:
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return

        message, room = result
        push_event({"type": "message_created", "roomId": room_id, "room": room, "message": message})
        self.send_json(message, HTTPStatus.CREATED)

    def current_user(self) -> dict | None:
        token = self.read_session_token()
        username = SESSIONS.get_username(token)
        if username is None:
            return None
        return STORE.get_user_public(username)

    def require_auth(self) -> dict | None:
        user = self.current_user()
        if user is None:
            self.send_json({"error": "로그인이 필요합니다."}, HTTPStatus.UNAUTHORIZED)
            return None
        return user

    def require_auth_record(self) -> dict | None:
        public_user = self.require_auth()
        if public_user is None:
            return None
        user = STORE.get_user_record(public_user["username"])
        if user is None:
            self.send_json({"error": "사용자를 찾을 수 없습니다."}, HTTPStatus.UNAUTHORIZED)
            return None
        return user

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()

    def read_session_token(self) -> str | None:
        raw_cookie = self.headers.get("Cookie", "")
        cookie = SimpleCookie()
        cookie.load(raw_cookie)
        morsel = cookie.get(SESSION_COOKIE_NAME)
        return morsel.value if morsel else None

    def read_json_body(self) -> dict | None:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(payload, dict):
            self.send_json({"error": "JSON object expected"}, HTTPStatus.BAD_REQUEST)
            return None
        return payload

    def discard_request_body(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > 0:
            self.rfile.read(content_length)

    def send_json(self, data: object, status: HTTPStatus, headers: dict[str, str] | None = None) -> None:
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), ChatHandler)
    print(f"{APP_NAME} running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
