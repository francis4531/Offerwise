"""
test_wedge_sweep.py — v5.87.99

Tests the /api/admin/outreach/b2b/wedge-sweep endpoint.

Behavior under test:
  - Eligible: wedge IS NULL, '', or 'other'
  - Manual wedges (renovation_lenders, insurtechs, etc.) are NEVER touched
  - dry_run=true returns proposed changes without committing
  - dry_run=false persists the changes
  - Idempotent — running twice leaves the same result
  - Returns counts and a sample for review
"""
import json
import os
import unittest

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-wedge-sweep'
# Force isolated DB to avoid stale schema from a shared test.db
os.environ['DATABASE_URL'] = 'sqlite:///test_wedge_sweep.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-wedge-sweep')

ADMIN_KEY_VALUE = os.environ['ADMIN_KEY']

# Clear stale db file
import os as _os
_db_path = 'test_wedge_sweep.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)


class TestWedgeSweep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            from models import db, OutreachContact
            cls.app = app
            cls.db = db
            cls.OutreachContact = OutreachContact
            cls.client = app.test_client(use_cookies=False)
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"App not available: {self.skip_reason}")
        with self.app.app_context():
            self.OutreachContact.query.delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.OutreachContact.query.delete()
            self.db.session.commit()

    def _admin_url(self, path):
        sep = '&' if '?' in path else '?'
        return f'{path}{sep}admin_key={ADMIN_KEY_VALUE}'

    def _add(self, email, company='', wedge=None):
        with self.app.app_context():
            c = self.OutreachContact(
                cohort='b2b', email=email, company=company, wedge=wedge,
            )
            self.db.session.add(c)
            self.db.session.commit()
            return c.id

    def _wedge_of(self, contact_id):
        with self.app.app_context():
            c = self.OutreachContact.query.get(contact_id)
            return c.wedge if c else None

    def test_dry_run_does_not_commit(self):
        cid = self._add('jane@renofi.com', company='Renofi', wedge=None)
        r = self.client.post(
            self._admin_url('/api/admin/outreach/b2b/wedge-sweep'),
            json={'dry_run': True},
        )
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertFalse(data['committed'])
        self.assertGreaterEqual(data['classified_now'], 1)
        # DB should NOT be updated
        self.assertIsNone(self._wedge_of(cid))

    def test_commit_actually_persists(self):
        cid = self._add('jane@renofi.com', company='Renofi', wedge=None)
        r = self.client.post(
            self._admin_url('/api/admin/outreach/b2b/wedge-sweep'),
            json={'dry_run': False},
        )
        data = r.get_json()
        self.assertTrue(data['committed'])
        # DB IS updated
        self.assertEqual(self._wedge_of(cid), 'renovation_lenders')

    def test_manual_wedges_never_touched(self):
        """A prospect with wedge=insurtechs should NOT be reclassified
        to renovation_lenders even if the keyword scan would suggest it."""
        # Set up a prospect at a hippo.com email (would normally classify
        # as insurtechs via keyword), but with a manual wedge of
        # 'brokerage_tech' (the founder knows better).
        cid = self._add('jane@hippo.com', company='Hippo Insurance',
                        wedge='brokerage_tech')
        r = self.client.post(
            self._admin_url('/api/admin/outreach/b2b/wedge-sweep'),
            json={'dry_run': False},
        )
        # The manual wedge should be PRESERVED
        self.assertEqual(self._wedge_of(cid), 'brokerage_tech')

    def test_other_wedge_is_eligible_and_can_be_resolved(self):
        """A prospect explicitly tagged 'other' is eligible for re-sweep
        and gets a real wedge assigned if keywords match."""
        cid = self._add('cto@hippo.com', company='Hippo', wedge='other')
        r = self.client.post(
            self._admin_url('/api/admin/outreach/b2b/wedge-sweep'),
            json={'dry_run': False},
        )
        self.assertEqual(self._wedge_of(cid), 'insurtechs')

    def test_empty_wedge_is_eligible(self):
        """Empty string wedge ('') should also be eligible."""
        cid = self._add('cto@kiavi.com', company='Kiavi', wedge='')
        r = self.client.post(
            self._admin_url('/api/admin/outreach/b2b/wedge-sweep'),
            json={'dry_run': False},
        )
        self.assertEqual(self._wedge_of(cid), 'renovation_lenders')

    def test_no_keyword_match_stays_other(self):
        """A prospect at a domain we can't classify stays at 'other'."""
        cid = self._add('jane@randomstartup.io', company='RandomStartup',
                        wedge=None)
        r = self.client.post(
            self._admin_url('/api/admin/outreach/b2b/wedge-sweep'),
            json={'dry_run': False},
        )
        data = r.get_json()
        self.assertGreaterEqual(data['still_other'], 1)
        # NULL gets normalized to 'other' on commit
        self.assertEqual(self._wedge_of(cid), 'other')

    def test_idempotent(self):
        """Running the sweep twice is a no-op the second time."""
        cid = self._add('jane@hippo.com', company='Hippo', wedge=None)
        r1 = self.client.post(
            self._admin_url('/api/admin/outreach/b2b/wedge-sweep'),
            json={'dry_run': False},
        )
        d1 = r1.get_json()
        self.assertEqual(d1['classified_now'], 1)

        r2 = self.client.post(
            self._admin_url('/api/admin/outreach/b2b/wedge-sweep'),
            json={'dry_run': False},
        )
        d2 = r2.get_json()
        # On the second run, the prospect is no longer eligible
        self.assertEqual(d2['eligible_total'], 0)
        self.assertEqual(d2['classified_now'], 0)
        # And the wedge remains the same
        self.assertEqual(self._wedge_of(cid), 'insurtechs')

    def test_returns_changes_by_wedge(self):
        self._add('a@hippo.com', company='Hippo', wedge=None)
        self._add('b@lemonade.com', company='Lemonade', wedge=None)
        self._add('c@kiavi.com', company='Kiavi', wedge=None)
        r = self.client.post(
            self._admin_url('/api/admin/outreach/b2b/wedge-sweep'),
            json={'dry_run': True},
        )
        data = r.get_json()
        self.assertEqual(data['changes_by_wedge'].get('insurtechs', 0), 2)
        self.assertEqual(data['changes_by_wedge'].get('renovation_lenders', 0), 1)

    def test_default_is_dry_run(self):
        """Empty body / no dry_run param defaults to dry_run=true to
        prevent accidental commits."""
        cid = self._add('jane@hippo.com', company='Hippo', wedge=None)
        r = self.client.post(
            self._admin_url('/api/admin/outreach/b2b/wedge-sweep'),
            json={},  # No dry_run param
        )
        data = r.get_json()
        self.assertFalse(data['committed'])
        # DB should not be touched
        self.assertIsNone(self._wedge_of(cid))

    def test_sample_excludes_unclassified(self):
        """Sample only shows prospects that resolved to a specific wedge,
        not the still-other ones."""
        self._add('jane@hippo.com', company='Hippo', wedge=None)  # → insurtechs
        self._add('bob@randomstartup.io', company='Random',
                  wedge=None)  # → still other
        r = self.client.post(
            self._admin_url('/api/admin/outreach/b2b/wedge-sweep'),
            json={'dry_run': True},
        )
        data = r.get_json()
        sample_emails = [s['email'] for s in data['sample']]
        self.assertIn('jane@hippo.com', sample_emails)
        self.assertNotIn('bob@randomstartup.io', sample_emails)


if __name__ == '__main__':
    unittest.main()
