"""
test_email_deliverability.py — v5.89.206

A deliverability smoke test for email_service.send_email. The product has a
known 0%-opens symptom, and the send path has two ways to "succeed" without
actually delivering a tracked email:

  1. Silent success: resend.Emails.send returns something truthy but no real
     send happened (or the seam was never reached). The test asserts the seam
     is actually invoked with a well-formed payload.
  2. Tracking silently off: Resend only injects the open pixel / rewrites links
     for click-tracking when params['tracking'] = {'opens': True, 'clicks': True}.
     That block (added in v5.88.67) is exactly what fixed the 0%-opens dashboard.
     If a refactor drops it, opens go back to zero with no error. The test pins
     it on.

Hermetic: the Resend seam (email_service.resend) is mocked; no network, no keys.
"""
import os
import unittest
from unittest.mock import patch, MagicMock

os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-secret-deliverability')
os.environ.setdefault('DATABASE_URL', 'sqlite:///test_coverage.db')


class TestSendEmailDeliverability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import email_service
            cls.email_service = email_service
            cls.send_email = email_service.send_email
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"email_service unavailable: {self.skip_reason}")

    def _send(self, mock_resend, **overrides):
        """Call send_email with EMAIL_ENABLED forced on and the Resend seam
        mocked. Returns (return_value, params_sent_to_resend_or_None)."""
        mock_resend.Emails.send.return_value = {'id': 're_test_123'}
        kwargs = dict(
            to_email='buyer@example.com',
            subject='Your analysis is ready',
            html_content='<p>Hello</p>',
        )
        kwargs.update(overrides)
        with patch.object(self.email_service, 'resend', mock_resend), \
             patch.object(self.email_service, 'EMAIL_ENABLED', True):
            rv = self.email_service.send_email(**kwargs)
        if mock_resend.Emails.send.called:
            params = mock_resend.Emails.send.call_args.args[0] \
                if mock_resend.Emails.send.call_args.args \
                else mock_resend.Emails.send.call_args.kwargs.get('params')
        else:
            params = None
        return rv, params

    def test_send_actually_reaches_the_resend_seam(self):
        """The whole point: a 'sent' result must mean the Resend API was
        actually called, not silently skipped."""
        mock_resend = MagicMock()
        rv, params = self._send(mock_resend)
        self.assertTrue(rv, 'send_email should return True on a successful send')
        self.assertEqual(mock_resend.Emails.send.call_count, 1,
                         'resend.Emails.send must be invoked exactly once')
        self.assertIsNotNone(params, 'a payload must be passed to the Resend seam')

    def test_payload_has_required_fields(self):
        mock_resend = MagicMock()
        _, params = self._send(mock_resend)
        self.assertTrue(params.get('from'), "payload missing a 'from' address")
        self.assertEqual(params.get('to'), ['buyer@example.com'])
        self.assertEqual(params.get('subject'), 'Your analysis is ready')
        self.assertIn('html', params)
        self.assertTrue(params.get('html'), "payload has empty html body")

    def test_open_and_click_tracking_stay_enabled(self):
        """Regression guard for the 0%-opens problem. If this fails, Resend
        will deliver mail but stop injecting the open pixel / rewriting links,
        and the engagement dashboard silently flatlines again."""
        mock_resend = MagicMock()
        _, params = self._send(mock_resend)
        tracking = params.get('tracking')
        self.assertIsInstance(tracking, dict,
            "payload must carry a 'tracking' block (opens/clicks were dropped)")
        self.assertIs(tracking.get('opens'), True,
            'open tracking must be ON or the dashboard shows 0% opens')
        self.assertIs(tracking.get('clicks'), True,
            'click tracking must be ON or the dashboard shows 0% clicks')

    def test_custom_from_address_is_honored(self):
        """Cold outreach passes a personal from_email; it must reach Resend."""
        mock_resend = MagicMock()
        _, params = self._send(mock_resend,
                               from_email='Francis Anthony <francis@getofferwise.ai>')
        self.assertEqual(params.get('from'),
                         'Francis Anthony <francis@getofferwise.ai>')

    def test_disabled_email_returns_false_without_calling_seam(self):
        """When email is disabled (e.g. no API key) the function must return a
        falsy result AND not pretend to have sent — a *visible* no-op, not a
        silent success."""
        mock_resend = MagicMock()
        mock_resend.Emails.send.return_value = {'id': 're_should_not_happen'}
        with patch.object(self.email_service, 'resend', mock_resend), \
             patch.object(self.email_service, 'EMAIL_ENABLED', False):
            rv = self.email_service.send_email(to_email='x@example.com', subject='s',
                                 html_content='<p>h</p>')
        self.assertFalse(rv, 'disabled email must return False, not a fake success')
        self.assertFalse(mock_resend.Emails.send.called,
                         'disabled email must not call the Resend seam')

    def test_send_failure_returns_false_not_fake_success(self):
        """If the Resend call raises, send_email must report failure — never
        swallow the error and return True."""
        mock_resend = MagicMock()
        mock_resend.Emails.send.side_effect = RuntimeError('Resend 500')
        with patch.object(self.email_service, 'resend', mock_resend), \
             patch.object(self.email_service, 'EMAIL_ENABLED', True):
            rv = self.email_service.send_email(to_email='x@example.com', subject='s',
                                 html_content='<p>h</p>')
        self.assertFalse(rv, 'a failed send must return False')


if __name__ == '__main__':
    unittest.main()
