"""
test_v5_88_07.py — v5.88.07

Tests the four releases bundled in this version:
  1. Buyer drip auto-firing — User table now has drip columns + scheduler
  2. Per-step onboarding tracking — feature_events + admin endpoint
  3. Cost data corrections — zillow / nextdoor / internachi endpoints
  4. Source acquisition sort — front-end only, structural test
"""
import json
import os
import unittest
from datetime import datetime, timezone, timedelta, date

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-v88-07'
os.environ['DATABASE_URL'] = 'sqlite:///test_v88_07.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-v88-07')

ADMIN_KEY_VALUE = os.environ['ADMIN_KEY']

import os as _os
_db_path = 'test_v88_07.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)


class TestUserDripScheduler(unittest.TestCase):
    """v5.88.07: buyer drip now auto-fires through scheduler."""

    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            from models import db, User, FeatureEvent
            cls.app = app
            cls.db = db
            cls.User = User
            cls.FeatureEvent = FeatureEvent
            cls.client = app.test_client(use_cookies=False)
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"App not available: {self.skip_reason}")
        with self.app.app_context():
            self.User.query.delete()
            self.FeatureEvent.query.delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.User.query.delete()
            self.FeatureEvent.query.delete()
            self.db.session.commit()

    def test_user_has_drip_columns(self):
        """User model has the new drip columns."""
        with self.app.app_context():
            u = self.User(email='test@example.com', tier='free')
            u.drip_step = 0
            u.drip_completed = False
            u.email_unsubscribed = False
            self.db.session.add(u)
            self.db.session.commit()

            fetched = self.User.query.filter_by(email='test@example.com').first()
            self.assertEqual(fetched.drip_step, 0)
            self.assertFalse(fetched.drip_completed)
            self.assertFalse(fetched.email_unsubscribed)

    def test_user_drip_entry_reads_real_state(self):
        """The _UserDripEntry shim now reads actual drip_step from User."""
        with self.app.app_context():
            from drip_campaign import _UserDripEntry
            u = self.User(email='jane@example.com', tier='free')
            u.drip_step = 3
            u.drip_completed = False
            self.db.session.add(u)
            self.db.session.commit()

            entry = _UserDripEntry(u)
            self.assertEqual(entry.drip_step, 3)  # Was hardcoded to 0 before
            self.assertFalse(entry.drip_completed)

    def test_persist_user_drip_state_writes_back(self):
        """After a successful send, drip_step + drip_last_sent_at persist."""
        with self.app.app_context():
            from drip_campaign import _persist_user_drip_state
            u = self.User(email='jane@example.com', tier='free')
            u.drip_step = 0
            self.db.session.add(u)
            self.db.session.commit()

            now = datetime.now(timezone.utc)
            _persist_user_drip_state(u, step=2, sent_at=now)

            fetched = self.User.query.get(u.id)
            self.assertEqual(fetched.drip_step, 2)
            self.assertIsNotNone(fetched.drip_last_sent_at)

    def test_run_user_drip_scheduler_skips_completed(self):
        """drip_completed=True users are never re-fired."""
        with self.app.app_context():
            from drip_campaign import run_user_drip_scheduler
            u = self.User(email='done@example.com', tier='free',
                          created_at=datetime.utcnow() - timedelta(days=20))
            u.drip_step = 5
            u.drip_completed = True
            self.db.session.add(u)
            self.db.session.commit()

            stats = run_user_drip_scheduler(self.db.session)
            # The user is filtered out by query before reaching send logic
            self.assertEqual(stats['sent'], 0)

    def test_run_user_drip_scheduler_skips_unsubscribed(self):
        with self.app.app_context():
            from drip_campaign import run_user_drip_scheduler
            u = self.User(email='unsub@example.com', tier='free',
                          created_at=datetime.utcnow() - timedelta(days=5))
            u.email_unsubscribed = True
            self.db.session.add(u)
            self.db.session.commit()

            stats = run_user_drip_scheduler(self.db.session)
            self.assertEqual(stats['sent'], 0)

    def test_run_user_drip_scheduler_skips_bots(self):
        with self.app.app_context():
            from drip_campaign import run_user_drip_scheduler
            u = self.User(email='test@mailinator.com', tier='free',
                          created_at=datetime.utcnow() - timedelta(days=1))
            u.drip_step = 0
            self.db.session.add(u)
            self.db.session.commit()

            stats = run_user_drip_scheduler(self.db.session)
            self.assertEqual(stats['sent'], 0)
            self.assertGreaterEqual(stats['skipped'], 1)


class TestOnboardingFunnel(unittest.TestCase):
    """v5.88.07: per-step onboarding tracking endpoint."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, FeatureEvent
        cls.app = app
        cls.db = db
        cls.FeatureEvent = FeatureEvent
        cls.client = app.test_client(use_cookies=False)

    def setUp(self):
        with self.app.app_context():
            self.FeatureEvent.query.delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.FeatureEvent.query.delete()
            self.db.session.commit()

    def _admin_url(self, path):
        sep = '&' if '?' in path else '?'
        return f'{path}{sep}admin_key={ADMIN_KEY_VALUE}'

    def _add_event(self, action, user_id=None, session_id=None):
        with self.app.app_context():
            ev = self.FeatureEvent(
                feature='onboarding',
                action=action,
                user_id=user_id,
                session_id=session_id,
            )
            self.db.session.add(ev)
            self.db.session.commit()

    def test_empty_returns_zero_counts(self):
        r = self.client.get(self._admin_url('/api/admin/onboarding-funnel'))
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d['total_events'], 0)
        for s in d['steps']:
            self.assertEqual(s['unique_users'], 0)

    def test_funnel_counts_unique_users_per_step(self):
        # 5 users see step 1, 3 see step 2, 1 see step 3
        for i in range(1, 6):
            self._add_event('step_1_viewed', user_id=i)
        for i in range(1, 4):
            self._add_event('step_2_viewed', user_id=i)
        self._add_event('step_3_viewed', user_id=1)

        r = self.client.get(self._admin_url('/api/admin/onboarding-funnel'))
        d = r.get_json()
        self.assertEqual(d['steps'][0]['unique_users'], 5)
        self.assertEqual(d['steps'][1]['unique_users'], 3)
        self.assertEqual(d['steps'][2]['unique_users'], 1)

    def test_funnel_computes_retention_pct(self):
        # 10 see step 1, 6 see step 2 → 60% retention
        for i in range(1, 11):
            self._add_event('step_1_viewed', user_id=i)
        for i in range(1, 7):
            self._add_event('step_2_viewed', user_id=i)

        r = self.client.get(self._admin_url('/api/admin/onboarding-funnel'))
        d = r.get_json()
        self.assertEqual(d['steps'][1]['retention_from_prev_pct'], 60.0)

    def test_funnel_counts_skips(self):
        self._add_event('skipped_at_step_1', user_id=1)
        self._add_event('skipped_at_step_2', user_id=2)
        self._add_event('skipped_at_step_2', user_id=3)

        r = self.client.get(self._admin_url('/api/admin/onboarding-funnel'))
        d = r.get_json()
        self.assertEqual(d['steps'][0]['skipped_here'], 1)
        self.assertEqual(d['steps'][1]['skipped_here'], 2)

    def test_funnel_counts_anonymous_via_session_id(self):
        """Pre-login users are tracked by session_id, not user_id."""
        self._add_event('step_1_viewed', session_id='anon-abc')
        self._add_event('step_1_viewed', session_id='anon-xyz')
        # Same session_id repeated counts as one user
        self._add_event('step_1_viewed', session_id='anon-abc')

        r = self.client.get(self._admin_url('/api/admin/onboarding-funnel'))
        d = r.get_json()
        self.assertEqual(d['steps'][0]['unique_users'], 2)


class TestCostCorrections(unittest.TestCase):
    """v5.88.07: Zillow / Nextdoor / INTERNACHI cost data corrections."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, GTMAdPerformance
        cls.app = app
        cls.db = db
        cls.GTMAdPerformance = GTMAdPerformance
        cls.client = app.test_client(use_cookies=False)

    def setUp(self):
        with self.app.app_context():
            self.GTMAdPerformance.query.delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.GTMAdPerformance.query.delete()
            self.db.session.commit()

    def _admin_url(self, path):
        sep = '&' if '?' in path else '?'
        return f'{path}{sep}admin_key={ADMIN_KEY_VALUE}'

    def test_zillow_dry_run_does_not_modify(self):
        # Pre-existing junk Zillow data
        with self.app.app_context():
            self.db.session.add(self.GTMAdPerformance(
                channel='zillow', date=date(2026, 4, 1), spend=100, clicks=10
            ))
            self.db.session.add(self.GTMAdPerformance(
                channel='zillow', date=date(2026, 4, 2), spend=200, clicks=20
            ))
            self.db.session.commit()
            count_before = self.GTMAdPerformance.query.count()

        r = self.client.post(self._admin_url('/api/admin/cost-correction/zillow'),
                             json={'apply': False})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d['dry_run'])
        self.assertEqual(len(d['existing_zillow_rows']), 2)

        with self.app.app_context():
            self.assertEqual(self.GTMAdPerformance.query.count(), count_before)

    def test_zillow_apply_replaces_with_canonical_row(self):
        # Pre-existing junk
        with self.app.app_context():
            self.db.session.add(self.GTMAdPerformance(
                channel='zillow', date=date(2026, 4, 5), spend=999, clicks=5
            ))
            self.db.session.commit()

        r = self.client.post(self._admin_url('/api/admin/cost-correction/zillow'),
                             json={'apply': True})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d['applied'])

        with self.app.app_context():
            zillow_rows = self.GTMAdPerformance.query.filter(
                self.GTMAdPerformance.channel.in_(['zillow', 'zillow_ads', 'zillow_display'])
            ).all()
            self.assertEqual(len(zillow_rows), 1)
            self.assertEqual(float(zillow_rows[0].spend), 501.00)
            self.assertEqual(zillow_rows[0].clicks, 68)
            self.assertEqual(zillow_rows[0].date, date(2026, 3, 31))

    def test_nextdoor_apply_deletes_all(self):
        with self.app.app_context():
            self.db.session.add(self.GTMAdPerformance(
                channel='nextdoor', date=date(2026, 4, 1), spend=50
            ))
            self.db.session.add(self.GTMAdPerformance(
                channel='nextdoor', date=date(2026, 4, 2), spend=50
            ))
            # Other channel preserved
            self.db.session.add(self.GTMAdPerformance(
                channel='google_ads', date=date(2026, 4, 1), spend=100
            ))
            self.db.session.commit()

        r = self.client.post(self._admin_url('/api/admin/cost-correction/nextdoor'),
                             json={'apply': True})
        d = r.get_json()
        self.assertTrue(d['applied'])
        self.assertEqual(d['gtm_ad_performance_rows_deleted'], 2)

        with self.app.app_context():
            # Nextdoor gone
            self.assertEqual(
                self.GTMAdPerformance.query.filter_by(channel='nextdoor').count(),
                0
            )
            # Google still there
            self.assertEqual(
                self.GTMAdPerformance.query.filter_by(channel='google_ads').count(),
                1
            )

    def test_internachi_requires_start_date(self):
        r = self.client.post(self._admin_url('/api/admin/cost-correction/internachi'),
                             json={'apply': True})
        self.assertEqual(r.status_code, 400)
        d = r.get_json()
        self.assertIn('start_date', d['error'])

    def test_internachi_dry_run_returns_preview(self):
        # Start 3 months ago
        from datetime import date as _date
        today = _date.today()
        if today.month >= 3:
            start = _date(today.year, today.month - 2, 1)
        else:
            start = _date(today.year - 1, 12 - (2 - today.month), 1)
        r = self.client.post(self._admin_url('/api/admin/cost-correction/internachi'),
                             json={'apply': False, 'start_date': start.isoformat()})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d['dry_run'])
        # Should be 3 months in range
        self.assertGreaterEqual(d['months_in_range'], 3)

    def test_internachi_apply_creates_monthly_rows(self):
        from datetime import date as _date
        # Start 2 months ago — get exactly 3 rows (start month + current + 1 between)
        today = _date.today()
        if today.month >= 3:
            start = _date(today.year, today.month - 2, 1)
        else:
            start = _date(today.year - 1, 12 - (2 - today.month), 1)
        r = self.client.post(self._admin_url('/api/admin/cost-correction/internachi'),
                             json={'apply': True, 'start_date': start.isoformat()})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d['applied'])
        self.assertGreaterEqual(d['rows_created'], 3)

        with self.app.app_context():
            rows = self.GTMAdPerformance.query.filter_by(channel='internachi').all()
            self.assertGreaterEqual(len(rows), 3)
            for r in rows:
                self.assertEqual(float(r.spend), 49.00)
                self.assertEqual(r.date.day, 1)  # 1st of month

    def test_internachi_idempotent(self):
        """Running the backfill twice doesn't create duplicate rows."""
        from datetime import date as _date
        today = _date.today()
        start = _date(today.year, today.month, 1)

        r1 = self.client.post(self._admin_url('/api/admin/cost-correction/internachi'),
                              json={'apply': True, 'start_date': start.isoformat()})
        first = r1.get_json()['rows_created']

        r2 = self.client.post(self._admin_url('/api/admin/cost-correction/internachi'),
                              json={'apply': True, 'start_date': start.isoformat()})
        second = r2.get_json()['rows_created']

        self.assertEqual(first, 1)  # First run creates 1 row (this month)
        self.assertEqual(second, 0)  # Second run is a no-op


class TestNextdoorRemovedFromCode(unittest.TestCase):
    """v5.88.07: Nextdoor channel is removed from valid_channels and
    fallback dashboard list."""

    def setUp(self):
        admin_path = os.path.join(os.path.dirname(__file__), 'admin_routes.py')
        with open(admin_path, 'r') as f:
            self.src = f.read()

    def test_nextdoor_not_in_valid_channels(self):
        # The valid_channels set in the campaign-config endpoint
        idx = self.src.find('valid_channels = {')
        self.assertNotEqual(idx, -1)
        line_end = self.src.find('}', idx)
        valid_channels_line = self.src[idx:line_end]
        self.assertNotIn('nextdoor', valid_channels_line)

    def test_nextdoor_not_in_fallback_paid_list(self):
        # The fallback list that always-shows known channels
        idx = self.src.find("for paid_src in (")
        self.assertNotEqual(idx, -1)
        line_end = self.src.find('):', idx)
        line = self.src[idx:line_end]
        self.assertNotIn('nextdoor', line)


class TestSourceUsersSort(unittest.TestCase):
    """v5.88.07: front-end sort latest signup first."""

    def setUp(self):
        admin_path = os.path.join(os.path.dirname(__file__), 'static', 'admin.html')
        with open(admin_path, 'r') as f:
            self.src = f.read()

    def test_loadSourceUsers_sorts_by_first_seen_desc(self):
        idx = self.src.find('async function loadSourceUsers')
        self.assertNotEqual(idx, -1)
        # Within ~3000 chars, expect a sort call
        nearby = self.src[idx:idx + 3000]
        self.assertIn('users.sort(', nearby)
        # Sort comparator uses first_seen
        self.assertIn('first_seen', nearby[nearby.find('users.sort('):nearby.find('users.sort(') + 300])


if __name__ == '__main__':
    unittest.main()
