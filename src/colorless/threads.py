"""Identity-scoped Threads domain. No account identifier is serialized."""
import re
import time
import uuid
from .threads_repository import ThreadRepository
from .identity import resolve_actor
from .utils import utc_now_iso

ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9_]{3,32})")
WRITE_ACTIONS = {"create", "edit", "delete", "like", "follow", "block", "report", "read", "read_many"}
READ_ACTIONS = {"feed", "detail", "notifications", "people", "relationships"}


class ThreadError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def checked_id(value, optional=False):
    if optional and (value is None or value == ""):
        return ""
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ThreadError("올바른 ID를 입력해 주세요.")
    return value


def new_id(prefix):
    return f"{prefix}_{time.time_ns():020d}_{uuid.uuid4().hex[:8]}"


class Threads:
    def __init__(self, store, actor):
        self.store, self.actor = store, actor["id"]
        self.db = ThreadRepository(store.repository)
        self.user_cache, self.edge_cache, self.post_cache, self.like_cache = {}, {}, {}, {}

    def prime(self, posts, viewers):
        """Batch each bounded page; never truncate an authorization edge lookup."""
        if not posts or not viewers:
            return
        self.post_cache.update((post["id"], post) for post in posts)
        authors = sorted({post["author_identity_id"] for post in posts})
        in_ids = lambda ids: "in.(" + ",".join(ids) + ")"
        for row in self.db.rows("users", {"id": in_ids(authors)}, limit=len(authors)):
            data = self.store.repository.decode(row["data_json"]) if "data_json" in row else row["data"]
            self.user_cache[row["id"]] = {"id": row["id"], "username": row["username"],
                                          "display_name": data.get("display_name") or row["username"]}
        for left, right in ((viewers, authors), (authors, viewers)):
            for actor in left:
                for target in right:
                    for kind in ("follow", "block"):
                        self.edge_cache[actor, target, kind] = False
            edges = self.db.rows("thread_edges", {"actor_identity_id": in_ids(left), "target_identity_id": in_ids(right)},
                                 order="created_at.desc", limit=len(left) * len(right) * 2)
            for row in edges:
                self.edge_cache[row["actor_identity_id"], row["target_identity_id"], row["kind"]] = True
        for post in posts:
            for viewer in viewers:
                self.like_cache[post["id"], viewer] = False
        for row in self.db.rows("thread_likes", {"post_id": in_ids([post["id"] for post in posts]),
                                                "actor_identity_id": in_ids(viewers)},
                                order="created_at.desc", limit=len(posts) * len(viewers)):
            self.like_cache[row["post_id"], row["actor_identity_id"]] = True

    def user(self, identity):
        if identity not in self.user_cache:
            rows = self.db.rows("users", {"id": f"eq.{identity}"}, limit=1)
            if not rows:
                return None
            row = rows[0]
            data = self.store.repository.decode(row["data_json"]) if "data_json" in row else row["data"]
            self.user_cache[identity] = {"id": identity, "username": row["username"],
                                        "display_name": data.get("display_name") or row["username"]}
        return self.user_cache[identity]

    def edge(self, actor, target, kind):
        key = (actor, target, kind)
        if key not in self.edge_cache:
            self.edge_cache[key] = bool(self.db.rows("thread_edges", {
                "actor_identity_id": f"eq.{actor}", "target_identity_id": f"eq.{target}", "kind": f"eq.{kind}",
            }, order="created_at.desc", limit=1))
        return self.edge_cache[key]

    def blocked(self, actor, target):
        return self.edge(actor, target, "block") or self.edge(target, actor, "block")

    def post(self, identity):
        checked_id(identity)
        if identity in self.post_cache:
            return self.post_cache[identity]
        rows = self.db.rows("thread_post_summary", {"id": f"eq.{checked_id(identity)}"}, limit=1)
        self.post_cache[identity] = rows[0] if rows else None
        return self.post_cache[identity]

    def visible(self, post, viewer):
        if not post or self.blocked(viewer, post["author_identity_id"]):
            return False
        root = self.post(post["root_id"]) if post.get("root_id") else post
        if not root or self.blocked(viewer, root["author_identity_id"]):
            return False
        return (viewer == root["author_identity_id"] or root["visibility"] == "public"
                or self.edge(viewer, root["author_identity_id"], "follow"))

    def require_post(self, identity):
        post = self.post(identity)
        if not self.visible(post, self.actor):
            raise ThreadError("게시물을 찾을 수 없거나 이 ID로 볼 수 없습니다.", 404)
        return post

    def owned_ids(self, all_ids):
        if not all_ids:
            return [self.actor]
        actor = self.user(self.actor)
        context = self.store.get_account_context(actor["username"])
        if not context:
            raise ThreadError("활동 ID를 확인할 수 없습니다.", 403)
        identities = [item["id"] for item in context["identities"] if resolve_actor(self.store, actor["username"], item["id"])]
        if not identities:
            raise ThreadError("사용할 수 있는 활동 ID가 없습니다.", 403)
        return identities

    def present(self, post, viewers):
        eligible = [viewer for viewer in viewers if self.visible(post, viewer)]
        if not eligible:
            return None
        viewer = self.actor if self.actor in eligible else eligible[0]
        liked = self.like_cache.get((post["id"], viewer))
        if liked is None:
            liked = bool(self.db.rows("thread_likes", {"post_id": f"eq.{post['id']}", "actor_identity_id": f"eq.{viewer}"},
                                     order="created_at.desc", limit=1))
        parent = self.post(post["parent_id"]) if post.get("parent_id") else None
        reply_to = None
        if parent and self.visible(parent, viewer):
            reply_to = {"id": parent["id"], "author": self.user(parent["author_identity_id"]),
                        "body": "" if parent["deleted"] else parent["body"][:120], "deleted": parent["deleted"]}
        return {key: post[key] for key in ("id", "parent_id", "root_id", "visibility", "created_at", "edited_at", "deleted")} | {
            "body": "" if post["deleted"] else post["body"], "author_identity_id": post["author_identity_id"],
            "author": self.user(post["author_identity_id"]), "like_count": post["like_count"],
            "reply_count": post["reply_count"], "liked": liked, "viewer_identity_id": viewer,
            "viewer_identity_ids": eligible, "following": self.edge(viewer, post["author_identity_id"], "follow"),
            "reply_to": reply_to,
        }

    def read(self, action, payload):
        cursor = checked_id(payload.get("cursor", ""), optional=True)
        viewers = self.owned_ids(payload.get("scope") == "all")
        if action == "detail":
            root = self.require_post(payload.get("id"))
            selected = root
            root = self.require_post(root["root_id"]) if root.get("root_id") else root
            filters = {"root_id": f"eq.{root['id']}"}
            if cursor:
                filters["id"] = f"gt.{cursor}"
            rows = self.db.rows("thread_post_summary", filters, order="id.asc", limit=41)
            self.prime([root, *rows[:40]], [self.actor])
            items = [self.present(row, [self.actor]) for row in rows[:40]]
            return {"post": self.present(root, [self.actor]), "items": [item for item in items if item],
                    "focused_reply": self.present(selected, [self.actor]) if not cursor and selected["id"] != root["id"]
                    and selected["id"] not in {row["id"] for row in rows[:40]} else None,
                    "next_cursor": rows[39]["id"] if len(rows) > 40 else ""}
        if action in ("people", "relationships"):
            if action == "relationships":
                kind = payload.get("kind", "follow")
                if kind not in ("follow", "block"):
                    raise ThreadError("지원하지 않는 관계입니다.")
                filters = {"actor_identity_id": f"eq.{self.actor}", "kind": f"eq.{kind}"}
                if cursor:
                    filters["target_identity_id"] = f"gt.{cursor}"
                rows = self.db.rows("thread_edges", filters, order="target_identity_id.asc", limit=41)
                users = [self.user(row["target_identity_id"]) for row in rows[:40]]
                return {"items": users, "next_cursor": rows[39]["target_identity_id"] if len(rows) > 40 else ""}
            query = str(payload.get("q", "")).strip().lstrip("@")
            if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", query):
                raise ThreadError("정확한 @사용자명을 입력해 주세요.")
            users = self.db.rows("users", {"username": f"eq.{query}"}, limit=1)
            return {"items": [self.user(row["id"]) for row in users if not self.blocked(self.actor, row["id"])]}
        if action == "notifications":
            filters = {"recipient_identity_id": "in.(" + ",".join(viewers) + ")"}
            if payload.get("unread") == "1":
                filters["read_at"] = "eq."
            if cursor:
                filters["id"] = f"lt.{cursor}"
            rows = self.db.rows("thread_notifications", filters, limit=41)
            items = []
            for row in rows[:40]:
                recipient = row["recipient_identity_id"]
                if self.blocked(recipient, row["actor_identity_id"]):
                    continue
                post = self.post(row["post_id"]) if row["post_id"] else None
                if row["post_id"] and (not self.visible(post, recipient) or post["deleted"]):
                    continue
                items.append({**row, "actor": self.user(row["actor_identity_id"]),
                              "recipient": self.user(recipient), "post_body": post["body"] if post else ""})
            return {"items": items, "next_cursor": rows[39]["id"] if len(rows) > 40 else ""}
        # Scan a bounded number of candidates and advance the cursor even when filtered.
        query = str(payload.get("q", "")).strip().casefold()
        if len(query) > 100:
            raise ThreadError("검색어는 100자 이내로 입력해 주세요.")
        items, scanned, more = [], cursor, True
        for _ in range(10):
            filters = {"parent_id": "is.null", "deleted": "eq.0"}
            if scanned:
                filters["id"] = f"lt.{scanned}"
            rows = self.db.rows("thread_post_summary", filters, limit=40)
            if not rows:
                more = False
                break
            self.prime(rows, viewers)
            for row in rows:
                scanned = row["id"]
                if query and query not in row["body"].casefold():
                    continue
                eligible = viewers
                if payload.get("mode") == "following":
                    eligible = [viewer for viewer in viewers if viewer == row["author_identity_id"]
                                or self.edge(viewer, row["author_identity_id"], "follow")]
                item = self.present(row, eligible)
                if item:
                    items.append(item)
                if len(items) == 20:
                    break
            if len(items) == 20:
                break
            if len(rows) < 40:
                more = False
                break
        return {"items": items, "next_cursor": scanned if more else ""}

    def write(self, action, payload):
        for _ in range(3):
            revision = self.db.revision()
            self.edge_cache.clear()
            self.post_cache.clear()
            self.like_cache.clear()
            mutations, result = self.plan(action, payload)
            if not mutations:
                return result
            committed = self.db.commit(self.actor, revision, mutations)
            if committed.get("error") == "conflict":
                continue
            if committed.get("error"):
                raise ThreadError("이 활동 ID로 요청할 수 없습니다.", 403)
            return {**result, "revision": committed["revision"]}
        raise ThreadError("동시에 변경되었습니다. 다시 시도해 주세요.", 409)

    def plan(self, action, payload):
        changes, now = [], utc_now_iso()
        def change(table, row, remove=False):
            changes.append({"table": table, "row": row, "remove": remove})
        def notify(recipient, kind, post=None):
            if recipient == self.actor or self.blocked(recipient, self.actor):
                return
            if post and not self.visible(post, recipient):
                return
            if self.db.rows("thread_notifications", {"recipient_identity_id": f"eq.{recipient}",
                    "actor_identity_id": f"eq.{self.actor}", "kind": f"eq.{kind}",
                    "post_id": f"eq.{post['id']}" if post else "is.null"}, limit=1):
                return
            change("thread_notifications", {"id": new_id("tn"), "recipient_identity_id": recipient,
                   "actor_identity_id": self.actor, "post_id": post["id"] if post else None, "kind": kind,
                   "created_at": now, "read_at": ""})
        if action in ("create", "edit"):
            body = payload.get("body", "")
            if not isinstance(body, str) or not body.strip() or len(body.strip()) > 2000:
                raise ThreadError("내용은 1~2,000자로 입력해 주세요.")
            body = body.strip()
            if len(set(MENTION.findall(body))) > 20:
                raise ThreadError("멘션은 한 번에 최대 20개까지 가능합니다.")
        if action == "create":
            client_id = checked_id(payload.get("clientId"))
            checked_id(payload.get("parentId", ""), optional=True)
            existing = self.db.rows("thread_posts", {"author_identity_id": f"eq.{self.actor}", "client_id": f"eq.{client_id}"}, limit=1)
            if existing:
                if (existing[0]["body"] != body or (existing[0]["parent_id"] or "") != payload.get("parentId", "")
                        or (not existing[0]["parent_id"] and existing[0]["visibility"] != payload.get("visibility", "public"))):
                    raise ThreadError("이미 사용한 작성 요청 ID입니다.", 409)
                return [], {"id": existing[0]["id"]}
            parent = self.require_post(payload["parentId"]) if payload.get("parentId") else None
            if parent and parent["deleted"]:
                raise ThreadError("삭제된 내용에는 답글을 달 수 없습니다.", 409)
            visibility = payload.get("visibility", "public")
            if visibility not in ("public", "followers"):
                raise ThreadError("공개 범위를 선택해 주세요.")
            post = {"id": new_id("tp"), "author_identity_id": self.actor,
                    "parent_id": parent["id"] if parent else None,
                    "root_id": (parent["root_id"] or parent["id"]) if parent else None,
                    "body": body, "visibility": parent["visibility"] if parent else visibility,
                    "client_id": client_id, "created_at": now, "edited_at": "", "deleted": 0}
            change("thread_posts", post)
            if parent:
                notify(parent["author_identity_id"], "reply", post)
            for username in set(MENTION.findall(body)):
                rows = self.db.rows("users", {"username": f"eq.{username}"}, limit=1)
                if rows:
                    notify(rows[0]["id"], "mention", post)
            return changes, {"id": post["id"]}
        if action in ("follow", "block"):
            target = checked_id(payload.get("identityId"))
            if target == self.actor or not self.user(target):
                raise ThreadError("다른 활동 ID를 선택해 주세요.")
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                raise ThreadError("enabled 값이 필요합니다.")
            if action == "follow" and self.blocked(self.actor, target):
                raise ThreadError("차단 관계에서는 팔로우할 수 없습니다.", 403)
            change("thread_edges", {"actor_identity_id": self.actor, "target_identity_id": target,
                   "kind": action, "created_at": now}, not enabled)
            if action == "block" and enabled:
                for first, second in ((self.actor, target), (target, self.actor)):
                    change("thread_edges", {"actor_identity_id": first, "target_identity_id": second, "kind": "follow"}, True)
            if action == "follow" and enabled:
                notify(target, "follow")
            return changes, {"ok": True}
        if action in ("read", "read_many"):
            ids = [payload.get("id")] if action == "read" else payload.get("ids")
            if not isinstance(ids, list) or not 1 <= len(ids) <= 40:
                raise ThreadError("읽음 처리는 한 번에 1~40개의 알림만 가능합니다.")
            ids = {checked_id(identity) for identity in ids}
            rows = self.db.rows("thread_notifications", {"id": "in.(" + ",".join(sorted(ids)) + ")",
                                                          "recipient_identity_id": f"eq.{self.actor}"}, limit=len(ids))
            if len(rows) != len(ids):
                raise ThreadError("이 ID의 알림이 아닙니다.", 404)
            for row in rows:
                if not row["read_at"]:
                    change("thread_notifications", {**row, "read_at": now})
            return changes, {"ok": True}
        post = self.require_post(payload.get("id"))
        if action in ("edit", "delete"):
            if post["author_identity_id"] != self.actor:
                raise ThreadError("작성 ID만 수정하거나 삭제할 수 있습니다.", 403)
            if post["deleted"]:
                raise ThreadError("이미 삭제된 내용입니다.", 409)
            if action == "edit" and "originalBody" in payload and payload["originalBody"] != post["body"]:
                raise ThreadError("다른 창에서 내용이 수정되었습니다. 새로고침한 뒤 다시 수정해 주세요.", 409)
            record = {key: value for key, value in post.items() if key not in ("like_count", "reply_count")}
            change("thread_posts", {**record, "body": body if action == "edit" else "",
                                   "edited_at": now, "deleted": int(action == "delete")})
            if action == "edit":
                for username in set(MENTION.findall(body)) - set(MENTION.findall(post["body"])):
                    rows = self.db.rows("users", {"username": f"eq.{username}"}, limit=1)
                    if rows:
                        notify(rows[0]["id"], "mention", post)
        elif action == "like":
            if post["deleted"] or not isinstance(payload.get("enabled"), bool):
                raise ThreadError("반응을 처리할 수 없습니다.")
            change("thread_likes", {"post_id": post["id"], "actor_identity_id": self.actor, "created_at": now}, not payload["enabled"])
            if payload["enabled"]:
                notify(post["author_identity_id"], "like", post)
        elif action == "report":
            reason = str(payload.get("reason", "")).strip()
            if not 5 <= len(reason) <= 500:
                raise ThreadError("신고 사유는 5~500자로 입력해 주세요.")
            existing = self.db.rows("thread_reports", {"actor_identity_id": f"eq.{self.actor}", "post_id": f"eq.{post['id']}"}, limit=1)
            if not existing:
                change("thread_reports", {"id": new_id("tr"), "actor_identity_id": self.actor,
                       "post_id": post["id"], "reason": reason, "created_at": now})
        else:
            raise ThreadError("지원하지 않는 작업입니다.", 404)
        return changes, {"ok": True}
