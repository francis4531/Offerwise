"""
test_factcheck_parse_failure.py — v5.89.290. verify_finding_against_source used a raw
json.loads() on the model's text, so a stray token / truncation raised "Expecting
value: line 1 column 1", got caught, and SILENTLY FAILED SAFE to supported=True —
asserting a finding was verified when verification never happened (the
tight-but-fabricated failure mode) while paging Sentry at error level. It now routes
through ai_json (truncation-aware retry + repair + telemetry) and, on a genuine parse
failure, reports NOT supported with zero confidence and a surfaced error.
"""
from unittest.mock import patch
import ai_json
from analysis_ai_helper import AnalysisAIHelper


def _instance():
    inst = AnalysisAIHelper.__new__(AnalysisAIHelper)   # skip __init__/API client
    inst.client = object()
    inst.enabled = True
    return inst


def test_unparseable_response_does_not_claim_support():
    bad = ai_json.AIJsonResult(ok=False, data=None, raw_text='<not json>',
                               error='Expecting value: line 1 column 1 (char 0)')
    with patch('ai_json.call_ai_json', return_value=bad):
        out = _instance().verify_finding_against_source('roof is failing', 'document text')
    assert out['supported'] is False        # never assert a support we never received
    assert out['confidence'] == 0.0
    assert out['verdict'] == 'uncertain'
    assert 'verification unavailable' in (out.get('error') or '')


def test_parsed_response_is_used_normally():
    good = ai_json.AIJsonResult(ok=True, data={
        'verdict': 'supported', 'confidence': 0.91,
        'evidence_quotes': ['shingles cupping'], 'explanation': 'inspector noted it'})
    with patch('ai_json.call_ai_json', return_value=good):
        out = _instance().verify_finding_against_source('roof is failing', 'document text')
    assert out['supported'] is True and out['confidence'] == 0.91
    assert out['evidence'] == ['shingles cupping']
