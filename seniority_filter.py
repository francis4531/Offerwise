"""
seniority_filter.py — v5.87.74

Post-filter for prospect discovery results. Provider seniority tags
(Hunter's 'senior'/'executive' classification, Apollo's enums) are
imperfect — sales ICs like 'Senior Account Executive' often slip
through tagged as 'senior' even though they aren't decision-makers.

This module applies a strict title-text-based filter AFTER provider
results come back, as a last-line defense. Activated by the
APPLY_SENIORITY_FILTER env flag (default on) or `apply_filter`
parameter passed to `filter_prospects()`.

Band B — "Decision-makers only":
  KEEP titles that look like C-suite, founder, president, VP, or
  Head-of-Function. EXCLUDE Director and below, ICs, sales/admin
  roles, and pure legal/compliance roles (unless the title also
  carries a product/engineering function).

Public API:
  - is_band_b_executive(title: str) -> bool
  - filter_prospects(prospects: list[dict], reason_log: list = None)
        -> tuple[list[dict], dict]  # (kept, stats)

Calibrated against real Hunter results from Lemonade and Opendoor
on 2026-05-04. Default behavior: STRICT.
"""
from __future__ import annotations
import logging
import os
import re
from typing import Iterable

logger = logging.getLogger(__name__)

# Whether the filter is on by default. Override per-call via
# `filter_prospects(..., apply_filter=False)`.
DEFAULT_APPLY = os.environ.get('APPLY_SENIORITY_FILTER', '1').strip() not in ('0', 'false', 'False', 'no', 'off', '')


# ── Title classification rules ────────────────────────────────────────────
#
# Order of evaluation for each title:
#   1. If title matches a LEGAL term AND no PRODUCT/ENG term, REJECT.
#   2. If title matches any HARD_REJECT term, REJECT.
#   3. If title matches any KEEP term, KEEP.
#   4. Otherwise, REJECT (default-deny).
#
# All matching is case-insensitive substring on the lowercased title.

# Terms that immediately reject regardless of other keywords. These catch
# IC roles, junior staff, and ambiguous titles that look senior but aren't.
HARD_REJECT_TERMS = [
    # IC-level engineering / technical
    'engineer', 'developer', 'designer', 'architect',
    # Sales ICs (Hunter often tags these 'senior' incorrectly)
    'account executive', 'sdr ', 'bdr ',
    'sales rep', 'sales representative', 'sales associate',
    'inside sales', 'business development representative',
    # Customer-facing / support
    'customer success manager', 'customer experience manager',
    'partner success manager', 'partner success specialist',
    'account manager', 'support specialist',
    # Admin / EA / logistics
    'executive assistant', 'executive specialist', 'administrative',
    'coordinator', 'recruiter', 'specialist',
    # Mid-management (Band B is C-suite, founders, president, VP, Head of)
    'director',  # rejects all Director levels including Senior Director
    ' manager', 'sr manager', 'senior manager', 'product manager',
    'sr. manager',
    # Consulting / temp / interns
    'intern ', ' intern', 'consultant', 'contractor', 'freelance',
    # IC analyst
    'analyst', 'researcher',
]

# Terms that signal real leadership. If a title contains any of these
# AND doesn't trip a hard reject, KEEP.
KEEP_TERMS = [
    # C-suite (the 'chief' substring catches "Chief X Officer" generically,
    # and the explicit acronyms catch the cases where the word 'chief' is
    # spelled out as 'CEO' / 'CTO' etc.)
    ' ceo', ' cto', ' cfo', ' coo', ' cmo', ' cpo', ' cro', ' cdo',
    ' cso', ' ciso', ' cpto',
    'ceo,', 'cto,', 'cfo,', 'coo,', 'cmo,', 'cpo,',  # comma-suffixed
    'chief ',  # "Chief X Officer", "Chief of Staff", "Chief X"
    # Founders
    'founder', 'co-founder', 'cofounder',
    # President / executive-tier
    'president', 'evp ', 'svp ', 'evp,', 'svp,',
    'executive vice president', 'senior vice president',
    # VP / Vice President
    'vp ', 'vp,', 'vp of ', 'vice president',
    # Head of [Function] — kept per founder direction (small-co=VP-equiv)
    'head of', 'global head',
]

# Legal/compliance terms that, when present alone, REJECT. If the title
# ALSO contains a product/engineering term, KEEP (rare hybrid case).
LEGAL_TERMS = [
    'general counsel', 'attorney', 'compliance officer',
    'chief legal officer', 'legal counsel', 'counsel,',
    'paralegal',
]

# Product/engineering signals that override the legal reject. If a person
# is "VP Product, Legal Tech" we want to keep them — they're a product VP.
LEGAL_OVERRIDE_TERMS = [
    'product', 'engineering', 'technology', 'data', 'design',
]


# ── Internal helpers ─────────────────────────────────────────────────────

def _lc(title: str) -> str:
    """Lowercased, single-spaced title with leading/trailing whitespace
    stripped. We add a leading and trailing space so substring checks like
    'vp ' and ' ceo' work correctly even at title boundaries."""
    return ' ' + re.sub(r'\s+', ' ', (title or '').lower().strip()) + ' '


def _matches_any(title_lc: str, terms: Iterable[str]) -> bool:
    return any(t in title_lc for t in terms)


# ── Public API ───────────────────────────────────────────────────────────

def is_band_b_executive(title: str) -> tuple[bool, str]:
    """Decide whether a title qualifies for Band B (decision-maker only).

    Returns (keep: bool, reason: str). The reason explains the decision
    in 1-3 words for logging/audit purposes.
    """
    if not title or not title.strip():
        return False, 'no title'

    title_lc = _lc(title)

    # Step 1: Legal/compliance rejection unless they're product/eng
    has_legal = _matches_any(title_lc, LEGAL_TERMS)
    has_product = _matches_any(title_lc, LEGAL_OVERRIDE_TERMS)
    if has_legal and not has_product:
        return False, 'legal/compliance role'

    # Step 2: Hard reject
    if _matches_any(title_lc, HARD_REJECT_TERMS):
        # Find which term tripped, for better logging
        for t in HARD_REJECT_TERMS:
            if t in title_lc:
                return False, f'reject: {t.strip()}'
        return False, 'hard reject'

    # Step 3: Keep
    if _matches_any(title_lc, KEEP_TERMS):
        return True, 'keep'

    # Step 4: Default deny — title doesn't match any leadership signal
    return False, 'no leadership signal'


def filter_prospects(
    prospects: list[dict],
    apply_filter: bool = None,
    title_key: str = 'title',
) -> tuple[list[dict], dict]:
    """Apply Band B seniority filter to a list of prospect dicts.

    Args:
      prospects: list of dicts with at least a `title` field
      apply_filter: True/False to override; None uses DEFAULT_APPLY env
      title_key: which dict key holds the title string. Defaults to 'title'
                 but Hunter's response shape uses 'position', so callers
                 working with that shape should pass title_key='position'.

    Returns:
      (kept_prospects, stats_dict)
      stats_dict has: total_in, total_kept, total_rejected,
                      reject_reasons {reason: count},
                      sample_rejected (first 5 rejected for debugging)
    """
    if apply_filter is None:
        apply_filter = DEFAULT_APPLY

    if not apply_filter:
        return list(prospects), {
            'total_in': len(prospects),
            'total_kept': len(prospects),
            'total_rejected': 0,
            'reject_reasons': {},
            'sample_rejected': [],
            'filter_applied': False,
        }

    kept: list[dict] = []
    rejected: list[dict] = []
    reasons: dict[str, int] = {}

    for p in prospects:
        title = (p.get(title_key) or '').strip()
        keep, reason = is_band_b_executive(title)
        if keep:
            kept.append(p)
        else:
            rejected.append({**p, '_reject_reason': reason})
            reasons[reason] = reasons.get(reason, 0) + 1

    stats = {
        'total_in': len(prospects),
        'total_kept': len(kept),
        'total_rejected': len(rejected),
        'reject_reasons': reasons,
        'sample_rejected': [
            {'title': r.get(title_key, ''), 'reason': r.get('_reject_reason', '')}
            for r in rejected[:5]
        ],
        'filter_applied': True,
    }

    if rejected:
        logger.info(
            'seniority_filter: kept %d / %d (rejected %d). reasons=%s',
            len(kept), len(prospects), len(rejected), reasons,
        )

    return kept, stats
