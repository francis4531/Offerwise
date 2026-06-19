"""
test_bulk_regenerate.py — v5.88.05

Tests the bulk regenerate flow for already-drafted B2B prospects.

Behavior under test:
  - skip_research=True path preserves cached focus_areas (doesn't clobber)
  - skip_research=False (initial research) overwrites focus_areas
  - The /api/admin/outreach/research-and-draft endpoint accepts both modes
  - Regenerate overwrites draft_subject and draft_body in both modes

The bulk regenerate workflow is: select drafted prospects → click
"🔄 Regenerate drafted" → endpoint runs with skip_research=true →
new draft body has greeting + signoff + URL but original focus_areas
research is preserved.
"""
import json
import os
import unittest
from unittest.mock import patch
from datetime import datetime

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-bulk-regen'
os.environ['DATABASE_URL'] = 'sqlite:///test_bulk_regen.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-bulk-regen')

ADMIN_KEY_VALUE = os.environ['ADMIN_KEY']

import os as _os
_db_path = 'test_bulk_regen.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)


class TestBulkRegenerate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            from models import db, OutreachContact
            cls.app = app
            cls.db = db
            cls.OutreachContact = OutreachContact
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
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.OutreachContact.query.delete()
            self.db.session.commit()

    def _admin_url(self, path):
        sep = '&' if '?' in path else '?'
        return f'{path}{sep}admin_key={ADMIN_KEY_VALUE}'

    def _add_drafted_prospect(self, email, name, company, wedge,
                              cached_focus='Original cached focus areas from earlier research'):
        with self.app.app_context():
            c = self.OutreachContact(
                cohort='b2b', email=email, name=name, company=company,
                wedge=wedge,
                draft_subject='Old subject',
                draft_body='Old body without greeting or signoff',
                focus_areas=cached_focus,
                status='drafted',
                draft_generated_at=datetime.utcnow(),
            )
            self.db.session.add(c)
            self.db.session.commit()
            return c.id

    def test_skip_research_preserves_cached_focus_areas(self):
        """v5.88.05 fix: when regenerating with skip_research=true, the
        existing focus_areas must not be clobbered with empty string."""
        cid = self._add_drafted_prospect(
            email='chris@roc360.com',
            name='Chris Stocking',
            company='Roc360',
            wedge='renovation_lenders',
            cached_focus='Roc360 focuses on renovation lending and recently expanded into fix-and-flip products.',
        )

        # Mock the LLM call to return a fresh body
        with patch('ai_client.get_ai_response') as mock_ai:
            mock_ai.return_value = json.dumps({
                'subject': 'Fresh subject after regenerate',
                'body': 'Fresh body content. Reference https://www.getofferwise.ai/for-lenders for context.',
            })
            r = self.client.post(
                self._admin_url('/api/admin/outreach/research-and-draft'),
                json={'contact_ids': [cid], 'skip_research': True},
            )

        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            c = self.OutreachContact.query.get(cid)
            # focus_areas preserved
            self.assertIn('Roc360 focuses on renovation lending', c.focus_areas)
            # draft was regenerated
            self.assertIn('Fresh body content', c.draft_body)
            # greeting prepended (v5.88.02)
            self.assertIn('Greetings Chris,', c.draft_body)
            # signoff appended (v5.88.03)
            self.assertIn('-Francis', c.draft_body)
            # status stays 'drafted' (no implicit transition)
            self.assertEqual(c.status, 'drafted')

    def test_initial_research_overwrites_focus_areas(self):
        """skip_research=false (the original research path) DOES update
        focus_areas with the new research output. Only the regenerate
        path should preserve cached values."""
        cid = self._add_drafted_prospect(
            email='jane@hippo.com',
            name='Jane Doe',
            company='Hippo',
            wedge='insurtechs',
            cached_focus='Stale focus areas',
        )

        with patch('ai_client.get_ai_response') as mock_ai, \
             patch('prospect_research_service.research_company_focus') as mock_research:
            mock_research.return_value = {
                'focus_areas': 'NEW: Hippo recently launched bundled coverage products',
                'sources_count': 3,
            }
            mock_ai.return_value = json.dumps({
                'subject': 'New subject',
                'body': 'Body referencing https://www.getofferwise.ai/for-insurance',
            })
            r = self.client.post(
                self._admin_url('/api/admin/outreach/research-and-draft'),
                json={'contact_ids': [cid], 'skip_research': False},
            )

        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            c = self.OutreachContact.query.get(cid)
            # focus_areas overwritten with fresh research
            self.assertIn('NEW: Hippo recently launched', c.focus_areas)
            self.assertNotIn('Stale focus areas', c.focus_areas)

    def test_regenerate_overwrites_old_draft_body(self):
        """The whole point of regenerate: old draft_body is replaced."""
        cid = self._add_drafted_prospect(
            email='chris@roc360.com',
            name='Chris Stocking',
            company='Roc360',
            wedge='renovation_lenders',
        )

        with patch('ai_client.get_ai_response') as mock_ai:
            mock_ai.return_value = json.dumps({
                'subject': 'New regenerated subject',
                'body': 'Brand new body text. https://www.getofferwise.ai/for-lenders',
            })
            r = self.client.post(
                self._admin_url('/api/admin/outreach/research-and-draft'),
                json={'contact_ids': [cid], 'skip_research': True},
            )

        with self.app.app_context():
            c = self.OutreachContact.query.get(cid)
            # Old body gone
            self.assertNotIn('Old body without greeting or signoff', c.draft_body)
            # New body present
            self.assertIn('Brand new body text', c.draft_body)
            # Subject also overwritten
            self.assertEqual(c.draft_subject, 'New regenerated subject')

    def test_regenerate_uses_wedge_url_for_persona_link(self):
        """Regenerated drafts use the wedge → URL mapping for the
        persona-specific landing page link, same as initial drafts."""
        cid = self._add_drafted_prospect(
            email='jane@hippo.com',
            name='Jane Doe',
            company='Hippo',
            wedge='insurtechs',
        )

        with patch('ai_client.get_ai_response') as mock_ai:
            # LLM returns a body WITHOUT the URL — defensive append fires
            mock_ai.return_value = json.dumps({
                'subject': 'Question on underwriting workflow',
                'body': 'Reaching out about your underwriting risk modeling.',
            })
            r = self.client.post(
                self._admin_url('/api/admin/outreach/research-and-draft'),
                json={'contact_ids': [cid], 'skip_research': True},
            )

        with self.app.app_context():
            c = self.OutreachContact.query.get(cid)
            # Persona-specific URL appears (insurtechs → /for-insurance)
            self.assertIn('/for-insurance', c.draft_body)
            # NOT the lender URL (would indicate wedge wasn't used)
            self.assertNotIn('/for-lenders', c.draft_body)

    def test_endpoint_accepts_multiple_ids(self):
        """Bulk regenerate of 3 prospects in one call."""
        cid1 = self._add_drafted_prospect('a@roc360.com', 'Alice', 'Roc360', 'renovation_lenders')
        cid2 = self._add_drafted_prospect('b@hippo.com', 'Bob', 'Hippo', 'insurtechs')
        cid3 = self._add_drafted_prospect('c@compass.com', 'Carol', 'Compass', 'brokerage_tech')

        with patch('ai_client.get_ai_response') as mock_ai:
            mock_ai.return_value = json.dumps({
                'subject': 'Bulk subject',
                'body': 'Bulk body content.',
            })
            r = self.client.post(
                self._admin_url('/api/admin/outreach/research-and-draft'),
                json={'contact_ids': [cid1, cid2, cid3], 'skip_research': True},
            )

        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data.get('total_processed'), 3)
        self.assertEqual(data.get('total_succeeded'), 3)


if __name__ == '__main__':
    unittest.main()
