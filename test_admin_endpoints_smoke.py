"""
test_admin_endpoints_smoke.py — v5.89.291. Real smoke tests for the admin endpoints
added across this arc, so the API-coverage gate has genuine route references (not
comment pings) and these routes can't silently break.

v5.89.291 rewrite: the original built a throwaway Flask app and called db.init_app()
on the SHARED models.db, registering a second app on the global SQLAlchemy instance for
the rest of the pytest session. In the full-suite ordering that poisoned a later test
("The current Flask app is not registered with this 'SQLAlchemy' instance ... multiple
'SQLAlchemy' instances?" -> PendingRollbackError in test_bulk_regenerate). It also
re-registered routes the real app already exposes.

This version uses the REAL app and its ALREADY-REGISTERED routes via its test client,
bypassing the admin gate for the test and restoring it afterwards. No global DB state is
mutated; nothing is re-registered.
"""
import pytest


@pytest.fixture
def client():
    import admin_routes
    from app import app as real_app

    saved_admin_req = admin_routes._api_admin_required
    saved_is_admin = admin_routes._is_admin
    admin_routes._api_admin_required = (lambda f: f)
    admin_routes._is_admin = (lambda: True)
    real_app.config['TESTING'] = True
    try:
        with real_app.test_client() as c:
            yield c
    finally:
        admin_routes._api_admin_required = saved_admin_req
        admin_routes._is_admin = saved_is_admin


def _ok(resp):
    """200/401/403 all prove the route EXISTS and didn't 500."""
    assert resp.status_code in (200, 401, 403), resp.status_code
    return resp


def test_latency_breakdown_endpoint_does_not_crash(client):
    _ok(client.get('/api/admin/latency-breakdown'))


def test_shadow_samples_endpoint_does_not_crash(client):
    _ok(client.get('/api/admin/reasoning/shadow-samples?state=CA'))


def test_metrics_snapshot_curated_and_no_cost_leak(client):
    r = _ok(client.get('/api/admin/metrics-snapshot'))
    if r.status_code != 200:
        pytest.skip('admin key gate active in this environment')
    d = r.get_json()
    assert all(k in d for k in ('traction', 'engineering', 'coverage', 'data', 'moat'))
    assert not any(k in d for k in ('costs', 'ad_spend', 'cac', 'infra'))


def test_generate_access_link_rejects_bad_email(client):
    r = _ok(client.post('/api/admin/access-requests/generate-link',
                        json={'email': 'not-an-email'}))
    if r.status_code != 200:
        pytest.skip('admin key gate active in this environment')
    assert r.get_json()['ok'] is False
