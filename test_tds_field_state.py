"""
Tests for pdf_handler.extract_tds_field_state — the bridge from extracted/OCR'd
TDS text (with [X]/[ ] checkbox markers, as the existing vision prompt emits) to
the structured field state the reasoning TDS parser consumes.

pdf_handler imports heavy OCR libs at module load (pdfplumber/PyPDF2/pdf2image/
PIL); those are prod deps absent from this sandbox, so we stub them. In CI with
requirements.txt installed, the stubs are simply unused.
"""
import sys
import types
import pytest


def _ensure_pdf_handler_importable():
    for mod in ("pdfplumber", "PyPDF2", "pytesseract"):
        sys.modules.setdefault(mod, types.ModuleType(mod))
    if "pdfminer" not in sys.modules:
        sys.modules["pdfminer"] = types.ModuleType("pdfminer")
        hl = types.ModuleType("pdfminer.high_level")
        hl.extract_text = lambda *a, **k: ""
        sys.modules["pdfminer.high_level"] = hl
    if "PIL" not in sys.modules:
        pil = types.ModuleType("PIL")
        imgmod = types.ModuleType("PIL.Image")
        class _Img:  # noqa
            pass
        imgmod.Image = _Img
        pil.Image = imgmod
        sys.modules["PIL"] = pil
        sys.modules["PIL.Image"] = imgmod
    if not hasattr(sys.modules.get("pdf2image", None), "convert_from_bytes"):
        p2i = sys.modules.setdefault("pdf2image", types.ModuleType("pdf2image"))
        p2i.convert_from_bytes = lambda *a, **k: []


@pytest.fixture(scope="module")
def extract_fn():
    _ensure_pdf_handler_importable()
    from pdf_handler import extract_tds_field_state
    return extract_tds_field_state


# A clean TDS in the [X] convention the vision prompt emits — mirrors 381 Tina Dr:
# only interior walls is a disclosed defect; all Section C = No.
CLEAN_TDS = """TRANSFER DISCLOSURE STATEMENT
Section A. The property has the items checked below:
Range [X]  Smoke Detector(s) [X]  Carbon Monoxide Device(s) [X]
Central Heating [X]  Central Air Conditioning [X]  Water Heater [X] Gas
Water-Conserving Plumbing Fixtures [X]
Roof: Type Tile  Age 35 years
Section B. Are you aware of significant defects/malfunctions:
Interior Walls [X]  Ceilings [ ]  Roof [ ]  Foundation [ ]  Slab [ ]  Plumbing [ ]  Electrical [ ]
Describe: Holes from hanging art/tvs
Section C. Are you aware of any of the following:
1. Environmental hazards  Yes [ ]  No [X]
4. Room additions without permit  Yes [ ]  No [X]
7. Settling from any cause  Yes [ ]  No [X]
13. Homeowners Association  Yes [ ]  No [X]
16. Lawsuits threatening  Yes [ ]  No [X]
Section D. Seller certifies smoke detector compliance [X]; water heater braced.
"""


def test_low_signal_text_returns_none(extract_fn):
    fs, score, notes = extract_fn("just some random text, not a disclosure")
    assert fs is None  # never fabricate field state


def test_clean_tds_section_b_only_interior_walls(extract_fn):
    fs, score, notes = extract_fn(CLEAN_TDS)
    assert fs is not None
    # region-scoped: 'Plumbing' in Section A 'Plumbing Fixtures' must NOT leak
    # into Section B; only the actually-checked Interior Walls is a defect.
    assert fs["section_B_defects_checked"] == ["B_interior_walls"]


def test_clean_tds_section_c_all_no(extract_fn):
    fs, _, _ = extract_fn(CLEAN_TDS)
    assert fs["section_C_yes"] == []


def test_roof_freetext_extracted(extract_fn):
    fs, _, _ = extract_fn(CLEAN_TDS)
    assert fs["section_A_freetext"].get("roof_type") == "Tile"
    assert fs["section_A_freetext"].get("roof_age") == 35


def test_section_d_certifications(extract_fn):
    fs, _, _ = extract_fn(CLEAN_TDS)
    assert fs["section_D"].get("D1_smoke_detector_compliance") == "certified"
    assert fs["section_D"].get("D2_water_heater_braced") == "certified"


def test_positive_control_real_yes_and_defect(extract_fn):
    text = CLEAN_TDS.replace(
        "7. Settling from any cause  Yes [ ]  No [X]",
        "7. Settling from any cause  Yes [X]  No [ ]",
    ).replace("Roof [ ]", "Roof [X]")
    fs, _, _ = extract_fn(text)
    assert "C7_settling_slippage" in fs["section_C_yes"]
    assert "B_roof" in fs["section_B_defects_checked"]


def test_end_to_end_through_pipeline(extract_fn):
    from reasoning.tds_parser import parse_tds_field_state
    from reasoning import run_pipeline, compose
    fs, _, _ = extract_fn(CLEAN_TDS)
    readings = parse_tds_field_state(fs)
    ck = set(compose("CA", "SFH").ids())
    assert all(r["item_id"] in ck for r in readings)  # nothing off-checklist
    r = run_pipeline(readings, "CA", "SFH")
    # clean form -> no concern claims
    assert [c for c in r.claims if c.polarity == "contradicts"] == []
