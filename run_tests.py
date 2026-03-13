#!/usr/bin/env python3
"""
OfferWise Test Runner v2.0
===========================
Runs the full test suite. Usage:
    python run_tests.py           # Run all tests
    python run_tests.py --quick   # Run core tests only (fastest)
    python run_tests.py --ci      # Run CI-compatible tests (no server needed)
"""

import subprocess
import sys
import os

CORE_TESTS = [
    "test_risk_check_engine.py",
    "test_ai_output_validator.py",
    "test_algorithms.py",
]

EXPANDED_TESTS = [
    "test_document_parser.py",
    "test_cross_reference.py",
    "test_analysis_cache.py",
    "test_confidence_scorer.py",
    "test_transparency_scorer.py",
    "test_negotiation.py",
    "test_pdf_handler.py",
]

SECURITY_TESTS = [
    "test_adversarial_pdfs.py",
    "test_critical_paths.py",
]

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    mode = sys.argv[1] if len(sys.argv) > 1 else "--all"

    if mode == "--quick":
        test_files = CORE_TESTS
        label = "QUICK (core only)"
    else:
        test_files = CORE_TESTS + EXPANDED_TESTS + SECURITY_TESTS
        label = "FULL SUITE"

    test_files = [f for f in test_files if os.path.exists(f)]
    print(f"\n{'='*60}\n  OfferWise Test Suite — {label}\n  Files: {len(test_files)}\n{'='*60}\n")

    cmd = [sys.executable, "-m", "pytest"] + test_files + ["-v", "--tb=short"]
    result = subprocess.run(cmd)

    print(f"\n{'='*60}")
    print(f"  {'✅ ALL TESTS PASSED' if result.returncode == 0 else '❌ SOME TESTS FAILED'}")
    print(f"{'='*60}\n")
    return result.returncode

if __name__ == "__main__":
    sys.exit(main())
