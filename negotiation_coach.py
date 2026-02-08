"""
OfferWise Negotiation Coach Agent
AI-powered negotiation strategy based on property analysis

Version: 1.0.0
"""

import anthropic
import json
import os
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class NegotiationStrategy:
    """Complete negotiation strategy output"""
    leverage_points: List[Dict[str, str]]
    talking_points: List[str]
    offer_justification: str
    counter_strategies: List[Dict[str, str]]
    risk_warnings: List[str]
    confidence_level: str
    recommended_approach: str
    opening_script: str
    offer_letter: str


class NegotiationCoach:
    """
    AI Agent that generates negotiation strategies based on property analysis.
    
    Uses the OfferWise analysis (OfferScore, Risk DNA, issues) to create
    actionable negotiation tactics, talking points, and offer letters.
    """
    
    def __init__(self):
        self.api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not self.api_key:
            logger.warning("⚠️ ANTHROPIC_API_KEY not set - Negotiation Coach disabled")
            self.client = None
            self.enabled = False
        else:
            try:
                self.client = anthropic.Anthropic(api_key=self.api_key)
                self.enabled = True
                logger.info("✅ Negotiation Coach Agent initialized")
            except Exception as e:
                logger.error(f"❌ Failed to initialize Negotiation Coach: {e}")
                self.client = None
                self.enabled = False
    
    def generate_strategy(
        self,
        analysis: Dict[str, Any],
        buyer_profile: Optional[Dict[str, Any]] = None,
        negotiation_style: str = "balanced"
    ) -> Dict[str, Any]:
        """
        Generate complete negotiation strategy from analysis.
        
        Args:
            analysis: OfferWise analysis results
            buyer_profile: Optional buyer preferences
            negotiation_style: "aggressive", "balanced", or "collaborative"
            
        Returns:
            Complete negotiation strategy with talking points, leverage, etc.
        """
        if not self.enabled:
            return {
                'success': False,
                'error': 'Negotiation Coach not available - API key not configured'
            }
        
        try:
            # Extract key data from analysis
            property_address = analysis.get('property_address', 'the property')
            asking_price = round(analysis.get('property_price', 0))
            recommended_offer = round(analysis.get('offer_strategy', {}).get('recommended_offer', asking_price))
            risk_dna = analysis.get('risk_dna', {})
            offer_score = round(100 - float(risk_dna.get('composite_score', 50) or 50))
            
            # Get issues for leverage
            issues = self._extract_issues(analysis)
            
            # Calculate savings potential
            savings = max(0, asking_price - recommended_offer)
            savings_pct = (savings / asking_price * 100) if asking_price > 0 else 0
            
            # Build the prompt
            prompt = self._build_strategy_prompt(
                property_address=property_address,
                asking_price=asking_price,
                recommended_offer=recommended_offer,
                offer_score=offer_score,
                issues=issues,
                risk_dna=risk_dna,
                savings=savings,
                savings_pct=savings_pct,
                buyer_profile=buyer_profile,
                negotiation_style=negotiation_style
            )
            
            # Call Claude
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Parse response
            result_text = response.content[0].text
            strategy = self._parse_strategy_response(result_text)
            
            # Add metadata
            strategy['success'] = True
            strategy['property_address'] = property_address
            strategy['asking_price'] = asking_price
            strategy['recommended_offer'] = recommended_offer
            strategy['potential_savings'] = savings
            strategy['savings_percentage'] = round(savings_pct, 1)
            strategy['negotiation_style'] = negotiation_style
            
            return strategy
            
        except Exception as e:
            logger.error(f"Error generating negotiation strategy: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _extract_issues(self, analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract and prioritize issues from analysis"""
        issues = []
        
        # Get red flags
        red_flags = analysis.get('red_flags', [])
        for flag in red_flags[:10]:  # Top 10
            issues.append({
                'title': flag.get('title', flag.get('issue', 'Issue')),
                'description': flag.get('description', flag.get('details', '')),
                'severity': flag.get('severity', 'medium'),
                'estimated_cost': flag.get('estimated_cost', flag.get('repair_cost', 0)),
                'source': flag.get('source', 'inspection')
            })
        
        # Get from issues list if red_flags empty
        if not issues:
            for issue in analysis.get('issues', [])[:10]:
                issues.append({
                    'title': issue.get('title', issue.get('description', 'Issue')),
                    'description': issue.get('description', ''),
                    'severity': issue.get('severity', 'medium'),
                    'estimated_cost': issue.get('repair_cost', 0),
                    'source': issue.get('category', 'inspection')
                })
        
        # Sort by severity and cost
        severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        issues.sort(key=lambda x: (
            severity_order.get(x.get('severity', 'medium'), 2),
            -(x.get('estimated_cost', 0) or 0)
        ))
        
        return issues
    
    def _build_strategy_prompt(
        self,
        property_address: str,
        asking_price: int,
        recommended_offer: int,
        offer_score: int,
        issues: List[Dict],
        risk_dna: Dict,
        savings: int,
        savings_pct: float,
        buyer_profile: Optional[Dict],
        negotiation_style: str
    ) -> str:
        """Build the prompt for strategy generation"""
        
        # Format issues for prompt
        issues_text = ""
        total_repair_cost = 0
        for i, issue in enumerate(issues[:8], 1):
            cost = issue.get('estimated_cost', 0) or 0
            total_repair_cost += cost
            cost_str = f" (Est. ${cost:,})" if cost > 0 else ""
            issues_text += f"{i}. [{issue.get('severity', 'medium').upper()}] {issue['title']}{cost_str}\n"
            if issue.get('description'):
                issues_text += f"   Details: {issue['description'][:200]}\n"
        
        if not issues_text:
            issues_text = "No significant issues found in the analysis.\n"
        
        # Risk DNA summary
        risk_summary = ""
        if risk_dna:
            risk_summary = f"""
Risk DNA Scores (higher = more risk):
- Structural: {risk_dna.get('structural_score', 'N/A')}/100
- Systems: {risk_dna.get('systems_score', 'N/A')}/100
- Transparency: {risk_dna.get('transparency_score', 'N/A')}/100
- Financial: {risk_dna.get('financial_score', 'N/A')}/100
- Composite Score: {risk_dna.get('composite_score', 'N/A')}/100
"""
        
        # Buyer profile context
        buyer_context = ""
        if buyer_profile:
            buyer_context = f"""
Buyer Profile:
- Max Budget: ${buyer_profile.get('max_budget', 'Not specified'):,}
- Repair Tolerance: {buyer_profile.get('repair_tolerance', 'moderate')}
- Ownership Duration: {buyer_profile.get('ownership_duration', 'Not specified')}
- Biggest Fear: {buyer_profile.get('biggest_regret', 'Not specified')}
"""
        
        # Style instructions
        style_instructions = {
            'aggressive': "Take a firm stance. Emphasize every issue. Push for maximum concessions. Be willing to walk away.",
            'balanced': "Be firm but fair. Use data to justify position. Leave room for compromise while protecting interests.",
            'collaborative': "Frame as problem-solving together. Acknowledge seller's perspective. Seek win-win outcomes."
        }
        
        prompt = f"""You are an expert real estate negotiation coach. Based on the following property analysis, generate a comprehensive negotiation strategy.

PROPERTY DETAILS:
- Address: {property_address}
- Asking Price: ${asking_price:,}
- AI Recommended Offer: ${recommended_offer:,}
- Potential Savings: ${savings:,} ({savings_pct:.1f}% below asking)
- OfferScore™: {offer_score}/100 (higher = better property quality)

ISSUES FOUND IN ANALYSIS:
{issues_text}
Total Estimated Repair Costs: ${total_repair_cost:,}

{risk_summary}
{buyer_context}

NEGOTIATION STYLE: {negotiation_style.upper()}
{style_instructions.get(negotiation_style, style_instructions['balanced'])}

Please generate a complete negotiation strategy with the following sections. Use the exact headers shown:

---LEVERAGE_POINTS---
List 3-5 strongest leverage points from the analysis. For each, provide:
- Point: The specific issue or fact
- How to use it: Specific language or approach
- Potential value: Dollar amount this could justify

---TALKING_POINTS---
List 5-7 specific talking points to use with the seller/agent. These should be professional, factual, and persuasive.

---RECOMMENDED_APPROACH---
A 2-3 sentence summary of the overall recommended negotiation approach.

---OPENING_SCRIPT---
Write a 3-4 sentence opening statement the buyer could use when presenting their offer or speaking with the seller's agent.

---COUNTER_STRATEGIES---
For each likely seller objection, provide a response:
- Objection: What they might say
- Response: How to handle it

---RISK_WARNINGS---
List 2-3 things the buyer should watch out for or be prepared for during negotiation.

---CONFIDENCE_LEVEL---
Rate confidence in achieving the recommended offer price: HIGH, MEDIUM, or LOW, with brief explanation.

---OFFER_LETTER---
Write a professional offer letter that:
1. Opens with genuine interest in the property
2. Presents the offer price with justification
3. References specific findings professionally (not aggressively)
4. Includes a reasonable timeline
5. Closes positively

The letter should be 200-300 words, professional in tone, and suitable to submit with a formal offer.
"""
        
        return prompt
    
    def _parse_strategy_response(self, response_text: str) -> Dict[str, Any]:
        """Parse Claude's response into structured strategy"""
        
        strategy = {
            'leverage_points': [],
            'talking_points': [],
            'recommended_approach': '',
            'opening_script': '',
            'counter_strategies': [],
            'risk_warnings': [],
            'confidence_level': 'MEDIUM',
            'confidence_explanation': '',
            'offer_letter': ''
        }
        
        # Parse each section
        sections = {
            '---LEVERAGE_POINTS---': 'leverage_points',
            '---TALKING_POINTS---': 'talking_points',
            '---RECOMMENDED_APPROACH---': 'recommended_approach',
            '---OPENING_SCRIPT---': 'opening_script',
            '---COUNTER_STRATEGIES---': 'counter_strategies',
            '---RISK_WARNINGS---': 'risk_warnings',
            '---CONFIDENCE_LEVEL---': 'confidence_level',
            '---OFFER_LETTER---': 'offer_letter'
        }
        
        current_section = None
        current_content = []
        
        for line in response_text.split('\n'):
            line_stripped = line.strip()
            
            # Check if this is a section header
            if line_stripped in sections:
                # Save previous section
                if current_section:
                    self._save_section(strategy, current_section, current_content)
                current_section = sections[line_stripped]
                current_content = []
            elif current_section:
                current_content.append(line)
        
        # Save last section
        if current_section:
            self._save_section(strategy, current_section, current_content)
        
        return strategy
    
    def _save_section(self, strategy: Dict, section_name: str, content: List[str]) -> None:
        """Save parsed content to appropriate strategy field"""
        
        text = '\n'.join(content).strip()
        
        if section_name == 'leverage_points':
            strategy['leverage_points'] = self._parse_leverage_points(text)
        elif section_name == 'talking_points':
            strategy['talking_points'] = self._parse_list(text)
        elif section_name == 'counter_strategies':
            strategy['counter_strategies'] = self._parse_counter_strategies(text)
        elif section_name == 'risk_warnings':
            strategy['risk_warnings'] = self._parse_list(text)
        elif section_name == 'confidence_level':
            # Extract HIGH/MEDIUM/LOW and explanation
            if 'HIGH' in text.upper():
                strategy['confidence_level'] = 'HIGH'
            elif 'LOW' in text.upper():
                strategy['confidence_level'] = 'LOW'
            else:
                strategy['confidence_level'] = 'MEDIUM'
            strategy['confidence_explanation'] = text
        else:
            strategy[section_name] = text
    
    def _parse_leverage_points(self, text: str) -> List[Dict[str, str]]:
        """Parse leverage points into structured format"""
        points = []
        current_point = {}
        
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                if current_point:
                    points.append(current_point)
                    current_point = {}
                continue
            
            # Look for structured format
            if line.startswith('- Point:') or line.startswith('Point:'):
                if current_point:
                    points.append(current_point)
                current_point = {'point': line.split(':', 1)[1].strip()}
            elif line.startswith('- How to use') or line.startswith('How to use'):
                current_point['how_to_use'] = line.split(':', 1)[1].strip() if ':' in line else ''
            elif line.startswith('- Potential value') or line.startswith('Potential value'):
                current_point['potential_value'] = line.split(':', 1)[1].strip() if ':' in line else ''
            elif line.startswith(('1.', '2.', '3.', '4.', '5.', '-', '•')):
                # Simple list format
                if current_point:
                    points.append(current_point)
                clean_line = line.lstrip('0123456789.-•) ').strip()
                current_point = {'point': clean_line, 'how_to_use': '', 'potential_value': ''}
        
        if current_point:
            points.append(current_point)
        
        return points[:5]
    
    def _parse_counter_strategies(self, text: str) -> List[Dict[str, str]]:
        """Parse counter strategies into objection/response pairs"""
        strategies = []
        current = {}
        
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                if current and 'objection' in current:
                    strategies.append(current)
                    current = {}
                continue
            
            if line.startswith('- Objection:') or line.startswith('Objection:'):
                if current and 'objection' in current:
                    strategies.append(current)
                current = {'objection': line.split(':', 1)[1].strip()}
            elif line.startswith('- Response:') or line.startswith('Response:'):
                current['response'] = line.split(':', 1)[1].strip()
            elif 'objection' in current and 'response' not in current:
                current['objection'] += ' ' + line
            elif 'response' in current:
                current['response'] += ' ' + line
        
        if current and 'objection' in current:
            strategies.append(current)
        
        return strategies[:5]
    
    def _parse_list(self, text: str) -> List[str]:
        """Parse a bulleted/numbered list into strings"""
        items = []
        
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # Remove common list prefixes
            clean_line = line.lstrip('0123456789.-•*) ').strip()
            if clean_line:
                items.append(clean_line)
        
        return items[:7]
    
    def generate_quick_tips(self, analysis: Dict[str, Any]) -> List[str]:
        """Generate quick negotiation tips without full strategy"""
        
        tips = []
        
        # Tip based on OfferScore (100 - composite, higher = better)
        risk_dna = analysis.get('risk_dna', {})
        offer_score = round(100 - float(risk_dna.get('composite_score', 50) or 50))
        if offer_score <= 30:
            tips.append("🚨 High-risk property - you have significant negotiating leverage. Don't be afraid to ask for major concessions.")
        elif offer_score <= 60:
            tips.append("⚠️ Moderate issues found - use documented findings to justify a below-asking offer.")
        else:
            tips.append("✅ Clean property - focus negotiation on market conditions and timing.")
        
        # Tip based on repair costs
        risk_score = analysis.get('risk_score', {})
        repair_low = risk_score.get('total_repair_cost_low', 0) or 0
        repair_high = risk_score.get('total_repair_cost_high', 0) or 0
        repair_avg = round((repair_low + repair_high) / 2)
        if repair_avg > 20000:
            tips.append(f"💰 ${repair_avg:,} in estimated repairs - request as price reduction or seller credit.")
        elif repair_avg > 5000:
            tips.append(f"🔧 ${repair_avg:,} in estimated repairs - use this to justify your offer price.")
        
        # Tip based on transparency
        transparency = risk_dna.get('transparency_score', 0)
        if transparency > 60:
            tips.append("🔍 Seller disclosure has gaps - request additional documentation before finalizing.")
        
        # General tips
        tips.append("📋 Always get your own inspection, even if you've seen the seller's.")
        tips.append("⏰ Don't rush - a few days of negotiation can save thousands.")
        
        return tips[:5]
