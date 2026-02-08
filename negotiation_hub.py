"""
OfferWise Negotiation Hub
Unified negotiation system combining AI strategy + professional documents

Version: 1.0.0

Architecture:
┌─────────────────────────────────────────────────────────────┐
│                     NegotiationHub                           │
│                  (Unified Entry Point)                       │
├─────────────────────────────────────────────────────────────┤
│  NegotiationCoach (AI)  ──▶  NegotiationToolkit (Templates) │
│  • Strategy generation       • Formatted documents          │
│  • Leverage analysis         • PDF-ready output             │
│  • Counter strategies        • Professional letters         │
└─────────────────────────────────────────────────────────────┘
"""

import anthropic
import os
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class NegotiationHub:
    """
    Unified negotiation system combining:
    - AI-powered strategy (NegotiationCoach)
    - Professional documents (NegotiationToolkit)
    
    Usage:
        hub = NegotiationHub()
        
        # Full package (AI + documents)
        result = hub.generate_full_package(analysis, style='balanced')
        
        # Strategy only (AI)
        strategy = hub.generate_strategy(analysis, style='aggressive')
        
        # Document only (template, instant)
        doc = hub.generate_document(analysis, 'offer_letter')
        
        # Quick tips (instant)
        tips = hub.get_quick_tips(analysis)
    """
    
    def __init__(self):
        self.api_key = os.environ.get('ANTHROPIC_API_KEY')
        
        if self.api_key:
            try:
                self.client = anthropic.Anthropic(api_key=self.api_key)
                self.ai_enabled = True
                logger.info("✅ NegotiationHub initialized with AI")
            except Exception as e:
                logger.error(f"❌ AI init failed: {e}")
                self.client = None
                self.ai_enabled = False
        else:
            logger.warning("⚠️ No API key - AI disabled")
            self.client = None
            self.ai_enabled = False
    
    # ========================================================================
    # PUBLIC API
    # ========================================================================
    
    def generate_full_package(
        self,
        analysis: Dict[str, Any],
        buyer_profile: Optional[Dict] = None,
        style: str = "balanced"
    ) -> Dict[str, Any]:
        """
        Generate complete package: AI strategy + formatted documents.
        """
        try:
            # Extract key values
            property_address = analysis.get('property_address', 'Property')
            asking_price = round(analysis.get('property_price', 0))
            recommended_offer = round(analysis.get('offer_strategy', {}).get('recommended_offer', asking_price))
            savings = max(0, asking_price - recommended_offer)
            savings_pct = (savings / asking_price * 100) if asking_price > 0 else 0
            
            # Quick tips (always available)
            quick_tips = self._generate_quick_tips(analysis)
            
            # AI strategy if available
            strategy = None
            if self.ai_enabled:
                strategy = self._generate_ai_strategy(analysis, buyer_profile, style)
            
            # Generate documents
            base_strategy = strategy if strategy else self._create_basic_strategy(analysis)
            
            offer_letter = self._format_offer_letter(analysis, base_strategy, buyer_profile)
            talking_points = self._format_talking_points(analysis, base_strategy)
            agent_email = self._format_agent_email(analysis, base_strategy)
            
            return {
                'success': True,
                'property_address': property_address,
                'asking_price': asking_price,
                'recommended_offer': recommended_offer,
                'potential_savings': savings,
                'savings_percentage': round(savings_pct, 1),
                'negotiation_style': style,
                'ai_enabled': self.ai_enabled,
                'generated_at': datetime.utcnow().isoformat(),
                
                # Strategy
                'strategy': strategy,
                
                # Documents
                'offer_letter': offer_letter,
                'talking_points': talking_points,
                'agent_email': agent_email,
                
                # Tips
                'quick_tips': quick_tips
            }
            
        except Exception as e:
            logger.error(f"❌ Full package error: {e}")
            return {'success': False, 'error': str(e)}
    
    def generate_strategy(
        self,
        analysis: Dict[str, Any],
        buyer_profile: Optional[Dict] = None,
        style: str = "balanced"
    ) -> Dict[str, Any]:
        """Generate AI strategy only (no documents)."""
        if not self.ai_enabled:
            return {'success': False, 'error': 'AI not available'}
        
        try:
            strategy = self._generate_ai_strategy(analysis, buyer_profile, style)
            
            asking_price = round(analysis.get('property_price', 0))
            recommended_offer = round(analysis.get('offer_strategy', {}).get('recommended_offer', asking_price))
            savings = max(0, asking_price - recommended_offer)
            
            return {
                'success': True,
                'property_address': analysis.get('property_address', 'Property'),
                'asking_price': asking_price,
                'recommended_offer': recommended_offer,
                'potential_savings': savings,
                'savings_percentage': round((savings / asking_price * 100) if asking_price > 0 else 0, 1),
                'negotiation_style': style,
                **strategy
            }
        except Exception as e:
            logger.error(f"❌ Strategy error: {e}")
            return {'success': False, 'error': str(e)}
    
    def generate_document(
        self,
        analysis: Dict[str, Any],
        document_type: str,
        buyer_name: str = "Buyer",
        context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Generate single document (no AI, instant)."""
        try:
            base_strategy = self._create_basic_strategy(analysis)
            
            if document_type == 'offer_letter':
                doc = self._format_offer_letter(analysis, base_strategy, {'name': buyer_name})
            elif document_type == 'talking_points':
                doc = self._format_talking_points(analysis, base_strategy)
            elif document_type == 'agent_email':
                doc = self._format_agent_email(analysis, base_strategy)
            elif document_type == 'counteroffer':
                doc = self._format_counteroffer(analysis, context or {})
            else:
                return {'success': False, 'error': f'Unknown type: {document_type}'}
            
            return {'success': True, 'document': doc}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_quick_tips(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Get instant tips (no AI)."""
        try:
            tips = self._generate_quick_tips(analysis)
            return {'success': True, 'tips': tips}
        except Exception as e:
            return {'success': False, 'tips': [], 'error': str(e)}
    
    # ========================================================================
    # AI STRATEGY GENERATION
    # ========================================================================
    
    def _generate_ai_strategy(
        self,
        analysis: Dict[str, Any],
        buyer_profile: Optional[Dict],
        style: str
    ) -> Dict[str, Any]:
        """Generate AI-powered strategy."""
        
        prompt = self._build_prompt(analysis, buyer_profile, style)
        
        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return self._parse_response(response.content[0].text)
    
    def _build_prompt(
        self,
        analysis: Dict[str, Any],
        buyer_profile: Optional[Dict],
        style: str
    ) -> str:
        """Build AI prompt."""
        
        property_address = analysis.get('property_address', 'the property')
        asking_price = round(analysis.get('property_price', 0))
        recommended_offer = round(analysis.get('offer_strategy', {}).get('recommended_offer', asking_price))
        risk_dna = analysis.get('risk_dna', {})
        offer_score = round(100 - float(risk_dna.get('composite_score', 50) or 50))
        
        issues = self._extract_issues(analysis)
        total_repair = sum(i.get('estimated_cost', 0) or 0 for i in issues)
        savings = max(0, asking_price - recommended_offer)
        savings_pct = (savings / asking_price * 100) if asking_price > 0 else 0
        
        # Format issues
        issues_text = ""
        for i, issue in enumerate(issues[:8], 1):
            cost = issue.get('estimated_cost', 0) or 0
            cost_str = f" (${cost:,})" if cost > 0 else ""
            issues_text += f"{i}. [{issue.get('severity', 'medium').upper()}] {issue['title']}{cost_str}\n"
        
        if not issues_text:
            issues_text = "No significant issues found.\n"
        
        # Risk DNA
        risk_dna = analysis.get('risk_dna', {})
        risk_text = ""
        if risk_dna:
            risk_text = f"""
Risk DNA:
- Structural: {risk_dna.get('structural_score', 'N/A')}/100
- Systems: {risk_dna.get('systems_score', 'N/A')}/100
- Transparency: {risk_dna.get('transparency_score', 'N/A')}/100
"""
        
        # Style guide
        style_guide = {
            'aggressive': "Be firm. Emphasize every issue. Push for max concessions. Show willingness to walk.",
            'balanced': "Firm but fair. Lead with data. Leave room for compromise while protecting interests.",
            'collaborative': "Problem-solve together. Acknowledge seller perspective. Seek win-win."
        }
        
        return f"""You are an expert real estate negotiation coach. Generate strategy for this property:

PROPERTY: {property_address}
ASKING: ${asking_price:,}
RECOMMENDED OFFER: ${recommended_offer:,}
SAVINGS: ${savings:,} ({savings_pct:.1f}%)
OFFERSCORE: {offer_score}/100 (higher = better property quality)

ISSUES:
{issues_text}
Total Repairs: ${total_repair:,}
{risk_text}

STYLE: {style.upper()}
{style_guide.get(style, style_guide['balanced'])}

Generate with these EXACT section headers:

---LEVERAGE_POINTS---
3-5 points. Each with:
- Point: The issue
- How to use: Language to use
- Value: Dollar justification

---TALKING_POINTS---
5-7 specific talking points.

---RECOMMENDED_APPROACH---
2-3 sentence summary.

---OPENING_SCRIPT---
3-4 sentence opener for presenting offer.

---COUNTER_STRATEGIES---
3-4 objections with responses:
- Objection: What they say
- Response: Your counter

---RISK_WARNINGS---
2-3 things to watch.

---CONFIDENCE_LEVEL---
HIGH, MEDIUM, or LOW with explanation.

---OFFER_LETTER---
Professional 200-300 word letter with:
1. Genuine interest
2. Offer with justification
3. Professional reference to findings
4. Timeline
5. Positive close
"""
    
    def _parse_response(self, text: str) -> Dict[str, Any]:
        """Parse AI response."""
        
        strategy = {
            'leverage_points': [],
            'talking_points': [],
            'recommended_approach': '',
            'opening_script': '',
            'counter_strategies': [],
            'risk_warnings': [],
            'confidence_level': 'MEDIUM',
            'confidence_explanation': '',
            'offer_letter_draft': ''
        }
        
        sections = {
            '---LEVERAGE_POINTS---': 'leverage_points',
            '---TALKING_POINTS---': 'talking_points',
            '---RECOMMENDED_APPROACH---': 'recommended_approach',
            '---OPENING_SCRIPT---': 'opening_script',
            '---COUNTER_STRATEGIES---': 'counter_strategies',
            '---RISK_WARNINGS---': 'risk_warnings',
            '---CONFIDENCE_LEVEL---': 'confidence_level',
            '---OFFER_LETTER---': 'offer_letter_draft'
        }
        
        current = None
        content = []
        
        for line in text.split('\n'):
            stripped = line.strip()
            if stripped in sections:
                if current:
                    self._save_section(strategy, current, content)
                current = sections[stripped]
                content = []
            elif current:
                content.append(line)
        
        if current:
            self._save_section(strategy, current, content)
        
        return strategy
    
    def _save_section(self, strategy: Dict, section: str, content: List[str]) -> None:
        """Save parsed section."""
        text = '\n'.join(content).strip()
        
        if section == 'leverage_points':
            strategy['leverage_points'] = self._parse_leverage(text)
        elif section == 'talking_points':
            strategy['talking_points'] = self._parse_list(text)
        elif section == 'counter_strategies':
            strategy['counter_strategies'] = self._parse_counters(text)
        elif section == 'risk_warnings':
            strategy['risk_warnings'] = self._parse_list(text)
        elif section == 'confidence_level':
            if 'HIGH' in text.upper():
                strategy['confidence_level'] = 'HIGH'
            elif 'LOW' in text.upper():
                strategy['confidence_level'] = 'LOW'
            else:
                strategy['confidence_level'] = 'MEDIUM'
            strategy['confidence_explanation'] = text
        else:
            strategy[section] = text
    
    def _parse_leverage(self, text: str) -> List[Dict]:
        """Parse leverage points."""
        points = []
        current = {}
        
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                if current.get('point'):
                    points.append(current)
                    current = {}
                continue
            
            lower = line.lower()
            if 'point:' in lower:
                if current.get('point'):
                    points.append(current)
                current = {'point': line.split(':', 1)[1].strip()}
            elif 'how to use' in lower and ':' in line:
                current['how_to_use'] = line.split(':', 1)[1].strip()
            elif ('value' in lower or 'potential' in lower) and ':' in line:
                current['potential_value'] = line.split(':', 1)[1].strip()
            elif line[0].isdigit() or line.startswith(('-', '•')):
                if current.get('point'):
                    points.append(current)
                current = {'point': line.lstrip('0123456789.-•) ').strip(), 'how_to_use': '', 'potential_value': ''}
        
        if current.get('point'):
            points.append(current)
        
        return points[:5]
    
    def _parse_counters(self, text: str) -> List[Dict]:
        """Parse counter strategies."""
        strategies = []
        current = {}
        
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                if 'objection' in current:
                    strategies.append(current)
                    current = {}
                continue
            
            lower = line.lower()
            if 'objection' in lower and ':' in line:
                if 'objection' in current:
                    strategies.append(current)
                current = {'objection': line.split(':', 1)[1].strip()}
            elif 'response' in lower and ':' in line:
                current['response'] = line.split(':', 1)[1].strip()
        
        if 'objection' in current:
            strategies.append(current)
        
        return strategies[:5]
    
    def _parse_list(self, text: str) -> List[str]:
        """Parse bulleted list."""
        items = []
        for line in text.split('\n'):
            clean = line.strip().lstrip('0123456789.-•*) ').strip()
            if clean:
                items.append(clean)
        return items[:7]
    
    # ========================================================================
    # DOCUMENT FORMATTING
    # ========================================================================
    
    def _format_offer_letter(
        self,
        analysis: Dict[str, Any],
        strategy: Dict[str, Any],
        buyer_profile: Optional[Dict]
    ) -> Dict[str, Any]:
        """Format offer letter."""
        
        # Use AI draft if available
        if strategy.get('offer_letter_draft'):
            return {
                'title': 'Offer Letter',
                'content': strategy['offer_letter_draft'],
                'type': 'offer_letter',
                'source': 'ai'
            }
        
        # Template fallback
        address = analysis.get('property_address', 'the property')
        asking = analysis.get('property_price', 0)
        offer = analysis.get('offer_strategy', {}).get('recommended_offer', asking)
        buyer = buyer_profile.get('name', 'Buyer') if buyer_profile else 'Buyer'
        
        issues = self._extract_issues(analysis)
        total = sum(i.get('estimated_cost', 0) or 0 for i in issues)
        
        letter = f"""Dear Seller,

We are pleased to submit an offer of ${offer:,} for {address}.

Our offer reflects genuine interest while accounting for documented repair needs:
"""
        
        for issue in issues[:3]:
            cost = issue.get('estimated_cost', 0)
            letter += f"• {issue['title']}" + (f" (est. ${cost:,})" if cost else "") + "\n"
        
        letter += f"""
Total estimated repairs: ${total:,}

We are pre-approved and ready to close within 30-45 days. We believe this offer represents fair market value.

Sincerely,
{buyer}
"""
        
        return {
            'title': 'Offer Letter',
            'content': letter.strip(),
            'type': 'offer_letter',
            'source': 'template'
        }
    
    def _format_talking_points(
        self,
        analysis: Dict[str, Any],
        strategy: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Format talking points."""
        
        address = analysis.get('property_address', 'Property')
        asking = analysis.get('property_price', 0)
        offer = analysis.get('offer_strategy', {}).get('recommended_offer', asking)
        
        content = f"""NEGOTIATION TALKING POINTS
{address}
Asking: ${asking:,} | Your Offer: ${offer:,}
{'=' * 50}

"""
        
        if strategy.get('talking_points'):
            content += "KEY POINTS:\n\n"
            for i, pt in enumerate(strategy['talking_points'], 1):
                content += f"{i}. {pt}\n\n"
        else:
            issues = self._extract_issues(analysis)
            content += "KEY LEVERAGE:\n\n"
            for i, issue in enumerate(issues[:5], 1):
                cost = issue.get('estimated_cost', 0)
                content += f"{i}. {issue['title']}"
                if cost:
                    content += f" - ${cost:,}"
                content += "\n\n"
        
        if strategy.get('counter_strategies'):
            content += "\n" + "=" * 50 + "\n\nIF THEY SAY... YOU SAY:\n\n"
            for cs in strategy['counter_strategies']:
                content += f"❌ \"{cs.get('objection', '')}\"\n"
                content += f"✅ \"{cs.get('response', '')}\"\n\n"
        
        return {
            'title': 'Talking Points',
            'content': content.strip(),
            'type': 'talking_points',
            'source': 'ai' if strategy.get('talking_points') else 'template'
        }
    
    def _format_agent_email(
        self,
        analysis: Dict[str, Any],
        strategy: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Format agent email."""
        
        address = analysis.get('property_address', 'Property')
        offer = analysis.get('offer_strategy', {}).get('recommended_offer', 0)
        issues = self._extract_issues(analysis)
        
        email = f"""Subject: Offer Submission - {address}

Hi [Agent Name],

Submitting an offer of ${offer:,} for {address}.

Professional inspection found:
"""
        for issue in issues[:3]:
            email += f"• {issue['title']}\n"
        
        email += """
Pre-approved, ready to close quickly. Let me know if questions.

Best,
[Your Name]
"""
        
        return {
            'title': 'Agent Email',
            'content': email.strip(),
            'type': 'agent_email',
            'source': 'template'
        }
    
    def _format_counteroffer(
        self,
        analysis: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Format counteroffer response."""
        
        original = context.get('original_offer', 0)
        counter = context.get('seller_counteroffer', 0)
        recommended = analysis.get('offer_strategy', {}).get('recommended_offer', 0)
        
        issues = self._extract_issues(analysis)
        total = sum(i.get('estimated_cost', 0) or 0 for i in issues)
        midpoint = (original + counter) / 2 if counter else original
        
        content = f"""COUNTEROFFER STRATEGY

Their Counter: ${counter:,}
Your Original: ${original:,}
Target: ${recommended:,}

{'=' * 50}

"""
        
        if counter <= recommended:
            content += "✅ ACCEPT - At or below target.\n"
        elif counter <= midpoint:
            content += f"🤝 CONSIDER - Counter at ${int(midpoint):,}\n"
        else:
            content += f"⚠️ COUNTER at ${int(midpoint):,}\n\nJustification:\n"
            for issue in issues[:3]:
                cost = issue.get('estimated_cost', 0)
                if cost:
                    content += f"• {issue['title']}: ${cost:,}\n"
        
        content += f"\nTotal repairs: ${total:,}"
        
        return {
            'title': 'Counteroffer Strategy',
            'content': content.strip(),
            'type': 'counteroffer',
            'source': 'template'
        }
    
    # ========================================================================
    # HELPERS
    # ========================================================================
    
    def _extract_issues(self, analysis: Dict[str, Any]) -> List[Dict]:
        """Extract issues from analysis."""
        issues = []
        
        for flag in analysis.get('red_flags', [])[:10]:
            issues.append({
                'title': flag.get('title', flag.get('issue', 'Issue')),
                'severity': flag.get('severity', 'medium'),
                'estimated_cost': flag.get('estimated_cost', flag.get('repair_cost', 0)),
            })
        
        if not issues:
            for issue in analysis.get('issues', [])[:10]:
                issues.append({
                    'title': issue.get('title', issue.get('description', 'Issue')),
                    'severity': issue.get('severity', 'medium'),
                    'estimated_cost': issue.get('repair_cost', 0),
                })
        
        # Sort by severity then cost
        order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        issues.sort(key=lambda x: (order.get(x.get('severity', 'medium'), 2), -(x.get('estimated_cost', 0) or 0)))
        
        return issues
    
    def _create_basic_strategy(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Create basic strategy without AI."""
        issues = self._extract_issues(analysis)
        
        return {
            'leverage_points': [
                {'point': i['title'], 'how_to_use': 'Reference in negotiations', 'potential_value': f"${i.get('estimated_cost', 0):,}" if i.get('estimated_cost') else 'Leverage'}
                for i in issues[:5]
            ],
            'talking_points': [
                "Professional inspection identified items requiring attention.",
                "Repair estimates based on local contractor costs.",
                "Offer reflects fair value given documented condition.",
                "Pre-approved and ready to close quickly."
            ],
            'recommended_approach': 'Lead with documented issues to justify your offer.',
            'opening_script': '',
            'counter_strategies': [],
            'risk_warnings': [],
            'confidence_level': 'MEDIUM',
            'confidence_explanation': 'Based on analysis without AI.',
            'offer_letter_draft': ''
        }
    
    def _generate_quick_tips(self, analysis: Dict[str, Any]) -> List[str]:
        """Generate instant tips."""
        tips = []
        
        # OfferScore = 100 - composite (higher = better quality)
        risk_dna = analysis.get('risk_dna', {})
        offer_score = round(100 - float(risk_dna.get('composite_score', 50) or 50))
        if offer_score <= 30:
            tips.append("🚨 High-risk property - significant leverage. Push for major concessions.")
        elif offer_score <= 60:
            tips.append("⚠️ Moderate issues found - use documented findings to justify below-asking offer.")
        else:
            tips.append("✅ Clean property - focus on market conditions and comparable sales.")
        
        risk_score = analysis.get('risk_score', {})
        repair_low = risk_score.get('total_repair_cost_low', 0) or 0
        repair_high = risk_score.get('total_repair_cost_high', 0) or 0
        repair_avg = round((repair_low + repair_high) / 2)
        if repair_avg > 20000:
            tips.append(f"💰 ${repair_avg:,} in estimated repairs - request as price reduction or credit.")
        elif repair_avg > 5000:
            tips.append(f"🔧 ${repair_avg:,} estimated repairs - supports your offer.")
        
        transparency = risk_dna.get('transparency_score', 0)
        if transparency > 60:
            tips.append("🔍 Disclosure gaps - request additional documentation.")
        
        tips.append("📋 Get your own independent inspection.")
        tips.append("⏰ Don't rush - negotiation saves thousands.")
        
        return tips[:6]


def get_negotiation_hub() -> NegotiationHub:
    """Get singleton instance."""
    if not hasattr(get_negotiation_hub, '_instance'):
        get_negotiation_hub._instance = NegotiationHub()
    return get_negotiation_hub._instance
