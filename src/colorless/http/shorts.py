from __future__ import annotations


class ShortsRoutesMixin:
    def serve_public_shorts(self, query: dict[str, list[str]], user: dict) -> None:
        if not self.allow_request(f"shorts:{user['username']}", 60, 60):
            return
        requested_cursor = query.get("cursor", [""])[0].strip()
        refresh = query.get("refresh", [""])[0] == "1"
        if requested_cursor and not self.context.re.fullmatch(r"catalog:\d{1,9}", requested_cursor):
            self.send_json({"error": "잘못된 쇼츠 페이지 요청이에요."}, self.context.HTTPStatus.BAD_REQUEST)
            return

        with self.context.SHORTS_FEED_LOCK:
            seen_ids, saved_cursor = self.context.STORE.get_shorts_feed(user["username"])
        if refresh and not requested_cursor:
            seen_ids = []
            saved_cursor = ""
        cursor = requested_cursor or (saved_cursor if not refresh else "")
        if cursor and not self.context.re.fullmatch(r"catalog:\d{1,9}", cursor):
            cursor = ""
        offset = int(cursor.removeprefix("catalog:")) if cursor else 0
        recent_set = set(seen_ids)
        now = self.context.time.time()
        items: list[dict] = []
        stale_hit = False
        newest_catalog_at = 0.0
        next_offset = offset

        # Scan bounded catalog pages to skip this user's seen rows without copying the catalog.
        reached_catalog_end = False
        for _ in range(5):
            candidates = self.context.STORE.repository.list_shorts_catalog(
                limit=self.context.SHORTS_CATALOG_SCAN_SIZE,
                offset=next_offset,
            )
            if not candidates:
                next_offset = 0
                reached_catalog_end = True
                break
            newest_catalog_at = max(
                newest_catalog_at,
                max(float(candidate.get("last_seen_at", 0)) for candidate in candidates),
            )
            consumed = 0
            for candidate in candidates:
                consumed += 1
                if candidate["id"] in recent_set:
                    continue
                items.append({
                    "id": candidate["id"],
                    "title": candidate.get("title", "YouTube 쇼츠"),
                    "channel_title": candidate.get("channel_title", "YouTube"),
                })
                stale_hit = stale_hit or float(candidate.get("expires_at", 0)) <= now
                if len(items) >= self.context.SHORTS_CATALOG_PAGE_SIZE:
                    break
            next_offset += consumed
            if len(items) >= self.context.SHORTS_CATALOG_PAGE_SIZE:
                break
            if len(candidates) < self.context.SHORTS_CATALOG_SCAN_SIZE:
                if consumed >= len(candidates):
                    next_offset = 0
                    reached_catalog_end = True
                break

        catalog_hit = bool(items)
        emergency_hit = False
        cycled = False
        if not items and reached_catalog_end:
            candidates = self.context.STORE.repository.list_shorts_catalog(limit=self.context.SHORTS_CATALOG_PAGE_SIZE, offset=0)
            if candidates:
                items = [
                    {
                        "id": candidate["id"],
                        "title": candidate.get("title", "YouTube 쇼츠"),
                        "channel_title": candidate.get("channel_title", "YouTube"),
                    }
                    for candidate in candidates
                ]
                newest_catalog_at = max(float(candidate.get("last_seen_at", 0)) for candidate in candidates)
                stale_hit = any(float(candidate.get("expires_at", 0)) <= now for candidate in candidates)
                next_offset = len(candidates) if len(candidates) >= self.context.SHORTS_CATALOG_PAGE_SIZE else 0
                seen_ids = []
                catalog_hit = True
                cycled = True
        if not items:
            items = [item for item in self.context.EMERGENCY_SHORTS if item["id"] not in recent_set]
            if not items:
                items = list(self.context.EMERGENCY_SHORTS)
                seen_ids = []
                cycled = bool(items)
            emergency_hit = bool(items)
        next_cursor = f"catalog:{next_offset}"
        with self.context.SHORTS_FEED_LOCK:
            if items:
                seen_ids.extend(str(item["id"]) for item in items)
            self.context.STORE.save_shorts_feed(user["username"], seen_ids, next_cursor)
        self.context.SHORTS_COLLECTOR.record_feed(
            catalog_hit=catalog_hit,
            stale_hit=stale_hit,
            emergency_hit=emergency_hit,
        )
        self.send_json(
            {
                "items": items,
                "next_cursor": next_cursor,
                "retry_after": 3 if not items else 0,
                "cycled": cycled,
                "catalog": {
                    "stale": stale_hit,
                    "age_seconds": round(max(0.0, now - newest_catalog_at), 3) if newest_catalog_at else None,
                },
            },
            self.context.HTTPStatus.OK,
        )
