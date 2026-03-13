"""
OfferWise AI Output Validator Test Suite
========================================
Tests that the validator catches every category of AI misbehavior
before it reaches users.

Groups:
  1. Trust Score Bounds — clamped to [0, 100]
  2. Grade Validation — must be A-F
  3. Red Flag Structure — required fields, severity enum
  4. Evidence Grounding — evidence must appear in source document
  5. Evasion Phrase Grounding — phrases must be verbatim from document
  6. Cross-Reference Validation — type/severity enums, confidence bounds
  7. Severity Rating Validation — issue IDs must match originals
  8. Clean Outputs Pass — valid AI output produces zero violations
  9. Truncation Detection — no "..." in detail fields
 10. Edge Cases — empty inputs, None values, wrong types
"""

import unittest
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_output_validator import (
    validate_truth_check,
    validate_cross_reference_findings,
    validate_severity_ratings,
    _check_grounding,
    log_ai_call,
    VALID_GRADES,
    VALID_SEVERITIES,
)


# ===========================================================================
# FIXTURES
# ===========================================================================

SAMPLE_PDF_TEXT = """
Real Estate Transfer Disclosure Statement
The seller discloses that the property located at 381 Tina Dr, Hollister CA 95023
has the following items: Range, Oven, Microwave, Dishwasher, Garbage Disposal.
Section B: The seller is aware of the following defects:
Interior walls - some cracks observed in bedroom 2.
Roof: tile roof approximately 35 years old. Minor wear noted.
Section C: Environmental hazards: Seller is not aware of any asbestos or lead paint.
Flooding: No
Earthquake damage: No
Room additions: Yes - converted garage without permit in 2019.
"""

def _good_truth_check():
    """A valid truth-check response."""
    return {
        'trust_score': 62,
        'grade': 'C',
        'red_flags': [
            {
                'title': 'Unpermitted garage conversion disclosed',
                'detail': 'The seller admits to converting the garage without a permit in 2019. This could affect the property valuation and insurability.',
                'severity': 'high',
                'category': 'permits',
                'evidence': 'Room additions: Yes - converted garage without permit in 2019',
            },
            {
                'title': 'Aging roof with minor wear',
                'detail': 'The roof is described as tile and approximately 35 years old with minor wear. This suggests replacement may be needed soon.',
                'severity': 'medium',
                'category': 'roof',
                'evidence': 'Roof: tile roof approximately 35 years old. Minor wear noted.',
            }
        ],
        'blank_unknown_count': 3,
        'evasion_phrases': ['is not aware of any'],
        'most_concerning': 'The unpermitted garage conversion poses the greatest risk, as it could require costly remediation or reduce resale value.',
        'overall_assessment': 'This disclosure reveals an unpermitted conversion and an aging roof. While the seller is relatively forthcoming about the garage issue, buyers should verify permit status with the county.',
    }


def _good_cross_ref_findings():
    """Valid cross-reference findings."""
    return [
        {
            'type': 'contradiction',
            'severity': 'high',
            'title': 'Seller denies flooding but FEMA shows flood zone',
            'detail': 'The seller answered No to flooding history, but FEMA NFHL data shows the property is in Zone AE, a Special Flood Hazard Area.',
            'source': 'FEMA NFHL',
            'category': 'water_damage',
            'confidence': 0.92,
        },
        {
            'type': 'omission',
            'severity': 'medium',
            'title': 'No mention of earthquake fault proximity',
            'detail': 'The disclosure does not mention proximity to the Calaveras fault, which is mapped within 10km.',
            'source': 'CGS Fault Map',
            'category': 'environmental',
            'confidence': 0.75,
        }
    ]


# ===========================================================================
# GROUP 1: TRUST SCORE BOUNDS
# ===========================================================================

class TestTrustScoreBounds(unittest.TestCase):

    def test_valid_score_passes(self):
        raw = _good_truth_check()
        out, violations = validate_truth_check(raw)
        score_violations = [v for v in violations if 'TRUST_SCORE' in v['code']]
        self.assertEqual(len(score_violations), 0)
        self.assertEqual(out['trust_score'], 62)

    def test_score_above_100_clamped(self):
        raw = _good_truth_check()
        raw['trust_score'] = 150
        out, violations = validate_truth_check(raw)
        self.assertEqual(out['trust_score'], 100)
        self.assertTrue(any('OUT_OF_BOUNDS' in v['code'] for v in violations))

    def test_score_below_zero_clamped(self):
        raw = _good_truth_check()
        raw['trust_score'] = -20
        out, violations = validate_truth_check(raw)
        self.assertEqual(out['trust_score'], 0)

    def test_score_missing_defaults_to_50(self):
        raw = _good_truth_check()
        del raw['trust_score']
        out, violations = validate_truth_check(raw)
        self.assertEqual(out['trust_score'], 50)
        self.assertTrue(any('MISSING' in v['code'] for v in violations))

    def test_score_string_type_rejected(self):
        raw = _good_truth_check()
        raw['trust_score'] = "seventy"
        out, violations = validate_truth_check(raw)
        self.assertEqual(out['trust_score'], 50)
        self.assertTrue(any('TYPE' in v['code'] for v in violations))

    def test_score_zero_is_valid(self):
        raw = _good_truth_check()
        raw['trust_score'] = 0
        out, violations = validate_truth_check(raw)
        score_violations = [v for v in violations if 'TRUST_SCORE' in v['code']]
        self.assertEqual(len(score_violations), 0)
        self.assertEqual(out['trust_score'], 0)

    def test_score_100_is_valid(self):
        raw = _good_truth_check()
        raw['trust_score'] = 100
        out, violations = validate_truth_check(raw)
        score_violations = [v for v in violations if 'TRUST_SCORE' in v['code']]
        self.assertEqual(len(score_violations), 0)


# ===========================================================================
# GROUP 2: GRADE VALIDATION
# ===========================================================================

class TestGradeValidation(unittest.TestCase):

    def test_valid_grades_pass(self):
        for grade in ('A', 'B', 'C', 'D', 'F'):
            raw = _good_truth_check()
            raw['grade'] = grade
            out, violations = validate_truth_check(raw)
            grade_violations = [v for v in violations if 'GRADE' in v['code']]
            self.assertEqual(len(grade_violations), 0, f"Grade {grade} should be valid")

    def test_invalid_grade_rejected(self):
        for bad_grade in ('Z', 'E', 'AA', '1', '', 'excellent'):
            raw = _good_truth_check()
            raw['grade'] = bad_grade
            out, violations = validate_truth_check(raw)
            self.assertEqual(out['grade'], 'C', f"Bad grade '{bad_grade}' should default to C")
            self.assertTrue(any('GRADE' in v['code'] for v in violations))


# ===========================================================================
# GROUP 3: RED FLAG STRUCTURE
# ===========================================================================

class TestRedFlagStructure(unittest.TestCase):

    def test_valid_flags_pass(self):
        raw = _good_truth_check()
        out, violations = validate_truth_check(raw)
        flag_violations = [v for v in violations if 'RED_FLAG' in v['code']]
        self.assertEqual(len(flag_violations), 0)

    def test_missing_required_field_caught(self):
        raw = _good_truth_check()
        raw['red_flags'] = [{'title': 'Test', 'severity': 'high'}]  # missing detail, evidence
        out, violations = validate_truth_check(raw)
        self.assertTrue(any('MISSING_FIELD' in v['code'] for v in violations))

    def test_invalid_severity_defaulted(self):
        raw = _good_truth_check()
        raw['red_flags'][0]['severity'] = 'catastrophic'
        out, violations = validate_truth_check(raw)
        self.assertEqual(out['red_flags'][0]['severity'], 'medium')
        self.assertTrue(any('INVALID_SEVERITY' in v['code'] for v in violations))

    def test_all_valid_severities_pass(self):
        for sev in VALID_SEVERITIES:
            raw = _good_truth_check()
            raw['red_flags'][0]['severity'] = sev
            out, violations = validate_truth_check(raw)
            sev_violations = [v for v in violations if 'SEVERITY' in v['code']]
            self.assertEqual(len(sev_violations), 0, f"Severity '{sev}' should be valid")

    def test_red_flags_not_list_handled(self):
        raw = _good_truth_check()
        raw['red_flags'] = "found 3 issues"
        out, violations = validate_truth_check(raw)
        self.assertEqual(out['red_flags'], [])
        self.assertTrue(any('NOT_LIST' in v['code'] for v in violations))

    def test_red_flag_entry_not_dict_skipped(self):
        raw = _good_truth_check()
        raw['red_flags'] = ["just a string", {'title': 'OK', 'detail': 'OK.', 'severity': 'low', 'evidence': 'test'}]
        out, violations = validate_truth_check(raw)
        self.assertEqual(len(out['red_flags']), 1)  # string entry removed


# ===========================================================================
# GROUP 4: EVIDENCE GROUNDING
# ===========================================================================

class TestEvidenceGrounding(unittest.TestCase):

    def test_grounded_evidence_passes(self):
        raw = _good_truth_check()
        # Evidence text from fixtures matches SAMPLE_PDF_TEXT
        out, violations = validate_truth_check(raw, pdf_text=SAMPLE_PDF_TEXT)
        grounding_violations = [v for v in violations if 'UNGROUNDED' in v['code']]
        self.assertEqual(len(grounding_violations), 0)

    def test_hallucinated_evidence_caught(self):
        raw = _good_truth_check()
        raw['red_flags'][0]['evidence'] = 'The seller admitted to toxic waste buried in the backyard in 2003'
        out, violations = validate_truth_check(raw, pdf_text=SAMPLE_PDF_TEXT)
        self.assertTrue(any('UNGROUNDED_EVIDENCE' in v['code'] for v in violations))

    def test_grounding_skipped_without_pdf(self):
        raw = _good_truth_check()
        raw['red_flags'][0]['evidence'] = 'completely fabricated evidence text here'
        out, violations = validate_truth_check(raw, pdf_text=None)
        grounding_violations = [v for v in violations if 'UNGROUNDED' in v['code']]
        self.assertEqual(len(grounding_violations), 0, "Grounding check should be skipped without pdf_text")

    def test_partial_match_grounding(self):
        """Evidence that shares many words with source but isn't exact."""
        evidence = "seller discloses property located Hollister defects cracks bedroom"
        grounded, pct = _check_grounding(evidence, SAMPLE_PDF_TEXT)
        self.assertTrue(grounded, f"Partial match should pass (pct={pct:.0%})")

    def test_completely_fabricated_fails(self):
        evidence = "the basement contained seventeen barrels of radioactive material stored since 1987"
        grounded, pct = _check_grounding(evidence, SAMPLE_PDF_TEXT)
        self.assertFalse(grounded, f"Fabricated evidence should fail (pct={pct:.0%})")

    def test_very_short_evidence_skipped(self):
        """Evidence under 4 words isn't reliably checkable."""
        grounded, pct = _check_grounding("No", SAMPLE_PDF_TEXT)
        self.assertTrue(grounded, "Very short evidence should be skipped")

    def test_grounding_tagged_on_flag(self):
        """Ungrounded evidence should add _grounding_warning to the flag."""
        raw = _good_truth_check()
        raw['red_flags'][0]['evidence'] = 'completely fabricated toxic mold asbestos contamination biohazard'
        out, violations = validate_truth_check(raw, pdf_text=SAMPLE_PDF_TEXT)
        warned_flag = out['red_flags'][0]
        self.assertTrue(warned_flag.get('_grounding_warning'),
                        "Ungrounded flag should have _grounding_warning=True")


# ===========================================================================
# GROUP 5: EVASION PHRASE GROUNDING
# ===========================================================================

class TestEvasionPhraseGrounding(unittest.TestCase):

    def test_real_phrase_kept(self):
        raw = _good_truth_check()
        raw['evasion_phrases'] = ['is not aware of any']
        out, violations = validate_truth_check(raw, pdf_text=SAMPLE_PDF_TEXT)
        self.assertIn('is not aware of any', out['evasion_phrases'])

    def test_fabricated_phrase_removed(self):
        raw = _good_truth_check()
        raw['evasion_phrases'] = ['to the best of my recollection']
        out, violations = validate_truth_check(raw, pdf_text=SAMPLE_PDF_TEXT)
        self.assertEqual(len(out['evasion_phrases']), 0)
        self.assertTrue(any('UNGROUNDED_EVASION' in v['code'] for v in violations))

    def test_mixed_phrases_filtered(self):
        raw = _good_truth_check()
        raw['evasion_phrases'] = [
            'is not aware of any',            # real
            'to the best of my knowledge',     # fake
            'converted garage without permit',  # real
        ]
        out, violations = validate_truth_check(raw, pdf_text=SAMPLE_PDF_TEXT)
        self.assertEqual(len(out['evasion_phrases']), 2)
        evasion_violations = [v for v in violations if 'EVASION' in v['code']]
        self.assertEqual(len(evasion_violations), 1)


# ===========================================================================
# GROUP 6: CROSS-REFERENCE FINDINGS VALIDATION
# ===========================================================================

class TestCrossReferenceValidation(unittest.TestCase):

    def test_valid_findings_pass(self):
        findings = _good_cross_ref_findings()
        out, violations = validate_cross_reference_findings(findings)
        self.assertEqual(len(violations), 0)
        self.assertEqual(len(out), 2)

    def test_invalid_type_defaulted(self):
        findings = [{'type': 'hallucination', 'severity': 'high',
                     'title': 'Test', 'detail': 'Test detail.'}]
        out, violations = validate_cross_reference_findings(findings)
        self.assertEqual(out[0]['type'], 'context')
        self.assertTrue(any('INVALID_FINDING_TYPE' in v['code'] for v in violations))

    def test_invalid_severity_defaulted(self):
        findings = [{'type': 'omission', 'severity': 'critical',
                     'title': 'Test', 'detail': 'Test detail.'}]
        out, violations = validate_cross_reference_findings(findings)
        self.assertEqual(out[0]['severity'], 'info')

    def test_confidence_clamped(self):
        findings = [{'type': 'omission', 'severity': 'high',
                     'title': 'Test', 'detail': 'Test.', 'confidence': 1.5}]
        out, violations = validate_cross_reference_findings(findings)
        self.assertEqual(out[0]['confidence'], 1.0)

    def test_negative_confidence_clamped(self):
        findings = [{'type': 'omission', 'severity': 'high',
                     'title': 'Test', 'detail': 'Test.', 'confidence': -0.3}]
        out, violations = validate_cross_reference_findings(findings)
        self.assertEqual(out[0]['confidence'], 0.0)

    def test_capped_at_5(self):
        findings = [{'type': 'context', 'severity': 'info',
                     'title': f'Item {i}', 'detail': f'Detail {i}.'}
                    for i in range(8)]
        out, violations = validate_cross_reference_findings(findings)
        self.assertEqual(len(out), 5)
        self.assertTrue(any('TOO_MANY' in v['code'] for v in violations))

    def test_missing_title_caught(self):
        findings = [{'type': 'omission', 'severity': 'high', 'detail': 'Some detail.'}]
        out, violations = validate_cross_reference_findings(findings)
        self.assertTrue(any('MISSING_TITLE' in v['code'] for v in violations))

    def test_not_list_handled(self):
        out, violations = validate_cross_reference_findings("not a list")
        self.assertEqual(out, [])
        self.assertTrue(any('NOT_LIST' in v['code'] for v in violations))

    def test_title_truncated_at_120(self):
        findings = [{'type': 'omission', 'severity': 'high',
                     'title': 'A' * 200, 'detail': 'Test.'}]
        out, violations = validate_cross_reference_findings(findings)
        self.assertEqual(len(out[0]['title']), 120)


# ===========================================================================
# GROUP 7: SEVERITY RATING VALIDATION
# ===========================================================================

class TestSeverityRatingValidation(unittest.TestCase):

    def test_valid_ratings_pass(self):
        ai_data = {
            'issues': [
                {'id': 'C1', 'severity': 'major', 'explanation': 'Test.', 'confidence': 0.9},
                {'id': 'U1', 'severity': 'moderate', 'explanation': 'Test.', 'confidence': 0.7},
            ],
            'transparency_score': 65,
            'summary': 'Two issues found.',
        }
        originals = [
            {'type': 'contradiction', 'id': 'C1'},
            {'type': 'undisclosed', 'id': 'U1'},
        ]
        out, violations = validate_severity_ratings(ai_data, originals)
        self.assertEqual(len(violations), 0)

    def test_invented_issue_id_caught(self):
        ai_data = {
            'issues': [
                {'id': 'C1', 'severity': 'major', 'explanation': 'Real.', 'confidence': 0.8},
                {'id': 'X99', 'severity': 'critical', 'explanation': 'Invented.', 'confidence': 0.9},
            ],
            'transparency_score': 40,
            'summary': 'Issues found.',
        }
        originals = [{'type': 'contradiction', 'id': 'C1'}]
        out, violations = validate_severity_ratings(ai_data, originals)
        self.assertTrue(any('INVENTED_ISSUE_ID' in v['code'] for v in violations))

    def test_invalid_severity_defaulted(self):
        ai_data = {
            'issues': [{'id': 'C1', 'severity': 'catastrophic', 'confidence': 0.9}],
            'transparency_score': 50,
        }
        out, violations = validate_severity_ratings(ai_data, [{'type': 'contradiction'}])
        self.assertEqual(out['issues'][0]['severity'], 'moderate')

    def test_transparency_score_clamped(self):
        ai_data = {'issues': [], 'transparency_score': 150}
        out, violations = validate_severity_ratings(ai_data, [])
        self.assertEqual(out['transparency_score'], 100)


# ===========================================================================
# GROUP 8: CLEAN OUTPUTS PASS
# ===========================================================================

class TestCleanOutputs(unittest.TestCase):

    def test_perfect_truth_check_zero_violations(self):
        raw = _good_truth_check()
        out, violations = validate_truth_check(raw, pdf_text=SAMPLE_PDF_TEXT)
        error_violations = [v for v in violations if v['severity'] == 'error']
        self.assertEqual(len(error_violations), 0,
                         f"Good output should have 0 error violations, got: {error_violations}")

    def test_perfect_cross_ref_zero_violations(self):
        findings = _good_cross_ref_findings()
        out, violations = validate_cross_reference_findings(findings)
        self.assertEqual(len(violations), 0)

    def test_output_preserves_all_fields(self):
        """Validator shouldn't strip fields it doesn't know about."""
        raw = _good_truth_check()
        raw['custom_field'] = 'should survive'
        out, violations = validate_truth_check(raw)
        self.assertEqual(out.get('custom_field'), 'should survive')


# ===========================================================================
# GROUP 9: EDGE CASES
# ===========================================================================

class TestEdgeCases(unittest.TestCase):

    def test_empty_dict(self):
        out, violations = validate_truth_check({})
        # Should have defaults, not crash
        self.assertEqual(out['trust_score'], 50)
        self.assertEqual(out['red_flags'], [])

    def test_none_fields(self):
        raw = {'trust_score': None, 'grade': None, 'red_flags': None}
        out, violations = validate_truth_check(raw)
        self.assertEqual(out['trust_score'], 50)
        self.assertEqual(out['grade'], 'C')
        self.assertEqual(out['red_flags'], [])

    def test_blank_unknown_count_negative(self):
        raw = _good_truth_check()
        raw['blank_unknown_count'] = -5
        out, violations = validate_truth_check(raw)
        self.assertEqual(out['blank_unknown_count'], 0)

    def test_blank_unknown_count_string(self):
        raw = _good_truth_check()
        raw['blank_unknown_count'] = "three"
        out, violations = validate_truth_check(raw)
        self.assertEqual(out['blank_unknown_count'], 0)

    def test_overall_assessment_not_string(self):
        raw = _good_truth_check()
        raw['overall_assessment'] = ['list', 'instead']
        out, violations = validate_truth_check(raw)
        self.assertIsInstance(out['overall_assessment'], str)

    def test_cross_ref_empty_list(self):
        out, violations = validate_cross_reference_findings([])
        self.assertEqual(out, [])
        self.assertEqual(violations, [])

    def test_cross_ref_none_entries(self):
        out, violations = validate_cross_reference_findings([None, None])
        self.assertEqual(len(out), 0)


# ===========================================================================
# GROUP 10: AUDIT LOGGING
# ===========================================================================

class TestAuditLogging(unittest.TestCase):

    def test_log_ai_call_does_not_crash(self):
        """Logging should never raise, even with bad inputs."""
        try:
            log_ai_call(
                endpoint='test',
                model='test-model',
                input_summary={'test': True},
                raw_output={'test': 'data'},
                validated_output={'test': 'data'},
                violations=[],
                latency_ms=123.4,
            )
        except Exception as e:
            self.fail(f"log_ai_call raised: {e}")

    def test_log_with_violations(self):
        try:
            log_ai_call(
                endpoint='test',
                model='test-model',
                input_summary={},
                raw_output={},
                validated_output={},
                violations=[{'code': 'TEST', 'message': 'test', 'severity': 'error'}],
            )
        except Exception as e:
            self.fail(f"log_ai_call with violations raised: {e}")


# ===========================================================================
# RUNNER
# ===========================================================================

def run_all():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestTrustScoreBounds,
        TestGradeValidation,
        TestRedFlagStructure,
        TestEvidenceGrounding,
        TestEvasionPhraseGrounding,
        TestCrossReferenceValidation,
        TestSeverityRatingValidation,
        TestCleanOutputs,
        TestEdgeCases,
        TestAuditLogging,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    total = suite.countTestCases()
    print(f"\n{'='*60}")
    print(f"  AI OUTPUT VALIDATOR TESTS — {total} tests")
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
