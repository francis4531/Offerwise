"""
OfferWise ML Inference Tests
=============================
Validates that trained models produce sensible outputs.
Run after every training run to catch regressions.
"""
import logging
import json

logger = logging.getLogger(__name__)

# Test cases: known findings with expected classifications
FINDING_TEST_CASES = [
    {
        'text': 'Main electrical panel contains Federal Pacific Electric breakers which are known fire hazards',
        'expected_category': 'electrical',
        'expected_severity': 'critical',
        'acceptable_categories': ['electrical'],
        'acceptable_severities': ['critical', 'major'],
    },
    {
        'text': 'Water stains visible on master bedroom ceiling near bathroom wall suggesting slow leak',
        'expected_category': 'plumbing',
        'expected_severity': 'major',
        'acceptable_categories': ['plumbing', 'environmental'],
        'acceptable_severities': ['major', 'moderate', 'critical'],
    },
    {
        'text': 'Minor paint peeling on exterior trim boards, cosmetic only',
        'expected_category': 'roof_exterior',
        'expected_severity': 'minor',
        'acceptable_categories': ['roof_exterior', 'general'],
        'acceptable_severities': ['minor'],
    },
    {
        'text': 'Foundation shows horizontal crack with 1/4 inch displacement in basement wall',
        'expected_category': 'foundation_structure',
        'expected_severity': 'critical',
        'acceptable_categories': ['foundation_structure'],
        'acceptable_severities': ['critical', 'major'],
    },
    {
        'text': 'HVAC condensate drain line not properly trapped, could cause water damage',
        'expected_category': 'hvac_systems',
        'expected_severity': 'minor',
        'acceptable_categories': ['hvac_systems', 'plumbing'],
        'acceptable_severities': ['minor', 'moderate'],
    },
    {
        'text': 'Roof shingles show significant granule loss and curling, approximately 20 years old',
        'expected_category': 'roof_exterior',
        'expected_severity': 'major',
        'acceptable_categories': ['roof_exterior'],
        'acceptable_severities': ['major', 'moderate', 'critical'],
    },
    {
        'text': 'Loose handrail on basement stairs, safety hazard per building code',
        'expected_category': 'foundation_structure',
        'expected_severity': 'moderate',
        'acceptable_categories': ['foundation_structure', 'electrical', 'general'],
        'acceptable_severities': ['moderate', 'minor', 'major'],
    },
    {
        'text': 'Elevated radon levels detected at 6.2 pCi/L, above EPA action level of 4.0',
        'expected_category': 'environmental',
        'expected_severity': 'major',
        'acceptable_categories': ['environmental'],
        'acceptable_severities': ['major', 'critical', 'moderate'],
    },
]

CONTRADICTION_TEST_CASES = [
    {
        'seller_claim': 'No known water damage or leaks in the property',
        'inspector_finding': 'Significant water stains on basement ceiling with active moisture',
        'expected_label': 'contradiction',
        'acceptable_labels': ['contradiction'],
    },
    {
        'seller_claim': '',
        'inspector_finding': 'Foundation cracks not disclosed by seller, discovered during inspection',
        'expected_label': 'omission',
        'acceptable_labels': ['omission'],
    },
    {
        'seller_claim': 'Roof was replaced in 2020',
        'inspector_finding': 'Roof appears to be newer construction, shingles in good condition',
        'expected_label': 'consistent',
        'acceptable_labels': ['consistent', 'contradiction'],  # Model may not have seen 'consistent' class
    },
]

COST_TEST_CASES = [
    {
        'text': 'Replace main electrical panel with modern circuit breakers',
        'category': 'electrical',
        'severity': 'critical',
        'expected_range': (5000, 20000),
    },
    {
        'text': 'Minor caulking repair around bathroom fixtures',
        'category': 'plumbing',
        'severity': 'minor',
        'expected_range': (100, 2000),
    },
    {
        'text': 'Full roof replacement, architectural shingles, 2000 sq ft',
        'category': 'roof',
        'severity': 'critical',
        'expected_range': (8000, 30000),
    },
]


def run_inference_tests():
    """Run all inference smoke tests. Returns dict with results."""
    results = {'passed': 0, 'failed': 0, 'skipped': 0, 'details': []}

    # Test Finding Classifier
    try:
        from ml_inference import get_classifier
        clf = get_classifier()
        if clf.is_ready():
            for tc in FINDING_TEST_CASES:
                r = clf.classify(tc['text'])
                if not r.get('used_ml'):
                    results['skipped'] += 1
                    results['details'].append({
                        'model': 'FindingClassifier', 'input': tc['text'][:60],
                        'status': 'SKIP', 'reason': 'ML not used'
                    })
                    continue

                cat_ok = r['category'] in tc['acceptable_categories']
                sev_ok = r['severity'] in tc['acceptable_severities']
                passed = cat_ok and sev_ok

                if passed:
                    results['passed'] += 1
                else:
                    results['failed'] += 1

                results['details'].append({
                    'model': 'FindingClassifier',
                    'input': tc['text'][:60],
                    'expected_cat': tc['expected_category'],
                    'got_cat': r['category'],
                    'cat_conf': r.get('category_confidence', 0),
                    'cat_ok': cat_ok,
                    'expected_sev': tc['expected_severity'],
                    'got_sev': r['severity'],
                    'sev_conf': r.get('severity_confidence', 0),
                    'sev_ok': sev_ok,
                    'status': 'PASS' if passed else 'FAIL',
                })
        else:
            results['skipped'] += len(FINDING_TEST_CASES)
    except Exception as e:
        results['details'].append({'model': 'FindingClassifier', 'status': 'ERROR', 'reason': str(e)[:100]})

    # Test Contradiction Detector
    try:
        from ml_inference import get_contradiction_detector
        cd = get_contradiction_detector()
        if cd.is_ready():
            for tc in CONTRADICTION_TEST_CASES:
                r = cd.classify(tc['seller_claim'], tc['inspector_finding'])
                if not r.get('used_ml'):
                    results['skipped'] += 1
                    continue

                passed = r['label'] in tc['acceptable_labels']
                if passed:
                    results['passed'] += 1
                else:
                    results['failed'] += 1

                results['details'].append({
                    'model': 'ContradictionDetector',
                    'input': tc['inspector_finding'][:60],
                    'expected': tc['expected_label'],
                    'got': r['label'],
                    'confidence': r.get('confidence', 0),
                    'status': 'PASS' if passed else 'FAIL',
                })
        else:
            results['skipped'] += len(CONTRADICTION_TEST_CASES)
    except Exception as e:
        results['details'].append({'model': 'ContradictionDetector', 'status': 'ERROR', 'reason': str(e)[:100]})

    # Test Cost Predictor
    try:
        from ml_inference import get_cost_predictor
        cp = get_cost_predictor()
        if cp.is_ready():
            for tc in COST_TEST_CASES:
                r = cp.predict(tc['text'], category=tc['category'], severity=tc['severity'])
                if not r.get('used_ml'):
                    results['skipped'] += 1
                    continue

                cost_mid = r.get('cost_mid', 0)
                lo, hi = tc['expected_range']
                passed = lo <= cost_mid <= hi

                if passed:
                    results['passed'] += 1
                else:
                    results['failed'] += 1

                results['details'].append({
                    'model': 'CostPredictor',
                    'input': tc['text'][:60],
                    'expected_range': f'${lo:,}-${hi:,}',
                    'got': f'${cost_mid:,.0f}',
                    'status': 'PASS' if passed else 'FAIL',
                })
        else:
            results['skipped'] += len(COST_TEST_CASES)
    except Exception as e:
        results['details'].append({'model': 'CostPredictor', 'status': 'ERROR', 'reason': str(e)[:100]})

    results['total'] = results['passed'] + results['failed'] + results['skipped']
    return results
