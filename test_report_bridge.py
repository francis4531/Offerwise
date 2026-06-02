"""
Tests for the Phase 1d report bridge (reasoning section in the buyer result).

Verifies the flag gating (off by default = live path untouched), the additive
attach, and that we never fabricate a section when there's no real input.
"""
import os
import pytest

from reasoning.report_bridge import (
    attach_reasoning_if_enabled, build_reasoning_section, reasoning_in_report_enabled,
)
from reasoning.tds_parser import load_specimen_field_state


@pytest.fixture
def flag_off():
    prev = os.environ.pop("OFFERWISE_REASONING_IN_REPORT", None)
    yield
    if prev is not None:
        os.environ["OFFERWISE_REASONING_IN_REPORT"] = prev


@pytest.fixture
def flag_on():
    prev = os.environ.get("OFFERWISE_REASONING_IN_REPORT")
    os.environ["OFFERWISE_REASONING_IN_REPORT"] = "1"
    yield
    if prev is None:
        os.environ.pop("OFFERWISE_REASONING_IN_REPORT", None)
    else:
        os.environ["OFFERWISE_REASONING_IN_REPORT"] = prev


def test_flag_off_by_default(flag_off):
    assert reasoning_in_report_enabled() is False


def test_flag_off_leaves_result_untouched(flag_off):
    result = {"risk_score": 42, "property_address": "x"}
    before = dict(result)
    attach_reasoning_if_enabled(result, tds_field_state=load_specimen_field_state())
    assert result == before  # nothing added, nothing changed


def test_flag_on_attaches_section_for_real_specimen(flag_on):
    result = {"risk_score": 42}
    attach_reasoning_if_enabled(result, tds_field_state=load_specimen_field_state())
    assert "reasoning" in result
    r = result["reasoning"]
    assert r["checklist_version"] == "v0.5"
    assert len(r["claims"]) >= 8
    assert "offer" in r
    # existing field preserved
    assert result["risk_score"] == 42


def test_no_fabrication_without_input(flag_on):
    # no field state -> no section (never invent claims)
    result = {"risk_score": 1}
    attach_reasoning_if_enabled(result, tds_field_state=None)
    assert "reasoning" not in result


def test_build_section_shape_for_specimen():
    section = build_reasoning_section(tds_field_state=load_specimen_field_state())
    assert section is not None
    assert set(["claims", "issues", "offer", "checklist_version"]).issubset(section.keys())
    # the clean specimen -> support claims, no concern claims
    assert all(c["polarity"] in ("supports", "contradicts") for c in section["claims"])


def test_persist_decoupled_from_buyer_exposure(monkeypatch, tmp_path):
    """Persist ON + buyer flag OFF: rows are written but no reasoning key is
    exposed. This is the intended production state."""
    monkeypatch.delenv("OFFERWISE_REASONING_IN_REPORT", raising=False)
    monkeypatch.setenv("OFFERWISE_REASONING_PERSIST", "1")
    from flask import Flask
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    from models import db, Finding, Claim, Issue
    db.init_app(app)
    with app.app_context():
        db.create_all()
        from reasoning.report_bridge import attach_reasoning_if_enabled
        from reasoning.inspection_parser import parse_inspection_text, load_inspection_field_map
        txt = open("test_inspection_parser.py").read().split('PENDLETON_DETAIL = """')[1].split('"""')[0]
        readings = parse_inspection_text(txt, load_inspection_field_map())
        rd = {"analysis_id": 3, "property_id": 9, "keep": "me"}
        attach_reasoning_if_enabled(rd, inspection_readings=readings,
                                    zip_code="95148", property_year_built=1977,
                                    analysis_id=3, property_id=9)
        assert "reasoning" not in rd          # buyer flag off -> not exposed
        assert rd["keep"] == "me"             # existing fields untouched
        assert Issue.query.count() >= 1       # persist flag on -> rows written


def test_buyer_flag_off_persist_off_is_noop(monkeypatch):
    """Both flags off: nothing happens, result untouched."""
    monkeypatch.delenv("OFFERWISE_REASONING_IN_REPORT", raising=False)
    monkeypatch.setenv("OFFERWISE_REASONING_PERSIST", "0")
    from reasoning.report_bridge import attach_reasoning_if_enabled
    rd = {"keep": "me"}
    attach_reasoning_if_enabled(rd, inspection_readings=[{"item_id": "x", "value": "yes"}])
    assert rd == {"keep": "me"}
