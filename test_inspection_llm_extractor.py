"""Tests for the LLM-based (format-general) inspection extractor and the
keyword-first / LLM-fallback orchestration. Uses mock clients — no live API."""
import json
from reasoning.inspection_llm_extractor import extract_inspection_findings_llm
from reasoning.inspection_parser import extract_inspection_readings
from reasoning import compose

IDS = sorted(compose("CA", "SFH").ids())


class _Resp:
    def __init__(self, t): self.content = [type("B", (), {"text": t})()]

class _Client:
    def __init__(self, payload): self._payload = payload
    class _M:
        pass
    @property
    def messages(self):
        c = self
        class M:
            def create(self, **k): return _Resp(c._payload)
        return M()


def test_llm_extract_maps_to_vocabulary_and_drops_unknown():
    payload = json.dumps([
        {"item_id": "hvac.flue_venting_integrity", "value": "yes", "severity": "major", "evidence": "CO risk at flue"},
        {"item_id": "not.real", "value": "yes", "severity": "critical", "evidence": "drop me"},
    ])
    out = extract_inspection_findings_llm("any report text", IDS, client=_Client(payload))
    ids = [r["item_id"] for r in out]
    assert "hvac.flue_venting_integrity" in ids
    assert "not.real" not in ids       # off-vocabulary dropped


def test_llm_extract_flags_silent_hazards():
    payload = json.dumps([
        {"item_id": "electrical.panel_brand_safety", "value": "yes", "severity": "major", "evidence": "FPE panel"},
    ])
    out = extract_inspection_findings_llm("x", IDS, client=_Client(payload))
    assert out and out[0]["silent_hazard"] is True


def test_llm_extract_handles_fences_and_bad_json():
    # fenced JSON should parse
    fenced = "```json\n[{\"item_id\":\"roof.leak_evidence\",\"value\":\"no\",\"severity\":\"clean\"}]\n```"
    out = extract_inspection_findings_llm("x", IDS, client=_Client(fenced))
    assert out and out[0]["value"] == "no"
    # garbage -> [] (never raises)
    out2 = extract_inspection_findings_llm("x", IDS, client=_Client("not json at all"))
    assert out2 == []


def test_orchestration_prefers_keyword_when_concerns_found():
    # WINspect-shaped text -> keyword path, LLM must NOT be called
    win = open("test_inspection_parser.py").read().split('PENDLETON_DETAIL = """')[1].split('"""')[0]
    class Boom:
        @property
        def messages(self):
            class M:
                def create(self, **k): raise AssertionError("LLM must not run")
            return M()
    out = extract_inspection_readings(win, llm_client=Boom())
    assert out["method"] == "keyword"
    assert sum(1 for r in out["readings"] if r["value"] == "yes") >= 2


def test_orchestration_falls_back_to_llm_when_keyword_empty():
    payload = json.dumps([
        {"item_id": "plumbing.active_leaks", "value": "yes", "severity": "critical", "evidence": "leak"},
    ])
    # narrative text the keyword parser can't handle
    out = extract_inspection_readings("Some narrative report with no rating words.",
                                      llm_client=_Client(payload))
    assert out["method"] == "llm"
    assert out["readings"][0]["item_id"] == "plumbing.active_leaks"


def test_orchestration_no_client_degrades_gracefully():
    # keyword empty + no usable client -> empty, no raise
    out = extract_inspection_readings("narrative with nothing parseable", allow_llm=False)
    assert out["method"] in ("keyword", "keyword_empty")
    assert out["readings"] == []
