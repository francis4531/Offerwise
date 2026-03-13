"""
OfferWise AI Analysis Helper
Adds intelligence and verification to rule-based analysis
Version: 1.0.0
"""

import anthropic
import json
import os
import re
import logging
import time
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class AnalysisAIHelper:
    """
    Enhances existing analysis with AI-powered verification
    and confidence scoring
    """
    
    def __init__(self):
        # DEBUG: Log environment check
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        
        logger.info("=" * 80)
        logger.info("🔍 AI HELPER INITIALIZATION DEBUG")
        logger.info(f"Environment variables present: {list(os.environ.keys())}")
        logger.info(f"ANTHROPIC_API_KEY exists: {api_key is not None}")
        if api_key:
            logger.info(f"ANTHROPIC_API_KEY configured: True, length: {len(api_key)}")
        else:
            logger.info("ANTHROPIC_API_KEY is None or empty")
        logger.info("=" * 80)
        
        if not api_key:
            logger.warning("⚠️ ANTHROPIC_API_KEY not set - AI features disabled")
            self.client = None
            self.enabled = False
        else:
            # Try multiple initialization methods for different anthropic versions
            self.client = None
            self.enabled = False
            
            logger.info("Attempting to initialize Anthropic client...")
            
            # Method 1: Try anthropic.Anthropic (newer versions)
            try:
                logger.info("Method 1: Trying anthropic.Anthropic()...")
                self.client = anthropic.Anthropic(api_key=api_key)
                self.enabled = True
                logger.info("✅ Anthropic API initialized (Anthropic class)")
            except (TypeError, AttributeError) as e:
                logger.warning(f"⚠️ Anthropic class failed: {e}")
                logger.warning(f"⚠️ Error type: {type(e).__name__}")
                
                # Method 2: Try anthropic.Client (some versions)
                try:
                    logger.info("Method 2: Trying anthropic.Client()...")
                    self.client = anthropic.Client(api_key=api_key)
                    self.enabled = True
                    logger.info("✅ Anthropic API initialized (Client class)")
                except (TypeError, AttributeError) as e2:
                    logger.warning(f"⚠️ Client class failed: {e2}")
                    logger.warning(f"⚠️ Error type: {type(e2).__name__}")
                    
                    # Method 3: Disable AI features gracefully
                    logger.error("❌ Could not initialize Anthropic API - AI features disabled")
                    logger.error(f"Error details: {e2}")
                    logger.error(f"Error type: {type(e2).__name__}")
                    import traceback
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    self.client = None
                    self.enabled = False
            except Exception as e:
                # Catch ANY other exception
                logger.error(f"❌ Unexpected error initializing Anthropic: {e}")
                logger.error(f"Error type: {type(e).__name__}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                self.client = None
                self.enabled = False
            
            logger.info(f"Final AI status: enabled={self.enabled}, client={self.client is not None}")
            logger.info("=" * 80)
    
    def fix_ocr_errors(self, text: str, max_length: int = 2000) -> str:
        """
        Fix common OCR errors in text
        
        Args:
            text: Raw OCR text
            max_length: Maximum characters to process (OPTIMIZED: 2000 for speed)
            
        Returns:
            Cleaned text
        """
        if not self.enabled or not text:
            return text
        
        # Quick pre-fix for common patterns (free, fast)
        text = self._quick_fix_common_errors(text)
        
        # If text is clean enough, skip AI
        quality = self._ocr_quality_score(text)
        if quality > 0.95:
            logger.info(f"✅ OCR quality high ({quality:.1%}), skipping AI fix")
            return text
        
        # Use AI for complex fixes
        try:
            # PERFORMANCE OPTIMIZATION: Process only first chunk to save time
            # Most critical info is usually in first 2000 chars
            if len(text) > max_length:
                logger.info(f"⚡ OPTIMIZATION: Processing first {max_length} chars of {len(text)} (saves API time)")
                text_to_fix = text[:max_length]
                remaining_text = text[max_length:]
            else:
                text_to_fix = text
                remaining_text = ""
            
            logger.info(f"🔧 Fixing OCR errors (quality: {quality:.1%})...")
            
            prompt = f"""Fix OCR errors in this real estate document. Fix character substitutions (0→O, 5→S, 1→l) and spacing. Preserve all meaning. Output fixed text only, no explanations.

TEXT:
{text_to_fix}"""
            
            _t0 = time.time()
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=len(text_to_fix) + 500,
                messages=[{"role": "user", "content": prompt}]
            )
            try:
                try:
                    from app import app as _ow_app, db as _ow_db
                except Exception:
                    _ow_app, _ow_db = None, None
                from ai_cost_tracker import track_ai_call as _track
                _track(response, "cross-reference-external", (time.time() - _t0) * 1000, db=_ow_db, app=_ow_app)
            except Exception:
                pass
            
            fixed_text = response.content[0].text
            
            # Combine fixed + remaining
            if remaining_text:
                full_fixed_text = fixed_text + remaining_text
                logger.info(f"✅ OCR fix complete: {len(text)} → {len(full_fixed_text)} chars (first {max_length} fixed)")
                return full_fixed_text
            else:
                logger.info(f"✅ OCR fix complete: {len(text)} → {len(fixed_text)} chars")
                return fixed_text
            
        except Exception as e:
            logger.error(f"❌ OCR fix failed: {e}")
            return text  # Return original if AI fails
    
    def _quick_fix_common_errors(self, text: str) -> str:
        """Fast fix for common OCR mistakes (no API call needed)"""
        
        # Common real estate term fixes
        fixes = {
            r'\bR00F\b': 'ROOF',
            r'\bF0UNDATION\b': 'FOUNDATION',
            r'\bG00D\b': 'GOOD',
            r'\bFA55\b': 'PASS',
            r'\bEXC3LLENT\b': 'EXCELLENT',
            r'\bC0NDITION\b': 'CONDITION',
            r'\bREPA1R\b': 'REPAIR',
            r'\bWA7ER\b': 'WATER',
            r'\bE1ECTRICAL\b': 'ELECTRICAL',
            r'\b1NSPECTION\b': 'INSPECTION',
            r'\bFA1L\b': 'FAIL',
            r'\bMA10R\b': 'MAJOR',
            r'\bM1NOR\b': 'MINOR',
        }
        
        for pattern, replacement in fixes.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        
        return text
    
    def _ocr_quality_score(self, text: str) -> float:
        """
        Estimate OCR quality (0.0 to 1.0)
        Higher = better quality = less fixing needed
        """
        if not text:
            return 0.0
        
        # Count suspicious patterns
        suspicious = 0
        
        # Pattern 1: Numbers where letters should be
        suspicious += len(re.findall(r'[A-Z][0-9][A-Z]', text))
        
        # Pattern 2: Excessive special characters
        special_chars = len(re.findall(r'[^a-zA-Z0-9\s.,!?\-()]', text))
        suspicious += special_chars / 10
        
        # Pattern 3: Very short "words" (often OCR errors)
        words = text.split()
        if words:
            short_words = sum(1 for w in words if len(w) == 1 and w.isalpha())
            suspicious += short_words / 5
        
        # Convert to quality score
        quality = max(0.0, 1.0 - (suspicious / max(len(text), 1) * 100))
        return min(1.0, quality)
    
    def verify_finding_against_source(
        self,
        finding_description: str,
        source_text: str,
        max_source_length: int = 1500
    ) -> Dict[str, Any]:
        """
        Verify a finding is actually supported by source document
        
        Args:
            finding_description: The finding to verify
            source_text: Source document text
            max_source_length: Max chars to send to API (OPTIMIZED: 1500 for speed)
            
        Returns:
            {
                'supported': True|False,
                'confidence': 0.0-1.0,
                'evidence': ['quote 1', 'quote 2'],
                'verdict': 'supported|unsupported|uncertain'
            }
        """
        if not self.enabled:
            # If no API, return neutral result
            return {
                'supported': True,
                'confidence': 0.5,
                'evidence': [],
                'verdict': 'uncertain',
                'note': 'AI verification disabled'
            }
        
        if not finding_description or not source_text:
            return {
                'supported': False,
                'confidence': 0.0,
                'evidence': [],
                'verdict': 'uncertain'
            }
        
        try:
            # Extract relevant section from source
            relevant_section = self._extract_relevant_section(
                source_text, 
                finding_description, 
                max_source_length
            )
            
            logger.info(f"🔍 Verifying: {finding_description[:80]}...")
            
            prompt = f"""Verify if this finding is supported by the source document.

FINDING: "{finding_description}"

SOURCE:
{relevant_section}

Determine: "supported" (clear evidence), "unsupported" (contradicts or not mentioned), or "uncertain" (unclear).

Return ONLY JSON:
{{
    "verdict": "supported|unsupported|uncertain",
    "confidence": 0.0-1.0,
    "evidence_quotes": ["quote 1", "quote 2"],
    "explanation": "brief reason"
}}"""
            
            _t0 = time.time()
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            try:
                try:
                    from app import app as _ow_app, db as _ow_db
                except Exception:
                    _ow_app, _ow_db = None, None
                from ai_cost_tracker import track_ai_call as _track
                _track(response, "document-analysis", (time.time() - _t0) * 1000, db=_ow_db, app=_ow_app)
            except Exception:
                pass
            
            result = json.loads(response.content[0].text)
            
            logger.info(f"✅ Verification: {result['verdict']} ({result['confidence']:.0%})")
            
            return {
                'supported': result['verdict'] == 'supported',
                'confidence': result['confidence'],
                'evidence': result.get('evidence_quotes', []),
                'verdict': result['verdict'],
                'explanation': result.get('explanation', '')
            }
            
        except Exception as e:
            logger.error(f"❌ Fact-check failed: {e}")
            return {
                'supported': True,  # Fail safe - assume supported if check fails
                'confidence': 0.5,
                'evidence': [],
                'verdict': 'uncertain',
                'error': str(e)
            }
    
    def _extract_relevant_section(
        self, 
        full_text: str, 
        finding_text: str, 
        max_length: int
    ) -> str:
        """Extract section of text most relevant to finding"""
        
        if len(full_text) <= max_length:
            return full_text
        
        # Try to find finding text in source
        finding_lower = finding_text.lower()
        text_lower = full_text.lower()
        
        # Search for key terms from finding
        key_terms = [w for w in finding_lower.split() 
                     if len(w) > 4 and w not in ['that', 'this', 'with', 'from', 'have', 'been']]
        
        if not key_terms:
            # No good terms, return first chunk
            return full_text[:max_length]
        
        best_pos = 0
        best_score = 0
        
        # Find position with most key terms nearby
        for i in range(0, len(text_lower), 100):
            chunk = text_lower[i:i+1000]
            score = sum(1 for term in key_terms if term in chunk)
            if score > best_score:
                best_score = score
                best_pos = i
        
        # Extract section around best position
        start = max(0, best_pos - max_length // 2)
        end = min(len(full_text), best_pos + max_length // 2)
        
        return full_text[start:end]
    
    def calculate_confidence_score(
        self,
        finding: Dict[str, Any],
        ocr_quality: float,
        verification: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Calculate overall confidence in this finding
        
        Args:
            finding: The finding dict (with description, location, etc.)
            ocr_quality: OCR quality score (0.0-1.0)
            verification: Verification result from fact-check
            
        Returns:
            Confidence score 0.0 to 1.0
        """
        confidence = 1.0
        
        # Factor 1: OCR quality (30% weight)
        confidence *= (0.7 + (ocr_quality * 0.3))
        
        # Factor 2: Specificity (20% weight)
        description = finding.get('description', '')
        vague_terms = ['some', 'appears', 'possible', 'may', 'might', 'could', 'unclear']
        vague_count = sum(1 for term in vague_terms if term in description.lower())
        specificity = max(0.5, 1.0 - (vague_count * 0.15))
        confidence *= (0.8 + (specificity * 0.2))
        
        # Factor 3: Completeness (20% weight)
        completeness = 1.0
        if not finding.get('location'):
            completeness *= 0.85
        if not finding.get('severity'):
            completeness *= 0.90
        if not finding.get('estimated_cost_low'):
            completeness *= 0.95
        confidence *= (0.8 + (completeness * 0.2))
        
        # Factor 4: Verification result (30% weight) - MOST IMPORTANT
        if verification:
            if verification['verdict'] == 'supported':
                verification_score = verification['confidence']
            elif verification['verdict'] == 'unsupported':
                verification_score = 0.3  # Low confidence if unsupported
            else:  # uncertain
                verification_score = 0.6
            confidence *= (0.7 + (verification_score * 0.3))
        
        return max(0.0, min(1.0, confidence))
    
    def generate_confidence_explanation(
        self,
        confidence: float,
        finding: Dict[str, Any],
        verification: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate human-readable explanation of confidence score"""
        
        if confidence >= 0.85:
            return "HIGH CONFIDENCE: Clear, specific finding with supporting evidence"
        elif confidence >= 0.65:
            reasons = []
            if verification and verification.get('verdict') == 'uncertain':
                reasons.append("some uncertainty in source")
            if not finding.get('location'):
                reasons.append("location not specified")
            
            if reasons:
                return f"MEDIUM CONFIDENCE: Finding appears valid but {' and '.join(reasons)}"
            else:
                return "MEDIUM CONFIDENCE: Finding appears valid but some uncertainty remains"
        else:
            reasons = []
            description = finding.get('description', '')
            
            if verification and verification.get('verdict') == 'unsupported':
                reasons.append("not clearly supported by inspection report")
            elif 'appears' in description.lower() or 'possible' in description.lower():
                reasons.append("description uses uncertain language")
            if not finding.get('location'):
                reasons.append("location not specified")
            if not finding.get('estimated_cost_low'):
                reasons.append("cost estimate unavailable")
            
            reason_text = ", ".join(reasons) if reasons else "limited information"
            return f"LOW CONFIDENCE: {reason_text}. Recommend specialist inspection."

    def cross_reference_against_public_records(
        self,
        research_profile: dict,
        seller_disclosure_text: str,
        inspection_report_text: str,
        property_price: float
    ) -> list:
        """
        🤖 AI-Powered External Verification (PATENT-WORTHY)
        
        Uses Claude to reason about contradictions between:
        - What the seller claimed in their disclosure
        - What government records show (FEMA, USGS, Census, CAL FIRE, EPA)
        - What the inspector found
        
        This replaces keyword matching with actual intelligence — Claude understands
        negation, context, intent, and can catch subtle omissions.
        
        Returns:
            List of findings, each with type, severity, title, detail, source, category
        """
        if not self.enabled:
            logger.warning("🤖 AI not available for external verification — skipping")
            return []
        
        if not research_profile:
            return []
        
        try:
            # Build a concise research summary for the prompt
            research_summary_parts = []
            
            if research_profile.get('flood_zone'):
                risk = research_profile.get('flood_risk_level', 'unknown')
                research_summary_parts.append(f"FEMA FLOOD ZONE: {research_profile['flood_zone']} ({risk} risk)")
            
            if research_profile.get('earthquake_zone') is not None:
                in_zone = "YES — property is in a California Earthquake Fault Zone" if research_profile['earthquake_zone'] else "No"
                research_summary_parts.append(f"EARTHQUAKE FAULT ZONE (CGS): {in_zone}")
            
            quakes = research_profile.get('recent_earthquakes', [])
            if quakes:
                max_q = max(quakes, key=lambda q: q.get('magnitude', 0))
                research_summary_parts.append(f"USGS EARTHQUAKES: {len(quakes)} earthquakes M3.0+ within 50km, largest M{max_q.get('magnitude', '?')}")
            
            if research_profile.get('fire_hazard_zone'):
                research_summary_parts.append(f"CAL FIRE THREAT LEVEL: {research_profile['fire_hazard_zone']}")
            
            disasters = research_profile.get('disaster_declarations', [])
            if disasters:
                county = research_profile.get('county', 'the area')
                types = {}
                for d in disasters:
                    t = d.get('type', d.get('title', 'Other'))
                    types[t] = types.get(t, 0) + 1
                type_str = ', '.join(f"{v} {k}" for k, v in types.items())
                research_summary_parts.append(f"FEMA DISASTER HISTORY: {len(disasters)} declarations for {county} County ({type_str})")
            
            if research_profile.get('zip_median_home_value'):
                research_summary_parts.append(f"CENSUS MEDIAN HOME VALUE (ZIP): ${research_profile['zip_median_home_value']:,.0f}")
            if research_profile.get('zip_median_income'):
                research_summary_parts.append(f"CENSUS MEDIAN INCOME (ZIP): ${research_profile['zip_median_income']:,.0f}")
            
            if research_profile.get('air_quality_index'):
                research_summary_parts.append(f"EPA AIR QUALITY: AQI {research_profile['air_quality_index']} ({research_profile.get('air_quality_category', 'unknown')})")
            
            if research_profile.get('year_built'):
                research_summary_parts.append(f"PROPERTY YEAR BUILT: {research_profile['year_built']}")
            if research_profile.get('estimated_value'):
                research_summary_parts.append(f"ESTIMATED VALUE (AVM): ${research_profile['estimated_value']:,.0f}")
            if research_profile.get('tax_assessed_value'):
                research_summary_parts.append(f"TAX ASSESSED VALUE: ${research_profile['tax_assessed_value']:,.0f}")
            if research_profile.get('walk_score'):
                research_summary_parts.append(f"WALK SCORE: {research_profile['walk_score']}/100")
            
            if not research_summary_parts:
                return []
            
            research_summary = "\n".join(research_summary_parts)
            
            # Truncate documents to key sections (keep prompt efficient)
            max_doc_chars = 3000
            disclosure_excerpt = seller_disclosure_text[:max_doc_chars] if seller_disclosure_text else "(No disclosure provided)"
            inspection_excerpt = inspection_report_text[:max_doc_chars] if inspection_report_text else "(No inspection provided)"
            
            prompt = f"""You are an expert real estate analyst cross-referencing a seller's disclosure against independent government records.

ASKING PRICE: ${property_price:,.0f}

=== GOVERNMENT & PUBLIC RECORDS ===
{research_summary}

=== SELLER'S DISCLOSURE (excerpt) ===
{disclosure_excerpt}

=== INSPECTION REPORT (excerpt) ===
{inspection_excerpt}

TASK: Identify where the seller's disclosure contradicts, omits, or is inconsistent with the government records above. Focus on material findings that would affect a buyer's decision.

For each finding, assess:
1. Did the seller explicitly deny something that government data contradicts?
2. Did the seller omit material information that government records reveal?
3. Does the asking price make sense relative to area data?
4. Are there environmental or hazard risks the seller didn't address?

Return ONLY a JSON array. Each item:
{{
  "type": "contradiction|omission|context",
  "severity": "high|medium|info",
  "title": "Brief headline (under 80 chars)",
  "detail": "2-3 sentence explanation of what the government data shows vs what the seller said or didn't say. Be specific.",
  "source": "The government data source (e.g., FEMA NFHL, USGS, US Census ACS, CAL FIRE, EPA AirNow)",
  "category": "water_damage|structural|environmental|financial|safety"
}}

Rules:
- Only flag MATERIAL issues. Don't flag minor or speculative items.
- "contradiction" = seller explicitly said something that government data disproves
- "omission" = seller failed to mention something material that government data reveals
- "context" = government data provides useful context (not necessarily seller's fault)
- If the seller's disclosure is honest and consistent with records, return an empty array []
- Maximum 5 findings. Quality over quantity.
- Be precise about what the seller said vs what the data shows."""

            logger.info(f"🤖 AI external verification: sending {len(prompt)} char prompt")
            
            _t0 = time.time()
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )
            try:
                try:
                    from app import app as _ow_app, db as _ow_db
                except Exception:
                    _ow_app, _ow_db = None, None
                from ai_cost_tracker import track_ai_call as _track
                _track(response, "external-verification", (time.time() - _t0) * 1000, db=_ow_db, app=_ow_app)
            except Exception:
                pass
            
            raw_text = response.content[0].text.strip()
            
            # Parse JSON — handle markdown code fences
            if raw_text.startswith('```'):
                raw_text = raw_text.split('\n', 1)[1] if '\n' in raw_text else raw_text[3:]
                if raw_text.endswith('```'):
                    raw_text = raw_text[:-3].strip()
            
            findings = json.loads(raw_text)
            
            if not isinstance(findings, list):
                logger.warning(f"🤖 AI returned non-list: {type(findings)}")
                return []
            
            # === AI OUTPUT VALIDATION (v5.60.2) ===
            try:
                from ai_output_validator import validate_cross_reference_findings, log_ai_call
                findings, violations = validate_cross_reference_findings(
                    findings,
                    disclosure_text=seller_disclosure_text,
                    inspection_text=inspection_report_text,
                )
                if violations:
                    logger.warning(f"🤖 AI external verification violations: {len(violations)} — "
                                 + "; ".join(v['code'] for v in violations[:5]))
                log_ai_call(
                    endpoint='external-verification',
                    model='claude-sonnet-4-20250514',
                    input_summary={'research_keys': list(research_profile.keys()),
                                   'disclosure_len': len(seller_disclosure_text) if seller_disclosure_text else 0},
                    raw_output=raw_text[:2000],
                    validated_output=findings,
                    violations=violations,
                )
            except Exception as ve:
                logger.warning(f"🤖 AI validation failed (non-fatal): {ve}")
            
            # Validate and clean findings
            valid_findings = []
            for f in findings[:5]:  # Cap at 5
                if not isinstance(f, dict):
                    continue
                if not f.get('title') or not f.get('detail'):
                    continue
                # Ensure required fields
                valid_findings.append({
                    'type': f.get('type', 'context'),
                    'severity': f.get('severity', 'info') if f.get('severity') in ('high', 'medium', 'info') else 'info',
                    'title': str(f.get('title', ''))[:120],
                    'detail': str(f.get('detail', ''))[:500],
                    'source': str(f.get('source', 'Public Records'))[:80],
                    'category': f.get('category', 'environmental'),
                    'ai_generated': True  # Flag for transparency
                })
            
            logger.info(f"🤖 AI external verification complete: {len(valid_findings)} findings")
            for vf in valid_findings:
                icon = '🔴' if vf['severity'] == 'high' else '🟡' if vf['severity'] == 'medium' else 'ℹ️'
                logger.info(f"   {icon} [{vf['type']}] {vf['title']}")
            
            return valid_findings
            
        except json.JSONDecodeError as e:
            logger.error(f"🤖 AI external verification JSON parse error: {e}")
            logger.error(f"   Raw response: {raw_text[:200]}...")
            return []
        except Exception as e:
            logger.error(f"🤖 AI external verification failed: {e}")
            return []
