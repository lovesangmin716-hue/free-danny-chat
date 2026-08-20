from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import hmac
import json
import math
import mimetypes
import os
import queue
import re
import secrets
import shutil
import socket
import sqlite3
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import httpx

try:
    import brotli
except ImportError:  # Local stdlib-only development keeps gzip as a safe fallback.
    brotli = None

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from persistence import ConcurrentUpdateError, NormalizedSqliteRepository, NormalizedSupabaseRepository
from profile_art import (
    PROFILE_ART_PIXEL_COUNT,
    blank_profile_pixels as build_blank_profile_pixels,
    is_blank_profile_pixels,
    normalize_profile_pixels as normalize_profile_art_pixels,
    pack_profile_pixels,
    profile_art_png,
    unpack_profile_pixels,
    valid_profile_pixels as are_valid_profile_pixels,
)


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
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_STORAGE_ORIGIN = (
    f"{urlparse(SUPABASE_URL).scheme}://{urlparse(SUPABASE_URL).netloc}"
    if SUPABASE_URL and urlparse(SUPABASE_URL).scheme in {"http", "https"}
    else ""
)
INDEX_FILE = BASE_DIR / "index.html"
SIGNUP_FILE = BASE_DIR / "signup.html"
ASSETS_DIR = BASE_DIR / "assets"
INDEX_CONTENT = INDEX_FILE.read_bytes()
INDEX_GZIP_CONTENT = gzip.compress(INDEX_CONTENT, compresslevel=6)
INDEX_BROTLI_CONTENT = brotli.compress(INDEX_CONTENT, quality=5) if brotli is not None else None
INDEX_ETAG = f'"{hashlib.sha256(INDEX_CONTENT).hexdigest()}"'
SIGNUP_CONTENT = SIGNUP_FILE.read_bytes()
SIGNUP_GZIP_CONTENT = gzip.compress(SIGNUP_CONTENT, compresslevel=6)
SIGNUP_BROTLI_CONTENT = brotli.compress(SIGNUP_CONTENT, quality=5) if brotli is not None else None
SIGNUP_ETAG = f'"{hashlib.sha256(SIGNUP_CONTENT).hexdigest()}"'
COMPRESSIBLE_ASSET_SUFFIXES = {".css", ".js", ".json", ".svg"}
ASSET_CONTENT = {
    asset_path.resolve(): asset_path.read_bytes()
    for asset_path in ASSETS_DIR.rglob("*")
    if asset_path.is_file()
}
ASSET_FINGERPRINTS = {
    asset_path: hashlib.sha256(content).hexdigest()[:12]
    for asset_path, content in ASSET_CONTENT.items()
}
ASSET_GZIP_CONTENT = {
    asset_path: gzip.compress(content, compresslevel=6)
    for asset_path, content in ASSET_CONTENT.items()
    if asset_path.suffix.lower() in COMPRESSIBLE_ASSET_SUFFIXES
}
ASSET_BROTLI_CONTENT = {
    asset_path: brotli.compress(content, quality=5)
    for asset_path, content in ASSET_CONTENT.items()
    if brotli is not None and asset_path.suffix.lower() in COMPRESSIBLE_ASSET_SUFFIXES
}
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
STATE_FILE = Path(os.getenv("STATE_FILE", str(DATA_DIR / "chat_state.json")))
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(DATA_DIR / "uploads")))
MAX_MESSAGES_PER_ROOM = 200
DEFAULT_MESSAGES_PAGE_SIZE = 30
MAX_MESSAGES_PAGE_SIZE = 50
DEFAULT_ENTITY_PAGE_SIZE = 30
MAX_ENTITY_PAGE_SIZE = 100
MAX_SYNC_EVENTS = 200
MIN_GROUP_PARTICIPANTS = 3
MAX_GROUP_PARTICIPANTS = 50
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_PROFILE_IMAGE_BYTES = 3 * 1024 * 1024
MAX_PROFILE_THUMBNAIL_BYTES = 256 * 1024
MAX_JSON_REQUEST_BYTES = 1024 * 1024
MAX_FORM_REQUEST_BYTES = 64 * 1024
MAX_SHORTS_SEEN_IDS = 500
SHORTS_CATALOG_PAGE_SIZE = max(1, min(200, int(os.getenv("SHORTS_CATALOG_PAGE_SIZE", "20"))))
SHORTS_CATALOG_SCAN_SIZE = max(100, SHORTS_CATALOG_PAGE_SIZE)
SHORTS_CATALOG_TTL_SECONDS = max(15 * 60, int(os.getenv("SHORTS_CATALOG_TTL_SECONDS", "21600")))
SHORTS_CATALOG_RETENTION_SECONDS = max(
    SHORTS_CATALOG_TTL_SECONDS,
    int(os.getenv("SHORTS_CATALOG_RETENTION_SECONDS", "604800")),
)
SHORTS_COLLECTION_INTERVAL_SECONDS = max(10, int(os.getenv("SHORTS_COLLECTION_INTERVAL_SECONDS", "1800")))
SHORTS_COLLECTION_LEASE_SECONDS = max(30, int(os.getenv("SHORTS_COLLECTION_LEASE_SECONDS", "120")))
SHORTS_DAILY_QUOTA_BUDGET = max(100, int(os.getenv("SHORTS_DAILY_QUOTA_BUDGET", "5000")))
MAX_REQUEST_THREADS = max(16, min(1024, int(os.getenv("MAX_REQUEST_THREADS", "64"))))
MAX_BODY_READERS = max(1, min(MAX_REQUEST_THREADS - 1, int(os.getenv("MAX_BODY_READERS", "16"))))
HEADER_READ_TIMEOUT_SECONDS = max(1.0, min(60.0, float(os.getenv("HEADER_READ_TIMEOUT_SECONDS", "5"))))
BODY_READ_TIMEOUT_SECONDS = max(1.0, min(120.0, float(os.getenv("BODY_READ_TIMEOUT_SECONDS", "10"))))
UPLOAD_READ_TIMEOUT_SECONDS = max(
    BODY_READ_TIMEOUT_SECONDS,
    min(300.0, float(os.getenv("UPLOAD_READ_TIMEOUT_SECONDS", "30"))),
)
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "script-src 'self' https://accounts.google.com",
        "style-src 'self' 'unsafe-inline'",
        f"img-src 'self' data: blob: {SUPABASE_STORAGE_ORIGIN}".rstrip(),
        "font-src 'self'",
        f"connect-src 'self' https://accounts.google.com https://www.googleapis.com {SUPABASE_STORAGE_ORIGIN}".rstrip(),
        "frame-src https://accounts.google.com https://www.youtube-nocookie.com",
        "form-action 'self' https://accounts.google.com https://kauth.kakao.com",
        f"media-src 'self' blob: {SUPABASE_STORAGE_ORIGIN}".rstrip(),
        "worker-src 'self' blob:",
    )
)
COMMON_SECURITY_HEADERS = (
    ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    (
        "Permissions-Policy",
        'accelerometer=(), camera=(), geolocation=(), gyroscope=(), microphone=(), payment=(), usb=(), '
        'autoplay=(self "https://www.youtube-nocookie.com"), fullscreen=(self "https://www.youtube-nocookie.com")',
    ),
    ("Cross-Origin-Opener-Policy", "same-origin-allow-popups"),
)
MAX_SSE_QUEUE_SIZE = max(8, min(256, int(os.getenv("MAX_SSE_QUEUE_SIZE", "32"))))
MAX_SSE_CONNECTIONS = max(
    1,
    min(MAX_REQUEST_THREADS - 8, 512, int(os.getenv("MAX_SSE_CONNECTIONS", "32"))),
)
SSE_HEARTBEAT_SECONDS = max(5, min(60, int(os.getenv("SSE_HEARTBEAT_SECONDS", "10"))))
SESSION_CLEANUP_INTERVAL_SECONDS = 60
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
SESSION_REFRESH_THRESHOLD_SECONDS = 24 * 60 * 60
SESSION_VALIDATION_CACHE_SECONDS = max(
    0.0,
    min(60.0, float(os.getenv("SESSION_VALIDATION_CACHE_SECONDS", "5"))),
)
MAX_SESSIONS = 10_000
STRUCTURED_LOGS_ENABLED = os.getenv("STRUCTURED_LOGS_ENABLED", "true").lower() not in {"0", "false", "off"}
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{8,128}")
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
ATTACHMENT_IMAGE_PROBE_BYTES = 512 * 1024
ATTACHMENT_IMAGE_PIXELS_MAX = 32 * 1000 * 1000
ATTACHMENT_IMAGE_DIMENSION_MAX = 16384
PROFILE_PIXEL_SIDE = 32
PROFILE_PIXEL_COUNT = PROFILE_PIXEL_SIDE * PROFILE_PIXEL_SIDE
PROFILE_IMAGE_SIDE = 1024
PROFILE_THUMBNAIL_SIDE = 128
PROFILE_IMAGE_NAME_PATTERN = re.compile(r"profile_[0-9a-f]{24}(?:_thumb)?\.webp")
ROOM_IMAGE_NAME_PATTERN = re.compile(r"room_[0-9a-f]{24}(?:_thumb)?\.webp")
ROOM_ID_PATTERN = re.compile(r"room_[0-9a-f]{8}")
ROOM_MEMBERS_PATH_PATTERN = re.compile(r"/rooms/(room_[0-9a-f]{8})/members")
PROFILE_ART_THUMBNAIL_PATH_PATTERN = re.compile(r"/profile-art/(user_[0-9a-f]{8})/thumbnail")
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
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_STATE_TABLE = "app_state"
SUPABASE_STATE_ID = "primary"
SUPABASE_UPLOAD_BUCKET = "chat-uploads"
SUPABASE_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)
UPLOAD_GRANT_TTL_SECONDS = max(60, min(30 * 60, int(os.getenv("UPLOAD_GRANT_TTL_SECONDS", "600"))))
DOWNLOAD_URL_TTL_SECONDS = max(15, min(5 * 60, int(os.getenv("DOWNLOAD_URL_TTL_SECONDS", "60"))))
INSTANCE_ID = os.getenv("INSTANCE_ID", "").strip() or f"instance-{secrets.token_hex(8)}"
EVENT_POLL_INTERVAL_SECONDS = max(0.05, float(os.getenv("EVENT_POLL_INTERVAL_SECONDS", "0.1")))
PRESENCE_TTL_SECONDS = max(15, min(60, int(os.getenv("PRESENCE_TTL_SECONDS", "45"))))
REQUIRE_SUPABASE = os.getenv("REQUIRE_SUPABASE", "false").lower() == "true"
if REQUIRE_SUPABASE and not SUPABASE_ENABLED:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for persistent storage.")
if REQUIRE_SUPABASE and (GOOGLE_CLIENT_ID or KAKAO_REST_API_KEY) and not PUBLIC_BASE_URL:
    raise RuntimeError("PUBLIC_BASE_URL is required when OAuth providers are enabled in production.")
SUBSCRIBERS: dict[queue.Queue, str] = {}
SUBSCRIBERS_BY_USERNAME: dict[str, set[queue.Queue]] = {}
SUBSCRIBERS_LOCK = threading.Lock()
SSE_CONNECTION_SLOTS = threading.BoundedSemaphore(MAX_SSE_CONNECTIONS)
SHORTS_FEED_LOCK = threading.Lock()
OUTBOUND_HTTP_CLIENT = httpx.Client(
    timeout=httpx.Timeout(30.0, connect=10.0),
    limits=httpx.Limits(max_connections=64, max_keepalive_connections=24, keepalive_expiry=30.0),
    follow_redirects=True,
)
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


def process_rss_bytes() -> int:
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            get_current_process = ctypes.windll.kernel32.GetCurrentProcess
            get_current_process.restype = wintypes.HANDLE
            get_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
            get_memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
            get_memory_info.restype = wintypes.BOOL
            handle = get_current_process()
            if get_memory_info(handle, ctypes.byref(counters), counters.cb):
                return int(counters.WorkingSetSize)
            return 0
        statm = Path("/proc/self/statm")
        if statm.exists():
            resident_pages = int(statm.read_text(encoding="ascii").split()[1])
            return resident_pages * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return 0
    return 0


class SseRuntimeMetrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started_at = time.monotonic()
        self.active = 0
        self.accepted_total = 0
        self.rejected_total = 0
        self.disconnected_total = 0
        self.events_enqueued_total = 0
        self.queue_drops_total = 0
        self.heartbeats_total = 0
        self.events_published_total = 0
        self.event_publish_failures_total = 0
        self.events_consumed_total = 0
        self.event_consume_failures_total = 0
        self.events_replayed_total = 0
        self.reconnects_total = 0
        self.delivery_latencies_ms: deque[float] = deque(maxlen=2_000)

    def increment(self, name: str, amount: int = 1) -> None:
        with self.lock:
            setattr(self, name, max(0, int(getattr(self, name)) + amount))

    def snapshot(self) -> dict:
        with self.lock:
            ordered_latencies = sorted(self.delivery_latencies_ms)
            p95_index = max(0, math.ceil(len(ordered_latencies) * 0.95) - 1)
            return {
                "active": self.active,
                "accepted_total": self.accepted_total,
                "rejected_total": self.rejected_total,
                "disconnected_total": self.disconnected_total,
                "events_enqueued_total": self.events_enqueued_total,
                "queue_drops_total": self.queue_drops_total,
                "heartbeats_total": self.heartbeats_total,
                "events_published_total": self.events_published_total,
                "event_publish_failures_total": self.event_publish_failures_total,
                "events_consumed_total": self.events_consumed_total,
                "event_consume_failures_total": self.event_consume_failures_total,
                "events_replayed_total": self.events_replayed_total,
                "reconnects_total": self.reconnects_total,
                "event_delivery_p95_ms": round(ordered_latencies[p95_index], 3) if ordered_latencies else 0,
                "uptime_seconds": round(time.monotonic() - self.started_at, 3),
            }

    def record_delivery_latency(self, milliseconds: float) -> None:
        with self.lock:
            self.delivery_latencies_ms.append(max(0.0, milliseconds))


SSE_METRICS = SseRuntimeMetrics()


class RequestRuntimeMetrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.accepted_total = 0
        self.rejected_total = 0
        self.header_timeouts_total = 0
        self.body_timeouts_total = 0
        self.body_reader_rejections_total = 0
        self.active_body_readers = 0
        self.completed_total = 0
        self.client_errors_total = 0
        self.server_errors_total = 0
        self.request_bytes_total = 0
        self.response_bytes_total = 0
        self.latencies_ms: deque[float] = deque(maxlen=10_000)
        self.routes: OrderedDict[str, dict] = OrderedDict()

    def increment(self, name: str, amount: int = 1) -> None:
        with self.lock:
            setattr(self, name, max(0, int(getattr(self, name)) + amount))

    def snapshot(self) -> dict:
        with self.lock:
            total_latency = self._latency_summary(self.latencies_ms)
            route_metrics = {
                route: {
                    "count": values["count"],
                    "server_errors": values["server_errors"],
                    "error_rate": round(values["server_errors"] / values["count"], 6) if values["count"] else 0,
                    "request_bytes": values["request_bytes"],
                    "response_bytes": values["response_bytes"],
                    "latency_ms": self._latency_summary(values["latencies"]),
                }
                for route, values in self.routes.items()
            }
            return {
                "active": self.active,
                "accepted_total": self.accepted_total,
                "rejected_total": self.rejected_total,
                "header_timeouts_total": self.header_timeouts_total,
                "body_timeouts_total": self.body_timeouts_total,
                "body_reader_rejections_total": self.body_reader_rejections_total,
                "active_body_readers": self.active_body_readers,
                "completed_total": self.completed_total,
                "client_errors_total": self.client_errors_total,
                "server_errors_total": self.server_errors_total,
                "server_error_rate": round(self.server_errors_total / self.completed_total, 6) if self.completed_total else 0,
                "request_bytes_total": self.request_bytes_total,
                "response_bytes_total": self.response_bytes_total,
                "latency_ms": total_latency,
                "routes": route_metrics,
            }

    @staticmethod
    def _latency_summary(values) -> dict:
        ordered = sorted(values)
        if not ordered:
            return {"p50": 0, "p95": 0, "p99": 0, "max": 0}
        value_at = lambda quantile: ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]
        return {
            "p50": round(value_at(0.50), 3),
            "p95": round(value_at(0.95), 3),
            "p99": round(value_at(0.99), 3),
            "max": round(ordered[-1], 3),
        }

    def record(
        self,
        route: str,
        status: int,
        latency_ms: float,
        request_bytes: int,
        response_bytes: int,
    ) -> None:
        with self.lock:
            self.completed_total += 1
            self.client_errors_total += int(400 <= status < 500)
            self.server_errors_total += int(status >= 500)
            self.request_bytes_total += max(0, request_bytes)
            self.response_bytes_total += max(0, response_bytes)
            self.latencies_ms.append(max(0.0, latency_ms))
            values = self.routes.pop(route, None)
            if values is None:
                values = {
                    "count": 0,
                    "server_errors": 0,
                    "request_bytes": 0,
                    "response_bytes": 0,
                    "latencies": deque(maxlen=2_000),
                }
            values["count"] += 1
            values["server_errors"] += int(status >= 500)
            values["request_bytes"] += max(0, request_bytes)
            values["response_bytes"] += max(0, response_bytes)
            values["latencies"].append(max(0.0, latency_ms))
            self.routes[route] = values
            while len(self.routes) > 100:
                self.routes.popitem(last=False)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def new_friend_code() -> str:
    return f"cl_{secrets.token_hex(4)}"


def encode_page_cursor(*values: str) -> str:
    payload = json.dumps(list(values), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_page_cursor(value: str, size: int) -> tuple[str, ...]:
    if not value:
        return ()
    if len(value) > 1024 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid cursor")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(value + padding).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid cursor") from error
    if not isinstance(decoded, list) or len(decoded) != size or any(not isinstance(item, str) for item in decoded):
        raise ValueError("invalid cursor")
    return tuple(decoded)


def normalize_friend_code(value: object) -> str:
    friend_code = str(value or "").strip().removeprefix("@")
    return friend_code.upper() if friend_code.upper().startswith("CL-") else friend_code.lower()


def blank_profile_pixels() -> list[str]:
    return build_blank_profile_pixels()


def valid_profile_pixels(value: object) -> bool:
    return are_valid_profile_pixels(value) and PROFILE_PIXEL_COUNT == PROFILE_ART_PIXEL_COUNT


def normalize_profile_pixels(value: object) -> list[str]:
    return normalize_profile_art_pixels(value, tuple(PROFILE_PALETTE))


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


def attachment_image_dimensions(content_type: str, content: bytes) -> tuple[int, int] | None:
    if content_type == "image/png" and len(content) >= 24:
        return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
    if content_type == "image/gif" and len(content) >= 10:
        return int.from_bytes(content[6:8], "little"), int.from_bytes(content[8:10], "little")
    if content_type == "image/webp" and len(content) >= 30:
        chunk_type = content[12:16]
        if chunk_type == b"VP8X":
            return int.from_bytes(content[24:27], "little") + 1, int.from_bytes(content[27:30], "little") + 1
        if chunk_type == b"VP8L" and content[20] == 0x2F:
            packed = int.from_bytes(content[21:25], "little")
            return (packed & 0x3FFF) + 1, ((packed >> 14) & 0x3FFF) + 1
        if chunk_type == b"VP8 " and content[23:26] == b"\x9d\x01\x2a":
            return int.from_bytes(content[26:28], "little") & 0x3FFF, int.from_bytes(content[28:30], "little") & 0x3FFF
        return None
    if content_type == "image/jpeg" and content.startswith(b"\xff\xd8"):
        offset = 2
        sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while offset + 4 <= len(content):
            if content[offset] != 0xFF:
                offset += 1
                continue
            marker = content[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(content):
                return None
            segment_length = int.from_bytes(content[offset:offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(content):
                return None
            if marker in sof_markers and segment_length >= 7:
                return (
                    int.from_bytes(content[offset + 5:offset + 7], "big"),
                    int.from_bytes(content[offset + 3:offset + 5], "big"),
                )
            offset += segment_length
        return None
    if content_type in {"image/heic", "image/heif", "image/avif"}:
        offset = content.find(b"ispe")
        while offset >= 0:
            if offset >= 4 and offset + 16 <= len(content):
                box_size = int.from_bytes(content[offset - 4:offset], "big")
                width = int.from_bytes(content[offset + 8:offset + 12], "big")
                height = int.from_bytes(content[offset + 12:offset + 16], "big")
                if box_size >= 20 and width and height:
                    return width, height
            offset = content.find(b"ispe", offset + 4)
    return None


def safe_attachment_image_dimensions(content_type: str, content: bytes) -> bool:
    dimensions = attachment_image_dimensions(content_type, content)
    if dimensions is None:
        return False
    width, height = dimensions
    return (
        0 < width <= ATTACHMENT_IMAGE_DIMENSION_MAX
        and 0 < height <= ATTACHMENT_IMAGE_DIMENSION_MAX
        and width * height <= ATTACHMENT_IMAGE_PIXELS_MAX
    )


def accepts_content_encoding(header_value: str, encoding: str) -> bool:
    qualities: dict[str, float] = {}
    for raw_item in header_value.lower().split(","):
        parts = [part.strip() for part in raw_item.split(";") if part.strip()]
        if not parts:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            if parameter.startswith("q="):
                try:
                    quality = float(parameter[2:])
                except ValueError:
                    quality = 0.0
        qualities[parts[0]] = quality
    if encoding in qualities:
        return qualities[encoding] > 0
    return qualities.get("*", 0) > 0


def normalized_request_route(method: str, request_target: str) -> str:
    path = urlparse(request_target).path
    if re.fullmatch(r"/uploads/upload_[0-9a-f]{32}\.[a-z0-9]+", path):
        path = "/uploads/:object"
    else:
        path = re.sub(r"/rooms/room_[0-9a-f]{8}/members", "/rooms/:room_id/members", path)
        path = re.sub(r"/profile-art/user_[0-9a-f]{8}/thumbnail", "/profile-art/:user_id/thumbnail", path)
    return f"{method.upper()} {path[:200]}"


def safe_user_identifier(username: str) -> str:
    return hashlib.sha256(username.encode("utf-8")).hexdigest()[:12] if username else ""


def process_open_file_descriptors() -> int:
    descriptor_path = Path("/proc/self/fd")
    if not descriptor_path.is_dir():
        return 0
    try:
        return len(list(descriptor_path.iterdir()))
    except OSError:
        return 0


def write_structured_log(payload: dict) -> None:
    if not STRUCTURED_LOGS_ENABLED:
        return
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


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


def make_oauth_state_cookie(state: str, *, secure: bool, max_age: int = OAUTH_STATE_TTL_SECONDS) -> str:
    cookie = SimpleCookie()
    cookie[OAUTH_STATE_COOKIE_NAME] = state
    cookie[OAUTH_STATE_COOKIE_NAME]["path"] = "/"
    cookie[OAUTH_STATE_COOKIE_NAME]["httponly"] = True
    cookie[OAUTH_STATE_COOKIE_NAME]["samesite"] = "Lax"
    cookie[OAUTH_STATE_COOKIE_NAME]["max-age"] = str(max_age)
    if max_age <= 0:
        cookie[OAUTH_STATE_COOKIE_NAME]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    if secure:
        cookie[OAUTH_STATE_COOKIE_NAME]["secure"] = True
    return cookie.output(header="").strip()


def clear_oauth_state_cookie(*, secure: bool) -> str:
    return make_oauth_state_cookie("", secure=secure, max_age=0)


def normalize_phone(phone: str) -> str:
    digits = "".join(character for character in phone if character.isdigit())
    if len(digits) not in (10, 11):
        return ""
    if not digits.startswith("0"):
        return ""
    return digits


def saved_activity_emoji(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 16:
        return ""
    if re.fullmatch(r"[0-9#*]\ufe0f?\u20e3", normalized):
        return normalized

    base_codepoints: list[int] = []
    regional_count = 0
    has_joiner = "\u200d" in normalized
    for character in normalized:
        codepoint = ord(character)
        if 0x1F3FB <= codepoint <= 0x1F3FF:
            continue
        is_regional = 0x1F1E6 <= codepoint <= 0x1F1FF
        is_emoji_base = (
            0x1F000 <= codepoint <= 0x1FAFF
            or 0x2300 <= codepoint <= 0x23FF
            or 0x2600 <= codepoint <= 0x27BF
            or 0x2B00 <= codepoint <= 0x2BFF
            or codepoint in {0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299}
        )
        if is_emoji_base:
            base_codepoints.append(codepoint)
            regional_count += int(is_regional)
            continue
        if codepoint in {0x200D, 0x20E3, 0xFE0E, 0xFE0F} or 0xE0020 <= codepoint <= 0xE007F:
            continue
        return ""
    if not base_codepoints:
        return ""
    if len(base_codepoints) == 1 or has_joiner:
        return normalized
    if len(base_codepoints) == 2 and regional_count == 2:
        return normalized
    return ""


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
    try:
        response = OUTBOUND_HTTP_CLIENT.request(method, url, headers=headers, content=data, timeout=15.0)
        if response.is_error:
            raise ValueError(response.text or f"HTTP {response.status_code}")
        return response.json() if response.content else {}
    except httpx.RequestError as error:
        raise ConnectionError(str(error)) from error


def fetch_bytes(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> bytes:
    try:
        response = OUTBOUND_HTTP_CLIENT.request(method, url, headers=headers, content=data)
        if response.is_error:
            raise ValueError(response.text or f"HTTP {response.status_code}")
        return response.content
    except httpx.RequestError as error:
        raise ConnectionError(str(error)) from error


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


class YoutubeCatalogError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def fetch_youtube_catalog_json(url: str, *, attempts: int = 3) -> dict:
    for attempt in range(attempts):
        try:
            response = OUTBOUND_HTTP_CLIENT.get(url, timeout=15.0)
            if response.is_error:
                code = f"http-{response.status_code}"
                if (
                    response.status_code in {403, 429}
                    or response.status_code < 500
                    or attempt + 1 >= attempts
                ):
                    raise YoutubeCatalogError(code)
            else:
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
        except httpx.RequestError as error:
            code = "network"
            if attempt + 1 >= attempts:
                raise YoutubeCatalogError(code) from error
        time.sleep((0.25 * (2 ** attempt)) + (secrets.randbelow(100) / 1000))
    raise YoutubeCatalogError("unknown")


def youtube_catalog_item(video: dict, rank_score: float, *, max_duration: int) -> dict | None:
    duration = youtube_duration_seconds(str(video.get("contentDetails", {}).get("duration", "")))
    if not 0 < duration <= max_duration or not video.get("status", {}).get("embeddable", False):
        return None
    snippet = video.get("snippet", {})
    language = str(snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or "").lower()
    if language and not language.startswith("ko"):
        return None
    text = f"{snippet.get('title', '')} {snippet.get('channelTitle', '')}".lower()
    if any(term.lower() in text for term in YOUTH_SHORTS_BLOCKLIST):
        return None
    video_id = str(video.get("id", "")).strip()
    if not video_id:
        return None
    return {
        "id": video_id,
        "title": str(snippet.get("title", "YouTube 쇼츠")),
        "channel_title": str(snippet.get("channelTitle", "YouTube")),
        "rank_score": rank_score,
    }


def collect_youtube_catalog_job(job: dict) -> list[dict]:
    if job["kind"] == "popular":
        params = {
            "key": YOUTUBE_API_KEY,
            "part": "snippet,contentDetails,status",
            "chart": "mostPopular",
            "regionCode": "KR",
            "maxResults": "50",
            "videoCategoryId": job["value"],
        }
        payload = fetch_youtube_catalog_json(
            f"https://www.googleapis.com/youtube/v3/videos?{urlencode(params)}"
        )
        candidates = [
            youtube_catalog_item(video, 2000 - index, max_duration=600)
            for index, video in enumerate(payload.get("items", []))
        ]
        return [item for item in candidates if item is not None]

    search_params = {
        "key": YOUTUBE_API_KEY,
        "part": "snippet",
        "q": job["value"],
        "type": "video",
        "maxResults": "50",
        "order": "viewCount",
        "regionCode": "KR",
        "relevanceLanguage": "ko",
        "videoDuration": "short",
        "videoEmbeddable": "true",
        "videoSyndicated": "true",
    }
    search_payload = fetch_youtube_catalog_json(
        f"https://www.googleapis.com/youtube/v3/search?{urlencode(search_params)}"
    )
    video_ids = [
        str(item.get("id", {}).get("videoId", "")).strip()
        for item in search_payload.get("items", [])
        if str(item.get("id", {}).get("videoId", "")).strip()
    ]
    if not video_ids:
        return []
    video_params = {
        "key": YOUTUBE_API_KEY,
        "part": "snippet,contentDetails,status",
        "id": ",".join(video_ids),
        "maxResults": "50",
    }
    video_payload = fetch_youtube_catalog_json(
        f"https://www.googleapis.com/youtube/v3/videos?{urlencode(video_params)}"
    )
    rank_by_id = {video_id: 1000 - index for index, video_id in enumerate(video_ids)}
    candidates = [
        youtube_catalog_item(video, rank_by_id.get(str(video.get("id", "")), 0), max_duration=180)
        for video in video_payload.get("items", [])
    ]
    return [item for item in candidates if item is not None]


class ShortsCatalogCollector:
    def __init__(self, repository, instance_id: str, *, start: bool = True) -> None:
        self.repository = repository
        self.instance_id = instance_id
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.runs = 0
        self.successes = 0
        self.failures = 0
        self.external_calls = 0
        self.items_upserted = 0
        self.lease_skips = 0
        self.feed_requests = 0
        self.catalog_hits = 0
        self.stale_hits = 0
        self.emergency_hits = 0
        self.last_run_ms = 0.0
        self.jobs = [
            {"name": f"search-{index}", "kind": "search", "value": query, "quota": 101}
            for index, query in enumerate(korean_shorts_search_queries())
        ]
        self.thread = threading.Thread(target=self._run, name="shorts-catalog-collector", daemon=True)
        if start:
            self.start()

    def start(self) -> None:
        if not self.thread.is_alive() and self.thread.ident is None:
            self.thread.start()

    def run_once(self) -> bool:
        if not YOUTUBE_API_KEY or not self.jobs:
            return False
        started = time.monotonic()
        # search.list (100 units) + videos.list (1 unit), reserved atomically.
        lease = self.repository.acquire_shorts_collection_lease(
            self.instance_id,
            time.time(),
            SHORTS_COLLECTION_LEASE_SECONDS,
            101,
            SHORTS_DAILY_QUOTA_BUDGET,
        )
        if lease is None:
            with self.lock:
                self.lease_skips += 1
            return False
        job_index = int(lease.get("next_job_index", 0)) % len(self.jobs)
        job = self.jobs[job_index]
        with self.lock:
            self.runs += 1
            self.external_calls += 1 if job["kind"] == "popular" else 2
        try:
            items = collect_youtube_catalog_job(job)
            now = time.time()
            self.repository.upsert_shorts_catalog(items, str(job["name"]), now, SHORTS_CATALOG_TTL_SECONDS)
            self.repository.prune_shorts_catalog(now - SHORTS_CATALOG_RETENTION_SECONDS)
            self.repository.finish_shorts_collection(
                self.instance_id,
                now=now,
                next_job=(job_index + 1) % len(self.jobs),
                success=True,
            )
            with self.lock:
                self.successes += 1
                self.items_upserted += len(items)
            return True
        except YoutubeCatalogError as error:
            circuit_seconds = 15 * 60 if error.code in {"http-429", "http-403"} else 2 * 60
            self.repository.finish_shorts_collection(
                self.instance_id,
                now=time.time(),
                next_job=job_index,
                success=False,
                error=error.code,
                circuit_seconds=circuit_seconds,
            )
            with self.lock:
                self.failures += 1
            return False
        except Exception:
            self.repository.finish_shorts_collection(
                self.instance_id,
                now=time.time(),
                next_job=job_index,
                success=False,
                error="collector-error",
                circuit_seconds=2 * 60,
            )
            with self.lock:
                self.failures += 1
            return False
        finally:
            with self.lock:
                self.last_run_ms = round((time.monotonic() - started) * 1000, 3)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.run_once()
            self.wake_event.wait(SHORTS_COLLECTION_INTERVAL_SECONDS)
            self.wake_event.clear()

    def snapshot(self) -> dict:
        with self.lock:
            runtime = {
                "runs": self.runs,
                "successes": self.successes,
                "failures": self.failures,
                "external_calls": self.external_calls,
                "items_upserted": self.items_upserted,
                "lease_skips": self.lease_skips,
                "feed_requests": self.feed_requests,
                "catalog_hits": self.catalog_hits,
                "stale_hits": self.stale_hits,
                "emergency_hits": self.emergency_hits,
                "catalog_hit_rate": round(self.catalog_hits / self.feed_requests, 4) if self.feed_requests else 0,
                "last_run_ms": self.last_run_ms,
            }
        try:
            return {**runtime, **self.repository.shorts_catalog_status(time.time())}
        except Exception:
            return {**runtime, "status_error": True}

    def record_feed(self, *, catalog_hit: bool, stale_hit: bool, emergency_hit: bool) -> None:
        with self.lock:
            self.feed_requests += 1
            self.catalog_hits += int(catalog_hit)
            self.stale_hits += int(stale_hit)
            self.emergency_hits += int(emergency_hit)

    def close(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)


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


def supabase_signed_upload_url(filename: str) -> str:
    response = fetch_json(
        f"{SUPABASE_URL}/storage/v1/object/upload/sign/{SUPABASE_UPLOAD_BUCKET}/{quote(filename)}",
        method="POST",
        headers=supabase_headers("application/json"),
        data=b"{}",
    )
    relative_url = str(response.get("url", "")) if isinstance(response, dict) else ""
    if not relative_url:
        raise ValueError("Storage did not return a signed upload URL")
    if relative_url.startswith(("http://", "https://")):
        return relative_url
    return f"{SUPABASE_URL}/storage/v1/{relative_url.lstrip('/')}"


def supabase_signed_download_url(filename: str, expires_in: int = DOWNLOAD_URL_TTL_SECONDS) -> str:
    response = fetch_json(
        f"{SUPABASE_URL}/storage/v1/object/sign/{SUPABASE_UPLOAD_BUCKET}/{quote(filename)}",
        method="POST",
        headers=supabase_headers("application/json"),
        data=json.dumps({"expiresIn": expires_in}, separators=(",", ":")).encode("utf-8"),
    )
    relative_url = str(response.get("signedURL", "")) if isinstance(response, dict) else ""
    if not relative_url:
        raise ValueError("Storage did not return a signed download URL")
    if relative_url.startswith(("http://", "https://")):
        return relative_url
    return f"{SUPABASE_URL}/storage/v1/{relative_url.lstrip('/')}"


def probe_supabase_upload(filename: str) -> tuple[int, str, bytes]:
    headers = supabase_headers()
    headers["Range"] = f"bytes=0-{ATTACHMENT_IMAGE_PROBE_BYTES - 1}"
    try:
        with OUTBOUND_HTTP_CLIENT.stream(
            "GET",
            supabase_object_url(filename),
            headers=headers,
            timeout=15.0,
        ) as response:
            if response.is_error:
                response.read()
                raise ValueError(f"Storage verification failed with HTTP {response.status_code}")
            prefix = b""
            for chunk in response.iter_bytes():
                prefix += chunk
                if len(prefix) >= ATTACHMENT_IMAGE_PROBE_BYTES:
                    prefix = prefix[:ATTACHMENT_IMAGE_PROBE_BYTES]
                    break
            content_range = response.headers.get("Content-Range", "")
            match = re.fullmatch(r"bytes \d+-\d+/(\d+)", content_range)
            size = int(match.group(1)) if match else int(response.headers.get("Content-Length", "0"))
            content_type = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].lower()
            return size, content_type, prefix
    except httpx.RequestError as error:
        raise ConnectionError(str(error)) from error


def delete_upload_object(filename: str) -> None:
    if SUPABASE_ENABLED:
        fetch_bytes(supabase_object_url(filename), method="DELETE", headers=supabase_headers())
        return
    upload_path = (UPLOADS_DIR / filename).resolve()
    upload_path.relative_to(UPLOADS_DIR.resolve())
    upload_path.unlink(missing_ok=True)


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
            }

    def create_pending(
        self,
        filename: str,
        username: str,
        *,
        name: str,
        content_type: str,
        size: int,
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


def cleanup_expired_uploads() -> int:
    removed = 0
    for grant in UPLOAD_GRANTS.pop_expired():
        try:
            delete_upload_object(str(grant["filename"]))
            removed += 1
        except (ConnectionError, OSError, ValueError):
            pass
    return removed


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


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
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
        presence = PRESENCE.for_user(user["username"])
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
        if all_messages is None:
            all_messages = self._room_messages_locked(room["id"])
        message_positions = {
            message["id"]: index
            for index, message in enumerate(all_messages)
        }
        participant_ids = list(room.get("participant_ids", []))
        reader_positions: dict[str, int] = {}
        last_read_by = room.get("last_read_by", {})
        for reader_id in participant_ids:
            reader_positions[reader_id] = message_positions.get(
                str(last_read_by.get(reader_id, "")),
                -1,
            )

        response_messages: list[dict] = []
        for message in messages:
            mine = message.get("username") == user["username"]
            sender = self._users_by_username.get(str(message.get("username", "")))
            sender_id = str(sender.get("id", "")) if sender is not None else ""
            message_position = message_positions.get(str(message.get("id")), -1)
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
                if reader_positions.get(reader_id, -1) >= message_position >= 0
                if (reader := self._users_by_id.get(reader_id)) is not None
            ]
            unread_by = [
                {
                    "id": reader["id"],
                    "username": reader["username"],
                    "display_name": reader.get("display_name") or reader["username"],
                }
                for reader_id in eligible_reader_ids
                if reader_positions.get(reader_id, -1) < message_position or message_position < 0
                if (reader := self._users_by_id.get(reader_id)) is not None
            ]
            response_message = {
                **message,
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
            all_messages = self._room_messages_locked(room_id)
            if around:
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
            if before:
                page_end = next(
                    (index for index, message in enumerate(all_messages) if message.get("id") == before),
                    0,
                )
                available_messages = all_messages[:page_end]
            else:
                available_messages = all_messages
            messages = available_messages[-(limit + 1):]
            has_more = len(available_messages) > limit
            if has_more:
                messages = messages[-limit:]
            page_messages = self._messages_with_read_state_locked(
                room,
                user,
                messages,
                all_messages=all_messages,
            )
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
                    PRESENCE.set_demo_active(contact["username"], active_emojis[index - 1])
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
                        return existing_message, self._room_summary(room), False
                    raise ValueError("message persistence constraint failed")
                room_messages.clear()
            if self.repository is not None:
                self._save_locked("rooms")
            else:
                self._save_locked("rooms", f"messages:{room_id}")
            return message, self._room_summary(room), True

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


def push_event(event: dict, recipients: set[str]) -> None:
    if not recipients:
        return
    with SUBSCRIBERS_LOCK:
        dead_subscribers: list[queue.Queue] = []
        for username in recipients:
            for subscriber in SUBSCRIBERS_BY_USERNAME.get(username, ()):
                try:
                    subscriber.put_nowait(event)
                    SSE_METRICS.increment("events_enqueued_total")
                except queue.Full:
                    SSE_METRICS.increment("queue_drops_total")
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


class DurableEventBroker:
    """Shared database event log with local fan-out and retryable publishing."""

    def __init__(
        self,
        repository,
        instance_id: str,
        presence_recipients,
        state_refresh,
        deliver=push_event,
    ) -> None:
        self.repository = repository
        self.instance_id = instance_id
        self.presence_recipients = presence_recipients
        self.state_refresh = state_refresh
        self.deliver = deliver
        self.cursor = repository.latest_event_sequence()
        self.lock = threading.Lock()
        self.outbox: deque[tuple[dict, set[str]]] = deque(maxlen=10_000)
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.next_presence_cleanup = time.monotonic()
        self.thread = threading.Thread(target=self._run, name="durable-event-broker", daemon=True)
        self.thread.start()

    def publish(self, event: dict, recipients: set[str]) -> dict:
        if not recipients:
            return event
        durable_candidate = {
            **event,
            "event_id": str(event.get("event_id") or uuid.uuid4().hex),
            "occurred_at": event.get("occurred_at") or utc_now_iso(),
        }
        try:
            published = self.repository.publish_event(durable_candidate, recipients, self.instance_id)
            SSE_METRICS.increment("events_published_total")
            self.wake_event.set()
            return published
        except Exception:
            SSE_METRICS.increment("event_publish_failures_total")
            with self.lock:
                self.outbox.append((durable_candidate, set(recipients)))
            self.deliver(durable_candidate, recipients)
            self.wake_event.set()
            return durable_candidate

    def replay(self, username: str, after_sequence: int, *, limit: int = 500) -> list[dict]:
        events = self.repository.events_for_user_after(username, after_sequence, limit=limit)
        if events:
            SSE_METRICS.increment("events_replayed_total", len(events))
        return events

    def _retry_outbox(self) -> None:
        with self.lock:
            pending = list(self.outbox)
            self.outbox.clear()
        for index, (event, recipients) in enumerate(pending):
            try:
                self.repository.publish_event(event, recipients, self.instance_id)
                SSE_METRICS.increment("events_published_total")
            except Exception:
                SSE_METRICS.increment("event_publish_failures_total")
                with self.lock:
                    for item in pending[index:]:
                        self.outbox.append(item)
                return

    @staticmethod
    def _latency_ms(event: dict) -> float:
        try:
            occurred_at = datetime.fromisoformat(str(event.get("occurred_at", "")).replace("Z", "+00:00"))
            return (utc_now() - occurred_at).total_seconds() * 1000
        except (TypeError, ValueError):
            return 0.0

    def _consume(self) -> None:
        while True:
            events = self.repository.list_events_after(self.cursor, limit=500)
            if not events:
                return
            for event, recipients in events:
                revision = int(event.get("revision", 0))
                if event.get("origin_instance_id") != self.instance_id:
                    self.state_refresh()
                self.deliver(event, recipients)
                self.cursor = max(self.cursor, revision)
                SSE_METRICS.increment("events_consumed_total")
                SSE_METRICS.record_delivery_latency(self._latency_ms(event))
            if len(events) < 500:
                return

    def _cleanup_presence(self) -> None:
        if time.monotonic() < self.next_presence_cleanup:
            return
        self.next_presence_cleanup = time.monotonic() + min(5, max(1, PRESENCE_TTL_SECONDS // 3))
        cleanup_expired_uploads()
        for username, presence in self.repository.cleanup_expired_presence():
            self.publish(
                {"type": "presence_updated", "username": username, "presence": presence},
                self.presence_recipients(username),
            )

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._retry_outbox()
                self._consume()
                self._cleanup_presence()
            except Exception:
                SSE_METRICS.increment("event_consume_failures_total")
            self.wake_event.wait(EVENT_POLL_INTERVAL_SECONDS)
            self.wake_event.clear()

    def close(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        self.thread.join(timeout=2)


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
PRESENCE = PresenceStore(STORE.repository, INSTANCE_ID)
UPLOAD_GRANTS = UploadGrantStore()
SHORTS_COLLECTOR = ShortsCatalogCollector(STORE.repository, INSTANCE_ID, start=False)
EVENT_BROKER = DurableEventBroker(
    STORE.repository,
    INSTANCE_ID,
    STORE.presence_event_recipients,
    STORE.refresh_from_repository,
)
PHONE_VERIFICATIONS = PhoneVerificationStore()
OAUTH_STATES = OAuthStateStore()
RATE_LIMITER = SlidingWindowRateLimiter()


class CommandFailure(Exception):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class CommandOutcome:
    def __init__(
        self,
        data: object,
        status: HTTPStatus = HTTPStatus.OK,
        events: list[tuple[dict, set[str]]] | None = None,
    ) -> None:
        self.data = data
        self.status = status
        self.events = events or []


class ApplicationServices:
    """Feature commands return data and domain events without writing HTTP responses."""

    def __init__(self, store: StateStore, presence: PresenceStore) -> None:
        self.store = store
        self.presence = presence

    def add_friend(self, user: dict, payload: dict) -> CommandOutcome:
        friend_code = str(payload.get("friendCode", "")).strip()
        friend, error = self.store.add_friend_by_code(user["username"], friend_code)
        if error or friend is None:
            raise CommandFailure(error or "친구를 추가하지 못했습니다.")
        return CommandOutcome(
            {"friend": friend},
            HTTPStatus.CREATED,
            [({"type": "friends_updated"}, {user["username"], friend["username"]})],
        )

    def create_direct_room(self, user: dict, payload: dict) -> CommandOutcome:
        friend_user_id = str(payload.get("userId", "")).strip()
        room, created, error = self.store.create_or_get_direct_room(user["username"], friend_user_id)
        if error or room is None:
            raise CommandFailure(error or "채팅방을 만들지 못했습니다.")
        events = []
        if created:
            events = [
                ({"type": "room_created", "room": summary}, {recipient})
                for recipient, summary in self.store.room_event_summaries(room["id"]).items()
            ]
        return CommandOutcome(
            {"room": room, "created": created},
            HTTPStatus.CREATED if created else HTTPStatus.OK,
            events,
        )

    def create_group_room(self, user: dict, payload: dict) -> CommandOutcome:
        name = str(payload.get("name", "")).strip()
        raw_member_user_ids = payload.get("memberUserIds")
        if not isinstance(raw_member_user_ids, list) or len(raw_member_user_ids) > MAX_GROUP_PARTICIPANTS - 1:
            raise CommandFailure("올바른 그룹 멤버를 선택해 주세요.")
        member_user_ids: list[str] = []
        for member_user_id in raw_member_user_ids:
            if not isinstance(member_user_id, str) or not USER_ID_PATTERN.fullmatch(member_user_id):
                raise CommandFailure("올바른 그룹 멤버를 선택해 주세요.")
            member_user_ids.append(member_user_id)
        room, error = self.store.create_group_room(user["username"], name, member_user_ids)
        if error or room is None:
            raise CommandFailure(error or "그룹 채팅방을 만들지 못했습니다.")
        events = [
            ({"type": "room_created", "room": summary}, {recipient})
            for recipient, summary in self.store.room_event_summaries(room["id"]).items()
        ]
        return CommandOutcome({"room": room}, HTTPStatus.CREATED, events)

    def update_group_room(self, user: dict, payload: dict) -> CommandOutcome:
        room_id = str(payload.get("roomId", "")).strip()
        name = str(payload.get("name", "")).strip()
        if not ROOM_ID_PATTERN.fullmatch(room_id) or not 1 <= len(name) <= 32:
            raise CommandFailure("채팅방 이름은 1~32자로 입력해 주세요.")
        room, error = self.store.update_group_room_name(user["username"], room_id, name)
        if error == "not_found":
            raise CommandFailure("채팅방을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
        if error == "forbidden":
            raise CommandFailure("방장만 채팅방 정보를 변경할 수 있습니다.", HTTPStatus.FORBIDDEN)
        if error or room is None:
            raise CommandFailure("채팅방 이름을 변경하지 못했습니다.")
        event = {"type": "room_updated", "roomId": room_id, "room": room}
        return CommandOutcome({"room": room}, events=[(event, self.store.room_event_recipients(room_id))])

    def leave_group_room(self, user: dict, payload: dict) -> CommandOutcome:
        room_id = str(payload.get("roomId", "")).strip()
        if not ROOM_ID_PATTERN.fullmatch(room_id):
            raise CommandFailure("올바른 채팅방을 선택해 주세요.")
        room, recipients, error = self.store.leave_group_room(user["username"], room_id)
        if error:
            raise CommandFailure("채팅방을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
        event = {
            "type": "room_left",
            "roomId": room_id,
            "username": user["username"],
            "room": room,
        }
        return CommandOutcome({"left": True, "roomId": room_id}, events=[(event, recipients)])

    def create_message(
        self,
        user: dict,
        payload: dict,
        attachment_resolver,
    ) -> CommandOutcome:
        room_id = str(payload.get("roomId", "")).strip()
        text = str(payload.get("text", "")).strip()
        client_message_id = str(payload.get("clientMessageId", "")).strip()
        if client_message_id and not CLIENT_MESSAGE_ID_PATTERN.fullmatch(client_message_id):
            raise CommandFailure("올바른 메시지 식별자가 아닙니다.")
        attachment = attachment_resolver(payload.get("attachment"), user["username"])
        if not room_id or (not text and attachment is None):
            raise CommandFailure("roomId와 text는 필수입니다.")
        try:
            result = self.store.add_message(room_id, user["username"], text, attachment, client_message_id)
        except ValueError as error:
            raise CommandFailure(
                "같은 메시지 식별자를 다른 내용에 다시 사용할 수 없습니다.",
                HTTPStatus.CONFLICT,
            ) from error
        if result is None:
            raise CommandFailure("채팅방을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
        message, room, created = result
        if created:
            visible_message = self.store.initial_message_read_state(
                room_id,
                user["username"],
                message,
            )
        else:
            visible_message = next(
                (
                    candidate
                    for candidate in reversed(self.store.get_messages(room_id, user["username"]) or [])
                    if candidate.get("id") == message.get("id")
                ),
                message,
            )
        if attachment is not None and created:
            UPLOAD_GRANTS.consume(Path(attachment["url"]).name, user["username"])
        if not created:
            return CommandOutcome(visible_message)
        sender_public = self.store.get_user_public(message["username"]) or {}
        event = {
            "type": "message_created",
            "roomId": room_id,
            "room": room,
            "message": message,
            "sender": {
                key: sender_public[key]
                for key in (
                    "id", "username", "display_name", "status_message",
                    "profile_image_url", "profile_thumbnail_url", "profile_art_version",
                )
                if key in sender_public
            },
        }
        return CommandOutcome(
            visible_message,
            HTTPStatus.CREATED,
            [(event, self.store.room_event_recipients(room_id))],
        )

    def delete_message(self, user: dict, payload: dict) -> CommandOutcome:
        room_id = str(payload.get("roomId", "")).strip()
        message_id = str(payload.get("messageId", "")).strip()
        if not ROOM_ID_PATTERN.fullmatch(room_id) or not MESSAGE_ID_PATTERN.fullmatch(message_id):
            raise CommandFailure("올바른 메시지를 선택해 주세요.")
        message, room, error = self.store.delete_message(room_id, user["username"], message_id)
        if error == "forbidden":
            raise CommandFailure("본인이 보낸 메시지만 지울 수 있습니다.", HTTPStatus.FORBIDDEN)
        if error or message is None or room is None:
            raise CommandFailure("메시지를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
        event = {
            "type": "message_deleted",
            "roomId": room_id,
            "messageId": message_id,
            "room": room,
        }
        return CommandOutcome(
            {"deleted": True, "roomId": room_id, "messageId": message_id, "room": room},
            events=[(event, self.store.room_event_recipients(room_id))],
        )

    def mark_room_read(self, user: dict, payload: dict) -> CommandOutcome:
        room_id = str(payload.get("roomId", "")).strip()
        room, changed = self.store.mark_room_read(room_id, user["username"])
        if room is None:
            raise CommandFailure("채팅방을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
        events = []
        if changed:
            event = {
                "type": "room_read",
                "roomId": room_id,
                "username": user["username"],
                "roomKind": room.get("kind", "direct"),
            }
            events.append((event, self.store.room_event_recipients(room_id)))
        return CommandOutcome({"room": room}, events=events)

    def update_presence(self, session_token: str | None, user: dict, payload: dict) -> CommandOutcome:
        active_room_id = str(payload.get("activeRoomId", "")).strip()[:80]
        emoji = saved_activity_emoji(payload.get("emoji"))
        changed = self.presence.update(session_token, user["username"], active_room_id, emoji)
        current = self.presence.for_user(user["username"])
        events = []
        if changed:
            event = {"type": "presence_updated", "username": user["username"], "presence": current}
            events.append((event, self.store.presence_event_recipients(user["username"])))
        return CommandOutcome({"presence": current}, events=events)


APPLICATION = ApplicationServices(STORE, PRESENCE)


class ChatHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    rbufsize = 0

    def handle_one_request(self) -> None:
        self._request_started_at = time.perf_counter()
        self._request_id = secrets.token_hex(12)
        self._response_status = 0
        self._request_bytes = 0
        self._response_bytes = 0
        self._safe_user_id = ""
        try:
            super().handle_one_request()
        finally:
            if getattr(self, "raw_requestline", b""):
                self._record_completed_request()

    def _record_completed_request(self) -> None:
        latency_ms = (time.perf_counter() - self._request_started_at) * 1000
        status = self._response_status or 499
        method = getattr(self, "command", "UNKNOWN")
        route = normalized_request_route(method, getattr(self, "path", "/"))
        self.server.request_metrics.record(
            route,
            status,
            latency_ms,
            self._request_bytes,
            self._response_bytes,
        )
        write_structured_log({
            "timestamp": utc_now_iso(),
            "level": "error" if status >= 500 else "info",
            "event": "http_request",
            "request_id": self._request_id,
            "method": method,
            "route": route.split(" ", 1)[1] if " " in route else route,
            "status": status,
            "latency_ms": round(latency_ms, 3),
            "request_bytes": self._request_bytes,
            "response_bytes": self._response_bytes,
            "user_id": self._safe_user_id,
        })

    def parse_request(self) -> bool:
        parsed = super().parse_request()
        if not parsed:
            return False
        supplied_request_id = self.headers.get("X-Request-ID", "").strip()
        if REQUEST_ID_PATTERN.fullmatch(supplied_request_id):
            self._request_id = supplied_request_id
        try:
            self._request_bytes = max(0, int(self.headers.get("Content-Length", "0")))
        except ValueError:
            self._request_bytes = 0
        return True

    def send_response(self, code: int, message: str | None = None) -> None:
        self._response_status = int(code)
        super().send_response(code, message)

    def send_header(self, keyword: str, value: str) -> None:
        if keyword.lower() == "content-length":
            try:
                self._response_bytes = max(0, int(value))
            except ValueError:
                pass
        super().send_header(keyword, value)

    def handle(self) -> None:
        self.close_connection = True
        while self.wait_for_complete_headers():
            self.close_connection = False
            self.handle_one_request()
            if self.close_connection:
                return

    def wait_for_complete_headers(self) -> bool:
        deadline = time.monotonic() + HEADER_READ_TIMEOUT_SECONDS
        try:
            while True:
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    raise TimeoutError("request headers deadline exceeded")
                self.connection.settimeout(remaining_seconds)
                pending = self.connection.recv(65537, socket.MSG_PEEK)
                if not pending:
                    return False
                if b"\r\n\r\n" in pending or b"\n\n" in pending or len(pending) > 65536:
                    return True
        except (TimeoutError, socket.timeout):
            self.server.request_metrics.increment("header_timeouts_total")
            self.close_connection = True
            return False
        except (ConnectionError, OSError):
            self.close_connection = True
            return False

    def end_headers(self) -> None:
        self.send_header("X-Request-ID", getattr(self, "_request_id", secrets.token_hex(12)))
        for name, value in COMMON_SECURITY_HEADERS:
            self.send_header(name, value)
        try:
            if self.request_scheme() == "https":
                self.send_header("Strict-Transport-Security", "max-age=31536000")
        except (AttributeError, TypeError):
            pass
        super().end_headers()

    def request_scheme(self) -> str:
        request_hostname = (urlparse(f"//{self.request_host()}").hostname or "").lower()
        if request_hostname in {"localhost", "127.0.0.1", "::1"}:
            return "http"
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

    def entity_page_query(self, query: dict[str, list[str]]) -> tuple[int, str] | None:
        cursor = str(query.get("cursor", [""])[0] or "")
        try:
            limit = int(query.get("limit", [str(DEFAULT_ENTITY_PAGE_SIZE)])[0])
        except (TypeError, ValueError):
            self.send_json({"error": "올바른 페이지 크기를 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
            return None
        if not 1 <= limit <= MAX_ENTITY_PAGE_SIZE:
            self.send_json({"error": "페이지 크기는 1~100이어야 합니다."}, HTTPStatus.BAD_REQUEST)
            return None
        return limit, cursor

    def complete_command(self, outcome: CommandOutcome) -> None:
        try:
            self.send_json(outcome.data, outcome.status)
            self.wfile.flush()
        finally:
            for event, recipients in outcome.events:
                EVENT_BROKER.publish(event, recipients)

    def run_json_command(self, command) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        try:
            outcome = command(payload)
        except CommandFailure as error:
            self.send_json({"error": error.message}, error.status)
            return
        except ConcurrentUpdateError:
            STORE.refresh_from_repository()
            self.send_json(
                {"error": "다른 서버에서 상태가 변경되었습니다. 최신 상태로 다시 시도해 주세요."},
                HTTPStatus.CONFLICT,
            )
            return
        self.complete_command(outcome)

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        if path == "/":
            self.serve_index()
            return
        if path == "/signup":
            self.serve_signup_page()
            return
        if path.startswith("/assets/"):
            self.serve_asset(path, query)
            return
        profile_art_match = PROFILE_ART_THUMBNAIL_PATH_PATTERN.fullmatch(path)
        if profile_art_match:
            if self.require_auth_record() is None:
                return
            self.serve_profile_art_thumbnail(profile_art_match.group(1))
            return
        if path.startswith("/uploads/"):
            user = self.require_auth_record()
            if user is None:
                return
            self.serve_upload(path, user)
            return
        if path in {"/health", "/live"}:
            self.send_json({"ok": True, "status": "live", "app_name": APP_NAME}, HTTPStatus.OK)
            return
        if path == "/ready":
            readiness = self.readiness_report()
            self.send_json(readiness, HTTPStatus.OK if readiness["ready"] else HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if path == "/metrics":
            with SUBSCRIBERS_LOCK:
                subscriber_count = len(SUBSCRIBERS)
                max_subscriber_queue_fill = max(
                    (subscriber.qsize() / subscriber.maxsize for subscriber in SUBSCRIBERS),
                    default=0,
                )
            with EVENT_BROKER.lock:
                outbox_pending = len(EVENT_BROKER.outbox)
            with STORE.lock:
                persistence_lag = max(0, STORE._revision - STORE._persisted_revision)
                persistence_pending_parts = len(STORE._pending_parts)
                persistence_error = type(STORE._persist_error).__name__ if STORE._persist_error is not None else ""
            self.send_json(
                {
                    "sse": SSE_METRICS.snapshot(),
                    "requests": self.server.request_metrics.snapshot(),
                    "shorts_catalog": SHORTS_COLLECTOR.snapshot(),
                    "runtime": {
                        "active_threads": threading.active_count(),
                        "rss_bytes": process_rss_bytes(),
                        "process_cpu_seconds": round(time.process_time(), 3),
                        "open_file_descriptors": process_open_file_descriptors(),
                        "subscribers": subscriber_count,
                        "max_subscriber_queue_fill_ratio": round(max_subscriber_queue_fill, 6),
                        "event_cursor": EVENT_BROKER.cursor,
                        "event_outbox_pending": outbox_pending,
                    },
                    "persistence": {
                        "revision_lag": persistence_lag,
                        "pending_parts": persistence_pending_parts,
                        "error": persistence_error,
                    },
                    "readiness": self.server.readiness_snapshot(),
                    "limits": {
                        "max_sse_connections": MAX_SSE_CONNECTIONS,
                        "max_sse_queue_size": MAX_SSE_QUEUE_SIZE,
                        "heartbeat_seconds": SSE_HEARTBEAT_SECONDS,
                        "event_poll_interval_seconds": EVENT_POLL_INTERVAL_SECONDS,
                        "presence_ttl_seconds": PRESENCE_TTL_SECONDS,
                        "max_request_threads": self.server.max_request_threads,
                        "max_body_readers": self.server.max_body_readers,
                        "header_read_timeout_seconds": HEADER_READ_TIMEOUT_SECONDS,
                        "body_read_timeout_seconds": BODY_READ_TIMEOUT_SECONDS,
                        "upload_read_timeout_seconds": UPLOAD_READ_TIMEOUT_SECONDS,
                    },
                },
                HTTPStatus.OK,
            )
            return
        if path == "/session":
            self.serve_session()
            return
        if path == "/profile/pixels":
            user = self.require_auth_record()
            if user is None:
                return
            profile_art = STORE.get_profile_pixels(user["username"])
            if profile_art is None:
                self.send_json({"error": "사용자를 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
                return
            self.send_json(profile_art, HTTPStatus.OK, headers={"Cache-Control": "no-store"})
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
        if path == "/me":
            user = self.require_auth_record()
            if user is None:
                return
            self.send_conditional_json(STORE.get_me_summary(user))
            return
        if path == "/friends":
            user = self.require_auth_record()
            if user is None:
                return
            page_query = self.entity_page_query(query)
            if page_query is None:
                return
            try:
                payload = STORE.get_friends_page(user, limit=page_query[0], cursor=page_query[1])
            except ValueError:
                self.send_json({"error": "올바른 친구 목록 커서를 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
                return
            self.send_conditional_json(payload)
            return
        if path == "/rooms":
            user = self.require_auth_record()
            if user is None:
                return
            page_query = self.entity_page_query(query)
            if page_query is None:
                return
            updated_since = str(query.get("updated_since", [""])[0] or "")
            if updated_since:
                try:
                    datetime.fromisoformat(updated_since.replace("Z", "+00:00"))
                except ValueError:
                    self.send_json({"error": "올바른 updated_since 값을 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
                    return
            try:
                payload = STORE.get_rooms_page(
                    user,
                    limit=page_query[0],
                    cursor=page_query[1],
                    updated_since=updated_since,
                )
            except ValueError:
                self.send_json({"error": "올바른 채팅방 목록 커서를 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
                return
            self.send_conditional_json(payload)
            return
        members_match = ROOM_MEMBERS_PATH_PATTERN.fullmatch(path)
        if members_match:
            user = self.require_auth_record()
            if user is None:
                return
            page_query = self.entity_page_query(query)
            if page_query is None:
                return
            try:
                payload = STORE.get_room_members_page(
                    members_match.group(1), user, limit=page_query[0], cursor=page_query[1]
                )
            except ValueError:
                self.send_json({"error": "올바른 참여자 목록 커서를 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
                return
            if payload is None:
                self.send_json({"error": "채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
                return
            self.send_conditional_json(payload)
            return
        if path == "/sync":
            user = self.require_auth_record()
            if user is None:
                return
            try:
                after_revision = int(query.get("after_revision", ["0"])[0])
                limit = int(query.get("limit", [str(MAX_SYNC_EVENTS)])[0])
            except (TypeError, ValueError):
                self.send_json({"error": "올바른 동기화 revision을 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
                return
            if after_revision < 0 or not 1 <= limit <= MAX_SYNC_EVENTS:
                self.send_json({"error": "올바른 동기화 범위를 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
                return
            self.send_conditional_json(STORE.get_sync_page(user["username"], after_revision=after_revision, limit=limit))
            return
        if path == "/messages/search":
            user = self.require_auth()
            if user is None:
                return
            self.serve_message_search(query, user)
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
            self.serve_events(user, query)
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

    def do_HEAD(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/uploads/"):
            user = self.require_auth_record()
            if user is not None:
                self.serve_upload(path, user, head_only=True)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_PUT(self) -> None:
        self._request_body_consumed = False
        try:
            path = urlparse(self.path).path
            if not path.startswith("/uploads/"):
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            user = self.require_auth()
            if user is None:
                self.close_connection = True
                return
            self.receive_attachment_upload(path, user)
        finally:
            self.discard_unread_request_body()

    def do_POST(self) -> None:
        self._request_body_consumed = False
        try:
            try:
                self.dispatch_post()
            except (ConcurrentUpdateError, sqlite3.IntegrityError):
                STORE.refresh_from_repository()
                self.send_json(
                    {"error": "다른 서버에서 상태가 변경되었습니다. 최신 상태로 다시 시도해 주세요."},
                    HTTPStatus.CONFLICT,
                )
        finally:
            self.discard_unread_request_body()

    def dispatch_post(self) -> None:
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
        if path == "/messages/delete":
            user = self.require_auth()
            if user is None:
                return
            self.delete_message(user)
            return
        if path == "/uploads":
            user = self.require_auth()
            if user is None:
                return
            self.upload_attachment(user)
            return
        if path == "/uploads/grant":
            user = self.require_auth()
            if user is None:
                return
            self.grant_attachment_upload(user)
            return
        if path == "/uploads/complete":
            user = self.require_auth()
            if user is None:
                return
            self.complete_attachment_upload(user)
            return
        if path == "/uploads/discard":
            user = self.require_auth()
            if user is None:
                return
            self.discard_attachment_upload(user)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def readiness_report(self) -> dict:
        checked_at = utc_now_iso()
        database_started = time.perf_counter()
        database_error = ""
        migration_ready = False
        try:
            migration_ready = bool(STORE.repository.is_legacy_imported())
        except (ConnectionError, OSError, sqlite3.Error, ValueError) as error:
            database_error = type(error).__name__
        database_latency_ms = (time.perf_counter() - database_started) * 1000
        with STORE.lock:
            persistence_lag = max(0, STORE._revision - STORE._persisted_revision)
            pending_parts = len(STORE._pending_parts)
            persistence_error = type(STORE._persist_error).__name__ if STORE._persist_error is not None else ""
        with EVENT_BROKER.lock:
            event_outbox_pending = len(EVENT_BROKER.outbox)
        request_metrics = self.server.request_metrics.snapshot()
        checks = {
            "database": not database_error and database_latency_ms < 2_000,
            "migration": migration_ready,
            "persistence_queue": not persistence_error and persistence_lag <= 100 and pending_parts <= 1_000,
            "event_outbox": event_outbox_pending < 8_000,
            "request_capacity": request_metrics["active"] < self.server.max_request_threads,
            "body_reader_capacity": request_metrics["active_body_readers"] < self.server.max_body_readers,
        }
        report = {
            "ready": all(checks.values()),
            "status": "ready" if all(checks.values()) else "not_ready",
            "checked_at": checked_at,
            "checks": checks,
            "database": {
                "backend": "supabase" if SUPABASE_ENABLED else "sqlite",
                "latency_ms": round(database_latency_ms, 3),
                "error": database_error,
            },
            "persistence": {
                "revision_lag": persistence_lag,
                "pending_parts": pending_parts,
                "error": persistence_error,
            },
            "event_outbox_pending": event_outbox_pending,
        }
        self.server.set_readiness_snapshot(report)
        return report

    def serve_index(self) -> None:
        self.serve_html(INDEX_CONTENT, INDEX_GZIP_CONTENT, INDEX_BROTLI_CONTENT, INDEX_ETAG)

    def serve_signup_page(self) -> None:
        self.serve_html(SIGNUP_CONTENT, SIGNUP_GZIP_CONTENT, SIGNUP_BROTLI_CONTENT, SIGNUP_ETAG)

    def serve_html(
        self,
        content: bytes,
        gzip_content: bytes,
        brotli_content: bytes | None,
        etag: str,
    ) -> None:
        cache_control = "no-cache, max-age=0"
        if self.headers.get("If-None-Match", "") == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("Cache-Control", cache_control)
            self.send_header("ETag", etag)
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return
        accepted = self.headers.get("Accept-Encoding", "")
        content_encoding = ""
        response_content = content
        if brotli_content is not None and accepts_content_encoding(accepted, "br"):
            response_content = brotli_content
            content_encoding = "br"
        elif accepts_content_encoding(accepted, "gzip"):
            response_content = gzip_content
            content_encoding = "gzip"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(response_content)))
        self.send_header("Vary", "Accept-Encoding")
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
        self.send_header("Cache-Control", cache_control)
        self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(response_content)

    def serve_asset(self, request_path: str, query: dict[str, list[str]]) -> None:
        relative_path = unquote(request_path.removeprefix("/assets/"))
        asset_path = (ASSETS_DIR / relative_path).resolve()

        try:
            asset_path.relative_to(ASSETS_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        source_content = ASSET_CONTENT.get(asset_path)
        if source_content is None:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        content_type = {
            ".ttf": "font/ttf",
            ".otf": "font/otf",
            ".woff2": "font/woff2",
        }.get(asset_path.suffix.lower(), mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream")
        fingerprint = ASSET_FINGERPRINTS[asset_path]
        supplied_fingerprint = query.get("v", [""])[0]
        immutable = bool(supplied_fingerprint) and hmac.compare_digest(supplied_fingerprint, fingerprint)
        cache_control = (
            "public, max-age=31536000, immutable"
            if immutable
            else "public, no-cache, max-age=0"
        )
        etag = f'"{fingerprint}"'
        if self.headers.get("If-None-Match", "") == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("Cache-Control", cache_control)
            self.send_header("ETag", etag)
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return
        accepted = self.headers.get("Accept-Encoding", "")
        content_encoding = ""
        response_content = source_content
        if accepts_content_encoding(accepted, "br") and asset_path in ASSET_BROTLI_CONTENT:
            response_content = ASSET_BROTLI_CONTENT[asset_path]
            content_encoding = "br"
        elif accepts_content_encoding(accepted, "gzip") and asset_path in ASSET_GZIP_CONTENT:
            response_content = ASSET_GZIP_CONTENT[asset_path]
            content_encoding = "gzip"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(response_content)))
        self.send_header("Vary", "Accept-Encoding")
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
        self.send_header("Cache-Control", cache_control)
        self.send_header("ETag", etag)
        self.end_headers()
        try:
            self.wfile.write(response_content)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def serve_upload(self, request_path: str, user: dict, *, head_only: bool = False) -> None:
        filename = Path(unquote(request_path.removeprefix("/uploads/"))).name
        if not filename or Path(filename).suffix.lower() not in ATTACHMENT_TYPES.values():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        if not STORE.can_access_attachment(filename, user["username"]):
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        content_type = mimetypes.guess_type(filename)[0] or next(
            (mime_type for mime_type, extension in ATTACHMENT_TYPES.items() if extension == Path(filename).suffix.lower()),
            "application/octet-stream",
        )

        if SUPABASE_ENABLED:
            try:
                signed_url = supabase_signed_download_url(filename)
            except (ConnectionError, ValueError):
                self.send_error(HTTPStatus.BAD_GATEWAY, "Unable to authorize download")
                return
            self.send_response(HTTPStatus.TEMPORARY_REDIRECT)
            self.send_header("Location", signed_url)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "private, no-store")
            self.end_headers()
            return

        try:
            upload_path = (UPLOADS_DIR / filename).resolve()
            upload_path.relative_to(UPLOADS_DIR.resolve())
            file_size = upload_path.stat().st_size
            start, end = self.local_byte_range(file_size)
            if start is None or end is None:
                return
            partial = bool(self.headers.get("Range", "").strip())
            self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            self.send_header("Content-Security-Policy", "sandbox")
            self.end_headers()
            if head_only:
                return
            with upload_path.open("rb") as upload_file:
                upload_file.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = upload_file.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (FileNotFoundError, ValueError):
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def local_byte_range(self, file_size: int) -> tuple[int | None, int | None]:
        range_header = self.headers.get("Range", "").strip()
        if not range_header:
            return 0, file_size - 1
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        if match is None or (not match.group(1) and not match.group(2)):
            self.send_range_not_satisfiable(file_size)
            return None, None
        if match.group(1):
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
        else:
            suffix_size = int(match.group(2))
            if suffix_size <= 0:
                self.send_range_not_satisfiable(file_size)
                return None, None
            start = max(0, file_size - suffix_size)
            end = file_size - 1
        if file_size <= 0 or start >= file_size or end < start:
            self.send_range_not_satisfiable(file_size)
            return None, None
        return start, min(end, file_size - 1)

    def send_range_not_satisfiable(self, file_size: int) -> None:
        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
        self.send_header("Content-Range", f"bytes */{file_size}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def serve_session(self) -> None:
        user = self.current_user()
        if user is None:
            self.send_json({"authenticated": False}, HTTPStatus.OK)
            return
        self.send_json({"authenticated": True, "user": user}, HTTPStatus.OK)

    def serve_profile_art_thumbnail(self, user_id: str) -> None:
        thumbnail = STORE.get_profile_art_thumbnail(user_id)
        if thumbnail is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        version, content = thumbnail
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("ETag", f'"profile-art-{user_id}-{version}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def serve_messages(self, query: dict[str, list[str]]) -> None:
        room_id = query.get("room_id", [""])[0]
        user = self.current_user()
        limit_value = query.get("limit", [""])[0].strip()
        before = query.get("before", [""])[0].strip()
        around = query.get("around", [""])[0].strip()
        if limit_value or before or around:
            try:
                limit = int(limit_value or DEFAULT_MESSAGES_PAGE_SIZE)
            except ValueError:
                self.send_json({"error": "올바른 메시지 개수를 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
                return
            if (
                not 1 <= limit <= MAX_MESSAGES_PAGE_SIZE
                or (before and not MESSAGE_ID_PATTERN.fullmatch(before))
                or (around and not MESSAGE_ID_PATTERN.fullmatch(around))
                or (before and around)
            ):
                self.send_json({"error": "올바른 메시지 커서를 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
                return
            messages = STORE.get_messages_page(
                room_id,
                user["username"],
                limit=limit,
                before=before,
                around=around,
            ) if user else None
        else:
            messages = STORE.get_messages(room_id, user["username"]) if user else None
        if messages is None:
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return
        self.send_json(messages, HTTPStatus.OK)

    def serve_message_search(self, query: dict[str, list[str]], user: dict) -> None:
        search_query = query.get("q", [""])[0].strip()
        try:
            limit = int(query.get("limit", ["50"])[0])
        except (TypeError, ValueError):
            self.send_json({"error": "올바른 검색 개수를 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
            return
        if not search_query or len(search_query) > 100 or not 1 <= limit <= 50:
            self.send_json({"error": "검색어는 1~100자로 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
            return
        results = STORE.search_messages(user["username"], search_query, limit=limit)
        if results is None:
            self.send_json({"error": "사용자를 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return
        self.send_json(results, HTTPStatus.OK)

    def serve_events(self, user: dict, query: dict[str, list[str]] | None = None) -> None:
        if not SSE_CONNECTION_SLOTS.acquire(blocking=False):
            SSE_METRICS.increment("rejected_total")
            self.send_json({"error": "실시간 연결이 혼잡합니다. 잠시 후 다시 시도해 주세요."}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            SSE_CONNECTION_SLOTS.release()
            return

        subscriber: queue.Queue = queue.Queue(maxsize=MAX_SSE_QUEUE_SIZE)
        token = self.read_session_token()
        with SUBSCRIBERS_LOCK:
            SUBSCRIBERS[subscriber] = user["username"]
            SUBSCRIBERS_BY_USERNAME.setdefault(user["username"], set()).add(subscriber)
        SSE_METRICS.increment("active")
        SSE_METRICS.increment("accepted_total")

        after_value = str((query or {}).get("after", [""])[0])
        header_value = str(self.headers.get("Last-Event-ID", "") or "")
        try:
            last_sent_revision = max(0, int(header_value or after_value or "0"))
        except ValueError:
            last_sent_revision = 0
        if last_sent_revision:
            SSE_METRICS.increment("reconnects_total")

        presence_connected = False
        try:
            presence_changed = PRESENCE.connect(token, user["username"])
            presence_connected = True
        except Exception:
            presence_changed = False
            SSE_METRICS.increment("event_consume_failures_total")
        if presence_changed:
            EVENT_BROKER.publish(
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

            if last_sent_revision:
                for event in EVENT_BROKER.replay(user["username"], last_sent_revision):
                    revision = int(event.get("revision", 0))
                    if revision <= last_sent_revision:
                        continue
                    payload = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"id: {revision}\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_sent_revision = revision

            while True:
                if SESSIONS.get_username(token) != user["username"]:
                    break
                try:
                    event = subscriber.get(timeout=SSE_HEARTBEAT_SECONDS)
                except queue.Empty:
                    try:
                        PRESENCE.heartbeat(token)
                    except Exception:
                        SSE_METRICS.increment("event_consume_failures_total")
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    SSE_METRICS.increment("heartbeats_total")
                    continue
                if event is None:
                    break
                revision = int(event.get("revision", 0))
                if revision and revision <= last_sent_revision:
                    continue
                payload = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
                if revision:
                    payload = f"id: {revision}\n".encode("utf-8") + payload
                self.wfile.write(payload)
                self.wfile.flush()
                last_sent_revision = max(last_sent_revision, revision)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self.close_connection = True
            with SUBSCRIBERS_LOCK:
                SUBSCRIBERS.pop(subscriber, None)
                username_subscribers = SUBSCRIBERS_BY_USERNAME.get(user["username"])
                if username_subscribers is not None:
                    username_subscribers.discard(subscriber)
                    if not username_subscribers:
                        SUBSCRIBERS_BY_USERNAME.pop(user["username"], None)
            username, went_offline = "", False
            if presence_connected:
                try:
                    username, went_offline = PRESENCE.disconnect(token)
                except Exception:
                    SSE_METRICS.increment("event_consume_failures_total")
            if went_offline:
                EVENT_BROKER.publish(
                    {"type": "presence_updated", "username": username, "presence": PRESENCE.for_user(username)},
                    STORE.presence_event_recipients(username),
                )
            SSE_METRICS.increment("active", -1)
            SSE_METRICS.increment("disconnected_total")
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
        self.send_header("Content-Length", "0")
        self.end_headers()

    def consume_bound_oauth_state(self, state: str) -> bool:
        if not state:
            return False
        cookie_state = self.read_cookie_value(OAUTH_STATE_COOKIE_NAME)
        if not cookie_state or not hmac.compare_digest(state, cookie_state):
            return False
        return OAUTH_STATES.consume(state)

    def redirect_after_oauth(self, location: str, session_token: str = "") -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        if session_token:
            self.send_header(
                "Set-Cookie",
                make_cookie_header(session_token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure()),
            )
        self.send_header("Set-Cookie", clear_oauth_state_cookie(secure=self.cookie_secure()))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def finish_google_login(self, query: dict[str, list[str]]) -> None:
        state = query.get("state", [""])[0].strip()
        state_is_valid = self.consume_bound_oauth_state(state)
        if "error" in query:
            self.redirect_after_oauth("/?auth_error=google_access_denied" if state_is_valid else "/?auth_error=oauth_state_invalid")
            return

        code = query.get("code", [""])[0].strip()
        if not code or not state_is_valid:
            self.redirect_after_oauth("/?auth_error=oauth_state_invalid")
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
            self.redirect_after_oauth("/?auth_error=google_login_failed")
            return

        token = SESSIONS.create(user["username"])
        self.redirect_after_oauth("/", token)

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
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", f"https://kauth.kakao.com/oauth/authorize?{urlencode(params)}")
        self.send_header("Set-Cookie", make_oauth_state_cookie(state, secure=self.cookie_secure()))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def finish_kakao_login(self, query: dict[str, list[str]]) -> None:
        state = query.get("state", [""])[0].strip()
        state_is_valid = self.consume_bound_oauth_state(state)
        if "error" in query:
            self.redirect_after_oauth("/?auth_error=kakao_access_denied" if state_is_valid else "/?auth_error=oauth_state_invalid")
            return

        code = query.get("code", [""])[0].strip()
        if not code or not state_is_valid:
            self.redirect_after_oauth("/?auth_error=oauth_state_invalid")
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
            self.redirect_after_oauth("/?auth_error=kakao_login_failed")
            return

        token = SESSIONS.create(user["username"])
        self.redirect_after_oauth("/", token)

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
        requested_cursor = query.get("cursor", [""])[0].strip()
        refresh = query.get("refresh", [""])[0] == "1"
        if requested_cursor and not re.fullmatch(r"catalog:\d{1,9}", requested_cursor):
            self.send_json({"error": "잘못된 쇼츠 페이지 요청이에요."}, HTTPStatus.BAD_REQUEST)
            return

        with SHORTS_FEED_LOCK:
            seen_ids, saved_cursor = STORE.get_shorts_feed(user["username"])
        if refresh and not requested_cursor:
            seen_ids = []
            saved_cursor = ""
        cursor = requested_cursor or (saved_cursor if not refresh else "")
        if cursor and not re.fullmatch(r"catalog:\d{1,9}", cursor):
            cursor = ""
        offset = int(cursor.removeprefix("catalog:")) if cursor else 0
        recent_set = set(seen_ids)
        now = time.time()
        items: list[dict] = []
        stale_hit = False
        newest_catalog_at = 0.0
        next_offset = offset

        # Scan bounded catalog pages to skip this user's seen rows without copying the catalog.
        reached_catalog_end = False
        for _ in range(5):
            candidates = STORE.repository.list_shorts_catalog(
                limit=SHORTS_CATALOG_SCAN_SIZE,
                offset=next_offset,
            )
            if not candidates:
                next_offset = 0
                reached_catalog_end = True
                break
            newest_catalog_at = max(
                newest_catalog_at,
                max(float(candidate.get("last_seen_at", 0)) for candidate in candidates),
            )
            consumed = 0
            for candidate in candidates:
                consumed += 1
                if candidate["id"] in recent_set:
                    continue
                items.append({
                    "id": candidate["id"],
                    "title": candidate.get("title", "YouTube 쇼츠"),
                    "channel_title": candidate.get("channel_title", "YouTube"),
                })
                stale_hit = stale_hit or float(candidate.get("expires_at", 0)) <= now
                if len(items) >= SHORTS_CATALOG_PAGE_SIZE:
                    break
            next_offset += consumed
            if len(items) >= SHORTS_CATALOG_PAGE_SIZE:
                break
            if len(candidates) < SHORTS_CATALOG_SCAN_SIZE:
                if consumed >= len(candidates):
                    next_offset = 0
                    reached_catalog_end = True
                break

        catalog_hit = bool(items)
        emergency_hit = False
        cycled = False
        if not items and reached_catalog_end:
            candidates = STORE.repository.list_shorts_catalog(limit=SHORTS_CATALOG_PAGE_SIZE, offset=0)
            if candidates:
                items = [
                    {
                        "id": candidate["id"],
                        "title": candidate.get("title", "YouTube 쇼츠"),
                        "channel_title": candidate.get("channel_title", "YouTube"),
                    }
                    for candidate in candidates
                ]
                newest_catalog_at = max(float(candidate.get("last_seen_at", 0)) for candidate in candidates)
                stale_hit = any(float(candidate.get("expires_at", 0)) <= now for candidate in candidates)
                next_offset = len(candidates) if len(candidates) >= SHORTS_CATALOG_PAGE_SIZE else 0
                seen_ids = []
                catalog_hit = True
                cycled = True
        if not items:
            items = [item for item in EMERGENCY_SHORTS if item["id"] not in recent_set]
            if not items:
                items = list(EMERGENCY_SHORTS)
                seen_ids = []
                cycled = bool(items)
            emergency_hit = bool(items)
        next_cursor = f"catalog:{next_offset}"
        with SHORTS_FEED_LOCK:
            if items:
                seen_ids.extend(str(item["id"]) for item in items)
            STORE.save_shorts_feed(user["username"], seen_ids, next_cursor)
        SHORTS_COLLECTOR.record_feed(
            catalog_hit=catalog_hit,
            stale_hit=stale_hit,
            emergency_hit=emergency_hit,
        )
        self.send_json(
            {
                "items": items,
                "next_cursor": next_cursor,
                "retry_after": 3 if not items else 0,
                "cycled": cycled,
                "catalog": {
                    "stale": stale_hit,
                    "age_seconds": round(max(0.0, now - newest_catalog_at), 3) if newest_catalog_at else None,
                },
            },
            HTTPStatus.OK,
        )

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
        if payload.get("statusEmojiOnly") and saved_activity_emoji(status_message) != status_message.strip():
            self.send_json({"error": "텍스트 없이 이모티콘 하나만 선택해 주세요."}, HTTPStatus.BAD_REQUEST)
            return
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
        if not self.allow_request(f"group-room:{user['username']}", 20, 60 * 60):
            return
        self.run_json_command(lambda payload: APPLICATION.create_group_room(user, payload))

    def update_group_room_settings(self, user: dict) -> None:
        if not self.allow_request(f"room-settings:{user['username']}", 60, 60 * 60):
            return
        self.run_json_command(lambda payload: APPLICATION.update_group_room(user, payload))

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
            EVENT_BROKER.publish(
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
            EVENT_BROKER.publish(
                {"type": "room_updated", "roomId": room_id, "room": room},
                STORE.room_event_recipients(room_id),
            )

    def leave_group_room(self, user: dict) -> None:
        if not self.allow_request(f"leave-room:{user['username']}", 60, 60 * 60):
            return
        self.run_json_command(lambda payload: APPLICATION.leave_group_room(user, payload))

    def add_friend(self, user: dict) -> None:
        self.run_json_command(lambda payload: APPLICATION.add_friend(user, payload))

    def create_direct_room(self, user: dict) -> None:
        self.run_json_command(lambda payload: APPLICATION.create_direct_room(user, payload))

    def create_message(self, user: dict) -> None:
        self.run_json_command(
            lambda payload: APPLICATION.create_message(user, payload, self.message_attachment)
        )

    def delete_message(self, user: dict) -> None:
        self.run_json_command(lambda payload: APPLICATION.delete_message(user, payload))

    def upload_attachment(self, user: dict) -> None:
        self.close_connection = True
        self.send_json(
            {"error": "This upload endpoint was replaced by the signed upload flow."},
            HTTPStatus.GONE,
        )

    def grant_attachment_upload(self, user: dict) -> None:
        if not self.allow_request(f"upload:{user['username']}", 30, 60 * 60):
            return
        cleanup_expired_uploads()
        payload = self.read_json_body()
        if payload is None:
            return
        content_type = str(payload.get("type", "")).split(";", 1)[0].strip().lower()
        extension = ATTACHMENT_TYPES.get(content_type)
        try:
            size = int(payload.get("size", 0))
        except (TypeError, ValueError):
            size = 0
        original_name = Path(str(payload.get("name", "file"))).name.strip()[:120]
        if extension is None or not 1 <= size <= MAX_ATTACHMENT_BYTES:
            self.send_json(
                {"error": "Unsupported or oversized file. Images and PDFs can be up to 8MB."},
                HTTPStatus.BAD_REQUEST,
            )
            return
        if not original_name:
            original_name = f"file{extension}"
        if Path(original_name).suffix.lower() not in {extension, ".jpeg" if extension == ".jpg" else extension}:
            self.send_json({"error": "The file extension does not match its media type."}, HTTPStatus.BAD_REQUEST)
            return

        filename = f"upload_{uuid.uuid4().hex}{extension}"
        upload_token = UPLOAD_GRANTS.create_pending(
            filename,
            user["username"],
            name=original_name,
            content_type=content_type,
            size=size,
        )
        if upload_token is None:
            self.send_json(
                {"error": "Too many unfinished uploads. Finish or cancel an upload first."},
                HTTPStatus.TOO_MANY_REQUESTS,
            )
            return
        try:
            if SUPABASE_ENABLED:
                upload_url = supabase_signed_upload_url(filename)
                upload_headers = {
                    "Content-Type": content_type,
                    "cache-control": "3600",
                    "x-upsert": "false",
                }
            else:
                upload_url = f"/uploads/{filename}?grant={quote(upload_token)}"
                upload_headers = {"Content-Type": content_type}
        except (ConnectionError, ValueError):
            UPLOAD_GRANTS.fail(filename, user["username"])
            self.send_json({"error": "Unable to authorize the upload."}, HTTPStatus.BAD_GATEWAY)
            return
        self.send_json(
            {
                "upload": {
                    "id": filename,
                    "url": upload_url,
                    "method": "PUT",
                    "headers": upload_headers,
                    "expires_in": UPLOAD_GRANT_TTL_SECONDS,
                    "storage": "object" if SUPABASE_ENABLED else "local-stream",
                }
            },
            HTTPStatus.CREATED,
        )

    def receive_attachment_upload(self, request_path: str, user: dict) -> None:
        if SUPABASE_ENABLED:
            self.close_connection = True
            self.send_json({"error": "Direct storage uploads must use the signed object URL."}, HTTPStatus.NOT_FOUND)
            return
        parsed_url = urlparse(self.path)
        filename = Path(unquote(request_path.removeprefix("/uploads/"))).name
        token = parse_qs(parsed_url.query).get("grant", [""])[0]
        grant = UPLOAD_GRANTS.authorize_transfer(filename, user["username"], token)
        if grant is None or not UPLOAD_NAME_PATTERN.fullmatch(filename):
            self.close_connection = True
            self.send_json({"error": "Upload grant is invalid or expired."}, HTTPStatus.FORBIDDEN)
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != grant["type"]:
            self.close_connection = True
            self.send_json({"error": "The upload media type does not match its grant."}, HTTPStatus.BAD_REQUEST)
            return
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        upload_path = (UPLOADS_DIR / filename).resolve()
        upload_path.relative_to(UPLOADS_DIR.resolve())
        temporary_path = upload_path.with_name(f".{filename}.{uuid.uuid4().hex}.part")
        try:
            result = self.stream_request_body_to_file(temporary_path, int(grant["size"]))
            if result is None:
                return
            size, prefix = result
            if size != int(grant["size"]) or not self.valid_attachment_content(content_type, prefix):
                UPLOAD_GRANTS.fail(filename, user["username"])
                self.send_json({"error": "The uploaded file did not match its grant."}, HTTPStatus.BAD_REQUEST)
                return
            temporary_path.replace(upload_path)
            completed = UPLOAD_GRANTS.complete(
                filename,
                user["username"],
                size=size,
                content_type=content_type,
            )
            if completed is None:
                upload_path.unlink(missing_ok=True)
                self.send_json({"error": "Upload grant expired before completion."}, HTTPStatus.CONFLICT)
                return
            self.send_json({"uploaded": True}, HTTPStatus.OK)
        except (ConnectionError, OSError, ValueError):
            UPLOAD_GRANTS.fail(filename, user["username"])
            self.send_json({"error": "Unable to save the file."}, HTTPStatus.BAD_GATEWAY)
        finally:
            temporary_path.unlink(missing_ok=True)

    def complete_attachment_upload(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        filename = str(payload.get("id", ""))
        grant = UPLOAD_GRANTS.get(filename, user["username"])
        if grant is None or not UPLOAD_NAME_PATTERN.fullmatch(filename):
            self.send_json({"error": "Upload grant is invalid or expired."}, HTTPStatus.FORBIDDEN)
            return
        if grant["state"] == "failed":
            self.send_json({"error": "The upload failed and cannot be completed."}, HTTPStatus.CONFLICT)
            return
        try:
            if SUPABASE_ENABLED:
                size, stored_type, prefix = probe_supabase_upload(filename)
                content_type = str(grant["type"])
                if stored_type not in {content_type, "application/octet-stream"}:
                    raise ValueError("Stored media type does not match")
                if not self.valid_attachment_content(content_type, prefix):
                    raise ValueError("Stored content signature does not match")
                completed = UPLOAD_GRANTS.complete(
                    filename,
                    user["username"],
                    size=size,
                    content_type=content_type,
                )
            else:
                upload_path = (UPLOADS_DIR / filename).resolve()
                upload_path.relative_to(UPLOADS_DIR.resolve())
                size = upload_path.stat().st_size
                with upload_path.open("rb") as upload_file:
                    prefix = upload_file.read(ATTACHMENT_IMAGE_PROBE_BYTES)
                content_type = str(grant["type"])
                if not self.valid_attachment_content(content_type, prefix):
                    raise ValueError("Stored content signature does not match")
                completed = UPLOAD_GRANTS.complete(
                    filename,
                    user["username"],
                    size=size,
                    content_type=content_type,
                )
        except (ConnectionError, FileNotFoundError, OSError, ValueError):
            UPLOAD_GRANTS.fail(filename, user["username"])
            try:
                delete_upload_object(filename)
            except (ConnectionError, OSError, ValueError):
                pass
            self.send_json({"error": "The uploaded object could not be verified."}, HTTPStatus.BAD_GATEWAY)
            return
        if completed is None:
            self.send_json({"error": "The uploaded object does not match its grant."}, HTTPStatus.CONFLICT)
            return
        attachment = {
            "url": f"/uploads/{filename}",
            "name": completed["name"],
            "type": completed["type"],
            "size": completed["size"],
        }
        self.send_json({"attachment": attachment}, HTTPStatus.CREATED)

    def discard_attachment_upload(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        upload_url = unquote(str(payload.get("url", "")))
        filename = upload_url.removeprefix("/uploads/")
        discarded_grant = None
        if (
            not upload_url.startswith("/uploads/")
            or "/" in filename
            or "\\" in filename
            or not UPLOAD_NAME_PATTERN.fullmatch(filename)
        ):
            self.send_json({"discarded": False}, HTTPStatus.OK)
            return
        discarded_grant = UPLOAD_GRANTS.discard(filename, user["username"])
        if discarded_grant is None:
            self.send_json({"discarded": False}, HTTPStatus.OK)
            return
        try:
            delete_upload_object(filename)
        except (ConnectionError, OSError, ValueError):
            UPLOAD_GRANTS.restore(discarded_grant)
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
        signature_matches = signatures.get(content_type, False)
        if not signature_matches or not content_type.startswith("image/"):
            return bool(signature_matches)
        return safe_attachment_image_dimensions(content_type, content)

    def message_attachment(self, value: object, username: str) -> dict | None:
        if not isinstance(value, dict):
            return None
        url = str(value.get("url", "")).strip()
        filename = Path(unquote(url.removeprefix("/uploads/"))).name
        content_type = str(value.get("type", "")).strip().lower()
        if not url.startswith("/uploads/") or ATTACHMENT_TYPES.get(content_type) != Path(filename).suffix.lower():
            return None
        grant = UPLOAD_GRANTS.get(filename, username)
        if grant is not None:
            if grant["state"] != "completed":
                return None
            content_type = str(grant["type"])
            attachment_size = int(grant["size"])
            name = str(grant["name"])
        else:
            if not STORE.can_access_attachment(filename, username):
                return None
            try:
                attachment_size = int(value.get("size", 0))
            except (TypeError, ValueError):
                attachment_size = 0
            name = Path(str(value.get("name", filename))).name.strip()[:120] or filename
        if not SUPABASE_ENABLED:
            upload_path = (UPLOADS_DIR / filename).resolve()
            try:
                upload_path.relative_to(UPLOADS_DIR.resolve())
            except ValueError:
                return None
            if not upload_path.is_file():
                return None
            attachment_size = upload_path.stat().st_size
        return {
            "url": f"/uploads/{filename}",
            "name": name,
            "type": content_type,
            "size": attachment_size,
        }

    def mark_room_read(self, user: dict) -> None:
        self.run_json_command(lambda payload: APPLICATION.mark_room_read(user, payload))

    def update_presence(self, user: dict) -> None:
        self.run_json_command(
            lambda payload: APPLICATION.update_presence(self.read_session_token(), user, payload)
        )

    def current_user(self) -> dict | None:
        token = self.read_session_token()
        username = SESSIONS.get_username(token)
        if username is None:
            return None
        self._safe_user_id = safe_user_identifier(username)
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
        self.send_header("Content-Length", "0")
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
        if content_length == 0:
            self._request_body_consumed = True
            return b""
        if not self.server.body_reader_slots.acquire(blocking=False):
            self.server.request_metrics.increment("body_reader_rejections_total")
            self.close_connection = True
            self.send_json({"error": "Server is busy reading request bodies."}, HTTPStatus.SERVICE_UNAVAILABLE)
            return None
        self.server.request_metrics.increment("active_body_readers")
        try:
            timeout = UPLOAD_READ_TIMEOUT_SECONDS if max_bytes > MAX_JSON_REQUEST_BYTES else BODY_READ_TIMEOUT_SECONDS
            return self.read_exact_body(content_length, timeout, send_timeout_response=True)
        finally:
            self.server.request_metrics.increment("active_body_readers", -1)
            self.server.body_reader_slots.release()

    def stream_request_body_to_file(self, destination: Path, expected_size: int) -> tuple[int, bytes] | None:
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
        if content_length != expected_size or not 1 <= content_length <= MAX_ATTACHMENT_BYTES:
            self.close_connection = True
            self.send_json({"error": "Upload size does not match its grant."}, HTTPStatus.BAD_REQUEST)
            return None
        if not self.server.body_reader_slots.acquire(blocking=False):
            self.server.request_metrics.increment("body_reader_rejections_total")
            self.close_connection = True
            self.send_json({"error": "Server is busy reading request bodies."}, HTTPStatus.SERVICE_UNAVAILABLE)
            return None
        self.server.request_metrics.increment("active_body_readers")
        deadline = time.monotonic() + UPLOAD_READ_TIMEOUT_SECONDS
        received = 0
        prefix = bytearray()
        try:
            with destination.open("xb") as output:
                while received < content_length:
                    remaining_seconds = deadline - time.monotonic()
                    if remaining_seconds <= 0:
                        raise TimeoutError("upload deadline exceeded")
                    self.connection.settimeout(remaining_seconds)
                    chunk = self.rfile.read(min(64 * 1024, content_length - received))
                    if not chunk:
                        self.close_connection = True
                        return None
                    output.write(chunk)
                    received += len(chunk)
                    if len(prefix) < ATTACHMENT_IMAGE_PROBE_BYTES:
                        prefix.extend(chunk[:ATTACHMENT_IMAGE_PROBE_BYTES - len(prefix)])
                output.flush()
                os.fsync(output.fileno())
            self._request_body_consumed = True
            return received, bytes(prefix)
        except (TimeoutError, socket.timeout):
            self.server.request_metrics.increment("body_timeouts_total")
            self.close_connection = True
            try:
                self.send_json({"error": "Upload body timed out."}, HTTPStatus.REQUEST_TIMEOUT)
            except (TimeoutError, socket.timeout, ConnectionError, OSError):
                pass
            return None
        except (ConnectionError, OSError):
            self.close_connection = True
            return None
        finally:
            self.server.request_metrics.increment("active_body_readers", -1)
            self.server.body_reader_slots.release()
            try:
                self.connection.settimeout(HEADER_READ_TIMEOUT_SECONDS)
            except OSError:
                pass

    def read_exact_body(
        self,
        content_length: int,
        timeout_seconds: float,
        *,
        send_timeout_response: bool,
    ) -> bytes | None:
        deadline = time.monotonic() + timeout_seconds
        content = bytearray()
        try:
            while len(content) < content_length:
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    raise TimeoutError("request body deadline exceeded")
                self.connection.settimeout(remaining_seconds)
                chunk = self.rfile.read(min(64 * 1024, content_length - len(content)))
                if not chunk:
                    self.close_connection = True
                    return None
                content.extend(chunk)
        except (TimeoutError, socket.timeout):
            self.server.request_metrics.increment("body_timeouts_total")
            self.close_connection = True
            if send_timeout_response:
                try:
                    self.send_json({"error": "Request body timed out."}, HTTPStatus.REQUEST_TIMEOUT)
                except (TimeoutError, socket.timeout, ConnectionError, OSError):
                    pass
            return None
        except (ConnectionError, OSError):
            self.close_connection = True
            return None
        finally:
            try:
                self.connection.settimeout(HEADER_READ_TIMEOUT_SECONDS)
            except OSError:
                pass
        self._request_body_consumed = True
        return bytes(content)

    def discard_unread_request_body(self) -> None:
        if getattr(self, "_request_body_consumed", False) or self.close_connection:
            return
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.close_connection = True
            return
        if content_length < 0 or content_length > MAX_ATTACHMENT_BYTES:
            self.close_connection = True
            return
        if content_length:
            if not self.server.body_reader_slots.acquire(blocking=False):
                self.server.request_metrics.increment("body_reader_rejections_total")
                self.close_connection = True
                return
            self.server.request_metrics.increment("active_body_readers")
            try:
                timeout = UPLOAD_READ_TIMEOUT_SECONDS if content_length > MAX_JSON_REQUEST_BYTES else BODY_READ_TIMEOUT_SECONDS
                self.read_exact_body(content_length, timeout, send_timeout_response=False)
            finally:
                self.server.request_metrics.increment("active_body_readers", -1)
                self.server.body_reader_slots.release()
            return
        self._request_body_consumed = True

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

    def send_conditional_json(self, data: object) -> None:
        canonical = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        etag = f'"{hashlib.sha256(canonical).hexdigest()[:24]}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if self.headers.get("If-None-Match", "") == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_json(data, HTTPStatus.OK, headers=headers)

    def log_message(self, format: str, *args: object) -> None:
        return

    def log_error(self, format: str, *args: object) -> None:
        if format.startswith("Request timed out"):
            self.server.request_metrics.increment("header_timeouts_total")


class ChatServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        *,
        max_request_threads: int = MAX_REQUEST_THREADS,
        max_body_readers: int = MAX_BODY_READERS,
    ) -> None:
        self.max_request_threads = max(1, max_request_threads)
        self.max_body_readers = max(1, min(max_body_readers, self.max_request_threads))
        self.request_slots = threading.BoundedSemaphore(self.max_request_threads)
        self.body_reader_slots = threading.BoundedSemaphore(self.max_body_readers)
        self.request_metrics = RequestRuntimeMetrics()
        self._readiness_lock = threading.Lock()
        self._readiness = {"ready": False, "status": "not_checked", "checked_at": ""}
        super().__init__(server_address, request_handler_class)

    def set_readiness_snapshot(self, report: dict) -> None:
        with self._readiness_lock:
            self._readiness = copy.deepcopy(report)

    def readiness_snapshot(self) -> dict:
        with self._readiness_lock:
            return copy.deepcopy(self._readiness)

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        request, client_address = super().get_request()
        request.settimeout(HEADER_READ_TIMEOUT_SECONDS)
        return request, client_address

    def process_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        if not self.request_slots.acquire(blocking=False):
            self.request_metrics.increment("rejected_total")
            self.reject_overloaded_request(request)
            self.shutdown_request(request)
            return
        self.request_metrics.increment("active")
        self.request_metrics.increment("accepted_total")
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.request_metrics.increment("active", -1)
            self.request_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_metrics.increment("active", -1)
            self.request_slots.release()

    @staticmethod
    def reject_overloaded_request(request: socket.socket) -> None:
        content = b'{"error":"Server request capacity reached."}'
        request_id = secrets.token_hex(12)
        security_headers = b"".join(
            f"{name}: {value}\r\n".encode("ascii")
            for name, value in COMMON_SECURITY_HEADERS
        )
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Connection: close\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            + f"Content-Length: {len(content)}\r\n".encode("ascii")
            + f"X-Request-ID: {request_id}\r\n".encode("ascii")
            + b"Retry-After: 1\r\n"
            + security_headers
            + b"\r\n"
            + content
        )
        try:
            request.sendall(response)
        except (TimeoutError, socket.timeout, ConnectionError, OSError):
            pass


if __name__ == "__main__":
    SHORTS_COLLECTOR.start()
    server = ChatServer((HOST, PORT), ChatHandler)
    print(f"{APP_NAME} running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        SHORTS_COLLECTOR.close()
        EVENT_BROKER.close()
        STORE.close()
