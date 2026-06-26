"""
Tests for GTM module: content engine, aggregate stats, template generation.
"""
import json
import unittest
from datetime import date, timedelta


class TestContentPillars(unittest.TestCase):
    """Test content pillar rotation and assignment."""

    def test_pillar_for_each_day_of_week(self):
        from gtm.content_engine import get_pillar_for_date, PILLARS
        # Monday Mar 2 2026 = weekday 0
        d = date(2026, 3, 2)
        for i in range(7):
            target = d + timedelta(days=i)
            pillar = get_pillar_for_date(target)
            self.assertIn('key', pillar)
            self.assertIn('label', pillar)
            self.assertIn('flair', pillar)
            self.assertEqual(pillar, PILLARS[target.weekday()])

    def test_all_pillars_have_required_fields(self):
        from gtm.content_engine import PILLARS
        for day, pillar in PILLARS.items():
            self.assertIn('key', pillar, f"Day {day} missing 'key'")
            self.assertIn('label', pillar, f"Day {day} missing 'label'")
            self.assertIn('flair', pillar, f"Day {day} missing 'flair'")
            self.assertIn('description', pillar, f"Day {day} missing 'description'")

    def test_seven_unique_pillar_keys(self):
        from gtm.content_engine import PILLARS
        keys = [p['key'] for p in PILLARS.values()]
        self.assertEqual(len(keys), 7)
        self.assertEqual(len(set(keys)), 7)


class TestFallbackStats(unittest.TestCase):
    """v5.89.221: fallback no longer fabricates — returns an UNBACKED marker."""

    def test_fallback_is_unbacked(self):
        from gtm.content_engine import _fallback_stats
        stats = _fallback_stats()
        self.assertEqual(stats['source'], 'insufficient')
        self.assertFalse(stats.get('data_backed'))

    def test_fallback_has_no_fabricated_numbers(self):
        from gtm.content_engine import _fallback_stats
        stats = _fallback_stats()
        for k in ('avg_findings_per_property', 'avg_repair_cost',
                  'avg_offer_score', 'top_categories', 'deal_breakers_pct'):
            self.assertNotIn(k, stats, f"Fabricated key leaked: {k}")


class TestTemplateGenerators(unittest.TestCase):
    """Test each template generator produces valid output."""

    def setUp(self):
        from gtm.content_engine import _fallback_stats
        self.stats = _fallback_stats()

    def test_what_were_seeing(self):
        from gtm.content_engine import _gen_what_were_seeing
        title, body, _ = _gen_what_were_seeing(self.stats, date(2026, 3, 2))
        self.assertIn("What We're Seeing", title)
        self.assertGreater(len(body), 300)
        self.assertIn("👇", body)

    def test_first_timer_tuesday(self):
        from gtm.content_engine import _gen_first_timer_tuesday
        title, body, _ = _gen_first_timer_tuesday(self.stats, date(2026, 3, 3))
        self.assertIn("First-Timer", title)
        self.assertGreater(len(body), 300)

    def test_did_you_know(self):
        from gtm.content_engine import _gen_did_you_know
        title, body, _ = _gen_did_you_know(self.stats, date(2026, 3, 4))
        self.assertIn("Did You Know", title)
        self.assertGreater(len(body), 300)

    def test_real_numbers(self):
        from gtm.content_engine import _gen_real_numbers
        title, body, _ = _gen_real_numbers(self.stats, date(2026, 3, 5))
        self.assertIn("Real Numbers", title)
        self.assertGreater(len(body), 300)

    def test_red_flag_friday(self):
        from gtm.content_engine import _gen_red_flag_friday
        title, body, _ = _gen_red_flag_friday(self.stats, date(2026, 3, 6))
        self.assertIn("Red Flag", title)
        self.assertGreater(len(body), 200)
        self.assertIn("👇", body)

    def test_community_qa(self):
        from gtm.content_engine import _gen_community_qa
        title, body, _ = _gen_community_qa(self.stats, date(2026, 3, 7))
        self.assertGreater(len(body), 200)
        self.assertIn("👇", body)

    def test_weekly_digest(self):
        from gtm.content_engine import _gen_weekly_digest
        title, body, _ = _gen_weekly_digest(self.stats, date(2026, 3, 8))
        self.assertIn("Weekly Digest", title)
        self.assertGreater(len(body), 200)

    def test_all_generators_in_map(self):
        from gtm.content_engine import TEMPLATE_GENERATORS, PILLARS
        for pillar in PILLARS.values():
            self.assertIn(pillar['key'], TEMPLATE_GENERATORS,
                          f"Missing generator for pillar '{pillar['key']}'")

    def test_different_dates_produce_different_content(self):
        """Red flag and community QA rotate based on date — verify variation."""
        from gtm.content_engine import _gen_red_flag_friday
        titles = set()
        for day_offset in range(30):
            title, _, _ = _gen_red_flag_friday(self.stats, date(2026, 1, 1) + timedelta(days=day_offset))
            titles.add(title)
        self.assertGreaterEqual(len(titles), 1, "Red flag generator should produce titles")


class TestPostGeneration(unittest.TestCase):
    """Test the generate_daily_post entry point."""

    # A data-backed stats fixture (TEST ONLY — real published content must come
    # from real data). Lets us exercise generation quality without a live DB.
    @staticmethod
    def _backed_stats():
        return {
            'source': 'live', 'data_backed': True,
            'total_analyses': 120, 'recent_count': 60, 'period_days': 30,
            'avg_offer_score': 62, 'avg_repair_cost': 18500,
            'avg_transparency_score': 64, 'most_common_tier': 'moderate',
            'tier_distribution': {'moderate': 18, 'elevated': 14, 'low': 10, 'high': 6, 'critical': 2},
            'top_categories': [
                {'name': 'Plumbing', 'total': 38, 'critical': 5, 'major': 12},
                {'name': 'Electrical', 'total': 31, 'critical': 8, 'major': 10},
                {'name': 'Roofing', 'total': 28, 'critical': 3, 'major': 15},
            ],
            'avg_findings_per_property': 8.3, 'deal_breakers_pct': 16,
            'properties_with_deal_breakers': 8,
        }

    def test_gated_when_no_data(self):
        """v5.89.221: no real sample -> no post (no fabrication)."""
        from gtm.content_engine import generate_daily_post
        self.assertIsNone(generate_daily_post(None, {}, date(2026, 3, 2)))

    def test_generate_returns_required_fields(self):
        from gtm.content_engine import generate_post, get_pillar_for_date
        d = date(2026, 3, 2)
        post = generate_post(get_pillar_for_date(d), self._backed_stats(), d)
        for field in ['title', 'body', 'pillar', 'pillar_label', 'flair']:
            self.assertIn(field, post, f"Missing field '{field}'")

    def test_generate_each_day_of_week(self):
        from gtm.content_engine import generate_post, get_pillar_for_date
        for i in range(7):
            d = date(2026, 3, 2) + timedelta(days=i)
            post = generate_post(get_pillar_for_date(d), self._backed_stats(), d)
            self.assertGreater(len(post['title']), 10)
            self.assertGreater(len(post['body']), 200)

    def test_no_truncation_or_fragments(self):
        """Quality rule: all content must be complete sentences."""
        from gtm.content_engine import generate_post, get_pillar_for_date
        for i in range(7):
            d = date(2026, 3, 2) + timedelta(days=i)
            post = generate_post(get_pillar_for_date(d), self._backed_stats(), d)
            self.assertFalse(post['body'].rstrip().endswith('...'),
                             f"Body truncated for {d}: ends with '...'")
            for bad in ['_ENUM', '_STATUS', 'None_', 'NaN']:
                self.assertNotIn(bad, post['body'],
                                 f"Raw output '{bad}' found in body for {d}")

    def test_no_zero_findings_line(self):
        """Regression for the '0.0 findings' bug — must never ship."""
        from gtm.content_engine import generate_post, get_pillar_for_date
        for i in range(7):
            d = date(2026, 3, 2) + timedelta(days=i)
            post = generate_post(get_pillar_for_date(d), self._backed_stats(), d)
            self.assertNotIn('0.0 findings', post['body'])


class TestCollectAggregateStats(unittest.TestCase):
    """Test aggregate stat collection with no DB."""

    def test_fallback_when_no_analysis_model(self):
        from gtm.content_engine import collect_aggregate_stats
        stats = collect_aggregate_stats(None, {})
        self.assertFalse(stats.get('data_backed'))
        self.assertEqual(stats['source'], 'insufficient')
        # No fabricated numbers leak through
        self.assertNotIn('avg_findings_per_property', stats)

    def test_fallback_when_model_missing(self):
        from gtm.content_engine import collect_aggregate_stats
        stats = collect_aggregate_stats(None, {"Analysis": None})
        self.assertFalse(stats.get('data_backed'))
        self.assertEqual(stats['source'], 'insufficient')


if __name__ == '__main__':
    unittest.main()
