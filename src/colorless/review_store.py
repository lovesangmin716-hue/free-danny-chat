"""Isolated durable exact-vector indexes; no production schema or server setup.

Public and blocked records use separate files and capabilities. Complete,
revisioned snapshots replace an index atomically; partial snapshots never do.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sqlite3
import time
import threading
from collections import OrderedDict
from pathlib import Path

from .content_review import SOURCE_FIELDS


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _document(value, scope, policy):
    value = copy.deepcopy(value)
    if (not isinstance(value, dict) or value.get("scope") != scope or not isinstance(value.get("id"), str)
            or not value["id"] or value["id"] == "submission" or len(value["id"]) > 200
            or not isinstance(value.get("body"), str) or len(value["body"]) > 20000
            or not isinstance(value.get("title", ""), str) or len(value.get("title", "")) > 2000
            or value.get("source_kind") not in ("thread", "article")):
        raise ValueError("invalid index document")
    result = {key: value[key] for key in ("id", "body", "scope", "source_kind")}
    result.update(title=value.get("title", ""), segment_id=None, policy_version=None, block_reason=None, expires_at=None)
    for field in SOURCE_FIELDS:
        item = value.get(field)
        if item is not None and not isinstance(item, str):
            raise ValueError("invalid source metadata")
        result[field] = item
    if scope == "blocked":
        expires = value.get("expires_at")
        if (value.get("policy_version") != policy or not isinstance(value.get("block_reason"), str)
                or not value["block_reason"].strip() or type(expires) not in (int, float)
                or not math.isfinite(expires) or expires <= time.time()):
            raise ValueError("blocked record needs current policy, reason and TTL")
        result.update(policy_version=policy, block_reason=value["block_reason"], expires_at=expires)
    return result


class SQLiteReviewIndex:
    def __init__(self, path, *, scope, encoder, policy_version, capacity=10000, vector_capacity=None):
        if (scope not in ("public", "blocked") or not isinstance(policy_version, str) or not policy_version.strip()
                or type(capacity) is not int or not 1 <= capacity <= 10000
                or not isinstance(encoder.version, str) or not encoder.version
                or type(encoder.dimensions) is not int or not 1 <= encoder.dimensions <= 4096):
            raise ValueError("invalid index configuration")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.scope, self.encoder, self.policy, self.capacity = scope, encoder, policy_version, capacity
        max_vectors = min(20000, 4_000_000 // encoder.dimensions)
        if vector_capacity is not None and (type(vector_capacity) is not int or not 1 <= vector_capacity <= max_vectors):
            raise ValueError("invalid vector capacity")
        self.vector_capacity = vector_capacity if vector_capacity is not None else max_vectors
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS review_meta (id INTEGER PRIMARY KEY CHECK(id=1),
                    scope TEXT NOT NULL, model TEXT NOT NULL, dimensions INTEGER NOT NULL,
                    policy TEXT NOT NULL, revision INTEGER NOT NULL, digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS review_documents (id TEXT PRIMARY KEY,
                    document TEXT NOT NULL, content_digest TEXT NOT NULL, vectors TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO review_meta VALUES (1,?,?,?,?,?,?)",
                       (scope, encoder.version, encoder.dimensions, policy_version, -1, ""))
            self._metadata(db)

    def connect(self):
        # Closing is explicit: sqlite's context manager only commits/rolls back.
        from contextlib import closing
        connection = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        if self.scope == "blocked":
            connection.execute("PRAGMA secure_delete=ON")
        return closing(connection)

    def _metadata(self, db):
        row = db.execute("SELECT scope,model,dimensions,policy,revision,digest FROM review_meta WHERE id=1").fetchone()
        if not row or row[:4] != (self.scope, self.encoder.version, self.encoder.dimensions, self.policy):
            raise ValueError("index namespace mismatch; rebuild into a new directory")
        return row[4], row[5]

    def _vectors(self, vectors):
        if not isinstance(vectors, (list, tuple)) or not 1 <= len(vectors) <= 128:
            raise ValueError("bounded segments required")
        result, seen = [], set()
        for segment, vector in vectors:
            if not isinstance(segment, str) or not segment or segment in seen or len(vector) != self.encoder.dimensions:
                raise ValueError("invalid vector segment")
            seen.add(segment)
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
                raise ValueError("invalid embedding")
            norm = math.hypot(*vector)
            if not math.isfinite(norm) or norm == 0:
                raise ValueError("invalid vector norm")
            result.append((segment, [v / norm for v in vector]))
        return result

    def assert_ready(self, expected_revision=None):
        with self.connect() as db:
            revision, _ = self._metadata(db)
        if revision < 0 or expected_revision is not None and revision != expected_revision:
            raise ValueError("index is stale or uninitialized")

    def purge_expired(self):
        """Explicit private-index maintenance, never a production post mutation."""
        if self.scope != "blocked":
            raise ValueError("expiry maintenance is private-index only")
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._metadata(db)
                documents = [json.loads(row[0]) for row in db.execute("SELECT document FROM review_documents")]
                expired = [d for d in documents if d["expires_at"] <= now or d["policy_version"] != self.policy]
                if expired:
                    removed = {d["id"] for d in expired}
                    db.executemany("DELETE FROM review_documents WHERE id=?", [(identity,) for identity in removed])
                    remaining = sorted((d for d in documents if d["id"] not in removed), key=lambda d: d["id"])
                    # Keep the source watermark, but invalidate concurrent build
                    # baselines. Expired records cannot be restored by replace().
                    db.execute("UPDATE review_meta SET digest=? WHERE id=1", (_digest(remaining),))
                db.commit()
                return len(expired)
            except BaseException:
                db.rollback()
                raise

    def replace(self, documents, *, revision, timeout=120, source_revision=None):
        """Embed outside the write transaction; reject stale concurrent builders."""
        if type(revision) is not int or revision < 0 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("valid revision and timeout required")
        deadline = time.monotonic() + timeout
        normalized = []
        seen = set()
        for value in documents:
            if len(normalized) >= self.capacity:
                raise ValueError("complete snapshot exceeds index capacity")
            document = _document(value, self.scope, self.policy)
            if document["id"] in seen:
                raise ValueError("duplicate snapshot ID")
            seen.add(document["id"])
            normalized.append(document)
        normalized.sort(key=lambda d: d["id"])
        digest = _digest(normalized)
        with self.connect() as db:
            baseline = self._metadata(db)
            if revision < baseline[0] or revision == baseline[0] and digest != baseline[1]:
                raise ValueError("stale or reused snapshot revision")
            old = {row[0]: row[1:] for row in db.execute("SELECT id,content_digest,vectors FROM review_documents")}
        pending, embedded, vector_count = [], 0, 0
        for document in normalized:
            if time.monotonic() >= deadline:
                raise TimeoutError("index build deadline exceeded")
            content_digest = _digest([document["title"], document["body"]])
            cached = old.get(document["id"])
            if cached and cached[0] == content_digest:
                vectors = self._vectors(json.loads(cached[1]))
            else:
                vectors = self._vectors(self.encoder.document_vectors(document, deadline=deadline))
                embedded += 1
            vector_count += len(vectors)
            if vector_count > self.vector_capacity:
                raise ValueError("snapshot exceeds vector capacity")
            pending.append((document["id"], _json(document), content_digest, _json(vectors)))
        if time.monotonic() >= deadline:
            raise TimeoutError("index build deadline exceeded")
        if source_revision is not None and source_revision() != revision:
            raise ValueError("source changed during index build")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if self._metadata(db) != baseline:
                    raise ValueError("another builder replaced this index")
                db.execute("DELETE FROM review_documents")
                db.executemany("INSERT INTO review_documents VALUES (?,?,?,?)", pending)
                db.execute("UPDATE review_meta SET revision=?,digest=? WHERE id=1", (revision, digest))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return dict(revision=revision, documents=len(pending), embedded_documents=embedded)

    def rank(self, vectors, *, authorize, expected_revision=None, exclude_id=None, timeout=5, limit=20):
        if (not callable(authorize) or type(limit) is not int or not 1 <= limit <= 20
                or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("bounded authorized search required")
        if self.scope == "public" and (type(expected_revision) is not int or expected_revision < 0):
            raise ValueError("authoritative public source revision required")
        deadline = time.monotonic() + timeout
        queries = self._vectors(vectors)
        with self.connect() as db:
            db.execute("BEGIN")
            revision, _ = self._metadata(db)
            if revision < 0 or expected_revision is not None and revision != expected_revision:
                raise ValueError("index is stale or uninitialized")
            rows = db.execute("SELECT document,vectors FROM review_documents").fetchall()
        scored = []
        for raw, stored in rows:
            if time.monotonic() >= deadline:
                raise TimeoutError("search deadline exceeded")
            document = json.loads(raw)
            if document["id"] == exclude_id:
                continue
            if self.scope == "blocked":
                if document["policy_version"] != self.policy or document["expires_at"] <= time.time():
                    continue
            best = (-math.inf, "")
            for segment, vector in self._vectors(json.loads(stored)):
                score = max(sum(a * b for a, b in zip(query, vector)) for _, query in queries)
                if score > best[0]:
                    best = score, segment
            document["segment_id"] = best[1]
            scored.append((best[0], document))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        accepted = []
        for item in scored:
            # Do not truncate first: inaccessible high scorers must not crowd
            # out visible documents. Stop only after collecting k authorized
            # hits, avoiding an ACL database query for every lower scorer.
            if self.scope == "blocked" or authorize(copy.deepcopy(item[1])):
                accepted.append(item)
            if time.monotonic() >= deadline:
                raise TimeoutError("search deadline exceeded")
            if len(accepted) == limit:
                break
        if time.monotonic() >= deadline:
            raise TimeoutError("search deadline exceeded")
        return accepted


class PersistentReviewSearch:
    """Explicit experiment composition; no default production initialization."""
    def __init__(self, directory, *, encoder, policy_version, source, query_cache_size=64):
        if type(query_cache_size) is not int or not 0 <= query_cache_size <= 64:
            raise ValueError("invalid embedding cache capacity")
        self.encoder, self.source = encoder, source
        self.cache_size = query_cache_size
        self._cache, self._cache_lock = OrderedDict(), threading.Lock()
        self.public = SQLiteReviewIndex(Path(directory) / "public.sqlite3", scope="public", encoder=encoder,
                                        policy_version=policy_version)
        self.blocked = SQLiteReviewIndex(Path(directory) / "blocked.sqlite3", scope="blocked", encoder=encoder,
                                         policy_version=policy_version)

    def refresh_threads(self, *, timeout=120):
        revision, documents = self.source.review_snapshot()
        return self.public.replace(documents, revision=revision, timeout=timeout, source_revision=self.source.revision)

    def __call__(self, submission, *, authorize, timeout):
        return self.search(submission, authorize=authorize, timeout=timeout, include_blocked=True)

    def _query_vectors(self, submission, deadline):
        # Cache only content-derived embeddings, never candidates, ACLs, model
        # verdicts, source metadata or raw submission text. Bound both entries
        # and total segments so long articles cannot create unbounded memory.
        key = _digest([self.encoder.version, submission.get("title", ""), submission.get("body", "")])
        now = time.monotonic()
        with self._cache_lock:
            for expired in [k for k, (until, _) in self._cache.items() if until <= now]:
                self._cache.pop(expired)
            if key in self._cache:
                self._cache.move_to_end(key)
                return copy.deepcopy(self._cache[key][1])
        vectors = self.public._vectors(self.encoder.document_vectors(submission, deadline=deadline))
        if self.cache_size:
            with self._cache_lock:
                self._cache[key] = (time.monotonic() + 300, copy.deepcopy(vectors))
                self._cache.move_to_end(key)
                while len(self._cache) > self.cache_size or sum(len(v) for _, v in self._cache.values()) > 256:
                    self._cache.popitem(last=False)
        return vectors

    def search(self, submission, *, authorize, timeout=5, include_blocked=False, limit=20):
        if type(limit) is not int or not 1 <= limit <= 20 or not callable(authorize):
            raise ValueError("bounded authorized search required")
        deadline = time.monotonic() + timeout
        revision = self.source.revision()
        self.public.assert_ready(revision)
        if include_blocked:
            self.blocked.assert_ready()
        vectors = self._query_vectors(submission, deadline)
        remaining = lambda: max(0, deadline - time.monotonic())
        ranked = self.public.rank(vectors, authorize=authorize, expected_revision=revision,
                                   exclude_id=submission.get("id"), timeout=remaining(), limit=limit)
        if include_blocked:
            ranked += self.blocked.rank(vectors, authorize=authorize, exclude_id=submission.get("id"),
                                        timeout=remaining(), limit=limit)
        ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
        if self.source.revision() != revision:
            raise ValueError("source changed during retrieval")
        if remaining() <= 0:
            raise TimeoutError("search deadline exceeded")
        result, seen = [], set()
        for _, document in ranked:
            if document["id"] in seen:
                raise ValueError("public and blocked candidate IDs collide")
            seen.add(document["id"])
            result.append(document)
        return result[:limit]
