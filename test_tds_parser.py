"""
Unit tests for the Phase 1b TDS parser (C.A.R. Form TDS 6/24).

Built against and verified on a REAL executed specimen (381 Tina Dr). Pure
logic — the input is field state (the OCR/vision step's output), not the PDF.
"""
from reasoning.tds_parser import (
    load_tds_field_map, parse_tds_field_state, load_specimen_field_state,
)
from reasoning import run_pipeline, compose


def test_map_loads():
    tmap = load_tds_field_map()
    assert tmap.form_revision == "6/24"
    assert "C4_unpermitted_additions" in tmap.by_field


def test_specimen_readings_all_map_to_real_items():
    readings = parse_tds_field_state(load_specimen_field_state())
    ck = set(compose("CA", "SFH").ids())
    off = [r["item_id"] for r in readings if r["item_id"] not in ck]
    assert off == [], f"readings point at non-checklist items: {off}"


def test_gap_markers_never_become_readings():
    readings = parse_tds_field_state(load_specimen_field_state())
    assert not any(r["item_id"].startswith("NO_CHECKLIST_ITEM") for r in readings)


def test_clean_form_yields_no_concern_claims():
    # this specimen disclosed essentially nothing wrong (all Section C = No)
    readings = parse_tds_field_state(load_specimen_field_state())
    r = run_pipeline(readings, "CA", "SFH")
    concern = [c for c in r.claims if c.polarity == "contradicts"]
    assert concern == []
    assert len([c for c in r.claims if c.polarity == "supports"]) >= 8


def test_roof_freetext_flows_through():
    readings = parse_tds_field_state(load_specimen_field_state())
    roof_age = [r for r in readings if r["item_id"] == "roof.age"]
    roof_mat = [r for r in readings if r["item_id"] == "roof.cover_material"]
    assert roof_age and roof_age[0]["value"] == "35"
    assert roof_mat and roof_mat[0]["value"] == "Tile"


def test_disclosed_defect_becomes_concern_when_item_exists():
    # synthetic: a plumbing defect box checked -> a concern reading on a real item
    fs = {
        "section_B_defects_checked": ["B_plumbing_sewers_septics"],
        "section_C_yes": [],
        "section_A_present": [], "section_A_freetext": {}, "section_D": {},
    }
    readings = parse_tds_field_state(fs)
    plumb = [r for r in readings if r["item_id"] == "plumbing.active_leaks"]
    assert plumb and plumb[0]["value"] == "yes"


def test_section_c_yes_produces_concern():
    # synthetic: seller aware of unpermitted additions (C4 Yes)
    fs = {
        "section_B_defects_checked": [],
        "section_C_yes": ["C4_unpermitted_additions"],
        "section_A_present": [], "section_A_freetext": {}, "section_D": {},
    }
    r = run_pipeline(parse_tds_field_state(fs), "CA", "SFH")
    concern = [c for c in r.claims if c.checklist_item_id == "permits.unpermitted_additions"
               and c.polarity == "contradicts"]
    assert len(concern) >= 1
