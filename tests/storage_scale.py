from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path


APP_DIR = Path(__file__).parents[1] / "outputs" / "chat-app"
sys.path.insert(0, str(APP_DIR))
from persistence import NormalizedSqliteRepository


def rss_bytes() -> int:
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong), ("peak", ctypes.c_size_t), ("working", ctypes.c_size_t)] + [(f"x{i}", ctypes.c_size_t) for i in range(7)]

            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            get_process = ctypes.windll.kernel32.GetCurrentProcess
            get_process.restype = wintypes.HANDLE
            get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
            get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            get_memory.restype = wintypes.BOOL
            if get_memory(get_process(), ctypes.byref(counters), counters.cb):
                return int(counters.working)
        resident = Path("/proc/self/statm")
        if resident.exists():
            return int(resident.read_text().split()[1]) * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        pass
    return 0


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * quantile))]


def seed(database_path: Path, users: int, rooms: int, messages_per_room: int) -> None:
    repository = NormalizedSqliteRepository(database_path)
    with repository.connection() as database:
        database.execute("BEGIN IMMEDIATE")
        user_rows = []
        for index in range(users):
            user = {"id": f"user_{index:08d}", "username": f"user{index:08d}", "friend_code": f"scale{index:08d}"}
            user_rows.append((user["id"], user["username"], user["friend_code"], json.dumps(user, separators=(",", ":"))))
        database.executemany("INSERT OR REPLACE INTO users(id, username, friend_code, data_json) VALUES(?, ?, ?, ?)", user_rows)
        for room_index in range(rooms):
            room_id = f"room_{room_index:08d}"
            sender_index = room_index % users
            sender_id = f"user_{sender_index:08d}"
            sender_name = f"user{sender_index:08d}"
            room = {"id": room_id, "kind": "direct", "created_by": sender_name, "updated_at": "2026-01-01T00:00:00+00:00", "participant_ids": [sender_id]}
            database.execute(
                "INSERT OR REPLACE INTO rooms(id, kind, created_by, updated_at, data_json) VALUES(?, ?, ?, ?, ?)",
                (room_id, "direct", sender_name, room["updated_at"], json.dumps(room, separators=(",", ":"))),
            )
            rows = []
            for message_index in range(messages_per_room):
                message_id = f"msg_{room_index:08d}_{message_index:04d}"
                message = {"id": message_id, "room_id": room_id, "username": sender_name, "text": "scale", "timestamp": f"2026-01-01T00:00:{message_index % 60:02d}+00:00"}
                rows.append((message_id, room_id, sender_id, sender_name, None, message["timestamp"], json.dumps(message, separators=(",", ":"))))
            database.executemany(
                "INSERT OR REPLACE INTO messages(id, room_id, sender_id, sender_username, client_message_id, created_at, data_json) VALUES(?, ?, ?, ?, ?, ?, ?)",
                rows,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and measure normalized Colorless storage.")
    parser.add_argument("database", type=Path)
    parser.add_argument("--users", type=int, default=1000)
    parser.add_argument("--rooms", type=int, default=1000)
    parser.add_argument("--messages-per-room", type=int, default=200)
    parser.add_argument("--writes", type=int, default=100)
    parser.add_argument("--skip-seed", action="store_true")
    args = parser.parse_args()
    seed_started = time.perf_counter()
    if not args.skip_seed:
        seed(args.database, args.users, args.rooms, args.messages_per_room)
    seed_seconds = time.perf_counter() - seed_started

    ready_started = time.perf_counter()
    repository = NormalizedSqliteRepository(args.database)
    ready_ms = (time.perf_counter() - ready_started) * 1000
    room_id = "room_00000000"
    sender_id = "user_00000000"
    room = {"id": room_id, "kind": "direct", "created_by": "user00000000", "updated_at": "2026-01-02T00:00:00+00:00", "participant_ids": [sender_id]}
    latencies = []
    for index in range(args.writes):
        message = {"id": f"msg_probe_{time.time_ns()}_{index}", "room_id": room_id, "username": "user00000000", "text": "probe", "timestamp": f"2026-01-02T00:00:{index % 60:02d}+00:00"}
        started = time.perf_counter()
        if not repository.insert_message(message, sender_id, room, 200):
            raise RuntimeError("probe insert failed")
        latencies.append((time.perf_counter() - started) * 1000)

    counts = repository.verify()
    report = {
        "fixture": {"users": args.users, "rooms": args.rooms, "messages_per_room": args.messages_per_room},
        "seed_seconds": round(seed_seconds, 3),
        "ready_ms": round(ready_ms, 3),
        "rss_bytes": rss_bytes(),
        "message_write_ms": {
            "p50": round(statistics.median(latencies), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "p99": round(percentile(latencies, 0.99), 3),
        },
        "counts": counts,
    }
    print(json.dumps(report, indent=2))
    return 0 if ready_ms < 10_000 and report["message_write_ms"]["p95"] < 200 and counts["foreign_key_errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
