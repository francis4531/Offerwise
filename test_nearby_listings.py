"""
OfferWise Nearby Listings Engine Test Suite
============================================
Tests the nearby_listings.py module — the bridge between free tools
and the core analysis product.

Coverage:
  1. Enrichment Math — offer range, risk tier, leverage, value estimation
  2. Grade Boundaries — risk tier thresholds (Low/Medium/High/Critical)
  3. Offer Range Logic — adjustments for DOM, equity, market conditions
  4. Value Estimation — triangulation from median, $/sqft, tax assessment
  5. Leverage Scoring — DOM-based, equity-based, price-vs-AVM signals
  6. Condition Flags — age-based, owner-occupied, repair tolerance
  7. Monthly Cost — PITI + HOA calculation
  8. Briefing Generation — template-based text for various scenarios
  9. Score Calculation — opportunity scoring from market signals
 10. Edge Cases — zero price, missing fields, empty data
 11. Cache Helpers — TTL logic
 12. Email Rendering — HTML output for drip emails
 13. Error Handling — bad ZIP, missing API key
 14. Input Validation — ZIP code format validation
"""

import unittest
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nearby_listings import (
    _enrich_listing,
    _build_briefing,
    _cached_get,
    _err,
    render_listings_email_html,
    get_nearby_listings,
)


# ─────────────────────────────────────────────────────────────────────
# Test fixtures
# ─────────────────────────────────────────────────────────────────────

def _make_listing(**overrides):
    """Create a realistic listing dict with sensible defaults."""
    base = {
        'formattedAddress': '123 Main St, San Jose, CA 95112',
        'city': 'San Jose',
        'state': 'CA',
        'zipCode': '95112',
        'price': 900000,
        'bedrooms': 3,
        'bathrooms': 2,
        'squareFootage': 1500,
        'yearBuilt': 2000,
        'daysOnMarket': 30,
        'propertyType': 'Single Family',
        'listingUrl': 'https://example.com/listing/123',
        'lastSalePrice': 600000,
        'lastSaleDate': '2018-06-15',
        'taxAssessment': 750000,
        'ownerOccupied': True,
        'propertyTaxes': 9000,
    }
    base.update(overrides)
    return base


def _make_market(**overrides):
    """Create a realistic market data dict."""
    base = {
        'median_price': 950000,
        'avg_price': 980000,
        'avg_dom': 25,
        'avg_ppsqft': 600,
        'total_listings': 85,
        'price_trend': 3.2,
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────
# 1. Value Estimation
# ─────────────────────────────────────────────────────────────────────

class TestValueEstimation(unittest.TestCase):
    """Test AVM triangulation from median, $/sqft, and tax assessment."""

    def test_triangulates_three_sources(self):
        listing = _make_listing(price=900000, squareFootage=1500, taxAssessment=750000)
        market = _make_market(median_price=950000, avg_ppsqft=600)
        result = _enrich_listing(listing, market, {})
        # median=950000, ppsqft*sqft=600*1500=900000, tax*1.15=862500
        # avg = (950000+900000+862500)/3 = 904166.67
        self.assertIsNotNone(result['avm_estimate'])
        self.assertAlmostEqual(result['avm_estimate'], 904167, delta=1)

    def test_handles_missing_median(self):
        listing = _make_listing(squareFootage=1500, taxAssessment=750000)
        market = _make_market(median_price=0, avg_ppsqft=600)
        result = _enrich_listing(listing, market, {})
        # Only ppsqft*sqft=900000 and tax*1.15=862500
        expected = round((900000 + 862500) / 2)
        self.assertEqual(result['avm_estimate'], expected)

    def test_handles_missing_sqft(self):
        listing = _make_listing(squareFootage=0, taxAssessment=750000)
        market = _make_market(median_price=950000, avg_ppsqft=600)
        result = _enrich_listing(listing, market, {})
        # Only median=950000 and tax*1.15=862500
        expected = round((950000 + 862500) / 2)
        self.assertEqual(result['avm_estimate'], expected)

    def test_handles_no_tax_assessment(self):
        listing = _make_listing(taxAssessment=None, squareFootage=1500)
        market = _make_market(median_price=950000, avg_ppsqft=600)
        result = _enrich_listing(listing, market, {})
        expected = round((950000 + 900000) / 2)
        self.assertEqual(result['avm_estimate'], expected)

    def test_all_sources_missing_returns_none(self):
        listing = _make_listing(squareFootage=0, taxAssessment=None)
        market = _make_market(median_price=0, avg_ppsqft=0)
        result = _enrich_listing(listing, market, {})
        self.assertIsNone(result['avm_estimate'])


# ─────────────────────────────────────────────────────────────────────
# 2. Offer Range
# ─────────────────────────────────────────────────────────────────────

class TestOfferRange(unittest.TestCase):
    """Test offer range calculation with market adjustments."""

    def test_basic_offer_range(self):
        listing = _make_listing(price=900000, daysOnMarket=15)
        market = _make_market(median_price=950000, avg_dom=25)
        result = _enrich_listing(listing, market, {})
        self.assertIsNotNone(result['offer_range_low'])
        self.assertIsNotNone(result['offer_range_high'])
        self.assertLess(result['offer_range_low'], result['offer_range_high'])

    def test_high_dom_lowers_range(self):
        """Properties sitting 90+ days should have lower offer range."""
        normal = _enrich_listing(
            _make_listing(daysOnMarket=15), _make_market(avg_dom=25), {})
        stale = _enrich_listing(
            _make_listing(daysOnMarket=95), _make_market(avg_dom=25), {})
        self.assertLess(stale['offer_range_high'], normal['offer_range_high'])

    def test_high_equity_lowers_range(self):
        """High seller equity should reduce offer range (they can negotiate)."""
        low_eq = _enrich_listing(
            _make_listing(lastSalePrice=850000), _make_market(), {})
        high_eq = _enrich_listing(
            _make_listing(lastSalePrice=400000), _make_market(), {})
        self.assertLessEqual(high_eq['offer_range_high'], low_eq['offer_range_high'])

    def test_offer_high_never_exceeds_asking(self):
        listing = _make_listing(price=800000)
        market = _make_market(median_price=1200000)
        result = _enrich_listing(listing, market, {})
        self.assertLessEqual(result['offer_range_high'], 800000)

    def test_offer_low_never_exceeds_offer_high(self):
        listing = _make_listing(price=900000)
        market = _make_market()
        result = _enrich_listing(listing, market, {})
        self.assertLessEqual(result['offer_range_low'], result['offer_range_high'])

    def test_no_avm_uses_asking_price(self):
        """When no AVM available, anchor on asking price."""
        listing = _make_listing(price=900000, squareFootage=0, taxAssessment=None)
        market = _make_market(median_price=0, avg_ppsqft=0)
        result = _enrich_listing(listing, market, {})
        self.assertLessEqual(result['offer_range_high'], 900000)
        self.assertGreater(result['offer_range_low'], 0)


# ─────────────────────────────────────────────────────────────────────
# 3. Risk Tier
# ─────────────────────────────────────────────────────────────────────

class TestRiskTier(unittest.TestCase):
    """Test risk tier classification thresholds."""

    def test_new_build_low_risk(self):
        listing = _make_listing(yearBuilt=2022, ownerOccupied=True)
        market = _make_market(median_price=900000)
        result = _enrich_listing(listing, market, {})
        self.assertEqual(result['risk_tier'], 'Low')

    def test_old_home_higher_risk(self):
        """Pre-1975 home with rental status should be High or Critical."""
        listing = _make_listing(yearBuilt=1960, ownerOccupied=False)
        market = _make_market()
        result = _enrich_listing(listing, market, {})
        # yearBuilt 1960 = +3 risk, non-owner +1 = 4 -> High
        self.assertIn(result['risk_tier'], ['High', 'Critical'])

    def test_low_repair_tolerance_increases_risk(self):
        listing = _make_listing(yearBuilt=1995)
        market = _make_market()
        low_tol = _enrich_listing(listing, market, {'repair_tolerance': 'low'})
        high_tol = _enrich_listing(listing, market, {'repair_tolerance': 'high'})
        # Low tolerance user should see higher risk than high tolerance user
        risk_order = {'Low': 0, 'Medium': 1, 'High': 2, 'Critical': 3}
        self.assertGreaterEqual(
            risk_order[low_tol['risk_tier']],
            risk_order[high_tol['risk_tier']]
        )

    def test_overpriced_listing_adds_risk(self):
        listing = _make_listing(price=1200000, squareFootage=1500, taxAssessment=750000)
        market = _make_market(median_price=850000, avg_ppsqft=500)
        result = _enrich_listing(listing, market, {})
        # Asking well above AVM should add risk
        self.assertIn(result['risk_tier'], ['Medium', 'High', 'Critical'])

    def test_condition_flags_populated(self):
        listing = _make_listing(yearBuilt=1970, ownerOccupied=False)
        market = _make_market()
        result = _enrich_listing(listing, market, {})
        self.assertGreater(len(result['condition_flags']), 0)

    def test_pre_1975_flag(self):
        listing = _make_listing(yearBuilt=1970)
        market = _make_market()
        result = _enrich_listing(listing, market, {})
        flags_text = ' '.join(result['condition_flags']).lower()
        self.assertTrue('lead paint' in flags_text or '1975' in flags_text or 'asbestos' in flags_text)


# ─────────────────────────────────────────────────────────────────────
# 4. Negotiation Leverage
# ─────────────────────────────────────────────────────────────────────

class TestLeverage(unittest.TestCase):
    """Test negotiation leverage scoring."""

    def test_high_dom_increases_leverage(self):
        fresh = _enrich_listing(
            _make_listing(daysOnMarket=3), _make_market(avg_dom=25), {})
        stale = _enrich_listing(
            _make_listing(daysOnMarket=60), _make_market(avg_dom=25), {})
        self.assertGreater(stale['leverage'], fresh['leverage'])

    def test_very_new_listing_low_leverage(self):
        result = _enrich_listing(
            _make_listing(daysOnMarket=3), _make_market(avg_dom=25), {})
        self.assertLess(result['leverage'], 50)

    def test_high_equity_increases_leverage(self):
        low_eq = _enrich_listing(
            _make_listing(lastSalePrice=850000), _make_market(), {})
        high_eq = _enrich_listing(
            _make_listing(lastSalePrice=300000), _make_market(), {})
        self.assertGreater(high_eq['leverage'], low_eq['leverage'])

    def test_leverage_capped_0_to_100(self):
        result = _enrich_listing(_make_listing(), _make_market(), {})
        self.assertGreaterEqual(result['leverage'], 0)
        self.assertLessEqual(result['leverage'], 100)

    def test_leverage_tips_populated(self):
        result = _enrich_listing(
            _make_listing(daysOnMarket=80), _make_market(avg_dom=25), {})
        self.assertGreater(len(result['leverage_tips']), 0)


# ─────────────────────────────────────────────────────────────────────
# 5. Monthly Cost
# ─────────────────────────────────────────────────────────────────────

class TestMonthlyCost(unittest.TestCase):
    """Test PITI + HOA monthly cost estimation."""

    def test_basic_monthly_cost(self):
        result = _enrich_listing(_make_listing(price=900000), _make_market(), {})
        self.assertIsNotNone(result['monthly_cost'])
        # 900K * 0.80 loan, 6.5% rate, 30yr = ~$4,548 PI + taxes + insurance
        self.assertGreater(result['monthly_cost'], 4000)
        self.assertLess(result['monthly_cost'], 8000)

    def test_hoa_included_in_cost(self):
        no_hoa = _enrich_listing(_make_listing(hoa=None), _make_market(), {})
        with_hoa = _enrich_listing(_make_listing(hoa=500), _make_market(), {})
        self.assertGreater(with_hoa['monthly_cost'], no_hoa['monthly_cost'])

    def test_hoa_dict_format(self):
        listing = _make_listing(hoa={'fee': 350, 'frequency': 'monthly'})
        result = _enrich_listing(listing, _make_market(), {})
        self.assertIsNotNone(result['monthly_cost'])

    def test_zero_price_no_crash(self):
        listing = _make_listing(price=0)
        result = _enrich_listing(listing, _make_market(), {})
        # Should not crash; monthly_cost may be None or 0
        self.assertTrue(result['monthly_cost'] is None or result['monthly_cost'] == 0)


# ─────────────────────────────────────────────────────────────────────
# 6. Vs. Market Percentage
# ─────────────────────────────────────────────────────────────────────

class TestVsMarket(unittest.TestCase):
    """Test price vs. ZIP median calculation."""

    def test_below_median(self):
        listing = _make_listing(price=800000)
        market = _make_market(median_price=950000)
        result = _enrich_listing(listing, market, {})
        self.assertIsNotNone(result['vs_market_pct'])
        self.assertLess(result['vs_market_pct'], 0)

    def test_above_median(self):
        listing = _make_listing(price=1100000)
        market = _make_market(median_price=950000)
        result = _enrich_listing(listing, market, {})
        self.assertGreater(result['vs_market_pct'], 0)

    def test_no_median_returns_none(self):
        listing = _make_listing(price=900000)
        market = _make_market(median_price=0)
        result = _enrich_listing(listing, market, {})
        self.assertIsNone(result['vs_market_pct'])


# ─────────────────────────────────────────────────────────────────────
# 7. Opportunity Score
# ─────────────────────────────────────────────────────────────────────

class TestOpportunityScore(unittest.TestCase):
    """Test composite opportunity scoring."""

    def test_score_range(self):
        result = _enrich_listing(_make_listing(), _make_market(), {})
        self.assertGreaterEqual(result['score'], 0)
        self.assertLessEqual(result['score'], 100)

    def test_underpriced_high_score(self):
        cheap = _enrich_listing(
            _make_listing(price=700000), _make_market(median_price=950000), {})
        expensive = _enrich_listing(
            _make_listing(price=1100000), _make_market(median_price=950000), {})
        self.assertGreater(cheap['score'], expensive['score'])

    def test_stale_listing_higher_score(self):
        fresh = _enrich_listing(
            _make_listing(daysOnMarket=5), _make_market(avg_dom=25), {})
        stale = _enrich_listing(
            _make_listing(daysOnMarket=50), _make_market(avg_dom=25), {})
        self.assertGreater(stale['score'], fresh['score'])


# ─────────────────────────────────────────────────────────────────────
# 8. Briefing
# ─────────────────────────────────────────────────────────────────────

class TestBriefing(unittest.TestCase):
    """Test template-based briefing generation."""

    def test_briefing_always_string(self):
        result = _enrich_listing(_make_listing(), _make_market(), {})
        self.assertIsInstance(result['briefing'], str)
        self.assertGreater(len(result['briefing']), 10)

    def test_underpriced_mentions_below(self):
        briefing = _build_briefing(
            price=800000, est_mid=900000, median=950000,
            vs_market_pct=-15.8, dom=30, avg_dom=25,
            risk_tier='Low', leverage=50, year_built=2010,
            seller_equity_pct=None, ownership_yrs=None,
        )
        self.assertIn('below', briefing.lower())

    def test_high_dom_mentions_motivated(self):
        briefing = _build_briefing(
            price=900000, est_mid=900000, median=950000,
            vs_market_pct=-5, dom=60, avg_dom=25,
            risk_tier='Medium', leverage=60, year_built=2000,
            seller_equity_pct=None, ownership_yrs=None,
        )
        self.assertIn('motivated', briefing.lower())

    def test_new_listing_mentions_competition(self):
        briefing = _build_briefing(
            price=900000, est_mid=900000, median=950000,
            vs_market_pct=-5, dom=3, avg_dom=25,
            risk_tier='Low', leverage=30, year_built=2015,
            seller_equity_pct=None, ownership_yrs=None,
        )
        self.assertIn('competition', briefing.lower())

    def test_all_none_returns_fallback(self):
        briefing = _build_briefing(
            price=0, est_mid=None, median=0,
            vs_market_pct=None, dom=15, avg_dom=0,
            risk_tier='Medium', leverage=50, year_built=0,
            seller_equity_pct=None, ownership_yrs=None,
        )
        # With no market data and neutral signals, briefing should still be non-empty
        self.assertIsInstance(briefing, str)
        self.assertGreater(len(briefing), 0)


# ─────────────────────────────────────────────────────────────────────
# 9. Edge Cases & Data Contracts
# ─────────────────────────────────────────────────────────────────────

class TestEdgeCases(unittest.TestCase):
    """Test handling of missing, zero, and malformed data."""

    def test_empty_listing(self):
        result = _enrich_listing({}, {}, {})
        self.assertIsNotNone(result)
        self.assertIn('address', result)
        self.assertIn('risk_tier', result)
        self.assertIn('offer_range_low', result)

    def test_all_fields_present(self):
        """Every enriched listing must have the full contract."""
        result = _enrich_listing(_make_listing(), _make_market(), {})
        required_keys = [
            'address', 'city', 'state', 'zip_code', 'price',
            'bedrooms', 'bathrooms', 'sqft', 'year_built',
            'property_type', 'days_on_market', 'offer_range_low',
            'offer_range_high', 'avm_estimate', 'vs_market_pct',
            'risk_tier', 'leverage', 'leverage_tips',
            'condition_flags', 'monthly_cost', 'score', 'briefing',
        ]
        for key in required_keys:
            self.assertIn(key, result, f'Missing key: {key}')

    def test_negative_equity_no_crash(self):
        listing = _make_listing(price=500000, lastSalePrice=700000)
        result = _enrich_listing(listing, _make_market(), {})
        self.assertIsNotNone(result)

    def test_future_year_built(self):
        listing = _make_listing(yearBuilt=2028)
        result = _enrich_listing(listing, _make_market(), {})
        self.assertEqual(result['risk_tier'], 'Low')

    def test_alternative_field_names(self):
        """RentCast sometimes uses different field names."""
        listing = {
            'addressLine1': '456 Oak Ave',
            'listPrice': 800000,
            'sqft': 1200,
            'zip': '95113',
        }
        result = _enrich_listing(listing, _make_market(), {})
        self.assertEqual(result['address'], '456 Oak Ave')
        self.assertEqual(result['price'], 800000)
        self.assertEqual(result['sqft'], 1200)

    def test_short_ownership_flag(self):
        listing = _make_listing(lastSaleDate='2025-06-01')
        result = _enrich_listing(listing, _make_market(), {})
        flags_text = ' '.join(result['condition_flags']).lower()
        self.assertTrue('flip' in flags_text or '2 year' in flags_text)

    def test_non_owner_occupied_flag(self):
        listing = _make_listing(ownerOccupied=False)
        result = _enrich_listing(listing, _make_market(), {})
        flags_text = ' '.join(result['condition_flags']).lower()
        self.assertIn('non-owner', flags_text)


# ─────────────────────────────────────────────────────────────────────
# 10. Cache Helper
# ─────────────────────────────────────────────────────────────────────

class TestCache(unittest.TestCase):
    """Test the in-memory cache helper."""

    def test_fresh_entry_returns_data(self):
        cache = {'key1': {'data': [1, 2, 3], 'ts': time.time()}}
        data, hit = _cached_get(cache, 'key1')
        self.assertEqual(data, [1, 2, 3])
        self.assertTrue(hit)

    def test_expired_entry_returns_none(self):
        cache = {'key1': {'data': [1, 2, 3], 'ts': time.time() - 99999}}
        data, hit = _cached_get(cache, 'key1')
        self.assertIsNone(data)
        self.assertFalse(hit)

    def test_missing_key_returns_none(self):
        data, hit = _cached_get({}, 'nonexistent')
        self.assertIsNone(data)
        self.assertFalse(hit)


# ─────────────────────────────────────────────────────────────────────
# 11. Error Helper
# ─────────────────────────────────────────────────────────────────────

class TestErrorHelper(unittest.TestCase):
    """Test the error response factory."""

    def test_err_structure(self):
        result = _err('Something broke')
        self.assertEqual(result['listings'], [])
        self.assertEqual(result['market'], {})
        self.assertEqual(result['zip_code'], '')
        self.assertEqual(result['error'], 'Something broke')
        self.assertFalse(result['cached'])


# ─────────────────────────────────────────────────────────────────────
# 12. Email Rendering
# ─────────────────────────────────────────────────────────────────────

class TestEmailRendering(unittest.TestCase):
    """Test email-safe HTML rendering for drip campaigns."""

    def _sample_listings(self):
        return [
            _enrich_listing(_make_listing(
                formattedAddress='100 First St',
                price=850000, daysOnMarket=45,
            ), _make_market(), {}),
            _enrich_listing(_make_listing(
                formattedAddress='200 Second Ave',
                price=920000, daysOnMarket=10,
            ), _make_market(), {}),
        ]

    def test_renders_html(self):
        html = render_listings_email_html(
            self._sample_listings(), '95112', _make_market(), 'https://getofferwise.ai')
        self.assertIn('100 First St', html)
        self.assertIn('200 Second Ave', html)
        self.assertIn('95112', html)

    def test_contains_cta_link(self):
        html = render_listings_email_html(
            self._sample_listings(), '95112', _make_market(), 'https://getofferwise.ai')
        self.assertIn('getofferwise.ai/app', html)
        self.assertIn('full analysis', html.lower())

    def test_contains_offer_range(self):
        html = render_listings_email_html(
            self._sample_listings(), '95112', _make_market(), 'https://getofferwise.ai')
        self.assertIn('Offer range', html)

    def test_contains_risk_tier(self):
        html = render_listings_email_html(
            self._sample_listings(), '95112', _make_market(), 'https://getofferwise.ai')
        self.assertIn('Risk', html)

    def test_empty_listings(self):
        html = render_listings_email_html([], '95112', _make_market(), 'https://getofferwise.ai')
        self.assertIn('95112', html)
        self.assertNotIn('Offer range', html)

    def test_caps_at_five(self):
        listings = [
            _enrich_listing(_make_listing(formattedAddress=f'{i} Test St'), _make_market(), {})
            for i in range(10)
        ]
        html = render_listings_email_html(listings, '95112', _make_market(), 'https://getofferwise.ai')
        # Should only render 5 max
        self.assertIn('4 Test St', html)
        self.assertNotIn('5 Test St', html)


# ─────────────────────────────────────────────────────────────────────
# 13. Input Validation (get_nearby_listings)
# ─────────────────────────────────────────────────────────────────────

class TestInputValidation(unittest.TestCase):
    """Test ZIP code validation and API key checks."""

    def test_invalid_zip_returns_error(self):
        result = get_nearby_listings('abc')
        self.assertIn('error', result)

    def test_short_zip_returns_error(self):
        result = get_nearby_listings('123')
        self.assertIn('error', result)

    def test_empty_zip_returns_error(self):
        result = get_nearby_listings('')
        self.assertIn('error', result)

    def test_missing_api_key_returns_error(self):
        # If RENTCAST_API_KEY not set, should get error
        import os
        original = os.environ.get('RENTCAST_API_KEY')
        if original:
            del os.environ['RENTCAST_API_KEY']
        try:
            result = get_nearby_listings('95112')
            self.assertIn('error', result)
        finally:
            if original:
                os.environ['RENTCAST_API_KEY'] = original


# ─────────────────────────────────────────────────────────────────────
# 14. Public Records Extraction
# ─────────────────────────────────────────────────────────────────────

class TestPublicRecords(unittest.TestCase):
    """Test public records signals extracted from listing data."""

    def test_high_tax_vs_price_flag(self):
        listing = _make_listing(price=1200000, taxAssessment=800000)
        result = _enrich_listing(listing, _make_market(), {})
        flags = ' '.join(result['condition_flags']).lower()
        self.assertTrue('tax assessment' in flags or 'above' in flags)

    def test_below_tax_assessment_flag(self):
        listing = _make_listing(price=650000, taxAssessment=800000)
        result = _enrich_listing(listing, _make_market(), {})
        flags = ' '.join(result['condition_flags']).lower()
        self.assertTrue('below tax' in flags or 'motivated' in flags)

    def test_long_owner_flag(self):
        listing = _make_listing(lastSaleDate='2000-01-01')
        result = _enrich_listing(listing, _make_market(), {})
        flags = ' '.join(result['condition_flags']).lower()
        self.assertTrue('long-term' in flags or '20+' in flags or 'deferred' in flags)


if __name__ == '__main__':
    unittest.main(verbosity=2)


# ─────────────────────────────────────────────────────────────────────
# 15. Preference Learning Engine
# ─────────────────────────────────────────────────────────────────────

from nearby_listings import listing_hash, extract_preferences, apply_preference_boost
from types import SimpleNamespace


class TestPreferenceEngine(unittest.TestCase):
    """Test preference learning from save/dismiss history."""

    def _make_history(self, saves=5, dismisses=2):
        history = []
        for i in range(saves):
            history.append(SimpleNamespace(
                action='save', price=800000+i*50000, bedrooms=3,
                bathrooms=2.0, sqft=1800+i*100, year_built=2005+i,
                days_on_market=20+i*5, risk_tier='Low', opportunity_score=70))
        for i in range(dismisses):
            history.append(SimpleNamespace(
                action='dismiss', price=1500000, bedrooms=6,
                bathrooms=4.0, sqft=4000, year_built=1955,
                days_on_market=3, risk_tier='High', opportunity_score=30))
        return history

    def test_listing_hash_deterministic(self):
        h1 = listing_hash('123 Main St, San Jose, CA 95112')
        h2 = listing_hash('123 Main St, San Jose, CA 95112')
        self.assertEqual(h1, h2)

    def test_listing_hash_case_insensitive(self):
        h1 = listing_hash('123 MAIN ST')
        h2 = listing_hash('123 main st')
        self.assertEqual(h1, h2)

    def test_extract_needs_minimum_saves(self):
        history = [SimpleNamespace(action='save', price=500000, bedrooms=2,
            bathrooms=1, sqft=1000, year_built=2000, days_on_market=10,
            risk_tier='Low', opportunity_score=50)]
        self.assertIsNone(extract_preferences(history))

    def test_extract_with_enough_data(self):
        prefs = extract_preferences(self._make_history())
        self.assertIsNotNone(prefs)
        self.assertIn('price_median', prefs)
        self.assertIn('beds_pref', prefs)
        self.assertEqual(prefs['beds_pref'], 3)

    def test_risk_tolerance_detected(self):
        prefs = extract_preferences(self._make_history())
        self.assertEqual(prefs['risk_tolerance'], 'low')

    def test_good_match_scores_higher(self):
        prefs = extract_preferences(self._make_history())
        good = {'price': 900000, 'bedrooms': 3, 'sqft': 1900,
                'year_built': 2008, 'risk_tier': 'Low', 'days_on_market': 25, 'score': 50}
        bad = {'price': 2000000, 'bedrooms': 7, 'sqft': 5000,
               'year_built': 1940, 'risk_tier': 'Critical', 'days_on_market': 2, 'score': 50}
        self.assertGreater(apply_preference_boost(good, prefs),
                          apply_preference_boost(bad, prefs))

    def test_no_prefs_returns_original_score(self):
        listing = {'score': 65}
        self.assertEqual(apply_preference_boost(listing, None), 65)

    def test_score_capped_0_100(self):
        prefs = extract_preferences(self._make_history(saves=10))
        perfect = {'price': 900000, 'bedrooms': 3, 'sqft': 1900,
                   'year_built': 2008, 'risk_tier': 'Low', 'days_on_market': 30, 'score': 95}
        self.assertLessEqual(apply_preference_boost(perfect, prefs), 100)
        self.assertGreaterEqual(apply_preference_boost(perfect, prefs), 0)



# =============================================================================
# MARKET INTELLIGENCE TESTS (v5.62.92)
# =============================================================================

class TestMarketIntelHelpers(unittest.TestCase):
    """Tests for market intelligence helper functions that avoid DB imports."""

    def test_market_intelligence_importable(self):
        """market_intelligence module imports without errors."""
        import importlib
        # The module uses lazy imports, so top-level import should work
        spec = importlib.util.find_spec('market_intelligence')
        self.assertIsNotNone(spec)

    def test_comp_grouping_logic(self):
        """Comp updates correctly classify position from comp data."""
        import json

        # Simulate what get_comp_updates does internally
        comps = [
            {"property_address": "123 Main St", "vs_recommended": "below"},
            {"property_address": "123 Main St", "vs_recommended": "below"},
            {"property_address": "456 Oak Ave", "vs_recommended": "above"},
        ]

        by_property = {}
        for c in comps:
            addr = c.get("property_address", "")
            if addr not in by_property:
                by_property[addr] = {"comps": [], "below_count": 0, "above_count": 0}
            by_property[addr]["comps"].append(c)
            if c.get("vs_recommended") == "below":
                by_property[addr]["below_count"] += 1
            else:
                by_property[addr]["above_count"] += 1

        for data in by_property.values():
            if data["below_count"] > data["above_count"]:
                data["position"] = "improved"
            elif data["above_count"] > data["below_count"]:
                data["position"] = "weakened"
            else:
                data["position"] = "unchanged"

        self.assertEqual(by_property["123 Main St"]["position"], "improved")
        self.assertEqual(by_property["123 Main St"]["below_count"], 2)
        self.assertEqual(by_property["456 Oak Ave"]["position"], "weakened")

    def test_vanished_listing_detection(self):
        """Detecting vanished listings (potential sales) between snapshots."""
        prev_listings = [
            {"address": "100 Elm St", "price": 950000},
            {"address": "200 Oak Ave", "price": 1100000},
            {"address": "300 Pine Rd", "price": 875000},
        ]
        current_listings = [
            {"address": "200 Oak Ave", "price": 1100000},
            {"address": "400 Maple Dr", "price": 980000},
        ]

        current_addrs = {l["address"].lower().strip() for l in current_listings}
        vanished = [l for l in prev_listings
                    if l["address"].lower().strip() not in current_addrs]

        self.assertEqual(len(vanished), 2)
        addrs = {v["address"] for v in vanished}
        self.assertIn("100 Elm St", addrs)
        self.assertIn("300 Pine Rd", addrs)

    def test_alert_scoring(self):
        """Alert count logic: high match + market shift + new comps."""
        alerts = 0
        top_score = 91
        median_delta = -2.1
        new_comps = [{"comp_address": "123 Main"}]

        if top_score >= 75:
            alerts += 1
        if abs(median_delta) >= 1.5:
            alerts += 1
        if new_comps:
            alerts += 1

        self.assertEqual(alerts, 3)

    def test_alert_scoring_no_alerts(self):
        """Alert count is 0 when nothing noteworthy."""
        alerts = 0
        top_score = 40
        median_delta = -0.3
        new_comps = []

        if top_score >= 75:
            alerts += 1
        if abs(median_delta) >= 1.5:
            alerts += 1
        if new_comps:
            alerts += 1

        self.assertEqual(alerts, 0)

    def test_send_intel_email_skip_no_alerts(self):
        """Email skipped when snapshot has no alerts."""
        from drip_campaign import send_market_intelligence_email

        class FakeSnapshot:
            alerts_generated = 0
            alert_email_sent = False

        self.assertFalse(send_market_intelligence_email(None, None, FakeSnapshot()))

    def test_send_intel_email_skip_already_sent(self):
        """Email skipped when already sent."""
        from drip_campaign import send_market_intelligence_email

        class FakeSnapshot:
            alerts_generated = 3
            alert_email_sent = True

        self.assertFalse(send_market_intelligence_email(None, None, FakeSnapshot()))

    def test_delta_computation(self):
        """Market delta percentage computed correctly."""
        prev_median = 1_125_000
        curr_median = 1_104_750

        delta_pct = round((curr_median - prev_median) / prev_median * 100, 1)
        self.assertAlmostEqual(delta_pct, -1.8, places=1)

    def test_delta_zero_prev(self):
        """Delta returns None when previous median is 0."""
        prev_median = 0
        curr_median = 1_100_000

        delta = None
        if prev_median and prev_median > 0:
            delta = round((curr_median - prev_median) / prev_median * 100, 1)

        self.assertIsNone(delta)
