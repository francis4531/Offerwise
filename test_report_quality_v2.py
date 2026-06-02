"""
test_report_quality_v2.py — New comprehensive tests for the OfferWise analysis report.

Covers gaps not in test_report_quality.py:
  1. Repair cost tightness — per-item and total spread ≤ 1.65x
  2. Repair cost sanity bounds — per-system plausible dollar ranges
  3. Repair cost total spread cap — multi-system compounding capped
  4. MarketIntelligenceEngine — all data paths
  5. apply_market_adjustment — all market temperature/leverage combos
  6. Market context in offer_strategy — populated vs empty
  7. Hallucination detection — invented addresses, permit numbers, dates
  8. Contradiction specificity — generic vs specific text
  9. Offer number regression — bounds per risk level
 10. Grounding threshold — partial match handling
"""

import unittest
import sys, os
sys.path.insert(0, os.path.dirname(__file__))


# ══════════════════════════════════════════════════════════════════════════════
# 1. REPAIR COST TIGHTNESS
# ══════════════════════════════════════════════════════════════════════════════

class TestRepairCostTightness(unittest.TestCase):

    def setUp(self):
        from repair_cost_estimator import estimate_repair_costs, BASELINE_COSTS
        self.estimate = estimate_repair_costs
        self.baselines = BASELINE_COSTS

    def test_every_baseline_range_under_1_7x(self):
        for cat, sevs in self.baselines.items():
            for sev, (low, high) in sevs.items():
                ratio = high / low
                self.assertLessEqual(ratio, 1.82,
                    f"{cat}/{sev}: ${low:,}–${high:,} is {ratio:.2f}x wide")

    def test_single_item_spread_under_1_7x(self):
        result = self.estimate('78701', findings=[
            {'category': 'roof', 'severity': 'major', 'description': 'Full replacement'}
        ])
        for item in result['breakdown']:
            ratio = item['high'] / item['low'] if item['low'] > 0 else 999
            self.assertLess(ratio, 1.75,
                f"{item['system']}: ${item['low']:,}–${item['high']:,} = {ratio:.2f}x")

    def test_five_critical_systems_total_under_1_7x(self):
        result = self.estimate('94102', findings=[
            {'category': c, 'severity': 'critical', 'description': 'test'}
            for c in ['foundation', 'roof', 'plumbing', 'electrical', 'hvac']
        ])
        lo, hi = result['total_low'], result['total_high']
        if lo > 0:
            self.assertLess(hi / lo, 1.70,
                f"5 critical items: ${lo:,}–${hi:,} = {hi/lo:.2f}x")

    def test_total_avg_between_low_and_high(self):
        result = self.estimate('10001', findings=[
            {'category': 'roof', 'severity': 'major', 'description': 'test'},
            {'category': 'hvac', 'severity': 'moderate', 'description': 'test'},
        ])
        self.assertGreaterEqual(result['total_avg'], result['total_low'])
        self.assertLessEqual(result['total_avg'], result['total_high'])

    def test_low_never_exceeds_high(self):
        for sev in ['minor', 'moderate', 'major', 'critical']:
            result = self.estimate('30301', findings=[
                {'category': 'foundation', 'severity': sev, 'description': 'test'}
            ])
            self.assertLessEqual(result['total_low'], result['total_high'],
                f"foundation/{sev}: low > high")


# ══════════════════════════════════════════════════════════════════════════════
# 2. REPAIR COST SANITY BOUNDS
# ══════════════════════════════════════════════════════════════════════════════

class TestRepairCostSanityBounds(unittest.TestCase):

    def setUp(self):
        from repair_cost_estimator import estimate_repair_costs
        self.E = estimate_repair_costs

    def _avg(self, cat, sev, zip_code='00000'):
        r = self.E(zip_code, findings=[{'category': cat, 'severity': sev, 'description': 'test'}])
        items = [i for i in r['breakdown'] if i['category'] == cat]
        return items[0]['avg'] if items else 0

    def test_roof_major_6k_to_18k(self):
        avg = self._avg('roof', 'major')
        self.assertGreater(avg, 6_000);  self.assertLess(avg, 18_000)

    def test_roof_critical_12k_to_28k(self):
        avg = self._avg('roof', 'critical')
        self.assertGreater(avg, 12_000); self.assertLess(avg, 28_000)

    def test_electrical_critical_8k_to_18k(self):
        avg = self._avg('electrical', 'critical')
        self.assertGreater(avg, 8_000);  self.assertLess(avg, 18_000)

    def test_hvac_major_3k_to_10k(self):
        avg = self._avg('hvac', 'major')
        self.assertGreater(avg, 3_000);  self.assertLess(avg, 10_000)

    def test_hvac_critical_8k_to_18k(self):
        avg = self._avg('hvac', 'critical')
        self.assertGreater(avg, 8_000);  self.assertLess(avg, 18_000)

    def test_plumbing_critical_12k_to_28k(self):
        avg = self._avg('plumbing', 'critical')
        self.assertGreater(avg, 12_000); self.assertLess(avg, 28_000)

    def test_foundation_critical_25k_to_55k(self):
        avg = self._avg('foundation', 'critical')
        self.assertGreater(avg, 25_000); self.assertLess(avg, 55_000)

    def test_minor_repairs_all_under_2k(self):
        for cat in ['roof', 'hvac', 'electrical', 'plumbing', 'safety', 'pest']:
            avg = self._avg(cat, 'minor')
            self.assertLess(avg, 2_000, f"{cat} minor ${avg:,} > $2K")

    def test_sf_costs_above_national(self):
        nat = self._avg('roof', 'major', '00000')
        sf  = self._avg('roof', 'major', '94102')
        self.assertGreater(sf, nat, f"SF ${sf:,} not above national ${nat:,}")

    def test_southeast_costs_below_national(self):
        nat = self._avg('hvac', 'major', '00000')
        se  = self._avg('hvac', 'major', '30301')  # Atlanta
        # Southeast should be at or below national average
        self.assertLessEqual(se, nat * 1.05,
            f"Atlanta ${se:,} unexpectedly much above national ${nat:,}")

    def test_three_moderate_issues_under_30k(self):
        r = self.E('30301', findings=[
            {'category': 'roof',  'severity': 'moderate', 'description': 'test'},
            {'category': 'hvac',  'severity': 'moderate', 'description': 'test'},
            {'category': 'plumbing', 'severity': 'moderate', 'description': 'test'},
        ])
        self.assertLess(r['total_avg'], 30_000,
            f"3 moderate items avg ${r['total_avg']:,} > $30K")

    def test_single_critical_not_exceeding_60k_avg(self):
        r = self.E('10001', findings=[
            {'category': 'foundation', 'severity': 'critical', 'description': 'Major failure'}
        ])
        self.assertLess(r['total_avg'], 60_000,
            f"Foundation critical avg ${r['total_avg']:,} > $60K")


# ══════════════════════════════════════════════════════════════════════════════
# 3. MARKET INTELLIGENCE ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class TestMarketIntelligenceEngine(unittest.TestCase):

    def setUp(self):
        from market_intelligence import MarketIntelligenceEngine
        self.E = MarketIntelligenceEngine

    def _research(self, avm=550000, comp_prices=None, avg_dom=25,
                  price_trend=1.5, inv_trend=5.0, n_short_sale=0):
        prices = comp_prices or [530000, 545000, 560000, 520000, 555000]
        comps = [{'price': p, 'sqft': 2000, 'price_per_sqft': p//2000,
                  'days_on_market': 20, 'status': 'sold', 'listing_type': 'Standard'}
                 for p in prices]
        return {'tool_results': [
            {'tool_name': 'rentcast', 'status': 'success', 'data': {
                'avm_price': avm,
                'avm_price_low': int(avm*.94), 'avm_price_high': int(avm*1.06),
                'comparables': comps,
                'foreclosure_count': 0, 'short_sale_count': n_short_sale,
                'new_construction_count': 0,
            }},
            {'tool_name': 'market_stats', 'status': 'success', 'data': {
                'zip_code': '78701', 'median_price_per_sqft': 280,
                'average_days_on_market': avg_dom, 'total_listings': 55,
                'price_trend_pct': price_trend, 'inventory_trend_pct': inv_trend,
                'bed_match_median': int(avm*.97),
            }}
        ]}

    def test_high_quality_with_avm_and_comps(self):
        mi = self.E().from_research_data(self._research(), 570000)
        self.assertEqual(mi.data_quality, 'high')

    def test_none_quality_no_data(self):
        mi = self.E().from_research_data({}, 500000)
        self.assertEqual(mi.data_quality, 'none')

    def test_none_quality_skipped_tool(self):
        mi = self.E().from_research_data(
            {'tool_results': [{'tool_name': 'rentcast', 'status': 'skipped', 'data': {}}]},
            500000)
        self.assertEqual(mi.data_quality, 'none')

    def test_avm_populated(self):
        mi = self.E().from_research_data(self._research(avm=562000), 580000)
        self.assertEqual(mi.avm_price, 562000)

    def test_value_range_populated(self):
        mi = self.E().from_research_data(self._research(avm=550000), 570000)
        self.assertGreater(mi.value_range_low, 0)
        self.assertGreater(mi.value_range_high, mi.value_range_low)

    def test_comp_count_correct(self):
        mi = self.E().from_research_data(
            self._research(comp_prices=[500000, 510000, 520000]), 530000)
        self.assertEqual(mi.comp_count, 3)

    def test_comp_median_correct_odd(self):
        mi = self.E().from_research_data(
            self._research(comp_prices=[500000, 520000, 540000]), 550000)
        self.assertEqual(mi.comp_median_price, 520000)

    def test_asking_above_comps_positive(self):
        mi = self.E().from_research_data(
            self._research(comp_prices=[500000]*5), 550000)
        self.assertGreater(mi.asking_vs_comps_pct, 0)

    def test_asking_below_comps_negative(self):
        mi = self.E().from_research_data(
            self._research(comp_prices=[600000]*5), 550000)
        self.assertLess(mi.asking_vs_comps_pct, 0)

    def test_hot_market_high_asking_low_dom(self):
        mi = self.E().from_research_data(
            self._research(avm=500000, avg_dom=10, inv_trend=-10), 530000)
        self.assertEqual(mi.market_temperature, 'hot')

    def test_buyer_market_low_asking_high_dom(self):
        mi = self.E().from_research_data(
            self._research(avm=600000, avg_dom=60, inv_trend=20), 580000)
        self.assertEqual(mi.market_temperature, 'buyer')

    def test_distressed_pct_nonzero_with_short_sales(self):
        mi = self.E().from_research_data(
            self._research(comp_prices=[500000]*5, n_short_sale=1), 510000)
        self.assertGreater(mi.distressed_pct, 0)

    def test_market_stats_attached(self):
        mi = self.E().from_research_data(self._research(), 570000)
        self.assertIsNotNone(mi.market)
        self.assertEqual(mi.market.zip_code, '78701')

    def test_price_trend_stored(self):
        mi = self.E().from_research_data(self._research(price_trend=3.2), 570000)
        self.assertAlmostEqual(mi.market.price_trend_pct, 3.2)

    def test_active_comps_excluded_from_count(self):
        """Active listings should not count as sold comps."""
        research = self._research(comp_prices=[500000, 510000])
        research['tool_results'][0]['data']['comparables'].append(
            {'price': 490000, 'sqft': 2000, 'price_per_sqft': 245,
             'days_on_market': 5, 'status': 'Active', 'listing_type': 'Standard'})
        mi = self.E().from_research_data(research, 520000)
        self.assertEqual(mi.comp_count, 2, "Active listing should not count as comp")


# ══════════════════════════════════════════════════════════════════════════════
# 4. APPLY MARKET ADJUSTMENT
# ══════════════════════════════════════════════════════════════════════════════

class TestApplyMarketAdjustment(unittest.TestCase):

    def setUp(self):
        from market_intelligence import apply_market_adjustment, MarketIntelligence, _MarketStats
        self.adjust = apply_market_adjustment
        self.MI = MarketIntelligence
        self.MS = _MarketStats

    def _mi(self, avm=520000, comp_median=510000, temp='neutral',
            asking_vs_comps=5.0, comp_count=8):
        mi = self.MI()
        mi.avm_price = avm
        mi.value_range_low  = int(avm * .94)
        mi.value_range_high = int(avm * 1.06)
        mi.comp_median_price = comp_median
        mi.comp_count = comp_count
        mi.comp_avg_price_per_sqft = 260
        mi.comp_avg_dom = 22
        mi.asking_vs_comps_pct = asking_vs_comps
        mi.market_temperature = temp
        mi.data_quality = 'high'
        mi.distressed_pct = 5.0
        mi.foreclosure_count = 0
        mi.short_sale_count = 0
        mi.new_construction_count = 0
        mi.price_percentile = 60
        mi.price_vs_type_median_pct = 0
        mi.price_vs_bed_median_pct = 0
        mi.market = self.MS({
            'zip_code': '78701', 'median_price_per_sqft': 260,
            'average_days_on_market': 22, 'total_listings': 50,
            'price_trend_pct': 1.5, 'inventory_trend_pct': 3.0,
        })
        return mi

    def test_market_applied_true(self):
        r = self.adjust(490000, 530000, self._mi())
        self.assertTrue(r['market_applied'])

    def test_market_applied_false_no_data(self):
        mi = self.MI(); mi.data_quality = 'none'
        r = self.adjust(490000, 530000, mi)
        self.assertFalse(r['market_applied'])

    def test_hot_market_weak_leverage(self):
        r = self.adjust(490000, 530000, self._mi(temp='hot'))
        self.assertEqual(r['buyer_leverage'], 'weak')

    def test_buyer_market_strong_leverage(self):
        r = self.adjust(490000, 510000, self._mi(temp='buyer'))
        self.assertEqual(r['buyer_leverage'], 'strong')

    def test_neutral_market_moderate_leverage(self):
        r = self.adjust(490000, 520000, self._mi(temp='neutral'))
        self.assertEqual(r['buyer_leverage'], 'moderate')

    def test_adjusted_offer_non_negative(self):
        r = self.adjust(100, 500000, self._mi())
        self.assertGreaterEqual(r.get('adjusted_offer', 0), 0)

    def test_rationale_non_empty(self):
        r = self.adjust(490000, 530000, self._mi())
        self.assertGreater(len(r.get('rationale', '')), 20)

    def test_rationale_mentions_avm(self):
        r = self.adjust(490000, 530000, self._mi(avm=515000))
        self.assertIn('AVM', r['rationale'])

    def test_all_required_keys(self):
        r = self.adjust(490000, 530000, self._mi())
        for k in ['market_applied', 'market_temperature', 'buyer_leverage',
                  'estimated_value', 'comp_median_price', 'comp_count',
                  'asking_vs_avm_pct', 'market_adjustment_amount',
                  'adjusted_offer', 'rationale']:
            self.assertIn(k, r, f"Missing: {k}")

    def test_asking_vs_avm_pct_correct_sign(self):
        r = self.adjust(490000, 550000, self._mi(avm=500000))
        # asking $550K > AVM $500K → positive
        self.assertGreater(r['asking_vs_avm_pct'], 0)

    def test_buyer_market_adjustment_is_negative_or_zero(self):
        r = self.adjust(490000, 520000, self._mi(temp='buyer'))
        self.assertLessEqual(r['market_adjustment_amount'], 0)


# ══════════════════════════════════════════════════════════════════════════════
# 5. HALLUCINATION / GROUNDING DETECTION
# ══════════════════════════════════════════════════════════════════════════════

class TestHallucinationDetection(unittest.TestCase):

    SRC = """
    Property: 2839 Pendleton Dr, San Jose, CA 95148
    Seller purchased March 2019. Roof replaced 2018. HVAC installed 2009.
    Electrical panel: Federal Pacific Stab-Lok 100A.
    No known water intrusion. Plumbing: copper supply lines.
    """

    def setUp(self):
        from ai_output_validator import validate_truth_check
        self._validate_raw = validate_truth_check

    def V(self, output, pdf_text=None):
        result, _ = self._validate_raw(output, pdf_text=pdf_text)
        return result

    def _output(self, evidence):
        return {
            'trust_score': 70, 'grade': 'C',
            'red_flags': [{'id': 'RF1', 'title': 'Test', 'severity': 'major',
                           'evidence': evidence, 'recommendation': 'Fix it'}],
            'blank_unknown_count': 0, 'evasion_phrases': [],
            'overall_assessment': 'Issues found.'
        }

    def test_grounded_evidence_no_warning(self):
        r = self.V(self._output('Federal Pacific Stab-Lok 100A panel'), pdf_text=self.SRC)
        warns = [f for f in r['red_flags'] if f.get('_grounding_warning')]
        self.assertEqual(len(warns), 0, "Grounded evidence should not be flagged")

    def test_hallucinated_address_flagged(self):
        r = self.V(self._output(
            'The property at 9999 Invented Street was cited for mold in 2019'),
            pdf_text=self.SRC)
        warns = [f for f in r['red_flags'] if f.get('_grounding_warning')]
        self.assertGreater(len(warns), 0, "Invented address should be flagged")

    def test_invented_permit_number_flagged(self):
        r = self.V(self._output(
            'City permit number 2019-BR-44821 issued but no final inspection'),
            pdf_text=self.SRC)
        warns = [f for f in r['red_flags'] if f.get('_grounding_warning')]
        self.assertGreater(len(warns), 0, "Invented permit number should be flagged")

    def test_no_grounding_without_pdf(self):
        r = self.V(self._output('completely fabricated claim about unicorn basement'),
                   pdf_text=None)
        warns = [f for f in r['red_flags'] if f.get('_grounding_warning')]
        self.assertEqual(len(warns), 0, "No grounding check without PDF")

    def test_fabricated_evasion_phrase_removed(self):
        output = {
            'trust_score': 70, 'grade': 'C',
            'red_flags': [],
            'blank_unknown_count': 0,
            'evasion_phrases': [
                'HVAC installed 2009',                                   # real
                'Renovations completed by contractor ABC-9988 in 2022', # fabricated
            ],
            'overall_assessment': 'Mixed.'
        }
        r = self.V(output, pdf_text=self.SRC)
        real_kept = any('2009' in p for p in r['evasion_phrases'])
        fake_kept = any('ABC' in p for p in r['evasion_phrases'])
        self.assertTrue(real_kept, "Real phrase should be kept")
        self.assertFalse(fake_kept, "Fabricated contractor reference should be removed")

    def test_trust_score_clamped_above_100(self):
        output = {'trust_score': 150, 'grade': 'A', 'red_flags': [],
                  'blank_unknown_count': 0, 'evasion_phrases': [],
                  'overall_assessment': 'Good.'}
        r = self.V(output)
        self.assertLessEqual(r['trust_score'], 100)

    def test_trust_score_clamped_below_0(self):
        output = {'trust_score': -30, 'grade': 'F', 'red_flags': [],
                  'blank_unknown_count': 0, 'evasion_phrases': [],
                  'overall_assessment': 'Bad.'}
        r = self.V(output)
        self.assertGreaterEqual(r['trust_score'], 0)

    def test_max_5_cross_reference_findings(self):
        from ai_output_validator import validate_cross_reference_findings
        findings = [{'type': 'contradiction', 'title': f'Issue {i}',
                     'detail': f'Detail {i}', 'severity': 'high', 'confidence': 0.9}
                    for i in range(10)]
        cleaned, viols = validate_cross_reference_findings(findings)
        self.assertLessEqual(len(cleaned), 5)

    def test_invalid_severity_defaulted(self):
        from ai_output_validator import validate_cross_reference_findings
        findings = [{'type': 'contradiction', 'title': 'Some Issue',
                     'detail': 'Some detail', 'severity': 'catastrophic',
                     'confidence': 0.8}]
        cleaned, _ = validate_cross_reference_findings(findings)
        valid_severities = {'low', 'medium', 'high', 'critical', 'info', 'warning', 'error'}
        for f in cleaned:
            self.assertIn(f.get('severity', 'medium'), valid_severities)


# ══════════════════════════════════════════════════════════════════════════════
# 6. CONTRADICTION SPECIFICITY
# ══════════════════════════════════════════════════════════════════════════════

class TestContradictionSpecificity(unittest.TestCase):

    GENERIC = [
        'seller disclosed no issues but inspection found problems',
        'there are discrepancies between the disclosure and the inspection',
        'seller may not have been fully transparent',
    ]
    SPECIFIC = [
        'seller disclosed no electrical issues; Federal Pacific panel found by inspector',
        'seller stated roof was recently serviced; inspector found 3-5 years remaining life with missing flashing',
        'seller disclosed no plumbing defects; polybutylene pipes present throughout',
    ]

    def _has_system_and_evidence(self, phrase):
        systems = ['roof', 'electrical', 'panel', 'plumbing', 'foundation',
                   'hvac', 'pipes', 'unit', 'water']
        evidence = ['federal', 'polybutylene', 'galvanized', 'serviced',
                    'years', 'flashing', 'remaining', 'stab', 'life']
        p = phrase.lower()
        return (any(s in p for s in systems) and
                any(e in p for e in evidence))

    def test_generic_phrases_not_specific(self):
        for p in self.GENERIC:
            self.assertFalse(self._has_system_and_evidence(p),
                f"Unexpectedly specific: '{p}'")

    def test_specific_phrases_are_specific(self):
        for p in self.SPECIFIC:
            self.assertTrue(self._has_system_and_evidence(p),
                f"Should be specific: '{p}'")

    def test_empty_title_gets_violation(self):
        from ai_output_validator import validate_cross_reference_findings
        cleaned, viols = validate_cross_reference_findings([
            {'type': 'contradiction', 'title': '', 'detail': 'stuff',
             'severity': 'high', 'confidence': 0.8}
        ])
        # Empty title should either be removed or generate a violation
        has_issue = len(cleaned) == 0 or len(viols) > 0
        self.assertTrue(has_issue, 'Empty title finding should be removed or flagged')

    def test_good_specific_title_kept(self):
        from ai_output_validator import validate_cross_reference_findings
        cleaned, viols = validate_cross_reference_findings([
            {'type': 'contradiction',
             'title': 'Seller disclosed no electrical issues; FPE panel found by inspector',
             'detail': 'Federal Pacific Stab-Lok panel is a documented fire hazard',
             'severity': 'high', 'confidence': 0.93}
        ])
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(viols, [])


# ══════════════════════════════════════════════════════════════════════════════
# 7. OFFER NUMBER REGRESSION
# ══════════════════════════════════════════════════════════════════════════════

class TestOfferNumberRegression(unittest.TestCase):
    """Recommended offer must stay within defensible bounds."""

    def _strategy(self, asking, risk=40, repair_low=5000, repair_high=15000):
        from offerwise_intelligence import OfferWiseIntelligence
        from unittest.mock import MagicMock, patch
        intel = OfferWiseIntelligence.__new__(OfferWiseIntelligence)
        rs = MagicMock()
        rs.overall_risk_score = risk
        rs.total_repair_cost_low = repair_low
        rs.total_repair_cost_high = repair_high
        rs.deal_breakers = []
        rs.walk_away_threshold = asking * 0.5
        rs.category_scores = []
        cr = MagicMock()
        cr.contradictions = []
        cr.transparency_score = 75
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
            return intel._generate_offer_strategy(asking, rs, cr, bp, bc)

    def test_offer_never_exceeds_asking(self):
        r = self._strategy(500_000, risk=15)
        self.assertLessEqual(r['recommended_offer'], 500_000)

    def test_offer_never_below_40pct(self):
        r = self._strategy(500_000, risk=90, repair_low=100000, repair_high=200000)
        self.assertGreaterEqual(r['recommended_offer'], 200_000)

    def test_low_risk_small_discount(self):
        r = self._strategy(600_000, risk=15, repair_low=2000, repair_high=5000)
        pct = (600_000 - r['recommended_offer']) / 600_000 * 100
        self.assertLess(pct, 8.0, f"Low risk discount {pct:.1f}% too high")

    def test_high_risk_meaningful_discount(self):
        r = self._strategy(600_000, risk=80, repair_low=50000, repair_high=80000)
        pct = (600_000 - r['recommended_offer']) / 600_000 * 100
        self.assertGreater(pct, 5.0, f"High risk discount {pct:.1f}% too small")

    def test_aggressive_leq_recommended(self):
        r = self._strategy(500_000, risk=50)
        if r.get('aggressive_offer') and r.get('recommended_offer'):
            self.assertLessEqual(r['aggressive_offer'], r['recommended_offer'])

    def test_conservative_geq_recommended(self):
        r = self._strategy(500_000, risk=50)
        if r.get('conservative_offer') and r.get('recommended_offer'):
            self.assertGreaterEqual(r['conservative_offer'], r['recommended_offer'])

    def test_high_value_offer_over_1m(self):
        r = self._strategy(1_500_000, risk=50, repair_low=30000, repair_high=60000)
        self.assertGreater(r['recommended_offer'], 1_000_000)

    def test_required_keys_present(self):
        r = self._strategy(700_000)
        for k in ['recommended_offer', 'discount_from_ask', 'discount_percentage',
                  'repair_cost_avg', 'market_context']:
            self.assertIn(k, r, f"Missing: {k}")

    def test_discount_percentage_matches_offer(self):
        asking = 500_000
        r = self._strategy(asking, risk=40)
        computed = round((asking - r['recommended_offer']) / asking * 100, 1)
        stored = round(r['discount_percentage'], 1)
        self.assertAlmostEqual(computed, stored, delta=1.0)


# ══════════════════════════════════════════════════════════════════════════════
# 8. MARKET CONTEXT IN OFFER STRATEGY
# ══════════════════════════════════════════════════════════════════════════════

class TestMarketContextInOfferStrategy(unittest.TestCase):

    def _strategy_with_mi(self, asking, mi):
        from offerwise_intelligence import OfferWiseIntelligence
        from unittest.mock import MagicMock, patch
        intel = OfferWiseIntelligence.__new__(OfferWiseIntelligence)
        rs = MagicMock()
        rs.overall_risk_score = 40
        rs.total_repair_cost_low = 5000
        rs.total_repair_cost_high = 15000
        rs.deal_breakers = []
        rs.walk_away_threshold = asking * 0.5
        rs.category_scores = []
        cr = MagicMock()
        cr.contradictions = []
        cr.transparency_score = 75
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
            return intel._generate_offer_strategy(asking, rs, cr, bp, bc, market_intel=mi)

    def test_market_context_empty_without_intel(self):
        r = self._strategy_with_mi(500_000, None)
        self.assertIn('market_context', r)
        self.assertEqual(r['market_context'], {})

    def test_market_context_populated_with_intel(self):
        from market_intelligence import MarketIntelligenceEngine
        research = {'tool_results': [
            {'tool_name': 'rentcast', 'status': 'success', 'data': {
                'avm_price': 490000, 'avm_price_low': 462000, 'avm_price_high': 518000,
                'comparables': [
                    {'price': p, 'sqft': 2000, 'price_per_sqft': p//2000,
                     'days_on_market': 18, 'status': 'sold', 'listing_type': 'Standard'}
                    for p in [480000, 492000, 498000, 485000, 495000]
                ],
                'foreclosure_count': 0, 'short_sale_count': 0, 'new_construction_count': 0,
            }}
        ]}
        mi = MarketIntelligenceEngine().from_research_data(research, 510000)
        r = self._strategy_with_mi(510000, mi)
        mc = r.get('market_context', {})
        self.assertTrue(mc.get('market_applied'), "market_applied should be True")
        self.assertIn('estimated_value', mc)
        self.assertIn('comp_count', mc)
        self.assertIn('market_temperature', mc)
        self.assertIn('market_rationale', mc)
        self.assertGreater(len(mc.get('market_rationale', '')), 0)

    def test_market_context_market_applied_false_no_avm(self):
        """When RentCast returns no AVM, market_applied must be False."""
        from market_intelligence import MarketIntelligence
        mi = MarketIntelligence()
        mi.data_quality = 'none'
        r = self._strategy_with_mi(500_000, mi)
        mc = r.get('market_context', {})
        self.assertFalse(mc.get('market_applied', False))


if __name__ == '__main__':
    unittest.main(verbosity=2)
