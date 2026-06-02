"""
Negotiation Toolkit - Turn Analysis Into Action

Generates professional negotiation documents from property analysis:
- Offer justification letters
- Counteroffer calculators
- Email templates
- Talking points
"""

from typing import Dict, Any, List
from dataclasses import dataclass
import re


@dataclass
class NegotiationDocument:
    """Generated negotiation document"""
    title: str
    content: str
    document_type: str  # 'offer_letter', 'counteroffer', 'email', 'talking_points'
    

class NegotiationToolkit:
    """
    Generates professional negotiation documents from property analysis.
    
    This is the "secret sauce" that turns data into action.
    """
    
    def generate_offer_justification_letter(
        self,
        analysis: Dict[str, Any],
        property_address: str,
        asking_price: float,
        recommended_offer: float,
        buyer_name: str = "Buyer"
    ) -> NegotiationDocument:
        """
        Generate professional offer justification letter.
        
        This is what buyers REALLY need - a professional document they can
        send to the seller/agent that justifies their offer with data.
        
        Args:
            analysis: Full property analysis results
            property_address: Property address
            asking_price: Seller's asking price
            recommended_offer: OfferWise recommended offer
            buyer_name: Buyer's name (optional)
            
        Returns:
            Professional offer justification letter
        """
        
        # Extract key data
        risk_score = analysis.get('risk_score', {})
        findings = analysis.get('inspection_report', {}).get('inspection_findings', [])
        cross_ref = analysis.get('cross_reference', {})
        offer_strategy = analysis.get('offer_strategy', {})
        
        # Calculate discount
        discount = asking_price - recommended_offer
        discount_pct = (discount / asking_price * 100) if asking_price > 0 else 0
        
        # Get repair costs
        repair_low = risk_score.get('total_repair_cost_low', 0)
        repair_high = risk_score.get('total_repair_cost_high', 0)
        repair_avg = (repair_low + repair_high) / 2
        
        # Build letter sections
        sections = []
        
        # Header
        sections.append(f"OFFER JUSTIFICATION")
        sections.append(f"Property: {property_address}")
        sections.append(f"Date: {analysis.get('analysis_date', 'Today')}")
        sections.append("")
        sections.append("=" * 70)
        sections.append("")
        
        # Opening
        sections.append("Dear Seller,")
        sections.append("")
        sections.append(
            f"We are pleased to submit an offer of ${recommended_offer:,.0f} for the property located at "
            f"{property_address}. This offer is based on a comprehensive professional analysis of the "
            f"property's condition and reflects fair market value given documented repair needs."
        )
        sections.append("")
        sections.append("Our offer is supported by the following data-backed factors:")
        sections.append("")
        
        # Section 1: Major Findings
        sections.append("━" * 70)
        sections.append("DOCUMENTED REPAIR NEEDS")
        sections.append("━" * 70)
        sections.append("")
        
        # Get critical/high-cost findings
        critical_findings = self._extract_critical_findings(findings, risk_score)
        
        if critical_findings:
            for idx, finding in enumerate(critical_findings[:5], 1):  # Top 5
                sections.append(f"{idx}. {finding['title']}")
                sections.append(f"   Estimated Cost: ${finding['cost_low']:,.0f}-${finding['cost_high']:,.0f}")
                sections.append(f"   Source: {finding['source']}")
                if finding.get('details'):
                    sections.append(f"   Details: {finding['details']}")
                sections.append("")
        
        # Total repairs
        sections.append(f"Total Documented Repairs: ${repair_low:,.0f}-${repair_high:,.0f}")
        sections.append(f"Average Estimated Cost: ${repair_avg:,.0f}")
        sections.append("")
        
        # Section 2: Transparency Issues (if any)
        # FIXED: Check transparency_report first (more detailed), fallback to cross_reference
        transparency_report = analysis.get('transparency_report', {})
        undisclosed = transparency_report.get('undisclosed_issues', [])
        
        # Debug logging
        print(f"🔍 Transparency report exists: {bool(transparency_report)}")
        print(f"🔍 Undisclosed from transparency_report: {len(undisclosed)} items")
        if undisclosed and len(undisclosed) > 0:
            print(f"🔍 First item keys: {undisclosed[0].keys() if isinstance(undisclosed[0], dict) else 'Not a dict'}")
            print(f"🔍 First item: {undisclosed[0]}")
        
        # Fallback to cross_reference if transparency_report doesn't have it
        if not undisclosed or len(undisclosed) == 0:
            undisclosed = cross_ref.get('undisclosed_issues', [])
            print(f"🔍 Falling back to cross_reference: {len(undisclosed)} items")
            if undisclosed and len(undisclosed) > 0:
                print(f"🔍 First cross_ref item: {undisclosed[0]}")
        
        if undisclosed and len(undisclosed) > 0:
            sections.append("━" * 70)
            sections.append("DISCLOSURE CONCERNS")
            sections.append("━" * 70)
            sections.append("")
            sections.append(
                "Our analysis identified items found during inspection that were not "
                "adequately disclosed in the seller disclosure:"
            )
            sections.append("")
            
            for idx, issue in enumerate(undisclosed[:3], 1):  # Top 3
                # Handle both dict and object structures
                if isinstance(issue, dict):
                    # transparency_report uses 'finding', cross_reference might use 'issue' or 'description'
                    issue_text = issue.get('finding') or issue.get('issue') or issue.get('description') or 'Item not disclosed'
                    # Try multiple possible source fields
                    found_in = issue.get('source') or issue.get('found_in') or 'Inspection report'
                    severity = issue.get('severity', '')
                    cost = issue.get('cost') or issue.get('cost_impact')
                else:
                    # Handle object with attributes
                    issue_text = (getattr(issue, 'finding', None) or 
                                 getattr(issue, 'issue', None) or 
                                 getattr(issue, 'description', 'Item not disclosed'))
                    found_in = (getattr(issue, 'source', None) or 
                               getattr(issue, 'found_in', 'Inspection report'))
                    severity = getattr(issue, 'severity', '')
                    cost = getattr(issue, 'cost', None) or getattr(issue, 'cost_impact', None)
                
                # Format the item
                sections.append(f"{idx}. {issue_text}")
                
                # Add cost if available
                if cost and cost > 0:
                    sections.append(f"   Estimated Impact: ${cost:,.0f}")
                
                # Add severity if available
                if severity and severity != 'unknown':
                    sections.append(f"   Severity: {severity.upper()}")
                
                sections.append(f"   Found in: {found_in}")
                sections.append("")
        
        # Section 3: Offer Breakdown
        sections.append("━" * 70)
        sections.append("OFFER CALCULATION")
        sections.append("━" * 70)
        sections.append("")
        sections.append(f"Asking Price:                    ${asking_price:>12,.0f}")
        sections.append(f"Documented Repairs (Average):   -${repair_avg:>12,.0f}")
        
        # Risk adjustment
        risk_adjustment = asking_price - recommended_offer - repair_avg
        if abs(risk_adjustment) > 100:
            sections.append(f"Risk/Market Adjustment:         -${abs(risk_adjustment):>12,.0f}")
        
        sections.append(f"                                 {'─' * 15}")
        sections.append(f"Our Offer:                       ${recommended_offer:>12,.0f}")
        sections.append(f"                                 {'═' * 15}")
        sections.append("")
        sections.append(f"Discount from Ask: ${discount:,.0f} ({discount_pct:.1f}%)")
        sections.append("")
        
        # Section 4: Market Context
        sections.append("━" * 70)
        sections.append("MARKET CONTEXT")
        sections.append("━" * 70)
        sections.append("")
        
        # Get risk tier from Risk DNA (consistent with main analysis)
        risk_dna = analysis.get('risk_dna', {})
        composite = risk_dna.get('composite_score', 50)
        if composite >= 90: risk_tier = 'CRITICAL'
        elif composite >= 75: risk_tier = 'HIGH'
        elif composite >= 60: risk_tier = 'ELEVATED'
        elif composite >= 40: risk_tier = 'MODERATE'
        elif composite >= 20: risk_tier = 'LOW'
        else: risk_tier = 'MINIMAL'
        offer_score_quality = round(100 - composite)  # OfferScore (higher = better)
        
        sections.append(f"Property Risk Assessment: {risk_tier} (OfferScore™ {offer_score_quality}/100)")
        sections.append("")
        
        if composite >= 60:
            sections.append(
                "This property has below-average condition for its age and price point. "
                "Our offer reflects the significant work needed to bring it to market standards."
            )
        elif composite >= 40:
            sections.append(
                "This property has average condition with typical maintenance needs. "
                "Our offer accounts for deferred maintenance and necessary updates."
            )
        else:
            sections.append(
                "This property is in above-average condition. Our offer is competitive "
                "while accounting for documented repair needs."
            )
        sections.append("")
        
        # Section 5: Closing
        sections.append("━" * 70)
        sections.append("CONCLUSION")
        sections.append("━" * 70)
        sections.append("")
        sections.append(
            f"Our offer of ${recommended_offer:,.0f} represents fair market value given the property's "
            f"documented condition. This offer is competitive and accounts for necessary repairs "
            f"while remaining within our budget parameters."
        )
        sections.append("")
        sections.append(
            "We are serious buyers with financing in place and are prepared to move quickly. "
            "We believe this offer is fair to both parties and reflects the true condition "
            "of the property based on professional inspection and analysis."
        )
        sections.append("")
        sections.append("We look forward to working together to reach an agreement.")
        sections.append("")
        sections.append("Sincerely,")
        sections.append(buyer_name)
        sections.append("")
        sections.append("=" * 70)
        sections.append("")
        sections.append("This analysis was prepared using OfferWise AI Property Analysis")
        sections.append("For informational purposes only - not professional advice")
        
        # Combine all sections
        letter_content = "\n".join(sections)
        
        return NegotiationDocument(
            title=f"Offer Justification - {property_address}",
            content=letter_content,
            document_type='offer_letter'
        )
    
    def _extract_critical_findings(
        self, 
        findings: List[Dict], 
        risk_score: Dict
    ) -> List[Dict[str, Any]]:
        """
        Extract and format critical findings for negotiation.
        
        Prioritizes by:
        1. Safety concerns
        2. High cost items
        3. Major systems (HVAC, roof, foundation)
        """
        critical = []
        
        # Get category scores (these have costs)
        category_scores = risk_score.get('category_scores', [])
        
        for cat in category_scores:
            score = cat.get('score', 0)
            if score > 0:  # Has issues
                category = cat.get('category', {})
                category_name = category.get('value', 'Unknown') if isinstance(category, dict) else str(category)
                
                cost_low = cat.get('estimated_cost_low', 0)
                cost_high = cat.get('estimated_cost_high', 0)
                
                # Only include if significant cost
                if cost_low > 500:
                    # Find details from findings
                    details = self._find_category_details(findings, category_name)
                    
                    critical.append({
                        'title': self._format_category_name(category_name),
                        'cost_low': cost_low,
                        'cost_high': cost_high,
                        'score': score,
                        'safety': cat.get('safety_concern', False),
                        'source': 'Professional Inspection Report',
                        'details': details
                    })
        
        # Sort by priority: safety first, then cost
        critical.sort(key=lambda x: (
            not x['safety'],  # Safety first (False sorts before True)
            -x['cost_high']    # Then by cost (highest first)
        ))
        
        return critical
    
    def _find_category_details(self, findings: List[Dict], category: str) -> str:
        """Find specific details for a category from findings"""
        category_lower = category.lower()
        
        for finding in findings:
            desc = finding.get('description', '').lower()
            location = finding.get('location', '').lower()
            
            # Match category to finding
            if category_lower in desc or category_lower in location:
                # Return first sentence of description
                full_desc = finding.get('description', '')
                first_sentence = full_desc.split('.')[0] if full_desc else ''
                return first_sentence[:100] + ('...' if len(first_sentence) > 100 else '')
        
        return ""
    
    def _format_category_name(self, category: str) -> str:
        """Format category name for display"""
        # Handle enum-style names
        if '_' in category:
            words = category.split('_')
            return ' '.join(word.capitalize() for word in words)
        
        return category.capitalize()
    
    def generate_counteroffer_response(
        self,
        original_offer: float,
        seller_counteroffer: float,
        asking_price: float,
        recommended_offer: float,
        repair_costs: float
    ) -> NegotiationDocument:
        """
        Generate response to seller's counteroffer.
        
        Helps buyer decide: accept, counter, or walk away.
        """
        
        # Calculate gaps
        seller_asked_more = seller_counteroffer - original_offer
        still_above_recommended = seller_counteroffer - recommended_offer
        
        sections = []
        
        sections.append("COUNTEROFFER ANALYSIS")
        sections.append("=" * 70)
        sections.append("")
        sections.append(f"Your Original Offer:        ${original_offer:>12,.0f}")
        sections.append(f"Seller Countered At:        ${seller_counteroffer:>12,.0f}")
        sections.append(f"OfferWise Recommendation:   ${recommended_offer:>12,.0f}")
        sections.append(f"Asking Price:               ${asking_price:>12,.0f}")
        sections.append("")
        
        # Analysis
        sections.append("━" * 70)
        sections.append("ANALYSIS")
        sections.append("━" * 70)
        sections.append("")
        
        if seller_counteroffer <= recommended_offer:
            sections.append("✅ RECOMMENDATION: ACCEPT")
            sections.append("")
            sections.append(
                f"The seller's counteroffer of ${seller_counteroffer:,.0f} is at or below our "
                f"recommended price of ${recommended_offer:,.0f}. This is a good deal given "
                f"the property's condition."
            )
        elif seller_counteroffer <= recommended_offer * 1.02:  # Within 2%
            sections.append("⚠️ RECOMMENDATION: CONSIDER ACCEPTING")
            sections.append("")
            sections.append(
                f"The seller's counteroffer is ${still_above_recommended:,.0f} above our "
                f"recommended price, but within acceptable range. Consider accepting or "
                f"making a final counter at ${recommended_offer:,.0f}."
            )
        else:
            sections.append("❌ RECOMMENDATION: COUNTER OR WALK")
            sections.append("")
            sections.append(
                f"The seller's counteroffer is ${still_above_recommended:,.0f} above our "
                f"recommended price. Given documented repairs of ~${repair_costs:,.0f}, "
                f"this price does not reflect fair market value."
            )
            sections.append("")
            sections.append("YOUR OPTIONS:")
            sections.append("")
            
            # Calculate strategic counter
            strategic_counter = (seller_counteroffer + recommended_offer) / 2
            sections.append(f"1. COUNTER at ${strategic_counter:,.0f} (split the difference)")
            sections.append(f"2. HOLD FIRM at ${recommended_offer:,.0f} (our best offer)")
            sections.append(f"3. WALK AWAY (property is overpriced)")
        
        sections.append("")
        sections.append("=" * 70)
        
        content = "\n".join(sections)
        
        return NegotiationDocument(
            title="Counteroffer Response Strategy",
            content=content,
            document_type='counteroffer'
        )
    
    def generate_agent_email_template(
        self,
        property_address: str,
        recommended_offer: float,
        key_points: List[str]
    ) -> NegotiationDocument:
        """Generate email template for real estate agent"""
        
        sections = []
        
        sections.append(f"Subject: Offer Submission - {property_address}")
        sections.append("")
        sections.append("Hi [Agent Name],")
        sections.append("")
        sections.append(
            f"I'm ready to submit an offer on {property_address}. Based on my analysis "
            f"of the inspection report and property condition, I'd like to offer "
            f"${recommended_offer:,.0f}."
        )
        sections.append("")
        sections.append("Key factors supporting this offer:")
        sections.append("")
        
        for point in key_points:
            sections.append(f"• {point}")
        
        sections.append("")
        sections.append(
            "I have a detailed justification document prepared that we can share with "
            "the seller/listing agent if needed. I'm a serious buyer with financing in "
            "place and ready to move quickly."
        )
        sections.append("")
        sections.append("Can we discuss this offer and move forward?")
        sections.append("")
        sections.append("Thanks,")
        sections.append("[Your Name]")
        
        content = "\n".join(sections)
        
        return NegotiationDocument(
            title="Agent Email Template",
            content=content,
            document_type='email'
        )
    
    def generate_talking_points(
        self,
        analysis: Dict[str, Any],
        recommended_offer: float
    ) -> NegotiationDocument:
        """Generate bullet-point talking points for negotiations"""
        
        risk_score = analysis.get('risk_score', {})
        repair_low = risk_score.get('total_repair_cost_low', 0)
        repair_high = risk_score.get('total_repair_cost_high', 0)
        
        sections = []
        
        sections.append("NEGOTIATION TALKING POINTS")
        sections.append("=" * 70)
        sections.append("")
        sections.append("Use these points when discussing your offer:")
        sections.append("")
        
        sections.append("1. PROPERTY CONDITION")
        sections.append(f"   • Professional inspection identified ${repair_low:,.0f}-${repair_high:,.0f} in repairs")
        sections.append("   • All findings are documented with page references")
        sections.append("   • These are necessary repairs, not cosmetic preferences")
        sections.append("")
        
        sections.append("2. MARKET VALUE")
        sections.append(f"   • Our offer of ${recommended_offer:,.0f} reflects fair market value")
        sections.append("   • Price accounts for documented condition")
        sections.append("   • We're serious buyers ready to close quickly")
        sections.append("")
        
        sections.append("3. PROFESSIONAL ANALYSIS")
        sections.append("   • Used professional property analysis service")
        sections.append("   • Data-driven offer, not emotional decision")
        sections.append("   • Have detailed justification available")
        sections.append("")
        
        sections.append("4. FLEXIBILITY")
        sections.append("   • Open to discussing terms and timeline")
        sections.append("   • Can close quickly if needed")
        sections.append("   • Willing to work with seller's needs")
        sections.append("")
        
        sections.append("5. IF ASKED TO GO HIGHER")
        sections.append("   • 'Our offer is based on documented repair needs'")
        sections.append("   • 'We're at our maximum given the property condition'")
        sections.append("   • 'Would seller consider a credit for repairs instead?'")
        sections.append("")
        
        sections.append("=" * 70)
        
        content = "\n".join(sections)
        
        return NegotiationDocument(
            title="Negotiation Talking Points",
            content=content,
            document_type='talking_points'
        )


# Quick test
if __name__ == "__main__":
    # Test with sample data
    toolkit = NegotiationToolkit()
    
    sample_analysis = {
        'analysis_date': '2026-01-13',
        'risk_score': {
            'overall_risk_score': 58,
            'risk_tier': 'MODERATE',
            'total_repair_cost_low': 15000,
            'total_repair_cost_high': 22000,
            'category_scores': [
                {
                    'category': {'value': 'HVAC'},
                    'score': 75,
                    'estimated_cost_low': 8000,
                    'estimated_cost_high': 12000,
                    'safety_concern': False
                },
                {
                    'category': {'value': 'ROOF'},
                    'score': 45,
                    'estimated_cost_low': 3500,
                    'estimated_cost_high': 5500,
                    'safety_concern': False
                }
            ]
        },
        'inspection_report': {
            'inspection_findings': [
                {
                    'description': 'HVAC system is 19 years old, nearing end of useful life',
                    'location': 'Basement mechanical room'
                }
            ]
        },
        'cross_reference': {
            'undisclosed_issues': []
        },
        'offer_strategy': {}
    }
    
    letter = toolkit.generate_offer_justification_letter(
        analysis=sample_analysis,
        property_address="123 Main St, Anytown, CA",
        asking_price=495000,
        recommended_offer=475000,
        buyer_name="John Smith"
    )
    
    print(letter.content)
