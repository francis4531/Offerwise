"""infra_invoice_parser.py — v5.87.72

Parses an inbound vendor invoice email (forwarded by the founder via Gmail
filter → Resend Inbound webhook) into structured fields suitable for
writing to InfraInvoice.

Approach:
  1. Combine subject + plain text body + (if present) extracted PDF text
  2. Send to Claude Haiku 4.5 with a strict JSON schema prompt
  3. Parse Claude's JSON response
  4. Match parsed vendor name against existing InfraVendor table (fuzzy)
  5. Return a structured ParseResult that the webhook handler writes to DB

Cost: ~$0.005 per parse with Haiku 4.5. Five vendors × 1 invoice/month = $0.025/month.

Design notes:
  - This module is dependency-light. It does NOT touch the database directly;
    it returns a ParseResult and lets the caller (webhook handler) decide.
  - The Anthropic API call is wrapped in a single try/except — if Claude
    fails or returns malformed JSON, we return a low-confidence result that
    flags the invoice as needs_review rather than crashing.
  - Vendor matching uses simple normalized substring match; we do not
    auto-create new InfraVendor rows. If no match, the row is flagged for
    manual vendor selection on the costs page.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, List, Dict, Any


log = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Outcome of invoice parsing. Always returns a result; flags low-quality."""
    vendor_name_raw: Optional[str] = None       # what Claude extracted
    matched_vendor_id: Optional[int] = None     # int if matched against InfraVendor table
    matched_vendor_name: Optional[str] = None   # canonical name if matched
    amount_usd: Optional[float] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    invoice_ref: Optional[str] = None
    description: Optional[str] = None
    confidence: float = 0.0                     # 0.0 = parse failed, 1.0 = full confidence
    needs_review: bool = True                   # default to safe — needs human eyes
    parse_errors: List[str] = field(default_factory=list)

    def to_log_dict(self) -> Dict[str, Any]:
        return {
            'vendor_raw': self.vendor_name_raw,
            'matched_id': self.matched_vendor_id,
            'matched_name': self.matched_vendor_name,
            'amount_usd': self.amount_usd,
            'period_start': self.period_start.isoformat() if self.period_start else None,
            'period_end':   self.period_end.isoformat()   if self.period_end   else None,
            'invoice_ref': self.invoice_ref,
            'confidence': self.confidence,
            'needs_review': self.needs_review,
            'errors': self.parse_errors,
        }


# Confidence threshold below which we always flag for review.
REVIEW_THRESHOLD = 0.85

# Prompt — kept tight; more tokens = more cost, no benefit on Haiku-grade tasks.
_PROMPT = """You are extracting structured invoice fields from a forwarded vendor invoice email.

Return ONLY a single JSON object with this exact schema (no prose, no markdown fences):
{
  "vendor": "<company name as it appears in the invoice, e.g. 'Render', 'RentCast', 'Anthropic'>",
  "amount_usd": <number, the total amount due in USD; convert if other currency>,
  "period_start": "<YYYY-MM-DD, billing period start; if monthly, use first of month>",
  "period_end": "<YYYY-MM-DD, billing period end; if monthly, use last day of month>",
  "invoice_ref": "<invoice number or receipt ID, or null if absent>",
  "description": "<one-line summary, e.g. 'Pro plan + 2M tokens'>",
  "confidence": <number 0.0-1.0, your confidence the fields are correct>
}

Rules:
- If currency is not USD, convert using the email's stated rate or skip and set confidence < 0.7.
- If the email is NOT an invoice (it's a marketing email, support reply, etc.), set confidence: 0.0
- If amount or dates are ambiguous, lower the confidence accordingly.
- Use null for any field you cannot determine. Do not guess.
- Output JSON only. No commentary.

EMAIL CONTENT:
---
{email_content}
---
"""


def _parse_date(s: Any) -> Optional[date]:
    """Parse a date from Claude's output. Handles YYYY-MM-DD, ISO datetime, or null."""
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).date()
    except Exception:
        try:
            return datetime.strptime(s[:10], '%Y-%m-%d').date()
        except Exception:
            return None


def _normalize_vendor(name: str) -> str:
    """Normalize vendor name for matching: lowercase, alphanumeric only."""
    return re.sub(r'[^a-z0-9]', '', (name or '').lower())


def _match_vendor(raw_name: Optional[str], all_vendors: List[Dict[str, Any]]):
    """Fuzzy-match raw vendor name against existing InfraVendor rows.

    Strategy: normalize both sides, check substring containment in either direction.
    Returns (vendor_id, canonical_name) or (None, None).
    """
    if not raw_name or not all_vendors:
        return (None, None)
    raw_norm = _normalize_vendor(raw_name)
    if not raw_norm:
        return (None, None)
    for v in all_vendors:
        v_norm = _normalize_vendor(v.get('name', ''))
        if not v_norm:
            continue
        if raw_norm == v_norm:
            return (v['id'], v['name'])
        if raw_norm in v_norm or v_norm in raw_norm:
            return (v['id'], v['name'])
    return (None, None)


def parse_invoice_email(
    email_content: str,
    all_vendors: List[Dict[str, Any]],
    anthropic_client=None,
) -> ParseResult:
    """Parse an invoice email into structured fields.

    email_content: subject + body + (optional) PDF-extracted text, concatenated
    all_vendors: list of {'id': int, 'name': str} for matching
    anthropic_client: optional pre-initialized Anthropic client. If None, creates one.

    Returns: ParseResult with confidence-graded fields.
    """
    result = ParseResult()
    if not email_content or len(email_content.strip()) < 20:
        result.parse_errors.append('email_content_too_short')
        return result
    # Cap the input — vendor invoices are typically <5 KB; safety limit at 50K chars.
    if len(email_content) > 50000:
        email_content = email_content[:50000]
        result.parse_errors.append('email_truncated_to_50k')
    try:
        if anthropic_client is None:
            import anthropic
            anthropic_client = anthropic.Anthropic()
        prompt_full = _PROMPT.replace('{email_content}', email_content)
        # Haiku 4.5 — the cheapest current model that does structured extraction well
        msg = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=512,
            messages=[{'role': 'user', 'content': prompt_full}],
        )
        raw = msg.content[0].text.strip()
        # Strip ```json fences if Claude included them despite instructions
        if raw.startswith('```'):
            raw = re.sub(r'^```(?:json)?\s*\n?', '', raw)
            raw = re.sub(r'\n?```\s*$', '', raw)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as je:
            result.parse_errors.append(f'json_decode_error: {je}')
            log.warning('Invoice parse failed JSON decode. Raw: %s', raw[:300])
            return result
    except Exception as e:
        result.parse_errors.append(f'anthropic_error: {e}')
        log.exception('Invoice parse: Anthropic call failed')
        return result

    # Map parsed fields onto result
    result.vendor_name_raw = parsed.get('vendor')
    try:
        a = parsed.get('amount_usd')
        if a is not None:
            result.amount_usd = float(a)
    except Exception:
        result.parse_errors.append('amount_not_float')
    result.period_start = _parse_date(parsed.get('period_start'))
    result.period_end   = _parse_date(parsed.get('period_end'))
    result.invoice_ref  = parsed.get('invoice_ref') or None
    result.description  = parsed.get('description') or None
    try:
        c = float(parsed.get('confidence', 0.0))
        result.confidence = max(0.0, min(1.0, c))
    except Exception:
        result.confidence = 0.0
        result.parse_errors.append('confidence_not_float')

    # Vendor match against existing InfraVendor rows
    vid, vname = _match_vendor(result.vendor_name_raw, all_vendors)
    result.matched_vendor_id = vid
    result.matched_vendor_name = vname

    # Decide review status. Need ALL of: vendor matched, amount, period_start, conf >= threshold.
    has_required = (
        result.matched_vendor_id is not None and
        result.amount_usd is not None and
        result.amount_usd > 0 and
        result.period_start is not None and
        result.confidence >= REVIEW_THRESHOLD
    )
    result.needs_review = not has_required
    if not has_required:
        if result.matched_vendor_id is None:
            result.parse_errors.append('no_vendor_match')
        if not result.amount_usd:
            result.parse_errors.append('no_amount')
        if not result.period_start:
            result.parse_errors.append('no_period_start')
        if result.confidence < REVIEW_THRESHOLD:
            result.parse_errors.append(f'low_confidence_{result.confidence:.2f}')
    return result
