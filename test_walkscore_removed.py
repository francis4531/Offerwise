"""test_walkscore_removed.py — v5.89.347

Walk Score required a "Walk Score by Redfin" attribution (or a paid plan) and
OfferWise does not show vendor branding, so the integration was removed outright.
These tests make sure it never quietly comes back: no tool, no key, no API host,
no walk/transit/bike fields on the research profile, and nothing on the served
pages or the report that reads as a Walk Score product.
"""
import glob
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = [f for f in glob.glob(os.path.join(HERE, '*.py'))
       if not os.path.basename(f).startswith('test_')]
# vendor_scrub.py may name the retired FIELDS (to drop them from old stored
# analyses) but never the vendor itself.
VENDOR_NAME = re.compile(r"walkscore|Walk\s?Score|Transit\s?Score|Bike\s?Score", re.IGNORECASE)
STATIC = glob.glob(os.path.join(HERE, 'static', '**', '*.html'), recursive=True)
STATIC += glob.glob(os.path.join(HERE, 'static', '**', '*.js'), recursive=True)
STATIC += glob.glob(os.path.join(HERE, 'templates', '**', '*.html'), recursive=True)

FORBIDDEN = re.compile(r"walkscore|walk_score|transit_score|bike_score|WALKSCORE_API_KEY|"
                       r"Walk\s?Score|Transit\s?Score|Bike\s?Score|walkability", re.IGNORECASE)


class TestWalkScoreRemoved(unittest.TestCase):
    def test_tool_is_gone(self):
        import property_research_agent as pra
        self.assertFalse(hasattr(pra, 'WalkScoreTool'))
        names = [t.name for t in pra.PropertyResearchAgent.TOOL_CLASSES]
        self.assertNotIn('walk_score', names)
        fields = getattr(pra.PropertyProfile, '__dataclass_fields__', {})
        for f in ('walk_score', 'transit_score', 'bike_score'):
            self.assertNotIn(f, fields)

    def test_no_reference_in_python(self):
        hits = []
        for fn in SRC:
            pat = VENDOR_NAME if os.path.basename(fn) == 'vendor_scrub.py' else FORBIDDEN
            for i, line in enumerate(open(fn, encoding='utf-8', errors='ignore'), 1):
                if pat.search(line):
                    hits.append(f"{os.path.basename(fn)}:{i}: {line.strip()[:100]}")
        self.assertEqual(hits, [], "\n".join(hits))

    def test_no_reference_on_served_pages(self):
        hits = []
        for fn in STATIC:
            for i, line in enumerate(open(fn, encoding='utf-8', errors='ignore'), 1):
                if FORBIDDEN.search(line):
                    hits.append(f"{os.path.relpath(fn, HERE)}:{i}: {line.strip()[:100]}")
        self.assertEqual(hits, [], "\n".join(hits))

    def test_scrubber_still_neutralizes_old_stored_results(self):
        """Analyses saved by older builds carried walk_score tool rows; those
        must still be cleaned on the way out."""
        from vendor_scrub import scrub_result
        old = {'research': {'tool_results': [
            {'tool_name': 'walk_score', 'source_url': 'https://www.example-retired-vendor.com',
             'data': {'walk_description': 'retired vendor 40'}},
            {'tool_name': 'flood_zone', 'data': {}}]},
               'neighborhood': {'walk_score': 40, 'transit_score': 10, 'bike_score': 5, 'school_rating': 7.0}}
        out = scrub_result(old)
        self.assertEqual([r['tool_name'] for r in out['research']['tool_results']], ['flood_zone'])
        self.assertEqual(out['neighborhood'], {'school_rating': 7.0})
        self.assertNotIn('retired-vendor', str(out))


if __name__ == '__main__':
    unittest.main()
