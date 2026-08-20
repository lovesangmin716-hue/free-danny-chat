from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import uuid
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import unquote

from .config import (
    ATTACHMENT_IMAGE_DIMENSION_MAX,
    ATTACHMENT_IMAGE_PIXELS_MAX,
    OAUTH_STATE_COOKIE_NAME,
    OAUTH_STATE_TTL_SECONDS,
    PASSWORD_ITERATIONS,
    PORT,
    PROFILE_IMAGE_NAME_PATTERN,
    PROFILE_PALETTE,
    PROFILE_PIXEL_COUNT,
    ROOM_IMAGE_NAME_PATTERN,
    SESSION_COOKIE_NAME,
)
from .profile_art import (
    PROFILE_ART_PIXEL_COUNT,
    blank_profile_pixels as build_blank_profile_pixels,
    normalize_profile_pixels as normalize_profile_art_pixels,
    valid_profile_pixels as are_valid_profile_pixels,
)

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
