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
(none currently)

Recovered:
- test_e2e_credits_payments.py — RE-ADMITTED v5.89.205. It never needed Stripe
  keys; it already mocks Stripe via patch('app.stripe...'). It only failed when
  the real stripe package was absent (conftest's stub returns a non-subscriptable
  object for Webhook.construct_event). stripe is a hard dependency (requirements
  pins stripe==7.9.0), so CI has it and all 36 tests run for real; the webhook
  class now skips gracefully if only the stub is present. Also fixed a cleanup bug
  that removed ./test_e2e_pay.db while Flask wrote instance/test_e2e_pay.db, which
  leaked an Inspector row and tripped a UNIQUE constraint on re-run.
- test_e2e_analyze_cassettes.py — RE-ADMITTED v5.89.206. The cassettes were NOT
  missing/stale: all 5 are present and valid, and all 8 tests pass with the real
  deps installed. It only failed locally because conftest stubs PyPDF2 (so the
  doc-extraction helper returned empty text and /api/analyze correctly downgraded
  to address_only) and because vcrpy wasn't installed. Both vcrpy (>=5.1.0) and
  PyPDF2 (==3.0.1) are in requirements.txt, so CI runs the full replays for real;
  the replay tests now skip gracefully when those deps are only conftest stubs.
  Re-record (record_cassettes.py, needs ANTHROPIC_API_KEY) after prompt/orchestrator
  changes, per the file's own lifecycle note.

## Behaviour drift (assertions against changed behaviour — real repair needed)
- test_forum_scanner.py        (6 failing; Reddit fetch/draft behaviour changed)
- test_personas_page.py        (/thesis is now access-gated; test sees the gate)
- test_coverage_final.py       (16 failing)
- test_coverage_gaps.py        (5 failing)
- test_e2e_onboarding_drip.py  (3 failing)
