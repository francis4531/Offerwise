"""
permit_lookup.py — v5.87.82

Per-jurisdiction permit requirement lookups for repair line items.

Architecture:
  1. Batched Claude call. One Claude API call per analysis covers ALL repair
     line items, not one call per item. Reduces latency from O(n) to O(1) and
     cuts cost roughly proportionally.
  2. 90-day cache. Each unique (repair_category, jurisdiction) tuple is cached
     in the permit_cache table. Cache hits return zero-cost in <1ms. Cache
     misses fire the LLM call, persist the result, then return.
  3. Confidence-aware. Each finding includes a confidence level
     (high/medium/low). Low-confidence findings include "verify locally"
     guidance with the building department phone number when available.
  4. Honest defaults. When the model expresses uncertainty about a permit's
     necessity, we render that uncertainty rather than fabricating a definitive
     answer. Better to say "verify locally" than to confidently guide a buyer
     into the wrong action.

Public API:
    lookup_permits(repair_breakdown, jurisdiction) -> dict

    repair_breakdown is the same shape as risk_score.category_scores (list of
    {category, estimated_cost_low, estimated_cost_high, ...}).

    jurisdiction is a dict:
        {city: 'San Jose', county: 'Santa Clara', state: 'CA', zip: '95126'}

    Returns:
        {
          'findings': [
              {
                'system': 'HVAC · Furnace',
                'permit_required': 'required' | 'likely' | 'uncertain' | 'not_required',
                'confidence': 'high' | 'medium' | 'low',
                'permit_cost_low': 320,
                'permit_cost_high': 480,
                'permit_cost_currency': 'USD',
                'consequences': 'CO safety inspection bypassed; gas line work...',
                'verify_locally': null | {phone: '(408) 535-3555', dept: '...'},
              },
              ...
          ],
          'total_low': 1150,
          'total_high': 1760,
          'jurisdiction_label': 'San Jose, CA · Santa Clara County',
          'cache_hits': 3,
          'cache_misses': 2,
        }
"""
from __future__ import annotations
from model_config import HAIKU, SONNET
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Cache window. Permit rules can change, but rarely faster than this.
CACHE_DAYS = int(os.environ.get('PERMIT_CACHE_DAYS', '90'))

# Claude config. Use Haiku for cost; permit lookup is structured Q&A,
# not creative work.
PERMIT_MODEL = os.environ.get('PERMIT_LOOKUP_MODEL', HAIKU)

# v5.87.83: Fee web-search enrichment. When enabled, findings that are
# permit_required='required' or 'likely' get a follow-up web search for
# the actual jurisdiction fee schedule. Adds latency (~5-10s per finding
# enriched) but produces citable fee numbers instead of LLM estimates.
# Default OFF so the first deploy of v5.87.83 keeps the latency profile
# of v5.87.82 unchanged. Flip via env var when ready.
FEE_WEB_SEARCH_ENABLED = os.environ.get(
    'PERMIT_FEE_WEB_SEARCH', 'false'
).strip().lower() in ('1', 'true', 'yes', 'on')

# Model for the fee enrichment call. Sonnet is more reliable for structured
# extraction from search results; Haiku sometimes fabricates. Override-able.
FEE_SEARCH_MODEL = os.environ.get('PERMIT_FEE_SEARCH_MODEL', SONNET)

# Imported at module level so tests can patch permit_lookup.get_anthropic_client.
# In production this is a thin wrapper that returns an Anthropic client (or None
# if not configured). In tests, patching this isolates us from the real network.
try:
    from ai_client import get_anthropic_client
except ImportError:
    def get_anthropic_client():  # type: ignore
        return None


# ── Public API ───────────────────────────────────────────────────────────

def lookup_permits(
    repair_breakdown: list[dict],
    jurisdiction: dict,
) -> dict[str, Any]:
    """Look up permit requirements for all repair line items.

    Combines cache lookups with a single batched Claude call for misses.
    """
    if not repair_breakdown:
        return _empty_result(jurisdiction)

    juris_label = _format_jurisdiction(jurisdiction)
    juris_key = _jurisdiction_key(jurisdiction)

    # Phase 1: cache lookup for every repair category
    cache_hits, cache_misses = _split_by_cache(repair_breakdown, juris_key)

    findings: list[dict] = list(cache_hits)  # cached findings go in directly

    # Phase 2: single batched Claude call for the misses (if any)
    if cache_misses:
        llm_findings: list[dict] = []
        try:
            llm_findings = _llm_batch_lookup(cache_misses, jurisdiction, juris_label)
            # Persist each new finding to the cache for next time
            for f in llm_findings:
                try:
                    _cache_set(juris_key, f['system_key'], f)
                except Exception as e:
                    logger.warning('permit_lookup: cache set failed for %s: %s',
                                   f.get('system_key'), e)
        except Exception as e:
            logger.warning('permit_lookup: LLM batch failed: %s', e)

        if llm_findings:
            findings.extend(llm_findings)
        else:
            # Either the LLM call failed or returned empty (no client, no
            # response, parse error, etc). Degrade gracefully: emit "uncertain"
            # findings for the misses so the UI still renders something.
            for item in cache_misses:
                findings.append(_uncertain_fallback(item, juris_label))

    # Sort findings to match input order so the UI render matches
    # the order of repair categories shown in the breakdown.
    order_index = {_system_key(item): idx for idx, item in enumerate(repair_breakdown)}
    findings.sort(key=lambda f: order_index.get(f.get('system_key', ''), 999))

    # v5.87.83: Optionally enrich findings with web-searched fees.
    # Gated by PERMIT_FEE_WEB_SEARCH env var. Default off.
    if FEE_WEB_SEARCH_ENABLED:
        enriched: list[dict] = []
        for f in findings:
            # Only enrich findings that don't already have a citation
            # (cache hits may have one from a prior enrichment).
            if f.get('fee_source_url') is None and f.get('permit_required') in ('required', 'likely'):
                enriched_f = _enrich_finding_with_web_fee(f, jurisdiction, juris_label)
                # Re-cache the enriched finding so subsequent lookups skip
                # the web search and use the cited fee directly
                if enriched_f.get('fee_source_url'):
                    try:
                        _cache_set(juris_key, enriched_f['system_key'], enriched_f)
                    except Exception as e:
                        logger.debug('permit_lookup: re-cache after enrich failed: %s', e)
                enriched.append(enriched_f)
            else:
                enriched.append(f)
        findings = enriched

    # Compute totals across required/likely findings only
    total_low = sum(f.get('permit_cost_low', 0) or 0
                    for f in findings
                    if f.get('permit_required') in ('required', 'likely'))
    total_high = sum(f.get('permit_cost_high', 0) or 0
                     for f in findings
                     if f.get('permit_required') in ('required', 'likely'))

    return {
        'findings': findings,
        'total_low': total_low,
        'total_high': total_high,
        'jurisdiction_label': juris_label,
        'cache_hits': len(cache_hits),
        'cache_misses': len(cache_misses),
    }


# ── Cache layer ──────────────────────────────────────────────────────────

def _split_by_cache(
    repair_breakdown: list[dict],
    juris_key: str,
) -> tuple[list[dict], list[dict]]:
    """For each repair, check the cache. Return (hits, misses)."""
    hits, misses = [], []
    for item in repair_breakdown:
        sys_key = _system_key(item)
        cached = _cache_get(juris_key, sys_key)
        if cached:
            hits.append(cached)
        else:
            misses.append(item)
    return hits, misses


def _cache_get(juris_key: str, system_key: str) -> dict | None:
    """Read a cached permit finding. Returns None if missing or expired."""
    try:
        from models import db, PermitCache  # type: ignore
    except ImportError:
        return None

    try:
        row = (PermitCache.query
               .filter_by(jurisdiction_key=juris_key, system_key=system_key)
               .first())
        if not row:
            return None
        # TTL check
        cutoff = datetime.utcnow() - timedelta(days=CACHE_DAYS)
        if row.created_at < cutoff:
            return None
        return json.loads(row.payload_json)
    except Exception as e:
        logger.debug('permit_lookup cache_get error: %s', e)
        return None


def _cache_set(juris_key: str, system_key: str, finding: dict) -> None:
    """Persist a permit finding to the cache. Upsert by (juris, system)."""
    try:
        from models import db, PermitCache  # type: ignore
    except ImportError:
        return

    try:
        existing = (PermitCache.query
                    .filter_by(jurisdiction_key=juris_key, system_key=system_key)
                    .first())
        payload = json.dumps(finding)
        if existing:
            existing.payload_json = payload
            existing.created_at = datetime.utcnow()
        else:
            row = PermitCache(
                jurisdiction_key=juris_key,
                system_key=system_key,
                payload_json=payload,
                created_at=datetime.utcnow(),
            )
            db.session.add(row)
        db.session.commit()
    except Exception as e:
        logger.warning('permit_lookup cache_set error: %s', e)


# ── LLM call (batched) ───────────────────────────────────────────────────

_PERMIT_PROMPT = """You are a building-permit reference for U.S. real estate transactions. \
Given a list of repair categories and a property jurisdiction, return permit \
requirements for each.

JURISDICTION: {jurisdiction}

REPAIR CATEGORIES (use the exact `system_key` value in your response):
{repair_list}

For each repair, respond with a JSON object having these fields:

  - system_key: string. The exact system_key from the input list.
  - system: string. The display label (matches the input system label).
  - permit_required: one of "required", "likely", "uncertain", "not_required".
      "required": permit is definitively required by code in this jurisdiction.
      "likely": permit usually required, but may depend on scope (e.g. like-for-like
        replacement may be exempt).
      "uncertain": jurisdiction-specific rules unclear; needs local verification.
      "not_required": clearly exempt (cosmetic, minor maintenance, etc).
  - confidence: one of "high", "medium", "low". High = code is widely standardized.
      Medium = scope-dependent. Low = jurisdiction varies and you are not certain.
  - permit_cost_low: integer. Estimated low end of permit fee in USD. Null if not_required or uncertain.
  - permit_cost_high: integer. Estimated high end. Null if not_required or uncertain.
  - consequences: short string. If permit_required is "required" or "likely",
      describe the concrete consequences of skipping (insurance, resale, code
      enforcement). Concrete and specific. If "not_required", a brief
      affirmative ("Routine maintenance is exempt"). If "uncertain", explain
      the ambiguity.
  - verify_locally_dept: string or null. If permit_required is "uncertain" or
      "low" confidence, the building department name to call. Null otherwise.

Respond with ONLY a JSON array. No preamble, no markdown fences. The array \
must have exactly one entry per input repair, in input order.

Honesty requirements:
- If you don't know a specific local rule, say "uncertain" with "low" confidence.
  Never fabricate a permit fee or rule.
- Permit fee ranges should reflect the jurisdiction. Default to broad 2x ranges
  (e.g. $200-400) when local fee schedules aren't known.
- Cosmetic work, routine maintenance, and minor repairs to existing systems
  are exempt in essentially all U.S. jurisdictions; mark these "not_required"
  with "high" confidence.
"""


def _llm_batch_lookup(
    cache_misses: list[dict],
    jurisdiction: dict,
    juris_label: str,
) -> list[dict]:
    """Single Claude call for all cache misses. Returns parsed findings."""
    repair_list_lines = []
    for item in cache_misses:
        sys_key = _system_key(item)
        sys_label = item.get('category') or item.get('system') or sys_key
        if isinstance(sys_label, dict):
            sys_label = sys_label.get('value') or sys_label.get('label') or sys_key
        cost_low = item.get('estimated_cost_low', 0) or 0
        cost_high = item.get('estimated_cost_high', 0) or 0
        repair_list_lines.append(
            f'  - system_key: "{sys_key}", system: "{sys_label}", '
            f'estimated_cost: ${cost_low:,}-${cost_high:,}'
        )

    prompt = _PERMIT_PROMPT.format(
        jurisdiction=juris_label,
        repair_list='\n'.join(repair_list_lines),
    )

    # Use the project's existing AI client wrapper (imported at module level)
    client = get_anthropic_client()
    if not client:
        logger.warning('permit_lookup: no Anthropic client available')
        return []

    response = client.messages.create(
        model=PERMIT_MODEL,
        max_tokens=2000,
        messages=[{'role': 'user', 'content': prompt}],
    )

    raw = (response.content[0].text or '').strip()
    # Strip code fences if present
    if raw.startswith('```'):
        raw = raw.split('```', 2)[1] if '```' in raw[3:] else raw
        if raw.startswith('json'):
            raw = raw[4:]
        raw = raw.strip().rstrip('`').strip()

    findings_raw = json.loads(raw)
    if not isinstance(findings_raw, list):
        raise ValueError('permit_lookup: expected JSON array, got %s' % type(findings_raw))

    # Normalize and add the verify_locally object for low-confidence findings
    findings: list[dict] = []
    for f in findings_raw:
        if not isinstance(f, dict):
            continue
        normalized = {
            'system_key': f.get('system_key', ''),
            'system': f.get('system', f.get('system_key', '')),
            'permit_required': f.get('permit_required', 'uncertain'),
            'confidence': f.get('confidence', 'low'),
            'permit_cost_low': f.get('permit_cost_low'),
            'permit_cost_high': f.get('permit_cost_high'),
            'consequences': (f.get('consequences') or '').strip(),
            'verify_locally': None,
        }
        # Build verify_locally callout when applicable
        dept = (f.get('verify_locally_dept') or '').strip()
        if normalized['permit_required'] == 'uncertain' or normalized['confidence'] == 'low':
            if dept:
                normalized['verify_locally'] = {
                    'dept': dept,
                    'phone': None,  # Phone lookup not yet implemented
                }
        findings.append(normalized)

    logger.info('permit_lookup: LLM returned %d findings for %s',
                len(findings), juris_label)
    return findings


# ── Helpers ──────────────────────────────────────────────────────────────

def _system_key(item: dict) -> str:
    """Stable cache key for a repair category. Lowercased, alpha-only."""
    cat = item.get('category') or item.get('system') or ''
    if isinstance(cat, dict):
        cat = cat.get('value') or cat.get('label') or ''
    s = ''.join(c if c.isalnum() else '_' for c in str(cat).lower()).strip('_')
    while '__' in s:
        s = s.replace('__', '_')
    return s or 'unknown'


def _jurisdiction_key(j: dict) -> str:
    """Stable cache key for a jurisdiction. State + county + city."""
    parts = [
        (j.get('state') or '').upper().strip(),
        (j.get('county') or '').lower().strip(),
        (j.get('city') or '').lower().strip(),
    ]
    return ':'.join(p.replace(' ', '_') for p in parts if p)


def _format_jurisdiction(j: dict) -> str:
    """Human-readable jurisdiction label for the LLM and UI."""
    city = (j.get('city') or '').strip()
    state = (j.get('state') or '').strip()
    county = (j.get('county') or '').strip()
    if city and state and county:
        return f'{city}, {state} · {county} County'
    if city and state:
        return f'{city}, {state}'
    if state:
        return state
    return 'Unknown jurisdiction'


def _empty_result(jurisdiction: dict) -> dict:
    return {
        'findings': [],
        'total_low': 0,
        'total_high': 0,
        'jurisdiction_label': _format_jurisdiction(jurisdiction),
        'cache_hits': 0,
        'cache_misses': 0,
    }


def _uncertain_fallback(item: dict, juris_label: str) -> dict:
    """Used when the LLM call fails entirely. Renders something rather than nothing."""
    sys_key = _system_key(item)
    return {
        'system_key': sys_key,
        'system': item.get('category') or item.get('system') or sys_key,
        'permit_required': 'uncertain',
        'confidence': 'low',
        'permit_cost_low': None,
        'permit_cost_high': None,
        'consequences': (
            f'Permit lookup temporarily unavailable for {juris_label}. '
            f'Verify with your local building department before scheduling work.'
        ),
        'verify_locally': {
            'dept': 'Local building department',
            'phone': None,
        },
    }


# ── v5.87.83: Web-search fee enrichment ──────────────────────────────────

_FEE_SEARCH_PROMPT = """You are extracting a specific permit fee from official building \
department fee schedules. Find the actual fee for this work in this jurisdiction.

JURISDICTION: {jurisdiction}
PERMIT TYPE: {system}
WORK DESCRIPTION: Repair or replacement of {system}, residential property.

Search for the official fee schedule from the city or county building department. \
Look for terms like "permit fee schedule", "building permit fees", or "{system} permit fee". \
Prefer .gov domains and current-year schedules.

Return a JSON object with these fields ONLY (no preamble, no markdown):

  - fee_low: integer USD. Low end of the permit fee.
  - fee_high: integer USD. High end of the permit fee, or same as fee_low if a flat fee.
  - source_url: string. Direct URL to the fee schedule you cited.
  - source_label: string. Short citation label, e.g. "City of San Jose 2025 Building Permit Fee Schedule".
  - notes: string or null. If the fee is a formula (e.g. "1% of project value"),
      explain in 1 sentence. Otherwise null.

If you cannot find an authoritative fee from a .gov or official jurisdiction \
source, return: {{"fee_low": null, "fee_high": null, "source_url": null, "source_label": null, "notes": "Fee not located in official sources"}}

Be honest. Don't fabricate a number. A null result is correct when the data isn't available."""


def _enrich_finding_with_web_fee(
    finding: dict,
    jurisdiction: dict,
    juris_label: str,
) -> dict:
    """For a single permit finding, do a web search for the actual fee.

    On success: replaces finding's permit_cost_low/high with the cited values
    and adds fee_source_url, fee_source_label, fee_notes.

    On failure: returns the finding unchanged. The original LLM estimate
    remains, just without a citation.

    Idempotent in the sense that if the finding already has a fee_source_url,
    we skip the search.
    """
    if finding.get('fee_source_url'):
        return finding  # Already enriched

    if finding.get('permit_required') not in ('required', 'likely'):
        return finding  # No fee to look up for not_required / uncertain

    client = get_anthropic_client()
    if not client:
        return finding

    system = finding.get('system') or finding.get('system_key', '')
    prompt = _FEE_SEARCH_PROMPT.format(jurisdiction=juris_label, system=system)

    try:
        resp = client.messages.create(
            model=FEE_SEARCH_MODEL,
            max_tokens=500,
            tools=[{
                'type': 'web_search_20250305',
                'name': 'web_search',
                'max_uses': 2,
            }],
            messages=[{'role': 'user', 'content': prompt}],
        )

        # Aggregate text from response, keeping the last text block
        # (which is Claude's final answer after any tool calls)
        text_parts: list[str] = []
        for block in resp.content:
            btype = getattr(block, 'type', '')
            if btype == 'text':
                text_parts.append(getattr(block, 'text', '') or '')

        raw = (text_parts[-1] if text_parts else '').strip()
        if raw.startswith('```'):
            raw = raw.split('```', 2)[1]
            if raw.startswith('json'):
                raw = raw[4:]
            raw = raw.strip().rstrip('`').strip()

        parsed = json.loads(raw)
        fee_low = parsed.get('fee_low')
        fee_high = parsed.get('fee_high')
        source_url = parsed.get('source_url')
        source_label = parsed.get('source_label')
        notes = parsed.get('notes')

        # Don't overwrite estimate if web search returned null for fees
        if fee_low is not None and fee_high is not None and source_url:
            finding = dict(finding)  # don't mutate input
            finding['permit_cost_low'] = int(fee_low)
            finding['permit_cost_high'] = int(fee_high)
            finding['fee_source_url'] = source_url
            finding['fee_source_label'] = source_label or 'Official source'
            if notes:
                finding['fee_notes'] = notes

    except Exception as e:
        logger.info('permit_lookup web-search fee enrichment failed for %s: %s',
                    finding.get('system_key'), e)

    return finding
