"""
OfferWise Property Risk DNA™
PATENTABLE INNOVATION: Multi-dimensional risk encoding system

Patent Claims:
1. Novel method for encoding property risk as vector signature
2. Similarity matching algorithm for property comparison
3. Risk clustering methodology
4. Composite risk scoring framework

Version: 1.0.0
Status: Patent Pending
"""

import numpy as np
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import StandardScaler
import logging

logger = logging.getLogger(__name__)


@dataclass
class RiskVector:
    """Multi-dimensional risk vector components"""
    structural: np.ndarray  # Foundation, roof, framing
    systems: np.ndarray     # Electrical, plumbing, HVAC
    transparency: np.ndarray # Seller honesty, disclosure quality
    temporal: np.ndarray    # Deferred maintenance, time-to-failure
    financial: np.ndarray   # Repair costs, value impact


@dataclass
class PropertyRiskDNA:
    """
    Complete risk signature for a property
    PATENTABLE: Novel encoding methodology
    """
    property_id: str
    property_address: str
    dna_signature: np.ndarray  # 64-dimensional vector
    risk_vectors: RiskVector
    composite_score: float
    risk_category: str
    # Individual vector scores (0-100 scale for display)
    structural_score: float = 0.0
    systems_score: float = 0.0
    transparency_score: float = 0.0
    temporal_score: float = 0.0
    financial_score: float = 0.0
    cluster_id: Optional[int] = None
    
    def __str__(self):
        return f"RiskDNA({self.property_address}, score={self.composite_score:.1f}, category={self.risk_category})"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict"""
        return {
            'property_id': self.property_id,
            'property_address': self.property_address,
            'dna_signature': self.dna_signature.tolist() if isinstance(self.dna_signature, np.ndarray) else self.dna_signature,
            'composite_score': self.composite_score,
            'risk_category': self.risk_category,
            'cluster_id': self.cluster_id,
            # Individual scores for display
            'vector_scores': {
                'structural': round(self.structural_score, 1),
                'systems': round(self.systems_score, 1),
                'transparency': round(self.transparency_score, 1),
                'temporal': round(self.temporal_score, 1),
                'financial': round(self.financial_score, 1)
            },
            'risk_vectors': {
                'structural': self.risk_vectors.structural.tolist() if isinstance(self.risk_vectors.structural, np.ndarray) else self.risk_vectors.structural,
                'systems': self.risk_vectors.systems.tolist() if isinstance(self.risk_vectors.systems, np.ndarray) else self.risk_vectors.systems,
                'transparency': self.risk_vectors.transparency.tolist() if isinstance(self.risk_vectors.transparency, np.ndarray) else self.risk_vectors.transparency,
                'temporal': self.risk_vectors.temporal.tolist() if isinstance(self.risk_vectors.temporal, np.ndarray) else self.risk_vectors.temporal,
                'financial': self.risk_vectors.financial.tolist() if isinstance(self.risk_vectors.financial, np.ndarray) else self.risk_vectors.financial
            }
        }


class PropertyRiskDNAEncoder:
    """
    PATENTABLE: System for encoding property risk as DNA-like signature
    
    Innovation: Multi-dimensional vector encoding that enables:
    1. Property similarity matching
    2. Risk clustering and pattern detection
    3. Market benchmarking
    4. Predictive modeling
    """
    
    def __init__(self):
        self.scaler = StandardScaler()
        self.dna_database: List[PropertyRiskDNA] = []
        self.is_fitted = False
        
        # Risk category thresholds
        # Risk categorization thresholds
        # Higher score = Higher risk (consistent with OfferScore where low score = high risk, but Risk DNA calculates risk additively)
        # Score represents accumulated risk factors (0 = no risk, 100 = maximum risk)
        self.risk_categories = {
            'minimal': (0, 20),      # 0-20: Excellent condition, minimal issues
            'low': (20, 40),         # 20-40: Good condition, minor issues only
            'moderate': (40, 60),    # 40-60: Fair condition, notable issues present
            'elevated': (60, 75),    # 60-75: Poor condition, significant issues
            'high': (75, 90),        # 75-90: Bad condition, major repairs needed
            'critical': (90, 100)    # 90-100: Critical condition, severe issues
        }
        
        logger.info("🧬 Property Risk DNA Encoder initialized")
    
    def encode_property(
        self,
        property_analysis: Dict[str, Any],
        inspection_findings: List[Any],
        cross_reference_report: Any,
        property_metadata: Dict[str, Any]
    ) -> PropertyRiskDNA:
        """
        Encode property into Risk DNA signature
        
        PATENTABLE METHOD: Novel multi-dimensional encoding
        
        Args:
            property_analysis: Complete analysis results
            inspection_findings: List of issues found
            cross_reference_report: Disclosure vs inspection comparison
            property_metadata: Address, price, age, etc.
            
        Returns:
            PropertyRiskDNA with complete signature
        """
        
        logger.info(f"🧬 Encoding Risk DNA for {property_metadata.get('address', 'Unknown')}")
        
        # Step 1: Encode structural risk vector (16 dimensions)
        structural_vector = self._encode_structural_risk(inspection_findings)
        
        # Step 2: Encode systems risk vector (12 dimensions)
        systems_vector = self._encode_systems_risk(inspection_findings)
        
        # Step 3: Encode transparency vector (12 dimensions)
        transparency_vector = self._encode_transparency_risk(cross_reference_report)
        
        # Step 4: Encode temporal risk vector (12 dimensions)
        temporal_vector = self._encode_temporal_risk(inspection_findings, property_metadata)
        
        # Step 5: Encode financial risk vector (12 dimensions)
        financial_vector = self._encode_financial_risk(property_analysis, property_metadata)
        
        # Combine into complete 64-dimensional signature
        dna_signature = np.concatenate([
            structural_vector,
            systems_vector,
            transparency_vector,
            temporal_vector,
            financial_vector
        ])
        
        # Extract overall_risk_score for alignment correction
        overall_risk_score = property_analysis.get('overall_risk_score', None)
        if overall_risk_score is not None:
            logger.info(f"   📊 Overall risk score: {overall_risk_score:.1f}/100")
        
        # Calculate composite risk score with alignment correction
        composite_score = self._calculate_composite_score(
            dna_signature,
            overall_risk_score=overall_risk_score
        )
        
        # Determine risk category
        risk_category = self._categorize_risk(composite_score)
        
        # Calculate individual vector scores for display (0-100 scale)
        structural_score = np.mean(structural_vector) * 100
        systems_score = np.mean(systems_vector) * 100
        transparency_score = np.mean(transparency_vector) * 100
        temporal_score = np.mean(temporal_vector) * 100
        financial_score = np.mean(financial_vector) * 100
        
        # Create Risk DNA object
        risk_dna = PropertyRiskDNA(
            property_id=property_metadata.get('id', 'unknown'),
            property_address=property_metadata.get('address', 'Unknown'),
            dna_signature=dna_signature,
            risk_vectors=RiskVector(
                structural=structural_vector,
                systems=systems_vector,
                transparency=transparency_vector,
                temporal=temporal_vector,
                financial=financial_vector
            ),
            composite_score=composite_score,
            risk_category=risk_category,
            # Add individual scores for UI display
            structural_score=structural_score,
            systems_score=systems_score,
            transparency_score=transparency_score,
            temporal_score=temporal_score,
            financial_score=financial_score
        )
        
        # Add to database for future comparisons
        self.dna_database.append(risk_dna)
        
        logger.info(f"✅ Risk DNA encoded: {composite_score:.1f}/100 ({risk_category})")
        
        return risk_dna
    
    def _encode_structural_risk(self, findings: List[Any]) -> np.ndarray:
        """
        Encode structural issues into 16-dimensional vector
        
        Dimensions:
        0-3: Foundation (severity, location, progression, cost)
        4-7: Roof (severity, age, material, cost)
        8-11: Framing (severity, location, structural_impact, cost)
        12-15: Exterior (severity, coverage, urgency, cost)
        """
        
        vector = np.zeros(16)
        
        # Categorize findings
        foundation_issues = [f for f in findings if 'foundation' in str(getattr(f, 'category', '')).lower()]
        roof_issues = [f for f in findings if 'roof' in str(getattr(f, 'category', '')).lower()]
        framing_issues = [f for f in findings if 'framing' in str(getattr(f, 'description', '')).lower() or 'structural' in str(getattr(f, 'description', '')).lower()]
        exterior_issues = [f for f in findings if 'exterior' in str(getattr(f, 'category', '')).lower() or 'siding' in str(getattr(f, 'description', '')).lower()]
        
        # Encode foundation (dimensions 0-3)
        if foundation_issues:
            vector[0] = self._encode_severity(foundation_issues)
            vector[1] = len(foundation_issues) / 10.0  # Normalized count
            vector[2] = self._encode_urgency(foundation_issues)
            vector[3] = self._encode_cost(foundation_issues)
        
        # Encode roof (dimensions 4-7)
        if roof_issues:
            vector[4] = self._encode_severity(roof_issues)
            vector[5] = len(roof_issues) / 10.0
            vector[6] = self._encode_urgency(roof_issues)
            vector[7] = self._encode_cost(roof_issues)
        
        # Encode framing (dimensions 8-11)
        if framing_issues:
            vector[8] = self._encode_severity(framing_issues)
            vector[9] = len(framing_issues) / 10.0
            vector[10] = self._encode_urgency(framing_issues)
            vector[11] = self._encode_cost(framing_issues)
        
        # Encode exterior (dimensions 12-15)
        if exterior_issues:
            vector[12] = self._encode_severity(exterior_issues)
            vector[13] = len(exterior_issues) / 10.0
            vector[14] = self._encode_urgency(exterior_issues)
            vector[15] = self._encode_cost(exterior_issues)
        
        return vector
    
    def _encode_systems_risk(self, findings: List[Any]) -> np.ndarray:
        """
        Encode systems issues into 12-dimensional vector
        
        Dimensions:
        0-3: Electrical (severity, code_compliance, safety, cost)
        4-7: Plumbing (severity, leak_risk, age, cost)
        8-11: HVAC (severity, efficiency, age, cost)
        """
        
        vector = np.zeros(12)
        
        electrical_issues = [f for f in findings if 'electrical' in str(getattr(f, 'category', '')).lower()]
        plumbing_issues = [f for f in findings if 'plumbing' in str(getattr(f, 'category', '')).lower()]
        hvac_issues = [f for f in findings if 'hvac' in str(getattr(f, 'category', '')).lower()]
        
        # Encode electrical (0-3)
        if electrical_issues:
            vector[0] = self._encode_severity(electrical_issues)
            vector[1] = len(electrical_issues) / 10.0
            vector[2] = self._encode_safety_risk(electrical_issues)
            vector[3] = self._encode_cost(electrical_issues)
        
        # Encode plumbing (4-7)
        if plumbing_issues:
            vector[4] = self._encode_severity(plumbing_issues)
            vector[5] = len(plumbing_issues) / 10.0
            vector[6] = self._encode_urgency(plumbing_issues)
            vector[7] = self._encode_cost(plumbing_issues)
        
        # Encode HVAC (8-11)
        if hvac_issues:
            vector[8] = self._encode_severity(hvac_issues)
            vector[9] = len(hvac_issues) / 10.0
            vector[10] = self._encode_urgency(hvac_issues)
            vector[11] = self._encode_cost(hvac_issues)
        
        return vector
    
    def _encode_transparency_risk(self, cross_ref: Any) -> np.ndarray:
        """
        Encode seller transparency into 12-dimensional vector
        
        Dimensions:
        0-2: Disclosure quality (completeness, detail, proactivity)
        3-5: Contradictions (count, severity, impact)
        6-8: Omissions (count, severity, suspicious)
        9-11: Overall trust (transparency_score, red_flags, confidence)
        """
        
        vector = np.zeros(12)
        
        if cross_ref:
            # Disclosure quality (0-2)
            vector[0] = getattr(cross_ref, 'disclosure_completeness', 0.5)
            vector[1] = getattr(cross_ref, 'disclosure_detail_level', 0.5)
            vector[2] = getattr(cross_ref, 'proactive_disclosure_bonus', 0)
            
            # Contradictions (3-5)
            contradictions = getattr(cross_ref, 'contradictions', [])
            vector[3] = min(len(contradictions) / 10.0, 1.0)
            vector[4] = self._encode_contradiction_severity(contradictions)
            vector[5] = getattr(cross_ref, 'contradiction_impact', 0)
            
            # Omissions (6-8)
            omissions = getattr(cross_ref, 'undisclosed_issues', [])
            vector[6] = min(len(omissions) / 10.0, 1.0)
            vector[7] = self._encode_omission_severity(omissions)
            vector[8] = self._encode_omission_suspicion(omissions)
            
            # Overall trust (9-11)
            vector[9] = getattr(cross_ref, 'transparency_score', 50) / 100.0
            vector[10] = min(len(getattr(cross_ref, 'red_flags', [])) / 5.0, 1.0)
            vector[11] = getattr(cross_ref, 'trust_confidence', 0.5)
        
        return vector
    
    def _encode_temporal_risk(self, findings: List[Any], metadata: Dict) -> np.ndarray:
        """
        Encode time-based risk into 12-dimensional vector
        
        Dimensions:
        0-3: Deferred maintenance (level, urgency, cost, impact)
        4-7: Age-related (property_age, system_ages, expected_failures, timeline)
        8-11: Progression (issue_advancement, acceleration, critical_timeline, urgency)
        """
        
        vector = np.zeros(12)
        
        # Deferred maintenance (0-3)
        deferred = [f for f in findings if self._is_deferred_maintenance(f)]
        vector[0] = min(len(deferred) / 10.0, 1.0)
        vector[1] = self._encode_urgency(deferred) if deferred else 0
        vector[2] = self._encode_cost(deferred) if deferred else 0
        vector[3] = self._calculate_deferred_impact(deferred)
        
        # Age-related (4-7)
        property_age = metadata.get('property_age', 0)
        vector[4] = min(property_age / 100.0, 1.0)  # Normalized age
        vector[5] = self._encode_system_ages(findings)
        vector[6] = self._estimate_failure_probability(findings, property_age)
        vector[7] = self._estimate_failure_timeline(findings)
        
        # Progression risk (8-11)
        vector[8] = self._estimate_issue_progression(findings)
        vector[9] = self._estimate_acceleration_risk(findings)
        vector[10] = self._estimate_critical_timeline(findings)
        vector[11] = self._calculate_temporal_urgency(findings)
        
        return vector
    
    def _encode_financial_risk(self, analysis: Dict, metadata: Dict) -> np.ndarray:
        """
        Encode financial risk into 12-dimensional vector
        
        Dimensions:
        0-3: Repair costs (total, immediate, near-term, long-term)
        4-7: Value impact (price_reduction, marketability, liquidity, appreciation)
        8-11: Budget fit (buyer_budget, cost_ratio, affordability, risk_tolerance)
        """
        
        vector = np.zeros(12)
        
        # Repair costs (0-3) - Handle None values explicitly
        total_cost = analysis.get('total_repair_cost_high') or 0
        property_price = metadata.get('price') or 500000
        
        vector[0] = min(total_cost / property_price, 1.0) if property_price > 0 else 0.0
        vector[1] = self._encode_immediate_costs(analysis)
        vector[2] = self._encode_near_term_costs(analysis)
        vector[3] = self._encode_long_term_costs(analysis)
        
        # Value impact (4-7)
        price_reduction = analysis.get('recommended_price_reduction') or 0
        vector[4] = price_reduction / property_price if property_price > 0 else 0.0
        vector[5] = self._estimate_marketability_impact(analysis)
        vector[6] = self._estimate_liquidity_impact(analysis)
        vector[7] = self._estimate_appreciation_impact(analysis)
        
        # Budget fit (8-11) - FIXED: Clamp all values to 0-1 range
        buyer_budget = metadata.get('buyer_max_budget') or property_price
        vector[8] = min(property_price / buyer_budget, 1.0) if buyer_budget > 0 else 1.0
        vector[9] = min((property_price + total_cost) / buyer_budget, 1.0) if buyer_budget > 0 else 1.0
        vector[10] = self._calculate_affordability_score(property_price, total_cost, buyer_budget)
        vector[11] = min(metadata.get('risk_tolerance') or 0.5, 1.0)
        
        # CRITICAL: Ensure all vector values are in [0, 1] range
        vector = np.clip(vector, 0.0, 1.0)
        
        return vector
    
    def _calculate_composite_score(
        self, 
        dna_signature: np.ndarray,
        overall_risk_score: Optional[float] = None
    ) -> float:
        """
        Calculate composite risk score from DNA signature
        
        TRANSPARENT METHODOLOGY:
        - Weighted average of 5 risk vectors
        - No alignment correction - pure DNA calculation
        - Users can verify the math themselves
        
        Args:
            dna_signature: 64-dimensional risk vector
            overall_risk_score: Overall risk from main scoring model (logged for reference only)
        
        Returns:
            DNA composite score (0-100)
        """
        
        # Extract vectors
        structural = dna_signature[0:16]
        systems = dna_signature[16:28]
        transparency = dna_signature[28:40]
        temporal = dna_signature[40:52]
        financial = dna_signature[52:64]
        
        # Calculate individual vector scores (0-1 scale)
        structural_score = np.mean(structural)
        systems_score = np.mean(systems)
        transparency_score = np.mean(transparency)
        temporal_score = np.mean(temporal)
        financial_score = np.mean(financial)
        
        # Weighted average (weights are proprietary/patentable)
        weights = {
            'structural': 0.30,    # Most important - foundation, roof, framing
            'systems': 0.20,       # Electrical, plumbing, HVAC
            'transparency': 0.20,  # Trust matters! Seller honesty
            'temporal': 0.15,      # Age, deferred maintenance
            'financial': 0.15      # Repair costs, value impact
        }
        
        # Calculate transparent composite score
        dna_composite = (
            structural_score * weights['structural'] * 100 +
            systems_score * weights['systems'] * 100 +
            transparency_score * weights['transparency'] * 100 +
            temporal_score * weights['temporal'] * 100 +
            financial_score * weights['financial'] * 100
        )
        
        dna_composite = min(dna_composite, 100.0)
        
        # Log for transparency
        logger.info(f"   🧬 Risk DNA Composite Calculation:")
        logger.info(f"      Structural: {structural_score*100:.1f} × {weights['structural']} = {structural_score*weights['structural']*100:.1f}")
        logger.info(f"      Systems: {systems_score*100:.1f} × {weights['systems']} = {systems_score*weights['systems']*100:.1f}")
        logger.info(f"      Transparency: {transparency_score*100:.1f} × {weights['transparency']} = {transparency_score*weights['transparency']*100:.1f}")
        logger.info(f"      Temporal: {temporal_score*100:.1f} × {weights['temporal']} = {temporal_score*weights['temporal']*100:.1f}")
        logger.info(f"      Financial: {financial_score*100:.1f} × {weights['financial']} = {financial_score*weights['financial']*100:.1f}")
        logger.info(f"      DNA Composite: {dna_composite:.1f}/100")
        
        # Log overall risk for comparison (informational only)
        if overall_risk_score is not None:
            gap = abs(dna_composite - overall_risk_score)
            logger.info(f"      Overall Risk Score: {overall_risk_score:.1f}/100 (for reference)")
            logger.info(f"      Gap: {gap:.1f} points (DNA focuses on property condition, Overall includes all factors)")
        
        return dna_composite
    
    def _categorize_risk(self, score: float) -> str:
        """Categorize risk based on composite score"""
        for category, (low, high) in self.risk_categories.items():
            if low <= score < high:
                return category
        return 'high'
    
    def find_similar_properties(
        self,
        target_property: PropertyRiskDNA,
        top_k: int = 10
    ) -> List[Tuple[PropertyRiskDNA, float]]:
        """
        Find properties with similar Risk DNA
        PATENTABLE: Similarity matching algorithm
        
        Returns:
            List of (PropertyRiskDNA, similarity_score) tuples
        """
        
        if len(self.dna_database) < 2:
            logger.warning("⚠️ Not enough properties in database for comparison")
            return []
        
        similarities = []
        
        for prop in self.dna_database:
            if prop.property_id == target_property.property_id:
                continue
            
            # Calculate cosine similarity
            similarity = cosine_similarity(
                target_property.dna_signature.reshape(1, -1),
                prop.dna_signature.reshape(1, -1)
            )[0][0]
            
            similarities.append((prop, similarity))
        
        # Sort by similarity (descending)
        similarities.sort(key=lambda x: x[1], reverse=True)
        
        return similarities[:top_k]
    
    def get_market_benchmarks(self, target_property: PropertyRiskDNA) -> Dict[str, Any]:
        """
        Compare property against market benchmarks
        PATENTABLE: Market intelligence methodology
        """
        
        similar = self.find_similar_properties(target_property, top_k=50)
        
        if not similar:
            return {
                'similar_count': 0,
                'message': 'Insufficient data for benchmarking'
            }
        
        # Extract scores from similar properties
        similar_scores = [prop.composite_score for prop, _ in similar]
        
        benchmarks = {
            'similar_count': len(similar),
            'average_risk_score': np.mean(similar_scores),
            'median_risk_score': np.median(similar_scores),
            'percentile_rank': self._calculate_percentile(
                target_property.composite_score,
                similar_scores
            ),
            'category_distribution': self._calculate_category_distribution(similar),
            'comparative_position': self._describe_position(
                target_property.composite_score,
                np.mean(similar_scores)
            )
        }
        
        return benchmarks
    
    # Helper encoding methods
    def _encode_severity(self, findings: List[Any]) -> float:
        """
        Encode severity of findings
        FIXED: Use MAX severity weighted by COUNT to avoid averaging out critical issues
        
        Previous bug: Used MEAN which dampened critical findings
        Example: 1 critical + 2 moderate = mean(1.0, 0.5, 0.5) = 0.67 (WRONG)
        Now: max(1.0) × count_factor = ~0.85 (CORRECT)
        """
        if not findings:
            return 0.0
        
        severity_map = {
            'critical': 1.0,
            'major': 0.75,
            'moderate': 0.50,
            'minor': 0.25,
            'informational': 0.0
        }
        
        severities = []
        for f in findings:
            sev = str(getattr(f, 'severity', 'minor')).lower()
            severities.append(severity_map.get(sev, 0.5))
        
        if not severities:
            return 0.0
        
        # Use MAX severity (worst case) as base
        max_severity = max(severities)
        
        # Weight by count (more issues = higher risk)
        # Cap at 5 findings to prevent over-inflation
        count_weight = min(1.0, len(findings) / 5.0)
        
        # Blend: 70% max severity + 30% count factor
        # This ensures critical findings dominate while multiple issues still compound
        return max_severity * (0.7 + 0.3 * count_weight)
    
    def _encode_cost(self, findings: List[Any]) -> float:
        """
        Encode normalized cost
        IMPROVED: Use category-appropriate normalization caps
        """
        if not findings:
            return 0.0
        
        # Sum total costs (handle None values)
        total_cost = sum((getattr(f, 'estimated_cost_high', 0) or 0) for f in findings)
        
        if total_cost == 0:
            return 0.0
        
        # Normalize based on cost tiers
        # This prevents dampening of high-cost items
        if total_cost >= 50000:
            return 1.0  # $50K+ = maximum cost impact
        elif total_cost >= 25000:
            return 0.8  # $25-50K = high cost
        elif total_cost >= 10000:
            return 0.6  # $10-25K = moderate cost
        elif total_cost >= 5000:
            return 0.4  # $5-10K = low-moderate cost
        elif total_cost >= 2000:
            return 0.25  # $2-5K = low cost
        else:
            return min(total_cost / 2000.0, 0.25)  # <$2K = minimal
    
    def _encode_urgency(self, findings: List[Any]) -> float:
        """
        Encode urgency level
        IMPROVED: Weight by severity to align with new severity encoding
        """
        if not findings:
            return 0.0
        
        # Count findings by severity
        critical_count = sum(1 for f in findings 
                           if str(getattr(f, 'severity', '')).lower() == 'critical')
        major_count = sum(1 for f in findings 
                         if str(getattr(f, 'severity', '')).lower() == 'major')
        
        # Urgency based on worst findings
        if critical_count > 0:
            # Critical findings = maximum urgency
            return min(0.8 + (critical_count * 0.1), 1.0)
        elif major_count > 0:
            # Major findings = high urgency
            return min(0.5 + (major_count * 0.1), 0.8)
        else:
            # Moderate/minor = low urgency
            return min(len(findings) * 0.1, 0.5)
    
    def _encode_safety_risk(self, findings: List[Any]) -> float:
        """Encode safety risk level"""
        if not findings:
            return 0.0
        
        safety_concerns = sum(1 for f in findings if getattr(f, 'safety_concern', False))
        return min(safety_concerns / max(len(findings), 1), 1.0)
    
    def _is_deferred_maintenance(self, finding: Any) -> bool:
        """Check if finding indicates deferred maintenance"""
        description = str(getattr(finding, 'description', '')).lower()
        indicators = ['worn', 'aged', 'old', 'outdated', 'deteriorated', 'neglected']
        return any(ind in description for ind in indicators)
    
    def _encode_contradiction_severity(self, contradictions: List) -> float:
        """Encode severity of contradictions"""
        if not contradictions:
            return 0.0
        # Simplified - would need contradiction objects with severity
        return min(len(contradictions) / 5.0, 1.0)
    
    def _encode_omission_severity(self, omissions: List) -> float:
        """Encode severity of omissions"""
        if not omissions:
            return 0.0
        return min(len(omissions) / 5.0, 1.0)
    
    def _encode_omission_suspicion(self, omissions: List) -> float:
        """Encode suspicion level of omissions"""
        if not omissions:
            return 0.0
        # Higher suspicion if multiple significant omissions
        return min(len(omissions) / 3.0, 1.0)
    
    def _calculate_deferred_impact(self, deferred: List) -> float:
        """Calculate impact of deferred maintenance"""
        return min(len(deferred) / 10.0, 1.0)
    
    def _encode_system_ages(self, findings: List) -> float:
        """Encode average system age"""
        # Simplified - would need age data from findings
        return 0.5  # Default middle age
    
    def _estimate_failure_probability(self, findings: List, property_age: int) -> float:
        """Estimate probability of system failures"""
        # More findings + older property = higher failure risk
        finding_factor = min(len(findings) / 20.0, 1.0)
        age_factor = min(property_age / 100.0, 1.0)
        return (finding_factor + age_factor) / 2.0
    
    def _estimate_failure_timeline(self, findings: List) -> float:
        """Estimate timeline to failures"""
        # Urgent findings = shorter timeline
        urgent = sum(1 for f in findings 
                    if str(getattr(f, 'severity', '')).lower() in ['critical', 'major'])
        return 1.0 - min(urgent / 10.0, 1.0)
    
    def _estimate_issue_progression(self, findings: List) -> float:
        """Estimate how fast issues are progressing"""
        # Simplified - would need historical data
        return 0.5
    
    def _estimate_acceleration_risk(self, findings: List) -> float:
        """Estimate risk of issue acceleration"""
        return min(len(findings) / 15.0, 1.0)
    
    def _estimate_critical_timeline(self, findings: List) -> float:
        """Estimate time until critical failure"""
        critical = sum(1 for f in findings 
                      if str(getattr(f, 'severity', '')).lower() == 'critical')
        return min(critical / 5.0, 1.0)
    
    def _calculate_temporal_urgency(self, findings: List) -> float:
        """Calculate overall temporal urgency"""
        return self._encode_urgency(findings)
    
    def _encode_immediate_costs(self, analysis: Dict) -> float:
        """Encode immediate repair costs"""
        # Costs needed within 30 days
        return 0.0  # Placeholder
    
    def _encode_near_term_costs(self, analysis: Dict) -> float:
        """Encode near-term costs (30-365 days)"""
        return 0.0  # Placeholder
    
    def _encode_long_term_costs(self, analysis: Dict) -> float:
        """Encode long-term costs (1+ years)"""
        return 0.0  # Placeholder
    
    def _estimate_marketability_impact(self, analysis: Dict) -> float:
        """Estimate impact on property marketability"""
        return 0.5  # Placeholder
    
    def _estimate_liquidity_impact(self, analysis: Dict) -> float:
        """Estimate impact on sale speed"""
        return 0.5  # Placeholder
    
    def _estimate_appreciation_impact(self, analysis: Dict) -> float:
        """Estimate impact on future appreciation"""
        return 0.5  # Placeholder
    
    def _calculate_affordability_score(self, price: float, costs: float, budget: float) -> float:
        """Calculate affordability score"""
        total = price + costs
        if budget == 0:
            return 1.0
        return min(total / budget, 1.0)
    
    def _calculate_percentile(self, score: float, population: List[float]) -> int:
        """Calculate percentile rank"""
        if not population:
            return 50
        
        below = sum(1 for s in population if s < score)
        return int((below / len(population)) * 100)
    
    def _calculate_category_distribution(self, similar: List) -> Dict[str, int]:
        """Calculate distribution of risk categories"""
        distribution = {cat: 0 for cat in self.risk_categories.keys()}
        
        for prop, _ in similar:
            distribution[prop.risk_category] += 1
        
        return distribution
    
    def _describe_position(self, target_score: float, average_score: float) -> str:
        """Describe property position relative to market"""
        diff = target_score - average_score
        
        if diff < -15:
            return "Much better than average"
        elif diff < -5:
            return "Better than average"
        elif diff < 5:
            return "About average"
        elif diff < 15:
            return "Worse than average"
        else:
            return "Much worse than average"
