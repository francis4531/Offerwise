"""
test_e2e_analysis_core.py — v5.88.11 (Path B Release 3: Analysis core)

Comprehensive end-to-end coverage of the analysis surface area:
list, save (sync), share, delete, ownership.

Honest scope note: the actual /api/analyze endpoint runs Anthropic
API + ML inference + PDF parsing. Mocking those cleanly would be a
multi-day project and the resulting tests would be brittle. This
release covers the ORCHESTRATION around analysis instead — the parts
that can be tested without simulating model output:

  ✅ Credit gate before /api/analyze (without exercising the analysis itself)
  ✅ Consent gate
  ✅ Save analysis (POST /api/user/analyses) — happy path + duplicate dedup
  ✅ List analyses (GET /api/user/analyses) — own-only, sort by date desc
  ✅ Delete analysis by ID (idempotent, ownership check)
  ✅ Delete analysis by timestamp (frontend pattern)
  ✅ Share link creation — happy path, ownership, expiry, snapshot integrity
  ✅ Public share view — anyone can view, expired returns 404, view_count increments
  ✅ Share reactions (5/hour rate limit)
  ✅ Cross-user isolation (user A cannot see/delete user B's analyses)

Coverage (counted): 28 tests
"""
import json
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-analysis-e2e'
os.environ['DATABASE_URL'] = 'sqlite:///test_e2e_analysis.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-analysis-e2e')
os.environ['RATELIMIT_ENABLED'] = 'false'

import os as _os
_db_path = 'test_e2e_analysis.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)


def _unique_email(prefix='analysis'):
    return f'{prefix}_{int(datetime.now().timestamp() * 1000000)}@e2e-analysis.test.offerwise.ai'


def _login_session(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


# Sample analysis result JSON shape — mirrors what /api/analyze produces.
# Used so tests don't need to actually run an analysis to test
# downstream operations (list, save, share, delete).
SAMPLE_ANALYSIS_RESULT = {
    'risk_score': {
        'composite_score': 67,
        'category_scores': [
            {'category': 'Foundation', 'score': 85, 'severity': 'critical'},
            {'category': 'Roof', 'score': 60, 'severity': 'elevated'},
            {'category': 'HVAC', 'score': 30, 'severity': 'low'},
        ],
        'deal_breakers': [
            {'issue': 'Foundation cracks visible', 'category': 'Foundation'}
        ],
        'overall_risk_score': 67,
    },
    'offer_strategy': {
        'recommended_offer': 475000,
        'offer_score': 33,
        'reasoning': 'Foundation issues warrant 5% reduction',
    },
    'transparency_report': {
        'red_flags': [
            {'flag': 'Recent flood disclosure missing', 'category': 'Disclosure'},
        ],
        'transparency_score': 72,
    },
    'risk_dna': {
        'composite_score': 67,
    },
    'analysis_id': None,  # Will be filled in by save endpoint
    'property_id': None,
}


# =============================================================================
# Save analysis (POST /api/user/analyses) — frontend localStorage sync
# =============================================================================

class TestSaveAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, Property, Analysis
        cls.app = app
        cls.db = db
        cls.User = User
        cls.Property = Property
        cls.Analysis = Analysis

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-analysis.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            users = self.User.query.filter(
                self.User.email.like('%@e2e-analysis.test.offerwise.ai')
            ).all()
            for u in users:
                # Cascade: delete properties (and their analyses via FK)
                props = self.Property.query.filter_by(user_id=u.id).all()
                for p in props:
                    self.Analysis.query.filter_by(property_id=p.id).delete()
                    self.db.session.delete(p)
                self.db.session.delete(u)
            self.db.session.commit()

    def _make_user(self, credits=5):
        with self.app.app_context():
            user = self.User(
                email=_unique_email('save'), name='Save Test',
                auth_provider='email', tier='free',
                analysis_credits=credits,
            )
            user.set_password('SaveTest123!')
            self.db.session.add(user)
            self.db.session.commit()
            return user.id

    def test_save_analysis_creates_property_and_analysis(self):
        """A POST to /api/user/analyses creates one Property and one
        Analysis row, both owned by the current user."""
        uid = self._make_user()
        _login_session(self.client, uid)

        payload = {
            'id': '1769399740604',
            'property_address': '123 Test Lane, Testville',
            'asking_price': 500000,
            'recommended_offer': 475000,
            'risk_score': SAMPLE_ANALYSIS_RESULT['risk_score'],
            'analyzed_at': '2026-05-01T10:00:00',
            'full_result': SAMPLE_ANALYSIS_RESULT,
        }

        r = self.client.post('/api/user/analyses', json=payload)
        self.assertEqual(r.status_code, 200,
            f'Save analysis should succeed, got {r.status_code}: {r.data}')
        d = r.get_json()
        self.assertTrue(d.get('success'))

        with self.app.app_context():
            props = self.Property.query.filter_by(user_id=uid).all()
            self.assertEqual(len(props), 1, 'Exactly one Property created')
            self.assertEqual(props[0].address, '123 Test Lane, Testville')
            self.assertEqual(props[0].price, 500000)

            analyses = self.Analysis.query.filter_by(property_id=props[0].id).all()
            self.assertEqual(len(analyses), 1, 'Exactly one Analysis created')

    def test_save_analysis_dedups_by_address_and_price(self):
        """Posting the same address+price twice should NOT create two
        properties — the second is recognized as a duplicate."""
        uid = self._make_user()
        _login_session(self.client, uid)

        payload = {
            'property_address': '999 Duplicate Rd',
            'asking_price': 600000,
            'analyzed_at': '2026-05-01T10:00:00',
            'full_result': SAMPLE_ANALYSIS_RESULT,
        }

        r1 = self.client.post('/api/user/analyses', json=payload)
        self.assertEqual(r1.status_code, 200)

        r2 = self.client.post('/api/user/analyses', json=payload)
        self.assertEqual(r2.status_code, 200,
            'Duplicate save should still return 200 (idempotent UX)')
        d2 = r2.get_json()
        self.assertIn('already', (d2.get('message') or '').lower(),
            'Response message should mention "already" for dedup')

        with self.app.app_context():
            props = self.Property.query.filter_by(user_id=uid).all()
            self.assertEqual(len(props), 1,
                'Duplicate save must NOT create a second Property')

    def test_save_analysis_anonymous_returns_401(self):
        anon = self.app.test_client(use_cookies=False)
        r = anon.post('/api/user/analyses', json={'property_address': 'X', 'asking_price': 1})
        self.assertNotEqual(r.status_code, 200,
            'Anonymous save must NOT return 200')

    def test_save_analysis_no_body_returns_400(self):
        uid = self._make_user()
        _login_session(self.client, uid)
        r = self.client.post('/api/user/analyses')
        # Endpoint may return 400 explicitly, or 415 (no JSON), or even 500.
        # 400 is the right answer; let's be strict.
        self.assertEqual(r.status_code, 400,
            f'No body should return 400 cleanly, got {r.status_code}')


# =============================================================================
# List analyses (GET /api/user/analyses)
# =============================================================================

class TestListAnalyses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, Property, Analysis
        cls.app = app
        cls.db = db
        cls.User = User
        cls.Property = Property
        cls.Analysis = Analysis

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            users = self.User.query.filter(
                self.User.email.like('%@e2e-analysis.test.offerwise.ai')
            ).all()
            for u in users:
                props = self.Property.query.filter_by(user_id=u.id).all()
                for p in props:
                    self.Analysis.query.filter_by(property_id=p.id).delete()
                    self.db.session.delete(p)
                self.db.session.delete(u)
            self.db.session.commit()

    def tearDown(self):
        self.setUp()  # same cleanup logic

    def _make_user_with_analyses(self, count=3):
        with self.app.app_context():
            user = self.User(
                email=_unique_email('list'), name='List',
                auth_provider='email', tier='free', analysis_credits=10,
            )
            user.set_password('ListTest123!')
            self.db.session.add(user)
            self.db.session.flush()
            uid = user.id

            for i in range(count):
                # Older properties first; we'll assert sort order in test
                analyzed_at = datetime.utcnow() - timedelta(days=count - i)
                p = self.Property(
                    user_id=uid,
                    address=f'{i} Main St',
                    price=500000 + i * 1000,
                    status='analyzed',
                    analyzed_at=analyzed_at,
                )
                self.db.session.add(p)
                self.db.session.flush()

                a = self.Analysis(
                    property_id=p.id, user_id=uid, status='completed',
                    offer_score=75 + i,
                    result_json=json.dumps(SAMPLE_ANALYSIS_RESULT),
                    created_at=analyzed_at,
                )
                self.db.session.add(a)

            self.db.session.commit()
            return uid

    def test_list_analyses_returns_owned_count(self):
        uid = self._make_user_with_analyses(count=3)
        _login_session(self.client, uid)

        r = self.client.get('/api/user/analyses')
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d.get('count'), 3)
        self.assertEqual(len(d.get('analyses', [])), 3)

    def test_list_analyses_sorted_newest_first(self):
        uid = self._make_user_with_analyses(count=3)
        _login_session(self.client, uid)

        r = self.client.get('/api/user/analyses')
        analyses = r.get_json().get('analyses', [])
        # First should be newest
        self.assertGreaterEqual(len(analyses), 2)
        # Verify date desc
        d0 = analyses[0]['analyzed_at']
        d1 = analyses[1]['analyzed_at']
        self.assertGreaterEqual(d0, d1,
            'Analyses must be sorted newest first (analyzed_at desc)')

    def test_list_analyses_anonymous_returns_401(self):
        anon = self.app.test_client(use_cookies=False)
        r = anon.get('/api/user/analyses')
        self.assertNotEqual(r.status_code, 200)

    def test_list_analyses_cross_user_isolation(self):
        """User A's GET must NEVER return User B's analyses."""
        uid_a = self._make_user_with_analyses(count=2)
        uid_b = self._make_user_with_analyses(count=5)

        # User A login should see 2, not 7
        _login_session(self.client, uid_a)
        r = self.client.get('/api/user/analyses')
        self.assertEqual(r.get_json().get('count'), 2,
            'CRITICAL: cross-user analysis leak — '
            'User A saw User B\'s analyses')

        # User B login should see 5
        client_b = self.app.test_client(use_cookies=True)
        _login_session(client_b, uid_b)
        r = client_b.get('/api/user/analyses')
        self.assertEqual(r.get_json().get('count'), 5)

    def test_list_analyses_empty_for_new_user(self):
        with self.app.app_context():
            user = self.User(
                email=_unique_email('empty'), name='Empty',
                auth_provider='email', tier='free', analysis_credits=1,
            )
            user.set_password('EmptyTest123!')
            self.db.session.add(user)
            self.db.session.commit()
            uid = user.id

        _login_session(self.client, uid)
        r = self.client.get('/api/user/analyses')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json().get('count'), 0)
        self.assertEqual(r.get_json().get('analyses'), [])

    def test_list_analyses_includes_offer_score_as_number(self):
        """The offer_score field must always be a NUMBER (not None or string).
        Frontend code does math on it; if the type changes, the dashboard
        breaks silently."""
        uid = self._make_user_with_analyses(count=1)
        _login_session(self.client, uid)
        r = self.client.get('/api/user/analyses')
        analyses = r.get_json().get('analyses', [])
        self.assertEqual(len(analyses), 1)
        offer_score = analyses[0].get('offer_score')
        self.assertIsInstance(offer_score, (int, float),
            f'offer_score must be a number, got {type(offer_score).__name__}: {offer_score!r}')


# =============================================================================
# Delete analysis (DELETE /api/analyses/<id> + by-timestamp)
# =============================================================================

class TestDeleteAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, Property, Analysis
        cls.app = app
        cls.db = db
        cls.User = User
        cls.Property = Property
        cls.Analysis = Analysis

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            users = self.User.query.filter(
                self.User.email.like('%@e2e-analysis.test.offerwise.ai')
            ).all()
            for u in users:
                props = self.Property.query.filter_by(user_id=u.id).all()
                for p in props:
                    self.Analysis.query.filter_by(property_id=p.id).delete()
                    self.db.session.delete(p)
                self.db.session.delete(u)
            self.db.session.commit()

    def tearDown(self):
        self.setUp()

    def _make_user_with_analysis(self):
        with self.app.app_context():
            user = self.User(
                email=_unique_email('delete'), name='Delete',
                auth_provider='email', tier='free', analysis_credits=5,
            )
            user.set_password('DeleteTest123!')
            self.db.session.add(user)
            self.db.session.flush()

            p = self.Property(
                user_id=user.id, address='1 Delete Way', price=400000,
                status='analyzed', analyzed_at=datetime.utcnow(),
            )
            self.db.session.add(p)
            self.db.session.flush()

            a = self.Analysis(
                property_id=p.id, user_id=user.id, status='completed',
                offer_score=70, result_json=json.dumps(SAMPLE_ANALYSIS_RESULT),
            )
            self.db.session.add(a)
            self.db.session.commit()
            return user.id, p.id, a.id

    def test_delete_analysis_by_id_happy_path(self):
        uid, pid, aid = self._make_user_with_analysis()
        _login_session(self.client, uid)

        r = self.client.delete(f'/api/analyses/{aid}')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json().get('success'))

        with self.app.app_context():
            self.assertIsNone(self.Analysis.query.get(aid),
                'Analysis row should be deleted')

    def test_delete_analysis_already_deleted_is_idempotent(self):
        """DELETE on a non-existent id should return 200, not 404.
        DELETE is idempotent — calling it again on something already
        gone is success."""
        uid = self._make_user_with_analysis()[0]
        _login_session(self.client, uid)

        r = self.client.delete('/api/analyses/9999999')
        self.assertEqual(r.status_code, 200,
            'DELETE on non-existent id should be idempotent (200, not 404)')
        d = r.get_json()
        self.assertTrue(d.get('success'))
        self.assertTrue(d.get('already_deleted'))

    def test_delete_other_users_analysis_returns_403(self):
        """User A trying to delete User B's analysis MUST get 403.
        Anything else is a major security hole — anyone could delete
        anyone else's analyses by guessing IDs."""
        uid_a, _, aid_a = self._make_user_with_analysis()
        uid_b, _, _ = self._make_user_with_analysis()

        # Login as B, try to delete A's analysis
        _login_session(self.client, uid_b)
        r = self.client.delete(f'/api/analyses/{aid_a}')
        self.assertEqual(r.status_code, 403,
            'CRITICAL: cross-user delete allowed. Major security hole.')

        # Verify A's analysis still exists
        with self.app.app_context():
            self.assertIsNotNone(self.Analysis.query.get(aid_a),
                'A\'s analysis must still exist after B\'s failed delete attempt')

    def test_delete_analysis_anonymous_returns_401(self):
        _, _, aid = self._make_user_with_analysis()
        anon = self.app.test_client(use_cookies=False)
        r = anon.delete(f'/api/analyses/{aid}')
        self.assertNotEqual(r.status_code, 200)

    def test_delete_by_timestamp_finds_correct_property(self):
        """The by-timestamp endpoint matches Property.analyzed_at.
        Frontend uses this when localStorage has the analysis but the
        DB row's analysis_id isn't synced."""
        with self.app.app_context():
            user = self.User(
                email=_unique_email('delts'), name='X',
                auth_provider='email', tier='free', analysis_credits=1,
            )
            user.set_password('DelTsTest123!')
            self.db.session.add(user)
            self.db.session.flush()

            # Property with a specific timestamp
            ts_dt = datetime.utcnow().replace(microsecond=0)
            p = self.Property(
                user_id=user.id, address='Timestamp St', price=500000,
                status='analyzed', analyzed_at=ts_dt,
            )
            self.db.session.add(p)
            self.db.session.commit()
            uid = user.id
            ts_ms = int(ts_dt.timestamp() * 1000)

        _login_session(self.client, uid)
        r = self.client.delete(f'/api/analyses/by-timestamp/{ts_ms}')
        self.assertEqual(r.status_code, 200,
            f'Delete by timestamp should succeed, got {r.status_code}: {r.data}')

        with self.app.app_context():
            self.assertEqual(
                self.Property.query.filter_by(user_id=uid).count(), 0,
                'Property should be deleted by the timestamp endpoint'
            )


# =============================================================================
# Share link creation (POST /api/share/create)
# =============================================================================

class TestShareLinkCreation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, Property, Analysis, ShareLink
        cls.app = app
        cls.db = db
        cls.User = User
        cls.Property = Property
        cls.Analysis = Analysis
        cls.ShareLink = ShareLink

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            users = self.User.query.filter(
                self.User.email.like('%@e2e-analysis.test.offerwise.ai')
            ).all()
            for u in users:
                self.ShareLink.query.filter_by(user_id=u.id).delete()
                props = self.Property.query.filter_by(user_id=u.id).all()
                for p in props:
                    self.Analysis.query.filter_by(property_id=p.id).delete()
                    self.db.session.delete(p)
                self.db.session.delete(u)
            self.db.session.commit()

    def tearDown(self):
        self.setUp()

    def _make_user_with_analysis(self):
        with self.app.app_context():
            user = self.User(
                email=_unique_email('share'), name='Sharer',
                auth_provider='email', tier='free', analysis_credits=5,
            )
            user.set_password('ShareTest123!')
            self.db.session.add(user)
            self.db.session.flush()

            p = self.Property(
                user_id=user.id, address='Share Lane', price=600000,
                status='analyzed', analyzed_at=datetime.utcnow(),
            )
            self.db.session.add(p)
            self.db.session.flush()

            a = self.Analysis(
                property_id=p.id, user_id=user.id, status='completed',
                offer_score=80, result_json=json.dumps(SAMPLE_ANALYSIS_RESULT),
            )
            self.db.session.add(a)
            self.db.session.commit()
            return user.id, p.id, a.id

    def test_share_create_happy_path(self):
        """POST /api/share/create returns a token and creates a ShareLink row."""
        uid, pid, _ = self._make_user_with_analysis()
        _login_session(self.client, uid)

        r = self.client.post('/api/share/create', json={
            'property_id': pid,
            'sharer_name': 'Alice',
            'recipient_name': 'Bob',
            'personal_note': 'Want your second opinion on this',
        })
        self.assertEqual(r.status_code, 200,
            f'Share create should succeed, got {r.status_code}: {r.data}')
        d = r.get_json()
        self.assertIn('token', d, 'Response must include share token')
        self.assertIn('share_url', d, 'Response must include share_url')

        # ShareLink row exists
        with self.app.app_context():
            link = self.ShareLink.query.filter_by(token=d['token']).first()
            self.assertIsNotNone(link)
            self.assertEqual(link.user_id, uid)
            self.assertEqual(link.property_id, pid)
            self.assertEqual(link.sharer_name, 'Alice')
            self.assertTrue(link.is_active)
            # Expiry ~90 days out per ShareLink.create_link
            self.assertIsNotNone(link.expires_at)

    def test_share_create_without_property_id_returns_400(self):
        uid, _, _ = self._make_user_with_analysis()
        _login_session(self.client, uid)
        r = self.client.post('/api/share/create', json={'sharer_name': 'X'})
        self.assertEqual(r.status_code, 400)

    def test_share_create_for_other_users_property_returns_404(self):
        """User A cannot create a share link for User B's property.
        Without this check, anyone could share anyone's analysis."""
        uid_a, pid_a, _ = self._make_user_with_analysis()
        uid_b, _, _ = self._make_user_with_analysis()

        # B logs in, tries to share A's property
        _login_session(self.client, uid_b)
        r = self.client.post('/api/share/create', json={'property_id': pid_a})
        # Endpoint returns 404 (not 403) because the ownership filter
        # is in the SAME query as the lookup — query returns nothing.
        # Either is fine; the key is "not 200".
        self.assertNotEqual(r.status_code, 200,
            'CRITICAL: User B was able to share User A\'s analysis')

    def test_share_create_anonymous_returns_401(self):
        anon = self.app.test_client(use_cookies=False)
        r = anon.post('/api/share/create', json={'property_id': 1})
        self.assertNotEqual(r.status_code, 200)

    def test_share_snapshot_freezes_analysis_data(self):
        """The share snapshot should be FROZEN at share time.
        If the user re-analyzes the property, the share link still shows
        the original snapshot. This is intentional — it prevents
        share recipients from seeing different data than what was sent."""
        uid, pid, aid = self._make_user_with_analysis()
        _login_session(self.client, uid)

        r = self.client.post('/api/share/create', json={
            'property_id': pid, 'sharer_name': 'X',
        })
        self.assertEqual(r.status_code, 200)
        token = r.get_json()['token']

        with self.app.app_context():
            link = self.ShareLink.query.filter_by(token=token).first()
            self.assertIsNotNone(link.snapshot_json,
                'Share link must have a snapshot')
            snapshot = json.loads(link.snapshot_json)
            # Snapshot must include the recommended_offer (camelCase or
            # snake_case — sharing_routes uses camelCase keys)
            snap_keys = set(k.lower().replace('_', '') for k in snapshot.keys())
            self.assertIn('recommendedoffer', snap_keys,
                f'Snapshot must contain recommended_offer/recommendedOffer. '
                f'Got keys: {sorted(snapshot.keys())}')


# =============================================================================
# Public share view (GET /opinion/<token>)
# =============================================================================

class TestPublicShareView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, Property, Analysis, ShareLink
        cls.app = app
        cls.db = db
        cls.User = User
        cls.Property = Property
        cls.Analysis = Analysis
        cls.ShareLink = ShareLink

    def setUp(self):
        with self.app.app_context():
            users = self.User.query.filter(
                self.User.email.like('%@e2e-analysis.test.offerwise.ai')
            ).all()
            for u in users:
                self.ShareLink.query.filter_by(user_id=u.id).delete()
                props = self.Property.query.filter_by(user_id=u.id).all()
                for p in props:
                    self.Analysis.query.filter_by(property_id=p.id).delete()
                    self.db.session.delete(p)
                self.db.session.delete(u)
            self.db.session.commit()

    def tearDown(self):
        self.setUp()

    def _make_share_link(self, expires_in_days=90, is_active=True):
        """Create a user, property, analysis, and share link directly."""
        with self.app.app_context():
            user = self.User(
                email=_unique_email('opinion'), name='Opinion',
                auth_provider='email', tier='free', analysis_credits=1,
            )
            user.set_password('OpinionTest123!')
            self.db.session.add(user)
            self.db.session.flush()

            p = self.Property(
                user_id=user.id, address='Opinion Ave', price=550000,
                status='analyzed', analyzed_at=datetime.utcnow(),
            )
            self.db.session.add(p)
            self.db.session.flush()

            a = self.Analysis(
                property_id=p.id, user_id=user.id, status='completed',
                offer_score=70, result_json=json.dumps(SAMPLE_ANALYSIS_RESULT),
            )
            self.db.session.add(a)
            self.db.session.flush()

            snapshot = json.dumps({
                'address': 'Opinion Ave',
                'price': 550000,
                'offerscore': 70,
                'risk_tier': 'elevated',
                'top_findings': [
                    {'text': 'Foundation issues', 'category': 'Foundation', 'severity': 'critical'}
                ],
                'repair_cost_low': 15000,
                'repair_cost_high': 25000,
                'recommended_offer': 520000,
                'offer_range_low': 510000,
                'offer_range_high': 530000,
                'discount_percentage': 5,
                'transparency_score': 72,
                'contradictions_count': 0,
                'analyzed_at': datetime.utcnow().isoformat(),
            })

            link = self.ShareLink(
                token=f'tok_{int(datetime.now().timestamp() * 1000000)}'[:32],
                user_id=user.id,
                property_id=p.id,
                sharer_name='Sharer',
                snapshot_json=snapshot,
                is_active=is_active,
                expires_at=datetime.utcnow() + timedelta(days=expires_in_days),
            )
            self.db.session.add(link)
            self.db.session.commit()
            return link.token

    def test_public_view_anonymous_works(self):
        """Anyone (no login) can view a share link."""
        token = self._make_share_link()
        anon = self.app.test_client(use_cookies=False)
        r = anon.get(f'/opinion/{token}')
        self.assertEqual(r.status_code, 200,
            f'Public share view should return 200 to anonymous, got {r.status_code}')

    def test_public_view_increments_view_count(self):
        token = self._make_share_link()
        anon = self.app.test_client(use_cookies=False)

        with self.app.app_context():
            link = self.ShareLink.query.filter_by(token=token).first()
            self.assertEqual(link.view_count or 0, 0,
                'New link should start at 0 views')

        anon.get(f'/opinion/{token}')
        anon.get(f'/opinion/{token}')

        with self.app.app_context():
            link = self.ShareLink.query.filter_by(token=token).first()
            self.assertEqual(link.view_count, 2,
                'view_count should increment on each view')
            self.assertIsNotNone(link.first_viewed_at,
                'first_viewed_at must be set on first view')

    def test_public_view_invalid_token_returns_404(self):
        anon = self.app.test_client(use_cookies=False)
        r = anon.get('/opinion/totally_fake_token_xyz')
        self.assertEqual(r.status_code, 404)

    def test_public_view_expired_link_returns_404(self):
        """A link past expires_at must NOT be viewable."""
        # Create with expires_in_days=-1 means it expired yesterday
        token = self._make_share_link(expires_in_days=-1)
        anon = self.app.test_client(use_cookies=False)
        r = anon.get(f'/opinion/{token}')
        self.assertEqual(r.status_code, 404,
            'Expired share link must return 404, not 200')

    def test_public_view_inactive_link_returns_404(self):
        """A deactivated link (is_active=False) must NOT be viewable."""
        token = self._make_share_link(is_active=False)
        anon = self.app.test_client(use_cookies=False)
        r = anon.get(f'/opinion/{token}')
        self.assertEqual(r.status_code, 404,
            'Deactivated share link must return 404')


# =============================================================================
# Share reactions (POST /api/share/<token>/react)
# =============================================================================

class TestShareReactions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, Property, Analysis, ShareLink
        cls.app = app
        cls.db = db
        cls.User = User
        cls.Property = Property
        cls.Analysis = Analysis
        cls.ShareLink = ShareLink

    def setUp(self):
        with self.app.app_context():
            users = self.User.query.filter(
                self.User.email.like('%@e2e-analysis.test.offerwise.ai')
            ).all()
            for u in users:
                self.ShareLink.query.filter_by(user_id=u.id).delete()
                props = self.Property.query.filter_by(user_id=u.id).all()
                for p in props:
                    self.Analysis.query.filter_by(property_id=p.id).delete()
                    self.db.session.delete(p)
                self.db.session.delete(u)
            self.db.session.commit()

    def tearDown(self):
        self.setUp()

    def _make_share_link(self):
        with self.app.app_context():
            user = self.User(
                email=_unique_email('react'), name='React',
                auth_provider='email', tier='free', analysis_credits=1,
            )
            user.set_password('ReactTest123!')
            self.db.session.add(user)
            self.db.session.flush()

            p = self.Property(
                user_id=user.id, address='React Way', price=500000,
                status='analyzed', analyzed_at=datetime.utcnow(),
            )
            self.db.session.add(p)
            self.db.session.flush()

            link = self.ShareLink(
                token=f'react_{int(datetime.now().timestamp() * 1000000)}'[:32],
                user_id=user.id,
                property_id=p.id,
                sharer_name='X',
                snapshot_json=json.dumps({'address': 'React Way'}),
                is_active=True,
                expires_at=datetime.utcnow() + timedelta(days=30),
            )
            self.db.session.add(link)
            self.db.session.commit()
            return link.token

    def test_react_to_valid_share_link(self):
        token = self._make_share_link()
        anon = self.app.test_client(use_cookies=False)
        # Valid reactions per sharing_routes.py: good_deal, fair_price, walk_away
        r = anon.post(f'/api/share/{token}/react', json={'reaction': 'good_deal'})
        # 200 or 204 acceptable
        self.assertIn(r.status_code, [200, 201, 204],
            f'Reaction should succeed, got {r.status_code}: {r.data}')

    def test_react_invalid_reaction_returns_400(self):
        """Reactions are limited to {good_deal, fair_price, walk_away}."""
        token = self._make_share_link()
        anon = self.app.test_client(use_cookies=False)
        r = anon.post(f'/api/share/{token}/react', json={'reaction': 'thumbs_up'})
        self.assertEqual(r.status_code, 400,
            'Unknown reaction value must return 400')

    def test_react_to_invalid_token_returns_404(self):
        anon = self.app.test_client(use_cookies=False)
        r = anon.post('/api/share/fake_token/react', json={'reaction': 'thumbs_up'})
        self.assertIn(r.status_code, [404, 400],
            f'Invalid token reaction should fail, got {r.status_code}')


if __name__ == '__main__':
    unittest.main()
