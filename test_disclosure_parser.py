"""
test_disclosure_parser.py — the disclosure half of the moat must be format-
general in the BUYER path, not California-TDS-only.

Before this, the buyer path produced disclosure readings only for a recognizable
California TDS; every other state's disclosure yielded nothing, so the
seller-vs-inspection cross-reference (the product) silently didn't fire off-CA.
These lock the dispatcher's contract: deterministic-first, LLM-fallback, never
raises, and offers the extractor the full authored id universe when unscoped.
"""

from reasoning.disclosure_parser import extract_disclosure_readings
from reasoning.composition import all_authored_ids


def test_empty_text_returns_empty_never_raises():
    out = extract_disclosure_readings("")
    assert out == {"readings": [], "method": "empty"}
    out2 = extract_disclosure_readings(None)  # type: ignore[arg-type]
    assert out2["readings"] == []


def test_non_tds_with_llm_disabled_falls_through_cleanly():
    # A Texas TREC notice is not a CA TDS, so the deterministic path no-ops; with
    # the LLM path disabled it must return empty rather than crash or fabricate.
    out = extract_disclosure_readings(
        "TEXAS REAL ESTATE COMMISSION Seller's Disclosure Notice ...",
        allow_llm=False,
    )
    assert out["readings"] == []
    assert out["method"] == "empty"


def test_llm_fallback_is_offered_the_full_id_universe(monkeypatch):
    # When the deterministic path no-ops and no checklist is passed, the LLM
    # extractor must be offered the full authored universe (not compose('CA',...)).
    captured = {}

    def _fake_llm(text, checklist_ids, *, client=None, model=None):
        captured["ids"] = list(checklist_ids)
        return [{"item_id": "plumbing.active_leaks", "value": "yes", "raw_text": "leak"}]

    import reasoning.disclosure_llm_extractor as dmod
    monkeypatch.setattr(dmod, "extract_disclosure_findings_llm", _fake_llm)
    # also patch the name imported inside disclosure_parser's function scope
    import reasoning.disclosure_parser as dp
    monkeypatch.setattr(
        "reasoning.disclosure_llm_extractor.extract_disclosure_findings_llm", _fake_llm
    )

    out = extract_disclosure_readings("Washington Form 17 seller disclosure ...")
    assert out["method"] == "llm"
    assert out["readings"]
    assert set(captured["ids"]) == set(all_authored_ids())
