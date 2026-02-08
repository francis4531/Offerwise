"""
OfferWise Hybrid Cross-Reference Engine™
PATENT-PENDING: Novel combination of rule-based matching + AI-powered analysis

Innovation: Two-stage system that combines the speed and consistency of rules
with the intelligence and nuance of AI for superior accuracy.

Stage 1: Rule-based matching (fast, consistent baseline)
Stage 2: AI-powered analysis (intelligent interpretation)

Patent Claims:
1. Hybrid rule-based + AI cross-referencing methodology
2. Two-stage discrepancy detection system
3. AI-enhanced transparency scoring
4. Fallback architecture (rules if AI unavailable)

Version: 1.0.0
Status: Patent Pending
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import json
import logging
from anthropic import Anthropic

from cross_reference_engine import (
    CrossReferenceEngine, 
    CrossReferenceReport,
    CrossReferenceMatch
)
from document_parser import PropertyDocument

logger = logging.getLogger(__name__)


@dataclass
class AIEnhancedMatch:
    """Cross-reference match enhanced with AI analysis"""
    # Original rule-based match
    original_match: CrossReferenceMatch
    
    # AI enhancements
    ai_severity_assessment: str  # AI's take on how serious this is
    ai_explanation: str  # Natural language explanation
    ai_confidence: float  # 0-1, AI's confidence in this being a real issue
    deception_indicators: List[str]  # Specific red flags AI found
    buyer_impact: str  # What this means for the buyer
    recommended_action: str  # What buyer should do about it
    
    # Combined scoring
    final_severity: str  # Combines rules + AI assessment
    final_confidence: float  # Weighted combination


class HybridCrossReferenceEngine:
    """
    PATENT-PENDING: Hybrid rule-based + AI cross-referencing system
    
    Architecture:
    1. Rule-based engine identifies potential discrepancies (fast, consistent)
    2. AI analyzes each match for context and nuance (intelligent, explanatory)
    3. Combined output provides best of both worlds
    
    Benefits:
    - Faster than pure AI (rules do initial filtering)
    - Smarter than pure rules (AI catches subtle issues)
    - Fallback safety (if AI fails, rules still work)
    - Cost-efficient (AI only on flagged items, not entire documents)
    - Explainable (AI provides natural language reasoning)
    """
    
    def __init__(self, anthropic_api_key: Optional[str] = None, enable_ai: bool = True):
        """
        Initialize hybrid engine
        
        Args:
            anthropic_api_key: Claude API key (if None, AI disabled)
            enable_ai: Whether to use AI enhancement (can disable for testing)
        """
        # Stage 1: Rule-based engine
        self.rules_engine = CrossReferenceEngine()
        
        # Stage 2: AI engine
        self.enable_ai = enable_ai and anthropic_api_key is not None
        if self.enable_ai:
            self.ai_client = Anthropic(api_key=anthropic_api_key)
            logger.info("🤖 Hybrid engine initialized with AI enhancement enabled")
        else:
            self.ai_client = None
            logger.info("📋 Hybrid engine initialized in rules-only mode (AI disabled)")
    
    def cross_reference(
        self, 
        disclosure_doc: PropertyDocument, 
        inspection_doc: PropertyDocument
    ) -> CrossReferenceReport:
        """
        PATENT-PENDING: Hybrid two-stage cross-reference analysis
        
        Stage 1: Rules identify potential discrepancies
        Stage 2: AI analyzes each for context and severity
        
        Args:
            disclosure_doc: Parsed seller disclosure
            inspection_doc: Parsed inspection report
            
        Returns:
            CrossReferenceReport with AI-enhanced insights
        """
        
        logger.info("🔄 Starting hybrid cross-reference analysis...")
        
        # STAGE 1: Rule-based analysis (fast baseline)
        logger.info("📋 Stage 1: Rule-based matching...")
        rules_report = self.rules_engine.cross_reference(disclosure_doc, inspection_doc)
        
        logger.info(f"  ✅ Rules found: {len(rules_report.contradictions)} contradictions, "
                   f"{len(rules_report.undisclosed_issues)} undisclosed issues")
        
        # STAGE 2: AI enhancement (if enabled)
        if self.enable_ai and (rules_report.contradictions or rules_report.undisclosed_issues):
            logger.info("🤖 Stage 2: AI-powered analysis...")
            
            # Enhance contradictions with AI
            enhanced_contradictions = self._enhance_matches_with_ai(
                rules_report.contradictions,
                disclosure_doc,
                inspection_doc,
                match_type="contradiction"
            )
            
            # Enhance undisclosed issues with AI
            enhanced_undisclosed = self._enhance_matches_with_ai(
                rules_report.undisclosed_issues,
                disclosure_doc,
                inspection_doc,
                match_type="undisclosed"
            )
            
            # Recalculate transparency score with AI insights
            transparency_score = self._calculate_ai_enhanced_transparency(
                enhanced_contradictions,
                enhanced_undisclosed,
                rules_report
            )
            
            # Update report with AI enhancements
            rules_report.contradictions = enhanced_contradictions
            rules_report.undisclosed_issues = enhanced_undisclosed
            rules_report.transparency_score = transparency_score
            
            # Add AI-generated summary
            rules_report.summary = self._generate_ai_summary(
                enhanced_contradictions,
                enhanced_undisclosed,
                transparency_score
            )
            
            logger.info("  ✅ AI analysis complete - matches enhanced with context and reasoning")
        else:
            if not self.enable_ai:
                logger.info("  ⚠️ AI disabled - using rules-only analysis")
            else:
                logger.info("  ℹ️ No discrepancies found - AI analysis not needed")
        
        return rules_report
    
    def _enhance_matches_with_ai(
        self,
        matches: List[CrossReferenceMatch],
        disclosure_doc: PropertyDocument,
        inspection_doc: PropertyDocument,
        match_type: str
    ) -> List[CrossReferenceMatch]:
        """
        Enhance rule-based matches with AI analysis
        
        For each match found by rules:
        1. AI reads the actual text from both documents
        2. AI assesses severity and context
        3. AI explains WHY this matters to the buyer
        4. AI detects deception indicators (minimization, vagueness, etc.)
        """
        
        if not matches:
            return matches
        
        # PERFORMANCE OPTIMIZATION: Only analyze top 10 most important matches
        # This prevents huge prompts (30+ matches = 5000+ tokens = 10-15 seconds!)
        MAX_AI_MATCHES = 10
        
        if len(matches) > MAX_AI_MATCHES:
            # Sort by confidence (highest first) to focus on most significant issues
            sorted_matches = sorted(matches, key=lambda m: m.confidence, reverse=True)
            ai_matches = sorted_matches[:MAX_AI_MATCHES]
            non_ai_matches = sorted_matches[MAX_AI_MATCHES:]
            
            logger.info(f"⚡ OPTIMIZATION: Analyzing top {MAX_AI_MATCHES} of {len(matches)} {match_type}s with AI")
            logger.info(f"   Remaining {len(non_ai_matches)} will use rule-based analysis only")
        else:
            ai_matches = matches
            non_ai_matches = []
        
        # Prepare batch of matches for AI
        matches_summary = []
        for i, match in enumerate(ai_matches):
            # Build disclosure text
            disclosure_text = "N/A"
            if match.disclosure_item:
                disclosure_text = match.disclosure_item.question
                if match.disclosure_item.details:
                    disclosure_text += f" - {match.disclosure_item.details}"
            
            matches_summary.append({
                'id': i,
                'type': match_type,
                'disclosure': disclosure_text,
                'inspection': match.inspection_finding.description if match.inspection_finding else "N/A",
                'category': match.disclosure_item.category if match.disclosure_item else 
                           match.inspection_finding.category.value if match.inspection_finding else "unknown",
                'rules_confidence': match.confidence
            })
        
        # Call AI to analyze all matches at once (efficient batching)
        prompt = self._build_ai_analysis_prompt(matches_summary, match_type)
        
        try:
            response = self.ai_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4000,
                temperature=0,  # Deterministic: same inputs = same outputs
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Parse AI response
            ai_analysis = self._parse_ai_response(response.content[0].text)
            
            # Enhance AI-analyzed matches with AI insights
            for match, analysis in zip(ai_matches, ai_analysis):
                match.explanation = analysis.get('explanation', match.explanation)
                match.confidence = self._combine_confidences(
                    match.confidence,
                    analysis.get('ai_confidence', 0.5)
                )
                # Store AI metadata
                match.ai_metadata = {
                    'severity_assessment': analysis.get('severity', 'unknown'),
                    'deception_indicators': analysis.get('deception_indicators', []),
                    'buyer_impact': analysis.get('buyer_impact', ''),
                    'recommended_action': analysis.get('recommended_action', '')
                }
            
            # Add default AI metadata to non-AI-analyzed matches
            for match in non_ai_matches:
                match.ai_metadata = {
                    'severity_assessment': 'unknown',
                    'deception_indicators': [],
                    'buyer_impact': 'Not analyzed (performance optimization)',
                    'recommended_action': 'review_carefully'
                }
            
            # Return all matches (AI-enhanced + rule-based)
            return ai_matches + non_ai_matches
            
        except Exception as e:
            logger.error(f"AI enhancement failed: {e}")
            logger.warning("Falling back to rules-only analysis")
            return matches  # Return original matches if AI fails
    
    def _build_ai_analysis_prompt(self, matches: List[Dict], match_type: str) -> str:
        """
        Build prompt for AI to analyze rule-based matches
        """
        
        if match_type == "contradiction":
            context = "Analyze CONTRADICTIONS between seller disclosure and inspector findings."
        else:  # undisclosed
            context = "Analyze UNDISCLOSED ISSUES the seller didn't mention."
        
        prompt = f"""{context}

MATCHES:
{json.dumps(matches, indent=2)}

For EACH match return:
- severity: "critical|major|moderate|minor" (critical=$10K+/safety, major=$5K+, moderate=$1K-5K, minor=<$1K)
- explanation: 2-3 sentences about impact
- ai_confidence: 0.0-1.0
- deception_indicators: ["minimization", "vague_language", "omission", "timing_suspicious", "contradicts_prior"]
- buyer_impact: One sentence cost/impact
- recommended_action: "request_credit|renegotiate_price|specialist_inspection|repair_before_close|walk_away|monitor_closely"

Return ONLY JSON array (no markdown):
[{{"id": 0, "severity": "major", "explanation": "...", "ai_confidence": 0.85, "deception_indicators": [...], "buyer_impact": "...", "recommended_action": "..."}}]"""
        
        return prompt
    
    def _parse_ai_response(self, response_text: str) -> List[Dict]:
        """Parse AI response, handling markdown wrappers"""
        try:
            # Remove markdown code fences if present
            cleaned = response_text.strip()
            if cleaned.startswith('```'):
                # Remove ```json and ``` wrappers
                cleaned = cleaned.split('```')[1]
                if cleaned.startswith('json'):
                    cleaned = cleaned[4:]
                cleaned = cleaned.strip()
            
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response: {e}")
            logger.debug(f"Response was: {response_text[:500]}")
            return []
    
    def _combine_confidences(self, rules_confidence: float, ai_confidence: float) -> float:
        """
        Combine rule-based and AI confidence scores
        
        Uses weighted average favoring AI (AI is more nuanced):
        - Rules: 30% weight (good for obvious patterns)
        - AI: 70% weight (better for context and edge cases)
        """
        return (rules_confidence * 0.3) + (ai_confidence * 0.7)
    
    def _calculate_ai_enhanced_transparency(
        self,
        contradictions: List[CrossReferenceMatch],
        undisclosed: List[CrossReferenceMatch],
        original_report: CrossReferenceReport
    ) -> float:
        """
        Calculate transparency score with AI severity weighting
        
        Original: Simple count-based scoring
        Enhanced: Weighs by AI-assessed severity
        """
        
        # Start with base score
        base_score = original_report.transparency_score
        
        # Apply AI severity penalties
        severity_penalties = {
            'critical': 20,
            'major': 15,
            'moderate': 10,
            'minor': 5
        }
        
        total_penalty = 0
        for match in contradictions + undisclosed:
            if hasattr(match, 'ai_metadata') and match.ai_metadata:
                severity = match.ai_metadata.get('severity_assessment', 'moderate')
                penalty = severity_penalties.get(severity, 10)
                total_penalty += penalty
        
        # Apply penalties with floor at 0
        adjusted_score = max(0, base_score - total_penalty)
        
        return adjusted_score
    
    def _generate_ai_summary(
        self,
        contradictions: List[CrossReferenceMatch],
        undisclosed: List[CrossReferenceMatch],
        transparency_score: float
    ) -> str:
        """
        Generate natural language summary of transparency assessment
        """
        
        try:
            # Build context for AI
            # Top 3 most significant issues only
            top_issues = (contradictions + undisclosed)[:3]
            
            summary_prompt = f"""Generate a 2-3 sentence summary of this property's seller transparency.

Transparency Score: {transparency_score}/100

Contradictions Found: {len(contradictions)}
Undisclosed Issues Found: {len(undisclosed)}

Key Issues:
{json.dumps([
    {
        'type': 'contradiction' if m in contradictions else 'undisclosed',
        'explanation': m.explanation,
        'severity': m.ai_metadata.get('severity_assessment') if hasattr(m, 'ai_metadata') and m.ai_metadata else 'unknown'
    }
    for m in top_issues
], indent=2)}

Write a concise, professional summary explaining:
1. Overall transparency level (excellent/good/concerning/poor)
2. Most significant issue(s)
3. What this means for the buyer's trust in the seller

Tone: Professional but direct. Don't sugarcoat serious issues.
Length: 2-3 sentences max."""
            
            response = self.ai_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=200,
                temperature=0,
                messages=[{"role": "user", "content": summary_prompt}]
            )
            
            return response.content[0].text.strip()
            
        except Exception as e:
            logger.error(f"Failed to generate AI summary: {e}")
            # Fallback to simple template
            if transparency_score >= 80:
                return f"Seller transparency is excellent ({transparency_score}/100). Disclosures align well with inspection findings."
            elif transparency_score >= 60:
                return f"Seller transparency is acceptable ({transparency_score}/100) with some minor discrepancies noted."
            else:
                return f"Seller transparency is concerning ({transparency_score}/100). Multiple significant discrepancies identified."


# Export main class
__all__ = ['HybridCrossReferenceEngine']
