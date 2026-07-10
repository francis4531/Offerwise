"""
test_admin_endpoints_smoke.py — v5.89.273. Real smoke tests for the admin endpoints
added across this arc, so the API-coverage gate has genuine references (not comment
pings) and these routes can't silently break. Each registers the view on a fresh app
with a pass-through admin gate and an in-memory DB, and asserts a clean empty-state
response.
"""
import pytest
from flask import Flask


@pytest.fixture
def app_db():
    import admin_routes
    from models import db
    admin_routes._api_admin_required = (lambda f: f)
    admin_routes._is_admin = (lambda: True)
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()
    return app


def test_latency_breakdown_endpoint_empty_ok(app_db):
    import admin_routes
    app_db.add_url_rule('/api/admin/latency-breakdown', 'lat',
                        admin_routes.api_latency_breakdown, methods=['GET'])
    r = app_db.test_client().get('/api/admin/latency-breakdown')
    assert r.status_code == 200            # empty DB -> clean empty breakdown, no crash


def test_shadow_samples_endpoint_empty_ok(app_db):
    import admin_routes
    app_db.add_url_rule('/api/admin/reasoning/shadow-samples', 'ss',
                        admin_routes.admin_reasoning_shadow_samples, methods=['GET'])
    r = app_db.test_client().get('/api/admin/reasoning/shadow-samples?state=CA')
    assert r.status_code == 200
    d = r.get_json()
    assert d.get('ok') is True and d.get('samples') == []   # no rows yet


def test_metrics_snapshot_endpoint_empty_ok(app_db):
    import admin_routes
    app_db.add_url_rule('/api/admin/metrics-snapshot', 'metrics',
                        admin_routes.api_metrics_snapshot, methods=['GET'])
    r = app_db.test_client().get('/api/admin/metrics-snapshot')
    assert r.status_code == 200
    d = r.get_json()
    # curated snapshot present, and it does NOT leak cost/internal fields
    assert all(k in d for k in ('traction','engineering','coverage','data','moat'))
    assert d['traction']['signups'] == 0            # empty DB -> zeros, no crash
    assert not any(k in d for k in ('costs', 'ad_spend', 'cac', 'infra'))


def test_generate_access_link_validates_email(app_db):
    import admin_routes
    app_db.add_url_rule('/api/admin/access-requests/generate-link', 'vip',
                        admin_routes.api_generate_access_link, methods=['POST'])
    r = app_db.test_client().post('/api/admin/access-requests/generate-link',
                                  json={'email': 'not-an-email'})
    assert r.status_code == 200
    assert r.get_json()['ok'] is False   # invalid email rejected before any DB write
