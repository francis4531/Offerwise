"""
test_qa_async.py — v5.89.265 async QA runner. QA suites can exceed Cloudflare's
~100s origin timeout; the runner now submits a background job and polls, so no
request stays open long enough to be killed. This verifies the submit/poll flow
end-to-end and the path allowlist.
"""
import time
import pytest
from flask import Flask, jsonify


@pytest.fixture(scope="module")
def app():
    app = Flask(__name__)
    app.config['TESTING'] = True
    ident = lambda f: f  # pass-through admin/dev decorators

    class _StubLimiter:
        def limit(self, *a, **k):
            return (lambda f: f)

    import testing_routes
    # Wire the deferred decorator refs to pass-throughs and register the blueprint
    # on THIS app. We do NOT add routes to testing_bp — in the full suite it's
    # already registered (by app import) and adding a route to a registered
    # blueprint raises. The stub target goes on the APP instead.
    testing_routes._api_admin_required_ref[0] = ident
    testing_routes._dev_only_gate_ref[0] = ident
    testing_routes._admin_required_ref[0] = ident
    testing_routes._api_login_required_ref[0] = ident
    testing_routes._limiter_ref[0] = _StubLimiter()
    app.register_blueprint(testing_routes.testing_bp)

    # stub suite endpoint on the APP (not the blueprint) — reachable by test_client
    @app.route('/api/test/stub', methods=['POST'])
    def _stub():
        return jsonify({'results': [{'name': 'ok', 'passed': True}], 'echo': True})

    return app


def _wait(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f'/api/test/async/status/{job_id}')
        d = r.get_json()
        if d.get('status') != 'running':
            return d
        time.sleep(0.1)
    raise AssertionError('job did not finish in time')


def test_async_submit_and_poll_returns_result(app):
    c = app.test_client()
    start = c.post('/api/test/async/start', json={'path': '/api/test/stub'})
    assert start.status_code == 200
    job_id = start.get_json()['job_id']
    assert job_id
    done = _wait(c, job_id)
    assert done['status'] == 'complete'
    assert done['status_code'] == 200
    assert done['result']['echo'] is True
    assert done['result']['results'][0]['passed'] is True


def test_path_allowlist_rejects_non_test_paths(app):
    c = app.test_client()
    r = c.post('/api/test/async/start', json={'path': '/api/admin/secrets'})
    assert r.status_code == 400
    r2 = c.post('/api/test/async/start', json={'path': '/api/test/async/start'})
    assert r2.status_code == 400  # can't recurse into itself


def test_unknown_job_is_404(app):
    c = app.test_client()
    r = c.get('/api/test/async/status/deadbeef')
    assert r.status_code == 404
    assert r.get_json()['status'] == 'unknown'
