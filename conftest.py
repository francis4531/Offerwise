"""
Shared pytest fixtures for OfferWise test suite.
"""
import sys
import types
import pytest

# ── Stub heavy optional packages so tests run without them installed ──────────
def _make_stub(name):
    mod = types.ModuleType(name)
    class _S:
        def __init__(self, *a, **kw): pass
        def __call__(self, *a, **kw): return self
        def __getattr__(self, n): return _S()
        def __iter__(self): return iter([])
        def __bool__(self): return False
        def __len__(self): return 0
    mod.__getattr__ = lambda n: _S()
    mod.Anthropic = _S
    mod.APIError = Exception
    mod.RateLimitError = Exception
    mod.APIConnectionError = Exception
    return mod

for _pkg in ('anthropic', 'anthropic.types', 'google.cloud.vision',
             'google.cloud.vision_v1', 'paddleocr', 'pdf2image',
             'pytesseract', 'PyPDF2', 'pdfplumber',
             'pdfminer', 'pdfminer.high_level', 'pdfminer.layout',
             'google.analytics.data', 'google.analytics.data_v1beta',
             'google.oauth2', 'google.oauth2.service_account',
             'google_auth_oauthlib', 'google_auth_oauthlib.flow',
             'googleapiclient', 'googleapiclient.discovery',
             'googleads', 'openai', 'stripe', 'resend',
             'sentry_sdk', 'sentry_sdk.integrations',
             'sentry_sdk.integrations.flask',
             'apscheduler', 'apscheduler.schedulers',
             'apscheduler.schedulers.background',
             'flask_compress', 'flask_migrate',
             'alembic', 'alembic.config', 'alembic.script',
             'alembic.runtime', 'alembic.runtime.migration',
             'fcntl', 'crispy_tailwind'):
    if _pkg not in sys.modules:
        # Only stub if not already installed
        try:
            __import__(_pkg)
        except ImportError:
            sys.modules[_pkg] = _make_stub(_pkg)
            # Also register sub-packages
            parts = _pkg.split('.')
            for i in range(1, len(parts)):
                parent = '.'.join(parts[:i])
                if parent not in sys.modules:
                    sys.modules[parent] = _make_stub(parent)



def pytest_addoption(parser):
    parser.addoption(
        "--base-url",
        action="store",
        default=None,
        help="Base URL of a running OfferWise server for live integration tests",
    )


@pytest.fixture
def base_url(request):
    """Base URL for live server tests. Skip if not provided."""
    url = request.config.getoption("--base-url")
    if not url:
        pytest.skip("Live server tests require --base-url=<url>")
    return url.rstrip("/")


import os
os.environ.setdefault('DATABASE_URL', 'sqlite:///test_coverage.db')
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('FLASK_ENV', 'testing')


@pytest.fixture(scope='session')
def app():
    """Create a Flask app instance for tests that need app context."""
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    flask_app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///test_coverage.db'
    flask_app.config['WTF_CSRF_ENABLED'] = False
    return flask_app


@pytest.fixture
def app_ctx(app):
    """Push a Flask application context for tests that need it."""
    with app.app_context():
        yield app


# ── Per-module app isolation ───────────────────────────────────────────────────
# Each test *file* that calls _get_app() sets DATABASE_URL before importing.
# Without clearing sys.modules between files, the second file gets the
# first file's app (and DB). This autouse fixture clears the cached module
# after each test module so _get_app() re-imports fresh.

def pytest_runtest_setup(item):
    """Before each test: if this test's module sets DATABASE_URL, clear cached app."""
    pass  # clearing is handled at module tearDownClass level


def pytest_collection_modifyitems(session, config, items):
    """Group tests by file to reduce app re-initialisation overhead."""
    items.sort(key=lambda item: item.fspath)
