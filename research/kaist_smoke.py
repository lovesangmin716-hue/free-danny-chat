"""Optional real-weight smoke test; no model downloads occur during CI tests."""
import argparse
import json
import math
import platform
import statistics
import time
from pathlib import Path

import httpx

from colorless.kaist_research import KaistResearchModel, VECTOR_HEADS, SPAN_HEADS
from colorless.review_artifacts import Artifact, KAIST_ARCHIVE, KAIST_FILES, fetch_artifact


SAMPLE = Artifact("sample.parquet", KAIST_ARCHIVE, 140851,
                  "40ab0bb035f4db8add527a5ef95432da3cd1258c62a70404c9c9e6d065363a10",
                  (2819975854, 2820114501, 2820120047))


def main():
    import numpy
    import polars
    import tokenizers
    import torch
    import transformers

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_directory", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError("report already exists; choose a new report path")
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        sample = fetch_artifact(client, args.model_directory, SAMPLE)
    inputs = polars.read_parquet(sample)["full_text"].head(20).to_list()
    inputs += ["오늘 도서관에서 책을 읽었습니다.", "I read a book at the library today.", "가😀 나 다"]
    started = time.perf_counter()
    model = KaistResearchModel(args.model_directory, threads=2)
    load_ms = (time.perf_counter() - started) * 1000
    results = []
    rejected = 0
    for text in inputs:
        try:
            result = model.predict(text)
        except ValueError as error:
            if "512 tokens" not in str(error):
                raise
            rejected += 1
            continue
        assert result["research_only"] and not result["moderation_validated"] and "decision" not in result
        assert set(result["probabilities"]) == set(VECTOR_HEADS)
        assert set(result["spans"]) == set(SPAN_HEADS)
        assert all(len(result["probabilities"][key]) == width for key, width in VECTOR_HEADS.items())
        assert all(math.isfinite(v) and 0 <= v <= 1 for values in result["probabilities"].values() for v in values)
        assert all(text[s["start"]:s["end"]] == s["quote"] for spans in result["spans"].values() for s in spans)
        results.append(result)
    # Real tokenizer/input validation must reject long input, without inference.
    for invalid in ("", "x" * 20001, "일반적인 긴 문장입니다. " * 300):
        try:
            model.predict(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid input was accepted")
    latencies = [row["latency_ms"] for row in results]
    warm = sorted(latencies[1:])
    report = dict(date=time.strftime("%Y-%m-%d"),
                  scope="local CPU smoke only; no ground-truth accuracy or service throughput evaluation",
                  model_version=results[0]["model_version"], checkpoint_sha256=KAIST_FILES[0].sha256,
                  sample_sha256=SAMPLE.sha256,
                  environment=dict(python=platform.python_version(), platform=platform.platform(),
                                   processor=platform.processor(), torch=torch.__version__,
                                   transformers=transformers.__version__, tokenizers=tokenizers.__version__,
                                   numpy=numpy.__version__, cpu_threads=2),
                  load_ms=round(load_ms, 3), requested=len(inputs), completed=len(results),
                  rejected_over_512_tokens=rejected, invalid_inputs_rejected=3,
                  public_sample_inputs=20, synthetic_smoke_inputs=3,
                  token_range=[min(r["token_count"] for r in results), max(r["token_count"] for r in results)],
                  first_inference_ms=latencies[0], warm_median_ms=statistics.median(warm),
                  warm_p95_ms=warm[math.ceil(len(warm) * .95) - 1], quality_metrics=None,
                  moderation_validated=False)
    with args.report.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps(report, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
