"""
TDS parser (Phase 1b) — C.A.R. Form TDS, Revised 6/24.

Converts a TDS field-state dict into pipeline field readings keyed to checklist
items, using the real-layout field map (data/checklist/tds_field_map_v0_1.yaml,
built from a real executed specimen).

IMPORTANT — the input is FIELD STATE, not the PDF. Executed TDS forms are
commonly scanned images with no text layer (the 381 Tina Dr specimen is a CCITT
scan), so reading WHICH boxes are checked is an OCR/vision step. That extraction
step is the one remaining gap (flagged); this module is the deterministic layer
that turns verified field state into readings — pure and testable.

field_state shape (what the vision step yields):
    {
      "section_A_present": ["smoke_detectors_present", "water_conserving_fixtures", ...],
      "section_A_freetext": {"roof_type": "Tile", "roof_age": 35, ...},
      "section_B_defects_checked": ["B_interior_walls", ...],
      "section_C_yes": ["C4_unpermitted_additions", ...],   # items answered Yes
      "section_D": {"D1_smoke_detector_compliance": "certified",
                    "D2_water_heater_braced": "certified"},
    }
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_TDS_MAP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "checklist", "tds_field_map_v0_1.yaml",
)


class TDSMapError(ValueError):
    pass


@dataclass
class TDSFieldMap:
    form_revision: str
    by_field: Dict[str, Dict[str, Any]]
    raw: Dict[str, Any] = field(default_factory=dict)


def load_tds_field_map(path: Optional[str] = None) -> TDSFieldMap:
    path = path or DEFAULT_TDS_MAP_PATH
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    by_field: Dict[str, Dict[str, Any]] = {}
    for _section, entries in (raw.get("sections", {}) or {}).items():
        for e in entries:
            if "field" in e:
                by_field[e["field"]] = e
    if not by_field:
        raise TDSMapError("TDS field map has no field entries")
    return TDSFieldMap(form_revision=str(raw.get("form_revision", "?")),
                       by_field=by_field, raw=raw)


def _reading(item_id: str, value: str, locator: str, raw_text: str) -> Dict[str, str]:
    return {"item_id": item_id, "value": value, "source_form": "TDS",
            "locator": locator, "raw_text": raw_text}


def _is_real(item_id: str) -> bool:
    """A target that points at a real checklist item (not a documented gap)."""
    return bool(item_id) and not item_id.startswith("NO_CHECKLIST_ITEM")


def parse_tds_field_state(
    field_state: Dict[str, Any],
    tmap: Optional[TDSFieldMap] = None,
) -> List[Dict[str, str]]:
    """
    Turn TDS field state into pipeline field readings.

    A reading's `value` is 'yes' (concern present / contradicts clean) or 'no'
    (favorable / supports clean), matching the deterministic mapper's vocabulary.
    """
    tmap = tmap or load_tds_field_map()
    readings: List[Dict[str, str]] = []

    # Section B: each checked defect box -> a 'yes' (concern) reading.
    for f in field_state.get("section_B_defects_checked", []) or []:
        m = tmap.by_field.get(f)
        if m and _is_real(m.get("maps_to","")) and m.get("polarity_when_checked") == "contradicts":
            readings.append(_reading(m["maps_to"], "yes", f"TDS B/{f}",
                                     f"disclosed defect: {f}"))

    # Section C: items answered Yes -> 'yes' (concern). Items NOT in the yes-list
    # were answered No -> emit 'no' (favorable) so the clean reading is recorded.
    c_yes = set(field_state.get("section_C_yes", []) or [])
    for f, m in tmap.by_field.items():
        if not f.startswith("C"):
            continue
        if "polarity_when_yes" not in m or not _is_real(m.get("maps_to","")):
            continue
        if f in c_yes:
            readings.append(_reading(m["maps_to"], "yes", f"TDS C/{f}",
                                     f"seller aware: {f}"))
        else:
            readings.append(_reading(m["maps_to"], "no", f"TDS C/{f}",
                                     f"seller not aware: {f}"))

    # Section A presence: emit favorable readings for the decision-relevant ones.
    present = set(field_state.get("section_A_present", []) or [])
    for f, m in tmap.by_field.items():
        if m.get("polarity_when_present") == "supports" and _is_real(m.get("maps_to","")):
            val = "no" if f in present else "yes"  # present amenity = favorable = 'no concern'
            readings.append(_reading(m["maps_to"], val, f"TDS A/{f}",
                                     f"section A presence: {f}={'yes' if f in present else 'no'}"))

    # Section A free-text: roof type/age feed their checklist items as values.
    ft = field_state.get("section_A_freetext", {}) or {}
    if "roof_age" in ft:
        readings.append({"item_id": "roof.age", "value": str(ft["roof_age"]),
                         "source_form": "TDS", "locator": "TDS A/roof_age",
                         "raw_text": f"roof age (approx): {ft['roof_age']}"})
    if "roof_type" in ft:
        readings.append({"item_id": "roof.cover_material", "value": str(ft["roof_type"]),
                         "source_form": "TDS", "locator": "TDS A/roof_type",
                         "raw_text": f"roof type: {ft['roof_type']}"})

    # Section D: certifications -> favorable readings.
    d = field_state.get("section_D", {}) or {}
    for f, status in d.items():
        m = tmap.by_field.get(f)
        if m and _is_real(m.get("maps_to","")) and status == "certified" and m.get("polarity_when_certified") == "supports":
            readings.append(_reading(m["maps_to"], "no", f"TDS D/{f}",
                                     f"seller certified: {f}"))

    return readings


def load_specimen_field_state(tmap: Optional[TDSFieldMap] = None) -> Dict[str, Any]:
    """Return the verified 381 Tina Dr specimen field state from the map asset."""
    tmap = tmap or load_tds_field_map()
    spec = tmap.raw.get("specimen_381_tina_dr", {})
    return {
        "section_A_present": spec.get("section_A_present", []),
        "section_A_freetext": spec.get("section_A_freetext", {}),
        "section_B_defects_checked": spec.get("section_B_defects_checked", []),
        "section_C_yes": spec.get("section_C_yes", []),
        "section_D": spec.get("section_D", {}),
    }
