"""Tests for v5.89.39 access-gate module (access_gate.py).

Covers two layers:

  1. Pure-function tests — trusted_domains() and is_trusted_email().
     No DB, no Flask context needed. Verify the env-var parsing and
     the suffix-vs-exact domain matching.

  2. DB-backed lifecycle tests — create_request, approve_request,
     deny_request, consume_magic_token, has_valid_cookie. Uses a
     SQLite in-memory database via Flask-SQLAlchemy app context.

This is a security feature; the failure modes really matter. A bug
in consume_magic_token() that returns the row when it shouldn't would
silently grant access. A bug in has_valid_cookie() that ignores
cookie_revoked or cookie_expires_at would silently extend access.
The tests below specifically exercise those negative paths.

Added in v5.89.40 in response to integrity_tests coverage check
flagging access_gate as untested.
"""
import os
import unittest
from datetime import datetime, timedelta

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-access-gate'
os.environ['DATABASE_URL'] = 'sqlite:///test_access_gate.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-access-gate')

import os as _os
_db_path = 'test_access_gate.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)


# ─────────────────────────────────────────────────────────────────────
# Layer 1: pure-function tests (no DB)
# ─────────────────────────────────────────────────────────────────────

class TestTrustedDomainsEnvParsing(unittest.TestCase):
    """trusted_domains() reads ACCESS_GATE_TRUSTED_DOMAINS env var.
    Verify it parses correctly across reasonable inputs."""

    def setUp(self):
        # Snapshot + clear env so tests don't leak into each other
        self._saved = os.environ.pop('ACCESS_GATE_TRUSTED_DOMAINS', None)

    def tearDown(self):
        if self._saved is not None:
            os.environ['ACCESS_GATE_TRUSTED_DOMAINS'] = self._saved

    def test_empty_when_env_unset(self):
        from access_gate import trusted_domains
        self.assertEqual(trusted_domains(), [])

    def test_empty_when_env_blank(self):
        os.environ['ACCESS_GATE_TRUSTED_DOMAINS'] = '   '
        from access_gate import trusted_domains
        self.assertEqual(trusted_domains(), [])

    def test_single_domain(self):
        os.environ['ACCESS_GATE_TRUSTED_DOMAINS'] = 'ycombinator.com'
        from access_gate import trusted_domains
        self.assertEqual(trusted_domains(), ['ycombinator.com'])

    def test_multiple_domains_comma_separated(self):
        os.environ['ACCESS_GATE_TRUSTED_DOMAINS'] = '.edu,ycombinator.com,sequoia.com'
        from access_gate import trusted_domains
        self.assertEqual(trusted_domains(), ['.edu', 'ycombinator.com', 'sequoia.com'])

    def test_whitespace_trimmed(self):
        os.environ['ACCESS_GATE_TRUSTED_DOMAINS'] = ' .edu , ycombinator.com '
        from access_gate import trusted_domains
        self.assertEqual(trusted_domains(), ['.edu', 'ycombinator.com'])

    def test_lowercased(self):
        os.environ['ACCESS_GATE_TRUSTED_DOMAINS'] = 'YCombinator.COM'
        from access_gate import trusted_domains
        self.assertEqual(trusted_domains(), ['ycombinator.com'])


class TestIsTrustedEmailMatching(unittest.TestCase):
    """is_trusted_email() matches email domain against the env-var list.
    Critical: suffix (.edu) and exact (ycombinator.com) modes behave
    differently. Verify both."""

    def setUp(self):
        os.environ.pop('ACCESS_GATE_TRUSTED_DOMAINS', None)

    def tearDown(self):
        os.environ.pop('ACCESS_GATE_TRUSTED_DOMAINS', None)

    def test_no_match_when_env_unset(self):
        from access_gate import is_trusted_email
        self.assertFalse(is_trusted_email('foo@anywhere.com'))

    def test_suffix_match_dot_edu(self):
        os.environ['ACCESS_GATE_TRUSTED_DOMAINS'] = '.edu'
        from access_gate import is_trusted_email
        self.assertTrue(is_trusted_email('alice@mit.edu'))
        self.assertTrue(is_trusted_email('bob@cs.stanford.edu'))

    def test_suffix_does_not_match_lookalike(self):
        # .edu should NOT match 'edu.example.com' (which ends in 'edu' but
        # isn't an .edu domain). The leading dot in the suffix is what
        # enforces this.
        os.environ['ACCESS_GATE_TRUSTED_DOMAINS'] = '.edu'
        from access_gate import is_trusted_email
        self.assertFalse(is_trusted_email('alice@edu.example.com'))
        self.assertFalse(is_trusted_email('alice@evil-edu.com'))

    def test_exact_match_no_dot_prefix(self):
        os.environ['ACCESS_GATE_TRUSTED_DOMAINS'] = 'ycombinator.com'
        from access_gate import is_trusted_email
        self.assertTrue(is_trusted_email('partner@ycombinator.com'))
        # Subdomain also accepted under the "exact OR endswith .exact" rule
        self.assertTrue(is_trusted_email('alice@news.ycombinator.com'))

    def test_exact_does_not_match_lookalike(self):
        os.environ['ACCESS_GATE_TRUSTED_DOMAINS'] = 'ycombinator.com'
        from access_gate import is_trusted_email
        self.assertFalse(is_trusted_email('attacker@evilycombinator.com'))
        self.assertFalse(is_trusted_email('attacker@ycombinator.com.evil.io'))

    def test_case_insensitive_email(self):
        os.environ['ACCESS_GATE_TRUSTED_DOMAINS'] = 'ycombinator.com'
        from access_gate import is_trusted_email
        self.assertTrue(is_trusted_email('Alice@YCombinator.COM'))

    def test_returns_false_for_malformed(self):
        os.environ['ACCESS_GATE_TRUSTED_DOMAINS'] = '.edu'
        from access_gate import is_trusted_email
        self.assertFalse(is_trusted_email(''))
        self.assertFalse(is_trusted_email('no-at-sign'))
        self.assertFalse(is_trusted_email(None))


# ─────────────────────────────────────────────────────────────────────
# Layer 2: DB-backed lifecycle tests
# ─────────────────────────────────────────────────────────────────────

class TestAccessRequestLifecycle(unittest.TestCase):
    """Full lifecycle: create_request → approve_request →
    consume_magic_token → has_valid_cookie. Plus negative paths."""

    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            from models import db, AccessRequest
            cls.app = app
            cls.db = db
            cls.AccessRequest = AccessRequest
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"App not available: {self.skip_reason}")
        # Clear any prior rows
        with self.app.app_context():
            self.AccessRequest.query.delete()
            self.db.session.commit()
        # Make sure trusted-domain auto-approval is OFF by default
        os.environ.pop('ACCESS_GATE_TRUSTED_DOMAINS', None)

    def tearDown(self):
        with self.app.app_context():
            self.AccessRequest.query.delete()
            self.db.session.commit()
        os.environ.pop('ACCESS_GATE_TRUSTED_DOMAINS', None)

    # ── create_request ──

    def test_create_request_basic_fields_persisted(self):
        from access_gate import create_request
        with self.app.app_context():
            row, auto = create_request(
                db=self.db,
                name='Alice',
                email='alice@example.com',
                company='Acme',
                role='Investor',
                reason='Looking into the deal',
                page_requested='/architecture',
                ip_address='1.2.3.4',
                user_agent='Mozilla/5.0',
            )
            self.assertIsNotNone(row.id)
            self.assertEqual(row.name, 'Alice')
            self.assertEqual(row.email, 'alice@example.com')
            self.assertEqual(row.company, 'Acme')
            self.assertEqual(row.role, 'Investor')
            self.assertEqual(row.reason, 'Looking into the deal')
            self.assertEqual(row.page_requested, '/architecture')
            self.assertEqual(row.ip_address, '1.2.3.4')
            self.assertEqual(row.user_agent, 'Mozilla/5.0')
            self.assertEqual(row.status, 'pending')
            self.assertFalse(auto)
            self.assertIsNone(row.magic_token)
            self.assertIsNone(row.cookie_token)

    def test_create_request_email_normalized_to_lowercase(self):
        from access_gate import create_request
        with self.app.app_context():
            row, _ = create_request(
                db=self.db, name='Alice', email='Alice@Example.COM',
                company='', role='', reason='', page_requested='/thesis',
                ip_address='', user_agent='',
            )
            self.assertEqual(row.email, 'alice@example.com')

    def test_create_request_trusted_domain_auto_approves(self):
        os.environ['ACCESS_GATE_TRUSTED_DOMAINS'] = '.edu'
        from access_gate import create_request
        with self.app.app_context():
            row, auto = create_request(
                db=self.db, name='Prof', email='prof@mit.edu',
                company='', role='', reason='', page_requested='/architecture',
                ip_address='', user_agent='',
            )
            self.assertTrue(auto)
            self.assertEqual(row.status, 'auto_approved')
            self.assertIsNotNone(row.magic_token)
            self.assertIsNotNone(row.magic_sent_at)
            self.assertEqual(row.reviewed_by, 'auto (trusted domain)')

    # ── approve_request ──

    def test_approve_pending_request(self):
        from access_gate import create_request, approve_request
        with self.app.app_context():
            row, _ = create_request(
                db=self.db, name='Alice', email='alice@example.com',
                company='', role='', reason='', page_requested='/architecture',
                ip_address='', user_agent='',
            )
            approved = approve_request(self.db, row.id, reviewer='admin@offerwise')
            self.assertIsNotNone(approved)
            self.assertEqual(approved.status, 'approved')
            self.assertIsNotNone(approved.magic_token)
            self.assertIsNotNone(approved.magic_sent_at)
            self.assertEqual(approved.reviewed_by, 'admin@offerwise')

    def test_approve_already_approved_is_idempotent(self):
        from access_gate import create_request, approve_request
        with self.app.app_context():
            row, _ = create_request(
                db=self.db, name='Alice', email='alice@example.com',
                company='', role='', reason='', page_requested='/architecture',
                ip_address='', user_agent='',
            )
            first = approve_request(self.db, row.id)
            first_token = first.magic_token
            # Second call should be a no-op (returns row but doesn't re-mint token)
            second = approve_request(self.db, row.id)
            self.assertEqual(second.magic_token, first_token,
                             'approve should not re-mint token on already-approved request')

    def test_approve_nonexistent_returns_none(self):
        from access_gate import approve_request
        with self.app.app_context():
            self.assertIsNone(approve_request(self.db, 99999))

    # ── deny_request ──

    def test_deny_pending_request(self):
        from access_gate import create_request, deny_request
        with self.app.app_context():
            row, _ = create_request(
                db=self.db, name='Bob', email='bob@spam.com',
                company='', role='', reason='', page_requested='/architecture',
                ip_address='', user_agent='',
            )
            denied = deny_request(self.db, row.id, reviewer='admin', note='spam')
            self.assertEqual(denied.status, 'denied')
            self.assertEqual(denied.review_note, 'spam')
            self.assertIsNone(denied.magic_token,
                              'deny must NOT generate a magic token')

    # ── consume_magic_token ──

    def test_consume_magic_token_happy_path(self):
        from access_gate import create_request, approve_request, consume_magic_token
        with self.app.app_context():
            row, _ = create_request(
                db=self.db, name='Alice', email='alice@example.com',
                company='', role='', reason='', page_requested='/thesis',
                ip_address='', user_agent='',
            )
            approve_request(self.db, row.id)
            token = row.magic_token

            consumed = consume_magic_token(self.db, token)
            self.assertIsNotNone(consumed)
            self.assertIsNotNone(consumed.magic_consumed_at)
            self.assertIsNotNone(consumed.cookie_token)
            self.assertIsNotNone(consumed.cookie_expires_at)
            self.assertFalse(consumed.cookie_revoked)

    def test_consume_invalid_token_returns_none(self):
        from access_gate import consume_magic_token
        with self.app.app_context():
            self.assertIsNone(consume_magic_token(self.db, 'not-a-real-token'))
            self.assertIsNone(consume_magic_token(self.db, ''))

    def test_consume_token_on_denied_request_returns_none(self):
        # If a request was denied, even if we somehow have its magic_token
        # (we don't generate one for denied requests, but defensive check),
        # consume should refuse.
        from access_gate import create_request, deny_request, consume_magic_token
        with self.app.app_context():
            row, _ = create_request(
                db=self.db, name='Bob', email='bob@spam.com',
                company='', role='', reason='', page_requested='/architecture',
                ip_address='', user_agent='',
            )
            deny_request(self.db, row.id)
            # Manually plant a magic_token to simulate the defensive case
            row.magic_token = 'planted-token-12345'
            self.db.session.commit()
            self.assertIsNone(consume_magic_token(self.db, 'planted-token-12345'))

    def test_consume_double_click_returns_row_if_cookie_still_valid(self):
        # Real-world: user clicks the magic link, then refreshes the page.
        # First consume sets magic_consumed_at and cookie_token. Second
        # consume should NOT fail — return the same row so the cookie can
        # be re-set on the browser.
        from access_gate import create_request, approve_request, consume_magic_token
        with self.app.app_context():
            row, _ = create_request(
                db=self.db, name='Alice', email='alice@example.com',
                company='', role='', reason='', page_requested='/architecture',
                ip_address='', user_agent='',
            )
            approve_request(self.db, row.id)
            token = row.magic_token

            first = consume_magic_token(self.db, token)
            self.assertIsNotNone(first)
            cookie_1 = first.cookie_token

            # Second click: should return the row (not None), same cookie token
            second = consume_magic_token(self.db, token)
            self.assertIsNotNone(second)
            self.assertEqual(second.cookie_token, cookie_1,
                             'double-click should return same cookie, not re-mint')

    def test_consume_after_cookie_expired_returns_none_on_replay(self):
        # If magic link was consumed AND the cookie has since expired,
        # a replay of the magic link should NOT re-issue access. The user
        # must submit a new request.
        from access_gate import create_request, approve_request, consume_magic_token
        with self.app.app_context():
            row, _ = create_request(
                db=self.db, name='Alice', email='alice@example.com',
                company='', role='', reason='', page_requested='/architecture',
                ip_address='', user_agent='',
            )
            approve_request(self.db, row.id)
            token = row.magic_token

            first = consume_magic_token(self.db, token)
            self.assertIsNotNone(first)

            # Force cookie expiry into the past
            first.cookie_expires_at = datetime.utcnow() - timedelta(days=1)
            self.db.session.commit()

            second = consume_magic_token(self.db, token)
            self.assertIsNone(second,
                              'replay after cookie expiry must NOT re-issue access')

    def test_consume_expired_magic_link_returns_none(self):
        # Magic link itself has a TTL (72h). After that, even before
        # first consumption, the link should refuse.
        from access_gate import create_request, approve_request, consume_magic_token, MAGIC_LINK_TTL_HOURS
        with self.app.app_context():
            row, _ = create_request(
                db=self.db, name='Alice', email='alice@example.com',
                company='', role='', reason='', page_requested='/architecture',
                ip_address='', user_agent='',
            )
            approve_request(self.db, row.id)
            token = row.magic_token

            # Backdate magic_sent_at past the TTL
            row.magic_sent_at = datetime.utcnow() - timedelta(hours=MAGIC_LINK_TTL_HOURS + 1)
            self.db.session.commit()

            self.assertIsNone(consume_magic_token(self.db, token))

    # ── has_valid_cookie ──

    def test_has_valid_cookie_with_valid_token(self):
        # Build a request → approve → consume → simulate browser cookie
        from access_gate import create_request, approve_request, consume_magic_token, has_valid_cookie, COOKIE_NAME
        from unittest.mock import MagicMock

        with self.app.app_context():
            row, _ = create_request(
                db=self.db, name='Alice', email='alice@example.com',
                company='', role='', reason='', page_requested='/thesis',
                ip_address='', user_agent='',
            )
            approve_request(self.db, row.id)
            consumed = consume_magic_token(self.db, row.magic_token)
            cookie_value = consumed.cookie_token

            # Simulate a request with the cookie present
            fake_request = MagicMock()
            fake_request.cookies = {COOKIE_NAME: cookie_value}

            found = has_valid_cookie(fake_request, self.db)
            self.assertIsNotNone(found)
            self.assertEqual(found.id, row.id)
            # Audit counter incremented
            self.assertEqual(found.access_count, 1)

    def test_has_valid_cookie_revoked_returns_none(self):
        from access_gate import create_request, approve_request, consume_magic_token, has_valid_cookie, COOKIE_NAME, revoke_cookie
        from unittest.mock import MagicMock

        with self.app.app_context():
            row, _ = create_request(
                db=self.db, name='Alice', email='alice@example.com',
                company='', role='', reason='', page_requested='/architecture',
                ip_address='', user_agent='',
            )
            approve_request(self.db, row.id)
            consumed = consume_magic_token(self.db, row.magic_token)
            cookie_value = consumed.cookie_token
            revoke_cookie(self.db, row.id)

            fake_request = MagicMock()
            fake_request.cookies = {COOKIE_NAME: cookie_value}
            self.assertIsNone(has_valid_cookie(fake_request, self.db))

    def test_has_valid_cookie_expired_returns_none(self):
        from access_gate import create_request, approve_request, consume_magic_token, has_valid_cookie, COOKIE_NAME
        from unittest.mock import MagicMock

        with self.app.app_context():
            row, _ = create_request(
                db=self.db, name='Alice', email='alice@example.com',
                company='', role='', reason='', page_requested='/architecture',
                ip_address='', user_agent='',
            )
            approve_request(self.db, row.id)
            consumed = consume_magic_token(self.db, row.magic_token)
            cookie_value = consumed.cookie_token
            # Force expiry into the past
            consumed.cookie_expires_at = datetime.utcnow() - timedelta(days=1)
            self.db.session.commit()

            fake_request = MagicMock()
            fake_request.cookies = {COOKIE_NAME: cookie_value}
            self.assertIsNone(has_valid_cookie(fake_request, self.db))

    def test_has_valid_cookie_no_cookie_returns_none(self):
        from access_gate import has_valid_cookie, COOKIE_NAME
        from unittest.mock import MagicMock
        with self.app.app_context():
            fake_request = MagicMock()
            fake_request.cookies = {}
            self.assertIsNone(has_valid_cookie(fake_request, self.db))

    def test_has_valid_cookie_unknown_token_returns_none(self):
        from access_gate import has_valid_cookie, COOKIE_NAME
        from unittest.mock import MagicMock
        with self.app.app_context():
            fake_request = MagicMock()
            fake_request.cookies = {COOKIE_NAME: 'not-a-token-we-issued'}
            self.assertIsNone(has_valid_cookie(fake_request, self.db))

    def test_has_valid_cookie_increments_access_count(self):
        from access_gate import create_request, approve_request, consume_magic_token, has_valid_cookie, COOKIE_NAME
        from unittest.mock import MagicMock

        with self.app.app_context():
            row, _ = create_request(
                db=self.db, name='Alice', email='alice@example.com',
                company='', role='', reason='', page_requested='/architecture',
                ip_address='', user_agent='',
            )
            approve_request(self.db, row.id)
            consumed = consume_magic_token(self.db, row.magic_token)

            fake_request = MagicMock()
            fake_request.cookies = {COOKIE_NAME: consumed.cookie_token}

            # First hit
            r1 = has_valid_cookie(fake_request, self.db)
            self.assertEqual(r1.access_count, 1)
            self.assertIsNotNone(r1.last_accessed_at)
            t1 = r1.last_accessed_at

            # Second hit increments
            r2 = has_valid_cookie(fake_request, self.db)
            self.assertEqual(r2.access_count, 2)
            self.assertGreaterEqual(r2.last_accessed_at, t1)


if __name__ == '__main__':
    unittest.main()
