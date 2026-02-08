"""
OfferWise Fix Verification Script
Run this to verify all bugs are fixed
"""

import json
import sys

def check_text_quality(analysis):
    """Check for text quality bugs"""
    issues = []
    
    if 'critical_issues' in analysis:
        for issue_text in analysis['critical_issues']:
            # Check for "cRITICAL" bug
            if 'cRITICAL' in issue_text or 'cRITICAL' in issue_text:
                issues.append(f"❌ TEXT BUG: 'cRITICAL' found in: {issue_text[:80]}")
            
            # Check for severity keywords
            if any(word in issue_text for word in ['CRITICAL', 'MAJOR', 'MODERATE', 'MINOR']):
                issues.append(f"⚠️  SEVERITY LEAK: Keyword found in: {issue_text[:80]}")
            
            # Check for duplicate words
            words = issue_text.split()
            for i in range(len(words) - 1):
                if words[i].lower() == words[i+1].lower():
                    issues.append(f"⚠️  DUPLICATE: '{words[i]}' repeated in: {issue_text[:80]}")
    
    return issues

def check_cost_consistency(analysis):
    """Check for cost consistency bugs"""
    issues = []
    
    if 'category_scores' in analysis:
        for cat in analysis['category_scores']:
            category = cat.get('category', 'unknown')
            score = cat.get('score', 0)
            cost_low = cat.get('estimated_cost_low', 0)
            cost_high = cat.get('estimated_cost_high', 0)
            
            # Check: Critical issues should have realistic costs
            if score >= 75:  # CRITICAL
                if 'foundation' in category.lower() and cost_high < 25000:
                    issues.append(f"❌ COST BUG: Critical {category} only ${cost_high:,} (should be $25K+)")
                
                if 'electrical' in category.lower() and cost_high < 8000:
                    issues.append(f"❌ COST BUG: Critical {category} only ${cost_high:,} (should be $8K+)")
                
                if 'plumbing' in category.lower() and cost_high < 10000:
                    issues.append(f"❌ COST BUG: Critical {category} only ${cost_high:,} (should be $10K+)")
                
                if 'hvac' in category.lower() and cost_high < 6000:
                    issues.append(f"❌ COST BUG: Critical {category} only ${cost_high:,} (should be $6K+)")
            
            # Check: Cost low should not exceed cost high
            if cost_low > cost_high and cost_high > 0:
                issues.append(f"❌ COST BUG: Inverted range in {category}: ${cost_low:,} > ${cost_high:,}")
    
    return issues

def check_price_parsing(analysis, expected_price):
    """Check for price parsing bugs"""
    issues = []
    
    if 'property_price' in analysis:
        actual_price = analysis['property_price']
        if actual_price != expected_price:
            issues.append(f"❌ PRICE BUG: Expected ${expected_price:,}, got ${actual_price:,}")
    else:
        issues.append(f"❌ PRICE BUG: property_price missing from analysis")
    
    return issues

def check_risk_scoring(analysis):
    """Check for risk scoring bugs"""
    issues = []
    
    if 'risk_tier' not in analysis:
        issues.append(f"❌ RISK BUG: risk_tier missing from analysis")
        return issues
    
    risk_tier = analysis['risk_tier']
    
    if 'category_scores' in analysis:
        critical_count = sum(1 for cat in analysis['category_scores'] if cat.get('score', 0) >= 75)
        
        # Multiple critical issues should result in CRITICAL tier
        if critical_count >= 2 and risk_tier != 'CRITICAL':
            issues.append(f"❌ RISK BUG: {critical_count} critical issues but tier is {risk_tier}")
    
    # Risk score should be 0-100
    risk_score = analysis.get('overall_risk_score', 50)
    if risk_score < 0 or risk_score > 100:
        issues.append(f"❌ RISK BUG: Risk score {risk_score} out of range (0-100)")
    
    return issues

def check_offer_calculation(analysis):
    """Check for offer calculation bugs"""
    issues = []
    
    if 'offer_strategy' not in analysis:
        issues.append(f"⚠️  OFFER: offer_strategy missing")
        return issues
    
    strategy = analysis['offer_strategy']
    asking_price = analysis.get('property_price', 0)
    
    if 'recommended_offer' in strategy:
        offer = strategy['recommended_offer']
        
        # Check for NaN or null
        if offer is None or str(offer).lower() == 'nan':
            issues.append(f"❌ OFFER BUG: recommended_offer is NaN/null")
        
        # Offer usually shouldn't exceed asking
        if offer > asking_price * 1.1:  # Allow 10% buffer for bidding wars
            issues.append(f"⚠️  OFFER: Offer ${offer:,} exceeds asking ${asking_price:,}")
    
    if 'discount_from_ask' in strategy:
        discount = strategy['discount_from_ask']
        
        # Discount shouldn't be negative
        if discount < 0:
            issues.append(f"❌ OFFER BUG: Negative discount ${discount:,}")
        
        # Check if breakdown sums correctly
        if 'discount_breakdown' in strategy:
            breakdown = strategy['discount_breakdown']
            total_breakdown = (
                breakdown.get('repair_costs', 0) +
                breakdown.get('risk_premium', 0) +
                breakdown.get('transparency_issues', 0)
            )
            
            # Allow small rounding differences
            if abs(total_breakdown - discount) > 1000:
                issues.append(f"⚠️  OFFER: Discount ${discount:,} doesn't match breakdown ${total_breakdown:,}")
    
    return issues

def verify_analysis(analysis, property_data):
    """Run all verification checks on an analysis"""
    all_issues = []
    
    # Run all checks
    all_issues.extend(check_text_quality(analysis))
    all_issues.extend(check_cost_consistency(analysis))
    all_issues.extend(check_price_parsing(analysis, property_data.get('asking_price', 0)))
    all_issues.extend(check_risk_scoring(analysis))
    all_issues.extend(check_offer_calculation(analysis))
    
    return all_issues

def verify_test_results(results_file):
    """Verify all test results"""
    print(f"\n{'='*70}")
    print(f"VERIFYING FIXES - {results_file}")
    print(f"{'='*70}\n")
    
    with open(results_file, 'r') as f:
        results = json.load(f)
    
    total_analyses = len(results)
    total_issues = 0
    critical_issues = 0
    warnings = 0
    
    issue_types = {}
    
    for i, result in enumerate(results, 1):
        prop = result['property']
        analysis = result.get('result', {})
        
        issues = verify_analysis(analysis, prop)
        
        if issues:
            print(f"\n[{i}/{total_analyses}] {prop['address']}")
            for issue in issues:
                print(f"  {issue}")
                total_issues += 1
                
                if '❌' in issue:
                    critical_issues += 1
                    issue_type = issue.split(':')[0].strip('❌ ')
                    issue_types[issue_type] = issue_types.get(issue_type, 0) + 1
                else:
                    warnings += 1
    
    print(f"\n{'='*70}")
    print(f"VERIFICATION SUMMARY")
    print(f"{'='*70}")
    print(f"Total Analyses: {total_analyses}")
    print(f"Clean Analyses: {total_analyses - len([r for r in results if verify_analysis(r.get('result', {}), r['property'])])} ({(total_analyses - len([r for r in results if verify_analysis(r.get('result', {}), r['property'])]))/total_analyses*100:.1f}%)")
    print(f"Total Issues: {total_issues}")
    print(f"  Critical Bugs: {critical_issues}")
    print(f"  Warnings: {warnings}")
    print(f"{'='*70}")
    
    if issue_types:
        print(f"\nISSUE BREAKDOWN:")
        for issue_type, count in sorted(issue_types.items(), key=lambda x: x[1], reverse=True):
            print(f"  {issue_type}: {count} occurrences")
    
    print(f"\n{'='*70}")
    
    if critical_issues == 0:
        print(f"✅ SUCCESS: No critical bugs found!")
        print(f"{'='*70}\n")
        return True
    else:
        print(f"❌ FAILED: {critical_issues} critical bugs found")
        print(f"{'='*70}\n")
        return False

if __name__ == '__main__':
    import glob
    
    # Find most recent results file
    results_files = glob.glob('test_results/results_*.json')
    
    if not results_files:
        print("❌ No results files found. Run test_runner.py first!")
        sys.exit(1)
    
    latest_results = max(results_files)
    
    success = verify_test_results(latest_results)
    
    sys.exit(0 if success else 1)
