"""Isolated exact-vector reference index, for small offline evaluation only.

No production database writes, network fetches or API routes. Embeddings must
come from an independently evaluated multilingual retrieval model.
"""
import copy
import math
import threading
import time


class ReviewIndex:
    def __init__(self, *, embedding_version, dimensions, policy_version, capacity=10000):
        if not embedding_version or not policy_version or type(dimensions) is not int or dimensions < 1:
            raise ValueError("embedding model, dimensions and policy required")
        if type(capacity) is not int or not 1 <= capacity <= 100000:
            raise ValueError("invalid capacity")
        self.embedding_version, self.dimensions, self.policy_version = embedding_version, dimensions, policy_version
        self.capacity = capacity
        self._public, self._blocked = {}, {}
        self._lock = threading.RLock()

    def _vector(self, values):
        if (not isinstance(values, (list, tuple)) or len(values) != self.dimensions
                or any(type(x) not in (int, float) or not math.isfinite(x) for x in values)):
            raise ValueError("invalid embedding")
        norm = math.hypot(*values)
        if not math.isfinite(norm) or norm == 0:
            raise ValueError("zero/overflow embedding")
        return tuple(x / norm for x in values)

    def upsert(self, document, vector, *, embedding_version):
        with self._lock:
            self._upsert(document, vector, embedding_version=embedding_version)

    def _upsert(self, document, vector, *, embedding_version):
        if embedding_version != self.embedding_version:
            raise ValueError("reindex required")
        document = copy.deepcopy(document)
        identity, scope = document.get("id"), document.get("scope")
        if not isinstance(identity, str) or not identity or identity == "submission" or scope not in ("public", "blocked"):
            raise ValueError("invalid index document")
        if not isinstance(document.get("body"), str) or not isinstance(document.get("title", ""), str):
            raise ValueError("text required")
        if scope == "blocked" and (document.get("policy_version") != self.policy_version
                or not document.get("block_reason") or type(document.get("expires_at")) not in (int, float)
                or not math.isfinite(document["expires_at"]) or document["expires_at"] <= time.time()):
            raise ValueError("blocked record requires current policy, reason and finite TTL")
        entry = document, self._vector(vector)
        with self._lock:
            if identity not in self._public and identity not in self._blocked and len(self._public) + len(self._blocked) >= self.capacity:
                raise ValueError("index capacity reached")
            self.remove(identity)
            (self._public if scope == "public" else self._blocked)[identity] = entry

    def remove(self, identity):
        """Call for deletion, followers visibility, reversal or source withdrawal."""
        with self._lock:
            self._public.pop(identity, None)
            self._blocked.pop(identity, None)

    def clear(self, *, embedding_version, dimensions, policy_version):
        # Construct first so an invalid version change cannot lose the old index.
        replacement = ReviewIndex(embedding_version=embedding_version, dimensions=dimensions,
                                  policy_version=policy_version, capacity=self.capacity)
        with self._lock:
            self._public.clear()
            self._blocked.clear()
            self.embedding_version = replacement.embedding_version
            self.dimensions = replacement.dimensions
            self.policy_version = replacement.policy_version

    def search_public(self, vector, *, authorize, limit=20):
        return self._search(vector, authorize=authorize, include_blocked=False, limit=limit)

    def search_for_review(self, vector, *, authorize, limit=20):
        """Server-only capability. Never use for public feed/API responses."""
        return self._search(vector, authorize=authorize, include_blocked=True, limit=limit)

    def _search(self, vector, *, authorize, include_blocked, limit):
        if type(limit) is not int or not 1 <= limit <= 20 or not callable(authorize):
            raise ValueError("bounded authorized search required")
        with self._lock:
            query = self._vector(vector)
            now = time.time()
            for identity, (document, _) in list(self._blocked.items()):
                if document["expires_at"] <= now or document["policy_version"] != self.policy_version:
                    del self._blocked[identity]
            entries = copy.deepcopy(list(self._public.values()) + (list(self._blocked.values()) if include_blocked else []))
        scored = []
        for document, embedding in entries:
            # Filter before top-k, so inaccessible hits cannot crowd out valid ones.
            if document["scope"] == "public" and not authorize(copy.deepcopy(document)):
                continue
            scored.append((sum(a * b for a, b in zip(query, embedding)), document["id"], document))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored[:limit]]
