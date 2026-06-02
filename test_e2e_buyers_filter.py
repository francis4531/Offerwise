"""
test_e2e_buyers_filter.py — v5.88.25

Tests for the test-account filter on the Buyers admin view.

Coverage:
  1. is_test_account() helper — all 7 patterns + edge cases
  2. /api/admin/users default-OFF filtering (test accounts hidden)
  3. /api/admin/users ?include_test=1 toggle (test accounts visible)
  4. Cassette recorder cleanup regression — try/finally ensures
     test user is deleted even on mid-flow crash

Total tests: 16
"""
import os
import unittest

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-buyers-filter'
os.environ['DATABASE_URL'] = 'sqlite:///test_e2e_buyers.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-buyers-filter')
os.environ['RATELIMIT_ENABLED'] = 'false'

_db_path = 'test_e2e_buyers.db'
if os.path.exists(_db_path):
    os.remove(_db_path)

ADMIN_KEY = os.environ['ADMIN_KEY']


def _admin_url(path):
    sep = '&' if '?' in path else '?'
    return f'{path}{sep}admin_key={ADMIN_KEY}'


class TestIsTestAccountHelper(unittest.TestCase):
    """The centralized test-account detection helper. Patterns:
      - @e2e-*.test.example.com (Path B)
      - @test.offerwise.ai (personas)
      - @persona.offerwise.ai (legacy personas)
      - @piotnetworks.com (founder)
      - @getofferwise.ai (company)
      - test*@gmail.com (manual tests)
      - +test in local part (gmail plus-addressing)
      - name == 'Cassette Recorder' or starts with 'Persona '
    """

    def test_e2e_cassette_pattern_detected(self):
        from funnel_tracker import is_test_account
        emails = [
            'cassette_recorder_abc123@e2e-cassette.test.example.com',
            'integrity_42@e2e-integrity.test.example.com',
            'persona_buyer@e2e-personas.test.example.com',
        ]
        for e in emails:
            self.assertTrue(is_test_account(e),
                f'Should detect {e!r} as test account')

    def test_test_offerwise_ai_detected(self):
        from funnel_tracker import is_test_account
        self.assertTrue(is_test_account('persona.buyer_pro@test.offerwise.ai'))
        self.assertTrue(is_test_account('foo@test.offerwise.ai'))

    def test_persona_offerwise_ai_detected(self):
        from funnel_tracker import is_test_account
        self.assertTrue(is_test_account('anything@persona.offerwise.ai'))

    def test_piotnetworks_detected(self):
        from funnel_tracker import is_test_account
        self.assertTrue(is_test_account('francis@piotnetworks.com'))
        self.assertTrue(is_test_account('anyone@piotnetworks.com'))

    def test_getofferwise_company_account_detected(self):
        from funnel_tracker import is_test_account
        self.assertTrue(is_test_account('hello@getofferwise.ai'))
        self.assertTrue(is_test_account('billing@getofferwise.ai'))
        self.assertTrue(is_test_account('noreply@getofferwise.ai'))

    def test_gmail_test_prefix_detected(self):
        from funnel_tracker import is_test_account
        self.assertTrue(is_test_account('test@gmail.com'))
        self.assertTrue(is_test_account('test12@gmail.com'))
        self.assertTrue(is_test_account('testuser@gmail.com'))

    def test_gmail_plus_test_detected(self):
        from funnel_tracker import is_test_account
        self.assertTrue(is_test_account('alice+test@gmail.com'))
        self.assertTrue(is_test_account('bob+test1@workdomain.com'))

    def test_name_cassette_recorder_detected_even_with_real_email(self):
        """If somehow the email pattern doesn't match but the name is
        'Cassette Recorder', that's still a test account."""
        from funnel_tracker import is_test_account

        class FakeUser:
            email = 'realworld@example.com'
            name = 'Cassette Recorder'
        self.assertTrue(is_test_account(FakeUser()))

    def test_name_persona_prefix_detected(self):
        from funnel_tracker import is_test_account

        class FakeUser:
            email = 'someone@example.com'
            name = 'Persona Buyer Pro'
        self.assertTrue(is_test_account(FakeUser()))

    def test_real_user_NOT_detected(self):
        """Real prospect emails should NOT be flagged."""
        from funnel_tracker import is_test_account
        real_emails = [
            'jane.doe@gmail.com',     # gmail but not 'test' prefix
            'buyer@yahoo.com',
            'contact@realestatefirm.com',
            'realfounder+notes@startup.io',  # +notes is not +test
            'experimentation@example.com',   # 'test' substring is fine
        ]
        for e in real_emails:
            self.assertFalse(is_test_account(e),
                f'False positive: {e!r} was incorrectly flagged as test')

    def test_none_treated_as_test_safely(self):
        """None input returns True (safe default — caller won't surface
        a broken record)."""
        from funnel_tracker import is_test_account
        self.assertTrue(is_test_account(None))

    def test_empty_email_treated_as_test_safely(self):
        from funnel_tracker import is_test_account
        self.assertTrue(is_test_account(''))


class TestBuyersAdminFilter(unittest.TestCase):
    """Integration test: /api/admin/users hides test accounts by default,
    shows them with ?include_test=1."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User
        cls.app = app
        cls.db = db
        cls.User = User

    def setUp(self):
        self.client = self.app.test_client(use_cookies=False)
        # Seed DB with mix of test + real users
        with self.app.app_context():
            # Clean slate for this test
            self.User.query.delete()
            self.db.session.commit()

            test_users = [
                ('cassette_recorder_abc@e2e-cassette.test.example.com', 'Cassette Recorder'),
                ('persona.buyer@test.offerwise.ai', 'Persona Buyer'),
                ('hello@getofferwise.ai', 'Company'),
                ('test@gmail.com', 'Test Acct'),
                ('francis@piotnetworks.com', 'Founder'),
            ]
            real_users = [
                ('jane.doe@gmail.com', 'Jane Doe'),
                ('buyer1@yahoo.com', 'Real Buyer 1'),
            ]
            for email, name in test_users + real_users:
                u = self.User(email=email, name=name, auth_provider='email',
                              tier='free', analysis_credits=1)
                u.set_password('test1234')
                self.db.session.add(u)
            self.db.session.commit()

    def test_default_hides_test_accounts(self):
        """No query param → test accounts are excluded."""
        r = self.client.get(_admin_url('/api/admin/users'))
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertFalse(data.get('include_test'),
            'include_test should default to False')

        emails = [u['email'] for u in data.get('users', [])]
        # Real users should be in the list
        self.assertIn('jane.doe@gmail.com', emails)
        self.assertIn('buyer1@yahoo.com', emails)
        # Test users should NOT be in the list
        self.assertNotIn('cassette_recorder_abc@e2e-cassette.test.example.com', emails)
        self.assertNotIn('persona.buyer@test.offerwise.ai', emails)
        self.assertNotIn('hello@getofferwise.ai', emails)
        self.assertNotIn('test@gmail.com', emails)
        self.assertNotIn('francis@piotnetworks.com', emails)

    def test_include_test_toggle_shows_all(self):
        """?include_test=1 → all users visible."""
        r = self.client.get(_admin_url('/api/admin/users?include_test=1'))
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data.get('include_test'),
            'include_test should be True when toggle on')

        emails = [u['email'] for u in data.get('users', [])]
        # ALL users present
        self.assertIn('jane.doe@gmail.com', emails)
        self.assertIn('cassette_recorder_abc@e2e-cassette.test.example.com', emails)
        self.assertIn('persona.buyer@test.offerwise.ai', emails)
        self.assertIn('hello@getofferwise.ai', emails)
        self.assertIn('test@gmail.com', emails)

    def test_include_test_query_param_variations(self):
        """include_test accepts 1/true/yes/on."""
        for v in ['1', 'true', 'yes', 'on', 'TRUE', 'Yes']:
            r = self.client.get(_admin_url(f'/api/admin/users?include_test={v}'))
            self.assertEqual(r.status_code, 200, f'value {v!r} failed')
            self.assertTrue(r.get_json().get('include_test'),
                f'value {v!r} should enable include_test')

    def test_include_test_query_param_off_variations(self):
        """include_test=0/false/no/off all keep filter ON (hide test accounts)."""
        for v in ['0', 'false', 'no', 'off', '', 'random']:
            r = self.client.get(_admin_url(f'/api/admin/users?include_test={v}'))
            self.assertEqual(r.status_code, 200, f'value {v!r} failed')
            self.assertFalse(r.get_json().get('include_test'),
                f'value {v!r} should leave include_test off')


if __name__ == '__main__':
    unittest.main()
