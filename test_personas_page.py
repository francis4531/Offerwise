"""
test_personas_page.py — v5.87.96

Tests the /personas route + page integration with /thesis.
"""
import os
import unittest

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-personas'
os.environ['DATABASE_URL'] = 'sqlite:///test_personas.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-personas')

# Clear stale db file
import os as _os
_db_path = 'test_personas.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)


class TestPersonasPage(unittest.TestCase):
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

    _cookie = None

    def _get(self, path):
        """Fetch as an APPROVED investor (v5.89.345: /personas and /pitch sit behind
        the same access gate as /thesis and /architecture)."""
        if self._cookie is None:
            from app import db
            from access_gate import create_request, approve_request, consume_magic_token, COOKIE_NAME
            with self.app.app_context():
                db.create_all()
                row, _ = create_request(db=db, name='T', email='t@example.com', company='', role='',
                                        reason='', page_requested='/thesis', ip_address='', user_agent='')
                approve_request(db, row.id)
                type(self)._cookie = (COOKIE_NAME, consume_magic_token(db, row.magic_token).cookie_token)
        return self.client.get(path, headers={'Cookie': f'{self._cookie[0]}={self._cookie[1]}'})

    def test_personas_is_access_gated(self):
        """v5.89.345: anonymous visitors see the access-request form, not the page."""
        r = self.client.get('/personas')
        self.assertIn(b'Request access', r.data)
        self.assertNotIn(b'persona', r.data.lower()[:4000])

    def setUp(self):
        if not self.available:
            self.skipTest(f"App not available: {self.skip_reason}")

    def test_personas_route_returns_200(self):
        r = self._get('/personas')
        self.assertEqual(r.status_code, 200)

    def test_personas_html_alias_returns_200(self):
        r = self._get('/personas.html')
        self.assertEqual(r.status_code, 200)

    def test_personas_page_is_noindexed(self):
        """Like /thesis, the personas page is internal/investor-facing
        and must not be indexed by search engines."""
        r = self._get('/personas')
        self.assertIn(b'noindex', r.data)
        self.assertIn(b'nofollow', r.data)

    def test_personas_page_contains_all_eight_personas(self):
        r = self._get('/personas')
        body = r.data.decode('utf-8')
        # Each persona must appear by name. Text in the page uses both forms.
        for persona in [
            'Homebuyer',
            'Home Inspector',
            "Buyer's Agent",
            'Contractor',
            'Lender',
            'Title Company',
            'Insurance Underwriter',
            'Appraiser',
        ]:
            self.assertIn(persona, body, f"Persona '{persona}' missing from page")

    def test_personas_page_lists_three_trademarks(self):
        r = self._get('/personas')
        body = r.data.decode('utf-8')
        self.assertIn('OfferScore', body)
        self.assertIn('Property Risk DNA', body)
        self.assertIn('Seller Transparency Report', body)

    def test_personas_page_links_back_to_thesis(self):
        r = self._get('/personas')
        body = r.data.decode('utf-8')
        self.assertIn('/thesis', body)

    def test_thesis_page_links_to_personas(self):
        """Reciprocal link — when reading /thesis, user can jump to /personas."""
        r = self._get('/thesis')
        body = r.data.decode('utf-8')
        self.assertIn('/personas', body)

    def test_personas_page_flags_thesis_divergence(self):
        """The thesis page says 'Four personas' but we now have 8.
        The personas page should explicitly call this out for honest reading."""
        r = self._get('/personas')
        body = r.data.decode('utf-8')
        # The callout block exists
        self.assertIn('Thesis page divergence', body)
        # Mentions the four-persona historical framing
        self.assertIn('Four personas', body)


class TestPersonasPageStructure(unittest.TestCase):
    """Static checks on the file itself (no Flask client needed)."""

    def test_file_exists_in_internal_docs(self):
        path = os.path.join(
            os.path.dirname(__file__), 'docs', 'internal', 'personas.html'
        )
        self.assertTrue(os.path.exists(path))

    def test_file_has_route_handler(self):
        with open(
            os.path.join(os.path.dirname(__file__), 'app.py'), 'r'
        ) as f:
            app_py = f.read()
        self.assertIn("@app.route('/personas')", app_py)
        self.assertIn("@app.route('/personas.html')", app_py)


if __name__ == '__main__':
    unittest.main()
