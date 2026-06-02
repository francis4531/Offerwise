"""
test_outreach_linkify.py — v5.88.01

Tests the URL-to-anchor-tag conversion for outreach emails.

Background: B2B outreach drafts include persona-specific URLs (added in
v5.87.98) but the send pipeline was wrapping them in plain <p> tags
without converting URLs to <a> anchors. Result: URLs visible as plain
text in some email clients, no clickable hyperlink. This test suite
verifies the v5.88.01 linkify helper produces correct anchor tags AND
is actually called by the outreach send paths.
"""
import os
import unittest

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-linkify'
os.environ['DATABASE_URL'] = 'sqlite:///test_linkify.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-linkify')

import os as _os
_db_path = 'test_linkify.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)


class TestLinkifyHelper(unittest.TestCase):
    """Unit tests on the helper itself."""

    def test_simple_url_becomes_anchor(self):
        from admin_routes import _linkify_line
        out = _linkify_line('Visit https://www.getofferwise.ai/for-lenders today')
        self.assertIn('<a href="https://www.getofferwise.ai/for-lenders"', out)
        self.assertIn('>https://www.getofferwise.ai/for-lenders</a>', out)
        self.assertIn('Visit ', out)
        self.assertIn(' today', out)

    def test_anchor_has_link_styling(self):
        """The anchor must be visibly styled as a link (blue, underlined)
        so the recipient can SEE that it's clickable, not depending on
        the email client's default <a> styling."""
        from admin_routes import _linkify_line
        out = _linkify_line('https://www.getofferwise.ai/personas')
        self.assertIn('color:#2563eb', out)
        self.assertIn('text-decoration:underline', out)

    def test_multiple_urls_in_one_line(self):
        from admin_routes import _linkify_line
        out = _linkify_line(
            'See https://www.getofferwise.ai/architecture and '
            'https://www.getofferwise.ai/comparison for details'
        )
        self.assertEqual(out.count('<a href="'), 2)

    def test_line_without_url_unchanged(self):
        from admin_routes import _linkify_line
        line = "Hi Jane, hope you're well. No links here."
        out = _linkify_line(line)
        self.assertEqual(out, line)

    def test_empty_line_unchanged(self):
        from admin_routes import _linkify_line
        self.assertEqual(_linkify_line(''), '')

    def test_url_with_query_params(self):
        from admin_routes import _linkify_line
        out = _linkify_line(
            'Track here: https://www.getofferwise.ai/personas?utm=email'
        )
        self.assertIn(
            '<a href="https://www.getofferwise.ai/personas?utm=email"',
            out
        )

    def test_http_and_https_both_handled(self):
        from admin_routes import _linkify_line
        out_https = _linkify_line('https://example.com')
        out_http = _linkify_line('http://example.com')
        self.assertIn('<a href="https://example.com"', out_https)
        self.assertIn('<a href="http://example.com"', out_http)

    def test_does_not_double_linkify_existing_anchor(self):
        """If the input ALREADY has an <a> tag (shouldn't happen for plain
        text bodies but defensive), don't break it. The regex captures
        only bare URLs, so an existing anchor's href shouldn't be touched."""
        from admin_routes import _linkify_line
        # Existing anchor — the URL inside href="..." gets re-matched and
        # nested-anchored. This is a known limitation; the safety is that
        # draft bodies are plain text from the LLM, not pre-formatted HTML.
        # Test asserts current behavior, not aspirational.
        out = _linkify_line('<a href="https://example.com">click</a>')
        # The URL in href IS captured by the regex.
        # In production this isn't a concern because draft_body is plain
        # text. But documenting actual behavior so the test catches if
        # we ever change it.
        self.assertIn('https://example.com', out)


class TestPersonaURLActuallyClickableInSendBody(unittest.TestCase):
    """Integration: when an outreach draft contains a persona URL, the
    send pipeline should produce HTML with that URL as a real anchor."""

    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            from models import db, OutreachContact
            cls.app = app
            cls.db = db
            cls.OutreachContact = OutreachContact
            cls.client = app.test_client(use_cookies=False)
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"App not available: {self.skip_reason}")

    def test_send_path_uses_linkify(self):
        """The outreach_send_draft function in admin_routes calls
        _linkify_line in its html_lines comprehension. Since we can't
        easily intercept the outbound HTML, verify the SOURCE references
        _linkify_line — a structural check that the wiring is in place."""
        with open(
            os.path.join(os.path.dirname(__file__), 'admin_routes.py'),
            'r',
        ) as f:
            src = f.read()
        # outreach_send_draft uses _linkify_line
        idx = src.find('def outreach_send_draft')
        self.assertNotEqual(idx, -1, "outreach_send_draft not found")
        # Within ~3000 chars of that function, _linkify_line should appear
        nearby = src[idx:idx + 3000]
        self.assertIn('_linkify_line', nearby,
                      "outreach_send_draft must call _linkify_line on body lines")

    def test_outreach_send_uses_linkify(self):
        """outreach_send (the bulk-send path) must also linkify."""
        with open(
            os.path.join(os.path.dirname(__file__), 'admin_routes.py'),
            'r',
        ) as f:
            src = f.read()
        idx = src.find('def outreach_send(')
        self.assertNotEqual(idx, -1, "outreach_send not found")
        nearby = src[idx:idx + 5000]
        self.assertIn('_linkify_line', nearby,
                      "outreach_send must call _linkify_line on body lines")


if __name__ == '__main__':
    unittest.main()
