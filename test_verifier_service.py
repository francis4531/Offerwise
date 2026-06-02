"""test_verifier_service.py — Pluggable verifier guards (v5.87.49).

Verifies the dispatch logic, credit-floor protection, and response
normalization of verifier_service without making real API calls. Live
verification is appropriate as a manual smoke check after deploy
(/api/admin/verifier/status), not a CI guard.
"""
import os
import unittest
from unittest.mock import patch, MagicMock

import verifier_service


class TestVerifierServiceContract(unittest.TestCase):
    """Public surface and config-load behavior."""

    def test_module_exports_expected_functions(self):
        for name in ('account_info', 'verify_email', 'verify_emails_batch'):
            self.assertTrue(hasattr(verifier_service, name),
                            f'verifier_service is missing {name}')
            self.assertTrue(callable(getattr(verifier_service, name)),
                            f'verifier_service.{name} is not callable')

    def test_default_provider_is_millionverifier(self):
        # When VERIFIER_PROVIDER is unset, the module should default to
        # MillionVerifier (chosen for stable free tier + lowest pricing).
        # We can't reload the module from a test cleanly, but we can
        # verify the default the module recorded at import time.
        provider = (os.environ.get('VERIFIER_PROVIDER', 'millionverifier')
                    or 'millionverifier').strip().lower()
        # If the env var is set in this CI run, just confirm the module
        # honored it; otherwise confirm the default.
        self.assertEqual(verifier_service.VERIFIER_PROVIDER, provider)

    def test_credit_floor_default(self):
        self.assertGreaterEqual(verifier_service.VERIFIER_CREDIT_FLOOR, 1)

    def test_unconfigured_state_returns_structured_error(self):
        """When no provider key is set, every public function returns a
        clear error rather than raising."""
        if verifier_service._is_configured():
            self.skipTest('A verifier provider IS configured in this env')
        info = verifier_service.account_info()
        self.assertEqual(info.get('configured'), False)
        self.assertIn('error', info)
        # Must surface which provider was selected so the user knows
        # which env var to set
        self.assertIn('provider', info)


class TestProviderDispatch(unittest.TestCase):
    """Verify the dispatch table routes to the right backend."""

    def setUp(self):
        # Force a known state we can mutate
        self._orig_provider = verifier_service.VERIFIER_PROVIDER
        self._orig_mv_key = verifier_service.MILLIONVERIFIER_API_KEY
        self._orig_zb_key = verifier_service.ZEROBOUNCE_API_KEY
        verifier_service._account_cache['data'] = None
        verifier_service._account_cache['fetched_at'] = 0

    def tearDown(self):
        verifier_service.VERIFIER_PROVIDER = self._orig_provider
        verifier_service.MILLIONVERIFIER_API_KEY = self._orig_mv_key
        verifier_service.ZEROBOUNCE_API_KEY = self._orig_zb_key

    @patch('verifier_service.requests.get')
    def test_millionverifier_dispatch(self, mock_get):
        """Setting provider=millionverifier routes account_info to MV's
        /credits endpoint."""
        verifier_service.VERIFIER_PROVIDER = 'millionverifier'
        verifier_service.MILLIONVERIFIER_API_KEY = 'mv-test-key'

        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {'credits': 850}
        mock_get.return_value = mock_resp

        info = verifier_service.account_info(force_refresh=True)
        self.assertEqual(info.get('provider'), 'millionverifier')
        self.assertEqual(info.get('credits_left'), 850)

        # And confirm the URL hit was the MV one, not ZB or Hunter
        called_url = mock_get.call_args.args[0]
        self.assertIn('millionverifier.com', called_url)

    @patch('verifier_service.requests.get')
    def test_zerobounce_dispatch(self, mock_get):
        """Setting provider=zerobounce routes account_info to ZB's
        /getcredits endpoint."""
        verifier_service.VERIFIER_PROVIDER = 'zerobounce'
        verifier_service.ZEROBOUNCE_API_KEY = 'zb-test-key'

        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {'Credits': 4200}
        mock_get.return_value = mock_resp

        info = verifier_service.account_info(force_refresh=True)
        self.assertEqual(info.get('provider'), 'zerobounce')
        self.assertEqual(info.get('credits_left'), 4200)

        called_url = mock_get.call_args.args[0]
        self.assertIn('zerobounce.net', called_url)


class TestCreditFloorProtection(unittest.TestCase):
    """The credit-floor short-circuit must apply to ALL providers, not
    just Hunter. This is the guard that prevents accidentally burning
    the last few credits on a misclick."""

    def setUp(self):
        self._orig_provider = verifier_service.VERIFIER_PROVIDER
        self._orig_mv_key = verifier_service.MILLIONVERIFIER_API_KEY
        verifier_service.VERIFIER_PROVIDER = 'millionverifier'
        verifier_service.MILLIONVERIFIER_API_KEY = 'mv-test-key'
        verifier_service._account_cache['data'] = None
        verifier_service._account_cache['fetched_at'] = 0

    def tearDown(self):
        verifier_service.VERIFIER_PROVIDER = self._orig_provider
        verifier_service.MILLIONVERIFIER_API_KEY = self._orig_mv_key

    @patch('verifier_service.requests.get')
    def test_verify_refuses_when_credits_at_floor(self, mock_get):
        """When MV credits ≤ floor (5), verify_email refuses without
        making the actual probe call."""
        # Mock account_info: 3 credits, below floor
        mock_account = MagicMock(status_code=200)
        mock_account.json.return_value = {'credits': 3}
        mock_get.return_value = mock_account

        result = verifier_service.verify_email('test@example.com')

        self.assertTrue(result.get('credit_exhausted'),
                        'verify_email must refuse below floor')
        # The verify endpoint must NOT have been called — only /credits
        called_urls = [c.args[0] for c in mock_get.call_args_list]
        for url in called_urls:
            self.assertNotIn('/api?',
                             url.replace('credits', ''),
                             'verify endpoint must not be hit when below floor')


class TestSafeToSendDerivation(unittest.TestCase):
    """The `safe_to_send` flag is the convenience boolean callers use to
    drop bad addresses before sending. It must be conservative across
    all backends — one provider returning slightly different shapes can't
    weaken the safety guarantee."""

    def setUp(self):
        self._orig_provider = verifier_service.VERIFIER_PROVIDER
        self._orig_mv_key = verifier_service.MILLIONVERIFIER_API_KEY
        verifier_service.VERIFIER_PROVIDER = 'millionverifier'
        verifier_service.MILLIONVERIFIER_API_KEY = 'mv-test-key'
        verifier_service._account_cache['data'] = None
        verifier_service._account_cache['fetched_at'] = 0

    def tearDown(self):
        verifier_service.VERIFIER_PROVIDER = self._orig_provider
        verifier_service.MILLIONVERIFIER_API_KEY = self._orig_mv_key

    @patch('verifier_service.requests.get')
    def test_mv_catch_all_is_not_safe(self, mock_get):
        """MillionVerifier resultcode=2 (catch-all) must NOT be safe_to_send,
        even if quality is 'good'. Catch-all addresses tank deliverability."""
        mock_account = MagicMock(status_code=200)
        mock_account.json.return_value = {'credits': 100}
        mock_verify = MagicMock(status_code=200)
        mock_verify.json.return_value = {
            'resultcode': 2,  # catch_all
            'quality': 'good',
            'result': 'catch_all',
        }
        mock_get.side_effect = [mock_account, mock_verify]

        result = verifier_service.verify_email('test@example.com')
        self.assertEqual(result.get('result'), 'risky')
        self.assertFalse(result.get('safe_to_send'),
                         'catch-all must not be marked safe_to_send')

    @patch('verifier_service.requests.get')
    def test_mv_disposable_is_not_safe(self, mock_get):
        """Disposable addresses (resultcode=5) must not be safe_to_send."""
        mock_account = MagicMock(status_code=200)
        mock_account.json.return_value = {'credits': 100}
        mock_verify = MagicMock(status_code=200)
        mock_verify.json.return_value = {
            'resultcode': 5, 'quality': 'bad', 'disposable': True,
        }
        mock_get.side_effect = [mock_account, mock_verify]

        result = verifier_service.verify_email('throwaway@10minutemail.com')
        self.assertEqual(result.get('result'), 'undeliverable')
        self.assertFalse(result.get('safe_to_send'))

    @patch('verifier_service.requests.get')
    def test_mv_good_quality_is_safe(self, mock_get):
        """The happy path: MV resultcode=1, quality=good → safe_to_send."""
        mock_account = MagicMock(status_code=200)
        mock_account.json.return_value = {'credits': 100}
        mock_verify = MagicMock(status_code=200)
        mock_verify.json.return_value = {
            'resultcode': 1, 'quality': 'good', 'result': 'ok',
        }
        mock_get.side_effect = [mock_account, mock_verify]

        result = verifier_service.verify_email('valid@example.com')
        self.assertEqual(result.get('result'), 'deliverable')
        self.assertTrue(result.get('safe_to_send'))


class TestNoApiKeyLeakage(unittest.TestCase):
    """API keys must never appear in logs or response bodies."""

    def test_safe_log_error_redacts_key(self):
        original_mv = verifier_service.MILLIONVERIFIER_API_KEY
        try:
            verifier_service.MILLIONVERIFIER_API_KEY = 'sensitive-mv-key-12345'
            exc = RuntimeError(
                'connection failed: api=sensitive-mv-key-12345 timeout'
            )
            with self.assertLogs('verifier_service', level='WARNING') as cm:
                verifier_service._safe_log_error(
                    'test_prefix', exc,
                    key=verifier_service.MILLIONVERIFIER_API_KEY,
                )
            log = '\n'.join(cm.output)
            self.assertNotIn('sensitive-mv-key-12345', log,
                             'API key was leaked through warning log')
            self.assertIn('[redacted]', log)
        finally:
            verifier_service.MILLIONVERIFIER_API_KEY = original_mv


if __name__ == '__main__':
    unittest.main(verbosity=2)
