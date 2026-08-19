from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import math
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


APP_DIR = Path(__file__).parents[1] / "outputs" / "chat-app"
SERVER_PATH = APP_DIR / "server.py"


def load_server(data_dir: Path):
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["STATE_FILE"] = str(data_dir / "state.json")
    os.environ["UPLOADS_DIR"] = str(data_dir / "uploads")
    spec = importlib.util.spec_from_file_location("colorless_bootstrap_scale_server", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("server module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def make_user(index: int, created_at: str) -> dict:
    return {
        "id": f"user_{index:08x}",
        "username": f"scale{index:04d}",
        "friend_code": f"scale_{index:04d}",
        "display_name": f"Scale User {index}",
        "status_message": "",
        "created_at": created_at,
        "profile_pixels": ["#ffffff"] * 1024,
        "profile_pixels_blank": True,
        "profile_image_url": "",
        "profile_thumbnail_url": "",
        "custom_palette": [],
        "_revision": 1,
    }


def build_fixture(store, count: int) -> dict:
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    users = [make_user(index, started_at.isoformat()) for index in range(count + 1)]
    owner = users[0]
    friendships = []
    rooms = []
    for index, friend in enumerate(users[1:], start=1):
        friendships.append({"user_ids": [owner["id"], friend["id"]], "created_at": started_at.isoformat()})
        updated_at = (started_at + timedelta(seconds=index)).isoformat()
        room = store._new_room(f"room_{index:08x}", f"Room {index}", "", owner["username"], updated_at)
        room.update({
            "kind": "direct",
            "participant_ids": [owner["id"], friend["id"]],
            "_revision": 1,
        })
        rooms.append(room)
    with store.lock:
        store.state.update({
            "users": users,
            "friendships": friendships,
            "rooms": rooms,
            "messages": {},
            "shorts_feeds": {},
            "sessions": {},
        })
        store._rebuild_indexes_locked()
    return owner


def encode_payload(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def crawl_pages(fetch_page) -> tuple[int, int]:
    cursor = ""
    ids: list[str] = []
    pages = 0
    while True:
        page = fetch_page(cursor)
        pages += 1
        ids.extend(str(item["id"]) for item in page["items"])
        cursor = str(page["next_cursor"])
        if not cursor:
            break
    if len(ids) != len(set(ids)):
        raise RuntimeError("pagination returned duplicate entities")
    return len(ids), pages


def run(count: int, iterations: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="colorless-bootstrap-scale-") as temp_dir:
        server = load_server(Path(temp_dir))
        store = server.StateStore(Path(temp_dir) / "fixture-state.json")
        try:
            owner = build_fixture(store, count)
            latencies_ms: list[float] = []
            raw_sizes: list[int] = []
            gzip_sizes: list[int] = []
            for _ in range(iterations):
                started = time.perf_counter()
                payloads = (
                    store.get_me_summary(owner),
                    store.get_friends_page(owner, limit=30),
                    store.get_rooms_page(owner, limit=30),
                )
                latencies_ms.append((time.perf_counter() - started) * 1000)
                encoded = [encode_payload(payload) for payload in payloads]
                raw_sizes.append(sum(map(len, encoded)))
                gzip_sizes.append(sum(len(gzip.compress(payload)) for payload in encoded))

            friend_count, friend_pages = crawl_pages(
                lambda cursor: store.get_friends_page(owner, limit=50, cursor=cursor)
            )
            room_count, room_pages = crawl_pages(
                lambda cursor: store.get_rooms_page(owner, limit=50, cursor=cursor)
            )
            first_friends = store.get_friends_page(owner, limit=30)["items"]
            first_rooms = store.get_rooms_page(owner, limit=30)["items"]
            compact = (
                all("profile_pixels" not in friend for friend in first_friends)
                and all("participants" not in room for room in first_rooms)
            )
            report = {
                "fixture": {"friends": count, "rooms": count, "iterations": iterations},
                "first_load": {
                    "p95_ms": round(percentile(latencies_ms, 0.95), 3),
                    "raw_bytes_max": max(raw_sizes),
                    "gzip_bytes_max": max(gzip_sizes),
                },
                "pagination": {
                    "friends": friend_count,
                    "friend_pages": friend_pages,
                    "rooms": room_count,
                    "room_pages": room_pages,
                    "compact": compact,
                },
            }
            return report
        finally:
            store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure paged messenger bootstrap with a large fixture.")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()
    report = run(args.count, args.iterations)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    passed = (
        report["pagination"]["friends"] == args.count
        and report["pagination"]["rooms"] == args.count
        and report["pagination"]["compact"]
        and report["first_load"]["gzip_bytes_max"] < 100 * 1024
        and report["first_load"]["p95_ms"] < 300
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
