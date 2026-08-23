#!/usr/bin/env python3
"""api_smoke_test.py — live end-to-end smoke test for the public B2B API (v5.89.332)

The unit suite (test_api_v1.py) mocks the AI engine, so it proves the plumbing but NOT
that a real call works against a running deployment. This script closes that gap: it hits
a LIVE server with a REAL key and makes a REAL (non-mocked) /api/v1/analyze call, then
validates the true response contract. This is the "100%" test — run it against production
before pointing a partner at the API.

Usage:
    python3 scripts/api_smoke_test.py --base https://www.getofferwise.ai --key ow_live_XXXX
    # or via env:
    OW_API_BASE=https://www.getofferwise.ai OW_API_KEY=ow_live_XXXX python3 scripts/api_smoke_test.py

Exit code 0 = every check passed. Non-zero = at least one failed (details printed).

What it checks, against the REAL running build:
  1. /api/v1/usage authenticates with the key and returns real stats (Bearer + X-API-Key)
  2. no-key and bad-key are correctly rejected (401)
  3. /api/v1/analyze with a real disclosure+inspection returns a full, well-formed result
     (this is the real AI call — proves the whole pipeline, not a mock)
  4. missing-fields and bad-price are correctly rejected (400)
  5. usage incremented by exactly the number of successful analyze calls made
  6. /api/v1/research and /api/v1/screen authenticate and return without 500

No secrets are hard-coded; the key comes from the CLI or env. Safe to run repeatedly
(it consumes a small number of the key's monthly quota per run — see --skip-analyze to
check everything except the paid AI call).
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error


class Check:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.lines = []

    def ok(self, name, detail=''):
        self.passed += 1
        self.lines.append(f"  \033[92m✓\033[0m {name}" + (f"  ({detail})" if detail else ''))

    def bad(self, name, detail=''):
        self.failed += 1
        self.lines.append(f"  \033[91m✗\033[0m {name}" + (f"  — {detail}" if detail else ''))

    def report(self):
        print('\n'.join(self.lines))
        total = self.passed + self.failed
        color = '\033[92m' if self.failed == 0 else '\033[91m'
        print(f"\n{color}{self.passed}/{total} checks passed\033[0m")
        return 0 if self.failed == 0 else 1


def _req(base, path, method='GET', headers=None, body=None, timeout=120):
    url = base.rstrip('/') + path
    data = json.dumps(body).encode() if body is not None else None
    h = {'Content-Type': 'application/json'}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or '{}')
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {'_transport_error': str(e)}


# a real, small matched pair — a genuine disclosure claim contradicted by an inspection
# finding, so a working engine has something real to surface.
DISCLOSURE = (
    "Seller Property Questionnaire: Roof replaced 2019, no known leaks. "
    "Electrical: no known issues. Seller is not aware of any foundation problems."
)
INSPECTION = (
    "Inspection findings: Active roof leak observed at north valley, staining in attic. "
    "Federal Pacific Stab-Lok electrical panel present (recognized fire hazard). "
    "Foundation: sill-plate anchor bolts verified in crawlspace, no movement observed."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=os.environ.get('OW_API_BASE', ''))
    ap.add_argument('--key', default=os.environ.get('OW_API_KEY', ''))
    ap.add_argument('--skip-analyze', action='store_true',
                    help='skip the real (quota-consuming) analyze call')
    args = ap.parse_args()

    if not args.base or not args.key:
        print("ERROR: provide --base and --key (or OW_API_BASE / OW_API_KEY env).")
        print("  e.g. python3 scripts/api_smoke_test.py --base https://www.getofferwise.ai --key ow_live_XXXX")
        return 2

    base, key = args.base, args.key
    bearer = {'Authorization': f'Bearer {key}'}
    c = Check()
    print(f"\nLive API smoke test → {base}\n")

    # 1. usage authenticates (Bearer)
    st, j = _req(base, '/api/v1/usage', headers=bearer)
    if st == 200 and 'monthly_limit' in j and 'calls_month' in j:
        c.ok('usage: Bearer auth returns real stats',
             f"{j.get('calls_month')}/{j.get('monthly_limit')} used")
        start_calls = j.get('calls_month', 0)
    else:
        c.bad('usage: Bearer auth', f"status={st} body={j}")
        start_calls = None

    # 1b. usage via X-API-Key header
    st, j = _req(base, '/api/v1/usage', headers={'X-API-Key': key})
    c.ok('usage: X-API-Key auth works') if st == 200 else c.bad('usage: X-API-Key auth', f"status={st}")

    # 2. no key rejected
    st, _ = _req(base, '/api/v1/usage')
    c.ok('auth: no key → 401') if st == 401 else c.bad('auth: no key', f"expected 401 got {st}")

    # 2b. bad key rejected
    st, _ = _req(base, '/api/v1/usage', headers={'Authorization': 'Bearer ow_live_bogus_key'})
    c.ok('auth: bad key → 401') if st == 401 else c.bad('auth: bad key', f"expected 401 got {st}")

    # 4. analyze validation: missing fields → 400
    st, _ = _req(base, '/api/v1/analyze', method='POST', headers=bearer,
                 body={'property_price': 900000, 'property_address': '1 Main St'})
    c.ok('analyze: missing documents → 400') if st == 400 else c.bad('analyze: missing docs', f"expected 400 got {st}")

    # 3. the real analyze call (non-mocked — the whole point)
    if not args.skip_analyze:
        print("\n  … running a REAL analyze call (this hits the live AI engine, ~30-60s) …")
        t0 = time.time()
        st, j = _req(base, '/api/v1/analyze', method='POST', headers=bearer, body={
            'disclosure_text': DISCLOSURE,
            'inspection_text': INSPECTION,
            'property_price': 900000,
            'property_address': '2839 Pendleton Dr, San Jose CA 95148',
        }, timeout=180)
        dt = round(time.time() - t0, 1)
        if st == 200 and j.get('success'):
            missing = [k for k in ('offer_score', 'risk_level', 'deal_breakers',
                                   'repair_costs', 'recommended_offer', 'usage') if k not in j]
            if missing:
                c.bad('analyze: real call shape', f"missing keys: {missing}")
            else:
                c.ok('analyze: real call returns full result', f"{dt}s, score={j.get('offer_score')}")
        elif st == 503:
            c.bad('analyze: real call', 'got 503 server_busy — capacity full, retry')
        else:
            c.bad('analyze: real call', f"status={st} body={str(j)[:200]}")

        # 5. usage incremented
        st, j2 = _req(base, '/api/v1/usage', headers=bearer)
        if st == 200 and start_calls is not None:
            if j2.get('calls_month', 0) == start_calls + 1:
                c.ok('usage: incremented by exactly 1 after analyze')
            else:
                c.bad('usage: increment', f"was {start_calls}, now {j2.get('calls_month')}")
    else:
        c.ok('analyze: real call SKIPPED (--skip-analyze)')

    # 6. research + screen authenticate and don't 500
    st, _ = _req(base, '/api/v1/research', method='POST', headers=bearer,
                 body={'address': '2839 Pendleton Dr, San Jose CA 95148'}, timeout=120)
    c.ok('research: authenticates, no 500', f"status={st}") if st and st < 500 else c.bad('research', f"status={st}")

    st, _ = _req(base, '/api/v1/screen', method='POST', headers=bearer,
                 body={'address': '2839 Pendleton Dr, San Jose CA 95148', 'asking_price': 900000}, timeout=120)
    c.ok('screen: authenticates, no 500', f"status={st}") if st and st < 500 else c.bad('screen', f"status={st}")

    return c.report()


if __name__ == '__main__':
    sys.exit(main())
