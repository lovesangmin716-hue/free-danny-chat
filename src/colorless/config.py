from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlparse

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


load_local_env(Path(os.getenv("COLORLESS_ENV_FILE", str(Path.cwd() / ".env"))))

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8765"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_STORAGE_ORIGIN = (
    f"{urlparse(SUPABASE_URL).scheme}://{urlparse(SUPABASE_URL).netloc}"
    if SUPABASE_URL and urlparse(SUPABASE_URL).scheme in {"http", "https"}
    else ""
)

DEFAULT_DATA_DIR = Path.cwd() / ".colorless-data"
LEGACY_DATA_DIR = Path.cwd() / "outputs" / "chat-app"
if not DEFAULT_DATA_DIR.exists() and any(
    (LEGACY_DATA_DIR / name).exists()
    for name in ("chat_state.json", "chat_state.json.sqlite3", "uploads")
):
    DEFAULT_DATA_DIR = LEGACY_DATA_DIR
DATA_DIR = Path(os.getenv("DATA_DIR", str(DEFAULT_DATA_DIR)))
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
MAX_VOICE_MESSAGE_DURATION_MS = 5 * 60 * 1000
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
        'accelerometer=(), camera=(), geolocation=(), gyroscope=(), microphone=(self), payment=(), usb=(), '
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
    "audio/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "application/rtf": ".rtf",
    "application/zip": ".zip",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}
VOICE_ATTACHMENT_TYPES = {"audio/webm", "audio/mp4", "audio/ogg"}
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
UPLOAD_NAME_PATTERN = re.compile(
    r"upload_[0-9a-f]{32}\.(?:jpg|png|gif|webp|heic|heif|avif|pdf|webm|m4a|ogg|txt|csv|md|rtf|zip|doc|xls|ppt|docx|xlsx|pptx)"
)
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
UPLOAD_BUCKET_CONFIGURED = not SUPABASE_ENABLED
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
    {"id": "k_ANHTu0XlA", "title": "2080년 한국 학생", "channel_title": "김켈리 Kellyfornia"},
    {"id": "7F1vyoPlh98", "title": "같은 기술 다른 느낌", "channel_title": "웃또또"},
    {"id": "UloIWifOjt0", "title": "한국 유머 쇼츠", "channel_title": "world with Funny video"},
)
POPULAR_VIDEO_CATEGORIES = (
    "24", "23", "10", "17", "20", "22", "26", "1", "2", "19", "25", "27", "28",
    "15", "21", "29", "18", "30", "31", "32", "33", "34", "35", "36", "37", "38",
    "39", "40", "41", "42", "43", "44",
)
