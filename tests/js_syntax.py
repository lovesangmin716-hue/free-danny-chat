from __future__ import annotations

import shutil
import subprocess
import re
from pathlib import Path


ROOT_DIR = Path(__file__).parents[1]
SOURCE_JS_DIR = ROOT_DIR / "frontend" / "src"
PRODUCTION_JS_DIR = ROOT_DIR / "src" / "colorless" / "web" / "assets"
MODULE_IMPORT = re.compile(r'import(?:\s+[^"\']+\s+from\s+|\s*)["\']([^"\']+)["\']')


def main() -> None:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js is required to check browser JavaScript syntax")

    source_files = sorted(SOURCE_JS_DIR.rglob("*.js"))
    production_files = sorted(PRODUCTION_JS_DIR.rglob("*.js"))
    javascript_files = source_files + production_files
    if not source_files or not production_files:
        raise SystemExit("Both frontend sources and production JavaScript are required")

    for javascript_file in javascript_files:
        subprocess.run(
            [node, "--check", str(javascript_file)],
            check=True,
            cwd=ROOT_DIR,
        )
        if javascript_file not in source_files:
            continue
        for import_url in MODULE_IMPORT.findall(javascript_file.read_text(encoding="utf-8")):
            imported_file = (javascript_file.parent / import_url.split("?", 1)[0]).resolve()
            try:
                imported_file.relative_to(SOURCE_JS_DIR.resolve())
            except ValueError as error:
                raise SystemExit(f"Module import escapes JavaScript root: {javascript_file} -> {import_url}") from error
            if not imported_file.is_file():
                raise SystemExit(f"Missing JavaScript module import: {javascript_file} -> {import_url}")

    print(f"JavaScript syntax OK: {len(source_files)} source files, {len(production_files)} production files")


if __name__ == "__main__":
    main()
