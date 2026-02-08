"""
OfferWise Seller Transparency Scorer™
PATENTABLE INNOVATION: Objective measurement of seller disclosure quality

Patent Claims:
1. Novel algorithm for quantifying seller honesty
2. Multi-source verification methodology
3. Red flag detection system
4. Trust index calculation

Version: 1.0.0
Status: Patent Pending
"""

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class TransparencyGrade(Enum):
    """Letter grades for transparency"""
    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


@dataclass
class RedFlag:
    """A suspicious pattern detected in disclosure"""
    flag_type: str
    severity: str  # 'critical', 'major', 'minor'
    description: str
    evidence: List[str]
    impact: str
    recommendation: str
    
    # SOURCE TRACKING (v5.51.0 - Screenshot Evidence Feature)
    disclosure_page: Optional[int] = None  # Page in seller disclosure
    inspection_page: Optional[int] = None  # Page in inspection report
    source_quote: Optional[str] = None  # Exact quote for highlighting


@dataclass
class TransparencyReport:
    """
    Complete seller transparency analysis
    PATENTABLE: Comprehensive disclosure quality assessment
    """
    property_address: str
    transparency_score: int  # 0-100
    grade: TransparencyGrade
    trust_level: str  # 'high', 'medium', 'low', 'very_low'
    
    # Detailed scores
    omission_score: int
    minimization_score: int
    proactivity_score: int
    consistency_score: int
    
    # Analysis results
    red_flags: List[RedFlag]
    undisclosed_issues: List[Dict[str, Any]]
    minimized_issues: List[Dict[str, Any]]
    proactive_disclosures: List[Dict[str, Any]]
    
    # Recommendations
    risk_adjustment: float  # Additional % to reduce offer
    investigation_recommendations: List[str]
    negotiation_leverage: List[str]
    
    def __str__(self):
        return f"Transparency: {self.score}/100 (Grade {self.grade.value})"


class SellerTransparencyScorer:
    """
    PATENTABLE: System for objectively scoring seller disclosure quality
    
    Innovation: Multi-factor analysis combining:
    1. Disclosure vs inspection comparison
    2. Public records verification
    3. Pattern recognition for deception
    4. Temporal analysis (timing of repairs/disclosures)
    """
    
    def __init__(self):
        # Scoring weights (proprietary/patentable)
        self.weights = {
            'omission': 0.40,      # Most important
            'minimization': 0.25,
            'proactivity': 0.20,
            'consistency': 0.15
        }
        
        # Grade thresholds
        self.grade_thresholds = {
            95: TransparencyGrade.A_PLUS,
            85: TransparencyGrade.A,
            70: TransparencyGrade.B,
            55: TransparencyGrade.C,
            40: TransparencyGrade.D,
            0: TransparencyGrade.F
        }
        
        logger.info("🔍 Seller Transparency Scorer initialized")
    
    def score_transparency(
        self,
        disclosure_doc: Any,
        inspection_doc: Any,
        cross_reference_report: Any,
        property_metadata: Optional[Dict] = None
    ) -> TransparencyReport:
        """
        Calculate comprehensive transparency score
        
        PATENTABLE METHOD: Novel multi-factor scoring algorithm
        
        Args:
            disclosure_doc: Parsed seller disclosure
            inspection_doc: Parsed inspection report
            cross_reference_report: Cross-reference analysis
            property_metadata: Property info, permit history, etc.
            
        Returns:
            TransparencyReport with complete analysis
        """
        
        address = getattr(disclosure_doc, 'property_address', 'Unknown')
        logger.info(f"🔍 Analyzing seller transparency for {address}")
        
        # Step 1: Detect omissions (what seller didn't disclose)
        omissions, omission_score = self._analyze_omissions(
            disclosure_doc,
            inspection_doc,
            cross_reference_report
        )
        
        # Step 2: Detect minimizations (what seller downplayed)
        minimizations, minimization_score = self._analyze_minimizations(
            disclosure_doc,
            inspection_doc,
            cross_reference_report
        )
        
        # Step 3: Score proactivity (what seller voluntarily disclosed)
        proactive, proactivity_score = self._analyze_proactivity(
            disclosure_doc,
            inspection_doc
        )
        
        # Step 4: Check consistency with public records
        consistency_issues, consistency_score = self._analyze_consistency(
            disclosure_doc,
            property_metadata
        )
        
        # Step 5: Detect red flags
        red_flags = self._detect_red_flags(
            omissions,
            minimizations,
            consistency_issues,
            property_metadata,
            inspection_doc
        )
        
        # Calculate composite transparency score
        transparency_score = self._calculate_composite_score(
            omission_score,
            minimization_score,
            proactivity_score,
            consistency_score
        )
        
        # Determine grade and trust level
        grade = self._score_to_grade(transparency_score)
        trust_level = self._calculate_trust_level(transparency_score, red_flags)
        
        # Generate recommendations
        risk_adjustment = self._calculate_risk_adjustment(transparency_score, red_flags)
        investigations = self._recommend_investigations(red_flags, omissions)
        leverage_points = self._identify_leverage_points(omissions, minimizations, red_flags)
        
        report = TransparencyReport(
            property_address=address,
            transparency_score=int(transparency_score),
            grade=grade,
            trust_level=trust_level,
            omission_score=int(omission_score),
            minimization_score=int(minimization_score),
            proactivity_score=int(proactivity_score),
            consistency_score=int(consistency_score),
            red_flags=red_flags,
            undisclosed_issues=omissions,
            minimized_issues=minimizations,
            proactive_disclosures=proactive,
            risk_adjustment=risk_adjustment,
            investigation_recommendations=investigations,
            negotiation_leverage=leverage_points
        )
        
        logger.info(f"✅ Transparency score: {transparency_score:.0f}/100 (Grade {grade.value})")
        logger.info(f"   Red flags: {len(red_flags)}, Omissions: {len(omissions)}, Minimizations: {len(minimizations)}")
        
        return report
    
    def _analyze_omissions(
        self,
        disclosure: Any,
        inspection: Any,
        cross_ref: Any
    ) -> Tuple[List[Dict], float]:
        """
        Detect issues found in inspection but not disclosed
        PATENTABLE: Omission detection algorithm
        """
        
        omissions = []
        
        # Pattern to filter out garbage form field labels like "FINDINGS: None" (v5.52.6)
        garbage_pattern = re.compile(r'^[A-Z\s]+:\s*(None|N/?A|No)\s*$', re.IGNORECASE)
        
        # v5.58.3 + v5.59.8: Quality filter for non-issue findings
        # These patterns indicate the text is NOT describing an actual problem
        non_issue_patterns = [
            # Negation phrases: inspector found NO problem
            re.compile(r'\b(does not (show|appear|indicate|reveal|have)|did not (show|find|observe|note|reveal)|no (significant|major|notable|visible|apparent|evidence of|signs of)|not (observed|found|noted|detected|present)|appears? (to be )?(in )?(good|satisfactory|acceptable|normal|adequate)|no (issue|problem|defect|damage|concern|leak|crack|stain|rust)s?\b)', re.IGNORECASE),
            # Generic definitions (educational text, not findings)
            re.compile(r'\b(is a (colorless|type of|common|natural)|has been (linked|associated|connected) to|is (considered|known|defined|classified) (by|as|to)|can cause|refers to|is known to|are an indication that|is designed to|is intended to|should be|is recommended|is typically)\b', re.IGNORECASE),
            # "Yes/No" checkbox text concatenated with descriptions
            re.compile(r'^(Yes|No)\s+(The |This |It |There )', re.IGNORECASE),
            # Extremely short (likely parsing artifacts)
            re.compile(r'^.{0,20}$'),
            # AI-generated commentary leaked into finding text
            re.compile(r'TRANSPARENCY CONCERN|DISCLOSURE (ISSUE|CONCERN|NOTE)|MATERIAL DEFECT|IMPORTANT NOTE|NOTE:|SUMMARY:|RECOMMENDATION:', re.IGNORECASE),
            # Negotiation recommendations (not findings)
            re.compile(r'\b(credits? for (major|critical|necessary)|price reduction (reflecting|of|for)|completion of (critical|major|necessary) repairs|before closing|request (that|the) seller|negotiate|walk away|contingency)\b', re.IGNORECASE),
            # Risk severity descriptions (not findings)
            re.compile(r'^(system is at|is at immediate|immediate failure|is a safety concern|poses? a? ?(serious|significant|immediate) (risk|danger|threat|concern))', re.IGNORECASE),
        ]
        
        # Get undisclosed issues from cross-reference
        if cross_ref and hasattr(cross_ref, 'undisclosed_issues'):
            undisclosed = cross_ref.undisclosed_issues
            
            for issue in undisclosed:
                # CrossReferenceMatch is a dataclass with nested objects
                # Access inspection_finding attributes directly
                finding = issue.inspection_finding
                disclosure_item = issue.disclosure_item if hasattr(issue, 'disclosure_item') else None
                
                # Skip garbage form field labels (v5.52.6)
                finding_desc = finding.description if finding else 'Unknown'
                if garbage_pattern.match(finding_desc):
                    logger.debug(f"Skipping garbage finding: {finding_desc}")
                    continue
                
                # v5.58.3: Skip non-issue findings (educational text, negations, too short)
                is_non_issue = False
                for pattern in non_issue_patterns:
                    if pattern.search(finding_desc):
                        logger.info(f"🧹 Filtering non-issue finding: {finding_desc[:80]}...")
                        is_non_issue = True
                        break
                if is_non_issue:
                    continue
                
                # Build omission dictionary
                omission_dict = {
                    'finding': finding_desc,
                    'severity': finding.severity.value if finding and finding.severity else 'unknown',
                    'cost': finding.estimated_cost_high if finding and finding.estimated_cost_high else 0,
                    'explanation': issue.explanation if hasattr(issue, 'explanation') else '',
                    # SOURCE TRACKING (v5.51.0 - Screenshot Evidence Feature)
                    'inspection_page': finding.source_page if finding and hasattr(finding, 'source_page') else None,
                    'disclosure_page': disclosure_item.source_page if disclosure_item and hasattr(disclosure_item, 'source_page') else None,
                    'source_quote': finding.source_quote if finding and hasattr(finding, 'source_quote') else None,
                    'category': finding.category.value if finding and hasattr(finding, 'category') and finding.category else 'unknown',
                    'description': finding_desc
                }
                
                # Add suspicious flag based on the dictionary
                omission_dict['suspicious'] = self._is_omission_suspicious(omission_dict)
                
                # For compatibility, also set cost_impact
                omission_dict['cost_impact'] = omission_dict['cost']
                
                omissions.append(omission_dict)
        
        # Score: Start at 100, deduct for omissions
        omission_score = 100.0
        
        for omission in omissions:
            # Deduct more for severe/suspicious omissions
            if omission['severity'] == 'critical':
                omission_score -= 20
            elif omission['severity'] == 'major':
                omission_score -= 15
            elif omission['severity'] == 'moderate':
                omission_score -= 10
            else:
                omission_score -= 5
            
            # Extra deduction if suspicious
            if omission['suspicious']:
                omission_score -= 10
        
        omission_score = max(0, omission_score)
        
        return omissions, omission_score
    
    def _analyze_minimizations(
        self,
        disclosure: Any,
        inspection: Any,
        cross_ref: Any
    ) -> Tuple[List[Dict], float]:
        """
        Detect issues the seller downplayed
        PATENTABLE: Minimization detection algorithm
        """
        
        minimizations = []
        
        # Compare severity: disclosure vs inspection
        if hasattr(disclosure, 'disclosure_items') and hasattr(inspection, 'inspection_findings'):
            for disc_item in disclosure.disclosure_items:
                # Find corresponding inspection findings
                matching_findings = self._find_matching_findings(
                    disc_item,
                    inspection.inspection_findings
                )
                
                for finding in matching_findings:
                    # Check if seller minimized severity
                    if self._is_minimization(disc_item, finding):
                        minimizations.append({
                            'disclosed_as': disc_item.details or 'Minor issue',
                            'actually_is': finding.description,
                            'severity_gap': self._calculate_severity_gap(disc_item, finding),
                            'cost_impact': getattr(finding, 'estimated_cost_high', 0),
                            'category': str(getattr(finding, 'category', 'Unknown'))
                        })
        
        # Score: Start at 100, deduct for minimizations
        minimization_score = 100.0
        
        for mini in minimizations:
            severity_gap = mini['severity_gap']
            
            if severity_gap >= 3:  # e.g., "minor" vs "critical"
                minimization_score -= 20
            elif severity_gap >= 2:  # e.g., "minor" vs "major"
                minimization_score -= 15
            elif severity_gap >= 1:  # e.g., "minor" vs "moderate"
                minimization_score -= 10
        
        minimization_score = max(0, minimization_score)
        
        return minimizations, minimization_score
    
    def _analyze_proactivity(
        self,
        disclosure: Any,
        inspection: Any
    ) -> Tuple[List[Dict], float]:
        """
        Score seller's proactive disclosure
        PATENTABLE: Proactivity scoring method
        """
        
        proactive = []
        
        # Find issues seller disclosed that inspector didn't mention
        if hasattr(disclosure, 'disclosure_items') and hasattr(inspection, 'inspection_findings'):
            for disc_item in disclosure.disclosure_items:
                if disc_item.disclosed:  # Seller said yes to issue
                    # Check if inspector found it
                    found_in_inspection = self._was_found_in_inspection(
                        disc_item,
                        inspection.inspection_findings
                    )
                    
                    if not found_in_inspection:
                        # Seller disclosed something inspector didn't find
                        proactive.append({
                            'item': disc_item.question,
                            'details': disc_item.details,
                            'category': disc_item.category,
                            'bonus_points': 5
                        })
        
        # Score: Start at 50 (neutral), add bonus for proactive
        proactivity_score = 50.0
        
        for item in proactive:
            proactivity_score += item['bonus_points']
        
        # Also add points for detailed disclosures
        if hasattr(disclosure, 'disclosure_items'):
            detailed = sum(1 for item in disclosure.disclosure_items 
                          if item.disclosed and item.details and len(item.details) > 50)
            proactivity_score += detailed * 3
        
        proactivity_score = min(100, proactivity_score)
        
        return proactive, proactivity_score
    
    def _analyze_consistency(
        self,
        disclosure: Any,
        metadata: Optional[Dict]
    ) -> Tuple[List[Dict], float]:
        """
        Check consistency with public records
        PATENTABLE: Cross-source verification method
        """
        
        consistency_issues = []
        
        if not metadata:
            return consistency_issues, 75.0  # Neutral score if no metadata
        
        # Check permit history
        permit_history = metadata.get('permit_history', [])
        
        for permit in permit_history:
            # Check if seller disclosed work done
            work_type = permit.get('work_type', '').lower()
            permit_date = permit.get('date')
            
            # Major work that should have been disclosed
            if any(keyword in work_type for keyword in ['foundation', 'roof', 'electrical', 'plumbing', 'structural']):
                # Check if mentioned in disclosure
                disclosed = self._check_if_work_disclosed(permit, disclosure)
                
                if not disclosed:
                    consistency_issues.append({
                        'permit': permit,
                        'work_type': work_type,
                        'date': permit_date,
                        'cost': permit.get('cost', 0),
                        'reason': 'Major work not disclosed'
                    })
        
        # Score: Start at 100, deduct for inconsistencies
        consistency_score = 100.0
        
        for issue in consistency_issues:
            consistency_score -= 15  # Significant deduction
        
        consistency_score = max(0, consistency_score)
        
        return consistency_issues, consistency_score
    
    def _detect_red_flags(
        self,
        omissions: List[Dict],
        minimizations: List[Dict],
        consistency_issues: List[Dict],
        metadata: Optional[Dict],
        inspection_doc: Any = None
    ) -> List[RedFlag]:
        """
        Detect suspicious patterns indicating dishonesty
        PATENTABLE: Pattern recognition for seller deception
        """
        
        red_flags = []
        
        # RED FLAG 1: Multiple major omissions
        major_omissions = [o for o in omissions if o['severity'] in ['critical', 'major']]
        
        # Filter out garbage entries like "FINDINGS: None", "CONCERNS: None" (v5.52.6)
        garbage_pattern = re.compile(r'^[A-Z\s]+:\s*(None|N/?A|No)\s*$', re.IGNORECASE)
        major_omissions = [o for o in major_omissions if not garbage_pattern.match(o.get('finding', ''))]
        
        if len(major_omissions) >= 2:
            # Get page numbers from first omission for source tracking
            first_omission = major_omissions[0] if major_omissions else {}
            # Filter evidence to remove any remaining garbage entries
            evidence = [o['finding'] for o in major_omissions if not garbage_pattern.match(o.get('finding', ''))]
            red_flags.append(RedFlag(
                flag_type='multiple_major_omissions',
                severity='critical',
                description=f'Seller failed to disclose {len(major_omissions)} major issues',
                evidence=evidence,
                impact='Indicates possible intentional concealment',
                recommendation='Request full disclosure of all known issues. Consider walking away.',
                inspection_page=first_omission.get('inspection_page'),
                disclosure_page=first_omission.get('disclosure_page'),
                source_quote=first_omission.get('source_quote')
            ))
        
        # RED FLAG 2: Suspicious timing (recent repairs not disclosed)
        if metadata and consistency_issues:
            recent_undisclosed = [
                issue for issue in consistency_issues
                if self._is_recent_work(issue.get('date'))
            ]
            
            if recent_undisclosed:
                red_flags.append(RedFlag(
                    flag_type='suspicious_timing',
                    severity='major',
                    description='Recent major work found in permits but not disclosed',
                    evidence=[f"{issue['work_type']} on {issue['date']}" 
                             for issue in recent_undisclosed],
                    impact='Seller likely knows about these issues',
                    recommendation='Confront seller about undisclosed repairs. Request documentation.'
                ))
        
        # RED FLAG 3: Pattern of minimization
        if len(minimizations) >= 3:
            red_flags.append(RedFlag(
                flag_type='pattern_of_minimization',
                severity='major',
                description='Seller consistently downplayed issue severity',
                evidence=[f"{m['disclosed_as']} (actually: {m['actually_is']})" 
                         for m in minimizations[:3]],
                impact='Indicates untrustworthiness',
                recommendation='Assume more issues are minimized. Get specialist inspections.'
            ))
        
        # RED FLAG 4: High-cost undisclosed issues
        expensive_omissions = [o for o in omissions if o['cost_impact'] > 10000]
        if expensive_omissions:
            total_cost = sum(o['cost_impact'] for o in expensive_omissions)
            first_expensive = expensive_omissions[0] if expensive_omissions else {}
            red_flags.append(RedFlag(
                flag_type='expensive_omissions',
                severity='critical',
                description=f'${total_cost:,.0f} in undisclosed repairs',
                evidence=[f"{o['finding']}: ${o['cost_impact']:,.0f}" 
                         for o in expensive_omissions],
                impact='Significant financial risk',
                recommendation=f'Reduce offer by ${total_cost:,.0f} minimum. Request seller credits.',
                inspection_page=first_expensive.get('inspection_page'),
                disclosure_page=first_expensive.get('disclosure_page'),
                source_quote=first_expensive.get('source_quote')
            ))
        
        # RED FLAG 5: No proactive disclosures when inspection found issues
        # CRITICAL FIX: Only flag if inspection actually found issues!
        # If inspection found 0 issues and seller disclosed 0 issues, that's PERFECT alignment, not suspicious
        if not any(omissions) and len(minimizations) == 0:
            # Check if inspection actually found any issues
            inspection_findings_count = 0
            if inspection_doc and hasattr(inspection_doc, 'inspection_findings'):
                inspection_findings_count = len(inspection_doc.inspection_findings or [])
            
            # Only flag if inspection found issues but seller disclosed nothing
            if inspection_findings_count > 0:
                red_flags.append(RedFlag(
                    flag_type='suspiciously_clean',
                    severity='minor',
                    description=f'Seller disclosed no issues despite {inspection_findings_count} inspection findings',
                    evidence=['Zero voluntary disclosures'],
                    impact='May indicate selective disclosure',
                    recommendation='Verify all inspection findings are recent, not long-standing.'
                ))
            # If inspection found 0 issues and seller disclosed 0 issues = Perfect! No red flag.
        
        return red_flags
    
    def _calculate_composite_score(
        self,
        omission_score: float,
        minimization_score: float,
        proactivity_score: float,
        consistency_score: float
    ) -> float:
        """
        Calculate weighted composite transparency score
        PATENTABLE: Proprietary weighting formula
        """
        
        composite = (
            omission_score * self.weights['omission'] +
            minimization_score * self.weights['minimization'] +
            proactivity_score * self.weights['proactivity'] +
            consistency_score * self.weights['consistency']
        )
        
        return max(0, min(100, composite))
    
    def _score_to_grade(self, score: float) -> TransparencyGrade:
        """Convert numeric score to letter grade"""
        for threshold, grade in sorted(self.grade_thresholds.items(), reverse=True):
            if score >= threshold:
                return grade
        return TransparencyGrade.F
    
    def _calculate_trust_level(self, score: float, red_flags: List[RedFlag]) -> str:
        """Calculate overall trust level"""
        critical_flags = sum(1 for f in red_flags if f.severity == 'critical')
        
        if score >= 85 and critical_flags == 0:
            return 'high'
        elif score >= 70 and critical_flags == 0:
            return 'medium'
        elif score >= 55 and critical_flags <= 1:
            return 'low'
        else:
            return 'very_low'
    
    def _calculate_risk_adjustment(self, score: float, red_flags: List[RedFlag]) -> float:
        """
        Calculate additional % to reduce offer due to transparency concerns
        PATENTABLE: Risk adjustment formula
        """
        
        base_adjustment = 0.0
        
        # Base adjustment from score
        if score < 40:
            base_adjustment = 0.05  # 5% reduction
        elif score < 55:
            base_adjustment = 0.03  # 3% reduction
        elif score < 70:
            base_adjustment = 0.01  # 1% reduction
        
        # Additional adjustment for red flags
        critical_flags = sum(1 for f in red_flags if f.severity == 'critical')
        major_flags = sum(1 for f in red_flags if f.severity == 'major')
        
        flag_adjustment = (critical_flags * 0.02) + (major_flags * 0.01)
        
        total_adjustment = base_adjustment + flag_adjustment
        
        return min(total_adjustment, 0.10)  # Cap at 10%
    
    def _recommend_investigations(
        self,
        red_flags: List[RedFlag],
        omissions: List[Dict]
    ) -> List[str]:
        """Generate investigation recommendations"""
        
        recommendations = []
        
        if any(f.flag_type == 'suspicious_timing' for f in red_flags):
            recommendations.append("Request all permit records and repair receipts from last 10 years")
        
        if any(f.flag_type == 'multiple_major_omissions' for f in red_flags):
            recommendations.append("Hire specialist inspectors for all major systems")
        
        if any(f.flag_type == 'expensive_omissions' for f in red_flags):
            recommendations.append("Request seller to provide written disclosure of ALL known issues")
        
        # Category-specific recommendations
        categories_with_issues = set(o.get('category', '') for o in omissions)
        
        if 'foundation' in str(categories_with_issues).lower():
            recommendations.append("Get structural engineer evaluation")
        
        if 'electrical' in str(categories_with_issues).lower():
            recommendations.append("Request electrical safety inspection by licensed electrician")
        
        return recommendations
    
    def _identify_leverage_points(
        self,
        omissions: List[Dict],
        minimizations: List[Dict],
        red_flags: List[RedFlag]
    ) -> List[str]:
        """Identify negotiation leverage points"""
        
        leverage = []
        
        if omissions:
            leverage.append(f"Seller failed to disclose {len(omissions)} issues - request price reduction")
        
        if minimizations:
            leverage.append(f"Seller minimized {len(minimizations)} issues - question their honesty")
        
        if any(f.severity == 'critical' for f in red_flags):
            leverage.append("Critical red flags detected - you have strong negotiating position")
        
        # Calculate total undisclosed cost
        total_undisclosed = sum(o.get('cost_impact', 0) for o in omissions)
        if total_undisclosed > 5000:
            leverage.append(f"${total_undisclosed:,.0f} in undisclosed repairs - demand seller credits")
        
        return leverage
    
    # Helper methods
    
    def _is_omission_suspicious(self, issue: Dict) -> bool:
        """Determine if omission seems intentional"""
        # Obvious issues (visible, expensive) are more suspicious
        cost = issue.get('cost', 0)
        severity = issue.get('severity', 'minor')
        
        return cost > 5000 or severity in ['critical', 'major']
    
    def _generate_omission_explanation(self, issue: Dict) -> str:
        """Generate explanation for why omission occurred"""
        cost = issue.get('cost', 0)
        severity = issue.get('severity', 'minor')
        
        if cost > 10000 or severity == 'critical':
            return "Seller likely knew and intentionally concealed"
        elif cost > 5000 or severity == 'major':
            return "Seller probably knew but failed to disclose"
        else:
            return "Seller may not have been aware"
    
    def _find_matching_findings(self, disc_item: Any, findings: List) -> List:
        """Find inspection findings matching disclosure item"""
        matches = []
        
        disc_category = str(getattr(disc_item, 'category', '')).lower()
        disc_question = str(getattr(disc_item, 'question', '')).lower()
        
        for finding in findings:
            finding_cat = str(getattr(finding, 'category', '')).lower()
            finding_desc = str(getattr(finding, 'description', '')).lower()
            
            # Simple keyword matching
            if disc_category in finding_cat or any(
                word in finding_desc 
                for word in disc_question.split() 
                if len(word) > 4
            ):
                matches.append(finding)
        
        return matches
    
    def _is_minimization(self, disc_item: Any, finding: Any) -> bool:
        """Check if seller minimized issue severity"""
        # Compare disclosure language vs finding severity
        details = str(getattr(disc_item, 'details', '')).lower()
        severity = str(getattr(finding, 'severity', 'minor')).lower()
        
        # Minimization indicators
        minimization_words = ['minor', 'small', 'slight', 'cosmetic', 'normal wear']
        
        has_minimization = any(word in details for word in minimization_words)
        is_serious = severity in ['major', 'critical']
        
        return has_minimization and is_serious
    
    def _calculate_severity_gap(self, disc_item: Any, finding: Any) -> int:
        """Calculate gap between disclosed and actual severity"""
        severity_levels = {'minor': 1, 'moderate': 2, 'major': 3, 'critical': 4}
        
        # Try to infer disclosed severity from details
        details = str(getattr(disc_item, 'details', '')).lower()
        disclosed_severity = 1  # Assume minor if not specified
        
        for level, value in severity_levels.items():
            if level in details:
                disclosed_severity = value
                break
        
        # Get actual severity
        actual_severity_str = str(getattr(finding, 'severity', 'minor')).lower()
        if hasattr(finding.severity, 'value'):
            actual_severity_str = finding.severity.value.lower()
        
        actual_severity = severity_levels.get(actual_severity_str, 2)
        
        return max(0, actual_severity - disclosed_severity)
    
    def _was_found_in_inspection(self, disc_item: Any, findings: List) -> bool:
        """Check if disclosed item was found in inspection"""
        matches = self._find_matching_findings(disc_item, findings)
        return len(matches) > 0
    
    def _check_if_work_disclosed(self, permit: Dict, disclosure: Any) -> bool:
        """Check if permitted work was mentioned in disclosure"""
        work_type = permit.get('work_type', '').lower()
        
        if not hasattr(disclosure, 'disclosure_items'):
            return False
        
        for item in disclosure.disclosure_items:
            if item.disclosed and item.details:
                details = item.details.lower()
                # Check if work type is mentioned
                if any(word in details for word in work_type.split()):
                    return True
        
        return False
    
    def _is_recent_work(self, date_str: Optional[str]) -> bool:
        """Check if work was done recently (last 3 years)"""
        if not date_str:
            return False
        
        try:
            # Parse date
            work_date = datetime.strptime(date_str, '%Y-%m-%d')
            cutoff = datetime.now() - timedelta(days=365*3)
            return work_date > cutoff
        except:
            return False
