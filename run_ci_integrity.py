#!/usr/bin/env python3
"""CI runner for integrity tests. Replaces inline Python in ci.yml."""
import os
import sys

# Set CI environment
os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-ci')
os.environ.setdefault('DATABASE_URL', 'sqlite:///test_integrity.db')

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
