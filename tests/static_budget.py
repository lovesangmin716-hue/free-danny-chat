from __future__ import annotations

import gzip
import hashlib
import re
from pathlib import Path
from urllib.parse import unquote

try:
    import brotli
except ImportError:
    brotli = None


ROOT = Path(__file__).parents[1]
APP = ROOT / "outputs" / "chat-app"
ASSETS = APP / "assets"
BUDGETS = {
    ".js": 250 * 1024,
    ".css": 16 * 1024,
    ".woff2": 250 * 1024,
    ".image": 100 * 1024,
    ".html": 80 * 1024,
    ".static-total": 512 * 1024,
}
IMAGE_SUFFIXES = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
VERSIONED_URL = re.compile(r"(?P<url>(?:/?assets/|\.\./|js/|[a-z0-9-]+\.js)[^\"')]*?)\?v=(?P<hash>[0-9a-f]{12})")


def referenced_asset(source: Path, url: str) -> Path:
    url = unquote(url)
    if url.startswith("/assets/"):
        return APP / url.removeprefix("/")
    if url.startswith("assets/"):
        return APP / url
    return (source.parent / url).resolve()


def main() -> int:
    files = [path for path in ASSETS.rglob("*") if path.is_file()]
    forbidden_fonts = [path for path in files if path.suffix.lower() in {".ttf", ".otf"}]
    if forbidden_fonts:
        raise SystemExit(f"Unreferenced production fonts are forbidden: {forbidden_fonts}")

    totals = {key: 0 for key in BUDGETS}
    for path in files:
        suffix = path.suffix.lower()
        if suffix in {".js", ".css", ".woff2"}:
            totals[suffix] += path.stat().st_size
        if suffix in IMAGE_SUFFIXES:
            totals[".image"] += path.stat().st_size
    totals[".html"] = (APP / "index.html").stat().st_size + (APP / "signup.html").stat().st_size + sum(
        path.stat().st_size for path in ASSETS.rglob("*.html")
    )
    totals[".static-total"] = sum(path.stat().st_size for path in files) + (APP / "index.html").stat().st_size + (APP / "signup.html").stat().st_size
    for group, limit in BUDGETS.items():
        if totals[group] > limit:
            raise SystemExit(f"{group} budget exceeded: {totals[group]} > {limit}")

    text_sources = [APP / "index.html", APP / "signup.html", ASSETS / "image-worker-benchmark.html"]
    text_sources.extend(path for path in files if path.suffix.lower() in {".js", ".css", ".html"})
    checked = 0
    for source in text_sources:
        text = source.read_text(encoding="utf-8")
        for match in VERSIONED_URL.finditer(text):
            asset = referenced_asset(source, match.group("url"))
            if not asset.is_file():
                raise SystemExit(f"Missing fingerprint asset referenced by {source}: {asset}")
            actual = hashlib.sha256(asset.read_bytes()).hexdigest()[:12]
            if actual != match.group("hash"):
                raise SystemExit(f"Stale fingerprint in {source}: {asset.name} has {actual}")
            checked += 1
    if checked < 20:
        raise SystemExit(f"Expected at least 20 fingerprinted references, found {checked}")

    compressible = b"\n".join(path.read_bytes() for path in files if path.suffix.lower() in {".css", ".html", ".js", ".json", ".svg"})
    gzip_bytes = len(gzip.compress(compressible, compresslevel=6))
    brotli_bytes = len(brotli.compress(compressible, quality=5)) if brotli is not None else None
    print({"raw": totals, "gzip_compressible": gzip_bytes, "brotli_compressible": brotli_bytes, "fingerprints": checked})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
