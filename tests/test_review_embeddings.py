import unittest
from types import SimpleNamespace

from colorless.review_embeddings import E5Encoder


class EmbeddingSegmentationTest(unittest.TestCase):
    def setUp(self):
        # Token boundary fixture only: real model/tokenizer run separately.
        self.encoder = E5Encoder.__new__(E5Encoder)
        def tokens(text):
            text = self.encoder.prefix + text
            return SimpleNamespace(ids=[0] * (len(text) + 2),
                                   offsets=[(0, 0)] + [(i, i + 1) for i in range(len(text))] + [(0, 0)])
        self.encoder._tokens = tokens

    def test_short_full_text_and_paragraph_offsets_preserve_unicode(self):
        text = "한글😀\nEnglish text\n 마지막 문단"
        segments = self.encoder.segments(text)
        self.assertIn((0, len(text), text), segments)
        self.assertIn((0, 3, "한글😀"), segments)
        for start, end, segment in segments:
            self.assertEqual(text[start:end], segment)

    def test_long_windows_cover_every_nonblank_character_including_tail(self):
        text = "가😀" * 2000 + "\nfinal tail"
        segments = self.encoder.segments(text)
        covered = set()
        for start, end, segment in segments:
            self.assertLessEqual(len(self.encoder._tokens(segment).ids), 512)
            self.assertEqual(text[start:end], segment)
            covered.update(range(start, end))
        self.assertTrue(all(i in covered for i, char in enumerate(text) if not char.isspace()))
        self.assertEqual(max(end for _, end, _ in segments), len(text))
        for invalid in ("", "   ", "x" * 20001, None):
            with self.assertRaises(ValueError):
                self.encoder.segments(invalid)


if __name__ == "__main__":
    unittest.main()
