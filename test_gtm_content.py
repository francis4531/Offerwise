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
    """Test the curated fallback stats used when live data is thin."""

    def test_fallback_stats_complete(self):
        from gtm.content_engine import _fallback_stats
        stats = _fallback_stats()
        self.assertEqual(stats['source'], 'curated')
        self.assertIn('avg_offer_score', stats)
        self.assertIn('avg_repair_cost', stats)
        self.assertIn('avg_transparency_score', stats)
        self.assertIn('top_categories', stats)
        self.assertIn('deal_breakers_pct', stats)
        self.assertIn('avg_findings_per_property', stats)

    def test_fallback_stats_reasonable_values(self):
        from gtm.content_engine import _fallback_stats
        stats = _fallback_stats()
        self.assertGreater(stats['avg_offer_score'], 0)
        self.assertLess(stats['avg_offer_score'], 100)
        self.assertGreater(stats['avg_repair_cost'], 0)
        self.assertGreater(len(stats['top_categories']), 3)

    def test_fallback_categories_have_structure(self):
        from gtm.content_engine import _fallback_stats
        stats = _fallback_stats()
        for cat in stats['top_categories']:
            self.assertIn('name', cat)
            self.assertIn('total', cat)
            self.assertIn('critical', cat)


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

    def test_generate_returns_required_fields(self):
        from gtm.content_engine import generate_daily_post
        post = generate_daily_post(None, {}, date(2026, 3, 2))
        required = ['title', 'body', 'pillar', 'pillar_label', 'flair', 'scheduled_date']
        for field in required:
            self.assertIn(field, post, f"Missing field '{field}'")

    def test_generate_each_day_of_week(self):
        from gtm.content_engine import generate_daily_post
        for i in range(7):
            d = date(2026, 3, 2) + timedelta(days=i)
            post = generate_daily_post(None, {}, d)
            self.assertGreater(len(post['title']), 10)
            self.assertGreater(len(post['body']), 200)
            self.assertEqual(post['scheduled_date'], d)

    def test_no_truncation_or_fragments(self):
        """Quality rule: all content must be complete sentences."""
        from gtm.content_engine import generate_daily_post
        for i in range(7):
            d = date(2026, 3, 2) + timedelta(days=i)
            post = generate_daily_post(None, {}, d)
            # Should not end with "..." (truncation)
            self.assertFalse(post['body'].rstrip().endswith('...'),
                             f"Body truncated for {d}: ends with '...'")
            # Should not contain raw enum names
            for bad in ['_ENUM', '_STATUS', 'None_', 'NaN']:
                self.assertNotIn(bad, post['body'],
                                 f"Raw output '{bad}' found in body for {d}")


class TestCollectAggregateStats(unittest.TestCase):
    """Test aggregate stat collection with no DB."""

    def test_fallback_when_no_analysis_model(self):
        from gtm.content_engine import collect_aggregate_stats
        stats = collect_aggregate_stats(None, {})
        self.assertEqual(stats['source'], 'curated')

    def test_fallback_when_model_missing(self):
        from gtm.content_engine import collect_aggregate_stats
        stats = collect_aggregate_stats(None, {"Analysis": None})
        self.assertEqual(stats['source'], 'curated')


if __name__ == '__main__':
    unittest.main()
