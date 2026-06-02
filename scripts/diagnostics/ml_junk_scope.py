#!/usr/bin/env python3
"""CLI wrapper around ml_junk_audit.audit_ml_training_data().

Usage (from /app on Render):
    python scripts/diagnostics/ml_junk_scope.py

Prints a human-readable report to stdout. For programmatic access, hit
GET /api/admin/ml-junk-scope or call audit_ml_training_data() directly.
"""
import os
import sys


def main() -> int:
    # Add app root to path so we can import from it regardless of cwd
    script_dir = os.path.dirname(os.path.abspath(__file__))
    app_root = os.path.abspath(os.path.join(script_dir, '..', '..'))
    if app_root not in sys.path:
        sys.path.insert(0, app_root)

    # OFFERWISE_TRAINING_SUBPROCESS=1 tells app.py not to start the scheduler.
    # We just want the Flask app context so we can hit the database.
    os.environ.setdefault('OFFERWISE_TRAINING_SUBPROCESS', '1')

    from app import app
    from ml_junk_audit import audit_ml_training_data

    with app.app_context():
        report = audit_ml_training_data()

    # Pretty-print to stdout
    print('═' * 78)
    print('  ML TRAINING DATA QUALITY AUDIT')
    print('═' * 78)
    print()
    print(f'  Total rows:  {report["total_rows"]:>8,}')
    print(f'  Junk rows:   {report["junk_rows"]:>8,}  ({report["junk_pct"]}%)')
    print(f'  Clean rows:  {report["clean_rows"]:>8,}')
    print()

    if not report['junk_rows']:
        print('  ✓ No junk detected. Training data is clean.')
        return 0

    print('─' * 78)
    print('  JUNK BY PATTERN (top 10)')
    print('─' * 78)
    for p in report['by_pattern'][:10]:
        print(f'  {p["count"]:>6,}  {p["name"]}')
        print(f'          example: {p["sample"][:90]}...' if len(p["sample"]) > 90 else f'          example: {p["sample"]}')

    print()
    print('─' * 78)
    print('  JUNK BY SOURCE')
    print('─' * 78)
    for s in report['by_source']:
        bar = '█' * int(min(40, s['junk_pct'] / 2.5))
        print(f'  {s["source"]:<20}  {s["junk_count"]:>6,} / {s["total_count"]:>6,}  ({s["junk_pct"]:>4.1f}%)  {bar}')

    print()
    print('─' * 78)
    print('  JUNK BY CATEGORY')
    print('─' * 78)
    for c in report['by_category']:
        bar = '█' * int(min(40, c['junk_pct'] / 2.5))
        print(f'  {c["category"]:<25}  {c["junk_count"]:>6,} / {c["total_count"]:>6,}  ({c["junk_pct"]:>4.1f}%)  {bar}')

    print()
    print('─' * 78)
    print('  RANDOM JUNK SAMPLES (sanity check — are these really junk?)')
    print('─' * 78)
    for s in report['sample_junk']:
        print(f'  [{s["category"]}/{s["severity"]}] ({s["source"]}):')
        print(f'    {s["text"][:200]}')
        print()

    print('═' * 78)
    print(f'  Remove these {report["junk_rows"]:,} rows → retrain → see if tests improve.')
    print(f'  Use the Diagnostics panel "Data Quality Audit" button to view this in-app.')
    print('═' * 78)

    return 0


if __name__ == '__main__':
    sys.exit(main())
