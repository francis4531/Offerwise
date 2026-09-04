"""test_repair_no_fabrication.py — v5.89.337

THE real root cause of the 13180 Edgemont fabricated foundation finding.

The "Confirmed repairs" section of the report renders from repair_estimate.breakdown,
built by repair_cost_estimator.estimate_repair_costs — NOT from the (gated) inspection
findings. When `findings` was empty, that function fell through to `category_scores` and
manufactured one confirmed-repair line item per category RISK SCORE — converting a
statistical risk score into an itemized dollar cost in the section that claims findings
"came directly out of the inspection report". A house whose inspection says the foundation
is SOUND still produced FOUNDATION & STRUCTURE · CRITICAL · $25-60k, because the structural
risk SCORE was high. This bypassed every findings-layer fix (.333/.334/.335/.336).

Fix: confirmed-repair line items are built ONLY from real findings. No findings -> no
confirmed repairs. Risk scores never become itemized repair costs.
"""
import importlib.util, sys, unittest

def _est():
    for k in ('repair_cost_estimator',):
        if k in sys.modules: del sys.modules[k]
    spec = importlib.util.spec_from_file_location('repair_cost_estimator', 'repair_cost_estimator.py')
    m = importlib.util.module_from_spec(spec); sys.modules['repair_cost_estimator']=m; spec.loader.exec_module(m)
    return m.estimate_repair_costs

class TestNoFabricatedRepairs(unittest.TestCase):
    def setUp(self): self.est = _est()

    def test_no_findings_yields_no_breakdown_even_with_high_category_scores(self):
        r = self.est(zip_code='75035', findings=[],
                     category_scores=[{'category':'foundation_structure','score':75},
                                      {'category':'roof_exterior','score':50}],
                     property_year_built=2018)
        self.assertEqual(len(r['breakdown']), 0,
            "with no real findings there are NO confirmed repairs — risk scores must not "
            "be converted into itemized repair line items")

    def test_no_foundation_line_from_score_alone(self):
        r = self.est(zip_code='75035', findings=[],
                     category_scores=[{'category':'foundation_structure','score':90}],
                     property_year_built=2018)
        systems = ' '.join(str(b.get('system','')).lower() for b in r['breakdown'])
        self.assertNotIn('foundation', systems)
        self.assertNotIn('structure', systems)

    def test_real_findings_still_produce_breakdown(self):
        r = self.est(zip_code='75035',
                     findings=[{'category':'plumbing','severity':'moderate',
                                'description':'sprinkler leak',
                                'estimated_cost_low':2000,'estimated_cost_high':5000}],
                     category_scores=[], property_year_built=2018)
        self.assertEqual(len(r['breakdown']), 1)
        self.assertEqual(r['breakdown'][0]['category'], 'plumbing')

    def test_real_findings_win_even_when_scores_present(self):
        # real plumbing finding + high foundation SCORE -> only plumbing appears
        r = self.est(zip_code='75035',
                     findings=[{'category':'plumbing','severity':'moderate',
                                'description':'sprinkler leak'}],
                     category_scores=[{'category':'foundation_structure','score':90}],
                     property_year_built=2018)
        systems = ' '.join(str(b.get('system','')).lower() for b in r['breakdown'])
        self.assertIn('plumbing', systems)
        self.assertNotIn('foundation', systems)

if __name__ == '__main__':
    unittest.main(verbosity=2)
