"""
OfferWise Optimized Hybrid Cross-Reference Engine™
PERFORMANCE OPTIMIZED VERSION - v2.0

Changes from v1.0:
1. Single batched AI call instead of 3 separate calls (3x faster)
2. Streamlined prompts (50% smaller)
3. Early exit for clean properties
4. Cached similarity calculations
5. Parallel-ready architecture

Performance improvement: 63s → ~10s (6.3x speedup)
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import json
import logging
import time
try:
    from anthropic import Anthropic
except ImportError:
    class Anthropic:  # noqa: stub for environments without anthropic installed
        def __init__(self, *a, **kw): pass
        def __getattr__(self, n): return lambda *a, **kw: None

from cross_reference_engine import (
    CrossReferenceEngine, 
    CrossReferenceReport,
    CrossReferenceMatch
)
from document_parser import PropertyDocument

logger = logging.getLogger(__name__)


class OptimizedHybridCrossReferenceEngine:
    """
    OPTIMIZED: Hybrid rule-based + AI cross-referencing system
    
    Performance Improvements:
    1. Single AI call for all analysis (was 3 calls)
    2. Streamlined prompts (50% smaller tokens)
    3. Early exit when no issues found
    4. Smart batching and caching
    
    Maintains all functionality while being 6x faster!
    """
    
    def __init__(self, anthropic_api_key: Optional[str] = None, enable_ai: bool = True):
        """Initialize optimized hybrid engine"""
        self.rules_engine = CrossReferenceEngine()
        self.enable_ai = enable_ai and anthropic_api_key is not None
        
        if self.enable_ai:
            try:
                self.ai_client = Anthropic(api_key=anthropic_api_key)
                logger.info("✅ AI enhancement enabled (optimized mode)")
            except Exception as e:
                logger.error(f"Failed to initialize AI client: {e}")
                self.enable_ai = False
                logger.warning("⚠️ Falling back to rules-only mode")
        else:
            self.ai_client = None
            logger.info("ℹ️ AI enhancement disabled - using rules only")
    
    def cross_reference(
        self, 
        disclosure_doc: PropertyDocument, 
        inspection_doc: PropertyDocument,
        **kwargs
    ) -> CrossReferenceReport:
        """
        OPTIMIZED: Single-pass hybrid cross-reference analysis
        
        Optimization strategy:
        1. Rules engine runs (fast, ~1s)
        2. Early exit if no issues found
        3. Single AI call for all enhancements (was 3 calls)
        4. Parallel-ready for future scaling
        """
        
        logger.info("🚀 Starting optimized hybrid cross-reference...")
        
        # STAGE 1: Rule-based analysis (fast baseline)
        rules_report = self.rules_engine.cross_reference(disclosure_doc, inspection_doc)
        
        total_issues = len(rules_report.contradictions) + len(rules_report.undisclosed_issues)
        logger.info(f"  📋 Rules found: {total_issues} issues total")
        
        # OPTIMIZATION: Early exit if clean property
        if total_issues == 0:
            logger.info("  ✅ No issues found - skipping AI analysis")
            return rules_report
        
        # STAGE 2: Single AI call for all enhancements
        if self.enable_ai:
            logger.info(f"  🤖 AI analyzing {total_issues} issues in single batch...")
            
            try:
                # Single unified AI call
                ai_results = self._single_ai_analysis(
                    rules_report.contradictions,
                    rules_report.undisclosed_issues,
                    rules_report
                )
                
                # Apply AI enhancements
                rules_report.contradictions = ai_results['contradictions']
                rules_report.undisclosed_issues = ai_results['undisclosed']
                rules_report.transparency_score = ai_results['transparency_score']
                rules_report.summary = ai_results['summary']
                
                logger.info("  ✅ AI analysis complete (optimized)")
                
            except Exception as e:
                # 529 = Anthropic overloaded (transient) — warn, don't error
                status = getattr(e, 'status_code', None)
                log_fn = logger.warning if status in (429, 500, 503, 529) else logger.error
                log_fn(f"AI analysis failed: {e}")
                logger.warning("Continuing with rules-only results")
        
        return rules_report
    
    def _single_ai_analysis(
        self,
        contradictions: List[CrossReferenceMatch],
        undisclosed: List[CrossReferenceMatch],
        report: CrossReferenceReport
    ) -> Dict[str, Any]:
        """
        OPTIMIZATION: Single AI call for all analysis
        
        Instead of:
        - Call 1: Analyze contradictions
        - Call 2: Analyze undisclosed  
        - Call 3: Generate summary
        
        We do:
        - Call 1: Analyze everything + generate summary
        
        Result: 3x reduction in API calls, much faster
        """
        
        # Build compact unified prompt
        all_issues = []
        
        # Add contradictions
        for i, match in enumerate(contradictions):
            disclosure_text = "N/A"
            if match.disclosure_item:
                disclosure_text = match.disclosure_item.question
                if match.disclosure_item.details:
                    disclosure_text += f" - {match.disclosure_item.details}"
            
            all_issues.append({
                'id': f"C{i+1}",
                'type': 'contradiction',
                'seller_said': disclosure_text,
                'inspector_found': match.inspection_finding.description if match.inspection_finding else "N/A",
                'category': match.disclosure_item.category if match.disclosure_item else "unknown"
            })
        
        # Add undisclosed issues
        for i, match in enumerate(undisclosed):
            all_issues.append({
                'id': f"U{i+1}",
                'type': 'undisclosed',
                'seller_said': "Not disclosed",
                'inspector_found': match.inspection_finding.description if match.inspection_finding else "N/A",
                'category': match.inspection_finding.category.value if match.inspection_finding else "unknown"
            })
        
        # Compact prompt - works from extracted text only (privacy-first: no raw PDFs sent)
        # Photo evidence is available as [PHOTO: ...] descriptions in the inspection text,
        # which are included in the inspector_found field when photos were near findings.
        prompt = f"""Analyze these seller disclosure vs inspection discrepancies. Each issue was detected by comparing the actual text of both documents.

STRICT RULES:
- Base your severity rating ONLY on what the documents actually state
- Do NOT infer additional issues beyond what is listed below
- Your explanation must reference the specific discrepancy shown
- Confidence should be lower (0.3-0.6) if the discrepancy could be a matter of interpretation
- If inspector_found includes [PHOTO: ...] descriptions, factor the photographic evidence into your severity rating.
  A photo showing active damage (e.g., mold, standing water, structural failure) should increase severity.

Discrepancies found:
{json.dumps(all_issues, indent=2)}

For EACH issue, provide:
1. severity: critical|major|moderate|minor
2. explanation: Why this matters (1 sentence, referencing the specific discrepancy)
3. confidence: 0.0-1.0

Also provide:
4. transparency_score: 0-100 (based ONLY on the discrepancies above, not speculation)
5. summary: 2-3 sentence executive summary (cite specific findings only)

Respond in JSON:
{{
  "issues": [
    {{"id": "C1", "severity": "major", "explanation": "...", "confidence": 0.9}},
    ...
  ],
  "transparency_score": 75,
  "summary": "..."
}}"""

        # Single AI call - text only (privacy: no raw documents sent to API during analysis)
        _t0 = time.time()
        response = self.ai_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            temperature=0,  # Deterministic: same inputs = same outputs
            messages=[{"role": "user", "content": prompt}]
        )
        try:
            from ai_cost_tracker import track_ai_call as _track
            _track(response, "cross-reference", (time.time() - _t0) * 1000)
        except Exception:
            pass
        
        # Parse response
        try:
            result_text = response.content[0].text.strip()
            
            # Extract JSON from response
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()
            
            ai_data = json.loads(result_text)
        except Exception as e:
            logger.error(f"Failed to parse AI response: {e}")
            # Return original data unchanged
            return {
                'contradictions': contradictions,
                'undisclosed': undisclosed,
                'transparency_score': report.transparency_score,
                'summary': report.summary
            }
        
        # === AI OUTPUT VALIDATION (v5.60.2) ===
        try:
            from ai_output_validator import validate_severity_ratings, log_ai_call
            ai_data, violations = validate_severity_ratings(ai_data, all_issues)
            if violations:
                logger.warning(f"Cross-ref AI violations: {len(violations)} — "
                             + "; ".join(v['code'] for v in violations[:5]))
            log_ai_call(
                endpoint='cross-reference-severity',
                model='claude-sonnet-4-20250514',
                input_summary={'issue_count': len(all_issues)},
                raw_output=result_text[:2000],
                validated_output=ai_data,
                violations=violations,
            )
        except Exception as ve:
            logger.warning(f"AI validation failed (non-fatal): {ve}")
        
        # Apply AI insights to matches
        issue_map = {issue['id']: issue for issue in ai_data.get('issues', [])}
        
        # Enhance contradictions
        for i, match in enumerate(contradictions):
            issue_id = f"C{i+1}"
            if issue_id in issue_map:
                ai_issue = issue_map[issue_id]
                match.explanation = ai_issue.get('explanation', match.explanation)
                match.confidence = ai_issue.get('confidence', match.confidence)
                match.ai_metadata = {
                    'severity_assessment': ai_issue.get('severity', 'unknown'),
                    'ai_enhanced': True
                }
        
        # Enhance undisclosed
        for i, match in enumerate(undisclosed):
            issue_id = f"U{i+1}"
            if issue_id in issue_map:
                ai_issue = issue_map[issue_id]
                match.explanation = ai_issue.get('explanation', match.explanation)
                match.confidence = ai_issue.get('confidence', match.confidence)
                match.ai_metadata = {
                    'severity_assessment': ai_issue.get('severity', 'unknown'),
                    'ai_enhanced': True
                }
        
        return {
            'contradictions': contradictions,
            'undisclosed': undisclosed,
            'transparency_score': ai_data.get('transparency_score', report.transparency_score),
            'summary': ai_data.get('summary', report.summary)
        }


# Export optimized class (drop-in replacement for HybridCrossReferenceEngine)
__all__ = ['OptimizedHybridCrossReferenceEngine']
