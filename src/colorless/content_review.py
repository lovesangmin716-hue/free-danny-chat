"""Experimental joint-review contract. No model, network client or heuristic verdict.

Providers are trusted server collaborators, never values supplied by a browser.
All offsets count Python Unicode code points in the unmodified input field.
"""
from __future__ import annotations

import copy
import math
import queue
import threading
import time

TASK = "joint_content_review"
SCHEMA_VERSION = "1"
CHECKS = ("context_distortion", "duplicate_rewrite", "blocked_reupload")
STATUSES = {"pass", "violation", "undetermined"}


class ReviewFailure(Exception):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


def validate_response(request, response, *, stage="joint"):
    """Reject invented quotations, unknown candidates and inconsistent decisions."""
    if not isinstance(response, dict):
        raise ValueError("response must be an object")
    if (response.get("schema_version") != SCHEMA_VERSION
            or response.get("policy_version") != request["policy_version"]
            or not isinstance(response.get("model_version"), str)
            or not response["model_version"].strip()):
        raise ValueError("missing/mismatched versions")
    names = CHECKS if stage == "joint" else ("agitation",)
    checks = response.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(names):
        raise ValueError("all checks must be returned together")
    documents = {"submission": request["submission"]}
    documents.update({item["id"]: item for item in request["candidates"]})
    for name in names:
        check = checks[name]
        if not isinstance(check, dict) or not isinstance(check.get("status"), str) or check["status"] not in STATUSES:
            raise ValueError("invalid check")
        evidence = check.get("evidence")
        missing = check.get("missing_evidence")
        if (not isinstance(evidence, list) or len(evidence) > 40 or not isinstance(missing, list)
                or len(missing) > 20 or not all(isinstance(x, str) for x in missing)):
            raise ValueError("evidence lists required")
        if check["status"] == "undetermined" and not any(missing):
            raise ValueError("undetermined requires missing evidence")
        for span in evidence:
            if not isinstance(span, dict):
                raise ValueError("invalid evidence span")
            if not isinstance(span.get("document_id"), str):
                raise ValueError("invalid document id")
            document = documents.get(span.get("document_id"))
            field = span.get("field")
            start, end = span.get("start"), span.get("end")
            if (document is None or field not in ("title", "body")
                    or type(start) is not int or type(end) is not int):
                raise ValueError("invalid span source")
            text = document.get(field) or ""
            if not 0 <= start < end <= len(text) or text[start:end] != span.get("quote"):
                raise ValueError("evidence is not in input")
        if check["status"] == "violation":
            sources = {span["document_id"] for span in evidence}
            if "submission" not in sources:
                raise ValueError("violation requires submission evidence")
            if name in ("duplicate_rewrite", "blocked_reupload"):
                if not isinstance(check.get("candidate_id"), str):
                    raise ValueError("comparison candidate required")
                candidate = documents.get(check["candidate_id"])
                if candidate is None or candidate is request["submission"] or check["candidate_id"] not in sources:
                    raise ValueError("comparison requires candidate evidence")
                if name == "blocked_reupload" and candidate.get("scope") != "blocked":
                    raise ValueError("reupload requires private blocked record")
                if not isinstance(check.get("matching_information"), str) or not isinstance(check.get("new_information"), str):
                    raise ValueError("comparison explanation required")
            if name == "context_distortion" and not {"title", "body"} <= {span["field"] for span in evidence}:
                raise ValueError("distortion requires title and body evidence")
    statuses = {check["status"] for check in checks.values()}
    expected = "block" if "violation" in statuses else "hold" if "undetermined" in statuses else "allow"
    if response.get("decision") != expected:
        raise ValueError("inconsistent policy decision")
    return response


class ContentReview:
    """One retrieval, one joint inference, then a separate mandatory KAIST adapter.

    Providers accept keyword ``timeout`` and must enforce it on outbound I/O.
    A bounded daemon worker also bounds caller latency for a broken provider;
    timed-out workers retain their slot until they really finish.
    """
    def __init__(self, *, search, joint_model, kaist, policy_version, timeout=5.0, concurrency=2):
        if (not isinstance(policy_version, str) or not policy_version.strip()
                or not math.isfinite(timeout) or not 0 < timeout <= 60
                or type(concurrency) is not int or not 1 <= concurrency <= 16):
            raise ValueError("invalid review configuration")
        self.search, self.joint_model, self.kaist = search, joint_model, kaist
        self.policy_version, self.timeout = policy_version, timeout
        self.slots = threading.BoundedSemaphore(concurrency)

    def check(self, submission, *, authorize):
        if not self.slots.acquire(blocking=False):
            raise ReviewFailure("review_busy")
        result = queue.Queue(maxsize=1)
        deadline = time.monotonic() + self.timeout

        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError()
            return seconds

        def work():
            try:
                if not all(callable(x) for x in (self.search, self.joint_model, self.kaist)):
                    raise ReviewFailure("review_unavailable")
                candidates = self.search(copy.deepcopy(submission), timeout=remaining())
                if not isinstance(candidates, list) or len(candidates) > 20:
                    raise ValueError("bounded candidate list required")
                ids = set()
                for item in candidates:
                    if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                            or not item["id"] or item["id"] == "submission" or item["id"] in ids
                            or item.get("scope") not in ("public", "blocked")
                            or not isinstance(item.get("body"), str) or len(item["body"]) > 20000
                            or not isinstance(item.get("title", ""), str) or len(item.get("title", "")) > 2000):
                        raise ValueError("invalid search candidate")
                    ids.add(item["id"])
                    if item["scope"] == "blocked":
                        if item.get("policy_version") != self.policy_version or not item.get("block_reason"):
                            raise ValueError("stale blocked policy")
                        if (type(item.get("expires_at")) not in (int, float)
                                or not math.isfinite(item["expires_at"]) or not item["expires_at"] > time.time()):
                            raise ValueError("expired blocked record")
                    elif not authorize(item):
                        raise ValueError("candidate is stale or inaccessible")
                request = {"task": TASK, "schema_version": SCHEMA_VERSION, "policy_version": self.policy_version,
                           "submission": copy.deepcopy(submission), "candidates": copy.deepcopy(candidates)}
                response = self.joint_model(copy.deepcopy(request), timeout=remaining())
                validate_response(request, response)
                self._require_allow(response)
                # Do not forward joint output as instructions to the second model.
                second = self.kaist(copy.deepcopy(request), timeout=remaining())
                validate_response(request, second, stage="kaist")
                self._require_allow(second)
                remaining()
                result.put(None)
            except ReviewFailure as error:
                result.put(error)
            except Exception:
                # Never expose provider errors, blocked quotes, IDs or source URLs.
                result.put(ReviewFailure("review_unavailable"))
            finally:
                self.slots.release()

        worker = threading.Thread(target=work, name="content-review", daemon=True)
        try:
            worker.start()
        except Exception:
            self.slots.release()
            raise ReviewFailure("review_unavailable") from None
        try:
            error = result.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise ReviewFailure("review_timeout") from None
        if error:
            raise error

    @staticmethod
    def _require_allow(response):
        if response["decision"] == "block":
            name = next(name for name, check in response["checks"].items() if check["status"] == "violation")
            raise ReviewFailure("review_" + name, 422)
        if response["decision"] != "allow":
            raise ReviewFailure("review_undetermined")
