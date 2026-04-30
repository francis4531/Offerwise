"""
test_required_html_ids.py — Smoke test for HTML element IDs that JS depends on.

Why this exists
---------------
v5.87.32 fixed a P0 bug (reported by user Matt Wheeler 2026-04-27) where the
Settings → Legal panel showed "Documents require review · 0 of 3 complete"
with no documents to read or accept and a permanently-disabled Accept button.

Root cause: a prior refactor removed the document acceptance HTML sections
from `consents-required-view` but left the JS that targeted them intact.
Every `getElementById()` returned null, every `if (element)` guard silently
no-op'd, and the user saw an incomplete UI. Nothing logged, nothing crashed,
the bug shipped silently for some unknown amount of time.

This test prevents the same category of regression: when a page's JS depends
on specific element IDs being present in the HTML, that contract is now
explicit and verified on every CI run.

How it works
------------
The MANIFEST below is a list of (page, panel_description, required_ids).
For each entry, we read the HTML file and assert every required ID is present
as `id="..."`. If anything is missing, the test fails with a message that
names the page, the panel, and the missing IDs.

When to extend this
-------------------
Whenever you write JS that does `document.getElementById('...')` for a
visually-critical element (one whose absence would render the panel broken),
add the ID to the manifest. The cost is one line per ID, the benefit is
catching the next "ghost UI" regression in CI rather than via a customer
email.

DO NOT add IDs for elements that are:
  - dynamically created at runtime (these don't exist in the static HTML)
  - genuinely optional (where missing-element is a valid state)
  - in templates / shared partials (covered separately)

Run with: python -m pytest test_required_html_ids.py -v
"""
import os
import re
import unittest

BASE = os.path.join(os.path.dirname(__file__), 'static')


# Manifest of element IDs that JS expects to find in static HTML.
# Format: (filename, panel_description, [required_ids])
#
# Each entry is one logical "panel" or "feature" on a page. If any of the IDs
# are missing, the panel is broken in a way that won't necessarily throw a
# JS error but will silently show a half-rendered UI to the user.
REQUIRED_IDS = [
    # ─────────────────────────────────────────────────────────────────────
    # settings.html — Legal panel (Matt's bug, v5.87.32)
    # ─────────────────────────────────────────────────────────────────────
    (
        'settings.html',
        'Legal panel — top-level structure',
        [
            'panel-legal',
            'consent-loading',
            'consents-accepted-view',
            'consents-required-view',
            'consents-list',
        ],
    ),
    (
        'settings.html',
        'Legal panel — required-view: progress bar + button',
        [
            'consent-progress',
            'consent-progress-bar',
            'consent-progress-text',  # the dedicated counter span (v5.87.32)
            'accept-all-consents-btn',
            'consent-error',
        ],
    ),
    (
        'settings.html',
        'Legal panel — Analysis Disclaimer section',
        [
            'disclaimer-section',
            'disclaimer-full-text',  # populated by loadConsentTexts()
            'accept-disclaimer',     # checkbox listened by setupConsentCheckboxes()
            'disclaimer-check',
            'disclaimer-status',
        ],
    ),
    (
        'settings.html',
        'Legal panel — Terms of Service section',
        [
            'terms-section',
            'terms-full-text',
            'accept-terms',
            'terms-check',
            'terms-status',
        ],
    ),
    (
        'settings.html',
        'Legal panel — Privacy Policy section',
        [
            'privacy-section',
            'privacy-full-text',
            'accept-privacy',
            'privacy-check',
            'privacy-status',
        ],
    ),
]


def _read(fname):
    path = os.path.join(BASE, fname)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


def _has_id(content, element_id):
    """Return True if the HTML contains an element with this id.

    We accept either id="..." or id='...' since some pages use single quotes.
    Whitespace tolerant. Does NOT do full HTML parsing — this is a smoke test,
    not a structural validator.
    """
    pattern = r'\bid\s*=\s*["\']' + re.escape(element_id) + r'["\']'
    return re.search(pattern, content) is not None


class TestRequiredHtmlIds(unittest.TestCase):
    """One test per (page, panel) entry in REQUIRED_IDS."""

    def test_manifest_is_non_empty(self):
        """Sanity: the manifest itself shouldn't be empty.

        Catches the failure mode where someone deletes all the entries thinking
        the test is too noisy. If we ever genuinely don't need this test, the
        right move is to delete the file, not empty the manifest.
        """
        self.assertGreater(len(REQUIRED_IDS), 0,
                           'REQUIRED_IDS manifest is empty — see file docstring')

    def test_all_required_ids_present(self):
        """For every (page, panel) entry, assert all required IDs are in the HTML.

        Failure message names the page, the panel, and the specific missing IDs
        so the developer who broke the test can diagnose without re-reading
        the codebase.
        """
        failures = []  # collected so we report ALL missing IDs, not just the first

        for fname, panel, ids in REQUIRED_IDS:
            content = _read(fname)
            if content is None:
                failures.append(
                    f'{fname}: file not found (expected at static/{fname})'
                )
                continue

            missing = [eid for eid in ids if not _has_id(content, eid)]
            if missing:
                failures.append(
                    f'{fname} · {panel}: missing IDs {missing}'
                )

        if failures:
            msg_lines = [
                '',
                'Required HTML element IDs are missing.',
                '',
                'JS in these files queries elements by ID. When the IDs are absent,',
                "the JS silently no-ops via 'if (element)' guards and users see a",
                'half-rendered UI with no error logged. This is the exact bug class',
                "that caused v5.87.32's P0 (Matt Wheeler's screenshot).",
                '',
                'Failures:',
            ]
            msg_lines.extend(f'  • {f}' for f in failures)
            msg_lines.append('')
            msg_lines.append(
                'Fix: either add the missing element to the HTML, or remove '
                'the entry from REQUIRED_IDS in test_required_html_ids.py if '
                'the contract is genuinely no longer needed.'
            )
            self.fail('\n'.join(msg_lines))


if __name__ == '__main__':
    unittest.main(verbosity=2)
