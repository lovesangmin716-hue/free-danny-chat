"""Account ownership checks shared by chat and future social actors."""
from __future__ import annotations

from .utils import utc_now_iso


def resolve_actor(store, owner_username: str, identity_id: str = "", room_id: str = "") -> dict | None:
    with store.lock:
        owner = store._users_by_username.get(owner_username)
        if owner is None:
            return None
        account = store._accounts_by_id.get(owner["account_id"], {})
        if account.get("status", "active") != "active":
            return None
        target_id = identity_id or owner["id"]
        if store.repository is not None:
            target = store.repository.active_identity_by_id(owner["account_id"], target_id)
            if target is not None:
                cached = store._users_by_id.get(target_id)
                if cached is not None:
                    cached.update(target)
                else:
                    store.state["users"].append(target)
                    store._register_user_locked(target)
        else:
            target = store._users_by_id.get(target_id)
        if (target is None or target.get("account_id") != owner["account_id"]
                or target.get("disabled_at")):
            return None
        if room_id:
            room = store._rooms_by_id.get(room_id)
            if room is None or (not room.get("is_public") and target_id not in room.get("participant_ids", [])):
                return None
        return store._user_public(target)


def disable_identity(store, owner_username: str, identity_id: str) -> tuple[bool, str | None]:
    with store.lock:
        owner = store._users_by_username.get(owner_username)
        target = store._users_by_id.get(identity_id)
        if owner is None or target is None or target.get("account_id") != owner["account_id"]:
            return False, "forbidden"
        if owner["id"] == identity_id:
            return False, "switch_first"
        result = store.repository.disable_identity(owner["id"], identity_id, utc_now_iso())
        if result.get("error"):
            return False, result["error"]
        store._session_validation_cache.clear()
        for token in store._session_validation_versions:
            store._session_validation_versions[token] += 1
    store.refresh_from_repository()
    return True, None


def sqlite_disable_identity(repository, owner_id: str, target_id: str, disabled_at: str) -> dict:
    with repository.connection() as database:
        database.execute("BEGIN IMMEDIATE")
        owner_row = database.execute(
            "SELECT u.account_id FROM users u JOIN accounts a ON a.id=u.account_id "
            "WHERE u.id=? AND a.status='active' AND COALESCE(json_extract(u.data_json,'$.disabled_at'),'')=''",
            (owner_id,),
        ).fetchone()
        row = database.execute("SELECT account_id, data_json FROM users WHERE id=?", (target_id,)).fetchone()
        if owner_row is None or row is None or owner_row[0] != row[0]:
            return {"error": "forbidden"}
        if owner_id == target_id:
            return {"error": "switch_first"}
        target = repository.decode(row[1])
        target["disabled_at"] = target.get("disabled_at") or disabled_at
        database.execute("UPDATE users SET data_json=?, revision=revision+1 WHERE id=?", (repository.encode(target), target_id))
        database.execute("UPDATE sessions SET user_id=?, active_user_id=? WHERE account_id=? AND active_user_id=?",
                         (owner_id, owner_id, row[0], target_id))
        database.execute("DELETE FROM presence_leases WHERE username=?", (target["username"],))
    return {"disabled": True}


def sqlite_actor_active(database, user_id: str) -> bool:
    return database.execute(
        "SELECT 1 FROM users u JOIN accounts a ON a.id=u.account_id WHERE u.id=? "
        "AND a.status='active' AND COALESCE(json_extract(u.data_json,'$.disabled_at'),'')=''", (user_id,),
    ).fetchone() is not None


def event_for_viewer(store, username: str, event: dict) -> dict:
    """Recipient metadata is added only to the owner's private delivery copy."""
    with store.lock:
        owner = store._users_by_username.get(username)
        room = store._rooms_by_id.get(str(event.get("roomId", "")))
        identities = store._account_identities_locked(owner) if owner else []
        recipients = [identity["id"] for identity in identities
                      if room and (room.get("is_public") or identity["id"] in room.get("participant_ids", []))]
        actor_name = str(event.get("actorUsername") or event.get("message", {}).get("username") or event.get("username", ""))
        actor = store._users_by_username.get(actor_name)
        return {**event, "recipient_identity_ids": recipients, "actor_identity_id": actor["id"] if actor else ""}


def unread_summary(store, username: str) -> dict:
    with store.lock:
        owner = store._users_by_username.get(username)
        identities = store._account_identities_locked(owner) if owner else []
        room_maps = {identity["id"]: {
            room_id: identity["id"] for room_id in store._room_ids_by_user.get(identity["id"], set())
            if (room := store._rooms_by_id.get(room_id)) and not room.get("archived_at")
            and room.get("kind") in ("direct", "group", "ticket_listing", "ticket_deal")
            and store._can_access_room_locked(room, identity)
        } for identity in identities}
    counts = {identity_id: sum(store._unread_counts_for_rooms(rooms).values())
              for identity_id, rooms in room_maps.items()}
    return {"counts": counts, "total": sum(counts.values())}
