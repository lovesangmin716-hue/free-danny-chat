from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

from .config import (
    CLIENT_MESSAGE_ID_PATTERN,
    MAX_GROUP_PARTICIPANTS,
    MESSAGE_ID_PATTERN,
    ROOM_ID_PATTERN,
    USER_ID_PATTERN,
)
from .runtime import PresenceStore
from .state import StateStore
from .utils import saved_activity_emoji


class CommandFailure(Exception):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class CommandOutcome:
    def __init__(
        self,
        data: object,
        status: HTTPStatus = HTTPStatus.OK,
        events: list[tuple[dict, set[str]]] | None = None,
    ) -> None:
        self.data = data
        self.status = status
        self.events = events or []


class ApplicationServices:
    """Feature commands return data and domain events without writing HTTP responses."""

    def __init__(
        self,
        store: StateStore,
        presence: PresenceStore,
        upload_grants=None,
    ) -> None:
        self.store = store
        self.presence = presence
        self.upload_grants = upload_grants

    def add_friend(self, user: dict, payload: dict) -> CommandOutcome:
        friend_code = str(payload.get("friendCode", "")).strip()
        friend, error = self.store.add_friend_by_code(user["username"], friend_code)
        if error or friend is None:
            raise CommandFailure(error or "친구를 추가하지 못했습니다.")
        return CommandOutcome(
            {"friend": friend},
            HTTPStatus.CREATED,
            [({"type": "friends_updated"}, {user["username"], friend["username"]})],
        )

    def create_direct_room(self, user: dict, payload: dict) -> CommandOutcome:
        friend_user_id = str(payload.get("userId", "")).strip()
        room, created, error = self.store.create_or_get_direct_room(user["username"], friend_user_id)
        if error or room is None:
            raise CommandFailure(error or "채팅방을 만들지 못했습니다.")
        events = []
        if created:
            events = [
                ({"type": "room_created", "room": summary}, {recipient})
                for recipient, summary in self.store.room_event_summaries(room["id"]).items()
            ]
        return CommandOutcome(
            {"room": room, "created": created},
            HTTPStatus.CREATED if created else HTTPStatus.OK,
            events,
        )

    def create_group_room(self, user: dict, payload: dict) -> CommandOutcome:
        name = str(payload.get("name", "")).strip()
        raw_member_user_ids = payload.get("memberUserIds")
        if not isinstance(raw_member_user_ids, list) or len(raw_member_user_ids) > MAX_GROUP_PARTICIPANTS - 1:
            raise CommandFailure("올바른 그룹 멤버를 선택해 주세요.")
        member_user_ids: list[str] = []
        for member_user_id in raw_member_user_ids:
            if not isinstance(member_user_id, str) or not USER_ID_PATTERN.fullmatch(member_user_id):
                raise CommandFailure("올바른 그룹 멤버를 선택해 주세요.")
            member_user_ids.append(member_user_id)
        room, error = self.store.create_group_room(user["username"], name, member_user_ids)
        if error or room is None:
            raise CommandFailure(error or "그룹 채팅방을 만들지 못했습니다.")
        events = [
            ({"type": "room_created", "room": summary}, {recipient})
            for recipient, summary in self.store.room_event_summaries(room["id"]).items()
        ]
        return CommandOutcome({"room": room}, HTTPStatus.CREATED, events)

    def update_group_room(self, user: dict, payload: dict) -> CommandOutcome:
        room_id = str(payload.get("roomId", "")).strip()
        name = str(payload.get("name", "")).strip()
        if not ROOM_ID_PATTERN.fullmatch(room_id) or not 1 <= len(name) <= 32:
            raise CommandFailure("채팅방 이름은 1~32자로 입력해 주세요.")
        room, error = self.store.update_group_room_name(user["username"], room_id, name)
        if error == "not_found":
            raise CommandFailure("채팅방을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
        if error == "forbidden":
            raise CommandFailure("방장만 채팅방 정보를 변경할 수 있습니다.", HTTPStatus.FORBIDDEN)
        if error or room is None:
            raise CommandFailure("채팅방 이름을 변경하지 못했습니다.")
        event = {"type": "room_updated", "roomId": room_id, "room": room}
        return CommandOutcome({"room": room}, events=[(event, self.store.room_event_recipients(room_id))])

    def leave_group_room(self, user: dict, payload: dict) -> CommandOutcome:
        room_id = str(payload.get("roomId", "")).strip()
        if not ROOM_ID_PATTERN.fullmatch(room_id):
            raise CommandFailure("올바른 채팅방을 선택해 주세요.")
        room, recipients, error = self.store.leave_group_room(user["username"], room_id)
        if error:
            raise CommandFailure("채팅방을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
        event = {
            "type": "room_left",
            "roomId": room_id,
            "username": user["username"],
            "room": room,
        }
        return CommandOutcome({"left": True, "roomId": room_id}, events=[(event, recipients)])

    def create_message(
        self,
        user: dict,
        payload: dict,
        attachment_resolver,
    ) -> CommandOutcome:
        room_id = str(payload.get("roomId", "")).strip()
        text = str(payload.get("text", "")).strip()
        client_message_id = str(payload.get("clientMessageId", "")).strip()
        if client_message_id and not CLIENT_MESSAGE_ID_PATTERN.fullmatch(client_message_id):
            raise CommandFailure("올바른 메시지 식별자가 아닙니다.")
        attachment = attachment_resolver(payload.get("attachment"), user["username"])
        if not room_id or (not text and attachment is None):
            raise CommandFailure("roomId와 text는 필수입니다.")
        try:
            result = self.store.add_message(room_id, user["username"], text, attachment, client_message_id)
        except ValueError as error:
            raise CommandFailure(
                "같은 메시지 식별자를 다른 내용에 다시 사용할 수 없습니다.",
                HTTPStatus.CONFLICT,
            ) from error
        if result is None:
            raise CommandFailure("채팅방을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
        message, room, created = result
        visible_message = self.store.sent_message_with_read_state(
            room_id,
            user["username"],
            message,
            created=created,
        )
        upload_grants = self.upload_grants() if callable(self.upload_grants) else self.upload_grants
        if attachment is not None and created and upload_grants is not None:
            upload_grants.consume(Path(attachment["url"]).name, user["username"])
        if not created:
            return CommandOutcome(visible_message)
        sender_public = self.store.get_user_public(message["username"]) or {}
        event = {
            "type": "message_created",
            "roomId": room_id,
            "room": room,
            "message": message,
            "sender": {
                key: sender_public[key]
                for key in (
                    "id", "username", "display_name", "status_message",
                    "profile_image_url", "profile_thumbnail_url", "profile_art_version",
                )
                if key in sender_public
            },
        }
        return CommandOutcome(
            visible_message,
            HTTPStatus.CREATED,
            [(event, self.store.room_event_recipients(room_id))],
        )

    def delete_message(self, user: dict, payload: dict) -> CommandOutcome:
        room_id = str(payload.get("roomId", "")).strip()
        message_id = str(payload.get("messageId", "")).strip()
        if not ROOM_ID_PATTERN.fullmatch(room_id) or not MESSAGE_ID_PATTERN.fullmatch(message_id):
            raise CommandFailure("올바른 메시지를 선택해 주세요.")
        message, room, error = self.store.delete_message(room_id, user["username"], message_id)
        if error == "forbidden":
            raise CommandFailure("본인이 보낸 메시지만 지울 수 있습니다.", HTTPStatus.FORBIDDEN)
        if error or message is None or room is None:
            raise CommandFailure("메시지를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
        event = {
            "type": "message_deleted",
            "roomId": room_id,
            "messageId": message_id,
            "room": room,
        }
        return CommandOutcome(
            {"deleted": True, "roomId": room_id, "messageId": message_id, "room": room},
            events=[(event, self.store.room_event_recipients(room_id))],
        )

    def mark_room_read(self, user: dict, payload: dict) -> CommandOutcome:
        room_id = str(payload.get("roomId", "")).strip()
        room, changed = self.store.mark_room_read(room_id, user["username"])
        if room is None:
            raise CommandFailure("채팅방을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
        events = []
        if changed:
            event = {
                "type": "room_read",
                "roomId": room_id,
                "username": user["username"],
                "roomKind": room.get("kind", "direct"),
            }
            events.append((event, self.store.room_event_recipients(room_id)))
        return CommandOutcome({"room": room}, events=events)

    def update_presence(self, session_token: str | None, user: dict, payload: dict) -> CommandOutcome:
        active_room_id = str(payload.get("activeRoomId", "")).strip()[:80]
        emoji = saved_activity_emoji(payload.get("emoji"))
        changed = self.presence.update(session_token, user["username"], active_room_id, emoji)
        current = self.presence.for_user(user["username"])
        events = []
        if changed:
            event = {"type": "presence_updated", "username": user["username"], "presence": current}
            events.append((event, self.store.presence_event_recipients(user["username"])))
        return CommandOutcome({"presence": current}, events=events)
