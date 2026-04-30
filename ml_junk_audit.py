"""ML training data quality audit.

Identifies "junk" rows in ml_finding_labels — text that was ingested as if it
were a finding but is actually disclaimer boilerplate, list fragments, or
document metadata. Training on this polluted data degrades model accuracy by
teaching it that keywords like "radon" or "EPA action level" map to whatever
category/severity the boilerplate was labeled with, rather than to the real
finding semantics.

Shared by:
  - scripts/diagnostics/ml_junk_scope.py (CLI)
  - /api/admin/ml-junk-scope endpoint (Diagnostics panel button)

Design note: the junk patterns are kept in one place (JUNK_PATTERNS and
JUNK_SHORT_LENGTH below) so both the audit and any future cleanup operation
use the exact same definition of "junk." Don't let these drift.
"""
from __future__ import annotations

from typing import Any

# ── Junk detection patterns ────────────────────────────────────────────
# Each pattern is an ILIKE expression. Patterns starting with text at the
# beginning match findings whose FIRST words are boilerplate. Patterns with
# leading/trailing %...% match boilerplate appearing anywhere in the text.
#
# Everything here has been sampled from real database rows and confirmed to
# be non-finding text. Do NOT add a pattern without sampling to confirm it
# isn't catching real findings.

# Patterns that should match at the START of a finding (most reliable signal)
JUNK_STARTERS = [
    'These services may',
    'The following were not inspected',
    'Buyer and Seller are advised',
    'General advisories',
    'Environmental Hazards:',
    'A general physical inspection',
    'Agent will not determine',
    'The inspector did not',
    'This inspection does not',
    'Items not inspected',
    'Excluded from this',
    'Not part of this',
    'Disclaimer:',
    'Items outside the scope',
    'Not within the scope',
    'The Buyer should',
    'Buyer should',
    'It is recommended that',
    'We recommend',
    'SUGGESTION:',
    'RECOMMENDATION:',
    'Note:',
]

# Patterns that match anywhere in the text (use sparingly — risk of false positives)
JUNK_CONTAINS = [
    'beyond the scope',
    'outside the scope of',
    'not included in this inspection',
    'advised to consult',
    'recommended to hire a qualified',
    'such as but not limited',
    'including but not limited to',
    'consult with a qualified',
    'recommend further evaluation',  # often appears in disclaimers, but could be real — watch this
]

# Rows with text shorter than this are likely fragments, not findings
JUNK_SHORT_LENGTH = 30


def _build_junk_filter(MLFindingLabel, func, or_):
    """Build a SQLAlchemy filter matching all junk patterns."""
    conditions = []
    for pat in JUNK_STARTERS:
        conditions.append(MLFindingLabel.finding_text.ilike(f'{pat}%'))
    for pat in JUNK_CONTAINS:
        conditions.append(MLFindingLabel.finding_text.ilike(f'%{pat}%'))
    conditions.append(func.length(MLFindingLabel.finding_text) < JUNK_SHORT_LENGTH)
    return or_(*conditions)


def audit_ml_training_data() -> dict[str, Any]:
    """Run the full junk analysis. Returns a dict suitable for JSON serialization.

    Structure:
        {
            'total_rows': int,
            'junk_rows': int,
            'clean_rows': int,
            'junk_pct': float,
            'by_pattern': [ {'name': str, 'count': int, 'sample': str} ],
            'by_source': [ {'source': str, 'junk_count': int, 'total_count': int} ],
            'by_category': [ {'category': str, 'junk_count': int, 'total_count': int} ],
            'sample_junk': [ {'category': str, 'severity': str, 'source': str, 'text': str} ],
        }

    Performance note: this function reads all ml_finding_labels rows into memory
    once (SELECT finding_text, category, severity, source) and does pattern
    matching in Python. That's ~8MB for 65K rows, and runs in <1s. The earlier
    version issued ~24 separate Postgres COUNT queries with ILIKE filters, each
    doing a full table scan (can't use indexes on ILIKE %...%); that took long
    enough to hit the HTTP timeout.
    """
    from models import MLFindingLabel, db
    import random

    total = MLFindingLabel.query.count()
    if total == 0:
        return {'total_rows': 0, 'junk_rows': 0, 'clean_rows': 0, 'junk_pct': 0.0,
                'by_pattern': [], 'by_source': [], 'by_category': [], 'sample_junk': []}

    # Single query — grab everything we need in one go.
    rows = db.session.query(
        MLFindingLabel.id,
        MLFindingLabel.finding_text,
        MLFindingLabel.category,
        MLFindingLabel.severity,
        MLFindingLabel.source,
    ).all()

    # Pre-lowercase patterns once (Python's str.startswith/in are case-sensitive,
    # but ILIKE in SQL is not — so we lowercase both sides to match ILIKE semantics).
    starters_lower = [p.lower() for p in JUNK_STARTERS]
    contains_lower = [p.lower() for p in JUNK_CONTAINS]

    # Per-pattern buckets: {pattern_name: {'count': int, 'sample': str, 'ids': set}}
    # Tracking ids lets us build the union later without re-scanning.
    pattern_buckets = {}
    for p in JUNK_STARTERS:
        pattern_buckets[f'starts with "{p}"'] = {'count': 0, 'sample': '', 'matcher': ('starts', p.lower())}
    for p in JUNK_CONTAINS:
        pattern_buckets[f'contains "{p}"'] = {'count': 0, 'sample': '', 'matcher': ('contains', p.lower())}
    pattern_buckets[f'text shorter than {JUNK_SHORT_LENGTH} chars'] = {
        'count': 0, 'sample': '', 'matcher': ('short', JUNK_SHORT_LENGTH)
    }

    # Track which row ids matched any pattern (for union dedup)
    junk_ids = set()
    # Keep references to junk rows so we can sample from them at the end
    # (avoids an ORDER BY RANDOM() query which is slow on Postgres).
    junk_rows_list = []

    for row in rows:
        text = row.finding_text or ''
        text_lower = text.lower()
        matched_any = False

        # Starter patterns
        for p_name, bucket in pattern_buckets.items():
            matcher = bucket['matcher']
            if matcher[0] == 'starts':
                if text_lower.startswith(matcher[1]):
                    bucket['count'] += 1
                    if not bucket['sample']:
                        bucket['sample'] = text[:150]
                    matched_any = True
            elif matcher[0] == 'contains':
                if matcher[1] in text_lower:
                    bucket['count'] += 1
                    if not bucket['sample']:
                        bucket['sample'] = text[:150]
                    matched_any = True
            elif matcher[0] == 'short':
                if len(text) < matcher[1]:
                    bucket['count'] += 1
                    if not bucket['sample']:
                        bucket['sample'] = text
                    matched_any = True

        if matched_any:
            junk_ids.add(row.id)
            junk_rows_list.append(row)

    # Build by_pattern output, only include non-zero
    by_pattern = []
    for name, bucket in pattern_buckets.items():
        if bucket['count'] > 0:
            by_pattern.append({'name': name, 'count': bucket['count'], 'sample': bucket['sample']})
    by_pattern.sort(key=lambda x: -x['count'])

    junk_cnt = len(junk_ids)

    # Source breakdown — one pass through all rows
    src_total = {}
    src_junk = {}
    for row in rows:
        s = row.source or '(null)'
        src_total[s] = src_total.get(s, 0) + 1
        if row.id in junk_ids:
            src_junk[s] = src_junk.get(s, 0) + 1
    by_source = [
        {
            'source': s,
            'total_count': src_total[s],
            'junk_count': src_junk.get(s, 0),
            'junk_pct': round(100 * src_junk.get(s, 0) / src_total[s], 1) if src_total[s] else 0.0,
        }
        for s in src_total
    ]
    by_source.sort(key=lambda x: -x['junk_count'])

    # Category breakdown — same single pass approach
    cat_total = {}
    cat_junk = {}
    for row in rows:
        c = row.category or '(null)'
        cat_total[c] = cat_total.get(c, 0) + 1
        if row.id in junk_ids:
            cat_junk[c] = cat_junk.get(c, 0) + 1
    by_category = [
        {
            'category': c,
            'total_count': cat_total[c],
            'junk_count': cat_junk.get(c, 0),
            'junk_pct': round(100 * cat_junk.get(c, 0) / cat_total[c], 1) if cat_total[c] else 0.0,
        }
        for c in cat_total
    ]
    by_category.sort(key=lambda x: -x['junk_count'])

    # Sample 15 random junk rows (in-memory sample, not ORDER BY RANDOM)
    random.seed()
    sample_size = min(15, len(junk_rows_list))
    samples = random.sample(junk_rows_list, sample_size) if junk_rows_list else []
    sample_junk = [
        {
            'category': s.category or '',
            'severity': s.severity or '',
            'source': s.source or '',
            'text': (s.finding_text or '')[:200],
        }
        for s in samples
    ]

    return {
        'total_rows': total,
        'junk_rows': junk_cnt,
        'clean_rows': total - junk_cnt,
        'junk_pct': round(100 * junk_cnt / total, 2),
        'by_pattern': by_pattern,
        'by_source': by_source,
        'by_category': by_category,
        'sample_junk': sample_junk,
    }
