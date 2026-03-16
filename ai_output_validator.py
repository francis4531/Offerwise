"""
OfferWise AI Output Validator & Audit Logger v1.0
===================================================
Sits between every AI call and the user. Three jobs:

1. VALIDATE — enforce output contracts (bounds, enums, required fields)
2. GROUND — verify evidence strings actually appear in source documents
3. LOG — structured audit trail of every AI response for debugging & drift detection

Usage:
    from ai_output_validator import validate_truth_check, validate_cross_reference

    # In /api/truth-check:
    raw = json.loads(response_text)
    analysis, violations = validate_truth_check(raw, pdf_text=extracted_text)
    # 'analysis' is sanitized and safe to return to user
    # 'violations' tells you what was wrong (empty = clean)

    # In cross-reference pipeline:
    findings, violations = validate_cross_reference_findings(findings, disclosure_text, inspection_text)
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AUDIT LOG — append-only JSONL file, one line per AI call
# ---------------------------------------------------------------------------

_AUDIT_DIR = os.environ.get('OFFERWISE_AUDIT_DIR',
                            os.path.join(os.path.dirname(__file__), 'logs'))
_AUDIT_FILE = os.path.join(_AUDIT_DIR, 'ai_audit.jsonl')


def _write_audit(entry: dict):
    """Append one JSON line to the audit log. Never raises."""
    try:
        os.makedirs(_AUDIT_DIR, exist_ok=True)
        with open(_AUDIT_FILE, 'a') as f:
            f.write(json.dumps(entry, default=str) + '\n')
    except Exception as e:
        logger.warning(f"Audit log write failed: {e}")


def log_ai_call(
    endpoint: str,
    model: str,
    input_summary: dict,
    raw_output: Any,
    validated_output: Any,
    violations: List[dict],
    latency_ms: float = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
):
    """Log a complete AI call to the audit trail."""
    entry = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'endpoint': endpoint,
        'model': model,
        'latency_ms': round(latency_ms, 1),
        'tokens_in': tokens_in,
        'tokens_out': tokens_out,
        'input_summary': input_summary,
        'raw_output': _truncate(raw_output, 3000),
        'validated_output_summary': _summarize_output(validated_output),
        'violations': violations,
        'violation_count': len(violations),
        'clean': len(violations) == 0,
    }
    _write_audit(entry)

    # Also log to standard logger for real-time visibility
    if violations:
        logger.warning(
            f"AI output violations [{endpoint}]: {len(violations)} issues — "
            + "; ".join(v['code'] for v in violations[:5])
        )
    else:
        logger.info(f"AI output clean [{endpoint}]")


def _truncate(obj, max_chars):
    s = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
    return s[:max_chars] + '...' if len(s) > max_chars else s


def _summarize_output(obj):
    """Extract key fields for the audit log without the full payload."""
    if isinstance(obj, dict):
        summary = {}
        for key in ('trust_score', 'grade', 'red_flags', 'transparency_score',
                     'blank_unknown_count', 'evasion_phrases'):
            if key in obj:
                val = obj[key]
                if isinstance(val, list):
                    summary[key] = f"{len(val)} items"
                else:
                    summary[key] = val
        return summary or obj
    return str(obj)[:500]


# ---------------------------------------------------------------------------
# CONSTANTS — the output contracts
# ---------------------------------------------------------------------------

VALID_GRADES = {'A', 'A-', 'A+', 'B', 'B-', 'B+', 'C', 'C-', 'C+', 'D', 'D-', 'D+', 'F'}
VALID_SEVERITIES = {'high', 'medium', 'low'}
VALID_RED_FLAG_CATEGORIES = {
    'water', 'structural', 'electrical', 'plumbing', 'roof',
    'environmental', 'permits', 'pest', 'other',
    # Also accept broader categories from cross-ref
    'water_damage', 'safety', 'financial',
}
VALID_CROSS_REF_TYPES = {'contradiction', 'omission', 'context'}
VALID_CROSS_REF_SEVERITIES = {'high', 'medium', 'info'}

# Fuzzy match threshold — what fraction of an evidence string
# must appear in the source document to count as "grounded"
EVIDENCE_MIN_WORDS = 4          # Evidence must be at least 4 words
EVIDENCE_GROUNDING_THRESHOLD = 0.5  # At least 50% of words must appear in source


# ---------------------------------------------------------------------------
# TRUTH CHECK VALIDATOR
# ---------------------------------------------------------------------------

def validate_truth_check(
    raw: dict,
    pdf_text: Optional[str] = None,
) -> Tuple[dict, List[dict]]:
    """
    Validate and sanitize a Truth Check AI response.

    Args:
        raw: The parsed JSON from Claude
        pdf_text: The extracted text from the uploaded PDF (for grounding checks).
                  If None, grounding checks are skipped.

    Returns:
        (sanitized_output, violations)
        - sanitized_output is safe to return to the user
        - violations is a list of dicts describing what was wrong
    """
    violations = []
    out = dict(raw)  # shallow copy

    # --- trust_score: must be int/float in [0, 100] ---
    score = out.get('trust_score')
    if score is None:
        violations.append(_v('MISSING_TRUST_SCORE', 'trust_score is missing'))
        out['trust_score'] = 50
    elif not isinstance(score, (int, float)):
        violations.append(_v('INVALID_TRUST_SCORE_TYPE', f'trust_score is {type(score).__name__}'))
        out['trust_score'] = 50
    elif score < 0 or score > 100:
        violations.append(_v('TRUST_SCORE_OUT_OF_BOUNDS',
                             f'trust_score={score}, clamped to [0,100]'))
        out['trust_score'] = max(0, min(100, score))

    # --- grade: must be A-F ---
    grade = out.get('grade', '')
    if grade not in VALID_GRADES:
        violations.append(_v('INVALID_GRADE', f"grade='{grade}', defaulting to C"))
        out['grade'] = 'C'

    # --- red_flags: list of dicts with required fields ---
    flags = out.get('red_flags')
    if not isinstance(flags, list):
        violations.append(_v('RED_FLAGS_NOT_LIST', f'red_flags is {type(flags).__name__}'))
        out['red_flags'] = []
    else:
        clean_flags = []
        for i, flag in enumerate(flags):
            if not isinstance(flag, dict):
                violations.append(_v('RED_FLAG_NOT_DICT', f'red_flags[{i}] is {type(flag).__name__}'))
                continue

            # Required fields
            for field in ('title', 'detail', 'severity', 'evidence'):
                if not flag.get(field):
                    violations.append(_v('RED_FLAG_MISSING_FIELD',
                                         f'red_flags[{i}] missing "{field}"'))

            # Severity enum
            sev = flag.get('severity', '')
            if sev not in VALID_SEVERITIES:
                violations.append(_v('INVALID_SEVERITY',
                                     f'red_flags[{i}] severity="{sev}", '
                                     f'defaulting to "medium"'))
                flag['severity'] = 'medium'

            # Category enum (warn but don't reject — non-critical)
            cat = flag.get('category', '')
            if cat and cat not in VALID_RED_FLAG_CATEGORIES:
                violations.append(_v('UNKNOWN_CATEGORY',
                                     f'red_flags[{i}] category="{cat}"',
                                     severity='warn'))

            # Detail must be a complete sentence
            detail = flag.get('detail', '')
            if detail and not detail.rstrip().endswith(('.', '?', '!', '"', "'")):
                violations.append(_v('INCOMPLETE_DETAIL',
                                     f'red_flags[{i}] detail doesn\'t end with punctuation',
                                     severity='warn'))

            # Evidence grounding check
            evidence = flag.get('evidence', '')
            if evidence and pdf_text:
                grounded, match_pct = _check_grounding(evidence, pdf_text)
                if not grounded:
                    violations.append(_v('UNGROUNDED_EVIDENCE',
                                         f'red_flags[{i}] evidence not found in document '
                                         f'(match={match_pct:.0%}): "{evidence[:80]}"'))
                    # Tag it so frontend can show with reduced confidence
                    flag['_grounding_warning'] = True
                    flag['_grounding_match'] = round(match_pct, 2)

            clean_flags.append(flag)
        out['red_flags'] = clean_flags

    # --- blank_unknown_count: non-negative integer ---
    buc = out.get('blank_unknown_count')
    if buc is not None:
        if not isinstance(buc, (int, float)) or buc < 0:
            violations.append(_v('INVALID_BLANK_COUNT',
                                 f'blank_unknown_count={buc}, clamped to 0'))
            out['blank_unknown_count'] = max(0, int(buc)) if isinstance(buc, (int, float)) else 0

    # --- evasion_phrases: list of strings that must appear in document ---
    phrases = out.get('evasion_phrases')
    if isinstance(phrases, list) and pdf_text:
        clean_phrases = []
        pdf_lower = pdf_text.lower()
        for phrase in phrases:
            if not isinstance(phrase, str):
                continue
            if phrase.lower() in pdf_lower:
                clean_phrases.append(phrase)
            else:
                violations.append(_v('UNGROUNDED_EVASION_PHRASE',
                                     f'Evasion phrase not found in document: "{phrase[:80]}"'))
        out['evasion_phrases'] = clean_phrases

    # --- overall_assessment & most_concerning: must be strings ---
    for field in ('overall_assessment', 'most_concerning'):
        val = out.get(field)
        if val and not isinstance(val, str):
            violations.append(_v(f'INVALID_{field.upper()}', f'{field} is {type(val).__name__}'))
            out[field] = str(val)[:500]

    return out, violations


# ---------------------------------------------------------------------------
# CROSS-REFERENCE / EXTERNAL VERIFICATION VALIDATOR
# ---------------------------------------------------------------------------

def validate_cross_reference_findings(
    findings: list,
    disclosure_text: Optional[str] = None,
    inspection_text: Optional[str] = None,
) -> Tuple[list, List[dict]]:
    """
    Validate AI-generated cross-reference findings.

    Args:
        findings: List of finding dicts from AI
        disclosure_text: Seller disclosure text (for grounding)
        inspection_text: Inspection report text (for grounding)

    Returns:
        (sanitized_findings, violations)
    """
    violations = []
    if not isinstance(findings, list):
        return [], [_v('FINDINGS_NOT_LIST', f'findings is {type(findings).__name__}')]

    combined_text = ' '.join(filter(None, [disclosure_text, inspection_text]))
    clean = []
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            violations.append(_v('FINDING_NOT_DICT', f'findings[{i}] is {type(f).__name__}'))
            continue

        # Type enum
        ftype = f.get('type', '')
        if ftype not in VALID_CROSS_REF_TYPES:
            violations.append(_v('INVALID_FINDING_TYPE',
                                 f'findings[{i}] type="{ftype}", defaulting to "context"'))
            f['type'] = 'context'

        # Severity enum
        sev = f.get('severity', '')
        if sev not in VALID_CROSS_REF_SEVERITIES:
            violations.append(_v('INVALID_FINDING_SEVERITY',
                                 f'findings[{i}] severity="{sev}", defaulting to "info"'))
            f['severity'] = 'info'

        # Required fields
        if not f.get('title'):
            violations.append(_v('FINDING_MISSING_TITLE', f'findings[{i}] has no title'))
        if not f.get('detail'):
            violations.append(_v('FINDING_MISSING_DETAIL', f'findings[{i}] has no detail'))

        # Title length
        title = f.get('title', '')
        if len(title) > 120:
            violations.append(_v('FINDING_TITLE_TOO_LONG',
                                 f'findings[{i}] title is {len(title)} chars (max 120)',
                                 severity='warn'))
            f['title'] = title[:120]

        # Detail length
        detail = f.get('detail', '')
        if len(detail) > 500:
            violations.append(_v('FINDING_DETAIL_TOO_LONG',
                                 f'findings[{i}] detail is {len(detail)} chars (max 500)',
                                 severity='warn'))
            f['detail'] = detail[:500]

        # Confidence bounds
        conf = f.get('confidence')
        if conf is not None:
            if not isinstance(conf, (int, float)) or conf < 0 or conf > 1:
                violations.append(_v('INVALID_CONFIDENCE',
                                     f'findings[{i}] confidence={conf}, clamping to [0,1]'))
                f['confidence'] = max(0.0, min(1.0, float(conf))) if isinstance(conf, (int, float)) else 0.5

        clean.append(f)

    # Cap at 5 findings (as the prompt requests)
    if len(clean) > 5:
        violations.append(_v('TOO_MANY_FINDINGS',
                             f'{len(clean)} findings returned, capping at 5', severity='warn'))
        clean = clean[:5]

    return clean, violations


# ---------------------------------------------------------------------------
# CROSS-REFERENCE SEVERITY RATING VALIDATOR
# ---------------------------------------------------------------------------

def validate_severity_ratings(
    ai_data: dict,
    original_issues: list,
) -> Tuple[dict, List[dict]]:
    """
    Validate the AI severity ratings from OptimizedHybridCrossReferenceEngine.

    Args:
        ai_data: Parsed JSON from AI (has 'issues', 'transparency_score', 'summary')
        original_issues: The issues that were sent to AI for rating

    Returns:
        (sanitized_data, violations)
    """
    violations = []
    out = dict(ai_data)

    # transparency_score
    ts = out.get('transparency_score')
    if ts is not None:
        if not isinstance(ts, (int, float)) or ts < 0 or ts > 100:
            violations.append(_v('INVALID_TRANSPARENCY_SCORE',
                                 f'transparency_score={ts}, clamping to [0,100]'))
            out['transparency_score'] = max(0, min(100, int(ts))) if isinstance(ts, (int, float)) else 50

    # summary
    summary = out.get('summary')
    if summary and not isinstance(summary, str):
        violations.append(_v('INVALID_SUMMARY_TYPE', f'summary is {type(summary).__name__}'))
        out['summary'] = str(summary)[:500]

    # issues — each must have valid severity + confidence
    valid_issue_severities = {'critical', 'major', 'moderate', 'minor'}
    issues = out.get('issues', [])
    if not isinstance(issues, list):
        violations.append(_v('ISSUES_NOT_LIST', f'issues is {type(issues).__name__}'))
        out['issues'] = []
    else:
        for i, issue in enumerate(issues):
            if not isinstance(issue, dict):
                continue
            sev = issue.get('severity', '')
            if sev not in valid_issue_severities:
                violations.append(_v('INVALID_ISSUE_SEVERITY',
                                     f'issues[{i}] severity="{sev}", defaulting to "moderate"'))
                issue['severity'] = 'moderate'
            conf = issue.get('confidence')
            if conf is not None:
                if not isinstance(conf, (int, float)) or conf < 0 or conf > 1:
                    violations.append(_v('INVALID_ISSUE_CONFIDENCE',
                                         f'issues[{i}] confidence={conf}, clamping'))
                    issue['confidence'] = max(0.0, min(1.0, float(conf))) if isinstance(conf, (int, float)) else 0.5

    # Check count consistency — AI shouldn't invent new issue IDs
    original_ids = {f"C{i+1}" for i in range(len([x for x in original_issues if x.get('type') == 'contradiction']))}
    original_ids.update(f"U{i+1}" for i in range(len([x for x in original_issues if x.get('type') == 'undisclosed'])))
    for issue in out.get('issues', []):
        iid = issue.get('id', '')
        if iid and iid not in original_ids:
            violations.append(_v('INVENTED_ISSUE_ID',
                                 f'AI returned issue ID "{iid}" not in original set'))

    return out, violations


# ---------------------------------------------------------------------------
# EVIDENCE GROUNDING CHECK
# ---------------------------------------------------------------------------

def _check_grounding(evidence: str, source_text: str) -> Tuple[bool, float]:
    """
    Check whether an evidence string is grounded in the source document.

    Strategy: tokenize both into words, check what fraction of evidence
    words appear in the source. This handles paraphrasing, OCR noise,
    and formatting differences better than exact substring matching.

    Returns:
        (is_grounded, match_percentage)
    """
    if not evidence or not source_text:
        return True, 1.0  # Can't check → assume OK

    # Normalize
    ev_words = _tokenize(evidence)
    if len(ev_words) < EVIDENCE_MIN_WORDS:
        return True, 1.0  # Too short to meaningfully check

    source_words_set = set(_tokenize(source_text))

    matches = sum(1 for w in ev_words if w in source_words_set)
    match_pct = matches / len(ev_words) if ev_words else 0

    return match_pct >= EVIDENCE_GROUNDING_THRESHOLD, match_pct


def _tokenize(text: str) -> list:
    """Split text into lowercase words, strip punctuation."""
    import re
    return re.findall(r'[a-z0-9]+', text.lower())


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _v(code: str, message: str, severity: str = 'error') -> dict:
    """Create a violation entry."""
    return {'code': code, 'message': message, 'severity': severity}


# ---------------------------------------------------------------------------
# CONVENIENCE: Wrap an entire AI call with timing + validation + logging
# ---------------------------------------------------------------------------

def validated_truth_check_call(
    client,
    model: str,
    messages: list,
    pdf_text: Optional[str] = None,
    **kwargs
) -> Tuple[dict, List[dict]]:
    """
    Make a Claude API call for truth-check, validate the output,
    and log everything. Returns (sanitized_analysis, violations).

    Usage:
        analysis, violations = validated_truth_check_call(
            client, 'claude-sonnet-4-5-20250929', messages,
            pdf_text=extracted_text, max_tokens=2000
        )
    """
    start = time.time()
    try:
        response = client.messages.create(model=model, messages=messages, **kwargs)
        latency_ms = (time.time() - start) * 1000

        raw_text = response.content[0].text.strip()

        # Strip markdown fences
        if raw_text.startswith('```'):
            raw_text = raw_text.split('\n', 1)[1] if '\n' in raw_text else raw_text[3:]
            if raw_text.endswith('```'):
                raw_text = raw_text[:-3]
            raw_text = raw_text.strip()

        raw = json.loads(raw_text)
        analysis, violations = validate_truth_check(raw, pdf_text=pdf_text)

        # Extract token counts from response
        usage = getattr(response, 'usage', None)
        tokens_in = getattr(usage, 'input_tokens', 0) if usage else 0
        tokens_out = getattr(usage, 'output_tokens', 0) if usage else 0

        log_ai_call(
            endpoint='truth-check',
            model=model,
            input_summary={'pdf_text_length': len(pdf_text) if pdf_text else 0},
            raw_output=raw,
            validated_output=analysis,
            violations=violations,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

        return analysis, violations

    except json.JSONDecodeError as e:
        latency_ms = (time.time() - start) * 1000
        violations = [_v('JSON_PARSE_ERROR', f'Claude response not valid JSON: {str(e)[:100]}')]
        log_ai_call(
            endpoint='truth-check',
            model=model,
            input_summary={'pdf_text_length': len(pdf_text) if pdf_text else 0},
            raw_output=raw_text if 'raw_text' in dir() else '(no response)',
            validated_output=None,
            violations=violations,
            latency_ms=latency_ms,
        )
        raise

    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        log_ai_call(
            endpoint='truth-check',
            model=model,
            input_summary={'pdf_text_length': len(pdf_text) if pdf_text else 0},
            raw_output=str(e)[:200],
            validated_output=None,
            violations=[_v('API_ERROR', str(e)[:200])],
            latency_ms=latency_ms,
        )
        raise
