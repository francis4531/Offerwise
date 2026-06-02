"""
Unit tests for the Phase 0b reasoning-tier persistence (Finding/Claim/Issue).

Runs against an in-memory SQLite DB with a minimal Flask app context, so it
does not require the full production stack. Verifies:
  - the models import and create_all builds all five tables
  - the many-to-many relationships link Finding <-> Claim <-> Issue
  - the role-tagged claim_findings association works
  - the Alembic migration imports with the correct revision chain
"""
import pytest

from flask import Flask


@pytest.fixture
def app_ctx():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    from models import db
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield db
        db.session.remove()
        db.drop_all()


def test_models_import():
    from models import Finding, Claim, Issue, claim_findings, issue_claims  # noqa: F401


def test_create_all_builds_tables(app_ctx):
    import sqlalchemy as sa
    insp = sa.inspect(app_ctx.engine)
    tables = set(insp.get_table_names())
    for t in ('reasoning_findings', 'reasoning_claims', 'reasoning_issues',
              'claim_findings', 'issue_claims'):
        assert t in tables, f"missing table {t}"


def test_claim_links_findings_with_roles(app_ctx):
    from models import Finding, Claim
    db = app_ctx
    f1 = Finding(source_document='inspection', raw_text='aluminum branch wiring on 120v circuits',
                 modality='freetext_narrative')
    f2 = Finding(source_document='disclosure', raw_text='seller marked no electrical issues',
                 modality='structured_form_field')
    c = Claim(checklist_item_id='electrical.wiring_material', checklist_version='v0.5',
              resolved_value='aluminum branch wiring present', resolution_state='contradiction',
              inference_confidence=0.8, evidence_quality_confidence=0.9)
    db.session.add_all([f1, f2, c])
    db.session.flush()
    c.findings.append(f1)
    c.findings.append(f2)
    db.session.commit()
    assert c.findings.count() == 2
    # back-reference works
    assert f1.claims.first().checklist_item_id == 'electrical.wiring_material'


def test_issue_clusters_claims(app_ctx):
    from models import Claim, Issue
    db = app_ctx
    c1 = Claim(checklist_item_id='electrical.panel_brand_safety', checklist_version='v0.5')
    c2 = Claim(checklist_item_id='electrical.wiring_material', checklist_version='v0.5')
    iss = Issue(decision_class='negotiation_lever', silent_hazard_flag=True,
                severity='major', is_reserve=False, title='Electrical modernization')
    db.session.add_all([c1, c2, iss])
    db.session.flush()
    iss.claims.append(c1)
    iss.claims.append(c2)
    db.session.commit()
    assert iss.claims.count() == 2
    assert c1.issues.first().decision_class == 'negotiation_lever'


def test_dual_confidence_fields_persist(app_ctx):
    from models import Claim
    db = app_ctx
    c = Claim(checklist_item_id='roof.age', checklist_version='v0.5',
              inference_confidence=0.7, evidence_quality_confidence=0.5)
    db.session.add(c)
    db.session.commit()
    got = Claim.query.filter_by(checklist_item_id='roof.age').first()
    assert got.inference_confidence == 0.7
    assert got.evidence_quality_confidence == 0.5


def test_migration_revision_chain():
    # Parse the migration's revision identifiers without importing it (the module
    # imports `alembic.op`, which need not be installed in the test env).
    import os, re
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'alembic', 'versions', 'b2d5e8a1c4f7_v5_89_86_reasoning_tiers.py')
    src = open(path, encoding='utf-8').read()
    rev = re.search(r"^revision:\s*str\s*=\s*'([^']+)'", src, re.M)
    down = re.search(r"^down_revision:[^=]*=\s*'([^']+)'", src, re.M)
    assert rev and rev.group(1) == 'b2d5e8a1c4f7'
    assert down and down.group(1) == 'a1c4e7f9b2d6'
    assert 'def upgrade()' in src and 'def downgrade()' in src
    # all five tables are created and dropped
    for t in ('reasoning_findings', 'reasoning_claims', 'reasoning_issues',
              'claim_findings', 'issue_claims'):
        assert t in src


def _pendleton_readings():
    from reasoning.inspection_parser import parse_inspection_text, load_inspection_field_map
    txt = open('test_inspection_parser.py').read().split('PENDLETON_DETAIL = """')[1].split('"""')[0]
    return parse_inspection_text(txt, load_inspection_field_map())


def test_pipeline_persists_full_graph(app_ctx):
    """persist=True writes Finding/Claim/Issue rows with ids, costs, and links."""
    from reasoning import run_pipeline
    from models import Finding, Claim, Issue
    res = run_pipeline([], 'CA', 'SFH', inspection_readings=_pendleton_readings(),
                       zip_code='95148', property_year_built=1977,
                       persist=True, analysis_id=42, property_id=7)
    assert res.persisted and res.persisted['issues'] >= 1
    assert Finding.query.count() >= 1
    assert Claim.query.count() >= 1
    assert Issue.query.count() >= 1
    iss = Issue.query.filter_by(silent_hazard_flag=True).first()
    assert iss is not None
    assert iss.analysis_id == 42 and iss.property_id == 7
    assert iss.claims.count() >= 1  # issue<->claim link persisted


def test_persist_is_atomic_on_failure(app_ctx):
    """A mid-write failure inside the REAL _persist must roll back (no orphans)."""
    from reasoning import run_pipeline
    from reasoning import pipeline as P
    from models import Finding, Claim, Issue, db
    readings = _pendleton_readings()
    run_pipeline([], 'CA', 'SFH', inspection_readings=readings, persist=True)
    before = (Finding.query.count(), Claim.query.count(), Issue.query.count())

    # Force a failure partway through the REAL _persist by making Issue() raise
    # AFTER some Finding/Claim rows have been added+flushed. _persist must catch,
    # roll back, and re-raise -> zero new rows.
    import models as M
    real_issue = M.Issue
    class BoomIssue:
        def __init__(self, *a, **k):
            raise RuntimeError("simulated mid-write failure")
    M.Issue = BoomIssue
    try:
        run_pipeline([], 'CA', 'SFH', inspection_readings=readings, persist=True)
    except Exception:
        pass
    finally:
        M.Issue = real_issue
    after = (Finding.query.count(), Claim.query.count(), Issue.query.count())
    assert before == after, f"orphan rows left behind: {before} -> {after}"
