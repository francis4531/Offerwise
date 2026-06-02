"""
test_topbar_admin.py — v5.87.95

Tests the /api/admin/topbar-widget endpoint which aggregates FeatureEvent
rows for the address widget funnel.
"""
import json
import os
import unittest
from datetime import datetime, timedelta

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-topbar-admin'
# Force a dedicated DB file to avoid stale schema from a shared test.db
os.environ['DATABASE_URL'] = 'sqlite:///test_topbar_admin.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-tw')

ADMIN_KEY_VALUE = os.environ['ADMIN_KEY']

# Clear stale db file so create_all gets fresh schema
import os as _os
_db_path = 'test_topbar_admin.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)


class TestTopbarWidgetAdmin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            from models import db, FeatureEvent
            cls.app = app
            cls.db = db
            cls.FeatureEvent = FeatureEvent
            cls.client = app.test_client(use_cookies=False)
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"App not available: {self.skip_reason}")
        # Clear feature events from prior tests
        with self.app.app_context():
            try:
                self.FeatureEvent.query.filter(
                    self.FeatureEvent.feature == 'topbar_address_widget'
                ).delete()
                self.db.session.commit()
            except Exception:
                self.db.session.rollback()

    def tearDown(self):
        with self.app.app_context():
            try:
                self.FeatureEvent.query.filter(
                    self.FeatureEvent.feature == 'topbar_address_widget'
                ).delete()
                self.db.session.commit()
            except Exception:
                self.db.session.rollback()

    def _admin_url(self, path):
        sep = '&' if '?' in path else '?'
        return f'{path}{sep}admin_key={ADMIN_KEY_VALUE}'

    def test_returns_empty_shape_with_no_events(self):
        r = self.client.get(self._admin_url('/api/admin/topbar-widget?days=30'))
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['submits'], 0)
        self.assertEqual(data['arrivals'], 0)
        self.assertIsNone(data['submit_to_arrival_pct'])
        self.assertEqual(data['daily'], [])
        self.assertEqual(data['has_zip'], {'yes': 0, 'no': 0})
        self.assertEqual(data['viewport'], {'desktop': 0, 'mobile': 0})

    def test_aggregates_submits_and_arrivals(self):
        with self.app.app_context():
            for _ in range(5):
                self.db.session.add(self.FeatureEvent(
                    feature='topbar_address_widget',
                    action='submit',
                    meta=json.dumps({'address_length': 35, 'has_zip': True, 'viewport_w': 1440}),
                    created_at=datetime.utcnow() - timedelta(hours=1),
                ))
            for _ in range(3):
                self.db.session.add(self.FeatureEvent(
                    feature='topbar_address_widget',
                    action='arrived',
                    meta=json.dumps({'address_length': 35, 'has_zip': True, 'viewport_w': 1440}),
                    created_at=datetime.utcnow() - timedelta(hours=1),
                ))
            self.db.session.commit()

        r = self.client.get(self._admin_url('/api/admin/topbar-widget?days=30'))
        data = r.get_json()
        self.assertEqual(data['submits'], 5)
        self.assertEqual(data['arrivals'], 3)
        # 3/5 = 60%
        self.assertEqual(data['submit_to_arrival_pct'], 60.0)

    def test_has_zip_distribution(self):
        with self.app.app_context():
            # 7 with ZIP, 3 without
            for _ in range(7):
                self.db.session.add(self.FeatureEvent(
                    feature='topbar_address_widget',
                    action='submit',
                    meta=json.dumps({'has_zip': True, 'viewport_w': 1440}),
                    created_at=datetime.utcnow(),
                ))
            for _ in range(3):
                self.db.session.add(self.FeatureEvent(
                    feature='topbar_address_widget',
                    action='submit',
                    meta=json.dumps({'has_zip': False, 'viewport_w': 1440}),
                    created_at=datetime.utcnow(),
                ))
            self.db.session.commit()

        r = self.client.get(self._admin_url('/api/admin/topbar-widget?days=30'))
        data = r.get_json()
        self.assertEqual(data['has_zip']['yes'], 7)
        self.assertEqual(data['has_zip']['no'], 3)

    def test_viewport_breakdown(self):
        """Viewports < 768 are mobile, >= 768 are desktop."""
        with self.app.app_context():
            self.db.session.add(self.FeatureEvent(
                feature='topbar_address_widget', action='submit',
                meta=json.dumps({'viewport_w': 1920}),  # desktop
                created_at=datetime.utcnow(),
            ))
            self.db.session.add(self.FeatureEvent(
                feature='topbar_address_widget', action='submit',
                meta=json.dumps({'viewport_w': 1024}),  # desktop
                created_at=datetime.utcnow(),
            ))
            self.db.session.add(self.FeatureEvent(
                feature='topbar_address_widget', action='submit',
                meta=json.dumps({'viewport_w': 600}),  # mobile (tablet)
                created_at=datetime.utcnow(),
            ))
            self.db.session.commit()

        r = self.client.get(self._admin_url('/api/admin/topbar-widget?days=30'))
        data = r.get_json()
        self.assertEqual(data['viewport']['desktop'], 2)
        self.assertEqual(data['viewport']['mobile'], 1)

    def test_respects_days_window(self):
        """Events older than the window should be excluded."""
        with self.app.app_context():
            # Recent event (in window)
            self.db.session.add(self.FeatureEvent(
                feature='topbar_address_widget', action='submit',
                meta=json.dumps({'has_zip': True}),
                created_at=datetime.utcnow() - timedelta(days=2),
            ))
            # Old event (out of window for days=7)
            self.db.session.add(self.FeatureEvent(
                feature='topbar_address_widget', action='submit',
                meta=json.dumps({'has_zip': True}),
                created_at=datetime.utcnow() - timedelta(days=20),
            ))
            self.db.session.commit()

        r7 = self.client.get(self._admin_url('/api/admin/topbar-widget?days=7'))
        d7 = r7.get_json()
        self.assertEqual(d7['submits'], 1)

        r30 = self.client.get(self._admin_url('/api/admin/topbar-widget?days=30'))
        d30 = r30.get_json()
        self.assertEqual(d30['submits'], 2)

    def test_handles_malformed_meta_json(self):
        """Don't crash if meta is invalid JSON."""
        with self.app.app_context():
            self.db.session.add(self.FeatureEvent(
                feature='topbar_address_widget', action='submit',
                meta='not valid json {{{',
                created_at=datetime.utcnow(),
            ))
            self.db.session.commit()

        r = self.client.get(self._admin_url('/api/admin/topbar-widget?days=30'))
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['submits'], 1)
        # Has_zip and viewport are unknown for malformed meta — both buckets stay 0
        self.assertEqual(data['has_zip']['yes'], 0)
        self.assertEqual(data['has_zip']['no'], 0)

    def test_other_features_excluded(self):
        """Only topbar_address_widget events should be counted."""
        with self.app.app_context():
            self.db.session.add(self.FeatureEvent(
                feature='topbar_address_widget', action='submit',
                meta=json.dumps({}), created_at=datetime.utcnow(),
            ))
            self.db.session.add(self.FeatureEvent(
                feature='other_feature', action='submit',
                meta=json.dumps({}), created_at=datetime.utcnow(),
            ))
            self.db.session.commit()

        r = self.client.get(self._admin_url('/api/admin/topbar-widget?days=30'))
        data = r.get_json()
        self.assertEqual(data['submits'], 1)
        self.assertEqual(data['total_events'], 1)


class TestTopbarTrackingFromWidget(unittest.TestCase):
    """Verify the existing /api/track/feature endpoint accepts the topbar
    widget event shape correctly."""

    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            from models import db, FeatureEvent
            cls.app = app
            cls.db = db
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
            self.FeatureEvent.query.filter(
                self.FeatureEvent.feature == 'topbar_address_widget'
            ).delete()
            self.db.session.commit()

    def test_track_feature_accepts_topbar_event(self):
        r = self.client.post('/api/track/feature', json={
            'feature': 'topbar_address_widget',
            'action': 'submit',
            'meta': {'address_length': 32, 'has_zip': True, 'viewport_w': 1440},
        })
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data['ok'])

        # Verify it was actually persisted
        with self.app.app_context():
            evt = self.FeatureEvent.query.filter(
                self.FeatureEvent.feature == 'topbar_address_widget'
            ).first()
            self.assertIsNotNone(evt)
            self.assertEqual(evt.action, 'submit')
            meta = json.loads(evt.meta)
            self.assertEqual(meta['address_length'], 32)
            self.assertTrue(meta['has_zip'])

    def test_track_feature_rejects_empty_feature_name(self):
        r = self.client.post('/api/track/feature', json={'action': 'submit'})
        self.assertEqual(r.status_code, 400)


if __name__ == '__main__':
    unittest.main()
