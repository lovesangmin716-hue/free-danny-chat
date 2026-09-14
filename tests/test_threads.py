import json
import time
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

try:
    import test_server as support
except ImportError:
    from tests import test_server as support
from colorless.threads import Threads, ThreadError
from colorless.threads_repository import ThreadRepository

server = support.server


class ThreadsTest(unittest.TestCase):
    request = support.AttachmentTransferIntegrationTestCase.request

    def setUp(self):
        # test_server's module teardown removes its shared DB. Own the fixture so
        # these tests work both independently and after the complete server suite.
        self.thread_data = tempfile.TemporaryDirectory(prefix="colorless-thread-tests-")
        self.thread_store = server.StateStore(Path(self.thread_data.name) / "state.json")
        self.store_patch = mock.patch.object(server, "STORE", self.thread_store)
        self.session_patch = mock.patch.object(server, "SESSIONS", server.SessionStore(state_store=self.thread_store))
        self.store_patch.start(); self.session_patch.start()
        support.AttachmentTransferIntegrationTestCase.setUp(self)

    def tearDown(self):
        support.AttachmentTransferIntegrationTestCase.tearDown(self)
        self.session_patch.stop(); self.store_patch.stop()
        self.thread_store.close()
        self.thread_data.cleanup()

    def service(self, user=None):
        return Threads(server.STORE, user or self.user)

    def post(self, body="Hello", user=None, **options):
        return self.service(user).write("create", {"body": body, "clientId": str(time.time_ns()), **options})["id"]

    def alternate(self):
        suffix = str(time.time_ns())[-10:]
        user, error = server.STORE.create_identity(self.username, f"alt{suffix}", "Alternate", f"al{suffix}")
        self.assertIsNone(error)
        return user

    def get(self, action, identity=None, **params):
        status, headers, body = self.request("GET", "/threads/api/" + action + "?" + urlencode(params),
                                            headers={"X-Acting-Identity": identity or self.user["id"]})
        return status, json.loads(body), headers

    def send(self, action, payload, identity=None):
        status, _, body = self.request("POST", "/threads/api/" + action, json.dumps(payload).encode(),
                                      {"Content-Type": "application/json", "X-Acting-Identity": identity or self.user["id"]})
        return status, json.loads(body)

    def test_http_auth_actor_binding_and_no_store(self):
        payload = {"body": "Pinned actor", "clientId": "http-1"}
        self.assertEqual(self.send("create", payload, self.peer["id"])[0], 403)
        self.assertEqual(self.send("create", {**payload, "acting_identity_id": self.peer["id"]})[0], 403)
        alternate = self.alternate()
        status, created = self.send("create", payload, alternate["id"])
        self.assertEqual(status, 200, created)
        self.assertEqual(self.service().post(created["id"])["author_identity_id"], alternate["id"])
        status, data, headers = self.get("detail", alternate["id"], id=created["id"])
        self.assertEqual(status, 200, data)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("account_id", json.dumps(data))
        self.assertEqual(self.request("GET", "/threads/api/feed", headers={"Cookie": ""})[0], 401)
        self.assertEqual(self.send("unknown", {})[0], 404)

    def test_other_owned_identity_cannot_edit_or_delete(self):
        alternate = self.alternate()
        post = self.post(user=alternate)
        for action in ("edit", "delete"):
            self.assertEqual(self.send(action, {"id": post, "body": "Tampered"})[0], 403)
        self.assertEqual(self.send("edit", {"id": post, "body": "Updated"}, alternate["id"])[0], 200)
        self.assertEqual(self.service().post(post)["body"], "Updated")

    def test_followers_visibility_and_union_feed(self):
        alternate = self.alternate()
        post = self.post("private-marker", self.peer, visibility="followers")
        self.service(alternate).write("follow", {"identityId": self.peer["id"], "enabled": True})
        self.assertEqual(self.get("detail", id=post)[0], 404)
        self.assertEqual(self.get("detail", alternate["id"], id=post)[0], 200)
        single = self.get("feed", q="private-marker")[1]["items"]
        self.assertEqual(single, [])
        merged = self.get("feed", scope="all", q="private-marker")[1]["items"]
        self.assertEqual([item["id"] for item in merged], [post])
        self.assertEqual(merged[0]["viewer_identity_ids"], [alternate["id"]])
        self.assertEqual(merged[0]["viewer_identity_id"], alternate["id"])
        self.assertEqual(self.send("like", {"id": post, "enabled": True})[0], 404)

    def test_likes_and_notification_reads_are_independent(self):
        alternate = self.alternate()
        post = self.post(user=self.peer)
        for user in (self.user, alternate):
            self.service(user).write("like", {"id": post, "enabled": True})
        self.service().write("like", {"id": post, "enabled": False})
        self.assertEqual(self.service().post(post)["like_count"], 1)
        self.assertTrue(self.service(alternate).read("detail", {"id": post})["post"]["liked"])
        self.post(f"@{self.username} @{alternate['username']}", self.peer)
        items = self.service().read("notifications", {"scope": "all"})["items"]
        mentions = [item for item in items if item["kind"] == "mention"]
        self.assertEqual(len(mentions), 2)
        notification = next(item for item in mentions if item["recipient_identity_id"] == alternate["id"])
        self.assertEqual(self.send("read", {"id": notification["id"]})[0], 404)
        self.assertEqual(self.send("read", {"id": notification["id"]}, alternate["id"])[0], 200)
        mine = self.service().read("notifications", {})["items"]
        self.assertTrue(all(not item["read_at"] for item in mine))

    def test_block_only_one_identity_revokes_both_follows_and_interactions(self):
        alternate = self.alternate()
        post = self.post(user=self.peer)
        for actor, target in ((self.user, self.peer), (self.peer, self.user), (alternate, self.peer)):
            self.service(actor).write("follow", {"identityId": target["id"], "enabled": True})
        self.service().write("block", {"identityId": self.peer["id"], "enabled": True})
        self.assertFalse(self.service().edge(self.user["id"], self.peer["id"], "follow"))
        self.assertFalse(self.service().edge(self.peer["id"], self.user["id"], "follow"))
        self.assertTrue(self.service().edge(alternate["id"], self.peer["id"], "follow"))
        self.assertEqual(self.get("detail", id=post)[0], 404)
        self.assertEqual(self.get("detail", alternate["id"], id=post)[0], 200)
        self.assertEqual(self.send("like", {"id": post, "enabled": True})[0], 404)
        self.assertEqual(self.send("follow", {"identityId": self.peer["id"], "enabled": True})[0], 403)
        self.assertEqual(self.service().read("notifications", {})["items"], [])
        self.service().write("block", {"identityId": self.peer["id"], "enabled": False})
        self.assertEqual(self.get("detail", id=post)[0], 200)

    def test_replies_tombstones_and_notification_privacy(self):
        post = self.post("Original")
        reply = self.post("Comment", self.peer, parentId=post)
        nested = self.post("Nested", parentId=reply)
        self.assertEqual(self.service().post(nested)["root_id"], post)
        self.service(self.peer).write("delete", {"id": reply})
        detail = self.service().read("detail", {"id": post})
        self.assertEqual([item["id"] for item in detail["items"]], [reply, nested])
        self.assertEqual(detail["items"][0]["body"], "")
        self.assertEqual(self.send("create", {"body": "Late", "clientId": "late", "parentId": reply})[0], 409)
        self.assertFalse(any(item["post_id"] == reply for item in self.service().read("notifications", {})["items"]))
        self.service().write("delete", {"id": post})
        self.assertEqual(self.get("detail", id=post)[1]["post"]["deleted"], 1)

    def test_idempotent_create_and_mentions_on_edit(self):
        payload = {"body": "hello", "clientId": "retry-once"}
        first = self.service().write("create", payload)
        self.assertEqual(self.service().write("create", payload)["id"], first["id"])
        self.assertEqual(self.send("create", {**payload, "visibility": "followers"})[0], 409)
        self.service().write("edit", {"id": first["id"], "body": f"Hi @{self.peer['username']}"})
        self.service().write("edit", {"id": first["id"], "body": f"Again @{self.peer['username']}"})
        mentions = [item for item in self.service(self.peer).read("notifications", {})["items"] if item["kind"] == "mention"]
        self.assertEqual(len(mentions), 1)
        secret = self.post(f"@{self.peer['username']}", visibility="followers")
        self.assertFalse(any(item["post_id"] == secret for item in self.service(self.peer).read("notifications", {})["items"]))

    def test_disabled_identity_and_stale_transaction_cannot_write(self):
        alternate = self.alternate()
        post = self.post(user=alternate)
        db = self.service().db
        revision = db.revision()
        self.post("revision change")
        self.assertEqual(db.commit(self.user["id"], revision, []), {"error": "conflict"})
        server.STORE.repository.disable_identity(self.user["id"], alternate["id"], "2026-09-14")
        self.assertEqual(db.commit(alternate["id"], db.revision(), []), {"error": "forbidden"})
        self.assertEqual(self.send("edit", {"id": post, "body": "No"}, alternate["id"])[0], 403)
        self.assertNotIn(alternate["id"], self.service().owned_ids(True))
        self.assertEqual(self.service().post(post)["body"], "Hello")

    def test_pagination_search_and_batched_page_reads(self):
        marker = "page-" + str(time.time_ns())
        posts = [self.post(f"{marker}-{i}") for i in range(25)]
        service = self.service()
        with mock.patch.object(service.db, "rows", wraps=service.db.rows) as reads:
            page = service.read("feed", {"q": marker})
        self.assertEqual(len(page["items"]), 20)
        self.assertLess(reads.call_count, 10)
        rest = self.service().read("feed", {"q": marker, "cursor": page["next_cursor"]})
        self.assertEqual([item["id"] for item in page["items"] + rest["items"]], posts[::-1])

    def test_validation_and_confidential_report(self):
        for payload in ({}, {"body": "x", "clientId": "a,b"}, {"body": "x" * 2001, "clientId": "ok"},
                        {"body": "x", "clientId": "valid", "parentId": []}, {"body": "x", "clientId": "valid", "parentId": {"id": "x"}}):
            self.assertEqual(self.send("create", payload)[0], 400)
        self.assertEqual(self.get("feed", cursor="x),or=(id.gt.0")[0], 400)
        post = self.post(user=self.peer)
        self.assertEqual(self.send("report", {"id": post, "reason": "Abusive content"})[0], 200)
        self.assertEqual(self.send("report", {"id": post, "reason": "Same report"})[0], 200)
        self.assertEqual(len(self.service().db.rows("thread_reports", {"post_id": f"eq.{post}"})), 1)
        self.assertNotIn("report", json.dumps(self.get("detail", id=post)[1]))

    def test_supabase_adapter_uses_bounded_reads_and_atomic_rpc(self):
        repository = mock.Mock(spec=["rows", "rpc"])
        repository.rpc.return_value = {"revision": 4}
        db = ThreadRepository(repository)
        db.rows("thread_posts", {"id": "eq.safe"}, limit=20)
        repository.rows.assert_called_once_with("thread_posts", {"select": "*", "id": "eq.safe", "order": "id.desc", "limit": "20"})
        self.assertEqual(db.commit("identity-1", 3, []), {"revision": 4})
        repository.rpc.assert_called_once_with("colorless_threads_commit", {"actor_id": "identity-1", "expected_revision": 3, "mutations": []})

    def test_racing_block_invalidates_prepared_like(self):
        post = self.post(user=self.peer)
        service = self.service()
        original = service.db.commit
        def race(actor, revision, mutations):
            self.service(self.peer).write("block", {"identityId": self.user["id"], "enabled": True})
            return original(actor, revision, mutations)
        with mock.patch.object(service.db, "commit", side_effect=race):
            with self.assertRaises(ThreadError) as error:
                service.write("like", {"id": post, "enabled": True})
        self.assertEqual(error.exception.status, 404)
        self.assertEqual(self.service().post(post)["like_count"], 0)


if __name__ == "__main__":
    unittest.main()
