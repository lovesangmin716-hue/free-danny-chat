"""Pinned multilingual E5 CPU embeddings, loaded only by explicit experiments."""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from .review_artifacts import E5_FILES, E5_REVISION, verify_artifact


class E5Encoder:
    dimensions = 384
    # Prefix, pooling and segmentation changes require a fresh namespace too.
    version = f"multilingual-e5-small:{E5_REVISION}:query-mean-l2:windows-v1"
    prefix = "query: "

    def __init__(self, directory, *, threads=2):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if type(threads) is not int or not 1 <= threads <= 16:
            raise ValueError("invalid CPU thread count")
        for artifact in E5_FILES:
            verify_artifact(Path(directory) / artifact.name, artifact)
        self.np = np
        self.tokenizer = Tokenizer.from_file(str(Path(directory) / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.lock = threading.Lock()
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(Path(directory) / "model.onnx"), sess_options=options,
                                            providers=["CPUExecutionProvider"])
        self.inputs = {item.name for item in self.session.get_inputs()}
        if not {"input_ids", "attention_mask"} <= self.inputs or self.inputs - {"input_ids", "attention_mask", "token_type_ids"}:
            raise ValueError("unexpected E5 graph inputs")
        self.output = self.session.get_outputs()[0].name

    def _tokens(self, text):
        with self.lock:
            return self.tokenizer.encode(self.prefix + text)

    def segments(self, text):
        """Full short text + paragraphs, or overlapping windows of long text.

        The entire unmodified text remains in the candidate document. Segment
        IDs describe original character offsets, not a truncated replacement.
        """
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise ValueError("bounded nonempty embedding text required")
        encoded = self._tokens(text)
        spans = set()
        if len(encoded.ids) <= 512:
            spans.add((0, len(text)))
        else:
            prefix = len(self.prefix)
            offsets = [(max(0, start - prefix), min(len(text), end - prefix))
                       for start, end in encoded.offsets if end > prefix and start < prefix + len(text)]
            for index in range(0, len(offsets), 320):
                window = offsets[index:index + 384]
                start = 0 if index == 0 else window[0][0]
                end = len(text) if index + 384 >= len(offsets) else window[-1][1]
                if start < end:
                    spans.add((start, end))
                if end == len(text):
                    break
        for match in re.finditer(r"[^\n]+", text):
            if match.span() not in spans and match.group().strip() and len(self._tokens(match.group()).ids) <= 512:
                spans.add(match.span())
        if not spans or len(spans) > 128:
            raise ValueError("document exceeds segment capacity")
        # Check coverage instead of silently losing a long tail or tokenizer gap.
        covered = 0
        for start, end in sorted(spans):
            if start > covered and text[covered:start].strip():
                raise ValueError("embedding segments omit source content")
            covered = max(covered, end)
        if text[covered:].strip():
            raise ValueError("embedding segments omit source tail")
        return [(start, end, text[start:end]) for start, end in sorted(spans)]

    def encode(self, texts, *, deadline):
        if not isinstance(texts, list) or not 1 <= len(texts) <= 128:
            raise ValueError("bounded embedding batch required")
        if any(not isinstance(text, str) or not text.strip() or len(text) > 20000 for text in texts):
            raise ValueError("invalid embedding text")
        vectors = []
        np = self.np
        for offset in range(0, len(texts), 8):
            if time.monotonic() >= deadline:
                raise TimeoutError("embedding deadline exceeded")
            batch = [self._tokens(text) for text in texts[offset:offset + 8]]
            width = max(len(item.ids) for item in batch)
            if width > 512:
                raise ValueError("embedding exceeds 512 tokens; segment explicitly")
            ids = np.full((len(batch), width), 1, dtype=np.int64)
            mask = np.zeros_like(ids)
            for index, item in enumerate(batch):
                ids[index, :len(item.ids)] = item.ids
                mask[index, :len(item.ids)] = 1
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run([self.output], feed)[0]
            if hidden.shape != (len(batch), width, self.dimensions) or not np.isfinite(hidden).all():
                raise ValueError("invalid E5 hidden states")
            pooled = (hidden * mask[:, :, None]).sum(1) / mask.sum(1)[:, None]
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            if not np.isfinite(norms).all() or (norms <= 0).any():
                raise ValueError("invalid E5 vector norms")
            vectors.extend((pooled / norms).tolist())
        if time.monotonic() >= deadline:
            raise TimeoutError("embedding deadline exceeded")
        return vectors

    def document_vectors(self, document, *, deadline):
        parts = []
        for field in ("title", "body"):
            text = document.get(field, "")
            if not isinstance(text, str):
                raise ValueError("embedding document fields must be strings")
            if text.strip():
                parts.extend((f"{field}:{start}:{end}", segment) for start, end, segment in self.segments(text))
        if not parts or len(parts) > 128:
            raise ValueError("invalid document segment count")
        vectors = self.encode([part[1] for part in parts], deadline=deadline)
        return [(part[0], vector) for part, vector in zip(parts, vectors)]
