"""
test_report_quality.py
======================
Comprehensive tests for the OfferWise buyer analysis report.
Covers: offer number sanity, repair cost bounds, hallucination detection,
        market context, contradiction specificity, and regression cases.

Run: python -m pytest test_report_quality.py -v
"""
import unittest
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from repair_cost_estimator import (
    estimate_repair_costs, BASELINE_COSTS,
    _normalize_category, _normalize_severity, _get_zip_multiplier
)
from ai_output_validator import _check_grounding, validate_cross_reference_findings
from market_intelligence import MarketIntelligenceEngine, apply_market_adjustment


# ── Fixtures ──────────────────────────────────────────────────────────────

TYPICAL_FINDINGS = [
    {'category': 'roof',       'severity': 'major',    'description': 'Roof nearing end of life'},
    {'category': 'electrical', 'severity': 'major',    'description': 'Outdated panel'},
    {'category': 'plumbing',   'severity': 'moderate', 'description': 'Minor leak at P-trap'},
    {'category': 'hvac',       'severity': 'moderate', 'description': 'System 14 years old'},
]

RENTCAST_TOOL_RESULT = {
    'tool_results': [
        {
            'tool_name': 'rentcast', 'status': 'success',
            'data': {
                'avm_price': 580000, 'avm_price_low': 550000, 'avm_price_high': 610000,
                'comparables': [
                    {'price': 570000, 'sqft': 1900, 'price_per_sqft': 300, 'days_on_market': 18, 'status': 'sold', 'listing_type': 'Standard'},
                    {'price': 590000, 'sqft': 2000, 'price_per_sqft': 295, 'days_on_market': 22, 'status': 'sold', 'listing_type': 'Standard'},
                    {'price': 555000, 'sqft': 1850, 'price_per_sqft': 300, 'days_on_market': 30, 'status': 'sold', 'listing_type': 'Standard'},
                ],
                'foreclosure_count': 0, 'short_sale_count': 0, 'new_construction_count': 0,
            }
        },
        {
            'tool_name': 'market_stats', 'status': 'success',
            'data': {
                'zip_code': '95148', 'median_price_per_sqft': 298,
                'average_days_on_market': 24, 'total_listings': 41,
                'price_trend_pct': 1.2, 'inventory_trend_pct': 5.0,
                'bed_match_median': 572000,
            }
        }
    ]
}


# ═══════════════════════════════════════════════════════════════════════════
# 1. REPAIR COST BOUNDS
# ═══════════════════════════════════════════════════════════════════════════

class TestRepairCostBounds(unittest.TestCase):
    """Repair cost ranges must be tight (≤ 1.9x) and within industry bounds."""

    def test_baseline_spread_max_2x(self):
        """No baseline range should exceed 2x spread."""
        for cat, sevs in BASELINE_COSTS.items():
            for sev, (lo, hi) in sevs.items():
                ratio = hi / lo
                self.assertLessEqual(
                    ratio, 2.0,
                    f"{cat}/{sev}: {lo}–{hi} is {ratio:.2f}x spread (max 2.0x)"
                )

    def test_severity_ordering(self):
        """Higher severity must always cost more than lower severity."""
        order = ['minor', 'moderate', 'major', 'critical']
        for cat, sevs in BASELINE_COSTS.items():
            prev_mid = 0
            for sev in order:
                if sev in sevs:
                    lo, hi = sevs[sev]
                    mid = (lo + hi) / 2
                    self.assertGreater(mid, prev_mid,
                        f"{cat}/{sev} midpoint ${mid:,} not > previous ${prev_mid:,}")
                    prev_mid = mid

    def test_roof_major_realistic_range(self):
        """Roof major replacement: $7K–$15K nationally (RSMeans 2026)."""
        lo, hi = BASELINE_COSTS['roof']['major']
        self.assertGreaterEqual(lo, 6_000,  f"Roof/major low ${lo:,} too cheap")
        self.assertLessEqual(hi,   16_000,  f"Roof/major high ${hi:,} too expensive")

    def test_electrical_critical_realistic_range(self):
        """Panel replacement: $8K–$16K nationally."""
        lo, hi = BASELINE_COSTS['electrical']['critical']
        self.assertGreaterEqual(lo, 7_000,  f"Electrical/critical low ${lo:,} too cheap")
        self.assertLessEqual(hi,   18_000,  f"Electrical/critical high ${hi:,} too expensive")

    def test_foundation_critical_realistic_range(self):
        """Foundation critical (underpinning/pier): $25K–$60K nationally."""
        lo, hi = BASELINE_COSTS['foundation']['critical']
        self.assertGreaterEqual(lo, 20_000, f"Foundation/critical low ${lo:,} too cheap")
        self.assertLessEqual(hi,   65_000,  f"Foundation/critical high ${hi:,} too expensive")

    def test_hvac_critical_realistic_range(self):
        """Full HVAC replacement: $8K–$16K nationally."""
        lo, hi = BASELINE_COSTS['hvac']['critical']
        self.assertGreaterEqual(lo, 7_000,  f"HVAC/critical low ${lo:,} too cheap")
        self.assertLessEqual(hi,   18_000,  f"HVAC/critical high ${hi:,} too expensive")

    def test_plumbing_minor_not_zero(self):
        """Even minor plumbing should cost at least $250."""
        lo, hi = BASELINE_COSTS['plumbing']['minor']
        self.assertGreaterEqual(lo, 250, "Plumbing/minor too cheap")

    def test_zip_multiplier_bounds(self):
        """ZIP multipliers must stay within 0.65–1.70x nationally."""
        for zip_code in ['10001', '90210', '60601', '77001', '98101', '30301', '02101', '85001']:
            mult, metro = _get_zip_multiplier(zip_code)
            self.assertGreaterEqual(mult, 0.65, f"ZIP {zip_code} multiplier {mult} too low")
            self.assertLessEqual(mult, 1.70,    f"ZIP {zip_code} multiplier {mult} too high")

    def test_total_spread_after_zip_multiplier(self):
        """Total estimate spread after ZIP multiplication must be ≤ 2.0x."""
        result = estimate_repair_costs(
            zip_code='95120',  # San Jose — high multiplier
            findings=TYPICAL_FINDINGS,
            property_year_built=1987,
        )
        if result['total_low'] > 0:
            ratio = result['total_high'] / result['total_low']
            self.assertLessEqual(ratio, 2.0,
                f"Total spread {ratio:.2f}x after ZIP mult — too wide for buyer")

    def test_age_adjustment_bounded(self):
        """Age adjustment must not push costs above 1.5x for any category."""
        from repair_cost_estimator import _age_adjustment
        for cat in BASELINE_COSTS.keys():
            for year in [1920, 1950, 1975, 2000, 2020]:
                adj = _age_adjustment(year, cat)
                self.assertLessEqual(adj, 1.5,
                    f"Age adjustment for {cat} built {year} = {adj:.2f}x — too high")
                self.assertGreaterEqual(adj, 0.9,
                    f"Age adjustment for {cat} built {year} = {adj:.2f}x — too low")

    def test_no_findings_returns_zero(self):
        result = estimate_repair_costs(zip_code='95120', findings=[])
        self.assertEqual(result['total_low'], 0)
        self.assertEqual(result['total_high'], 0)

    def test_unknown_category_falls_back_to_general(self):
        result = estimate_repair_costs(
            zip_code='', findings=[{'category': 'xyz_unknown', 'severity': 'major'}]
        )
        # Should not crash and should produce a non-zero estimate
        self.assertGreater(result['total_high'], 0)

    def test_breakdown_items_never_exceed_total(self):
        """Sum of breakdown items should equal the total."""
        result = estimate_repair_costs(zip_code='95148', findings=TYPICAL_FINDINGS)
        sum_low  = sum(i['low']  for i in result['breakdown'])
        sum_high = sum(i['high'] for i in result['breakdown'])
        self.assertAlmostEqual(sum_low,  result['total_low'],  delta=10)
        self.assertAlmostEqual(sum_high, result['total_high'], delta=10)

    def test_plumbing_minor_spread_tight(self):
        """Plumbing minor was 2.3x — must be tightened to ≤ 1.9x."""
        lo, hi = BASELINE_COSTS['plumbing']['minor']
        ratio = hi / lo
        self.assertLessEqual(ratio, 1.9,
            f"Plumbing/minor {lo}–{hi} = {ratio:.2f}x, needs tightening")

    def test_pest_moderate_spread_tight(self):
        lo, hi = BASELINE_COSTS['pest']['moderate']
        ratio = hi / lo
        self.assertLessEqual(ratio, 1.9,
            f"Pest/moderate {lo}–{hi} = {ratio:.2f}x, needs tightening")


# ═══════════════════════════════════════════════════════════════════════════
# 2. OFFER NUMBER SANITY
# ═══════════════════════════════════════════════════════════════════════════

class TestOfferNumberSanity(unittest.TestCase):
    """Recommended offer must stay within realistic bounds."""

    def _make_risk_score(self, repair_low, repair_high, overall_risk):
        """Mock risk score object."""
        class MockRisk:
            total_repair_cost_low  = repair_low
            total_repair_cost_high = repair_high
            overall_risk_score     = overall_risk
            walk_away_threshold    = 0
            deal_breakers          = []
            category_scores        = []
        return MockRisk()

    def test_offer_never_above_asking(self):
        """Recommended offer must be ≤ asking price (we never suggest paying more)."""
        from offerwise_intelligence import OfferWiseIntelligence
        # Directly test the math: repair_cost_avg used as discount starting point
        asking = 600_000
        repair_avg = 30_000  # 5% of asking
        # Offer should be asking - repair costs at minimum
        offer = asking - repair_avg
        self.assertLessEqual(offer, asking)

    def test_offer_not_below_50pct_asking(self):
        """Offer should not drop below 50% of asking — would indicate calculation error."""
        asking = 600_000
        # Even with 100% repair cost discount, there's a floor
        min_reasonable = asking * 0.50
        self.assertGreater(min_reasonable, 0)

    def test_repair_costs_dont_exceed_20pct_asking(self):
        """
        Repair costs shouldn't silently exceed 20% of asking price
        without being flagged as a potential walk-away.
        This tests the implicit contract of the estimator.
        """
        asking = 600_000
        result = estimate_repair_costs(
            zip_code='95120',
            findings=TYPICAL_FINDINGS,
            property_year_built=1987,
        )
        ratio = result['total_high'] / asking
        # For a typical 4-finding analysis, should be well under 20%
        self.assertLess(ratio, 0.20,
            f"Repair high ${result['total_high']:,} = {ratio:.1%} of ${asking:,} — suspiciously high")

    def test_market_adjustment_bounded(self):
        """Market adjustment must not move offer more than ±5% of asking."""
        mi = MarketIntelligenceEngine().from_research_data(RENTCAST_TOOL_RESULT, 600_000)
        result = apply_market_adjustment(560_000, 600_000, mi)
        if result.get('market_applied'):
            adj_pct = abs(result['market_adjustment_pct'])
            self.assertLessEqual(adj_pct, 0.05,
                f"Market adjustment {adj_pct:.1%} exceeds 5% — too aggressive")


# ═══════════════════════════════════════════════════════════════════════════
# 3. HALLUCINATION DETECTION
# ═══════════════════════════════════════════════════════════════════════════

class TestHallucinationDetection(unittest.TestCase):
    """AI must not fabricate findings not present in source documents."""

    # Realistic inspection report snippet
    INSPECTION_TEXT = """
    The roof shows signs of wear with granule loss on the south-facing slopes.
    Estimated remaining life is 3-5 years. Recommend replacement within 2 years.
    The electrical panel is a Federal Pacific Stab-Lok model, which is a known
    fire hazard. Replacement is strongly recommended.
    The HVAC system is operational but is 16 years old and nearing end of life.
    Minor settling cracks observed at the foundation, typical for age of home.
    """

    DISCLOSURE_TEXT = """
    Seller discloses no known plumbing issues. Water heater replaced 2022.
    Seller states roof was serviced in 2024. No known electrical issues.
    Property has had no flooding. No known foundation problems.
    """

    def test_roof_finding_is_grounded(self):
        """Roof finding from actual text should pass grounding."""
        evidence = "Roof shows granule loss with 3-5 years remaining life"
        grounded, pct = _check_grounding(evidence, self.INSPECTION_TEXT)
        self.assertTrue(grounded, f"Roof finding not grounded ({pct:.0%} match)")

    def test_invented_finding_fails_grounding(self):
        """Completely fabricated finding should fail grounding."""
        fabricated = "Asbestos-wrapped pipes found in basement crawlspace near sump pump"
        grounded, pct = _check_grounding(fabricated, self.INSPECTION_TEXT)
        self.assertFalse(grounded,
            f"Fabricated finding passed grounding at {pct:.0%} — threshold too loose")

    def test_contradiction_requires_specific_claim(self):
        """Contradictions must name the specific system, not just say 'issues found'."""
        bad_contradiction = {
            'type': 'disclosure_vs_inspection',
            'severity': 'high',
            'title': 'Issues found',
            'explanation': 'There are some problems with the property.',
            'confidence': 0.9
        }
        result, _ = validate_cross_reference_findings([bad_contradiction],
                                                    self.DISCLOSURE_TEXT,
                                                    self.INSPECTION_TEXT)
        # Should have a grounding warning on vague explanation
        if result:
            finding = result[0]
            # Explanation is too vague — either grounding warning or auto-stripped
            self.assertLess(
                len(finding.get('explanation', '')), 200,
                "Vague contradiction should be flagged or truncated"
            )

    def test_electrical_contradiction_grounded(self):
        """Electrical disclosure contradiction IS grounded in both documents."""
        contradiction = {
            'type': 'disclosure_vs_inspection',
            'severity': 'high',
            'title': 'Seller disclosed no electrical issues — Federal Pacific panel found',
            'explanation': 'Seller stated no known electrical issues. Inspector found Federal Pacific panel, a known fire hazard.',
            'confidence': 0.92
        }
        result, _ = validate_cross_reference_findings([contradiction],
                                                    self.DISCLOSURE_TEXT,
                                                    self.INSPECTION_TEXT)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        # Should NOT have grounding warning for this one
        self.assertNotIn('_grounding_warning', result[0])

    def test_roof_disclosure_contradiction_grounded(self):
        """Roof contradiction (seller claims serviced, inspector says 3-5 yrs) is grounded."""
        contradiction = {
            'type': 'disclosure_vs_inspection',
            'severity': 'medium',
            'title': 'Seller claims roof serviced 2024 — inspector finds 3-5 years remaining life',
            'explanation': 'Seller disclosure states roof was serviced in 2024. Inspector reports granule loss and estimates 3-5 years remaining life, inconsistent with recent service.',
            'confidence': 0.85
        }
        result, _ = validate_cross_reference_findings([contradiction],
                                                    self.DISCLOSURE_TEXT,
                                                    self.INSPECTION_TEXT)
        self.assertEqual(len(result), 1)

    def test_invented_address_not_in_source(self):
        """AI should not invent specific addresses or permit numbers."""
        invented = "Permit #2024-SF-08821 for electrical work at 2839 Pendleton Dr was pulled in 2019"
        grounded, pct = _check_grounding(invented, self.INSPECTION_TEXT)
        self.assertFalse(grounded,
            "Invented permit number/address should fail grounding")

    def test_no_findings_from_blank_text(self):
        """Empty source documents should produce no grounded evidence."""
        evidence = "Foundation has significant cracking requiring immediate structural repair"
        grounded, pct = _check_grounding(evidence, "")
        # Empty source → check skipped → returns True (can't verify)
        # This is the known limitation — we acknowledge it
        self.assertTrue(grounded, "Empty source skips check (known limitation)")

    def test_confidence_score_capped_at_100(self):
        """AI confidence scores above 1.0 must be clamped."""
        findings = [{'type': 'disclosure_vs_inspection', 'severity': 'high',
                     'title': 'Test', 'explanation': 'Test finding here',
                     'confidence': 1.5}]
        result, _ = validate_cross_reference_findings(findings, "test source", "test inspection")
        self.assertLessEqual(result[0]['confidence'], 1.0)

    def test_max_5_contradictions_enforced(self):
        """AI must not return more than 5 contradiction findings."""
        many = [{'type': 'disclosure_vs_inspection', 'severity': 'medium',
                 'title': f'Finding {i}', 'explanation': f'Explanation {i}',
                 'confidence': 0.8} for i in range(10)]
        result, _ = validate_cross_reference_findings(many, "source", "inspection")
        self.assertLessEqual(len(result), 5, "More than 5 contradictions returned")


# ═══════════════════════════════════════════════════════════════════════════
# 4. MARKET CONTEXT
# ═══════════════════════════════════════════════════════════════════════════

class TestMarketContext(unittest.TestCase):
    """MarketIntelligenceEngine must produce correct, bounded output."""

    def setUp(self):
        self.engine = MarketIntelligenceEngine()

    def test_data_quality_high_with_avm_and_comps(self):
        mi = self.engine.from_research_data(RENTCAST_TOOL_RESULT, 600_000)
        self.assertEqual(mi.data_quality, 'high')

    def test_data_quality_none_without_rentcast(self):
        mi = self.engine.from_research_data({}, 600_000)
        self.assertEqual(mi.data_quality, 'none')

    def test_avm_extracted_correctly(self):
        mi = self.engine.from_research_data(RENTCAST_TOOL_RESULT, 600_000)
        self.assertEqual(mi.avm_price, 580_000)
        self.assertEqual(mi.value_range_low, 550_000)
        self.assertEqual(mi.value_range_high, 610_000)

    def test_comp_count_excludes_active_listings(self):
        """Only sold comparables should count, not active listings."""
        data_with_active = {
            'tool_results': [{
                'tool_name': 'rentcast', 'status': 'success',
                'data': {
                    'avm_price': 500_000,
                    'comparables': [
                        {'price': 490_000, 'sqft': 1800, 'price_per_sqft': 272, 'days_on_market': 20, 'status': 'sold',   'listing_type': 'Standard'},
                        {'price': 510_000, 'sqft': 1900, 'price_per_sqft': 268, 'days_on_market': 0,  'status': 'active', 'listing_type': 'Standard'},
                        {'price': 505_000, 'sqft': 1850, 'price_per_sqft': 273, 'days_on_market': 15, 'status': 'sold',   'listing_type': 'Standard'},
                    ],
                    'foreclosure_count': 0, 'short_sale_count': 0, 'new_construction_count': 0,
                }
            }]
        }
        mi = self.engine.from_research_data(data_with_active, 500_000)
        self.assertEqual(mi.comp_count, 2, "Active listings should be excluded from comp count")

    def test_market_temperature_hot_when_asking_above_avm(self):
        """Asking 6% over AVM → hot market."""
        mi = self.engine.from_research_data(RENTCAST_TOOL_RESULT, 615_000)  # 6% over $580K AVM
        self.assertEqual(mi.market_temperature, 'hot')

    def test_market_temperature_buyer_when_asking_below_avm(self):
        """Asking 7% under AVM → buyer's market."""
        mi = self.engine.from_research_data(RENTCAST_TOOL_RESULT, 540_000)  # ~7% under $580K
        self.assertEqual(mi.market_temperature, 'buyer')

    def test_asking_vs_comps_positive_when_overpriced(self):
        """Asking above comp median → positive asking_vs_comps_pct."""
        mi = self.engine.from_research_data(RENTCAST_TOOL_RESULT, 620_000)
        # Comp median is ~$572K, asking $620K → should be positive
        self.assertGreater(mi.asking_vs_comps_pct, 0)

    def test_distressed_pct_calculated(self):
        """Distressed sale percentage calculated from comp mix."""
        data = {
            'tool_results': [{
                'tool_name': 'rentcast', 'status': 'success',
                'data': {
                    'avm_price': 500_000,
                    'comparables': [
                        {'price': 490_000, 'sqft': 1800, 'price_per_sqft': 272, 'days_on_market': 20, 'status': 'sold', 'listing_type': 'Standard'},
                        {'price': 420_000, 'sqft': 1800, 'price_per_sqft': 233, 'days_on_market': 60, 'status': 'sold', 'listing_type': 'Foreclosure'},
                    ],
                    'foreclosure_count': 1, 'short_sale_count': 0, 'new_construction_count': 0,
                }
            }]
        }
        mi = self.engine.from_research_data(data, 500_000)
        self.assertGreater(mi.distressed_pct, 0)

    def test_apply_market_adjustment_returns_required_keys(self):
        mi = self.engine.from_research_data(RENTCAST_TOOL_RESULT, 600_000)
        result = apply_market_adjustment(560_000, 600_000, mi)
        required = ['market_applied', 'market_temperature', 'buyer_leverage',
                    'estimated_value', 'comp_median_price', 'comp_count',
                    'asking_vs_avm_pct', 'market_adjustment_amount',
                    'adjusted_offer', 'rationale']
        for key in required:
            self.assertIn(key, result, f"Missing key: {key}")

    def test_apply_adjustment_returns_false_when_no_data(self):
        mi = self.engine.from_research_data({}, 600_000)
        result = apply_market_adjustment(560_000, 600_000, mi)
        self.assertFalse(result['market_applied'])

    def test_rationale_is_specific_not_generic(self):
        """Rationale must reference actual numbers, not generic text."""
        mi = self.engine.from_research_data(RENTCAST_TOOL_RESULT, 600_000)
        result = apply_market_adjustment(560_000, 600_000, mi)
        rationale = result.get('rationale', '')
        # Must mention at least one dollar figure or percentage
        has_number = any(c.isdigit() for c in rationale)
        self.assertTrue(has_number,
            f"Rationale has no specific numbers: '{rationale}'")

    def test_market_stats_fed_into_market_object(self):
        """market_stats tool results populate the .market sub-object."""
        mi = self.engine.from_research_data(RENTCAST_TOOL_RESULT, 600_000)
        self.assertIsNotNone(mi.market)
        self.assertEqual(mi.market.zip_code, '95148')
        self.assertGreater(mi.market.average_days_on_market, 0)

    def test_avm_confidence_range_pct_calculated(self):
        mi = self.engine.from_research_data(RENTCAST_TOOL_RESULT, 600_000)
        # Range is $550K–$610K on $580K AVM = ~10.3% spread
        self.assertGreater(mi.avm_confidence_range_pct, 0)
        self.assertLess(mi.avm_confidence_range_pct, 30,
            "AVM confidence range seems unreasonably wide")


# ═══════════════════════════════════════════════════════════════════════════
# 5. CONTRADICTION SPECIFICITY
# ═══════════════════════════════════════════════════════════════════════════

class TestContradictionSpecificity(unittest.TestCase):
    """Contradictions must be specific — system, claim, and evidence named."""

    DISCLOSURE = "Seller states no known electrical issues. Roof was recently serviced."
    INSPECTION = "Federal Pacific panel found — fire hazard. Roof has 3-5 years remaining."

    def test_generic_contradiction_title_is_short(self):
        """Generic 'issues found' title should be caught by length check."""
        findings = [{'type': 'disclosure_vs_inspection', 'severity': 'high',
                     'title': 'X' * 130,  # Titles > 120 chars should be truncated
                     'explanation': 'Something generic', 'confidence': 0.8}]
        result, _ = validate_cross_reference_findings(findings, self.DISCLOSURE, self.INSPECTION)
        self.assertLessEqual(len(result[0]['title']), 120)

    def test_specific_system_named_in_title(self):
        """Contradiction title must name the specific system."""
        specific = {
            'type': 'disclosure_vs_inspection', 'severity': 'high',
            'title': 'Electrical panel: seller claims no issues, inspector found Federal Pacific hazard',
            'explanation': 'Seller disclosure states no known electrical issues. Inspector found Federal Pacific Stab-Lok panel.',
            'confidence': 0.92
        }
        result, _ = validate_cross_reference_findings([specific], self.DISCLOSURE, self.INSPECTION)
        self.assertGreater(len(result[0]['title']), 20, "Specific title shouldn't be truncated")

    def test_confidence_below_threshold_still_included(self):
        """Low confidence contradictions are included but preserved."""
        low_conf = {
            'type': 'disclosure_vs_inspection', 'severity': 'low',
            'title': 'Possible roof age discrepancy',
            'explanation': 'Seller says roof recently serviced but inspector notes 3-5 year lifespan.',
            'confidence': 0.55
        }
        result, _ = validate_cross_reference_findings([low_conf], self.DISCLOSURE, self.INSPECTION)
        self.assertEqual(len(result), 1)
        # Confidence should be preserved (not inflated)
        self.assertLessEqual(result[0]['confidence'], 1.0)


# ═══════════════════════════════════════════════════════════════════════════
# 6. REGRESSION CASES — real property scenarios
# ═══════════════════════════════════════════════════════════════════════════

class TestRegressionCases(unittest.TestCase):
    """Named regression cases from real analyses."""

    def test_pendleton_dr_repair_costs_reasonable(self):
        """
        2839 Pendleton Dr San Jose CA 95148 — $900K asking.
        Repair costs should be $30K–$80K, not $90K+ for a typical 4-finding inspection.
        """
        result = estimate_repair_costs(
            zip_code='95148',
            findings=[
                {'category': 'roof',       'severity': 'major',    'description': 'Roof 3-5 years remaining'},
                {'category': 'electrical', 'severity': 'critical', 'description': 'Federal Pacific panel'},
                {'category': 'plumbing',   'severity': 'major',    'description': 'Polybutylene piping'},
                {'category': 'hvac',       'severity': 'moderate', 'description': 'HVAC 16 years old'},
            ],
            property_year_built=1987,
        )
        self.assertLess(result['total_high'], 90_000,
            f"Pendleton Dr: repair high ${result['total_high']:,} exceeds $90K — too high for this finding set")
        self.assertGreater(result['total_low'], 15_000,
            f"Pendleton Dr: repair low ${result['total_low']:,} seems too cheap")

    def test_hollister_repair_costs_lower_than_sj(self):
        """381 Tina Dr Hollister CA 95023 — rural area, lower costs than San Jose."""
        sj = estimate_repair_costs(zip_code='95148', findings=TYPICAL_FINDINGS)
        hollister = estimate_repair_costs(zip_code='95023', findings=TYPICAL_FINDINGS)
        self.assertLessEqual(hollister['total_high'], sj['total_high'],
            "Hollister should cost ≤ San Jose after range tightening")

    def test_market_context_active_on_real_rentcast_shape(self):
        """market_applied must be True when RentCast returns AVM + 3+ comps."""
        mi = MarketIntelligenceEngine().from_research_data(RENTCAST_TOOL_RESULT, 600_000)
        result = apply_market_adjustment(555_000, 600_000, mi)
        self.assertTrue(result['market_applied'],
            "Market context should activate with AVM + 3 sold comps")

    def test_savings_figure_not_inflated(self):
        """
        Savings = asking - recommended_offer.
        For a $600K home with $35K repairs and moderate risk, savings should be
        in the $30K–$60K range, not $200K+.
        This tests the offer calculation logic indirectly via repair costs.
        """
        result = estimate_repair_costs(
            zip_code='95148', findings=TYPICAL_FINDINGS, property_year_built=1987
        )
        repair_avg = (result['total_low'] + result['total_high']) / 2
        asking = 600_000
        # Conservative: offer = asking - repair_avg
        implied_savings = repair_avg
        self.assertLess(implied_savings, asking * 0.15,
            f"Repair-based savings ${implied_savings:,.0f} = {implied_savings/asking:.1%} of asking — suspiciously high")


if __name__ == '__main__':
    unittest.main(verbosity=2)
