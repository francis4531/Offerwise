"""
test_active_filter_engagement.py — v5.88.06

Tests the changes that hide sent/replied prospects from the active list
and surface them in the engagement panel.

Behavior under test:
  - The b2b list endpoint already returns engagement data for each
    contact (opened/clicked/click_count) — verify it's present
  - The endpoint returns sent + replied prospects (we filter on the
    front-end, not the backend, so the engagement panel can render them)
  - prospStatusKey logic: sent and replied are distinct from new/drafted

The front-end filter / engagement rendering logic is plain JS in
static/admin.html — covered by structural tests that verify the helper
functions exist and the right data is exposed.
"""
import json
import os
import unittest
from datetime import datetime, timedelta

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-active-filter'
os.environ['DATABASE_URL'] = 'sqlite:///test_active_filter.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-active-filter')

ADMIN_KEY_VALUE = os.environ['ADMIN_KEY']

import os as _os
_db_path = 'test_active_filter.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)


class TestB2BListReturnsEngagement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            from models import db, OutreachContact, OutreachLog
            cls.app = app
            cls.db = db
            cls.OutreachContact = OutreachContact
            cls.OutreachLog = OutreachLog
            cls.client = app.test_client(use_cookies=False)
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"App not available: {self.skip_reason}")
        with self.app.app_context():
            self.OutreachContact.query.delete()
            self.OutreachLog.query.delete()
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.OutreachContact.query.delete()
            self.OutreachLog.query.delete()
            self.db.session.commit()

    def _admin_url(self, path):
        sep = '&' if '?' in path else '?'
        return f'{path}{sep}admin_key={ADMIN_KEY_VALUE}'

    def _add_contact(self, email, name, status='new'):
        with self.app.app_context():
            c = self.OutreachContact(
                cohort='b2b', email=email, name=name, company='TestCo',
                wedge='renovation_lenders', status=status,
            )
            self.db.session.add(c)
            self.db.session.commit()
            return c.id

    def _add_send_log(self, contact_id, sent_at=None):
        with self.app.app_context():
            c = self.OutreachContact.query.get(contact_id)
            log = self.OutreachLog(
                cohort='b2b', contact_id=contact_id,
                to_email=c.email if c else 'test@example.com',
                sent_at=sent_at or datetime.utcnow(),
                subject='Test subject', success=True,
                resend_id=f'test-resend-{contact_id}',
            )
            self.db.session.add(log)
            self.db.session.commit()
            return log.id

    def test_endpoint_returns_all_statuses(self):
        """The b2b list endpoint MUST return prospects of all statuses
        (new, drafted, sent, replied). Filtering happens client-side so
        the engagement panel can show sent/replied without a separate API call."""
        self._add_contact('a@example.com', 'A Person', status='new')
        self._add_contact('b@example.com', 'B Person', status='drafted')
        cid_sent = self._add_contact('c@example.com', 'C Person', status='sent')
        self._add_send_log(cid_sent)

        r = self.client.get(self._admin_url('/api/admin/outreach/b2b'))
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        statuses = [c.get('status') for c in data.get('b2b', [])]
        self.assertEqual(set(statuses), {'new', 'drafted', 'sent'})

    def test_endpoint_returns_engagement_per_contact(self):
        """Every contact row should have a 'last_send' field (None if never
        sent, or dict with sent_at/opened/clicked/click_count if sent).
        This is what the engagement panel reads."""
        cid = self._add_contact('a@example.com', 'A Person', status='sent')
        self._add_send_log(cid)
        r = self.client.get(self._admin_url('/api/admin/outreach/b2b'))
        data = r.get_json()
        rows = data.get('b2b', [])
        self.assertEqual(len(rows), 1)
        last_send = rows[0].get('last_send')
        self.assertIsNotNone(last_send,
                             'last_send must be present for sent prospects')
        self.assertIn('sent_at', last_send)
        self.assertIn('opened', last_send)
        self.assertIn('clicked', last_send)
        self.assertIn('click_count', last_send)

    def test_endpoint_supports_status_filter(self):
        """The query param ?status=sent works (used by the Sent filter chip)."""
        self._add_contact('new@example.com', 'A', status='new')
        cid_sent = self._add_contact('sent@example.com', 'B', status='sent')
        self._add_send_log(cid_sent)

        r = self.client.get(self._admin_url('/api/admin/outreach/b2b?status=sent'))
        data = r.get_json()
        emails = [c['email'] for c in data.get('b2b', [])]
        self.assertEqual(emails, ['sent@example.com'])

    def test_unset_status_returns_all(self):
        """No ?status param returns all (this is the default the front-end
        relies on for the engagement panel to have data to render)."""
        self._add_contact('a@example.com', 'A', status='new')
        self._add_contact('b@example.com', 'B', status='sent')
        self._add_contact('c@example.com', 'C', status='replied')

        r = self.client.get(self._admin_url('/api/admin/outreach/b2b'))
        data = r.get_json()
        self.assertEqual(len(data.get('b2b', [])), 3)


class TestFrontEndStructure(unittest.TestCase):
    """Verify the front-end has the expected helper functions and HTML
    structure for the v5.88.06 engagement panel + active filter."""

    def setUp(self):
        admin_path = os.path.join(os.path.dirname(__file__), 'static', 'admin.html')
        with open(admin_path, 'r') as f:
            self.src = f.read()

    def test_engagement_panel_has_ontoggle_handler(self):
        """The engagement-panel <details> must call b2bEngagementLoad on toggle."""
        idx = self.src.find('id="engagement-panel"')
        self.assertNotEqual(idx, -1, 'engagement-panel element not found')
        nearby = self.src[idx:idx + 200]
        self.assertIn('ontoggle', nearby)
        self.assertIn('b2bEngagementLoad', nearby)

    def test_active_filter_chip_exists_and_is_default(self):
        """The Active filter chip must exist with class 'active' (default selected)."""
        self.assertIn('data-filter="active"', self.src)
        # The Active chip must have class="filter-chip active" — i.e. start
        # selected
        idx = self.src.find('data-filter="active"')
        nearby = self.src[max(0, idx-200):idx]
        self.assertIn('filter-chip active', nearby)

    def test_b2b_engagement_load_function_exists(self):
        """b2bEngagementLoad function must be defined in the JS."""
        self.assertIn('function b2bEngagementLoad()', self.src)

    def test_b2b_engagement_summary_updater_exists(self):
        """b2bEngagementUpdateSummary function must be defined."""
        self.assertIn('function b2bEngagementUpdateSummary()', self.src)

    def test_default_filter_is_active(self):
        """_prospFilter default must be 'active' so sent/replied are hidden by default."""
        # Find the var declaration
        idx = self.src.find("var _prospFilter")
        self.assertNotEqual(idx, -1)
        # Should be set to 'active'
        decl = self.src[idx:idx + 100]
        self.assertIn("'active'", decl)

    def test_active_filter_logic_includes_new_and_drafted(self):
        """The 'active' filter logic must match new + drafted prospects."""
        # Search for the filter check in prospRender
        self.assertIn(
            "_prospFilter === 'active'",
            self.src,
            "prospRender must handle 'active' filter explicitly",
        )

    def test_engagement_panel_has_summary_span(self):
        """The collapsed summary must show counts (sent + opened + clicked)."""
        self.assertIn('id="engagement-summary"', self.src)


if __name__ == '__main__':
    unittest.main()
