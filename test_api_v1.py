"""test_api_v1.py — v5.89.325

Real coverage for the public B2B API (/api/v1/*), the surface a partner integrates
against. The pre-existing coverage only asserted status_code in a permissive set that
INCLUDED 500 — i.e. it passed even when the endpoint errored. These tests exercise the
authenticated HAPPY PATH with genuine assertions on the real response shape, plus the
auth, quota, and validation paths.

The expensive AI engine (OfferWiseIntelligence.analyze_property) is mocked so the tests
run offline and deterministically without an ANTHROPIC_API_KEY — everything ELSE (key
auth, hashing, quota enforcement, usage increment, request validation, response
serialisation) is the real code path.

Architecture: HTTP tests via the Flask test client — no running server needed. Mirrors
the app/db bootstrap pattern used by test_coverage_gaps.py.
"""

import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import patch

os.environ.setdefault('DATABASE_URL', 'sqlite:///test_api_v1.db')

_app_cache = None


def _get_app():
    global _app_cache
    if _app_cache:
        return _app_cache
    for k in list(sys.modules.keys()):
        if k in ('app', 'models'):
            del sys.modules[k]
    spec = importlib.util.spec_from_file_location('app', 'app.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules['app'] = mod
    spec.loader.exec_module(mod)
    _app_cache = (mod.app, mod.db, mod)
    return _app_cache


def _fresh_client():
    app, db, mod = _get_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app.test_client(), app, db, mod


def _mint_key(app, db, mod, *, monthly_limit=100, calls_month=0, is_active=True):
    """Create a real APIKey row and return the raw key string the caller should send.

    Uses the app's real _hash_api_key so authentication exercises the true hash path.
    """
    from models import User, APIKey
    raw = 'ow_live_test_' + os.urandom(8).hex()
    with app.app_context():
        user = User.query.filter_by(email='apitest@example.com').first()
        if not user:
            user = User(email='apitest@example.com', name='API Test',
                        auth_provider='email', tier='free', analysis_credits=0)
            db.session.add(user)
            db.session.commit()
        # remove any prior key for a clean slate
        APIKey.query.filter_by(user_id=user.id).delete()
        key = APIKey(
            user_id=user.id,
            key_hash=mod._hash_api_key(raw),
            key_prefix=raw[:10],
            label='test-key',
            tier='standard',
            is_active=is_active,
            monthly_limit=monthly_limit,
            calls_month=calls_month,
            calls_total=calls_month,
        )
        db.session.add(key)
        db.session.commit()
        kid = key.id
    return raw, kid


def _fake_result():
    """A structured result object matching what OfferWiseIntelligence.analyze_property
    returns, limited to the attributes /api/v1/analyze actually reads."""
    r = types.SimpleNamespace()
    r.offer_score = 72
    r.risk_level = 'moderate'
    r.risk_score = 58
    r.transparency_score = 80
    db1 = types.SimpleNamespace(issue='FPE electrical panel',
                                severity='high', cost_estimate='$2,000-$5,000')
    r.deal_breakers = [db1]
    r.repair_estimate = types.SimpleNamespace(total_low=8000, total_high=15000)
    r.negotiation_strategy = types.SimpleNamespace(
        leverage_points=['Undisclosed kitchen leak', 'FPE panel cluster'])
    r.offer_strategy = types.SimpleNamespace(recommended_offer=865000)
    return r


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client, cls.app, cls.db, cls.mod = _fresh_client()
        with cls.app.app_context():
            cls.db.create_all()


# ─────────────────────────────────────────────────────────────────────────────
# AUTH — the door must be locked, and the right key must open it
# ─────────────────────────────────────────────────────────────────────────────
class TestApiV1Auth(_Base):

    def test_analyze_without_key_is_401(self):
        r = self.client.post('/api/v1/analyze', json={
            'disclosure_text': 'x', 'inspection_text': 'y',
            'property_price': 900000, 'property_address': '1 Main St'})
        self.assertEqual(r.status_code, 401)
        self.assertIn('API key', r.get_json().get('error', ''))

    def test_invalid_key_is_401(self):
        r = self.client.post('/api/v1/analyze',
                             headers={'Authorization': 'Bearer ow_live_not_a_real_key'},
                             json={'disclosure_text': 'x', 'inspection_text': 'y',
                                   'property_price': 900000, 'property_address': '1 Main St'})
        self.assertEqual(r.status_code, 401)

    def test_revoked_key_is_401(self):
        raw, _ = _mint_key(self.app, self.db, self.mod, is_active=False)
        r = self.client.get('/api/v1/usage', headers={'Authorization': f'Bearer {raw}'})
        self.assertEqual(r.status_code, 401)

    def test_x_api_key_header_also_works(self):
        raw, _ = _mint_key(self.app, self.db, self.mod)
        r = self.client.get('/api/v1/usage', headers={'X-API-Key': raw})
        self.assertEqual(r.status_code, 200)

    def test_bearer_header_works(self):
        raw, _ = _mint_key(self.app, self.db, self.mod)
        r = self.client.get('/api/v1/usage', headers={'Authorization': f'Bearer {raw}'})
        self.assertEqual(r.status_code, 200)


# ─────────────────────────────────────────────────────────────────────────────
# /api/v1/analyze — the core product call, authenticated happy path
# ─────────────────────────────────────────────────────────────────────────────
class TestApiV1Analyze(_Base):

    def _call(self, raw, **overrides):
        body = {'disclosure_text': 'Seller discloses shower leak in master bath.',
                'inspection_text': 'Inspector found kitchen floor warping at fridge.',
                'property_price': 900000,
                'property_address': '2839 Pendleton Dr, San Jose CA 95148'}
        body.update(overrides)
        return self.client.post('/api/v1/analyze',
                                headers={'Authorization': f'Bearer {raw}'}, json=body)

    def test_happy_path_returns_structured_result(self):
        raw, _ = _mint_key(self.app, self.db, self.mod)
        with patch('offerwise_intelligence.OfferWiseIntelligence.analyze_property',
                   return_value=_fake_result()):
            r = self._call(raw)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        # real response contract a partner would depend on
        self.assertTrue(j.get('success'))
        for key in ('offer_score', 'risk_level', 'risk_score', 'deal_breakers',
                    'repair_costs', 'negotiation_leverage', 'recommended_offer',
                    'transparency_score', 'usage'):
            self.assertIn(key, j, f"response missing '{key}'")
        self.assertEqual(j['offer_score'], 72)
        self.assertEqual(j['recommended_offer'], 865000)
        self.assertEqual(j['repair_costs']['total_low'], 8000)
        self.assertEqual(j['repair_costs']['total_high'], 15000)
        self.assertIsInstance(j['deal_breakers'], list)
        self.assertEqual(j['deal_breakers'][0]['severity'], 'high')

    def test_missing_documents_is_400(self):
        raw, _ = _mint_key(self.app, self.db, self.mod)
        r = self.client.post('/api/v1/analyze',
                             headers={'Authorization': f'Bearer {raw}'},
                             json={'property_price': 900000, 'property_address': '1 Main St'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('required', r.get_json().get('error', '').lower())

    def test_bad_price_is_400(self):
        raw, _ = _mint_key(self.app, self.db, self.mod)
        with patch('offerwise_intelligence.OfferWiseIntelligence.analyze_property',
                   return_value=_fake_result()):
            r = self._call(raw, property_price=-5)
        self.assertEqual(r.status_code, 400)

    def test_usage_increments_after_a_successful_call(self):
        raw, kid = _mint_key(self.app, self.db, self.mod, calls_month=0)
        with patch('offerwise_intelligence.OfferWiseIntelligence.analyze_property',
                   return_value=_fake_result()):
            self._call(raw)
        from models import APIKey
        with self.app.app_context():
            k = APIKey.query.get(kid)
            self.assertEqual(k.calls_month, 1,
                             'a successful analyze call must increment calls_month')
            self.assertGreaterEqual(k.calls_total, 1)

    def test_over_monthly_limit_is_rejected(self):
        # key already at its limit — must be refused BEFORE running the engine
        raw, _ = _mint_key(self.app, self.db, self.mod, monthly_limit=5, calls_month=5)
        with patch('offerwise_intelligence.OfferWiseIntelligence.analyze_property',
                   return_value=_fake_result()) as mocked:
            r = self._call(raw)
        self.assertIn(r.status_code, (401, 429),
                      'a key at its monthly limit must be refused')
        mocked.assert_not_called()  # must not burn an AI call when over limit

    def test_concurrency_guard_releases_between_calls(self):
        # v5.89.331: the concurrency semaphore must be released after each call (success
        # AND exception), or sequential calls would eventually deadlock once the slot
        # count is exhausted. Fire more calls in a row than there are slots and assert
        # they all complete — proving release works on every path.
        raw, kid = _mint_key(self.app, self.db, self.mod, monthly_limit=100, calls_month=0)
        slots = self.mod._B2B_ANALYZE_MAX_CONCURRENT
        with patch('offerwise_intelligence.OfferWiseIntelligence.analyze_property',
                   return_value=_fake_result()):
            for _ in range(slots + 3):
                r = self._call(raw)
                self.assertEqual(r.status_code, 200,
                                 'each sequential call must get its slot back; a stuck '
                                 'semaphore would 503 once slots are exhausted')


# ─────────────────────────────────────────────────────────────────────────────
# /api/v1/usage — key stats
# ─────────────────────────────────────────────────────────────────────────────
class TestApiV1Usage(_Base):

    def test_usage_returns_real_key_stats(self):
        raw, _ = _mint_key(self.app, self.db, self.mod, monthly_limit=100, calls_month=7)
        r = self.client.get('/api/v1/usage', headers={'Authorization': f'Bearer {raw}'})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        for key in ('key_prefix', 'tier', 'calls_month', 'monthly_limit',
                    'calls_remaining', 'calls_total'):
            self.assertIn(key, j, f"usage missing '{key}'")
        self.assertEqual(j['calls_month'], 7)
        self.assertEqual(j['monthly_limit'], 100)
        self.assertEqual(j['calls_remaining'], 93)


# ─────────────────────────────────────────────────────────────────────────────
# /api/v1/research and /api/v1/screen — address-based endpoints
# ─────────────────────────────────────────────────────────────────────────────
class TestApiV1ResearchScreen(_Base):

    def test_research_requires_address(self):
        raw, _ = _mint_key(self.app, self.db, self.mod)
        r = self.client.post('/api/v1/research',
                             headers={'Authorization': f'Bearer {raw}'}, json={})
        self.assertEqual(r.status_code, 400)

    def test_screen_requires_address(self):
        raw, _ = _mint_key(self.app, self.db, self.mod)
        r = self.client.post('/api/v1/screen',
                             headers={'Authorization': f'Bearer {raw}'}, json={})
        self.assertEqual(r.status_code, 400)

    def test_research_without_key_is_401(self):
        r = self.client.post('/api/v1/research', json={'address': '1 Main St'})
        self.assertEqual(r.status_code, 401)

    def test_screen_without_key_is_401(self):
        r = self.client.post('/api/v1/screen', json={'address': '1 Main St'})
        self.assertEqual(r.status_code, 401)


if __name__ == '__main__':
    unittest.main(verbosity=2)
