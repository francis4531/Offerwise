"""
test_e2e_tickets.py — v5.88.34

Tests for the new Support Ticket workstation:
  - In-product share creates a Ticket + first TicketMessage
  - Legacy SupportShare model still importable (no migration runs)
  - Inbound email scaffolding (uses helper directly, not webhook)
  - Status transitions (allowed + blocked)
  - Subject formatting helpers
  - Admin notification dispatched (mocked, just verify call)
"""
import json
import os
import unittest
from unittest.mock import patch

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-tickets'
os.environ['DATABASE_URL'] = 'sqlite:///test_e2e_tickets.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-tickets-key')
os.environ['RATELIMIT_ENABLED'] = 'false'

if os.path.exists('test_e2e_tickets.db'):
    os.remove('test_e2e_tickets.db')


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _make_test_app():
    """Get the production Flask app singleton."""
    from app import app
    return app


def _create_user_and_property(app, db, User, Property, Analysis,
                              email_suffix='ticket-test',
                              tier='premium',
                              credits=10):
    from datetime import datetime
    import time
    unique = int(time.time() * 1000000)
    email = f'tickettest_{unique}@e2e-{email_suffix}.test.example.com'

    with app.app_context():
        user = User(email=email, name='Ticket Tester',
                    auth_provider='email', tier=tier,
                    analysis_credits=credits, analyses_completed=1)
        user.set_password('TicketTest123!')
        db.session.add(user)
        db.session.commit()
        uid = user.id

        prop = Property(user_id=uid, address='123 Ticket Lane, Test City, CA',
                        price=500000, analyzed_at=datetime.utcnow())
        db.session.add(prop)
        db.session.commit()
        pid = prop.id

        analysis = Analysis(property_id=pid, status='completed',
                            risk_tier='LOW',
                            result_json=json.dumps({
                                'risk_score':       {'risk_tier': 'LOW',
                                                     'total_repair_cost_low': 1000,
                                                     'total_repair_cost_high': 3000},
                                'offer_strategy':   {'recommended_offer': 485000,
                                                     'discount_percentage': 3.0},
                                'transparency_report': {'transparency_score': 88},
                                'risk_dna':         {'composite_score': 18.0},
                                'findings':         [],
                            }))
        db.session.add(analysis)
        db.session.commit()

        return uid, pid, email


def _login_via_endpoint(client, email, password='TicketTest123!'):
    """Authenticate using the real /auth/login-email endpoint (same approach
    as cassette tests after the v5.88.31 fix)."""
    r = client.post('/auth/login-email', json={
        'email': email, 'password': password,
    })
    return r.status_code == 200


# -----------------------------------------------------------------------------
# Subject formatting helpers
# -----------------------------------------------------------------------------

class TestSubjectHelpers(unittest.TestCase):
    """format_ticket_subject and extract_ticket_id."""

    def test_format_adds_prefix(self):
        from support_service import format_ticket_subject
        self.assertEqual(
            format_ticket_subject(42, 'Help with my analysis'),
            '[Ticket #42] Help with my analysis')

    def test_format_idempotent(self):
        from support_service import format_ticket_subject
        already = '[Ticket #7] Re: Previous subject'
        self.assertEqual(format_ticket_subject(7, already), already)

    def test_extract_present(self):
        from support_service import extract_ticket_id
        self.assertEqual(extract_ticket_id('[Ticket #15] hello'), 15)
        # Case-insensitive
        self.assertEqual(extract_ticket_id('[ticket #99] casing'), 99)

    def test_extract_absent(self):
        from support_service import extract_ticket_id
        self.assertIsNone(extract_ticket_id('Just a subject'))
        self.assertIsNone(extract_ticket_id(''))
        self.assertIsNone(extract_ticket_id(None))


# -----------------------------------------------------------------------------
# In-product share creates a Ticket
# -----------------------------------------------------------------------------

class TestInProductShareCreatesTicket(unittest.TestCase):
    """The /api/support/share endpoint creates a Ticket (not a SupportShare)."""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_test_app()
        from models import db, User, Property, Analysis
        cls.db = db
        cls.User = User
        cls.Property = Property
        cls.Analysis = Analysis

    def setUp(self):
        self.client = self.app.test_client(use_cookies=True)
        # Patch admin notification to avoid trying to send real email
        self._patcher = patch('support_service.send_admin_notification',
                              return_value=True)
        self.mock_notify = self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_share_creates_ticket_with_first_message(self):
        uid, pid, email = _create_user_and_property(
            self.app, self.db, self.User, self.Property, self.Analysis)
        self.assertTrue(_login_via_endpoint(self.client, email))

        r = self.client.post('/api/support/share', json={
            'property_id': pid,
            'message': 'My OfferScore looks too low, can you help?',
        })
        self.assertEqual(r.status_code, 200, r.data)
        body = r.get_json()
        self.assertTrue(body.get('success'))
        self.assertIn('ticket_id', body)
        self.assertIn('share_id', body, 'backward-compat: share_id alias kept')
        self.assertEqual(body['ticket_id'], body['share_id'])

        # Now verify the Ticket + TicketMessage exist
        from models import Ticket, TicketMessage
        with self.app.app_context():
            ticket = Ticket.query.get(body['ticket_id'])
            self.assertIsNotNone(ticket)
            self.assertEqual(ticket.user_id, uid)
            self.assertEqual(ticket.property_id, pid)
            self.assertEqual(ticket.source, 'in_product_share')
            self.assertEqual(ticket.status, 'open')
            self.assertIn('123 Ticket Lane', ticket.subject)

            msgs = ticket.messages.all()
            self.assertEqual(len(msgs), 1)
            self.assertEqual(msgs[0].author_kind, 'user')
            self.assertEqual(msgs[0].author_user_id, uid)
            self.assertIn('OfferScore looks too low', msgs[0].body)
            # Snapshot was captured on the first message
            self.assertIsNotNone(msgs[0].snapshot_json)
            snapshot = json.loads(msgs[0].snapshot_json)
            self.assertEqual(snapshot['price'], 500000)

    def test_share_dispatches_admin_notification(self):
        uid, pid, email = _create_user_and_property(
            self.app, self.db, self.User, self.Property, self.Analysis)
        self.assertTrue(_login_via_endpoint(self.client, email))

        r = self.client.post('/api/support/share', json={
            'property_id': pid, 'message': 'Question about my report',
        })
        self.assertEqual(r.status_code, 200)
        # Admin notification helper was called exactly once
        self.assertEqual(self.mock_notify.call_count, 1)


# -----------------------------------------------------------------------------
# Inbound email scaffolding (v5.88.34 ships the helper; webhook in v5.88.36)
# -----------------------------------------------------------------------------

class TestInboundEmailScaffolding(unittest.TestCase):
    """create_ticket_from_inbound_email — the helper that v5.88.36's webhook will call."""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_test_app()
        from models import db, User
        cls.db = db
        cls.User = User

    def setUp(self):
        # Suppress admin notifications during this test
        self._patcher = patch('support_service.send_admin_notification',
                              return_value=True)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_new_email_creates_new_ticket(self):
        from support_service import create_ticket_from_inbound_email
        with self.app.app_context():
            ticket, is_new = create_ticket_from_inbound_email(
                from_email='stranger@example.com',
                subject='Question about your service',
                body='Does your tool work for condos?',
            )
            self.assertTrue(is_new)
            self.assertEqual(ticket.source, 'inbound_email')
            self.assertIsNone(ticket.user_id)
            self.assertEqual(ticket.email_for_anonymous, 'stranger@example.com')
            self.assertEqual(ticket.status, 'open')
            msgs = ticket.messages.all()
            self.assertEqual(len(msgs), 1)
            self.assertEqual(msgs[0].author_email, 'stranger@example.com')

    def test_reply_email_appends_to_existing_ticket(self):
        from support_service import create_ticket_from_inbound_email
        with self.app.app_context():
            # First inbound — new ticket
            ticket1, new1 = create_ticket_from_inbound_email(
                from_email='thread@example.com',
                subject='Initial subject',
                body='First message',
            )
            self.assertTrue(new1)
            initial_id = ticket1.id

            # Simulate admin replying (this would normally happen in v5.88.35).
            # For now we just mark the ticket as awaiting user.
            from support_service import transition_ticket_status
            transition_ticket_status(ticket1, 'in_progress')
            transition_ticket_status(ticket1, 'waiting_on_user')

        # Second inbound, with [Ticket #N] prefix — should APPEND
        with self.app.app_context():
            ticket2, new2 = create_ticket_from_inbound_email(
                from_email='thread@example.com',
                subject=f'Re: [Ticket #{initial_id}] Initial subject',
                body='Reply from user',
            )
            self.assertFalse(new2, 'Expected reply to attach to existing ticket')
            self.assertEqual(ticket2.id, initial_id)
            # User reply on a waiting ticket should flip back to in_progress
            self.assertEqual(ticket2.status, 'in_progress')
            msgs = ticket2.messages.all()
            # 1 user msg + 2 system status changes + 1 reply
            self.assertGreaterEqual(len(msgs), 2)


# -----------------------------------------------------------------------------
# Status transitions
# -----------------------------------------------------------------------------

class TestStatusTransitions(unittest.TestCase):
    """transition_ticket_status — workflow guard rails."""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_test_app()
        from models import db, Ticket
        cls.db = db
        cls.Ticket = Ticket

    def _new_open_ticket(self):
        from models import Ticket
        with self.app.app_context():
            t = Ticket(
                email_for_anonymous='trans-test@example.com',
                subject='Transition test',
                source='manual',
                status='open',
            )
            self.db.session.add(t)
            self.db.session.commit()
            return t.id

    def test_open_to_in_progress_allowed(self):
        from support_service import transition_ticket_status
        tid = self._new_open_ticket()
        with self.app.app_context():
            t = self.Ticket.query.get(tid)
            ok = transition_ticket_status(t, 'in_progress')
            self.assertTrue(ok)
            self.assertEqual(t.status, 'in_progress')

    def test_open_to_waiting_on_user_blocked(self):
        from support_service import transition_ticket_status
        tid = self._new_open_ticket()
        with self.app.app_context():
            t = self.Ticket.query.get(tid)
            ok = transition_ticket_status(t, 'waiting_on_user')
            self.assertFalse(ok, 'open -> waiting_on_user should not be allowed directly')
            self.assertEqual(t.status, 'open')

    def test_resolved_sets_resolved_at(self):
        from support_service import transition_ticket_status
        tid = self._new_open_ticket()
        with self.app.app_context():
            t = self.Ticket.query.get(tid)
            transition_ticket_status(t, 'resolved')
            self.assertEqual(t.status, 'resolved')
            self.assertIsNotNone(t.resolved_at)

    def test_resolved_to_reopened_allowed(self):
        from support_service import transition_ticket_status
        tid = self._new_open_ticket()
        with self.app.app_context():
            t = self.Ticket.query.get(tid)
            transition_ticket_status(t, 'resolved')
            ok = transition_ticket_status(t, 'reopened')
            self.assertTrue(ok)
            self.assertEqual(t.status, 'reopened')

    def test_invalid_status_rejected(self):
        from support_service import transition_ticket_status
        tid = self._new_open_ticket()
        with self.app.app_context():
            t = self.Ticket.query.get(tid)
            ok = transition_ticket_status(t, 'bogus_state')
            self.assertFalse(ok)
            self.assertEqual(t.status, 'open')


# -----------------------------------------------------------------------------
# Legacy SupportShare model still importable (no DB migration runs yet)
# -----------------------------------------------------------------------------

class TestLegacySupportShareRemoved(unittest.TestCase):
    """v5.88.38: SupportShare class is gone. Verify the import fails so
    nothing else can sneak in and reference it. Also verify the legacy
    GET endpoint returns 410 Gone with a migration note instead of 404."""

    def test_import_raises(self):
        with self.assertRaises(ImportError):
            from models import SupportShare  # noqa: F401

    def test_legacy_endpoint_returns_410(self):
        """The old /api/admin/support-shares URL exists as a permanent
        410 Gone stub so any stale code referencing it gets a clear
        signal instead of a mystery 404."""
        app = _make_test_app()
        client = app.test_client()
        admin_key = os.environ.get('ADMIN_KEY', 'test-admin-tickets-key')
        r = client.get(f'/api/admin/support-shares?admin_key={admin_key}')
        self.assertEqual(r.status_code, 410)
        body = r.get_json() or {}
        self.assertIn('migration_note', body)
        self.assertIn('Ticket', body.get('migration_note', ''))


# -----------------------------------------------------------------------------
# Admin endpoints
# -----------------------------------------------------------------------------

ADMIN_KEY = os.environ['ADMIN_KEY']


def _admin_url(path):
    sep = '&' if '?' in path else '?'
    return f'{path}{sep}admin_key={ADMIN_KEY}'


class TestAdminListAndGetTickets(unittest.TestCase):
    """The new admin endpoints used by the Inbox UI."""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_test_app()
        from models import db, User, Property, Analysis, Ticket, TicketMessage
        cls.db = db
        cls.User = User
        cls.Property = Property
        cls.Analysis = Analysis
        cls.Ticket = Ticket
        cls.TicketMessage = TicketMessage

    def setUp(self):
        self.client = self.app.test_client()
        self._patcher = patch('support_service.send_admin_notification',
                              return_value=True)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_list_tickets_returns_counts(self):
        # Seed: one ticket in each of a few states
        from support_service import (create_ticket_from_inbound_email,
                                     transition_ticket_status)
        with self.app.app_context():
            t1, _ = create_ticket_from_inbound_email(
                'a@x.com', 'A subject', 'A body')
            t2, _ = create_ticket_from_inbound_email(
                'b@x.com', 'B subject', 'B body')
            transition_ticket_status(t2, 'in_progress')
            t3, _ = create_ticket_from_inbound_email(
                'c@x.com', 'C subject', 'C body')
            transition_ticket_status(t3, 'in_progress')
            transition_ticket_status(t3, 'resolved')

        r = self.client.get(_admin_url('/api/admin/tickets?status=all'))
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIn('tickets', body)
        self.assertIn('counts', body)
        self.assertGreaterEqual(body['counts']['open'], 1)
        self.assertGreaterEqual(body['counts']['in_progress'], 1)
        self.assertGreaterEqual(body['counts']['resolved'], 1)

    def test_get_ticket_returns_messages(self):
        from support_service import create_ticket_from_inbound_email
        with self.app.app_context():
            ticket, _ = create_ticket_from_inbound_email(
                'detail@x.com', 'Detail subject', 'Detail body line.')
            tid = ticket.id

        r = self.client.get(_admin_url(f'/api/admin/tickets/{tid}'))
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body['id'], tid)
        self.assertEqual(body['source'], 'inbound_email')
        self.assertIn('messages', body)
        self.assertEqual(len(body['messages']), 1)
        self.assertEqual(body['messages'][0]['author_kind'], 'user')

    def test_status_patch_endpoint(self):
        from support_service import create_ticket_from_inbound_email
        with self.app.app_context():
            ticket, _ = create_ticket_from_inbound_email(
                'patch@x.com', 'Patch subject', 'body')
            tid = ticket.id

        # Allowed transition: open -> in_progress
        r = self.client.patch(
            _admin_url(f'/api/admin/tickets/{tid}/status'),
            data=json.dumps({'status': 'in_progress'}),
            content_type='application/json')
        self.assertEqual(r.status_code, 200)

        # Disallowed transition: in_progress -> reopened
        r = self.client.patch(
            _admin_url(f'/api/admin/tickets/{tid}/status'),
            data=json.dumps({'status': 'reopened'}),
            content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_legacy_support_shares_endpoint_returns_410(self):
        """v5.88.38: the old /api/admin/support-shares endpoint is gone.
        Any code still hitting it should see 410 Gone, not a 404 mystery."""
        r = self.client.get(_admin_url('/api/admin/support-shares'))
        self.assertEqual(r.status_code, 410)
        body = r.get_json()
        self.assertIn('tickets', (body or {}).get('error', '').lower())


# -----------------------------------------------------------------------------
# v5.88.35: Admin reply composer
# -----------------------------------------------------------------------------

class TestAdminReply(unittest.TestCase):
    """The admin reply endpoint composes/sends/persists/transitions."""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_test_app()
        from models import db, Ticket, TicketMessage
        cls.db = db
        cls.Ticket = Ticket
        cls.TicketMessage = TicketMessage

    def setUp(self):
        self.client = self.app.test_client()
        # Avoid real Resend calls — mock send_email so it returns True
        # but lets us assert on call args.
        # support_service imports send_email locally inside the function,
        # so patch at the module path it imports from.
        self._send_patcher = patch('email_service.send_email', return_value=True)
        self.mock_send = self._send_patcher.start()
        # Also suppress admin notifications on ticket creation
        self._notify_patcher = patch('support_service.send_admin_notification',
                                     return_value=True)
        self._notify_patcher.start()

    def tearDown(self):
        self._send_patcher.stop()
        self._notify_patcher.stop()

    def _make_open_ticket(self, email='reply-test@example.com',
                          subject='Need help'):
        from support_service import create_ticket_from_inbound_email
        with self.app.app_context():
            ticket, _ = create_ticket_from_inbound_email(
                from_email=email,
                subject=subject,
                body='Initial question from customer.',
            )
            return ticket.id

    def test_reply_sends_email_and_persists_message(self):
        tid = self._make_open_ticket()
        r = self.client.post(
            _admin_url(f'/api/admin/tickets/{tid}/reply'),
            data=json.dumps({'body': 'Thanks for reaching out — here is what to do.'}),
            content_type='application/json')
        self.assertEqual(r.status_code, 200, r.data)
        body = r.get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['message']['author_kind'], 'admin')
        self.assertEqual(body['ticket_status'], 'waiting_on_user',
                         'Reply on an open ticket should transition to waiting_on_user')

        # Email was sent through Resend exactly once
        self.assertEqual(self.mock_send.call_count, 1)
        # Inspect kwargs to verify the subject prefix and Reply-To
        call_kwargs = self.mock_send.call_args.kwargs
        self.assertIn('[Ticket #', call_kwargs['subject'])
        self.assertIn(f'#{tid}]', call_kwargs['subject'])
        self.assertEqual(call_kwargs['to_email'], 'reply-test@example.com')

    def test_reply_persists_even_if_email_fails(self):
        tid = self._make_open_ticket()
        # Make send_email return False (e.g. RESEND_API_KEY missing in test env)
        self.mock_send.return_value = False
        r = self.client.post(
            _admin_url(f'/api/admin/tickets/{tid}/reply'),
            data=json.dumps({'body': 'My reply text.'}),
            content_type='application/json')
        # The API still returns 200 — the message is saved, but the response
        # includes a warning so the UI can surface it.
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body['success'])
        self.assertIn('warning', body)

        # Message IS persisted
        with self.app.app_context():
            t = self.Ticket.query.get(tid)
            admin_msgs = [m for m in t.messages.all() if m.author_kind == 'admin']
            self.assertEqual(len(admin_msgs), 1)

    def test_internal_note_does_not_send_email_or_change_status(self):
        tid = self._make_open_ticket()
        r = self.client.post(
            _admin_url(f'/api/admin/tickets/{tid}/reply'),
            data=json.dumps({
                'body': 'Reminder: this user mentioned a deadline next week.',
                'internal_note': True,
            }),
            content_type='application/json')
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body['message']['author_kind'], 'note')
        self.assertEqual(body['ticket_status'], 'open',
                         'Internal note must not change status')
        # NO email sent
        self.assertEqual(self.mock_send.call_count, 0)

    def test_empty_body_rejected(self):
        tid = self._make_open_ticket()
        r = self.client.post(
            _admin_url(f'/api/admin/tickets/{tid}/reply'),
            data=json.dumps({'body': '   '}),  # whitespace only
            content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_overlong_body_rejected(self):
        tid = self._make_open_ticket()
        huge = 'x' * 20001
        r = self.client.post(
            _admin_url(f'/api/admin/tickets/{tid}/reply'),
            data=json.dumps({'body': huge}),
            content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_reply_to_nonexistent_ticket_404(self):
        r = self.client.post(
            _admin_url('/api/admin/tickets/999999/reply'),
            data=json.dumps({'body': 'Hello'}),
            content_type='application/json')
        self.assertEqual(r.status_code, 404)

    def test_reply_to_in_progress_ticket_transitions_to_waiting(self):
        from support_service import transition_ticket_status
        tid = self._make_open_ticket()
        with self.app.app_context():
            t = self.Ticket.query.get(tid)
            transition_ticket_status(t, 'in_progress')

        r = self.client.post(
            _admin_url(f'/api/admin/tickets/{tid}/reply'),
            data=json.dumps({'body': 'Working on it now.'}),
            content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['ticket_status'], 'waiting_on_user')

    def test_reply_subject_uses_ticket_prefix(self):
        """The outbound email subject must include [Ticket #N] so customer
        replies route back to the right thread (v5.88.36 inbound matcher)."""
        tid = self._make_open_ticket(subject='Question about my analysis')
        self.client.post(
            _admin_url(f'/api/admin/tickets/{tid}/reply'),
            data=json.dumps({'body': 'My reply.'}),
            content_type='application/json')
        subject = self.mock_send.call_args.kwargs['subject']
        self.assertIn(f'[Ticket #{tid}]', subject)
        self.assertIn('Question about my analysis', subject)


# -----------------------------------------------------------------------------
# v5.88.36: Inbound webhook
# -----------------------------------------------------------------------------

class TestSpamFilter(unittest.TestCase):
    """is_likely_spam_or_autoresponder — bouncer at the door."""

    def test_legitimate_user_passes(self):
        from support_service import is_likely_spam_or_autoresponder
        is_spam, _ = is_likely_spam_or_autoresponder(
            'alice@example.com', 'Question about my analysis',
            'Hi, my OfferScore looks wrong, can you check?'
        )
        self.assertFalse(is_spam)

    def test_mailer_daemon_dropped(self):
        from support_service import is_likely_spam_or_autoresponder
        is_spam, reason = is_likely_spam_or_autoresponder(
            'MAILER-DAEMON@mail.example.com', 'Delivery Status Notification',
            'Your message bounced.'
        )
        self.assertTrue(is_spam)
        self.assertIn('automated', reason.lower() + ' ' + reason.lower())

    def test_out_of_office_dropped(self):
        from support_service import is_likely_spam_or_autoresponder
        is_spam, _ = is_likely_spam_or_autoresponder(
            'alice@example.com', 'Out of Office: I am away until Monday',
            'I will be back Monday.'
        )
        self.assertTrue(is_spam)

    def test_noreply_sender_dropped(self):
        from support_service import is_likely_spam_or_autoresponder
        is_spam, _ = is_likely_spam_or_autoresponder(
            'noreply@somesite.com', 'Your account update', 'Hello.'
        )
        self.assertTrue(is_spam)

    def test_own_domain_loop_dropped(self):
        from support_service import is_likely_spam_or_autoresponder
        is_spam, _ = is_likely_spam_or_autoresponder(
            'support@getofferwise.ai', 'Re: something',
            'This should never become a ticket.'
        )
        self.assertTrue(is_spam)

    def test_empty_everything_dropped(self):
        from support_service import is_likely_spam_or_autoresponder
        is_spam, _ = is_likely_spam_or_autoresponder(
            'alice@example.com', '', ''
        )
        self.assertTrue(is_spam)

    def test_missing_from_dropped(self):
        from support_service import is_likely_spam_or_autoresponder
        is_spam, _ = is_likely_spam_or_autoresponder('', 'subject', 'body')
        self.assertTrue(is_spam)


class TestSignatureVerification(unittest.TestCase):
    """verify_resend_signature — HMAC against the Svix headers."""

    def _make_signature(self, secret_b64: str, msg_id: str,
                        timestamp: str, body: bytes) -> str:
        """Helper: produce a valid v1 signature for the given inputs.
        Matches the algorithm Resend documents."""
        import hmac as _hmac, hashlib as _hashlib, base64 as _b64
        key = _b64.b64decode(secret_b64)
        signed = f'{msg_id}.{timestamp}.{body.decode("utf-8")}'
        return 'v1,' + _b64.b64encode(
            _hmac.new(key, signed.encode('utf-8'), _hashlib.sha256).digest()
        ).decode('ascii')

    def test_valid_signature_accepted(self):
        from support_service import verify_resend_signature
        import base64
        key_bytes = b'supersecretwebhookkey1234567890abc'
        secret = 'whsec_' + base64.b64encode(key_bytes).decode('ascii')
        body = b'{"type":"email.received","data":{"email_id":"abc"}}'
        msg_id = 'msg_test_1'
        ts = '1700000000'
        sig = self._make_signature(
            base64.b64encode(key_bytes).decode('ascii'),
            msg_id, ts, body
        )
        headers = {
            'svix-id': msg_id, 'svix-timestamp': ts, 'svix-signature': sig,
        }
        self.assertTrue(verify_resend_signature(body, headers, secret))

    def test_invalid_signature_rejected(self):
        from support_service import verify_resend_signature
        body = b'{"x":1}'
        headers = {
            'svix-id': 'msg1', 'svix-timestamp': '1700000000',
            'svix-signature': 'v1,bogusbase64sig',
        }
        self.assertFalse(verify_resend_signature(body, headers, 'whsec_abc'))

    def test_missing_headers_rejected(self):
        from support_service import verify_resend_signature
        self.assertFalse(verify_resend_signature(b'{}', {}, 'whsec_x'))

    def test_no_secret_rejected(self):
        from support_service import verify_resend_signature
        self.assertFalse(verify_resend_signature(
            b'{}', {'svix-id': 'a', 'svix-timestamp': '1', 'svix-signature': 'v1,b'},
            ''))


class TestInboundWebhook(unittest.TestCase):
    """End-to-end webhook flow with mocked Resend body-fetch + signature."""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_test_app()
        from models import db, Ticket, TicketMessage
        cls.db = db
        cls.Ticket = Ticket
        cls.TicketMessage = TicketMessage

    def setUp(self):
        self.client = self.app.test_client()
        # Set a webhook secret for the duration of this test
        import base64
        self._key = b'testkey_supersecret_1234567890ab'
        self._secret = 'whsec_' + base64.b64encode(self._key).decode('ascii')
        self._env = patch.dict(os.environ, {
            'RESEND_WEBHOOK_SECRET': self._secret,
            'SUPPORT_EMAIL': 'support@getofferwise.ai',
        })
        self._env.start()
        # Block real Resend body fetches; return canned text body
        self._fetch_patcher = patch(
            'webhooks_routes.fetch_inbound_email_body',
            return_value=('Hello, this is the body of the email.', None)
        )
        self._fetch_patcher.start()
        # Block real admin notification emails
        self._notify_patcher = patch(
            'support_service.send_admin_notification', return_value=True
        )
        self._notify_patcher.start()

    def tearDown(self):
        self._env.stop()
        self._fetch_patcher.stop()
        self._notify_patcher.stop()

    def _post_event(self, event: dict, sign: bool = True):
        """Helper: POST a webhook event with valid signature."""
        import base64, hmac as _hmac, hashlib as _hashlib
        body = json.dumps(event).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if sign:
            msg_id = 'msg_test_' + str(event.get('data', {}).get('email_id', 'x'))
            ts = '1700000000'
            signed = f'{msg_id}.{ts}.{body.decode("utf-8")}'
            sig = 'v1,' + base64.b64encode(
                _hmac.new(self._key, signed.encode('utf-8'),
                          _hashlib.sha256).digest()
            ).decode('ascii')
            headers.update({
                'Svix-Id': msg_id,
                'Svix-Timestamp': ts,
                'Svix-Signature': sig,
            })
        return self.client.post(
            '/api/webhooks/resend/inbound',
            data=body, headers=headers,
        )

    def test_valid_webhook_creates_ticket(self):
        # v5.88.37: use a unique email_id per test invocation. The
        # idempotency check (has_inbound_message_been_processed) means a
        # repeated test run with the same id will silently no-op, which
        # breaks the assertion. Time-based suffix keeps it fresh.
        import time
        eid = f'webhook_test_{int(time.time() * 1000000)}'
        event = {
            'type': 'email.received',
            'data': {
                'email_id': eid,
                'from': 'newuser@example.com',
                'to': ['support@getofferwise.ai'],
                'subject': 'I need help with my analysis',
            }
        }
        r = self._post_event(event)
        self.assertEqual(r.status_code, 200, r.data)
        body = r.get_json()
        self.assertTrue(body['success'])
        self.assertTrue(body['is_new'])
        # Ticket exists, has the inbound id on its first message
        with self.app.app_context():
            t = self.Ticket.query.get(body['ticket_id'])
            self.assertIsNotNone(t)
            self.assertEqual(t.source, 'inbound_email')
            self.assertEqual(t.email_for_anonymous, 'newuser@example.com')
            msgs = t.messages.all()
            self.assertEqual(len(msgs), 1)
            self.assertEqual(msgs[0].inbound_message_id, eid)

    def test_invalid_signature_rejected(self):
        event = {
            'type': 'email.received',
            'data': {'email_id': 'sig_test_1', 'from': 'a@b.com',
                     'to': ['support@getofferwise.ai'], 'subject': 'x'}
        }
        # Skip signing
        body = json.dumps(event).encode('utf-8')
        r = self.client.post(
            '/api/webhooks/resend/inbound',
            data=body,
            headers={'Content-Type': 'application/json',
                     'Svix-Id': 'x', 'Svix-Timestamp': '1',
                     'Svix-Signature': 'v1,wrong'},
        )
        self.assertEqual(r.status_code, 401)

    def test_no_secret_returns_503(self):
        with patch.dict(os.environ, {'RESEND_WEBHOOK_SECRET': ''}, clear=False):
            r = self.client.post('/api/webhooks/resend/inbound',
                                 data=b'{}',
                                 headers={'Content-Type': 'application/json'})
            self.assertEqual(r.status_code, 503)

    def test_non_email_received_event_ignored_200(self):
        event = {'type': 'email.sent', 'data': {}}
        r = self._post_event(event)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json().get('ignored'))

    def test_wrong_recipient_ignored_200(self):
        event = {
            'type': 'email.received',
            'data': {
                'email_id': 'wrongto_1',
                'from': 'alice@example.com',
                'to': ['random@getofferwise.ai'],  # not support@
                'subject': 'Hi',
            }
        }
        r = self._post_event(event)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json().get('reason'), 'wrong recipient')

    def test_spam_sender_silently_dropped(self):
        event = {
            'type': 'email.received',
            'data': {
                'email_id': 'spam_1',
                'from': 'MAILER-DAEMON@mail.example.com',
                'to': ['support@getofferwise.ai'],
                'subject': 'Delivery Status Notification (Failure)',
            }
        }
        r = self._post_event(event)
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body.get('ignored'))
        self.assertIn('automated', body.get('reason', '').lower() + ' '
                      + body.get('reason', '').lower())

    def test_out_of_office_silently_dropped(self):
        event = {
            'type': 'email.received',
            'data': {
                'email_id': 'ooo_1',
                'from': 'alice@example.com',
                'to': ['support@getofferwise.ai'],
                'subject': 'Out of Office: I am away until next week',
            }
        }
        r = self._post_event(event)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json().get('ignored'))

    def test_duplicate_email_id_ignored(self):
        import time
        # v5.88.37: use unique email_id so we don't collide with prior runs
        eid = f'dup_test_{int(time.time() * 1000000)}'
        event = {
            'type': 'email.received',
            'data': {
                'email_id': eid,
                'from': 'alice@example.com',
                'to': ['support@getofferwise.ai'],
                'subject': 'Real question',
            }
        }
        # First call creates the ticket
        r1 = self._post_event(event)
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.get_json()['success'])

        # Second call with same email_id should idempotently no-op
        r2 = self._post_event(event)
        self.assertEqual(r2.status_code, 200)
        body = r2.get_json()
        self.assertTrue(body.get('ignored'))
        self.assertEqual(body.get('reason'), 'duplicate')

    def test_reply_threads_into_existing_ticket(self):
        import time
        # v5.88.37: unique email_ids so this test is reentrant
        first_eid = f'thread_first_{int(time.time() * 1000000)}'
        reply_eid = f'thread_reply_{int(time.time() * 1000000)}'
        # First message creates ticket
        first = {
            'type': 'email.received',
            'data': {
                'email_id': first_eid,
                'from': 'bob@example.com',
                'to': ['support@getofferwise.ai'],
                'subject': 'Question about my OfferScore',
            }
        }
        r = self._post_event(first)
        ticket_id = r.get_json()['ticket_id']

        # User replies with [Ticket #N] in subject (admin would have replied
        # and our outbound code would have added that prefix automatically)
        reply = {
            'type': 'email.received',
            'data': {
                'email_id': reply_eid,
                'from': 'bob@example.com',
                'to': ['support@getofferwise.ai'],
                'subject': f'Re: [Ticket #{ticket_id}] Question about my OfferScore',
            }
        }
        r2 = self._post_event(reply)
        self.assertEqual(r2.status_code, 200)
        body = r2.get_json()
        self.assertTrue(body['success'])
        self.assertFalse(body['is_new'])
        self.assertEqual(body['ticket_id'], ticket_id)


# -----------------------------------------------------------------------------
# v5.88.37: Aging, templates, search
# -----------------------------------------------------------------------------

class TestAgingIndicators(unittest.TestCase):
    """ticket_age_info — 24h threshold on open/waiting_on_user/reopened."""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_test_app()
        from models import db, Ticket
        cls.db = db
        cls.Ticket = Ticket

    def _new_ticket(self, status='open', created_hours_ago=0,
                    last_admin_reply_hours_ago=None):
        from models import Ticket
        from datetime import datetime, timedelta
        with self.app.app_context():
            now = datetime.utcnow()
            t = Ticket(
                email_for_anonymous='age-test@example.com',
                subject='Age test',
                source='manual',
                status=status,
                created_at=now - timedelta(hours=created_hours_ago),
            )
            if last_admin_reply_hours_ago is not None:
                t.last_admin_reply_at = now - timedelta(hours=last_admin_reply_hours_ago)
            self.db.session.add(t)
            self.db.session.commit()
            return t.id

    def test_open_under_24h_not_stale(self):
        from support_service import ticket_age_info
        tid = self._new_ticket(status='open', created_hours_ago=12)
        with self.app.app_context():
            t = self.Ticket.query.get(tid)
            info = ticket_age_info(t)
            self.assertFalse(info['is_stale'])
            self.assertAlmostEqual(info['age_hours'], 12, delta=1)

    def test_open_over_24h_is_stale(self):
        from support_service import ticket_age_info
        tid = self._new_ticket(status='open', created_hours_ago=30)
        with self.app.app_context():
            t = self.Ticket.query.get(tid)
            info = ticket_age_info(t)
            self.assertTrue(info['is_stale'])

    def test_waiting_on_user_over_24h_is_stale(self):
        from support_service import ticket_age_info
        tid = self._new_ticket(
            status='waiting_on_user',
            created_hours_ago=72,
            last_admin_reply_hours_ago=30,
        )
        with self.app.app_context():
            t = self.Ticket.query.get(tid)
            info = ticket_age_info(t)
            self.assertTrue(info['is_stale'])
            self.assertAlmostEqual(info['age_hours'], 30, delta=1)

    def test_in_progress_never_stale(self):
        """A ticket actively being worked on doesn't show stale."""
        from support_service import ticket_age_info
        tid = self._new_ticket(status='in_progress', created_hours_ago=200)
        with self.app.app_context():
            t = self.Ticket.query.get(tid)
            info = ticket_age_info(t)
            self.assertFalse(info['is_stale'])

    def test_resolved_never_stale(self):
        from support_service import ticket_age_info
        tid = self._new_ticket(status='resolved', created_hours_ago=200)
        with self.app.app_context():
            t = self.Ticket.query.get(tid)
            info = ticket_age_info(t)
            self.assertFalse(info['is_stale'])

    def test_to_dict_includes_aging(self):
        tid = self._new_ticket(status='open', created_hours_ago=2)
        with self.app.app_context():
            t = self.Ticket.query.get(tid)
            d = t.to_dict()
            self.assertIn('aging', d)
            self.assertIn('age_hours', d['aging'])
            self.assertIn('is_stale', d['aging'])


class TestTemplates(unittest.TestCase):
    """Template seeding, rendering, and the admin endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_test_app()
        from models import db, Ticket, TicketTemplate
        cls.db = db
        cls.Ticket = Ticket
        cls.TicketTemplate = TicketTemplate

    def setUp(self):
        self.client = self.app.test_client()
        self._notify_patcher = patch(
            'support_service.send_admin_notification', return_value=True
        )
        self._notify_patcher.start()

    def tearDown(self):
        self._notify_patcher.stop()

    def test_seed_inserts_defaults_first_time(self):
        from support_service import seed_default_templates, DEFAULT_TEMPLATES
        with self.app.app_context():
            # Clear any pre-existing seeded rows so we can test from clean
            self.TicketTemplate.query.filter_by(is_seeded=True).delete()
            self.db.session.commit()
            inserted = seed_default_templates()
            self.assertEqual(inserted, len(DEFAULT_TEMPLATES))

    def test_seed_idempotent(self):
        from support_service import seed_default_templates
        with self.app.app_context():
            seed_default_templates()
            inserted = seed_default_templates()
            # Second call should insert zero new ones
            self.assertEqual(inserted, 0)

    def test_render_substitutes_known_variables(self):
        from support_service import (render_template,
                                     create_ticket_from_inbound_email)
        with self.app.app_context():
            ticket, _ = create_ticket_from_inbound_email(
                'render-test@example.com',
                'Question about my analysis',
                'Hello, need help.',
            )
            body = ('Hi {user_name}, your email is {user_email}, '
                    'ticket #{ticket_id}.')
            rendered, unresolved = render_template(body, ticket)
            self.assertEqual(unresolved, [])
            self.assertIn('render-test@example.com', rendered)
            self.assertIn(f'ticket #{ticket.id}', rendered)

    def test_render_reports_unresolved_variables(self):
        from support_service import (render_template,
                                     create_ticket_from_inbound_email)
        with self.app.app_context():
            ticket, _ = create_ticket_from_inbound_email(
                'r@example.com', 'subject', 'body')
            body = 'Hi {user_name}, here are {custmer_name} and {bogus_var}.'
            rendered, unresolved = render_template(body, ticket)
            self.assertIn('custmer_name', unresolved)
            self.assertIn('bogus_var', unresolved)
            # Unresolved vars LEFT IN PLACE
            self.assertIn('{custmer_name}', rendered)
            self.assertIn('{bogus_var}', rendered)

    def test_render_fills_property_from_snapshot(self):
        """In-product share tickets carry a snapshot — variables should
        resolve from it."""
        # Reuse the in-product share helper's setup since it produces a
        # ticket with a real snapshot.
        from models import User, Property, Analysis
        # Patch sending so we don't fire a real email when creating the ticket
        with patch('support_service.send_admin_notification', return_value=True):
            uid, pid, email = _create_user_and_property(
                self.app, self.db, User, Property, Analysis,
                email_suffix='template-test')

        # Create the ticket via the share endpoint
        self.assertTrue(_login_via_endpoint(self.client, email))
        with patch('support_service.send_admin_notification', return_value=True):
            r = self.client.post('/api/support/share', json={
                'property_id': pid,
                'message': 'Need help with this property',
            })
        self.assertEqual(r.status_code, 200)
        ticket_id = r.get_json()['ticket_id']

        from support_service import render_template
        with self.app.app_context():
            ticket = self.Ticket.query.get(ticket_id)
            body = 'Property: {property_address}, score: {offerscore}'
            rendered, unresolved = render_template(body, ticket)
            self.assertEqual(unresolved, [])
            self.assertIn('123 Ticket Lane', rendered)
            # offerscore was 82 = 100 - composite_score(18) — see the
            # fixture _create_user_and_property
            self.assertIn('82', rendered)

    def test_list_templates_endpoint(self):
        from support_service import seed_default_templates
        with self.app.app_context():
            seed_default_templates()

        r = self.client.get(_admin_url('/api/admin/ticket-templates'))
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIn('templates', body)
        # At least the defaults exist
        names = [t['name'] for t in body['templates']]
        self.assertIn('Thanks, looking into it', names)
        # Sort order respected
        sorts = [t['sort_order'] for t in body['templates']]
        self.assertEqual(sorts, sorted(sorts))

    def test_render_template_endpoint(self):
        from support_service import (seed_default_templates,
                                     create_ticket_from_inbound_email)
        with self.app.app_context():
            seed_default_templates()
            ticket, _ = create_ticket_from_inbound_email(
                'apicall@example.com', 'API render test', 'body')
            tid = ticket.id
            tpl = self.TicketTemplate.query.filter_by(
                name='Thanks, looking into it').first()
            tpl_id = tpl.id

        r = self.client.get(_admin_url(
            f'/api/admin/tickets/{tid}/render-template/{tpl_id}'))
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIn('rendered_body', body)
        self.assertIn('apicall@example.com', body['rendered_body']
                      .replace('@', '@'))  # email is in there somewhere
        # Inbound tickets don't have property snapshots -> these stay generic
        self.assertIn('(not available)', body['rendered_body'])

    def test_render_template_404_for_missing_ticket(self):
        r = self.client.get(_admin_url(
            '/api/admin/tickets/9999999/render-template/1'))
        self.assertEqual(r.status_code, 404)


class TestSearchAndStaleFilter(unittest.TestCase):
    """Admin list endpoint search (q) and stale_only filters."""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_test_app()
        from models import db, Ticket
        cls.db = db
        cls.Ticket = Ticket

    def setUp(self):
        self.client = self.app.test_client()
        self._notify_patcher = patch(
            'support_service.send_admin_notification', return_value=True
        )
        self._notify_patcher.start()

    def tearDown(self):
        self._notify_patcher.stop()

    def _seed(self, *items):
        """items: list of dicts with email, subject."""
        from support_service import create_ticket_from_inbound_email
        out = []
        with self.app.app_context():
            for it in items:
                t, _ = create_ticket_from_inbound_email(
                    it['email'], it['subject'], 'body')
                out.append(t.id)
        return out

    def test_search_filters_by_subject(self):
        self._seed(
            {'email': 'alice@x.com', 'subject': 'Refund please'},
            {'email': 'bob@x.com',   'subject': 'Help with analysis'},
            {'email': 'carol@x.com', 'subject': 'Random question'},
        )
        r = self.client.get(_admin_url(
            '/api/admin/tickets?status=all&q=refund'))
        self.assertEqual(r.status_code, 200)
        tickets = r.get_json()['tickets']
        subjects = [t['subject'] for t in tickets]
        self.assertTrue(any('Refund' in s for s in subjects))
        self.assertFalse(any('analysis' in s.lower() for s in subjects))

    def test_search_filters_by_email(self):
        self._seed(
            {'email': 'unique@example.com', 'subject': 'Some subject'},
            {'email': 'other@example.com',  'subject': 'Different'},
        )
        r = self.client.get(_admin_url(
            '/api/admin/tickets?status=all&q=unique'))
        self.assertEqual(r.status_code, 200)
        tickets = r.get_json()['tickets']
        emails = [t['user_email'] for t in tickets]
        self.assertTrue(any('unique' in (e or '') for e in emails))

    def test_stale_only_filter(self):
        # Create one stale and one fresh ticket
        from models import Ticket
        from datetime import datetime, timedelta
        with self.app.app_context():
            stale = Ticket(
                email_for_anonymous='stale@x.com', subject='Stale ticket',
                source='manual', status='open',
                created_at=datetime.utcnow() - timedelta(hours=48),
            )
            fresh = Ticket(
                email_for_anonymous='fresh@x.com', subject='Fresh ticket',
                source='manual', status='open',
            )
            self.db.session.add(stale)
            self.db.session.add(fresh)
            self.db.session.commit()

        r = self.client.get(_admin_url(
            '/api/admin/tickets?status=open&stale_only=1'))
        self.assertEqual(r.status_code, 200)
        tickets = r.get_json()['tickets']
        subjects = [t['subject'] for t in tickets]
        self.assertIn('Stale ticket', subjects)
        self.assertNotIn('Fresh ticket', subjects)


# -----------------------------------------------------------------------------
# v5.88.39: Manual ticket creation
# -----------------------------------------------------------------------------

class TestManualTicketCreation(unittest.TestCase):
    """POST /api/admin/tickets — admin pastes customer email details."""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_test_app()
        from models import db, Ticket, TicketMessage
        cls.db = db
        cls.Ticket = Ticket
        cls.TicketMessage = TicketMessage

    def setUp(self):
        self.client = self.app.test_client()
        # No admin notifications fire on manual creation by design
        # (it's already a logged action by an admin), but patch defensively
        # in case future code adds them.
        self._notify_patcher = patch(
            'support_service.send_admin_notification', return_value=True
        )
        self._notify_patcher.start()

    def tearDown(self):
        self._notify_patcher.stop()

    def test_create_with_all_fields(self):
        r = self.client.post(
            _admin_url('/api/admin/tickets'),
            data=json.dumps({
                'from_email': 'alice@example.com',
                'from_name': 'Alice Smith',
                'subject': 'Question about my analysis',
                'body': 'Hi, my OfferScore looks wrong, can you check?',
            }),
            content_type='application/json')
        self.assertEqual(r.status_code, 201, r.data)
        body = r.get_json()
        self.assertTrue(body['success'])
        ticket = body['ticket']
        self.assertEqual(ticket['source'], 'manual')
        self.assertEqual(ticket['status'], 'open')
        self.assertEqual(ticket['subject'], 'Question about my analysis')
        # First message is the customer's, second is the system note
        self.assertEqual(len(ticket['messages']), 2)
        self.assertEqual(ticket['messages'][0]['author_kind'], 'user')
        self.assertEqual(ticket['messages'][1]['author_kind'], 'system')
        # System note records who created it and the customer name
        self.assertIn('manually by admin', ticket['messages'][1]['body'])
        self.assertIn('Alice Smith', ticket['messages'][1]['body'])

    def test_create_minimal_fields(self):
        r = self.client.post(
            _admin_url('/api/admin/tickets'),
            data=json.dumps({
                'from_email': 'minimal@example.com',
                'subject': 'Hi',
                'body': 'Hello',
            }),
            content_type='application/json')
        self.assertEqual(r.status_code, 201)
        body = r.get_json()
        ticket = body['ticket']
        # No from_name provided, system note shouldn't mention it
        sys_note = ticket['messages'][1]['body']
        self.assertNotIn('Customer name as provided', sys_note)

    def test_missing_email_rejected(self):
        r = self.client.post(
            _admin_url('/api/admin/tickets'),
            data=json.dumps({
                'subject': 'Some subject',
                'body': 'Some body',
            }),
            content_type='application/json')
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertIn('from_email', body.get('fields', {}))

    def test_invalid_email_rejected(self):
        r = self.client.post(
            _admin_url('/api/admin/tickets'),
            data=json.dumps({
                'from_email': 'not-an-email',
                'subject': 'Some subject',
                'body': 'Some body',
            }),
            content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('from_email', r.get_json().get('fields', {}))

    def test_missing_subject_rejected(self):
        r = self.client.post(
            _admin_url('/api/admin/tickets'),
            data=json.dumps({
                'from_email': 'alice@example.com',
                'body': 'Some body',
            }),
            content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('subject', r.get_json().get('fields', {}))

    def test_missing_body_rejected(self):
        r = self.client.post(
            _admin_url('/api/admin/tickets'),
            data=json.dumps({
                'from_email': 'alice@example.com',
                'subject': 'Subject only',
            }),
            content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('body', r.get_json().get('fields', {}))

    def test_overlong_body_rejected(self):
        r = self.client.post(
            _admin_url('/api/admin/tickets'),
            data=json.dumps({
                'from_email': 'alice@example.com',
                'subject': 'Big paste',
                'body': 'x' * 50001,
            }),
            content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_invalid_property_id_silently_ignored(self):
        """Nonexistent property ID should not block ticket creation —
        the admin can fix the link later. System note records the skip."""
        r = self.client.post(
            _admin_url('/api/admin/tickets'),
            data=json.dumps({
                'from_email': 'alice@example.com',
                'subject': 'About a property',
                'body': 'Customer asked about property X',
                'linked_property_id': 9999999,
            }),
            content_type='application/json')
        self.assertEqual(r.status_code, 201)
        ticket = r.get_json()['ticket']
        self.assertIsNone(ticket['property_id'])
        # System note mentions the skip
        sys_note = ticket['messages'][1]['body']
        self.assertIn('was invalid, skipped', sys_note)

    def test_non_integer_property_id_rejected(self):
        r = self.client.post(
            _admin_url('/api/admin/tickets'),
            data=json.dumps({
                'from_email': 'alice@example.com',
                'subject': 'Whatever',
                'body': 'body',
                'linked_property_id': 'not-a-number',
            }),
            content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('linked_property_id', r.get_json().get('fields', {}))

    def test_existing_user_email_links_to_user(self):
        """When customer email matches a real user, the ticket links."""
        from models import User
        with self.app.app_context():
            u = User(email='real-user@example.com', name='Real User',
                    auth_provider='test', analysis_credits=5, tier='free')
            self.db.session.add(u)
            self.db.session.commit()
            uid = u.id

        r = self.client.post(
            _admin_url('/api/admin/tickets'),
            data=json.dumps({
                'from_email': 'real-user@example.com',
                'subject': 'Linked test',
                'body': 'Should link to the user',
            }),
            content_type='application/json')
        self.assertEqual(r.status_code, 201)
        ticket = r.get_json()['ticket']
        self.assertEqual(ticket['user_id'], uid)

    def test_reply_flow_works_on_manually_created_ticket(self):
        """Smoke test the full path: create manually, then reply via the
        existing reply endpoint."""
        # Create ticket
        r = self.client.post(
            _admin_url('/api/admin/tickets'),
            data=json.dumps({
                'from_email': 'flow@example.com',
                'subject': 'End-to-end flow',
                'body': 'Customer pasted text',
            }),
            content_type='application/json')
        self.assertEqual(r.status_code, 201)
        ticket_id = r.get_json()['ticket']['id']

        # Mock the outbound email send for the reply step
        with patch('email_service.send_email', return_value=True) as mock_send:
            r2 = self.client.post(
                _admin_url(f'/api/admin/tickets/{ticket_id}/reply'),
                data=json.dumps({'body': 'Hi, looking into this.'}),
                content_type='application/json')
            self.assertEqual(r2.status_code, 200, r2.data)
            self.assertEqual(r2.get_json()['ticket_status'], 'waiting_on_user')
            # Email was sent with the right To
            self.assertEqual(mock_send.call_count, 1)
            self.assertEqual(mock_send.call_args.kwargs['to_email'],
                             'flow@example.com')


if __name__ == '__main__':
    unittest.main()
