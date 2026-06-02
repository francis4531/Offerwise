"""
Tests for the inspection report parser (the moat) against the REAL specimen
(2839 Pendleton Dr, WIN Home Inspection / ASHI).

Pure logic — input is inspection report text (the existing extraction layer's
output). Verifies the four-plus silent hazards are caught, clean items read
clean, and there is no cross-section token bleed.
"""
from reasoning.inspection_parser import (
    load_inspection_field_map, parse_inspection_text,
    load_inspection_specimen_findings,
)
from reasoning import compose


# Faithful detail-section text reconstructed from the real report. The parser
# needs the component labels + the rating/token context that actually appear.
PENDLETON_DETAIL = """HOME INSPECTION DETAILS
Structure
10. Smoke Detector(s) Missing BR. no smoke detectors installed in the bedrooms.
11. Carbon Monoxide Detector(s) Present. multiple carbon monoxide detectors.
13. Asbestos Noted Pre 1978. popcorn ceiling could contain asbestos.
14. Lead Pre 1978. could contain lead based paint.
Electrical Service
3. Over Current Devices Federal Pacific. FPE brand panels with Stab-Lock circuit breakers.
5. Panel to Structure Copper/Aluminum. some of the electrical circuit wiring is aluminum.
11. G.F.C.I. Protection Attention. would not trip when tested.
12. Receptacle Ground Verify Functional. has not found any that were not correctly grounded.
Heating System
11. Vents / Flues Attention. furnace exhaust flue is partially detached in the garage. spent gasses.
Plumbing
2. Structure Pipe Material Combination. galvanized steel water supply piping remaining.
6. Encrustations Evident Yes. encrustation on the galvanized water supply pipe.
9. Evidence of Leaks Yes. leak at the incoming water supply pipe.
Water Heater
9. Safety Tie Down(s) Attention. the tape does not encircle the tank.
Roof
14. Indications of Leaking No. no evidence the roof is leaking.
Structure Perimeter Exterior
7. Evidence of Movement No. no significant settlement.
9. Site Drainage Satisfactory. grading slopes away from the foundation.
"""


def _readings():
    return parse_inspection_text(PENDLETON_DETAIL, load_inspection_field_map())


def test_map_loads():
    fmap = load_inspection_field_map()
    assert fmap.format == "WINspect"
    assert len(fmap.findings) > 10


def test_five_silent_hazards_caught():
    silent = sorted(r["item_id"] for r in _readings() if r["silent_hazard"])
    assert silent == [
        "electrical.panel_brand_safety",
        "electrical.wiring_material",
        "environmental.asbestos_risk",
        "environmental.lead_paint_risk",
        "hvac.flue_venting_integrity",
    ]


def test_fpe_panel_is_major_concern():
    fpe = [r for r in _readings() if r["item_id"] == "electrical.panel_brand_safety"]
    assert fpe and fpe[0]["value"] == "yes"
    assert fpe[0]["severity"] == "major"
    assert fpe[0]["silent_hazard"] is True


def test_clean_items_read_clean():
    clean = {r["item_id"] for r in _readings() if r["value"] == "no"}
    # receptacle grounding is Functional; CO detectors are Present; roof not leaking
    assert "electrical.receptacle_grounding" in clean
    assert "safety.co_detectors" in clean
    assert "roof.leak_evidence" in clean


def test_no_cross_section_token_bleed():
    # the 'Attention' on the Heating flue must not turn receptacle grounding into
    # a concern (the bug the window-bounding fix addresses)
    rg = [r for r in _readings() if r["item_id"] == "electrical.receptacle_grounding"]
    assert rg and rg[0]["value"] == "no"


def test_all_readings_on_checklist():
    ck = set(compose("CA", "SFH").ids())
    off = [r["item_id"] for r in _readings() if r["item_id"] not in ck]
    assert off == [], f"off-checklist readings: {off}"


def test_plumbing_cluster_caught():
    concerns = {r["item_id"] for r in _readings() if r["value"] == "yes"}
    assert "plumbing.active_leaks" in concerns
    assert "plumbing.supply_pipe_material" in concerns
    assert "plumbing.known_defect_pipe_material" in concerns


def test_specimen_fixture_present():
    spec = load_inspection_specimen_findings()
    assert spec.get("property", "").startswith("2839 Pendleton")
    assert "electrical.panel_brand_safety" in spec.get("concerns", {})
