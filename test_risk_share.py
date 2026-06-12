"""Tests for the shareable risk-check viral loop: /r/<token> page + OG card."""
import os
os.environ['FLASK_ENV'] = 'testing'
os.environ['DATABASE_URL'] = 'sqlite:///test_risk_share.db'
os.environ['RATELIMIT_ENABLED'] = 'false'

import json  # noqa: E402
import pytest  # noqa: E402
from app import app, _risk_share_headline  # noqa: E402
from models import db, SharedRiskCheck  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.app_context():
        db.create_all()
        if not SharedRiskCheck.query.filter_by(token='testtok123').first():
            src = SharedRiskCheck(
                token='testtok123', address='128 Maple Ave, San Jose, CA 95112',
                city='San Jose', state='CA', risk_grade='D', risk_exposure=47500, risk_count=3,
                headline="This home may sit in a flood zone the seller won't mention",
                result_json=json.dumps({'risks': [{'icon': '🌊', 'title': 'Flood Zone',
                    'level': 'HIGH', 'detail': 'FEMA Zone AE',
                    'seller_hide': 'Sellers routinely check No on flooding.'}]}))
            db.session.add(src)
            db.session.commit()
    return app.test_client()


def test_share_page_renders_with_og(client):
    r = client.get('/r/testtok123')
    assert r.status_code == 200
    assert b'og:image' in r.data
    assert b'flood zone' in r.data
    assert b'/r/testtok123/card.png' in r.data


def test_share_card_is_png(client):
    r = client.get('/r/testtok123/card.png')
    assert r.status_code == 200
    assert r.headers.get('Content-Type') == 'image/png'
    assert r.data[:8] == b'\x89PNG\r\n\x1a\n'
    assert len(r.data) > 5000


def test_missing_token_redirects(client):
    r = client.get('/r/nope')
    assert r.status_code in (301, 302)
    assert '/risk-check' in r.headers.get('Location', '')


def test_missing_card_404(client):
    assert client.get('/r/nope/card.png').status_code == 404


def test_headline_picks_flood():
    h = _risk_share_headline({'risks': [{'title': 'Flood Zone'}], 'risk_count': 1})
    assert 'flood' in h.lower()


def test_headline_no_risks():
    h = _risk_share_headline({'risks': [], 'risk_count': 0})
    assert 'government databases' in h.lower()


# ── Scout on the Risk Check page (/api/risk-check/chat) ──────────────────────
import ask_engine  # noqa: E402


def test_chat_requires_message(client):
    assert client.post('/api/risk-check/chat', json={}).status_code == 400


def test_chat_general_mode(client, monkeypatch):
    # echo the grounding context back so we can see which mode was used
    monkeypatch.setattr(ask_engine, 'grounded_answer', lambda q, ctx, **k: ctx)
    r = client.post('/api/risk-check/chat', json={'message': 'what does this check?'})
    assert r.status_code == 200
    ans = r.get_json()['answer']
    assert 'Risk Check' in ans and 'OFFERWISE RISK SCAN for this property' not in ans


def test_chat_token_grounds_on_result(client, monkeypatch):
    monkeypatch.setattr(ask_engine, 'grounded_answer', lambda q, ctx, **k: ctx)
    r = client.post('/api/risk-check/chat', json={'message': 'how bad is it?', 'token': 'testtok123'})
    assert r.status_code == 200
    ans = r.get_json()['answer']
    assert 'Flood Zone' in ans and 'OFFERWISE RISK SCAN' in ans
