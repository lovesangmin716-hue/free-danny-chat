"""Explicit offline-research downloads; never imported by the chat server.

Only pinned, hash-checked files are saved. A range request must remain a range
request: downloading Zenodo's multi-gigabyte data archive is not a fallback.
"""
from __future__ import annotations

import argparse
import hashlib
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

import httpx


RANGE_CHUNK_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Artifact:
    name: str
    url: str
    size: int
    sha256: str
    # Inclusive compressed-data byte range, followed by the ZIP's total size.
    byte_range: tuple[int, int, int] | None = None


KAIST_REVISION = "681201a097b8120e6dd2a2e63a3e13d338736f88"
KAIST_BASE = f"https://huggingface.co/beomi/KcELECTRA-base/resolve/{KAIST_REVISION}/"
KAIST_ARCHIVE = "https://zenodo.org/records/21348299/files/code.zip?download=1"
KAIST_FILES = (
    Artifact("model.pth", KAIST_ARCHIVE, 434203698,
             "a49d9901256383935ac2140087f543e7a78f4ecddb74bbcf0490ae8eb1401556",
             (3586, 402717051, 2820120047)),
    Artifact("config.json", KAIST_BASE + "config.json", 514,
             "9be66e89461b56bf2c092881fcb567c271be62fc214915dcd4b7e79f25bdedeb"),
    Artifact("tokenizer.json", KAIST_BASE + "tokenizer.json", 1777527,
             "a5fa64ae82eb1ed3d2eb0bcb5034210c75cde7252b13a5ebfe4bfaff79a50dcd"),
    Artifact("tokenizer_config.json", KAIST_BASE + "tokenizer_config.json", 288,
             "b770f7717e85783b4dfaa813290901d35adf9babcbfe425f9b5ffff9973de99f"),
    Artifact("special_tokens_map.json", KAIST_BASE + "special_tokens_map.json", 124,
             "b3383b8739ae890383a6ed9ad871e592cc14d5b0c36ffdb9d3be6e416256640a"),
)

E5_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
E5_BASE = f"https://huggingface.co/intfloat/multilingual-e5-small/resolve/{E5_REVISION}/"
E5_FILES = (
    Artifact("model.onnx", E5_BASE + "onnx/model.onnx", 470268510,
             "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665"),
    Artifact("tokenizer.json", E5_BASE + "tokenizer.json", 17082730,
             "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"),
)


def verify_artifact(path, artifact):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != artifact.size:
        raise ValueError(f"missing or wrong-sized artifact: {artifact.name}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != artifact.sha256:
        raise ValueError(f"artifact hash mismatch: {artifact.name}")


def _download_chunks(client, artifact):
    if artifact.byte_range:
        first, last, total = artifact.byte_range
        step = RANGE_CHUNK_BYTES
        ranges = [(start, min(start + step - 1, last)) for start in range(first, last + 1, step)]
    else:
        ranges = [None]
    for interval in ranges:
        headers = {"Accept-Encoding": "identity"}
        if interval:
            start, end = interval
            headers["Range"] = f"bytes={start}-{end}"
        with client.stream("GET", artifact.url, headers=headers) as response:
            response.raise_for_status()
            if interval:
                if (response.status_code != 206 or
                        response.headers.get("Content-Range") != f"bytes {start}-{end}/{total}"):
                    raise ValueError("server did not honor the pinned byte range")
                expected = end - start + 1
            else:
                if response.status_code != 200:
                    raise ValueError("unexpected artifact response")
                expected = artifact.size
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise ValueError("unexpected transfer encoding")
            received = 0
            for chunk in response.iter_bytes(256 * 1024):
                received += len(chunk)
                if received > expected:
                    raise ValueError("artifact transfer exceeds pinned size")
                yield chunk
            if received != expected:
                raise ValueError("truncated artifact transfer")


def fetch_artifact(client, directory, artifact):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / artifact.name
    if target.resolve().parent != directory:
        raise ValueError("artifact path escapes destination")
    if target.exists():
        verify_artifact(target, artifact)
        return target
    decoder = zlib.decompressobj(-15) if artifact.byte_range else None
    temporary = None
    try:
        written = 0
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".download-", delete=False) as output:
            temporary = Path(output.name)
            for chunk in _download_chunks(client, artifact):
                data = decoder.decompress(chunk, artifact.size - written + 1) if decoder else chunk
                written += len(data)
                if written > artifact.size:
                    raise ValueError("decompressed artifact exceeds pinned size")
                output.write(data)
            if written != artifact.size:
                raise ValueError("truncated artifact")
            if decoder and (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail):
                raise ValueError("invalid compressed artifact")
        verify_artifact(temporary, artifact)
        temporary.replace(target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verify_kaist(directory):
    for artifact in KAIST_FILES:
        verify_artifact(Path(directory) / artifact.name, artifact)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download pinned offline research models")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--model", choices=("kaist", "e5"), default="kaist")
    args = parser.parse_args(argv)
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for artifact in E5_FILES if args.model == "e5" else KAIST_FILES:
            path = fetch_artifact(client, args.directory, artifact)
            print(f"verified {path.name}: {artifact.size} bytes")


if __name__ == "__main__":
    main()
