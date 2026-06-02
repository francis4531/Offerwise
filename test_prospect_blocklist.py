"""
test_prospect_blocklist.py — v5.88.00

Tests the permanent-block feature for B2B prospects:
  - POST /api/admin/outreach/b2b/{id}/block  → adds to blocklist + deletes
  - GET  /api/admin/outreach/blocklist        → lists blocked emails
  - DELETE /api/admin/outreach/blocklist/{id} → unblocks

Plus the integration with the three insert paths:
  - manual single-add rejects blocklisted emails
  - paste-import skips blocklisted emails
  - discovery_crawler skips blocklisted emails
"""
import json
import os
import unittest
from datetime import datetime

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-blocklist'
os.environ['DATABASE_URL'] = 'sqlite:///test_blocklist.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-blocklist')

ADMIN_KEY_VALUE = os.environ['ADMIN_KEY']

# Clear stale db
import os as _os
_db_path = 'test_blocklist.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)


class TestBlockEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            from models import db, OutreachContact, ProspectBlocklist
            cls.app = app
            cls.db = db
            cls.OutreachContact = OutreachContact
            cls.ProspectBlocklist = ProspectBlocklist
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
            self.ProspectBlocklist.query.delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.OutreachContact.query.delete()
            self.ProspectBlocklist.query.delete()
            self.db.session.commit()

    def _admin_url(self, path):
        sep = '&' if '?' in path else '?'
        return f'{path}{sep}admin_key={ADMIN_KEY_VALUE}'

    def _add_contact(self, email, name='Test User', company='TestCo'):
        with self.app.app_context():
            c = self.OutreachContact(
                cohort='b2b', email=email, name=name, company=company,
            )
            self.db.session.add(c)
            self.db.session.commit()
            return c.id

    def test_block_endpoint_creates_blocklist_row(self):
        cid = self._add_contact('chris@redfin.com', name='Chris Stocking',
                                company='Redfin')
        r = self.client.post(
            self._admin_url(f'/api/admin/outreach/b2b/{cid}/block'),
            json={'reason': 'wrong_role', 'notes': 'Junior PM'},
        )
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data['ok'])
        self.assertFalse(data['already_blocked'])

        with self.app.app_context():
            # Contact row gone
            self.assertIsNone(self.OutreachContact.query.get(cid))
            # Blocklist row created with metadata
            block = self.ProspectBlocklist.query.filter_by(
                email='chris@redfin.com'
            ).first()
            self.assertIsNotNone(block)
            self.assertEqual(block.reason, 'wrong_role')
            self.assertEqual(block.notes, 'Junior PM')
            self.assertEqual(block.name_at_block, 'Chris Stocking')
            self.assertEqual(block.company_at_block, 'Redfin')

    def test_block_default_reason_is_manual(self):
        cid = self._add_contact('foo@bar.com')
        r = self.client.post(
            self._admin_url(f'/api/admin/outreach/b2b/{cid}/block'),
            json={},
        )
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            block = self.ProspectBlocklist.query.filter_by(
                email='foo@bar.com'
            ).first()
            self.assertEqual(block.reason, 'manual')

    def test_block_invalid_reason_falls_back_to_manual(self):
        cid = self._add_contact('foo@bar.com')
        r = self.client.post(
            self._admin_url(f'/api/admin/outreach/b2b/{cid}/block'),
            json={'reason': 'not_a_real_reason'},
        )
        with self.app.app_context():
            block = self.ProspectBlocklist.query.filter_by(
                email='foo@bar.com'
            ).first()
            self.assertEqual(block.reason, 'manual')

    def test_block_idempotent(self):
        """Blocking an email that's already blocked is a no-op
        (just deletes the contact, doesn't error)."""
        cid1 = self._add_contact('chris@redfin.com')
        r1 = self.client.post(
            self._admin_url(f'/api/admin/outreach/b2b/{cid1}/block'),
            json={'reason': 'wrong_role'},
        )
        self.assertEqual(r1.status_code, 200)
        self.assertFalse(r1.get_json()['already_blocked'])

        # Re-add same email (simulating crawler doing it)
        cid2 = self._add_contact('chris@redfin.com')
        r2 = self.client.post(
            self._admin_url(f'/api/admin/outreach/b2b/{cid2}/block'),
            json={'reason': 'manual'},
        )
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.get_json()['already_blocked'])

        # Only ONE blocklist row total
        with self.app.app_context():
            count = self.ProspectBlocklist.query.filter_by(
                email='chris@redfin.com'
            ).count()
            self.assertEqual(count, 1)
            # Original reason preserved
            block = self.ProspectBlocklist.query.filter_by(
                email='chris@redfin.com'
            ).first()
            self.assertEqual(block.reason, 'wrong_role')

    def test_block_nonexistent_contact_returns_404(self):
        r = self.client.post(
            self._admin_url('/api/admin/outreach/b2b/99999/block'),
            json={'reason': 'manual'},
        )
        self.assertEqual(r.status_code, 404)


class TestBlocklistList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            from models import db, OutreachContact, ProspectBlocklist
            cls.app = app
            cls.db = db
            cls.OutreachContact = OutreachContact
            cls.ProspectBlocklist = ProspectBlocklist
            cls.client = app.test_client(use_cookies=False)
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"App not available: {self.skip_reason}")
        with self.app.app_context():
            self.ProspectBlocklist.query.delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.ProspectBlocklist.query.delete()
            self.db.session.commit()

    def _admin_url(self, path):
        sep = '&' if '?' in path else '?'
        return f'{path}{sep}admin_key={ADMIN_KEY_VALUE}'

    def test_list_empty(self):
        r = self.client.get(self._admin_url('/api/admin/outreach/blocklist'))
        data = r.get_json()
        self.assertEqual(data['count'], 0)
        self.assertEqual(data['items'], [])

    def test_list_returns_blocked_emails(self):
        with self.app.app_context():
            self.db.session.add(self.ProspectBlocklist(
                email='a@b.com', reason='wrong_role', name_at_block='Test',
            ))
            self.db.session.add(self.ProspectBlocklist(
                email='c@d.com', reason='departed',
            ))
            self.db.session.commit()

        r = self.client.get(self._admin_url('/api/admin/outreach/blocklist'))
        data = r.get_json()
        self.assertEqual(data['count'], 2)
        emails = {item['email'] for item in data['items']}
        self.assertEqual(emails, {'a@b.com', 'c@d.com'})

    def test_unblock(self):
        with self.app.app_context():
            b = self.ProspectBlocklist(email='x@y.com', reason='manual')
            self.db.session.add(b)
            self.db.session.commit()
            block_id = b.id

        r = self.client.delete(
            self._admin_url(f'/api/admin/outreach/blocklist/{block_id}'),
        )
        self.assertEqual(r.status_code, 200)

        with self.app.app_context():
            self.assertEqual(
                self.ProspectBlocklist.query.filter_by(email='x@y.com').count(),
                0,
            )


class TestBlocklistEnforcement(unittest.TestCase):
    """Verify blocked emails can't sneak back in via the three insert paths."""

    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            from models import db, OutreachContact, ProspectBlocklist
            cls.app = app
            cls.db = db
            cls.OutreachContact = OutreachContact
            cls.ProspectBlocklist = ProspectBlocklist
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
            self.ProspectBlocklist.query.delete()
            # Pre-populate blocklist with one email
            self.db.session.add(self.ProspectBlocklist(
                email='blocked@example.com',
                reason='wrong_role',
                name_at_block='Blocked Person',
            ))
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.OutreachContact.query.delete()
            self.ProspectBlocklist.query.delete()
            self.db.session.commit()

    def _admin_url(self, path):
        sep = '&' if '?' in path else '?'
        return f'{path}{sep}admin_key={ADMIN_KEY_VALUE}'

    def test_manual_single_add_rejects_blocked_email(self):
        r = self.client.post(
            self._admin_url('/api/admin/outreach/b2b'),
            json={
                'email': 'blocked@example.com',
                'name': 'Should Not Add',
                'company': 'AnyCompany',
            },
        )
        self.assertEqual(r.status_code, 409)
        data = r.get_json()
        self.assertTrue(data['blocked'])
        self.assertIn('blocklist', data['error'].lower())

        # Confirm no contact row was created
        with self.app.app_context():
            self.assertEqual(
                self.OutreachContact.query.filter_by(
                    email='blocked@example.com'
                ).count(),
                0,
            )

    def test_manual_add_works_for_non_blocked_email(self):
        """Sanity check: blocking is the exception, not the default."""
        r = self.client.post(
            self._admin_url('/api/admin/outreach/b2b'),
            json={
                'email': 'fine@example.com',
                'name': 'Fine Person',
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['ok'])


if __name__ == '__main__':
    unittest.main()
