"""
test_topbar_widget.py — v5.87.91

Tests for the inline address-check widget in the top nav of index.html
and the address pre-fill on /risk-check.

These tests verify HTML structure (markup is present where expected)
plus integration (the destination route handles the ?address= param
without crashing). The actual scanner flow is exercised by the existing
test_integration.py — we don't re-test the API here.
"""
import os
import unittest

os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-secret-topbar')
os.environ.setdefault('DATABASE_URL', 'sqlite:///test_topbar.db')


class TestTopbarWidgetMarkup(unittest.TestCase):
    """The widget is server-side static HTML — verify markup is present."""

    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            cls.app = app
            cls.client = app.test_client(use_cookies=False)
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"App not available: {self.skip_reason}")

    def test_homepage_contains_topbar_widget_form(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        body = r.data.decode('utf-8')
        # Form must be present
        self.assertIn('id="topbarAddrForm"', body)
        # Input must have the right autocomplete attribute for browser address
        # autofill (street-address is the WHATWG standard token)
        self.assertIn('autocomplete="street-address"', body)
        self.assertIn('id="topbarAddrInput"', body)
        # Submit handler must be wired
        self.assertIn('submitTopbarAddress', body)

    def test_homepage_contains_compact_button_fallback(self):
        """The compact button is shown via @media query when the nav is too
        narrow for the full widget. The link must exist in markup so the
        responsive CSS has something to reveal."""
        r = self.client.get('/')
        body = r.data.decode('utf-8')
        self.assertIn('class="topbar-address-compact"', body)
        self.assertIn('href="/risk-check"', body)

    def test_homepage_widget_keeps_existing_pricing_login_cta(self):
        """Reading 2 — we keep ALL existing nav items. Widget is added
        alongside, not replacing anything."""
        r = self.client.get('/')
        body = r.data.decode('utf-8')
        # Existing items must still be there
        self.assertIn('href="#pricing"', body)
        self.assertIn('href="/login"', body)
        self.assertIn('Analyze Free', body)
        # And mega-menus must still be there
        self.assertIn('Products', body)
        self.assertIn('Resources', body)

    def test_homepage_widget_styles_are_inline(self):
        """The widget CSS must be present in the page so rendering
        doesn't depend on a separate stylesheet that might 404."""
        r = self.client.get('/')
        body = r.data.decode('utf-8')
        self.assertIn('.topbar-address-widget', body)
        self.assertIn('.topbar-address-input', body)
        self.assertIn('.topbar-address-btn', body)


class TestRiskCheckAddressPrefill(unittest.TestCase):
    """The /risk-check page pre-fills + auto-scans when ?address= is
    supplied. We can't trigger JS from a Flask test client, but we can
    verify the page renders (no 500) and the pre-fill JS is present."""

    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            cls.app = app
            cls.client = app.test_client(use_cookies=False)
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"App not available: {self.skip_reason}")

    def test_risk_check_renders_without_address_param(self):
        """Original /risk-check use case — no params, page just renders."""
        r = self.client.get('/risk-check')
        self.assertEqual(r.status_code, 200)
        body = r.data.decode('utf-8')
        self.assertIn('What\'s', body)  # hero copy

    def test_risk_check_renders_with_address_param(self):
        """v5.87.91: ?address=X should not 500. Pre-fill happens client-side."""
        r = self.client.get('/risk-check?address=100+Main+St+San+Jose+CA')
        self.assertEqual(r.status_code, 200)
        body = r.data.decode('utf-8')
        self.assertIn('addressInput', body)

    def test_risk_check_contains_address_prefill_js(self):
        """The page must contain the JS that reads ?address= and auto-scans.
        We're verifying the integration point exists, not running it."""
        r = self.client.get('/risk-check')
        body = r.data.decode('utf-8')
        # The new pre-fill block uses URLSearchParams to read 'address'
        self.assertIn("URLSearchParams(window.location.search).get('address')", body)
        self.assertIn('startScan', body)

    def test_risk_check_with_dangerous_address_param_does_not_crash(self):
        """Defensive: someone might pass weird stuff in ?address=. The Flask
        route doesn't process it server-side (JS does, client-side), but
        the page should still render cleanly."""
        # XSS-shaped string — server should just pass it through static HTML
        # and let the client-side JS escape it before injecting into DOM
        r = self.client.get('/risk-check?address=%3Cscript%3Ealert(1)%3C%2Fscript%3E')
        self.assertEqual(r.status_code, 200)


if __name__ == '__main__':
    unittest.main()
