"""
test_outreach_drafts.py — v5.87.98

Tests the wedge-to-URL mapping for B2B outreach drafts. When the founder
sends drafts to lender prospects, they should land on /for-lenders;
when sent to insurtech prospects, /for-insurance; etc.

Two layers of behavior under test:
  1. WEDGE_URL_LOOKUP / get_landing_url_for_wedge — the mapping itself
  2. draft_email + _fallback_draft — that the URL gets into the body
"""
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('FLASK_ENV', 'testing')


class TestWedgeURLLookup(unittest.TestCase):
    """Unit tests on the mapping table itself."""

    def test_renovation_lenders_maps_to_for_lenders(self):
        from prospect_research_service import get_landing_url_for_wedge
        url, role = get_landing_url_for_wedge('renovation_lenders')
        self.assertIn('/for-lenders', url)
        self.assertIn('lender', role.lower())

    def test_insurtechs_maps_to_for_insurance(self):
        from prospect_research_service import get_landing_url_for_wedge
        url, role = get_landing_url_for_wedge('insurtechs')
        self.assertIn('/for-insurance', url)
        self.assertIn('underwriter', role.lower())

    def test_brokerage_tech_maps_to_for_agents(self):
        from prospect_research_service import get_landing_url_for_wedge
        url, role = get_landing_url_for_wedge('brokerage_tech')
        self.assertIn('/for-agents', url)

    def test_title_closing_maps_to_for_title(self):
        from prospect_research_service import get_landing_url_for_wedge
        url, role = get_landing_url_for_wedge('title_closing')
        self.assertIn('/for-title-companies', url)

    def test_ibuyer_maps_to_enterprise(self):
        """iBuyer is a sales-led acquisition partnership, not a /for-* page.
        Maps to /enterprise."""
        from prospect_research_service import get_landing_url_for_wedge
        url, role = get_landing_url_for_wedge('ibuyer')
        self.assertIn('/enterprise', url)

    def test_buyer_fintech_maps_to_enterprise(self):
        from prospect_research_service import get_landing_url_for_wedge
        url, role = get_landing_url_for_wedge('buyer_fintech')
        self.assertIn('/enterprise', url)

    def test_unknown_wedge_falls_back_to_personas(self):
        """Unknown / 'other' / empty wedge → /personas page so the
        prospect can self-identify."""
        from prospect_research_service import get_landing_url_for_wedge
        url, role = get_landing_url_for_wedge('not_a_real_wedge')
        self.assertIn('/personas', url)
        url2, _ = get_landing_url_for_wedge('')
        self.assertIn('/personas', url2)
        url3, _ = get_landing_url_for_wedge('other')
        self.assertIn('/personas', url3)

    def test_url_is_absolute_with_scheme(self):
        """All URLs must be absolute (https://...) so they're clickable
        in email clients without depending on the recipient's client to
        resolve relative paths."""
        from prospect_research_service import WEDGE_URL_LOOKUP
        for wedge, (url, _role) in WEDGE_URL_LOOKUP.items():
            self.assertTrue(
                url.startswith('https://'),
                f"Wedge '{wedge}' URL must start with https:// — got: {url}"
            )

    def test_all_named_wedges_have_distinct_pages(self):
        """The named professional wedges (lenders, insurance, brokerage,
        title) should each point to their own /for-* page, not collapse
        to a generic page."""
        from prospect_research_service import WEDGE_URL_LOOKUP
        wedge_to_url = {w: u for w, (u, _) in WEDGE_URL_LOOKUP.items()}
        self.assertNotEqual(wedge_to_url['renovation_lenders'],
                            wedge_to_url['insurtechs'])
        self.assertNotEqual(wedge_to_url['brokerage_tech'],
                            wedge_to_url['title_closing'])


class TestFallbackDraftIncludesURL(unittest.TestCase):
    """The static fallback used when the LLM call fails must also include
    the persona URL so even degraded drafts have the right link."""

    def test_fallback_includes_lender_url(self):
        from prospect_research_service import _fallback_draft
        d = _fallback_draft(
            name='Jane Doe',
            company='ExampleCorp',
            wedge_pain='repair-cost risk on the properties you finance',
            landing_url='https://www.getofferwise.ai/for-lenders',
            company_role='lender / underwriter',
            error='test',
        )
        self.assertIn('/for-lenders', d['body'])

    def test_fallback_includes_role_label(self):
        from prospect_research_service import _fallback_draft
        d = _fallback_draft(
            name='Jane Doe', company='ExampleCorp',
            wedge_pain='hidden risk in disclosure documents',
            landing_url='https://www.getofferwise.ai/for-insurance',
            company_role='underwriter',
        )
        self.assertIn('underwriter', d['body'].lower())

    def test_fallback_without_landing_url_uses_default_links(self):
        """Backward compat — if no landing_url provided, fall back to the
        old architecture/comparison links (existing behavior)."""
        from prospect_research_service import _fallback_draft
        d = _fallback_draft(
            name='Jane Doe', company='ExampleCorp',
            wedge_pain='this space',
        )
        self.assertIn('architecture', d['body'])

    def test_fallback_no_em_dashes(self):
        """Founder rule: no em-dashes in any outreach drafts."""
        from prospect_research_service import _fallback_draft
        d = _fallback_draft(
            name='Jane Doe', company='Example',
            wedge_pain='this space',
            landing_url='https://www.getofferwise.ai/personas',
            company_role='partner',
        )
        self.assertNotIn('—', d['body'])
        self.assertNotIn('–', d['body'])


class TestDraftEmailURLAppending(unittest.TestCase):
    """When the LLM somehow returns a body without the URL, the safety
    net must append it as a P.S. so the founder doesn't have to add it
    manually for every draft."""

    @patch('ai_client.get_ai_response')
    def test_appends_url_when_llm_omits_it(self, mock_ai):
        """LLM returns a body without the URL → safety net appends P.S."""
        from prospect_research_service import draft_email
        mock_ai.return_value = json.dumps({
            'subject': 'Question on collateral risk',
            'body': "I'm building OfferWise. Quick question about your "
                    "underwriting workflow. Worth 20 minutes?",
        })
        d = draft_email(
            name='Jane Doe',
            title='Head of Underwriting',
            company='ExampleBank',
            wedge='renovation_lenders',
        )
        self.assertIn('/for-lenders', d['body'])
        self.assertIn('P.S.', d['body'])

    @patch('ai_client.get_ai_response')
    def test_does_not_double_append_when_llm_includes_url(self, mock_ai):
        """LLM includes the URL naturally → no P.S. appended (avoid dupe)."""
        from prospect_research_service import draft_email
        mock_ai.return_value = json.dumps({
            'subject': 'Question on collateral risk',
            'body': "I'm building OfferWise. There's a one-pager for "
                    "lender folks at https://www.getofferwise.ai/for-lenders. "
                    "Worth 20 minutes?",
        })
        d = draft_email(
            name='Jane Doe', title='Head of Underwriting',
            company='ExampleBank', wedge='renovation_lenders',
        )
        # URL is in body once
        self.assertEqual(d['body'].count('/for-lenders'), 1)
        # No double "P.S." prefix
        self.assertNotIn('P.S.', d['body'])

    @patch('ai_client.get_ai_response')
    def test_unknown_wedge_appends_personas_url(self, mock_ai):
        """Unknown wedge → safety net uses /personas URL."""
        from prospect_research_service import draft_email
        mock_ai.return_value = json.dumps({
            'subject': 'Quick question',
            'body': "Reaching out about your platform. Worth 20 minutes?",
        })
        d = draft_email(
            name='Jane Doe', title='CEO', company='ExampleCo',
            wedge='other',
        )
        self.assertIn('/personas', d['body'])


if __name__ == '__main__':
    unittest.main()
