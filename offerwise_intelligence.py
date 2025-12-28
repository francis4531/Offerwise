"""
OfferWise Intelligence System - Master Integration
Complete pipeline from PDF documents to actionable insights
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
import json

from document_parser import DocumentParser, PropertyDocument
from cross_reference_engine import CrossReferenceEngine, CrossReferenceReport
from risk_scoring_model import RiskScoringModel, PropertyRiskScore, BuyerProfile


@dataclass
class PropertyAnalysis:
    """Complete property analysis output"""
    property_address: str
    analysis_date: str
    
    # Document intelligence
    seller_disclosure: PropertyDocument
    inspection_report: PropertyDocument
    
    # Cross-reference analysis
    cross_reference: CrossReferenceReport
    
    # Risk scoring
    risk_score: PropertyRiskScore
    
    # Buyer-specific outputs
    offer_strategy: Dict[str, Any]
    inspection_priorities: Dict[str, Any]
    negotiation_strategy: Dict[str, Any]
    decision_framework: Dict[str, Any]


class OfferWiseIntelligence:
    """
    Master intelligence system that orchestrates all analysis components.
    
    This is what replaces "just asking ChatGPT" with structured, quantified,
    reproducible analysis.
    """
    
    def __init__(self):
        self.parser = DocumentParser()
        self.cross_ref_engine = CrossReferenceEngine()
        self.risk_model = RiskScoringModel()
    
    def analyze_property(
        self,
        seller_disclosure_text: str,
        inspection_report_text: str,
        property_price: float,
        buyer_profile: BuyerProfile,
        property_address: Optional[str] = None
    ) -> PropertyAnalysis:
        """
        Complete end-to-end property analysis.
        
        Args:
            seller_disclosure_text: Raw text from seller disclosure PDF
            inspection_report_text: Raw text from inspection report PDF
            property_price: Asking or offer price
            buyer_profile: Buyer's preferences and constraints
            property_address: Property address (optional)
            
        Returns:
            PropertyAnalysis with all insights
        """
        
        # Step 1: Parse documents into structured data
        print("Parsing seller disclosure...")
        disclosure_doc = self.parser.parse_seller_disclosure(
            seller_disclosure_text, 
            property_address
        )
        
        print("Parsing inspection report...")
        inspection_doc = self.parser.parse_inspection_report(
            inspection_report_text,
            property_address or disclosure_doc.property_address
        )
        
        # Step 2: Cross-reference disclosures vs inspection
        print("Cross-referencing documents...")
        cross_ref = self.cross_ref_engine.cross_reference(
            disclosure_doc,
            inspection_doc
        )
        
        # Step 3: Calculate risk scores
        print("Calculating risk scores...")
        risk_score = self.risk_model.calculate_risk_score(
            findings=inspection_doc.inspection_findings,
            cross_ref_report=cross_ref,
            property_price=property_price,
            buyer_profile=buyer_profile
        )
        risk_score.property_address = disclosure_doc.property_address or inspection_doc.property_address
        
        # Step 4: Generate buyer-specific strategies
        print("Generating strategies...")
        offer_strategy = self._generate_offer_strategy(
            property_price, risk_score, cross_ref, buyer_profile
        )
        
        inspection_priorities = self._generate_inspection_priorities(
            inspection_doc, risk_score, buyer_profile
        )
        
        negotiation_strategy = self._generate_negotiation_strategy(
            property_price, risk_score, cross_ref, buyer_profile
        )
        
        decision_framework = self._generate_decision_framework(
            property_price, risk_score, cross_ref, buyer_profile
        )
        
        return PropertyAnalysis(
            property_address=risk_score.property_address,
            analysis_date="2024-12-22",
            seller_disclosure=disclosure_doc,
            inspection_report=inspection_doc,
            cross_reference=cross_ref,
            risk_score=risk_score,
            offer_strategy=offer_strategy,
            inspection_priorities=inspection_priorities,
            negotiation_strategy=negotiation_strategy,
            decision_framework=decision_framework
        )
    
    def _generate_offer_strategy(
        self,
        property_price: float,
        risk_score: PropertyRiskScore,
        cross_ref: CrossReferenceReport,
        buyer_profile: BuyerProfile
    ) -> Dict[str, Any]:
        """Generate specific offer recommendations"""
        
        # Calculate recommended offer price
        base_discount = 0.0
        
        # Discount based on repair costs
        repair_cost_avg = (risk_score.total_repair_cost_low + risk_score.total_repair_cost_high) / 2
        cost_discount = repair_cost_avg * 0.8  # Take 80% of repair costs
        
        # Additional discount for risk
        if risk_score.risk_tier == "CRITICAL":
            risk_discount = property_price * 0.10
        elif risk_score.risk_tier == "HIGH":
            risk_discount = property_price * 0.05
        elif risk_score.risk_tier == "MODERATE":
            risk_discount = property_price * 0.02
        else:
            risk_discount = 0.0
        
        # Additional discount for transparency issues
        transparency_discount = 0.0
        if cross_ref.transparency_score < 50:
            transparency_discount = property_price * 0.03
        
        total_discount = cost_discount + risk_discount + transparency_discount
        recommended_offer = property_price - total_discount
        
        # Adjust for buyer profile
        if buyer_profile.biggest_regret == "lose_house" and buyer_profile.replaceability == "very_rare":
            # Buyer fears losing house - be more aggressive
            recommended_offer = min(property_price, recommended_offer + (total_discount * 0.3))
        elif buyer_profile.biggest_regret == "overpay":
            # Buyer fears overpaying - be more conservative
            recommended_offer = recommended_offer - (total_discount * 0.1)
        
        # Cap at buyer's max budget
        if buyer_profile.max_budget:
            recommended_offer = min(recommended_offer, buyer_profile.max_budget)
        
        # Contingency strategy
        has_resale_impact = any(cat.affects_resale for cat in risk_score.category_scores)
        contingencies = {
            "inspection": "REQUIRED" if risk_score.risk_tier in ["HIGH", "CRITICAL"] else "RECOMMENDED",
            "financing": "STANDARD",
            "appraisal": "RECOMMENDED" if has_resale_impact else "OPTIONAL"
        }
        
        return {
            "recommended_offer": recommended_offer,
            "discount_from_ask": property_price - recommended_offer,
            "discount_percentage": ((property_price - recommended_offer) / property_price * 100),
            "discount_breakdown": {
                "repair_costs": cost_discount,
                "risk_premium": risk_discount,
                "transparency_issues": transparency_discount
            },
            "contingencies": contingencies,
            "escalation_cap": min(property_price, buyer_profile.max_budget or property_price),
            "walk_away_price": risk_score.walk_away_threshold,
            "confidence_level": self._calculate_confidence(risk_score, cross_ref)
        }
    
    def _generate_inspection_priorities(
        self,
        inspection_doc: PropertyDocument,
        risk_score: PropertyRiskScore,
        buyer_profile: BuyerProfile
    ) -> Dict[str, Any]:
        """Generate prioritized inspection checklist"""
        
        must_inspect = []
        should_inspect = []
        optional_inspect = []
        
        for cat_score in risk_score.category_scores:
            if cat_score.score == 0:
                continue
            
            category_name = cat_score.category.value
            
            # Categorize by priority
            if cat_score.score > 60 or cat_score.safety_concern:
                must_inspect.append({
                    "category": category_name,
                    "reason": f"High risk score ({cat_score.score:.0f}/100)" if cat_score.score > 60 
                             else "Safety concern",
                    "specialist": cat_score.requires_specialist,
                    "est_cost": f"${cat_score.estimated_cost_low:,.0f}-${cat_score.estimated_cost_high:,.0f}"
                })
            elif cat_score.score > 30:
                should_inspect.append({
                    "category": category_name,
                    "reason": f"Moderate risk ({cat_score.score:.0f}/100)",
                    "specialist": cat_score.requires_specialist,
                    "est_cost": f"${cat_score.estimated_cost_low:,.0f}-${cat_score.estimated_cost_high:,.0f}"
                })
            else:
                optional_inspect.append({
                    "category": category_name,
                    "reason": "Low risk, standard inspection coverage"
                })
        
        # Add buyer deal-breakers to must-inspect
        for deal_breaker in buyer_profile.deal_breakers:
            if not any(item['category'] == deal_breaker for item in must_inspect):
                must_inspect.append({
                    "category": deal_breaker,
                    "reason": "Buyer-specified deal-breaker",
                    "specialist": True,
                    "est_cost": "TBD"
                })
        
        return {
            "must_inspect": must_inspect,
            "should_inspect": should_inspect,
            "optional": optional_inspect,
            "estimated_inspection_cost": len(must_inspect) * 500 + len(should_inspect) * 300
        }
    
    def _generate_negotiation_strategy(
        self,
        property_price: float,
        risk_score: PropertyRiskScore,
        cross_ref: CrossReferenceReport,
        buyer_profile: BuyerProfile
    ) -> Dict[str, Any]:
        """Generate tactical negotiation approach"""
        
        # Determine negotiation posture
        if risk_score.risk_tier == "CRITICAL":
            posture = "HARD - Walk away or demand major concessions"
        elif len(cross_ref.contradictions) > 3:
            posture = "FIRM - Seller transparency issues give strong leverage"
        elif risk_score.risk_tier == "HIGH":
            posture = "FIRM - Significant issues warrant price reduction"
        elif risk_score.risk_tier == "MODERATE":
            posture = "BALANCED - Some leverage, negotiate repair credits"
        else:
            posture = "FLEXIBLE - Minor issues, standard negotiation"
        
        # Key talking points
        talking_points = []
        
        if cross_ref.contradictions:
            talking_points.append(
                f"Seller failed to disclose {len(cross_ref.contradictions)} issue(s) found during inspection"
            )
        
        if risk_score.total_repair_cost_high > property_price * 0.05:
            talking_points.append(
                f"Inspection revealed ${risk_score.total_repair_cost_high:,.0f} in necessary repairs "
                f"({risk_score.total_repair_cost_high/property_price*100:.1f}% of purchase price)"
            )
        
        for deal_breaker in risk_score.deal_breakers:
            talking_points.append(f"Critical issue: {deal_breaker}")
        
        # Negotiation options
        options = {
            "option_1_price_reduction": {
                "ask": risk_score.total_repair_cost_high,
                "fallback": risk_score.total_repair_cost_low,
                "rationale": "Full cost of repairs"
            },
            "option_2_repair_credit": {
                "ask": risk_score.total_repair_cost_high * 0.8,
                "fallback": risk_score.total_repair_cost_low * 0.8,
                "rationale": "Partial repair credit at closing"
            },
            "option_3_seller_repairs": {
                "must_fix": [item for item in risk_score.deal_breakers],
                "optional_fix": risk_score.negotiation_items[:3],
                "rationale": "Seller addresses critical items before close"
            }
        }
        
        return {
            "posture": posture,
            "talking_points": talking_points,
            "negotiation_options": options,
            "leverage_score": self._calculate_leverage(risk_score, cross_ref),
            "walk_away_threshold": risk_score.walk_away_threshold,
            "timeline": {
                "inspection_period": "10 days" if risk_score.risk_tier in ["HIGH", "CRITICAL"] else "7 days",
                "response_deadline": "3 business days after inspection report"
            }
        }
    
    def _generate_decision_framework(
        self,
        property_price: float,
        risk_score: PropertyRiskScore,
        cross_ref: CrossReferenceReport,
        buyer_profile: BuyerProfile
    ) -> Dict[str, Any]:
        """Generate decision tree for buyer"""
        
        # Immediate decisions
        if len(risk_score.deal_breakers) > 2:
            recommendation = "WALK AWAY - Multiple critical deal-breakers present"
            confidence = 0.95
        elif risk_score.risk_tier == "CRITICAL":
            recommendation = "WALK AWAY - Risk exceeds acceptable threshold"
            confidence = 0.90
        elif cross_ref.transparency_score < 30:
            recommendation = "STRONG CAUTION - Seller transparency is very concerning"
            confidence = 0.85
        elif risk_score.buyer_adjusted_score > 70:
            recommendation = "NEGOTIATE HARD - Significant issues but deal may work with major price reduction"
            confidence = 0.75
        elif risk_score.buyer_adjusted_score > 45:
            recommendation = "PROCEED WITH CAUTION - Address issues through negotiation or repair credits"
            confidence = 0.70
        else:
            recommendation = "PROCEED - Typical issues for property type and age"
            confidence = 0.80
        
        # Scenario analysis
        scenarios = {
            "best_case": {
                "seller_response": "Accepts price reduction",
                "final_price": risk_score.walk_away_threshold + (property_price - risk_score.walk_away_threshold) * 0.3,
                "total_investment": "final_price + low_repair_costs",
                "outcome": "Good deal with manageable repairs"
            },
            "likely_case": {
                "seller_response": "Counters with partial credit",
                "final_price": property_price - (risk_score.total_repair_cost_high * 0.5),
                "total_investment": "final_price + half_repair_costs",
                "outcome": "Fair deal, split repair costs"
            },
            "worst_case": {
                "seller_response": "Refuses concessions",
                "final_price": property_price,
                "total_investment": "full_price + full_repair_costs",
                "outcome": "Walk away or overpay significantly"
            }
        }
        
        return {
            "recommendation": recommendation,
            "confidence": confidence,
            "scenarios": scenarios,
            "key_decision_points": {
                "inspection_findings": "Confirmed via professional inspection",
                "seller_response": "Gauge willingness to negotiate",
                "max_total_cost": buyer_profile.max_budget,
                "deal_breakers_present": len(risk_score.deal_breakers) > 0
            }
        }
    
    def _calculate_confidence(
        self,
        risk_score: PropertyRiskScore,
        cross_ref: CrossReferenceReport
    ) -> float:
        """Calculate confidence in analysis (0-1)"""
        confidence = 0.8  # Base confidence
        
        # Reduce confidence if limited data
        if cross_ref.total_disclosures < 5:
            confidence -= 0.1
        
        if cross_ref.total_findings < 5:
            confidence -= 0.1
        
        # Increase confidence with good transparency
        if cross_ref.transparency_score > 80:
            confidence += 0.1
        
        return max(0.5, min(1.0, confidence))
    
    def _calculate_leverage(
        self,
        risk_score: PropertyRiskScore,
        cross_ref: CrossReferenceReport
    ) -> float:
        """Calculate buyer's negotiation leverage (0-100)"""
        leverage = 50.0  # Base leverage
        
        # Increase leverage with issues
        leverage += risk_score.overall_risk_score * 0.3
        
        # Major boost for contradictions (seller dishonesty)
        leverage += len(cross_ref.contradictions) * 5
        
        # Boost for undisclosed issues
        leverage += len(cross_ref.undisclosed_issues) * 3
        
        return min(100, leverage)
    
    def generate_report(self, analysis: PropertyAnalysis) -> str:
        """Generate comprehensive text report"""
        lines = []
        lines.append("=" * 100)
        lines.append(" " * 35 + "OFFERWISE PROPERTY ANALYSIS")
        lines.append("=" * 100)
        lines.append(f"\nProperty: {analysis.property_address}")
        lines.append(f"Analysis Date: {analysis.analysis_date}")
        
        # Executive Summary
        lines.append("\n" + "=" * 100)
        lines.append("EXECUTIVE SUMMARY")
        lines.append("=" * 100)
        lines.append(f"\nRisk Tier: {analysis.risk_score.risk_tier}")
        lines.append(f"Overall Risk Score: {analysis.risk_score.overall_risk_score:.1f}/100")
        lines.append(f"Buyer-Adjusted Risk: {analysis.risk_score.buyer_adjusted_score:.1f}/100")
        lines.append(f"Seller Transparency: {analysis.cross_reference.transparency_score:.0f}/100")
        lines.append(f"\nRecommendation: {analysis.decision_framework['recommendation']}")
        lines.append(f"Confidence: {analysis.decision_framework['confidence']*100:.0f}%")
        
        # Offer Strategy
        lines.append("\n" + "=" * 100)
        lines.append("OFFER STRATEGY")
        lines.append("=" * 100)
        offer = analysis.offer_strategy
        lines.append(f"\nRecommended Offer: ${offer['recommended_offer']:,.0f}")
        lines.append(f"Discount from Ask: ${offer['discount_from_ask']:,.0f} ({offer['discount_percentage']:.1f}%)")
        lines.append(f"Walk-Away Threshold: ${offer['walk_away_price']:,.0f}")
        
        # Risk Scorecard
        lines.append("\n" + "=" * 100)
        lines.append("RISK SCORECARD")
        lines.append("=" * 100)
        lines.append(f"\n{'Category':<30} {'Score':<10} {'Cost Range':<25} {'Status'}")
        lines.append("-" * 100)
        for cat_score in analysis.risk_score.category_scores:
            if cat_score.score > 0:
                cost_range = f"${cat_score.estimated_cost_low:,.0f}-${cat_score.estimated_cost_high:,.0f}"
                status = "⚠ CRITICAL" if cat_score.safety_concern else "Moderate"
                lines.append(f"{cat_score.category.value:<30} {cat_score.score:>5.1f}/100  {cost_range:<25} {status}")
        
        lines.append(f"\n{'TOTAL ESTIMATED REPAIRS':<30} {'':10} "
                    f"${analysis.risk_score.total_repair_cost_low:,.0f}-"
                    f"${analysis.risk_score.total_repair_cost_high:,.0f}")
        
        # Cross-Reference Findings
        if analysis.cross_reference.contradictions or analysis.cross_reference.undisclosed_issues:
            lines.append("\n" + "=" * 100)
            lines.append("DISCLOSURE ISSUES")
            lines.append("=" * 100)
            
            if analysis.cross_reference.contradictions:
                lines.append(f"\n⚠ {len(analysis.cross_reference.contradictions)} CONTRADICTION(S) FOUND")
                for match in analysis.cross_reference.contradictions[:3]:
                    lines.append(f"  • {match.explanation[:150]}")
            
            if analysis.cross_reference.undisclosed_issues:
                lines.append(f"\n⚠ {len(analysis.cross_reference.undisclosed_issues)} UNDISCLOSED ISSUE(S)")
                for match in analysis.cross_reference.undisclosed_issues[:3]:
                    lines.append(f"  • {match.explanation[:150]}")
        
        # Negotiation Strategy
        lines.append("\n" + "=" * 100)
        lines.append("NEGOTIATION STRATEGY")
        lines.append("=" * 100)
        neg = analysis.negotiation_strategy
        lines.append(f"\nPosture: {neg['posture']}")
        lines.append(f"Leverage Score: {neg['leverage_score']:.0f}/100")
        lines.append("\nKey Talking Points:")
        for point in neg['talking_points'][:5]:
            lines.append(f"  • {point}")
        
        return '\n'.join(lines)


# ============================================================================
# USAGE EXAMPLE WITH COMPLETE WORKFLOW
# ============================================================================

if __name__ == "__main__":
    # Initialize intelligence system
    intelligence = OfferWiseIntelligence()
    
    # Sample documents (in production, these come from PDF extraction)
    seller_disclosure = """
    SELLER'S DISCLOSURE STATEMENT
    Property: 456 Oak Avenue, San Jose, CA 95120
    Date: November 15, 2024
    
    FOUNDATION & STRUCTURE
    1. Foundation cracks? [ ] Yes [X] No
    2. Structural issues? [ ] Yes [X] No
    
    ROOF & EXTERIOR
    3. Roof leaks? [ ] Yes [X] No
    4. Window problems? [X] Yes [ ] No
       Explanation: One window in bedroom has minor crack, cosmetic only
    
    PLUMBING
    5. Plumbing issues? [X] Yes [ ] No
       Explanation: Kitchen faucet drips occasionally, needs washer replacement
    6. Sewer line problems? [ ] Yes [X] No
    
    ENVIRONMENTAL
    7. Lead paint? [ ] Yes [X] No
    8. Asbestos? [ ] Yes [X] No
    """
    
    inspection_report = """
    RESIDENTIAL INSPECTION REPORT
    Property: 456 Oak Avenue, San Jose, CA 95120
    Inspection Date: December 10, 2024
    Inspector: John Smith, License #12345
    
    FOUNDATION - SIGNIFICANT CONCERNS
    Multiple diagonal cracks observed in foundation walls, particularly southeast corner.
    Cracks are 3/8 inch wide and show signs of active movement. Water intrusion evident.
    This is a major structural concern requiring immediate structural engineer evaluation.
    Estimated repair cost: $35,000 - $75,000
    RECOMMENDATION: Structural engineer evaluation REQUIRED before closing
    
    ROOF - MODERATE CONCERNS
    Composition shingle roof showing significant wear. Age estimated 18-20 years.
    Multiple areas of granule loss. Several damaged/missing shingles on north slope.
    Life expectancy: 2-4 years maximum.
    Estimated replacement cost: $15,000 - $22,000
    RECOMMENDATION: Budget for replacement within 2 years
    
    PLUMBING - CRITICAL ISSUE
    Main sewer line shows severe root intrusion throughout via camera inspection.
    Multiple sections heavily compromised. High risk of complete failure.
    Partial blockage currently present.
    Estimated repair cost: $12,000 - $25,000 for full replacement
    RECOMMENDATION: Immediate replacement of affected sections
    
    ELECTRICAL - MINOR CONCERNS
    Electrical panel adequate but near capacity. Some outlets lack GFCI protection
    in bathrooms and kitchen. Otherwise functional.
    Estimated cost: $800 - $1,500 for GFCI upgrades
    
    INTERIOR - MINOR ISSUES
    Bedroom window shows crack in glass as disclosed by seller.
    Kitchen faucet drips as disclosed.
    """
    
    # Define buyer profile
    buyer = BuyerProfile(
        max_budget=950000,
        repair_tolerance="moderate",
        ownership_duration="7-10",
        biggest_regret="hidden_issues",
        replaceability="somewhat_unique",
        deal_breakers=["foundation", "insurance"]
    )
    
    # Run complete analysis
    print("\n" + "=" * 100)
    print("RUNNING OFFERWISE INTELLIGENCE SYSTEM")
    print("=" * 100 + "\n")
    
    analysis = intelligence.analyze_property(
        seller_disclosure_text=seller_disclosure,
        inspection_report_text=inspection_report,
        property_price=925000,
        buyer_profile=buyer,
        property_address="456 Oak Avenue, San Jose, CA 95120"
    )
    
    # Generate and print comprehensive report
    report = intelligence.generate_report(analysis)
    print(report)
    
    print("\n\n" + "=" * 100)
    print("DETAILED OFFER STRATEGY")
    print("=" * 100)
    print(json.dumps(analysis.offer_strategy, indent=2))
