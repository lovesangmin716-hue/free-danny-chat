from __future__ import annotations

import queue
from ..identity import resolve_actor, disable_identity, event_for_viewer, unread_summary



class MessagingRoutesMixin:
    def serve_identity_unread(self, user: dict) -> None:
        self.send_json(unread_summary(self.context.STORE, user["username"]), self.context.HTTPStatus.OK)

    def serve_session(self) -> None:
        user = self.current_user()
        if user is None:
            self.send_json({"authenticated": False}, self.context.HTTPStatus.OK)
            return
        account_context = self.context.STORE.get_account_context(user["username"]) or {}
        self.send_json({"authenticated": True, "user": user, **account_context}, self.context.HTTPStatus.OK)

    def create_identity(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        if not self.allow_actor_request(user, "identity-create", 10, 3600):
            return
        identity, error = self.context.STORE.create_identity(
            user["username"],
            str(payload.get("username", "")),
            str(payload.get("displayName", "")),
            str(payload.get("friendCode", "")),
            str(payload.get("statusMessage", "")),
        )
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return
        account_context = self.context.STORE.get_account_context(user["username"]) or {}
        self.send_json({"identity": identity, **account_context}, self.context.HTTPStatus.CREATED)

    def switch_identity(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        identity_id = str(payload.get("identityId", "")).strip()
        token = self.read_session_token()
        username = self.context.SESSIONS.switch_identity(token, identity_id)
        if username is None:
            self.send_json({"error": "이 계정이 소유한 활동 ID가 아닙니다."}, self.context.HTTPStatus.FORBIDDEN)
            return
        active_user = self.context.STORE.get_user_public(username)
        if active_user is None:
            self.send_json({"error": "활동 ID를 찾을 수 없습니다."}, self.context.HTTPStatus.NOT_FOUND)
            return
        account_context = self.context.STORE.get_account_context(username) or {}
        self.send_json({"authenticated": True, "user": active_user, **account_context}, self.context.HTTPStatus.OK)

    def disable_activity_identity(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        if not self.allow_actor_request(user, "identity-disable", 10, 3600):
            return
        disabled, error = disable_identity(self.context.STORE, user["username"], str(payload.get("identityId", "")))
        if error:
            self.send_json({"error": "다른 ID로 전환한 뒤 비활성화해 주세요." if error == "switch_first"
                           else "사용할 수 없는 활동 ID입니다."}, self.context.HTTPStatus.CONFLICT
                           if error == "switch_first" else self.context.HTTPStatus.FORBIDDEN)
            return
        self.send_json({"disabled": disabled, **(self.context.STORE.get_account_context(user["username"]) or {})}, self.context.HTTPStatus.OK)

    def allow_actor_request(self, user: dict, scope: str, limit: int, seconds: int) -> bool:
        record = self.context.STORE.get_user_record(user["username"])
        if record is None:
            return False
        for key, budget in ((f"account:{record['account_id']}:{scope}", limit * 3),
                            (f"identity:{user['id']}:{scope}", limit)):
            allowed, retry_after = self.context.RATE_LIMITER.allow(key, budget, seconds)
            if not allowed:
                self.send_json({"error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."},
                               self.context.HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": str(retry_after)})
                return False
        return True

    def serve_profile_art_thumbnail(self, user_id: str) -> None:
        thumbnail = self.context.STORE.get_profile_art_thumbnail(user_id)
        if thumbnail is None:
            self.send_error(self.context.HTTPStatus.NOT_FOUND)
            return
        version, content = thumbnail
        self.send_response(self.context.HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("ETag", f'"profile-art-{user_id}-{version}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def serve_messages(self, query: dict[str, list[str]]) -> None:
        room_id = query.get("room_id", [""])[0]
        user = self.require_auth()
        if user is None:
            return
        limit_value = query.get("limit", [""])[0].strip()
        before = query.get("before", [""])[0].strip()
        around = query.get("around", [""])[0].strip()
        if limit_value or before or around:
            try:
                limit = int(limit_value or self.context.DEFAULT_MESSAGES_PAGE_SIZE)
            except ValueError:
                self.send_json({"error": "올바른 메시지 개수를 입력해 주세요."}, self.context.HTTPStatus.BAD_REQUEST)
                return
            if (
                not 1 <= limit <= self.context.MAX_MESSAGES_PAGE_SIZE
                or (before and not self.context.MESSAGE_ID_PATTERN.fullmatch(before))
                or (around and not self.context.MESSAGE_ID_PATTERN.fullmatch(around))
                or (before and around)
            ):
                self.send_json({"error": "올바른 메시지 커서를 입력해 주세요."}, self.context.HTTPStatus.BAD_REQUEST)
                return
            messages = self.context.STORE.get_messages_page(
                room_id,
                user["username"],
                limit=limit,
                before=before,
                around=around,
            ) if user else None
        else:
            messages = self.context.STORE.get_messages(room_id, user["username"]) if user else None
        if messages is None:
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, self.context.HTTPStatus.NOT_FOUND)
            return
        self.send_json(messages, self.context.HTTPStatus.OK)

    def serve_message_search(self, query: dict[str, list[str]], user: dict) -> None:
        search_query = query.get("q", [""])[0].strip()
        try:
            limit = int(query.get("limit", ["50"])[0])
        except (TypeError, ValueError):
            self.send_json({"error": "올바른 검색 개수를 입력해 주세요."}, self.context.HTTPStatus.BAD_REQUEST)
            return
        if not search_query or len(search_query) > 100 or not 1 <= limit <= 50:
            self.send_json({"error": "검색어는 1~100자로 입력해 주세요."}, self.context.HTTPStatus.BAD_REQUEST)
            return
        results = self.context.STORE.search_messages(user["username"], search_query, limit=limit)
        if results is None:
            self.send_json({"error": "사용자를 찾을 수 없습니다."}, self.context.HTTPStatus.NOT_FOUND)
            return
        self.send_json(results, self.context.HTTPStatus.OK)

    def serve_events(self, user: dict, query: dict[str, list[str]] | None = None) -> None:
        if not self.context.SSE_CONNECTION_SLOTS.acquire(blocking=False):
            self.context.SSE_METRICS.increment("rejected_total")
            self.send_json({"error": "실시간 연결이 혼잡합니다. 잠시 후 다시 시도해 주세요."}, self.context.HTTPStatus.SERVICE_UNAVAILABLE)
            return
        try:
            self.send_response(self.context.HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.context.SSE_CONNECTION_SLOTS.release()
            return

        subscriber: queue.Queue = self.context.queue.Queue(maxsize=self.context.MAX_SSE_QUEUE_SIZE)
        token = self.read_session_token()
        presence_token = f"{token}:{user['id']}"
        account_context = self.context.STORE.get_account_context(user["username"]) or {}
        identity_usernames = {
            str(identity.get("username", ""))
            for identity in account_context.get("identities", [])
            if identity.get("username")
        } or {user["username"]}
        with self.context.SUBSCRIBERS_LOCK:
            self.context.SUBSCRIBERS[subscriber] = user["username"]
            for identity_username in identity_usernames:
                self.context.SUBSCRIBERS_BY_USERNAME.setdefault(identity_username, set()).add(subscriber)
        self.context.SSE_METRICS.increment("active")
        self.context.SSE_METRICS.increment("accepted_total")

        after_value = str((query or {}).get("after", [""])[0])
        header_value = str(self.headers.get("Last-Event-ID", "") or "")
        try:
            last_sent_revision = max(0, int(header_value or after_value or "0"))
        except ValueError:
            last_sent_revision = 0
        if last_sent_revision:
            self.context.SSE_METRICS.increment("reconnects_total")

        presence_connected = False
        try:
            presence_changed = self.context.PRESENCE.connect(presence_token, user["username"])
            presence_connected = True
        except Exception:
            presence_changed = False
            self.context.SSE_METRICS.increment("event_consume_failures_total")
        if presence_changed:
            self.context.EVENT_BROKER.publish(
                {
                    "type": "presence_updated",
                    "username": user["username"],
                    "presence": self.context.PRESENCE.for_user(user["username"]),
                },
                self.context.STORE.presence_event_recipients(user["username"]),
            )

        try:
            hello = {"type": "hello", "timestamp": self.context.utc_now_iso(), "username": user["username"]}
            self.wfile.write(f"data: {self.context.json.dumps(hello, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()

            if last_sent_revision and self.context.EVENT_BROKER.replay_expired(last_sent_revision):
                last_sent_revision = self.context.EVENT_BROKER.latest_revision()
                reset_event = {
                    "type": "sync_required",
                    "reason": "event_history_expired",
                    "revision": last_sent_revision,
                }
                payload = self.context.json.dumps(reset_event, ensure_ascii=False)
                self.wfile.write(
                    f"id: {last_sent_revision}\ndata: {payload}\n\n".encode("utf-8")
                )
                self.wfile.flush()
            elif last_sent_revision:
                replay_events = {}
                for identity_username in identity_usernames:
                    for replayed in self.context.EVENT_BROKER.replay_batches(
                        identity_username, last_sent_revision, limit=500
                    ):
                        for event in replayed:
                            replay_events[int(event.get("revision", 0))] = event
                for revision, event in sorted(replay_events.items()):
                    if revision <= last_sent_revision:
                        continue
                    payload = self.context.json.dumps(event_for_viewer(self.context.STORE, user["username"], event), ensure_ascii=False)
                    self.wfile.write(f"id: {revision}\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_sent_revision = revision

            while True:
                session_username = self.context.SESSIONS.get_username(token)
                if not session_username or resolve_actor(self.context.STORE, session_username, user["id"]) is None:
                    break
                try:
                    event = subscriber.get(timeout=self.context.SSE_HEARTBEAT_SECONDS)
                except self.context.queue.Empty:
                    try:
                        self.context.PRESENCE.heartbeat(presence_token)
                    except Exception:
                        self.context.SSE_METRICS.increment("event_consume_failures_total")
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    self.context.SSE_METRICS.increment("heartbeats_total")
                    continue
                if event is None:
                    break
                revision = int(event.get("revision", 0))
                if revision and revision <= last_sent_revision:
                    continue
                event = event_for_viewer(self.context.STORE, user["username"], event)
                payload = f"data: {self.context.json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
                if revision:
                    payload = f"id: {revision}\n".encode("utf-8") + payload
                self.wfile.write(payload)
                self.wfile.flush()
                last_sent_revision = max(last_sent_revision, revision)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self.close_connection = True
            with self.context.SUBSCRIBERS_LOCK:
                self.context.SUBSCRIBERS.pop(subscriber, None)
                for identity_username in identity_usernames:
                    username_subscribers = self.context.SUBSCRIBERS_BY_USERNAME.get(identity_username)
                    if username_subscribers is not None:
                        username_subscribers.discard(subscriber)
                        if not username_subscribers:
                            self.context.SUBSCRIBERS_BY_USERNAME.pop(identity_username, None)
            username, went_offline = "", False
            if presence_connected:
                try:
                    username, went_offline = self.context.PRESENCE.disconnect(presence_token)
                except Exception:
                    self.context.SSE_METRICS.increment("event_consume_failures_total")
            if went_offline:
                self.context.EVENT_BROKER.publish(
                    {"type": "presence_updated", "username": username, "presence": self.context.PRESENCE.for_user(username)},
                    self.context.STORE.presence_event_recipients(username),
                )
            self.context.SSE_METRICS.increment("active", -1)
            self.context.SSE_METRICS.increment("disconnected_total")
            self.context.SSE_CONNECTION_SLOTS.release()

    def update_profile_pixels(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        pixels = payload.get("pixels")
        profile = self.context.STORE.update_profile_pixels(user["username"], pixels)
        if profile is None:
            self.send_json({"error": "프로필 픽셀 데이터가 올바르지 않습니다."}, self.context.HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"user": profile}, self.context.HTTPStatus.OK)

    def update_profile(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        display_name = str(payload.get("displayName", ""))
        status_message = str(payload.get("statusMessage", ""))
        friend_code = str(payload.get("friendCode", ""))
        if payload.get("statusEmojiOnly") and self.context.saved_activity_emoji(status_message) != status_message.strip():
            self.send_json({"error": "텍스트 없이 이모티콘 하나만 선택해 주세요."}, self.context.HTTPStatus.BAD_REQUEST)
            return
        profile, error = self.context.STORE.update_profile(user["username"], display_name, status_message, friend_code, payload.get("pixels"))
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"user": profile}, self.context.HTTPStatus.OK)

    def update_custom_palette(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        profile, error = self.context.STORE.update_custom_palette(user["username"], payload.get("colors"))
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"user": profile}, self.context.HTTPStatus.OK)

    def create_group_room(self, user: dict) -> None:
        if not self.allow_request(f"group-room:{user['username']}", 20, 60 * 60):
            return
        self.run_json_command(lambda payload: self.context.APPLICATION.create_group_room(user, payload))

    def update_group_room_settings(self, user: dict) -> None:
        if not self.allow_request(f"room-settings:{user['username']}", 60, 60 * 60):
            return
        self.run_json_command(lambda payload: self.context.APPLICATION.update_group_room(user, payload))

    def leave_room(self, user: dict) -> None:
        if not self.allow_request(f"leave-room:{user['username']}", 60, 60 * 60):
            return
        self.run_json_command(lambda payload: self.context.APPLICATION.leave_room(user, payload))

    def add_friend(self, user: dict) -> None:
        self.run_json_command(lambda payload: self.context.APPLICATION.add_friend(user, payload))

    def create_direct_room(self, user: dict) -> None:
        self.run_json_command(lambda payload: self.context.APPLICATION.create_direct_room(user, payload))

    def create_message(self, user: dict) -> None:
        self.run_json_command(
            lambda payload: self.context.APPLICATION.create_message(user, payload, self.message_attachment),
            actor_limit=(user, "message-create", 120, 60),
        )

    def edit_message(self, user: dict) -> None:
        self.run_json_command(lambda payload: self.context.APPLICATION.edit_message(user, payload),
                              actor_limit=(user, "message-edit", 60, 60))

    def toggle_message_reaction(self, user: dict) -> None:
        self.run_json_command(lambda payload: self.context.APPLICATION.toggle_message_reaction(user, payload),
                              actor_limit=(user, "message-reaction", 180, 60))

    def delete_message(self, user: dict) -> None:
        self.run_json_command(lambda payload: self.context.APPLICATION.delete_message(user, payload),
                              actor_limit=(user, "message-delete", 60, 60))

    def mark_room_read(self, user: dict) -> None:
        self.run_json_command(lambda payload: self.context.APPLICATION.mark_room_read(user, payload))

    def update_presence(self, user: dict) -> None:
        self.run_json_command(
            lambda payload: self.context.APPLICATION.update_presence(f"{self.read_session_token()}:{user['id']}", user, payload)
        )

    def current_user(self) -> dict | None:
        token = self.read_session_token()
        username = self.context.SESSIONS.get_username(token)
        if username is None:
            return None
        self._safe_user_id = self.context.safe_user_identifier(username)
        return self.context.STORE.get_user_public(username)

    def require_auth(self) -> dict | None:
        user = self.current_user()
        if user is None:
            self.send_json({"error": "로그인이 필요합니다."}, self.context.HTTPStatus.UNAUTHORIZED)
            return None
        self._actor_owner_username = user["username"]
        query = self.context.parse_qs(self.context.urlparse(self.path).query)
        identity_id = str(self.headers.get("X-Acting-Identity", "") or query.get("acting_identity_id", [""])[0])
        actor = resolve_actor(self.context.STORE, user["username"], identity_id,
                                (query.get("roomId", [""])[0] or query.get("room_id", [""])[0]) if identity_id else "")
        if actor is None:
            self.send_json({"error": "사용할 수 없는 활동 ID입니다."}, self.context.HTTPStatus.FORBIDDEN)
            return None
        self._request_actor = actor
        self._request_actor_explicit = bool(identity_id)
        return actor

    def require_auth_record(self) -> dict | None:
        public_user = self.require_auth()
        if public_user is None:
            return None
        user = self.context.STORE.get_user_record(public_user["username"])
        if user is None:
            self.send_json({"error": "사용자를 찾을 수 없습니다."}, self.context.HTTPStatus.UNAUTHORIZED)
            return None
        self._request_actor = user
        return user

    def bind_payload_actor(self, payload: dict) -> bool:
        actor = getattr(self, "_request_actor", None)
        if actor is None:
            return True
        identity_id = str(payload.get("acting_identity_id", ""))
        if identity_id and self._request_actor_explicit and identity_id != actor["id"]:
            self.send_json({"error": "활동 ID가 요청과 일치하지 않습니다."}, self.context.HTTPStatus.FORBIDDEN)
            return False
        explicit = bool(identity_id or self._request_actor_explicit)
        if not explicit:
            return True
        target = resolve_actor(self.context.STORE, self._actor_owner_username, identity_id or actor["id"],
                               str(payload.get("roomId", "") or payload.get("activeRoomId", "")))
        if target is None:
            self.send_json({"error": "이 활동 ID로 요청을 처리할 수 없습니다."}, self.context.HTTPStatus.FORBIDDEN)
            return False
        replacement = self.context.STORE.get_user_record(target["username"]) if "account_id" in actor else target
        actor.clear()
        actor.update(replacement)
        return True
