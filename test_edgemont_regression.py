"""
v5.89.338 — Regression: 13180 Edgemont Ln, Frisco TX (the "fabricated foundation" deal)

Both source documents say the foundation is sound. The Sept 4 report still showed
"FOUNDATION & STRUCTURE · CRITICAL · $25,000-$60,000", "HVAC & SYSTEMS · CRITICAL",
"$64,250 in confirmed repairs", "Imminent HVAC failure 94%", "Hidden mold 86%".

Reproduced offline, bit-for-bit, from the client-side text of the real PDFs
(test_corpus/regression_edgemont/, client name redacted). The mechanisms, each
covered by a test below:

  1. The AI extractor truncated the report to first-7k + last-7k chars and lost every
     finding (all sit in pages 4-14). It returned nothing; nothing was treated as
     "AI failed"; the keyword parser then ran on the FULL text.
  2. The keyword parser manufactured 15 "findings" out of TREC boilerplate.
  3. Unknown categories defaulted to FOUNDATION_STRUCTURE (three places).
  4. Category score = SUM of severity points, so 4 moderate items = "CRITICAL" (>=75),
     which unlocked a hard-coded $25k-60k foundation cost with no critical finding.
  5. The PDF/web "Confirmed repairs" rendered category_scores, not findings.
  6. Predictive engine: any "water" => water stain => mold; "age" in "damage" => old HVAC.
  7. analysis_routes passed result_dict['findings'] (never exists) to the estimator.
"""
import json
import os
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, 'test_corpus', 'regression_edgemont')


def _read(name):
    with open(os.path.join(CORPUS, name), encoding='utf-8') as f:
        return f.read()


INSPECTION = _read('inspection_client.txt')
DISCLOSURE = _read('disclosure_client.txt')

# What a careful read of the Edgemont report yields (the answer key). Used to mock
# the AI extractor so the downstream pipeline is exercised on realistic input.
EDGEMONT_AI_FINDINGS = {"findings": [
    {"category": "general", "severity": "moderate",
     "description": "Attic insulation is missing over the upstairs gameroom and over the breakfast nook, kitchen and dining room ceilings; thermal imaging shows heat transfer through exposed sheetrock.",
     "location": "Attic", "recommendation": "Install insulation.", "safety_concern": False, "requires_specialist": False,
     "source_quote": "The heat on the walls and ceilings indicates a lack of insulation on the attic side of the drywall"},
    {"category": "roof_exterior", "severity": "minor",
     "description": "Caulk is needed at separations between the exterior siding and the horizontal wood trim over the kitchen and breakfast nook windows.",
     "location": "Exterior", "recommendation": "Caulk.", "safety_concern": False, "requires_specialist": False,
     "source_quote": "caulk is needed in separations between the exterior siding and the horizontal wood trim over the kitchen windows"},
    {"category": "roof_exterior", "severity": "moderate",
     "description": "Two large exposed openings in the exterior walls beneath the eaves over the dining room must be covered to prevent rodent entry.",
     "location": "Exterior walls", "recommendation": "Cover the openings.", "safety_concern": False, "requires_specialist": False,
     "source_quote": "There are two large exposed openings in the exterior walls beneath the eaves over the left and right sides of the dining room"},
    {"category": "roof_exterior", "severity": "minor",
     "description": "The escutcheon at the water heater gas vent pipe on the west wall is loose and not covering the opening in the brick.",
     "location": "West exterior wall", "recommendation": "Attach and caulk.", "safety_concern": False, "requires_specialist": False,
     "source_quote": "the escutcheon at the water heater gas vent pipe is loose and not properly covering the opening in the brick wall"},
    {"category": "general", "severity": "minor",
     "description": "The gas fireplace burner could not be operated because no control switch was found on the living room walls.",
     "location": "Living room", "recommendation": "Seller to demonstrate.", "safety_concern": False, "requires_specialist": False,
     "source_quote": "The gas burner in the fireplace could not be operated by the inspector because the control switch was not found"},
    {"category": "hvac_systems", "severity": "minor",
     "description": "The exhaust fan in the laundry room did not function.",
     "location": "Laundry room", "recommendation": "Repair.", "safety_concern": False, "requires_specialist": False,
     "source_quote": "The exhaust fan in the laundry room did not function at the time of the inspection"},
    {"category": "plumbing", "severity": "minor",
     "description": "Leaks in the underground drip tubing of sprinkler zones #6 and #10.",
     "location": "Front yard", "recommendation": "Sprinkler technician to repair.", "safety_concern": False, "requires_specialist": False,
     "source_quote": "In zone #6 of the sprinkler system, there is a leak in the underground drip tubing"},
]}


class _FakeMessage:
    def __init__(self, text):
        self.content = [type('C', (), {'text': text})()]
        self.usage = type('U', (), {'input_tokens': 1, 'output_tokens': 1})()


class _FakeClient:
    def __init__(self, payload):
        outer = self

        class _Messages:
            @staticmethod
            def create(**kw):
                return _FakeMessage(payload)
        self.messages = _Messages()


def _fake_anthropic(payload):
    """Patch anthropic.Anthropic so the AI extractor returns `payload` verbatim."""
    return mock.patch('anthropic.Anthropic', lambda **kw: _FakeClient(payload))


def _buyer():
    from risk_scoring_model import BuyerProfile
    return BuyerProfile(0, 'moderate', '3-7', '', 'somewhat_unique', [])


def _cat_scores(risk):
    return {c.category.value: c for c in risk.category_scores}


# ─────────────────────────────────────────────────────────────────────────────
# 1. The AI extractor must see the WHOLE document
# ─────────────────────────────────────────────────────────────────────────────
class TestAIExtractorSeesWholeDocument(unittest.TestCase):

    def test_split_never_drops_text(self):
        from document_parser import DocumentParser
        p = DocumentParser()
        chunks = p._split_for_ai(INSPECTION)
        self.assertEqual(''.join(chunks), INSPECTION)
        for c in chunks:
            self.assertLessEqual(len(c), p.AI_CHUNK_CHARS)

    def test_whole_report_fits_in_one_call(self):
        from document_parser import DocumentParser
        p = DocumentParser()
        self.assertEqual(len(p._split_for_ai(INSPECTION)), 1)

    def test_prompt_contains_the_findings_pages(self):
        """The old 7k+7k truncation lost pages 4-14. Capture what Claude is sent."""
        from document_parser import DocumentParser
        sent = {}

        class _Capture(_FakeClient):
            def __init__(self):
                class _M:
                    @staticmethod
                    def create(**kw):
                        sent['prompt'] = kw['messages'][0]['content']
                        return _FakeMessage('{"findings": []}')
                self.messages = _M()

        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test'}), \
             mock.patch('anthropic.Anthropic', lambda **kw: _Capture()):
            DocumentParser()._ai_extract_findings(INSPECTION)
        for needle in ('lack of insulation', 'escutcheon', 'sprinkler system, there is a leak',
                       'exhaust fan in the laundry room', 'caulk is needed'):
            self.assertIn(needle, sent['prompt'], f"AI prompt lost the finding: {needle}")

    def test_long_document_is_chunked_on_page_boundaries(self):
        from document_parser import DocumentParser
        p = DocumentParser()
        big = INSPECTION * 4  # ~165k chars
        chunks = p._split_for_ai(big)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(''.join(chunks), big)


# ─────────────────────────────────────────────────────────────────────────────
# 2. "AI found nothing" is an answer, not a failure
# ─────────────────────────────────────────────────────────────────────────────
class TestEmptyAIResultIsAuthoritative(unittest.TestCase):

    def test_no_api_key_returns_none(self):
        from document_parser import DocumentParser
        env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(DocumentParser()._ai_extract_findings(INSPECTION))

    def test_ai_says_clean_means_zero_findings_and_no_rules_fallback(self):
        from document_parser import DocumentParser
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test'}), \
             _fake_anthropic('{"findings": []}'):
            doc = DocumentParser().parse_inspection_report(INSPECTION, '13180 Edgemont')
        self.assertEqual(doc.extraction_method, 'ai')
        self.assertEqual(doc.inspection_findings, [],
                         "AI read the document and found nothing; the keyword parser must not be consulted")

    def test_ai_failure_uses_rules_and_is_labelled(self):
        from document_parser import DocumentParser
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test'}), \
             _fake_anthropic('Sorry, I cannot help with that.'):
            doc = DocumentParser().parse_inspection_report(INSPECTION, '13180 Edgemont')
        self.assertEqual(doc.extraction_method, 'rules_fallback')


# ─────────────────────────────────────────────────────────────────────────────
# 3. The keyword fallback must not manufacture findings from TREC boilerplate
# ─────────────────────────────────────────────────────────────────────────────
class TestRulesFallbackOnRealEdgemontText(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from document_parser import DocumentParser
        p = DocumentParser()
        cls.findings = p._gate_findings_by_provenance(p._extract_problems(INSPECTION), INSPECTION)

    def test_no_foundation_finding(self):
        cats = {f.category.value for f in self.findings}
        self.assertNotIn('foundation_structure', cats, [f.description for f in self.findings])

    def test_no_critical_finding(self):
        self.assertFalse(any(f.severity.value == 'critical' for f in self.findings),
                         [f.description for f in self.findings if f.severity.value == 'critical'])

    def test_boilerplate_is_not_a_finding(self):
        text = ' '.join(f.description.lower() for f in self.findings)
        for phrase in ('texans sustain', 'client understands', 'client agrees', 'standards of practice',
                       'liability', 'arbitrat', 'attorney', 'general deficiencies include',
                       'fungi, molds', 'expressed or implied', 'for which it was intended'):
            self.assertNotIn(phrase, text, f"boilerplate leaked into findings: {phrase!r}")

    def test_real_findings_survive(self):
        text = ' '.join(f.description.lower() for f in self.findings)
        for phrase in ('lack of insulation', 'caulk is needed', 'escutcheon', 'exhaust fan', 'drip tubing'):
            self.assertIn(phrase, text, f"real finding lost: {phrase!r}")

    def test_fallback_count_is_sane(self):
        # The deployed build produced 21 on this text (15 boilerplate). A careful
        # read finds 7-8 items.
        self.assertLessEqual(len(self.findings), 10)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Category defaults never land in FOUNDATION_STRUCTURE
# ─────────────────────────────────────────────────────────────────────────────
class TestNoFoundationDefault(unittest.TestCase):

    def test_rules_categorizer_default_is_general(self):
        from document_parser import DocumentParser, IssueCategory
        p = DocumentParser()
        self.assertEqual(p._categorize_text('The exhaust fan in the laundry room did not function.'), IssueCategory.HVAC)
        self.assertEqual(p._categorize_text('The gas fireplace control switch was not found.'), IssueCategory.GENERAL)
        # substring traps: "adjACent" is not HVAC, "water heater" is plumbing, bare "water" is not
        self.assertEqual(p._categorize_text('there is a leak in the drip tubing adjacent to the corner'), IssueCategory.PLUMBING)
        self.assertEqual(p._categorize_text('rodents can enter through the openings adjacent to the eaves'), IssueCategory.ROOF_EXTERIOR)
        self.assertNotEqual(p._categorize_text('the access door to the crawl area is damaged'), IssueCategory.HVAC)

    def test_ai_unknown_category_is_general(self):
        from document_parser import DocumentParser, IssueCategory
        payload = json.dumps({"findings": [
            {"category": "appliances", "severity": "minor", "description": "Dishwasher door gasket torn.",
             "source_quote": "Dishwasher"},
            {"category": "", "severity": "minor", "description": "Something else.", "source_quote": "x"},
        ]})
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test'}), _fake_anthropic(payload):
            found = DocumentParser()._ai_extract_findings("Dishwasher door gasket torn. Something else.")
        self.assertTrue(all(f.category == IssueCategory.GENERAL for f in found))

    def test_risk_model_unknown_category_is_general(self):
        from document_parser import InspectionFinding, Severity
        from risk_scoring_model import RiskScoringModel, RiskCategory
        f = InspectionFinding(category='mystery', severity=Severity.MODERATE, location='',
                              description='x', recommendation='')
        grouped = RiskScoringModel()._group_by_category([f])
        self.assertEqual(grouped[RiskCategory.GENERAL], [f])
        self.assertEqual(grouped[RiskCategory.FOUNDATION_STRUCTURE], [])


# ─────────────────────────────────────────────────────────────────────────────
# 5. Score is bounded by the worst finding; cost is per finding, never per score
# ─────────────────────────────────────────────────────────────────────────────
class TestSeverityCeilingAndCosts(unittest.TestCase):

    def _f(self, cat, sev, desc='Finding text long enough to count.'):
        from document_parser import InspectionFinding, IssueCategory, Severity
        return InspectionFinding(category=cat, severity=sev, location='', description=desc, recommendation='')

    def test_many_moderate_findings_never_read_as_critical(self):
        from document_parser import IssueCategory, Severity
        from risk_scoring_model import RiskScoringModel
        fs = [self._f(IssueCategory.FOUNDATION_STRUCTURE, Severity.MODERATE, f'Hairline crack {i}') for i in range(6)]
        risk = RiskScoringModel().calculate_risk_score(fs, None, 850000, _buyer())
        cs = _cat_scores(risk)['foundation_structure']
        self.assertLess(cs.score, 50)
        # 6 moderate foundation repairs at national baseline, NOT a $25k-60k "critical" floor
        self.assertLess(cs.estimated_cost_high, 60000)
        self.assertEqual(risk.deal_breakers, [])

    def test_minor_only_category_stays_minor(self):
        from document_parser import IssueCategory, Severity
        from risk_scoring_model import RiskScoringModel
        fs = [self._f(IssueCategory.HVAC, Severity.MINOR, f'Fan {i} noisy') for i in range(10)]
        cs = _cat_scores(RiskScoringModel().calculate_risk_score(fs, None, 850000, _buyer()))['hvac_systems']
        self.assertLessEqual(cs.score, 24)

    def test_a_real_critical_finding_still_scores_critical(self):
        from document_parser import IssueCategory, Severity
        from risk_scoring_model import RiskScoringModel
        fs = [self._f(IssueCategory.FOUNDATION_STRUCTURE, Severity.CRITICAL,
                      'Significant differential settlement with 1/2 inch cracks; structural engineer required.')]
        cs = _cat_scores(RiskScoringModel().calculate_risk_score(fs, None, 850000, _buyer()))['foundation_structure']
        self.assertGreaterEqual(cs.score, 60)
        self.assertGreaterEqual(cs.estimated_cost_high, 20000)

    def test_cost_is_zero_without_findings(self):
        from risk_scoring_model import RiskScoringModel
        risk = RiskScoringModel().calculate_risk_score([], None, 850000, _buyer())
        self.assertEqual(risk.total_repair_cost_high, 0)
        self.assertTrue(all(c.estimated_cost_high == 0 for c in risk.category_scores))

    def test_key_issues_list_every_finding(self):
        from document_parser import IssueCategory, Severity
        from risk_scoring_model import RiskScoringModel
        fs = [self._f(IssueCategory.PLUMBING, Severity.MINOR, 'Drip tubing leak at zone six of the sprinklers.'),
              self._f(IssueCategory.PLUMBING, Severity.MODERATE, 'Escutcheon loose at the vent pipe on the west wall.')]
        cs = _cat_scores(RiskScoringModel().calculate_risk_score(fs, None, 850000, _buyer()))['plumbing']
        self.assertEqual(len(cs.key_issues), 2)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Predictive engine must not invent stains or old HVAC
# ─────────────────────────────────────────────────────────────────────────────
class TestPredictiveEngineHeuristics(unittest.TestCase):

    def _f(self, cat, desc):
        from document_parser import InspectionFinding, Severity
        return InspectionFinding(category=cat, severity=Severity.MINOR, location='', description=desc, recommendation='')

    def test_water_heater_vent_is_not_a_water_stain(self):
        from document_parser import IssueCategory
        from predictive_engine import PredictiveIssueEngine
        e = PredictiveIssueEngine()
        f = self._f(IssueCategory.ROOF_EXTERIOR, 'The escutcheon at the water heater gas vent pipe is loose.')
        self.assertNotEqual(e._normalize_finding_type(f), 'water_stain')
        self.assertEqual(e.predict_hidden_issues([f], {'age': 0}), [])

    def test_damage_is_not_age(self):
        from document_parser import IssueCategory
        from predictive_engine import PredictiveIssueEngine
        e = PredictiveIssueEngine()
        for desc in ('Damage to the duct in the garage.', 'Leakage at the drainage line near the storage area.'):
            f = self._f(IssueCategory.HVAC, desc)
            self.assertNotEqual(e._normalize_finding_type(f), 'old_hvac', desc)

    def test_real_stain_and_real_old_hvac_still_detected(self):
        from document_parser import IssueCategory
        from predictive_engine import PredictiveIssueEngine
        e = PredictiveIssueEngine()
        self.assertEqual(e._normalize_finding_type(self._f(IssueCategory.ROOF_EXTERIOR, 'Water stains on the ceiling below the bathroom.')), 'water_stain')
        self.assertEqual(e._normalize_finding_type(self._f(IssueCategory.HVAC, 'The furnace is 24 years old and past its expected service life.')), 'old_hvac')

    def test_negated_stain_is_not_a_stain(self):
        from document_parser import IssueCategory
        from predictive_engine import PredictiveIssueEngine
        f = self._f(IssueCategory.ROOF_EXTERIOR, 'No evidence of moisture or water stains was observed.')
        self.assertNotEqual(PredictiveIssueEngine()._normalize_finding_type(f), 'water_stain')

    def test_prediction_reasoning_cites_the_observed_finding(self):
        from document_parser import IssueCategory
        from predictive_engine import PredictiveIssueEngine
        f = self._f(IssueCategory.ROOF_EXTERIOR, 'Water stains on the ceiling below the upstairs bathroom.')
        preds = PredictiveIssueEngine().predict_hidden_issues([f], {'age': 0})
        self.assertTrue(preds)
        self.assertIn('Water stains on the ceiling', preds[0].observable_indicators[0])


# ─────────────────────────────────────────────────────────────────────────────
# 7. End to end on the real deal — both extraction paths
# ─────────────────────────────────────────────────────────────────────────────
class TestEdgemontEndToEnd(unittest.TestCase):

    def _run(self):
        from offerwise_intelligence import OfferWiseIntelligence
        oi = OfferWiseIntelligence()
        oi.ai_helper.enabled = False
        return oi.analyze_property(DISCLOSURE, INSPECTION, 850000, _buyer(),
                                   '13180 Edgemont Ln, Frisco, TX 75035', None)

    def _assert_no_fabrication(self, r):
        cs = _cat_scores(r.risk_score)
        self.assertEqual(cs['foundation_structure'].score, 0, "foundation must be untouched")
        self.assertEqual(cs['foundation_structure'].estimated_cost_high, 0)
        self.assertFalse(any(c.score >= 75 for c in r.risk_score.category_scores), "no CRITICAL category")
        self.assertLess(r.risk_score.total_repair_cost_high, 25000)
        self.assertEqual(r.risk_score.deal_breakers, [])
        self.assertEqual(list(r.predicted_issues or []), [], "no invented HVAC-failure / mold reserve")
        self.assertGreater(r.offer_strategy['recommended_offer'], 820000)

    def test_rules_fallback_path(self):
        env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
        with mock.patch.dict(os.environ, env, clear=True):
            r = self._run()
        self.assertEqual(r.inspection_report.extraction_method, 'rules_fallback')
        self._assert_no_fabrication(r)

    def test_ai_path(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test'}), \
             _fake_anthropic(json.dumps(EDGEMONT_AI_FINDINGS)):
            r = self._run()
        self.assertEqual(r.inspection_report.extraction_method, 'ai')
        self.assertEqual(len(r.inspection_report.inspection_findings), 7)
        self.assertTrue(all(f.verified for f in r.inspection_report.inspection_findings))
        self._assert_no_fabrication(r)

    def test_repair_estimate_is_built_from_findings(self):
        """analysis_routes now passes inspection_report.inspection_findings (not the
        non-existent result_dict['findings']). Mirror that call here."""
        from dataclasses import asdict
        from repair_cost_estimator import estimate_repair_costs
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test'}), \
             _fake_anthropic(json.dumps(EDGEMONT_AI_FINDINGS)):
            r = self._run()
        fd = []
        for f in r.inspection_report.inspection_findings:
            d = asdict(f)
            d['category'] = f.category.value
            d['severity'] = f.severity.value
            fd.append(d)
        est = estimate_repair_costs('75035', fd, [], r.risk_score.total_repair_cost_low,
                                    r.risk_score.total_repair_cost_high, None)
        self.assertGreater(len(est['breakdown']), 0)
        systems = ' '.join(b['system'].lower() + ' ' + b['category'] for b in est['breakdown'])
        self.assertNotIn('foundation', systems)
        self.assertEqual(est['total_low'], sum(b['low'] for b in est['breakdown']))
        self.assertLess(est['total_high'], 25000)

    def test_analysis_routes_reads_findings_from_inspection_report(self):
        with open(os.path.join(HERE, 'analysis_routes.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn("findings=result_dict.get('findings', [])", src)
        self.assertIn("inspection_findings", src)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Report rendering + cache
# ─────────────────────────────────────────────────────────────────────────────
class TestRenderAndCache(unittest.TestCase):

    def test_pdf_confirmed_repairs_prefers_breakdown_and_requires_findings(self):
        with open(os.path.join(HERE, 'static', 'app.html'), encoding='utf-8') as f:
            src = f.read()
        i = src.index('SECTION 3 (CONFIRMED REPAIRS)')
        block = src[i:i + 6000]
        self.assertIn('result.repair_estimate', block)
        self.assertIn('_hasFindings', block)
        self.assertNotIn('score >= 50 || c.safety_concern', block,
                         "a category must never become a confirmed repair on score alone")

    def test_cache_version_tracks_build(self):
        import analysis_cache
        with open(os.path.join(HERE, 'VERSION')) as f:
            self.assertEqual(analysis_cache.ANALYSIS_VERSION, f.read().strip())


if __name__ == '__main__':
    unittest.main()


# ─────────────────────────────────────────────────────────────────────────────
# 9. v5.89.339 — the second screenshot round: Syracuse/NY, "Other Items", $31K vs $17-29K
# ─────────────────────────────────────────────────────────────────────────────
class TestZipExtraction(unittest.TestCase):
    """'13180 Edgemont Ln, Frisco, Texas 75035' was priced at Syracuse rates and
    permitted for Frisco, NY because every ZIP regex took the FIRST 5-digit token —
    the house number (131xx = Syracuse)."""

    def test_house_number_is_not_the_zip(self):
        from address_utils import extract_zip
        self.assertEqual(extract_zip('13180 Edgemont Ln, Frisco, Texas 75035'), '75035')
        self.assertEqual(extract_zip('13180 Edgemont Ln, Frisco, TX 75035-1234'), '75035')
        self.assertEqual(extract_zip('13180 Edgemont Ln, Frisco, Texas'), '')
        self.assertEqual(extract_zip('2839 Pendleton Dr, San Jose, CA 95148'), '95148')
        self.assertEqual(extract_zip('75035'), '75035')
        self.assertEqual(extract_zip(''), '')
        self.assertEqual(extract_zip(None), '')

    def test_edgemont_resolves_to_dallas_and_texas(self):
        from address_utils import extract_zip
        from repair_cost_estimator import _get_zip_multiplier
        from state_disclosures import detect_state_from_zip
        z = extract_zip('13180 Edgemont Ln, Frisco, Texas 75035')
        self.assertEqual(_get_zip_multiplier(z)[1], 'Dallas')
        self.assertEqual(detect_state_from_zip(z), 'TX')

    def test_jurisdiction_resolver_uses_the_real_zip(self):
        from jurisdiction_resolver import resolve_report_jurisdiction
        rj = resolve_report_jurisdiction({}, address='13180 Edgemont Ln, Frisco, Texas 75035')
        self.assertEqual(rj['zip_code'], '75035')
        self.assertEqual(rj['jurisdiction'], 'TX')

    def test_no_first_token_zip_regex_left_in_the_analysis_path(self):
        import re
        offenders = []
        for fn in ('analysis_routes.py', 'offerwise_intelligence.py', 'market_intelligence.py',
                   'property_research_agent.py', 'jurisdiction_resolver.py', 'ml_data_collector.py'):
            with open(os.path.join(HERE, fn), encoding='utf-8') as f:
                for i, line in enumerate(f, 1):
                    if re.search(r"re2?\.search\(r'\\b\(\\d\{5\}\)", line) or re.search(r"_ZIP_RE\.search\(", line):
                        offenders.append(f'{fn}:{i}')
        self.assertEqual(offenders, [])


class TestMLNeverOverridesToGeneral(unittest.TestCase):
    def test_general_from_classifier_is_ignored(self):
        with open(os.path.join(HERE, 'offerwise_intelligence.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn('if ml_cat is IssueCategory.GENERAL:', src)


class TestRepairNumbersReconcile(unittest.TestCase):
    """Header "$31K avg" vs card "$17K-$29K" vs line "$17K-$49K": three numbers for one
    set of findings. Now: risk-model category costs, the offer math and the itemized
    breakdown are the same per-finding, metro-adjusted numbers."""

    def _run(self):
        from offerwise_intelligence import OfferWiseIntelligence
        oi = OfferWiseIntelligence()
        oi.ai_helper.enabled = False
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test'}), \
             _fake_anthropic(json.dumps(EDGEMONT_AI_FINDINGS)):
            return oi.analyze_property(DISCLOSURE, INSPECTION, 850000, _buyer(),
                                       '13180 Edgemont Ln, Frisco, Texas 75035', None)

    def test_breakdown_total_equals_risk_total_equals_offer_math(self):
        from dataclasses import asdict
        from repair_cost_estimator import estimate_repair_costs
        r = self._run()
        fd = []
        for f in r.inspection_report.inspection_findings:
            d = asdict(f); d['category'] = f.category.value; d['severity'] = f.severity.value; fd.append(d)
        est = estimate_repair_costs('75035', fd, [], r.risk_score.total_repair_cost_low,
                                    r.risk_score.total_repair_cost_high, None)
        self.assertEqual(est['metro_area'], 'Dallas')
        self.assertEqual(est['total_low'], sum(b['low'] for b in est['breakdown']))
        self.assertEqual(est['total_high'], sum(b['high'] for b in est['breakdown']))
        self.assertAlmostEqual(est['total_low'], r.risk_score.total_repair_cost_low, delta=len(fd))
        self.assertAlmostEqual(est['total_high'], r.risk_score.total_repair_cost_high, delta=len(fd))
        math_repairs = r.offer_strategy['discount_breakdown']['repair_costs']
        self.assertAlmostEqual(math_repairs, (r.risk_score.total_repair_cost_low + r.risk_score.total_repair_cost_high) / 2, delta=1)

    def test_findings_are_not_all_general(self):
        r = self._run()
        cats = {f.category.value for f in r.inspection_report.inspection_findings}
        self.assertIn('roof_exterior', cats)
        self.assertIn('plumbing', cats)

    def test_finding_cost_used_as_is_not_blended(self):
        from repair_cost_estimator import estimate_repair_costs
        est = estimate_repair_costs('75035', [{'category': 'plumbing', 'severity': 'minor',
                                               'description': 'Drip tubing leak in zone 6 of the sprinklers',
                                               'estimated_cost_low': 300, 'estimated_cost_high': 500}], [], 0, 0, None)
        self.assertEqual((est['breakdown'][0]['low'], est['breakdown'][0]['high']), (300, 500))
        self.assertEqual((est['total_low'], est['total_high']), (300, 500))


class TestPermitsOnlyForCategoriesWithFindings(unittest.TestCase):
    def test_route_filters_permit_categories(self):
        with open(os.path.join(HERE, 'analysis_routes.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn('repair_breakdown=_permit_items', src)
        self.assertNotIn("repair_breakdown=risk_score_data.get('category_scores', [])", src)


# ─────────────────────────────────────────────────────────────────────────────
# 10. v5.89.340 — never print a cut-off sentence
# ─────────────────────────────────────────────────────────────────────────────
class TestNoTruncatedFindings(unittest.TestCase):
    LONG = ('The gas burner in the fireplace could not be operated by the inspector because the '
            'control switch was not found anywhere on the living room walls, so the homeowner should '
            'be asked to demonstrate proper operation of the fireplace before closing.')

    def test_estimator_keeps_every_sentence_whole(self):
        from repair_cost_estimator import estimate_repair_costs
        fs = [{'category': 'general', 'severity': 'minor', 'description': self.LONG, 'trade': 'fireplace_chimney'},
              {'category': 'general', 'severity': 'minor', 'trade': 'fireplace_chimney', 'description': 'The chimney cap is missing and the flue damper does not close fully.'},
              {'category': 'general', 'severity': 'minor', 'trade': 'fireplace_chimney', 'description': 'Third finding sentence that is also complete.'},
              {'category': 'general', 'severity': 'minor', 'trade': 'fireplace_chimney', 'description': 'Fourth finding sentence that must not be dropped.'}]
        est = estimate_repair_costs('75035', fs, [], 0, 0, None)
        item = est['breakdown'][0]
        self.assertEqual(len(item['findings']), 4)
        self.assertIn(self.LONG, item['findings'])
        self.assertIn(self.LONG, item['description'])
        self.assertNotIn('issues:', item['description'])

    def test_risk_model_key_issues_are_whole(self):
        from document_parser import InspectionFinding, IssueCategory, Severity
        from risk_scoring_model import RiskScoringModel
        f = InspectionFinding(category=IssueCategory.GENERAL, severity=Severity.MINOR, location='',
                              description=self.LONG, recommendation='')
        cs = _cat_scores(RiskScoringModel().calculate_risk_score([f], None, 850000, _buyer()))['general']
        self.assertEqual(cs.key_issues, [self.LONG])

    def test_renderers_do_not_slice_findings(self):
        with open(os.path.join(HERE, 'static', 'app.html'), encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('item.description.slice(0, 80)', src)
        self.assertNotIn("c.issues.slice(0, 4)", src)
        self.assertNotIn("s.slice(0, 70) + '…'", src)
        i = src.index('SECTION 3 (CONFIRMED REPAIRS)')
        self.assertIn('b.findings', src[i:i + 6000])


# ─────────────────────────────────────────────────────────────────────────────
# 11. v5.89.341 — trades, and no cross-customer "learning"
# ─────────────────────────────────────────────────────────────────────────────
EDGEMONT_TRADES = ['attic_insulation', 'exterior_walls_trim', 'exterior_walls_trim',
                   'exterior_walls_trim', 'fireplace_chimney', 'ventilation_fans', 'irrigation_grounds']
EDGEMONT_AI_WITH_TRADES = {"findings": [dict(f, trade=t) for f, t in
                                        zip(EDGEMONT_AI_FINDINGS['findings'], EDGEMONT_TRADES)]}


class TestTrades(unittest.TestCase):
    def _run(self, payload):
        from offerwise_intelligence import OfferWiseIntelligence
        oi = OfferWiseIntelligence()
        oi.ai_helper.enabled = False
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test'}), \
             _fake_anthropic(json.dumps(payload)):
            return oi.analyze_property(DISCLOSURE, INSPECTION, 850000, _buyer(),
                                       '13180 Edgemont Ln, Frisco, Texas 75035', None)

    def _breakdown(self, r):
        from dataclasses import asdict
        from repair_cost_estimator import estimate_repair_costs
        fd = []
        for f in r.inspection_report.inspection_findings:
            d = asdict(f); d['category'] = f.category.value; d['severity'] = f.severity.value; fd.append(d)
        return estimate_repair_costs('75035', fd, [], r.risk_score.total_repair_cost_low,
                                     r.risk_score.total_repair_cost_high, None)

    def test_items_are_grouped_by_trade_with_buyer_facing_labels(self):
        est = self._breakdown(self._run(EDGEMONT_AI_WITH_TRADES))
        systems = {b['system']: b for b in est['breakdown']}
        self.assertIn('Exterior Walls & Trim', systems)
        self.assertEqual(systems['Exterior Walls & Trim']['issue_count'], 3)   # caulk, openings, escutcheon
        self.assertIn('Attic & Insulation', systems)
        self.assertIn('Fireplace & Chimney', systems)
        self.assertIn('Ventilation & Exhaust Fans', systems)
        self.assertIn('Irrigation & Grounds', systems)
        for bad in ('Roof Exterior', 'Other Items', 'Plumbing', 'Hvac Systems'):
            self.assertNotIn(bad, systems)

    def test_trade_derives_the_risk_category(self):
        r = self._run(EDGEMONT_AI_WITH_TRADES)
        by_trade = {f.trade: f.category.value for f in r.inspection_report.inspection_findings}
        self.assertEqual(by_trade['exterior_walls_trim'], 'roof_exterior')
        self.assertEqual(by_trade['ventilation_fans'], 'hvac_systems')
        self.assertEqual(by_trade['attic_insulation'], 'general')

    def test_trade_costs_are_realistic_and_reconcile(self):
        r = self._run(EDGEMONT_AI_WITH_TRADES)
        est = self._breakdown(r)
        systems = {b['system']: b for b in est['breakdown']}
        self.assertLess(systems['Ventilation & Exhaust Fans']['high'], 1000)   # a bath fan, not an HVAC unit
        self.assertLess(systems['Irrigation & Grounds']['high'], 1000)         # drip tubing, not a repipe
        self.assertLess(est['total_high'], 10000)
        self.assertAlmostEqual(est['total_low'], r.risk_score.total_repair_cost_low, delta=8)
        self.assertAlmostEqual(est['total_high'], r.risk_score.total_repair_cost_high, delta=8)

    def test_missing_trade_is_derived_from_the_sentence(self):
        from trades import trade_for_text
        self.assertEqual(trade_for_text('The escutcheon at the water heater gas vent pipe is loose.'), 'exterior_walls_trim')
        self.assertEqual(trade_for_text('A leak was observed in the underground drip tubing of zone #6 of the sprinkler system.'), 'irrigation_grounds')
        self.assertEqual(trade_for_text('The exhaust fan in the laundry room did not function.'), 'ventilation_fans')
        self.assertEqual(trade_for_text('Thermal imaging revealed areas lacking insulation on the attic side of drywall.'), 'attic_insulation')
        self.assertEqual(trade_for_text('The gas burner in the fireplace could not be operated.'), 'fireplace_chimney')
        self.assertEqual(trade_for_text('Caulking is needed at separations between the exterior siding and the wood trim.'), 'exterior_walls_trim')

    def test_rules_fallback_assigns_trades_too(self):
        from document_parser import DocumentParser
        p = DocumentParser()
        fs = p._gate_findings_by_provenance(p._extract_problems(INSPECTION), INSPECTION)
        self.assertTrue(all(getattr(f, 'trade', '') for f in fs))
        self.assertIn('irrigation_grounds', {f.trade for f in fs})


class TestNoCrossCustomerLearning(unittest.TestCase):
    def test_second_analysis_on_same_engine_is_identical_to_the_first(self):
        """One long-lived worker serves many customers. The previous house must never
        become a 'prediction' for the next one."""
        from offerwise_intelligence import OfferWiseIntelligence
        oi = OfferWiseIntelligence()
        oi.ai_helper.enabled = False
        results = []
        for _ in range(3):
            with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test'}), \
                 _fake_anthropic(json.dumps(EDGEMONT_AI_FINDINGS)):
                r = oi.analyze_property(DISCLOSURE, INSPECTION, 850000, _buyer(),
                                        '13180 Edgemont Ln, Frisco, Texas 75035', None)
            results.append((round(r.offer_strategy['recommended_offer']),
                            len(r.predicted_issues or []),
                            r.offer_strategy['discount_breakdown'].get('risk_premium', 0)))
        self.assertEqual(len(set(results)), 1, results)
        self.assertEqual(results[0][1], 0)
        self.assertEqual(results[0][2], 0)


# ─────────────────────────────────────────────────────────────────────────────
# 12. v5.89.342 — no "permit lookup temporarily unavailable" filler
# ─────────────────────────────────────────────────────────────────────────────
class TestPermitFillerGone(unittest.TestCase):
    def test_failed_lookup_yields_no_rows(self):
        import permit_lookup
        with mock.patch.object(permit_lookup, '_split_by_cache', return_value=([], [{'category': 'roof_exterior', 'score': 30}])), \
             mock.patch.object(permit_lookup, '_llm_batch_lookup', side_effect=RuntimeError('no client')):
            res = permit_lookup.lookup_permits([{'category': 'roof_exterior', 'score': 30}],
                                               {'state': 'TX', 'city': 'Frisco', 'zip': '75035'})
        self.assertEqual(res['findings'], [])
        self.assertEqual(res['undetermined'], 1)
        self.assertNotIn('temporarily unavailable', json.dumps(res))

    def test_uncertain_rows_are_filtered_even_from_cache(self):
        import permit_lookup
        cached = [{'system_key': 'roof', 'system': 'roof', 'permit_required': 'uncertain', 'confidence': 'low'},
                  {'system_key': 'electrical', 'system': 'electrical', 'permit_required': 'required',
                   'confidence': 'high', 'permit_cost_low': 100, 'permit_cost_high': 300}]
        with mock.patch.object(permit_lookup, '_split_by_cache', return_value=(cached, [])):
            res = permit_lookup.lookup_permits([{'category': 'roof'}, {'category': 'electrical'}],
                                               {'state': 'TX', 'city': 'Frisco', 'zip': '75035'})
        self.assertEqual([f['permit_required'] for f in res['findings']], ['required'])

    def test_web_view_filters_legacy_uncertain_rows(self):
        with open(os.path.join(HERE, 'static', 'app.html'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn("['required', 'likely', 'not_required'].includes(p.permit_required)", src)


# ─────────────────────────────────────────────────────────────────────────────
# 13. v5.89.343 — the permit lookup never ran: the client getter did not exist
# ─────────────────────────────────────────────────────────────────────────────
class TestPermitLookupActuallyRuns(unittest.TestCase):
    def test_ai_client_exposes_get_anthropic_client(self):
        """permit_lookup imports this name; before .343 the import silently fell back
        to a stub returning None and the feature never made a single call."""
        import ai_client
        self.assertTrue(callable(getattr(ai_client, 'get_anthropic_client', None)))
        import permit_lookup
        self.assertIs(permit_lookup.get_anthropic_client, ai_client.get_anthropic_client)

    def test_client_is_none_without_key_and_real_with_key(self):
        import ai_client
        env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(ai_client.get_anthropic_client())
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test'}):
            self.assertIsNotNone(ai_client.get_anthropic_client())

    def test_lookup_makes_the_call_with_trade_and_findings(self):
        import permit_lookup
        seen = {}

        class _C:
            class messages:
                @staticmethod
                def create(**kw):
                    seen['prompt'] = kw['messages'][0]['content']
                    return _FakeMessage(json.dumps([
                        {'system_key': 'exterior_walls_trim:minor', 'system': 'Exterior Walls & Trim',
                         'permit_required': 'not_required', 'confidence': 'high',
                         'permit_cost_low': None, 'permit_cost_high': None, 'consequences': 'Routine maintenance is exempt.'},
                        {'system_key': 'water_heater:major', 'system': 'Water Heater',
                         'permit_required': 'required', 'confidence': 'high',
                         'permit_cost_low': 75, 'permit_cost_high': 150, 'consequences': 'Gas appliance replacement needs a mechanical permit.'},
                    ]))
        items = [
            {'trade': 'exterior_walls_trim', 'category': 'Exterior Walls & Trim', 'system': 'Exterior Walls & Trim',
             'severity': 'minor', 'estimated_cost_low': 250, 'estimated_cost_high': 420,
             'findings': ['Caulk is needed at separations between the siding and the trim.']},
            {'trade': 'water_heater', 'category': 'Water Heater', 'system': 'Water Heater',
             'severity': 'major', 'estimated_cost_low': 1700, 'estimated_cost_high': 2900,
             'findings': ['The tankless water heater is leaking at the heat exchanger and needs replacement.']},
        ]
        with mock.patch.object(permit_lookup, '_cache_get', return_value=None), \
             mock.patch.object(permit_lookup, '_cache_set'), \
             mock.patch.object(permit_lookup, 'get_anthropic_client', return_value=_C()):
            res = permit_lookup.lookup_permits(items, {'state': 'TX', 'city': 'Frisco', 'zip': '75035'})
        self.assertIn('Caulk is needed', seen['prompt'])
        self.assertIn('exterior_walls_trim:minor', seen['prompt'])
        self.assertIn('Frisco, TX', seen['prompt'])
        self.assertEqual([f['permit_required'] for f in res['findings']], ['not_required', 'required'])
        self.assertEqual((res['total_low'], res['total_high']), (75, 150))

    def test_route_feeds_the_trade_breakdown(self):
        with open(os.path.join(HERE, 'analysis_routes.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn("repair_breakdown=_permit_items", src)
        self.assertIn("'trade': b.get('trade')", src)

    def test_web_view_shows_only_permit_needed_rows(self):
        with open(os.path.join(HERE, 'static', 'app.html'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn('permitNeeded.map((p, idx)', src)
        self.assertIn('No permit needed for:', src)
