"""Real E5 + isolated SQLite Threads lifecycle smoke; synthetic text, no accuracy claim."""
import argparse
import json
import math
import os
import platform
import statistics
import tempfile
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_directory", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError("report already exists")
    with tempfile.TemporaryDirectory(prefix="colorless-e5-smoke-") as folder:
        # Set before importing configuration: never consume a workspace .env or
        # Supabase credentials while creating experimental fixture data.
        os.environ.update(SUPABASE_URL="", SUPABASE_SERVICE_ROLE_KEY="", REQUIRE_SUPABASE="false",
                          COLORLESS_ENV_FILE=str(Path(folder) / "no-env"))
        from colorless.state import StateStore
        from colorless.threads import Threads
        from colorless.threads_repository import ThreadRepository
        from colorless.review_embeddings import E5Encoder
        from colorless.review_store import PersistentReviewSearch
        import numpy
        import onnxruntime
        import tokenizers

        started = time.perf_counter()
        encoder = E5Encoder(args.model_directory, threads=2)
        load_ms = (time.perf_counter() - started) * 1000
        store = StateStore(Path(folder) / "state.json")
        try:
            user, error = store.create_local_user("retrieval-smoke", "retrieval_smoke", "fixture-password", "",
                                                   "01012345678", "20대", "남성")
            if error:
                raise RuntimeError(error)
            def write(action, payload):
                return Threads(store, user).write(action, payload)
            def post(body, visibility="public"):
                return write("create", dict(body=body, visibility=visibility, clientId=str(time.time_ns())))["id"]
            ko = "오늘 도서관에서 책을 읽었습니다."
            en = "I read a book at the library today."
            ko_id, en_id = post(ko), post(en)
            rocket = post("The rocket launched into orbit.")
            private = post(ko + " private marker", "followers")
            source = ThreadRepository(store.repository)
            search = PersistentReviewSearch(Path(folder) / "index", encoder=encoder, policy_version="smoke",
                                            source=source, query_cache_size=0)
            first_build = search.refresh_threads()
            search.blocked.replace([], revision=0)  # New fixture has no block records.
            assert first_build["documents"] == 3

            def authorize(candidate):
                rows = source.rows("thread_posts", {"id": f"eq.{candidate['id']}"}, limit=1)
                return bool(rows and not rows[0]["deleted"] and rows[0]["visibility"] == "public"
                            and rows[0]["body"] == candidate["body"]
                            and Threads(store, user).visible(rows[0], user["id"]))
            def query(body, identity):
                return search(dict(id=identity, body=body, title=""), authorize=authorize, timeout=10)

            latencies = []
            for index in range(20):
                body, identity, counterpart = (ko, ko_id, en_id) if index % 2 == 0 else (en, en_id, ko_id)
                started = time.perf_counter()
                hits = query(body, identity)
                latencies.append((time.perf_counter() - started) * 1000)
                assert hits[0]["id"] == counterpart
                assert identity not in [hit["id"] for hit in hits] and private not in [hit["id"] for hit in hits]
            write("like", dict(id=rocket, enabled=True))
            metadata_refresh = search.refresh_threads()
            assert metadata_refresh["embedded_documents"] == 0
            stale_rejected = 0
            for action, payload in (("edit", dict(id=en_id, body="An updated report with new information.")),
                                    ("delete", dict(id=en_id))):
                write(action, payload)
                try:
                    query(ko, ko_id)
                except ValueError as error:
                    assert "stale" in str(error)
                    stale_rejected += 1
                else:
                    raise AssertionError("stale source was accepted")
                refresh = search.refresh_threads()
                assert refresh["embedded_documents"] == (1 if action == "edit" else 0)
            assert en_id not in [hit["id"] for hit in query(ko, ko_id)]
            search.cache_size = 64
            query(ko, ko_id)
            cached_latencies = []
            for _ in range(20):
                started = time.perf_counter()
                query(ko, ko_id)
                cached_latencies.append((time.perf_counter() - started) * 1000)
            long = "첫 문단입니다.\n" + "추가 정보가 담긴 긴 본문입니다. " * 200 + "\n마지막 정보입니다."
            segments = encoder.segments(long)
            vectors = encoder.document_vectors(dict(body=long), deadline=time.monotonic() + 120)
            assert segments[-1][1] == len(long) and len(vectors) == len(segments)
            assert all(len(v) == 384 and abs(math.hypot(*v) - 1) < 1e-5 for _, v in vectors)
            report = dict(date=time.strftime("%Y-%m-%d"), scope=__doc__, quality_metrics=None,
                          model_version=encoder.version, dimensions=384, model_load_ms=round(load_ms, 3),
                          environment=dict(python=platform.python_version(), platform=platform.platform(),
                                           onnxruntime=onnxruntime.__version__, tokenizers=tokenizers.__version__,
                                           numpy=numpy.__version__, cpu_threads=2),
                          initial_public_documents=3, excluded_private_documents=1,
                          metadata_only_reembedded_documents=0, stale_mutations_rejected=stale_rejected,
                          long_text_characters=len(long), long_text_segments=len(segments),
                          retrieval_requests=len(latencies), retrieval_median_ms=round(statistics.median(latencies), 3),
                          retrieval_p95_ms=round(sorted(latencies)[math.ceil(len(latencies) * .95) - 1], 3),
                          cached_retrieval_requests=20,
                          cached_retrieval_p95_ms=round(sorted(cached_latencies)[18], 3),
                          includes="query embedding + exact search + authoritative DB ACL + revision checks",
                          excludes="HTTP, joint classification, KAIST, commit, notifications and throughput")
            with args.report.open("x", encoding="utf-8") as output:
                json.dump(report, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.write("\n")
            print(json.dumps(report, ensure_ascii=True))
        finally:
            store.close()


if __name__ == "__main__":
    main()
