"""
Unit tests for the Phase 1a deterministic form-field mapper.

Pure logic — no DB, no network, no live path. Verifies the curated map loads,
coverage is as generated, and the deterministic mapper emits correct Claims
with correct polarity and confidence.
"""
import pytest

from reasoning import load_form_field_map, map_field_to_claim, map_fields_to_claims
from reasoning.form_field_map import FormMapError, DEFAULT_MAP_PATH
import os


def test_map_loads_and_covers_items():
    fmap = load_form_field_map()
    assert fmap.version == "v0.1"
    assert fmap.coverage.get("deterministic_items") == 47
    assert len(fmap.item_ids()) == 47


def test_map_file_present():
    assert os.path.exists(DEFAULT_MAP_PATH)


def test_positive_defect_reading_contradicts_clean_property():
    fmap = load_form_field_map()
    # pick any mapped item
    iid = fmap.item_ids()[0]
    claim = map_field_to_claim(iid, "yes", fmap)
    assert claim is not None
    assert claim.checklist_item_id == iid
    assert claim.polarity == "contradicts"      # a 'yes' defect reading contradicts a clean property
    assert claim.inference_confidence == 1.0     # deterministic = no inference ambiguity
    assert 0.5 <= claim.evidence_quality_confidence <= 1.0


def test_negative_reading_supports_clean_property():
    fmap = load_form_field_map()
    iid = fmap.item_ids()[0]
    claim = map_field_to_claim(iid, "no", fmap)
    assert claim is not None
    assert claim.polarity == "supports"


def test_unknown_value_not_resolved():
    fmap = load_form_field_map()
    iid = fmap.item_ids()[0]
    assert map_field_to_claim(iid, "maybe-ish", fmap) is None


def test_unmapped_item_ignored():
    fmap = load_form_field_map()
    assert map_field_to_claim("not.a.real.item", "yes", fmap) is None


def test_resolved_checklist_gate():
    fmap = load_form_field_map()
    iid = fmap.item_ids()[0]
    # item not in the property's resolved checklist -> ignored
    assert map_field_to_claim(iid, "yes", fmap, resolved_item_ids=set()) is None
    # item in the resolved set -> resolved
    assert map_field_to_claim(iid, "yes", fmap, resolved_item_ids={iid}) is not None


def test_batch_mapping():
    fmap = load_form_field_map()
    ids = fmap.item_ids()[:3]
    readings = [{"item_id": i, "value": "yes"} for i in ids]
    claims = map_fields_to_claims(readings, fmap)
    assert len(claims) == 3
    assert all(c.resolution_state == "answered" for c in claims)


def test_confidence_tracks_locator_quality():
    fmap = load_form_field_map()
    # cited locators should yield higher evidence quality than needs_specimen ones
    cited = [m["item_id"] for m in fmap.raw["mappings"] if m["locator_confidence"] == "cited"]
    needs = [m["item_id"] for m in fmap.raw["mappings"] if m["locator_confidence"] == "needs_specimen"]
    if cited and needs:
        c1 = map_field_to_claim(cited[0], "yes", fmap)
        c2 = map_field_to_claim(needs[0], "yes", fmap)
        assert c1.evidence_quality_confidence > c2.evidence_quality_confidence
