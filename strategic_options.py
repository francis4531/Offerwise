"""
Strategic Options Generator for OfferWise
Generates property-specific buyer action recommendations
"""

from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class StrategicOption:
    """A strategic buying option with concrete tactics"""
    title: str
    strategy: str  # One-line summary
    tactics: List[str]  # Concrete actionable steps
    rationale: str  # Why this works
    risk_level: str  # "low", "medium", "high"
    probability_success: str  # "low", "medium", "high"
    next_steps: List[str]  # Immediate actions to take
    icon: str  # Emoji for display


class StrategicOptionsGenerator:
    """Generate property-specific buyer strategies"""
    
    def generate_options(
        self,
        offer_score: float,
        findings_count: int,
        transparency_score: float,
        total_repair_costs: float,
        property_price: float,
        buyer_profile: Optional[Dict] = None
    ) -> List[StrategicOption]:
        """
        Generate 3 strategic options based on property analysis.
        
        Args:
            offer_score: OfferScore (0-100, higher is better)
            findings_count: Number of inspection findings
            transparency_score: Seller transparency (0-100)
            total_repair_costs: Estimated total repair costs
            property_price: Asking price
            buyer_profile: Buyer preferences/constraints
            
        Returns:
            List of 3 StrategicOption objects
        """
        
        # Determine property tier
        if offer_score >= 90:
            return self._generate_excellent_options(
                property_price, buyer_profile, findings_count
            )
        elif offer_score >= 70:
            return self._generate_good_options(
                property_price, total_repair_costs, findings_count
            )
        elif offer_score >= 50:
            return self._generate_moderate_options(
                property_price, total_repair_costs, transparency_score, findings_count
            )
        else:
            return self._generate_poor_options(
                property_price, total_repair_costs, findings_count
            )
    
    def _generate_excellent_options(
        self,
        property_price: float,
        buyer_profile: Optional[Dict],
        findings_count: int
    ) -> List[StrategicOption]:
        """Options for properties scoring 90-100"""
        
        options = []
        
        # Check if buyer is worried about losing property (keyword-based from free-form text)
        biggest_regret = buyer_profile.get('biggest_regret', '').lower() if buyer_profile else ""
        is_competitive_minded = (
            buyer_profile and 
            ("lose" in biggest_regret or "losing" in biggest_regret or "miss" in biggest_regret or "another buyer" in biggest_regret)
        )
        
        # Option 1: Competitive Edge Package
        options.append(StrategicOption(
            title="The Competitive Edge Package",
            strategy="Position yourself as the ideal buyer with speed and certainty",
            tactics=[
                f"Offer full asking price: ${property_price:,.0f}",
                "Waive inspection contingency (property is excellent)",
                "Offer flexible closing date to match seller's preferred timeline",
                "Include strong pre-approval letter + proof of funds",
                "Write personal letter to seller about why you love this home",
                "Commit to 30-day close (or 15 days if paying cash)"
            ],
            rationale=(
                "Properties scoring 90+ are rare and move fast in any market. "
                "Your analysis confirms it's solid—now focus on winning the deal, "
                "not saving 2-3% that could cost you the home."
            ),
            risk_level="low",
            probability_success="high",
            next_steps=[
                "Get updated pre-approval letter from lender today",
                "Draft personal letter highlighting specific features you love",
                "Call listing agent to understand seller's priorities and timeline",
                "Submit complete offer package within 24 hours"
            ],
            icon="🏆"
        ))
        
        # Option 2: Escalation Strategy
        max_escalation = property_price * 1.03
        options.append(StrategicOption(
            title="The Automated Escalation Strategy",
            strategy="Let an escalation clause compete for you automatically",
            tactics=[
                f"Starting offer: ${property_price:,.0f} (asking price)",
                f"Escalation clause: Beat highest offer by $2,500 increments",
                f"Cap escalation at: ${max_escalation:,.0f} (103% of asking)",
                "Include $10,000 appraisal gap coverage commitment",
                "Require proof of funds for gap coverage"
            ],
            rationale=(
                "Win without overpaying unnecessarily. The escalation clause "
                "automatically beats other offers up to your maximum, so you pay "
                "the minimum needed to win—not a penny more."
            ),
            risk_level="low",
            probability_success="high",
            next_steps=[
                "Confirm your true maximum budget with your financial advisor",
                "Have lender draft proper escalation clause language",
                "Prepare proof of funds documentation for $10K gap coverage",
                "Submit offer with 48-hour expiration to create urgency"
            ],
            icon="📈"
        ))
        
        # Option 3: Cash-Equivalent Strategy
        options.append(StrategicOption(
            title="The Cash-Equivalent Strategy",
            strategy="Make your financed offer compete with cash buyers",
            tactics=[
                f"Offer ${property_price * 1.01:,.0f} (1% over asking)",
                "Put 25% down payment (demonstrates financial strength)",
                "Complete full underwriting BEFORE submitting offer",
                "Waive financing contingency (you're confident in approval)",
                "Guarantee 30-day close with penalties if you delay",
                "Keep only appraisal contingency (protects if value comes low)"
            ],
            rationale=(
                "Sellers love certainty more than a few extra dollars. Completing "
                "underwriting before offering removes financing risk and makes your "
                "financed offer look like cash to the seller."
            ),
            risk_level="medium",
            probability_success="high",
            next_steps=[
                "Call lender immediately: rush full underwriting this week",
                "Request 'Clear to Close' letter before submitting offer",
                "Verify 25% down payment funds are liquid and available",
                "Coordinate with agent to submit strongest offer package"
            ],
            icon="💰"
        ))
        
        return options
    
    def _generate_good_options(
        self,
        property_price: float,
        total_repair_costs: float,
        findings_count: int
    ) -> List[StrategicOption]:
        """Options for properties scoring 70-89"""
        
        options = []
        
        repair_credit = min(5000, total_repair_costs * 1.5)  # Ask for 1.5x actual
        
        # Option 1: Repair Credit Strategy
        options.append(StrategicOption(
            title="The Repair Credit Strategy",
            strategy="Get money for repairs while keeping deal momentum",
            tactics=[
                f"Offer ${property_price * 0.985:,.0f} (98.5% of asking)",
                f"Request ${repair_credit:,.0f} repair credit at closing",
                "Keep standard inspection contingency for protection",
                "Seller doesn't do repairs—just gives you money at close",
                "You handle repairs after closing on your timeline"
            ],
            rationale=(
                f"Seller gets near-asking price, you get ${repair_credit:,.0f} for repairs. "
                "No negotiating contractor selection, no delays waiting for work to complete. "
                "Clean, fast transaction that works for everyone."
            ),
            risk_level="low",
            probability_success="high",
            next_steps=[
                "Get 2-3 contractor estimates for actual repair costs",
                f"Draft offer with specific ${repair_credit:,.0f} credit request",
                "Include standard inspection contingency language",
                "Submit offer with 3-day response deadline"
            ],
            icon="💵"
        ))
        
        # Option 2: Pre-Negotiated Approach
        options.append(StrategicOption(
            title="The Pre-Negotiated Approach",
            strategy="Present data-driven offer backed by contractor quotes",
            tactics=[
                "Get 3 licensed contractor bids BEFORE making offer",
                f"Present documentation: 'Repairs cost ${total_repair_costs:,.0f}'",
                f"Offer ${property_price - total_repair_costs:,.0f} (asking - documented costs)",
                "Include 10-day inspection period for verification only",
                "Show seller you've done homework—hard to argue with facts"
            ],
            rationale=(
                "This isn't emotional negotiation—it's math. You have documentation "
                "showing what repairs actually cost. Seller can either accept your "
                "fair offer or try to get full price from someone who hasn't done analysis."
            ),
            risk_level="medium",
            probability_success="medium",
            next_steps=[
                "Contact 3 licensed contractors for itemized repair estimates",
                "Take photos of issues found to share with contractors",
                "Compile professional bid package to present with offer",
                "Submit offer with contractor quotes attached as exhibits"
            ],
            icon="📊"
        ))
        
        # Option 3: Home Warranty Play
        options.append(StrategicOption(
            title="The Home Warranty Strategy",
            strategy="Offer full price in exchange for seller-paid warranty",
            tactics=[
                f"Offer full asking price: ${property_price:,.0f}",
                "Request seller include 1-year home warranty ($500-800 cost)",
                "Warranty covers you for systems/appliances in first year",
                "Waive repair requests under $500 each (keeps deal moving)",
                "Focus on major issues only in negotiation"
            ],
            rationale=(
                f"Seller pays $500-800, you get protection worth ${repair_credit:,.0f}+ in potential "
                "claims. Small cost to seller, big value to you. Plus seller gets full price "
                "and quick close—everyone wins."
            ),
            risk_level="low",
            probability_success="high",
            next_steps=[
                "Research home warranty companies (American Home Shield, Choice Home)",
                "Verify warranty covers specific systems/issues you're concerned about",
                "Include warranty requirement in offer with specific provider",
                "Submit offer positioning warranty as win-win solution"
            ],
            icon="🛡️"
        ))
        
        return options
    
    def _generate_moderate_options(
        self,
        property_price: float,
        total_repair_costs: float,
        transparency_score: float,
        findings_count: int
    ) -> List[StrategicOption]:
        """Options for properties scoring 50-69"""
        
        options = []
        
        as_is_discount = property_price * 0.08  # 8% discount
        conditional_price = property_price * 0.93  # 7% discount
        
        # Option 1: Conditional Offer
        options.append(StrategicOption(
            title="The Conditional Offer",
            strategy="Seller completes major repairs before you close",
            tactics=[
                f"Offer ${conditional_price:,.0f} (93% of asking)",
                "Contingent on seller completing all major repairs before closing",
                "You provide list of approved/licensed contractors seller must use",
                "Re-inspection required to verify work completed properly",
                "Your lender will need sign-off that repairs meet standards"
            ],
            rationale=(
                "Shifts repair burden to seller while keeping deal alive. You know "
                "repairs are done right (by licensed contractors) and you're not "
                "taking on project risk. Seller gets deal done but at fair price."
            ),
            risk_level="medium",
            probability_success="medium",
            next_steps=[
                "Create list of pre-approved contractors for each trade",
                "Define specific completion standards for each repair",
                "Include re-inspection clause with certified inspector",
                "Build in 30-day repair completion timeline before close"
            ],
            icon="🔧"
        ))
        
        # Option 2: As-Is Discount
        options.append(StrategicOption(
            title="The As-Is Discount Strategy",
            strategy="Buy property exactly as-is with appropriate discount",
            tactics=[
                f"Offer ${property_price - as_is_discount:,.0f} (92% of asking)",
                "Purchase property in current condition—no repair requests",
                "Waive ALL repair negotiations (keeps transaction simple)",
                "Quick 30-day close (or less if seller prefers)",
                "Both parties save time, money, and hassle"
            ],
            rationale=(
                f"Clean, fast transaction. Seller gets certainty of close without "
                f"repair headaches, you get ${as_is_discount:,.0f} discount to handle repairs "
                "yourself. No contractor coordination, no delays, no surprises."
            ),
            risk_level="medium",
            probability_success="high",
            next_steps=[
                "Verify you have budget for post-closing repairs",
                "Line up contractors to start work immediately after close",
                "Calculate if discount covers repairs plus your time/hassle",
                "Submit offer emphasizing quick, clean transaction"
            ],
            icon="⚡"
        ))
        
        # Option 3: Dual-Track Approach
        options.append(StrategicOption(
            title="The Dual-Track Negotiation",
            strategy="Make initial offer while simultaneously gathering repair data",
            tactics=[
                f"Initial offer: ${property_price * 0.95:,.0f} (95% of asking)",
                "Simultaneously get 3 contractor bids for all major repairs",
                "After inspection period, present data: 'Market value minus repairs'",
                f"Final position: ${property_price - total_repair_costs:,.0f} (asking - actual costs)",
                "Be prepared to walk away if seller won't negotiate reasonably"
            ],
            rationale=(
                "Show seller you're serious but informed. You start with reasonable "
                "offer, then prove your numbers with contractor documentation. If "
                "seller won't meet fair price, you walk—plenty of other properties."
            ),
            risk_level="medium",
            probability_success="medium",
            next_steps=[
                "Submit initial offer at 95% to get property under contract",
                "During inspection period, get comprehensive contractor estimates",
                "Calculate true market value: asking price - verified repair costs",
                "Present data-driven counter-offer with deadline for response"
            ],
            icon="🎯"
        ))
        
        return options
    
    def _generate_poor_options(
        self,
        property_price: float,
        total_repair_costs: float,
        findings_count: int
    ) -> List[StrategicOption]:
        """Options for properties scoring <50"""
        
        options = []
        
        lowball_price = property_price * 0.75  # 25% discount
        investment_price = (property_price * 1.2) - total_repair_costs - (property_price * 0.2)
        
        # Option 1: Walk Away (Actually valuable here!)
        options.append(StrategicOption(
            title="Walk Away & Keep Looking",
            strategy="Sometimes the best decision is to pass on a bad deal",
            tactics=[
                f"This property has {findings_count} significant issues totaling ${total_repair_costs:,.0f}",
                "Your time and money are better spent finding a better property",
                "Use this analysis experience to spot red flags faster",
                "Continue your search with confidence you made the right call",
                "Properties in better condition are worth the wait"
            ],
            rationale=(
                "Not every property is a good buy—even at a discount. The stress, "
                "time, and unexpected costs of major repairs often exceed the savings. "
                "You've done the analysis; trust it. Find something better."
            ),
            risk_level="none",
            probability_success="guaranteed",
            next_steps=[
                "Thank the seller's agent for their time professionally",
                "Review what red flags you spotted for future searches",
                "Resume property search with your criteria refined",
                "Use this analysis to educate your agent on what to avoid"
            ],
            icon="🚪"
        ))
        
        # Option 2: Strategic Low-Ball
        options.append(StrategicOption(
            title="The Strategic Low-Ball Offer",
            strategy="Only pursue if you're prepared for major renovation project",
            tactics=[
                f"Offer ${lowball_price:,.0f} (75% of asking)",
                "All-cash strongly preferred (financing may not approve condition)",
                "Quick close within 15 days (show you're serious despite low price)",
                "Purchase completely as-is with no inspection contingency",
                "Include escalator: 'Will go to 78% if you can close in 10 days'"
            ],
            rationale=(
                f"Seller may be distressed and need quick sale. At ${lowball_price:,.0f}, "
                f"you have ${property_price - lowball_price:,.0f} buffer for repairs and profit. "
                "Only works if seller is motivated—but worth a shot if you like location."
            ),
            risk_level="high",
            probability_success="low",
            next_steps=[
                "Verify seller's motivation level through agent",
                "Ensure you have cash available or hard money financing lined up",
                "Get comprehensive contractor bids before submitting offer",
                "Submit offer with 48-hour expiration to force quick decision"
            ],
            icon="💰"
        ))
        
        # Option 3: Investor Flip Strategy
        options.append(StrategicOption(
            title="The Investor Wholesale Strategy",
            strategy="Buy low and resell to another investor without doing repairs",
            tactics=[
                f"After Repair Value (ARV): ${property_price * 1.2:,.0f}",
                f"Minus repair costs: ${total_repair_costs:,.0f}",
                f"Minus 20% profit margin: ${(property_price * 1.2) * 0.2:,.0f}",
                f"Your maximum offer: ${investment_price:,.0f}",
                f"Resell assignment to investor for ${investment_price + 15000:,.0f}",
                "Make $15K-25K without doing any actual work"
            ],
            rationale=(
                "If you found this property below market, other investors want it too. "
                "Buy it under contract, then 'wholesale' the contract to another investor "
                "who will do the repairs. You found the deal—that's worth something."
            ),
            risk_level="medium",
            probability_success="medium",
            next_steps=[
                "Research local investor groups and wholesalers in your area",
                "Ensure your purchase contract allows assignment to another buyer",
                "Calculate ARV using comparable sales in excellent condition",
                "Build buyer list before submitting offer so you can close fast"
            ],
            icon="🔄"
        ))
        
        return options


# Usage example
if __name__ == "__main__":
    generator = StrategicOptionsGenerator()
    
    # Example: Excellent property
    options = generator.generate_options(
        offer_score=95,
        findings_count=0,
        transparency_score=100,
        total_repair_costs=0,
        property_price=1480000,
        buyer_profile={'biggest_regret': 'lose_house'}
    )
    
    print("STRATEGIC OPTIONS:")
    print("=" * 80)
    for i, opt in enumerate(options, 1):
        print(f"\n{opt.icon} OPTION {i}: {opt.title}")
        print(f"Strategy: {opt.strategy}")
        print(f"\nTactics:")
        for tactic in opt.tactics:
            print(f"  • {tactic}")
        print(f"\nRationale: {opt.rationale}")
        print(f"Risk: {opt.risk_level} | Success: {opt.probability_success}")
        print(f"\nNext Steps:")
        for step in opt.next_steps:
            print(f"  {step}")
        print("-" * 80)
