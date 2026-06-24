"""
test_funnel_debug_exclusion.py — Phase 2 backend rider

/api/admin/funnel-debug used to sample the last 200 buyers *including*
test/persona/e2e seed accounts, which padded the headline buyer count. It now
excludes them using the same TEST_EMAIL_DOMAINS source of truth as the canonical
/api/admin/funnel. These tests seed real + test-domain users and assert the
test-domain ones are gone from both the count and the returned rows.
"""
import os
import unittest
from datetime import datetime

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-funnel-debug'
os.environ['DATABASE_URL'] = 'sqlite:///test_funnel_debug.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-funnel-debug')

ADMIN_KEY_VALUE = os.environ['ADMIN_KEY']

_db_path = 'test_funnel_debug.db'
if os.path.exists(_db_path):
    os.remove(_db_path)


class TestFunnelDebugExcludesTestAccounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from app import app, TEST_EMAIL_DOMAINS
            from models import db, User
            cls.app = app
            cls.db = db
            cls.User = User
            cls.TEST_EMAIL_DOMAINS = TEST_EMAIL_DOMAINS
            cls.client = app.test_client(use_cookies=False)
            with app.app_context():
                db.create_all()
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"App not available: {self.skip_reason}")
        with self.app.app_context():
            self.User.query.delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.User.query.delete()
            self.db.session.commit()

    def _admin_url(self, path):
        sep = '&' if '?' in path else '?'
        return f'{path}{sep}admin_key={ADMIN_KEY_VALUE}'

    def _add_user(self, email, tier='free'):
        with self.app.app_context():
            u = self.User(email=email, tier=tier, created_at=datetime.utcnow())
            self.db.session.add(u)
            self.db.session.commit()
            return u.id

    def _fetch(self):
        r = self.client.get(self._admin_url('/api/admin/funnel-debug'))
        self.assertEqual(r.status_code, 200, r.data[:300])
        return r.get_json()

    def test_test_domain_users_excluded_from_count_and_rows(self):
        self._add_user('alice@gmail.com')
        self._add_user('bob@acme.co')
        self._add_user('carol@outlook.com')
        # one per test/persona/e2e domain
        self._add_user('persona1@persona.offerwise.ai')
        self._add_user('seed@test.offerwise.ai')
        self._add_user('bot@qa.test.example.com')

        d = self._fetch()
        self.assertEqual(d['total_buyers'], 3,
                         f"expected 3 real buyers, got {d['total_buyers']}")
        emails = {row['email'] for row in d['users']}
        self.assertEqual(emails, {'alice@gmail.com', 'bob@acme.co', 'carol@outlook.com'})
        for bad in ('persona1@persona.offerwise.ai', 'seed@test.offerwise.ai',
                    'bot@qa.test.example.com'):
            self.assertNotIn(bad, emails)

    def test_all_real_users_pass_through(self):
        for e in ('a@x.com', 'b@y.com', 'c@z.com', 'd@w.io'):
            self._add_user(e)
        d = self._fetch()
        self.assertEqual(d['total_buyers'], 4)

    def test_uses_canonical_test_email_domains_source(self):
        # The rider must align to app.TEST_EMAIL_DOMAINS — including the
        # .test.example.com domain Phase 1 added. Guard against drift.
        self.assertIn('.test.example.com', self.TEST_EMAIL_DOMAINS)
        self._add_user('real@example.org')
        self._add_user('e2e@run.test.example.com')
        d = self._fetch()
        self.assertEqual(d['total_buyers'], 1)
        self.assertEqual({r['email'] for r in d['users']}, {'real@example.org'})


if __name__ == '__main__':
    unittest.main()
