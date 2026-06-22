# Test quarantine

These test files exist in the repo but are excluded from normal CI collection via
`collect_ignore` in `conftest.py`. They currently fail or error, so running them
would redden the gate. This is the repair backlog: fix the cause, remove the file
from `collect_ignore`, and it rejoins CI automatically (the suite is
auto-discovered).

History:
- v5.89.193 moved CI from an opt-in list of ~18 files to auto-discovery
  (`pytest -m "not e2e"` plus `pytest -m e2e`); 66 orphaned files were wired in.
- v5.89.194 repaired and re-admitted 4 (advanced, server, pdf_parser, agentic_monitor).
- v5.89.195 recovered test_all_60_workflows (290 tests) by running it in its own
  isolated CI step — it sets ANTHROPIC_API_KEY at import, which contaminates
  test_adversarial_pdfs, so it is gated out of normal collection (added to
  collect_ignore unless PYTEST_ISOLATED=1) and run alone. It is NOT quarantined.

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

## Behaviour drift (assertions against changed behaviour — real repair needed)
- test_forum_scanner.py        (6 failing; Reddit fetch/draft behaviour changed)
- test_personas_page.py        (/thesis is now access-gated; test sees the gate)
- test_coverage_final.py       (16 failing)
- test_coverage_gaps.py        (5 failing)
- test_e2e_onboarding_drip.py  (3 failing)
