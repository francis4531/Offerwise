"""
test_attribution.py — v5.89.176

Covers first-party ad attribution end to end:
  - capture_ad_attribution before_request stashes utm_* + gclid into the session
    on first touch, site-wide (so /try and any landing page are covered).
  - first touch wins: a later visit with different params does not overwrite.
  - _apply_signup_attribution persists those session values onto a new user row
    (the signup_utm_*/signup_gclid columns that nothing wrote before).
"""
import os
import unittest

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-attribution'
os.environ['DATABASE_URL'] = 'sqlite:///test_attribution.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-attr')
os.environ['RATELIMIT_ENABLED'] = 'false'

if os.path.exists('test_attribution.db'):
    os.remove('test_attribution.db')

from app import app  # noqa: E402
import auth_routes  # noqa: E402


class _FakeUser:
    """A user with the attribution columns present (so hasattr passes)."""
    def __init__(self):
        self.signup_utm_source = None
        self.signup_utm_medium = None
        self.signup_utm_campaign = None
        self.signup_utm_term = None
        self.signup_utm_content = None
        self.signup_referrer = None
        self.signup_landing_page = None
        self.signup_gclid = None
        self.id = 1


class CaptureBeforeRequestTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_captures_utm_and_gclid_on_any_landing(self):
        self.client.get('/try?utm_source=google&utm_medium=cpc'
                        '&utm_campaign=ca_buyers&gclid=ABC123')
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('utm_source'), 'google')
            self.assertEqual(sess.get('utm_medium'), 'cpc')
            self.assertEqual(sess.get('utm_campaign'), 'ca_buyers')
            self.assertEqual(sess.get('gclid'), 'ABC123')

    def test_first_touch_wins(self):
        self.client.get('/try?utm_source=google&gclid=FIRST')
        self.client.get('/try?utm_source=reddit&gclid=SECOND')
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('utm_source'), 'google')
            self.assertEqual(sess.get('gclid'), 'FIRST')

    def test_no_params_sets_nothing(self):
        self.client.get('/try')
        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get('utm_source'))
            self.assertIsNone(sess.get('gclid'))


class ApplySignupAttributionTests(unittest.TestCase):
    def test_session_values_persist_onto_user(self):
        with app.test_request_context('/login'):
            from flask import session
            session['utm_source'] = 'google'
            session['utm_medium'] = 'cpc'
            session['utm_campaign'] = 'ca_buyers'
            session['gclid'] = 'XYZ789'
            user = _FakeUser()
            auth_routes._apply_signup_attribution(user)
            self.assertEqual(user.signup_utm_source, 'google')
            self.assertEqual(user.signup_utm_medium, 'cpc')
            self.assertEqual(user.signup_utm_campaign, 'ca_buyers')
            self.assertEqual(user.signup_gclid, 'XYZ789')

    def test_reads_from_request_args_when_session_empty(self):
        with app.test_request_context('/login?utm_source=reddit&gclid=RDT1'):
            user = _FakeUser()
            auth_routes._apply_signup_attribution(user)
            self.assertEqual(user.signup_utm_source, 'reddit')
            self.assertEqual(user.signup_gclid, 'RDT1')

    def test_no_attribution_is_safe(self):
        with app.test_request_context('/login'):
            user = _FakeUser()
            auth_routes._apply_signup_attribution(user)
            self.assertIsNone(user.signup_utm_source)
            self.assertIsNone(user.signup_gclid)


if __name__ == '__main__':
    unittest.main()
