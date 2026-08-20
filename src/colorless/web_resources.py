from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

try:
    import brotli
except ImportError:  # Local stdlib-only development keeps gzip as a safe fallback.
    brotli = None

PACKAGE_DIR = Path(__file__).resolve().parent
WEB_DIR = PACKAGE_DIR / "web"
# Kept as the web root for code that resolves browser asset paths.
BASE_DIR = WEB_DIR

INDEX_FILE = WEB_DIR / "index.html"
SIGNUP_FILE = WEB_DIR / "signup.html"
ASSETS_DIR = WEB_DIR / "assets"
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
