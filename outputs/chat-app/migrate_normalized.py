from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from persistence import NormalizedSqliteRepository


def load_legacy_parts(database_path: Path) -> dict:
    database = sqlite3.connect(database_path)
    try:
        rows = database.execute("SELECT id, state_json FROM state_parts").fetchall()
    finally:
        database.close()
    parts = {str(part_id): json.loads(state_json) for part_id, state_json in rows}
    required = {"users", "friendships", "rooms", "sessions"}
    if not required.issubset(parts):
        raise RuntimeError(f"legacy state_parts is incomplete: missing {sorted(required - parts.keys())}")
    state = {
        "users": parts["users"],
        "friendships": parts["friendships"],
        "rooms": parts["rooms"],
        "sessions": parts["sessions"],
        "messages": {},
        "shorts_feeds": {},
    }
    for part_id, value in parts.items():
        if part_id.startswith("messages:"):
            state["messages"][part_id.removeprefix("messages:")] = value
        elif part_id.startswith("shorts:"):
            state["shorts_feeds"][part_id.removeprefix("shorts:")] = value
    return state


def backup_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    current = sqlite3.connect(source)
    backup = sqlite3.connect(destination)
    try:
        current.backup(backup)
    finally:
        backup.close()
        current.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate Colorless state_parts into normalized SQLite rows.")
    parser.add_argument("database", type=Path, help="Path to chat_state.json.sqlite3")
    parser.add_argument("--verify-only", action="store_true", help="Only print normalized counts and FK errors.")
    parser.add_argument("--backup", type=Path, help="Backup path (default: DATABASE.pre-normalized.bak)")
    args = parser.parse_args()

    repository = NormalizedSqliteRepository(args.database)
    if args.verify_only:
        print(json.dumps(repository.verify(), ensure_ascii=False, indent=2))
        return 0 if repository.verify()["foreign_key_errors"] == 0 else 1

    backup_path = args.backup or args.database.with_suffix(f"{args.database.suffix}.pre-normalized.bak")
    backup_database(args.database, backup_path)
    state = load_legacy_parts(args.database)
    counts = repository.import_legacy_state(state)
    expected_messages = sum(len(messages) for messages in state["messages"].values())
    expected = {
        "users": len(state["users"]),
        "friendships": len(state["friendships"]),
        "rooms": len(state["rooms"]),
        "messages": expected_messages,
    }
    mismatches = {
        key: {"expected": value, "actual": counts[key]}
        for key, value in expected.items()
        if counts[key] != value
    }
    print(json.dumps({"backup": str(backup_path), "counts": counts, "mismatches": mismatches}, ensure_ascii=False, indent=2))
    return 1 if mismatches or counts["foreign_key_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
