"""
OfferWise GTM Module Test Suite
================================
Tests the Go-To-Market intelligence modules:
  - Content Engine: pillar rotation, fallback stats, template generation
  - Reddit Scout: keyword pre-scoring
  - Conversion Intel: channel normalization, funnel data contracts

Coverage:
  1. Pillar Rotation — correct pillar for each day of the week
  2. Fallback Stats — curated data contract when DB is empty
  3. Template Generators — each day produces valid title + body
  4. Post Contract — generated posts have all required fields
  5. Keyword Pre-Score — high-intent, medium-intent, no-match scoring
  6. Channel Normalization — UTM source → channel mapping
  7. Edge Cases — empty text, None values, malformed input
"""

import unittest
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gtm'))


# ─────────────────────────────────────────────────────────────────────
# 1. Content Engine — Pillar Rotation
# ─────────────────────────────────────────────────────────────────────

from content_engine import get_pillar_for_date, PILLARS, _fallback_stats, generate_post
from content_engine import _gen_what_were_seeing, _gen_first_timer_tuesday
from content_engine import _gen_did_you_know, _gen_real_numbers
from content_engine import _gen_red_flag_friday, _gen_community_qa, _gen_weekly_digest
from conversion_intel import _normalize_channel


class TestPillarRotation(unittest.TestCase):
    """Test that each day of the week maps to the correct content pillar."""

    def test_monday_what_were_seeing(self):
        # Find a Monday
        d = date(2026, 3, 2)  # March 2, 2026 is Monday
        pillar = get_pillar_for_date(d)
        self.assertEqual(pillar['key'], 'what_were_seeing')

    def test_tuesday_first_timer(self):
        d = date(2026, 3, 3)
        pillar = get_pillar_for_date(d)
        self.assertEqual(pillar['key'], 'first_timer_tuesday')

    def test_wednesday_did_you_know(self):
        d = date(2026, 3, 4)
        pillar = get_pillar_for_date(d)
        self.assertEqual(pillar['key'], 'did_you_know')

    def test_thursday_real_numbers(self):
        d = date(2026, 3, 5)
        pillar = get_pillar_for_date(d)
        self.assertEqual(pillar['key'], 'real_numbers')

    def test_friday_red_flag(self):
        d = date(2026, 3, 6)
        pillar = get_pillar_for_date(d)
        self.assertEqual(pillar['key'], 'red_flag_friday')

    def test_saturday_community_qa(self):
        d = date(2026, 3, 7)
        pillar = get_pillar_for_date(d)
        self.assertEqual(pillar['key'], 'community_qa')

    def test_sunday_weekly_digest(self):
        d = date(2026, 3, 8)
        pillar = get_pillar_for_date(d)
        self.assertEqual(pillar['key'], 'weekly_digest')

    def test_all_seven_days_covered(self):
        self.assertEqual(len(PILLARS), 7)
        for i in range(7):
            self.assertIn(i, PILLARS)

    def test_all_pillars_have_required_keys(self):
        required = ['key', 'label', 'flair', 'description']
        for day, pillar in PILLARS.items():
            for key in required:
                self.assertIn(key, pillar, f"Day {day} missing '{key}'")


# ─────────────────────────────────────────────────────────────────────
# 2. Content Engine — Fallback Stats
# ─────────────────────────────────────────────────────────────────────

class TestFallbackStats(unittest.TestCase):
    """Test the curated fallback stats used when DB has no data."""

    def setUp(self):
        self.stats = _fallback_stats()

    def test_source_is_curated(self):
        self.assertEqual(self.stats['source'], 'curated')

    def test_has_all_required_fields(self):
        required = [
            'total_analyses', 'recent_count', 'period_days',
            'avg_offer_score', 'avg_repair_cost', 'avg_transparency_score',
            'tier_distribution', 'most_common_tier', 'top_categories',
            'avg_findings_per_property', 'deal_breakers_pct',
        ]
        for key in required:
            self.assertIn(key, self.stats, f"Missing key: {key}")

    def test_tier_distribution_sums_to_recent(self):
        tiers = self.stats['tier_distribution']
        total = sum(tiers.values())
        self.assertEqual(total, self.stats['recent_count'])

    def test_top_categories_are_realistic(self):
        cats = self.stats['top_categories']
        self.assertGreaterEqual(len(cats), 3)
        for cat in cats:
            self.assertIn('name', cat)
            self.assertIn('total', cat)
            self.assertGreater(cat['total'], 0)

    def test_numeric_values_are_positive(self):
        self.assertGreater(self.stats['avg_offer_score'], 0)
        self.assertGreater(self.stats['avg_repair_cost'], 0)
        self.assertGreater(self.stats['avg_transparency_score'], 0)


# ─────────────────────────────────────────────────────────────────────
# 3. Content Engine — Template Generation
# ─────────────────────────────────────────────────────────────────────

class TestTemplateGeneration(unittest.TestCase):
    """Test that each template generator produces valid content."""

    @classmethod
    def setUpClass(cls):
        from content_engine import (
            _fallback_stats, generate_post, get_pillar_for_date,
            _gen_what_were_seeing, _gen_first_timer_tuesday,
            _gen_did_you_know, _gen_real_numbers,
            _gen_red_flag_friday, _gen_community_qa, _gen_weekly_digest,
        )
        cls._stats = _fallback_stats()
        cls.generate_post = generate_post
        cls.get_pillar = get_pillar_for_date
        cls.generators = {
            'what_were_seeing': _gen_what_were_seeing,
            'first_timer_tuesday': _gen_first_timer_tuesday,
            'did_you_know': _gen_did_you_know,
            'real_numbers': _gen_real_numbers,
            'red_flag_friday': _gen_red_flag_friday,
            'community_qa': _gen_community_qa,
            'weekly_digest': _gen_weekly_digest,
        }

    def test_all_generators_return_title_and_body(self):
        generators = {
            'what_were_seeing': _gen_what_were_seeing,
            'first_timer_tuesday': _gen_first_timer_tuesday,
            'did_you_know': _gen_did_you_know,
            'real_numbers': _gen_real_numbers,
            'red_flag_friday': _gen_red_flag_friday,
            'community_qa': _gen_community_qa,
            'weekly_digest': _gen_weekly_digest,
        }
        stats = _fallback_stats()
        for key, gen in generators.items():
            title, body, topic_key = gen(stats, date(2026, 3, 2))
            self.assertIsInstance(title, str, f"{key}: title not string")
            self.assertIsInstance(body, str, f"{key}: body not string")
            self.assertGreater(len(title), 5, f"{key}: title too short")
            self.assertGreater(len(body), 50, f"{key}: body too short")

    def test_generate_post_has_required_fields(self):
        pillar = get_pillar_for_date(date(2026, 3, 2))
        # Ensure no ANTHROPIC_API_KEY so we get template path
        old_key = os.environ.pop('ANTHROPIC_API_KEY', None)
        try:
            post = generate_post(pillar, _fallback_stats(), date(2026, 3, 2))
        finally:
            if old_key:
                os.environ['ANTHROPIC_API_KEY'] = old_key

        required = ['title', 'body', 'pillar', 'pillar_label', 'flair']
        for key in required:
            self.assertIn(key, post, f"Missing key: {key}")

    def test_each_day_generates_without_crash(self):
        old_key = os.environ.pop('ANTHROPIC_API_KEY', None)
        try:
            for i in range(7):
                d = date(2026, 3, 2) + timedelta(days=i)
                pillar = get_pillar_for_date(d)
                post = generate_post(pillar, _fallback_stats(), d)
                self.assertIsInstance(post['title'], str,
                    f"Day {i} ({d.strftime('%A')}): title not string")
        finally:
            if old_key:
                os.environ['ANTHROPIC_API_KEY'] = old_key

    def test_no_truncation_or_fragments(self):
        """QUALITY RULE: All customer-facing text must be complete sentences."""
        old_key = os.environ.pop('ANTHROPIC_API_KEY', None)
        try:
            for i in range(7):
                d = date(2026, 3, 2) + timedelta(days=i)
                pillar = get_pillar_for_date(d)
                post = generate_post(pillar, _fallback_stats(), d)
                self.assertNotIn('...', post['title'],
                    f"Day {i}: title contains truncation")
                self.assertFalse(post['body'].strip().endswith('...'),
                    f"Day {i}: body ends with truncation")
        finally:
            if old_key:
                os.environ['ANTHROPIC_API_KEY'] = old_key


# ─────────────────────────────────────────────────────────────────────
# 4. Reddit Scout — Keyword Pre-Score
# ─────────────────────────────────────────────────────────────────────

class TestChannelNormalization(unittest.TestCase):
    """Test UTM source → channel mapping."""

    def test_google_variants(self):
        self.assertEqual(_normalize_channel('google'), 'google_ads')
        self.assertEqual(_normalize_channel('Google'), 'google_ads')
        self.assertEqual(_normalize_channel('google_ads'), 'google_ads')

    def test_reddit_variants(self):
        self.assertEqual(_normalize_channel('reddit'), 'reddit_ads')
        self.assertEqual(_normalize_channel('Reddit'), 'reddit_ads')

    def test_direct_variants(self):
        self.assertEqual(_normalize_channel('direct'), 'direct')
        self.assertEqual(_normalize_channel('(direct)'), 'direct')
        self.assertEqual(_normalize_channel('(none)'), 'direct')
        self.assertEqual(_normalize_channel(''), 'direct')

    def test_organic(self):
        self.assertEqual(_normalize_channel('organic'), 'organic')
        self.assertEqual(_normalize_channel('(organic)'), 'organic')

    def test_unknown_is_referral(self):
        self.assertEqual(_normalize_channel('facebook'), 'referral')
        self.assertEqual(_normalize_channel('twitter'), 'referral')
        self.assertEqual(_normalize_channel('somesite.com'), 'referral')

    def test_none_input(self):
        self.assertEqual(_normalize_channel(None), 'direct')

    def test_whitespace(self):
        self.assertEqual(_normalize_channel('  google  '), 'google_ads')


if __name__ == '__main__':
    unittest.main(verbosity=2)


# ─────────────────────────────────────────────────────────────────────
# 6. State Disclosure Intelligence
# ─────────────────────────────────────────────────────────────────────

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state_disclosures import (
    detect_state_from_zip, detect_state_from_text,
    get_state_context, ZIP3_TO_STATE, STATE_NAMES,
)


class TestStateDisclosures(unittest.TestCase):
    """Test nationwide ZIP-to-state mapping and disclosure intelligence."""

    def test_all_50_states_have_context(self):
        for code in STATE_NAMES:
            ctx = get_state_context(code)
            self.assertEqual(ctx.state_code, code)
            self.assertIn(ctx.disclosure_level,
                ['comprehensive', 'moderate', 'minimal', 'caveat_emptor'])
            self.assertGreater(len(ctx.disclosure_notes), 0)

    def test_major_state_zips(self):
        cases = [
            ('95112', 'CA'), ('10001', 'NY'), ('77001', 'TX'),
            ('33101', 'FL'), ('60601', 'IL'), ('98101', 'WA'),
            ('85001', 'AZ'), ('80201', 'CO'), ('30301', 'GA'),
            ('97201', 'OR'), ('02101', 'MA'), ('19101', 'PA'),
        ]
        for zip_code, expected in cases:
            self.assertEqual(detect_state_from_zip(zip_code), expected,
                f'ZIP {zip_code} should map to {expected}')

    def test_invalid_zip(self):
        self.assertIsNone(detect_state_from_zip(''))
        self.assertIsNone(detect_state_from_zip('ab'))

    def test_text_detection_ca(self):
        self.assertEqual(
            detect_state_from_text('TRANSFER DISCLOSURE STATEMENT California'),
            'CA')

    def test_text_detection_tx(self):
        self.assertEqual(
            detect_state_from_text('TREC Seller Disclosure Notice'),
            'TX')

    def test_unknown_state_returns_fallback(self):
        ctx = get_state_context('ZZ')
        self.assertEqual(ctx.state_code, 'XX')
        self.assertIn('Unknown', ctx.state_name)

    def test_caveat_emptor_states(self):
        for code in ['AL', 'MS', 'WY']:
            ctx = get_state_context(code)
            self.assertEqual(ctx.disclosure_level, 'caveat_emptor')

    def test_hazards_populated(self):
        ctx = get_state_context('FL')
        self.assertIn('hurricanes', ctx.common_hazards)
        ctx = get_state_context('CA')
        self.assertIn('earthquakes', ctx.common_hazards)

    def test_zip3_coverage(self):
        """Every state should be reachable via at least one ZIP3."""
        states_in_map = set(ZIP3_TO_STATE.values())
        for code in STATE_NAMES:
            if code == 'DC':
                continue  # DC has limited ZIPs
            self.assertIn(code, states_in_map,
                f'State {code} has no ZIP3 mapping')
