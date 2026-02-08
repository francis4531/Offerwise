"""
OfferWise Cross-Reference Engine
Compares seller disclosures against inspection findings to identify contradictions,
undisclosed issues, and validate seller transparency
"""

from typing import List, Tuple
from dataclasses import dataclass
from document_parser import (
    DisclosureItem, InspectionFinding, CrossReferenceMatch,
    PropertyDocument, IssueCategory, Severity
)
import re
import logging


@dataclass
class CrossReferenceReport:
    """Complete cross-reference analysis between disclosure and inspection"""
    property_address: str
    total_disclosures: int
    total_findings: int
    contradictions: List[CrossReferenceMatch]
    undisclosed_issues: List[CrossReferenceMatch]
    confirmed_disclosures: List[CrossReferenceMatch]
    disclosed_not_found: List[CrossReferenceMatch]
    transparency_score: float  # 0-100, higher is better
    risk_score: float  # 0-100, higher is worse
    summary: str


class CrossReferenceEngine:
    """
    Compares disclosures against inspection findings to identify:
    1. Contradictions (seller said No, inspector found Yes)
    2. Undisclosed issues (inspector found, seller didn't mention)
    3. Confirmed disclosures (seller disclosed, inspector confirmed)
    4. Disclosed but not found (seller mentioned, inspector didn't see)
    """

    def __init__(self):
        self.category_keywords = self._load_category_keywords()
        self.semantic_similarity_threshold = 0.6
        self._non_issue_filters = self._build_non_issue_filters()

    @staticmethod
    def _build_non_issue_filters():
        """
        Quality filters to prevent non-findings from being flagged as undisclosed issues.
        v5.59.4+: Filters educational text, negations, AI commentary, recommendations.
        """
        import re
        return [
            # Inspector explicitly says NO problem found
            re.compile(r'\b(does not (show|appear|indicate|reveal|have)|did not (show|find|observe|note|reveal)|no (significant|major|notable|visible|apparent|evidence of|signs of)|not (observed|found|noted|detected|present)|appears? (to be )?(in )?(good|satisfactory|acceptable|normal|adequate)|no (issue|problem|defect|damage|concern|leak|crack|stain|rust)s?\b)', re.IGNORECASE),
            # Generic educational/definitional text (not property-specific)
            re.compile(r'\b(is a (colorless|type of|common|natural)|has been (linked|associated|connected) to|is (considered|known|defined|classified) (by|as|to)|can cause|refers to|is known to|are an indication that|is designed to|is intended to|should be|is recommended|is typically)\b', re.IGNORECASE),
            # Form field labels and checkbox artifacts
            re.compile(r'^[A-Z\s]{2,}:\s*(None|N/?A|No|Yes|See|Refer)\s*$', re.IGNORECASE),
            # Too short to be meaningful (parsing artifacts)
            re.compile(r'^.{0,20}$'),
            # "Yes/No" checkbox text concatenated with descriptions
            re.compile(r'^(Yes|No)\s+(The |This |It |There )', re.IGNORECASE),
            # AI-generated commentary leaked into finding text
            re.compile(r'TRANSPARENCY CONCERN|DISCLOSURE (ISSUE|CONCERN|NOTE)|MATERIAL DEFECT|IMPORTANT NOTE|NOTE:|SUMMARY:|RECOMMENDATION:', re.IGNORECASE),
            # Negotiation recommendations / action items (not findings)
            re.compile(r'\b(credits? for (major|critical|necessary)|price reduction (reflecting|of|for)|completion of (critical|major|necessary) repairs|before closing|request (that|the) seller|negotiate|walk away|contingency)\b', re.IGNORECASE),
            # Risk severity descriptions (describing HOW bad, not WHAT is wrong)
            re.compile(r'^(system is at|is at immediate|immediate failure|is a safety concern|poses? a? ?(serious|significant|immediate) (risk|danger|threat|concern))', re.IGNORECASE),
        ]

    def _is_non_issue(self, description: str) -> bool:
        """Check if a finding description is NOT an actual property issue."""
        return any(p.search(description) for p in self._non_issue_filters)

    def _load_category_keywords(self):
        """Keywords for matching disclosures to findings"""
        return {
            'foundation': ['foundation', 'basement', 'crawlspace', 'structural', 'slab', 'pier', 'footing', 
                          'crack', 'settling', 'subsidence', 'concrete'],
            'roof': ['roof', 'shingle', 'flashing', 'gutter', 'downspout', 'soffit', 'fascia', 
                    'leak', 'membrane', 'tile', 'composition'],
            'plumbing': ['plumbing', 'pipe', 'sewer', 'drain', 'water', 'leak', 'faucet', 'toilet',
                        'septic', 'line', 'backup', 'polybutylene'],
            'electrical': ['electrical', 'wiring', 'panel', 'outlet', 'circuit', 'breaker', 'gfci',
                          'aluminum', 'knob', 'tube', 'federal pacific'],
            'hvac': ['heating', 'cooling', 'furnace', 'air conditioning', 'hvac', 'ac', 'duct',
                    'boiler', 'heat pump', 'thermostat'],
            'water_damage': ['water', 'moisture', 'leak', 'stain', 'damp', 'wet', 'flood', 'mold',
                           'mildew', 'intrusion'],
            'pest': ['termite', 'pest', 'rodent', 'insect', 'wood destroying', 'infestation',
                    'damage', 'droppings'],
            'structural': ['structural', 'beam', 'joist', 'support', 'load bearing', 'framing',
                          'settlement', 'sagging']
        }

    def cross_reference(
        self, 
        disclosure_doc: PropertyDocument, 
        inspection_doc: PropertyDocument
    ) -> CrossReferenceReport:
        """
        Perform complete cross-reference analysis.
        
        Args:
            disclosure_doc: Parsed seller disclosure document
            inspection_doc: Parsed inspection report document
            
        Returns:
            CrossReferenceReport with all matches and analysis
        """
        
        # CRITICAL FIX: Handle missing/minimal disclosure (Bug #27)
        disclosure_missing = False
        if not disclosure_doc or not disclosure_doc.disclosure_items or len(disclosure_doc.disclosure_items) == 0:
            disclosure_missing = True
            logging.warning("Disclosure document is missing or empty")
        
        # Check for minimal/bank-owned disclosure
        if disclosure_doc and hasattr(disclosure_doc, 'content'):
            content_lower = disclosure_doc.content.lower() if disclosure_doc.content else ""
            if any(keyword in content_lower for keyword in [
                'no disclosure', 'bank-owned', 'foreclosure', 'sold as-is', 
                'seller declined', 'not available', 'reo property'
            ]):
                disclosure_missing = True
                logging.warning("Disclosure indicates bank-owned or no disclosure provided")
        
        # If disclosure missing, return special result
        if disclosure_missing:
            undisclosed = []
            for finding in inspection_doc.inspection_findings:
                desc = finding.description or ''
                if self._is_non_issue(desc):
                    logging.info(f"🧹 Cross-ref filter (no-disclosure): skipping non-issue: {desc[:80]}...")
                    continue
                    
                undisclosed.append(CrossReferenceMatch(
                    disclosure_item=None,
                    inspection_finding=finding,
                    match_type="undisclosed",
                    confidence=1.0,
                    explanation=f"Seller disclosure not provided (common in foreclosures/bank-owned properties). Inspector found {finding.severity.value} issue.",
                    risk_impact="increases_risk"
                ))
            
            # Calculate transparency score (low due to missing disclosure)
            transparency_score = 25
            
            return CrossReferenceReport(
                property_address=inspection_doc.property_address if inspection_doc else "",
                total_disclosures=0,  # No disclosure provided
                total_findings=len(inspection_doc.inspection_findings) if inspection_doc else 0,
                contradictions=[],
                undisclosed_issues=undisclosed,  # FIXED: was 'undisclosed'
                confirmed_disclosures=[],  # FIXED: was 'confirmed'
                disclosed_not_found=[],
                transparency_score=transparency_score,
                risk_score=75,  # High risk due to no disclosure
                summary=f"Seller disclosure not provided. Common in foreclosure/bank-owned properties. All {len(undisclosed)} inspection findings are undisclosed. Exercise extreme caution."
            )
        
        # Normal cross-reference analysis continues
        contradictions = []
        undisclosed = []
        confirmed = []
        disclosed_not_found = []
        
        logging.info(f"🔍 Starting cross-reference with {len(disclosure_doc.disclosure_items)} disclosure items")

        # Match each disclosure to findings
        for disclosure in disclosure_doc.disclosure_items:
            matches = self._find_related_findings(disclosure, inspection_doc.inspection_findings)
            
            logging.info(f"  📝 Disclosure: {disclosure.category} | disclosed={disclosure.disclosed} | matches={len(matches)}")
            
            if disclosure.disclosed:  # Seller said YES (issue exists)
                if matches:
                    # Seller disclosed, inspector confirmed
                    for finding in matches:
                        confirmed.append(CrossReferenceMatch(
                            disclosure_item=disclosure,
                            inspection_finding=finding,
                            match_type="consistent",
                            confidence=0.9,
                            explanation=f"Seller disclosed {disclosure.category} issue, confirmed by inspection",
                            risk_impact="neutral"
                        ))
                else:
                    # Seller disclosed, inspector didn't find it
                    # Could be: (1) repaired, (2) inspector missed it, (3) seller over-disclosed
                    disclosed_not_found.append(CrossReferenceMatch(
                        disclosure_item=disclosure,
                        inspection_finding=None,
                        match_type="disclosed_not_found",
                        confidence=0.7,
                        explanation=f"Seller disclosed {disclosure.category} issue but inspector did not note it. "
                                  f"May have been repaired or outside inspection scope.",
                        risk_impact="decreases_risk"  # Good sign - seller was proactive
                    ))
            else:  # Seller said NO (no issue)
                if matches:
                    # CONTRADICTION: Seller said no, inspector found issue
                    for finding in matches:
                        severity_multiplier = {
                            Severity.CRITICAL: 1.0,
                            Severity.MAJOR: 0.9,
                            Severity.MODERATE: 0.7,
                            Severity.MINOR: 0.5
                        }.get(finding.severity, 0.7)
                        
                        contradictions.append(CrossReferenceMatch(
                            disclosure_item=disclosure,
                            inspection_finding=finding,
                            match_type="contradiction",
                            confidence=0.85 * severity_multiplier,
                            explanation=f"Seller stated no {disclosure.category} issues, but inspector found: "
                                      f"{finding.description}. This is a {finding.severity.value} concern.",
                            risk_impact="increases_risk"
                        ))
                else:
                    # GOOD: Seller said NO, inspector confirmed NO issues
                    # This is honest disclosure and should boost transparency!
                    confirmed.append(CrossReferenceMatch(
                        disclosure_item=disclosure,
                        inspection_finding=None,
                        match_type="consistent",
                        confidence=1.0,
                        explanation=f"Seller accurately stated no {disclosure.category} issues - confirmed by inspection",
                        risk_impact="decreases_risk"
                    ))

        # Find undisclosed issues (inspector found, no matching disclosure)
        # v5.59.4+: Quality filter - only flag REAL problems, not educational text or "no issue" findings
        
        for finding in inspection_doc.inspection_findings:
            matching_disclosures = self._find_related_disclosures(
                finding, 
                disclosure_doc.disclosure_items
            )
            
            if not matching_disclosures:
                # Quality check: is this actually describing a real problem?
                desc = finding.description or ''
                
                if self._is_non_issue(desc):
                    logging.info(f"🧹 Cross-ref filter: skipping non-issue: {desc[:80]}...")
                    continue
                
                # Inspector found real issue with no related disclosure
                undisclosed.append(CrossReferenceMatch(
                    disclosure_item=None,
                    inspection_finding=finding,
                    match_type="undisclosed",
                    confidence=0.8,
                    explanation=f"Inspector found {finding.severity.value} {finding.category.value} issue "
                              f"with no corresponding seller disclosure: {finding.description}",
                    risk_impact="increases_risk"
                ))

        # Calculate scores
        transparency_score = self._calculate_transparency_score(
            len(disclosure_doc.disclosure_items),
            len(confirmed),
            len(contradictions),
            len(undisclosed)
        )
        
        risk_score = self._calculate_risk_score(
            contradictions,
            undisclosed,
            inspection_doc.inspection_findings
        )

        # Generate summary
        summary = self._generate_summary(
            contradictions, undisclosed, confirmed, disclosed_not_found,
            transparency_score, risk_score
        )

        return CrossReferenceReport(
            property_address=disclosure_doc.property_address or inspection_doc.property_address,
            total_disclosures=len(disclosure_doc.disclosure_items),
            total_findings=len(inspection_doc.inspection_findings),
            contradictions=contradictions,
            undisclosed_issues=undisclosed,
            confirmed_disclosures=confirmed,
            disclosed_not_found=disclosed_not_found,
            transparency_score=transparency_score,
            risk_score=risk_score,
            summary=summary
        )

    def _find_related_findings(
        self, 
        disclosure: DisclosureItem, 
        findings: List[InspectionFinding]
    ) -> List[InspectionFinding]:
        """Find inspection findings related to a disclosure item"""
        related = []
        
        disclosure_text = f"{disclosure.category} {disclosure.question} {disclosure.details or ''}".lower()
        
        for finding in findings:
            finding_text = f"{finding.category.value} {finding.description} {finding.location}".lower()
            
            # Check for keyword overlap
            if self._has_keyword_overlap(disclosure_text, finding_text):
                related.append(finding)
                continue
            
            # Special case: if disclosure is about foundation and finding has 'foundation' or 'structural'
            if 'foundation' in disclosure_text or 'structural' in disclosure_text:
                if 'foundation' in finding_text or 'structural' in finding_text or 'crack' in finding_text:
                    related.append(finding)
                    continue
            
            # Check for any shared category keywords
            for category, keywords in self.category_keywords.items():
                disclosure_has = any(kw in disclosure_text for kw in keywords)
                finding_has = any(kw in finding_text for kw in keywords)
                if disclosure_has and finding_has:
                    related.append(finding)
                    break
        
        return related

    def _find_related_disclosures(
        self, 
        finding: InspectionFinding, 
        disclosures: List[DisclosureItem]
    ) -> List[DisclosureItem]:
        """Find disclosure items related to an inspection finding"""
        related = []
        
        finding_text = f"{finding.category.value} {finding.description} {finding.location}".lower()
        
        for disclosure in disclosures:
            disclosure_text = f"{disclosure.category} {disclosure.question} {disclosure.details or ''}".lower()
            
            if self._has_keyword_overlap(finding_text, disclosure_text):
                related.append(disclosure)
        
        return related

    def _has_keyword_overlap(self, text1: str, text2: str) -> bool:
        """Check if two texts share relevant keywords"""
        # Count keyword matches across all categories
        total_matches = 0
        
        for category, keywords in self.category_keywords.items():
            text1_matches = sum(1 for keyword in keywords if keyword in text1)
            text2_matches = sum(1 for keyword in keywords if keyword in text2)
            
            # If both texts match keywords from same category, that's a strong signal
            if text1_matches > 0 and text2_matches > 0:
                total_matches += min(text1_matches, text2_matches)
        
        # Require at least 2 shared keywords for confidence
        return total_matches >= 2

    def _calculate_transparency_score(
        self, 
        total_disclosures: int,
        confirmed: int,
        contradictions: int,
        undisclosed: int
    ) -> float:
        """
        Calculate seller transparency score (0-100).
        Higher score = more transparent/honest seller.
        
        IMPROVED: Properties with few issues should score HIGH if seller was honest.
        """
        logging.info(f"🔍 Transparency calculation: disclosures={total_disclosures}, confirmed={confirmed}, contradictions={contradictions}, undisclosed={undisclosed}")
        
        # CRITICAL FIX: If there are no contradictions and no undisclosed issues,
        # the seller is being transparent! Score should be high.
        
        # Perfect transparency: No contradictions, no undisclosed issues
        if contradictions == 0 and undisclosed == 0:
            # If seller provided some disclosures, give bonus
            if total_disclosures >= 5:
                logging.info(f"✅ Perfect transparency: {total_disclosures} disclosures, all confirmed → 100")
                return 100.0  # Perfect transparency
            elif total_disclosures >= 3:
                logging.info(f"✅ Very good transparency: {total_disclosures} disclosures, all confirmed → 85")
                return 85.0   # Very good transparency
            else:
                logging.info(f"✅ Good transparency: {total_disclosures} disclosures, all confirmed → 75")
                return 75.0   # Good transparency (minimal doc but honest)
        
        # If there are issues, use the original calculation
        if total_disclosures == 0:
            # No disclosure provided at all
            if undisclosed > 0:
                logging.warning(f"⚠️ No disclosure, {undisclosed} undisclosed issues → 0")
                return 0.0  # Bank-owned or seller hiding everything
            else:
                logging.info(f"ℹ️ No disclosure, no issues → 50")
                return 50.0  # No disclosure but also no issues found
        
        # Base score from disclosure completeness
        base_score = min(100, (total_disclosures / 15) * 60)  # Increased from 50
        
        # Bonus for confirmed disclosures
        confirmation_bonus = min(35, confirmed * 5)  # Increased from 30 and 3
        
        # Penalty for contradictions
        contradiction_penalty = contradictions * 20  # Increased from 15
        
        # Penalty for undisclosed major issues
        undisclosed_penalty = undisclosed * 15  # Increased from 10
        
        score = base_score + confirmation_bonus - contradiction_penalty - undisclosed_penalty
        
        final_score = max(0, min(100, score))
        logging.info(f"📊 Calculated transparency: base={base_score:.0f} + bonus={confirmation_bonus:.0f} - contradictions={contradiction_penalty:.0f} - undisclosed={undisclosed_penalty:.0f} = {final_score:.0f}")
        
        return final_score

    def _calculate_risk_score(
        self,
        contradictions: List[CrossReferenceMatch],
        undisclosed: List[CrossReferenceMatch],
        all_findings: List[InspectionFinding]
    ) -> float:
        """
        Calculate overall property risk score (0-100).
        Higher score = higher risk.
        """
        risk_points = 0
        
        # Base risk from inspection findings
        severity_weights = {
            Severity.CRITICAL: 20,
            Severity.MAJOR: 10,
            Severity.MODERATE: 5,
            Severity.MINOR: 1,
            Severity.INFORMATIONAL: 0
        }
        
        for finding in all_findings:
            risk_points += severity_weights.get(finding.severity, 5)
        
        # Additional risk from contradictions (trust issues)
        risk_points += len(contradictions) * 15
        
        # Additional risk from undisclosed major issues
        risk_points += len(undisclosed) * 12
        
        # Normalize to 0-100 scale (assume 100 points = max risk)
        risk_score = min(100, (risk_points / 150) * 100)
        
        return risk_score

    def _generate_summary(
        self,
        contradictions: List[CrossReferenceMatch],
        undisclosed: List[CrossReferenceMatch],
        confirmed: List[CrossReferenceMatch],
        disclosed_not_found: List[CrossReferenceMatch],
        transparency_score: float,
        risk_score: float
    ) -> str:
        """Generate human-readable summary of cross-reference analysis"""
        
        parts = []
        
        # Transparency assessment
        if transparency_score >= 80:
            parts.append(f"Seller appears highly transparent (score: {transparency_score:.0f}/100). "
                        f"Disclosures align well with inspection findings.")
        elif transparency_score >= 60:
            parts.append(f"Seller transparency is moderate (score: {transparency_score:.0f}/100). "
                        f"Some discrepancies exist between disclosures and inspection.")
        else:
            parts.append(f"Seller transparency is concerning (score: {transparency_score:.0f}/100). "
                        f"Significant discrepancies found between disclosures and inspection.")
        
        # Contradictions
        if contradictions:
            critical_contradictions = [c for c in contradictions 
                                     if c.inspection_finding.severity == Severity.CRITICAL]
            if critical_contradictions:
                parts.append(f"\n⚠ CRITICAL: {len(critical_contradictions)} critical issue(s) "
                           f"were not disclosed by seller.")
            parts.append(f"\nFound {len(contradictions)} contradiction(s) where seller stated "
                        f"no issue but inspector found problems.")
        
        # Undisclosed issues
        if undisclosed:
            parts.append(f"\nFound {len(undisclosed)} major issue(s) with no corresponding "
                        f"seller disclosure.")
        
        # Confirmed disclosures (positive signal)
        if confirmed:
            parts.append(f"\nSeller properly disclosed {len(confirmed)} issue(s) that were "
                        f"confirmed during inspection.")
        
        # Risk assessment
        if risk_score >= 75:
            parts.append(f"\n\nOverall risk score: {risk_score:.0f}/100 (HIGH RISK). "
                        f"Proceed with extreme caution or consider walking away.")
        elif risk_score >= 50:
            parts.append(f"\n\nOverall risk score: {risk_score:.0f}/100 (MODERATE RISK). "
                        f"Significant issues present. Strong negotiation leverage exists.")
        else:
            parts.append(f"\n\nOverall risk score: {risk_score:.0f}/100 (LOW RISK). "
                        f"Typical issues for property age and condition.")
        
        return ''.join(parts)

    def generate_detailed_report(self, report: CrossReferenceReport) -> str:
        """Generate detailed text report of cross-reference findings"""
        
        lines = []
        lines.append("=" * 80)
        lines.append("OFFERWISE CROSS-REFERENCE ANALYSIS")
        lines.append("=" * 80)
        lines.append(f"\nProperty: {report.property_address}")
        lines.append(f"Transparency Score: {report.transparency_score:.0f}/100")
        lines.append(f"Risk Score: {report.risk_score:.0f}/100")
        lines.append(f"\nTotal Disclosures: {report.total_disclosures}")
        lines.append(f"Total Inspection Findings: {report.total_findings}")
        
        lines.append("\n" + "=" * 80)
        lines.append("EXECUTIVE SUMMARY")
        lines.append("=" * 80)
        lines.append(report.summary)
        
        if report.contradictions:
            lines.append("\n" + "=" * 80)
            lines.append("CONTRADICTIONS (Seller Said No, Inspector Found Yes)")
            lines.append("=" * 80)
            for i, match in enumerate(report.contradictions, 1):
                lines.append(f"\n{i}. {match.explanation}")
                if match.inspection_finding.estimated_cost_low:
                    lines.append(f"   Estimated repair cost: "
                               f"${match.inspection_finding.estimated_cost_low:,.0f} - "
                               f"${match.inspection_finding.estimated_cost_high:,.0f}")
                lines.append(f"   Confidence: {match.confidence * 100:.0f}%")
        
        if report.undisclosed_issues:
            lines.append("\n" + "=" * 80)
            lines.append("UNDISCLOSED ISSUES (Inspector Found, Seller Didn't Mention)")
            lines.append("=" * 80)
            for i, match in enumerate(report.undisclosed_issues, 1):
                lines.append(f"\n{i}. {match.explanation}")
                if match.inspection_finding.estimated_cost_low:
                    lines.append(f"   Estimated repair cost: "
                               f"${match.inspection_finding.estimated_cost_low:,.0f} - "
                               f"${match.inspection_finding.estimated_cost_high:,.0f}")
        
        if report.confirmed_disclosures:
            lines.append("\n" + "=" * 80)
            lines.append("CONFIRMED DISCLOSURES (Seller Disclosed, Inspector Confirmed)")
            lines.append("=" * 80)
            for i, match in enumerate(report.confirmed_disclosures, 1):
                lines.append(f"\n{i}. {match.disclosure_item.question}")
                lines.append(f"   Inspector finding: {match.inspection_finding.description}")
        
        if report.disclosed_not_found:
            lines.append("\n" + "=" * 80)
            lines.append("DISCLOSED BUT NOT FOUND (Seller Mentioned, Inspector Didn't See)")
            lines.append("=" * 80)
            lines.append("\nNote: These may have been repaired or are outside inspection scope.")
            for i, match in enumerate(report.disclosed_not_found, 1):
                lines.append(f"\n{i}. {match.disclosure_item.question}")
                if match.disclosure_item.details:
                    lines.append(f"   Seller explanation: {match.disclosure_item.details}")
        
        return '\n'.join(lines)


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

if __name__ == "__main__":
    from document_parser import DocumentParser
    
    parser = DocumentParser()
    engine = CrossReferenceEngine()
    
    # Sample seller disclosure
    sample_disclosure = """
    SELLER'S DISCLOSURE
    Property: 123 Elm Street, San Jose, CA 95110
    
    1. Foundation cracks? [X] Yes [ ] No
       Explanation: Minor hairline cracks noted in 2020, monitored since then
    
    2. Roof leaks? [ ] Yes [X] No
    
    3. Sewer line issues? [ ] Yes [X] No
    
    4. Water damage? [ ] Yes [X] No
    """
    
    # Sample inspection report
    sample_inspection = """
    INSPECTION REPORT
    Property: 123 Elm Street, San Jose, CA 95110
    
    FOUNDATION:
    Multiple vertical cracks in foundation walls, 1/4" wide, extending floor to ceiling.
    This is a significant structural concern requiring immediate evaluation by structural
    engineer. Estimated cost: $25,000 - $50,000 for foundation repair.
    
    ROOF:
    Minor granule loss. Life expectancy 3-5 years. Recommend budgeting for replacement.
    
    PLUMBING:
    Main sewer line shows severe root intrusion via camera inspection. Full section
    replacement recommended immediately. Estimated cost: $8,000 - $15,000.
    
    BASEMENT:
    Water staining on walls indicates past flooding. Sump pump appears undersized.
    """
    
    # Parse documents
    disclosure_doc = parser.parse_seller_disclosure(sample_disclosure)
    inspection_doc = parser.parse_inspection_report(sample_inspection)
    
    # Cross-reference
    report = engine.cross_reference(disclosure_doc, inspection_doc)
    
    # Print detailed report
    print(engine.generate_detailed_report(report))
    
    print("\n\n" + "=" * 80)
    print("ANALYSIS DETAILS")
    print("=" * 80)
    print(f"\nContradictions found: {len(report.contradictions)}")
    print(f"Undisclosed issues found: {len(report.undisclosed_issues)}")
    print(f"Confirmed disclosures: {len(report.confirmed_disclosures)}")
    print(f"\nTransparency Score: {report.transparency_score:.1f}/100")
    print(f"Risk Score: {report.risk_score:.1f}/100")
