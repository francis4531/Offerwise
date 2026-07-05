"""
test_extractor_diagnostic.py — the water cross-reference diagnostic's verdict
logic (v5.89.246) classifies why the disclosed bath finding did/didn't corroborate.
Locks every branch so the diagnostic can't silently mis-diagnose a real vs
fabricated corroboration — the distinction that gates whether we touch the
derivation at all.
"""
from admin_routes import water_xref_verdict

BATH = "structure.water_intrusion_bath"
LEAK = "plumbing.active_leaks"
ROOF = "roof.leak_evidence"
RELATED = {LEAK, "structure.water_intrusion_history"}


def code(*args, **kw):
    return water_xref_verdict(*args, **kw)[0]


def test_no_inspection_readings():
    assert code([], [{"x": 1}], set(), set(), RELATED) == "no_inspection"


def test_no_disclosure_readings():
    assert code([{"x": 1}], [], set(), set(), RELATED) == "no_disclosure"


def test_exact_match_should_corroborate():
    # both sides on the bath item -> corroboration should form; if it doesn't,
    # it's a pipeline bug, not extraction.
    assert code([1], [1], {BATH}, {BATH}, RELATED) == "exact"


def test_related_item_divergence_is_the_fixable_case():
    # disclosure on bath, inspection water on a directly-related item.
    assert code([1], [1], {LEAK}, {BATH}, RELATED) == "related_divergence"


def test_recall_gap_means_disclosed_not_found_is_correct():
    # inspection pulled NO water concern -> corroboration would be fabricated.
    assert code([1], [1], set(), {BATH}, RELATED) == "recall_gap"


def test_unrelated_water_would_be_fabricated_corroboration():
    # inspection water on an UNrelated item (e.g. roof) -> different finding.
    assert code([1], [1], {ROOF}, {BATH}, RELATED) == "unrelated_water"


def test_message_is_actionable_for_divergence():
    _c, msg = water_xref_verdict([1], [1], {LEAK}, {BATH}, RELATED)
    assert LEAK in msg and "CONFIRM" in msg  # names the item + warns to verify
