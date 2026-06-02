"""Tests for SellerTransparencyScorer — Seller Transparency Report™."""
import unittest, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from transparency_scorer import SellerTransparencyScorer, TransparencyGrade, TransparencyReport, RedFlag

class TestGradeBoundaries(unittest.TestCase):
    def setUp(self): self.s = SellerTransparencyScorer()
    def test_A_plus(self): self.assertEqual(self.s._score_to_grade(95), TransparencyGrade.A_PLUS)
    def test_A(self): self.assertEqual(self.s._score_to_grade(85), TransparencyGrade.A)
    def test_B(self): self.assertEqual(self.s._score_to_grade(75), TransparencyGrade.B)
    def test_C(self): self.assertEqual(self.s._score_to_grade(60), TransparencyGrade.C)
    def test_D(self): self.assertEqual(self.s._score_to_grade(45), TransparencyGrade.D)
    def test_F(self): self.assertEqual(self.s._score_to_grade(20), TransparencyGrade.F)
    def test_extremes(self):
        self.assertIsInstance(self.s._score_to_grade(150), TransparencyGrade)
        self.assertIsInstance(self.s._score_to_grade(-10), TransparencyGrade)

class TestTrustLevel(unittest.TestCase):
    def setUp(self): self.s = SellerTransparencyScorer()
    def test_high_score(self): self.assertIsInstance(self.s._calculate_trust_level(90, []), str)
    def test_low_score(self): self.assertIsInstance(self.s._calculate_trust_level(15, []), str)
    def test_with_flags(self):
        f = [RedFlag(flag_type='omission', severity='high', description='d', evidence=['e'], impact='i', recommendation='r')]
        self.assertIsInstance(self.s._calculate_trust_level(70, f), str)

class TestRiskAdjustment(unittest.TestCase):
    def setUp(self): self.s = SellerTransparencyScorer()
    def test_high_score(self): self.assertGreaterEqual(self.s._calculate_risk_adjustment(90, []), 0)
    def test_low_score_flags(self):
        f = [RedFlag(flag_type='omission', severity='high', description='d', evidence=['e'], impact='i', recommendation='r')]
        self.assertGreater(self.s._calculate_risk_adjustment(20, f), 0)

class TestCompositeScore(unittest.TestCase):
    def setUp(self): self.s = SellerTransparencyScorer()
    def test_perfect(self):
        self.assertGreaterEqual(self.s._calculate_composite_score(100, 100, 100, 100), 80)
    def test_zero(self):
        self.assertLessEqual(self.s._calculate_composite_score(0, 0, 0, 0), 20)
    def test_range(self):
        score = self.s._calculate_composite_score(50, 60, 40, 70)
        self.assertTrue(0 <= score <= 100)

class TestRedFlag(unittest.TestCase):
    def test_required_fields(self):
        f = RedFlag(flag_type='omission', severity='high', description='Test', evidence=['e'], impact='i', recommendation='r')
        self.assertEqual(f.flag_type, 'omission')
    def test_optional_pages(self):
        f = RedFlag(flag_type='x', severity='m', description='d', evidence=['e'], impact='i', recommendation='r', disclosure_page=3)
        self.assertEqual(f.disclosure_page, 3)

class TestReport(unittest.TestCase):
    def test_fields(self):
        r = TransparencyReport(
            property_address='123 Main', transparency_score=75.0, grade=TransparencyGrade.B,
            trust_level='moderate', omission_score=80.0, minimization_score=70.0,
            proactivity_score=60.0, consistency_score=90.0, red_flags=[],
            undisclosed_issues=[], minimized_issues=[], proactive_disclosures=[],
            risk_adjustment=2.5, investigation_recommendations=[], negotiation_leverage=[])
        self.assertEqual(r.transparency_score, 75.0)
        self.assertEqual(r.grade, TransparencyGrade.B)

if __name__ == '__main__': unittest.main()
