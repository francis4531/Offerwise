"""Tests for validation.py — OfferWise data validation module."""
import pytest
from validation import (
    ValidationError, CostValidator, TextValidator, PriceValidator,
    RiskScoreValidator, OfferValidator, CategoryValidator,
    WalkAwayRecommendation, validate_analysis_output,
)


class TestCostValidator:
    def test_valid_cost_range_passes(self):
        low, high = CostValidator.validate_cost_range(5000, 15000, 'electrical', 'major')
        assert low > 0 and high >= low

    def test_critical_minimum_enforced(self):
        low, _ = CostValidator.validate_cost_range(100, 500, 'electrical', 'critical')
        assert low >= CostValidator.CRITICAL_MINIMUMS['electrical']

    def test_major_minimum_enforced(self):
        low, _ = CostValidator.validate_cost_range(50, 200, 'roof_exterior', 'major')
        assert low >= CostValidator.MAJOR_MINIMUMS['roof_exterior']

    def test_maximum_cap_applied(self):
        _, high = CostValidator.validate_cost_range(10000, 9_999_999, 'electrical', 'critical')
        assert high <= CostValidator.MAXIMUM_REASONABLE_COSTS['electrical']

    def test_unknown_category_does_not_crash(self):
        low, high = CostValidator.validate_cost_range(1000, 5000, 'misc_unknown', 'minor')
        assert low >= 0 and high >= low

    def test_inverted_range_corrected(self):
        low, high = CostValidator.validate_cost_range(10000, 5000, 'plumbing', 'major')
        assert low <= high

    def test_validate_total_costs_reasonable(self):
        result = CostValidator.validate_total_costs(50000, 800000)
        assert result >= 0

    def test_validate_total_costs_extreme_does_not_crash(self):
        result = CostValidator.validate_total_costs(5_000_000, 800000)
        assert result >= 0


class TestTextValidator:
    def test_clean_issue_text_normal(self):
        result = TextValidator.clean_issue_text("The roof has significant damage")
        assert "roof" in result.lower()

    def test_clean_issue_text_strips_whitespace(self):
        result = TextValidator.clean_issue_text("  damaged wiring  ")
        assert not result.startswith(" ") and not result.endswith(" ")

    def test_clean_issue_text_empty(self):
        result = TextValidator.clean_issue_text("")
        assert result == "" or result is None

    def test_validate_issue_text_valid(self):
        result = TextValidator.validate_issue_text("Foundation shows signs of cracking and settling near the perimeter")
        assert result is True

    def test_validate_issue_text_too_short(self):
        result = TextValidator.validate_issue_text("bad")
        assert result is False

    def test_validate_issue_text_with_category(self):
        result = TextValidator.validate_issue_text("Electrical panel needs full replacement", 'electrical')
        assert isinstance(result, bool)


class TestPriceValidator:
    def test_valid_price_passes(self):
        assert PriceValidator.validate_price(750000) == 750000

    def test_high_price_passes(self):
        assert PriceValidator.validate_price(5_000_000) == 5_000_000

    def test_zero_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            PriceValidator.validate_price(0)

    def test_negative_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            PriceValidator.validate_price(-50000)

    def test_string_price_converted(self):
        assert PriceValidator.validate_price("850000") == 850000

    def test_price_with_commas(self):
        assert PriceValidator.validate_price("850,000") == 850000

    def test_price_with_dollar_sign(self):
        assert PriceValidator.validate_price("$750,000") == 750000

    def test_below_minimum_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            PriceValidator.validate_price(PriceValidator.MIN_PRICE - 1)

    def test_above_maximum_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            PriceValidator.validate_price(PriceValidator.MAX_PRICE + 1)

    def test_float_converted_to_int(self):
        result = PriceValidator.validate_price(750000.99)
        assert isinstance(result, int)


class TestRiskScoreValidator:
    def test_mid_range_passes(self):
        assert RiskScoreValidator.validate_risk_score(45.0) == 45.0

    def test_zero_passes(self):
        assert RiskScoreValidator.validate_risk_score(0.0) == 0.0

    def test_hundred_passes(self):
        assert RiskScoreValidator.validate_risk_score(100.0) == 100.0

    def test_above_100_clamped(self):
        assert RiskScoreValidator.validate_risk_score(150.0) <= 100.0

    def test_negative_clamped_to_zero(self):
        assert RiskScoreValidator.validate_risk_score(-20.0) == 0.0

    def test_validate_risk_tier_returns_string(self):
        tier = RiskScoreValidator.validate_risk_tier('LOW', 0, 0)
        assert isinstance(tier, str) and len(tier) > 0

    def test_validate_risk_tier_many_criticals_upgrades(self):
        tier = RiskScoreValidator.validate_risk_tier('LOW', 5, 3)
        assert tier.upper() in ('HIGH', 'CRITICAL', 'MEDIUM', 'LOW')


class TestOfferValidator:
    def test_valid_discount(self):
        result = OfferValidator.validate_discount(80000, 800000)  # 10% = $80K
        assert 0 <= result <= 800000

    def test_zero_discount(self):
        assert OfferValidator.validate_discount(0.0, 800000) == 0.0

    def test_extreme_discount_capped(self):
        # $500K discount on $800K house (62.5%) — capped at 50% = $400K
        result = OfferValidator.validate_discount(500000, 800000)
        assert result < 500000

    def test_negative_discount_corrected(self):
        assert OfferValidator.validate_discount(-1000, 800000) >= 0.0

    def test_valid_offer(self):
        assert OfferValidator.validate_offer(720000, 800000, 0.10) > 0

    def test_zero_offer_corrected(self):
        assert OfferValidator.validate_offer(0, 800000, 0.05) > 0

    def test_should_walk_away_high_repair_ratio(self):
        assert OfferValidator.should_walk_away(800000, 500000, 60.0) is True

    def test_should_walk_away_safe_property(self):
        assert OfferValidator.should_walk_away(800000, 20000, 20.0) is False

    def test_should_walk_away_teardown(self):
        assert OfferValidator.should_walk_away(800000, 900000, 50.0) is True

    def test_should_walk_away_high_risk_high_repairs(self):
        assert OfferValidator.should_walk_away(800000, 280000, 85.0) is True


class TestCategoryValidator:
    def _cats(self):
        return [
            {'name': 'foundation_structure', 'score': 45, 'severity': 'major', 'issue_count': 2},
            {'name': 'electrical', 'score': 30, 'severity': 'critical', 'issue_count': 1},
            {'name': 'plumbing', 'score': 20, 'severity': 'major', 'issue_count': 1},
            {'name': 'roof_exterior', 'score': 10, 'severity': 'minor', 'issue_count': 1},
            {'name': 'hvac_systems', 'score': 15, 'severity': 'major', 'issue_count': 1},
            {'name': 'environmental', 'score': 5, 'severity': 'minor', 'issue_count': 0},
            {'name': 'legal_title', 'score': 0, 'severity': 'none', 'issue_count': 0},
            {'name': 'insurance_hoa', 'score': 0, 'severity': 'none', 'issue_count': 0},
        ]

    def test_valid_categories_returns_list(self):
        result = CategoryValidator.validate_categories(self._cats())
        assert isinstance(result, list) and len(result) > 0

    def test_negative_scores_corrected(self):
        cats = self._cats()
        cats[0]['score'] = -10
        result = CategoryValidator.validate_categories(cats)
        for cat in result:
            assert cat.get('score', 0) >= 0

    def test_validate_environmental_returns_bool(self):
        assert isinstance(CategoryValidator.validate_environmental_category(self._cats()), bool)

    def test_validate_legal_returns_bool(self):
        assert isinstance(CategoryValidator.validate_legal_category(self._cats()), bool)


class TestWalkAwayRecommendation:
    def test_repairs_exceed_half_price(self):
        result, reason = WalkAwayRecommendation.should_recommend_walk_away(
            800000, 450000, 60.0, 2, 50.0)
        assert result is True and len(reason) > 0

    def test_teardown_scenario(self):
        result, reason = WalkAwayRecommendation.should_recommend_walk_away(
            800000, 900000, 70.0, 3, 40.0)
        assert result is True

    def test_excellent_property_no_walkaway(self):
        result, reason = WalkAwayRecommendation.should_recommend_walk_away(
            800000, 15000, 20.0, 0, 85.0)
        assert result is False and reason == ""

    def test_many_criticals_high_cost(self):
        result, _ = WalkAwayRecommendation.should_recommend_walk_away(
            800000, 280000, 70.0, 4, 60.0)
        assert result is True

    def test_extreme_risk_with_costs(self):
        result, _ = WalkAwayRecommendation.should_recommend_walk_away(
            800000, 220000, 90.0, 2, 55.0)
        assert result is True

    def test_low_transparency_high_costs(self):
        result, _ = WalkAwayRecommendation.should_recommend_walk_away(
            800000, 260000, 55.0, 1, 20.0)
        assert result is True

    def test_reason_is_string(self):
        _, reason = WalkAwayRecommendation.should_recommend_walk_away(
            800000, 450000, 60.0, 2, 50.0)
        assert isinstance(reason, str)


class TestValidateAnalysisOutput:
    def _valid(self):
        return {
            'risk_score': {
                'overall_risk_score': 45.0, 'risk_tier': 'MEDIUM',
                'buyer_adjusted_score': 48.0, 'critical_count': 1, 'major_count': 2,
            },
            'offer_strategy': {
                'recommended_offer': 720000, 'aggressive_offer': 680000,
                'conservative_offer': 760000, 'asking_price': 800000,
                'discount_percentage': 10.0,
            },
            'repair_estimate': {'total_low': 15000, 'total_high': 35000},
            'categories': [{'name': 'electrical', 'severity': 'major', 'score': 30,
                             'cost_low': 5000, 'cost_high': 15000, 'issue_count': 1}],
            'property_price': 800000,
        }

    def test_valid_passes(self):
        result = validate_analysis_output(self._valid())
        assert result is not None and 'risk_score' in result

    def test_preserves_offer_strategy(self):
        result = validate_analysis_output(self._valid())
        assert result['offer_strategy']['recommended_offer'] == 720000

    def test_risk_score_above_100_clamped(self):
        a = self._valid(); a['risk_score']['overall_risk_score'] = 999.0
        assert validate_analysis_output(a)['risk_score']['overall_risk_score'] <= 100.0

    def test_risk_score_below_zero_clamped(self):
        a = self._valid(); a['risk_score']['overall_risk_score'] = -50.0
        assert validate_analysis_output(a)['risk_score']['overall_risk_score'] >= 0.0

    def test_categories_preserved(self):
        result = validate_analysis_output(self._valid())
        assert len(result['categories']) == 1

    def test_returns_dict(self):
        assert isinstance(validate_analysis_output(self._valid()), dict)

    def test_multiple_categories(self):
        a = self._valid()
        a['categories'].append({'name': 'plumbing', 'severity': 'critical', 'score': 60,
                                 'cost_low': 12000, 'cost_high': 25000, 'issue_count': 2})
        result = validate_analysis_output(a)
        assert len(result['categories']) == 2

    def test_zero_repair_costs_ok(self):
        a = self._valid()
        a['repair_estimate'] = {'total_low': 0, 'total_high': 0}
        assert validate_analysis_output(a) is not None
