"""
test_telemetry_integrity.py — v5.89.210

Hermetic tests for the pure integrity engine. No DB, no Flask — synthetic event
rows only. A healthy dataset goes all-green; each pollution trips exactly the
check it should.
"""
import unittest
from datetime import datetime

from telemetry_integrity import (
    build_integrity_report, START_COMPLETE_PAIRS, KNOWN_STAGES,
    ONCE_PER_SESSION_STAGES,
)


def row(stage, source='google', session_id='s1', user_id=None, created_at=None):
    return {'stage': stage, 'source': source, 'session_id': session_id,
            'user_id': user_id, 'created_at': created_at or datetime.utcnow()}


def _check(report, cid):
    return next(c for c in report['checks'] if c['id'] == cid)


def _healthy_rows():
    """One event for every known stage, with start/complete pairs sharing a
    session and each once-stage on its own session — so all checks pass."""
    rows = []
    completes = {c for _s, c in START_COMPLETE_PAIRS}
    starts = {s for s, _c in START_COMPLETE_PAIRS}
    pair_session = {s: f'pair_{i}' for i, (s, c) in enumerate(START_COMPLETE_PAIRS)}
    complete_session = {c: f'pair_{i}' for i, (s, c) in enumerate(START_COMPLETE_PAIRS)}
    n = 0
    for stage in KNOWN_STAGES:
        if stage in starts:
            sid = pair_session[stage]
        elif stage in completes:
            sid = complete_session[stage]   # same session as its start
        else:
            sid = f'sess_{n}'
            n += 1
        rows.append(row(stage, session_id=sid))
    return rows


class TestHealthyDataset(unittest.TestCase):
    def test_all_pass(self):
        report = build_integrity_report(_healthy_rows(), test_user_ids=[], days=30)
        statuses = {c['id']: c['status'] for c in report['checks']}
        self.assertEqual(report['overall'], 'pass',
                         f"expected all-pass, got {statuses}")
        for cid in ('coverage', 'start_complete', 'duplicates',
                    'source_bucketing', 'test_write_leak', 'internal_source',
                    'fanout'):
            self.assertEqual(statuses[cid], 'pass', f'{cid} should pass')


class TestStartComplete(unittest.TestCase):
    def test_orphan_completion_fails(self):
        rows = _healthy_rows()
        # a completion on a session that never started
        rows.append(row('risk_check_complete', session_id='ghost'))
        c = _check(build_integrity_report(rows), 'start_complete')
        self.assertEqual(c['status'], 'fail')
        self.assertEqual(c['detail']['violations'][0]['orphan_sessions'], 1)

    def test_complete_with_start_ok(self):
        rows = [row('analysis_started', session_id='a1'),
                row('analysis_complete', session_id='a1')]
        c = _check(build_integrity_report(rows), 'start_complete')
        self.assertEqual(c['status'], 'pass')


class TestDuplicates(unittest.TestCase):
    def test_double_purchase_fails(self):
        rows = _healthy_rows()
        rows.append(row('purchase', session_id='sess_buy', user_id=7))
        rows.append(row('purchase', session_id='sess_buy', user_id=7))
        c = _check(build_integrity_report(rows), 'duplicates')
        self.assertEqual(c['status'], 'fail')   # revenue stage
        self.assertIn('purchase', c['detail']['per_stage'])

    def test_double_noncritical_warns(self):
        rows = [row('risk_check_complete', session_id='r1'),
                row('risk_check_complete', session_id='r1'),
                row('risk_check_start', session_id='r1')]
        c = _check(build_integrity_report(rows), 'duplicates')
        self.assertEqual(c['status'], 'warn')


class TestSourceBucketing(unittest.TestCase):
    def test_null_source_warns(self):
        rows = _healthy_rows()
        rows.append(row('visit', source='', session_id='nosrc'))
        c = _check(build_integrity_report(rows), 'source_bucketing')
        self.assertEqual(c['status'], 'warn')
        self.assertEqual(c['detail']['null_source_events'], 1)


class TestExclusionLeak(unittest.TestCase):
    def test_test_user_event_warns(self):
        rows = _healthy_rows()
        rows.append(row('signup', session_id='t1', user_id=999))
        c = _check(build_integrity_report(rows, test_user_ids=[999]), 'test_write_leak')
        self.assertEqual(c['status'], 'warn')
        self.assertEqual(c['detail']['test_users'], 1)

    def test_no_test_users_passes(self):
        c = _check(build_integrity_report(_healthy_rows(), test_user_ids=[999]),
                   'test_write_leak')
        self.assertEqual(c['status'], 'pass')


class TestInternalSource(unittest.TestCase):
    def test_tagassistant_warns(self):
        rows = _healthy_rows()
        rows.append(row('visit', source='tagassistant.google.com',
                        session_id='ta1', user_id=None))
        c = _check(build_integrity_report(rows), 'internal_source')
        self.assertEqual(c['status'], 'warn')
        self.assertEqual(c['detail']['total'], 1)

    def test_internal_but_logged_in_not_flagged(self):
        # internal source only counts on anonymous events
        rows = [row('visit', source='tagassistant.google.com',
                    session_id='x', user_id=5)]
        c = _check(build_integrity_report(rows), 'internal_source')
        self.assertEqual(c['status'], 'pass')


class TestCoverage(unittest.TestCase):
    def test_missing_critical_stage_fails(self):
        rows = [r for r in _healthy_rows() if r['stage'] != 'purchase']
        c = _check(build_integrity_report(rows), 'coverage')
        self.assertEqual(c['status'], 'fail')
        self.assertIn('purchase', c['detail']['zero_critical'])

    def test_absent_noncritical_stage_warns_not_fails(self):
        rows = [r for r in _healthy_rows() if r['stage'] != 'risk_chat_message']
        c = _check(build_integrity_report(rows), 'coverage')
        self.assertEqual(c['status'], 'warn')
        self.assertIn('risk_chat_message', c['detail']['zero_stages'])


class TestFanout(unittest.TestCase):
    def test_high_events_per_session_warns(self):
        # 4 analysis_complete across 2 sessions -> ratio 2.0
        rows = [row('analysis_complete', session_id='f1'),
                row('analysis_complete', session_id='f1'),
                row('analysis_complete', session_id='f2'),
                row('analysis_complete', session_id='f2'),
                row('analysis_started', session_id='f1'),
                row('analysis_started', session_id='f2')]
        c = _check(build_integrity_report(rows), 'fanout')
        self.assertEqual(c['status'], 'warn')
        self.assertIn('analysis_complete', c['detail']['ratios'])


class TestReportShape(unittest.TestCase):
    def test_overall_is_worst_status_and_counts_events(self):
        rows = _healthy_rows()
        rows.append(row('purchase', session_id='b', user_id=1))
        rows.append(row('purchase', session_id='b', user_id=1))  # fail
        report = build_integrity_report(rows)
        self.assertEqual(report['overall'], 'fail')
        self.assertEqual(report['event_count'], len(rows))
        self.assertIn('generated_at', report['period'])

    def test_accepts_attr_objects_not_just_dicts(self):
        class E:
            def __init__(self, **k): self.__dict__.update(k)
        rows = [E(stage='analysis_started', source='google', session_id='a', user_id=None,
                  created_at=datetime.utcnow()),
                E(stage='analysis_complete', source='google', session_id='a', user_id=None,
                  created_at=datetime.utcnow())]
        c = _check(build_integrity_report(rows), 'start_complete')
        self.assertEqual(c['status'], 'pass')


if __name__ == '__main__':
    unittest.main()
