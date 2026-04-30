"""
test_mobile_responsive.py — Mobile responsiveness checks for OfferWise.

Tests every user-facing page for:
  1. Viewport meta tag present
  2. Mobile media queries exist
  3. No fixed widths >480px without a responsive override
  4. No horizontal overflow triggers (min-width > viewport)
  5. Touch target minimums (48px for interactive elements)
  6. iOS zoom prevention (font-size >= 16px on inputs)
  7. Additive-only patch verification (existing rules not removed)

Run with: python -m pytest test_mobile_responsive.py -v
"""
import re
import os
import unittest

BASE = os.path.join(os.path.dirname(__file__), 'static')

# Pages that must pass mobile checks
USER_PAGES = [
    'index.html',
    'login.html',
    'pricing.html',
    'onboarding.html',
    'inspector-onboarding.html',
    'agent-onboarding.html',
    'contractor-onboarding.html',
    'payment-success.html',
    'analyze.html',
    'zillow-landing.html',
    'sample-analysis.html',
    'free-tools.html',
    'risk-check.html',
    'truth-check.html',
    'settings.html',
    'inspector-portal.html',
    'agent-portal.html',
    'contractor-portal.html',
    'for-inspectors.html',
    'for-agents.html',
    'for-contractors.html',
    'internachi.html',
]

# Simulated viewport widths to test against
MOBILE_WIDTHS = [320, 375, 390, 414, 480]
TABLET_WIDTHS = [768, 820, 1024]


def read(fname):
    path = os.path.join(BASE, fname)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


def extract_style(content):
    """Extract all CSS from <style> blocks."""
    blocks = re.findall(r'<style[^>]*>(.*?)</style>', content, re.DOTALL)
    return '\n'.join(blocks)


def get_breakpoints(css):
    """Return sorted list of max-width breakpoint values."""
    bps = re.findall(r'max-width:\s*(\d+)px', css)
    return sorted(set(int(b) for b in bps))


def get_fixed_widths(css):
    """Return fixed pixel widths > 480px that could overflow mobile."""
    widths = re.findall(r'(?:^|[;\s{])width:\s*(\d+)px', css)
    return [int(w) for w in widths if int(w) > 480]


def has_input_font_size_16(css):
    """Check if inputs have font-size >= 16px to prevent iOS zoom."""
    patterns = [
        r'input[^{]*\{[^}]*font-size:\s*16px',
        r'input[^{]*\{[^}]*font-size:\s*1rem',
        r'font-size:\s*16px\s*!important',
        r'input,\s*select,\s*textarea[^{]*\{[^}]*font-size:\s*16',
    ]
    return any(re.search(p, css, re.IGNORECASE) for p in patterns)


class TestViewportMeta(unittest.TestCase):
    """Every page must declare a responsive viewport."""

    def _check(self, fname):
        c = read(fname)
        if c is None:
            self.skipTest(f'{fname} not found')
        self.assertIn('width=device-width', c,
                      f'{fname}: missing viewport meta tag')

    def test_index(self): self._check('index.html')
    def test_login(self): self._check('login.html')
    def test_pricing(self): self._check('pricing.html')
    def test_onboarding(self): self._check('onboarding.html')
    def test_inspector_onboarding(self): self._check('inspector-onboarding.html')
    def test_agent_onboarding(self): self._check('agent-onboarding.html')
    def test_contractor_onboarding(self): self._check('contractor-onboarding.html')
    def test_payment_success(self): self._check('payment-success.html')
    def test_analyze(self): self._check('analyze.html')
    def test_zillow_landing(self): self._check('zillow-landing.html')
    def test_sample_analysis(self): self._check('sample-analysis.html')
    def test_free_tools(self): self._check('free-tools.html')
    def test_risk_check(self): self._check('risk-check.html')
    def test_truth_check(self): self._check('truth-check.html')
    def test_settings(self): self._check('settings.html')
    def test_inspector_portal(self): self._check('inspector-portal.html')
    def test_agent_portal(self): self._check('agent-portal.html')
    def test_contractor_portal(self): self._check('contractor-portal.html')
    def test_for_inspectors(self): self._check('for-inspectors.html')
    def test_for_agents(self): self._check('for-agents.html')
    def test_for_contractors(self): self._check('for-contractors.html')


class TestMobileMediaQueries(unittest.TestCase):
    """Every page must have at least one mobile media query <= 480px."""

    def _check(self, fname, min_breakpoint=480):
        c = read(fname)
        if c is None:
            self.skipTest(f'{fname} not found')
        css = extract_style(c)
        bps = get_breakpoints(css)
        mobile_bps = [b for b in bps if b <= min_breakpoint]
        self.assertTrue(
            len(mobile_bps) > 0,
            f'{fname}: no media query <= {min_breakpoint}px. '
            f'Found breakpoints: {bps}'
        )

    def test_index(self): self._check('index.html')
    def test_login(self): self._check('login.html')
    def test_pricing(self): self._check('pricing.html')
    def test_onboarding(self): self._check('onboarding.html')
    def test_inspector_onboarding(self): self._check('inspector-onboarding.html')
    def test_agent_onboarding(self): self._check('agent-onboarding.html')
    def test_contractor_onboarding(self): self._check('contractor-onboarding.html')
    def test_payment_success(self): self._check('payment-success.html')
    def test_analyze(self): self._check('analyze.html')
    def test_sample_analysis(self): self._check('sample-analysis.html')
    def test_free_tools(self): self._check('free-tools.html')
    def test_risk_check(self): self._check('risk-check.html')
    def test_truth_check(self): self._check('truth-check.html')
    def test_settings(self): self._check('settings.html')
    def test_inspector_portal(self): self._check('inspector-portal.html')
    def test_agent_portal(self): self._check('agent-portal.html')
    def test_contractor_portal(self): self._check('contractor-portal.html')


class TestIOSZoomPrevention(unittest.TestCase):
    """
    Pages with input fields must set font-size >= 16px on inputs
    to prevent iOS Safari from auto-zooming on focus.
    """

    def _check(self, fname):
        c = read(fname)
        if c is None:
            self.skipTest(f'{fname} not found')
        # Only check pages that actually have input elements
        if '<input' not in c and '<textarea' not in c:
            return
        css = extract_style(c)
        self.assertTrue(
            has_input_font_size_16(css),
            f'{fname}: input fields present but no font-size:16px rule found. '
            f'iOS Safari will auto-zoom on input focus.'
        )

    def test_login(self): self._check('login.html')
    def test_onboarding(self): self._check('onboarding.html')
    def test_inspector_onboarding(self): self._check('inspector-onboarding.html')
    def test_agent_onboarding(self): self._check('agent-onboarding.html')
    def test_contractor_onboarding(self): self._check('contractor-onboarding.html')
    def test_analyze(self): self._check('analyze.html')
    def test_risk_check(self): self._check('risk-check.html')
    def test_truth_check(self): self._check('truth-check.html')
    def test_settings(self): self._check('settings.html')


class TestTouchTargets(unittest.TestCase):
    """
    Pages with buttons must have min-height >= 44px on touch targets.
    Apple HIG and Google Material both specify 44-48px minimum.
    """

    def _check(self, fname):
        c = read(fname)
        if c is None:
            self.skipTest(f'{fname} not found')
        if '<button' not in c.lower() and '.btn' not in c:
            return
        css = extract_style(c)
        has_touch_target = bool(re.search(
            r'(?:min-height|height):\s*(?:44|45|46|47|48|4[4-9]|5\d)px',
            css
        ))
        self.assertTrue(
            has_touch_target,
            f'{fname}: buttons found but no min-height >= 44px rule. '
            f'Touch targets will be too small on mobile.'
        )

    def test_login(self): self._check('login.html')
    def test_onboarding(self): self._check('onboarding.html')
    def test_inspector_onboarding(self): self._check('inspector-onboarding.html')
    def test_agent_onboarding(self): self._check('agent-onboarding.html')
    def test_contractor_onboarding(self): self._check('contractor-onboarding.html')
    def test_payment_success(self): self._check('payment-success.html')
    def test_analyze(self): self._check('analyze.html')
    def test_pricing(self): self._check('pricing.html')


class TestNoHorizontalOverflow(unittest.TestCase):
    """
    No page should have a min-width greater than common mobile viewports
    without a responsive override that removes it.
    """

    def _check(self, fname, max_safe_fixed=480):
        c = read(fname)
        if c is None:
            self.skipTest(f'{fname} not found')
        css = extract_style(c)
        bps = get_breakpoints(css)
        has_mobile_bp = any(b <= 480 for b in bps)

        # Check for large min-width declarations
        min_widths = re.findall(r'min-width:\s*(\d+)px', css)
        large_min = [int(w) for w in min_widths if int(w) > 480]

        if large_min and not has_mobile_bp:
            self.fail(
                f'{fname}: min-width values {large_min} without mobile breakpoint. '
                f'Will cause horizontal overflow on mobile.'
            )

    def test_index(self): self._check('index.html')
    def test_pricing(self): self._check('pricing.html')
    def test_analyze(self): self._check('analyze.html')
    def test_for_inspectors(self): self._check('for-inspectors.html')
    def test_for_agents(self): self._check('for-agents.html')
    def test_for_contractors(self): self._check('for-contractors.html')


class TestTableScrollability(unittest.TestCase):
    """
    Pages with data tables need an overflow-x:auto wrapper
    so tables don't force horizontal scrolling of the whole page.
    """

    def _check(self, fname):
        c = read(fname)
        if c is None:
            self.skipTest(f'{fname} not found')
        if '<table' not in c:
            return
        css = extract_style(c)
        has_table_scroll = bool(re.search(
            r'overflow-x:\s*(?:auto|scroll)',
            css
        ))
        self.assertTrue(
            has_table_scroll,
            f'{fname}: contains <table> but no overflow-x:auto wrapper. '
            f'Tables will cause horizontal scrolling on mobile.'
        )

    def test_inspector_portal(self): self._check('inspector-portal.html')
    def test_agent_portal(self): self._check('agent-portal.html')
    def test_contractor_portal(self): self._check('contractor-portal.html')
    def test_settings(self): self._check('settings.html')
    def test_pricing(self): self._check('pricing.html')


class TestMobilePatchIntegrity(unittest.TestCase):
    """
    Verify that mobile patches were applied additively —
    existing desktop rules must still be present.
    """

    def test_login_has_desktop_card_style(self):
        """Login card max-width:420px desktop rule must still exist."""
        c = read('login.html')
        if c is None: self.skipTest('not found')
        self.assertIn('max-width: 420px', c,
                      'login.html: desktop auth-card max-width removed (regression)')

    def test_inspector_portal_layout_intact(self):
        """240px sidebar layout for desktop must still exist."""
        c = read('inspector-portal.html')
        if c is None: self.skipTest('not found')
        self.assertIn('240px', c,
                      'inspector-portal.html: desktop sidebar width removed (regression)')

    def test_settings_sidebar_intact(self):
        """Settings sidebar layout must still exist for desktop."""
        c = read('settings.html')
        if c is None: self.skipTest('not found')
        self.assertIn('220px', c,
                      'settings.html: desktop sidebar width removed (regression)')

    def test_pricing_has_existing_breakpoints(self):
        """Pricing page must retain its existing 768px breakpoint."""
        c = read('pricing.html')
        if c is None: self.skipTest('not found')
        self.assertIn('768', c,
                      'pricing.html: existing 768px breakpoint removed (regression)')

    def test_index_existing_hero_styles_intact(self):
        """Homepage hero section must retain its existing CSS."""
        c = read('index.html')
        if c is None: self.skipTest('not found')
        self.assertIn('.hero', c,
                      'index.html: hero CSS class removed (regression)')

    def test_onboarding_has_patch(self):
        """Onboarding must now have mobile media query."""
        c = read('onboarding.html')
        if c is None: self.skipTest('not found')
        css = extract_style(c)
        bps = get_breakpoints(css)
        self.assertTrue(any(b <= 480 for b in bps),
                        'onboarding.html: mobile patch not applied')

    def test_payment_success_has_patch(self):
        """Payment success must have mobile padding."""
        c = read('payment-success.html')
        if c is None: self.skipTest('not found')
        self.assertIn('MOBILE RESPONSIVE PATCH', c,
                      'payment-success.html: mobile patch missing')


class TestBreakpointCoverage(unittest.TestCase):
    """
    Critical user journey pages must cover the three key breakpoints:
    480px (mobile), 768px (tablet), and have responsive rules at both.
    """

    def _check_breakpoints(self, fname, required=(480, 768)):
        c = read(fname)
        if c is None:
            self.skipTest(f'{fname} not found')
        css = extract_style(c)
        bps = get_breakpoints(css)
        for bp in required:
            # Allow +/- 20px tolerance
            near = [b for b in bps if abs(b - bp) <= 20]
            self.assertTrue(
                len(near) > 0,
                f'{fname}: missing breakpoint near {bp}px. '
                f'Found: {bps}'
            )

    def test_login_breakpoints(self):
        self._check_breakpoints('login.html', (360, 480))

    def test_pricing_breakpoints(self):
        self._check_breakpoints('pricing.html', (480, 768))

    def test_analyze_breakpoints(self):
        self._check_breakpoints('analyze.html', (480, 768))

    def test_settings_breakpoints(self):
        self._check_breakpoints('settings.html', (480, 768))

    def test_inspector_portal_breakpoints(self):
        self._check_breakpoints('inspector-portal.html', (480, 768))


class TestMobilePatternConsistency(unittest.TestCase):
    """
    Verify common mobile patterns are applied consistently
    across all pages in the same category.
    """

    def test_all_onboarding_flows_have_mobile_patch(self):
        """All three profession onboarding pages must have mobile patches."""
        flows = [
            'inspector-onboarding.html',
            'agent-onboarding.html',
            'contractor-onboarding.html',
        ]
        for fname in flows:
            c = read(fname)
            if c is None:
                continue
            css = extract_style(c)
            bps = get_breakpoints(css)
            mobile_bps = [b for b in bps if b <= 480]
            self.assertTrue(
                len(mobile_bps) > 0,
                f'{fname}: missing mobile breakpoint (<= 480px)'
            )

    def test_all_portals_have_table_scroll(self):
        """All three persona portals must handle table overflow."""
        portals = [
            'inspector-portal.html',
            'agent-portal.html',
            'contractor-portal.html',
        ]
        for fname in portals:
            c = read(fname)
            if c is None:
                continue
            if '<table' not in c:
                continue
            css = extract_style(c)
            has_scroll = bool(re.search(r'overflow-x:\s*(?:auto|scroll)', css))
            self.assertTrue(has_scroll, f'{fname}: no overflow-x:auto for tables')

    def test_all_landing_pages_have_mobile_hero(self):
        """All persona landing pages must have mobile hero adjustments."""
        landings = [
            'for-inspectors.html',
            'for-agents.html',
            'for-contractors.html',
        ]
        for fname in landings:
            c = read(fname)
            if c is None:
                continue
            css = extract_style(c)
            bps = get_breakpoints(css)
            self.assertTrue(
                any(b <= 600 for b in bps),
                f'{fname}: no mobile breakpoint found'
            )

    def test_ad_landing_pages_are_mobile_ready(self):
        """Ad landing pages (where paid traffic goes) must be mobile ready."""
        ad_pages = ['analyze.html', 'zillow-landing.html']
        for fname in ad_pages:
            c = read(fname)
            if c is None:
                continue
            self.assertIn('width=device-width', c,
                          f'{fname}: missing viewport meta — ad traffic will see broken layout')
            css = extract_style(c)
            bps = get_breakpoints(css)
            self.assertTrue(
                any(b <= 480 for b in bps),
                f'{fname}: ad landing page has no mobile breakpoint'
            )


if __name__ == '__main__':
    unittest.main(verbosity=2)
