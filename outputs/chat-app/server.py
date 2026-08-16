from __future__ import annotations

import copy
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict, deque
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
INDEX_CONTENT = INDEX_FILE.read_bytes()
INDEX_GZIP_CONTENT = gzip.compress(INDEX_CONTENT, compresslevel=6)
COMPRESSIBLE_ASSET_SUFFIXES = {".css", ".js", ".json", ".svg"}
ASSET_GZIP_CONTENT = {
    asset_path.resolve(): gzip.compress(asset_path.read_bytes(), compresslevel=6)
    for asset_path in ASSETS_DIR.rglob("*")
    if asset_path.is_file() and asset_path.suffix.lower() in COMPRESSIBLE_ASSET_SUFFIXES
}
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
STATE_FILE = Path(os.getenv("STATE_FILE", str(DATA_DIR / "chat_state.json")))
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(DATA_DIR / "uploads")))
MAX_MESSAGES_PER_ROOM = 200
DEFAULT_MESSAGES_PAGE_SIZE = 30
MAX_MESSAGES_PAGE_SIZE = 50
MIN_GROUP_PARTICIPANTS = 3
MAX_GROUP_PARTICIPANTS = 50
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_PROFILE_IMAGE_BYTES = 3 * 1024 * 1024
MAX_PROFILE_THUMBNAIL_BYTES = 256 * 1024
MAX_JSON_REQUEST_BYTES = 1024 * 1024
MAX_FORM_REQUEST_BYTES = 64 * 1024
MAX_SHORTS_SEEN_IDS = 500
MAX_SSE_QUEUE_SIZE = 64
MAX_SSE_CONNECTIONS = int(os.getenv("MAX_SSE_CONNECTIONS", "256"))
SSE_HEARTBEAT_SECONDS = 15
SESSION_CLEANUP_INTERVAL_SECONDS = 60
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
SESSION_REFRESH_THRESHOLD_SECONDS = 24 * 60 * 60
MAX_SESSIONS = 10_000
ATTACHMENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/avif": ".avif",
    "application/pdf": ".pdf",
}
PROFILE_PIXEL_SIDE = 32
PROFILE_PIXEL_COUNT = PROFILE_PIXEL_SIDE * PROFILE_PIXEL_SIDE
PROFILE_IMAGE_SIDE = 1024
PROFILE_THUMBNAIL_SIDE = 128
PROFILE_IMAGE_NAME_PATTERN = re.compile(r"profile_[0-9a-f]{24}(?:_thumb)?\.webp")
ROOM_IMAGE_NAME_PATTERN = re.compile(r"room_[0-9a-f]{24}(?:_thumb)?\.webp")
ROOM_ID_PATTERN = re.compile(r"room_[0-9a-f]{8}")
MESSAGE_ID_PATTERN = re.compile(r"msg_[0-9a-f]{8}")
USER_ID_PATTERN = re.compile(r"user_[0-9a-f]{8}")
CLIENT_MESSAGE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,64}")
UPLOAD_NAME_PATTERN = re.compile(r"upload_[0-9a-f]{32}\.(?:jpg|png|gif|webp|heic|heif|avif|pdf)")
PROFILE_PALETTE = ("#ffffff", "#000000", "#777777", "#d9d9d9", "#e53935", "#fb8c00", "#fdd835", "#43a047", "#1e88e5", "#8e24aa", "#6d4c41", "#ec407a")
SESSION_COOKIE_NAME = "codex_talk_session"
OAUTH_STATE_COOKIE_NAME = "colorless_oauth_state"
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
SUBSCRIBERS: dict[queue.Queue, str] = {}
SUBSCRIBERS_BY_USERNAME: dict[str, set[queue.Queue]] = {}
SUBSCRIBERS_LOCK = threading.Lock()
SSE_CONNECTION_SLOTS = threading.BoundedSemaphore(MAX_SSE_CONNECTIONS)
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


def normalize_profile_image_url(value: object) -> str:
    url = str(value or "").strip()
    if not url.startswith("/uploads/"):
        return ""
    filename = Path(unquote(url.removeprefix("/uploads/"))).name
    if not PROFILE_IMAGE_NAME_PATTERN.fullmatch(filename):
        return ""
    return f"/uploads/{filename}"


def profile_image_filename(user_id: str, thumbnail: bool = False) -> str:
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]
    suffix = "_thumb" if thumbnail else ""
    return f"profile_{digest}{suffix}.webp"


def normalize_room_image_url(value: object) -> str:
    url = str(value or "").strip()
    if not url.startswith("/uploads/"):
        return ""
    filename = Path(unquote(url.removeprefix("/uploads/"))).name
    if not ROOM_IMAGE_NAME_PATTERN.fullmatch(filename):
        return ""
    return f"/uploads/{filename}"


def room_image_filename(room_id: str, thumbnail: bool = False) -> str:
    digest = hashlib.sha256(room_id.encode("utf-8")).hexdigest()[:24]
    suffix = "_thumb" if thumbnail else ""
    return f"room_{digest}{suffix}.webp"


def webp_dimensions(content: bytes) -> tuple[int, int] | None:
    if len(content) < 20 or content[:4] != b"RIFF" or content[8:12] != b"WEBP":
        return None
    if int.from_bytes(content[4:8], "little") + 8 != len(content):
        return None

    offset = 12
    max_chunks = 64
    for _ in range(max_chunks):
        if offset + 8 > len(content):
            return None
        chunk_type = content[offset:offset + 4]
        chunk_size = int.from_bytes(content[offset + 4:offset + 8], "little")
        data_start = offset + 8
        data_end = data_start + chunk_size
        if data_end > len(content):
            return None
        chunk = content[data_start:data_end]

        if chunk_type == b"VP8X" and len(chunk) >= 10:
            width = int.from_bytes(chunk[4:7], "little") + 1
            height = int.from_bytes(chunk[7:10], "little") + 1
            return width, height
        if chunk_type == b"VP8L" and len(chunk) >= 5 and chunk[0] == 0x2F:
            packed_dimensions = int.from_bytes(chunk[1:5], "little")
            width = (packed_dimensions & 0x3FFF) + 1
            height = ((packed_dimensions >> 14) & 0x3FFF) + 1
            return width, height
        if chunk_type == b"VP8 " and len(chunk) >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
            width = int.from_bytes(chunk[6:8], "little") & 0x3FFF
            height = int.from_bytes(chunk[8:10], "little") & 0x3FFF
            return width, height

        offset = data_end + (chunk_size & 1)
        if offset >= len(content):
            return None
    return None


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


def make_oauth_state_cookie(state: str, *, secure: bool) -> str:
    cookie = SimpleCookie()
    cookie[OAUTH_STATE_COOKIE_NAME] = state
    cookie[OAUTH_STATE_COOKIE_NAME]["path"] = "/"
    cookie[OAUTH_STATE_COOKIE_NAME]["httponly"] = True
    cookie[OAUTH_STATE_COOKIE_NAME]["samesite"] = "Lax"
    cookie[OAUTH_STATE_COOKIE_NAME]["max-age"] = str(OAUTH_STATE_TTL_SECONDS)
    if secure:
        cookie[OAUTH_STATE_COOKIE_NAME]["secure"] = True
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


class BoundedTTLCache:
    def __init__(self, max_entries: int = 256, ttl_seconds: int = 300) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.lock = threading.Lock()
        self.entries: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self.in_flight: dict[str, threading.Event] = {}

    def get_or_fetch(self, key: str, fetcher) -> object:
        while True:
            now = time.monotonic()
            with self.lock:
                cached = self.entries.get(key)
                if cached is not None and cached[0] > now:
                    self.entries.move_to_end(key)
                    return cached[1]
                if cached is not None:
                    self.entries.pop(key, None)

                waiter = self.in_flight.get(key)
                if waiter is None:
                    waiter = threading.Event()
                    self.in_flight[key] = waiter
                    is_owner = True
                else:
                    is_owner = False

            if is_owner:
                break
            if not waiter.wait(20):
                raise ConnectionError("Cached upstream request timed out")

        try:
            value = fetcher()
        except Exception:
            with self.lock:
                self.in_flight.pop(key, None)
                waiter.set()
            raise

        with self.lock:
            self.entries[key] = (time.monotonic() + self.ttl_seconds, value)
            self.entries.move_to_end(key)
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)
            self.in_flight.pop(key, None)
            waiter.set()
        return value


YOUTUBE_RESPONSE_CACHE = BoundedTTLCache()


def fetch_youtube_json(url: str) -> object:
    return YOUTUBE_RESPONSE_CACHE.get_or_fetch(url, lambda: fetch_json(url))


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
    def __init__(self, ttl_seconds: int = 600) -> None:
        self.lock = threading.Lock()
        self.ttl_seconds = ttl_seconds
        self.grants: dict[str, tuple[str, float]] = {}

    def _cleanup_locked(self, now: float) -> None:
        for filename, (_, expires_at) in list(self.grants.items()):
            if expires_at <= now:
                self.grants.pop(filename, None)

    def create(self, filename: str, username: str) -> None:
        now = time.monotonic()
        with self.lock:
            self._cleanup_locked(now)
            self.grants[filename] = (username, now + self.ttl_seconds)

    def owns(self, filename: str, username: str) -> bool:
        now = time.monotonic()
        with self.lock:
            self._cleanup_locked(now)
            grant = self.grants.get(filename)
            return grant is not None and hmac.compare_digest(grant[0], username)

    def consume(self, filename: str, username: str) -> bool:
        now = time.monotonic()
        with self.lock:
            self._cleanup_locked(now)
            grant = self.grants.get(filename)
            if grant is None or not hmac.compare_digest(grant[0], username):
                return False
            self.grants.pop(filename, None)
            return True


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
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, dict] = {}
        self.tokens_by_username: dict[str, set[str]] = {}

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
            was_online = any(
                self.sessions[candidate]["connections"] > 0
                for candidate in self.tokens_by_username.get(username, set())
                if candidate != token
            ) or entry["connections"] > 0
            entry["connections"] += 1
            entry["updated_at"] = time.monotonic()
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
                self._remove_token_locked(token, username)
            is_online = any(
                self.sessions[candidate]["connections"] > 0
                for candidate in self.tokens_by_username.get(username, set())
            )
            return username, not is_online

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
            return changed

    def for_user(self, username: str) -> dict:
        with self.lock:
            entries = [
                self.sessions[token]
                for token in self.tokens_by_username.get(username, set())
                if self.sessions[token]["connections"] > 0
            ]
            active_room_ids = sorted({item["active_room_id"] for item in entries if item["active_room_id"]})
            emoji_entries = [item for item in entries if item["emoji"]]
            emoji = max(emoji_entries, key=lambda item: item["updated_at"])["emoji"] if emoji_entries else ""
            return {"online": bool(entries), "active_room_ids": active_room_ids, "emoji": emoji}

    def set_demo_active(self, username: str, emoji: str) -> None:
        with self.lock:
            token = f"demo:{username}"
            self.sessions[token] = {
                "username": username,
                "connections": 1,
                "active_room_id": "",
                "emoji": emoji,
                "updated_at": time.monotonic(),
            }
            self.tokens_by_username.setdefault(username, set()).add(token)


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
        self.database_path = path.with_suffix(f"{path.suffix}.sqlite3")
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
        self._supabase_legacy_mode = False
        self.state = self._load_state()
        self._rebuild_indexes_locked()
        self._persist_thread = threading.Thread(
            target=self._persist_worker,
            name="chat-state-writer",
            daemon=True,
        )
        self._persist_thread.start()

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
            user["profile_pixels"] = normalize_profile_pixels(user.get("profile_pixels"))
            user["profile_pixels_blank"] = all(color == "#ffffff" for color in user["profile_pixels"])
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
        parts, legacy_state = self._load_persisted_parts()
        state = self._state_from_parts(parts)
        if state is not None:
            return state

        state = self._load_legacy_state(legacy_state)
        self._write_state(self._state_to_parts(state), state)
        return state

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

        overflow = len(sessions) - max_sessions
        if overflow > 0:
            oldest = sorted(sessions, key=lambda token_hash: float(sessions[token_hash].get("created_at", 0)))[:overflow]
            for token_hash in oldest:
                sessions.pop(token_hash, None)
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
            if len(self.state["sessions"]) > max_sessions:
                changed = self._cleanup_sessions_locked(now, max_sessions) or changed
            self._save_locked("sessions")

    def get_session_username(self, token_hash: str, ttl_seconds: int) -> str | None:
        now = time.time()
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
            if self.state["sessions"].pop(token_hash, None) is not None:
                self._save_locked("sessions")

    def get_shorts_feed(self, username: str) -> tuple[list[str], str]:
        with self.lock:
            feed = self.state["shorts_feeds"].get(username, {})
            return list(feed.get("seen_ids", [])), str(feed.get("next_cursor", ""))

    def save_shorts_feed(self, username: str, seen_ids: list[str], next_cursor: str) -> None:
        bounded_seen_ids = list(dict.fromkeys(seen_ids))[-MAX_SHORTS_SEEN_IDS:]
        with self.lock:
            self.state["shorts_feeds"][username] = {
                "seen_ids": bounded_seen_ids,
                "next_cursor": next_cursor[:200],
            }
            self._save_locked(f"shorts:{username}")

    def _user_public(self, user: dict) -> dict:
        provider = user.get("auth_provider", "local")
        profile_pixels = user.get("profile_pixels", [])
        profile_image_url = normalize_profile_image_url(user.get("profile_image_url"))
        profile_thumbnail_url = normalize_profile_image_url(user.get("profile_thumbnail_url"))
        profile_image_version = user.get("profile_image_version", 0)
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
            "profile_pixels": [] if user.get("profile_pixels_blank", False) else profile_pixels,
            "profile_image_url": f"{profile_image_url}?v={profile_image_version}" if profile_image_url else "",
            "profile_thumbnail_url": f"{profile_thumbnail_url}?v={profile_image_version}" if profile_thumbnail_url else "",
            "custom_palette": user.get("custom_palette", []),
        }

    def _presence_for_user(self, user: dict) -> dict:
        presence = PRESENCE.for_user(user["username"])
        saved_emoji = saved_activity_emoji(user.get("status_message"))
        if presence["online"] and saved_emoji:
            presence["emoji"] = saved_emoji
        return presence

    def _room_summary(self, room: dict, viewer: dict | None = None) -> dict:
        messages = self.state["messages"].get(room["id"], [])
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
        elif room.get("kind") == "group" and viewer is not None:
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

    def update_profile_pixels(self, username: str, pixels: object) -> dict | None:
        if not valid_profile_pixels(pixels):
            return None
        with self.lock:
            user = self._users_by_username.get(username)
            if user is None:
                return None
            user["profile_pixels"] = normalize_profile_pixels(pixels)
            user["profile_pixels_blank"] = all(color == "#ffffff" for color in user["profile_pixels"])
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
            self._save_locked("users")
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
            user["profile_pixels"] = normalize_profile_pixels(pixels)
            user["profile_pixels_blank"] = all(color == "#ffffff" for color in user["profile_pixels"])
            if previous_friend_code != normalized_friend_code:
                self._users_by_friend_code.pop(previous_friend_code, None)
                self._users_by_friend_code[normalized_friend_code] = user
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

    def _messages_with_read_state_locked(
        self,
        room: dict,
        user: dict,
        messages: list[dict],
    ) -> list[dict]:
        reader_ids: list[str] = []
        if room.get("kind") == "direct":
            reader_id = next(
                (user_id for user_id in room.get("participant_ids", []) if user_id != user["id"]),
                "",
            )
            if reader_id:
                reader_ids.append(reader_id)
        elif room.get("kind") == "group":
            reader_ids = [
                user_id
                for user_id in room.get("participant_ids", [])
                if user_id != user["id"]
            ]

        read_message_ids: set[str] = set()
        all_messages = self.state["messages"].get(room["id"], [])
        if room.get("kind") == "group" and not reader_ids:
            read_message_ids = {message["id"] for message in all_messages}
        elif reader_ids and all_messages:
            message_positions = {
                message["id"]: index
                for index, message in enumerate(all_messages)
            }
            reader_positions: list[int] = []
            last_read_by = room.get("last_read_by", {})
            for reader_id in reader_ids:
                position = message_positions.get(str(last_read_by.get(reader_id, "")))
                if position is None:
                    reader_positions = []
                    break
                reader_positions.append(position)
            if len(reader_positions) == len(reader_ids):
                read_through_index = min(reader_positions)
                read_message_ids = {
                    message["id"]
                    for message in all_messages[:read_through_index + 1]
                }

        return [
            {**message, "read": message.get("username") == user["username"] and message.get("id") in read_message_ids}
            for message in messages
        ]

    def get_messages(self, room_id: str, username: str) -> list[dict] | None:
        with self.lock:
            user = self._users_by_username.get(username)
            room = self._rooms_by_id.get(room_id)
            if user is None or room is None or not self._can_access_room_locked(room, user):
                return None
            messages = self.state["messages"].get(room_id, [])
            return self._messages_with_read_state_locked(room, user, messages)

    def get_messages_page(
        self,
        room_id: str,
        username: str,
        *,
        limit: int,
        before: str = "",
    ) -> dict | None:
        with self.lock:
            user = self._users_by_username.get(username)
            room = self._rooms_by_id.get(room_id)
            if user is None or room is None or not self._can_access_room_locked(room, user):
                return None
            messages = self.state["messages"].get(room_id, [])
            end = len(messages)
            if before:
                end = next(
                    (index for index, message in enumerate(messages) if message.get("id") == before),
                    0,
                )
            start = max(0, end - limit)
            page_messages = self._messages_with_read_state_locked(room, user, messages[start:end])
            return {
                "items": page_messages,
                "next_cursor": messages[start]["id"] if start > 0 and page_messages else "",
            }

    def mark_room_read(self, room_id: str, username: str) -> tuple[dict | None, bool]:
        with self.lock:
            user = self._users_by_username.get(username)
            room = self._rooms_by_id.get(room_id)
            if user is None or room is None or not self._can_access_room_locked(room, user):
                return None, False
            messages = self.state["messages"].get(room_id, [])
            if not messages:
                return self._room_summary(room, user), False
            last_read_by = room.setdefault("last_read_by", {})
            last_message_id = messages[-1]["id"]
            if last_read_by.get(user["id"]) == last_message_id:
                return self._room_summary(room, user), False
            last_read_by[user["id"]] = last_message_id
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
            return any(
                (room := self._rooms_by_id.get(room_id)) is not None and self._can_access_room_locked(room, user)
                for room_id in self._attachment_rooms.get(filename, set())
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
                self.state["friendships"].append({"user_ids": user_ids, "created_at": utc_now_iso()})
                self._register_friendship_locked(user_ids[0], user_ids[1])
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
                self.state["friendships"].append({"user_ids": user_ids, "created_at": utc_now_iso()})
                self._register_friendship_locked(user_ids[0], user_ids[1])
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
            self._save_locked("rooms", f"messages:{room['id']}")
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
            self._save_locked("rooms", f"messages:{room['id']}")
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
                "profile_pixels": blank_profile_pixels(),
                "profile_pixels_blank": True,
                "profile_image_url": "",
                "profile_thumbnail_url": "",
                "profile_image_version": 0,
                "custom_palette": [],
                "age_group": age_group,
                "gender": gender,
            }
            self.state["users"].append(user)
            self._register_user_locked(user)
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
                "profile_pixels": blank_profile_pixels(),
                "profile_pixels_blank": True,
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
                        "profile_pixels": blank_profile_pixels(),
                        "profile_pixels_blank": True,
                        "profile_image_url": "",
                        "profile_thumbnail_url": "",
                        "profile_image_version": 0,
                        "custom_palette": [],
                        "age_group": "",
                        "gender": "",
                    }
                    self.state["users"].append(contact)
                    self._register_user_locked(contact)
                    changed = True
                if index <= len(active_emojis):
                    emoji = active_emojis[index - 1]
                    if contact.get("status_message") != emoji:
                        contact["status_message"] = emoji
                        changed = True
                    PRESENCE.set_demo_active(contact["username"], active_emojis[index - 1])
                contacts.append(contact)

            for contact in contacts:
                user_ids = sorted([user["id"], contact["id"]])
                pair = tuple(user_ids)
                if pair not in self._friendship_pairs:
                    self.state["friendships"].append({"user_ids": user_ids, "created_at": utc_now_iso()})
                    self._register_friendship_locked(user_ids[0], user_ids[1])
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
            if existing_message is not None:
                if (
                    existing_message.get("text", "") != text[:300]
                    or existing_message.get("attachment") != attachment
                ):
                    raise ValueError("client message id was reused with different content")
                return existing_message, self._room_summary(room), False

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
            room_messages = self.state["messages"][room_id]
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
            self._save_locked("rooms", f"messages:{room_id}")
            return message, self._room_summary(room), True


def push_event(event: dict, recipients: set[str]) -> None:
    if not recipients:
        return
    with SUBSCRIBERS_LOCK:
        dead_subscribers: list[queue.Queue] = []
        for username in recipients:
            for subscriber in SUBSCRIBERS_BY_USERNAME.get(username, ()):
                try:
                    subscriber.put_nowait(event)
                except queue.Full:
                    dead_subscribers.append(subscriber)
        for subscriber in dead_subscribers:
            username = SUBSCRIBERS.pop(subscriber, None)
            if username:
                username_subscribers = SUBSCRIBERS_BY_USERNAME.get(username)
                if username_subscribers is not None:
                    username_subscribers.discard(subscriber)
                    if not username_subscribers:
                        SUBSCRIBERS_BY_USERNAME.pop(username, None)
            try:
                while True:
                    subscriber.get_nowait()
            except queue.Empty:
                subscriber.put_nowait(None)


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
SESSIONS = SessionStore(state_store=STORE)
PRESENCE = PresenceStore()
PHONE_VERIFICATIONS = PhoneVerificationStore()
OAUTH_STATES = OAuthStateStore()
UPLOAD_GRANTS = UploadGrantStore()
RATE_LIMITER = SlidingWindowRateLimiter()


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

    def allow_request(self, scope: str, limit: int, window_seconds: int) -> bool:
        client_ip = self.client_address[0] if self.client_address else "unknown"
        allowed, retry_after = RATE_LIMITER.allow(f"{scope}:{client_ip}", limit, window_seconds)
        if allowed:
            return True
        self.close_connection = True
        self.send_json(
            {"error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."},
            HTTPStatus.TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )
        return False

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
            user = self.require_auth_record()
            if user is None:
                return
            self.serve_upload(path, user)
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
        if path == "/profile/image":
            user = self.require_auth_record()
            if user is None:
                return
            self.upload_profile_image(user)
            return
        if path == "/profile/image/remove":
            user = self.require_auth_record()
            if user is None:
                return
            self.remove_profile_image(user)
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
        if path == "/rooms/settings":
            user = self.require_auth_record()
            if user is None:
                return
            self.update_group_room_settings(user)
            return
        if path == "/rooms/image":
            user = self.require_auth_record()
            if user is None:
                return
            self.upload_group_room_image(user)
            return
        if path == "/rooms/image/remove":
            user = self.require_auth_record()
            if user is None:
                return
            self.remove_group_room_image(user)
            return
        if path == "/rooms/leave":
            user = self.require_auth_record()
            if user is None:
                return
            self.leave_group_room(user)
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
            self.create_group_room(user)
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
        if path == "/uploads/discard":
            user = self.require_auth()
            if user is None:
                return
            self.discard_attachment_upload(user)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def serve_index(self) -> None:
        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "").lower()
        response_content = INDEX_GZIP_CONTENT if accepts_gzip else INDEX_CONTENT
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(response_content)))
        self.send_header("Vary", "Accept-Encoding")
        if accepts_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(response_content)

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

        content_type = {
            ".ttf": "font/ttf",
            ".otf": "font/otf",
            ".woff2": "font/woff2",
        }.get(asset_path.suffix.lower(), mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream")
        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "").lower()
        compressed_content = ASSET_GZIP_CONTENT.get(asset_path) if accepts_gzip else None
        content_length = len(compressed_content) if compressed_content is not None else asset_path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Vary", "Accept-Encoding")
        if compressed_content is not None:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Cache-Control", "public, max-age=604800, stale-while-revalidate=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            if compressed_content is not None:
                self.wfile.write(compressed_content)
            else:
                with asset_path.open("rb") as asset_file:
                    shutil.copyfileobj(asset_file, self.wfile, length=64 * 1024)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def serve_upload(self, request_path: str, user: dict) -> None:
        filename = Path(unquote(request_path.removeprefix("/uploads/"))).name
        if not filename or Path(filename).suffix.lower() not in ATTACHMENT_TYPES.values():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        if not STORE.can_access_attachment(filename, user["username"]):
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
                content = None
                content_length = upload_path.stat().st_size
        except (ConnectionError, FileNotFoundError, ValueError):
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        content_type = mimetypes.guess_type(filename)[0] or next(
            (mime_type for mime_type, extension in ATTACHMENT_TYPES.items() if extension == Path(filename).suffix.lower()),
            "application/octet-stream",
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content) if content is not None else content_length))
        self.send_header("Cache-Control", "private, max-age=31536000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "sandbox")
        self.end_headers()
        try:
            if content is not None:
                self.wfile.write(content)
            else:
                with upload_path.open("rb") as upload_file:
                    shutil.copyfileobj(upload_file, self.wfile, length=64 * 1024)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def serve_session(self) -> None:
        user = self.current_user()
        if user is None:
            self.send_json({"authenticated": False}, HTTPStatus.OK)
            return
        self.send_json({"authenticated": True, "user": user}, HTTPStatus.OK)

    def serve_messages(self, query: dict[str, list[str]]) -> None:
        room_id = query.get("room_id", [""])[0]
        user = self.current_user()
        limit_value = query.get("limit", [""])[0].strip()
        before = query.get("before", [""])[0].strip()
        if limit_value or before:
            try:
                limit = int(limit_value or DEFAULT_MESSAGES_PAGE_SIZE)
            except ValueError:
                self.send_json({"error": "올바른 메시지 개수를 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
                return
            if not 1 <= limit <= MAX_MESSAGES_PAGE_SIZE or (before and not MESSAGE_ID_PATTERN.fullmatch(before)):
                self.send_json({"error": "올바른 메시지 커서를 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
                return
            messages = STORE.get_messages_page(
                room_id,
                user["username"],
                limit=limit,
                before=before,
            ) if user else None
        else:
            messages = STORE.get_messages(room_id, user["username"]) if user else None
        if messages is None:
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return
        self.send_json(messages, HTTPStatus.OK)

    def serve_events(self, user: dict) -> None:
        if not SSE_CONNECTION_SLOTS.acquire(blocking=False):
            self.send_json({"error": "실시간 연결이 혼잡합니다. 잠시 후 다시 시도해 주세요."}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            SSE_CONNECTION_SLOTS.release()
            return

        subscriber: queue.Queue = queue.Queue(maxsize=MAX_SSE_QUEUE_SIZE)
        token = self.read_session_token()
        with SUBSCRIBERS_LOCK:
            SUBSCRIBERS[subscriber] = user["username"]
            SUBSCRIBERS_BY_USERNAME.setdefault(user["username"], set()).add(subscriber)

        if PRESENCE.connect(token, user["username"]):
            push_event(
                {
                    "type": "presence_updated",
                    "username": user["username"],
                    "presence": PRESENCE.for_user(user["username"]),
                },
                STORE.presence_event_recipients(user["username"]),
            )

        try:
            hello = {"type": "hello", "timestamp": utc_now_iso(), "username": user["username"]}
            self.wfile.write(f"data: {json.dumps(hello, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()

            while True:
                if SESSIONS.get_username(token) != user["username"]:
                    break
                try:
                    event = subscriber.get(timeout=SSE_HEARTBEAT_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    continue
                if event is None:
                    break
                payload = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
                self.wfile.write(payload)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            with SUBSCRIBERS_LOCK:
                SUBSCRIBERS.pop(subscriber, None)
                username_subscribers = SUBSCRIBERS_BY_USERNAME.get(user["username"])
                if username_subscribers is not None:
                    username_subscribers.discard(subscriber)
                    if not username_subscribers:
                        SUBSCRIBERS_BY_USERNAME.pop(user["username"], None)
            username, went_offline = PRESENCE.disconnect(token)
            if went_offline:
                push_event(
                    {"type": "presence_updated", "username": username, "presence": PRESENCE.for_user(username)},
                    STORE.presence_event_recipients(username),
                )
            SSE_CONNECTION_SLOTS.release()

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
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}")
        self.send_header("Set-Cookie", make_oauth_state_cookie(state, secure=self.cookie_secure()))
        self.end_headers()

    def finish_google_login(self, query: dict[str, list[str]]) -> None:
        if "error" in query:
            self.redirect("/?auth_error=google_access_denied")
            return

        code = query.get("code", [""])[0].strip()
        state = query.get("state", [""])[0].strip()
        cookie_state = self.read_cookie_value(OAUTH_STATE_COOKIE_NAME)
        state_is_valid = bool(cookie_state) and hmac.compare_digest(state, cookie_state)
        if not code or not state or not (state_is_valid or OAUTH_STATES.consume(state)):
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
        if not self.allow_request("google-login", 10, 15 * 60):
            return
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
            {"authenticated": True, "user": user},
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
        if not self.allow_request("demo-login", 10, 15 * 60):
            return
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
            {"authenticated": True, "user": user},
            HTTPStatus.OK,
            headers={"Set-Cookie": make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure())},
        )

    def serve_public_shorts(self, query: dict[str, list[str]], user: dict) -> None:
        if not self.allow_request(f"shorts:{user['username']}", 60, 60):
            return
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
                search_payload = fetch_youtube_json(f"https://www.googleapis.com/youtube/v3/search?{urlencode(search_params)}")
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
                video_payload = fetch_youtube_json(f"https://www.googleapis.com/youtube/v3/videos?{urlencode(video_params)}")
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
            payload = fetch_youtube_json(f"https://www.googleapis.com/youtube/v3/videos?{urlencode(popular_params)}")
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
        if not self.allow_request("signup", 10, 15 * 60):
            return
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
        if not self.allow_request(f"login:{username.lower()[:24]}", 10, 15 * 60):
            return
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
        token = self.read_session_token()
        SESSIONS.destroy(token)
        self.send_json(
            {"authenticated": False},
            HTTPStatus.OK,
            headers={"Set-Cookie": make_cookie_header("", max_age=0, secure=self.cookie_secure())},
        )

    def request_phone_code(self) -> None:
        if not self.allow_request("phone-code", 5, 10 * 60):
            return
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
        if not self.allow_request("phone-verify", 10, 10 * 60):
            return
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

    def upload_profile_image(self, user: dict) -> None:
        if not self.allow_request(f"profile-image:{user['username']}", 12, 60 * 60):
            return
        content = self.read_request_body(MAX_PROFILE_IMAGE_BYTES + MAX_PROFILE_THUMBNAIL_BYTES + 4)
        if content is None:
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        image_content = content
        thumbnail_content = b""
        if content_type == "application/x-colorless-profile-bundle" and len(content) >= 4:
            image_size = int.from_bytes(content[:4], "big")
            image_content = content[4:4 + image_size]
            thumbnail_content = content[4 + image_size:]
        elif content_type != "image/webp":
            image_content = b""

        is_valid_image = (
            0 < len(image_content) <= MAX_PROFILE_IMAGE_BYTES
            and webp_dimensions(image_content) == (PROFILE_IMAGE_SIDE, PROFILE_IMAGE_SIDE)
        )
        is_valid_thumbnail = (
            not thumbnail_content
            or (
                len(thumbnail_content) <= MAX_PROFILE_THUMBNAIL_BYTES
                and webp_dimensions(thumbnail_content) == (PROFILE_THUMBNAIL_SIDE, PROFILE_THUMBNAIL_SIDE)
            )
        )
        if not is_valid_image or not is_valid_thumbnail:
            self.send_json(
                {"error": "프로필 사진은 1024×1024 WebP 형식이어야 합니다."},
                HTTPStatus.BAD_REQUEST,
            )
            return

        filename = profile_image_filename(user["id"])
        thumbnail_filename = profile_image_filename(user["id"], thumbnail=True)
        try:
            if SUPABASE_ENABLED:
                headers = supabase_headers("image/webp")
                headers["x-upsert"] = "true"
                fetch_json(
                    supabase_object_url(filename),
                    method="POST",
                    headers=headers,
                    data=image_content,
                )
                if thumbnail_content:
                    fetch_json(
                        supabase_object_url(thumbnail_filename),
                        method="POST",
                        headers=headers,
                        data=thumbnail_content,
                    )
            else:
                UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
                upload_path = (UPLOADS_DIR / filename).resolve()
                upload_path.relative_to(UPLOADS_DIR.resolve())
                temp_path = (UPLOADS_DIR / f".{filename}.{uuid.uuid4().hex}.tmp").resolve()
                temp_path.relative_to(UPLOADS_DIR.resolve())
                try:
                    temp_path.write_bytes(image_content)
                    temp_path.replace(upload_path)
                finally:
                    temp_path.unlink(missing_ok=True)
                if thumbnail_content:
                    thumbnail_path = (UPLOADS_DIR / thumbnail_filename).resolve()
                    thumbnail_path.relative_to(UPLOADS_DIR.resolve())
                    thumbnail_temp_path = (UPLOADS_DIR / f".{thumbnail_filename}.{uuid.uuid4().hex}.tmp").resolve()
                    thumbnail_temp_path.relative_to(UPLOADS_DIR.resolve())
                    try:
                        thumbnail_temp_path.write_bytes(thumbnail_content)
                        thumbnail_temp_path.replace(thumbnail_path)
                    finally:
                        thumbnail_temp_path.unlink(missing_ok=True)
        except (ConnectionError, OSError, ValueError):
            self.send_json({"error": "프로필 사진을 저장하지 못했어요."}, HTTPStatus.BAD_GATEWAY)
            return

        profile = STORE.update_profile_image(
            user["username"],
            f"/uploads/{filename}",
            f"/uploads/{thumbnail_filename}" if thumbnail_content else "",
        )
        if profile is None:
            self.send_json({"error": "사용자를 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return
        self.send_json({"user": profile}, HTTPStatus.OK)

    def remove_profile_image(self, user: dict) -> None:
        if not self.allow_request(f"profile-image:{user['username']}", 12, 60 * 60):
            return
        profile = STORE.update_profile_image(user["username"], "")
        if profile is None:
            self.send_json({"error": "사용자를 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return

        filename = profile_image_filename(user["id"])
        thumbnail_filename = profile_image_filename(user["id"], thumbnail=True)
        try:
            if SUPABASE_ENABLED:
                for stored_filename in (filename, thumbnail_filename):
                    fetch_bytes(
                        supabase_object_url(stored_filename),
                        method="DELETE",
                        headers=supabase_headers(),
                    )
            else:
                for stored_filename in (filename, thumbnail_filename):
                    upload_path = (UPLOADS_DIR / stored_filename).resolve()
                    upload_path.relative_to(UPLOADS_DIR.resolve())
                    upload_path.unlink(missing_ok=True)
        except (ConnectionError, OSError, ValueError):
            pass
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

    def create_group_room(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        name = str(payload.get("name", "")).strip()
        raw_member_user_ids = payload.get("memberUserIds")
        if not isinstance(raw_member_user_ids, list) or len(raw_member_user_ids) > MAX_GROUP_PARTICIPANTS - 1:
            self.send_json({"error": "올바른 그룹 멤버를 선택해 주세요."}, HTTPStatus.BAD_REQUEST)
            return
        member_user_ids: list[str] = []
        for member_user_id in raw_member_user_ids:
            if not isinstance(member_user_id, str) or not USER_ID_PATTERN.fullmatch(member_user_id):
                self.send_json({"error": "올바른 그룹 멤버를 선택해 주세요."}, HTTPStatus.BAD_REQUEST)
                return
            member_user_ids.append(member_user_id)

        if not self.allow_request(f"group-room:{user['username']}", 20, 60 * 60):
            return
        room, error = STORE.create_group_room(user["username"], name, member_user_ids)
        if error or room is None:
            self.send_json({"error": error or "그룹 채팅방을 만들지 못했습니다."}, HTTPStatus.BAD_REQUEST)
            return
        try:
            self.send_json({"room": room}, HTTPStatus.CREATED)
        finally:
            push_event(
                {"type": "room_created", "room": room},
                STORE.room_event_recipients(room["id"]),
            )

    def update_group_room_settings(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        room_id = str(payload.get("roomId", "")).strip()
        name = str(payload.get("name", "")).strip()
        if not ROOM_ID_PATTERN.fullmatch(room_id) or not 1 <= len(name) <= 32:
            self.send_json({"error": "채팅방 이름은 1~32자로 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
            return
        if not self.allow_request(f"room-settings:{user['username']}", 60, 60 * 60):
            return
        room, error = STORE.update_group_room_name(user["username"], room_id, name)
        if error == "not_found":
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return
        if error == "forbidden":
            self.send_json({"error": "방장만 채팅방 정보를 변경할 수 있습니다."}, HTTPStatus.FORBIDDEN)
            return
        if error or room is None:
            self.send_json({"error": "채팅방 이름을 변경하지 못했습니다."}, HTTPStatus.BAD_REQUEST)
            return
        try:
            self.send_json({"room": room}, HTTPStatus.OK)
        finally:
            push_event(
                {"type": "room_updated", "roomId": room_id, "room": room},
                STORE.room_event_recipients(room_id),
            )

    def upload_group_room_image(self, user: dict) -> None:
        room_id = self.headers.get("X-Room-Id", "").strip()
        if not ROOM_ID_PATTERN.fullmatch(room_id):
            self.send_json({"error": "올바른 채팅방을 선택해 주세요."}, HTTPStatus.BAD_REQUEST)
            return
        access = STORE.group_room_access(user["username"], room_id)
        if access == "not_found":
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return
        if access != "owner":
            self.send_json({"error": "방장만 채팅방 사진을 변경할 수 있습니다."}, HTTPStatus.FORBIDDEN)
            return
        if not self.allow_request(f"room-image:{user['username']}", 20, 60 * 60):
            return

        content = self.read_request_body(MAX_PROFILE_IMAGE_BYTES + MAX_PROFILE_THUMBNAIL_BYTES + 4)
        if content is None:
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        image_content = b""
        thumbnail_content = b""
        if content_type == "application/x-colorless-room-bundle" and len(content) >= 4:
            image_size = int.from_bytes(content[:4], "big")
            image_content = content[4:4 + image_size]
            thumbnail_content = content[4 + image_size:]
        if (
            not 0 < len(image_content) <= MAX_PROFILE_IMAGE_BYTES
            or webp_dimensions(image_content) != (PROFILE_IMAGE_SIDE, PROFILE_IMAGE_SIDE)
            or not 0 < len(thumbnail_content) <= MAX_PROFILE_THUMBNAIL_BYTES
            or webp_dimensions(thumbnail_content) != (PROFILE_THUMBNAIL_SIDE, PROFILE_THUMBNAIL_SIDE)
        ):
            self.send_json({"error": "채팅방 사진은 1024×1024 WebP 형식이어야 합니다."}, HTTPStatus.BAD_REQUEST)
            return

        filename = room_image_filename(room_id)
        thumbnail_filename = room_image_filename(room_id, thumbnail=True)
        try:
            if SUPABASE_ENABLED:
                headers = supabase_headers("image/webp")
                headers["x-upsert"] = "true"
                for stored_filename, stored_content in (
                    (filename, image_content),
                    (thumbnail_filename, thumbnail_content),
                ):
                    fetch_json(
                        supabase_object_url(stored_filename),
                        method="POST",
                        headers=headers,
                        data=stored_content,
                    )
            else:
                UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
                for stored_filename, stored_content in (
                    (filename, image_content),
                    (thumbnail_filename, thumbnail_content),
                ):
                    upload_path = (UPLOADS_DIR / stored_filename).resolve()
                    upload_path.relative_to(UPLOADS_DIR.resolve())
                    temp_path = (UPLOADS_DIR / f".{stored_filename}.{uuid.uuid4().hex}.tmp").resolve()
                    temp_path.relative_to(UPLOADS_DIR.resolve())
                    try:
                        temp_path.write_bytes(stored_content)
                        temp_path.replace(upload_path)
                    finally:
                        temp_path.unlink(missing_ok=True)
        except (ConnectionError, OSError, ValueError):
            self.send_json({"error": "채팅방 사진을 저장하지 못했습니다."}, HTTPStatus.BAD_GATEWAY)
            return

        room, error = STORE.update_group_room_image(
            user["username"],
            room_id,
            f"/uploads/{filename}",
            f"/uploads/{thumbnail_filename}",
        )
        if error or room is None:
            status = HTTPStatus.FORBIDDEN if error == "forbidden" else HTTPStatus.NOT_FOUND
            self.send_json({"error": "채팅방 사진을 변경할 수 없습니다."}, status)
            return
        try:
            self.send_json({"room": room}, HTTPStatus.OK)
        finally:
            push_event(
                {"type": "room_updated", "roomId": room_id, "room": room},
                STORE.room_event_recipients(room_id),
            )

    def remove_group_room_image(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        room_id = str(payload.get("roomId", "")).strip()
        if not ROOM_ID_PATTERN.fullmatch(room_id):
            self.send_json({"error": "올바른 채팅방을 선택해 주세요."}, HTTPStatus.BAD_REQUEST)
            return
        if not self.allow_request(f"room-image:{user['username']}", 20, 60 * 60):
            return
        room, error = STORE.update_group_room_image(user["username"], room_id, "")
        if error == "not_found":
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return
        if error == "forbidden":
            self.send_json({"error": "방장만 채팅방 사진을 변경할 수 있습니다."}, HTTPStatus.FORBIDDEN)
            return
        if error or room is None:
            self.send_json({"error": "채팅방 사진을 삭제하지 못했습니다."}, HTTPStatus.BAD_REQUEST)
            return

        try:
            if SUPABASE_ENABLED:
                for stored_filename in (room_image_filename(room_id), room_image_filename(room_id, True)):
                    fetch_bytes(
                        supabase_object_url(stored_filename),
                        method="DELETE",
                        headers=supabase_headers(),
                    )
            else:
                for stored_filename in (room_image_filename(room_id), room_image_filename(room_id, True)):
                    upload_path = (UPLOADS_DIR / stored_filename).resolve()
                    upload_path.relative_to(UPLOADS_DIR.resolve())
                    upload_path.unlink(missing_ok=True)
        except (ConnectionError, OSError, ValueError):
            pass
        try:
            self.send_json({"room": room}, HTTPStatus.OK)
        finally:
            push_event(
                {"type": "room_updated", "roomId": room_id, "room": room},
                STORE.room_event_recipients(room_id),
            )

    def leave_group_room(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        room_id = str(payload.get("roomId", "")).strip()
        if not ROOM_ID_PATTERN.fullmatch(room_id):
            self.send_json({"error": "올바른 채팅방을 선택해 주세요."}, HTTPStatus.BAD_REQUEST)
            return
        if not self.allow_request(f"leave-room:{user['username']}", 60, 60 * 60):
            return
        room, recipients, error = STORE.leave_group_room(user["username"], room_id)
        if error:
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return
        try:
            self.send_json({"left": True, "roomId": room_id}, HTTPStatus.OK)
        finally:
            push_event(
                {
                    "type": "room_left",
                    "roomId": room_id,
                    "username": user["username"],
                    "room": room,
                },
                recipients,
            )

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
        client_message_id = str(payload.get("clientMessageId", "")).strip()
        if client_message_id and not CLIENT_MESSAGE_ID_PATTERN.fullmatch(client_message_id):
            self.send_json({"error": "올바른 메시지 식별자가 아닙니다."}, HTTPStatus.BAD_REQUEST)
            return
        attachment = self.message_attachment(payload.get("attachment"), user["username"])
        if not room_id or (not text and attachment is None):
            self.send_json({"error": "roomId와 text는 필수입니다."}, HTTPStatus.BAD_REQUEST)
            return

        try:
            result = STORE.add_message(
                room_id,
                user["username"],
                text,
                attachment,
                client_message_id,
            )
        except ValueError:
            self.send_json({"error": "같은 메시지 식별자를 다른 내용에 다시 사용할 수 없습니다."}, HTTPStatus.CONFLICT)
            return
        if result is None:
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return

        message, room, created = result
        if attachment is not None and created:
            UPLOAD_GRANTS.consume(Path(attachment["url"]).name, user["username"])
        if not created:
            self.send_json(message, HTTPStatus.OK)
            return
        try:
            self.send_json(message, HTTPStatus.CREATED)
        finally:
            push_event(
                {"type": "message_created", "roomId": room_id, "room": room, "message": message},
                STORE.room_event_recipients(room_id),
            )

    def upload_attachment(self, user: dict) -> None:
        if not self.allow_request(f"upload:{user['username']}", 30, 60 * 60):
            return
        content = self.read_request_body(MAX_ATTACHMENT_BYTES)
        if content is None:
            return

        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        extension = ATTACHMENT_TYPES.get(content_type)
        if extension is None:
            self.send_json({"error": "Only image and PDF files are supported."}, HTTPStatus.BAD_REQUEST)
            return

        if not content or not self.valid_attachment_content(content_type, content):
            self.send_json({"error": "Unsupported or oversized file. Images and PDFs can be up to 8MB."}, HTTPStatus.BAD_REQUEST)
            return

        filename = f"upload_{uuid.uuid4().hex}{extension}"
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
        original_name = Path(unquote(self.headers.get("X-File-Name", "file"))).name.strip()[:120] or f"file{extension}"
        UPLOAD_GRANTS.create(filename, user["username"])
        attachment = {
            "url": f"/uploads/{filename}",
            "name": original_name,
            "type": content_type,
            "size": len(content),
        }
        self.send_json({"attachment": attachment}, HTTPStatus.CREATED)

    def discard_attachment_upload(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        upload_url = unquote(str(payload.get("url", "")))
        filename = upload_url.removeprefix("/uploads/")
        if (
            not upload_url.startswith("/uploads/")
            or "/" in filename
            or "\\" in filename
            or not UPLOAD_NAME_PATTERN.fullmatch(filename)
            or not UPLOAD_GRANTS.consume(filename, user["username"])
        ):
            self.send_json({"discarded": False}, HTTPStatus.OK)
            return
        try:
            if SUPABASE_ENABLED:
                fetch_bytes(
                    supabase_object_url(filename),
                    method="DELETE",
                    headers=supabase_headers(),
                )
            else:
                upload_path = (UPLOADS_DIR / filename).resolve()
                upload_path.relative_to(UPLOADS_DIR.resolve())
                upload_path.unlink(missing_ok=True)
        except (ConnectionError, OSError, ValueError):
            UPLOAD_GRANTS.create(filename, user["username"])
            self.send_json({"error": "임시 첨부 파일을 정리하지 못했습니다."}, HTTPStatus.BAD_GATEWAY)
            return
        self.send_json({"discarded": True}, HTTPStatus.OK)

    def valid_attachment_content(self, content_type: str, content: bytes) -> bool:
        signatures = {
            "image/jpeg": content.startswith(b"\xff\xd8\xff"),
            "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/gif": content.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": content.startswith(b"RIFF") and content[8:12] == b"WEBP",
            "image/heic": content[4:8] == b"ftyp" and content[8:12] in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"},
            "image/heif": content[4:8] == b"ftyp" and content[8:12] in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"},
            "image/avif": content[4:8] == b"ftyp" and content[8:12] in {b"avif", b"avis"},
            "application/pdf": content.startswith(b"%PDF-"),
        }
        return signatures.get(content_type, False)

    def message_attachment(self, value: object, username: str) -> dict | None:
        if not isinstance(value, dict):
            return None
        url = str(value.get("url", "")).strip()
        filename = Path(unquote(url.removeprefix("/uploads/"))).name
        content_type = str(value.get("type", "")).strip().lower()
        if not url.startswith("/uploads/") or ATTACHMENT_TYPES.get(content_type) != Path(filename).suffix.lower():
            return None
        if not UPLOAD_GRANTS.owns(filename, username) and not STORE.can_access_attachment(filename, username):
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
        room, changed = STORE.mark_room_read(room_id, user["username"])
        if room is None:
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return
        if changed:
            push_event(
                {
                    "type": "room_read",
                    "roomId": room_id,
                    "username": user["username"],
                    "roomKind": room.get("kind", "direct"),
                },
                STORE.room_event_recipients(room_id),
            )
        self.send_json({"room": room}, HTTPStatus.OK)

    def update_presence(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        active_room_id = str(payload.get("activeRoomId", "")).strip()[:80]
        emoji = str(payload.get("emoji", "")).strip()[:16]
        changed = PRESENCE.update(self.read_session_token(), user["username"], active_room_id, emoji)
        if changed:
            push_event(
                {
                    "type": "presence_updated",
                    "username": user["username"],
                    "presence": PRESENCE.for_user(user["username"]),
                },
                STORE.presence_event_recipients(user["username"]),
            )
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
        return None

    def read_request_body(self, max_bytes: int) -> bytes | None:
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self.send_json({"error": "Chunked request bodies are not supported."}, HTTPStatus.LENGTH_REQUIRED)
            return None
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.close_connection = True
            self.send_json({"error": "Invalid Content-Length."}, HTTPStatus.BAD_REQUEST)
            return None
        if content_length < 0:
            self.close_connection = True
            self.send_json({"error": "Invalid Content-Length."}, HTTPStatus.BAD_REQUEST)
            return None
        if content_length > max_bytes:
            self.close_connection = True
            self.send_json({"error": "Request body is too large."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        return self.rfile.read(content_length)

    def read_json_body(self) -> dict | None:
        raw_body = self.read_request_body(MAX_JSON_REQUEST_BYTES)
        if raw_body is None:
            return None
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(payload, dict):
            self.send_json({"error": "JSON object expected"}, HTTPStatus.BAD_REQUEST)
            return None
        return payload

    def read_form_body(self) -> dict | None:
        raw_body = self.read_request_body(MAX_FORM_REQUEST_BYTES)
        if raw_body is None:
            return None
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


class ChatServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


if __name__ == "__main__":
    server = ChatServer((HOST, PORT), ChatHandler)
    print(f"{APP_NAME} running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        STORE.close()
