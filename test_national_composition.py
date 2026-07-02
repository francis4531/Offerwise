"""
test_national_composition.py — the product must work for properties all over the
US, not just California.

Guards against re-introducing a CA assumption: an unknown/non-CA state must
resolve to the national base (never CA's overlays), CA must remain a strict
superset (its depth intact), and issue derivation must work on national-base
readings for a non-CA property.
"""

from reasoning import compose, run_pipeline
from reasoning.composition import jurisdiction_coverage

_CA_ONLY_ITEM = "ca.water_heater_seismic_bracing"


def test_unknown_state_resolves_to_national_base_not_ca():
    tx = set(compose("TX", "SFH").ids())
    fl = set(compose("FL", "SFH").ids())
    star = set(compose("*", "SFH").ids())
    assert tx == star, "TX must resolve to the national base (no TX overlay authored)"
    assert fl == star, "FL must resolve to the national base"
    # a CA-only item must NOT leak into a non-CA property
    assert _CA_ONLY_ITEM not in tx
    assert _CA_ONLY_ITEM not in fl


def test_ca_is_a_strict_superset_of_national_base():
    base = set(compose("*", "SFH").ids())
    ca = set(compose("CA", "SFH").ids())
    assert base.issubset(ca), "national base must be included in CA"
    assert len(ca) > len(base), "CA overlay must add depth"
    assert _CA_ONLY_ITEM in ca


def test_pipeline_derives_issues_for_a_non_ca_property():
    # national-base readings only, scored as a Texas property
    readings = [
        dict(item_id="electrical.panel_brand_safety", value="yes", severity="critical",
             raw_text="Federal Pacific panel", corroborated_in_summary=True, locator="INSP/1"),
        dict(item_id="plumbing.active_leaks", value="yes", severity="major",
             raw_text="active supply-line leak", corroborated_in_summary=True, locator="INSP/2"),
        dict(item_id="structure.water_intrusion_kitchen", value="yes", severity="moderate",
             raw_text="kitchen floor warping at the fridge", corroborated_in_summary=False, locator="INSP/3"),
    ]
    r = run_pipeline(field_readings=[], jurisdiction="TX", property_type="SFH",
                     inspection_readings=readings, persist=False)
    issues = r.issues_result.issues if r.issues_result else []
    surfaced = set()
    for i in issues:
        surfaced.update(getattr(i, "claim_item_ids", []) or [])
    assert "electrical.panel_brand_safety" in surfaced
    assert "plumbing.active_leaks" in surfaced
    assert len(issues) >= 2


def test_coverage_reports_only_authored_overlays():
    cov = jurisdiction_coverage()
    assert cov["national_base_items"] >= 50
    assert cov["state_overlays"] == ["CA"], "only CA is authored today; keep this honest"
    assert all(m.startswith("CA:") for m in cov["municipal_overlays"])
