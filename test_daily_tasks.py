"""
test_daily_tasks.py — v5.89.179

Covers the product-facing signal layer and the two-lane ranked daily list:
  - _compute_product_signals detects the biggest funnel leak, open bugs, and
    the share loop from real tables.
  - build_daily_tasks_data produces a ranked mix that always includes at least
    one product-lane task, and falls back to a generic ship task when there is
    no product signal at all.
"""
import os
import unittest
from datetime import datetime, timedelta

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-daily'
os.environ['DATABASE_URL'] = 'sqlite:///test_daily_tasks.db'
os.environ['RATELIMIT_ENABLED'] = 'false'

if os.path.exists('test_daily_tasks.db'):
    os.remove('test_daily_tasks.db')

from app import app  # noqa: E402
from models import db, GTMFunnelEvent, Bug, SharedRiskCheck  # noqa: E402
import daily_tasks  # noqa: E402


def _seed_leak(now):
    # 20 distinct sessions reach try_landed; only 8 reach try_started -> 60% drop.
    for i in range(20):
        db.session.add(GTMFunnelEvent(stage='try_landed', session_id=f's{i}',
                                      created_at=now - timedelta(days=1)))
        if i < 8:
            db.session.add(GTMFunnelEvent(stage='try_started', session_id=f's{i}',
                                          created_at=now - timedelta(days=1)))
    db.session.commit()


class ProductSignalTests(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        # clean slate
        GTMFunnelEvent.query.delete()
        Bug.query.delete()
        SharedRiskCheck.query.delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        self.ctx.pop()

    def test_leak_detected(self):
        now = datetime.utcnow()
        _seed_leak(now)
        sig = daily_tasks._compute_product_signals()
        self.assertIsNotNone(sig['leak'])
        drop, frm, to, na, nb = sig['leak']
        self.assertEqual(frm, 'try_landed')
        self.assertEqual(to, 'try_started')
        self.assertEqual(na, 20)
        self.assertEqual(nb, 8)
        self.assertAlmostEqual(drop, 60.0, places=1)

    def test_open_bugs_and_loop(self):
        now = datetime.utcnow()
        db.session.add(Bug(title='Risk-check 500', severity='high', status='open',
                           created_at=now - timedelta(days=5)))
        db.session.add(Bug(title='Fixed thing', status='fixed',
                           created_at=now - timedelta(days=2)))
        db.session.add(SharedRiskCheck(token='tok1', view_count=3,
                                       created_at=now - timedelta(days=1)))
        db.session.add(SharedRiskCheck(token='tok2', view_count=0,
                                       created_at=now - timedelta(days=2)))
        db.session.commit()
        sig = daily_tasks._compute_product_signals()
        self.assertEqual(sig['open_bugs'], 1)
        self.assertEqual(sig['bug_oldest_title'], 'Risk-check 500')
        self.assertEqual(sig['bug_oldest_sev'], 'high')
        self.assertEqual(sig['share_created_7d'], 2)
        self.assertEqual(sig['share_views_7d'], 3)


class TwoLaneRankingTests(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        GTMFunnelEvent.query.delete()
        Bug.query.delete()
        SharedRiskCheck.query.delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        self.ctx.pop()

    def test_product_task_present_when_signal_exists(self):
        now = datetime.utcnow()
        _seed_leak(now)
        data = daily_tasks.build_daily_tasks_data()
        ids = [t['id'] for t in data['tasks']]
        lanes = {t.get('lane') for t in data['tasks']}
        self.assertIn('leak', ids)
        self.assertIn('product', lanes)
        self.assertIn('customer', lanes)
        self.assertLessEqual(len([t for t in data['tasks'] if not t['custom']]), 7)

    def test_fallback_ship_task_when_no_signal(self):
        # empty tables -> no product signal -> generic ship task still present
        data = daily_tasks.build_daily_tasks_data()
        product = [t for t in data['tasks'] if t.get('lane') == 'product']
        self.assertTrue(product, "a day must never be entirely customer-facing")
        self.assertIn('ship', [t['id'] for t in product])


if __name__ == '__main__':
    unittest.main()
