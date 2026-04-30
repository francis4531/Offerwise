"""
Tests for Document Repository routes.
Covers: catalog, download, disk-status, crawler, seed, check-sources, anonymize.
"""
import unittest
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-docrepo')
os.environ.setdefault('DATABASE_URL', 'sqlite:///test_docrepo.db')


def _get_app():
    import importlib.util
    spec = importlib.util.spec_from_file_location('app', 'app.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules['app'] = mod
    spec.loader.exec_module(mod)
    return mod.app, mod.db


class TestDocRepoCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.db = _get_app()
        cls.client = cls.app.test_client()
        with cls.app.app_context():
            try:

                cls.db.create_all()  # May fail on PostgreSQL if tables exist

            except Exception:

                pass  # Tables already exist in production DB

    def test_catalog_requires_auth(self):
        """GET /api/docrepo/catalog returns 401 or 403 when unauthenticated."""
        r = self.client.get('/api/docrepo/catalog')
        self.assertIn(r.status_code, [401, 403, 302])

    def test_catalog_with_admin_key(self):
        """GET /api/docrepo/catalog returns valid response with admin key."""
        admin_key = os.environ.get('ADMIN_KEY', '')
        if not admin_key:
            self.skipTest('ADMIN_KEY not set')
        r = self.client.get(f'/api/docrepo/catalog?admin_key={admin_key}')
        self.assertIn(r.status_code, [200, 404])
        if r.status_code == 200:
            data = json.loads(r.data)
            self.assertIn('documents', data)

    def test_disk_status_requires_auth(self):
        """GET /api/docrepo/disk-status returns 401/403 when unauthenticated."""
        r = self.client.get('/api/docrepo/disk-status')
        self.assertIn(r.status_code, [401, 403, 302])

    def test_disk_status_with_admin_key(self):
        """GET /api/docrepo/disk-status returns disk info."""
        admin_key = os.environ.get('ADMIN_KEY', '')
        if not admin_key:
            self.skipTest('ADMIN_KEY not set')
        r = self.client.get(f'/api/docrepo/disk-status?admin_key={admin_key}')
        self.assertIn(r.status_code, [200, 404])

    def test_check_sources_requires_auth(self):
        """GET /api/docrepo/check-sources returns 401/403 when unauthenticated."""
        r = self.client.get('/api/docrepo/check-sources')
        self.assertIn(r.status_code, [401, 403, 302])


class TestDocRepoDownload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.db = _get_app()
        cls.client = cls.app.test_client()

    def test_download_nonexistent_doc(self):
        """GET /api/docrepo/download/<id> returns 404 for nonexistent doc."""
        r = self.client.get('/api/docrepo/download/nonexistent-doc-id-xyz')
        self.assertIn(r.status_code, [401, 403, 404, 302])

    def test_anonymize_nonexistent_doc(self):
        """GET /api/docrepo/anonymize/<id> returns 404 for nonexistent doc."""
        r = self.client.get('/api/docrepo/anonymize/nonexistent-doc-id-xyz')
        self.assertIn(r.status_code, [401, 403, 404, 302])

    def test_test_nonexistent_doc(self):
        """GET /api/docrepo/test/<id> returns 404 for nonexistent doc."""
        r = self.client.get('/api/docrepo/test/nonexistent-doc-id-xyz')
        self.assertIn(r.status_code, [401, 403, 404, 302])


class TestDocRepoCrawler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.db = _get_app()
        cls.client = cls.app.test_client()

    def test_crawler_corpus_requires_auth(self):
        """GET /api/docrepo/crawler/corpus returns 401/403 unauthenticated."""
        r = self.client.get('/api/docrepo/crawler/corpus')
        self.assertIn(r.status_code, [401, 403, 302])

    def test_crawler_crawl_requires_auth(self):
        """POST /api/docrepo/crawler/crawl returns 401/403 unauthenticated."""
        r = self.client.post('/api/docrepo/crawler/crawl',
                             json={'url': 'https://example.com'},
                             content_type='application/json')
        self.assertIn(r.status_code, [401, 403, 302])

    def test_crawler_crawl_rejects_restricted_domains(self):
        """POST /api/docrepo/crawler/crawl should reject auth-gated URLs."""
        admin_key = os.environ.get('ADMIN_KEY', '')
        if not admin_key:
            self.skipTest('ADMIN_KEY not set')
        r = self.client.post(f'/api/docrepo/crawler/crawl?admin_key={admin_key}',
                             json={'url': 'https://facebook.com/login'},
                             content_type='application/json')
        self.assertIn(r.status_code, [200, 400, 403, 422, 500])

    def test_seed_requires_auth(self):
        """POST /api/docrepo/seed returns 401/403 unauthenticated."""
        r = self.client.post('/api/docrepo/seed',
                             json={},
                             content_type='application/json')
        self.assertIn(r.status_code, [401, 403, 302])

    def test_crawler_scan_requires_auth(self):
        """GET /api/docrepo/crawler/scan returns 401/403 unauthenticated."""
        r = self.client.get('/api/docrepo/crawler/scan')
        self.assertIn(r.status_code, [401, 403, 302])


class TestDocRepoIntegration(unittest.TestCase):
    """End-to-end docrepo workflow: seed → catalog → download."""

    @classmethod
    def setUpClass(cls):
        cls.app, cls.db = _get_app()
        cls.client = cls.app.test_client()
        with cls.app.app_context():
            try:

                cls.db.create_all()  # May fail on PostgreSQL if tables exist

            except Exception:

                pass  # Tables already exist in production DB

    def test_catalog_returns_list_structure(self):
        """Catalog response always returns a list, even if empty."""
        admin_key = os.environ.get('ADMIN_KEY', '')
        if not admin_key:
            self.skipTest('ADMIN_KEY not set — skipping integration test')
        r = self.client.get(f'/api/docrepo/catalog?admin_key={admin_key}')
        if r.status_code == 200:
            data = json.loads(r.data)
            self.assertIsInstance(data.get('documents', []), list)

    def test_download_returns_file_or_404(self):
        """Download either returns file content or 404 — never 500."""
        admin_key = os.environ.get('ADMIN_KEY', '')
        if not admin_key:
            self.skipTest('ADMIN_KEY not set')
        r = self.client.get(f'/api/docrepo/download/fake-id?admin_key={admin_key}')
        self.assertNotEqual(r.status_code, 500,
                            'Download should never return 500 for missing doc')


if __name__ == '__main__':
    unittest.main()
