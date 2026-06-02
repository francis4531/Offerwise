"""
test_e2e_credits_payments.py — v5.88.10 (Path B Release 2: Credits + Payments)

Comprehensive end-to-end coverage of the credit operations + payment surface.
Drives the Flask test client through real endpoints, asserts response codes
AND database state.

Coverage:
  Credits API
    - GET /api/user-credits: anonymous → 401, authenticated → balance
    - POST /api/deduct-credit: happy path, insufficient credits → 402,
      anonymous → 401, atomic guard under concurrent requests, transaction
      log row created on success
    - GET /api/purchase-history: returns own purchases only (cross-user
      isolation)

  Checkout creator (POST /api/create-checkout-session)
    - Anonymous → 401/redirect
    - Invalid plan → 400
    - Legacy plan name redirects (single → buyer_starter, etc.)
    - Missing body → 400 (NOT 500 — this is the bug we'll fix)
    - Plan with no Stripe price ID configured → 400 with helpful message
    - Stripe API key missing → 500 with config error message

  Stripe webhook (POST /webhook/stripe)
    - Missing/invalid signature → 400
    - checkout.session.completed for buyer_starter activates user subscription
    - Same for inspector_pro updates Inspector.plan
    - Same for contractor_starter updates Contractor.plan

  Payment success/cancel pages
    - /payment/success: authenticated → 200, anonymous → redirect
    - /payment/cancel: authenticated → 200, anonymous → redirect

  PRICING_TIERS contract
    - Every documented buyer/contractor/inspector tier has correct price
      and required keys
"""
import json
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-pay-e2e'
os.environ['DATABASE_URL'] = 'sqlite:///test_e2e_pay.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-pay-e2e')
os.environ['RATELIMIT_ENABLED'] = 'false'

import os as _os
_db_path = 'test_e2e_pay.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)


def _unique_email(prefix='test'):
    return f'{prefix}_{int(datetime.now().timestamp() * 1000000)}@e2e-pay.test.offerwise.ai'


def _login_session(client, user_id):
    """Inject a Flask-Login session cookie pointing at user_id."""
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


# ============================================================================
# CREDITS API
# ============================================================================

class TestUserCreditsEndpoint(unittest.TestCase):
    """GET /api/user-credits — credit balance lookup."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User
        cls.app = app
        cls.db = db
        cls.User = User

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-pay.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-pay.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def _make_user(self, credits=5):
        with self.app.app_context():
            u = self.User(
                email=_unique_email('cred'), name='Credit User',
                auth_provider='email', tier='free',
                analysis_credits=credits,
            )
            u.set_password('TestPass123!')
            self.db.session.add(u)
            self.db.session.commit()
            return u.id

    def test_anonymous_request_rejected(self):
        """No session cookie → must NOT return credit info."""
        client = self.app.test_client(use_cookies=False)
        r = client.get('/api/user-credits')
        self.assertNotEqual(r.status_code, 200,
            'CRITICAL: /api/user-credits returned 200 to anonymous user. '
            'Could expose other users\' credit balances if user_id in URL ever added.')

    def test_authenticated_returns_balance(self):
        uid = self._make_user(credits=7)
        _login_session(self.client, uid)
        r = self.client.get('/api/user-credits')
        self.assertEqual(r.status_code, 200,
            f'Authenticated user should get balance, got {r.status_code}')
        d = r.get_json()
        self.assertEqual(d['credits'], 7,
            f'Expected credits=7, got {d.get("credits")}')
        self.assertIn('total_purchased', d)
        self.assertIn('analyses_done', d)
        self.assertIn('referral_code', d)


class TestDeductCreditEndpoint(unittest.TestCase):
    """POST /api/deduct-credit — atomic credit consumption."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, CreditTransaction
        cls.app = app
        cls.db = db
        cls.User = User
        cls.CreditTransaction = CreditTransaction

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-pay.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-pay.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def _make_user(self, credits=3, completed=0):
        with self.app.app_context():
            u = self.User(
                email=_unique_email('deduct'), name='Deduct',
                auth_provider='email', tier='free',
                analysis_credits=credits,
                analyses_completed=completed,
            )
            u.set_password('Pass1234!')
            self.db.session.add(u)
            self.db.session.commit()
            return u.id

    def test_anonymous_rejected(self):
        client = self.app.test_client(use_cookies=False)
        r = client.post('/api/deduct-credit')
        self.assertNotEqual(r.status_code, 200,
            'CRITICAL: anonymous request to /api/deduct-credit returned 200. '
            'Anyone could trigger credit operations.')

    def test_happy_path_decrements_credits(self):
        uid = self._make_user(credits=3, completed=0)
        _login_session(self.client, uid)
        r = self.client.post('/api/deduct-credit')
        self.assertEqual(r.status_code, 200, f'Got {r.status_code}: {r.data}')
        d = r.get_json()
        self.assertTrue(d['success'])
        self.assertEqual(d['remaining_credits'], 2,
            'Remaining credits should be 2 after deducting from 3')

        with self.app.app_context():
            u = self.User.query.get(uid)
            self.assertEqual(u.analysis_credits, 2)
            self.assertEqual(u.analyses_completed, 1,
                'analyses_completed should increment to 1')

    def test_zero_credits_returns_402(self):
        """User with 0 credits MUST get 402 (Payment Required), not 500
        and not silent success. The atomic SQL guard handles this."""
        uid = self._make_user(credits=0)
        _login_session(self.client, uid)
        r = self.client.post('/api/deduct-credit')
        self.assertEqual(r.status_code, 402,
            f'0-credits deduction must return 402, got {r.status_code}')

        with self.app.app_context():
            u = self.User.query.get(uid)
            self.assertEqual(u.analysis_credits, 0,
                'CRITICAL: balance went negative on attempted deduction. '
                'Atomic guard is broken.')

    def test_credit_transaction_logged_on_success(self):
        """Successful deduction must log a CreditTransaction row with
        credits=-1 and plan_id='usage'."""
        uid = self._make_user(credits=2)
        _login_session(self.client, uid)

        with self.app.app_context():
            tx_before = self.CreditTransaction.query.filter_by(user_id=uid).count()

        self.client.post('/api/deduct-credit')

        with self.app.app_context():
            tx_after = self.CreditTransaction.query.filter_by(user_id=uid).count()
            self.assertEqual(tx_after, tx_before + 1,
                'Successful deduction must log exactly one CreditTransaction row')
            tx = self.CreditTransaction.query.filter_by(
                user_id=uid, plan_id='usage'
            ).order_by(self.CreditTransaction.id.desc()).first()
            self.assertIsNotNone(tx)
            self.assertEqual(tx.credits, -1)
            self.assertEqual(tx.status, 'completed')

    def test_concurrent_deductions_only_one_succeeds(self):
        """Two threads racing on user with credits=1: exactly one succeeds.
        The other must get 402. Final balance must be 0, NEVER -1.

        This is the atomic-guard defense. If it ever regresses, the product
        gives away free analyses on every concurrent click."""
        import threading
        uid = self._make_user(credits=1)

        results = {'r1': None, 'r2': None}

        def deduct(label):
            client = self.app.test_client(use_cookies=True)
            _login_session(client, uid)
            r = client.post('/api/deduct-credit')
            results[label] = r.status_code

        t1 = threading.Thread(target=deduct, args=('r1',))
        t2 = threading.Thread(target=deduct, args=('r2',))
        t1.start(); t2.start()
        t1.join(); t2.join()

        codes = list(results.values())
        successes = [c for c in codes if c == 200]
        rejections = [c for c in codes if c == 402]

        self.assertEqual(len(successes), 1,
            f'Expected exactly 1 success, got {len(successes)}. '
            f'Codes: {results}. Atomic guard at /api/deduct-credit is broken.')
        self.assertEqual(len(rejections), 1,
            f'Expected exactly 1 rejection (402), got {len(rejections)}.')

        with self.app.app_context():
            u = self.User.query.get(uid)
            self.assertEqual(u.analysis_credits, 0,
                f'CRITICAL: Final balance is {u.analysis_credits}, expected 0. '
                'If negative, the product is silently giving away free analyses.')

    def test_deduct_increments_analyses_completed(self):
        """Each successful deduct must increment analyses_completed.
        This counter feeds the dashboard "X analyses run" stat. If it
        regresses, the user dashboard silently lies about activity."""
        uid = self._make_user(credits=3)
        client = self.app.test_client(use_cookies=True)
        _login_session(client, uid)

        # Verify starting state
        with self.app.app_context():
            u = self.User.query.get(uid)
            starting = u.analyses_completed or 0

        # Deduct three times
        for i in range(3):
            r = client.post('/api/deduct-credit')
            self.assertEqual(r.status_code, 200,
                f'Deduct #{i+1} failed: {r.status_code}')

        # analyses_completed must be starting+3
        with self.app.app_context():
            u = self.User.query.get(uid)
            self.assertEqual(u.analyses_completed, starting + 3,
                f'analyses_completed must increment by 3 after 3 deducts. '
                f'Started at {starting}, ended at {u.analyses_completed}. '
                f'If only +1 or unchanged, the increment is broken.')


class TestPurchaseHistoryEndpoint(unittest.TestCase):
    """GET /api/purchase-history — returns CURRENT user's purchases only."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, CreditTransaction
        cls.app = app
        cls.db = db
        cls.User = User
        cls.CreditTransaction = CreditTransaction

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            # Clean orphan CreditTransactions from prior test runs.
            # Strategy: delete tx for any user_id where no User exists OR
            # where the User's email matches our test pattern. This survives
            # cross-run pollution where prior tests deleted users but left
            # tx rows behind.
            valid_ids = {u.id for u in self.User.query.all()}
            for tx in self.CreditTransaction.query.all():
                user = self.User.query.get(tx.user_id) if tx.user_id else None
                if (tx.user_id not in valid_ids) or (user and user.email and user.email.endswith('@e2e-pay.test.offerwise.ai')):
                    self.db.session.delete(tx)
            self.User.query.filter(
                self.User.email.like('%@e2e-pay.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            test_user_ids = {u.id for u in self.User.query.filter(
                self.User.email.like('%@e2e-pay.test.offerwise.ai')
            ).all()}
            if test_user_ids:
                self.CreditTransaction.query.filter(
                    self.CreditTransaction.user_id.in_(test_user_ids)
                ).delete(synchronize_session=False)
            self.User.query.filter(
                self.User.email.like('%@e2e-pay.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def test_anonymous_rejected(self):
        client = self.app.test_client(use_cookies=False)
        r = client.get('/api/purchase-history')
        self.assertNotEqual(r.status_code, 200)

    def test_returns_only_own_purchases(self):
        """User A and User B both have purchase rows. User A's request
        MUST NOT include User B's transactions. Cross-user data leak is a
        privacy violation."""
        with self.app.app_context():
            ua = self.User(email=_unique_email('a'), name='A',
                           auth_provider='email', tier='free',
                           analysis_credits=1)
            ua.set_password('Pass1234!')
            self.db.session.add(ua)
            ub = self.User(email=_unique_email('b'), name='B',
                           auth_provider='email', tier='free',
                           analysis_credits=1)
            ub.set_password('Pass1234!')
            self.db.session.add(ub)
            self.db.session.commit()

            # A buys 5 credits
            self.db.session.add(self.CreditTransaction(
                user_id=ua.id, credits=5, amount=79.00,
                plan_id='buyer_pro', status='completed',
                completed_at=datetime.utcnow(),
            ))
            # B buys 12 credits (more recent)
            self.db.session.add(self.CreditTransaction(
                user_id=ub.id, credits=12, amount=149.00,
                plan_id='buyer_unlimited', status='completed',
                completed_at=datetime.utcnow(),
            ))
            self.db.session.commit()
            ua_id, ub_id = ua.id, ub.id

        # User A logs in, requests history
        _login_session(self.client, ua_id)
        r = self.client.get('/api/purchase-history')
        self.assertEqual(r.status_code, 200)
        history = r.get_json()
        self.assertIsInstance(history, list)

        # All returned transactions must belong to User A
        for tx in history:
            self.assertEqual(tx['credits'], 5,
                f'CRITICAL: Got tx for credits={tx["credits"]} but User A bought 5. '
                f'Cross-user data leak — User B\'s purchases visible to A.')

    def test_includes_only_completed_purchases_not_usage(self):
        """The endpoint filters credits>0, status='completed' — so usage
        deductions (credits=-1) and pending purchases must NOT appear."""
        with self.app.app_context():
            u = self.User(email=_unique_email('hist'), name='Hist',
                          auth_provider='email', tier='free',
                          analysis_credits=10)
            u.set_password('Pass1234!')
            self.db.session.add(u)
            self.db.session.commit()
            uid = u.id

            # Real purchase
            self.db.session.add(self.CreditTransaction(
                user_id=uid, credits=5, amount=79.00,
                plan_id='buyer_pro', status='completed',
                completed_at=datetime.utcnow(),
            ))
            # Usage deduction (negative credits) — must NOT appear in history
            self.db.session.add(self.CreditTransaction(
                user_id=uid, credits=-1, amount=0,
                plan_id='usage', status='completed',
                completed_at=datetime.utcnow(),
            ))
            self.db.session.commit()

        _login_session(self.client, uid)
        r = self.client.get('/api/purchase-history')
        history = r.get_json()
        self.assertEqual(len(history), 1,
            f'Should have 1 purchase row (5 credits), not include usage. Got {history}')
        self.assertEqual(history[0]['credits'], 5)


# ============================================================================
# CHECKOUT SESSION CREATOR
# ============================================================================

class TestCheckoutSessionCreation(unittest.TestCase):
    """POST /api/create-checkout-session — Stripe checkout link creator."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User
        cls.app = app
        cls.db = db
        cls.User = User

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-pay.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-pay.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def _make_authenticated_user(self):
        with self.app.app_context():
            u = self.User(email=_unique_email('chk'), name='Checkout',
                          auth_provider='email', tier='free',
                          analysis_credits=1)
            u.set_password('Pass1234!')
            self.db.session.add(u)
            self.db.session.commit()
            uid = u.id
        _login_session(self.client, uid)
        return uid

    def test_anonymous_rejected(self):
        client = self.app.test_client(use_cookies=False)
        r = client.post('/api/create-checkout-session', json={'plan': 'buyer_pro'})
        self.assertNotEqual(r.status_code, 200,
            'CRITICAL: anonymous request to /api/create-checkout-session got 200')

    def test_no_body_returns_400_not_500(self):
        """POST with no JSON body must return 400 cleanly, not crash with 500.
        Same bug pattern fixed in v5.88.09 for register/login."""
        self._make_authenticated_user()
        # Mock Stripe so we get past the API key check and reach the body parse
        with patch('app.stripe') as mock_stripe:
            mock_stripe.api_key = 'sk_test_fake'
            r = self.client.post('/api/create-checkout-session')
        self.assertEqual(r.status_code, 400,
            f'No-body POST should return 400, got {r.status_code}: {r.data!r}. '
            f'This is the same crash pattern as v5.88.09 register/login bugs.')

    def test_invalid_plan_returns_400(self):
        self._make_authenticated_user()
        with patch('app.stripe') as mock_stripe:
            mock_stripe.api_key = 'sk_test_fake'
            r = self.client.post('/api/create-checkout-session',
                                 json={'plan': 'totally_made_up_plan'})
        self.assertEqual(r.status_code, 400,
            f'Invalid plan should return 400, got {r.status_code}')

    def test_legacy_single_redirects_to_buyer_starter(self):
        """The legacy plan name 'single' MUST map to buyer_starter so
        old links don't break. If buyer_starter has no Stripe price ID
        configured (test env), expect 400 with helpful message — but
        NOT 'invalid plan' (which would mean the redirect itself broke)."""
        self._make_authenticated_user()
        with patch('app.stripe') as mock_stripe:
            mock_stripe.api_key = 'sk_test_fake'
            mock_stripe.error = MagicMock()
            mock_stripe.error.StripeError = Exception
            r = self.client.post('/api/create-checkout-session',
                                 json={'plan': 'single'})
        # If price_id is not configured (likely in test env), we get 400
        # with a "not configured" message — that means the legacy redirect WORKED.
        # If we got "invalid plan", the redirect didn't fire.
        d = r.get_json() or {}
        err_msg = (d.get('error') or '') + ' ' + (d.get('message') or '')
        self.assertNotIn('Invalid plan', err_msg,
            f'Legacy plan name "single" did NOT redirect to buyer_starter. '
            f'Got: {err_msg!r}')

    def test_missing_stripe_api_key_returns_500(self):
        """If STRIPE_SECRET_KEY is unset, the endpoint must return 500
        with a helpful config-error message — not crash silently."""
        self._make_authenticated_user()
        with patch('app.stripe') as mock_stripe:
            mock_stripe.api_key = None  # Simulate missing
            r = self.client.post('/api/create-checkout-session',
                                 json={'plan': 'buyer_pro'})
        self.assertEqual(r.status_code, 500)
        d = r.get_json() or {}
        # Message should mention configuration / Stripe / API key
        err = (d.get('error') or '') + ' ' + (d.get('message') or '')
        self.assertTrue(
            any(kw in err.lower() for kw in ['stripe', 'api key', 'config']),
            f'500 error should mention Stripe config issue. Got: {err!r}')


# ============================================================================
# STRIPE WEBHOOK
# ============================================================================

class TestStripeWebhook(unittest.TestCase):
    """POST /webhook/stripe — Stripe-signed event handler."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User
        cls.app = app
        cls.db = db
        cls.User = User

    def setUp(self):
        self.client = self.app.test_client(use_cookies=False)
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-pay.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-pay.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def test_invalid_signature_returns_400(self):
        """Webhook with bogus/missing signature MUST be rejected. If this
        regresses, anyone could POST fake purchase events and get free
        subscriptions activated."""
        with patch('app.stripe.Webhook.construct_event') as mock_construct:
            import stripe as _stripe
            mock_construct.side_effect = _stripe.error.SignatureVerificationError(
                'Invalid sig', 'sig_header'
            )
            r = self.client.post('/webhook/stripe',
                                 data='{"some": "payload"}',
                                 headers={'Stripe-Signature': 'fake_sig'})
        self.assertEqual(r.status_code, 400,
            'CRITICAL: invalid Stripe signature should return 400. '
            'If 200, fake events could activate subscriptions.')

    def test_invalid_payload_returns_400(self):
        """Webhook with unparseable body returns 400."""
        with patch('app.stripe.Webhook.construct_event') as mock_construct:
            mock_construct.side_effect = ValueError('Bad payload')
            r = self.client.post('/webhook/stripe',
                                 data='not json',
                                 headers={'Stripe-Signature': 'whatever'})
        self.assertEqual(r.status_code, 400)

    def test_buyer_subscription_activated_on_completed_event(self):
        """checkout.session.completed for buyer_pro plan MUST set
        user.subscription_plan='buyer_pro' and subscription_status='active'."""
        with self.app.app_context():
            u = self.User(email=_unique_email('webhk_buyer'), name='Buyer',
                          auth_provider='email', tier='free',
                          analysis_credits=1,
                          subscription_plan=None,
                          subscription_status='inactive')
            u.set_password('Pass1234!')
            self.db.session.add(u)
            self.db.session.commit()
            uid = u.id

        # Build a fake event that mock_construct returns
        fake_event = {
            'type': 'checkout.session.completed',
            'data': {'object': {
                'metadata': {
                    'user_id': str(uid),
                    'plan': 'buyer_pro',
                    'credits': '0',
                },
                'amount_total': 7900,
                'customer': 'cus_test_buyer123',
                'subscription': 'sub_test_buyer123',
                'customer_email': 'webhook_test@e2e-pay.test.offerwise.ai',
            }},
        }

        with patch('app.stripe.Webhook.construct_event', return_value=fake_event), \
             patch('app.send_email', return_value=True):
            r = self.client.post('/webhook/stripe',
                                 data=json.dumps({}),
                                 headers={'Stripe-Signature': 'fake'})

        # Webhook should accept (any 2xx); the side effect is the DB change
        self.assertIn(r.status_code, [200, 204],
            f'Webhook should accept valid event, got {r.status_code}')

        with self.app.app_context():
            u = self.User.query.get(uid)
            self.assertEqual(u.subscription_plan, 'buyer_pro',
                'Buyer plan must be set on user after checkout.session.completed')
            self.assertEqual(u.subscription_status, 'active',
                'subscription_status must be "active" after webhook')
            self.assertEqual(u.stripe_customer_id, 'cus_test_buyer123',
                'stripe_customer_id must be persisted from webhook payload')

    # v5.88.10 (Path B Release 2 expansion):
    # The original Release 2 covered buyer subscription only. Adding
    # inspector_pro and contractor_pro paths plus error-tolerance tests.

    def test_inspector_pro_webhook_activates_inspector(self):
        """checkout.session.completed for inspector_pro must:
          - Find the Inspector record by user_id
          - Set inspector.plan='inspector_pro'
          - Set monthly_quota=-1 (unlimited)
          - Set quota_reset_at ~30 days out
        If the user has no Inspector record, the webhook must NOT crash
        (it logs and moves on)."""
        from models import Inspector
        with self.app.app_context():
            user = self.User(
                email=_unique_email('insp_pro'), name='Inspector',
                auth_provider='email', tier='free', analysis_credits=1,
            )
            user.set_password('InspPro123!')
            self.db.session.add(user)
            self.db.session.flush()
            uid = user.id

            insp = Inspector(
                user_id=uid,
                plan='inspector_free',
                monthly_quota=5,
            )
            self.db.session.add(insp)
            self.db.session.commit()
            insp_id = insp.id

        fake_event = {
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'metadata': {
                        'user_id': str(uid),
                        'plan': 'inspector_pro',
                        'credits': '0',
                    },
                    'amount_total': 4900,
                    'customer': 'cus_test_insp_xyz',
                    'subscription': 'sub_test_insp_xyz',
                }
            }
        }

        with patch('app.stripe.Webhook.construct_event', return_value=fake_event):
            r = self.client.post('/webhook/stripe',
                                 data=b'{}',
                                 headers={'Stripe-Signature': 'mocked'})
        self.assertEqual(r.status_code, 200)

        with self.app.app_context():
            insp = Inspector.query.get(insp_id)
            self.assertEqual(insp.plan, 'inspector_pro',
                'Inspector plan must be inspector_pro after webhook')
            self.assertEqual(insp.monthly_quota, -1,
                'inspector_pro must have unlimited quota (-1)')
            self.assertIsNotNone(insp.quota_reset_at,
                'quota_reset_at must be set on inspector_pro activation')

            # Cleanup
            Inspector.query.filter_by(id=insp_id).delete()
            self.db.session.commit()

    def test_contractor_subscription_webhook_activates_contractor(self):
        """contractor_pro webhook must:
          - Find Contractor by email (NOT user_id — different relation)
          - Set plan='contractor_pro'
          - Set status='active'
          - Set monthly_lead_limit=50 (per v5.88.94 corrected pricing).
            Was -1/unlimited pre-v5.88.94 but that was false advertising
            since the enforcement layer always capped at 50.
        """
        from models import Contractor
        email = _unique_email('contr_pro')
        with self.app.app_context():
            user = self.User(
                email=email, name='Contractor',
                auth_provider='email', tier='free', analysis_credits=1,
            )
            user.set_password('ContrPro123!')
            self.db.session.add(user)
            self.db.session.flush()
            uid = user.id

            contractor = Contractor(
                email=email,
                name='Contractor',
                plan='contractor_starter',
                status='active',
                monthly_lead_limit=5,
            )
            self.db.session.add(contractor)
            self.db.session.commit()
            contr_id = contractor.id

        fake_event = {
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'metadata': {
                        'user_id': str(uid),
                        'plan': 'contractor_pro',
                        'credits': '0',
                    },
                    'amount_total': 9900,
                    'customer': 'cus_test_contr_xyz',
                    'subscription': 'sub_test_contr_xyz',
                }
            }
        }

        with patch('app.stripe.Webhook.construct_event', return_value=fake_event):
            r = self.client.post('/webhook/stripe',
                                 data=b'{}',
                                 headers={'Stripe-Signature': 'mocked'})
        self.assertEqual(r.status_code, 200)

        with self.app.app_context():
            contractor = Contractor.query.get(contr_id)
            self.assertEqual(contractor.plan, 'contractor_pro')
            self.assertEqual(contractor.status, 'active')
            # v5.89.30: was asserting -1 (unlimited). Corrected to 50 per
            # v5.88.94 — Pro's enforced limit is and has always been 50/month.
            self.assertEqual(contractor.monthly_lead_limit, 50,
                'contractor_pro: 50 leads/month (corrected v5.88.94)')
            self.assertEqual(contractor.stripe_customer_id, 'cus_test_contr_xyz')

            # Cleanup
            Contractor.query.filter_by(id=contr_id).delete()
            self.db.session.commit()

    def test_webhook_unknown_user_does_not_crash(self):
        """Webhook for a user_id that no longer exists must NOT crash.
        Stripe retries failed webhooks aggressively; a 500 here causes
        retry storms. Return 200 so Stripe stops retrying."""
        fake_event = {
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'metadata': {
                        'user_id': '99999999',  # doesn't exist
                        'plan': 'buyer_pro',
                        'credits': '0',
                    },
                    'amount_total': 1900,
                    'customer': 'cus_test_unknown',
                }
            }
        }

        with patch('app.stripe.Webhook.construct_event', return_value=fake_event):
            r = self.client.post('/webhook/stripe',
                                 data=b'{}',
                                 headers={'Stripe-Signature': 'mocked'})
        self.assertIn(r.status_code, [200, 204],
            f'Webhook for unknown user must not crash, got {r.status_code}. '
            f'A 500 here causes Stripe retry storms.')

    def test_webhook_unknown_event_type_returns_200(self):
        """Stripe sends ~50 event types. We only handle a few. The rest
        must return 200 quickly so Stripe doesn't retry indefinitely."""
        fake_event = {
            'type': 'invoice.payment_failed',  # we don't handle this
            'data': {'object': {}}
        }
        with patch('app.stripe.Webhook.construct_event', return_value=fake_event):
            r = self.client.post('/webhook/stripe',
                                 data=b'{}',
                                 headers={'Stripe-Signature': 'mocked'})
        self.assertIn(r.status_code, [200, 204],
            'Unknown Stripe event types must return 200, not 4xx/5xx — '
            'otherwise Stripe spams retries')


# ============================================================================
# PAYMENT SUCCESS / CANCEL PAGES
# ============================================================================

class TestPaymentPages(unittest.TestCase):
    """/payment/success and /payment/cancel — auth gates."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User
        cls.app = app
        cls.db = db
        cls.User = User

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-pay.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.User.query.filter(
                self.User.email.like('%@e2e-pay.test.offerwise.ai')
            ).delete()
            self.db.session.commit()

    def test_payment_success_anonymous_redirects(self):
        client = self.app.test_client(use_cookies=False)
        r = client.get('/payment/success', follow_redirects=False)
        self.assertNotEqual(r.status_code, 200,
            '/payment/success served HTML to anonymous user')

    def test_payment_cancel_anonymous_redirects(self):
        client = self.app.test_client(use_cookies=False)
        r = client.get('/payment/cancel', follow_redirects=False)
        self.assertNotEqual(r.status_code, 200,
            '/payment/cancel served HTML to anonymous user')

    def test_payment_success_authenticated_returns_200(self):
        with self.app.app_context():
            u = self.User(email=_unique_email('paysucc'), name='X',
                          auth_provider='email', tier='free',
                          analysis_credits=1)
            u.set_password('Pass1234!')
            self.db.session.add(u)
            self.db.session.commit()
            uid = u.id

        _login_session(self.client, uid)
        r = self.client.get('/payment/success?session_id=cs_test_123')
        self.assertEqual(r.status_code, 200,
            f'Authenticated /payment/success should be 200, got {r.status_code}')


# ============================================================================
# PRICING_TIERS contract
# ============================================================================

class TestPricingTiersContract(unittest.TestCase):
    """The PRICING_TIERS dict is a contract: certain plans MUST exist
    with specific prices, or webhooks/checkouts will silently misbehave."""

    def test_inspector_pro_is_49_dollars(self):
        from auth_config import PRICING_TIERS
        self.assertEqual(PRICING_TIERS['inspector_pro']['price'], 49)

    def test_contractor_starter_is_49(self):
        from auth_config import PRICING_TIERS
        self.assertEqual(PRICING_TIERS['contractor_starter']['price'], 49)
        # v5.89.30: was asserting 5; corrected to 10 per v5.88.94 alignment
        # of auth_config.py to the limit contractor_routes.py actually enforces.
        # The pre-v5.88.94 value of 5 was false advertising — Starter has always
        # been 10 leads/month at the enforcement layer.
        self.assertEqual(PRICING_TIERS['contractor_starter']['monthly_lead_limit'], 10)
        self.assertEqual(PRICING_TIERS['contractor_starter']['zip_limit'], 3)

    def test_contractor_pro_is_99(self):
        from auth_config import PRICING_TIERS
        self.assertEqual(PRICING_TIERS['contractor_pro']['price'], 99)
        # v5.89.30: was asserting -1 (unlimited). Corrected to 50 per
        # v5.88.94 — Pro was previously advertised as "unlimited" but the
        # backend has always enforced 50/month. v5.88.94 aligned auth_config
        # to the enforced value (and stopped the false advertising). The
        # assertion + message both updated here.
        self.assertEqual(PRICING_TIERS['contractor_pro']['monthly_lead_limit'], 50,
            'Contractor Pro: 50 leads/month (corrected v5.88.94 — was previously falsely advertised as unlimited)')
        self.assertEqual(PRICING_TIERS['contractor_pro']['zip_limit'], 10)

    def test_contractor_enterprise_is_199(self):
        from auth_config import PRICING_TIERS
        self.assertEqual(PRICING_TIERS['contractor_enterprise']['price'], 199)
        self.assertEqual(PRICING_TIERS['contractor_enterprise']['zip_limit'], -1,
            'Contractor Enterprise should be statewide (-1)')

    def test_inspector_free_has_5_monthly_quota(self):
        from auth_config import PRICING_TIERS
        self.assertEqual(PRICING_TIERS['inspector_free']['monthly_quota'], 5)
        self.assertEqual(PRICING_TIERS['inspector_free']['price'], 0)

    def test_inspector_pro_unlimited_quota(self):
        from auth_config import PRICING_TIERS
        self.assertEqual(PRICING_TIERS['inspector_pro']['monthly_quota'], -1)

    def test_buyer_starter_is_9_dollars(self):
        from auth_config import PRICING_TIERS
        self.assertEqual(PRICING_TIERS['buyer_starter']['price'], 9)
        self.assertEqual(PRICING_TIERS['buyer_starter']['analyses_per_month'], 10)

    def test_buyer_pro_is_19_dollars(self):
        from auth_config import PRICING_TIERS
        self.assertEqual(PRICING_TIERS['buyer_pro']['price'], 19)
        self.assertEqual(PRICING_TIERS['buyer_pro']['analyses_per_month'], 30)

    def test_buyer_unlimited_is_49_and_actually_unlimited(self):
        """The Unlimited tier MUST have analyses_per_month=-1.
        If it ever gets a positive number, users who paid for unlimited
        get silently capped at that number — refund-worthy bug."""
        from auth_config import PRICING_TIERS
        self.assertEqual(PRICING_TIERS['buyer_unlimited']['price'], 49)
        self.assertEqual(
            PRICING_TIERS['buyer_unlimited']['analyses_per_month'], -1,
            'CRITICAL: buyer_unlimited must be analyses_per_month=-1. '
            'Any other value silently caps users who paid for unlimited.'
        )

    def test_free_tier_is_zero(self):
        from auth_config import PRICING_TIERS
        self.assertEqual(PRICING_TIERS['free']['price'], 0)
        self.assertEqual(PRICING_TIERS['free']['analyses_per_month'], 1,
            'Free tier should give 1 analysis/month — the hook')


if __name__ == '__main__':
    unittest.main()
