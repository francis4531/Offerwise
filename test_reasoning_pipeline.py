"""
Unit tests for the reasoning pipeline orchestrator.

Pure-mode tests need no DB. The persisted-mode test uses an in-memory SQLite DB
with a minimal Flask app context to verify rows are written and linked.
"""
import pytest
from flask import Flask

from reasoning import run_pipeline, load_form_field_map


def _readings(n_yes=3, n_no=2):
    ids = load_form_field_map().item_ids()
    out = []
    for i in range(n_yes):
        out.append({"item_id": ids[i], "value": "yes", "source_form": "TDS",
                    "locator": "TDS X", "raw_text": f"{ids[i]}: yes"})
    for i in range(n_yes, n_yes + n_no):
        out.append({"item_id": ids[i], "value": "no", "raw_text": f"{ids[i]}: no"})
    return out


# ── pure mode ──────────────────────────────────────────────────────────────

def test_pure_pipeline_produces_claims_and_issues():
    r = run_pipeline(_readings(3, 2), "CA", "SFH", persist=False)
    s = r.summary()
    assert s["claims"] == 5          # all 5 recognized readings -> claims
    assert s["issues"] >= 1          # the 'yes' (concern) claims cluster into issues
    assert s["resolved_items"] == 72
    assert s["persisted"] == {}      # nothing persisted in pure mode


def test_version_flows_through():
    r = run_pipeline(_readings(1, 0), "CA", "SFH")
    assert r.checklist_version == "v0.5"


def test_unknown_value_dropped():
    ids = load_form_field_map().item_ids()
    r = run_pipeline([{"item_id": ids[0], "value": "huh", "raw_text": "x"}], "CA", "SFH")
    assert len(r.claims) == 0


def test_offdomain_item_gated_out():
    # an item id not in the form-field map is ignored
    r = run_pipeline([{"item_id": "not.real", "value": "yes", "raw_text": "x"}], "CA", "SFH")
    assert len(r.claims) == 0


def test_clean_readings_make_no_issues():
    r = run_pipeline(_readings(0, 4), "CA", "SFH")  # all 'no' = clean
    assert len(r.claims) == 4
    assert len(r.issues_result.issues) == 0


# ── persisted mode ─────────────────────────────────────────────────────────

@pytest.fixture
def app_ctx():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    from models import db
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield db
        db.session.remove()
        db.drop_all()


def test_persisted_pipeline_writes_rows(app_ctx):
    from models import Finding, Claim, Issue
    r = run_pipeline(_readings(3, 1), "CA", "SFH", persist=True,
                     analysis_id=None, property_id=None)
    assert r.persisted["claims"] == 4
    assert r.persisted["findings"] == 4
    assert r.persisted["issues"] >= 1
    # rows actually in the DB
    assert Claim.query.count() == 4
    assert Finding.query.count() == 4
    assert Issue.query.count() == r.persisted["issues"]
    # linkage: each claim has at least one finding
    for c in Claim.query.all():
        assert c.findings.count() >= 1
    # at least one issue links at least one claim
    assert any(iss.claims.count() >= 1 for iss in Issue.query.all())
