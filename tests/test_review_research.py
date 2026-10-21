import hashlib
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import httpx

from colorless.kaist_research import decode_spans
from colorless.review_artifacts import Artifact, fetch_artifact, verify_artifact


class ResearchArtifactsTest(unittest.TestCase):
    def setUp(self):
        self.body = b"small pinned research weights"
        compressor = zlib.compressobj(wbits=-15)
        self.compressed = compressor.compress(self.body) + compressor.flush()
        self.end = 10 + len(self.compressed) - 1
        self.artifact = Artifact("model.pth", "https://example.test/code.zip", len(self.body),
                                 hashlib.sha256(self.body).hexdigest(), (10, self.end, 1000))

    def client(self, *, status=206, body=None, headers=None):
        def serve(request):
            self.assertEqual(request.headers["Range"], f"bytes=10-{self.end}")
            return httpx.Response(status, content=self.compressed if body is None else body,
                                  headers=headers or {"Content-Range": f"bytes 10-{self.end}/1000"})
        return httpx.Client(transport=httpx.MockTransport(serve))

    def test_partial_download_is_verified_and_existing_files_are_rechecked(self):
        with tempfile.TemporaryDirectory() as folder, self.client() as client:
            path = fetch_artifact(client, folder, self.artifact)
            self.assertEqual(path.read_bytes(), self.body)
            path.write_bytes(b"x" * len(self.body))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                fetch_artifact(client, folder, self.artifact)
            self.assertEqual(path.read_bytes(), b"x" * len(self.body))

    def test_ignored_range_wrong_range_truncation_and_corruption_leave_no_file(self):
        cases = [dict(status=200), dict(headers={"Content-Range": "bytes 0-1/1000"}),
                 dict(body=self.compressed[:-1]), dict(body=b"x" * len(self.compressed)),
                 dict(headers={"Content-Range": f"bytes 10-{self.end}/1000", "Content-Encoding": "gzip"})]
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder, self.client(**case) as client:
                with self.assertRaises((ValueError, zlib.error, httpx.DecodingError)):
                    fetch_artifact(client, folder, self.artifact)
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_decompression_bomb_is_bounded_and_hash_failure_is_atomic(self):
        compressor = zlib.compressobj(wbits=-15)
        self.compressed = compressor.compress(b"x" * 100000) + compressor.flush()
        self.end = 10 + len(self.compressed) - 1
        artifact = Artifact("model.pth", self.artifact.url, 10, "0" * 64, (10, self.end, 1000))
        with tempfile.TemporaryDirectory() as folder, self.client() as client:
            with self.assertRaisesRegex(ValueError, "exceeds pinned size"):
                fetch_artifact(client, folder, artifact)
            self.assertEqual(list(Path(folder).iterdir()), [])
        artifact = Artifact("model.pth", self.artifact.url, 100000, "0" * 64, (10, self.end, 1000))
        with tempfile.TemporaryDirectory() as folder, self.client() as client:
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                fetch_artifact(client, folder, artifact)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_unicode_offsets_and_special_tokens_are_not_utf16_offsets(self):
        text = "가😀 나 다"
        spans = decode_spans(text, [(0, 0), (0, 1), (1, 2), (3, 4), (5, 6), (0, 0)],
                             [1, 1, 2, 0, 2, 1])
        self.assertEqual(spans, [dict(start=0, end=2, quote="가😀"), dict(start=5, end=6, quote="다")])
        self.assertEqual(decode_spans(text, [(1, 2), (1, 2)], [1, 1]),
                         [dict(start=1, end=2, quote="😀")])
        for offsets, labels in [([(0, 20)], [1]), ([(0, 1)], [3]), ([(0, 1)], []),
                                 ([(1, 2), (0, 1)], [1, 2])]:
            with self.assertRaises(ValueError):
                decode_spans(text, offsets, labels)

    def test_range_chunks_preserve_one_deflate_stream_and_validate_each_response(self):
        requested = []

        def serve(request):
            start, end = map(int, request.headers["Range"].removeprefix("bytes=").split("-"))
            requested.append((start, end))
            return httpx.Response(206, content=self.compressed[start - 10:end - 9],
                                  headers={"Content-Range": f"bytes {start}-{end}/1000"})

        with tempfile.TemporaryDirectory() as folder, httpx.Client(transport=httpx.MockTransport(serve)) as client:
            with patch("colorless.review_artifacts.RANGE_CHUNK_BYTES", 7):
                path = fetch_artifact(client, folder, self.artifact)
            self.assertEqual(path.read_bytes(), self.body)
            self.assertGreater(len(requested), 1)
            self.assertEqual(requested[0][0], 10)
            self.assertEqual(requested[-1][1], self.end)


if __name__ == "__main__":
    unittest.main()
