"""
test_cost_provenance.py — v5.89.225

Two layers:
  1. Hermetic tests for the pure aggregator (no DB, no Flask) — synthetic record
     stubs. Verify the by-category fallback rate, worst-first ordering, the
     doc/preset exclusion from the rate, the uncategorized bucket, and the
     empty/uninstrumented case.
  2. One DB round-trip through the real model + writer + DB-backed aggregator.
"""
import os
os.environ.setdefault('DATABASE_URL', 'sqlite:///test_cost_provenance.db')
if os.path.exists('test_cost_provenance.db'):
    try:
        os.remove('test_cost_provenance.db')
    except OSError:
        pass

import unittest
from datetime import datetime, timedelta

from cost_provenance import (
    aggregate_provenance, _DECISION_SOURCES, _BASELINE_SOURCES, _VALID_SOURCES,
)


class Rec:
    """Minimal stand-in for a CostPricingProvenance row."""
    def __init__(self, category, source, confidence=None, threshold=0.85,
                 severity=None, created_at=None):
        self.category = category
        self.source = source
        self.confidence = confidence
        self.threshold = threshold
        self.severity = severity
        self.created_at = created_at or datetime.utcnow()


class TestAggregateEmpty(unittest.TestCase):
    def test_empty_is_uninstrumented(self):
        r = aggregate_provenance([], window_days=90)
        self.assertFalse(r['instrumented'])
        self.assertEqual(r['by_category'], [])
        self.assertIsNone(r['totals']['fallback_rate'])
        self.assertEqual(r['window_days'], 90)


class TestAggregateRates(unittest.TestCase):
    def test_fallback_rate_and_worst_first(self):
        recs = [
            # foundation: 1 ml, 3 baseline -> 75% fallback
            Rec('foundation', 'ml', confidence=0.91),
            Rec('foundation', 'baseline_lowconf', confidence=0.4),
            Rec('foundation', 'baseline_lowconf', confidence=0.5),
            Rec('foundation', 'baseline_noml'),
            # hvac: 3 ml, 1 baseline -> 25% fallback
            Rec('hvac', 'ml', confidence=0.88),
            Rec('hvac', 'ml', confidence=0.92),
            Rec('hvac', 'ml', confidence=0.90),
            Rec('hvac', 'baseline_lowconf', confidence=0.6),
        ]
        r = aggregate_provenance(recs)
        self.assertTrue(r['instrumented'])
        t = r['totals']
        self.assertEqual(t['ml'], 4)
        self.assertEqual(t['baseline'], 4)
        self.assertEqual(t['priced'], 8)
        self.assertAlmostEqual(t['fallback_rate'], 0.5)
        cats = r['by_category']
        # worst first: foundation (0.75) before hvac (0.25)
        self.assertEqual(cats[0]['category'], 'foundation')
        self.assertAlmostEqual(cats[0]['fallback_rate'], 0.75)
        self.assertEqual(cats[1]['category'], 'hvac')
        self.assertAlmostEqual(cats[1]['fallback_rate'], 0.25)
        # avg ML confidence over ml rows only
        self.assertAlmostEqual(cats[1]['avg_ml_confidence'], round((0.88 + 0.92 + 0.90) / 3, 3))

    def test_doc_and_preset_excluded_from_rate(self):
        recs = [
            Rec('roofing', 'ml', confidence=0.9),
            Rec('roofing', 'doc'),
            Rec('roofing', 'preset'),
        ]
        r = aggregate_provenance(recs)
        self.assertEqual(r['totals']['priced'], 1)   # ml only
        self.assertEqual(r['totals']['doc'], 1)
        self.assertEqual(r['totals']['preset'], 1)
        self.assertAlmostEqual(r['totals']['fallback_rate'], 0.0)
        self.assertEqual(len(r['by_category']), 1)
        self.assertAlmostEqual(r['by_category'][0]['fallback_rate'], 0.0)

    def test_category_with_only_doc_preset_omitted(self):
        recs = [Rec('pool', 'doc'), Rec('pool', 'preset')]
        r = aggregate_provenance(recs)
        self.assertEqual(r['by_category'], [])       # no model decision for pool
        self.assertIsNone(r['totals']['fallback_rate'])  # priced == 0

    def test_uncategorized_bucket(self):
        recs = [Rec(None, 'baseline_noml'), Rec(None, 'ml', confidence=0.9)]
        r = aggregate_provenance(recs)
        self.assertEqual(r['by_category'][0]['category'], '(uncategorized)')
        self.assertAlmostEqual(r['by_category'][0]['fallback_rate'], 0.5)

    def test_threshold_is_latest(self):
        old = datetime.utcnow() - timedelta(days=10)
        new = datetime.utcnow()
        recs = [Rec('hvac', 'ml', confidence=0.9, threshold=0.80, created_at=old),
                Rec('hvac', 'ml', confidence=0.9, threshold=0.85, created_at=new)]
        r = aggregate_provenance(recs)
        self.assertEqual(r['threshold'], 0.85)

    def test_all_baseline_is_full_fallback(self):
        recs = [Rec('plumbing', 'baseline_noml'), Rec('plumbing', 'baseline_lowconf', confidence=0.3)]
        r = aggregate_provenance(recs)
        self.assertAlmostEqual(r['by_category'][0]['fallback_rate'], 1.0)
        self.assertIsNone(r['by_category'][0]['avg_ml_confidence'])  # no ml rows


class TestSourceConstants(unittest.TestCase):
    def test_baseline_subset_of_decision(self):
        for s in _BASELINE_SOURCES:
            self.assertIn(s, _DECISION_SOURCES)

    def test_valid_sources_superset(self):
        for s in _DECISION_SOURCES:
            self.assertIn(s, _VALID_SOURCES)
        self.assertIn('doc', _VALID_SOURCES)
        self.assertIn('preset', _VALID_SOURCES)


class TestDBRoundTrip(unittest.TestCase):
    """Write through the real model + writer, read through the DB aggregator."""

    @classmethod
    def setUpClass(cls):
        from app import app, db
        cls.app = app
        cls.db = db
        cls.ctx = app.app_context()
        cls.ctx.push()
        db.create_all()
        from models import CostPricingProvenance
        # clean slate
        CostPricingProvenance.query.delete()
        db.session.commit()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.db.session.rollback()
        except Exception:
            pass
        cls.ctx.pop()

    def test_write_then_aggregate(self):
        from cost_provenance import record_pricing_provenance, baseline_fallback_by_category
        records = [
            {'category': 'foundation', 'severity': 'critical', 'source': 'baseline_lowconf',
             'confidence': 0.4, 'threshold': 0.85},
            {'category': 'foundation', 'severity': 'major', 'source': 'baseline_noml',
             'confidence': None, 'threshold': 0.85},
            {'category': 'foundation', 'severity': 'major', 'source': 'ml',
             'confidence': 0.9, 'threshold': 0.85},
            {'category': 'hvac', 'severity': 'minor', 'source': 'ml',
             'confidence': 0.88, 'threshold': 0.85},
            {'category': 'hvac', 'severity': 'minor', 'source': 'doc',
             'confidence': None, 'threshold': 0.85},
        ]
        n = record_pricing_provenance(records, analysis_id=123)
        self.assertEqual(n, 5)

        report = baseline_fallback_by_category(self.db.session, window_days=90)
        self.assertTrue(report['instrumented'])
        self.assertEqual(report['totals']['ml'], 2)
        self.assertEqual(report['totals']['baseline'], 2)
        self.assertEqual(report['totals']['doc'], 1)
        self.assertAlmostEqual(report['totals']['fallback_rate'], 0.5)
        # foundation 2/3 baseline = 66.7% > hvac 0/1 = 0% -> foundation first
        self.assertEqual(report['by_category'][0]['category'], 'foundation')
        self.assertAlmostEqual(report['by_category'][0]['fallback_rate'], 2 / 3)
        self.assertEqual(report['threshold'], 0.85)

    def test_bad_source_rejected(self):
        from cost_provenance import record_pricing_provenance
        n = record_pricing_provenance([{'category': 'x', 'source': 'garbage'}])
        self.assertEqual(n, 0)


if __name__ == '__main__':
    unittest.main()
