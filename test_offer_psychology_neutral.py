"""
test_offer_psychology_neutral.py — the recommended offer is what the PROPERTY and
report justify, identical for every buyer looking at the same house. Buyer
psychology (sentiment / time pressure / safety concern / budget / past trauma)
must never bend the recommended offer or appear as a "justified" line in THE MATH.

Regression lock for v5.89.250. Previously these terms swung the recommended offer
across a ~$50k range on one $900k property based on the buyer's stated feelings,
while rendering under "every dollar justified by something in this report."
Psychology now lives on the posture axis (the aggressive/conservative variants),
not inside the anchor.
"""
import unittest
from unittest.mock import MagicMock, patch
from offerwise_intelligence import OfferWiseIntelligence


class Pred:
    def __init__(self, lo, hi):
        self.estimated_cost_low = lo
        self.estimated_cost_high = hi
        self.estimated_cost_most_likely = (lo + hi) / 2


def _strategy(asking, profile):
    intel = OfferWiseIntelligence.__new__(OfferWiseIntelligence)
    rs = MagicMock()
    rs.overall_risk_score = 70
    rs.risk_tier = 'HIGH'
    rs.total_repair_cost_low = 30000
    rs.total_repair_cost_high = 60000
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
    bp.max_budget = asking * 1.3
    bp.repair_tolerance = 'Moderate'
    bc = MagicMock()
    bc.sentiment = profile.get('sentiment', 'neutral')
    bc.has_time_pressure = profile.get('time', False)
    bc.has_safety_concern = profile.get('safety', False)
    bc.has_budget_constraint = profile.get('budget', False)
    bc.has_past_trauma = profile.get('trauma', False)
    bc.primary_concerns = profile.get('primary_concerns', [])
    with patch.object(intel, '_calculate_confidence', return_value=0.8):
        return intel._generate_offer_strategy(
            asking, rs, cr, bp, bc, predicted_issues=[Pred(4000, 10000), Pred(3000, 8000)])


PROFILES = [
    {},                                                              # neutral
    {'sentiment': 'aggressive'},
    {'sentiment': 'aggressive', 'time': True},
    {'sentiment': 'conservative'},
    {'safety': True},
    {'budget': True, 'trauma': True},
    {'sentiment': 'conservative', 'safety': True, 'budget': True, 'trauma': True},
]


class TestOfferIsPropertyJustified(unittest.TestCase):
    def test_recommended_offer_identical_across_all_psychology(self):
        offers = {_strategy(900_000, p)['recommended_offer'] for p in PROFILES}
        self.assertEqual(len(offers), 1,
                         f"buyer psychology moved the recommended offer: {sorted(offers)}")

    def test_psychology_terms_are_zero_in_breakdown(self):
        for p in PROFILES:
            b = _strategy(900_000, p)['discount_breakdown']
            for key in ('buyer_sentiment', 'safety_buffer', 'trauma_buffer'):
                self.assertEqual(b[key], 0, f"{key} nonzero for profile {p}")

    def test_holds_across_price_points(self):
        for asking in (500_000, 1_500_000):
            offers = {_strategy(asking, p)['recommended_offer'] for p in PROFILES}
            self.assertEqual(len(offers), 1, f"psychology moved offer at ${asking:,}")

    def test_safety_no_longer_double_counts(self):
        # A stated safety concern on a high-risk property must not add a second
        # discount on top of the repair costs already in the repairs line.
        with_concern = _strategy(900_000, {'safety': True})['recommended_offer']
        without = _strategy(900_000, {})['recommended_offer']
        self.assertEqual(with_concern, without)


if __name__ == '__main__':
    unittest.main()
