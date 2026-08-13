from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent


def load_local_env(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_local_env(BASE_DIR / ".env")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8765"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
INDEX_FILE = BASE_DIR / "index.html"
ASSETS_DIR = BASE_DIR / "assets"
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
STATE_FILE = Path(os.getenv("STATE_FILE", str(DATA_DIR / "chat_state.json")))
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(DATA_DIR / "uploads")))
MAX_MESSAGES_PER_ROOM = 200
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENT_REQUEST_BYTES = 12 * 1024 * 1024
ATTACHMENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}
PROFILE_PIXEL_SIDE = 32
PROFILE_PIXEL_COUNT = PROFILE_PIXEL_SIDE * PROFILE_PIXEL_SIDE
PROFILE_PALETTE = ("#ffffff", "#000000", "#777777", "#d9d9d9", "#e53935", "#fb8c00", "#fdd835", "#43a047", "#1e88e5", "#8e24aa", "#6d4c41", "#ec407a")
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
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
SOCIAL_DEMO_LOGIN_ENABLED = os.getenv("SOCIAL_DEMO_LOGIN_ENABLED", "true").lower() != "false"
SOCIAL_DEMO_ADMIN_PASSWORD = os.getenv("SOCIAL_DEMO_ADMIN_PASSWORD", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_STATE_TABLE = "app_state"
SUPABASE_STATE_ID = "primary"
SUPABASE_UPLOAD_BUCKET = "chat-uploads"
SUPABASE_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)
SUBSCRIBERS: set[queue.Queue] = set()
SUBSCRIBERS_LOCK = threading.Lock()
SHORTS_FEED_LOCK = threading.Lock()
APP_NAME = "Colorless"
AGE_GROUPS = {"10대", "20대", "30대", "40대", "50대 이상"}
GENDERS = {"여성", "남성"}
FRIEND_CODE_PATTERN = re.compile(r"(?:CL-[A-Z0-9]{8}|[a-z][a-z0-9_]{3,19})")
SHORTS_PROFILE_TOPICS = {
    ("10대", "여성"): ("유머", "먹방", "아이돌", "뷰티"),
    ("10대", "남성"): ("유머", "먹방", "테크", "스포츠"),
    ("20대", "여성"): ("유머", "먹방", "연예", "여행"),
    ("20대", "남성"): ("유머", "먹방", "연예", "자동차"),
    ("30대", "여성"): ("유머", "먹방", "재테크", "여행"),
    ("30대", "남성"): ("유머", "먹방", "테크", "재테크"),
    ("40대", "여성"): ("유머", "먹방", "건강", "여행"),
    ("40대", "남성"): ("유머", "먹방", "경제", "여행"),
    ("50대 이상", "여성"): ("유머", "먹방", "건강", "취미"),
    ("50대 이상", "남성"): ("유머", "먹방", "건강", "역사"),
}
SHORTS_AGE_TRENDING_TOPICS = {
    "10대": ("유머", "먹방", "아이돌", "테크"),
    "20대": ("유머", "먹방", "연예", "여행"),
    "30대": ("유머", "먹방", "재테크", "여행"),
    "40대": ("유머", "먹방", "건강", "여행"),
    "50대 이상": ("유머", "먹방", "건강", "취미"),
}
YOUTH_SHORTS_BLOCKLIST = ("로보카 폴리", "뽀로로", "핑크퐁", "옐언니", "키즈", "어린이", "유아", "아기", "동요", "nursery", "kids")
EMERGENCY_SHORTS = (
    {"id": "C-50DxR1fGc", "title": "추천 쇼츠", "channel_title": "YouTube"},
    {"id": "WK7Fuaxpffg", "title": "추천 쇼츠", "channel_title": "YouTube"},
)
POPULAR_VIDEO_CATEGORIES = (
    "24", "23", "10", "17", "20", "22", "26", "1", "2", "19", "25", "27", "28",
    "15", "21", "29", "18", "30", "31", "32", "33", "34", "35", "36", "37", "38",
    "39", "40", "41", "42", "43", "44",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def new_friend_code() -> str:
    return f"cl_{secrets.token_hex(4)}"


def normalize_friend_code(value: object) -> str:
    friend_code = str(value or "").strip().removeprefix("@")
    return friend_code.upper() if friend_code.upper().startswith("CL-") else friend_code.lower()


def blank_profile_pixels() -> list[str]:
    return ["#ffffff"] * PROFILE_PIXEL_COUNT


def valid_profile_pixels(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == PROFILE_PIXEL_COUNT
        and all(
            isinstance(color, str)
            and len(color) == 7
            and color.startswith("#")
            and all(character in "0123456789abcdefABCDEF" for character in color[1:])
            for color in value
        )
    )


def normalize_profile_pixels(value: object) -> list[str]:
    if valid_profile_pixels(value):
        return [color.lower() for color in value]
    if isinstance(value, str) and len(value) == PROFILE_PIXEL_COUNT and all(character in "0123456789ab" for character in value):
        return [PROFILE_PALETTE[int(character, 12)] for character in value]
    return blank_profile_pixels()


def valid_hex_color(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 7
        and value.startswith("#")
        and all(character in "0123456789abcdefABCDEF" for character in value[1:])
    )


def normalize_custom_palette(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    colors: list[str] = []
    for color in value:
        if not valid_hex_color(color):
            continue
        normalized_color = color.lower()
        if normalized_color not in colors:
            colors.append(normalized_color)
        if len(colors) == 10:
            break
    return colors


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


def saved_activity_emoji(value: object) -> str:
    normalized = str(value or "").strip()[:16]
    return normalized if any(ord(character) >= 0x1F000 for character in normalized) else ""


def mask_phone(phone: str) -> str:
    normalized = normalize_phone(phone)
    if not normalized:
        return ""
    if len(normalized) == 10:
        return f"{normalized[:3]}-***-{normalized[-3:]}"
    return f"{normalized[:3]}-****-{normalized[-4:]}"


def youtube_duration_seconds(duration: str) -> int:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if match is None:
        return 0
    hours, minutes, seconds = (int(value or 0) for value in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def shorts_search_query_for(user: dict) -> str:
    profile = (str(user.get("age_group", "")), str(user.get("gender", "")))
    topics = SHORTS_PROFILE_TOPICS.get(profile)
    if not topics:
        return "한국어 쇼츠"
    excluded_terms = " ".join(f"-{term}" for term in YOUTH_SHORTS_BLOCKLIST)
    return f"한국어 쇼츠 {'|'.join(topics)} {excluded_terms}"


def trending_shorts_search_query(user: dict) -> str:
    topics = SHORTS_AGE_TRENDING_TOPICS.get(str(user.get("age_group", "")))
    if not topics:
        return "한국어 쇼츠"
    excluded_terms = " ".join(f"-{term}" for term in YOUTH_SHORTS_BLOCKLIST)
    return f"한국어 쇼츠 {'|'.join(topics)} {excluded_terms}"


def shorts_search_queries_for(user: dict) -> list[str]:
    excluded_terms = " ".join(f"-{term}" for term in YOUTH_SHORTS_BLOCKLIST)
    query_groups = [
        shorts_search_query_for(user),
        trending_shorts_search_query(user),
        f"한국어 쇼츠 유머|먹방|연예 {excluded_terms}",
        f"한국어 쇼츠 브이로그|여행|맛집 {excluded_terms}",
        f"한국어 쇼츠 재테크|건강|테크 {excluded_terms}",
        f"한국어 쇼츠 음악|패션|요리 {excluded_terms}",
    ]
    return list(dict.fromkeys(query_groups))


def korean_shorts_search_queries() -> list[str]:
    excluded_terms = " ".join(f"-{term}" for term in YOUTH_SHORTS_BLOCKLIST)
    query_groups = [
        "\ud55c\uad6d\uc5b4 \uc1fc\uce20",
        "\ud55c\uad6d \uc720\uba38 \uc1fc\uce20",
        "\ud55c\uad6d \uba39\ubc29 \uc1fc\uce20",
        "\ud55c\uad6d \uc5f0\uc608 \uc1fc\uce20",
        "\ud55c\uad6d \uc77c\uc0c1 \uc1fc\uce20",
    ]
    return [f"{query} {excluded_terms}" for query in query_groups]


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
) -> object:
    request = Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    try:
        with urlopen(request, timeout=15) as response:
            content = response.read().decode("utf-8")
            return json.loads(content) if content else {}
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="ignore")
        raise ValueError(body or f"HTTP {error.code}") from error
    except URLError as error:
        raise ConnectionError(str(error.reason)) from error


def fetch_bytes(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> bytes:
    request = Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    try:
        with urlopen(request, timeout=30) as response:
            return response.read()
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="ignore")
        raise ValueError(body or f"HTTP {error.code}") from error
    except URLError as error:
        raise ConnectionError(str(error.reason)) from error


def supabase_headers(content_type: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def supabase_object_url(filename: str) -> str:
    return f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_UPLOAD_BUCKET}/{quote(filename)}"


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


class PresenceStore:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, dict] = {}

    def connect(self, token: str | None, username: str) -> bool:
        if not token:
            return False
        with self.lock:
            entry = self.sessions.setdefault(token, {"username": username, "connections": 0, "active_room_id": "", "emoji": ""})
            entry["username"] = username
            was_online = any(item["username"] == username and item["connections"] > 0 for item in self.sessions.values())
            entry["connections"] += 1
            return not was_online

    def disconnect(self, token: str | None) -> tuple[str, bool]:
        if not token:
            return "", False
        with self.lock:
            entry = self.sessions.get(token)
            if entry is None:
                return "", False
            username = entry["username"]
            entry["connections"] = max(0, entry["connections"] - 1)
            if entry["connections"] == 0:
                self.sessions.pop(token, None)
            is_online = any(item["username"] == username and item["connections"] > 0 for item in self.sessions.values())
            return username, not is_online

    def update(self, token: str | None, username: str, active_room_id: str, emoji: str) -> bool:
        if not token:
            return False
        with self.lock:
            entry = self.sessions.setdefault(token, {"username": username, "connections": 0, "active_room_id": "", "emoji": ""})
            changed = entry["active_room_id"] != active_room_id or entry["emoji"] != emoji
            entry["username"] = username
            entry["active_room_id"] = active_room_id
            entry["emoji"] = emoji
            return changed

    def for_user(self, username: str) -> dict:
        with self.lock:
            entries = [item for item in self.sessions.values() if item["username"] == username and item["connections"] > 0]
            active_room_ids = sorted({item["active_room_id"] for item in entries if item["active_room_id"]})
            emoji = next((item["emoji"] for item in reversed(entries) if item["emoji"]), "")
            return {"online": bool(entries), "active_room_ids": active_room_ids, "emoji": emoji}

    def set_demo_active(self, username: str, emoji: str) -> None:
        with self.lock:
            self.sessions[f"demo:{username}"] = {
                "username": username,
                "connections": 1,
                "active_room_id": "",
                "emoji": emoji,
            }


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
            "kind": "group",
            "participant_ids": [],
            "is_public": False,
            "last_read_by": {},
        }

    def _write_state(self, state: dict) -> None:
        if SUPABASE_ENABLED:
            payload = json.dumps({"id": SUPABASE_STATE_ID, "state": state}, ensure_ascii=False).encode("utf-8")
            headers = supabase_headers("application/json")
            headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
            fetch_json(
                f"{SUPABASE_URL}/rest/v1/{SUPABASE_STATE_TABLE}?on_conflict=id",
                method="POST",
                headers=headers,
                data=payload,
            )
            return
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.path)

    def _migrate_state(self, state: dict) -> dict:
        state.setdefault("users", [])
        state.setdefault("friendships", [])
        state.setdefault("rooms", [])
        state.setdefault("messages", {})
        state.setdefault("shorts_feeds", {})

        if (
            not isinstance(state["users"], list)
            or not isinstance(state["friendships"], list)
            or not isinstance(state["rooms"], list)
            or not isinstance(state["messages"], dict)
            or not isinstance(state["shorts_feeds"], dict)
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
            user["profile_pixels"] = normalize_profile_pixels(user.get("profile_pixels"))
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
            if not room["participant_ids"] and not room["is_public"]:
                creator = users_by_name.get(room["created_by"])
                room["participant_ids"] = [creator["id"]] if creator else []
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

        valid_usernames = {user["username"] for user in state["users"]}
        state["shorts_feeds"] = {
            username: {
                "next_cursor": str(feed.get("next_cursor", ""))[:200],
                "seen_ids": [
                    video_id
                    for video_id in feed.get("seen_ids", [])
                    if isinstance(video_id, str) and 1 <= len(video_id) <= 64
                ],
            }
            for username, feed in state["shorts_feeds"].items()
            if username in valid_usernames and isinstance(feed, dict) and isinstance(feed.get("seen_ids", []), list)
        }

        return state

    def _load_state(self) -> dict:
        if SUPABASE_ENABLED:
            rows = fetch_json(
                f"{SUPABASE_URL}/rest/v1/{SUPABASE_STATE_TABLE}?id=eq.{SUPABASE_STATE_ID}&select=state",
                headers=supabase_headers(),
            )
            if isinstance(rows, list) and rows and isinstance(rows[0], dict) and isinstance(rows[0].get("state"), dict):
                state = self._migrate_state(rows[0]["state"])
            else:
                state = self._default_state()
            self._write_state(state)
            return state

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

    def get_shorts_feed(self, username: str) -> tuple[list[str], str]:
        with self.lock:
            feed = self.state["shorts_feeds"].get(username, {})
            return list(feed.get("seen_ids", [])), str(feed.get("next_cursor", ""))

    def save_shorts_feed(self, username: str, seen_ids: list[str], next_cursor: str) -> None:
        with self.lock:
            self.state["shorts_feeds"][username] = {
                "seen_ids": seen_ids,
                "next_cursor": next_cursor[:200],
            }
            self._save_locked()

    def _user_public(self, user: dict) -> dict:
        provider = user.get("auth_provider", "local")
        profile_pixels = normalize_profile_pixels(user.get("profile_pixels"))
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
            "profile_pixels": [] if all(color == "#ffffff" for color in profile_pixels) else profile_pixels,
            "custom_palette": normalize_custom_palette(user.get("custom_palette")),
        }

    def _presence_for_user(self, user: dict) -> dict:
        presence = PRESENCE.for_user(user["username"])
        saved_emoji = saved_activity_emoji(user.get("status_message"))
        if presence["online"] and saved_emoji:
            presence["emoji"] = saved_emoji
        return presence

    def _room_summary(self, room: dict, viewer: dict | None = None) -> dict:
        messages = self.state["messages"].get(room["id"], [])
        participants = {message["username"] for message in messages if message.get("username")}
        if room.get("created_by") and room["created_by"] != "system":
            participants.add(room["created_by"])
        last_message = messages[-1] if messages else None
        summary = {
            "id": room["id"],
            "name": room["name"],
            "description": room["description"],
            "created_by": room["created_by"],
            "created_at": room["created_at"],
            "updated_at": room["updated_at"],
            "kind": room.get("kind", "group"),
            "participant_count": len(room.get("participant_ids", [])) or len(participants),
            "message_count": len(messages),
            "last_message": last_message,
        }
        if room.get("kind") == "direct" and viewer is not None:
            peer_id = next((user_id for user_id in room.get("participant_ids", []) if user_id != viewer["id"]), "")
            peer = next((user for user in self.state["users"] if user["id"] == peer_id), None)
            if peer is not None:
                summary["name"] = peer.get("display_name") or peer["username"]
                summary["peer"] = self._user_public(peer)
                summary["peer"]["presence"] = self._presence_for_user(peer)
        return summary

    def get_user_record(self, username: str) -> dict | None:
        with self.lock:
            return next((item for item in self.state["users"] if item["username"] == username), None)

    def get_user_public(self, username: str) -> dict | None:
        user = self.get_user_record(username)
        if user is None:
            return None
        return self._user_public(user)

    def update_profile_pixels(self, username: str, pixels: object) -> dict | None:
        if not valid_profile_pixels(pixels):
            return None
        with self.lock:
            user = next((candidate for candidate in self.state["users"] if candidate["username"] == username), None)
            if user is None:
                return None
            user["profile_pixels"] = normalize_profile_pixels(pixels)
            self._save_locked()
            return self._user_public(user)

    def update_profile(self, username: str, display_name: str, status_message: str, friend_code: str, pixels: object) -> tuple[dict | None, str | None]:
        normalized_display_name = display_name.strip()[:24]
        normalized_status_message = status_message.strip()[:40]
        normalized_friend_code = normalize_friend_code(friend_code)
        if len(normalized_display_name) < 2:
            return None, "이름은 2자 이상이어야 합니다."
        if not valid_profile_pixels(pixels):
            return None, "프로필 픽셀 데이터가 올바르지 않습니다."
        if not FRIEND_CODE_PATTERN.fullmatch(normalized_friend_code):
            return None, "친구 ID는 영문 소문자, 숫자, 밑줄로 4~20자여야 합니다."

        with self.lock:
            user = next((candidate for candidate in self.state["users"] if candidate["username"] == username), None)
            if user is None:
                return None, "사용자를 찾을 수 없습니다."
            if any(candidate["username"] != username and candidate.get("friend_code") == normalized_friend_code for candidate in self.state["users"]):
                return None, "이미 사용 중인 친구 ID입니다."
            user["display_name"] = normalized_display_name
            user["status_message"] = normalized_status_message
            user["friend_code"] = normalized_friend_code
            user["profile_pixels"] = normalize_profile_pixels(pixels)
            self._save_locked()
            return self._user_public(user), None

    def update_custom_palette(self, username: str, colors: object) -> tuple[dict | None, str | None]:
        if not isinstance(colors, list) or len(colors) > 10 or any(not valid_hex_color(color) for color in colors):
            return None, "나만의 팔레트는 올바른 색상 10개까지 저장할 수 있습니다."

        with self.lock:
            user = next((candidate for candidate in self.state["users"] if candidate["username"] == username), None)
            if user is None:
                return None, "사용자를 찾을 수 없습니다."
            user["custom_palette"] = normalize_custom_palette(colors)
            self._save_locked()
            return self._user_public(user), None

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

    def _new_friend_code_locked(self) -> str:
        existing_codes = {str(user.get("friend_code", "")) for user in self.state["users"]}
        friend_code = new_friend_code()
        while friend_code in existing_codes:
            friend_code = new_friend_code()
        return friend_code

    def _user_by_id_locked(self, user_id: str) -> dict | None:
        return next((user for user in self.state["users"] if user["id"] == user_id), None)

    def _friend_ids_locked(self, user_id: str) -> set[str]:
        friend_ids: set[str] = set()
        for friendship in self.state["friendships"]:
            user_ids = friendship.get("user_ids", [])
            if user_id not in user_ids:
                continue
            friend_ids.update(candidate for candidate in user_ids if candidate != user_id)
        return friend_ids

    def _can_access_room_locked(self, room: dict, user: dict) -> bool:
        return bool(room.get("is_public")) or user["id"] in room.get("participant_ids", [])

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
            raw_friends = [friend for friend in self.state["users"] if friend["id"] in friend_ids]
            friends = [self._user_public(friend) for friend in raw_friends]
            for friend, raw_friend in zip(friends, raw_friends):
                friend["presence"] = self._presence_for_user(raw_friend)
            rooms = [
                self._room_summary(room, user)
                for room in self.state["rooms"]
                if room.get("kind") == "direct" and self._can_access_room_locked(room, user)
            ]
        return {
            "app_name": APP_NAME,
            "user": self._user_public(user),
            "friends": sorted(friends, key=lambda friend: friend["username"].lower()),
            "discoverable_users": [],
            "rooms": sorted(rooms, key=lambda room: room["updated_at"], reverse=True),
        }

    def get_messages(self, room_id: str, username: str) -> list[dict] | None:
        with self.lock:
            user = next((candidate for candidate in self.state["users"] if candidate["username"] == username), None)
            room = next((candidate for candidate in self.state["rooms"] if candidate["id"] == room_id), None)
            if user is None or room is None or not self._can_access_room_locked(room, user):
                return None
            messages = self.state["messages"].get(room_id, [])
            peer_read_message_id = ""
            if room.get("kind") == "direct":
                peer_id = next((user_id for user_id in room.get("participant_ids", []) if user_id != user["id"]), "")
                peer_read_message_id = str(room.get("last_read_by", {}).get(peer_id, ""))

            read_message_ids: set[str] = set()
            if peer_read_message_id:
                for message in messages:
                    read_message_ids.add(message["id"])
                    if message["id"] == peer_read_message_id:
                        break

            return [
                {**message, "read": message.get("username") == username and message.get("id") in read_message_ids}
                for message in messages
            ]

    def mark_room_read(self, room_id: str, username: str) -> dict | None:
        with self.lock:
            user = next((candidate for candidate in self.state["users"] if candidate["username"] == username), None)
            room = next((candidate for candidate in self.state["rooms"] if candidate["id"] == room_id), None)
            if user is None or room is None or not self._can_access_room_locked(room, user):
                return None
            messages = self.state["messages"].get(room_id, [])
            if not messages:
                return self._room_summary(room, user)
            last_read_by = room.setdefault("last_read_by", {})
            last_message_id = messages[-1]["id"]
            if last_read_by.get(user["id"]) != last_message_id:
                last_read_by[user["id"]] = last_message_id
                self._save_locked()
            return self._room_summary(room, user)

    def add_friend(self, username: str, friend_user_id: str) -> tuple[dict | None, str | None]:
        with self.lock:
            user = next((candidate for candidate in self.state["users"] if candidate["username"] == username), None)
            friend = self._user_by_id_locked(friend_user_id)
            if user is None or friend is None:
                return None, "사용자를 찾을 수 없습니다."
            if user["id"] == friend["id"]:
                return None, "자기 자신은 친구로 추가할 수 없습니다."

            user_ids = sorted([user["id"], friend["id"]])
            exists = any(sorted(friendship.get("user_ids", [])) == user_ids for friendship in self.state["friendships"])
            if not exists:
                self.state["friendships"].append({"user_ids": user_ids, "created_at": utc_now_iso()})
                self._save_locked()
            return self._user_public(friend), None

    def add_friend_by_code(self, username: str, friend_code: str) -> tuple[dict | None, str | None]:
        normalized_friend_code = normalize_friend_code(friend_code)
        if not FRIEND_CODE_PATTERN.fullmatch(normalized_friend_code):
            return None, "올바른 친구 ID를 입력해 주세요."

        with self.lock:
            user = next((candidate for candidate in self.state["users"] if candidate["username"] == username), None)
            friend = next((candidate for candidate in self.state["users"] if candidate.get("friend_code") == normalized_friend_code), None)
            if user is None or friend is None:
                return None, "해당 친구 ID의 사용자를 찾을 수 없습니다."
            if user["id"] == friend["id"]:
                return None, "자기 자신은 친구로 추가할 수 없습니다."

            user_ids = sorted([user["id"], friend["id"]])
            exists = any(sorted(friendship.get("user_ids", [])) == user_ids for friendship in self.state["friendships"])
            if not exists:
                self.state["friendships"].append({"user_ids": user_ids, "created_at": utc_now_iso()})
                self._save_locked()
            return self._user_public(friend), None

    def create_or_get_direct_room(self, username: str, friend_user_id: str) -> tuple[dict | None, bool, str | None]:
        with self.lock:
            user = next((candidate for candidate in self.state["users"] if candidate["username"] == username), None)
            friend = self._user_by_id_locked(friend_user_id)
            if user is None or friend is None:
                return None, False, "사용자를 찾을 수 없습니다."

            participant_ids = sorted([user["id"], friend["id"]])
            if friend["id"] not in self._friend_ids_locked(user["id"]):
                return None, False, "먼저 친구로 추가해 주세요."

            room = next(
                (
                    candidate
                    for candidate in self.state["rooms"]
                    if candidate.get("kind") == "direct" and sorted(candidate.get("participant_ids", [])) == participant_ids
                ),
                None,
            )
            if room is not None:
                return self._room_summary(room, user), False, None

            room = self._new_room(new_id("room"), friend["username"], "", username)
            room["kind"] = "direct"
            room["participant_ids"] = participant_ids
            self.state["rooms"].append(room)
            self.state["messages"][room["id"]] = []
            self._save_locked()
            return self._room_summary(room, user), True, None

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
            existing = next((item for item in self.state["users"] if item["username"] == normalized_username), None)
            if existing is not None:
                return None, "이미 존재하는 사용자 이름입니다."
            if any(candidate.get("friend_code") == normalized_friend_code for candidate in self.state["users"]):
                return None, "이미 사용 중인 친구 ID입니다."

            salt_hex, digest = hash_password(password)
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
                "profile_pixels": blank_profile_pixels(),
                "custom_palette": [],
                "age_group": age_group,
                "gender": gender,
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
                "friend_code": self._new_friend_code_locked(),
                    "display_name": nickname.strip()[:24] or username,
                    "status_message": (status_message or build_status_message(provider))[:40],
                    "phone": "",
                    "auth_provider": provider,
                    "provider_user_id": provider_user_id,
                    "password_salt": "",
                "password_hash": "",
                "created_at": utc_now_iso(),
                "profile_pixels": blank_profile_pixels(),
                "custom_palette": [],
                "age_group": "",
                "gender": "",
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

    def seed_demo_network(self, username: str) -> None:
        with self.lock:
            user = next((item for item in self.state["users"] if item["username"] == username), None)
            if user is None or user.get("auth_provider") != "demo":
                return

            contacts: list[dict] = []
            active_emojis = ["\U0001F600", "\U0001F60E", "\U0001F970", "\U0001F622", "\U0001F620"]
            for index in range(1, 21):
                provider_user_id = f"demo-contact-{index:02d}"
                contact = next(
                    (
                        item
                        for item in self.state["users"]
                        if item.get("auth_provider") == "demo" and item.get("provider_user_id") == provider_user_id
                    ),
                    None,
                )
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
                        "profile_pixels": blank_profile_pixels(),
                        "custom_palette": [],
                        "age_group": "",
                        "gender": "",
                    }
                    self.state["users"].append(contact)
                if index <= len(active_emojis):
                    contact["status_message"] = active_emojis[index - 1]
                    PRESENCE.set_demo_active(contact["username"], active_emojis[index - 1])
                contacts.append(contact)

            for contact in contacts:
                user_ids = sorted([user["id"], contact["id"]])
                if not any(sorted(item.get("user_ids", [])) == user_ids for item in self.state["friendships"]):
                    self.state["friendships"].append({"user_ids": user_ids, "created_at": utc_now_iso()})
                room = next(
                    (
                        item
                        for item in self.state["rooms"]
                        if item.get("kind") == "direct" and sorted(item.get("participant_ids", [])) == user_ids
                    ),
                    None,
                )
                if room is None:
                    room = self._new_room(new_id("room"), contact["username"], "", username)
                    room["kind"] = "direct"
                    room["participant_ids"] = user_ids
                    self.state["rooms"].append(room)
                    self.state["messages"][room["id"]] = []
            self._save_locked()

    def create_room(self, name: str, description: str, created_by: str) -> dict:
        with self.lock:
            room = self._new_room(new_id("room"), name[:32], description[:100], created_by[:24])
            creator = next((user for user in self.state["users"] if user["username"] == created_by), None)
            room["participant_ids"] = [creator["id"]] if creator else []
            self.state["rooms"].append(room)
            self.state["messages"][room["id"]] = []
            self._save_locked()
            return self._room_summary(room)

    def add_message(self, room_id: str, username: str, text: str, attachment: dict | None = None) -> tuple[dict, dict] | None:
        with self.lock:
            room = next((item for item in self.state["rooms"] if item["id"] == room_id), None)
            user = next((item for item in self.state["users"] if item["username"] == username), None)
            if room is None or user is None or not self._can_access_room_locked(room, user):
                return None

            message = {
                "id": new_id("msg"),
                "room_id": room_id,
                "username": username[:24],
                "text": text[:300],
                "timestamp": utc_now_iso(),
            }
            if attachment is not None:
                message["attachment"] = attachment
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
            "enabled": bool(GOOGLE_CLIENT_ID),
            "configured": bool(GOOGLE_CLIENT_ID),
            "client_id": GOOGLE_CLIENT_ID,
            "login_url": "/auth/google/credential",
            "redirect_uri": google_redirect_uri,
            "mode": "id_token",
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
            "enabled": SOCIAL_DEMO_LOGIN_ENABLED and bool(SOCIAL_DEMO_ADMIN_PASSWORD),
            "configured": bool(SOCIAL_DEMO_ADMIN_PASSWORD),
            "login_url": "/auth/demo-login",
            "mode": "local",
        },
    }


STORE = StateStore(STATE_FILE)
SESSIONS = SessionStore()
PRESENCE = PresenceStore()
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
        if path.startswith("/assets/"):
            self.serve_asset(path)
            return
        if path.startswith("/uploads/"):
            self.serve_upload(path)
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
        if path == "/messenger":
            user = self.require_auth_record()
            if user is None:
                return
            self.send_json(STORE.get_messenger_bootstrap(user), HTTPStatus.OK)
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
        if path == "/youtube/shorts":
            user = self.require_auth_record()
            if user is None:
                return
            self.serve_public_shorts(query, user)
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
        if path == "/auth/google/credential":
            self.google_id_token_login()
            return
        if path == "/auth/google/callback":
            self.google_id_token_login()
            return
        if path == "/profile":
            user = self.require_auth()
            if user is None:
                return
            self.update_profile(user)
            return
        if path == "/profile/custom-palette":
            user = self.require_auth()
            if user is None:
                return
            self.update_custom_palette(user)
            return
        if path == "/profile/pixels":
            user = self.require_auth()
            if user is None:
                return
            self.update_profile_pixels(user)
            return
        if path == "/friends":
            user = self.require_auth()
            if user is None:
                return
            self.add_friend(user)
            return
        if path == "/direct-rooms":
            user = self.require_auth()
            if user is None:
                return
            self.create_direct_room(user)
            return
        if path == "/rooms/read":
            user = self.require_auth()
            if user is None:
                return
            self.mark_room_read(user)
            return
        if path == "/presence":
            user = self.require_auth()
            if user is None:
                return
            self.update_presence(user)
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
        if path == "/uploads":
            user = self.require_auth()
            if user is None:
                return
            self.upload_attachment(user)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def serve_index(self) -> None:
        content = INDEX_FILE.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def serve_asset(self, request_path: str) -> None:
        relative_path = unquote(request_path.removeprefix("/assets/"))
        asset_path = (ASSETS_DIR / relative_path).resolve()

        try:
            asset_path.relative_to(ASSETS_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        if not asset_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        content = asset_path.read_bytes()
        content_type = {
            ".ttf": "font/ttf",
            ".otf": "font/otf",
        }.get(asset_path.suffix.lower(), mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def serve_upload(self, request_path: str) -> None:
        filename = Path(unquote(request_path.removeprefix("/uploads/"))).name
        if not filename or Path(filename).suffix.lower() not in ATTACHMENT_TYPES.values():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        try:
            if SUPABASE_ENABLED:
                content = fetch_bytes(supabase_object_url(filename), headers=supabase_headers())
            else:
                upload_path = (UPLOADS_DIR / filename).resolve()
                upload_path.relative_to(UPLOADS_DIR.resolve())
                if not upload_path.is_file():
                    raise FileNotFoundError(filename)
                content = upload_path.read_bytes()
        except (ConnectionError, FileNotFoundError, ValueError):
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
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
        user = self.current_user()
        messages = STORE.get_messages(room_id, user["username"]) if user else None
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
        token = self.read_session_token()
        with SUBSCRIBERS_LOCK:
            SUBSCRIBERS.add(subscriber)

        if PRESENCE.connect(token, user["username"]):
            push_event({"type": "presence_updated", "username": user["username"]})

        try:
            hello = {"type": "hello", "timestamp": utc_now_iso(), "username": user["username"]}
            self.wfile.write(f"data: {json.dumps(hello, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()

            while True:
                event = subscriber.get()
                payload = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
                self.wfile.write(payload)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            with SUBSCRIBERS_LOCK:
                SUBSCRIBERS.discard(subscriber)
            username, went_offline = PRESENCE.disconnect(token)
            if went_offline:
                push_event({"type": "presence_updated", "username": username})

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

    def google_id_token_login(self) -> None:
        is_redirect_login = "application/x-www-form-urlencoded" in self.headers.get("Content-Type", "")
        payload = self.read_form_body() if is_redirect_login else self.read_json_body()
        if payload is None:
            return
        if not GOOGLE_CLIENT_ID:
            self.google_login_error("구글 로그인 설정이 아직 완료되지 않았어요.", HTTPStatus.BAD_REQUEST, is_redirect_login)
            return

        if is_redirect_login:
            csrf_cookie = self.read_cookie_value("g_csrf_token")
            csrf_body = str(payload.get("g_csrf_token", "")).strip()
            if not csrf_cookie or not csrf_body or not hmac.compare_digest(csrf_cookie, csrf_body):
                self.google_login_error("구글 로그인 보안 확인에 실패했어요.", HTTPStatus.FORBIDDEN, is_redirect_login)
                return

        credential = str(payload.get("credential", "")).strip()
        if not credential:
            self.google_login_error("구글 인증 정보를 받지 못했어요.", HTTPStatus.BAD_REQUEST, is_redirect_login)
            return

        try:
            profile_payload = self.verify_google_id_token(credential)
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
            self.google_login_error("구글 로그인 처리 중 문제가 생겼어요. 다시 시도해 주세요.", HTTPStatus.UNAUTHORIZED, is_redirect_login)
            return

        token = SESSIONS.create(user["username"])
        if is_redirect_login:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure()))
            self.end_headers()
            return

        self.send_json(
            {"authenticated": True, "user": user, "sessionToken": token},
            HTTPStatus.OK,
            headers={"Set-Cookie": make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure())},
        )

    def google_login_error(self, message: str, status: HTTPStatus, is_redirect_login: bool) -> None:
        if is_redirect_login:
            self.redirect("/?auth_error=google_login_failed")
            return
        self.send_json({"error": message}, status)

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

    def verify_google_id_token(self, credential: str) -> dict:
        payload = fetch_json(f"https://oauth2.googleapis.com/tokeninfo?{urlencode({'id_token': credential})}")
        if payload.get("aud") != GOOGLE_CLIENT_ID:
            raise ValueError("구글 클라이언트 ID가 일치하지 않습니다.")
        if payload.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("구글 토큰 발급자를 확인하지 못했습니다.")
        return payload

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
        payload = self.read_json_body()
        if payload is None:
            return
        if not SOCIAL_DEMO_LOGIN_ENABLED or not SOCIAL_DEMO_ADMIN_PASSWORD:
            self.send_json({"error": "개발용 SNS 로그인이 비활성화되어 있습니다."}, HTTPStatus.BAD_REQUEST)
            return

        password = str(payload.get("adminPassword", ""))
        if not hmac.compare_digest(password, SOCIAL_DEMO_ADMIN_PASSWORD):
            self.send_json({"error": "Administrator password is incorrect."}, HTTPStatus.FORBIDDEN)
            return

        suffix = secrets.token_hex(3)
        user = STORE.create_or_update_social_user(
            "demo",
            f"demo-{suffix}",
            nickname=f"demo_{suffix}",
            status_message="개발용 SNS로 접속 중",
        )
        STORE.seed_demo_network(user["username"])
        token = SESSIONS.create(user["username"])
        self.send_json(
            {"authenticated": True, "user": user, "sessionToken": token},
            HTTPStatus.OK,
            headers={"Set-Cookie": make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure())},
        )

    def serve_public_shorts(self, query: dict[str, list[str]], user: dict) -> None:
        if not YOUTUBE_API_KEY:
            self.send_json({"error": "YOUTUBE_API_KEY 설정이 필요해요."}, HTTPStatus.SERVICE_UNAVAILABLE)
            return

        page_token = query.get("cursor", [""])[0].strip()
        refresh = query.get("refresh", [""])[0] == "1"
        if len(page_token) > 200:
            self.send_json({"error": "잘못된 쇼츠 페이지 요청이에요."}, HTTPStatus.BAD_REQUEST)
            return
        if refresh and not page_token:
            with SHORTS_FEED_LOCK:
                _, page_token = STORE.get_shorts_feed(user["username"])

        source_index = 0
        popular_cursor = ""
        popular_category_index = 0
        if page_token.startswith("popular:"):
            popular_value = page_token.removeprefix("popular:")
            popular_match = re.fullmatch(r"(\d+):(.*)", popular_value)
            if popular_match is not None:
                popular_category_index = int(popular_match.group(1))
                popular_cursor = popular_match.group(2)
            else:
                popular_cursor = popular_value
            page_token = ""
        elif page_token:
            source_match = re.fullmatch(r"(\d+):(.*)", page_token)
            if source_match is not None:
                source_index = int(source_match.group(1))
                page_token = source_match.group(2)

        def fetch_shorts(search_term: str, cursor: str = "", strict: bool = True) -> tuple[list[dict], str]:
            search_params = {
                "key": YOUTUBE_API_KEY,
                "part": "snippet",
                "q": search_term,
                "type": "video",
                "maxResults": "50",
                "order": "viewCount",
                "regionCode": "KR",
                "relevanceLanguage": "ko",
                "videoDuration": "short",
                "videoEmbeddable": "true",
                "videoSyndicated": "true",
            }
            if cursor:
                search_params["pageToken"] = cursor

            try:
                search_payload = fetch_json(f"https://www.googleapis.com/youtube/v3/search?{urlencode(search_params)}")
            except ValueError:
                if cursor:
                    return [], ""
                raise
            video_ids = [
                str(item.get("id", {}).get("videoId", "")).strip()
                for item in search_payload.get("items", [])
                if str(item.get("id", {}).get("videoId", "")).strip()
            ]
            if not video_ids:
                return [], str(search_payload.get("nextPageToken", ""))

            video_params = {
                "key": YOUTUBE_API_KEY,
                "part": "snippet,contentDetails,status",
                "id": ",".join(video_ids),
                "maxResults": "50",
            }
            try:
                video_payload = fetch_json(f"https://www.googleapis.com/youtube/v3/videos?{urlencode(video_params)}")
            except ValueError:
                if cursor:
                    return [], ""
                raise
            items = []
            for video in video_payload.get("items", []):
                duration = youtube_duration_seconds(str(video.get("contentDetails", {}).get("duration", "")))
                if not 0 < duration <= 180 or not video.get("status", {}).get("embeddable", False):
                    continue
                snippet = video.get("snippet", {})
                language = str(snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or "").lower()
                if strict and language and not language.startswith("ko"):
                    continue
                text = f"{snippet.get('title', '')} {snippet.get('channelTitle', '')}".lower()
                if strict and any(term.lower() in text for term in YOUTH_SHORTS_BLOCKLIST):
                    continue
                items.append({
                    "id": str(video.get("id", "")),
                    "title": str(snippet.get("title", "YouTube 쇼츠")),
                    "channel_title": str(snippet.get("channelTitle", "YouTube")),
                })
            return items, str(search_payload.get("nextPageToken", ""))

        def fetch_popular_shorts(cursor: str = "", category: str = "") -> tuple[list[dict], str]:
            popular_params = {
                "key": YOUTUBE_API_KEY,
                "part": "snippet,contentDetails,status",
                "chart": "mostPopular",
                "regionCode": "KR",
                "maxResults": "50",
            }
            if category:
                popular_params["videoCategoryId"] = category
            if cursor:
                popular_params["pageToken"] = cursor
            payload = fetch_json(f"https://www.googleapis.com/youtube/v3/videos?{urlencode(popular_params)}")
            items = []
            for video in payload.get("items", []):
                duration = youtube_duration_seconds(str(video.get("contentDetails", {}).get("duration", "")))
                if not 0 < duration <= 600 or not video.get("status", {}).get("embeddable", False):
                    continue
                snippet = video.get("snippet", {})
                language = str(snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or "").lower()
                if language and not language.startswith("ko"):
                    continue
                text = f"{snippet.get('title', '')} {snippet.get('channelTitle', '')}".lower()
                if any(term.lower() in text for term in YOUTH_SHORTS_BLOCKLIST):
                    continue
                items.append({
                    "id": str(video.get("id", "")),
                    "title": str(snippet.get("title", "YouTube \uc1fc\uce20")),
                    "channel_title": str(snippet.get("channelTitle", "YouTube")),
                })
            return items, str(payload.get("nextPageToken", ""))

        try:
            search_queries = korean_shorts_search_queries()
            with SHORTS_FEED_LOCK:
                seen_ids, saved_cursor = STORE.get_shorts_feed(user["username"])
                if refresh and not query.get("cursor", [""])[0].strip():
                    page_token = saved_cursor
                    popular_cursor = ""
                    popular_category_index = 0
                    if page_token.startswith("popular:"):
                        popular_value = page_token.removeprefix("popular:")
                        popular_match = re.fullmatch(r"(\d+):(.*)", popular_value)
                        if popular_match is not None:
                            popular_category_index = int(popular_match.group(1))
                            popular_cursor = popular_match.group(2)
                        else:
                            popular_cursor = popular_value
                        page_token = ""
                    else:
                        source_match = re.fullmatch(r"(\d+):(.*)", page_token)
                        if source_match is not None:
                            source_index = int(source_match.group(1))
                            page_token = source_match.group(2)
                recent_set = set(seen_ids)
                emergency_items = [item for item in EMERGENCY_SHORTS if item["id"] not in recent_set]
                if emergency_items:
                    seen_ids.extend(item["id"] for item in emergency_items)
                    STORE.save_shorts_feed(user["username"], seen_ids, page_token)
                    self.send_json({"items": emergency_items, "next_cursor": page_token, "retry_after": 0}, HTTPStatus.OK)
                    return

            items: list[dict] = []
            next_cursor = ""
            failed_sources = 0
            if (popular_cursor or (not page_token and source_index == 0)) and popular_category_index < len(POPULAR_VIDEO_CATEGORIES):
                try:
                    candidates, following_cursor = fetch_popular_shorts(
                        popular_cursor,
                        POPULAR_VIDEO_CATEGORIES[popular_category_index],
                    )
                    unseen_items = [item for item in candidates if item["id"] not in recent_set]
                    if unseen_items:
                        items = unseen_items
                    if following_cursor:
                        next_cursor = f"popular:{popular_category_index}:{following_cursor}"
                    elif popular_category_index + 1 < len(POPULAR_VIDEO_CATEGORIES):
                        next_cursor = f"popular:{popular_category_index + 1}:"
                except (ConnectionError, ValueError):
                    failed_sources += 1
                    if popular_category_index + 1 < len(POPULAR_VIDEO_CATEGORIES):
                        next_cursor = f"popular:{popular_category_index + 1}:"
            for query_index in range(source_index, len(search_queries)):
                if items or next_cursor:
                    break
                cursor = page_token if query_index == source_index else ""
                try:
                    candidates, following_cursor = fetch_shorts(search_queries[query_index], cursor)
                except (ConnectionError, ValueError):
                    failed_sources += 1
                    continue

                unseen_items = [item for item in candidates if item["id"] not in recent_set]
                if unseen_items:
                    items = unseen_items
                    if following_cursor:
                        next_cursor = f"{query_index}:{following_cursor}"
                    elif query_index + 1 < len(search_queries):
                        next_cursor = f"{query_index + 1}:"
                    break
                if following_cursor:
                    next_cursor = f"{query_index}:{following_cursor}"
                    break
                if query_index + 1 < len(search_queries):
                    next_cursor = f"{query_index + 1}:"
                    break
                if items:
                    break

            if not items and not next_cursor:
                for fallback_query in ("\uc1fc\uce20", "\uc720\uba38 \uc1fc\uce20", "\uba39\ubc29 \uc1fc\uce20", "\uc778\uae30 \uc1fc\uce20"):
                    try:
                        candidates, _ = fetch_shorts(fallback_query)
                    except (ConnectionError, ValueError):
                        failed_sources += 1
                        continue
                    unseen_items = [item for item in candidates if item["id"] not in recent_set]
                    if unseen_items:
                        items = unseen_items
                        break

            if not items and not next_cursor:
                items = [item for item in EMERGENCY_SHORTS if item["id"] not in recent_set]

            with SHORTS_FEED_LOCK:
                if items:
                    seen_ids.extend(item["id"] for item in items)
                STORE.save_shorts_feed(user["username"], seen_ids, next_cursor)
            self.send_json(
                {
                    "items": items,
                    "next_cursor": next_cursor,
                    "retry_after": 3 if not items and not next_cursor else 0,
                },
                HTTPStatus.OK,
            )
        except ValueError:
            self.send_json({"error": "YouTube 쇼츠를 불러오지 못했어요. API 키와 할당량을 확인해 주세요."}, HTTPStatus.BAD_GATEWAY)

    def signup(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        username = str(payload.get("username", "")).strip()
        friend_code = str(payload.get("friendCode", "")).strip()
        password = str(payload.get("password", "")).strip()
        status_message = str(payload.get("statusMessage", "")).strip()
        phone = str(payload.get("phone", "")).strip()
        age_group = str(payload.get("ageGroup", "")).strip()
        gender = str(payload.get("gender", "")).strip()
        verification_token = str(payload.get("verificationToken", "")).strip()

        if not PHONE_VERIFICATIONS.consume(phone, verification_token):
            self.send_json({"error": "휴대폰 인증을 먼저 완료해 주세요."}, HTTPStatus.BAD_REQUEST)
            return

        user, error = STORE.create_local_user(username, friend_code, password, status_message, phone, age_group, gender)
        if error:
            self.send_json({"error": error}, HTTPStatus.BAD_REQUEST)
            return

        token = SESSIONS.create(user["username"])
        self.send_json(
            {"authenticated": True, "user": user, "sessionToken": token},
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
            {"authenticated": True, "user": user, "sessionToken": token},
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

    def update_profile_pixels(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        pixels = payload.get("pixels")
        profile = STORE.update_profile_pixels(user["username"], pixels)
        if profile is None:
            self.send_json({"error": "프로필 픽셀 데이터가 올바르지 않습니다."}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"user": profile}, HTTPStatus.OK)

    def update_profile(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        display_name = str(payload.get("displayName", ""))
        status_message = str(payload.get("statusMessage", ""))
        friend_code = str(payload.get("friendCode", ""))
        profile, error = STORE.update_profile(user["username"], display_name, status_message, friend_code, payload.get("pixels"))
        if error:
            self.send_json({"error": error}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"user": profile}, HTTPStatus.OK)

    def update_custom_palette(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        profile, error = STORE.update_custom_palette(user["username"], payload.get("colors"))
        if error:
            self.send_json({"error": error}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"user": profile}, HTTPStatus.OK)

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

    def add_friend(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        friend_code = str(payload.get("friendCode", "")).strip()
        friend, error = STORE.add_friend_by_code(user["username"], friend_code)
        if error:
            self.send_json({"error": error}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"friend": friend}, HTTPStatus.CREATED)

    def create_direct_room(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        friend_user_id = str(payload.get("userId", "")).strip()
        room, created, error = STORE.create_or_get_direct_room(user["username"], friend_user_id)
        if error:
            self.send_json({"error": error}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"room": room, "created": created}, HTTPStatus.CREATED if created else HTTPStatus.OK)

    def create_message(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        room_id = str(payload.get("roomId", "")).strip()
        text = str(payload.get("text", "")).strip()
        attachment = self.message_attachment(payload.get("attachment"))
        if not room_id or (not text and attachment is None):
            self.send_json({"error": "roomId와 text는 필수입니다."}, HTTPStatus.BAD_REQUEST)
            return

        result = STORE.add_message(room_id, user["username"], text, attachment)
        if result is None:
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return

        message, room = result
        push_event({"type": "message_created", "roomId": room_id, "room": room, "message": message})
        self.send_json(message, HTTPStatus.CREATED)

    def upload_attachment(self, user: dict) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > MAX_ATTACHMENT_REQUEST_BYTES:
            self.discard_request_body()
            self.send_json({"error": "File size must be 8MB or less."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return

        payload = self.read_json_body()
        if payload is None:
            return

        content_type = str(payload.get("type", "")).strip().lower()
        extension = ATTACHMENT_TYPES.get(content_type)
        encoded_data = str(payload.get("data", "")).strip()
        if extension is None or not encoded_data:
            self.send_json({"error": "Only image and PDF files are supported."}, HTTPStatus.BAD_REQUEST)
            return

        try:
            content = base64.b64decode(encoded_data, validate=True)
        except (ValueError, TypeError):
            self.send_json({"error": "Unable to read the file."}, HTTPStatus.BAD_REQUEST)
            return

        if not content or len(content) > MAX_ATTACHMENT_BYTES or not self.valid_attachment_content(content_type, content):
            self.send_json({"error": "Unsupported or oversized file. Images and PDFs can be up to 8MB."}, HTTPStatus.BAD_REQUEST)
            return

        filename = f"{new_id('upload')}{extension}"
        if SUPABASE_ENABLED:
            headers = supabase_headers(content_type)
            headers["x-upsert"] = "false"
            try:
                fetch_json(
                    supabase_object_url(filename),
                    method="POST",
                    headers=headers,
                    data=content,
                )
            except (ConnectionError, ValueError):
                self.send_json({"error": "Unable to save the file."}, HTTPStatus.BAD_GATEWAY)
                return
        else:
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            (UPLOADS_DIR / filename).write_bytes(content)
        original_name = Path(str(payload.get("name", "file"))).name.strip()[:120] or f"file{extension}"
        attachment = {
            "url": f"/uploads/{filename}",
            "name": original_name,
            "type": content_type,
            "size": len(content),
        }
        self.send_json({"attachment": attachment}, HTTPStatus.CREATED)

    def valid_attachment_content(self, content_type: str, content: bytes) -> bool:
        signatures = {
            "image/jpeg": content.startswith(b"\xff\xd8\xff"),
            "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/gif": content.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": content.startswith(b"RIFF") and content[8:12] == b"WEBP",
            "application/pdf": content.startswith(b"%PDF-"),
        }
        return signatures.get(content_type, False)

    def message_attachment(self, value: object) -> dict | None:
        if not isinstance(value, dict):
            return None
        url = str(value.get("url", "")).strip()
        filename = Path(unquote(url.removeprefix("/uploads/"))).name
        content_type = str(value.get("type", "")).strip().lower()
        if not url.startswith("/uploads/") or ATTACHMENT_TYPES.get(content_type) != Path(filename).suffix.lower():
            return None
        if not SUPABASE_ENABLED:
            upload_path = (UPLOADS_DIR / filename).resolve()
            try:
                upload_path.relative_to(UPLOADS_DIR.resolve())
            except ValueError:
                return None
            if not upload_path.is_file():
                return None
        try:
            attachment_size = int(value.get("size", 0)) if SUPABASE_ENABLED else upload_path.stat().st_size
        except (TypeError, ValueError):
            attachment_size = 0
        name = Path(str(value.get("name", filename))).name.strip()[:120] or filename
        return {
            "url": f"/uploads/{filename}",
            "name": name,
            "type": content_type,
            "size": attachment_size,
        }

    def mark_room_read(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        room_id = str(payload.get("roomId", "")).strip()
        room = STORE.mark_room_read(room_id, user["username"])
        if room is None:
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return
        push_event({"type": "room_read", "roomId": room_id, "username": user["username"]})
        self.send_json({"room": room}, HTTPStatus.OK)

    def update_presence(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        active_room_id = str(payload.get("activeRoomId", "")).strip()[:80]
        emoji = str(payload.get("emoji", "")).strip()[:16]
        changed = PRESENCE.update(self.read_session_token(), user["username"], active_room_id, emoji)
        if changed:
            push_event({"type": "presence_updated", "username": user["username"]})
        self.send_json({"presence": PRESENCE.for_user(user["username"])}, HTTPStatus.OK)

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
        if morsel:
            return morsel.value
        return self.headers.get("X-Session-Token", "").strip() or None

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

    def read_form_body(self) -> dict | None:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        try:
            fields = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            self.redirect("/?auth_error=google_login_failed")
            return None
        return {key: values[-1] if values else "" for key, values in fields.items()}

    def read_cookie_value(self, name: str) -> str:
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        morsel = cookie.get(name)
        return morsel.value if morsel else ""

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
