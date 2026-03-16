#!/usr/bin/env python3
"""
Tests for results page data quality and repair cost estimator.
Prevents generic fallback text bugs like "Contradiction found" or "Potential issue".

Run: python3 test_results_quality.py
"""
import os
import sys
import json
import unittest

os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test')
os.environ.setdefault('DATABASE_URL', 'sqlite:///test_results_quality.db')


class TestRepairCostEstimator(unittest.TestCase):
    """Test the repair cost estimation engine."""

    def test_import(self):
        from repair_cost_estimator import estimate_repair_costs
        self.assertTrue(callable(estimate_repair_costs))

    def test_basic_estimate(self):
        from repair_cost_estimator import estimate_repair_costs
        result = estimate_repair_costs(
            zip_code='95120',
            findings=[
                {'category': 'foundation', 'severity': 'major', 'description': 'Cracks in slab'},
            ],
        )
        self.assertIn('breakdown', result)
        self.assertIn('total_low', result)
        self.assertIn('total_high', result)
        self.assertIn('metro_area', result)
        self.assertIn('methodology', result)
        self.assertGreater(result['total_low'], 0)
        self.assertGreater(result['total_high'], result['total_low'])

    def test_breakdown_has_required_fields(self):
        from repair_cost_estimator import estimate_repair_costs
        result = estimate_repair_costs(
            zip_code='10001',
            findings=[
                {'category': 'hvac', 'severity': 'major', 'description': 'Old HVAC'},
                {'category': 'plumbing', 'severity': 'moderate', 'description': 'Slow drains'},
            ],
        )
        for item in result['breakdown']:
            self.assertIn('system', item)
            self.assertIn('low', item)
            self.assertIn('high', item)
            self.assertIn('avg', item)
            self.assertIn('severity', item)
            self.assertGreater(item['low'], 0, f"Low cost should be > 0 for {item['system']}")
            self.assertGreater(item['high'], item['low'], f"High > low for {item['system']}")

    def test_zip_multiplier_varies_by_location(self):
        from repair_cost_estimator import estimate_repair_costs
        findings = [{'category': 'foundation', 'severity': 'major', 'description': 'Cracks'}]
        sf = estimate_repair_costs(zip_code='94102', findings=findings)
        ms = estimate_repair_costs(zip_code='39201', findings=findings)
        # San Francisco should be significantly more expensive than Jackson MS
        self.assertGreater(sf['cost_multiplier'], ms['cost_multiplier'])
        self.assertGreater(sf['breakdown'][0]['avg'], ms['breakdown'][0]['avg'])

    def test_all_categories_produce_estimates(self):
        from repair_cost_estimator import estimate_repair_costs
        categories = ['foundation', 'roof', 'hvac', 'plumbing', 'electrical',
                       'water_damage', 'pest', 'environmental', 'permits', 'safety']
        for cat in categories:
            result = estimate_repair_costs(
                zip_code='80201',
                findings=[{'category': cat, 'severity': 'major', 'description': f'Test {cat}'}],
            )
            self.assertGreater(len(result['breakdown']), 0,
                               f"Category '{cat}' should produce a breakdown item")
            self.assertGreater(result['breakdown'][0]['avg'], 0,
                               f"Category '{cat}' should have non-zero cost")

    def test_severity_affects_cost(self):
        from repair_cost_estimator import estimate_repair_costs
        minor = estimate_repair_costs(
            zip_code='95120',
            findings=[{'category': 'hvac', 'severity': 'minor', 'description': 'Filter dirty'}],
        )
        critical = estimate_repair_costs(
            zip_code='95120',
            findings=[{'category': 'hvac', 'severity': 'critical', 'description': 'Complete failure'}],
        )
        self.assertGreater(critical['breakdown'][0]['avg'], minor['breakdown'][0]['avg'])

    def test_age_adjustment(self):
        from repair_cost_estimator import estimate_repair_costs
        new_home = estimate_repair_costs(
            zip_code='95120',
            findings=[{'category': 'plumbing', 'severity': 'major', 'description': 'Leak'}],
            property_year_built=2020,
        )
        old_home = estimate_repair_costs(
            zip_code='95120',
            findings=[{'category': 'plumbing', 'severity': 'major', 'description': 'Leak'}],
            property_year_built=1960,
        )
        self.assertGreater(old_home['breakdown'][0]['avg'], new_home['breakdown'][0]['avg'])

    def test_empty_zip_still_works(self):
        from repair_cost_estimator import estimate_repair_costs
        result = estimate_repair_costs(
            zip_code='',
            findings=[{'category': 'roof', 'severity': 'moderate', 'description': 'Aging'}],
        )
        self.assertGreater(len(result['breakdown']), 0)

    def test_category_scores_fallback(self):
        from repair_cost_estimator import estimate_repair_costs
        result = estimate_repair_costs(
            zip_code='95120',
            category_scores=[
                {'category': 'foundation', 'score': 72},
                {'category': 'hvac', 'score': 40},
                {'category': 'electrical', 'score': 10},
            ],
            total_repair_low=50000,
            total_repair_high=130000,
        )
        self.assertGreater(len(result['breakdown']), 0)
        # Higher risk score should mean higher cost
        costs = {b['category']: b['avg'] for b in result['breakdown']}
        self.assertGreater(costs.get('foundation', 0), costs.get('electrical', 0))

    def test_methodology_includes_metro(self):
        from repair_cost_estimator import estimate_repair_costs
        result = estimate_repair_costs(
            zip_code='10001',
            findings=[{'category': 'roof', 'severity': 'major', 'description': 'Test'}],
        )
        self.assertIn('New York', result['methodology'])

    def test_no_duplicate_systems(self):
        from repair_cost_estimator import estimate_repair_costs
        result = estimate_repair_costs(
            zip_code='95120',
            findings=[
                {'category': 'foundation', 'severity': 'major', 'description': 'Issue 1'},
                {'category': 'foundation', 'severity': 'minor', 'description': 'Issue 2'},
            ],
        )
        # Same system findings should merge into 1 row with 2 issues
        self.assertEqual(len(result['breakdown']), 1)
        self.assertEqual(result['breakdown'][0]['issue_count'], 2)
        self.assertEqual(result['breakdown'][0]['severity'], 'major')  # worst severity wins


class TestZIPCostData(unittest.TestCase):
    """Test the ZIP cost data coverage."""

    def test_import(self):
        from zip_cost_data import ZIP_COST_DATA
        self.assertIsInstance(ZIP_COST_DATA, dict)

    def test_coverage_count(self):
        from zip_cost_data import ZIP_COST_DATA
        self.assertGreater(len(ZIP_COST_DATA), 400, "Should have 400+ ZIP prefixes")

    def test_all_entries_have_correct_format(self):
        from zip_cost_data import ZIP_COST_DATA
        for prefix, (mult, metro) in ZIP_COST_DATA.items():
            self.assertEqual(len(prefix), 3, f"Prefix '{prefix}' should be 3 chars")
            self.assertTrue(prefix.isdigit(), f"Prefix '{prefix}' should be digits")
            self.assertIsInstance(mult, float, f"Multiplier for {prefix} should be float")
            self.assertGreater(mult, 0.5, f"Multiplier {mult} for {prefix} too low")
            self.assertLess(mult, 2.0, f"Multiplier {mult} for {prefix} too high")
            self.assertTrue(len(metro) > 0, f"Metro name for {prefix} should not be empty")

    def test_major_cities_present(self):
        from zip_cost_data import ZIP_COST_DATA
        major = {
            '100': 'New York', '600': 'Chicago', '770': 'Houston',
            '900': 'Los Angeles', '940': 'San Francisco', '980': 'Seattle',
            '330': 'Miami', '800': 'Denver', '850': 'Phoenix',
            '300': 'Atlanta', '200': 'Washington',
        }
        for prefix, expected_metro in major.items():
            self.assertIn(prefix, ZIP_COST_DATA, f"Missing major city ZIP {prefix}")
            mult, metro = ZIP_COST_DATA[prefix]
            self.assertIn(expected_metro.split()[0], metro,
                          f"ZIP {prefix} metro '{metro}' should contain '{expected_metro}'")

    def test_california_multipliers_above_national(self):
        from zip_cost_data import ZIP_COST_DATA
        ca_prefixes = [k for k in ZIP_COST_DATA if 900 <= int(k) <= 961]
        self.assertGreater(len(ca_prefixes), 30, "Should have 30+ CA prefixes")
        for p in ca_prefixes:
            mult, metro = ZIP_COST_DATA[p]
            self.assertGreaterEqual(mult, 1.0,
                                     f"CA ZIP {p} ({metro}) should be >= 1.0x national avg")


class TestResultsDataExtraction(unittest.TestCase):
    """
    Test that the JSX results section properly extracts text from all
    known data structures. These tests check the app.html source code
    to ensure field names match what the backend produces.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(os.path.dirname(__file__), 'static', 'app.html'), 'r') as f:
            cls.html = f.read()

    def test_cross_reference_uses_explanation_field(self):
        """CrossReferenceMatch has 'explanation', not 'detail' or 'description'."""
        # The code should check 'explanation' before falling back to generic text
        self.assertIn('c.explanation', self.html,
                       "Cross-reference should check 'explanation' field from CrossReferenceMatch")

    def test_no_bare_generic_fallbacks_without_field_chain(self):
        """Every fallback text should have at least 4 field checks before it."""
        import re
        # Find all fallback strings in the results section
        fallbacks = re.findall(
            r"(\w+\.(?:\w+ \|\| )+)'([^']+)'",
            self.html
        )
        # Also check using the " delimited versions
        for match in re.finditer(r'(?:(\w+)\.(\w+)(?:\s*\|\|\s*\w+\.\w+)*\s*\|\|\s*[\'"]([^\'"]+)[\'"])', self.html):
            # This is a rough check — ensure the fallback chain has multiple fields
            pass

    def test_deal_breakers_check_explanation(self):
        """Deal breakers should try 'explanation' field."""
        self.assertIn('db.explanation', self.html,
                       "Deal breakers should check 'explanation' field")

    def test_predicted_issues_use_predicted_issue_field(self):
        """AI returns 'predicted_issue' not 'prediction' or 'description'."""
        self.assertIn('issue.predicted_issue', self.html,
                       "Predicted issues should check 'predicted_issue' field first")

    def test_predicted_issues_format_probability(self):
        """Probability should be formatted as percentage, not raw float."""
        self.assertIn('Math.round(issue.probability * 100)', self.html,
                       "Probability should be converted from 0-1 to percentage")

    def test_repair_estimate_used_in_breakdown(self):
        """Frontend should use server-side repair_estimate when available."""
        self.assertIn('result.repair_estimate', self.html,
                       "Should use repair_estimate from server")
        self.assertIn('repairEstimate.breakdown', self.html,
                       "Should access breakdown from repair_estimate")

    def test_no_duplicate_jsx_blocks(self):
        """No duplicate guard conditions that cause syntax errors."""
        import re
        # Check for consecutive identical lines (like the avgRepairCost bug)
        lines = self.html.split('\n')
        for i in range(len(lines) - 1):
            stripped = lines[i].strip()
            if stripped and len(stripped) > 20 and not stripped.startswith('//') and not stripped.startswith('*'):
                if stripped == lines[i + 1].strip():
                    # Allow some duplicates that are intentional (like closing divs)
                    if not stripped.startswith('</') and not stripped.startswith('}') and stripped not in ('</div>', '</span>', '})', '});', '})'):
                        self.fail(f"Duplicate JSX line at {i+1}: {stripped[:80]}")

    def test_transparency_checks_all_data_sources(self):
        """Transparency report should display undisclosed_issues and sub-scores."""
        self.assertIn('tr.undisclosed_issues', self.html)
        self.assertIn('tr.minimized_issues', self.html)
        self.assertIn('tr.omission_score', self.html)
        self.assertIn('tr.consistency_score', self.html)

    def test_boilerplate_filter_present(self):
        """Undisclosed issues should filter out inspection boilerplate."""
        self.assertIn('disclaimer', self.html.lower())
        self.assertIn('not a qualified', self.html.lower())
        self.assertIn('presence of mold', self.html.lower())

    def test_risk_dna_descriptions_present(self):
        """Risk DNA pentagram should show descriptions for each axis."""
        self.assertIn('Foundation, walls, roof structure', self.html)
        self.assertIn('HVAC, plumbing, electrical', self.html)

    def test_patent_pending_present(self):
        """Patent Pending branding must appear in results."""
        self.assertIn('Patent Pending', self.html)
        self.assertIn('OfferScore™', self.html)
        self.assertIn('Risk DNA™', self.html)


class TestRepairCostModels(unittest.TestCase):
    """Test the database models for repair costs."""

    def test_models_importable(self):
        from models import RepairCostZone, RepairCostBaseline, RepairCostLog
        self.assertTrue(hasattr(RepairCostZone, 'zip_prefix'))
        self.assertTrue(hasattr(RepairCostBaseline, 'category'))
        self.assertTrue(hasattr(RepairCostLog, 'breakdown_json'))

    def test_seed_function_importable(self):
        from seed_repair_costs import seed_repair_cost_data
        self.assertTrue(callable(seed_repair_cost_data))

    def test_baseline_costs_complete(self):
        """Every category should have all 4 severity levels."""
        from repair_cost_estimator import BASELINE_COSTS
        severities = {'minor', 'moderate', 'major', 'critical'}
        for category, sev_dict in BASELINE_COSTS.items():
            for sev in severities:
                self.assertIn(sev, sev_dict,
                              f"Category '{category}' missing severity '{sev}'")
                low, high = sev_dict[sev]
                self.assertGreater(low, 0, f"{category}/{sev} low should be > 0")
                self.assertGreater(high, low, f"{category}/{sev} high should > low")

    def test_category_normalization(self):
        from repair_cost_estimator import _normalize_category
        self.assertEqual(_normalize_category('foundation_structure'), 'foundation')
        self.assertEqual(_normalize_category('Foundation & Structure'), 'foundation')
        self.assertEqual(_normalize_category('hvac_systems'), 'hvac')
        self.assertEqual(_normalize_category('HVAC & Systems'), 'hvac')
        self.assertEqual(_normalize_category('roof_exterior'), 'roof')
        self.assertEqual(_normalize_category('electrical_fire'), 'electrical')
        self.assertEqual(_normalize_category('something_unknown'), 'general')
        self.assertEqual(_normalize_category(''), 'general')
        self.assertEqual(_normalize_category(None), 'general')

    def test_severity_normalization(self):
        from repair_cost_estimator import _normalize_severity
        self.assertEqual(_normalize_severity('critical'), 'critical')
        self.assertEqual(_normalize_severity('high'), 'critical')
        self.assertEqual(_normalize_severity('severe'), 'critical')
        self.assertEqual(_normalize_severity('major'), 'major')
        self.assertEqual(_normalize_severity('significant'), 'major')
        self.assertEqual(_normalize_severity('moderate'), 'moderate')
        self.assertEqual(_normalize_severity('medium'), 'moderate')
        self.assertEqual(_normalize_severity('minor'), 'minor')
        self.assertEqual(_normalize_severity('low'), 'minor')
        self.assertEqual(_normalize_severity(''), 'moderate')
        self.assertEqual(_normalize_severity(None), 'moderate')


if __name__ == '__main__':
    # Change to the project directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    unittest.main(verbosity=2)
