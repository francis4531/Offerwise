"""
OfferWise Hybrid AI Intelligence Layer
=======================================
Enhances the rule-based analysis engine with LLM intelligence.
Does NOT replace existing parsers — runs alongside them and merges results.

Architecture:
  1. Rule-based parser runs first (fast, reliable, deterministic)
  2. LLM layer runs in parallel or after (deeper understanding, catches edge cases)
  3. Merger combines both, preferring rules where confident, LLM where rules missed

Three capabilities:
  A. LLM Document Parser — reads inspection reports with semantic understanding
  B. LLM Disclosure Contradiction Detector — finds semantic contradictions
  C. LLM Repair Cost Contextualizer — adjusts cost estimates based on finding context
"""

import json
import logging
import os
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class LLMFinding:
    """A finding extracted by the LLM parser."""
    category: str           # foundation, roof, plumbing, electrical, hvac, environmental, etc.
    severity: str           # critical, major, moderate, minor
    description: str        # Human-readable description
    location: str           # Where in the property
    raw_excerpt: str        # The original text from the document
    safety_concern: bool = False
    requires_specialist: bool = False
    confidence: float = 0.9


@dataclass
class LLMContradiction:
    """A contradiction between disclosure and inspection found by LLM."""
    disclosure_claim: str    # What the seller said
    inspection_finding: str  # What the inspector found
    category: str            # Which system/area
    severity: str            # How serious the contradiction is
    explanation: str         # Why this matters
    confidence: float = 0.9


@dataclass
class LLMCostContext:
    """Contextual cost adjustment from the LLM."""
    category: str
    original_severity: str
    adjusted_severity: str   # May stay the same
    context_notes: str       # Why the adjustment
    cost_multiplier: float = 1.0  # 0.5 = half the baseline, 2.0 = double
    confidence: float = 0.85


# ─── LLM Client Wrapper ──────────────────────────────────────────────────────

def _call_llm(prompt: str, system: str = "", max_tokens: int = 4000) -> Optional[str]:
    """Call the LLM (Anthropic Claude, with OpenAI fallback)."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        logger.warning("No ANTHROPIC_API_KEY — LLM layer disabled")
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=system or "You are a property analysis expert. Respond only in valid JSON.",
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e:
        logger.warning(f"Anthropic LLM call failed: {e}")
        # Fallback to OpenAI if available
        oai_key = os.environ.get('OPENAI_API_KEY')
        if oai_key:
            try:
                import openai
                client = openai.OpenAI(api_key=oai_key)
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system or "You are a property analysis expert. Respond only in valid JSON."},
                        {"role": "user", "content": prompt},
                    ]
                )
                return resp.choices[0].message.content
            except Exception as e2:
                logger.warning(f"OpenAI fallback also failed: {e2}")
        return None


def _parse_json_response(text: str) -> Optional[dict]:
    """Safely parse JSON from LLM response, stripping markdown fences and preamble."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]  # skip first line
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    # Extract JSON object if there's preamble text
    if not cleaned.startswith('{') and not cleaned.startswith('['):
        idx = cleaned.find('{')
        if idx >= 0:
            cleaned = cleaned[idx:]
        else:
            logger.warning(f"LLM response has no JSON object: {cleaned[:100]}...")
            return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try extracting just the first complete JSON object
        brace = 0
        for i, ch in enumerate(cleaned):
            if ch == '{': brace += 1
            elif ch == '}': brace -= 1
            if brace == 0 and i > 0:
                try:
                    return json.loads(cleaned[:i+1])
                except json.JSONDecodeError:
                    break
        logger.warning(f"LLM JSON parse failed: {cleaned[:120]}...")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# A. LLM DOCUMENT PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def llm_parse_inspection_report(text: str, max_chars: int = 12000) -> List[LLMFinding]:
    """
    Use LLM to extract findings from an inspection report.
    Understands semantic meaning, not just keyword patterns.

    This catches:
    - Findings phrased as recommendations ("recommend further evaluation")
    - Findings buried in positive language ("overall good condition except...")
    - Inspector-specific jargon and abbreviations
    - Photo descriptions that indicate issues
    - Multi-sentence findings that span paragraphs
    """
    # Truncate to avoid token limits but keep start and end (most important)
    if len(text) > max_chars:
        half = max_chars // 2
        text = text[:half] + "\n\n[...middle section truncated...]\n\n" + text[-half:]

    prompt = f"""Analyze this home inspection report text. Extract ONLY actual problems, defects, safety concerns, and items needing repair or further evaluation.

Do NOT include:
- Positive observations ("in good condition", "functioning properly")
- General descriptions of systems
- Informational notes that aren't problems
- Maintenance recommendations that aren't defects

For each finding, provide:
- category: one of [foundation, roof, plumbing, electrical, hvac, water_damage, pest, environmental, safety, permits, general]
- severity: one of [critical, major, moderate, minor]
  critical = structural failure, active safety hazard, immediate action needed
  major = significant defect, will worsen, costly repair
  moderate = needs repair but not urgent, standard maintenance item
  minor = cosmetic, low priority, monitoring sufficient
- description: clear one-sentence description of the problem
- location: where in the property (if stated)
- raw_excerpt: the exact text from the report (max 100 chars)
- safety_concern: true if this is a life/safety issue
- requires_specialist: true if the inspector recommends further evaluation

Respond ONLY with a JSON object: {{"findings": [...]}}

INSPECTION REPORT TEXT:
{text}"""

    response = _call_llm(prompt)
    data = _parse_json_response(response)
    if not data or 'findings' not in data:
        return []

    results = []
    for f in data['findings']:
        try:
            results.append(LLMFinding(
                category=f.get('category', 'general'),
                severity=f.get('severity', 'moderate'),
                description=f.get('description', ''),
                location=f.get('location', ''),
                raw_excerpt=f.get('raw_excerpt', '')[:200],
                safety_concern=bool(f.get('safety_concern', False)),
                requires_specialist=bool(f.get('requires_specialist', False)),
                confidence=0.85,
            ))
        except Exception:
            continue

    logger.info(f"LLM parser found {len(results)} findings")
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# B. LLM DISCLOSURE CONTRADICTION DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

def llm_detect_contradictions(
    disclosure_text: str,
    inspection_text: str,
    max_chars: int = 8000
) -> List[LLMContradiction]:
    """
    Use LLM to find semantic contradictions between disclosure and inspection.

    This catches contradictions that string matching misses:
    - "Roof replaced 2018" vs "original composition shingles, 15-20 years old"
    - "No water damage" vs "evidence of moisture intrusion at crawlspace"
    - "All systems functional" vs "HVAC compressor failed to engage"
    - "No knowledge of foundation issues" vs "step cracks in foundation wall"
    """
    # Truncate both documents
    if len(disclosure_text) > max_chars:
        disclosure_text = disclosure_text[:max_chars]
    if len(inspection_text) > max_chars:
        inspection_text = inspection_text[:max_chars]

    prompt = f"""You are analyzing a real estate transaction. Compare these two documents and identify CONTRADICTIONS — places where the seller's disclosure says one thing but the inspection report says something different.

Focus on:
1. Direct contradictions (seller says no problem, inspector found a problem)
2. Omissions (seller didn't mention something the inspector flagged as significant)
3. Minimizations (seller acknowledged an issue but understated its severity)
4. Timeline inconsistencies (seller's dates don't match the condition observed)

For each contradiction, provide:
- disclosure_claim: what the seller said or didn't say (quote or paraphrase)
- inspection_finding: what the inspector found (quote or paraphrase)
- category: one of [foundation, roof, plumbing, electrical, hvac, water_damage, pest, environmental, permits, general]
- severity: high (could affect transaction), medium (negotiation leverage), low (minor discrepancy)
- explanation: one sentence explaining why this matters to the buyer

Respond ONLY with JSON: {{"contradictions": [...]}}

If there are no contradictions, return {{"contradictions": []}}

SELLER DISCLOSURE:
{disclosure_text}

INSPECTION REPORT:
{inspection_text}"""

    response = _call_llm(prompt)
    data = _parse_json_response(response)
    if not data or 'contradictions' not in data:
        return []

    results = []
    for c in data['contradictions']:
        try:
            results.append(LLMContradiction(
                disclosure_claim=c.get('disclosure_claim', ''),
                inspection_finding=c.get('inspection_finding', ''),
                category=c.get('category', 'general'),
                severity=c.get('severity', 'medium'),
                explanation=c.get('explanation', ''),
                confidence=0.85,
            ))
        except Exception:
            continue

    logger.info(f"LLM contradiction detector found {len(results)} contradictions")
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# C. LLM REPAIR COST CONTEXTUALIZER
# ═══════════════════════════════════════════════════════════════════════════════

def llm_contextualize_costs(
    findings: List[Dict],
    zip_code: str = '',
    property_year_built: int = None
) -> List[LLMCostContext]:
    """
    Use LLM to add context to repair cost estimates.

    The rule-based estimator maps "foundation, moderate" to a fixed range.
    The LLM reads the actual finding description and adjusts:
    - "Hairline crack in poured concrete foundation" → lower end of moderate
    - "Horizontal crack with 1/4 inch displacement in block foundation" → upper end, possibly major
    - "Efflorescence on foundation wall" → moisture indicator, moderate but check further
    """
    if not findings:
        return []

    # Build a concise summary of findings for the LLM
    finding_summaries = []
    for i, f in enumerate(findings[:20]):  # Cap at 20 to manage token usage
        finding_summaries.append({
            'id': i,
            'category': f.get('category', f.get('system', 'general')),
            'severity': f.get('severity', 'moderate'),
            'description': f.get('description', f.get('finding', ''))[:200],
        })

    context_info = f"ZIP code: {zip_code}" if zip_code else "ZIP unknown"
    if property_year_built:
        context_info += f", built {property_year_built}"

    prompt = f"""You are a residential construction cost estimator. For each inspection finding below, assess whether the rule-based severity classification is accurate based on the actual description.

Property context: {context_info}

For each finding, provide:
- id: the finding id (integer)
- original_severity: what it was classified as
- adjusted_severity: what it should be (critical/major/moderate/minor) — keep the same if the classification is correct
- cost_multiplier: a float between 0.5 and 2.0 adjusting the baseline cost range
  1.0 = baseline is accurate
  0.5 = baseline overstates (e.g., cosmetic crack classified as moderate)
  1.5 = baseline understates (e.g., description indicates worse condition than severity suggests)
  2.0 = significantly understated (e.g., "moderate" finding that actually needs major repair)
- context_notes: brief explanation of adjustment (or "accurate" if no change)

Respond ONLY with JSON: {{"adjustments": [...]}}

FINDINGS:
{json.dumps(finding_summaries, indent=2)}"""

    response = _call_llm(prompt, max_tokens=2000)
    data = _parse_json_response(response)
    if not data or 'adjustments' not in data:
        return []

    results = []
    for a in data['adjustments']:
        try:
            mult = float(a.get('cost_multiplier', 1.0))
            # Clamp multiplier to reasonable range
            mult = max(0.4, min(2.5, mult))
            results.append(LLMCostContext(
                category=finding_summaries[a['id']]['category'] if a.get('id', -1) < len(finding_summaries) else 'general',
                original_severity=a.get('original_severity', 'moderate'),
                adjusted_severity=a.get('adjusted_severity', a.get('original_severity', 'moderate')),
                context_notes=a.get('context_notes', ''),
                cost_multiplier=mult,
                confidence=0.8,
            ))
        except Exception:
            continue

    logger.info(f"LLM cost contextualizer returned {len(results)} adjustments")
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# MERGER — Combine rule-based and LLM results
# ═══════════════════════════════════════════════════════════════════════════════

def merge_findings(
    rule_findings: List[Dict],
    llm_findings: List[LLMFinding],
    overlap_threshold: float = 0.4
) -> List[Dict]:
    """
    Merge rule-based and LLM findings. Strategy:
    1. Keep all rule-based findings (they're fast and deterministic)
    2. For each LLM finding, check if it overlaps with a rule finding
       - If yes: enrich the rule finding with LLM confidence and details
       - If no: add as a new finding tagged as LLM-discovered
    3. Flag findings that only LLM caught (rules missed)
    """
    merged = list(rule_findings)  # start with all rule findings

    for llm_f in llm_findings:
        # Check overlap with existing rule findings
        best_overlap = 0.0
        best_idx = -1
        llm_words = set(llm_f.description.lower().split())

        for i, rf in enumerate(merged):
            rf_desc = rf.get('description', rf.get('finding', '')).lower()
            rf_words = set(rf_desc.split())
            if not rf_words or not llm_words:
                continue
            overlap = len(llm_words & rf_words) / max(len(llm_words), len(rf_words))
            # Also check category match
            rf_cat = rf.get('category', rf.get('system', '')).lower()
            if llm_f.category.lower() in rf_cat or rf_cat in llm_f.category.lower():
                overlap += 0.2  # boost for category match
            if overlap > best_overlap:
                best_overlap = overlap
                best_idx = i

        if best_overlap >= overlap_threshold and best_idx >= 0:
            # Enrich existing finding
            merged[best_idx]['llm_verified'] = True
            merged[best_idx]['llm_confidence'] = llm_f.confidence
            if llm_f.safety_concern:
                merged[best_idx]['safety_concern'] = True
            if llm_f.requires_specialist:
                merged[best_idx]['requires_specialist'] = True
            # If LLM thinks severity is higher, flag it
            sev_rank = {'minor': 0, 'moderate': 1, 'major': 2, 'critical': 3}
            llm_sev = sev_rank.get(llm_f.severity, 1)
            rule_sev = sev_rank.get(merged[best_idx].get('severity', 'moderate'), 1)
            if llm_sev > rule_sev:
                merged[best_idx]['llm_severity_upgrade'] = llm_f.severity
                merged[best_idx]['llm_severity_note'] = f"LLM suggests {llm_f.severity} (was {merged[best_idx].get('severity')})"
        else:
            # New finding that rules missed
            merged.append({
                'category': llm_f.category,
                'severity': llm_f.severity,
                'description': llm_f.description,
                'location': llm_f.location,
                'raw_text': llm_f.raw_excerpt,
                'safety_concern': llm_f.safety_concern,
                'requires_specialist': llm_f.requires_specialist,
                'source': 'llm',
                'llm_discovered': True,
                'llm_confidence': llm_f.confidence,
            })

    # Count stats
    rule_count = len(rule_findings)
    llm_new = sum(1 for f in merged if f.get('llm_discovered'))
    llm_verified = sum(1 for f in merged if f.get('llm_verified'))
    logger.info(f"Merged: {rule_count} rule + {llm_new} LLM-new + {llm_verified} LLM-verified = {len(merged)} total")

    return merged


def apply_cost_context(
    cost_breakdown: List[Dict],
    cost_contexts: List[LLMCostContext]
) -> List[Dict]:
    """
    Apply LLM cost context adjustments to the rule-based cost breakdown.
    Adjusts the cost ranges using the LLM's multiplier while keeping
    the rule-based structure intact.
    """
    if not cost_contexts:
        return cost_breakdown

    # Build lookup by category
    context_map = {}
    for ctx in cost_contexts:
        context_map[ctx.category.lower()] = ctx

    adjusted = []
    for item in cost_breakdown:
        item = dict(item)  # don't mutate original
        cat = item.get('system', item.get('category', '')).lower()

        # Normalize category for matching
        cat_normalized = cat.replace(' ', '_').replace('/', '_')
        ctx = context_map.get(cat_normalized) or context_map.get(cat)

        if ctx and ctx.cost_multiplier != 1.0:
            mult = ctx.cost_multiplier
            if 'low' in item:
                item['low'] = round(item['low'] * mult)
            if 'high' in item:
                item['high'] = round(item['high'] * mult)
            if 'avg' in item:
                item['avg'] = round(item['avg'] * mult)
            item['llm_adjusted'] = True
            item['llm_multiplier'] = mult
            item['llm_context'] = ctx.context_notes
            if ctx.adjusted_severity != ctx.original_severity:
                item['llm_severity_change'] = f"{ctx.original_severity} → {ctx.adjusted_severity}"

        adjusted.append(item)

    return adjusted


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN HYBRID ANALYSIS FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def enhance_analysis(
    inspection_text: str,
    disclosure_text: str = '',
    rule_findings: List[Dict] = None,
    rule_cost_breakdown: List[Dict] = None,
    zip_code: str = '',
    property_year_built: int = None,
) -> Dict:
    """
    Run the LLM enhancement layer on top of rule-based analysis.

    Call this AFTER the rule-based analysis completes.
    Returns enrichment data that can be merged into the existing analysis.

    Returns:
        {
            'llm_findings': [...],          # Additional/verified findings
            'llm_contradictions': [...],     # Semantic contradictions
            'llm_cost_adjustments': [...],   # Cost context adjustments
            'merged_findings': [...],        # Rule + LLM combined
            'adjusted_costs': [...],         # Costs with LLM context
            'stats': {
                'rule_findings': N,
                'llm_new_findings': N,
                'llm_verified': N,
                'contradictions_found': N,
                'costs_adjusted': N,
            }
        }
    """
    result = {
        'llm_findings': [],
        'llm_contradictions': [],
        'llm_cost_adjustments': [],
        'merged_findings': rule_findings or [],
        'adjusted_costs': rule_cost_breakdown or [],
        'stats': {},
    }

    # Check if LLM is available
    if not os.environ.get('ANTHROPIC_API_KEY') and not os.environ.get('OPENAI_API_KEY'):
        logger.info("No LLM API key — hybrid layer skipped")
        result['stats'] = {'llm_available': False}
        return result

    try:
        # A. LLM Document Parsing
        if inspection_text:
            llm_findings = llm_parse_inspection_report(inspection_text)
            result['llm_findings'] = [vars(f) for f in llm_findings]

            if rule_findings is not None:
                result['merged_findings'] = merge_findings(rule_findings, llm_findings)

        # B. Disclosure Contradiction Detection
        if disclosure_text and inspection_text:
            contradictions = llm_detect_contradictions(disclosure_text, inspection_text)
            result['llm_contradictions'] = [vars(c) for c in contradictions]

        # C. Cost Contextualization
        findings_for_cost = result['merged_findings'] or rule_findings or []
        if findings_for_cost:
            cost_contexts = llm_contextualize_costs(
                findings_for_cost, zip_code, property_year_built
            )
            result['llm_cost_adjustments'] = [vars(c) for c in cost_contexts]

            if rule_cost_breakdown:
                result['adjusted_costs'] = apply_cost_context(
                    rule_cost_breakdown, cost_contexts
                )

        # Stats
        llm_new = sum(1 for f in result['merged_findings'] if f.get('llm_discovered'))
        llm_verified = sum(1 for f in result['merged_findings'] if f.get('llm_verified'))
        costs_adjusted = sum(1 for c in result['adjusted_costs'] if c.get('llm_adjusted'))

        result['stats'] = {
            'llm_available': True,
            'rule_findings': len(rule_findings or []),
            'llm_new_findings': llm_new,
            'llm_verified': llm_verified,
            'total_findings': len(result['merged_findings']),
            'contradictions_found': len(result['llm_contradictions']),
            'costs_adjusted': costs_adjusted,
        }

        logger.info(f"Hybrid analysis complete: {result['stats']}")

    except Exception as e:
        logger.error(f"Hybrid AI layer error: {e}", exc_info=True)
        result['stats'] = {'llm_available': True, 'error': str(e)}

    return result
