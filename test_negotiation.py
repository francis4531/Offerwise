"""Tests for NegotiationHub — rule-based logic paths (no AI needed)."""
import unittest, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from negotiation_hub import NegotiationHub

def _analysis():
    return {
        'property_address': '123 Main St, San Jose, CA 95134', 'property_price': 850000,
        'risk_score': {'overall_risk_score': 42, 'total_repair_cost_low': 8000, 'total_repair_cost_high': 22000,
            'category_scores': [{'category': 'Roofing', 'score': 65, 'cost_low': 3000, 'cost_high': 8000, 'findings': ['Granule loss']}],
            'deal_breakers': []},
        'offer_strategy': {'recommended_price': 820000, 'aggressive_price': 790000, 'conservative_price': 840000, 'total_discount': 30000},
        'cross_reference': {'total_matches': 8, 'confirmed': 5, 'contradictions': 2, 'omissions': 1},
        'transparency_report': {'transparency_score': 55, 'grade': 'C', 'red_flags': []},
    }

class TestInit(unittest.TestCase):
    def test_creates(self): self.assertIsNotNone(NegotiationHub())

class TestQuickTips(unittest.TestCase):
    def setUp(self): self.h = NegotiationHub()
    def test_returns_dict(self): self.assertIsInstance(self.h.get_quick_tips(_analysis()), dict)
    def test_empty(self): self.assertIsInstance(self.h.get_quick_tips({}), dict)

class TestParseList(unittest.TestCase):
    def setUp(self): self.h = NegotiationHub()
    def test_numbered(self):
        r = self.h._parse_list("1. First\n2. Second")
        self.assertIsInstance(r, list)
        self.assertGreater(len(r), 0)
    def test_bullets(self):
        r = self.h._parse_list("- A\n- B")
        self.assertIsInstance(r, list)
    def test_empty(self):
        r = self.h._parse_list("")
        self.assertIsInstance(r, list)

class TestParseResponse(unittest.TestCase):
    def setUp(self): self.h = NegotiationHub()
    def test_structured(self):
        r = self.h._parse_response("OPENING:\nBe nice\n\nLEVERAGE:\n1. Plumbing\n2. Roof")
        self.assertIsInstance(r, dict)

class TestFormatters(unittest.TestCase):
    def setUp(self): self.h = NegotiationHub()
    def test_offer_letter(self):
        strategy = {'opening': 'test', 'leverage': [{'issue': 'roof', 'cost': '$3K'}]}
        r = self.h._format_offer_letter(_analysis(), strategy, None)
        self.assertIsInstance(r, dict)
    def test_talking_points(self):
        strategy = {'opening': 'test', 'leverage': []}
        r = self.h._format_talking_points(_analysis(), strategy)
        self.assertIsInstance(r, dict)

if __name__ == '__main__': unittest.main()
