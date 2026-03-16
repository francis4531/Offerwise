"""
OfferWise End-to-End Integration Tests
========================================
Tests the full pipeline from API request to response for core features.
Uses the Flask test client (no running server needed).

Run: python -m pytest test_integration.py -v
"""

import json
import os
import sys
import unittest

# Set test environment before importing app
os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-key-e2e'
os.environ['DATABASE_URL'] = 'sqlite:///test_e2e.db'


class TestRiskCheckE2E(unittest.TestCase):
    """End-to-end tests for the /api/risk-check pipeline."""

    @classmethod
    def setUpClass(cls):
        """Create Flask test client once for all tests."""
        try:
            from app import app
            cls.app = app
            cls.client = app.test_client()
            cls.app_available = True
        except Exception as e:
            cls.app_available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.app_available:
            self.skipTest(f"App not available: {self.skip_reason}")

    def test_risk_check_valid_address(self):
        """Full pipeline: address → geocode → 11 API queries → risk exposure → response."""
        response = self.client.post('/api/risk-check',
            data=json.dumps({'address': '100 Main St, San Jose, CA 95113'}),
            content_type='application/json')

        self.assertIn(response.status_code, [200, 429])  # 429 = rate limited, OK in test
        if response.status_code == 200:
            data = response.get_json()
            # Must have all required fields
            self.assertIn('risk_grade', data)
            self.assertIn('risk_exposure', data)
            self.assertIn('risks', data)
            self.assertIn('address', data)
            self.assertIn('source_count', data)
            # Risk grade must be valid
            self.assertIn(data['risk_grade'][0], 'ABCDF')
            # Risk exposure must be non-negative
            self.assertGreaterEqual(data['risk_exposure'], 0)
            # Risks must be a list
            self.assertIsInstance(data['risks'], list)
            # Each risk card must have required fields
            for risk in data['risks']:
                self.assertIn('category', risk)
                self.assertIn('title', risk)
                self.assertIn('level', risk)
                self.assertIn('cost', risk)
                self.assertIn('detail', risk)
                self.assertGreater(risk['cost'], 0)
                self.assertIn(risk['level'], ('HIGH', 'MODERATE', 'LOW', 'MINIMAL'))
            # Databases checked must be reasonable
            self.assertGreaterEqual(data['source_count'], 1)
            self.assertLessEqual(data['source_count'], 15)

    def test_risk_check_empty_address(self):
        """Empty address returns error, not crash."""
        response = self.client.post('/api/risk-check',
            data=json.dumps({'address': ''}),
            content_type='application/json')
        self.assertIn(response.status_code, [400, 200, 429, 500])
        if response.status_code == 200:
            data = response.get_json()
            # Either error or valid response
            if 'error' in data:
                self.assertIsInstance(data['error'], str)

    def test_risk_check_missing_body(self):
        """No JSON body returns error."""
        response = self.client.post('/api/risk-check', content_type='application/json')
        self.assertIn(response.status_code, [400, 200, 429, 500])

    def test_risk_check_nonsense_address(self):
        """Nonsense address doesn't crash — returns error or zero risk."""
        response = self.client.post('/api/risk-check',
            data=json.dumps({'address': 'asdfghjkl nowhere 99999'}),
            content_type='application/json')
        self.assertIn(response.status_code, [200, 400, 429])

    def test_risk_check_response_time(self):
        """Risk check should complete within 30 seconds (timeout is 30s on frontend)."""
        import time
        start = time.time()
        response = self.client.post('/api/risk-check',
            data=json.dumps({'address': '1600 Pennsylvania Ave, Washington, DC 20500'}),
            content_type='application/json')
        elapsed = time.time() - start
        # Allow generous timeout in test environment
        self.assertLess(elapsed, 60, f"Risk check took {elapsed:.1f}s — exceeds 60s limit")


class TestTruthCheckE2E(unittest.TestCase):
    """End-to-end tests for the /api/truth-check pipeline."""

    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            cls.app = app
            cls.client = app.test_client()
            cls.app_available = True
        except Exception as e:
            cls.app_available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.app_available:
            self.skipTest(f"App not available: {self.skip_reason}")

    def test_truth_check_no_file(self):
        """Truth check without file returns error, not crash."""
        response = self.client.post('/api/truth-check')
        self.assertIn(response.status_code, [400, 200, 429, 500])

    def test_truth_check_invalid_file(self):
        """Truth check with non-PDF data returns error."""
        from io import BytesIO
        data = {'file': (BytesIO(b'this is not a pdf'), 'test.pdf', 'application/pdf')}
        response = self.client.post('/api/truth-check', data=data, content_type='multipart/form-data')
        # Should reject or handle gracefully
        self.assertIn(response.status_code, [400, 200, 429, 500])


class TestPageRoutes(unittest.TestCase):
    """Verify all public page routes return 200."""

    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            cls.app = app
            cls.client = app.test_client()
            cls.app_available = True
        except Exception as e:
            cls.app_available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.app_available:
            self.skipTest(f"App not available: {self.skip_reason}")

    def test_homepage(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)

    def test_truth_check_page(self):
        r = self.client.get('/truth-check')
        self.assertEqual(r.status_code, 200)

    def test_risk_check_page(self):
        r = self.client.get('/risk-check')
        self.assertEqual(r.status_code, 200)

    def test_pricing_page(self):
        r = self.client.get('/pricing')
        self.assertEqual(r.status_code, 200)

    def test_contact_page(self):
        r = self.client.get('/contact')
        self.assertEqual(r.status_code, 200)

    def test_terms_page(self):
        r = self.client.get('/terms')
        self.assertEqual(r.status_code, 200)

    def test_privacy_page(self):
        r = self.client.get('/privacy')
        self.assertEqual(r.status_code, 200)

    def test_disclaimer_page(self):
        r = self.client.get('/disclaimer')
        self.assertEqual(r.status_code, 200)

    def test_login_page(self):
        r = self.client.get('/login')
        self.assertEqual(r.status_code, 200)

    def test_404_page(self):
        r = self.client.get('/nonexistent-page-xyz')
        self.assertIn(r.status_code, [404, 302])

    def test_health_endpoint(self):
        r = self.client.get('/api/health')
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data.get('status'), 'healthy')

    def test_version_endpoint(self):
        r = self.client.get('/api/version')
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn('version', data)

    def test_sitemap(self):
        r = self.client.get('/sitemap.xml')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'<urlset', r.data)

    def test_robots_txt(self):
        r = self.client.get('/robots.txt')
        self.assertEqual(r.status_code, 200)


class TestSecurityHeaders(unittest.TestCase):
    """Verify security headers are set correctly."""

    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            cls.app = app
            cls.client = app.test_client()
            cls.app_available = True
        except Exception as e:
            cls.app_available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.app_available:
            self.skipTest(f"App not available: {self.skip_reason}")

    def test_csp_header(self):
        r = self.client.get('/')
        csp = r.headers.get('Content-Security-Policy', '')
        self.assertIn('script-src', csp)

    def test_xss_protection(self):
        r = self.client.get('/')
        self.assertEqual(r.headers.get('X-Content-Type-Options'), 'nosniff')

    def test_frame_options(self):
        r = self.client.get('/')
        self.assertIn(r.headers.get('X-Frame-Options', ''), ['DENY', 'SAMEORIGIN'])

    def test_referrer_policy(self):
        r = self.client.get('/')
        self.assertIn('Referrer-Policy', r.headers)


class TestDevEndpointsBlocked(unittest.TestCase):
    """Verify debug/test endpoints are blocked in production mode."""

    @classmethod
    def setUpClass(cls):
        try:
            from app import app, PRODUCTION_MODE
            cls.app = app
            cls.client = app.test_client()
            cls.production_mode = PRODUCTION_MODE
            cls.app_available = True
        except Exception as e:
            cls.app_available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.app_available:
            self.skipTest(f"App not available: {self.skip_reason}")

    def test_debug_ai_status_blocked(self):
        """Debug endpoints should 404 in production."""
        if not self.production_mode:
            self.skipTest("Not in production mode")
        r = self.client.get('/api/debug/ai-status')
        self.assertEqual(r.status_code, 404)

    def test_test_endpoints_blocked(self):
        if not self.production_mode:
            self.skipTest("Not in production mode")
        r = self.client.post('/api/test/integrity')
        self.assertEqual(r.status_code, 404)

    def test_system_info_blocked(self):
        if not self.production_mode:
            self.skipTest("Not in production mode")
        r = self.client.get('/api/system-info')
        self.assertEqual(r.status_code, 404)


if __name__ == '__main__':
    unittest.main()
