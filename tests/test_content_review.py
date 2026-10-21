import copy
import json
from pathlib import Path
import threading
import time
import unittest
from unittest import mock

from colorless.content_review import ContentReview, ReviewFailure, validate_response
from colorless.content_search import ReviewIndex


def verdict(request, *, stage='joint', status='pass'):
    names = ('context_distortion', 'duplicate_rewrite', 'blocked_reupload') if stage == 'joint' else ('agitation',)
    return {'schema_version': '1', 'policy_version': request['policy_version'], 'model_version': 'fixture-only',
            'decision': 'hold' if status == 'undetermined' else 'allow',
            'checks': {name: {'status': status, 'evidence': [],
                             'missing_evidence': ['source body'] if status == 'undetermined' else []} for name in names}}


def span(identity, field, quote, start=0):
    return dict(document_id=identity, field=field, start=start, end=start + len(quote), quote=quote)


class ReviewContractTest(unittest.TestCase):
    def setUp(self):
        self.request = dict(task='joint_content_review', schema_version='1', policy_version='policy-1',
                            submission={'body': '한글 body', 'title': 'title'}, candidates=[])

    def test_jsonl_contract_examples_and_cluster_split(self):
        root = Path(__file__).parents[1] / 'contracts'
        schemas = {name: json.loads((root / f'content-review-{name}.schema.json').read_text(encoding='utf-8'))
                   for name in ('request', 'response', 'training')}
        rows = [json.loads(line) for line in (root / 'content-review-examples.jsonl').read_text(encoding='utf-8').splitlines()]
        groups = {}
        self.assertEqual({row['request']['submission']['language'] for row in rows}, {'ko', 'en'})
        for row in rows:
            self.assertEqual(row['annotation_status'], 'synthetic')
            self.assertTrue(set(schemas['training']['required']) <= row.keys())
            self.assertTrue(set(schemas['request']['required']) <= row['request'].keys())
            self.assertTrue(set(schemas['response']['required']) <= row['response'].keys())
            validate_response(row['request'], row['response'])
            groups.setdefault(row['cluster_id'], set()).add(row['split'])
        self.assertTrue(all(len(splits) == 1 for splits in groups.values()))

    def test_all_checks_versions_and_policy_must_agree(self):
        good = verdict(self.request)
        self.assertIs(validate_response(self.request, good), good)
        for mutate in (lambda r: r['checks'].pop('blocked_reupload'),
                       lambda r: r.update(policy_version='old'),
                       lambda r: r.update(decision='block'),
                       lambda r: r.update(model_version='')):
            bad = copy.deepcopy(good)
            mutate(bad)
            with self.assertRaises(ValueError):
                validate_response(self.request, bad)

    def test_exact_unicode_offsets_and_both_distortion_fields(self):
        response = verdict(self.request)
        response['decision'] = 'block'
        check = response['checks']['context_distortion']
        check.update(status='violation', evidence=[span('submission', 'title', 'title'), span('submission', 'body', '한글')])
        validate_response(self.request, response)
        for bad_span in (span('submission', 'body', 'invented'), span('secret', 'body', '한글'),
                         dict(document_id='submission', field='body', quote='한글', start=True, end=2)):
            check['evidence'][1] = bad_span
            with self.assertRaises(ValueError):
                validate_response(self.request, response)

    def test_duplicate_and_blocked_record_require_comparison_evidence(self):
        candidate = dict(id='prior', body='한글 body', scope='public')
        self.request['candidates'] = [candidate]
        response = verdict(self.request)
        response['decision'] = 'block'
        check = response['checks']['blocked_reupload']
        check.update(status='violation', candidate_id='prior', matching_information='same', new_information='',
                     evidence=[span('submission', 'body', '한글'), span('prior', 'body', '한글')])
        with self.assertRaises(ValueError):
            validate_response(self.request, response)
        candidate['scope'] = 'blocked'
        validate_response(self.request, response)
        check['candidate_id'] = 'unknown'
        with self.assertRaises(ValueError):
            validate_response(self.request, response)

    def test_undetermined_requires_missing_evidence(self):
        response = verdict(self.request, status='undetermined')
        validate_response(self.request, response)
        response['checks']['duplicate_rewrite']['missing_evidence'] = []
        with self.assertRaises(ValueError):
            validate_response(self.request, response)

    def test_single_joint_call_then_mandatory_kaist(self):
        order = []
        def joint(request, **kwargs):
            order.append('joint')
            self.assertGreater(kwargs['timeout'], 0)
            self.assertEqual(request['task'], 'joint_content_review')
            return verdict(request)
        def kaist(request, **kwargs):
            order.append('kaist')
            return verdict(request, stage='kaist')
        review = ContentReview(search=lambda *a, **k: [], joint_model=joint, kaist=kaist, policy_version='policy-1')
        review.check(self.request['submission'], authorize=lambda c: True)
        self.assertEqual(order, ['joint', 'kaist'])
        review.kaist = None
        with self.assertRaises(ReviewFailure):
            review.check(self.request['submission'], authorize=lambda c: True)
        self.assertEqual(order, ['joint', 'kaist'])

    def test_hold_and_invalid_provider_never_reach_kaist(self):
        kaist = mock.Mock()
        for joint in (lambda r, **k: verdict(r, status='undetermined'), lambda r, **k: {'decision': 'allow'}):
            review = ContentReview(search=lambda *a, **k: [], joint_model=joint, kaist=kaist, policy_version='policy-1')
            with self.assertRaises(ReviewFailure):
                review.check(self.request['submission'], authorize=lambda c: True)
        kaist.assert_not_called()

    def test_private_stale_and_expired_candidates_never_reach_model(self):
        model = mock.Mock()
        for candidate in (dict(id='x', body='secret', scope='followers'), dict(id='x', body='stale', scope='public'),
                          dict(id='x', body='private', scope='blocked', policy_version='old', block_reason='x', expires_at=time.time()+100),
                          dict(id='x', body='private', scope='blocked', policy_version='policy-1', block_reason='x', expires_at=0)):
            review = ContentReview(search=lambda *a, **k: [candidate], joint_model=model, kaist=model, policy_version='policy-1')
            with self.assertRaises(ReviewFailure):
                review.check(self.request['submission'], authorize=lambda c: False)
        model.assert_not_called()

    def test_timeout_and_saturation_are_bounded_until_worker_finishes(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args, **kwargs):
            entered.set()
            release.wait(2)
            return []
        review = ContentReview(search=slow, joint_model=lambda r, **k: verdict(r),
                               kaist=lambda r, **k: verdict(r, stage='kaist'), policy_version='p', timeout=.03, concurrency=1)
        try:
            with self.assertRaises(ReviewFailure) as timeout:
                review.check({'body': 'x'}, authorize=lambda c: True)
            self.assertTrue(entered.is_set())
            self.assertEqual(timeout.exception.code, 'review_timeout')
            with self.assertRaises(ReviewFailure) as busy:
                review.check({'body': 'x'}, authorize=lambda c: True)
            self.assertEqual(busy.exception.code, 'review_busy')
        finally:
            release.set()


class ReviewIndexTest(unittest.TestCase):
    def setUp(self):
        self.index = ReviewIndex(embedding_version='multilingual-fixture', dimensions=2, policy_version='p')

    def put(self, identity, scope='public', vector=(1, 0), **kwargs):
        self.index.upsert(dict(id=identity, scope=scope, title='', body='한국어 English', **kwargs), vector,
                          embedding_version='multilingual-fixture')

    def test_private_records_never_appear_in_public_search(self):
        self.put('public')
        self.put('blocked', 'blocked', policy_version='p', block_reason='distortion', expires_at=time.time()+60)
        self.assertEqual([x['id'] for x in self.index.search_public([1, 0], authorize=lambda c: True)], ['public'])
        self.assertEqual(len(self.index.search_for_review([1, 0], authorize=lambda c: True)), 2)
        self.assertEqual([x['id'] for x in self.index.search_for_review([1, 0], authorize=lambda c: False)], ['blocked'])
        with self.assertRaises(ValueError):
            self.put('followers', 'followers')

    def test_filter_before_top_k_update_remove_and_expiry(self):
        self.put('inaccessible')
        self.put('visible', vector=(0, 1))
        self.assertEqual(self.index.search_public([1, 0], authorize=lambda c: c['id']=='visible', limit=1)[0]['id'], 'visible')
        self.put('visible', vector=(1, 0))
        self.assertEqual(len(self.index.search_public([1, 0], authorize=lambda c: True)), 2)
        self.index.remove('visible')
        self.put('expired', 'blocked', policy_version='p', block_reason='x', expires_at=time.time()+10)
        with mock.patch('colorless.content_search.time.time', return_value=time.time()+20):
            self.assertEqual([x['id'] for x in self.index.search_for_review([1, 0], authorize=lambda c: True)], ['inaccessible'])
        self.index.clear(embedding_version='next', dimensions=3, policy_version='p2')
        self.assertEqual(self.index.search_public([1, 0, 0], authorize=lambda c: True), [])

    def test_invalid_embeddings_model_versions_and_copy_isolation(self):
        for vector in ([0, 0], [float('nan'), 1], [1], [True, 1], [float('inf'), 1]):
            with self.assertRaises(ValueError):
                self.put('x', vector=vector)
        self.put('x')
        results = self.index.search_public([1, 0], authorize=lambda c: True)
        results[0]['body'] = 'tampered'
        self.assertEqual(self.index.search_public([1, 0], authorize=lambda c: True)[0]['body'], '한국어 English')
        with self.assertRaises(ValueError):
            self.index.upsert(dict(id='x', scope='public', body='x'), [1, 0], embedding_version='wrong')


if __name__ == '__main__':
    unittest.main()
