"""
Unit tests for the Phase 0 reasoning layer (checklist loader + composition).

Covers:
  - asset loads and validates against the Section 3.5 anatomy
  - composition resolves the right item set per jurisdiction + property type
  - national base is jurisdiction-agnostic (unserved state -> base only)
  - state overlay adds CA-specific items and refines base items (provenance + value)
  - municipal overlay refines with its own provenance
  - the no-silent-delete invariant is enforced
  - applicability filters by property type

These are pure-logic tests: no DB, no network, no app context.
"""
import os
import pytest

from reasoning import load_checklist, compose
from reasoning.checklist_loader import ChecklistValidationError, DEFAULT_CHECKLIST_PATH
from reasoning.composition import CompositionError, _jurisdiction_tokens


# ── loading + validation ──────────────────────────────────────────────────

def test_asset_file_present():
    assert os.path.exists(DEFAULT_CHECKLIST_PATH), "bundled checklist asset missing"


def test_load_and_validate_passes():
    asset = load_checklist()  # validate=True by default
    assert len(asset.national_base) == 58
    assert "CA" in asset.state_overlays
    assert "CA:santa_clara:san_jose" in asset.municipal_overlays


def test_validation_rejects_bad_enum():
    asset = load_checklist(validate=False)
    bad = dict(asset.national_base[0].raw)
    bad["importance"] = "extremely-major"  # not in the allowed set
    from reasoning.checklist_loader import _coerce_item
    with pytest.raises(ChecklistValidationError):
        _coerce_item(bad, "national_base", validate=True)


def test_cost_impact_bool_is_coerced():
    # YAML coerces bare yes/no to bool; loader must normalize back to the vocab.
    from reasoning.checklist_loader import _coerce_item
    item = _coerce_item(
        {"id": "x", "cost_impact": True}, "national_base", validate=False
    )
    assert item.cost_impact == "yes"


# ── composition: jurisdiction scope ────────────────────────────────────────

def test_national_only_for_unserved_state():
    # Texas has no overlay authored yet -> national base only (national scope works)
    national = compose("*", "SFH")
    texas = compose("TX", "SFH")
    assert len(national.items) == 57
    assert len(texas.items) == 57
    assert set(national.ids()) == set(texas.ids())


def test_ca_overlay_adds_items():
    ca = compose("CA", "SFH")
    national = compose("*", "SFH")
    assert len(ca.items) == 72
    ca_only = set(ca.ids()) - set(national.ids())
    assert len(ca_only) == 15
    # a known CA-only item
    assert "ca.flood_hazard_zone" in ca_only


def test_property_type_filter():
    # condo resolves to fewer items than SFH (SFH-only items excluded)
    ca_sfh = compose("CA", "SFH")
    ca_condo = compose("CA", "condo")
    assert len(ca_condo.items) < len(ca_sfh.items)


# ── composition: refine + provenance ───────────────────────────────────────

def test_state_refine_elevates_compliance_basis():
    # CA elevates smoke detectors from best_practice -> legal_requirement
    national = compose("*", "SFH").by_id()["safety.smoke_detectors"]
    ca = compose("CA", "SFH").by_id()["safety.smoke_detectors"]
    assert national.compliance_basis.get("basis") == "best_practice"
    assert ca.compliance_basis.get("basis") == "legal_requirement"
    assert "state_overlay:CA" in ca.refined_by


def test_municipal_refine_carries_provenance():
    sj = compose("CA:santa_clara:san_jose", "SFH").by_id()
    item = sj["ca.earthquake_fault_zone"]
    assert "municipal_overlay:CA:santa_clara:san_jose" in item.refined_by


def test_resolution_trace_is_provenance():
    # every item records the layer it came from (audit trail, Commitment 2.3)
    for it in compose("CA", "SFH").items:
        assert it.source_layer  # non-empty


# ── composition: invariants ────────────────────────────────────────────────

def test_no_silent_delete_rejected():
    asset = load_checklist()
    # inject an illegal delete directive into the CA overlay and expect refusal
    asset.state_overlays["CA"] = dict(asset.state_overlays["CA"])
    asset.state_overlays["CA"]["remove"] = ["roof.age"]
    with pytest.raises(CompositionError):
        compose("CA", "SFH", asset=asset)


def test_jurisdiction_tokenizer():
    state, muni = _jurisdiction_tokens("CA:santa_clara:san_jose")
    assert state == "CA"
    assert muni == ["CA:santa_clara", "CA:santa_clara:san_jose"]


def test_unique_ids_in_resolved():
    ids = compose("CA:santa_clara:san_jose", "SFH").ids()
    assert len(ids) == len(set(ids))


# ── public self-check (used by the admin tile) ─────────────────────────────

def test_reasoning_self_check_shape():
    from reasoning_health import reasoning_self_check
    result = reasoning_self_check()
    assert result["ok"] is True
    assert result["national_base_count"] == 58
    assert result["ca_sfh_count"] == 72
    assert result["invariants_passed"] is True


def test_self_check_runs_acceptance_fixture():
    # the self-check must run the REAL specimen end-to-end, not just synthetic
    from reasoning_health import reasoning_self_check
    acc = reasoning_self_check().get("acceptance_fixture")
    assert acc is not None
    assert acc["ok"] is True
    assert acc["all_on_checklist"] is True
    assert acc["readings"] > 0
    assert acc["concern_claims"] == 0  # the specimen disclosed nothing actionable


def test_self_check_reports_honest_coverage(monkeypatch):
    # the tile must tell the truth about what's wired vs not
    from reasoning_health import reasoning_self_check
    # the inspection moat, cost bands, and persistence are all wired now
    cov = reasoning_self_check().get("coverage")
    assert cov is not None
    assert cov["inspection_input"] is True
    assert cov["cost_bands_wired"] is True
    assert cov["persistence_wired"] is True
    # persistence_live tracks the flag: ON by default, off when explicitly disabled
    monkeypatch.setenv("OFFERWISE_REASONING_PERSIST", "0")
    cov_off = reasoning_self_check().get("coverage")
    assert cov_off["persistence_live"] is False
    monkeypatch.setenv("OFFERWISE_REASONING_PERSIST", "1")
    cov_on = reasoning_self_check().get("coverage")
    assert cov_on["persistence_live"] is True
    assert "INTEGRATION" in cov_on["honest_status"]
