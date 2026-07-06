"""
test_analyze_async.py — v5.89.268. /api/analyze now runs as a background job so the
buyer flow can't be killed by Cloudflare's ~100s timeout ("Analysis failed: Failed
to fetch" while the server actually finished). Verifies: the background call is
authenticated AS THE USER (the risky part — session-based auth replay), submit/poll
returns the persisted result, idempotency dedups a repeat submit, and cross-user
polling is forbidden.
"""
import time
import pytest
from flask import Flask, jsonify, request
from flask_login import LoginManager, UserMixin, current_user


class _U(UserMixin):
    def __init__(self, uid): self.id = uid


@pytest.fixture(scope="module")
def app():
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.secret_key = 'test-secret'
    lm = LoginManager(); lm.init_app(app)

    @lm.user_loader
    def _load(uid):
        return _U(int(uid))

    import analysis_routes
    analysis_routes._api_login_required_ref[0] = (lambda f: f)  # pass-through; Flask-Login still populates current_user

    app.add_url_rule('/api/analyze/async', 'aas', analysis_routes.analyze_async_start, methods=['POST'])
    app.add_url_rule('/api/analyze/status/<job_id>', 'aast', analysis_routes.analyze_async_status, methods=['GET'])

    # stub target: proves the background call is authenticated as the user
    @app.route('/api/analyze', methods=['POST'])
    def _stub_analyze():
        return jsonify({'ok': True,
                        'auth_user': current_user.id if current_user.is_authenticated else None,
                        'echo': request.get_json(silent=True)})
    # reset the shared job store between test modules
    analysis_routes._ANALYSIS_JOBS.clear()
    return app


def _as_user(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s['_user_id'] = str(uid); s['_fresh'] = True
    return c


def _wait(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = client.get(f'/api/analyze/status/{job_id}').get_json()
        if d.get('status') != 'running':
            return d
        time.sleep(0.1)
    raise AssertionError('job did not finish')


def test_background_call_is_authenticated_as_user(app):
    c = _as_user(app, 7)
    start = c.post('/api/analyze/async', json={'property_address': '2839 Pendleton Dr', 'property_price': 900000})
    assert start.status_code == 200
    job_id = start.get_json()['job_id']
    done = _wait(c, job_id)
    assert done['status'] == 'complete'
    assert done['result']['auth_user'] == 7          # replayed auth = the right user
    assert done['result']['echo']['property_price'] == 900000


def test_idempotent_repeat_returns_same_job(app):
    c = _as_user(app, 11)
    body = {'property_address': '1 Main St', 'property_price': 500000}
    j1 = c.post('/api/analyze/async', json=body).get_json()['job_id']
    _wait(c, j1)
    j2 = c.post('/api/analyze/async', json=body).get_json()  # identical, within dedup window
    assert j2['job_id'] == j1 and j2.get('idempotent') is True


def test_different_request_is_a_new_job(app):
    c = _as_user(app, 11)
    j1 = c.post('/api/analyze/async', json={'property_address': 'A', 'property_price': 1}).get_json()['job_id']
    j2 = c.post('/api/analyze/async', json={'property_address': 'B', 'property_price': 2}).get_json()['job_id']
    assert j1 != j2


def test_cross_user_status_is_forbidden(app):
    owner = _as_user(app, 20)
    job_id = owner.post('/api/analyze/async', json={'property_address': 'X', 'property_price': 3}).get_json()['job_id']
    other = _as_user(app, 21)
    assert other.get(f'/api/analyze/status/{job_id}').status_code == 403


def test_unknown_job_is_404(app):
    c = _as_user(app, 30)
    assert c.get('/api/analyze/status/nope').status_code == 404
