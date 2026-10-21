import copy
import json
import tempfile
import unittest
from pathlib import Path

from colorless.content_review import CHECKS
from colorless.review_evaluation import evaluate, read_jsonl, request_fingerprint, validate_dataset


class ReviewEvaluationTest(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'contracts' / 'content-review-examples.jsonl'
        self.rows = read_jsonl(path)
        for row in self.rows:
            row['split'] = 'test'
            row['response']['decision'] = 'allow'
            for check in row['response']['checks'].values():
                check.update(status='pass', evidence=[], missing_evidence=[])

    def predictions(self):
        return [dict(example_id=row['example_id'], request_sha256=request_fingerprint(row['request']),
                     response=copy.deepcopy(row['response']), latency_ms=10+index)
                for index, row in enumerate(self.rows)]

    def test_synthetic_fixtures_are_rejected_by_default_and_never_acceptance_evidence(self):
        with self.assertRaisesRegex(ValueError, 'human-reviewed'):
            evaluate(self.rows, self.predictions())
        report = evaluate(self.rows, self.predictions(), allow_synthetic=True)
        self.assertFalse(report['acceptance_eligible'])
        self.assertEqual(set(report['languages']), {'ko', 'en'})
        self.assertEqual(report['languages']['ko']['final_accuracy'], 1)
        self.assertIsNone(report['languages']['en']['retrieval_recall']['at_20'])
        self.assertIsNone(report['languages']['ko']['checks']['duplicate_rewrite']['recall'])
        self.assertNotIn('throughput', report)

    def test_duplicate_predictions_missing_predictions_and_mismatched_input_are_rejected(self):
        predictions = self.predictions()
        for invalid in (predictions[:1], predictions + predictions[:1],
                        [{**predictions[0], 'example_id': 'unknown'}, predictions[1]],
                        [{**predictions[0], 'request_sha256': 'wrong'}, predictions[1]]):
            with self.assertRaises(ValueError):
                evaluate(self.rows, invalid, allow_synthetic=True)

    def test_cluster_split_leak_and_unreviewed_annotations_are_rejected(self):
        self.rows[1]['split'] = 'train'
        with self.assertRaisesRegex(ValueError, 'cluster leaks'):
            validate_dataset(self.rows, allow_synthetic=True)
        self.rows[1]['split'] = 'test'
        self.rows[1]['annotation_status'] = 'unchecked'
        with self.assertRaises(ValueError):
            validate_dataset(self.rows, allow_synthetic=True)

    def test_exact_evidence_recall_latency_and_hold_rate(self):
        row = self.rows[0]
        request = row['request']
        text = request['submission']['body']
        request['candidates'] = [dict(id='source-hit', title='', body=text, scope='public', source_kind='article',
                                      source_url=None, original_id=None, published_at=None, modified_at=None,
                                      first_observed_at=None, policy_version=None, block_reason=None, expires_at=None, segment_id=None)]
        row['relevant_candidate_ids'] = ['source-hit', 'source-missed']
        check = row['response']['checks']['duplicate_rewrite']
        check.update(status='violation', candidate_id='source-hit', matching_information='Same information', new_information='',
                     evidence=[dict(document_id=identity, field='body', start=0, end=len(text), quote=text)
                               for identity in ('submission', 'source-hit')])
        row['response']['decision'] = 'block'
        predictions = self.predictions()
        # A held positive example is not a successful violation detection.
        predictions[0]['response']['checks']['duplicate_rewrite'] = dict(status='undetermined', evidence=[], missing_evidence=['source context'])
        predictions[0]['response']['decision'] = 'hold'
        report = evaluate(self.rows, predictions, allow_synthetic=True)
        korean = report['languages']['ko']
        self.assertEqual(korean['retrieval_recall']['at_5'], .5)
        self.assertEqual(korean['latency_p95_ms'], 10)
        counts = korean['checks']['duplicate_rewrite']
        self.assertEqual(counts['fn'], 1)
        self.assertEqual(counts['recall'], 0)
        self.assertEqual(counts['undetermined_rate'], 1)
        invalid = self.predictions()
        invalid[0]['response']['checks']['duplicate_rewrite']['evidence'][0]['quote'] = 'invented'
        with self.assertRaisesRegex(ValueError, 'evidence'):
            evaluate(self.rows, invalid, allow_synthetic=True)

    def test_no_mixed_model_versions_nan_or_infinite_latency(self):
        predictions = self.predictions()
        predictions[1]['response']['model_version'] = 'different-model'
        with self.assertRaisesRegex(ValueError, 'separate run'):
            evaluate(self.rows, predictions, allow_synthetic=True)
        for value in (float('nan'), float('inf'), True, -1):
            predictions = self.predictions()
            predictions[0]['latency_ms'] = value
            with self.assertRaisesRegex(ValueError, 'latency'):
                evaluate(self.rows, predictions, allow_synthetic=True)

    def test_jsonl_errors_do_not_echo_private_source_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private.jsonl'
            path.write_text('{private blocked source', encoding='utf-8')
            with self.assertRaises(ValueError) as error:
                read_jsonl(path)
            self.assertNotIn('private blocked source', str(error.exception))


if __name__ == '__main__':
    unittest.main()
