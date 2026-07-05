"""
test_research_synthesis_none.py — v5.89.265. When the AVM is suppressed at source
(profile.estimated_value is None), the research synthesis prompt must NOT crash
with 'unsupported format string passed to NoneType.__format__', and must not leak
a literal ternary into the LLM prompt. This was silently degrading live analyses
on every suppressed-AVM property (research "1 fail", AVM=0 downstream).
"""
from unittest.mock import patch
import property_research_agent as pra


def _profile(estimated_value):
    p = pra.PropertyProfile(address="2839 Pendleton Dr, San Jose, CA 95148")
    p.address_normalized = "2839 Pendleton Dr"
    p.year_built = 1977
    p.estimated_value = estimated_value
    p.sqft = 1200
    p.agent_findings = []
    return p


def _run_synth(estimated_value):
    captured = {}
    def _fake_ai(prompt, **kw):
        captured['prompt'] = prompt
        return "Synthesis OK."
    agent = pra.PropertyResearchAgent.__new__(pra.PropertyResearchAgent)
    agent.ai_client = object()  # truthy so synthesis runs
    with patch.object(pra, 'get_ai_response', _fake_ai, create=True), \
         patch('ai_client.get_ai_response', _fake_ai, create=True):
        out = agent._ai_synthesize(_profile(estimated_value), [])
    return out, captured.get('prompt', '')


def test_suppressed_avm_none_does_not_crash():
    out, prompt = _run_synth(None)          # must not raise NoneType.__format__
    assert 'ESTIMATED VALUE: Unknown' in prompt
    assert 'if profile.estimated_value' not in prompt   # no literal ternary leak


def test_zero_value_is_unknown():
    _out, prompt = _run_synth(0)
    assert 'ESTIMATED VALUE: Unknown' in prompt


def test_real_value_formats_with_commas():
    _out, prompt = _run_synth(1257000)
    assert 'ESTIMATED VALUE: $1,257,000' in prompt
