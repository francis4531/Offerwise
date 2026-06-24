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


class TestExclusionHelpers(unittest.TestCase):
    """The single-source helpers every admin surface routes through."""
    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            from models import db, User
            import admin_routes
            cls.app = app
            cls.db = db
            cls.User = User
            cls.admin_routes = admin_routes
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

    def _seed(self, *emails):
        ids = {}
        with self.app.app_context():
            for e in emails:
                u = self.User(email=e, tier='free')
                self.db.session.add(u)
                self.db.session.commit()
                ids[e] = u.id
        return ids

    def test_canonical_helper_matches_canonical_domains(self):
        ids = self._seed('real@gmail.com', 'p@persona.offerwise.ai',
                         't@test.offerwise.ai', 'e@x.test.example.com',
                         'company@getofferwise.ai', 'persona@persona.ai')
        with self.app.app_context():
            got = set(self.admin_routes._canonical_test_user_ids())
        # canonical excludes the 3 canonical domains but NOT the company ones
        self.assertEqual(got, {ids['p@persona.offerwise.ai'],
                               ids['t@test.offerwise.ai'],
                               ids['e@x.test.example.com']})

    def test_revenue_set_also_excludes_company_domains(self):
        # Revenue uses canonical + (@persona.ai, @getofferwise.ai). It must
        # still pick up .test.example.com (the canonical part) AND the company
        # domains — narrowing it would risk counting company accounts as paying.
        ids = self._seed('real@gmail.com', 'p@persona.offerwise.ai',
                         'e@x.test.example.com', 'company@getofferwise.ai',
                         'persona@persona.ai')
        domains = self.admin_routes._canonical_test_domains() + ('@persona.ai', '@getofferwise.ai')
        with self.app.app_context():
            got = set(self.admin_routes._test_user_ids(domains))
        self.assertEqual(got, {ids['p@persona.offerwise.ai'],
                               ids['e@x.test.example.com'],
                               ids['company@getofferwise.ai'],
                               ids['persona@persona.ai']})
        self.assertNotIn(ids['real@gmail.com'], got)

    def test_empty_domains_returns_empty(self):
        self._seed('real@gmail.com')
        with self.app.app_context():
            self.assertEqual(self.admin_routes._test_user_ids(()), [])
            self.assertEqual(self.admin_routes._test_user_ids(None), [])


if __name__ == '__main__':
    unittest.main()
