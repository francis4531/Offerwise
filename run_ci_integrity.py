#!/usr/bin/env python3
"""CI runner for integrity tests. Replaces inline Python in ci.yml."""
import os
import sys
import types

# Set CI environment
os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-ci')
os.environ.setdefault('DATABASE_URL', 'sqlite:///test_integrity.db')

# Stub optional heavy packages so the app imports cleanly in CI
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
        try:
            __import__(_pkg)
        except ImportError:
            sys.modules[_pkg] = _make_stub(_pkg)
            parts = _pkg.split('.')
            for i in range(1, len(parts)):
                parent = '.'.join(parts[:i])
                if parent not in sys.modules:
                    sys.modules[parent] = _make_stub(parent)

from app import app, db
from integrity_tests import IntegrityTestEngine

engine = IntegrityTestEngine(app=app, db=db)

with app.app_context():
    results = engine.run_all()

if not results['success']:
    failed_tests = [r for r in results['results'] if not r.get('passed')]
    print(f"\n❌ {results['summary']['failed']} integrity test(s) failed:\n")
    for f in failed_tests:
        print(f"  FAIL: {f['name']} — {f.get('details', '')[:120]}")
    sys.exit(1)

print(f"\n✅ All {results['summary']['total']} integrity tests passed "
      f"({results['summary']['duration_seconds']}s)")
