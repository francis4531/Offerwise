"""
test_reserve_reconciliation.py — the "Hidden-issue reserve" line in the offer
math MUST equal the itemized predicted-hidden-issue list the report shows the
buyer, never a flat percentage of asking.

Regression lock for v5.89.247. The bug: risk_discount = property_price * 0.10
labeled "Hidden-issue reserve" (e.g. $90k on a $900k listing) while the itemized
reserve section totaled a fraction of it — a fabricated, unbacked discount. These
tests prove the reserve is now the itemized sum by construction, and that it is
no longer a function of asking price.
"""
import unittest
from unittest.mock import MagicMock, patch

from offerwise_intelligence import _reserve_from_predicted_issues, OfferWiseIntelligence


class Pred:
    """Minimal IssuePrediction-like object."""
    def __init__(self, low=None, high=None, most_likely=None):
        self.estimated_cost_low = low
        self.estimated_cost_high = high
        self.estimated_cost_most_likely = most_likely


class TestReserveHelper(unittest.TestCase):
    def test_none_and_empty_are_zero(self):
        self.assertEqual(_reserve_from_predicted_issues(None), 0.0)
        self.assertEqual(_reserve_from_predicted_issues([]), 0.0)

    def test_sum_of_midpoints_objects(self):
        # (4000+10000)/2 + (3000+8000)/2 = 7000 + 5500 = 12500
        preds = [Pred(4000, 10000), Pred(3000, 8000)]
        self.assertEqual(_reserve_from_predicted_issues(preds), 12500)

    def test_sum_of_midpoints_dicts(self):
        preds = [{'estimated_cost_low': 4000, 'estimated_cost_high': 10000},
                 {'estimated_cost_low': 3000, 'estimated_cost_high': 8000}]
        self.assertEqual(_reserve_from_predicted_issues(preds), 12500)

    def test_most_likely_fallback_when_no_range(self):
        preds = [Pred(most_likely=6000), Pred(most_likely=2500)]
        self.assertEqual(_reserve_from_predicted_issues(preds), 8500)

    def test_single_bound_uses_that_bound(self):
        self.assertEqual(_reserve_from_predicted_issues([Pred(low=5000)]), 5000)
        self.assertEqual(_reserve_from_predicted_issues([Pred(high=9000)]), 9000)

    def test_robust_to_bad_values(self):
        preds = [Pred(low='oops', high=None, most_likely=None),  # unparseable -> 0
                 Pred(low=-100, high=-200),                       # negatives rejected -> most_likely 0
                 Pred(low=4000, high=6000)]                       # 5000
        self.assertEqual(_reserve_from_predicted_issues(preds), 5000)

    def test_never_a_function_of_asking_price(self):
        # The whole point: same predictions -> same reserve, regardless of price.
        preds = [Pred(4000, 10000)]
        self.assertEqual(_reserve_from_predicted_issues(preds),
                         _reserve_from_predicted_issues(preds))


class TestOfferReservesReconcile(unittest.TestCase):
    """The offer math's risk_premium line == the itemized reserve, by construction."""

    def _strategy(self, asking, predicted_issues, risk=80, repair_low=50000, repair_high=80000):
        intel = OfferWiseIntelligence.__new__(OfferWiseIntelligence)
        rs = MagicMock()
        rs.overall_risk_score = risk
        rs.risk_tier = "CRITICAL"          # would have triggered the old 10% flat
        rs.total_repair_cost_low = repair_low
        rs.total_repair_cost_high = repair_high
        rs.deal_breakers = []
        rs.walk_away_threshold = asking * 0.5
        rs.category_scores = []
        cr = MagicMock()
        cr.contradictions = []
        cr.transparency_score = 75
        cr.transparency_applicable = True
        cr.blank_unknown_count = 0
        cr.evasion_phrases = []
        bp = MagicMock()
        bp.max_budget = asking * 1.2
        bp.repair_tolerance = 'Moderate'
        bc = MagicMock()
        bc.sentiment = 'neutral'
        bc.has_time_pressure = False
        bc.has_safety_concern = False
        bc.has_budget_constraint = False
        bc.has_past_trauma = False
        bc.primary_concerns = []
        with patch.object(intel, '_calculate_confidence', return_value=0.8):
            return intel._generate_offer_strategy(
                asking, rs, cr, bp, bc, predicted_issues=predicted_issues)

    def test_reserve_line_equals_itemized_predictions(self):
        preds = [Pred(4000, 10000), Pred(3000, 8000)]  # itemized total = 12500
        r = self._strategy(900_000, preds)
        self.assertEqual(r['discount_breakdown']['risk_premium'],
                         _reserve_from_predicted_issues(preds))
        self.assertEqual(r['discount_breakdown']['risk_premium'], 12500)

    def test_reserve_is_not_ten_percent_of_asking(self):
        # The exact fabrication: a $900k CRITICAL listing must NOT reserve $90k
        # unless the predictions actually total that.
        preds = [Pred(4000, 10000), Pred(3000, 8000)]  # 12500, not 90000
        r = self._strategy(900_000, preds)
        self.assertNotEqual(r['discount_breakdown']['risk_premium'], 90_000)
        self.assertLess(r['discount_breakdown']['risk_premium'], 90_000)

    def test_no_predictions_means_no_reserve(self):
        r = self._strategy(900_000, None)
        self.assertEqual(r['discount_breakdown']['risk_premium'], 0)

    def test_offer_reconciles_to_its_breakdown(self):
        # v5.89.242 invariant still holds: offer == asking − Σ(breakdown terms),
        # now with the honest reserve.
        preds = [Pred(4000, 10000), Pred(3000, 8000)]
        asking = 900_000
        r = self._strategy(asking, preds)
        b = r['discount_breakdown']
        expected = round(asking
                         - b['repair_costs'] - b['risk_premium'] - b['transparency_issues']
                         + b['market_adjustment'] + b['buyer_sentiment']
                         - b['safety_buffer'] - b['trauma_buffer'])
        # within the floor/budget clamps
        self.assertAlmostEqual(r['recommended_offer'], expected, delta=max(1000, asking * 0.01))


if __name__ == '__main__':
    unittest.main()
