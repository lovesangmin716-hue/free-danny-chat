from __future__ import annotations


class UploadRoutesMixin:
    def serve_upload(self, request_path: str, user: dict, *, head_only: bool = False) -> None:
        filename = self.context.Path(self.context.unquote(request_path.removeprefix("/uploads/"))).name
        if not filename or self.context.Path(filename).suffix.lower() not in self.context.ATTACHMENT_TYPES.values():
            self.send_error(self.context.HTTPStatus.NOT_FOUND, "Not found")
            return
        if not self.context.STORE.can_access_attachment(filename, user["username"]):
            self.send_error(self.context.HTTPStatus.NOT_FOUND, "Not found")
            return

        content_type = next(
            (mime_type for mime_type, extension in self.context.ATTACHMENT_TYPES.items() if extension == self.context.Path(filename).suffix.lower()),
            self.context.mimetypes.guess_type(filename)[0] or "application/octet-stream",
        )

        if self.context.SUPABASE_ENABLED:
            try:
                signed_url = self.context.supabase_signed_download_url(filename)
            except (ConnectionError, ValueError):
                self.send_error(self.context.HTTPStatus.BAD_GATEWAY, "Unable to authorize download")
                return
            self.send_response(self.context.HTTPStatus.TEMPORARY_REDIRECT)
            self.send_header("Location", signed_url)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "private, no-store")
            self.end_headers()
            return

        try:
            upload_path = (self.context.UPLOADS_DIR / filename).resolve()
            upload_path.relative_to(self.context.UPLOADS_DIR.resolve())
            file_size = upload_path.stat().st_size
            start, end = self.local_byte_range(file_size)
            if start is None or end is None:
                return
            partial = bool(self.headers.get("Range", "").strip())
            self.send_response(self.context.HTTPStatus.PARTIAL_CONTENT if partial else self.context.HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            self.send_header("Content-Security-Policy", "sandbox")
            self.end_headers()
            if head_only:
                return
            with upload_path.open("rb") as upload_file:
                upload_file.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = upload_file.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (FileNotFoundError, ValueError):
            self.send_error(self.context.HTTPStatus.NOT_FOUND, "Not found")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def local_byte_range(self, file_size: int) -> tuple[int | None, int | None]:
        range_header = self.headers.get("Range", "").strip()
        if not range_header:
            return 0, file_size - 1
        match = self.context.re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        if match is None or (not match.group(1) and not match.group(2)):
            self.send_range_not_satisfiable(file_size)
            return None, None
        if match.group(1):
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
        else:
            suffix_size = int(match.group(2))
            if suffix_size <= 0:
                self.send_range_not_satisfiable(file_size)
                return None, None
            start = max(0, file_size - suffix_size)
            end = file_size - 1
        if file_size <= 0 or start >= file_size or end < start:
            self.send_range_not_satisfiable(file_size)
            return None, None
        return start, min(end, file_size - 1)

    def send_range_not_satisfiable(self, file_size: int) -> None:
        self.send_response(self.context.HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
        self.send_header("Content-Range", f"bytes */{file_size}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def upload_profile_image(self, user: dict) -> None:
        if not self.allow_request(f"profile-image:{user['username']}", 12, 60 * 60):
            return
        content = self.read_request_body(self.context.MAX_PROFILE_IMAGE_BYTES + self.context.MAX_PROFILE_THUMBNAIL_BYTES + 4)
        if content is None:
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        image_content = content
        thumbnail_content = b""
        if content_type == "application/x-colorless-profile-bundle" and len(content) >= 4:
            image_size = int.from_bytes(content[:4], "big")
            image_content = content[4:4 + image_size]
            thumbnail_content = content[4 + image_size:]
        elif content_type != "image/webp":
            image_content = b""

        is_valid_image = (
            0 < len(image_content) <= self.context.MAX_PROFILE_IMAGE_BYTES
            and self.context.webp_dimensions(image_content) == (self.context.PROFILE_IMAGE_SIDE, self.context.PROFILE_IMAGE_SIDE)
        )
        is_valid_thumbnail = (
            not thumbnail_content
            or (
                len(thumbnail_content) <= self.context.MAX_PROFILE_THUMBNAIL_BYTES
                and self.context.webp_dimensions(thumbnail_content) == (self.context.PROFILE_THUMBNAIL_SIDE, self.context.PROFILE_THUMBNAIL_SIDE)
            )
        )
        if not is_valid_image or not is_valid_thumbnail:
            self.send_json(
                {"error": "프로필 사진은 1024×1024 WebP 형식이어야 합니다."},
                self.context.HTTPStatus.BAD_REQUEST,
            )
            return

        filename = self.context.profile_image_filename(user["id"])
        thumbnail_filename = self.context.profile_image_filename(user["id"], thumbnail=True)
        try:
            if self.context.SUPABASE_ENABLED:
                headers = self.context.supabase_headers("image/webp")
                headers["x-upsert"] = "true"
                self.context.fetch_json(
                    self.context.supabase_object_url(filename),
                    method="POST",
                    headers=headers,
                    data=image_content,
                )
                if thumbnail_content:
                    self.context.fetch_json(
                        self.context.supabase_object_url(thumbnail_filename),
                        method="POST",
                        headers=headers,
                        data=thumbnail_content,
                    )
            else:
                self.context.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
                upload_path = (self.context.UPLOADS_DIR / filename).resolve()
                upload_path.relative_to(self.context.UPLOADS_DIR.resolve())
                temp_path = (self.context.UPLOADS_DIR / f".{filename}.{self.context.uuid.uuid4().hex}.tmp").resolve()
                temp_path.relative_to(self.context.UPLOADS_DIR.resolve())
                try:
                    temp_path.write_bytes(image_content)
                    temp_path.replace(upload_path)
                finally:
                    temp_path.unlink(missing_ok=True)
                if thumbnail_content:
                    thumbnail_path = (self.context.UPLOADS_DIR / thumbnail_filename).resolve()
                    thumbnail_path.relative_to(self.context.UPLOADS_DIR.resolve())
                    thumbnail_temp_path = (self.context.UPLOADS_DIR / f".{thumbnail_filename}.{self.context.uuid.uuid4().hex}.tmp").resolve()
                    thumbnail_temp_path.relative_to(self.context.UPLOADS_DIR.resolve())
                    try:
                        thumbnail_temp_path.write_bytes(thumbnail_content)
                        thumbnail_temp_path.replace(thumbnail_path)
                    finally:
                        thumbnail_temp_path.unlink(missing_ok=True)
        except (ConnectionError, OSError, ValueError):
            self.send_json({"error": "프로필 사진을 저장하지 못했어요."}, self.context.HTTPStatus.BAD_GATEWAY)
            return

        profile = self.context.STORE.update_profile_image(
            user["username"],
            f"/uploads/{filename}",
            f"/uploads/{thumbnail_filename}" if thumbnail_content else "",
        )
        if profile is None:
            self.send_json({"error": "사용자를 찾을 수 없습니다."}, self.context.HTTPStatus.NOT_FOUND)
            return
        self.send_json({"user": profile}, self.context.HTTPStatus.OK)

    def remove_profile_image(self, user: dict) -> None:
        if not self.allow_request(f"profile-image:{user['username']}", 12, 60 * 60):
            return
        profile = self.context.STORE.update_profile_image(user["username"], "")
        if profile is None:
            self.send_json({"error": "사용자를 찾을 수 없습니다."}, self.context.HTTPStatus.NOT_FOUND)
            return

        filename = self.context.profile_image_filename(user["id"])
        thumbnail_filename = self.context.profile_image_filename(user["id"], thumbnail=True)
        try:
            if self.context.SUPABASE_ENABLED:
                for stored_filename in (filename, thumbnail_filename):
                    self.context.fetch_bytes(
                        self.context.supabase_object_url(stored_filename),
                        method="DELETE",
                        headers=self.context.supabase_headers(),
                    )
            else:
                for stored_filename in (filename, thumbnail_filename):
                    upload_path = (self.context.UPLOADS_DIR / stored_filename).resolve()
                    upload_path.relative_to(self.context.UPLOADS_DIR.resolve())
                    upload_path.unlink(missing_ok=True)
        except (ConnectionError, OSError, ValueError):
            pass
        self.send_json({"user": profile}, self.context.HTTPStatus.OK)

    def upload_group_room_image(self, user: dict) -> None:
        room_id = self.headers.get("X-Room-Id", "").strip()
        if not self.context.ROOM_ID_PATTERN.fullmatch(room_id):
            self.send_json({"error": "올바른 채팅방을 선택해 주세요."}, self.context.HTTPStatus.BAD_REQUEST)
            return
        access = self.context.STORE.group_room_access(user["username"], room_id)
        if access == "not_found":
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, self.context.HTTPStatus.NOT_FOUND)
            return
        if access != "owner":
            self.send_json({"error": "방장만 채팅방 사진을 변경할 수 있습니다."}, self.context.HTTPStatus.FORBIDDEN)
            return
        if not self.allow_request(f"room-image:{user['username']}", 20, 60 * 60):
            return

        content = self.read_request_body(self.context.MAX_PROFILE_IMAGE_BYTES + self.context.MAX_PROFILE_THUMBNAIL_BYTES + 4)
        if content is None:
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        image_content = b""
        thumbnail_content = b""
        if content_type == "application/x-colorless-room-bundle" and len(content) >= 4:
            image_size = int.from_bytes(content[:4], "big")
            image_content = content[4:4 + image_size]
            thumbnail_content = content[4 + image_size:]
        if (
            not 0 < len(image_content) <= self.context.MAX_PROFILE_IMAGE_BYTES
            or self.context.webp_dimensions(image_content) != (self.context.PROFILE_IMAGE_SIDE, self.context.PROFILE_IMAGE_SIDE)
            or not 0 < len(thumbnail_content) <= self.context.MAX_PROFILE_THUMBNAIL_BYTES
            or self.context.webp_dimensions(thumbnail_content) != (self.context.PROFILE_THUMBNAIL_SIDE, self.context.PROFILE_THUMBNAIL_SIDE)
        ):
            self.send_json({"error": "채팅방 사진은 1024×1024 WebP 형식이어야 합니다."}, self.context.HTTPStatus.BAD_REQUEST)
            return

        filename = self.context.room_image_filename(room_id)
        thumbnail_filename = self.context.room_image_filename(room_id, thumbnail=True)
        try:
            if self.context.SUPABASE_ENABLED:
                headers = self.context.supabase_headers("image/webp")
                headers["x-upsert"] = "true"
                for stored_filename, stored_content in (
                    (filename, image_content),
                    (thumbnail_filename, thumbnail_content),
                ):
                    self.context.fetch_json(
                        self.context.supabase_object_url(stored_filename),
                        method="POST",
                        headers=headers,
                        data=stored_content,
                    )
            else:
                self.context.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
                for stored_filename, stored_content in (
                    (filename, image_content),
                    (thumbnail_filename, thumbnail_content),
                ):
                    upload_path = (self.context.UPLOADS_DIR / stored_filename).resolve()
                    upload_path.relative_to(self.context.UPLOADS_DIR.resolve())
                    temp_path = (self.context.UPLOADS_DIR / f".{stored_filename}.{self.context.uuid.uuid4().hex}.tmp").resolve()
                    temp_path.relative_to(self.context.UPLOADS_DIR.resolve())
                    try:
                        temp_path.write_bytes(stored_content)
                        temp_path.replace(upload_path)
                    finally:
                        temp_path.unlink(missing_ok=True)
        except (ConnectionError, OSError, ValueError):
            self.send_json({"error": "채팅방 사진을 저장하지 못했습니다."}, self.context.HTTPStatus.BAD_GATEWAY)
            return

        room, error = self.context.STORE.update_group_room_image(
            user["username"],
            room_id,
            f"/uploads/{filename}",
            f"/uploads/{thumbnail_filename}",
        )
        if error or room is None:
            status = self.context.HTTPStatus.FORBIDDEN if error == "forbidden" else self.context.HTTPStatus.NOT_FOUND
            self.send_json({"error": "채팅방 사진을 변경할 수 없습니다."}, status)
            return
        try:
            self.send_json({"room": room}, self.context.HTTPStatus.OK)
        finally:
            self.context.EVENT_BROKER.publish(
                {"type": "room_updated", "roomId": room_id, "room": room},
                self.context.STORE.room_event_recipients(room_id),
            )

    def remove_group_room_image(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        room_id = str(payload.get("roomId", "")).strip()
        if not self.context.ROOM_ID_PATTERN.fullmatch(room_id):
            self.send_json({"error": "올바른 채팅방을 선택해 주세요."}, self.context.HTTPStatus.BAD_REQUEST)
            return
        if not self.allow_request(f"room-image:{user['username']}", 20, 60 * 60):
            return
        room, error = self.context.STORE.update_group_room_image(user["username"], room_id, "")
        if error == "not_found":
            self.send_json({"error": "채팅방을 찾을 수 없습니다."}, self.context.HTTPStatus.NOT_FOUND)
            return
        if error == "forbidden":
            self.send_json({"error": "방장만 채팅방 사진을 변경할 수 있습니다."}, self.context.HTTPStatus.FORBIDDEN)
            return
        if error or room is None:
            self.send_json({"error": "채팅방 사진을 삭제하지 못했습니다."}, self.context.HTTPStatus.BAD_REQUEST)
            return

        try:
            if self.context.SUPABASE_ENABLED:
                for stored_filename in (self.context.room_image_filename(room_id), self.context.room_image_filename(room_id, True)):
                    self.context.fetch_bytes(
                        self.context.supabase_object_url(stored_filename),
                        method="DELETE",
                        headers=self.context.supabase_headers(),
                    )
            else:
                for stored_filename in (self.context.room_image_filename(room_id), self.context.room_image_filename(room_id, True)):
                    upload_path = (self.context.UPLOADS_DIR / stored_filename).resolve()
                    upload_path.relative_to(self.context.UPLOADS_DIR.resolve())
                    upload_path.unlink(missing_ok=True)
        except (ConnectionError, OSError, ValueError):
            pass
        try:
            self.send_json({"room": room}, self.context.HTTPStatus.OK)
        finally:
            self.context.EVENT_BROKER.publish(
                {"type": "room_updated", "roomId": room_id, "room": room},
                self.context.STORE.room_event_recipients(room_id),
            )

    def upload_attachment(self, user: dict) -> None:
        self.close_connection = True
        self.send_json(
            {"error": "This upload endpoint was replaced by the signed upload flow."},
            self.context.HTTPStatus.GONE,
        )

    def grant_attachment_upload(self, user: dict) -> None:
        if not self.allow_request(f"upload:{user['username']}", 30, 60 * 60):
            return
        self.context.cleanup_expired_uploads()
        payload = self.read_json_body()
        if payload is None:
            return
        content_type = str(payload.get("type", "")).split(";", 1)[0].strip().lower()
        extension = self.context.ATTACHMENT_TYPES.get(content_type)
        source = str(payload.get("source", "file-picker")).strip().lower()
        try:
            size = int(payload.get("size", 0))
        except (TypeError, ValueError):
            size = 0
        try:
            duration_ms = int(payload.get("durationMs", 0))
        except (TypeError, ValueError):
            duration_ms = 0
        is_voice_message = content_type in self.context.VOICE_ATTACHMENT_TYPES
        original_name = self.context.Path(str(payload.get("name", "file"))).name.strip()[:120]
        if extension is None or not 1 <= size <= self.context.MAX_ATTACHMENT_BYTES:
            self.send_json(
                {"error": "Unsupported or oversized attachment. Attachments can be up to 8MB."},
                self.context.HTTPStatus.BAD_REQUEST,
            )
            return
        if is_voice_message and (
            source != "voice-recorder"
            or not 300 <= duration_ms <= self.context.MAX_VOICE_MESSAGE_DURATION_MS
        ):
            self.send_json(
                {"error": "Voice messages must be recorded inside the app and can be up to 5 minutes."},
                self.context.HTTPStatus.BAD_REQUEST,
            )
            return
        if not is_voice_message and source == "voice-recorder":
            self.send_json({"error": "The recorded media type is not supported."}, self.context.HTTPStatus.BAD_REQUEST)
            return
        if not original_name:
            original_name = f"file{extension}"
        if self.context.Path(original_name).suffix.lower() not in {extension, ".jpeg" if extension == ".jpg" else extension}:
            self.send_json({"error": "The file extension does not match its media type."}, self.context.HTTPStatus.BAD_REQUEST)
            return

        filename = f"upload_{self.context.uuid.uuid4().hex}{extension}"
        upload_token = self.context.UPLOAD_GRANTS.create_pending(
            filename,
            user["username"],
            name=original_name,
            content_type=content_type,
            size=size,
            kind="voice" if is_voice_message else "file",
            duration_ms=duration_ms if is_voice_message else 0,
        )
        if upload_token is None:
            self.send_json(
                {"error": "Too many unfinished uploads. Finish or cancel an upload first."},
                self.context.HTTPStatus.TOO_MANY_REQUESTS,
            )
            return
        try:
            if self.context.SUPABASE_ENABLED:
                upload_url = self.context.supabase_signed_upload_url(filename)
                upload_headers = {
                    "Content-Type": content_type,
                    "cache-control": "3600",
                    "x-upsert": "false",
                }
            else:
                upload_url = f"/uploads/{filename}?grant={self.context.quote(upload_token)}"
                upload_headers = {"Content-Type": content_type}
        except (ConnectionError, ValueError):
            self.context.UPLOAD_GRANTS.fail(filename, user["username"])
            self.send_json({"error": "Unable to authorize the upload."}, self.context.HTTPStatus.BAD_GATEWAY)
            return
        self.send_json(
            {
                "upload": {
                    "id": filename,
                    "url": upload_url,
                    "method": "PUT",
                    "headers": upload_headers,
                    "expires_in": self.context.UPLOAD_GRANT_TTL_SECONDS,
                    "storage": "object" if self.context.SUPABASE_ENABLED else "local-stream",
                }
            },
            self.context.HTTPStatus.CREATED,
        )

    def receive_attachment_upload(self, request_path: str, user: dict) -> None:
        if self.context.SUPABASE_ENABLED:
            self.close_connection = True
            self.send_json({"error": "Direct storage uploads must use the signed object URL."}, self.context.HTTPStatus.NOT_FOUND)
            return
        parsed_url = self.context.urlparse(self.path)
        filename = self.context.Path(self.context.unquote(request_path.removeprefix("/uploads/"))).name
        token = self.context.parse_qs(parsed_url.query).get("grant", [""])[0]
        grant = self.context.UPLOAD_GRANTS.authorize_transfer(filename, user["username"], token)
        if grant is None or not self.context.UPLOAD_NAME_PATTERN.fullmatch(filename):
            self.close_connection = True
            self.send_json({"error": "Upload grant is invalid or expired."}, self.context.HTTPStatus.FORBIDDEN)
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != grant["type"]:
            self.close_connection = True
            self.send_json({"error": "The upload media type does not match its grant."}, self.context.HTTPStatus.BAD_REQUEST)
            return
        self.context.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        upload_path = (self.context.UPLOADS_DIR / filename).resolve()
        upload_path.relative_to(self.context.UPLOADS_DIR.resolve())
        temporary_path = upload_path.with_name(f".{filename}.{self.context.uuid.uuid4().hex}.part")
        try:
            result = self.stream_request_body_to_file(temporary_path, int(grant["size"]))
            if result is None:
                return
            size, prefix = result
            if size != int(grant["size"]) or not self.valid_attachment_content(content_type, prefix):
                self.context.UPLOAD_GRANTS.fail(filename, user["username"])
                self.send_json({"error": "The uploaded file did not match its grant."}, self.context.HTTPStatus.BAD_REQUEST)
                return
            temporary_path.replace(upload_path)
            completed = self.context.UPLOAD_GRANTS.complete(
                filename,
                user["username"],
                size=size,
                content_type=content_type,
            )
            if completed is None:
                upload_path.unlink(missing_ok=True)
                self.send_json({"error": "Upload grant expired before completion."}, self.context.HTTPStatus.CONFLICT)
                return
            self.send_json({"uploaded": True}, self.context.HTTPStatus.OK)
        except (ConnectionError, OSError, ValueError):
            self.context.UPLOAD_GRANTS.fail(filename, user["username"])
            self.send_json({"error": "Unable to save the file."}, self.context.HTTPStatus.BAD_GATEWAY)
        finally:
            temporary_path.unlink(missing_ok=True)

    def complete_attachment_upload(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        filename = str(payload.get("id", ""))
        grant = self.context.UPLOAD_GRANTS.get(filename, user["username"])
        if grant is None or not self.context.UPLOAD_NAME_PATTERN.fullmatch(filename):
            self.send_json({"error": "Upload grant is invalid or expired."}, self.context.HTTPStatus.FORBIDDEN)
            return
        if grant["state"] == "failed":
            self.send_json({"error": "The upload failed and cannot be completed."}, self.context.HTTPStatus.CONFLICT)
            return
        try:
            if self.context.SUPABASE_ENABLED:
                size, stored_type, prefix = self.context.probe_supabase_upload(filename)
                content_type = str(grant["type"])
                if stored_type not in {content_type, "application/octet-stream"}:
                    raise ValueError("Stored media type does not match")
                if not self.valid_attachment_content(content_type, prefix):
                    raise ValueError("Stored content signature does not match")
                completed = self.context.UPLOAD_GRANTS.complete(
                    filename,
                    user["username"],
                    size=size,
                    content_type=content_type,
                )
            else:
                upload_path = (self.context.UPLOADS_DIR / filename).resolve()
                upload_path.relative_to(self.context.UPLOADS_DIR.resolve())
                size = upload_path.stat().st_size
                with upload_path.open("rb") as upload_file:
                    prefix = upload_file.read(self.context.ATTACHMENT_IMAGE_PROBE_BYTES)
                content_type = str(grant["type"])
                if not self.valid_attachment_content(content_type, prefix):
                    raise ValueError("Stored content signature does not match")
                completed = self.context.UPLOAD_GRANTS.complete(
                    filename,
                    user["username"],
                    size=size,
                    content_type=content_type,
                )
        except (ConnectionError, FileNotFoundError, OSError, ValueError):
            self.context.UPLOAD_GRANTS.fail(filename, user["username"])
            try:
                self.context.delete_upload_object(filename)
            except (ConnectionError, OSError, ValueError):
                pass
            self.send_json({"error": "The uploaded object could not be verified."}, self.context.HTTPStatus.BAD_GATEWAY)
            return
        if completed is None:
            self.send_json({"error": "The uploaded object does not match its grant."}, self.context.HTTPStatus.CONFLICT)
            return
        attachment = {
            "url": f"/uploads/{filename}",
            "name": completed["name"],
            "type": completed["type"],
            "size": completed["size"],
            "kind": completed.get("kind", "file"),
            "duration_ms": int(completed.get("duration_ms", 0)),
        }
        self.send_json({"attachment": attachment}, self.context.HTTPStatus.CREATED)

    def discard_attachment_upload(self, user: dict) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        upload_url = self.context.unquote(str(payload.get("url", "")))
        filename = upload_url.removeprefix("/uploads/")
        discarded_grant = None
        if (
            not upload_url.startswith("/uploads/")
            or "/" in filename
            or "\\" in filename
            or not self.context.UPLOAD_NAME_PATTERN.fullmatch(filename)
        ):
            self.send_json({"discarded": False}, self.context.HTTPStatus.OK)
            return
        discarded_grant = self.context.UPLOAD_GRANTS.discard(filename, user["username"])
        if discarded_grant is None:
            self.send_json({"discarded": False}, self.context.HTTPStatus.OK)
            return
        try:
            self.context.delete_upload_object(filename)
        except (ConnectionError, OSError, ValueError):
            self.context.UPLOAD_GRANTS.restore(discarded_grant)
            self.send_json({"error": "임시 첨부 파일을 정리하지 못했습니다."}, self.context.HTTPStatus.BAD_GATEWAY)
            return
        self.send_json({"discarded": True}, self.context.HTTPStatus.OK)

    def valid_attachment_content(self, content_type: str, content: bytes) -> bool:
        if content_type in {"text/plain", "text/csv", "text/markdown"}:
            return b"\x00" not in content

        if content_type == "application/rtf":
            return content.lstrip().startswith(b"{\\rtf")

        if content_type in {
            "application/zip",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }:
            return content.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))

        if content_type in {
            "application/msword",
            "application/vnd.ms-excel",
            "application/vnd.ms-powerpoint",
        }:
            return content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")

        signatures = {
            "image/jpeg": content.startswith(b"\xff\xd8\xff"),
            "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/gif": content.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": content.startswith(b"RIFF") and content[8:12] == b"WEBP",
            "image/heic": content[4:8] == b"ftyp" and content[8:12] in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"},
            "image/heif": content[4:8] == b"ftyp" and content[8:12] in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"},
            "image/avif": content[4:8] == b"ftyp" and content[8:12] in {b"avif", b"avis"},
            "application/pdf": content.startswith(b"%PDF-"),
            "audio/webm": content.startswith(b"\x1aE\xdf\xa3"),
            "audio/mp4": content[4:8] == b"ftyp",
            "audio/ogg": content.startswith(b"OggS"),
        }
        signature_matches = signatures.get(content_type, False)
        if not signature_matches or not content_type.startswith("image/"):
            return bool(signature_matches)
        return self.context.safe_attachment_image_dimensions(content_type, content)

    def message_attachment(self, value: object, username: str) -> dict | None:
        if not isinstance(value, dict):
            return None
        url = str(value.get("url", "")).strip()
        filename = self.context.Path(self.context.unquote(url.removeprefix("/uploads/"))).name
        content_type = str(value.get("type", "")).strip().lower()
        if not url.startswith("/uploads/") or self.context.ATTACHMENT_TYPES.get(content_type) != self.context.Path(filename).suffix.lower():
            return None
        grant = self.context.UPLOAD_GRANTS.get(filename, username)
        if grant is not None:
            if grant["state"] != "completed":
                return None
            content_type = str(grant["type"])
            attachment_size = int(grant["size"])
            name = str(grant["name"])
        else:
            if not self.context.STORE.can_access_attachment(filename, username):
                return None
            try:
                attachment_size = int(value.get("size", 0))
            except (TypeError, ValueError):
                attachment_size = 0
            name = self.context.Path(str(value.get("name", filename))).name.strip()[:120] or filename
        if not self.context.SUPABASE_ENABLED:
            upload_path = (self.context.UPLOADS_DIR / filename).resolve()
            try:
                upload_path.relative_to(self.context.UPLOADS_DIR.resolve())
            except ValueError:
                return None
            if not upload_path.is_file():
                return None
            attachment_size = upload_path.stat().st_size
        try:
            duration_ms = int((grant or value).get("duration_ms", 0))
        except (TypeError, ValueError):
            duration_ms = 0
        return {
            "url": f"/uploads/{filename}",
            "name": name,
            "type": content_type,
            "size": attachment_size,
            "kind": "voice" if content_type in self.context.VOICE_ATTACHMENT_TYPES else "file",
            "duration_ms": min(self.context.MAX_VOICE_MESSAGE_DURATION_MS, max(0, duration_ms)),
        }
