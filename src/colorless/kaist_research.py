"""Offline reproduction of Kim et al., USENIX Security 2026, Zenodo v2.1.

Architecture adapted from their code/model.py (CC BY 4.0), DOI
10.5281/zenodo.21348299. This runner initializes the architecture locally and
loads the complete fine-tuned checkpoint; it does not fetch base weights or
execute downloaded Python. These domain-specific scores are not a moderation
decision and this module is deliberately not a ContentReview provider.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from .review_artifacts import verify_kaist


VECTOR_HEADS = {"foreign_state_suspected": 1, "moral_emotion": 2,
                "condemning_targets": 5, "praising_targets": 5, "troll": 1}
SPAN_HEADS = ("foreign_state_suspected_span", "condemning_span", "praising_span") + tuple(
    f"{target}_{emotion}_span" for emotion in ("condemning", "praising")
    for target in ("korea", "mns", "mns_rivals", "mns_partners", "others"))
MODEL_VERSION = "kaist-cross-national-zenodo-21348299-v2.1"


def decode_spans(text, offsets, labels):
    """BIO 0/1/2 -> exact Unicode slices; special tokens never become evidence."""
    if len(offsets) != len(labels):
        raise ValueError("span shape mismatch")
    spans = []
    active = None
    for (start, end), label in zip(offsets, labels):
        if not (0 <= start <= end <= len(text)) or label not in (0, 1, 2):
            raise ValueError("invalid token span")
        if start == end or label == 0:
            if active is not None:
                spans.append(active)
                active = None
            continue
        # An orphan I starts a span rather than joining unrelated earlier text.
        # Byte fallback / normalization can assign several tokens to one Unicode
        # character. Keep that character intact even if a BIO boundary splits it.
        if active is not None and start < active[1]:
            if start < active[0]:
                raise ValueError("nonmonotonic token offsets")
            active[1] = max(active[1], end)
        elif label == 1 or active is None:
            if active is not None:
                spans.append(active)
            active = [start, end]
        else:
            active[1] = end
    if active is not None:
        spans.append(active)
    return [dict(start=start, end=end, quote=text[start:end]) for start, end in spans]


class KaistResearchModel:
    def __init__(self, directory, *, threads=2):
        # Optional heavy dependencies are never loaded by ordinary server/tests.
        import torch
        from transformers import AutoConfig, AutoModel, AutoTokenizer

        if type(threads) is not int or not 1 <= threads <= 16:
            raise ValueError("CPU threads must be between 1 and 16")
        directory = Path(directory)
        verify_kaist(directory)
        config = AutoConfig.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
        if config.model_type != "electra" or config.hidden_size != 768:
            raise ValueError("unexpected KAIST encoder architecture")

        class Network(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.bert = AutoModel.from_config(config, trust_remote_code=False)
                for name, width in {**VECTOR_HEADS, **dict.fromkeys(SPAN_HEADS, 3)}.items():
                    setattr(self, name + "_classifier", torch.nn.Linear(config.hidden_size, width))

            def forward(self, tokens):
                outputs = self.bert(**tokens, return_dict=True)
                sequence = outputs.last_hidden_state
                pooled = outputs.pooler_output if "pooler_output" in outputs else sequence[:, 0]
                vectors = {name: torch.sigmoid(getattr(self, name + "_classifier")(pooled))[0].tolist()
                           for name in VECTOR_HEADS}
                spans = {name: getattr(self, name + "_classifier")(sequence).argmax(-1)[0].tolist()
                         for name in SPAN_HEADS}
                return vectors, spans

        torch.set_num_threads(threads)
        self.torch = torch
        self.model = Network()
        state = torch.load(directory / "model.pth", map_location="cpu", weights_only=True)
        self.model.load_state_dict(state, strict=True)
        self.model.eval()
        self.tokenizer = AutoTokenizer.from_pretrained(directory, use_fast=True,
                                                       local_files_only=True, trust_remote_code=False)
        if not self.tokenizer.is_fast:
            raise ValueError("offset-capable tokenizer required")

    def predict(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise ValueError("research text must contain 1–20000 characters")
        started = time.perf_counter()
        tokens = self.tokenizer(text, return_offsets_mapping=True, return_tensors="pt", truncation=False)
        offsets = tokens.pop("offset_mapping")[0].tolist()
        count = tokens["input_ids"].shape[1]
        if count > 512:
            raise ValueError("text exceeds 512 tokens; split explicitly, never silently truncate")
        with self.torch.inference_mode():
            vectors, labels = self.model(tokens)
        spans = {name: decode_spans(text, offsets, values) for name, values in labels.items()}
        return dict(model_version=MODEL_VERSION, research_only=True, moderation_validated=False,
                    input_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), token_count=count,
                    probabilities=vectors, spans=spans,
                    latency_ms=round((time.perf_counter() - started) * 1000, 3))


def main(argv=None):
    parser = argparse.ArgumentParser(description="KAIST offline research scores, not moderation decisions")
    parser.add_argument("model_directory", type=Path)
    parser.add_argument("input", type=Path, help='JSONL: {"id":"...", "text":"..."}')
    parser.add_argument("output", type=Path)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)
    model = KaistResearchModel(args.model_directory, threads=args.threads)
    # Exclusive creation avoids overwriting reviewed datasets or earlier outputs.
    with args.input.open(encoding="utf-8-sig") as source, args.output.open("x", encoding="utf-8") as output:
        seen = set()
        for number, line in enumerate(source, 1):
            if number > 10000 or len(line) > 200000:
                raise ValueError("research input exceeds offline limit")
            row = json.loads(line)
            identity = row.get("id")
            if not isinstance(identity, str) or not identity or identity in seen:
                raise ValueError(f"invalid/duplicate input ID at line {number}")
            seen.add(identity)
            result = dict(id=identity, **model.predict(row.get("text")))
            output.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
