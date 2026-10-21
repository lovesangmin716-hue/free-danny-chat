import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from colorless.content_review import CHECKS, ContentReview, ReviewFailure
from colorless.review_store import PersistentReviewSearch, SQLiteReviewIndex


def document(identity="post", body="near content", **extra):
    return {**dict(id=identity, body=body, title="", scope="public", source_kind="thread"), **extra}


class Encoder:
    version, dimensions = "test-v1", 2

    def __init__(self):
        self.calls = 0
        self.hook = lambda doc: None

    def document_vectors(self, doc, *, deadline):
        self.calls += 1
        self.hook(doc)
        return [("body:0:" + str(len(doc["body"])), [1, 0] if doc["body"].startswith("near") else [0, 1])]


class Source:
    def __init__(self):
        self.rev, self.documents = 1, [document()]

    def revision(self):
        return self.rev

    def review_snapshot(self):
        return self.rev, copy.deepcopy(self.documents)


class PersistentReviewTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.encoder, self.source = Encoder(), Source()
        self.search = PersistentReviewSearch(self.folder.name, encoder=self.encoder, policy_version="p", source=self.source)
        self.search.refresh_threads()
        self.search.blocked.replace([], revision=0)

    def query(self, **kwargs):
        return self.search.search(document("new"), authorize=lambda d: True, **kwargs)

    def test_restart_edit_delete_and_metadata_only_updates_reuse_embeddings(self):
        initial = self.encoder.calls
        self.source.rev += 1
        self.source.documents[0]["modified_at"] = "2026-10-21"
        self.assertEqual(self.search.refresh_threads()["embedded_documents"], 0)
        self.assertEqual(self.encoder.calls, initial)
        self.source.rev += 1
        self.source.documents[0]["body"] = "far edited"
        with self.assertRaisesRegex(ValueError, "stale"):
            self.query()
        self.assertEqual(self.search.refresh_threads()["embedded_documents"], 1)
        reopened = PersistentReviewSearch(self.folder.name, encoder=Encoder(), policy_version="p", source=self.source)
        self.assertEqual(reopened.search(document("new"), authorize=lambda d: True)[0]["body"], "far edited")
        self.source.rev += 1
        self.source.documents = []
        self.assertEqual(self.search.refresh_threads()["documents"], 0)
        self.assertEqual(self.query(), [])

    def test_permissions_filter_before_top_k_and_current_edit_is_excluded(self):
        self.source.rev += 1
        self.source.documents = [document("hidden"), document("self"), document("visible", "far")]
        self.source.documents += [document(f"z-lower-{index}", "far") for index in range(30)]
        self.search.refresh_threads()
        checked = []
        def authorize(doc):
            checked.append(doc["id"])
            return doc["id"] != "hidden"
        result = self.search.search(document("self"), authorize=authorize, limit=1)
        self.assertEqual([d["id"] for d in result], ["visible"])
        self.assertEqual(result[0]["body"], "far")
        self.assertTrue(result[0]["segment_id"].startswith("body:"))
        self.assertEqual(checked, ["hidden", "visible"])

    def test_cached_embeddings_never_cache_permissions_or_source_state_and_expire(self):
        self.assertEqual(len(self.query()), 1)
        calls = self.encoder.calls
        self.assertEqual(self.search.search(document("new"), authorize=lambda d: False), [])
        self.assertEqual(self.encoder.calls, calls)
        self.source.rev += 1
        with self.assertRaisesRegex(ValueError, "stale"):
            self.query()
        self.assertEqual(self.encoder.calls, calls)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.search.search(document("new", "uncached text"), authorize=lambda d: True)
        self.assertEqual(self.encoder.calls, calls)
        self.search.refresh_threads()
        with mock.patch("colorless.review_store.time.monotonic", return_value=time.monotonic() + 301):
            self.assertEqual(len(self.query()), 1)
        self.assertEqual(self.encoder.calls, calls + 1)
        with self.search._cache_lock:
            self.assertLessEqual(len(self.search._cache), 64)
            self.assertLessEqual(sum(len(v) for _, v in self.search._cache.values()), 256)

    def test_failed_build_overflow_and_changed_source_preserve_previous_snapshot(self):
        index = self.search.public
        with self.assertRaisesRegex(ValueError, "source changed"):
            index.replace([document("replacement")], revision=2, source_revision=lambda: 3)
        limited = SQLiteReviewIndex(index.path, scope="public", encoder=self.encoder, policy_version="p", capacity=1)
        with self.assertRaisesRegex(ValueError, "capacity"):
            limited.replace([document("a"), document("b")], revision=2)
        limited_vectors = SQLiteReviewIndex(index.path, scope="public", encoder=self.encoder, policy_version="p", vector_capacity=1)
        with self.assertRaisesRegex(ValueError, "vector capacity"):
            limited_vectors.replace([document("a"), document("b")], revision=2)
        self.encoder.hook = lambda doc: (_ for _ in ()).throw(RuntimeError("inference failed"))
        with self.assertRaises(RuntimeError):
            index.replace([document("changed")], revision=2)
        self.encoder.hook = lambda doc: None
        self.assertEqual(self.query()[0]["id"], "post")
        with self.assertRaisesRegex(ValueError, "reused"):
            index.replace([document("other")], revision=1)
        with self.assertRaises(ValueError):
            index.replace([document("private", scope="followers")], revision=2)

    def test_late_builder_cannot_overwrite_newer_revision(self):
        entered, release = threading.Event(), threading.Event()
        failures = []

        def pause(doc):
            entered.set()
            if not release.wait(3):
                raise TimeoutError()

        self.encoder.hook = pause

        def build():
            try:
                self.search.public.replace([document("slow")], revision=2)
            except ValueError as error:
                failures.append(str(error))

        worker = threading.Thread(target=build)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            fast = SQLiteReviewIndex(self.search.public.path, scope="public", encoder=Encoder(), policy_version="p")
            fast.replace([document("newer")], revision=3)
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(failures)
        self.source.rev = 3
        self.encoder.hook = lambda doc: None
        self.assertEqual(self.query()[0]["id"], "newer")

    def test_blocked_separate_file_expiry_reversal_and_policy_namespace(self):
        blocked = document("audit-1", "near private-marker")
        blocked.update(scope="blocked", policy_version="p", block_reason="reviewed distortion", expires_at=time.time() + 20)
        self.search.blocked.replace([blocked], revision=1)
        self.assertEqual([d["id"] for d in self.query()], ["post"])
        self.assertEqual(len(self.query(include_blocked=True)), 2)
        with self.search.public.connect() as db:
            self.assertNotIn("private-marker", str(db.execute("SELECT document FROM review_documents").fetchall()))
        with mock.patch("colorless.review_store.time.time", return_value=time.time() + 30):
            self.assertEqual([d["id"] for d in self.query(include_blocked=True)], ["post"])
            self.assertEqual(self.search.blocked.purge_expired(), 1)
            self.assertEqual(self.search.blocked.purge_expired(), 0)
            self.assertNotIn(b"private-marker", self.search.blocked.path.read_bytes())
        self.search.blocked.replace([], revision=2)
        with self.search.blocked.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM review_documents").fetchone()[0], 0)
        for policy, encoder in (("next", Encoder()), ("p", type("Next", (Encoder,), {"version": "next"})())):
            with self.assertRaisesRegex(ValueError, "namespace mismatch"):
                SQLiteReviewIndex(self.search.public.path, scope="public", encoder=encoder, policy_version=policy)

    def test_source_changes_during_authorization_or_missing_blocked_index_fail_closed(self):
        def changed(doc):
            self.source.rev += 1
            return True
        with self.assertRaisesRegex(ValueError, "source changed"):
            self.search.search(document("new"), authorize=changed)
        self.source.rev -= 1
        empty = SQLiteReviewIndex(Path(self.folder.name) / "missing.sqlite3", scope="blocked", encoder=Encoder(), policy_version="p")
        self.search.blocked = empty
        with self.assertRaisesRegex(ValueError, "uninitialized"):
            self.query(include_blocked=True)

    def test_content_review_passes_authorization_to_search_and_calls_joint_once(self):
        observed = []
        def verdict(request, *, timeout, stage="joint"):
            observed.append(stage)
            self.assertEqual([d["id"] for d in request["candidates"]], ["visible"])
            return dict(schema_version="1", policy_version="p", model_version="fixture", decision="allow",
                        checks={name: dict(status="pass", evidence=[], missing_evidence=[])
                                for name in (CHECKS if stage == "joint" else ["agitation"])})
        self.source.documents += [document("hidden"), document("visible", "far")]
        self.source.rev += 1
        self.search.refresh_threads()
        review = ContentReview(search=self.search, joint_model=verdict,
                                kaist=lambda r, **kw: verdict(r, stage="kaist", **kw), policy_version="p")
        submission = dict(document("new"), language="ko", visibility="public", parent_id=None, root_id=None,
                          source_url=None, original_id=None, first_observed_at=None, published_at=None, modified_at=None)
        review.check(submission, authorize=lambda d: d["id"] == "visible")
        self.assertEqual(observed, ["joint", "kaist"])
        self.source.rev += 1
        with self.assertRaises(ReviewFailure):
            review.check(submission, authorize=lambda d: True)
        self.assertEqual(observed, ["joint", "kaist"])


if __name__ == "__main__":
    unittest.main()
