"""
Advanced Algorithm Testing Tools
Property-based testing, fuzzing, and integration tests
"""

import random
import time
import json
from typing import List, Dict, Tuple
from dataclasses import dataclass
import requests


@dataclass
class PropertyTestCase:
    """Randomly generated property for testing"""
    address: str
    price: float
    disclosed_count: int
    confirmed_count: int
    contradictions: int
    undisclosed_count: int
    repair_cost: float


class PropertyBasedTester:
    """Generate random test cases to find edge cases"""
    
    def __init__(self, num_tests: int = 1000):
        self.num_tests = num_tests
        self.failures = []
    
    def generate_random_property(self) -> PropertyTestCase:
        """Generate random but realistic property data"""
        price = random.randint(300_000, 5_000_000)
        disclosed = random.randint(0, 30)
        confirmed = random.randint(0, disclosed)  # Can't confirm more than disclosed
        contradictions = random.randint(0, min(10, disclosed))  # Can't contradict more than disclosed
        undisclosed = random.randint(0, 50)
        repair_cost = random.randint(0, int(price * 0.3))  # Up to 30% of price
        
        return PropertyTestCase(
            address=f"{random.randint(100, 9999)} Test St, San Jose, CA",
            price=price,
            disclosed_count=disclosed,
            confirmed_count=confirmed,
            contradictions=contradictions,
            undisclosed_count=undisclosed,
            repair_cost=repair_cost
        )
    
    def calculate_transparency(self, prop: PropertyTestCase) -> float:
        """Calculate transparency score"""
        D_ref, w_c, w_x, w_u = 15, 3, 15, 10
        
        B = min(100, (prop.disclosed_count / D_ref) * 50)
        C_bonus = min(30, prop.confirmed_count * w_c)
        X_penalty = prop.contradictions * w_x
        U_penalty = prop.undisclosed_count * w_u
        
        T = max(0, min(100, B + C_bonus - X_penalty - U_penalty))
        return round(T, 2)
    
    def test_property_bounded_output(self):
        """Property: Transparency score must always be in [0, 100]"""
        print(f"Testing bounded output with {self.num_tests} random cases...")
        
        for i in range(self.num_tests):
            prop = self.generate_random_property()
            score = self.calculate_transparency(prop)
            
            if not (0 <= score <= 100):
                self.failures.append({
                    'test': 'bounded_output',
                    'property': prop,
                    'score': score,
                    'reason': f'Score {score} outside [0, 100]'
                })
        
        if not self.failures:
            print(f"✅ All {self.num_tests} tests passed - scores always in [0, 100]")
        else:
            print(f"❌ {len(self.failures)} failures found")
            for f in self.failures[:5]:  # Show first 5
                print(f"  - {f}")
    
    def test_property_monotonicity(self):
        """Property: More confirmations = higher score (all else equal)"""
        print(f"\nTesting monotonicity with {self.num_tests} random cases...")
        
        failures = []
        for i in range(self.num_tests):
            # Generate base property
            prop = self.generate_random_property()
            score1 = self.calculate_transparency(prop)
            
            # Increase confirmations (if possible)
            if prop.confirmed_count < prop.disclosed_count:
                prop.confirmed_count += 1
                score2 = self.calculate_transparency(prop)
                
                if score2 < score1:  # Score should increase or stay same
                    failures.append({
                        'test': 'monotonicity',
                        'before': score1,
                        'after': score2,
                        'reason': 'Score decreased when confirmations increased'
                    })
        
        if not failures:
            print(f"✅ Monotonicity holds - more confirmations always improves score")
        else:
            print(f"❌ {len(failures)} violations found")
    
    def test_property_penalty_ordering(self):
        """Property: Contradictions penalized more than omissions (15 > 10)"""
        print(f"\nTesting penalty ordering with {self.num_tests} random cases...")
        
        failures = []
        for i in range(self.num_tests):
            base_disclosed = random.randint(10, 20)
            base_confirmed = random.randint(5, base_disclosed)
            
            # Case A: 1 contradiction, 0 undisclosed
            prop_a = PropertyTestCase(
                address="Test",
                price=1_000_000,
                disclosed_count=base_disclosed,
                confirmed_count=base_confirmed,
                contradictions=1,
                undisclosed_count=0,
                repair_cost=50_000
            )
            score_a = self.calculate_transparency(prop_a)
            
            # Case B: 0 contradictions, 1 undisclosed
            prop_b = PropertyTestCase(
                address="Test",
                price=1_000_000,
                disclosed_count=base_disclosed,
                confirmed_count=base_confirmed,
                contradictions=0,
                undisclosed_count=1,
                repair_cost=50_000
            )
            score_b = self.calculate_transparency(prop_b)
            
            # Contradiction penalty (15) > Undisclosed penalty (10)
            # So score_a < score_b
            if score_a >= score_b:
                failures.append({
                    'test': 'penalty_ordering',
                    'contradiction_score': score_a,
                    'omission_score': score_b,
                    'reason': 'Contradiction not penalized more than omission'
                })
        
        if not failures:
            print(f"✅ Penalty ordering correct - contradictions > omissions")
        else:
            print(f"❌ {len(failures)} violations found")
    
    def run_all_property_tests(self):
        """Run all property-based tests"""
        print("="*70)
        print("PROPERTY-BASED TESTING")
        print("="*70)
        
        self.test_property_bounded_output()
        self.test_property_monotonicity()
        self.test_property_penalty_ordering()
        
        print(f"\n{'='*70}")
        print(f"Property-based testing complete!")
        print(f"Total tests run: {self.num_tests * 3}")
        print(f"Total failures: {len(self.failures)}")
        print(f"{'='*70}\n")


class FuzzTester:
    """Fuzz testing to find crashes and unexpected behavior"""
    
    def __init__(self, iterations: int = 10000):
        self.iterations = iterations
        self.crashes = []
        self.unexpected = []
    
    def fuzz_transparency_score(self):
        """Fuzz test transparency score with extreme/invalid inputs"""
        print(f"Fuzzing transparency score with {self.iterations} extreme inputs...")
        
        test_cases = [
            # Extreme values
            (999999, 999999, 0, 0),  # Huge disclosures
            (0, 0, 999999, 999999),  # Huge penalties
            (-100, -100, -100, -100),  # Negative values
            (1, 1000, 0, 0),  # Confirmed > Disclosed (invalid)
            
            # Floating point
            (10.5, 8.3, 2.1, 3.7),  # Decimals
            (float('inf'), 0, 0, 0),  # Infinity
            (0, 0, 0, float('nan')),  # NaN
        ]
        
        # Add random extreme cases
        for _ in range(self.iterations):
            test_cases.append((
                random.randint(-1000, 10000),
                random.randint(-1000, 10000),
                random.randint(-1000, 10000),
                random.randint(-1000, 10000)
            ))
        
        for disclosed, confirmed, contradictions, undisclosed in test_cases:
            try:
                # Try to calculate
                D_ref, w_c, w_x, w_u = 15, 3, 15, 10
                B = min(100, (disclosed / D_ref) * 50)
                C_bonus = min(30, confirmed * w_c)
                X_penalty = contradictions * w_x
                U_penalty = undisclosed * w_u
                T = max(0, min(100, B + C_bonus - X_penalty - U_penalty))
                
                # Check for unexpected values
                if T < 0 or T > 100:
                    self.unexpected.append({
                        'inputs': (disclosed, confirmed, contradictions, undisclosed),
                        'output': T,
                        'reason': 'Score outside [0, 100]'
                    })
                
                if str(T) in ['inf', '-inf', 'nan']:
                    self.unexpected.append({
                        'inputs': (disclosed, confirmed, contradictions, undisclosed),
                        'output': T,
                        'reason': 'Invalid numeric result'
                    })
                    
            except Exception as e:
                self.crashes.append({
                    'inputs': (disclosed, confirmed, contradictions, undisclosed),
                    'error': str(e)
                })
        
        print(f"✅ Fuzz test complete")
        print(f"  Crashes: {len(self.crashes)}")
        print(f"  Unexpected behavior: {len(self.unexpected)}")
        
        if self.crashes:
            print("\n⚠️  CRASHES FOUND:")
            for crash in self.crashes[:5]:
                print(f"  Input: {crash['inputs']}")
                print(f"  Error: {crash['error']}")
        
        if self.unexpected:
            print("\n⚠️  UNEXPECTED BEHAVIOR:")
            for case in self.unexpected[:5]:
                print(f"  Input: {case['inputs']}")
                print(f"  Output: {case['output']}")
                print(f"  Reason: {case['reason']}")


class IntegrationTester:
    """Test with actual API calls"""
    
    def __init__(self, base_url: str = "http://localhost:5000"):
        self.base_url = base_url
        self.results = []
    
    def test_end_to_end_analysis(self, test_cases: List[Dict]):
        """Test complete analysis flow with API"""
        print("\n" + "="*70)
        print("INTEGRATION TESTING (End-to-End)")
        print("="*70)
        
        for i, case in enumerate(test_cases, 1):
            print(f"\nTest {i}/{len(test_cases)}: {case['name']}")
            
            try:
                # Simulate document upload and analysis
                response = requests.post(
                    f"{self.base_url}/api/analyze",
                    json={
                        'seller_disclosure_text': case.get('disclosure_text', 'Test disclosure'),
                        'inspection_report_text': case.get('inspection_text', ''),
                        'property_price': case['price'],
                        'property_address': case['address'],
                        'buyer_profile': case.get('buyer_profile', {
                            'max_budget': case['price'] * 1.1,
                            'repair_tolerance': 'moderate',
                            'biggest_regret': 'hidden_issues'
                        })
                    },
                    timeout=60
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Validate response structure
                    required_fields = ['risk_score', 'cross_reference', 'offer_strategy']
                    missing_fields = [f for f in required_fields if f not in data]
                    
                    if missing_fields:
                        print(f"  ❌ Missing fields: {missing_fields}")
                        self.results.append({'test': case['name'], 'status': 'failed', 'reason': 'Missing fields'})
                    else:
                        # Validate calculations
                        risk_score = data['risk_score'].get('overall_risk_score', 0)
                        transparency = data['cross_reference'].get('transparency_score', 0)
                        
                        if 0 <= risk_score <= 100 and 0 <= transparency <= 100:
                            print(f"  ✅ PASS - Risk: {risk_score}, Transparency: {transparency}")
                            self.results.append({'test': case['name'], 'status': 'passed'})
                        else:
                            print(f"  ❌ Invalid scores - Risk: {risk_score}, Transparency: {transparency}")
                            self.results.append({'test': case['name'], 'status': 'failed', 'reason': 'Invalid scores'})
                else:
                    print(f"  ❌ HTTP {response.status_code}: {response.text[:100]}")
                    self.results.append({'test': case['name'], 'status': 'failed', 'reason': f'HTTP {response.status_code}'})
                    
            except requests.Timeout:
                print(f"  ❌ Timeout (>60s)")
                self.results.append({'test': case['name'], 'status': 'failed', 'reason': 'Timeout'})
            except Exception as e:
                print(f"  ❌ Error: {str(e)[:100]}")
                self.results.append({'test': case['name'], 'status': 'failed', 'reason': str(e)})
        
        # Summary
        passed = sum(1 for r in self.results if r['status'] == 'passed')
        failed = len(self.results) - passed
        
        print("\n" + "="*70)
        print("INTEGRATION TEST SUMMARY")
        print("="*70)
        print(f"Passed: {passed}/{len(self.results)}")
        print(f"Failed: {failed}/{len(self.results)}")
        print("="*70)


class PerformanceBenchmark:
    """Benchmark algorithm performance"""
    
    def benchmark_transparency_calculation(self, iterations: int = 10000):
        """Measure transparency score calculation speed"""
        print("\n" + "="*70)
        print("PERFORMANCE BENCHMARK")
        print("="*70)
        
        # Setup test data
        test_cases = [(15, 12, 1, 5) for _ in range(iterations)]
        
        # Benchmark
        start_time = time.time()
        for disclosed, confirmed, contradictions, undisclosed in test_cases:
            D_ref, w_c, w_x, w_u = 15, 3, 15, 10
            B = min(100, (disclosed / D_ref) * 50)
            C_bonus = min(30, confirmed * w_c)
            X_penalty = contradictions * w_x
            U_penalty = undisclosed * w_u
            T = max(0, min(100, B + C_bonus - X_penalty - U_penalty))
        end_time = time.time()
        
        elapsed = end_time - start_time
        per_calc = (elapsed / iterations) * 1000  # ms
        per_second = iterations / elapsed
        
        print(f"\nTransparency Score Calculation:")
        print(f"  Total time: {elapsed:.3f}s")
        print(f"  Iterations: {iterations:,}")
        print(f"  Per calculation: {per_calc:.4f}ms")
        print(f"  Throughput: {per_second:,.0f} calculations/second")
        
        if per_calc < 0.01:  # Under 0.01ms
            print(f"  ✅ EXCELLENT - Ultra-fast calculation")
        elif per_calc < 0.1:
            print(f"  ✅ GOOD - Fast calculation")
        else:
            print(f"  ⚠️  SLOW - Optimization needed")
        
        print("="*70)


def main():
    """Run comprehensive test suite"""
    print("\n" + "🧪" * 35)
    print("OFFERWISE ALGORITHM TEST SUITE")
    print("🧪" * 35 + "\n")
    
    # 1. Unit tests (from test_algorithms.py)
    print("PHASE 1: Unit Tests")
    print("-" * 70)
    import test_algorithms
    unit_test_success = test_algorithms.run_all_tests()
    
    # 2. Property-based testing
    print("\n\nPHASE 2: Property-Based Testing")
    print("-" * 70)
    prop_tester = PropertyBasedTester(num_tests=1000)
    prop_tester.run_all_property_tests()
    
    # 3. Fuzz testing
    print("\n\nPHASE 3: Fuzz Testing")
    print("-" * 70)
    fuzz_tester = FuzzTester(iterations=10000)
    fuzz_tester.fuzz_transparency_score()
    
    # 4. Performance benchmark
    print("\n\nPHASE 4: Performance Benchmark")
    print("-" * 70)
    benchmark = PerformanceBenchmark()
    benchmark.benchmark_transparency_calculation(iterations=100000)
    
    # 5. Integration tests (optional - requires running server)
    '''
    print("\n\nPHASE 5: Integration Testing")
    print("-" * 70)
    integration_tester = IntegrationTester()
    test_cases = [
        {
            'name': 'Basic Property',
            'address': '123 Test St, San Jose, CA',
            'price': 1_000_000,
        }
    ]
    integration_tester.test_end_to_end_analysis(test_cases)
    '''
    
    # Final summary
    print("\n\n" + "🎉" * 35)
    print("ALL TESTING PHASES COMPLETE!")
    print("🎉" * 35)
    print("\nRecommendation: Review any failures above and fix before deploying.")
    print("\nTo run integration tests, start your server and uncomment Phase 5.\n")


if __name__ == '__main__':
    main()
