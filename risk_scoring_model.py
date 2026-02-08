"""
OfferWise Risk Scoring Model
Quantifiable, weighted risk assessment across property categories.
Adjusts based on buyer profile to personalize risk perception.
"""

from dataclasses import dataclass
from typing import List, Dict, Optional
from enum import Enum
import re
from document_parser import InspectionFinding, Severity, IssueCategory
from cross_reference_engine import CrossReferenceReport


class RiskCategory(Enum):
    """Major risk assessment categories"""
    FOUNDATION_STRUCTURE = "foundation_structure"
    ROOF_EXTERIOR = "roof_exterior"
    PLUMBING = "plumbing"
    ELECTRICAL = "electrical"
    HVAC_SYSTEMS = "hvac_systems"
    ENVIRONMENTAL = "environmental"
    LEGAL_TITLE = "legal_title"
    INSURANCE_HOA = "insurance_hoa"


@dataclass
class CategoryRiskScore:
    """Risk score for a single category"""
    category: RiskCategory
    score: float  # 0-100
    severity_breakdown: Dict[str, int]  # Count by severity
    estimated_cost_low: float
    estimated_cost_high: float
    key_issues: List[str]
    requires_specialist: bool
    safety_concern: bool
    affects_insurability: bool
    affects_resale: bool
    costs_are_estimates: bool = True  # v5.55.8: True = industry estimate, False = from document


@dataclass
class PropertyRiskScore:
    """Complete risk assessment for a property"""
    property_address: str
    overall_risk_score: float  # 0-100, weighted composite
    category_scores: List[CategoryRiskScore]
    total_repair_cost_low: float
    total_repair_cost_high: float
    deal_breakers: List[str]  # Critical issues that should stop the deal
    negotiation_items: List[str]  # Items that justify price reduction
    walk_away_threshold: float  # Price above which deal doesn't make sense
    buyer_adjusted_score: float  # Risk score adjusted for buyer's profile
    risk_tier: str  # "LOW", "MODERATE", "HIGH", "CRITICAL"


@dataclass
class BuyerProfile:
    """Buyer's risk tolerance and preferences"""
    max_budget: float
    repair_tolerance: str  # "low", "moderate", "high"
    ownership_duration: str  # "<3", "3-7", "7-10", "10+"
    biggest_regret: str  # "overpay", "lose_house", "hidden_issues"
    replaceability: str  # "easy", "somewhat_unique", "very_rare"
    deal_breakers: List[str]  # e.g., "foundation", "insurance", "hoa"


class RiskScoringModel:
    """
    Quantitative risk scoring system.
    Produces reproducible, weighted scores based on inspection findings.
    """

    def __init__(self):
        self.category_weights = self._load_category_weights()
        self.severity_multipliers = self._load_severity_multipliers()
        self.cost_impact_thresholds = self._load_cost_thresholds()

    def _load_category_weights(self) -> Dict[RiskCategory, float]:
        """
        Relative importance of each category.
        Higher weight = bigger impact on overall score.
        """
        return {
            RiskCategory.FOUNDATION_STRUCTURE: 1.5,  # Most critical
            RiskCategory.ROOF_EXTERIOR: 1.2,
            RiskCategory.PLUMBING: 1.1,
            RiskCategory.ELECTRICAL: 1.3,  # Safety implications
            RiskCategory.HVAC_SYSTEMS: 1.0,
            RiskCategory.ENVIRONMENTAL: 1.4,  # Can be deal-breakers
            RiskCategory.LEGAL_TITLE: 1.5,  # Can block sale
            RiskCategory.INSURANCE_HOA: 1.2,  # Affects affordability
        }

    def _load_severity_multipliers(self) -> Dict[Severity, float]:
        """Base score multiplier by severity level"""
        return {
            Severity.CRITICAL: 45.0,  # Increased from 25 - critical issues should dominate
            Severity.MAJOR: 28.0,     # Increased from 15 - major issues are serious
            Severity.MODERATE: 15.0,  # Increased from 8 - moderate still matters
            Severity.MINOR: 5.0,      # Increased from 3 - minor adds up
            Severity.INFORMATIONAL: 0.0
        }

    def _load_cost_thresholds(self) -> Dict[str, float]:
        """Cost thresholds as percentage of purchase price"""
        return {
            'minor': 0.01,      # < 1% of price
            'moderate': 0.03,   # 1-3% of price
            'significant': 0.05, # 3-5% of price
            'major': 0.10       # > 5% of price
        }

    def calculate_risk_score(
        self,
        findings: List[InspectionFinding],
        cross_ref_report: Optional[CrossReferenceReport],
        property_price: float,
        buyer_profile: Optional[BuyerProfile] = None
    ) -> PropertyRiskScore:
        """
        Calculate comprehensive risk score for a property.
        
        Args:
            findings: List of inspection findings
            cross_ref_report: Cross-reference analysis (optional but recommended)
            property_price: Purchase price
            buyer_profile: Buyer's preferences and constraints
            
        Returns:
            PropertyRiskScore with detailed breakdown
        """
        
        # Group findings by category
        category_findings = self._group_by_category(findings)
        
        # Score each category
        category_scores = []
        total_cost_low = 0.0
        total_cost_high = 0.0
        
        for category in RiskCategory:
            cat_findings = category_findings.get(category, [])
            cat_score = self._score_category(category, cat_findings, property_price)
            category_scores.append(cat_score)
            # DEFENSIVE: Handle None values in cost estimates
            cost_low = cat_score.estimated_cost_low if cat_score.estimated_cost_low is not None else 0.0
            cost_high = cat_score.estimated_cost_high if cat_score.estimated_cost_high is not None else 0.0
            total_cost_low += cost_low
            total_cost_high += cost_high
        
        # Calculate weighted overall score
        overall_score = self._calculate_weighted_score(category_scores)
        
        # Apply cross-reference adjustments
        if cross_ref_report:
            overall_score = self._apply_transparency_adjustment(
                overall_score, 
                cross_ref_report
            )
        
        # Identify deal-breakers and negotiation items
        deal_breakers = self._identify_deal_breakers(category_scores, buyer_profile)
        negotiation_items = self._identify_negotiation_items(category_scores, property_price)
        
        # Calculate walk-away threshold
        walk_away = self._calculate_walk_away_threshold(
            property_price,
            total_cost_high,
            overall_score,
            buyer_profile
        )
        
        # Apply buyer profile adjustments
        buyer_adjusted_score = overall_score
        if buyer_profile:
            buyer_adjusted_score = self._adjust_for_buyer_profile(
                overall_score,
                category_scores,
                buyer_profile
            )
        
        # Determine risk tier
        risk_tier = self._determine_risk_tier(buyer_adjusted_score)
        
        return PropertyRiskScore(
            property_address="",  # To be filled by caller
            overall_risk_score=overall_score,
            category_scores=category_scores,
            total_repair_cost_low=total_cost_low,
            total_repair_cost_high=total_cost_high,
            deal_breakers=deal_breakers,
            negotiation_items=negotiation_items,
            walk_away_threshold=walk_away,
            buyer_adjusted_score=buyer_adjusted_score,
            risk_tier=risk_tier
        )

    def _group_by_category(
        self, 
        findings: List[InspectionFinding]
    ) -> Dict[RiskCategory, List[InspectionFinding]]:
        """Group findings by risk category"""
        grouped = {cat: [] for cat in RiskCategory}
        
        # Map IssueCategory to RiskCategory
        category_mapping = {
            IssueCategory.FOUNDATION_STRUCTURE: RiskCategory.FOUNDATION_STRUCTURE,
            IssueCategory.ROOF_EXTERIOR: RiskCategory.ROOF_EXTERIOR,
            IssueCategory.PLUMBING: RiskCategory.PLUMBING,
            IssueCategory.ELECTRICAL: RiskCategory.ELECTRICAL,
            IssueCategory.HVAC: RiskCategory.HVAC_SYSTEMS,
            IssueCategory.ENVIRONMENTAL: RiskCategory.ENVIRONMENTAL,
            IssueCategory.LEGAL_TITLE: RiskCategory.LEGAL_TITLE,
            IssueCategory.HOA: RiskCategory.INSURANCE_HOA,
        }
        
        for finding in findings:
            risk_cat = category_mapping.get(finding.category, RiskCategory.FOUNDATION_STRUCTURE)
            grouped[risk_cat].append(finding)
        
        return grouped

    def _score_category(
        self,
        category: RiskCategory,
        findings: List[InspectionFinding],
        property_price: float
    ) -> CategoryRiskScore:
        """Calculate risk score for a single category"""
        
        if not findings:
            return CategoryRiskScore(
                category=category,
                score=0.0,
                severity_breakdown={},
                estimated_cost_low=0.0,
                estimated_cost_high=0.0,
                key_issues=[],
                requires_specialist=False,
                safety_concern=False,
                affects_insurability=False,
                affects_resale=False
            )
        
        # Base score from severity
        base_score = 0.0
        severity_breakdown = {}
        total_cost_low = 0.0
        total_cost_high = 0.0
        key_issues = []
        requires_specialist = False
        safety_concern = False
        has_document_costs = False  # v5.55.8: Track if any cost came from document
        
        for finding in findings:
            # Add severity points
            base_score += self.severity_multipliers[finding.severity]
            
            # Track severity breakdown
            sev_name = finding.severity.value
            severity_breakdown[sev_name] = severity_breakdown.get(sev_name, 0) + 1
            
            # Accumulate costs - DEFENSIVE: Handle None values
            if finding.estimated_cost_low:
                cost_low_val = finding.estimated_cost_low if finding.estimated_cost_low is not None else 0.0
                cost_high_val = finding.estimated_cost_high if finding.estimated_cost_high is not None else cost_low_val
                total_cost_low += cost_low_val
                total_cost_high += cost_high_val
                # Track if this cost came from document (v5.55.8)
                if hasattr(finding, 'cost_from_document') and finding.cost_from_document:
                    has_document_costs = True
            
            # Track critical attributes
            if finding.severity in [Severity.CRITICAL, Severity.MAJOR]:
                # Truncate at sentence boundary to avoid broken sentences (v5.55.10)
                desc = finding.description
                if len(desc) > 120:
                    # Find last sentence break before 120 chars
                    for end_char in ['. ', '! ', '? ']:
                        last_break = desc[:120].rfind(end_char)
                        if last_break > 40:  # At least 40 chars for meaningful content
                            desc = desc[:last_break + 1].strip()
                            break
                    else:
                        # No sentence break found, truncate at last space
                        last_space = desc[:120].rfind(' ')
                        if last_space > 40:
                            desc = desc[:last_space].strip() + '...'
                        else:
                            desc = desc[:120].strip() + '...'
                key_issues.append(desc)
            
            if finding.requires_specialist:
                requires_specialist = True
            
            if finding.safety_concern:
                safety_concern = True
        
        # Apply cost impact multiplier
        cost_percent = total_cost_high / property_price if property_price > 0 else 0
        if cost_percent > self.cost_impact_thresholds['major']:
            base_score *= 1.8  # Increased from 1.5
        elif cost_percent > self.cost_impact_thresholds['significant']:
            base_score *= 1.5  # Increased from 1.3
        elif cost_percent > self.cost_impact_thresholds['moderate']:
            base_score *= 1.2  # Increased from 1.1
        
        # Apply category weight
        weighted_score = base_score * self.category_weights[category]
        
        # Normalize to 0-100 scale
        normalized_score = min(100, weighted_score)
        
        # v5.55.8: Track if we're using estimates vs document costs
        costs_were_estimated = False
        
        # CRITICAL: Add minimum cost estimates if category has issues but no costs
        # Nothing is ever $0 to fix!
        if normalized_score > 0 and total_cost_high == 0:
            costs_were_estimated = True  # We're adding estimates
            # Estimate based on severity and category
            if normalized_score >= 75:  # Critical
                if category == RiskCategory.FOUNDATION_STRUCTURE:
                    total_cost_low, total_cost_high = 25000, 60000
                elif category == RiskCategory.ROOF_EXTERIOR:
                    total_cost_low, total_cost_high = 8000, 20000
                else:
                    total_cost_low, total_cost_high = 5000, 15000
            elif normalized_score >= 50:  # Major
                if category == RiskCategory.FOUNDATION_STRUCTURE:
                    total_cost_low, total_cost_high = 10000, 25000
                elif category == RiskCategory.ROOF_EXTERIOR:
                    total_cost_low, total_cost_high = 5000, 12000
                else:
                    total_cost_low, total_cost_high = 3000, 8000
            else:  # Moderate
                if category == RiskCategory.FOUNDATION_STRUCTURE:
                    total_cost_low, total_cost_high = 5000, 12000
                elif category == RiskCategory.ROOF_EXTERIOR:
                    total_cost_low, total_cost_high = 2000, 6000
                elif category in [RiskCategory.ELECTRICAL, RiskCategory.PLUMBING]:
                    total_cost_low, total_cost_high = 1500, 5000
                else:
                    total_cost_low, total_cost_high = 1000, 4000
        
        # CRITICAL FIX: Validate costs are realistic for severity level
        # Even if LLM provided costs, ensure they meet minimums for severity
        if normalized_score > 0 and total_cost_high > 0:
            # Check if costs are unrealistically low for the severity level
            if normalized_score >= 75:  # Critical - enforce strict minimums
                if category == RiskCategory.FOUNDATION_STRUCTURE:
                    if total_cost_high < 25000:
                        costs_were_estimated = True  # We're adjusting
                        total_cost_low = max(total_cost_low, 25000)
                        total_cost_high = max(total_cost_high, 55000)
                elif category == RiskCategory.ROOF_EXTERIOR:
                    if total_cost_high < 15000:
                        costs_were_estimated = True
                        total_cost_low = max(total_cost_low, 15000)
                        total_cost_high = max(total_cost_high, 30000)
                elif category == RiskCategory.ELECTRICAL:
                    if total_cost_high < 8000:
                        costs_were_estimated = True
                        total_cost_low = max(total_cost_low, 8000)
                        total_cost_high = max(total_cost_high, 15000)
                elif category == RiskCategory.PLUMBING:
                    if total_cost_high < 10000:
                        costs_were_estimated = True
                        total_cost_low = max(total_cost_low, 10000)
                        total_cost_high = max(total_cost_high, 20000)
                elif category == RiskCategory.HVAC_SYSTEMS:
                    if total_cost_high < 6000:
                        costs_were_estimated = True
                        total_cost_low = max(total_cost_low, 6000)
                        total_cost_high = max(total_cost_high, 12000)
                else:
                    # Generic critical issue
                    if total_cost_high < 5000:
                        costs_were_estimated = True
                        total_cost_low = max(total_cost_low, 5000)
                        total_cost_high = max(total_cost_high, 15000)
            
            elif normalized_score >= 50:  # Major - enforce reasonable minimums
                if category == RiskCategory.FOUNDATION_STRUCTURE:
                    if total_cost_high < 10000:
                        costs_were_estimated = True
                        total_cost_low = max(total_cost_low, 10000)
                        total_cost_high = max(total_cost_high, 25000)
                elif category == RiskCategory.ROOF_EXTERIOR:
                    if total_cost_high < 5000:
                        costs_were_estimated = True
                        total_cost_low = max(total_cost_low, 5000)
                        total_cost_high = max(total_cost_high, 12000)
                elif category in [RiskCategory.ELECTRICAL, RiskCategory.PLUMBING]:
                    if total_cost_high < 3000:
                        costs_were_estimated = True
                        total_cost_low = max(total_cost_low, 3000)
                        total_cost_high = max(total_cost_high, 8000)
                else:
                    if total_cost_high < 3000:
                        costs_were_estimated = True
                        total_cost_low = max(total_cost_low, 3000)
                        total_cost_high = max(total_cost_high, 8000)
        
        # Determine impacts
        affects_insurability = self._affects_insurability(category, findings)
        affects_resale = self._affects_resale(category, findings)
        
        return CategoryRiskScore(
            category=category,
            score=normalized_score,
            severity_breakdown=severity_breakdown,
            estimated_cost_low=total_cost_low,
            estimated_cost_high=total_cost_high,
            key_issues=key_issues,
            requires_specialist=requires_specialist,
            safety_concern=safety_concern,
            affects_insurability=affects_insurability,
            affects_resale=affects_resale,
            costs_are_estimates=costs_were_estimated or not has_document_costs  # v5.55.8
        )

    def _calculate_weighted_score(self, category_scores: List[CategoryRiskScore]) -> float:
        """Calculate overall weighted risk score"""
        total_weighted = 0.0
        total_weight = 0.0
        
        # Only include categories with actual findings
        for cat_score in category_scores:
            if cat_score.score > 0:  # Only count categories with issues
                weight = self.category_weights[cat_score.category]
                total_weighted += cat_score.score * weight
                total_weight += weight
        
        # If no findings at all, return 0
        if total_weight == 0:
            return 0.0
        
        # Calculate average but apply minimum floor for critical issues
        avg_score = total_weighted / total_weight
        
        # If any category has critical findings, ensure overall score reflects that
        has_critical = any(
            cs.severity_breakdown.get('critical', 0) > 0 
            for cs in category_scores
        )
        if has_critical and avg_score < 40:
            avg_score = max(avg_score, 40)  # Floor at 40 for critical issues (was 60 - too aggressive)
        
        return avg_score

    def _apply_transparency_adjustment(
        self,
        base_score: float,
        cross_ref_report: CrossReferenceReport
    ) -> float:
        """Adjust risk score based on seller transparency"""
        
        # Low transparency = higher risk (trust issues)
        if cross_ref_report.transparency_score < 50:
            # Increase risk by up to 20%
            adjustment = (50 - cross_ref_report.transparency_score) / 50 * 0.2
            base_score *= (1 + adjustment)
        
        # High number of contradictions = higher risk
        if len(cross_ref_report.contradictions) > 3:
            base_score *= 1.15
        
        return min(100, base_score)

    def _identify_deal_breakers(
        self,
        category_scores: List[CategoryRiskScore],
        buyer_profile: Optional[BuyerProfile]
    ) -> List[str]:
        """Identify critical issues that should stop the deal"""
        deal_breakers = []
        
        def is_valid_issue_text(text: str, category_name: str) -> bool:
            """Validate that issue text is specific and relevant to the category"""
            text_lower = text.lower()
            
            # Reject if too short
            if len(text) < 30:
                return False
            
            # Reject generic summary phrases
            generic_phrases = [
                'well-maintained',
                'single-family residence',
                'overall condition',
                'executive summary',
                'property summary',
                'age-related maintenance',
                'typical for',
                'normal wear',
                'this property',
                'the property',
                'inspection report',
                'disclosure statement'
            ]
            if any(phrase in text_lower for phrase in generic_phrases):
                return False
            
            # Reject if it's clearly executive summary or overview text
            if text_lower.startswith(('this is', 'the property', 'overall', 'summary', 'executive')):
                return False
            
            # Reject incomplete sentences
            if text.endswith(('requiring.', 'with.', 'and.', 'or.')):
                return False
            
            # Must mention something specific to be valid
            specific_indicators = [
                'crack', 'leak', 'damage', 'deteriorat', 'corrosi', 'rot', 'fail',
                'defect', 'issue', 'concern', 'hazard', 'risk', 'problem',
                'replace', 'repair', 'service', 'inspect', 'evaluat'
            ]
            if not any(indicator in text_lower for indicator in specific_indicators):
                return False
            
            # Category-specific validation
            category_lower = category_name.lower()
            
            if 'foundation' in category_lower or 'structure' in category_lower:
                required_terms = ['foundation', 'crack', 'settlement', 'structural', 'slab', 'basement', 'wall']
                if not any(term in text_lower for term in required_terms):
                    return False
                    
            elif 'roof' in category_lower or 'exterior' in category_lower:
                required_terms = ['roof', 'shingle', 'leak', 'exterior', 'siding', 'gutter', 'flashing']
                if not any(term in text_lower for term in required_terms):
                    return False
                    
            elif 'electrical' in category_lower:
                required_terms = ['electrical', 'panel', 'wiring', 'outlet', 'circuit', 'breaker', 'power']
                if not any(term in text_lower for term in required_terms):
                    return False
                    
            elif 'plumbing' in category_lower:
                required_terms = ['plumb', 'pipe', 'water', 'leak', 'drain', 'sewer', 'heater', 'fixture']
                if not any(term in text_lower for term in required_terms):
                    return False
                    
            elif 'hvac' in category_lower:
                required_terms = ['hvac', 'furnace', 'heating', 'cooling', 'ac', 'air', 'duct', 'heat exchanger']
                if not any(term in text_lower for term in required_terms):
                    return False
            
            return True
        
        # CRITICAL: Ensure ALL categories with CRITICAL severity (75+) are represented
        for cat_score in category_scores:
            if cat_score.score >= 75:  # CRITICAL threshold
                category_name = cat_score.category.value.replace('_', ' ').title()
                
                # Try to find a valid issue from key_issues
                valid_issue_text = None
                if cat_score.key_issues:
                    for issue in cat_score.key_issues:
                        issue_cleaned = issue.strip()
                        
                        # CRITICAL: Strip severity keywords that LLM might have included
                        # Remove "CRITICAL", "MAJOR", "MODERATE", "MINOR" from start
                        severity_pattern = r'^(CRITICAL|MAJOR|MODERATE|MINOR|Critical|Major|Moderate|Minor)[\s:-]*'
                        issue_cleaned = re.sub(severity_pattern, '', issue_cleaned).strip()
                        
                        if is_valid_issue_text(issue_cleaned, category_name):
                            valid_issue_text = issue_cleaned
                            break
                
                if valid_issue_text:
                    # Use the valid specific issue
                    # Make sure first letter is lowercase for natural flow
                    if valid_issue_text[0].isupper() and not valid_issue_text.startswith(category_name):
                        valid_issue_text = valid_issue_text[0].lower() + valid_issue_text[1:]
                    
                    if cat_score.estimated_cost_high > 0:
                        deal_breakers.append(
                            f"{category_name} exhibits {valid_issue_text}. "
                            f"Estimated repair cost ranges from ${cat_score.estimated_cost_low:,.0f} to ${cat_score.estimated_cost_high:,.0f}. "
                            f"This represents a significant concern requiring immediate professional evaluation and remediation."
                        )
                    else:
                        deal_breakers.append(
                            f"{category_name} exhibits {valid_issue_text}. "
                            f"This represents a significant concern requiring immediate professional evaluation and remediation."
                        )
                else:
                    # No valid specific issues found - use generic professional description
                    # CRITICAL: Ensure realistic minimum costs for critical categories
                    cost_low = cat_score.estimated_cost_low
                    cost_high = cat_score.estimated_cost_high
                    
                    # If costs are unrealistically low for a CRITICAL category, use realistic minimums
                    if cat_score.score >= 75:  # CRITICAL severity
                        category_lower = category_name.lower()
                        
                        # Set realistic minimum costs based on category
                        if 'foundation' in category_lower or 'structure' in category_lower:
                            if cost_high < 25000:  # Foundation issues can't be $500!
                                cost_low = 25000
                                cost_high = 55000
                        elif 'roof' in category_lower or 'exterior' in category_lower:
                            if cost_high < 15000:
                                cost_low = 15000
                                cost_high = 30000
                        elif 'electrical' in category_lower:
                            if cost_high < 8000:
                                cost_low = 8000
                                cost_high = 15000
                        elif 'plumbing' in category_lower:
                            if cost_high < 10000:
                                cost_low = 10000
                                cost_high = 20000
                        elif 'hvac' in category_lower:
                            if cost_high < 6000:
                                cost_low = 6000
                                cost_high = 12000
                        else:
                            # Generic critical issue
                            if cost_high < 5000:
                                cost_low = 5000
                                cost_high = 15000
                    
                    if cost_high > 0:
                        deal_breakers.append(
                            f"{category_name} exhibits significant deficiencies requiring attention. "
                            f"Estimated repair costs range from ${cost_low:,.0f} to ${cost_high:,.0f}. "
                            f"A comprehensive evaluation by a qualified specialist is strongly recommended before proceeding."
                        )
                    else:
                        deal_breakers.append(
                            f"{category_name} exhibits significant deficiencies based on inspection findings. "
                            f"A comprehensive evaluation by a qualified specialist is strongly recommended before proceeding."
                        )
            
            # Also include critical severity individual findings
            elif cat_score.severity_breakdown.get('critical', 0) > 0:
                category_name = cat_score.category.value.replace('_', ' ').title()
                
                # Find valid issues from the list
                for issue in cat_score.key_issues[:3]:  # Check top 3
                    issue_cleaned = issue.strip()
                    
                    # CRITICAL: Strip severity keywords that LLM might have included
                    severity_pattern = r'^(CRITICAL|MAJOR|MODERATE|MINOR|Critical|Major|Moderate|Minor)[\s:-]*'
                    issue_cleaned = re.sub(severity_pattern, '', issue_cleaned).strip()
                    
                    if is_valid_issue_text(issue_cleaned, category_name):
                        # Make sure first letter is lowercase for natural flow
                        if issue_cleaned[0].isupper():
                            issue_cleaned = issue_cleaned[0].lower() + issue_cleaned[1:]
                        
                        deal_breakers.append(
                            f"{category_name} exhibits {issue_cleaned}. "
                            f"This defect requires immediate attention and professional assessment."
                        )
                        break  # Only add one valid issue per category
        
        # Limit to top 6 most critical
        return deal_breakers[:6]

    def _identify_negotiation_items(
        self,
        category_scores: List[CategoryRiskScore],
        property_price: float
    ) -> List[str]:
        """Identify issues that justify price reduction"""
        negotiation_items = []
        
        for cat_score in category_scores:
            # Cost-based negotiation leverage
            cost_percent = cat_score.estimated_cost_high / property_price if property_price > 0 else 0
            
            if cost_percent > 0.03:  # > 3% of price
                negotiation_items.append(
                    f"{cat_score.category.value}: ${cat_score.estimated_cost_low:,.0f}-"
                    f"${cat_score.estimated_cost_high:,.0f} ({cost_percent*100:.1f}% of price)"
                )
            
            # Major severity items
            if cat_score.severity_breakdown.get('major', 0) > 0:
                for issue in cat_score.key_issues:
                    negotiation_items.append(f"Major issue: {issue}")
        
        return negotiation_items

    def _calculate_walk_away_threshold(
        self,
        property_price: float,
        total_repair_cost: float,
        risk_score: float,
        buyer_profile: Optional[BuyerProfile]
    ) -> float:
        """Calculate the price above which the deal doesn't make sense"""
        
        # Base threshold: asking price minus repairs
        threshold = property_price - total_repair_cost
        
        # Adjust for risk score
        # Higher risk = lower threshold (need bigger discount)
        if risk_score > 75:
            threshold -= property_price * 0.10  # Additional 10% discount
        elif risk_score > 50:
            threshold -= property_price * 0.05  # Additional 5% discount
        
        # Apply buyer profile
        if buyer_profile:
            # If buyer has low repair tolerance, lower threshold
            if buyer_profile.repair_tolerance == "low":
                threshold -= property_price * 0.03
            
            # If buyer's max budget is constraint
            if buyer_profile.max_budget:
                threshold = min(threshold, buyer_profile.max_budget)
        
        return max(0, threshold)

    def _adjust_for_buyer_profile(
        self,
        base_score: float,
        category_scores: List[CategoryRiskScore],
        buyer_profile: BuyerProfile
    ) -> float:
        """Adjust risk score based on buyer's risk tolerance and priorities"""
        
        adjusted_score = base_score
        
        # Repair tolerance adjustment
        if buyer_profile.repair_tolerance == "low":
            # Low tolerance = amplify all risks
            adjusted_score *= 1.3
        elif buyer_profile.repair_tolerance == "high":
            # High tolerance = dampen moderate risks
            if base_score < 60:
                adjusted_score *= 0.8
        
        # Biggest regret adjustment
        if buyer_profile.biggest_regret == "hidden_issues":
            # Amplify any undisclosed items
            adjusted_score *= 1.2
        elif buyer_profile.biggest_regret == "lose_house":
            # Suppress minor issues
            if base_score < 50:
                adjusted_score *= 0.7
        
        # Ownership duration adjustment
        if buyer_profile.ownership_duration in ["<3", "3-7"]:
            # Short-term = amplify resale concerns
            for cat_score in category_scores:
                if cat_score.affects_resale and cat_score.score > 40:
                    adjusted_score += 5
        
        return min(100, adjusted_score)

    def _determine_risk_tier(self, score: float) -> str:
        """Convert numeric score to risk tier"""
        if score >= 70:
            return "CRITICAL"
        elif score >= 50:
            return "HIGH"
        elif score >= 30:
            return "MODERATE"
        else:
            return "LOW"

    def _affects_insurability(
        self,
        category: RiskCategory,
        findings: List[InspectionFinding]
    ) -> bool:
        """Determine if issues affect ability to get insurance"""
        if category in [RiskCategory.FOUNDATION_STRUCTURE, RiskCategory.ROOF_EXTERIOR]:
            # Critical foundation or roof issues often block insurance
            return any(f.severity == Severity.CRITICAL for f in findings)
        
        if category == RiskCategory.ENVIRONMENTAL:
            # Environmental issues can block insurance
            return len(findings) > 0
        
        return False

    def _affects_resale(
        self,
        category: RiskCategory,
        findings: List[InspectionFinding]
    ) -> bool:
        """Determine if issues significantly affect resale value"""
        high_impact_categories = [
            RiskCategory.FOUNDATION_STRUCTURE,
            RiskCategory.ROOF_EXTERIOR,
            RiskCategory.ENVIRONMENTAL
        ]
        
        if category in high_impact_categories:
            return any(f.severity in [Severity.CRITICAL, Severity.MAJOR] for f in findings)
        
        return False


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

if __name__ == "__main__":
    from document_parser import DocumentParser, InspectionFinding, Severity, IssueCategory
    
    # Create sample inspection findings
    findings = [
        InspectionFinding(
            category=IssueCategory.FOUNDATION_STRUCTURE,
            severity=Severity.CRITICAL,
            location="South wall",
            description="Multiple vertical cracks in foundation, 1/4 inch wide",
            recommendation="Immediate structural engineer evaluation required",
            estimated_cost_low=25000,
            estimated_cost_high=50000,
            safety_concern=True,
            requires_specialist=True
        ),
        InspectionFinding(
            category=IssueCategory.PLUMBING,
            severity=Severity.MAJOR,
            location="Main sewer line",
            description="Severe root intrusion in sewer line",
            recommendation="Full section replacement needed",
            estimated_cost_low=8000,
            estimated_cost_high=15000,
            requires_specialist=True
        ),
        InspectionFinding(
            category=IssueCategory.ROOF_EXTERIOR,
            severity=Severity.MODERATE,
            location="South slope",
            description="Roof showing typical wear, 3-5 years remaining life",
            recommendation="Budget for replacement",
            estimated_cost_low=12000,
            estimated_cost_high=18000
        ),
    ]
    
    # Create buyer profile
    buyer = BuyerProfile(
        max_budget=1500000,
        repair_tolerance="moderate",
        ownership_duration="7-10",
        biggest_regret="hidden_issues",
        replaceability="somewhat_unique",
        deal_breakers=["foundation", "insurance"]
    )
    
    # Calculate risk score
    model = RiskScoringModel()
    risk_score = model.calculate_risk_score(
        findings=findings,
        cross_ref_report=None,
        property_price=1480000,
        buyer_profile=buyer
    )
    
    # Print results
    print("=" * 80)
    print("OFFERWISE RISK SCORE ANALYSIS")
    print("=" * 80)
    print(f"\nOverall Risk Score: {risk_score.overall_risk_score:.1f}/100")
    print(f"Buyer-Adjusted Score: {risk_score.buyer_adjusted_score:.1f}/100")
    print(f"Risk Tier: {risk_score.risk_tier}")
    print(f"\nEstimated Total Repairs: ${risk_score.total_repair_cost_low:,.0f} - ${risk_score.total_repair_cost_high:,.0f}")
    print(f"Walk-Away Threshold: ${risk_score.walk_away_threshold:,.0f}")
    
    print("\n" + "=" * 80)
    print("CATEGORY BREAKDOWN")
    print("=" * 80)
    for cat_score in risk_score.category_scores:
        if cat_score.score > 0:
            print(f"\n{cat_score.category.value.upper()}: {cat_score.score:.1f}/100")
            print(f"  Severity breakdown: {cat_score.severity_breakdown}")
            print(f"  Est. cost: ${cat_score.estimated_cost_low:,.0f} - ${cat_score.estimated_cost_high:,.0f}")
            print(f"  Safety concern: {cat_score.safety_concern}")
            print(f"  Affects insurability: {cat_score.affects_insurability}")
            if cat_score.key_issues:
                print(f"  Key issues:")
                for issue in cat_score.key_issues:
                    print(f"    - {issue}")
    
    if risk_score.deal_breakers:
        print("\n" + "=" * 80)
        print("DEAL-BREAKERS")
        print("=" * 80)
        for db in risk_score.deal_breakers:
            print(f"  ⚠ {db}")
    
    if risk_score.negotiation_items:
        print("\n" + "=" * 80)
        print("NEGOTIATION LEVERAGE")
        print("=" * 80)
        for item in risk_score.negotiation_items:
            print(f"  • {item}")
