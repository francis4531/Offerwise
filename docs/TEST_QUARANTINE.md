# Test quarantine

These test files exist in the repo but are excluded from CI via `collect_ignore`
in `conftest.py`. They currently fail, error, or contaminate other tests, so
running them in CI would redden the gate. This is the repair backlog: fix the
cause, delete the file from `collect_ignore`, and it rejoins CI automatically
(the suite is auto-discovered).

History:
- v5.89.193 moved CI from an opt-in list of ~18 files to auto-discovery
  (`pytest -m "not e2e"` plus `pytest -m e2e`). 66 orphaned files were wired in;
  16 were held back.
- v5.89.194 repaired and re-admitted 4 of those 16 (test_advanced — 0 tests;
  test_server — skips cleanly; test_pdf_parser — now skips when its sample PDF is
  absent; test_agentic_monitor — stale job-count 5 corrected to the 6 the code
  registers). 12 remain below.

## Needs real Postgres (SQLite can't model the locking/concurrency under test)
- test_e2e_oauth_concurrency.py
- test_e2e_oauth_ratelimit_races.py
- test_e2e_oauth_ratelimits_concurrency.py
- test_e2e_oauth_subcancel_concurrency.py
Fix: run against a Postgres service container (see run_postgres_tests.py), or put
them behind a `postgres` marker and a dedicated CI job with a Postgres service.

## Needs external fixtures or secrets
- test_e2e_credits_payments.py   (Stripe; needs test keys or a stripe mock)
- test_e2e_analyze_cassettes.py  (recorded HTTP cassettes missing/stale)

## Process-isolation conflict (needs its own pytest process)
- test_all_60_workflows.py
  Sets ANTHROPIC_API_KEY at import; that flips the no-key truth-check path in
  test_adversarial_pdfs and fails 3 of its tests. 290 of its own tests pass.
  Fix: run it in an isolated step (same pattern as the e2e split) so its import
  side effects don't leak into the shared process.

## Behaviour drift (assertions against changed behaviour — real repair needed)
- test_forum_scanner.py        (6 failing; Reddit fetch/draft behaviour changed)
- test_personas_page.py        (/thesis is now access-gated; test sees the gate)
- test_coverage_final.py       (16 failing)
- test_coverage_gaps.py        (5 failing)
- test_e2e_onboarding_drip.py  (3 failing)
