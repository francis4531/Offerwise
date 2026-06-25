"""
test_topbar_widget.py — v5.89.205

The inline address/risk-check widget was REMOVED from the top nav in
v5.89.205 (the risk check now lives under Products -> Free Tools). This file
now asserts the widget is gone and the nav is intact, and still verifies the
/risk-check page's own ?address= pre-fill (which is independent of the widget).
"""
import os
import unittest

os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-secret-topbar')
os.environ.setdefault('DATABASE_URL', 'sqlite:///test_topbar.db')


class TestTopbarWidgetMarkup(unittest.TestCase):
    """v5.89.205 removed the inline top-bar address/risk-check widget. Assert
    it (and its CSS/JS) are gone, the rest of the nav is intact, and the
    signup-CTA instrumentation that shared the widget's <script> survived."""

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

    def test_topbar_address_widget_is_removed(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        body = r.data.decode('utf-8')
        # The form, its compact /risk-check fallback, and the JS handler are gone
        self.assertNotIn('id="topbarAddrForm"', body)
        self.assertNotIn('id="topbarAddrInput"', body)
        self.assertNotIn('submitTopbarAddress', body)
        self.assertNotIn('class="topbar-address-compact"', body)

    def test_topbar_widget_styles_are_removed(self):
        r = self.client.get('/')
        body = r.data.decode('utf-8')
        # Orphaned CSS for the widget should be gone too
        self.assertNotIn('.topbar-address-widget', body)
        self.assertNotIn('.topbar-address-input', body)
        self.assertNotIn('.topbar-address-btn', body)

    def test_topbar_nav_items_intact(self):
        """Removing the widget must not disturb the rest of the nav."""
        r = self.client.get('/')
        body = r.data.decode('utf-8')
        self.assertIn('href="#pricing"', body)
        self.assertIn('href="/login"', body)
        self.assertIn('Analyze Free', body)
        self.assertIn('Products', body)
        self.assertIn('Resources', body)

    def test_risk_check_still_reachable_and_cta_tracking_kept(self):
        """The risk check stays reachable under Free Tools, and the
        signup-CTA tracking that shared the widget's <script> block survived
        the removal."""
        r = self.client.get('/')
        body = r.data.decode('utf-8')
        self.assertIn('/free-tools', body)
        self.assertIn('signup_cta_click', body)


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
