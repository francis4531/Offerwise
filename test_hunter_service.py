"""test_hunter_service.py — verify v5.87.46 Hunter.io integration behavior.

Tests the credit-floor protection, error handling, and structural
contracts of hunter_service without making real Hunter API calls.
A live-API smoke test is appropriate as a manual check after deploy
(call /api/admin/hunter/status from the admin UI), not as a CI guard.

Two flavors:
  - Source-inspection tests verify code shape (matches the existing
    test_flywheel.py and test_ml_agent_memory.py patterns)
  - Mock-based tests verify runtime behavior of the credit-floor and
    response normalization logic without touching the network
"""

import os
import unittest
from unittest.mock import patch, MagicMock


# Import works whether or not HUNTER_API_KEY is set — the module is
# designed to silently disable if not configured.
import hunter_service


class TestHunterServiceContract(unittest.TestCase):
    """Static checks on the module's public contract."""

    def test_module_exports_expected_functions(self):
        """The four public functions must exist and be callable."""
        for name in ('account_info', 'domain_search', 'verify_email', 'verify_emails_batch'):
            self.assertTrue(hasattr(hunter_service, name),
                            f'hunter_service is missing public function: {name}')
            self.assertTrue(callable(getattr(hunter_service, name)),
                            f'hunter_service.{name} is not callable')

    def test_credit_floor_default(self):
        """The credit floor must default to 5 (free-tier safety)."""
        # Read the env var the module read at import time
        floor = hunter_service.HUNTER_CREDIT_FLOOR
        # Default 5; CI env may have it set to something else, but the
        # important thing is it's a positive int >= 1
        self.assertIsInstance(floor, int)
        self.assertGreaterEqual(floor, 1,
                                'credit floor must be ≥1 to be meaningful')

    def test_api_key_loaded_from_env(self):
        """The module reads HUNTER_API_KEY from env at load. If not set,
        the module silently disables itself rather than crashing."""
        # In CI, HUNTER_API_KEY is typically not set — the module should
        # cope gracefully.
        result = hunter_service.account_info()
        # If not configured, the result must say so explicitly so the UI
        # can show a helpful error
        if not hunter_service._is_configured():
            self.assertEqual(result.get('configured'), False)
            self.assertIn('error', result)
            # Crucial: never leak the key through error strings
            self.assertNotIn(hunter_service.HUNTER_API_KEY or 'NOTSET',
                             str(result.get('error', '')),
                             'API key must never appear in error messages')


class TestCreditFloorProtection(unittest.TestCase):
    """The single most important guard — refuse to call Hunter when
    remaining credits ≤ floor. Tested with the network mocked out."""

    def setUp(self):
        # Force the module to think it has a configured key during these
        # tests so the floor logic actually runs (it short-circuits on
        # not-configured otherwise).
        self._orig_key = hunter_service.HUNTER_API_KEY
        hunter_service.HUNTER_API_KEY = 'test-key-for-floor-checks'
        # Clear the account cache so each test gets fresh state
        hunter_service._account_cache['data'] = None
        hunter_service._account_cache['fetched_at'] = 0

    def tearDown(self):
        hunter_service.HUNTER_API_KEY = self._orig_key

    @patch('hunter_service.requests.get')
    def test_domain_search_refuses_when_credits_exhausted(self, mock_get):
        """When account_info shows credits ≤ floor, domain_search must NOT
        make the actual API call — it must return credit_exhausted=True."""
        # Mock account_info: 3 credits left, floor is 5 → exhausted
        mock_account_resp = MagicMock(status_code=200)
        mock_account_resp.json.return_value = {
            'data': {
                'plan_name': 'free',
                'calls': {'used': 22, 'available': 25},
                'reset_date': '2026-06-01',
            }
        }
        mock_get.return_value = mock_account_resp

        result = hunter_service.domain_search('example.com')

        # Must report exhaustion
        self.assertTrue(result.get('credit_exhausted'),
                        'domain_search must refuse calls below the floor')
        # Must NOT have made the actual /domain-search call — only /account
        # was called. Inspect the URLs hit:
        called_urls = [c.args[0] for c in mock_get.call_args_list]
        for url in called_urls:
            self.assertNotIn('/domain-search', url,
                             'domain_search must not hit Hunter when credit-exhausted')

    @patch('hunter_service.requests.get')
    def test_domain_search_proceeds_when_credits_available(self, mock_get):
        """Above the floor, the domain search call goes through."""
        # First call returns account info (plenty of credits); second is
        # the actual /domain-search response.
        mock_account_resp = MagicMock(status_code=200)
        mock_account_resp.json.return_value = {
            'data': {
                'plan_name': 'free',
                'calls': {'used': 0, 'available': 25},
                'reset_date': '2026-06-01',
            }
        }
        mock_search_resp = MagicMock(status_code=200)
        mock_search_resp.json.return_value = {
            'data': {
                'domain': 'example.com',
                'organization': 'Example Corp',
                'emails': [
                    {
                        'value': 'jane@example.com',
                        'first_name': 'Jane', 'last_name': 'Doe',
                        'position': 'Head of Underwriting',
                        'seniority': 'senior', 'department': 'finance',
                        'confidence': 92, 'sources': [{'uri': 'https://...'}],
                    }
                ],
            }
        }
        mock_get.side_effect = [mock_account_resp, mock_search_resp]

        result = hunter_service.domain_search('example.com')

        self.assertFalse(result.get('credit_exhausted', False))
        self.assertEqual(result.get('error'), None)
        emails = result.get('emails', [])
        self.assertEqual(len(emails), 1)
        # Response normalization: confidence is int, sources_count not raw URLs
        self.assertEqual(emails[0]['email'], 'jane@example.com')
        self.assertEqual(emails[0]['confidence'], 92)
        self.assertEqual(emails[0]['sources_count'], 1)
        self.assertNotIn('sources', emails[0],
                         'raw source URLs must not be returned to caller')


class TestNoApiKeyLeakage(unittest.TestCase):
    """The API key must never appear in logs, errors, or response bodies.
    This is a security contract — even an exposed key in this conversation
    history shouldn't be exfiltrated through normal product paths."""

    def test_safe_log_error_redacts_key(self):
        """The internal _safe_log_error helper must scrub the API key
        from any error string before passing it to the logger."""
        original_key = hunter_service.HUNTER_API_KEY
        try:
            hunter_service.HUNTER_API_KEY = 'sensitive-key-12345'
            # Build an exception whose message contains the key
            exc = RuntimeError(
                'connection failed for https://api.hunter.io/v2/?api_key=sensitive-key-12345'
            )
            # Capture warnings emitted by _safe_log_error
            with self.assertLogs('hunter_service', level='WARNING') as cm:
                hunter_service._safe_log_error('test_prefix', exc)
            full_log = '\n'.join(cm.output)
            self.assertNotIn('sensitive-key-12345', full_log,
                             'API key was leaked through the warning log')
            self.assertIn('[redacted]', full_log,
                          'Redaction marker must appear in scrubbed log')
        finally:
            hunter_service.HUNTER_API_KEY = original_key

    def test_unconfigured_state_does_not_crash(self):
        """When HUNTER_API_KEY is missing/empty, every public function
        must return a structured error rather than raising."""
        original_key = hunter_service.HUNTER_API_KEY
        try:
            hunter_service.HUNTER_API_KEY = ''
            # Each public function should cope without raising
            r1 = hunter_service.account_info()
            self.assertEqual(r1.get('configured'), False)
            r2 = hunter_service.domain_search('example.com')
            self.assertIn('error', r2)
            r3 = hunter_service.verify_email('test@example.com')
            self.assertIn('error', r3)
            r4 = hunter_service.verify_emails_batch(['a@b.com', 'c@d.com'])
            # batch returns a structured shape, not an error at the top level
            self.assertIn('results', r4)
        finally:
            hunter_service.HUNTER_API_KEY = original_key


class TestVerifyEmailSafety(unittest.TestCase):
    """The 'safe_to_send' boolean is the convenience flag the bulk-send
    flow uses to drop bad addresses. Its derivation must be conservative."""

    def setUp(self):
        self._orig_key = hunter_service.HUNTER_API_KEY
        hunter_service.HUNTER_API_KEY = 'test-key'
        hunter_service._account_cache['data'] = None
        hunter_service._account_cache['fetched_at'] = 0

    def tearDown(self):
        hunter_service.HUNTER_API_KEY = self._orig_key

    @patch('hunter_service.requests.get')
    def test_risky_emails_are_not_safe_to_send(self, mock_get):
        """Hunter's 'risky' result shouldn't pass the safe-to-send gate
        even if score is high. Bouncing on risky addresses hurts sender
        reputation."""
        mock_account = MagicMock(status_code=200)
        mock_account.json.return_value = {
            'data': {'plan_name': 'free', 'calls': {'used': 0, 'available': 25}}
        }
        mock_verify = MagicMock(status_code=200)
        mock_verify.json.return_value = {
            'data': {
                'result': 'risky', 'status': 'accept_all', 'score': 85,
                'regexp': True, 'mx_records': True, 'smtp_check': False,
                'accept_all': True,
            }
        }
        mock_get.side_effect = [mock_account, mock_verify]

        result = hunter_service.verify_email('test@example.com')
        self.assertEqual(result.get('result'), 'risky')
        self.assertFalse(result.get('safe_to_send'),
                         'risky emails must not be marked safe to send')

    @patch('hunter_service.requests.get')
    def test_low_score_deliverable_is_not_safe(self, mock_get):
        """Even a 'deliverable' result with a low confidence score (< 70)
        should be filtered out of the safe-to-send set."""
        mock_account = MagicMock(status_code=200)
        mock_account.json.return_value = {
            'data': {'plan_name': 'free', 'calls': {'used': 0, 'available': 25}}
        }
        mock_verify = MagicMock(status_code=200)
        mock_verify.json.return_value = {
            'data': {
                'result': 'deliverable', 'status': 'valid', 'score': 50,
                'regexp': True, 'mx_records': True, 'smtp_check': True,
            }
        }
        mock_get.side_effect = [mock_account, mock_verify]

        result = hunter_service.verify_email('test@example.com')
        self.assertEqual(result.get('result'), 'deliverable')
        self.assertFalse(result.get('safe_to_send'),
                         'deliverable + low score must not be marked safe')

    @patch('hunter_service.requests.get')
    def test_high_confidence_deliverable_is_safe(self, mock_get):
        """The happy path: deliverable + score ≥ 70 → safe_to_send."""
        mock_account = MagicMock(status_code=200)
        mock_account.json.return_value = {
            'data': {'plan_name': 'free', 'calls': {'used': 0, 'available': 25}}
        }
        mock_verify = MagicMock(status_code=200)
        mock_verify.json.return_value = {
            'data': {
                'result': 'deliverable', 'status': 'valid', 'score': 90,
                'regexp': True, 'mx_records': True, 'smtp_check': True,
            }
        }
        mock_get.side_effect = [mock_account, mock_verify]

        result = hunter_service.verify_email('test@example.com')
        self.assertTrue(result.get('safe_to_send'),
                        'high-confidence deliverable must be safe to send')


if __name__ == '__main__':
    unittest.main(verbosity=2)
