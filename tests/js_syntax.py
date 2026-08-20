from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT_DIR = Path(__file__).parents[1]
JS_DIR = ROOT_DIR / "outputs" / "chat-app" / "assets" / "js"


def main() -> None:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js is required to check browser JavaScript syntax")

    javascript_files = sorted(JS_DIR.rglob("*.js"))
    if not javascript_files:
        raise SystemExit(f"No JavaScript files found in {JS_DIR}")

    for javascript_file in javascript_files:
        subprocess.run(
            [node, "--check", str(javascript_file)],
            check=True,
            cwd=ROOT_DIR,
        )

    print(f"JavaScript syntax OK: {len(javascript_files)} files")


if __name__ == "__main__":
    main()
