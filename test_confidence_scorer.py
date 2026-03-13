"""Tests for ConfidenceScorer — 7-layer confidence system."""
import unittest, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from confidence_scorer import ConfidenceScorer

def _analysis():
    return {'risk_score': {'overall_risk_score': 45, 'category_scores': [{'category': 'structural', 'score': 50, 'cost_low': 1000, 'cost_high': 3000}], 'total_repair_cost_low': 1000, 'total_repair_cost_high': 3000, 'deal_breakers': []}, 'offer_strategy': {'recommended_price': 450000}}

def _input():
    return {'disclosure_text': 'A ' * 500, 'inspection_text': 'B ' * 500, 'property_price': 500000}

class TestScoreRange(unittest.TestCase):
    def setUp(self): self.s = ConfidenceScorer()
    def test_normal(self):
        r = self.s.calculate(_analysis(), _input())
        self.assertTrue(0 <= r['score'] <= 100)
    def test_empty_analysis(self):
        r = self.s.calculate({}, _input())
        self.assertTrue(0 <= r['score'] <= 100)
    def test_empty_input(self):
        r = self.s.calculate(_analysis(), {})
        self.assertTrue(0 <= r['score'] <= 100)

class TestInputQuality(unittest.TestCase):
    def setUp(self): self.s = ConfidenceScorer()
    def test_more_text_better(self):
        short = {'disclosure_text': 'x'*100, 'inspection_text': 'y'*100, 'property_price': 500000}
        long = {'disclosure_text': 'x'*10000, 'inspection_text': 'y'*10000, 'property_price': 500000}
        self.assertGreaterEqual(self.s._score_input_quality(long), self.s._score_input_quality(short))

class TestConfidenceLevel(unittest.TestCase):
    def setUp(self): self.s = ConfidenceScorer()
    def test_high(self):
        level = self.s._get_confidence_level(90)
        self.assertIsInstance(level, str)
    def test_low(self):
        level = self.s._get_confidence_level(20)
        self.assertIsInstance(level, str)

class TestResultStructure(unittest.TestCase):
    def setUp(self): self.s = ConfidenceScorer()
    def test_keys(self):
        r = self.s.calculate(_analysis(), _input())
        for k in ['score', 'level', 'message', 'breakdown', 'recommendations']:
            self.assertIn(k, r, f"Missing: {k}")
    def test_breakdown_is_dict(self):
        r = self.s.calculate(_analysis(), _input())
        self.assertIsInstance(r['breakdown'], dict)
    def test_recommendations_is_list(self):
        r = self.s.calculate(_analysis(), _input())
        self.assertIsInstance(r['recommendations'], list)

class TestDisplayFormat(unittest.TestCase):
    def setUp(self): self.s = ConfidenceScorer()
    def test_output(self):
        r = self.s.calculate(_analysis(), _input())
        d = self.s.format_for_display(r)
        self.assertIsInstance(d, str)
        self.assertGreater(len(d), 0)

if __name__ == '__main__': unittest.main()
