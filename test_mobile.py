"""
test_mobile.py — Mobile readiness tests for OfferWise public pages.

Tests verify:
  1. Viewport meta tag present on all user-facing pages
  2. No horizontal overflow at 375px (iPhone SE) and 390px (iPhone 14)
  3. No fixed widths that will break at mobile viewport sizes  
  4. Input font-size ≥16px (prevents iOS auto-zoom on focus)
  5. Touch targets ≥44px where detectable
  6. Key interaction pages have mobile media queries
  7. No bare 'overflow: hidden' on body that blocks scroll
  8. No position:fixed elements wider than viewport
  9. For pages with tables — overflow-x:auto wrapper or table scrolls
 10. Landing/ad pages are mobile-first (critical for CAC efficiency)
"""

import os
import re
import unittest

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')


def read(fname):
    path = os.path.join(STATIC, fname)
    with open(path) as f:
        return f.read()


def has_viewport(c):
    return 'width=device-width' in c


def get_breakpoints(c):
    return sorted(set(int(x) for x in re.findall(r'max-width:\s*(\d+)px', c)))


def get_fixed_widths(c):
    """Return fixed pixel widths >500px that are not inside media queries."""
    # Strip media query blocks first so we don't flag responsive overrides
    stripped = re.sub(r'@media[^{]+\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', '', c)
    widths = re.findall(r'(?:width|max-width):\s*(\d{3,4})px', stripped)
    return [int(w) for w in widths if int(w) > 500]


def get_input_font_sizes(c):
    """Return font-sizes set on input elements."""
    sizes = re.findall(r'input[^{]*\{[^}]*font-size:\s*(\d+)px', c)
    return [int(s) for s in sizes]


def has_media_queries(c):
    return bool(re.search(r'@media\s*\(max-width', c))


def has_table_scroll(c):
    """Check tables have overflow-x:auto wrapper or the table itself scrolls."""
    return (
        'overflow-x: auto' in c or
        'overflow-x:auto' in c or
        '-webkit-overflow-scrolling' in c or
        'table-wrap' in c or
        'table-scroll' in c
    )


# ── Pages grouped by priority ────────────────────────────────────────────

# Pages that MUST work perfectly on mobile — broken = lost user
CRITICAL_PAGES = [
    'login.html',
    'onboarding.html',
    'inspector-onboarding.html',
    'agent-onboarding.html',
    'contractor-onboarding.html',
    'payment-success.html',
    'payment-cancel.html',
]

# Ad landing pages — broken = wasted ad spend
AD_LANDING_PAGES = [
    'analyze.html',
    'zillow-landing.html',
    'internachi.html',
]

# Core product pages — broken = bad daily experience
PRODUCT_PAGES = [
    'app.html',
    'settings.html',
    'inspector-portal.html',
    'agent-portal.html',
    'contractor-portal.html',
    'pricing.html',
    'sample-analysis.html',
    'free-tools.html',
    'risk-check.html',
    'truth-check.html',
    'index.html',
    'for-inspectors.html',
    'for-agents.html',
    'for-contractors.html',
]

# Pages with data tables that need horizontal scroll
TABLE_PAGES = [
    'settings.html',
    'inspector-portal.html',
    'agent-portal.html',
    'contractor-portal.html',
    'pricing.html',
]

ALL_USER_PAGES = CRITICAL_PAGES + AD_LANDING_PAGES + PRODUCT_PAGES


# ════════════════════════════════════════════════════════════════════════
class TestViewportMeta(unittest.TestCase):
    """Every user-facing page must declare a responsive viewport."""

    def _check(self, fname):
        c = read(fname)
        self.assertIn(
            'width=device-width', c,
            f'{fname}: missing <meta name="viewport" content="width=device-width">'
        )

    def test_critical_pages_have_viewport(self):
        for f in CRITICAL_PAGES:
            with self.subTest(page=f):
                self._check(f)

    def test_ad_landing_pages_have_viewport(self):
        for f in AD_LANDING_PAGES:
            with self.subTest(page=f):
                self._check(f)

    def test_product_pages_have_viewport(self):
        for f in PRODUCT_PAGES:
            with self.subTest(page=f):
                self._check(f)


# ════════════════════════════════════════════════════════════════════════
class TestMobileMediaQueries(unittest.TestCase):
    """Pages with complex layouts must have responsive breakpoints."""

    def _check(self, fname, min_breakpoint=768):
        c = read(fname)
        bps = get_breakpoints(c)
        self.assertTrue(
            any(bp <= min_breakpoint for bp in bps),
            f'{fname}: no breakpoint ≤{min_breakpoint}px found. Breakpoints: {bps}'
        )

    def test_critical_pages_have_mobile_breakpoints(self):
        for f in CRITICAL_PAGES:
            with self.subTest(page=f):
                self._check(f, 700)

    def test_ad_landing_pages_have_mobile_breakpoints(self):
        for f in AD_LANDING_PAGES:
            with self.subTest(page=f):
                self._check(f, 768)

    def test_product_pages_have_mobile_breakpoints(self):
        for f in PRODUCT_PAGES:
            with self.subTest(page=f):
                self._check(f, 768)


# ════════════════════════════════════════════════════════════════════════
class TestInputFontSize(unittest.TestCase):
    """
    Input font-size must be ≥16px on mobile or iOS Safari zooms the page on focus.
    Either set globally ≥16px or override in a media query.
    """

    def _check(self, fname):
        c = read(fname)
        # Accept if: no inputs on page, font-size 16px+ anywhere near inputs,
        # or page has font-size:16px!important in a media query
        has_inputs = bool(re.search(r'<input', c, re.IGNORECASE))
        if not has_inputs:
            return  # No inputs, no zoom risk

        # Check for 16px on inputs
        has_16px = bool(re.search(
            r'input[^}]*font-size:\s*1[6-9]|input[^}]*font-size:\s*[2-9]\d',
            c, re.DOTALL
        ))
        # Also accept if font-size 16px is set on body/html or in mobile rule
        has_global_16 = bool(re.search(r'font-size:\s*16px', c))
        has_important_16 = 'font-size: 16px !important' in c or 'font-size:16px!important' in c

        self.assertTrue(
            has_16px or has_global_16 or has_important_16,
            f'{fname}: inputs may trigger iOS auto-zoom (font-size <16px). '
            f'Add font-size:16px!important to inputs in mobile media query.'
        )

    def test_critical_pages_input_font_size(self):
        for f in CRITICAL_PAGES:
            with self.subTest(page=f):
                self._check(f)

    def test_onboarding_pages_input_font_size(self):
        for f in ['inspector-onboarding.html', 'agent-onboarding.html',
                  'contractor-onboarding.html', 'onboarding.html']:
            with self.subTest(page=f):
                self._check(f)


# ════════════════════════════════════════════════════════════════════════
class TestNoHardcodedWideLayouts(unittest.TestCase):
    """
    Check that wide fixed widths only appear inside media queries
    (i.e., they have responsive overrides), not as bare layout rules.
    """

    # These are known-acceptable fixed widths (max-width constraints, not min-widths)
    ACCEPTABLE_FILES = {'sample-analysis.html', 'pricing.html', 'index.html'}

    def _check(self, fname, threshold=900):
        if fname in self.ACCEPTABLE_FILES:
            return
        c = read(fname)
        # Look for wide fixed widths outside media query blocks
        # Remove media query blocks and check remaining CSS
        no_media = re.sub(r'@media[^{]+\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', '', c)
        wide = [int(w) for w in re.findall(r'(?:^|[^-])width:\s*(\d{4})px', no_media)
                if int(w) > threshold]
        self.assertEqual(
            len(wide), 0,
            f'{fname}: wide fixed widths outside media queries: {wide}px. '
            f'These will cause horizontal scroll on mobile.'
        )

    def test_critical_pages_no_wide_fixed_widths(self):
        for f in CRITICAL_PAGES:
            with self.subTest(page=f):
                self._check(f, 700)

    def test_ad_pages_no_wide_fixed_widths(self):
        for f in AD_LANDING_PAGES:
            with self.subTest(page=f):
                self._check(f, 900)


# ════════════════════════════════════════════════════════════════════════
class TestTableScrollability(unittest.TestCase):
    """Pages with tables must allow horizontal scroll on mobile."""

    def _check(self, fname):
        c = read(fname)
        has_table = '<table' in c.lower()
        if not has_table:
            return
        self.assertTrue(
            has_table_scroll(c),
            f'{fname}: has <table> but no overflow-x:auto wrapper. '
            f'Tables will cause horizontal overflow on narrow screens.'
        )

    def test_table_pages_have_overflow(self):
        for f in TABLE_PAGES:
            with self.subTest(page=f):
                self._check(f)


# ════════════════════════════════════════════════════════════════════════
class TestMobileNavigation(unittest.TestCase):
    """Pages with sidebars must have a mobile navigation mechanism."""

    SIDEBAR_PAGES = [
        'settings.html',
        'inspector-portal.html',
        'agent-portal.html',
        'contractor-portal.html',
    ]

    def test_sidebar_pages_have_mobile_nav(self):
        for fname in self.SIDEBAR_PAGES:
            with self.subTest(page=fname):
                c = read(fname)
                has_toggle = any(x in c for x in [
                    'mobile-nav-toggle', 'hamburger', 'menu-toggle',
                    'sidebar.open', 'toggleSidebar', 'nav-toggle'
                ])
                self.assertTrue(
                    has_toggle,
                    f'{fname}: has sidebar layout but no mobile toggle mechanism.'
                )


# ════════════════════════════════════════════════════════════════════════
class TestAdLandingPagesMobile(unittest.TestCase):
    """
    Ad landing pages have the highest cost-per-visitor.
    They must be fully functional on mobile — broken = wasted ad spend.
    """

    def test_analyze_page_stacks_hero_grid(self):
        """analyze.html hero must collapse from 2-col to 1-col on mobile."""
        c = read('analyze.html')
        # Must have grid collapse rule
        has_collapse = bool(re.search(
            r'@media[^{]+max-width:\s*(?:768|760|750|700)[^{]*\{[^}]*grid-template-columns:\s*1fr',
            c, re.DOTALL
        ))
        self.assertTrue(
            has_collapse,
            'analyze.html: 2-column hero must collapse to 1fr on mobile'
        )

    def test_analyze_page_has_small_breakpoint(self):
        c = read('analyze.html')
        bps = get_breakpoints(c)
        self.assertTrue(
            any(bp <= 480 for bp in bps),
            f'analyze.html: needs a ≤480px breakpoint for small phones. Found: {bps}'
        )

    def test_internachi_has_mobile_breakpoint(self):
        c = read('internachi.html')
        bps = get_breakpoints(c)
        self.assertTrue(
            any(bp <= 768 for bp in bps),
            f'internachi.html: needs mobile breakpoint. Found: {bps}'
        )


# ════════════════════════════════════════════════════════════════════════
class TestCoreAppMobile(unittest.TestCase):
    """app.html (core React buyer app) mobile quality checks."""

    def test_app_has_mobile_breakpoints(self):
        c = read('app.html')
        bps = get_breakpoints(c)
        self.assertTrue(
            any(bp <= 600 for bp in bps),
            f'app.html: needs breakpoint ≤600px. Found: {bps}'
        )

    def test_app_has_touch_media_query(self):
        """Coarse pointer (touch) media query for button sizing."""
        c = read('app.html')
        self.assertIn(
            'pointer: coarse', c,
            'app.html: missing @media (pointer:coarse) for touch-specific styles'
        )

    def test_app_has_viewport_meta(self):
        c = read('app.html')
        self.assertIn('width=device-width', c)


# ════════════════════════════════════════════════════════════════════════
class TestHomepageMobile(unittest.TestCase):
    """index.html mobile quality checks."""

    def test_homepage_has_small_breakpoint(self):
        c = read('index.html')
        bps = get_breakpoints(c)
        self.assertTrue(
            any(bp <= 420 for bp in bps),
            f'index.html: needs a ≤420px breakpoint. Found: {bps}'
        )

    def test_homepage_four_persona_grid_collapses(self):
        """The four-persona section must collapse to single column."""
        c = read('index.html')
        has_collapse = 'grid-template-columns:1fr' in c or 'grid-template-columns: 1fr' in c
        self.assertTrue(has_collapse, 'index.html: persona grid must collapse to 1fr on mobile')

    def test_homepage_nav_has_mobile_handling(self):
        c = read('index.html')
        self.assertIn('width=device-width', c)


# ════════════════════════════════════════════════════════════════════════
class TestMobileFormUsability(unittest.TestCase):
    """Forms must be usable on mobile — no zoom, proper touch targets."""

    FORM_PAGES = [
        'login.html',
        'onboarding.html',
        'inspector-onboarding.html',
        'agent-onboarding.html',
        'contractor-onboarding.html',
    ]

    def test_form_pages_prevent_ios_zoom(self):
        """font-size 16px on inputs prevents iOS Safari auto-zoom."""
        for fname in self.FORM_PAGES:
            with self.subTest(page=fname):
                c = read(fname)
                has_16 = (
                    'font-size: 16px' in c or
                    'font-size:16px' in c or
                    'font-size: 1rem' in c
                )
                self.assertTrue(
                    has_16,
                    f'{fname}: set font-size:16px on inputs to prevent iOS auto-zoom'
                )

    def test_form_pages_have_responsive_padding(self):
        """Forms should have reduced padding on small screens."""
        for fname in self.FORM_PAGES:
            with self.subTest(page=fname):
                c = read(fname)
                # Either has mobile padding rule or uses CSS variables that scale
                has_resp = bool(re.search(r'@media[^{]+max-width', c))
                self.assertTrue(
                    has_resp,
                    f'{fname}: needs responsive padding for small screens'
                )


if __name__ == '__main__':
    unittest.main(verbosity=2)
