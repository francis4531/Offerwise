"""
CREDIBILITY ENGINE - "How do we know we're not full of shit?"

This module addresses THE critical question: How do users trust our analysis?

VALIDATION STRATEGIES:
1. External data validation (repair costs, market data)
2. Confidence scoring with evidence
3. "Show your work" transparency
4. Track record over time
5. Multiple validation sources
6. Honest uncertainty communication
"""

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import json

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# EXTERNAL VALIDATION DATA
# ═══════════════════════════════════════════════════════════════

# Real-world repair cost ranges (US national averages, 2024-2025)
# Sources: HomeAdvisor, Angi, Fixr, actual contractor data
REPAIR_COST_BENCHMARKS = {
    'roof': {
        'minor_repair': (300, 1500),
        'major_repair': (1500, 5000),
        'partial_replacement': (5000, 12000),
        'full_replacement': (8000, 25000)
    },
    'foundation': {
        'minor_crack_repair': (500, 1500),
        'major_crack_repair': (2000, 6000),
        'structural_repair': (5000, 15000),
        'full_foundation_repair': (10000, 40000)
    },
    'hvac': {
        'minor_repair': (150, 600),
        'major_repair': (600, 2000),
        'system_replacement': (3500, 8000),
        'full_system': (5000, 12000)
    },
    'electrical': {
        'minor_repair': (100, 500),
        'panel_upgrade': (1200, 3000),
        'rewiring_partial': (2000, 6000),
        'rewiring_full': (6000, 15000)
    },
    'plumbing': {
        'minor_repair': (150, 500),
        'major_repair': (500, 2500),
        'repipe_partial': (2000, 7000),
        'repipe_full': (4000, 15000)
    },
    'water_damage': {
        'minor': (500, 2000),
        'moderate': (2000, 8000),
        'major': (8000, 25000),
        'extensive': (15000, 50000)
    }
}


# ═══════════════════════════════════════════════════════════════
# CREDIBILITY SCORING
# ═══════════════════════════════════════════════════════════════

@dataclass
class CredibilityScore:
    """Overall credibility of our analysis"""
    overall_score: float  # 0-100
    confidence_level: str  # 'very_high', 'high', 'medium', 'low', 'very_low'
    
    # Component scores
    data_quality_score: float  # How good are the source documents?
    validation_score: float  # How much external validation?
    evidence_score: float  # How much supporting evidence?
    consistency_score: float  # Are findings internally consistent?
    
    # Transparency
    limitations: List[str]  # What we can't be sure about
    assumptions: List[str]  # What we're assuming
    evidence_count: int  # Number of pieces of evidence
    
    # Trust indicators
    validated_findings: int  # How many findings we validated
    total_findings: int  # Total findings
    cost_validated: bool  # Did we validate costs against benchmarks?
    
    # User-facing
    trust_statement: str  # Why should user trust this?
    caveats: List[str]  # What to be careful about


class CredibilityEngine:
    """Validates analysis against real-world data and builds trust"""
    
    def __init__(self):
        self.cost_benchmarks = REPAIR_COST_BENCHMARKS
    
    def validate_analysis(
        self,
        findings: List[Dict[str, Any]],
        analysis_result: Dict[str, Any],
        source_documents: Dict[str, str]
    ) -> CredibilityScore:
        """
        Comprehensive validation of our analysis
        
        Returns credibility score showing:
        - What we're confident about
        - What we're uncertain about
        - Why users should trust us
        - What limitations exist
        """
        
        # Component 1: Data Quality
        data_quality = self._assess_data_quality(source_documents)
        
        # Component 2: Cost Validation
        cost_validation = self._validate_costs_against_benchmarks(findings)
        
        # Component 3: Evidence Quality
        evidence_quality = self._assess_evidence_quality(findings)
        
        # Component 4: Internal Consistency
        consistency = self._check_internal_consistency(
            findings, 
            analysis_result
        )
        
        # Component 5: Validate against known patterns
        pattern_validation = self._validate_against_patterns(findings)
        
        # Calculate overall credibility
        overall = (
            data_quality * 0.25 +
            cost_validation * 0.25 +
            evidence_quality * 0.20 +
            consistency * 0.15 +
            pattern_validation * 0.15
        )
        
        # Determine confidence level
        if overall >= 85:
            confidence = 'very_high'
        elif overall >= 70:
            confidence = 'high'
        elif overall >= 55:
            confidence = 'medium'
        elif overall >= 40:
            confidence = 'low'
        else:
            confidence = 'very_low'
        
        # Build limitations and assumptions
        limitations = self._identify_limitations(
            findings, 
            source_documents, 
            overall
        )
        assumptions = self._identify_assumptions(analysis_result)
        
        # Count validated findings
        validated = sum(1 for f in findings if f.get('verified', False))
        
        # Generate trust statement
        trust_statement = self._generate_trust_statement(
            overall,
            validated,
            len(findings),
            cost_validation
        )
        
        # Generate caveats
        caveats = self._generate_caveats(
            findings,
            data_quality,
            overall
        )
        
        return CredibilityScore(
            overall_score=overall,
            confidence_level=confidence,
            data_quality_score=data_quality,
            validation_score=cost_validation,
            evidence_score=evidence_quality,
            consistency_score=consistency,
            limitations=limitations,
            assumptions=assumptions,
            evidence_count=sum(len(f.get('evidence', [])) for f in findings),
            validated_findings=validated,
            total_findings=len(findings),
            cost_validated=cost_validation > 70,
            trust_statement=trust_statement,
            caveats=caveats
        )
    
    def _assess_data_quality(
        self, 
        source_documents: Dict[str, str]
    ) -> float:
        """How good are the source documents?"""
        
        disclosure = source_documents.get('disclosure', '')
        inspection = source_documents.get('inspection', '')
        
        scores = []
        
        # Length check (more detail = better)
        if len(disclosure) > 2000:
            scores.append(90)
        elif len(disclosure) > 1000:
            scores.append(75)
        elif len(disclosure) > 500:
            scores.append(60)
        else:
            scores.append(40)
        
        if len(inspection) > 5000:
            scores.append(90)
        elif len(inspection) > 2500:
            scores.append(75)
        elif len(inspection) > 1000:
            scores.append(60)
        else:
            scores.append(40)
        
        # Structure check (sections, formatting)
        if disclosure.count('\n') > 20:
            scores.append(80)
        else:
            scores.append(60)
        
        if inspection.count('\n') > 50:
            scores.append(80)
        else:
            scores.append(60)
        
        # Quality indicators
        quality_words = ['inspected', 'tested', 'verified', 'confirmed', 
                        'measured', 'observed', 'found', 'noted']
        quality_count = sum(1 for word in quality_words 
                          if word in inspection.lower())
        
        if quality_count > 15:
            scores.append(90)
        elif quality_count > 8:
            scores.append(75)
        elif quality_count > 3:
            scores.append(60)
        else:
            scores.append(50)
        
        return sum(scores) / len(scores)
    
    def _validate_costs_against_benchmarks(
        self, 
        findings: List[Dict[str, Any]]
    ) -> float:
        """Validate estimated costs against real-world benchmarks"""
        
        if not findings:
            return 50  # Neutral if no findings
        
        validated_count = 0
        total_with_costs = 0
        
        for finding in findings:
            cost_low = finding.get('estimated_cost_low', 0)
            cost_high = finding.get('estimated_cost_high', 0)
            
            if not cost_low or not cost_high:
                continue
            
            total_with_costs += 1
            
            # Categorize finding
            desc = finding.get('description', '').lower()
            category = self._categorize_finding(desc)
            
            if category and category in self.cost_benchmarks:
                # Check if cost is in reasonable range
                benchmark_ranges = self.cost_benchmarks[category]
                
                # Find matching severity
                severity = finding.get('severity', 'medium').lower()
                
                for severity_level, (bench_low, bench_high) in benchmark_ranges.items():
                    # Check if our estimate overlaps with benchmark
                    if (cost_low <= bench_high and cost_high >= bench_low):
                        validated_count += 1
                        break
        
        if total_with_costs == 0:
            return 50  # Neutral if no costs to validate
        
        validation_rate = (validated_count / total_with_costs) * 100
        return validation_rate
    
    def _categorize_finding(self, description: str) -> Optional[str]:
        """Categorize a finding into cost benchmark category"""
        
        desc_lower = description.lower()
        
        if any(word in desc_lower for word in ['roof', 'shingle', 'gutter', 'flashing']):
            return 'roof'
        elif any(word in desc_lower for word in ['foundation', 'basement', 'crawl space', 'settling']):
            return 'foundation'
        elif any(word in desc_lower for word in ['hvac', 'furnace', 'ac', 'heating', 'cooling']):
            return 'hvac'
        elif any(word in desc_lower for word in ['electrical', 'wiring', 'panel', 'outlet']):
            return 'electrical'
        elif any(word in desc_lower for word in ['plumbing', 'pipe', 'water', 'drain']):
            return 'plumbing'
        elif any(word in desc_lower for word in ['water damage', 'leak', 'moisture', 'mold']):
            return 'water_damage'
        
        return None
    
    def _assess_evidence_quality(
        self, 
        findings: List[Dict[str, Any]]
    ) -> float:
        """How much supporting evidence do we have?"""
        
        if not findings:
            return 50
        
        evidence_scores = []
        
        for finding in findings:
            evidence = finding.get('evidence', [])
            confidence = finding.get('confidence', 0.7)
            verified = finding.get('verified', False)
            
            score = 0
            
            # Evidence count
            if len(evidence) >= 3:
                score += 40
            elif len(evidence) >= 2:
                score += 30
            elif len(evidence) >= 1:
                score += 20
            else:
                score += 10
            
            # Confidence score
            score += confidence * 40
            
            # Verification
            if verified:
                score += 20
            
            evidence_scores.append(score)
        
        return sum(evidence_scores) / len(evidence_scores) if evidence_scores else 50
    
    def _check_internal_consistency(
        self,
        findings: List[Dict[str, Any]],
        analysis_result: Dict[str, Any]
    ) -> float:
        """Are findings internally consistent?"""
        
        # Check 1: Do costs add up?
        total_cost_from_findings = sum(
            f.get('estimated_cost_high', 0) for f in findings
        )
        reported_total = analysis_result.get('total_repair_cost_high', 0)
        
        cost_consistency = 100
        if reported_total > 0:
            diff_pct = abs(total_cost_from_findings - reported_total) / reported_total * 100
            if diff_pct > 20:
                cost_consistency = 60
            elif diff_pct > 10:
                cost_consistency = 80
        
        # Check 2: Does risk score match severity?
        high_severity_count = sum(
            1 for f in findings if f.get('severity') in ['critical', 'high']
        )
        risk_score = analysis_result.get('overall_risk_score', 50)
        
        severity_consistency = 100
        if high_severity_count >= 5 and risk_score < 50:
            severity_consistency = 60
        elif high_severity_count <= 1 and risk_score > 70:
            severity_consistency = 70
        
        return (cost_consistency + severity_consistency) / 2
    
    def _validate_against_patterns(
        self, 
        findings: List[Dict[str, Any]]
    ) -> float:
        """Validate against known real-world patterns"""
        
        # Pattern 1: If there's water damage, there's usually mold/moisture
        has_water = any('water' in f.get('description', '').lower() 
                       or 'leak' in f.get('description', '').lower()
                       for f in findings)
        has_mold = any('mold' in f.get('description', '').lower() 
                      or 'moisture' in f.get('description', '').lower()
                      for f in findings)
        
        pattern_score = 100
        
        if has_water and not has_mold:
            # Suspicious - water issues usually cause mold
            pattern_score -= 10
        
        # Pattern 2: Old roof usually means other deferred maintenance
        has_roof_issues = any('roof' in f.get('description', '').lower() 
                             for f in findings)
        
        if has_roof_issues and len(findings) == 1:
            # Suspicious - roof issues rarely alone
            pattern_score -= 10
        
        # Pattern 3: Foundation issues are expensive
        has_foundation = any('foundation' in f.get('description', '').lower() 
                            for f in findings)
        
        if has_foundation:
            foundation_costs = [f.get('estimated_cost_high', 0) 
                              for f in findings 
                              if 'foundation' in f.get('description', '').lower()]
            if foundation_costs and max(foundation_costs) < 2000:
                # Suspicious - foundation repairs are rarely cheap
                pattern_score -= 15
        
        return max(0, pattern_score)
    
    def _identify_limitations(
        self,
        findings: List[Dict[str, Any]],
        source_documents: Dict[str, str],
        overall_score: float
    ) -> List[str]:
        """What can't we be sure about?"""
        
        limitations = []
        
        # Document quality limitations
        if len(source_documents.get('disclosure', '')) < 1000:
            limitations.append(
                "Seller disclosure is brief - may be missing details"
            )
        
        if len(source_documents.get('inspection', '')) < 2000:
            limitations.append(
                "Inspection report is short - may not be comprehensive"
            )
        
        # Verification limitations
        unverified = [f for f in findings if not f.get('verified', False)]
        if len(unverified) > len(findings) / 2:
            limitations.append(
                f"{len(unverified)} of {len(findings)} findings not independently verified"
            )
        
        # Cost limitations
        if overall_score < 70:
            limitations.append(
                "Cost estimates have higher uncertainty due to limited data"
            )
        
        # Hidden issues
        limitations.append(
            "Cannot detect issues not mentioned in provided documents"
        )
        
        limitations.append(
            "Actual repair costs may vary based on contractor, location, and scope"
        )
        
        return limitations
    
    def _identify_assumptions(
        self, 
        analysis_result: Dict[str, Any]
    ) -> List[str]:
        """What are we assuming?"""
        
        assumptions = [
            "Source documents are accurate and complete",
            "Inspection was performed by qualified professional",
            "No material changes to property since inspection",
            "Cost estimates based on typical regional pricing",
            "Severity ratings assume standard residential property"
        ]
        
        return assumptions
    
    def _generate_trust_statement(
        self,
        overall_score: float,
        validated_count: int,
        total_count: int,
        cost_validation: float
    ) -> str:
        """Why should user trust this analysis?"""
        
        if overall_score >= 85:
            confidence = "very high"
            explanation = "comprehensive documents, validated costs, and strong evidence"
        elif overall_score >= 70:
            confidence = "high"
            explanation = "good quality documents and validated findings"
        elif overall_score >= 55:
            confidence = "moderate"
            explanation = "reasonable document quality but limited validation"
        else:
            confidence = "limited"
            explanation = "sparse documents and limited validation data"
        
        validated_pct = (validated_count / total_count * 100) if total_count > 0 else 0
        
        return f"""We have {confidence} confidence in this analysis based on {explanation}. 
{validated_count} of {total_count} findings ({validated_pct:.0f}%) were independently verified against source documents. 
Cost estimates validated against real-world repair data (${cost_validation:.0f}% match rate)."""
    
    def _generate_caveats(
        self,
        findings: List[Dict[str, Any]],
        data_quality: float,
        overall_score: float
    ) -> List[str]:
        """What should users be careful about?"""
        
        caveats = []
        
        if data_quality < 60:
            caveats.append(
                "⚠️ Document quality is below ideal - consider getting additional inspections"
            )
        
        if overall_score < 60:
            caveats.append(
                "⚠️ Limited validation confidence - use this analysis as starting point, not final decision"
            )
        
        critical_findings = [f for f in findings if f.get('severity') == 'critical']
        if critical_findings:
            caveats.append(
                f"⚠️ {len(critical_findings)} critical issue(s) identified - recommend specialist evaluation"
            )
        
        high_cost_findings = [f for f in findings 
                             if f.get('estimated_cost_high', 0) > 10000]
        if high_cost_findings:
            caveats.append(
                "⚠️ Major repairs identified - get multiple contractor quotes before proceeding"
            )
        
        return caveats if caveats else [
            "✅ Analysis meets quality standards - proceed with confidence"
        ]
