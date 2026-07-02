"""
test_disclosure_llm_extractor.py — the disclosure side of the cross-reference.

Verifies it captures both disclosed concerns ('yes') and affirmative clean
answers ('no' — what creates contradictions), constrains to the disclosure-
addressable vocabulary, dedups preferring the concern, and never raises.
"""
import json

from reasoning.disclosure_llm_extractor import (
    extract_disclosure_findings_llm,
    _disclosure_addressable_ids,
)

IDS = ["structure.water_intrusion_bath", "structure.water_intrusion_kitchen",
       "environmental.asbestos_risk"]


class _Block:
    def __init__(self, t): self.text = t


class _Resp:
    def __init__(self, arr):
        self.content = [_Block(json.dumps(arr))]
        self.stop_reason = "end_turn"


class _Msgs:
    def __init__(self, arr): self._arr = arr
    def create(self, **k): return _Resp(self._arr)


class _Client:
    def __init__(self, arr): self.messages = _Msgs(arr)


def test_addressable_intersects_form_map():
    a = _disclosure_addressable_ids(IDS + ["not.a.real.item"])
    assert "structure.water_intrusion_bath" in a
    assert "not.a.real.item" not in a  # not disclosure-addressable -> dropped


def test_captures_disclosed_concern_and_clean_answer():
    arr = [
        {"item_id": "structure.water_intrusion_bath", "value": "yes", "evidence": "water leak from master shower"},
        {"item_id": "environmental.asbestos_risk", "value": "no", "evidence": "not aware of asbestos"},
        {"item_id": "bogus.item", "value": "yes", "evidence": "x"},  # outside vocab
    ]
    out = extract_disclosure_findings_llm("packet text", IDS, client=_Client(arr))
    byid = {r["item_id"]: r for r in out}
    assert byid["structure.water_intrusion_bath"]["value"] == "yes"
    assert byid["environmental.asbestos_risk"]["value"] == "no"   # clean answer kept
    assert "bogus.item" not in byid                                # invented id dropped


def test_prefers_concern_over_clean_on_duplicate():
    arr = [
        {"item_id": "structure.water_intrusion_kitchen", "value": "no", "evidence": "none"},
        {"item_id": "structure.water_intrusion_kitchen", "value": "yes", "evidence": "kitchen leak fixed"},
    ]
    out = extract_disclosure_findings_llm("t", IDS, client=_Client(arr))
    assert len(out) == 1 and out[0]["value"] == "yes"


def test_empty_inputs_return_empty():
    assert extract_disclosure_findings_llm("", IDS) == []
    assert extract_disclosure_findings_llm("t", []) == []


def test_disclosure_plus_inspection_yields_corroborated_and_contradiction():
    """The payoff: disclosure extractor output + inspection readings through the
    real pipeline must produce the full cross-reference statuses."""
    from reasoning import run_pipeline
    disc = extract_disclosure_findings_llm("packet", IDS, client=_Client([
        {"item_id": "structure.water_intrusion_bath", "value": "yes", "evidence": "master shower leak"},
        {"item_id": "environmental.asbestos_risk", "value": "no", "evidence": "not aware of asbestos"},
    ]))
    insp = [
        dict(item_id="structure.water_intrusion_bath", value="yes", severity="moderate",
             raw_text="bath water pattern", corroborated_in_summary=True, locator="INSP/1"),
        dict(item_id="environmental.asbestos_risk", value="yes", severity="moderate",
             raw_text="pre-1978 popcorn ceiling", corroborated_in_summary=False, locator="INSP/2"),
    ]
    r = run_pipeline(field_readings=disc, jurisdiction="CA", property_type="SFH",
                     inspection_readings=insp, persist=False)
    statuses = {getattr(i, "disclosure_status", "") for i in r.issues_result.issues}
    assert "corroborated" in statuses    # bath: seller disclosed + inspection found
    assert "contradiction" in statuses   # asbestos: seller said clean + inspection found
