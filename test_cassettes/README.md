# Cassette tests for `/api/analyze`

## What this is

VCR-style cassette tests that exercise the full `/api/analyze` endpoint
against recorded responses from Anthropic + RentCast + Hunter + etc.
Cassettes capture real upstream API responses once; tests then replay
those responses without making any real API calls.

This catches:
- **Prompt drift**: if `intelligence_engine.py` or `offerwise_intelligence.py`
  changes a prompt template in a way that breaks parsing, the cassette
  returns the OLD response — the parser fails — the test fails.
- **Response shape mismatch**: orchestrator expects a field that's
  no longer in the model output → fail loudly in CI before production.
- **Persistence regression**: Property + Analysis row creation breaks → fail.

This does NOT catch:
- **Real upstream API changes**: Anthropic releases a new model with
  different output, but the cassette holds the old response. Test passes,
  production breaks. Mitigation: re-record cassettes quarterly.

## Recording cassettes (you, locally, with real API keys)

You'll do this once now, and then re-record:
- Anytime `intelligence_engine.py` changes
- Anytime a prompt template changes
- Quarterly even if nothing else triggers it

```bash
cd offerwise_render
export ANTHROPIC_API_KEY='sk-ant-...'
# Optional — if you have these keys, recording also captures their
# responses so the orchestrator runs end-to-end:
# export RENTCAST_API_KEY='...'
# export HUNTER_API_KEY='...'

python test_cassettes/record_cassettes.py
```

The script will:
1. Spin up Flask with a SQLite test DB
2. Create a recorder user with 100 credits
3. Run `/api/analyze` for 3 scenarios:
   - Address-only (no documents) — `analyze_address_only.yaml`
   - Clean disclosure PDF — `analyze_clean_disclosure.yaml`
   - Nightmare disclosure PDF (red flags) — `analyze_nightmare_disclosure.yaml`
4. Save cassettes to `test_cassettes/cassettes/*.yaml`
5. Sanitize: strip `ANTHROPIC_API_KEY`, `RENTCAST_API_KEY`, etc. from
   headers and query params before write

After recording:
```bash
# Verify nothing leaked through
grep -ri "sk-ant" test_cassettes/cassettes/  # should return nothing
grep -ri "ANTHROPIC_API_KEY" test_cassettes/cassettes/  # should return nothing

# Verify replay works
pytest test_e2e_analyze_cassettes.py -v

# Commit
git add test_cassettes/cassettes/*.yaml
git commit -m "Record /api/analyze cassettes (YYYY-MM-DD)"
```

## Replaying (CI, your test admin page, anyone)

No API keys needed. Just run:
```bash
pytest test_e2e_analyze_cassettes.py
```

Tests auto-skip if cassettes are missing — they'll print:
> Cassette analyze_address_only.yaml not yet recorded. Run: ...

This means CI doesn't block on missing cassettes (so the suite stays
green during the gap between recordings), but you'll see clear messages
about which cassettes need recording.

## What's in a cassette

Each `.yaml` file is a list of HTTP request/response pairs. Roughly:
```yaml
interactions:
- request:
    method: POST
    uri: https://api.anthropic.com/v1/messages
    body: '{"model":"claude-...","messages":[...]}'
    headers:
      authorization: <REDACTED>
  response:
    status: {code: 200, message: OK}
    body: {string: '{"id":"msg_...","content":[...]}}'
```

Cassettes are matched by `(method, scheme, host, path)` — NOT by query
string or body. So the same cassette plays for any request to that
endpoint, even if the prompt content drifts slightly. This is intentional:
a small prompt edit shouldn't invalidate the entire cassette.

## When cassettes go stale

Symptoms:
- Cassette test fails with parse errors → orchestrator output shape
  changed; re-record
- Test fails with "expected risk >= 40, got 25" on the nightmare
  cassette → either model behavior shifted (re-record) or the risk
  scoring weakened (investigate code change)
- All cassettes still pass but production logs show parse errors →
  upstream API changed but cassettes captured old version. Re-record
  immediately.

## Files

- `test_cassettes/record_cassettes.py` — Run this to record
- `test_cassettes/cassettes/*.yaml` — The recorded cassettes (commit these)
- `test_e2e_analyze_cassettes.py` — Replay tests (in repo root)
- `requirements.txt` — `vcrpy>=5.1.0`
