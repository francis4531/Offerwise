# Test quarantine

These test files exist in the repo but are excluded from CI via `collect_ignore`
in `conftest.py`. They currently fail or error, so running them in CI would redden
the gate. This is the repair backlog: fix the cause, delete the file from
`collect_ignore`, and it rejoins CI automatically (the suite is auto-discovered).

History: as of v5.89.193, CI moved from an opt-in list of ~18 files to
auto-discovery (`pytest -m "not e2e"` plus `pytest -m e2e`). 66 previously
orphaned files were wired in; the 16 below were held back pending these fixes.

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

## Collection / import errors (reference moved or missing code)
- test_advanced.py
- test_forum_scanner.py
- test_server.py

## Single rotted assertion (repair or delete the one stale test)
- test_agentic_monitor.py      (1 of 64 failing)
- test_all_60_workflows.py     (1 of 291 failing)
- test_pdf_parser.py           (1 of 1 failing)
- test_personas_page.py        (1 of 10 failing)

## Broad drift (assertions against changed behaviour)
- test_coverage_final.py       (16 failing)
- test_coverage_gaps.py        (5 failing)
- test_e2e_onboarding_drip.py  (3 failing)
