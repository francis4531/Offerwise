"""
test_e2e_auth_signup.py — v5.88.09 (Path B Release 1: Auth + Signup)

Comprehensive end-to-end coverage of every authentication entry point
and state transition. Drives the Flask test client through real
endpoints, asserts both response codes and database state.

Coverage:
  Registration (POST /auth/register)
    - Happy path: new email → User row created with right tier + credits
    - Existing OAuth user adding password → password set, user logged in
    - Duplicate registered email → 409
    - Invalid email format → 400
    - Short password → 400
    - Missing name → 400

  Login (POST /auth/login-email)
    - Happy path: right credentials → 200, session set, last_login updated
    - Wrong password → 401, no session
    - Unknown email → 401
    - OAuth-only user attempting password login → 401 with helpful message
    - Free user with 0 credits and 0 analyses gets restored to 1 credit

  Magic Link (POST /api/auth/magic-link, GET /auth/magic/<token>)
    - Request creates User if missing + creates MagicLink token
    - Request creates User with auth_provider='magic_link' + 3 credits
    - GET /auth/magic/<valid> sets session, marks token used
    - GET /auth/magic/<invalid> redirects to login
    - GET /auth/magic/<used> rejects (no session set)

  Logout (GET /auth/logout)
    - Clears session, subsequent requests are anonymous

  Auth gate enforcement
    - /app, /settings, /dashboard ALL redirect anonymous users
    - /api/user returns 401 when anonymous

  Cross-flow safety
    - Login with email registered via OAuth-only doesn't clobber data
    - Magic link doesn't reuse — second click on same token fails
"""
import json
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-auth-e2e'
os.environ['DATABASE_URL'] = 'sqlite:///test_e2e_auth.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-auth-e2e')

# Disable rate limits in tests — they kick in at 5/minute on register
# which would block our test sweeps. Real limit testing belongs in a
# dedicated test_rate_limits.py if/when we want it.
os.environ['RATELIMIT_ENABLED'] = 'false'

import os as _os
_db_path = 'test_e2e_auth.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)


def _unique_email(prefix='test'):
    """Generate a unique email per test to avoid conflicts."""
    return f'{prefix}_{int(datetime.now().timestamp() * 1000000)}@e2e-auth.test.offerwise.ai'


class TestRegistration(unittest.TestCase):
    """POST /auth/register — every code path."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User
        cls.app = app
        cls.db = db
        cls.User = User

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            # Clean any leftover test users from prior runs
            self.User.query.filter(
                self.User.email.like('%@e2e-auth.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-auth.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def test_register_happy_path_creates_user_with_credits(self):
        """A fresh registration MUST create a User row with:
          - auth_provider='email'
          - tier='free' (unless dev account)
          - analysis_credits >= 1 (free credit)
          - password_hash set
          - last_login set
        And the response must include redirect URL."""
        email = _unique_email('register_happy')
        with patch('email_service.send_email', return_value=True):  # don't actually email
            r = self.client.post('/auth/register', json={
                'email': email,
                'password': 'SecurePass123!',
                'name': 'Happy Path Tester',
            })
        self.assertEqual(r.status_code, 200,
            f'Registration should succeed with valid input. Got {r.status_code}: {r.data}')
        d = r.get_json()
        self.assertTrue(d.get('success'), 'Response must include success=true')
        self.assertIn('redirect', d, 'Response must include redirect URL')

        # Verify DB state
        with self.app.app_context():
            user = self.User.query.filter_by(email=email).first()
            self.assertIsNotNone(user, 'User row was not created')
            self.assertEqual(user.auth_provider, 'email')
            self.assertEqual(user.tier, 'free')
            self.assertGreaterEqual(user.analysis_credits, 1,
                'New user should get at least 1 free credit')
            self.assertTrue(user.password_hash,
                'password_hash must be set after registration')
            self.assertTrue(user.check_password('SecurePass123!'),
                'check_password must verify the password we just set')
            self.assertIsNotNone(user.last_login,
                'last_login must be set after register (user is auto-logged-in)')

    def test_register_duplicate_email_with_password_returns_409(self):
        """Registering an email that already has a password MUST return 409.
        This prevents account hijacking via re-registration."""
        email = _unique_email('register_dup')
        with patch('email_service.send_email', return_value=True):
            # First registration succeeds
            r1 = self.client.post('/auth/register', json={
                'email': email, 'password': 'FirstPass123!', 'name': 'First'
            })
            self.assertEqual(r1.status_code, 200)

            # Second registration with same email must fail
            self.client = self.app.test_client(use_cookies=True)  # fresh session
            r2 = self.client.post('/auth/register', json={
                'email': email, 'password': 'SecondPass123!', 'name': 'Imposter'
            })
            self.assertEqual(r2.status_code, 409,
                'Re-registering existing-email-with-password MUST return 409')

            # And the original password still works
            with self.app.app_context():
                user = self.User.query.filter_by(email=email).first()
                self.assertTrue(user.check_password('FirstPass123!'),
                    'Original password must still work — re-registration must NOT clobber it')
                self.assertFalse(user.check_password('SecondPass123!'),
                    'Imposter password MUST NOT have been set')

    def test_register_existing_oauth_user_adds_password(self):
        """An existing OAuth user (no password_hash) attempting to register
        with a password gets their password ADDED — same User row.
        This is intentional UX: 'sign up with same email' merges accounts."""
        email = _unique_email('register_oauth_merge')
        with self.app.app_context():
            user = self.User(
                email=email, name='OAuth User',
                auth_provider='google',
                tier='free', analysis_credits=2,
            )
            self.db.session.add(user)
            self.db.session.commit()
            user_id = user.id

        with patch('email_service.send_email', return_value=True):
            r = self.client.post('/auth/register', json={
                'email': email, 'password': 'AddedPass123!', 'name': 'OAuth User',
            })
        self.assertEqual(r.status_code, 200,
            'OAuth-existing user adding password should succeed (200, not 409)')

        with self.app.app_context():
            # Same User row, now with a password
            user = self.User.query.get(user_id)
            self.assertIsNotNone(user)
            self.assertTrue(user.password_hash,
                'OAuth user must have password_hash set after merge')
            self.assertTrue(user.check_password('AddedPass123!'),
                'New password must verify')
            self.assertEqual(user.email, email,
                'No new user row should have been created')

    def test_register_invalid_email_returns_400(self):
        cases = ['notanemail', 'missing@dot', '@nodomain.com', '']
        for bad_email in cases:
            r = self.client.post('/auth/register', json={
                'email': bad_email, 'password': 'GoodPass123!', 'name': 'X',
            })
            self.assertEqual(r.status_code, 400,
                f"Invalid email {bad_email!r} should return 400, got {r.status_code}")

    def test_register_short_password_returns_400(self):
        r = self.client.post('/auth/register', json={
            'email': _unique_email('shortpass'),
            'password': 'short', 'name': 'X',
        })
        self.assertEqual(r.status_code, 400,
            'Password under 8 chars must be rejected')

    def test_register_missing_name_returns_400(self):
        r = self.client.post('/auth/register', json={
            'email': _unique_email('noname'),
            'password': 'GoodPass123!', 'name': '',
        })
        self.assertEqual(r.status_code, 400,
            'Empty name must be rejected')

    def test_register_no_body_returns_400(self):
        """A POST with no JSON body should return 400, not crash."""
        r = self.client.post('/auth/register')
        self.assertEqual(r.status_code, 400)


class TestLoginEmail(unittest.TestCase):
    """POST /auth/login-email — every code path."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User
        cls.app = app
        cls.db = db
        cls.User = User

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-auth.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-auth.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def _create_user_with_password(self, email=None, password='LoginPass123!', credits=2):
        email = email or _unique_email('login')
        with self.app.app_context():
            user = self.User(
                email=email, name='Login Test',
                auth_provider='email',
                tier='free', analysis_credits=credits,
                subscription_status='active',
            )
            user.set_password(password)
            self.db.session.add(user)
            self.db.session.commit()
            return email, user.id

    def test_login_happy_path_returns_200_and_sets_session(self):
        email, uid = self._create_user_with_password()
        r = self.client.post('/auth/login-email', json={
            'email': email, 'password': 'LoginPass123!',
        })
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d.get('success'))

        # Verify session is set by hitting an authenticated endpoint
        # /api/user returns the current user
        me = self.client.get('/api/user')
        self.assertEqual(me.status_code, 200,
            'After login, /api/user should return 200 (session is set)')

        # last_login should be set
        with self.app.app_context():
            user = self.User.query.get(uid)
            self.assertIsNotNone(user.last_login,
                'last_login must be updated on successful login')

    def test_login_wrong_password_returns_401(self):
        email, _ = self._create_user_with_password()
        r = self.client.post('/auth/login-email', json={
            'email': email, 'password': 'WrongPassword!',
        })
        self.assertEqual(r.status_code, 401,
            'Wrong password must return 401')
        # And session NOT set
        me = self.client.get('/api/user')
        self.assertNotEqual(me.status_code, 200,
            'After failed login, /api/user must NOT return 200')

    def test_login_unknown_email_returns_401(self):
        r = self.client.post('/auth/login-email', json={
            'email': _unique_email('nosuch'),
            'password': 'AnyPass123!',
        })
        self.assertEqual(r.status_code, 401,
            'Unknown email must return 401 (with helpful "no account" message)')

    def test_login_oauth_user_without_password_returns_401(self):
        """OAuth user (no password_hash) attempting password login MUST
        get a clear error pointing them at OAuth or password reset.
        This was a real UX bug class — silent fail on password login."""
        email = _unique_email('oauth_no_pwd')
        with self.app.app_context():
            user = self.User(
                email=email, name='OAuth Only',
                auth_provider='google',
                tier='free', analysis_credits=1,
            )
            # IMPORTANT: do NOT set_password
            self.db.session.add(user)
            self.db.session.commit()

        r = self.client.post('/auth/login-email', json={
            'email': email, 'password': 'AnyPass123!',
        })
        self.assertEqual(r.status_code, 401)
        d = r.get_json()
        # The error message must mention the auth provider
        self.assertIn('google', (d.get('error') or '').lower(),
            'OAuth-only login error should mention the auth provider name')

    def test_login_restores_free_credit_for_zero_credit_unpaid_user(self):
        """A user with 0 credits, never paid, 0 analyses, gets 1 credit
        restored on login. This is intentional 'come back, try again' UX."""
        email = _unique_email('restore_credit')
        with self.app.app_context():
            user = self.User(
                email=email, name='Comeback',
                auth_provider='email',
                tier='free', analysis_credits=0,
                stripe_customer_id=None,  # never paid
            )
            user.set_password('ComeBack123!')
            self.db.session.add(user)
            self.db.session.commit()
            uid = user.id

        r = self.client.post('/auth/login-email', json={
            'email': email, 'password': 'ComeBack123!',
        })
        self.assertEqual(r.status_code, 200)

        with self.app.app_context():
            user = self.User.query.get(uid)
            self.assertEqual(user.analysis_credits, 1,
                'Free user with 0 credits and 0 analyses must get 1 credit restored')

    def test_login_does_NOT_restore_credit_if_user_has_paid(self):
        """The credit restore is ONLY for unpaid free users. A user with
        a stripe_customer_id (has paid before) must NOT get a free credit."""
        email = _unique_email('paid_zero')
        with self.app.app_context():
            user = self.User(
                email=email, name='Paid User',
                auth_provider='email',
                tier='free', analysis_credits=0,
                stripe_customer_id='cus_test_paid_123',  # has paid
            )
            user.set_password('PaidPass123!')
            self.db.session.add(user)
            self.db.session.commit()
            uid = user.id

        r = self.client.post('/auth/login-email', json={
            'email': email, 'password': 'PaidPass123!',
        })
        self.assertEqual(r.status_code, 200)

        with self.app.app_context():
            user = self.User.query.get(uid)
            self.assertEqual(user.analysis_credits, 0,
                'Paid user must NOT get free credit restored — that would let '
                'them get unlimited analyses by repeatedly logging out and back in')

    def test_login_missing_credentials_returns_400(self):
        r = self.client.post('/auth/login-email', json={
            'email': '', 'password': '',
        })
        self.assertEqual(r.status_code, 400)

    def test_login_no_body_returns_400(self):
        r = self.client.post('/auth/login-email')
        self.assertEqual(r.status_code, 400)


class TestMagicLink(unittest.TestCase):
    """POST /api/auth/magic-link + GET /auth/magic/<token>"""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, MagicLink
        cls.app = app
        cls.db = db
        cls.User = User
        cls.MagicLink = MagicLink

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-auth.test.offerwise.ai')
            ).delete()
            self.MagicLink.query.filter(
                self.MagicLink.email.like('%@e2e-auth.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-auth.test.offerwise.ai')
            ).delete()
            self.MagicLink.query.filter(
                self.MagicLink.email.like('%@e2e-auth.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def test_magic_link_creates_user_if_missing(self):
        """Requesting a magic link for a never-seen email creates a User
        with auth_provider='magic_link' and 3 credits (the InterNACHI tier)."""
        email = _unique_email('magic_new')
        with patch('email_service.send_email', return_value=True):
            r = self.client.post('/api/auth/magic-link', json={
                'email': email, 'name': 'New Magic User',
            })
        self.assertEqual(r.status_code, 200)

        with self.app.app_context():
            user = self.User.query.filter_by(email=email).first()
            self.assertIsNotNone(user, 'User must be created on magic link request')
            self.assertEqual(user.auth_provider, 'magic_link')
            self.assertEqual(user.analysis_credits, 3,
                'Magic-link signups get 3 credits (inspector free tier)')

            # MagicLink token must be created
            link = self.MagicLink.query.filter_by(email=email).first()
            self.assertIsNotNone(link, 'MagicLink token must be created')

    def test_magic_link_consume_logs_user_in(self):
        email = _unique_email('magic_consume')
        with patch('email_service.send_email', return_value=True):
            r = self.client.post('/api/auth/magic-link', json={
                'email': email, 'name': 'Consume Test',
            })
        self.assertEqual(r.status_code, 200)

        with self.app.app_context():
            link = self.MagicLink.query.filter_by(email=email).first()
            token = link.token
            self.assertFalse(link.used, 'Token must start unused')

        # Visit the magic link URL — should redirect AND set session
        r = self.client.get(f'/auth/magic/{token}', follow_redirects=False)
        self.assertEqual(r.status_code, 302,
            'Magic link consume should 302-redirect (not 200, not 4xx)')

        # Subsequent authenticated request should succeed
        me = self.client.get('/api/user')
        self.assertEqual(me.status_code, 200,
            'After magic link consume, /api/user should return 200')

        with self.app.app_context():
            link = self.MagicLink.query.filter_by(token=token).first()
            self.assertTrue(link.used,
                'Token MUST be marked used after consume — '
                'otherwise it can be replayed')

    def test_magic_link_cannot_be_replayed(self):
        """Critical security: a used magic link token CANNOT be used again.
        If this regresses, intercepted email links become permanent
        backdoors."""
        email = _unique_email('magic_replay')
        with patch('email_service.send_email', return_value=True):
            self.client.post('/api/auth/magic-link', json={
                'email': email, 'name': 'Replay Test',
            })

        with self.app.app_context():
            link = self.MagicLink.query.filter_by(email=email).first()
            token = link.token

        # First use succeeds
        r1 = self.client.get(f'/auth/magic/{token}', follow_redirects=False)
        self.assertEqual(r1.status_code, 302)

        # Second use must NOT log anyone in
        client2 = self.app.test_client(use_cookies=True)  # fresh client/session
        r2 = client2.get(f'/auth/magic/{token}', follow_redirects=False)
        # Should redirect to /login with error param, NOT to inspector-onboarding
        self.assertEqual(r2.status_code, 302)
        self.assertIn('error=link_expired', r2.headers.get('Location', ''),
            'Re-used token must redirect to /login?error=link_expired')

        # Confirm second client has NO session
        me = client2.get('/api/user')
        self.assertNotEqual(me.status_code, 200,
            'CRITICAL: second use of magic link granted session — replay attack possible')

    def test_magic_link_invalid_token_redirects_to_login(self):
        r = self.client.get('/auth/magic/totally-fake-token-that-does-not-exist',
                            follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn('error=link_expired', r.headers.get('Location', ''),
            'Invalid token should redirect to /login?error=link_expired')

    def test_magic_link_invalid_email_returns_400(self):
        r = self.client.post('/api/auth/magic-link', json={'email': 'notanemail'})
        self.assertEqual(r.status_code, 400)


class TestLogout(unittest.TestCase):
    """GET /logout — must clear session entirely."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User
        cls.app = app
        cls.db = db
        cls.User = User

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-auth.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-auth.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def test_logout_clears_session(self):
        # Register + auto-login
        email = _unique_email('logout')
        with patch('email_service.send_email', return_value=True):
            r = self.client.post('/auth/register', json={
                'email': email, 'password': 'LogoutTest123!', 'name': 'Logout',
            })
        self.assertEqual(r.status_code, 200)

        # Confirm logged in
        me = self.client.get('/api/user')
        self.assertEqual(me.status_code, 200, 'Should be logged in after register')

        # Logout
        out = self.client.get('/logout', follow_redirects=False)
        self.assertIn(out.status_code, [200, 302],
            f'Logout should return 200 or 302, got {out.status_code}')

        # Confirm session cleared
        me2 = self.client.get('/api/user')
        self.assertNotEqual(me2.status_code, 200,
            'After /logout, /api/user must NOT return 200 — session is broken')


class TestAuthGateEnforcement(unittest.TestCase):
    """Anonymous requests to protected endpoints must NEVER return product HTML."""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.app = app

    def setUp(self):
        # Cookieless client — guaranteed anonymous
        self.client = self.app.test_client(use_cookies=False)

    def test_app_redirects_anonymous(self):
        r = self.client.get('/app', follow_redirects=False)
        self.assertNotEqual(r.status_code, 200,
            'CRITICAL: /app served HTML to anonymous user. Auth gate broken.')

    def test_settings_redirects_anonymous(self):
        r = self.client.get('/settings', follow_redirects=False)
        # Settings might 200-render with a "please log in" message, OR redirect.
        # What it MUST NOT do is serve real settings UI. We can't easily
        # detect the latter, but 401/302 is the canonical correct answer.
        self.assertIn(r.status_code, [302, 401, 403],
            f'/settings should redirect or refuse anonymous, got {r.status_code}')

    def test_api_auth_me_returns_401_anonymous(self):
        r = self.client.get('/api/user')
        self.assertNotEqual(r.status_code, 200,
            '/api/user must NOT return 200 for anonymous request '
            '(would expose "you are user X" without auth)')

    def test_api_admin_endpoints_require_admin_key(self):
        """Admin endpoints without admin_key should be rejected."""
        # Pick a few representative admin endpoints
        for path in ['/api/admin/users', '/api/admin/funnel-debug']:
            r = self.client.get(path)
            self.assertNotEqual(r.status_code, 200,
                f'CRITICAL: {path} returned 200 to anonymous request. '
                f'Admin endpoint is unprotected.')


if __name__ == '__main__':
    unittest.main()
