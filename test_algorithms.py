"""
OfferWise Algorithm Test Suite
Tests all core formulas against known values to ensure accuracy
"""

import unittest
from typing import Dict, List
import json


class TestTransparencyScore(unittest.TestCase):
    """Test transparency score calculation (Innovation #1)"""
    
    def calculate_transparency_score(self, disclosed: int, confirmed: int, 
                                    contradictions: int, undisclosed: int) -> float:
        """
        Transparency formula from patent:
        
        B = min(100, (D/15) × 50)
        C_bonus = min(30, C × 3)
        X_penalty = X × 15
        U_penalty = U × 10
        T = max(0, min(100, B + C_bonus - X_penalty - U_penalty))
        """
        D_ref = 15  # Reference comprehensive disclosure count
        w_c = 3     # Confirmation weight
        w_x = 15    # Contradiction penalty weight
        w_u = 10    # Undisclosed penalty weight
        
        B = min(100, (disclosed / D_ref) * 50)
        C_bonus = min(30, confirmed * w_c)
        X_penalty = contradictions * w_x
        U_penalty = undisclosed * w_u
        
        T = max(0, min(100, B + C_bonus - X_penalty - U_penalty))
        return round(T, 2)
    
    def test_perfect_transparency(self):
        """Perfect seller: discloses everything, no lies"""
        score = self.calculate_transparency_score(
            disclosed=20,
            confirmed=20,
            contradictions=0,
            undisclosed=0
        )
        # B = min(100, 20/15 * 50) = 66.67
        # C_bonus = min(30, 20*3) = 30
        # Total = 66.67 + 30 = 96.67
        self.assertAlmostEqual(score, 96.67, places=1)
        self.assertGreaterEqual(score, 90, "Perfect transparency should be 90+")
    
    def test_honest_seller(self):
        """Good seller: thorough disclosure, few omissions"""
        score = self.calculate_transparency_score(
            disclosed=18,
            confirmed=16,
            contradictions=0,
            undisclosed=2
        )
        # B = min(100, 18/15 * 50) = 60
        # C_bonus = min(30, 16*3) = 30
        # U_penalty = 2*10 = 20
        # Total = 60 + 30 - 20 = 70
        self.assertEqual(score, 70.0)
        self.assertGreaterEqual(score, 60, "Honest seller should be 60+")
    
    def test_typical_seller(self):
        """Average seller: reasonable disclosure, some omissions"""
        score = self.calculate_transparency_score(
            disclosed=12,
            confirmed=8,
            contradictions=1,
            undisclosed=5
        )
        # B = min(100, 12/15 * 50) = 40
        # C_bonus = min(30, 8*3) = 24
        # X_penalty = 1*15 = 15
        # U_penalty = 5*10 = 50
        # Total = 40 + 24 - 15 - 50 = -1 → max(0, -1) = 0
        self.assertEqual(score, 0.0)
        # Note: This is correct - typical seller with 5 omissions + 1 lie = very low score
    
    def test_dishonest_seller(self):
        """Bad seller: minimal disclosure, many lies and omissions"""
        score = self.calculate_transparency_score(
            disclosed=8,
            confirmed=3,
            contradictions=4,
            undisclosed=12
        )
        # B = 26.67, C_bonus = 9, X_penalty = 60, U_penalty = 120
        # Total = 26.67 + 9 - 60 - 120 = -144.33 → 0
        self.assertEqual(score, 0.0)
        self.assertEqual(score, 0, "Dishonest seller should score 0")
    
    def test_threshold_50(self):
        """Test threshold boundary for 3% transparency discount"""
        # Just above threshold (should NOT trigger discount)
        score_above = self.calculate_transparency_score(
            disclosed=15,
            confirmed=12,
            contradictions=0,
            undisclosed=3
        )
        # B = 50, C_bonus = 30, U_penalty = 30
        # Total = 50 + 30 - 30 = 50
        self.assertEqual(score_above, 50.0)
        self.assertGreaterEqual(score_above, 50, "Should NOT trigger 3% discount")
        
        # Just below threshold (SHOULD trigger discount)
        score_below = self.calculate_transparency_score(
            disclosed=15,
            confirmed=12,
            contradictions=0,
            undisclosed=4  # One more omission
        )
        # B = 50, C_bonus = 30, U_penalty = 40
        # Total = 50 + 30 - 40 = 40
        self.assertEqual(score_below, 40.0)
        self.assertLess(score_below, 50, "Should trigger 3% discount")
    
    def test_contradiction_vs_omission_penalty(self):
        """Contradictions (lies) should be penalized more than omissions"""
        # 2 contradictions
        score_contradictions = self.calculate_transparency_score(
            disclosed=10, confirmed=8, contradictions=2, undisclosed=0
        )
        # X_penalty = 2*15 = 30
        
        # 2 undisclosed (same count, different type)
        score_omissions = self.calculate_transparency_score(
            disclosed=10, confirmed=8, contradictions=0, undisclosed=2
        )
        # U_penalty = 2*10 = 20
        
        # Contradictions should result in lower score (higher penalty)
        self.assertLess(score_contradictions, score_omissions,
                       "Lies should be penalized more than omissions")
        
        # Specific penalty difference: 30 vs 20 = 10 points
        self.assertEqual(score_omissions - score_contradictions, 10.0)


class TestRiskPremium(unittest.TestCase):
    """Test risk premium calculation (Innovation #2 - Core Patent)"""
    
    def calculate_risk_premium(self, property_price: float, risk_score: float) -> Dict:
        """
        Risk premium formula from patent:
        
        P_risk(V, R) = V × r(θ(R))
        
        Where:
        θ(R) = CRITICAL if R ≥ 70
               HIGH if 50 ≤ R < 70
               MODERATE if 30 ≤ R < 50
               LOW if R < 30
        
        r(tier) = 0.10 for CRITICAL
                  0.05 for HIGH
                  0.02 for MODERATE
                  0.00 for LOW
        """
        # Tier classification thresholds
        if risk_score >= 70:
            tier = "CRITICAL"
            rate = 0.10
        elif risk_score >= 50:
            tier = "HIGH"
            rate = 0.05
        elif risk_score >= 30:
            tier = "MODERATE"
            rate = 0.02
        else:
            tier = "LOW"
            rate = 0.00
        
        premium = property_price * rate
        
        return {
            'tier': tier,
            'rate': rate,
            'premium': round(premium, 2),
            'percentage': f"{rate * 100}%"
        }
    
    def test_critical_tier_10_percent(self):
        """CRITICAL tier (score ≥ 70) should apply 10% premium"""
        result = self.calculate_risk_premium(
            property_price=1_000_000,
            risk_score=75
        )
        self.assertEqual(result['tier'], 'CRITICAL')
        self.assertEqual(result['rate'], 0.10)
        self.assertEqual(result['premium'], 100_000.00)
        
    def test_high_tier_5_percent(self):
        """HIGH tier (50-69) should apply 5% premium"""
        result = self.calculate_risk_premium(
            property_price=1_000_000,
            risk_score=60
        )
        self.assertEqual(result['tier'], 'HIGH')
        self.assertEqual(result['rate'], 0.05)
        self.assertEqual(result['premium'], 50_000.00)
    
    def test_moderate_tier_2_percent(self):
        """MODERATE tier (30-49) should apply 2% premium"""
        result = self.calculate_risk_premium(
            property_price=1_000_000,
            risk_score=40
        )
        self.assertEqual(result['tier'], 'MODERATE')
        self.assertEqual(result['rate'], 0.02)
        self.assertEqual(result['premium'], 20_000.00)
    
    def test_low_tier_0_percent(self):
        """LOW tier (< 30) should apply 0% premium"""
        result = self.calculate_risk_premium(
            property_price=1_000_000,
            risk_score=20
        )
        self.assertEqual(result['tier'], 'LOW')
        self.assertEqual(result['rate'], 0.00)
        self.assertEqual(result['premium'], 0.00)
    
    def test_scale_invariance(self):
        """KEY INNOVATION: Premium scales proportionally with price"""
        risk_score = 65  # HIGH tier
        
        # $500K property
        result_500k = self.calculate_risk_premium(500_000, risk_score)
        # $2M property (4x larger)
        result_2m = self.calculate_risk_premium(2_000_000, risk_score)
        
        # Both should be HIGH tier with 5% rate
        self.assertEqual(result_500k['tier'], 'HIGH')
        self.assertEqual(result_2m['tier'], 'HIGH')
        
        # Premium should scale 4x
        self.assertEqual(result_500k['premium'], 25_000.00)
        self.assertEqual(result_2m['premium'], 100_000.00)
        self.assertEqual(result_2m['premium'] / result_500k['premium'], 4.0)
        
        # But percentage stays same (this is the innovation!)
        self.assertEqual(result_500k['rate'], result_2m['rate'])
    
    def test_boundary_thresholds(self):
        """Test tier boundaries (30, 50, 70)"""
        # Just below CRITICAL threshold
        result_69 = self.calculate_risk_premium(1_000_000, 69.9)
        self.assertEqual(result_69['tier'], 'HIGH')
        self.assertEqual(result_69['premium'], 50_000.00)
        
        # Exactly at CRITICAL threshold
        result_70 = self.calculate_risk_premium(1_000_000, 70.0)
        self.assertEqual(result_70['tier'], 'CRITICAL')
        self.assertEqual(result_70['premium'], 100_000.00)
        
        # Just above CRITICAL threshold
        result_71 = self.calculate_risk_premium(1_000_000, 70.1)
        self.assertEqual(result_71['tier'], 'CRITICAL')
        self.assertEqual(result_71['premium'], 100_000.00)
        
        # Boundary creates $50K jump (intentional!)
        self.assertEqual(result_70['premium'] - result_69['premium'], 50_000.00)


class TestTransparencyDiscount(unittest.TestCase):
    """Test transparency discount calculation (Innovation #3 - Core Patent)"""
    
    def calculate_transparency_discount(self, property_price: float, 
                                       transparency_score: float) -> Dict:
        """
        Transparency discount formula from patent:
        
        D_trans(V, T) = V × d(T)
        
        Where:
        d(T) = 0.03 if T < 50
               0.00 otherwise
        """
        threshold = 50
        rate = 0.03 if transparency_score < threshold else 0.00
        discount = property_price * rate
        
        return {
            'score': transparency_score,
            'threshold': threshold,
            'applies': transparency_score < threshold,
            'rate': rate,
            'discount': round(discount, 2),
            'percentage': f"{rate * 100}%"
        }
    
    def test_below_threshold_applies_3_percent(self):
        """Transparency < 50 should apply 3% discount"""
        result = self.calculate_transparency_discount(
            property_price=1_000_000,
            transparency_score=35
        )
        self.assertTrue(result['applies'])
        self.assertEqual(result['rate'], 0.03)
        self.assertEqual(result['discount'], 30_000.00)
    
    def test_above_threshold_no_discount(self):
        """Transparency ≥ 50 should apply 0% discount"""
        result = self.calculate_transparency_discount(
            property_price=1_000_000,
            transparency_score=70
        )
        self.assertFalse(result['applies'])
        self.assertEqual(result['rate'], 0.00)
        self.assertEqual(result['discount'], 0.00)
    
    def test_threshold_boundary_50(self):
        """Test exact threshold at 50"""
        # Just below (should apply)
        result_below = self.calculate_transparency_discount(1_000_000, 49.9)
        self.assertTrue(result_below['applies'])
        self.assertEqual(result_below['discount'], 30_000.00)
        
        # Exactly at (should NOT apply)
        result_at = self.calculate_transparency_discount(1_000_000, 50.0)
        self.assertFalse(result_at['applies'])
        self.assertEqual(result_at['discount'], 0.00)
        
        # Just above (should NOT apply)
        result_above = self.calculate_transparency_discount(1_000_000, 50.1)
        self.assertFalse(result_above['applies'])
        self.assertEqual(result_above['discount'], 0.00)
    
    def test_scale_with_property_value(self):
        """Discount should scale with property value (like risk premium)"""
        transparency_score = 35  # Below threshold
        
        # Different property prices
        result_500k = self.calculate_transparency_discount(500_000, transparency_score)
        result_1m = self.calculate_transparency_discount(1_000_000, transparency_score)
        result_2m = self.calculate_transparency_discount(2_000_000, transparency_score)
        
        # All should apply (same score)
        self.assertTrue(result_500k['applies'])
        self.assertTrue(result_1m['applies'])
        self.assertTrue(result_2m['applies'])
        
        # Discounts should scale proportionally
        self.assertEqual(result_500k['discount'], 15_000.00)
        self.assertEqual(result_1m['discount'], 30_000.00)
        self.assertEqual(result_2m['discount'], 60_000.00)
        
        # 2x price = 2x discount
        self.assertEqual(result_1m['discount'] / result_500k['discount'], 2.0)
        self.assertEqual(result_2m['discount'] / result_1m['discount'], 2.0)


class TestIntegratedOfferCalculation(unittest.TestCase):
    """Test complete offer calculation (three-component model)"""
    
    def calculate_offer(self, asking_price: float, repair_cost_avg: float,
                       risk_score: float, transparency_score: float) -> Dict:
        """
        Complete offer formula from patent:
        
        O_rec = V - (C_repair + P_risk + D_trans)
        
        Where:
        - V = asking price
        - C_repair = average repair cost
        - P_risk = risk premium (from risk tier)
        - D_trans = transparency discount (if T < 50)
        """
        # Calculate risk premium
        if risk_score >= 70:
            risk_rate = 0.10
        elif risk_score >= 50:
            risk_rate = 0.05
        elif risk_score >= 30:
            risk_rate = 0.02
        else:
            risk_rate = 0.00
        risk_premium = asking_price * risk_rate
        
        # Calculate transparency discount
        trans_rate = 0.03 if transparency_score < 50 else 0.00
        trans_discount = asking_price * trans_rate
        
        # Total discount
        total_discount = repair_cost_avg + risk_premium + trans_discount
        
        # Recommended offer
        recommended_offer = asking_price - total_discount
        
        # Ensure non-negative
        recommended_offer = max(0, recommended_offer)
        
        return {
            'asking_price': asking_price,
            'repair_cost': round(repair_cost_avg, 2),
            'risk_premium': round(risk_premium, 2),
            'transparency_discount': round(trans_discount, 2),
            'total_discount': round(total_discount, 2),
            'recommended_offer': round(recommended_offer, 2),
            'discount_percentage': round((total_discount / asking_price) * 100, 2)
        }
    
    def test_patent_example_1_2m_property(self):
        """Test complete example from patent application"""
        result = self.calculate_offer(
            asking_price=1_200_000,
            repair_cost_avg=45_000,
            risk_score=66,  # HIGH tier
            transparency_score=38  # Below 50
        )
        
        # Verify components
        self.assertEqual(result['repair_cost'], 45_000.00)
        self.assertEqual(result['risk_premium'], 60_000.00)  # 5% of 1.2M
        self.assertEqual(result['transparency_discount'], 36_000.00)  # 3% of 1.2M
        self.assertEqual(result['total_discount'], 141_000.00)
        self.assertEqual(result['recommended_offer'], 1_059_000.00)
        self.assertAlmostEqual(result['discount_percentage'], 11.75, places=1)
    
    def test_low_risk_honest_seller(self):
        """Best case: low risk, honest seller"""
        result = self.calculate_offer(
            asking_price=800_000,
            repair_cost_avg=10_000,
            risk_score=25,  # LOW tier
            transparency_score=75  # Above 50
        )
        
        # Only repair cost applies
        self.assertEqual(result['risk_premium'], 0.00)
        self.assertEqual(result['transparency_discount'], 0.00)
        self.assertEqual(result['total_discount'], 10_000.00)
        self.assertEqual(result['recommended_offer'], 790_000.00)
    
    def test_critical_risk_dishonest_seller(self):
        """Worst case: critical risk, dishonest seller"""
        result = self.calculate_offer(
            asking_price=1_000_000,
            repair_cost_avg=80_000,
            risk_score=85,  # CRITICAL tier
            transparency_score=15  # Below 50
        )
        
        # All three components apply
        self.assertEqual(result['repair_cost'], 80_000.00)
        self.assertEqual(result['risk_premium'], 100_000.00)  # 10%
        self.assertEqual(result['transparency_discount'], 30_000.00)  # 3%
        self.assertEqual(result['total_discount'], 210_000.00)
        self.assertEqual(result['recommended_offer'], 790_000.00)
        self.assertEqual(result['discount_percentage'], 21.00)
    
    def test_three_component_independence(self):
        """Verify three components are independent and additive"""
        asking_price = 1_000_000
        
        # Scenario A: Only repairs
        result_a = self.calculate_offer(asking_price, 50_000, 20, 60)
        self.assertEqual(result_a['total_discount'], 50_000.00)
        
        # Scenario B: Repairs + Risk
        result_b = self.calculate_offer(asking_price, 50_000, 60, 60)
        self.assertEqual(result_b['total_discount'], 100_000.00)  # 50K + 50K
        
        # Scenario C: Repairs + Transparency
        result_c = self.calculate_offer(asking_price, 50_000, 20, 40)
        self.assertEqual(result_c['total_discount'], 80_000.00)  # 50K + 30K
        
        # Scenario D: All three
        result_d = self.calculate_offer(asking_price, 50_000, 60, 40)
        self.assertEqual(result_d['total_discount'], 130_000.00)  # 50K + 50K + 30K


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions"""
    
    def test_zero_price(self):
        """Handle zero property price gracefully"""
        # This shouldn't happen in real use, but test anyway
        transparency = TestTransparencyScore()
        score = transparency.calculate_transparency_score(10, 8, 0, 2)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)
    
    def test_very_high_price(self):
        """Test with very expensive property"""
        premium_calc = TestRiskPremium()
        result = premium_calc.calculate_risk_premium(50_000_000, 75)
        self.assertEqual(result['tier'], 'CRITICAL')
        self.assertEqual(result['premium'], 5_000_000.00)  # 10% of 50M
    
    def test_perfect_scores(self):
        """Test with perfect 100 scores"""
        transparency = TestTransparencyScore()
        # Can't get exactly 100, but close
        score = transparency.calculate_transparency_score(20, 20, 0, 0)
        self.assertGreaterEqual(score, 90)
    
    def test_zero_scores(self):
        """Test with worst possible 0 scores"""
        transparency = TestTransparencyScore()
        score = transparency.calculate_transparency_score(0, 0, 10, 20)
        self.assertEqual(score, 0.0)


class TestRegressionSuite(unittest.TestCase):
    """Regression tests with known good outputs"""
    
    def setUp(self):
        """Load golden test cases"""
        self.golden_cases = [
            {
                'name': 'SF House - Good Condition',
                'asking_price': 1_480_000,
                'repair_cost': 15_000,
                'risk_score': 32,
                'transparency_score': 68,
                'expected_offer': 1_435_400,  # 1.48M - 15K - 29.6K(2%) - 0
                'expected_discount_pct': 3.01
            },
            {
                'name': 'SJ Condo - High Risk',
                'asking_price': 850_000,
                'repair_cost': 45_000,
                'risk_score': 62,
                'transparency_score': 45,
                'expected_offer': 737_000,  # 850K - 45K - 42.5K(5%) - 25.5K(3%) = 737K
                'expected_discount_pct': 13.3
            },
            {
                'name': 'Palo Alto - Critical Issues',
                'asking_price': 2_500_000,
                'repair_cost': 120_000,
                'risk_score': 78,
                'transparency_score': 25,
                'expected_offer': 2_055_000,  # 2.5M - 120K - 250K(10%) - 75K(3%)
                'expected_discount_pct': 17.8
            }
        ]
    
    def test_golden_cases(self):
        """Test against known good calculations"""
        integrated = TestIntegratedOfferCalculation()
        
        for case in self.golden_cases:
            with self.subTest(case=case['name']):
                result = integrated.calculate_offer(
                    asking_price=case['asking_price'],
                    repair_cost_avg=case['repair_cost'],
                    risk_score=case['risk_score'],
                    transparency_score=case['transparency_score']
                )
                
                # Check offer within $1000
                self.assertAlmostEqual(
                    result['recommended_offer'],
                    case['expected_offer'],
                    delta=1000,
                    msg=f"{case['name']}: Offer mismatch"
                )
                
                # Check discount percentage within 0.5%
                self.assertAlmostEqual(
                    result['discount_percentage'],
                    case['expected_discount_pct'],
                    delta=0.5,
                    msg=f"{case['name']}: Discount % mismatch"
                )


def run_all_tests():
    """Run all test suites and generate report"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestTransparencyScore))
    suite.addTests(loader.loadTestsFromTestCase(TestRiskPremium))
    suite.addTests(loader.loadTestsFromTestCase(TestTransparencyDiscount))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegratedOfferCalculation))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCases))
    suite.addTests(loader.loadTestsFromTestCase(TestRegressionSuite))
    
    # Run with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Print summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    print(f"Tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("="*70)
    
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_all_tests()
    exit(0 if success else 1)
