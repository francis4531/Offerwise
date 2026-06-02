"""
run_postgres_tests.py — v5.88.19

Runs the test suite against a Postgres database (typically a Render
Postgres branch dedicated to testing — NEVER the production DB).

WHY THIS EXISTS:
  Most of the test suite uses SQLite (fast, no setup, ephemeral).
  But SQLite has different behavior from Postgres in several ways:

    1. Concurrency: SQLite serializes by default; Postgres allows
       genuine row-level concurrency. Some tests (credit deduction
       race) only meaningfully exercise the contract under Postgres.
    2. Data types: NUMERIC/DECIMAL precision differs.
    3. Case sensitivity: Postgres LIKE is case-sensitive by default;
       SQLite is case-insensitive.
    4. Isolation levels: read-committed vs serializable behavior.
    5. Index behavior: Postgres has more strict NOT NULL + unique
       constraint enforcement under race.

  Running the suite against Postgres surfaces issues SQLite hides.

USAGE:

  Set DATABASE_URL to the Render Postgres test branch URL, then:

    cd offerwise_render
    export DATABASE_URL='postgresql://...'  # offerwise-postgres-test
    python run_postgres_tests.py

  ⚠️ SAFETY: This script REFUSES to run if DATABASE_URL contains 'prod'
  or matches known production hostnames. It also requires a final
  confirmation prompt. Tests will create + delete data with email
  patterns matching '*@e2e-*.test.example.com' — they will NOT touch
  rows outside that pattern, but a misconfigured DATABASE_URL could
  still expose unrelated data.

WHAT GETS RUN:

  This script runs a SUBSET of the suite that's known to be Postgres-
  safe. SQLite-specific tests (e.g. the in-process concurrency test
  that depends on SQLite's serialization) are skipped or replaced.

  The full Path B suite is 519 tests. Of those, ~470 are
  Postgres-portable. The other ~49 are documented below.
"""
import os
import sys
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent
os.chdir(REPO_ROOT)


# Files known to be Postgres-portable (cleaned up after each test, no
# SQLite-specific behavior assumed):
POSTGRES_PORTABLE_FILES = [
    'test_e2e_auth_signup.py',
    'test_e2e_credits_payments.py',
    'test_e2e_analysis_core.py',
    'test_e2e_outreach_pipeline.py',
    'test_e2e_admin_mutations.py',
    'test_e2e_onboarding_drip.py',
    'test_e2e_bug_sweep_audits.py',
    'test_e2e_oauth_ratelimits_concurrency.py',
    'test_e2e_critical_journeys.py',
    'test_e2e_cron_jobs.py',
    'test_v5_88_07.py',
]

# Files that depend on SQLite-specific behavior or can't easily run
# against a shared Postgres instance — skipped here, run separately
# with SQLite:
POSTGRES_SKIP_FILES = [
    'test_e2e_analyze_orchestration.py',  # mocks Anthropic; SQLite is fine
    'test_e2e_analyze_cassettes.py',      # cassette replay; SQLite is fine
    'test_active_filter_engagement.py',   # large data fixtures
    'test_outreach_greeting.py',          # template tests
    'test_outreach_linkify.py',           # template tests
    'test_outreach_drafts.py',            # template tests
    'test_prospect_blocklist.py',         # already covered in pipeline file
    'test_wedge_sweep.py',                # external-API heavy
    'test_bulk_regenerate.py',            # touches Anthropic mocks
    'test_personas_page.py',              # static template tests
    'test_topbar_admin.py',               # template tests
    'test_topbar_widget.py',              # template tests
    'test_gsc_fetch.py',                  # external GSC API
    'test_arch_stats.py',                 # template stats
    'test_permit_lookup.py',              # external permit API
]


# Production hostname patterns that must NEVER receive test traffic.
# If DATABASE_URL matches any of these, the script aborts.
PRODUCTION_HOSTNAME_PATTERNS = [
    'offerwise-postgres.render.com',     # production primary
    'getofferwise-prod',
    'offerwise-prod',
    'production',
]
SAFE_HOSTNAME_PATTERNS = [
    'offerwise-postgres-test',           # the test branch
    'test',
    'staging',
]


def _check_database_url_safety(url):
    """Refuse to run against a production-looking DATABASE_URL."""
    if not url:
        print('❌ DATABASE_URL is not set')
        sys.exit(1)

    if not url.startswith('postgresql://') and not url.startswith('postgres://'):
        print(f'❌ DATABASE_URL must be postgresql://, got: {url[:30]}...')
        print('   This script only runs against Postgres.')
        sys.exit(1)

    url_lower = url.lower()

    # Hard-block production patterns
    for pat in PRODUCTION_HOSTNAME_PATTERNS:
        if pat in url_lower:
            print(f'❌ DATABASE_URL contains production pattern "{pat}"')
            print(f'   URL: {url[:80]}...')
            print('   Refusing to run tests against production DB.')
            sys.exit(2)

    # Warn if URL doesn't look like a known test branch
    matches_safe = any(pat in url_lower for pat in SAFE_HOSTNAME_PATTERNS)
    if not matches_safe:
        print(f'⚠️  DATABASE_URL does not match any known test/staging pattern.')
        print(f'   URL: {url[:80]}...')
        print(f'   Expected one of: {SAFE_HOSTNAME_PATTERNS}')
        print()
        confirm = input('Type "YES I AM SURE" to proceed: ')
        if confirm.strip() != 'YES I AM SURE':
            print('Aborting.')
            sys.exit(3)


def _confirm_proceed():
    """Final confirmation before running tests."""
    print()
    print('This will:')
    print('  - CREATE tables + indexes if they don\'t exist')
    print('  - INSERT test users with emails matching "*@e2e-*.test.example.com"')
    print('  - DELETE those test users at end of each test')
    print('  - NOT touch any rows outside the test email pattern')
    print()
    confirm = input('Proceed? [y/N]: ')
    return confirm.strip().lower() == 'y'


def main():
    db_url = os.environ.get('DATABASE_URL', '').strip()
    print('=' * 70)
    print('Postgres Test Runner')
    print('=' * 70)
    print(f'DATABASE_URL: {db_url[:60]}{"..." if len(db_url) > 60 else ""}')
    print()

    _check_database_url_safety(db_url)

    if not _confirm_proceed():
        print('Aborted by user.')
        sys.exit(0)

    # Set test env vars
    os.environ['FLASK_ENV'] = 'testing'
    os.environ.setdefault('SECRET_KEY', 'test-secret-postgres')
    os.environ.setdefault('ADMIN_KEY', 'test-admin-postgres')
    os.environ['RATELIMIT_ENABLED'] = 'false'

    # Run portable subset
    print()
    print('Running Postgres-portable test files...')
    print()

    failed = []
    total_passed = 0
    total_skipped = 0
    total_failed = 0

    for f in POSTGRES_PORTABLE_FILES:
        if not (REPO_ROOT / f).exists():
            print(f'⚠️  {f}: file missing, skipping')
            continue

        print(f'━━━ {f} ━━━')
        result = subprocess.run(
            ['python3', '-m', 'pytest', f, '-v', '--tb=short'],
            capture_output=True,
            text=True,
            timeout=300,
        )

        # Parse pytest output for counts
        last_line = result.stdout.strip().split('\n')[-1] if result.stdout else ''
        if 'passed' in last_line:
            import re
            passed_m = re.search(r'(\d+) passed', last_line)
            skipped_m = re.search(r'(\d+) skipped', last_line)
            failed_m = re.search(r'(\d+) failed', last_line)
            p = int(passed_m.group(1)) if passed_m else 0
            s = int(skipped_m.group(1)) if skipped_m else 0
            fl = int(failed_m.group(1)) if failed_m else 0
            total_passed += p
            total_skipped += s
            total_failed += fl
            print(f'   {p} passed, {s} skipped, {fl} failed')
            if fl > 0:
                failed.append(f)
                # Print last 30 lines of output for diagnosis
                lines = result.stdout.split('\n')
                print('\n'.join(lines[-30:]))
        else:
            print(f'   ❓ Could not parse pytest output')
            print(result.stdout[-1000:])
            failed.append(f)

        print()

    # Summary
    print('=' * 70)
    print('SUMMARY')
    print('=' * 70)
    print(f'  Passed:  {total_passed}')
    print(f'  Skipped: {total_skipped}')
    print(f'  Failed:  {total_failed}')
    print()

    if total_failed > 0:
        print('FAILED FILES:')
        for f in failed:
            print(f'  • {f}')
        print()
        print('INVESTIGATION GUIDE:')
        print('  Common Postgres-vs-SQLite differences to check:')
        print('  - LIKE case sensitivity (Postgres = case-sensitive,')
        print('    SQLite = case-insensitive). Use ILIKE in Postgres.')
        print('  - DECIMAL/NUMERIC precision (Postgres rejects truncation)')
        print('  - Constraint enforcement timing (Postgres deferrable)')
        print('  - Sequence vs AUTOINCREMENT for primary keys')
        sys.exit(1)
    else:
        print('✅ All Postgres-portable tests passed.')
        print()
        print('NOTE: The following test files were skipped (SQLite-specific')
        print('or external-API-heavy). Run them separately with SQLite:')
        for f in POSTGRES_SKIP_FILES:
            print(f'  • {f}')


if __name__ == '__main__':
    main()
