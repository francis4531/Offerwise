"""
test_e2e_audit_sweep.py — v5.88.16 (Path B Release 8a: Audit sweeps)

Confirms the v5.88.16 fixes for two patterns Path B repeatedly caught:

  Pattern 1: bare `request.get_json()` crashes with 415→500 on no-body
  Pattern 2: loose `'@' in email` validators accept '@nodomain.com'

In v5.88.16 we fixed these globally:
  - Added an app-level 415 error handler that translates the Flask
    default to a clean JSON 400 for /api/ and /auth/ paths
  - Added blueprint_helpers.is_valid_email() shared validator
  - Tightened the 5 remaining loose user-facing email checks
  - Added silent=True to the 15 bare get_json() calls

This test file:
  1. Verifies the helper itself rejects/accepts the right inputs
  2. Confirms representative endpoints across each blueprint return 4xx
     (not 500) on no-body POST
  3. Confirms representative endpoints reject '@nodomain.com'-style
     malformed emails after the fix

  ~25 tests total
"""
import json
import os
import unittest
from datetime import datetime
from unittest.mock import patch

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-audit-e2e'
os.environ['DATABASE_URL'] = 'sqlite:///test_e2e_audit.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-audit-e2e-key')
os.environ['RATELIMIT_ENABLED'] = 'false'

import os as _os
_db_path = 'test_e2e_audit.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)

ADMIN_KEY_VALUE = os.environ['ADMIN_KEY']


def _admin_url(path):
    sep = '&' if '?' in path else '?'
    return f'{path}{sep}admin_key={ADMIN_KEY_VALUE}'


def _unique_email(prefix='audit'):
    return f'{prefix}_{int(datetime.now().timestamp() * 1000000)}@e2e-audit.test.example.com'


# =============================================================================
# is_valid_email helper unit tests
# =============================================================================

class TestIsValidEmailHelper(unittest.TestCase):
    """The shared validator added in v5.88.16. All 5+ user-facing
    endpoints now route through this. If it regresses, every email
    input regresses with it."""

    def test_accepts_normal_email(self):
        from blueprint_helpers import is_valid_email
        self.assertTrue(is_valid_email('user@example.com'))
        self.assertTrue(is_valid_email('First.Last+tag@subdomain.example.co.uk'))
        self.assertTrue(is_valid_email('  user@example.com  '),
            'Leading/trailing whitespace must be tolerated (we strip)')

    def test_rejects_empty(self):
        from blueprint_helpers import is_valid_email
        self.assertFalse(is_valid_email(''))
        self.assertFalse(is_valid_email('   '))
        self.assertFalse(is_valid_email(None))

    def test_rejects_no_at_sign(self):
        from blueprint_helpers import is_valid_email
        self.assertFalse(is_valid_email('not-an-email'))
        self.assertFalse(is_valid_email('userexample.com'))

    def test_rejects_at_first_char(self):
        """The exact bug pattern Path B kept catching: '@nodomain.com'
        passes loose checks because '@' is in the string and '.' is
        in the string."""
        from blueprint_helpers import is_valid_email
        self.assertFalse(is_valid_email('@nodomain.com'),
            'CRITICAL: @nodomain.com must be rejected — '
            'this is the exact pattern Path B caught 6+ times')
        self.assertFalse(is_valid_email('@a.b'))

    def test_rejects_at_last_char(self):
        from blueprint_helpers import is_valid_email
        self.assertFalse(is_valid_email('user@'))

    def test_rejects_no_tld(self):
        from blueprint_helpers import is_valid_email
        self.assertFalse(is_valid_email('user@nodomain'),
            'Domain without TLD must be rejected')
        self.assertFalse(is_valid_email('user@.com'),
            'Domain starting with dot must be rejected')
        self.assertFalse(is_valid_email('user@com.'),
            'Domain ending with dot must be rejected')

    def test_rejects_non_string_inputs(self):
        from blueprint_helpers import is_valid_email
        self.assertFalse(is_valid_email(123))
        self.assertFalse(is_valid_email([]))
        self.assertFalse(is_valid_email({}))


# =============================================================================
# Global 415 error handler
# =============================================================================

class TestGlobal415Handler(unittest.TestCase):
    """The v5.88.16 global handler translates Flask's default 415
    'Unsupported Media Type' into a clean JSON 400 for API routes.

    Before this fix, hitting any endpoint that called bare
    request.get_json() with no Content-Type returned 500 (when
    wrapped in try/except) or HTML 415 (when not).
    """

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.app = app

    def setUp(self):
        self.client = self.app.test_client(use_cookies=False)

    def test_no_body_post_to_api_returns_400_json(self):
        """Hit a representative API endpoint with no body. Should get
        a 400 with JSON body — NOT a 500 and NOT HTML.

        We use /api/admin/set-credits which:
        - Has the @_api_admin_req_dec decorator (we pass admin_key)
        - Calls request.get_json(silent=True) — should return None gracefully
        - Returns 400 with explicit "Email required" message
        """
        r = self.client.post(_admin_url('/api/admin/set-credits'))
        self.assertNotEqual(r.status_code // 100, 5,
            f'No-body POST must NOT return 5xx, got {r.status_code}')
        self.assertEqual(r.status_code, 400,
            f'Should return 400 cleanly, got {r.status_code}')

    def test_no_body_returns_json_for_api_routes(self):
        """When the global handler fires, content-type must be JSON."""
        # /auth/register with no body
        r = self.client.post('/auth/register')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.content_type.split(';')[0].strip(),
                         'application/json',
            f'Error response must be JSON, got {r.content_type}')

    def test_no_body_returns_400_with_helpful_message(self):
        r = self.client.post('/auth/register')
        d = r.get_json()
        self.assertIn('error', d)
        # The message must be helpful, not "Internal server error"
        msg = (d.get('error') or '').lower()
        self.assertNotIn('internal', msg,
            f'Error message looks like a generic 500: {d.get("error")!r}')


# =============================================================================
# Audit: representative endpoints reject malformed emails
# =============================================================================

class TestEmailValidationAcrossEndpoints(unittest.TestCase):
    """Confirm the 5 user-facing email check sites we tightened in
    v5.88.16 actually reject '@nodomain.com'."""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.app = app

    def setUp(self):
        self.client = self.app.test_client(use_cookies=False)

    def test_register_rejects_at_nodomain(self):
        """Already covered by test_e2e_auth_signup.py but worth
        cross-checking after the audit-sweep refactor that we didn't
        regress the previous fix."""
        r = self.client.post('/auth/register', json={
            'email': '@nodomain.com',
            'password': 'GoodPass123!',
            'name': 'X',
        })
        self.assertEqual(r.status_code, 400,
            'auth/register must still reject @nodomain.com after v5.88.16 refactor')

    def test_magic_link_rejects_at_nodomain(self):
        """v5.88.16 fix: tightened from "@ in email and . in email" to
        is_valid_email."""
        r = self.client.post('/api/auth/magic-link', json={
            'email': '@nodomain.com',
            'name': 'X',
        })
        self.assertEqual(r.status_code, 400,
            'magic-link must reject @nodomain.com — '
            'previously accepted this malformed email')

    def test_waitlist_rejects_at_nodomain(self):
        """Already covered by test_e2e_onboarding_drip.py — re-verifying
        after audit sweep didn't regress."""
        r = self.client.post('/api/waitlist/community', json={
            'email': '@nodomain.com',
        })
        self.assertEqual(r.status_code, 400)

    def test_outreach_b2b_rejects_at_nodomain(self):
        """v5.88.12 fix verified post-refactor."""
        r = self.client.post(
            _admin_url('/api/admin/outreach/b2b'),
            json={'email': '@nodomain.com', 'name': 'X'},
        )
        self.assertEqual(r.status_code, 400)

    def test_subscribe_rejects_at_nodomain(self):
        """v5.88.16 fix to the /api/subscribe contact form."""
        r = self.client.post('/api/subscribe', json={
            'email': '@nodomain.com',
        })
        self.assertEqual(r.status_code, 400,
            '/api/subscribe must reject @nodomain.com after v5.88.16 fix')


# =============================================================================
# Audit: representative endpoints from each blueprint handle no-body
# =============================================================================

class TestNoBodyAcrossBlueprints(unittest.TestCase):
    """Verify the 415→400 global handler + per-endpoint silent=True
    fixes work across each route blueprint. Picks one representative
    POST endpoint from each blueprint module."""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.app = app

    def setUp(self):
        self.client = self.app.test_client(use_cookies=False)

    def _assert_no_5xx(self, r, endpoint_name):
        self.assertNotEqual(r.status_code // 100, 5,
            f'{endpoint_name} no-body POST returned 5xx: {r.status_code}. '
            f'The 415 handler + silent=True fix is missing or broken.')

    # auth_routes
    def test_auth_register_no_body(self):
        r = self.client.post('/auth/register')
        self._assert_no_5xx(r, '/auth/register')
        self.assertEqual(r.status_code, 400)

    def test_auth_login_email_no_body(self):
        r = self.client.post('/auth/login-email')
        self._assert_no_5xx(r, '/auth/login-email')
        self.assertEqual(r.status_code, 400)

    def test_auth_forgot_password_no_body(self):
        # Was a bare request.get_json() pre-v5.88.16
        r = self.client.post('/auth/forgot-password')
        self._assert_no_5xx(r, '/auth/forgot-password')

    # waitlist_routes
    def test_waitlist_no_body(self):
        r = self.client.post('/api/waitlist/community')
        self._assert_no_5xx(r, '/api/waitlist/community')

    # admin_routes (representative)
    def test_admin_send_email_no_body(self):
        r = self.client.post(_admin_url('/api/admin/send-email'))
        self._assert_no_5xx(r, '/api/admin/send-email')

    def test_admin_set_credits_no_body(self):
        r = self.client.post(_admin_url('/api/admin/set-credits'))
        self._assert_no_5xx(r, '/api/admin/set-credits')

    def test_admin_outreach_b2b_no_body(self):
        r = self.client.post(_admin_url('/api/admin/outreach/b2b'))
        self._assert_no_5xx(r, '/api/admin/outreach/b2b')

    # analysis_routes (was bare get_json)
    def test_analysis_routes_no_body(self):
        # /api/analyze requires auth (login_required), so it'll redirect
        # before get_json. That's fine — we just need to confirm no 5xx.
        r = self.client.post('/api/analyze')
        self._assert_no_5xx(r, '/api/analyze')

    # sharing_routes (was bare get_json)
    def test_sharing_share_create_no_body(self):
        r = self.client.post('/api/share/create')
        self._assert_no_5xx(r, '/api/share/create')

    # negotiation_routes (was bare get_json — 4 sites)
    def test_negotiation_no_body(self):
        # /api/negotiation/* — try a representative
        r = self.client.post('/api/negotiation/start')
        self._assert_no_5xx(r, '/api/negotiation/start')

    # bug_routes (was bare get_json)
    def test_bug_routes_no_body(self):
        r = self.client.post('/api/bugs')
        self._assert_no_5xx(r, '/api/bugs')

    # user_routes (was bare get_json at line 661)
    def test_user_routes_preferences_no_body(self):
        r = self.client.post('/api/user/preferences')
        self._assert_no_5xx(r, '/api/user/preferences')

    # consent (already fixed in v5.88.13 but cross-check)
    def test_consent_record_no_body(self):
        r = self.client.post('/api/consent/record')
        self._assert_no_5xx(r, '/api/consent/record')


if __name__ == '__main__':
    unittest.main()
