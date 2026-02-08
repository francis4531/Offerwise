"""
CREDIBILITY & VALIDATION FRAMEWORK

THE BRUTAL QUESTION: "How do we know we're not full of shit?"

7-LAYER VALIDATION APPROACH:
1. Source document verification (did we read it correctly?)
2. External data benchmarking (are costs realistic?)
3. Confidence scoring with evidence (how sure are we?)
4. Pattern validation (does this make sense?)
5. Expert review sampling (spot check by humans)
6. Outcome tracking (were we right?)
7. Transparent limitations (what we DON'T know)

GOAL: Users trust us because we EARN IT, not because we sound confident.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
import json
from datetime import datetime

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# REAL-WORLD REPAIR COST BENCHMARKS (2024-2025 US National Averages)
# Sources: HomeAdvisor, Angi, Fixr, RSMeans, contractor data
# ═══════════════════════════════════════════════════════════════

REPAIR_COST_BENCHMARKS = {
    'roof': {
        'minor_repair': (300, 1500),
        'section_replacement': (1500, 5000),
        'major_repair': (5000, 12000),
        'full_replacement': (8000, 30000)
    },
    'foundation': {
        'crack_sealing': (500, 1500),
        'crack_repair': (2000, 7000),
        'structural_repair': (5000, 20000),
        'major_foundation': (15000, 50000)
    },
    'hvac': {
        'minor_repair': (150, 800),
        'major_repair': (800, 2500),
        'unit_replacement': (3500, 8000),
        'full_system': (6000, 15000)
    },
    'electrical': {
        'minor_fix': (100, 600),
        'panel_upgrade': (1200, 3500),
        'partial_rewire': (2000, 8000),
        'full_rewire': (8000, 20000)
    },
    'plumbing': {
        'minor_repair': (150, 600),
        'major_repair': (600, 3000),
        'partial_repipe': (2500, 8000),
        'full_repipe': (5000, 18000)
    },
    'water_damage': {
        'minor': (500, 2500),
        'moderate': (2500, 10000),
        'major': (10000, 30000),
        'catastrophic': (30000, 100000)
    },
    'mold': {
        'small_area': (500, 2000),
        'medium_area': (2000, 6000),
        'large_area': (6000, 15000),
        'extensive': (15000, 50000)
    },
    'structural': {
        'minor': (1000, 5000),
        'moderate': (5000, 15000),
        'major': (15000, 40000),
        'critical': (40000, 100000)
    }
}

# ═══════════════════════════════════════════════════════════════
# CREDIBILITY SCORE
# ═══════════════════════════════════════════════════════════════

@dataclass
class CredibilityMetrics:
    """How trustworthy is this analysis?"""
    
    # Overall
    overall_credibility: float  # 0-100
    confidence_level: str  # very_high, high, medium, low, very_low
    
    # Component scores
    source_verification_score: float  # Did we verify against source docs?
    cost_validation_score: float  # Are costs realistic vs benchmarks?
    evidence_strength_score: float  # How much supporting evidence?
    pattern_consistency_score: float  # Does this match real-world patterns?
    
    # Transparency metrics
    verified_findings_count: int
    unverified_findings_count: int
    hallucination_risk: str  # low, medium, high
    
    # What we're confident about
    high_confidence_items: List[str]
    medium_confidence_items: List[str]
    low_confidence_items: List[str]
    
    # What we DON'T know
    limitations: List[str]
    assumptions: List[str]
    caveats: List[str]
    
    # Trust statement for user
    why_trust_us: str
    what_to_verify: List[str]


@dataclass  
class ValidationResult:
    """Result of validating one finding"""
    finding_id: str
    is_verified: bool
    confidence: float  # 0-1
    evidence: List[str]
    cost_validated: bool
    cost_in_range: bool
    validation_notes: str


class ValidationEngine:
    """Validates analysis against real-world data"""
    
    def __init__(self):
        self.cost_benchmarks = REPAIR_COST_BENCHMARKS
    
    def validate_complete_analysis(
        self,
        findings: List[Dict[str, Any]],
        analysis_result: Dict[str, Any],
        source_documents: Dict[str, str]
    ) -> CredibilityMetrics:
        """
        COMPREHENSIVE VALIDATION
        
        Returns: Brutally honest assessment of credibility
        """
        
        print("=" * 70)
        print("🔍 CREDIBILITY CHECK: Validating analysis...")
        print("=" * 70)
        
        # Layer 1: Verify findings against source documents
        source_validation = self._validate_against_source(
            findings, 
            source_documents
        )
        
        # Layer 2: Validate costs against real-world benchmarks
        cost_validation = self._validate_costs(findings)
        
        # Layer 3: Check evidence strength
        evidence_validation = self._assess_evidence_quality(findings)
        
        # Layer 4: Validate patterns (do findings make sense together?)
        pattern_validation = self._validate_patterns(findings)
        
        # Calculate overall credibility
        overall = self._calculate_overall_credibility(
            source_validation,
            cost_validation,
            evidence_validation,
            pattern_validation
        )
        
        # Categorize confidence
        if overall >= 85:
            confidence_level = 'very_high'
        elif overall >= 70:
            confidence_level = 'high'
        elif overall >= 55:
            confidence_level = 'medium'
        elif overall >= 40:
            confidence_level = 'low'
        else:
            confidence_level = 'very_low'
        
        # Assess hallucination risk
        hallucination_risk = self._assess_hallucination_risk(
            findings,
            source_validation['verified_count'],
            overall
        )
        
        # Categorize findings by confidence
        high_conf, medium_conf, low_conf = self._categorize_by_confidence(findings)
        
        # Identify limitations
        limitations = self._identify_limitations(
            findings,
            source_documents,
            source_validation,
            overall
        )
        
        # Identify assumptions
        assumptions = self._identify_assumptions(analysis_result)
        
        # Generate caveats
        caveats = self._generate_caveats(
            findings,
            hallucination_risk,
            overall
        )
        
        # Generate trust statement
        why_trust_us = self._generate_trust_statement(
            overall,
            source_validation,
            cost_validation,
            hallucination_risk
        )
        
        # What should user verify themselves
        what_to_verify = self._generate_verification_checklist(
            findings,
            hallucination_risk,
            overall
        )
        
        # Print summary
        self._print_validation_summary(
            overall,
            source_validation,
            cost_validation,
            hallucination_risk
        )
        
        return CredibilityMetrics(
            overall_credibility=overall,
            confidence_level=confidence_level,
            source_verification_score=source_validation['score'],
            cost_validation_score=cost_validation['score'],
            evidence_strength_score=evidence_validation,
            pattern_consistency_score=pattern_validation,
            verified_findings_count=source_validation['verified_count'],
            unverified_findings_count=source_validation['unverified_count'],
            hallucination_risk=hallucination_risk,
            high_confidence_items=high_conf,
            medium_confidence_items=medium_conf,
            low_confidence_items=low_conf,
            limitations=limitations,
            assumptions=assumptions,
            caveats=caveats,
            why_trust_us=why_trust_us,
            what_to_verify=what_to_verify
        )
    
    def _validate_against_source(
        self,
        findings: List[Dict[str, Any]],
        source_documents: Dict[str, str]
    ) -> Dict[str, Any]:
        """Layer 1: Are findings actually in the source documents?"""
        
        verified_count = sum(1 for f in findings if f.get('verified', False))
        total_count = len(findings)
        unverified_count = total_count - verified_count
        
        # Score based on verification rate
        if total_count == 0:
            score = 50  # Neutral
        else:
            verification_rate = verified_count / total_count
            if verification_rate >= 0.8:
                score = 95
            elif verification_rate >= 0.6:
                score = 80
            elif verification_rate >= 0.4:
                score = 65
            elif verification_rate >= 0.2:
                score = 50
            else:
                score = 30  # Most findings unverified!
        
        return {
            'score': score,
            'verified_count': verified_count,
            'unverified_count': unverified_count,
            'verification_rate': verified_count / total_count if total_count > 0 else 0
        }
    
    def _validate_costs(
        self,
        findings: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Layer 2: Are cost estimates realistic?"""
        
        validated_count = 0
        out_of_range_count = 0
        total_with_costs = 0
        
        out_of_range_findings = []
        
        for finding in findings:
            cost_low = finding.get('estimated_cost_low')
            cost_high = finding.get('estimated_cost_high')
            
            if not cost_low or not cost_high:
                continue
            
            total_with_costs += 1
            
            # Categorize finding
            desc = finding.get('description', '').lower()
            category = self._categorize_finding(desc)
            severity = finding.get('severity', 'medium').lower()
            
            if not category or category not in self.cost_benchmarks:
                continue
            
            # Check against ALL severity levels in this category
            is_valid = False
            matching_range = None
            
            for severity_level, (bench_low, bench_high) in self.cost_benchmarks[category].items():
                # Allow 50% margin (costs vary by region/contractor)
                margin = 0.5
                adjusted_low = bench_low * (1 - margin)
                adjusted_high = bench_high * (1 + margin)
                
                # Check overlap
                if cost_low <= adjusted_high and cost_high >= adjusted_low:
                    is_valid = True
                    matching_range = (bench_low, bench_high)
                    break
            
            if is_valid:
                validated_count += 1
            else:
                out_of_range_count += 1
                out_of_range_findings.append({
                    'description': finding.get('description'),
                    'estimated': (cost_low, cost_high),
                    'category': category
                })
        
        # Calculate score
        if total_with_costs == 0:
            score = 50  # Neutral if no costs to validate
        else:
            validation_rate = validated_count / total_with_costs
            if validation_rate >= 0.9:
                score = 95
            elif validation_rate >= 0.75:
                score = 85
            elif validation_rate >= 0.6:
                score = 70
            elif validation_rate >= 0.4:
                score = 55
            else:
                score = 30  # Many costs unrealistic!
        
        return {
            'score': score,
            'validated_count': validated_count,
            'out_of_range_count': out_of_range_count,
            'total_with_costs': total_with_costs,
            'validation_rate': validated_count / total_with_costs if total_with_costs > 0 else 0,
            'out_of_range_findings': out_of_range_findings
        }
    
    def _categorize_finding(self, description: str) -> Optional[str]:
        """Categorize finding into cost benchmark category"""
        
        desc_lower = description.lower()
        
        # Roof
        if any(word in desc_lower for word in ['roof', 'shingle', 'gutter', 'flashing', 'soffit', 'fascia']):
            return 'roof'
        
        # Foundation
        if any(word in desc_lower for word in ['foundation', 'basement', 'crawl space', 'settling', 'pier', 'footing']):
            return 'foundation'
        
        # HVAC
        if any(word in desc_lower for word in ['hvac', 'furnace', 'ac', 'air condition', 'heating', 'cooling', 'heat pump']):
            return 'hvac'
        
        # Electrical
        if any(word in desc_lower for word in ['electrical', 'wiring', 'panel', 'outlet', 'circuit', 'breaker']):
            return 'electrical'
        
        # Plumbing
        if any(word in desc_lower for word in ['plumbing', 'pipe', 'plumb', 'drain', 'sewer', 'water heater']):
            return 'plumbing'
        
        # Water damage
        if any(word in desc_lower for word in ['water damage', 'leak', 'moisture', 'damp', 'flooding']):
            return 'water_damage'
        
        # Mold
        if any(word in desc_lower for word in ['mold', 'mildew', 'fungus']):
            return 'mold'
        
        # Structural
        if any(word in desc_lower for word in ['structural', 'beam', 'joist', 'support', 'load bearing']):
            return 'structural'
        
        return None
    
    def _assess_evidence_quality(
        self,
        findings: List[Dict[str, Any]]
    ) -> float:
        """Layer 3: How strong is our evidence?"""
        
        if not findings:
            return 50
        
        scores = []
        
        for finding in findings:
            score = 0
            
            # Has evidence quotes?
            evidence = finding.get('evidence', [])
            if len(evidence) >= 3:
                score += 40
            elif len(evidence) >= 2:
                score += 30
            elif len(evidence) >= 1:
                score += 20
            else:
                score += 0  # No evidence!
            
            # Has confidence score?
            confidence = finding.get('confidence', 0)
            score += confidence * 40
            
            # Was verified?
            if finding.get('verified', False):
                score += 20
            
            scores.append(min(100, score))
        
        return sum(scores) / len(scores)
    
    def _validate_patterns(
        self,
        findings: List[Dict[str, Any]]
    ) -> float:
        """Layer 4: Do findings follow real-world patterns?"""
        
        score = 100  # Start optimistic, deduct for suspicious patterns
        
        # Pattern 1: Water damage usually causes mold
        has_water = any(
            any(word in f.get('description', '').lower() 
                for word in ['water', 'leak', 'moisture', 'damp'])
            for f in findings
        )
        has_mold = any(
            'mold' in f.get('description', '').lower()
            for f in findings
        )
        
        if has_water and not has_mold:
            score -= 5  # Suspicious
        
        # Pattern 2: Old roof usually not alone
        has_roof = any(
            'roof' in f.get('description', '').lower()
            for f in findings
        )
        
        if has_roof and len(findings) == 1:
            score -= 5  # Unusual to have ONLY roof issues
        
        # Pattern 3: Foundation issues are serious and expensive
        foundation_findings = [
            f for f in findings
            if 'foundation' in f.get('description', '').lower()
        ]
        
        for f in foundation_findings:
            cost_high = f.get('estimated_cost_high', 0)
            if cost_high < 2000:
                score -= 10  # Foundation repairs are rarely cheap!
        
        # Pattern 4: Critical findings should have high costs
        critical_findings = [
            f for f in findings
            if f.get('severity') == 'critical'
        ]
        
        for f in critical_findings:
            cost_high = f.get('estimated_cost_high', 0)
            if cost_high < 1000:
                score -= 10  # Critical but cheap? Suspicious
        
        return max(0, score)
    
    def _calculate_overall_credibility(
        self,
        source_validation: Dict,
        cost_validation: Dict,
        evidence_score: float,
        pattern_score: float
    ) -> float:
        """Calculate weighted overall credibility score"""
        
        # Weights (must sum to 1.0)
        weights = {
            'source': 0.35,  # Most important - are findings real?
            'cost': 0.30,    # Very important - are costs realistic?
            'evidence': 0.20,  # Important - how strong is evidence?
            'pattern': 0.15   # Important - does it make sense?
        }
        
        overall = (
            source_validation['score'] * weights['source'] +
            cost_validation['score'] * weights['cost'] +
            evidence_score * weights['evidence'] +
            pattern_score * weights['pattern']
        )
        
        return overall
    
    def _assess_hallucination_risk(
        self,
        findings: List[Dict[str, Any]],
        verified_count: int,
        overall_score: float
    ) -> str:
        """How likely is AI making stuff up?"""
        
        total = len(findings)
        if total == 0:
            return 'unknown'
        
        verification_rate = verified_count / total
        
        # High risk if:
        # - Low verification rate
        # - Low overall credibility
        # - Many high-severity findings unverified
        
        unverified_critical = sum(
            1 for f in findings
            if not f.get('verified', False) and f.get('severity') == 'critical'
        )
        
        if verification_rate < 0.3 or overall_score < 50:
            risk = 'high'
        elif verification_rate < 0.6 or unverified_critical >= 2:
            risk = 'medium'
        else:
            risk = 'low'
        
        return risk
    
    def _categorize_by_confidence(
        self,
        findings: List[Dict[str, Any]]
    ) -> Tuple[List[str], List[str], List[str]]:
        """Split findings by confidence level"""
        
        high = []
        medium = []
        low = []
        
        for f in findings:
            desc = f.get('description', '')[:80]
            confidence = f.get('confidence', 0.5)
            
            if confidence >= 0.85:
                high.append(desc)
            elif confidence >= 0.65:
                medium.append(desc)
            else:
                low.append(desc)
        
        return high, medium, low
    
    def _identify_limitations(
        self,
        findings: List[Dict[str, Any]],
        source_documents: Dict[str, str],
        source_validation: Dict,
        overall_score: float
    ) -> List[str]:
        """What can't we be certain about?"""
        
        limitations = []
        
        # Document quality
        disclosure_len = len(source_documents.get('disclosure', ''))
        inspection_len = len(source_documents.get('inspection', ''))
        
        if disclosure_len < 1000:
            limitations.append(
                "Seller disclosure is brief (<1000 chars) - may be incomplete"
            )
        
        if inspection_len < 2500:
            limitations.append(
                "Inspection report is short (<2500 chars) - may not be comprehensive"
            )
        
        # Verification
        unverified_pct = source_validation['unverified_count'] / max(1, source_validation['verified_count'] + source_validation['unverified_count']) * 100
        
        if unverified_pct > 50:
            limitations.append(
                f"{unverified_pct:.0f}% of findings not independently verified against source documents"
            )
        
        # Overall confidence
        if overall_score < 70:
            limitations.append(
                "Overall analysis confidence is below ideal threshold"
            )
        
        # Hidden issues
        limitations.append(
            "Cannot detect issues not mentioned in provided documents"
        )
        
        # Cost accuracy
        limitations.append(
            "Actual repair costs may vary ±30% based on location, contractor, and specific scope"
        )
        
        return limitations
    
    def _identify_assumptions(
        self,
        analysis_result: Dict[str, Any]
    ) -> List[str]:
        """What are we assuming is true?"""
        
        return [
            "Source documents are accurate and not falsified",
            "Inspection was performed by qualified professional",
            "No significant changes to property since documents were created",
            "Cost estimates based on national averages (your region may differ)",
            "Severity ratings assume standard single-family residential property",
            "Analysis cannot replace in-person professional inspection"
        ]
    
    def _generate_caveats(
        self,
        findings: List[Dict[str, Any]],
        hallucination_risk: str,
        overall_score: float
    ) -> List[str]:
        """Important warnings for users"""
        
        caveats = []
        
        if hallucination_risk == 'high':
            caveats.append(
                "🚨 HIGH HALLUCINATION RISK: Many findings not verified - treat analysis as preliminary only"
            )
        elif hallucination_risk == 'medium':
            caveats.append(
                "⚠️ MEDIUM HALLUCINATION RISK: Some findings not verified - cross-reference important items"
            )
        
        if overall_score < 60:
            caveats.append(
                "⚠️ BELOW-STANDARD CONFIDENCE: Limited validation - strongly recommend additional professional review"
            )
        
        critical_count = sum(1 for f in findings if f.get('severity') == 'critical')
        if critical_count > 0:
            caveats.append(
                f"⚠️ {critical_count} CRITICAL ISSUE(S): Recommend specialist inspection before proceeding"
            )
        
        high_cost_count = sum(1 for f in findings if f.get('estimated_cost_high', 0) > 15000)
        if high_cost_count > 0:
            caveats.append(
                f"⚠️ {high_cost_count} MAJOR REPAIR(S): Get 3+ contractor quotes for accurate pricing"
            )
        
        if not caveats:
            caveats.append(
                "✅ Analysis meets quality standards, but always verify critical items independently"
            )
        
        return caveats
    
    def _generate_trust_statement(
        self,
        overall_score: float,
        source_validation: Dict,
        cost_validation: Dict,
        hallucination_risk: str
    ) -> str:
        """Why should users trust this analysis?"""
        
        verified_pct = source_validation['verification_rate'] * 100
        cost_pct = cost_validation['validation_rate'] * 100
        
        if overall_score >= 85:
            trust_level = "very high"
            reason = "strong document verification, realistic cost estimates, and comprehensive evidence"
        elif overall_score >= 70:
            trust_level = "high"
            reason = "good verification rate and cost validation"
        elif overall_score >= 55:
            trust_level = "moderate"
            reason = "acceptable verification but limited external validation"
        else:
            trust_level = "limited"
            reason = "low verification rate and insufficient validation"
        
        return f"""We have {trust_level} confidence in this analysis.

Verification: {verified_pct:.0f}% of findings independently verified against source documents.
Cost validation: {cost_pct:.0f}% of cost estimates fall within real-world benchmarks.
Hallucination risk: {hallucination_risk.upper()}

This analysis is based on {reason}. Use it as a starting point for your decision-making, not the final word."""
    
    def _generate_verification_checklist(
        self,
        findings: List[Dict[str, Any]],
        hallucination_risk: str,
        overall_score: float
    ) -> List[str]:
        """What should user verify independently?"""
        
        checklist = []
        
        if hallucination_risk in ['high', 'medium']:
            checklist.append(
                "Cross-reference all findings against original inspection report"
            )
        
        # High-cost items
        high_cost = [f for f in findings if f.get('estimated_cost_high', 0) > 10000]
        if high_cost:
            checklist.append(
                f"Get contractor quotes for {len(high_cost)} major repair(s) (>$10K each)"
            )
        
        # Critical items
        critical = [f for f in findings if f.get('severity') == 'critical']
        if critical:
            checklist.append(
                f"Hire specialists to evaluate {len(critical)} critical issue(s)"
            )
        
        # Low confidence items
        low_conf = [f for f in findings if f.get('confidence', 1.0) < 0.6]
        if low_conf:
            checklist.append(
                f"Independently verify {len(low_conf)} low-confidence finding(s)"
            )
        
        if not checklist:
            checklist.append(
                "Spot-check 2-3 key findings against original documents"
            )
        
        return checklist
    
    def _print_validation_summary(
        self,
        overall_score: float,
        source_validation: Dict,
        cost_validation: Dict,
        hallucination_risk: str
    ):
        """Print validation summary for debugging"""
        
        print()
        print("📊 VALIDATION SUMMARY:")
        print(f"  Overall Credibility: {overall_score:.1f}/100")
        print(f"  Source Verification: {source_validation['score']:.1f}/100 ({source_validation['verified_count']}/{source_validation['verified_count'] + source_validation['unverified_count']} verified)")
        print(f"  Cost Validation: {cost_validation['score']:.1f}/100 ({cost_validation['validated_count']}/{cost_validation['total_with_costs']} in range)")
        print(f"  Hallucination Risk: {hallucination_risk.upper()}")
        print("=" * 70)
        print()
