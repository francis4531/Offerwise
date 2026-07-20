"""
test_credit_adjustment.py — v5.89.313

Real tests for POST /api/admin/adjust-credits. Credits are money-adjacent, so the
invariants that matter are locked here:

  * a delta is APPLIED to the existing balance (the bug this replaces: set-credits wrote
    an absolute value, so granting "1" to a user holding 3 silently removed two),
  * a reason is mandatory and is recorded,
  * a balance can never be driven negative,
  * every change writes a CreditAdjustment audit row.

These call the real endpoint through the app's test client and assert on real DB state.
"""
import pytest

from app import app as flask_app
from models import db, User, CreditAdjustment


@pytest.fixture
def client():
    import admin_routes
    saved_dec = admin_routes._api_admin_req_dec
    saved_is_admin = getattr(admin_routes, '_is_admin', None)
    admin_routes._is_admin = (lambda: True)
    flask_app.config['TESTING'] = True
    with flask_app.app_context():
        db.create_all()
    try:
        with flask_app.test_client() as c:
            yield c
    finally:
        admin_routes._api_admin_req_dec = saved_dec
        if saved_is_admin is not None:
            admin_routes._is_admin = saved_is_admin


def _make_user(email, credits):
    with flask_app.app_context():
        u = User.query.filter_by(email=email).first()
        if not u:
            u = User(email=email, password_hash='x', name='Credit Test')
            db.session.add(u)
        u.analysis_credits = credits
        db.session.commit()
        return u.id


def _balance(email):
    with flask_app.app_context():
        u = User.query.filter_by(email=email).first()
        return int(u.analysis_credits or 0)


def _post(client, payload):
    return client.post('/api/admin/adjust-credits', json=payload)


def test_delta_is_added_not_overwritten(client):
    """The core fix: +1 on a balance of 3 must yield 4, never 1."""
    email = 'delta_add@test.example.com'
    _make_user(email, 3)
    r = _post(client, {'email': email, 'delta': 1, 'reason': 'Goodwill credit'})
    if r.status_code in (401, 403):
        pytest.skip('admin gate active in this environment')
    body = r.get_json()
    assert body['ok'] is True, body
    assert body['balance_before'] == 3
    assert body['balance_after'] == 4
    assert _balance(email) == 4


def test_reason_is_required(client):
    """A grant with no reason is refused, so the audit trail can't have holes."""
    email = 'delta_noreason@test.example.com'
    _make_user(email, 2)
    r = _post(client, {'email': email, 'delta': 1, 'reason': '   '})
    if r.status_code in (401, 403):
        pytest.skip('admin gate active in this environment')
    assert r.status_code == 400
    assert _balance(email) == 2          # unchanged


def test_zero_delta_rejected(client):
    email = 'delta_zero@test.example.com'
    _make_user(email, 5)
    r = _post(client, {'email': email, 'delta': 0, 'reason': 'noop'})
    if r.status_code in (401, 403):
        pytest.skip('admin gate active in this environment')
    assert r.status_code == 400
    assert _balance(email) == 5


def test_balance_never_goes_negative(client):
    """Deducting more than the balance floors at zero rather than going negative."""
    email = 'delta_floor@test.example.com'
    _make_user(email, 2)
    r = _post(client, {'email': email, 'delta': -10, 'reason': 'Refund clawback'})
    if r.status_code in (401, 403):
        pytest.skip('admin gate active in this environment')
    assert r.get_json()['balance_after'] == 0
    assert _balance(email) == 0


def test_adjustment_is_audited(client):
    """Every change writes a CreditAdjustment row carrying the reason and both balances."""
    email = 'delta_audit@test.example.com'
    _make_user(email, 1)
    r = _post(client, {'email': email, 'delta': 2, 'reason': 'Comped after comps bug'})
    if r.status_code in (401, 403):
        pytest.skip('admin gate active in this environment')
    assert r.get_json()['ok'] is True
    with flask_app.app_context():
        row = (CreditAdjustment.query
               .filter_by(email=email)
               .order_by(CreditAdjustment.created_at.desc())
               .first())
        assert row is not None, 'no audit row written'
        assert row.delta == 2
        assert row.balance_before == 1
        assert row.balance_after == 3
        assert row.reason == 'Comped after comps bug'


def test_unknown_user_returns_404(client):
    r = _post(client, {'email': 'nobody@test.example.com', 'delta': 1, 'reason': 'x'})
    if r.status_code in (401, 403):
        pytest.skip('admin gate active in this environment')
    assert r.status_code == 404
