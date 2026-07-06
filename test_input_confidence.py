"""
test_input_confidence.py — v5.89.269. Input-quality confidence gate: a document
that was provided but couldn't be read must flag the analysis low-confidence, so a
failed read can't masquerade as a defensible offer (the confident $129k discount on
an unreadable inspection from the logs). Same philosophy as the AVM gate.
"""
from analysis_routes import compute_input_confidence

REAL = ("The roof shows active water intrusion at the northeast corner. The Federal "
        "Pacific electrical panel is a recognized fire hazard. Foundation settling "
        "noted near the garage. Water heater is past service life. ") * 6


def test_unreadable_inspection_flags_low_confidence():
    low, reasons = compute_input_confidence(
        has_disclosure=False, has_inspection=True,
        disclosure_text="", inspection_text="")   # empty = unreadable
    assert low is True
    assert any("inspection" in r.lower() for r in reasons)


def test_unreadable_disclosure_flags_low_confidence():
    low, reasons = compute_input_confidence(
        has_disclosure=True, has_inspection=False,
        disclosure_text=". . .", inspection_text="")   # near-empty
    assert low is True
    assert any("disclosure" in r.lower() for r in reasons)


def test_both_readable_is_high_confidence():
    low, reasons = compute_input_confidence(
        has_disclosure=True, has_inspection=True,
        disclosure_text=REAL, inspection_text=REAL)
    assert low is False and reasons == []


def test_absent_document_is_not_a_confidence_hit():
    # No inspection provided at all -> not a low-confidence signal (that's the
    # missing-doc CTA case, not a failed read).
    low, reasons = compute_input_confidence(
        has_disclosure=True, has_inspection=False,
        disclosure_text=REAL, inspection_text="")
    assert low is False


def test_readable_inspection_but_unreadable_disclosure():
    low, reasons = compute_input_confidence(
        has_disclosure=True, has_inspection=True,
        disclosure_text="", inspection_text=REAL)
    assert low is True and len(reasons) == 1 and "disclosure" in reasons[0].lower()
