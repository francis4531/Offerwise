#!/usr/bin/env python3
"""
Quick Test Runner - Run all algorithm tests with one command
Usage: python run_tests.py
"""

import sys
import os

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'
BOLD = '\033[1m'


def print_header(text):
    """Print formatted header"""
    print(f"\n{BLUE}{BOLD}{'=' * 70}{RESET}")
    print(f"{BLUE}{BOLD}{text.center(70)}{RESET}")
    print(f"{BLUE}{BOLD}{'=' * 70}{RESET}\n")


def print_success(text):
    """Print success message"""
    print(f"{GREEN}✅ {text}{RESET}")


def print_error(text):
    """Print error message"""
    print(f"{RED}❌ {text}{RESET}")


def print_warning(text):
    """Print warning message"""
    print(f"{YELLOW}⚠️  {text}{RESET}")


def run_unit_tests():
    """Run unit tests"""
    print_header("UNIT TESTS")
    
    try:
        import test_algorithms
        success = test_algorithms.run_all_tests()
        return success
    except ImportError:
        print_error("test_algorithms.py not found!")
        print("Make sure you're in the offerwise_render directory")
        return False
    except Exception as e:
        print_error(f"Error running unit tests: {e}")
        return False


def run_advanced_tests():
    """Run property-based and fuzz tests"""
    print_header("ADVANCED TESTS")
    
    try:
        from test_advanced import PropertyBasedTester, FuzzTester, PerformanceBenchmark
        
        # Property-based testing
        print(f"{BOLD}Property-Based Testing{RESET}")
        prop_tester = PropertyBasedTester(num_tests=1000)
        prop_tester.run_all_property_tests()
        
        # Fuzz testing
        print(f"\n{BOLD}Fuzz Testing{RESET}")
        fuzz_tester = FuzzTester(iterations=10000)
        fuzz_tester.fuzz_transparency_score()
        
        # Performance benchmark
        benchmark = PerformanceBenchmark()
        benchmark.benchmark_transparency_calculation(iterations=100000)
        
        # Check for failures
        if prop_tester.failures or fuzz_tester.crashes or fuzz_tester.unexpected:
            return False
        return True
        
    except ImportError:
        print_error("test_advanced.py not found!")
        return False
    except Exception as e:
        print_error(f"Error running advanced tests: {e}")
        return False


def main():
    """Main test runner"""
    print(f"\n{BOLD}{'🧪' * 35}{RESET}")
    print(f"{BOLD}OFFERWISE ALGORITHM TEST SUITE{RESET}".center(70))
    print(f"{BOLD}{'🧪' * 35}{RESET}")
    
    # Run tests
    unit_success = run_unit_tests()
    advanced_success = run_advanced_tests()
    
    # Final report
    print_header("FINAL REPORT")
    
    if unit_success:
        print_success("Unit Tests: PASSED")
    else:
        print_error("Unit Tests: FAILED")
    
    if advanced_success:
        print_success("Advanced Tests: PASSED")
    else:
        print_error("Advanced Tests: FAILED")
    
    # Overall result
    print(f"\n{BOLD}{'=' * 70}{RESET}")
    if unit_success and advanced_success:
        print_success(f"{BOLD}ALL TESTS PASSED! ✨{RESET}".center(80))
        print_success("Your algorithms are working correctly!")
        print_success("Safe to deploy! 🚀")
        exit_code = 0
    else:
        print_error(f"{BOLD}SOME TESTS FAILED ⚠️{RESET}".center(80))
        print_warning("Review failures above before deploying")
        print_warning("Do NOT deploy until all tests pass")
        exit_code = 1
    
    print(f"{BOLD}{'=' * 70}{RESET}\n")
    
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
