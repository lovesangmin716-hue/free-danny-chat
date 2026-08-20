from __future__ import annotations

import re
import struct
import zlib


PROFILE_ART_SIDE = 32
PROFILE_ART_PIXEL_COUNT = PROFILE_ART_SIDE * PROFILE_ART_SIDE
PROFILE_ART_PACKED_BYTES = PROFILE_ART_PIXEL_COUNT * 3
BLANK_PROFILE_COLOR = "#ffffff"
COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}")


def blank_profile_pixels() -> list[str]:
    return [BLANK_PROFILE_COLOR] * PROFILE_ART_PIXEL_COUNT


def valid_profile_pixels(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == PROFILE_ART_PIXEL_COUNT
        and all(isinstance(color, str) and COLOR_PATTERN.fullmatch(color) for color in value)
    )


def normalize_profile_pixels(value: object, legacy_palette: tuple[str, ...] = ()) -> list[str]:
    if valid_profile_pixels(value):
        return [color.lower() for color in value]
    if (
        legacy_palette
        and isinstance(value, str)
        and len(value) == PROFILE_ART_PIXEL_COUNT
        and all(character in "0123456789ab" for character in value)
    ):
        return [legacy_palette[int(character, 12)] for character in value]
    return blank_profile_pixels()


def is_blank_profile_pixels(value: object) -> bool:
    return valid_profile_pixels(value) and all(color.lower() == BLANK_PROFILE_COLOR for color in value)


def pack_profile_pixels(value: object) -> bytes:
    if not valid_profile_pixels(value):
        raise ValueError("invalid profile pixels")
    packed = bytearray(PROFILE_ART_PACKED_BYTES)
    for index, color in enumerate(value):
        offset = index * 3
        packed[offset:offset + 3] = bytes.fromhex(color[1:])
    return bytes(packed)


def unpack_profile_pixels(packed: object) -> list[str]:
    if not isinstance(packed, (bytes, bytearray, memoryview)):
        raise ValueError("invalid packed profile pixels")
    value = bytes(packed)
    if len(value) != PROFILE_ART_PACKED_BYTES:
        raise ValueError("invalid packed profile pixels")
    return [
        f"#{value[offset]:02x}{value[offset + 1]:02x}{value[offset + 2]:02x}"
        for offset in range(0, PROFILE_ART_PACKED_BYTES, 3)
    ]


def profile_art_png(packed: object) -> bytes:
    value = bytes(packed) if isinstance(packed, (bytes, bytearray, memoryview)) else b""
    if len(value) != PROFILE_ART_PACKED_BYTES:
        raise ValueError("invalid packed profile pixels")
    scanlines = b"".join(
        b"\x00" + value[row * PROFILE_ART_SIDE * 3:(row + 1) * PROFILE_ART_SIDE * 3]
        for row in range(PROFILE_ART_SIDE)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", PROFILE_ART_SIDE, PROFILE_ART_SIDE, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(scanlines, 9)) + chunk(b"IEND", b"")


def compact_user_profile_fields(user: dict) -> dict:
    compact = dict(user)
    compact.pop("profile_pixels", None)
    return compact
