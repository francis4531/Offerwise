"""
test_ai_json.py — robust structured-output handling.

These tests pin the behavior that fixes the live production bug
(optimized_hybrid_cross_reference "Unterminated string ...") and the silent
fallback it caused: truncation is detected via stop_reason, retried at a higher
budget, partial JSON is salvaged, and a genuine failure is surfaced (ok=False)
rather than swallowed.

Telemetry is disabled (record_telemetry=False) so these run without an app
context / database.
"""

import json

from ai_json import (
    extract_json_text,
    try_parse_json,
    call_ai_json,
)


# --- fakes -----------------------------------------------------------------

class _Block:
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text, stop_reason):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, scripted):
        self._scripted = list(scripted)  # list of (text, stop_reason)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if not self._scripted:
            raise RuntimeError("no more scripted responses")
        text, sr = self._scripted.pop(0)
        return _Resp(text, sr)


class _FakeClient:
    def __init__(self, scripted):
        self.messages = _FakeMessages(scripted)


class _BoomClient:
    class messages:  # noqa
        @staticmethod
        def create(**kwargs):
            raise RuntimeError("transport boom")


# --- extraction ------------------------------------------------------------

def test_extract_strips_json_fence():
    raw = 'Here you go:\n```json\n{"a": 1}\n```\nthanks'
    assert json.loads(extract_json_text(raw)) == {"a": 1}


def test_extract_isolates_balanced_span_from_prose():
    raw = 'Sure! {"a": 1, "b": [2, 3]} hope that helps'
    assert json.loads(extract_json_text(raw)) == {"a": 1, "b": [2, 3]}


# --- repair of the real failure signature ----------------------------------

def test_parse_recovers_truncated_array_of_objects():
    # The exact shape that truncated in production: an issues array cut off
    # mid-string inside the last object.
    truncated = (
        '{"issues": ['
        '{"id":"C1","severity":"major","explanation":"mixing valve dead","confidence":0.9},'
        '{"id":"C2","severity":"critical","explanation":"FPE panel is a known fire haz'
    )
    ok, data, repaired, err = try_parse_json(truncated)
    assert ok is True
    assert repaired is True
    issues = data["issues"]
    # C1 fully survives; C2 is salvaged (possibly without its truncated field).
    assert any(i.get("id") == "C1" and i.get("explanation") == "mixing valve dead"
               for i in issues)
    assert any(i.get("id") == "C2" for i in issues)


def test_parse_clean_json_is_not_marked_repaired():
    ok, data, repaired, err = try_parse_json('{"issues": [], "transparency_score": 80}')
    assert ok is True
    assert repaired is False
    assert data["transparency_score"] == 80


def test_parse_unrecoverable_returns_not_ok():
    ok, data, repaired, err = try_parse_json("not json at all <<<")
    assert ok is False
    assert data is None
    assert err


# --- the call wrapper: truncation retry + no silent failure ----------------

def test_truncation_triggers_higher_budget_retry():
    big_partial = '{"issues": [{"id":"C1","severity":"major","explanation":"x"'
    good = '{"issues": [{"id":"C1","severity":"major","explanation":"x","confidence":0.9}], "transparency_score": 70, "summary": "ok"}'
    client = _FakeClient([
        (big_partial, "max_tokens"),  # first call truncates
        (good, "end_turn"),           # retry at higher ceiling succeeds
    ])
    res = call_ai_json(
        "prompt", max_tokens=1000, max_tokens_ceiling=4000,
        ai_client=client, endpoint="unit-test", record_telemetry=False,
    )
    assert res.ok is True
    assert res.attempts == 2
    assert res.truncated is False  # reflects the final (successful) call
    assert client.messages.calls == 2
    assert res.data["transparency_score"] == 70


def test_truncation_without_retry_still_salvages_not_raises():
    partial = '{"issues": [{"id":"C1","severity":"major","explanation":"x","confidence":0.9},{"id":"C2","severity":"crit'
    client = _FakeClient([(partial, "max_tokens")])
    res = call_ai_json(
        "prompt", max_tokens=1000, retry_on_truncation=False,
        ai_client=client, endpoint="unit-test", record_telemetry=False,
    )
    assert res.truncated is True
    # salvage recovers at least C1
    assert res.ok is True
    assert any(i["id"] == "C1" for i in res.data["issues"])


def test_transport_failure_is_surfaced_not_raised():
    res = call_ai_json(
        "prompt", max_tokens=1000,
        ai_client=_BoomClient(), endpoint="unit-test", record_telemetry=False,
    )
    assert res.ok is False
    assert "call_failed" in (res.error or "")


def test_track_hook_is_invoked_with_response_and_ms():
    good = '{"ok": true}'
    client = _FakeClient([(good, "end_turn")])
    seen = []
    call_ai_json(
        "prompt", max_tokens=500, ai_client=client, endpoint="unit-test",
        record_telemetry=False, track=lambda r, ms: seen.append((r, ms)),
    )
    assert len(seen) == 1
    resp, ms = seen[0]
    assert resp.stop_reason == "end_turn"
    assert isinstance(ms, float)


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
