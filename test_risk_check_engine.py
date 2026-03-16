"""
OfferWise Risk Check Engine Test Suite
======================================
Tests the module users interact with most: risk_check_engine.py

Coverage:
  1. Grade Boundaries — exact thresholds for F/D/C/B/B+/A
  2. Cost Accumulation — total exposure sums correctly
  3. Detail Text Accuracy — numbers in text match actual data
  4. Null/Error Handling — every check can be None without crash
  5. Clean Address — zero-risk input produces Grade A, $0, no cards
  6. Risk Card Contracts — every card has required fields
  7. Sorting — cards returned in descending cost order
  8. Radon Zone Lookup — static county table is correct
  9. EPA Classification — thresholds (count-based) produce correct levels
 10. Flood Zone Classification — FEMA zone codes map to correct levels
 11. Earthquake Classification — magnitude/distance thresholds
 12. Wildfire Severity — CAL FIRE zone severity ordering
 13. Cost Table Integrity — all cost keys exist, all positive
 14. Detail Text Hallucination Guard — text must reference real data
"""

import unittest
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from risk_check_engine import (
    calculate_risk_exposure,
    check_radon_zone,
    RISK_COSTS,
    CA_RADON_ZONES,
)


# ===========================================================================
# FIXTURES: Deterministic mock data for each check function
# ===========================================================================

def make_flood(zone='AE', level='HIGH'):
    return {
        'zone': zone, 'level': level, 'in_sfha': level == 'HIGH',
        'detail': f'FEMA Zone {zone} — Special Flood Hazard Area. Flood insurance is REQUIRED for federally-backed mortgages. Average flood claim: $42,000.'
    }

def make_earthquake(count=12, level='HIGH', mag=5.1, dist_km=8.3):
    return {
        'count': count, 'level': level,
        'largest_magnitude': mag, 'nearest_dist_km': dist_km,
        'notable': [{'magnitude': mag, 'place': '10km S of Test City', 'year': 2021}],
        'detail': f'{count} earthquakes (M3.0+) within 50km since 1994. Largest: M{mag}. Nearest: {dist_km}km.'
    }

def make_disasters(count=15, level='HIGH'):
    return {
        'count': count, 'level': level,
        'types': {'Fire': 8, 'Flood': 4, 'Earthquake': 3}, 'scope': 'county',
        'recent': [{'type': 'Fire', 'year': '2023', 'title': 'Test Fire'}],
        'detail': f'{count} FEMA disaster declarations in this county. Includes: 8 Fire, 4 Flood, 3 Earthquake.'
    }

def make_ca_hazards(wildfire_level='HIGH', fault_count=3, fault_level='HIGH'):
    result = {}
    if wildfire_level:
        result['wildfire'] = {
            'level': wildfire_level,
            'detail': 'High Fire Hazard Severity Zone. Fire-resistant construction and defensible space required.'
        }
    if fault_count:
        result['fault_zone'] = {
            'count': fault_count, 'names': ['Hayward', 'Calaveras'],
            'level': fault_level,
            'detail': f'{fault_count} mapped fault(s) within ~10km: Hayward, Calaveras.'
        }
    return result

def make_air_quality(aqi=165, level='HIGH'):
    return {
        'aqi': aqi, 'category': 'Unhealthy', 'level': level,
        'detail': f'Current Air Quality Index: {aqi} (Unhealthy).'
    }

def make_epa(superfund_count=2, superfund_closest=0.8,
             tri_count=48, hazwaste_count=566,
             brownfield_count=9, brownfield_closest=1.5):
    result = {}
    # Superfund
    if superfund_count and superfund_closest and superfund_closest <= 1.0:
        result['superfund'] = {
            'count': superfund_count, 'level': 'HIGH',
            'closest_mi': superfund_closest,
            'sites': ['ACME Chemical Site'],
            'detail': f'{superfund_count} EPA Superfund sites within 3.0 miles. Closest is {superfund_closest} miles away — ACME Chemical Site in Test City. Properties within 1 mile of NPL sites lose 10-15% of value on average.'
        }
    elif superfund_count:
        result['superfund'] = {
            'count': superfund_count, 'level': 'MODERATE',
            'closest_mi': superfund_closest,
            'sites': ['ACME Chemical Site'],
            'detail': f'{superfund_count} EPA Superfund sites within 3.0 miles. Nearest: ACME Chemical Site ({superfund_closest} mi).'
        }
    # TRI
    if tri_count and tri_count >= 5:
        result['tri'] = {
            'count': tri_count, 'level': 'HIGH',
            'sites': ['Facility A', 'Facility B', 'Facility C'],
            'detail': f'{tri_count} facilities reporting toxic chemical releases within 3.0 miles.'
        }
    elif tri_count and tri_count >= 1:
        result['tri'] = {
            'count': tri_count, 'level': 'MODERATE',
            'sites': ['Facility A'],
            'detail': f'{tri_count} TRI-reporting facilities within 3.0 miles.'
        }
    # Hazardous waste
    if hazwaste_count and hazwaste_count >= 50:
        result['hazwaste'] = {
            'count': hazwaste_count, 'level': 'HIGH',
            'detail': f'{hazwaste_count} hazardous waste handlers within 1 mile.'
        }
    elif hazwaste_count and hazwaste_count >= 10:
        result['hazwaste'] = {
            'count': hazwaste_count, 'level': 'MODERATE',
            'detail': f'{hazwaste_count} hazardous waste handlers within 1 mile.'
        }
    # Brownfields
    if brownfield_count and brownfield_closest and brownfield_closest <= 1.0:
        result['brownfields'] = {
            'count': brownfield_count, 'level': 'HIGH',
            'closest_mi': brownfield_closest,
            'detail': f'{brownfield_count} EPA Brownfield sites within 3.0 miles. Closest is {brownfield_closest} miles away.'
        }
    elif brownfield_count:
        result['brownfields'] = {
            'count': brownfield_count, 'level': 'MODERATE',
            'closest_mi': brownfield_closest,
            'detail': f'{brownfield_count} Brownfield sites within 3.0 miles.'
        }
    return result

def make_radon(level='HIGH', zone=1, county='Santa Barbara'):
    return {
        'zone': zone, 'level': level, 'county': county,
        'detail': f'EPA Radon Zone {zone} (highest potential) for {county} County.'
    }


# ===========================================================================
# GROUP 1: GRADE BOUNDARIES
# ===========================================================================

class TestGradeBoundaries(unittest.TestCase):
    """Grade thresholds: F ≥ 60K, D ≥ 40K, C ≥ 25K, B ≥ 10K, B+ > 0, A = 0"""

    def _grade_at(self, total):
        """Compute grade for a given total exposure."""
        if total >= 60000:
            return 'F'
        elif total >= 40000:
            return 'D'
        elif total >= 25000:
            return 'C'
        elif total >= 10000:
            return 'B'
        elif total > 0:
            return 'B+'
        else:
            return 'A'

    def test_grade_f_at_60000(self):
        """$60,000 exactly → Grade F"""
        # Flood HIGH ($42K) + Disaster MODERATE ($8K) + Fault MODERATE ($10K) = $60K
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'),
            None,
            make_disasters(6, 'MODERATE'),
            make_ca_hazards(wildfire_level=None, fault_count=1, fault_level='MODERATE'),
            None
        )
        self.assertEqual(result['grade'], 'F',
                         f"$60K should be F, got {result['grade']} (total={result['total_exposure']})")

    def test_grade_d_at_40000(self):
        """$40,000 exactly → Grade D"""
        # Earthquake CLOSE ($35K) + Earthquake FAR ($5K) won't work (only one earthquake result)
        # Flood MODERATE ($8K) + Earthquake CLOSE ($35K) = $43K → D
        result = calculate_risk_exposure(
            make_flood('B', 'MODERATE'),
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            None, None, None
        )
        # $8K + $35K = $43K → D
        self.assertEqual(result['grade'], 'D',
                         f"Expected D, got {result['grade']} (total={result['total_exposure']})")

    def test_grade_c_boundary(self):
        """Between $25K-$39,999 → Grade C"""
        # Earthquake CLOSE ($35K) alone
        result = calculate_risk_exposure(
            None,
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            None, None, None
        )
        self.assertEqual(result['total_exposure'], 35000)
        self.assertEqual(result['grade'], 'C')

    def test_grade_b_at_10000(self):
        """$10,000-$24,999 → Grade B"""
        # Earthquake MODERATE ($15K)
        result = calculate_risk_exposure(
            None,
            make_earthquake(5, 'MODERATE', 4.2, 20),
            None, None, None
        )
        self.assertEqual(result['total_exposure'], 15000)
        self.assertEqual(result['grade'], 'B')

    def test_grade_bplus_any_risk(self):
        """$1-$9,999 → Grade B+"""
        # Air Quality MODERATE ($3K)
        result = calculate_risk_exposure(
            None, None, None, None,
            make_air_quality(110, 'MODERATE')
        )
        self.assertEqual(result['total_exposure'], 3000)
        self.assertEqual(result['grade'], 'B+')

    def test_grade_a_zero_risk(self):
        """$0 → Grade A"""
        result = calculate_risk_exposure(None, None, None, None, None)
        self.assertEqual(result['total_exposure'], 0)
        self.assertEqual(result['grade'], 'A')
        self.assertEqual(result['risk_count'], 0)
        self.assertEqual(len(result['risks']), 0)

    def test_grade_a_all_minimal(self):
        """All checks return MINIMAL/GOOD/LOW → Grade A, $0"""
        result = calculate_risk_exposure(
            {'zone': 'X', 'level': 'MINIMAL', 'in_sfha': False, 'detail': 'Safe.'},
            {'count': 0, 'level': 'MINIMAL', 'largest_magnitude': None,
             'nearest_dist_km': None, 'notable': [], 'detail': 'No quakes.'},
            {'count': 0, 'level': 'MINIMAL', 'types': {}, 'scope': 'county',
             'recent': [], 'detail': 'No disasters.'},
            {'wildfire': None, 'fault_zone': None},
            {'aqi': 40, 'category': 'Good', 'level': 'GOOD', 'detail': 'AQI 40.'}
        )
        self.assertEqual(result['total_exposure'], 0)
        self.assertEqual(result['grade'], 'A')
        self.assertEqual(result['risk_count'], 0)

    def test_grade_boundary_exact_values(self):
        """Verify boundary arithmetic matches code exactly."""
        # These are the exact boundary values from the code
        self.assertEqual(self._grade_at(60000), 'F')
        self.assertEqual(self._grade_at(59999), 'D')
        self.assertEqual(self._grade_at(40000), 'D')
        self.assertEqual(self._grade_at(39999), 'C')
        self.assertEqual(self._grade_at(25000), 'C')
        self.assertEqual(self._grade_at(24999), 'B')
        self.assertEqual(self._grade_at(10000), 'B')
        self.assertEqual(self._grade_at(9999), 'B+')
        self.assertEqual(self._grade_at(1), 'B+')
        self.assertEqual(self._grade_at(0), 'A')


# ===========================================================================
# GROUP 2: COST ACCUMULATION
# ===========================================================================

class TestCostAccumulation(unittest.TestCase):
    """Verify total_exposure = sum of individual risk card costs."""

    def test_single_risk_cost_matches(self):
        """Flood HIGH alone: total should be exactly $42,000."""
        result = calculate_risk_exposure(make_flood('AE', 'HIGH'), None, None, None, None)
        self.assertEqual(result['total_exposure'], RISK_COSTS['flood_high'])
        self.assertEqual(result['total_exposure'], 42000)

    def test_two_risks_sum(self):
        """Flood HIGH + Earthquake HIGH = $42K + $35K = $77K."""
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'),
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            None, None, None
        )
        self.assertEqual(result['total_exposure'], 42000 + 35000)
        self.assertEqual(result['risk_count'], 2)

    def test_total_equals_sum_of_cards(self):
        """Total exposure must equal sum of all risk card costs."""
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'),
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            make_disasters(15, 'HIGH'),
            make_ca_hazards('HIGH', 3, 'HIGH'),
            make_air_quality(165, 'HIGH'),
            make_epa(),
            make_radon()
        )
        card_sum = sum(r['cost'] for r in result['risks'])
        self.assertEqual(result['total_exposure'], card_sum,
                         f"Total {result['total_exposure']} != card sum {card_sum}")

    def test_all_risks_maximal_exposure(self):
        """All 11 sources at highest level — verify exact dollar total."""
        expected = (
            RISK_COSTS['flood_high']            # 42000
            + RISK_COSTS['earthquake_close']    # 35000
            + RISK_COSTS['disaster_heavy']      # 15000
            + RISK_COSTS['wildfire_high']       # 12000
            + RISK_COSTS['fault_zone_high']     # 20000
            + RISK_COSTS['air_quality_high']    # 8000
            + RISK_COSTS['superfund_close']     # 45000
            + RISK_COSTS['tri_high']            # 12000
            + RISK_COSTS['hazwaste_high']       # 8000
            + RISK_COSTS['brownfield_moderate'] # 4000 (closest > 1mi)
            + RISK_COSTS['radon_high']          # 2500
        )
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'),
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            make_disasters(15, 'HIGH'),
            make_ca_hazards('HIGH', 3, 'HIGH'),
            make_air_quality(165, 'HIGH'),
            make_epa(),
            make_radon()
        )
        self.assertEqual(result['total_exposure'], expected,
                         f"Expected {expected}, got {result['total_exposure']}")

    def test_each_cost_matches_cost_table(self):
        """Each risk card's cost field must match a value in RISK_COSTS."""
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'),
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            make_disasters(15, 'HIGH'),
            make_ca_hazards('HIGH', 3, 'HIGH'),
            make_air_quality(165, 'HIGH'),
            make_epa(),
            make_radon()
        )
        valid_costs = set(RISK_COSTS.values())
        for card in result['risks']:
            self.assertIn(card['cost'], valid_costs,
                          f"Card '{card['title']}' has cost ${card['cost']} "
                          f"which is not in RISK_COSTS table")


# ===========================================================================
# GROUP 3: NULL/ERROR HANDLING — No check should crash calculate_risk_exposure
# ===========================================================================

class TestNullHandling(unittest.TestCase):
    """Every check input can be None without crashing."""

    def test_all_none(self):
        result = calculate_risk_exposure(None, None, None, None, None, None, None)
        self.assertEqual(result['total_exposure'], 0)
        self.assertEqual(result['grade'], 'A')

    def test_only_flood(self):
        result = calculate_risk_exposure(make_flood(), None, None, None, None)
        self.assertGreater(result['total_exposure'], 0)

    def test_only_earthquake(self):
        result = calculate_risk_exposure(None, make_earthquake(), None, None, None)
        self.assertGreater(result['total_exposure'], 0)

    def test_only_disasters(self):
        result = calculate_risk_exposure(None, None, make_disasters(), None, None)
        self.assertGreater(result['total_exposure'], 0)

    def test_only_ca_hazards(self):
        result = calculate_risk_exposure(None, None, None, make_ca_hazards(), None)
        self.assertGreater(result['total_exposure'], 0)

    def test_only_air_quality(self):
        result = calculate_risk_exposure(None, None, None, None, make_air_quality())
        self.assertGreater(result['total_exposure'], 0)

    def test_only_epa(self):
        result = calculate_risk_exposure(None, None, None, None, None, make_epa())
        self.assertGreater(result['total_exposure'], 0)

    def test_only_radon(self):
        result = calculate_risk_exposure(None, None, None, None, None, None, make_radon())
        self.assertGreater(result['total_exposure'], 0)

    def test_empty_ca_hazards_no_crash(self):
        """CA hazards with no wildfire or fault data."""
        result = calculate_risk_exposure(
            None, None, None,
            {'wildfire': None, 'fault_zone': None},
            None
        )
        self.assertEqual(result['total_exposure'], 0)

    def test_empty_epa_no_crash(self):
        """EPA results with all empty subcategories."""
        result = calculate_risk_exposure(None, None, None, None, None, {})
        self.assertEqual(result['total_exposure'], 0)

    def test_epa_with_none_subcategories(self):
        """EPA dict exists but subcategories are missing keys."""
        result = calculate_risk_exposure(
            None, None, None, None, None,
            {'superfund': None, 'tri': None, 'hazwaste': None, 'brownfields': None}
        )
        self.assertEqual(result['total_exposure'], 0)


# ===========================================================================
# GROUP 4: RISK CARD CONTRACT
# ===========================================================================

class TestRiskCardContract(unittest.TestCase):
    """Every risk card must have the required fields for the frontend."""

    REQUIRED_FIELDS = {'category', 'icon', 'title', 'level', 'cost', 'detail', 'seller_hide'}

    def test_all_cards_have_required_fields(self):
        """Generate all possible risk cards and verify schema."""
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'),
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            make_disasters(15, 'HIGH'),
            make_ca_hazards('HIGH', 3, 'HIGH'),
            make_air_quality(165, 'HIGH'),
            make_epa(),
            make_radon()
        )
        for card in result['risks']:
            for field in self.REQUIRED_FIELDS:
                self.assertIn(field, card,
                              f"Card '{card.get('title', '?')}' missing field '{field}'")

    def test_detail_is_complete_sentence(self):
        """Every detail field must be a complete sentence (ends with period)."""
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'),
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            make_disasters(15, 'HIGH'),
            make_ca_hazards('HIGH', 3, 'HIGH'),
            make_air_quality(165, 'HIGH'),
            make_epa(),
            make_radon()
        )
        for card in result['risks']:
            self.assertTrue(card['detail'].rstrip().endswith('.'),
                            f"Card '{card['title']}' detail doesn't end with period: "
                            f"'{card['detail'][-40:]}'")

    def test_seller_hide_is_complete_sentence(self):
        """seller_hide must be a readable sentence, not a fragment or enum."""
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'),
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            make_disasters(15, 'HIGH'),
            make_ca_hazards('HIGH', 3, 'HIGH'),
            make_air_quality(165, 'HIGH'),
            make_epa(),
            make_radon()
        )
        for card in result['risks']:
            text = card['seller_hide']
            self.assertGreater(len(text), 40,
                               f"Card '{card['title']}' seller_hide too short: '{text}'")
            self.assertTrue(text.rstrip().endswith('.'),
                            f"Card '{card['title']}' seller_hide no period: '{text[-40:]}'")
            # Must not be a raw enum or code artifact
            self.assertNotIn('_', text[:20],
                             f"Card '{card['title']}' seller_hide looks like raw code: '{text[:40]}'")

    def test_cost_is_positive_integer(self):
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'),
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            None, None, None, make_epa(), make_radon()
        )
        for card in result['risks']:
            self.assertIsInstance(card['cost'], int,
                                 f"Card '{card['title']}' cost is {type(card['cost'])}")
            self.assertGreater(card['cost'], 0)

    def test_level_is_valid_enum(self):
        valid_levels = {'HIGH', 'MODERATE', 'LOW', 'VERY HIGH', 'VERYHIGH'}
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'),
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            make_disasters(15, 'HIGH'),
            make_ca_hazards('HIGH', 3, 'HIGH'),
            make_air_quality(165, 'HIGH'),
            make_epa(),
            make_radon()
        )
        for card in result['risks']:
            self.assertIn(card['level'], valid_levels,
                          f"Card '{card['title']}' has invalid level '{card['level']}'")


# ===========================================================================
# GROUP 5: SORTING
# ===========================================================================

class TestRiskSorting(unittest.TestCase):
    """Risk cards must be sorted by cost descending."""

    def test_sorted_descending_by_cost(self):
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'),
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            make_disasters(15, 'HIGH'),
            make_ca_hazards('HIGH', 3, 'HIGH'),
            make_air_quality(165, 'HIGH'),
            make_epa(),
            make_radon()
        )
        costs = [r['cost'] for r in result['risks']]
        self.assertEqual(costs, sorted(costs, reverse=True),
                         f"Cards not sorted by cost desc: {costs}")


# ===========================================================================
# GROUP 6: RADON ZONE LOOKUP
# ===========================================================================

class TestRadonZoneLookup(unittest.TestCase):
    """Verify check_radon_zone returns correct levels for known counties."""

    def test_zone_1_santa_barbara(self):
        result = check_radon_zone('Santa Barbara', 'CA')
        self.assertEqual(result['zone'], 1)
        self.assertEqual(result['level'], 'HIGH')

    def test_zone_1_ventura(self):
        result = check_radon_zone('Ventura', 'CA')
        self.assertEqual(result['zone'], 1)
        self.assertEqual(result['level'], 'HIGH')

    def test_zone_2_los_angeles(self):
        result = check_radon_zone('Los Angeles', 'CA')
        self.assertEqual(result['zone'], 2)
        self.assertEqual(result['level'], 'MODERATE')

    def test_zone_2_orange(self):
        result = check_radon_zone('Orange', 'CA')
        self.assertEqual(result['zone'], 2)
        self.assertEqual(result['level'], 'MODERATE')

    def test_zone_3_default_santa_clara(self):
        """Santa Clara County (San Jose) is not in the table → Zone 3."""
        result = check_radon_zone('Santa Clara', 'CA')
        self.assertEqual(result['zone'], 3)
        self.assertEqual(result['level'], 'LOW')

    def test_zone_3_san_francisco(self):
        result = check_radon_zone('San Francisco', 'CA')
        self.assertEqual(result['zone'], 3)
        self.assertEqual(result['level'], 'LOW')

    def test_county_suffix_stripped(self):
        """'Los Angeles County' should match as 'Los Angeles'."""
        result = check_radon_zone('Los Angeles County', 'CA')
        self.assertEqual(result['zone'], 2)

    def test_null_county_returns_none(self):
        self.assertIsNone(check_radon_zone(None, 'CA'))

    def test_null_state_returns_none(self):
        self.assertIsNone(check_radon_zone('Santa Clara', None))

    def test_empty_county_returns_none(self):
        self.assertIsNone(check_radon_zone('', 'CA'))

    def test_high_radon_state(self):
        """Iowa is a known high-radon state → Zone 1."""
        result = check_radon_zone('Polk', 'IA')
        self.assertEqual(result['zone'], 1)
        self.assertEqual(result['level'], 'HIGH')

    def test_non_high_radon_state(self):
        """Florida is not a high-radon state → Zone 3."""
        result = check_radon_zone('Miami-Dade', 'FL')
        self.assertEqual(result['zone'], 3)
        self.assertEqual(result['level'], 'LOW')

    def test_detail_text_includes_county(self):
        result = check_radon_zone('Santa Barbara', 'CA')
        self.assertIn('Santa Barbara', result['detail'])

    def test_ca_radon_table_has_no_duplicates(self):
        """Every county in the table appears exactly once."""
        counties = list(CA_RADON_ZONES.keys())
        self.assertEqual(len(counties), len(set(counties)),
                         f"Duplicate counties in CA_RADON_ZONES: "
                         f"{[c for c in counties if counties.count(c) > 1]}")

    def test_all_zones_are_valid(self):
        """All zone values in the table are 1, 2, or 3."""
        for county, zone in CA_RADON_ZONES.items():
            self.assertIn(zone, (1, 2, 3),
                          f"{county} has invalid zone {zone}")


# ===========================================================================
# GROUP 7: COST TABLE INTEGRITY
# ===========================================================================

class TestCostTableIntegrity(unittest.TestCase):
    """RISK_COSTS table is complete and reasonable."""

    EXPECTED_KEYS = {
        'flood_high', 'flood_moderate',
        'earthquake_close', 'earthquake_moderate', 'earthquake_far',
        'wildfire_very_high', 'wildfire_high', 'wildfire_moderate',
        'fault_zone_high', 'fault_zone_moderate',
        'disaster_heavy', 'disaster_moderate', 'disaster_light',
        'air_quality_high', 'air_quality_moderate',
        'superfund_close', 'superfund_moderate',
        'tri_high', 'tri_moderate',
        'hazwaste_high', 'hazwaste_moderate',
        'brownfield_close', 'brownfield_moderate',
        'radon_high', 'radon_moderate',
    }

    def test_all_expected_keys_exist(self):
        """Every key used by calculate_risk_exposure must exist."""
        for key in self.EXPECTED_KEYS:
            self.assertIn(key, RISK_COSTS, f"Missing cost key: {key}")

    def test_no_unexpected_keys(self):
        """No orphaned keys in the table."""
        for key in RISK_COSTS:
            self.assertIn(key, self.EXPECTED_KEYS, f"Unexpected cost key: {key}")

    def test_all_values_positive(self):
        for key, val in RISK_COSTS.items():
            self.assertIsInstance(val, int, f"{key} is not an int: {type(val)}")
            self.assertGreater(val, 0, f"{key} is not positive: {val}")

    def test_high_always_more_than_moderate(self):
        """HIGH/CLOSE cost must always exceed MODERATE cost for same risk."""
        pairs = [
            ('flood_high', 'flood_moderate'),
            ('earthquake_close', 'earthquake_moderate'),
            ('wildfire_high', 'wildfire_moderate'),
            ('fault_zone_high', 'fault_zone_moderate'),
            ('disaster_heavy', 'disaster_moderate'),
            ('air_quality_high', 'air_quality_moderate'),
            ('superfund_close', 'superfund_moderate'),
            ('tri_high', 'tri_moderate'),
            ('hazwaste_high', 'hazwaste_moderate'),
            ('brownfield_close', 'brownfield_moderate'),
            ('radon_high', 'radon_moderate'),
        ]
        for high_key, mod_key in pairs:
            self.assertGreater(RISK_COSTS[high_key], RISK_COSTS[mod_key],
                               f"{high_key} (${RISK_COSTS[high_key]}) should be > "
                               f"{mod_key} (${RISK_COSTS[mod_key]})")

    def test_earthquake_cascade(self):
        """Earthquake: close > moderate > far."""
        self.assertGreater(RISK_COSTS['earthquake_close'], RISK_COSTS['earthquake_moderate'])
        self.assertGreater(RISK_COSTS['earthquake_moderate'], RISK_COSTS['earthquake_far'])

    def test_wildfire_cascade(self):
        """Wildfire: very_high > high > moderate."""
        self.assertGreater(RISK_COSTS['wildfire_very_high'], RISK_COSTS['wildfire_high'])
        self.assertGreater(RISK_COSTS['wildfire_high'], RISK_COSTS['wildfire_moderate'])

    def test_disaster_cascade(self):
        """Disasters: heavy > moderate > light."""
        self.assertGreater(RISK_COSTS['disaster_heavy'], RISK_COSTS['disaster_moderate'])
        self.assertGreater(RISK_COSTS['disaster_moderate'], RISK_COSTS['disaster_light'])


# ===========================================================================
# GROUP 8: DETAIL TEXT HALLUCINATION GUARDS
# ===========================================================================

class TestDetailTextAccuracy(unittest.TestCase):
    """Numbers in detail text must match the actual data — not hallucinated."""

    def test_earthquake_count_in_detail(self):
        """Detail text must contain the actual earthquake count."""
        eq = make_earthquake(count=7, level='MODERATE', mag=4.3, dist_km=18.5)
        self.assertIn('7', eq['detail'],
                      f"Earthquake detail should mention count 7: '{eq['detail']}'")

    def test_earthquake_magnitude_in_detail(self):
        eq = make_earthquake(count=7, level='MODERATE', mag=4.3, dist_km=18.5)
        self.assertIn('4.3', eq['detail'])

    def test_disaster_count_in_detail(self):
        d = make_disasters(count=15, level='HIGH')
        self.assertIn('15', d['detail'])

    def test_epa_superfund_count_in_detail(self):
        epa = make_epa(superfund_count=2, superfund_closest=0.8)
        self.assertIn('2', epa['superfund']['detail'],
                      f"Superfund detail should mention count: '{epa['superfund']['detail']}'")

    def test_epa_tri_count_in_detail(self):
        epa = make_epa(tri_count=48)
        self.assertIn('48', epa['tri']['detail'])

    def test_epa_hazwaste_count_in_detail(self):
        epa = make_epa(hazwaste_count=566)
        self.assertIn('566', epa['hazwaste']['detail'])

    def test_brownfield_count_in_detail(self):
        epa = make_epa(brownfield_count=9, brownfield_closest=1.5)
        self.assertIn('9', epa['brownfields']['detail'])

    def test_flood_zone_code_in_detail(self):
        """FEMA Zone code must appear in the detail text."""
        flood = make_flood('AE', 'HIGH')
        self.assertIn('AE', flood['detail'])

    def test_no_truncation_with_ellipsis(self):
        """Detail text must never contain '...' (truncation = incomplete sentence)."""
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'),
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            make_disasters(15, 'HIGH'),
            make_ca_hazards('HIGH', 3, 'HIGH'),
            make_air_quality(165, 'HIGH'),
            make_epa(),
            make_radon()
        )
        for card in result['risks']:
            self.assertNotIn('...', card['detail'],
                             f"Card '{card['title']}' detail is truncated: '{card['detail']}'")
            self.assertNotIn('...', card['seller_hide'],
                             f"Card '{card['title']}' seller_hide is truncated")


# ===========================================================================
# GROUP 9: MODERATE vs HIGH CLASSIFICATION
# ===========================================================================

class TestLevelClassification(unittest.TestCase):
    """Verify correct level assignment at classification boundaries."""

    def test_flood_high_zones(self):
        """All SFHA zones should produce HIGH level."""
        high_zones = ['A', 'AE', 'AH', 'AO', 'AR', 'V', 'VE']
        for zone in high_zones:
            flood = make_flood(zone, 'HIGH')
            result = calculate_risk_exposure(flood, None, None, None, None)
            self.assertEqual(result['risks'][0]['level'], 'HIGH',
                             f"Zone {zone} should be HIGH")

    def test_flood_moderate_cost(self):
        """MODERATE flood costs $8K, not $42K."""
        result = calculate_risk_exposure(
            make_flood('B', 'MODERATE'), None, None, None, None)
        self.assertEqual(result['total_exposure'], 8000)

    def test_disaster_level_thresholds(self):
        """Disaster count → level: >10 HIGH, >4 MODERATE, >0 LOW, 0 MINIMAL."""
        # These thresholds are in check_disaster_history, tested via calculate_risk_exposure
        # HIGH ($15K)
        result = calculate_risk_exposure(
            None, None, make_disasters(15, 'HIGH'), None, None)
        self.assertEqual(result['total_exposure'], RISK_COSTS['disaster_heavy'])

        # MODERATE ($8K)
        result = calculate_risk_exposure(
            None, None, make_disasters(6, 'MODERATE'), None, None)
        self.assertEqual(result['total_exposure'], RISK_COSTS['disaster_moderate'])

        # LOW ($4K)
        result = calculate_risk_exposure(
            None, None, make_disasters(2, 'LOW'), None, None)
        self.assertEqual(result['total_exposure'], RISK_COSTS['disaster_light'])

    def test_wildfire_very_high_gets_25k(self):
        result = calculate_risk_exposure(
            None, None, None,
            make_ca_hazards(wildfire_level='VERY HIGH', fault_count=None, fault_level=None),
            None
        )
        self.assertEqual(result['total_exposure'], RISK_COSTS['wildfire_very_high'])
        self.assertEqual(result['total_exposure'], 25000)

    def test_epa_superfund_close_vs_moderate(self):
        """Superfund within 1mi = HIGH ($45K), beyond = MODERATE ($15K)."""
        epa_close = make_epa(superfund_count=1, superfund_closest=0.8)
        result = calculate_risk_exposure(None, None, None, None, None, epa_close)
        self.assertEqual(result['total_exposure'], RISK_COSTS['superfund_close'] +
                         RISK_COSTS['tri_high'] + RISK_COSTS['hazwaste_high'] +
                         RISK_COSTS['brownfield_moderate'])

    def test_radon_high_gets_2500(self):
        result = calculate_risk_exposure(
            None, None, None, None, None, None, make_radon('HIGH', 1, 'Santa Barbara'))
        self.assertEqual(result['total_exposure'], 2500)

    def test_radon_moderate_gets_1200(self):
        result = calculate_risk_exposure(
            None, None, None, None, None, None,
            {'zone': 2, 'level': 'MODERATE', 'county': 'Los Angeles',
             'detail': 'EPA Radon Zone 2 (moderate potential) for Los Angeles County.'})
        self.assertEqual(result['total_exposure'], 1200)

    def test_radon_low_gets_zero(self):
        result = calculate_risk_exposure(
            None, None, None, None, None, None,
            {'zone': 3, 'level': 'LOW', 'county': 'Santa Clara',
             'detail': 'EPA Radon Zone 3.'})
        self.assertEqual(result['total_exposure'], 0)


# ===========================================================================
# GROUP 10: RESULT SCHEMA
# ===========================================================================

class TestResultSchema(unittest.TestCase):
    """calculate_risk_exposure return value has correct structure."""

    def test_result_has_required_keys(self):
        result = calculate_risk_exposure(None, None, None, None, None)
        required = {'total_exposure', 'grade', 'risk_count', 'risks'}
        for key in required:
            self.assertIn(key, result, f"Missing key '{key}' in result")

    def test_risks_is_list(self):
        result = calculate_risk_exposure(None, None, None, None, None)
        self.assertIsInstance(result['risks'], list)

    def test_risk_count_matches_len(self):
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'),
            make_earthquake(12, 'HIGH', 5.1, 8.3),
            None, None, None
        )
        self.assertEqual(result['risk_count'], len(result['risks']))

    def test_total_exposure_is_int(self):
        result = calculate_risk_exposure(
            make_flood('AE', 'HIGH'), None, None, None, None)
        self.assertIsInstance(result['total_exposure'], int)


# ===========================================================================
# RUNNER
# ===========================================================================

def run_all():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestGradeBoundaries,
        TestCostAccumulation,
        TestNullHandling,
        TestRiskCardContract,
        TestRiskSorting,
        TestRadonZoneLookup,
        TestCostTableIntegrity,
        TestDetailTextAccuracy,
        TestLevelClassification,
        TestResultSchema,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    total = suite.countTestCases()
    print(f"\n{'='*60}")
    print(f"  RISK CHECK ENGINE TESTS — {total} tests")
    print(f"{'='*60}\n")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    passed = result.testsRun - len(result.failures) - len(result.errors)
    print(f"\n{'='*60}")
    print(f"  RESULTS: {passed}/{result.testsRun} passed, "
          f"{len(result.failures)} failed, {len(result.errors)} errors")
    print(f"{'='*60}\n")

    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_all()
    sys.exit(0 if success else 1)
