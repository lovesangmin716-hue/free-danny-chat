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

from .config import (
    AGE_GROUPS,
    APP_NAME,
    ATTACHMENT_IMAGE_DIMENSION_MAX,
    ATTACHMENT_IMAGE_PIXELS_MAX,
    ATTACHMENT_IMAGE_PROBE_BYTES,
    ATTACHMENT_TYPES,
    BODY_READ_TIMEOUT_SECONDS,
    CLIENT_MESSAGE_ID_PATTERN,
    COMMON_SECURITY_HEADERS,
    CONTENT_SECURITY_POLICY,
    DATA_DIR,
    DEFAULT_DATA_DIR,
    DEFAULT_ENTITY_PAGE_SIZE,
    DEFAULT_MESSAGES_PAGE_SIZE,
    DOWNLOAD_URL_TTL_SECONDS,
    EMERGENCY_SHORTS,
    EVENT_POLL_INTERVAL_SECONDS,
    FRIEND_CODE_PATTERN,
    GENDERS,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    HEADER_READ_TIMEOUT_SECONDS,
    HOST,
    INSTANCE_ID,
    KAKAO_CLIENT_SECRET,
    KAKAO_REDIRECT_URI,
    KAKAO_REST_API_KEY,
    LEGACY_DATA_DIR,
    MAX_ATTACHMENT_BYTES,
    MAX_BODY_READERS,
    MAX_ENTITY_PAGE_SIZE,
    MAX_FORM_REQUEST_BYTES,
    MAX_GROUP_PARTICIPANTS,
    MAX_JSON_REQUEST_BYTES,
    MAX_MESSAGES_PAGE_SIZE,
    MAX_MESSAGES_PER_ROOM,
    MAX_PROFILE_IMAGE_BYTES,
    MAX_PROFILE_THUMBNAIL_BYTES,
    MAX_REQUEST_THREADS,
    MAX_SESSIONS,
    MAX_SHORTS_SEEN_IDS,
    MAX_SSE_CONNECTIONS,
    MAX_SSE_QUEUE_SIZE,
    MAX_SYNC_EVENTS,
    MAX_VOICE_MESSAGE_DURATION_MS,
    MESSAGE_ID_PATTERN,
    MIN_GROUP_PARTICIPANTS,
    OAUTH_STATE_COOKIE_NAME,
    OAUTH_STATE_TTL_SECONDS,
    PASSWORD_ITERATIONS,
    PHONE_CODE_TTL_SECONDS,
    PHONE_TOKEN_TTL_SECONDS,
    PHONE_VERIFICATION_MODE,
    POPULAR_VIDEO_CATEGORIES,
    PORT,
    PRESENCE_TTL_SECONDS,
    PROFILE_ART_THUMBNAIL_PATH_PATTERN,
    PROFILE_IMAGE_NAME_PATTERN,
    PROFILE_IMAGE_SIDE,
    PROFILE_PALETTE,
    PROFILE_PIXEL_COUNT,
    PROFILE_PIXEL_SIDE,
    PROFILE_THUMBNAIL_SIDE,
    PUBLIC_BASE_URL,
    REQUEST_ID_PATTERN,
    REQUIRE_SUPABASE,
    ROOM_ID_PATTERN,
    ROOM_IMAGE_NAME_PATTERN,
    ROOM_MEMBERS_PATH_PATTERN,
    SESSION_CLEANUP_INTERVAL_SECONDS,
    SESSION_COOKIE_NAME,
    SESSION_REFRESH_THRESHOLD_SECONDS,
    SESSION_TTL_SECONDS,
    SESSION_VALIDATION_CACHE_SECONDS,
    SHORTS_AGE_TRENDING_TOPICS,
    SHORTS_CATALOG_PAGE_SIZE,
    SHORTS_CATALOG_RETENTION_SECONDS,
    SHORTS_CATALOG_SCAN_SIZE,
    SHORTS_CATALOG_TTL_SECONDS,
    SHORTS_COLLECTION_INTERVAL_SECONDS,
    SHORTS_COLLECTION_LEASE_SECONDS,
    SHORTS_DAILY_QUOTA_BUDGET,
    SHORTS_PROFILE_TOPICS,
    SOCIAL_DEMO_ADMIN_PASSWORD,
    SOCIAL_DEMO_LOGIN_ENABLED,
    SSE_HEARTBEAT_SECONDS,
    STATE_FILE,
    STRUCTURED_LOGS_ENABLED,
    SUPABASE_ENABLED,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_STATE_ID,
    SUPABASE_STATE_TABLE,
    SUPABASE_STORAGE_ORIGIN,
    SUPABASE_UPLOAD_BUCKET,
    SUPABASE_URL,
    UPLOADS_DIR,
    UPLOAD_BUCKET_CONFIGURED,
    UPLOAD_GRANT_TTL_SECONDS,
    UPLOAD_NAME_PATTERN,
    UPLOAD_READ_TIMEOUT_SECONDS,
    USER_ID_PATTERN,
    VOICE_ATTACHMENT_TYPES,
    YOUTH_SHORTS_BLOCKLIST,
    YOUTUBE_API_KEY,
)

from .observability import (
    RequestRuntimeMetrics,
    SSE_METRICS,
    SseRuntimeMetrics,
    normalized_request_route,
    process_open_file_descriptors,
    process_rss_bytes,
    safe_user_identifier,
    write_structured_log,
)

from .utils import (
    attachment_image_dimensions,
    blank_profile_pixels,
    build_status_message,
    clear_oauth_state_cookie,
    decode_page_cursor,
    default_public_base_url,
    encode_page_cursor,
    hash_password,
    make_cookie_header,
    make_oauth_state_cookie,
    mask_phone,
    new_friend_code,
    new_id,
    normalize_custom_palette,
    normalize_friend_code,
    normalize_phone,
    normalize_profile_image_url,
    normalize_profile_pixels,
    normalize_room_image_url,
    profile_image_filename,
    room_image_filename,
    safe_attachment_image_dimensions,
    sanitize_username_seed,
    saved_activity_emoji,
    utc_now,
    utc_now_iso,
    valid_hex_color,
    valid_profile_pixels,
    webp_dimensions,
)

from .web_resources import (
    ASSETS_DIR,
    ASSET_BROTLI_CONTENT,
    ASSET_CONTENT,
    ASSET_FINGERPRINTS,
    ASSET_GZIP_CONTENT,
    BASE_DIR,
    COMPRESSIBLE_ASSET_SUFFIXES,
    INDEX_BROTLI_CONTENT,
    INDEX_CONTENT,
    INDEX_ETAG,
    INDEX_FILE,
    INDEX_GZIP_CONTENT,
    PACKAGE_DIR,
    SIGNUP_BROTLI_CONTENT,
    SIGNUP_CONTENT,
    SIGNUP_ETAG,
    SIGNUP_FILE,
    SIGNUP_GZIP_CONTENT,
    WEB_DIR,
    accepts_content_encoding,
    brotli,
)

from .runtime import (
    OAuthStateStore,
    PhoneVerificationStore,
    PresenceStore,
    SessionStore,
    SlidingWindowRateLimiter,
    UploadGrantStore,
)
from .cache import BoundedTTLCache
from .integrations import OUTBOUND_HTTP_CLIENT, fetch_bytes, fetch_json, supabase_headers
from .shorts import (
    ShortsCatalogCollector,
    YoutubeCatalogError,
    collect_youtube_catalog_job,
    fetch_youtube_catalog_json,
    korean_shorts_search_queries,
    shorts_search_queries_for,
    shorts_search_query_for,
    trending_shorts_search_query,
    youtube_catalog_item,
    youtube_duration_seconds,
)
from .persistence import ConcurrentUpdateError, NormalizedSqliteRepository, NormalizedSupabaseRepository
from .state import StateStore
from .realtime import DurableEventBroker
from .application import ApplicationServices, CommandFailure, CommandOutcome
from .http import (
    AuthRoutesMixin,
    HandlerContext,
    MessagingRoutesMixin,
    ShortsRoutesMixin,
    UploadRoutesMixin,
)
from .profile_art import (
    PROFILE_ART_PIXEL_COUNT,
    blank_profile_pixels as build_blank_profile_pixels,
    is_blank_profile_pixels,
    normalize_profile_pixels as normalize_profile_art_pixels,
    pack_profile_pixels,
    profile_art_png,
    unpack_profile_pixels,
    valid_profile_pixels as are_valid_profile_pixels,
)

SUBSCRIBERS: dict[queue.Queue, str] = {}
SUBSCRIBERS_BY_USERNAME: dict[str, set[queue.Queue]] = {}
SUBSCRIBERS_LOCK = threading.Lock()
SSE_CONNECTION_SLOTS = threading.BoundedSemaphore(MAX_SSE_CONNECTIONS)
SHORTS_FEED_LOCK = threading.Lock()








def supabase_object_url(filename: str) -> str:
    return f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_UPLOAD_BUCKET}/{quote(filename)}"


def configure_supabase_upload_bucket() -> None:
    global UPLOAD_BUCKET_CONFIGURED
    if not SUPABASE_ENABLED:
        UPLOAD_BUCKET_CONFIGURED = True
        return
    payload = {
        "id": SUPABASE_UPLOAD_BUCKET,
        "name": SUPABASE_UPLOAD_BUCKET,
        "public": False,
        "file_size_limit": MAX_ATTACHMENT_BYTES,
        "allowed_mime_types": list(ATTACHMENT_TYPES),
    }
    fetch_json(
        f"{SUPABASE_URL}/storage/v1/bucket/{quote(SUPABASE_UPLOAD_BUCKET)}",
        method="PUT",
        headers=supabase_headers("application/json"),
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )
    UPLOAD_BUCKET_CONFIGURED = True


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






def cleanup_expired_uploads() -> int:
    removed = 0
    for grant in UPLOAD_GRANTS.pop_expired():
        try:
            delete_upload_object(str(grant["filename"]))
            removed += 1
        except (ConnectionError, OSError, ValueError):
            pass
    return removed










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
STORE.bind_presence(PRESENCE)
UPLOAD_GRANTS = UploadGrantStore()
SHORTS_COLLECTOR = ShortsCatalogCollector(STORE.repository, INSTANCE_ID, start=False)
EVENT_BROKER = DurableEventBroker(
    STORE.repository,
    INSTANCE_ID,
    STORE.presence_event_recipients,
    STORE.refresh_from_repository,
    deliver=push_event,
    cleanup=cleanup_expired_uploads,
)
PHONE_VERIFICATIONS = PhoneVerificationStore()
OAUTH_STATES = OAuthStateStore()
RATE_LIMITER = SlidingWindowRateLimiter()


APPLICATION = ApplicationServices(STORE, PRESENCE, lambda: UPLOAD_GRANTS)


class ChatHandler(
    AuthRoutesMixin,
    ShortsRoutesMixin,
    MessagingRoutesMixin,
    UploadRoutesMixin,
    BaseHTTPRequestHandler,
):
    context = HandlerContext(globals())
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
            "upload_bucket": UPLOAD_BUCKET_CONFIGURED,
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


def main() -> None:
    configure_supabase_upload_bucket()
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


if __name__ == "__main__":
    main()
