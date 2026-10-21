"""Offline, evidence-checked evaluation of actual joint-model predictions.

No inference is synthesized here. Synthetic examples require an explicit flag
and are visibly marked unsuitable for model acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from .content_review import CHECKS, validate_request, validate_response


def request_fingerprint(request):
    encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def read_jsonl(path):
    rows = []
    with Path(path).open(encoding="utf-8-sig") as source:
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            if len(line) > 2_000_000 or len(rows) >= 100_000:
                raise ValueError("evaluation input exceeds the offline limit")
            try:
                row = json.loads(line)
            except ValueError:
                raise ValueError(f"invalid JSONL at line {number}") from None
            if not isinstance(row, dict):
                raise ValueError(f"expected an object at line {number}")
            rows.append(row)
    return rows


def validate_dataset(rows, *, allow_synthetic=False):
    examples, clusters = {}, {}
    for row in rows:
        identity, cluster, split = row.get("example_id"), row.get("cluster_id"), row.get("split")
        if not isinstance(identity, str) or not identity or identity in examples:
            raise ValueError("missing or repeated example_id")
        if not isinstance(cluster, str) or not cluster or split not in ("train", "validation", "test"):
            raise ValueError("cluster_id and valid split required")
        if clusters.setdefault(cluster, split) != split:
            raise ValueError("cluster leaks across dataset splits")
        annotation = row.get("annotation_status")
        if annotation != "human_reviewed" and not (allow_synthetic and annotation == "synthetic"):
            raise ValueError("human-reviewed data required; synthetic fixtures are not acceptance evidence")
        request = validate_request(row.get("request"))
        if request["submission"]["language"] == "auto":
            raise ValueError("evaluation language must be labeled ko/en/mixed")
        relevant = row.get("relevant_candidate_ids")
        if relevant is not None and (not isinstance(relevant, list) or not relevant
                or any(not isinstance(item, str) or not item for item in relevant)
                or len(set(relevant)) != len(relevant)):
            raise ValueError("relevance labels must be unique, nonempty IDs")
        validate_response(request, row.get("response"))
        request_fingerprint(request)
        examples[identity] = row
    if not examples:
        raise ValueError("empty dataset")
    return examples


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def evaluate(dataset, predictions, *, split="test", allow_synthetic=False):
    examples = validate_dataset(dataset, allow_synthetic=allow_synthetic)
    selected = {key: row for key, row in examples.items() if row["split"] == split}
    if not selected:
        raise ValueError("selected split has no examples")
    by_id = {}
    for prediction in predictions:
        identity = prediction.get("example_id")
        if not isinstance(identity, str) or identity not in selected or identity in by_id:
            raise ValueError("unknown, duplicate or wrong-split prediction")
        by_id[identity] = prediction
    if set(by_id) != set(selected):
        raise ValueError("every selected example requires exactly one prediction")
    report = {"split": split, "examples": len(selected), "acceptance_eligible": all(
        row["annotation_status"] == "human_reviewed" for row in selected.values()), "models": [], "languages": {}}
    versions = set()
    for identity, row in selected.items():
        prediction, request = by_id[identity], row["request"]
        if prediction.get("request_sha256") != request_fingerprint(request):
            raise ValueError("prediction does not match the exact evaluated request")
        response = validate_response(request, prediction.get("response"))
        versions.add((response["model_version"], response["policy_version"]))
        elapsed = prediction.get("latency_ms")
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("finite measured latency_ms required")
        language = request["submission"]["language"]
        bucket = report["languages"].setdefault(language, {"examples": 0, "latencies": [], "final_correct": 0,
            "checks": {name: {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "undetermined": 0,
                              "determinate_labels": 0, "matched_status": 0} for name in CHECKS},
            "recalls": {5: [], 10: [], 20: []}})
        bucket["examples"] += 1
        bucket["latencies"].append(elapsed)
        bucket["final_correct"] += int(response["decision"] == row["response"]["decision"])
        for name in CHECKS:
            expected, actual = row["response"]["checks"][name]["status"], response["checks"][name]["status"]
            counts = bucket["checks"][name]
            counts["matched_status"] += int(expected == actual)
            counts["undetermined"] += int(actual == "undetermined")
            if expected != "undetermined":
                counts["determinate_labels"] += 1
                key = ("tp" if actual == "violation" else "fn") if expected == "violation" else ("fp" if actual == "violation" else "tn")
                counts[key] += 1
        relevant = row.get("relevant_candidate_ids")
        if relevant:
            retrieved = [candidate["id"] for candidate in request["candidates"]]
            for k in bucket["recalls"]:
                bucket["recalls"][k].append(len(set(retrieved[:k]) & set(relevant)) / len(relevant))
    if len(versions) != 1:
        raise ValueError("evaluate each model and policy version in a separate run")
    report["models"] = [{"model_version": model, "policy_version": policy} for model, policy in versions]
    for bucket in report["languages"].values():
        latencies = sorted(bucket.pop("latencies"))
        bucket["latency_p95_ms"] = latencies[max(0, math.ceil(len(latencies) * .95) - 1)]
        bucket["final_accuracy"] = bucket.pop("final_correct") / bucket["examples"]
        bucket["retrieval_recall"] = {f"at_{k}": ratio(sum(values), len(values)) for k, values in bucket.pop("recalls").items()}
        for counts in bucket["checks"].values():
            counts.update(precision=ratio(counts["tp"], counts["tp"] + counts["fp"]),
                          recall=ratio(counts["tp"], counts["tp"] + counts["fn"]),
                          false_positive_rate=ratio(counts["fp"], counts["fp"] + counts["tn"]),
                          false_negative_rate=ratio(counts["fn"], counts["fn"] + counts["tp"]),
                          undetermined_rate=counts["undetermined"] / bucket["examples"],
                          status_accuracy=counts.pop("matched_status") / bucket["examples"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--split", choices=("train", "validation", "test"), default="test")
    parser.add_argument("--allow-synthetic", action="store_true", help="contract smoke tests only; never model acceptance")
    arguments = parser.parse_args()
    try:
        report = evaluate(read_jsonl(arguments.dataset), read_jsonl(arguments.predictions), split=arguments.split,
                          allow_synthetic=arguments.allow_synthetic)
    except (ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
