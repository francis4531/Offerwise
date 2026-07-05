"""
test_latency_timing.py — per-stage timing (v5.89.262). elapsed_ms accumulates on
the LLM result; record_stage_timing writes non-LLM phases; aggregate_latency rolls
them up slowest-first. This is the data that gates the progressive-delivery work,
so it must be correct.
"""
import ai_json
from admin_routes import aggregate_latency


class Row:
    def __init__(self, endpoint, elapsed_ms):
        self.endpoint = endpoint
        self.elapsed_ms = elapsed_ms


def test_result_carries_elapsed_ms():
    import dataclasses
    fields = {f.name for f in dataclasses.fields(ai_json.AIJsonResult)}
    assert 'elapsed_ms' in fields
    r = ai_json.AIJsonResult(ok=True)
    r.elapsed_ms += 100.0
    r.elapsed_ms += 50.0
    assert r.elapsed_ms == 150.0  # accumulates across attempts


def test_stage_timing_no_app_context_is_safe():
    # Outside a Flask app context it must skip cleanly, never raise.
    assert ai_json.record_stage_timing('ocr', 4200) == 0


def test_aggregate_sorts_slowest_first():
    rows = [Row('stage:research_wait', 8200), Row('stage:research_wait', 6100),
            Row('cross-reference', 4200), Row('inspection_extract', 5100),
            Row('permit', 2100)]
    stages = aggregate_latency(rows)
    assert stages[0]['stage'] == 'stage:research_wait'
    assert stages[0]['avg_ms'] == 7150 and stages[0]['count'] == 2
    # sorted strictly descending by avg
    avgs = [s['avg_ms'] for s in stages]
    assert avgs == sorted(avgs, reverse=True)


def test_aggregate_ignores_zero_and_none():
    rows = [Row('permit', 0), Row('permit', None), Row('permit', 2000)]
    stages = aggregate_latency(rows)
    assert len(stages) == 1 and stages[0]['count'] == 1 and stages[0]['avg_ms'] == 2000


def test_aggregate_empty():
    assert aggregate_latency([]) == []
