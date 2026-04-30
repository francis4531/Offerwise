#!/usr/bin/env python3
"""
OfferWise Test Runner v3.0
===========================
Runs the full test suite. Usage:
    python run_tests.py           # Run all tests
    python run_tests.py --quick   # Run core tests only (fastest)
    python run_tests.py --ci      # Run CI-compatible tests (no server needed)
"""

import subprocess
import sys
import os

# ── Core: pure logic, no server/DB needed ────────────────────────────────────
CORE_TESTS = [
    "test_risk_check_engine.py",
    "test_ai_output_validator.py",
    "test_algorithms.py",
    "test_validation.py",
    "test_negotiation.py",
    "test_results_quality.py",
    "test_confidence_scorer.py",
    "test_transparency_scorer.py",
    "test_analysis_cache.py",
]

# ── Document & PDF pipeline ───────────────────────────────────────────────────
PDF_TESTS = [
    "test_document_parser.py",
    "test_pdf_handler.py",
    "test_adversarial_pdfs.py",
    "test_cross_reference.py",
]

# ── Flows, workflows, integration ────────────────────────────────────────────
FLOW_TESTS = [
    "test_all_60_workflows.py",
    "test_new_workflows.py",
    "test_integration.py",
    "test_critical_paths.py",
    "test_comprehensive.py",
    "test_advanced.py",
]

# ── GTM, growth, infra ────────────────────────────────────────────────────────
GTM_TESTS = [
    "test_gtm.py",
    "test_gtm_content.py",
    "test_drip_campaign.py",
    "test_nearby_listings.py",
    "test_agentic_monitor.py",
]

# ── Security ──────────────────────────────────────────────────────────────────
SECURITY_TESTS = [
    "test_adversarial_pdfs.py",
    "test_critical_paths.py",
]

ALL_TESTS = CORE_TESTS + PDF_TESTS + FLOW_TESTS + GTM_TESTS


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    mode = sys.argv[1] if len(sys.argv) > 1 else "--all"

    if mode == "--quick":
        test_files = CORE_TESTS
        label = "QUICK (core only)"
    elif mode == "--pdf":
        test_files = PDF_TESTS
        label = "PDF PIPELINE"
    elif mode == "--flows":
        test_files = FLOW_TESTS
        label = "FLOWS & WORKFLOWS"
    elif mode == "--gtm":
        test_files = GTM_TESTS
        label = "GTM & GROWTH"
    elif mode == "--security":
        test_files = SECURITY_TESTS
        label = "SECURITY"
    else:
        test_files = ALL_TESTS
        label = "FULL SUITE"

    # Deduplicate preserving order
    seen = set()
    test_files = [f for f in test_files if not (f in seen or seen.add(f))]
    test_files = [f for f in test_files if os.path.exists(f)]

    total_tests = 0
    for f in test_files:
        try:
            count = open(f).read().count("def test_")
            total_tests += count
        except Exception:
            pass

    print(f"\n{'='*60}")
    print(f"  OfferWise Test Suite — {label}")
    print(f"  Files: {len(test_files)} | Estimated tests: {total_tests}")
    print(f"{'='*60}\n")

    cmd = [sys.executable, "-m", "pytest"] + test_files + ["-v", "--tb=short", "-x"]
    result = subprocess.run(cmd)

    print(f"\n{'='*60}")
    print(f"  {'✅ ALL TESTS PASSED' if result.returncode == 0 else '❌ SOME TESTS FAILED'}")
    print(f"{'='*60}\n")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
