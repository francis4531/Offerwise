"""
test_content_gen_null_safety.py — v5.89.301

Regression test for the Sentry issue "TypeError: 'NoneType' object is not subscriptable"
in app._content_gen_job (31 events / 22 days).

Causal chain this locks down:
  1. Address-only analyses persist result_json with EXPLICIT nulls
     ('risk_score': None, 'transparency_report': None, ...).
  2. collect_aggregate_stats used `result.get('risk_score', {})`. A dict's .get default
     applies only when the key is ABSENT — a key present with value None returns None.
     So `.get(...)` on it raised "'NoneType' object has no attribute 'get'", which was
     swallowed as a warning and degraded the job to _fallback_stats().
  3. Fallback stats are not data_backed, so generate_daily_post correctly returned None
     ("suppress rather than fabricate", v5.89.221).
  4. app._content_gen_job then did post_data['title'] on None -> TypeError, every run.

NOTE ON STYLE: these tests call the REAL functions with REAL shaped data. They do not
assert on dict literals constructed inside the test — a test that never touches the
product cannot catch a product bug.
"""
import json
from datetime import datetime

from gtm.content_engine import collect_aggregate_stats


class _FakeAnalysis:
    """Shaped like models.Analysis for the fields the collector reads. `status` and
    `created_at` are also CLASS attributes because the collector filters on the model
    class, not just instances."""
    status = 'completed'
    created_at = datetime.utcnow()

    def __init__(self, result_json):
        self.result_json = result_json
        self.risk_tier = 'moderate'
        self.offer_score = 70
        self.status = 'completed'
        self.created_at = datetime.utcnow()


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def filter_by(self, *a, **k):
        return self

    def all(self):
        return self._rows

    def count(self):
        return len(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *a, **k):
        return _FakeQuery(self._rows)


def _address_only_result_json():
    """Exactly the shape analysis_routes.py persists for an address-only analysis:
    the keys EXIST but are explicitly null."""
    return json.dumps({
        'analysis_depth': 'address_only',
        'risk_score': None,
        'transparency_report': None,
        'findings': None,
        'repair_costs': None,
        'cross_reference': None,
    })


def test_collector_survives_explicitly_null_keys():
    """The exact production payload that crashed the collector must not raise."""
    rows = [_FakeAnalysis(_address_only_result_json()) for _ in range(12)]
    session = _FakeSession(rows)

    stats = collect_aggregate_stats(session, {'Analysis': _FakeAnalysis})

    # It must return real stats, NOT silently degrade to the fallback.
    assert isinstance(stats, dict)
    assert stats.get('source') == 'live', (
        "collector fell back — a null-valued key still breaks it: %r" % (stats.get('source'),)
    )


def test_get_default_does_not_apply_to_null_values():
    """Pins the language behaviour the bug depended on, so the fix can't be reverted
    to `.get(key, {})` by someone assuming the default covers nulls."""
    payload = {'risk_score': None}
    assert payload.get('risk_score', {}) is None      # default does NOT apply
    assert (payload.get('risk_score') or {}) == {}    # the pattern the fix uses


def _content_gen_job_source():
    """Extract the body of _content_gen_job from app.py.

    It is a NESTED function (defined inside the scheduler setup), so it is not a
    module attribute — `app._content_gen_job` does not exist and inspect.getsource
    on it raises AttributeError. Read the source and slice out the block instead.
    """
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, 'app.py'), encoding='utf-8') as fh:
        lines = fh.read().split('\n')

    start = None
    indent = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith('def _content_gen_job('):
            start = i
            indent = len(line) - len(stripped)
            break
    assert start is not None, 'def _content_gen_job( not found in app.py'

    body = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break                      # dedented out of the function
        body.append(line)
    return '\n'.join(body)


def test_content_gen_job_guards_the_suppressed_post():
    """generate_daily_post returns None BY DESIGN when there's no real data sample
    ("suppress rather than fabricate"). The caller must treat that as a normal
    outcome, not subscript it — that unguarded subscript was 31 Sentry events."""
    src = _content_gen_job_source()

    subscript_pos = src.find("post_data['title']")
    guard_pos = src.find('if not post_data')

    assert subscript_pos != -1, 'test is stale — _content_gen_job no longer builds from post_data'
    assert guard_pos != -1, '_content_gen_job must guard the documented None return'
    assert guard_pos < subscript_pos, 'the None guard must come BEFORE post_data is subscripted'
