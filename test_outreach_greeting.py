"""
test_outreach_greeting.py — v5.88.02

Tests the "Greetings <FirstName>," opener prepended to outreach drafts.

Two layers of behavior under test:
  1. _extract_first_name helper — handles real-world name shapes
  2. draft_email + _fallback_draft — actually prepend the greeting
"""
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('FLASK_ENV', 'testing')


class TestExtractFirstName(unittest.TestCase):
    """Unit tests on the helper itself."""

    def test_simple_first_last(self):
        from prospect_research_service import _extract_first_name
        self.assertEqual(_extract_first_name('Jane Doe'), 'Jane')

    def test_single_name(self):
        from prospect_research_service import _extract_first_name
        self.assertEqual(_extract_first_name('Sridhar'), 'Sridhar')

    def test_linkedin_export_format(self):
        """LinkedIn often exports as 'Last, First'. Must take the part
        after the comma."""
        from prospect_research_service import _extract_first_name
        self.assertEqual(_extract_first_name('Smith, John'), 'John')
        self.assertEqual(_extract_first_name('Doe, Jane Marie'), 'Jane')

    def test_lowercase_gets_capitalized(self):
        from prospect_research_service import _extract_first_name
        self.assertEqual(_extract_first_name('jane doe'), 'Jane')

    def test_uppercase_gets_capitalized(self):
        from prospect_research_service import _extract_first_name
        self.assertEqual(_extract_first_name('JANE DOE'), 'Jane')

    def test_mixed_case_preserved(self):
        """If the founder/database has 'McKinsey' or 'DeAndre' as the
        first name, preserve the deliberate capitalization rather than
        force-titlecase it."""
        from prospect_research_service import _extract_first_name
        self.assertEqual(_extract_first_name('DeAndre Smith'), 'DeAndre')
        self.assertEqual(_extract_first_name('McKinsey Doe'), 'McKinsey')

    def test_honorific_stripped(self):
        from prospect_research_service import _extract_first_name
        self.assertEqual(_extract_first_name('Mr. John Smith'), 'John')
        self.assertEqual(_extract_first_name('Dr. Jane Doe'), 'Jane')
        self.assertEqual(_extract_first_name('Prof Smith'), 'Smith')

    def test_middle_initial_ignored(self):
        from prospect_research_service import _extract_first_name
        self.assertEqual(_extract_first_name('Jane M. Doe'), 'Jane')

    def test_empty_or_none_returns_empty(self):
        from prospect_research_service import _extract_first_name
        self.assertEqual(_extract_first_name(''), '')
        self.assertEqual(_extract_first_name(None), '')
        self.assertEqual(_extract_first_name('   '), '')

    def test_only_honorific_returns_empty(self):
        """Edge case: name field is just 'Mr.' — return empty rather than
        treating 'Mr' as the first name."""
        from prospect_research_service import _extract_first_name
        self.assertEqual(_extract_first_name('Mr.'), '')
        self.assertEqual(_extract_first_name('Dr'), '')


class TestGreetingInDraftEmail(unittest.TestCase):
    """Verify draft_email actually prepends the greeting."""

    @patch('ai_client.get_ai_response')
    def test_greeting_prepended_with_first_name(self, mock_ai):
        from prospect_research_service import draft_email
        mock_ai.return_value = json.dumps({
            'subject': 'Quick question on collateral risk',
            'body': "I'm reaching out about your underwriting workflow. "
                    "There's a one-pager at https://www.getofferwise.ai/for-lenders."
        })
        d = draft_email(
            name='Jane Doe',
            title='Head of Underwriting',
            company='ExampleBank',
            wedge='renovation_lenders',
        )
        self.assertTrue(d['body'].startswith('Greetings Jane,\n\n'))

    @patch('ai_client.get_ai_response')
    def test_greeting_uses_only_first_name_not_full(self, mock_ai):
        from prospect_research_service import draft_email
        mock_ai.return_value = json.dumps({
            'subject': 'Quick',
            'body': 'Body text https://www.getofferwise.ai/for-lenders'
        })
        d = draft_email(
            name='Sarah Chen',
            title='VP', company='Co',
            wedge='renovation_lenders',
        )
        self.assertIn('Greetings Sarah,', d['body'])
        self.assertNotIn('Greetings Sarah Chen,', d['body'])

    @patch('ai_client.get_ai_response')
    def test_greeting_handles_linkedin_export_format(self, mock_ai):
        from prospect_research_service import draft_email
        mock_ai.return_value = json.dumps({
            'subject': 'Quick',
            'body': 'Body text https://www.getofferwise.ai/for-lenders',
        })
        d = draft_email(
            name='Smith, John',
            title='VP', company='Co',
            wedge='renovation_lenders',
        )
        self.assertIn('Greetings John,', d['body'])
        # Must NOT use last name as first name
        self.assertNotIn('Greetings Smith,', d['body'])

    @patch('ai_client.get_ai_response')
    def test_no_name_uses_generic_greeting(self, mock_ai):
        """When name is empty/missing, fall back to 'Greetings,' rather
        than 'Greetings ,' or 'Greetings None,'."""
        from prospect_research_service import draft_email
        mock_ai.return_value = json.dumps({
            'subject': 'Quick',
            'body': 'Body https://www.getofferwise.ai/for-lenders',
        })
        d = draft_email(
            name='',
            title='VP', company='Co',
            wedge='renovation_lenders',
        )
        self.assertTrue(d['body'].startswith('Greetings,\n\n'))
        # No double comma, no stray space
        self.assertNotIn('Greetings ,', d['body'])
        self.assertNotIn('Greetings None,', d['body'])

    @patch('ai_client.get_ai_response')
    def test_greeting_appears_before_body_content(self, mock_ai):
        """The greeting comes FIRST, then the LLM body. Order matters."""
        from prospect_research_service import draft_email
        mock_ai.return_value = json.dumps({
            'subject': 'Quick',
            'body': 'When a property you finance has issues...',
        })
        d = draft_email(
            name='Jane Doe', title='VP', company='Co',
            wedge='renovation_lenders',
        )
        body = d['body']
        greeting_pos = body.find('Greetings Jane,')
        content_pos = body.find('When a property')
        self.assertNotEqual(greeting_pos, -1)
        self.assertNotEqual(content_pos, -1)
        self.assertLess(greeting_pos, content_pos)

    @patch('ai_client.get_ai_response')
    def test_greeting_separated_from_body_by_blank_line(self, mock_ai):
        """For visual clarity in admin review and email rendering, the
        greeting is followed by a blank line before the body."""
        from prospect_research_service import draft_email
        mock_ai.return_value = json.dumps({
            'subject': 'Quick',
            'body': 'When a property...',
        })
        d = draft_email(
            name='Jane Doe', title='VP', company='Co',
            wedge='renovation_lenders',
        )
        # \n\n between greeting and body content
        self.assertIn('Greetings Jane,\n\nWhen a property', d['body'])


class TestGreetingInFallbackDraft(unittest.TestCase):
    """The static fallback (used when LLM call fails) must also include
    the greeting, in the same format."""

    def test_fallback_includes_greeting_with_first_name(self):
        from prospect_research_service import _fallback_draft
        d = _fallback_draft(
            name='Jane Doe',
            company='ExampleCorp',
            wedge_pain='this space',
            landing_url='https://www.getofferwise.ai/for-lenders',
            company_role='lender / underwriter',
        )
        self.assertTrue(d['body'].startswith('Greetings Jane,\n\n'))

    def test_fallback_handles_no_name(self):
        from prospect_research_service import _fallback_draft
        d = _fallback_draft(
            name='',
            company='ExampleCorp',
            wedge_pain='this space',
            landing_url='https://www.getofferwise.ai/personas',
            company_role='partner',
        )
        self.assertTrue(d['body'].startswith('Greetings,\n\n'))

    def test_fallback_old_buggy_no_greeting_behavior_is_gone(self):
        """Regression test: old code had `first_name = ... or 'there'`
        but never USED first_name in the body. Now it's properly used."""
        from prospect_research_service import _fallback_draft
        d = _fallback_draft(
            name='Jane Doe',
            company='Co',
            wedge_pain='this space',
        )
        # Verify the greeting actually appears (not just defined as a var)
        self.assertIn('Greetings Jane,', d['body'])


class TestSignoff(unittest.TestCase):
    """v5.88.03: every outreach draft body must end with '-Francis'
    on its own line, separated from the body content above by a blank line.
    The signoff is a deliberate part of the draft so the founder sees and
    can edit it during review."""

    @patch('ai_client.get_ai_response')
    def test_signoff_appended_to_llm_body(self, mock_ai):
        from prospect_research_service import draft_email
        mock_ai.return_value = json.dumps({
            'subject': 'Quick question',
            'body': 'When a property has issues, the gap surfaces too late. '
                    'There is a one-pager at https://www.getofferwise.ai/for-lenders.',
        })
        d = draft_email(
            name='Jane Doe', title='VP', company='Co',
            wedge='renovation_lenders',
        )
        # Signoff is on its own line at the end (with the URL P.S. NOT
        # firing because the URL is already in the body)
        self.assertTrue(d['body'].rstrip().endswith('-Francis'))

    @patch('ai_client.get_ai_response')
    def test_signoff_separated_from_body_by_blank_line(self, mock_ai):
        """Founder explicitly asked: 'Give a line break just before this end.'
        That means \\n\\n separating the body from the signoff."""
        from prospect_research_service import draft_email
        mock_ai.return_value = json.dumps({
            'subject': 'Quick question',
            'body': 'Body text here. There is a one-pager at '
                    'https://www.getofferwise.ai/for-lenders.',
        })
        d = draft_email(
            name='Jane Doe', title='VP', company='Co',
            wedge='renovation_lenders',
        )
        # The signoff is preceded by a blank line (so the textarea/email
        # shows visual separation between body and sign)
        self.assertIn('\n\n-Francis', d['body'])

    @patch('ai_client.get_ai_response')
    def test_signoff_comes_before_url_ps(self, mock_ai):
        """Email convention: signoff comes BEFORE the P.S., not after.
        When the LLM omits the URL and the safety net adds a P.S., the
        order should be: body → -Francis → P.S."""
        from prospect_research_service import draft_email
        # LLM returns a body WITHOUT the URL — defensive append fires
        mock_ai.return_value = json.dumps({
            'subject': 'Quick question',
            'body': 'When a property has issues, the gap surfaces too late.',
        })
        d = draft_email(
            name='Jane Doe', title='VP', company='Co',
            wedge='renovation_lenders',
        )
        body = d['body']
        signoff_pos = body.find('-Francis')
        ps_pos = body.find('P.S.')
        self.assertNotEqual(signoff_pos, -1, 'signoff missing')
        self.assertNotEqual(ps_pos, -1, 'P.S. missing (URL fallback should fire)')
        # Signoff comes BEFORE the P.S. (lower index)
        self.assertLess(signoff_pos, ps_pos,
                        'signoff must come before P.S. (email convention)')

    @patch('ai_client.get_ai_response')
    def test_no_double_signoff_if_llm_already_signed(self, mock_ai):
        """Defensive: if the LLM ignored the no-signature instruction
        and signed off itself, don't double-stamp."""
        from prospect_research_service import draft_email
        mock_ai.return_value = json.dumps({
            'subject': 'Quick question',
            'body': 'Body text here.\n\n-Francis',
        })
        d = draft_email(
            name='Jane Doe', title='VP', company='Co',
            wedge='renovation_lenders',
        )
        # Should appear EXACTLY once
        self.assertEqual(d['body'].count('-Francis'), 1)

    @patch('ai_client.get_ai_response')
    def test_no_double_signoff_for_alternate_signoff_phrasings(self, mock_ai):
        """Guard against common alternate signoffs the LLM might write."""
        from prospect_research_service import draft_email
        for ending in [
            'Body text.\n\nBest, Francis',
            'Body text.\n\nThanks, Francis',
            'Body text.\n\nFrancis Anthony',
        ]:
            mock_ai.return_value = json.dumps({
                'subject': 'Quick',
                'body': ending,
            })
            d = draft_email(
                name='Jane', title='VP', company='Co',
                wedge='renovation_lenders',
            )
            # The original signature must remain; we should NOT have
            # added a fresh "-Francis" on top
            self.assertNotIn('-Francis', d['body'].rstrip()[-15:],
                             f'double-signoff detected after ending: {ending!r}')

    def test_fallback_includes_signoff(self):
        """The static fallback draft must also end with -Francis."""
        from prospect_research_service import _fallback_draft
        d = _fallback_draft(
            name='Jane Doe', company='Co',
            wedge_pain='this space',
            landing_url='https://www.getofferwise.ai/for-lenders',
            company_role='lender / underwriter',
        )
        self.assertTrue(d['body'].rstrip().endswith('-Francis'))
        self.assertIn('\n\n-Francis', d['body'])

    @patch('ai_client.get_ai_response')
    def test_full_shape_greeting_body_signoff_ps(self, mock_ai):
        """End-to-end shape test: the order must be
        Greetings → body → -Francis → P.S. (when URL fallback fires)."""
        from prospect_research_service import draft_email
        mock_ai.return_value = json.dumps({
            'subject': 'Quick',
            'body': 'When a property has problems, the cost lands later.',
        })
        d = draft_email(
            name='Jane Doe', title='VP', company='Co',
            wedge='renovation_lenders',
        )
        body = d['body']
        positions = {
            'greeting': body.find('Greetings Jane,'),
            'content': body.find('When a property'),
            'signoff': body.find('-Francis'),
            'ps': body.find('P.S.'),
        }
        # All four pieces present
        for label, pos in positions.items():
            self.assertNotEqual(pos, -1, f'{label} missing from body')
        # Strict order
        self.assertLess(positions['greeting'], positions['content'])
        self.assertLess(positions['content'], positions['signoff'])
        self.assertLess(positions['signoff'], positions['ps'])


if __name__ == '__main__':
    unittest.main()
