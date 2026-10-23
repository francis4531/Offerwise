"""test_vendor_scrub.py — v5.89.347

No commercial data or infrastructure vendor may appear on any surface a user can
reach: served static pages, the JSON an analysis is built from, stored results, or
shared-report snapshots. Government sources (FEMA, USGS, EPA, Census) are public
data and stay attributed.
"""
import glob
import json
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

VENDOR_RE = re.compile(
    r"RentCast|rentcast|AirNow|airnowapi|GreatSchools|greatschools|"
    r"RSMeans|HomeAdvisor|HomeGuide|\bAngi\b|Anthropic|\bClaude\b|\bSonnet\b|\bHaiku\b|"
    r"\bGPT-?5\b|OpenAI|\bSentry\b|Resend API|OpenStreetMap|Overpass|Tesseract|PaddleOCR|Google Vision",
    re.IGNORECASE)

# Admin-only and internal surfaces are allowed to name vendors.
ALLOWED = ('admin', 'debug.html', 'mockup', 'roadmap', 'persona-matrix', 'sw.js')


class TestServedStaticIsClean(unittest.TestCase):
    def test_no_vendor_names_in_public_static(self):
        offenders = []
        for fn in glob.glob(os.path.join(HERE, 'static', '**', '*'), recursive=True):
            if not fn.endswith(('.html', '.js', '.css', '.txt', '.xml', '.json')):
                continue
            if any(a in os.path.basename(fn) for a in ALLOWED):
                continue
            with open(fn, encoding='utf-8', errors='ignore') as f:
                for i, line in enumerate(f, 1):
                    m = VENDOR_RE.search(line)
                    if m:
                        offenders.append(f"{os.path.relpath(fn, HERE)}:{i}: {m.group(0)}")
        self.assertEqual(offenders, [], "\n".join(offenders[:40]))

    def test_internal_pages_are_not_under_static(self):
        for name in ('architecture.html', 'thesis.html', 'pitch.html', 'personas.html'):
            self.assertFalse(os.path.exists(os.path.join(HERE, 'static', name)), name)
            self.assertTrue(os.path.exists(os.path.join(HERE, 'docs', 'internal', name)), name)

    def test_internal_routes_are_access_gated(self):
        with open(os.path.join(HERE, 'app.py'), encoding='utf-8') as f:
            src = f.read()
        for route in ("def architecture_page(", "def thesis(", "def pitch(", "def personas("):
            i = src.index(route)
            self.assertIn('has_valid_cookie(request, db)', src[i:i + 900], route)


class TestResultScrub(unittest.TestCase):
    SAMPLE = {
        'research_data': {'tool_results': [
            {'tool_name': 'rentcast', 'status': 'success', 'source_url': 'https://www.rentcast.io',
             'error': 'RentCast quota exceeded (HTTP 402). Check usage at app.rentcast.io/app/api',
             'data': {'avm_price': 683000}},
            {'tool_name': 'walk_score', 'source_url': 'https://www.example-retired-vendor.com', 'data': {'walk_score': 40}},
            {'tool_name': 'flood_zone', 'source_url': 'https://hazards.fema.gov', 'data': {}},
        ]},
        'repair_estimate': {'methodology': 'Costs based on RSMeans 2026 and HomeAdvisor national averages.'},
        'predicted_issues': [{'observable_indicators': ['Per GreatSchools the school is fine']}],
        'offer_strategy': {'market_context': {'market_rationale': 'RentCast AVM $683k'}},
    }

    def test_scrub(self):
        from vendor_scrub import scrub_result
        out = scrub_result(self.SAMPLE)
        self.assertFalse(VENDOR_RE.search(json.dumps(out)), json.dumps(out)[:400])
        tools = {t['tool_name']: t for t in out['research_data']['tool_results']}
        self.assertIn('valuation', tools)
        self.assertNotIn('source_url', tools['valuation'])
        self.assertEqual(tools['valuation']['data']['avm_price'], 683000)
        self.assertEqual(tools['flood_zone']['source_url'], 'https://hazards.fema.gov')  # government stays
        self.assertNotIn('walk_score', tools)   # retired integration rows are dropped, not renamed
        self.assertNotIn('retired-vendor', json.dumps(out))

    def test_app_reads_valuation_tool_name(self):
        with open(os.path.join(HERE, 'static', 'app.html'), encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn("tool_name === 'rentcast'", src)
        self.assertIn("tool_name === 'valuation'", src)

    def test_scrub_is_wired_at_store_and_serve(self):
        with open(os.path.join(HERE, 'analysis_routes.py'), encoding='utf-8') as f:
            ar = f.read()
        self.assertEqual(ar.count('from vendor_scrub import scrub_result'), 2)
        with open(os.path.join(HERE, 'app.py'), encoding='utf-8') as f:
            self.assertIn('return jsonify(_scrub(result_json))', f.read())
        with open(os.path.join(HERE, 'sharing_routes.py'), encoding='utf-8') as f:
            self.assertEqual(f.read().count('_scrub('), 2)

    def test_repair_methodology_text_is_neutral(self):
        from repair_cost_estimator import estimate_repair_costs
        est = estimate_repair_costs('75035', [{'category': 'plumbing', 'severity': 'minor', 'description': 'Drip line leak', 'trade': 'irrigation_grounds'}], [], 0, 0, None)
        self.assertFalse(VENDOR_RE.search(json.dumps(est)), est['methodology'])


if __name__ == '__main__':
    unittest.main()
