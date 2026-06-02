"""ML Training Data Audit — detect mislabeled rows for human review before bulk fixes.

Each rule scans a Postgres table and returns rows that match a "this looks wrong"
heuristic, along with a suggested correction and rationale. The admin UI surfaces
these for one-click approval/rejection — no rule auto-applies.

Design principle: rules are intentionally conservative. False positives waste
your review time but don't corrupt data. False negatives just mean some
mislabels stay in the dataset (already the status quo). So we err toward
high precision, lower recall.

Adding a new rule:
1. Write a function `_rule_<name>(limit)` returning list[dict]
2. Each dict has: id, table, finding_text, current_value, suggested_value, rationale, rule_name, action
3. Register in AUDIT_RULES below
"""

import re
from typing import List, Dict, Any


# ─────────────────────────────────────────────────────────────────────────────
# Detection rules — finding labels (category mislabels)
# ─────────────────────────────────────────────────────────────────────────────

# Map of (keyword pattern, expected_category, rationale).
# Pattern matches finding_text; if current category != expected, it's a candidate.
# Order matters — earlier patterns checked first.
CATEGORY_KEYWORDS = [
    (r'\bradon\b',                          'environmental',       'Radon is canonically environmental'),
    (r'\basbestos\b',                       'environmental',       'Asbestos is canonically environmental'),
    (r'\blead\s+(paint|pipe|service)\b',    'environmental',       'Lead paint/pipe/service is environmental'),
    (r'\bmold\b',                           'environmental',       'Mold is environmental'),
    (r'\bformaldehyde\b',                   'environmental',       'Formaldehyde is environmental'),
    (r'\bcarbon\s+monoxide\b',              'environmental',       'CO hazard is environmental'),
    (r'\b(roof|shingle|gutter|downspout|fascia|soffit|chimney\s+flashing)\b',
                                            'roof_exterior',       'Roof/exterior keyword'),
    (r'\b(siding|stucco|exterior\s+paint|trim\s+rot|exterior\s+trim)\b',
                                            'roof_exterior',       'Exterior surface keyword'),
    (r'\b(foundation|crawlspace|basement\s+wall|concrete\s+slab|footing)\b',
                                            'foundation_structure', 'Foundation/structural keyword'),
    (r'\b(electrical\s+panel|breaker|GFCI|wiring|outlet|service\s+entry)\b',
                                            'electrical',          'Electrical-system keyword'),
    (r'\b(plumbing|water\s+heater|toilet|faucet|drain|sewer|copper\s+pipe|pex)\b',
                                            'plumbing',            'Plumbing-system keyword'),
    (r'\b(hvac|furnace|air\s+conditioner|condenser|heat\s+pump|ductwork)\b',
                                            'hvac_systems',        'HVAC keyword'),
]


def _rule_category_keyword_mismatch(limit: int = 100) -> List[Dict[str, Any]]:
    """Find rows where finding_text strongly suggests a category but DB says otherwise."""
    from models import MLFindingLabel

    candidates = []
    # Pull a generous batch and filter in Python — patterns + regex don't translate
    # cleanly to SQL across Postgres/SQLite, and we're working with ~56K rows max.
    rows = MLFindingLabel.query.limit(20000).all()
    for row in rows:
        if not row.finding_text:
            continue
        text_lower = row.finding_text.lower()
        for pattern, expected_cat, rationale in CATEGORY_KEYWORDS:
            if re.search(pattern, text_lower):
                if row.category != expected_cat:
                    candidates.append({
                        'id': row.id,
                        'table': 'ml_finding_labels',
                        'finding_text': row.finding_text[:200],
                        'current_value': f'category={row.category} severity={row.severity}',
                        'suggested_value': f'category={expected_cat} severity={row.severity}',
                        'change_field': 'category',
                        'change_to': expected_cat,
                        'rationale': rationale,
                        'rule_name': 'category_keyword_mismatch',
                        'source': row.source,
                        'is_validated': row.is_validated,
                    })
                break  # first matching pattern wins
        if len(candidates) >= limit:
            break
    return candidates


def _rule_severity_critical_keywords(limit: int = 100) -> List[Dict[str, Any]]:
    """Find rows whose text contains severity-amplifying language but isn't labeled critical/major.

    Conservative: only flags VERY strong signals.
    """
    from models import MLFindingLabel

    # Strong critical indicators — cause immediate safety/structural concern
    critical_patterns = [
        r'\bactive\s+leak\b', r'\bstructural\s+failure\b', r'\bcollapse\b',
        r'\bcondemned\b', r'\bimminent\s+failure\b', r'\bgas\s+leak\b',
        r'\barcing\b', r'\belectrocution\s+hazard\b', r'\bsewage\s+backup\b',
        r'\bload-bearing\b.*\b(crack|damage|failure|removed)\b',
        r'\b(severe|major)\s+structural\b', r'\bsinking\s+foundation\b',
        r'\bhorizontal\s+crack\b.*\bfoundation\b',
    ]

    candidates = []
    rows = MLFindingLabel.query.filter(
        MLFindingLabel.severity.in_(['minor', 'moderate'])
    ).limit(20000).all()

    for row in rows:
        if not row.finding_text:
            continue
        text_lower = row.finding_text.lower()
        for pattern in critical_patterns:
            if re.search(pattern, text_lower):
                candidates.append({
                    'id': row.id,
                    'table': 'ml_finding_labels',
                    'finding_text': row.finding_text[:200],
                    'current_value': f'category={row.category} severity={row.severity}',
                    'suggested_value': f'category={row.category} severity=critical',
                    'change_field': 'severity',
                    'change_to': 'critical',
                    'rationale': f'Text matches critical-severity pattern: {pattern}',
                    'rule_name': 'severity_critical_keywords',
                    'source': row.source,
                    'is_validated': row.is_validated,
                })
                break
        if len(candidates) >= limit:
            break
    return candidates


def _rule_severity_minor_keywords(limit: int = 100) -> List[Dict[str, Any]]:
    """Find rows whose text contains explicit cosmetic/minor language but isn't labeled minor."""
    from models import MLFindingLabel

    minor_patterns = [
        r'\bcosmetic\s+only\b', r'\bcosmetic\s+issue\b',
        r'\bminor\s+(scratch|wear|peeling|chip|stain)\b',
        r'\baesthetic\b', r'\bmonitor\s+for\b', r'\bno\s+immediate\b',
        r'\btouch[\s-]?up\b', r'\bnormal\s+wear\b',
    ]

    candidates = []
    rows = MLFindingLabel.query.filter(
        MLFindingLabel.severity.in_(['major', 'critical'])
    ).limit(20000).all()

    for row in rows:
        if not row.finding_text:
            continue
        text_lower = row.finding_text.lower()
        for pattern in minor_patterns:
            if re.search(pattern, text_lower):
                candidates.append({
                    'id': row.id,
                    'table': 'ml_finding_labels',
                    'finding_text': row.finding_text[:200],
                    'current_value': f'category={row.category} severity={row.severity}',
                    'suggested_value': f'category={row.category} severity=minor',
                    'change_field': 'severity',
                    'change_to': 'minor',
                    'rationale': f'Text indicates cosmetic/minor: {pattern}',
                    'rule_name': 'severity_minor_keywords',
                    'source': row.source,
                    'is_validated': row.is_validated,
                })
                break
        if len(candidates) >= limit:
            break
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# Detection rules — cost data
# ─────────────────────────────────────────────────────────────────────────────

def _rule_cost_panel_inflation(limit: int = 100) -> List[Dict[str, Any]]:
    """Find 'electrical panel' rows priced > $10K — almost certainly a misclassified
    full-rewire job that should be filtered or re-categorized.

    Real residential panel replacement: $1,500-$4,000.
    Anything > $10K is either (a) a full rewire, (b) a multi-unit building, or
    (c) a permit description that says 'panel' but covers much more work.
    """
    from models import MLCostData

    candidates = []
    # Match anything mentioning panel/breaker box but priced suspiciously high
    rows = MLCostData.query.filter(
        MLCostData.cost_mid > 10000
    ).limit(5000).all()

    panel_pattern = re.compile(r'\b(electrical\s+panel|breaker\s+box|service\s+panel|main\s+panel)\b', re.I)

    for row in rows:
        if not row.finding_text or not panel_pattern.search(row.finding_text):
            continue
        candidates.append({
            'id': row.id,
            'table': 'ml_cost_data',
            'finding_text': row.finding_text[:200],
            'current_value': f'${row.cost_mid:,.0f} (category={row.category}, source={row.source})',
            'suggested_value': 'EXCLUDE from training OR re-categorize as full_rewire',
            'change_field': 'action',
            'change_to': 'exclude_from_training',
            'rationale': f'Panel replacement priced ${row.cost_mid:,.0f}, real residential range $1.5K-$4K. Likely a full rewire mislabeled as panel.',
            'rule_name': 'cost_panel_inflation',
            'source': row.source,
        })
        if len(candidates) >= limit:
            break
    return candidates


def _rule_cost_outlier_per_category(limit: int = 50) -> List[Dict[str, Any]]:
    """Find cost rows that are >5x the median for their category — possible bad data."""
    from models import MLCostData
    from sqlalchemy import func

    candidates = []
    # Compute median cost per category. SQLite doesn't have percentile_cont, so use a
    # crude approach: cost_mid > 5x mean as proxy for 'extreme outlier'.
    cat_stats = {}
    for row in MLCostData.query.with_entities(
        MLCostData.category, func.avg(MLCostData.cost_mid).label('avg_cost')
    ).group_by(MLCostData.category).all():
        if row.category and row.avg_cost:
            cat_stats[row.category] = row.avg_cost

    # Find outliers
    for cat, avg in cat_stats.items():
        threshold = avg * 5
        outliers = MLCostData.query.filter(
            MLCostData.category == cat,
            MLCostData.cost_mid > threshold
        ).limit(20).all()
        for row in outliers:
            candidates.append({
                'id': row.id,
                'table': 'ml_cost_data',
                'finding_text': row.finding_text[:200],
                'current_value': f'${row.cost_mid:,.0f} (category={row.category}, avg ${avg:,.0f})',
                'suggested_value': 'EXCLUDE from training (extreme outlier)',
                'change_field': 'action',
                'change_to': 'exclude_from_training',
                'rationale': f'${row.cost_mid:,.0f} is >5x category average (${avg:,.0f}). Likely commercial/multi-unit.',
                'rule_name': 'cost_outlier_per_category',
                'source': row.source,
            })
            if len(candidates) >= limit:
                return candidates
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# Rule registry
# ─────────────────────────────────────────────────────────────────────────────

AUDIT_RULES = [
    {
        'name': 'category_keyword_mismatch',
        'description': 'Finding text contains keywords for category X but DB says category Y (radon/asbestos/etc → environmental)',
        'fn': _rule_category_keyword_mismatch,
        'expected_severity': 'high — most matches will be real mislabels',
    },
    {
        'name': 'severity_critical_keywords',
        'description': 'Text contains critical-severity language (active leak, structural failure, gas leak, etc) but DB labels minor/moderate',
        'fn': _rule_severity_critical_keywords,
        'expected_severity': 'high — addresses the 0.09% critical-class problem directly',
    },
    {
        'name': 'severity_minor_keywords',
        'description': 'Text says cosmetic/aesthetic/touch-up but DB labels major/critical',
        'fn': _rule_severity_minor_keywords,
        'expected_severity': 'medium — fewer matches expected',
    },
    {
        'name': 'cost_panel_inflation',
        'description': 'Electrical-panel rows priced >$10K — likely full-rewire jobs misclassified',
        'fn': _rule_cost_panel_inflation,
        'expected_severity': 'high — directly fixes the failing inference test',
    },
    {
        'name': 'cost_outlier_per_category',
        'description': 'Cost rows >5x their category average — possible commercial/multi-unit data leaked into residential training',
        'fn': _rule_cost_outlier_per_category,
        'expected_severity': 'medium — review case by case',
    },
]


def run_all_audits(limit_per_rule: int = 100) -> Dict[str, Any]:
    """Run every rule and return a summary + grouped candidates.

    Result structure:
        {
            'summary': {
                'total_candidates': N,
                'by_rule': {rule_name: count},
                'by_table': {table: count},
            },
            'rules': [
                {'name': ..., 'description': ..., 'count': N, 'candidates': [...]},
                ...
            ]
        }
    """
    rule_results = []
    summary = {'total_candidates': 0, 'by_rule': {}, 'by_table': {}}

    for spec in AUDIT_RULES:
        try:
            candidates = spec['fn'](limit=limit_per_rule)
        except Exception as e:
            candidates = []
            import logging
            logging.warning(f"Audit rule {spec['name']} failed: {e}")

        rule_results.append({
            'name': spec['name'],
            'description': spec['description'],
            'expected_severity': spec['expected_severity'],
            'count': len(candidates),
            'candidates': candidates,
        })
        summary['total_candidates'] += len(candidates)
        summary['by_rule'][spec['name']] = len(candidates)
        for c in candidates:
            tbl = c.get('table', 'unknown')
            summary['by_table'][tbl] = summary['by_table'].get(tbl, 0) + 1

    return {'summary': summary, 'rules': rule_results}


def apply_correction(table: str, row_id: int, change_field: str, change_to: str) -> Dict[str, Any]:
    """Apply a single approved correction. Returns the before/after for audit log."""
    from models import db, MLFindingLabel, MLCostData

    if table == 'ml_finding_labels':
        row = MLFindingLabel.query.get(row_id)
        if not row:
            return {'ok': False, 'error': 'Row not found'}

        if change_field == 'category':
            before = row.category
            row.original_category = row.original_category or before
            row.category = change_to
            row.is_validated = True
            db.session.commit()
            return {'ok': True, 'before': before, 'after': change_to, 'field': 'category'}

        elif change_field == 'severity':
            before = row.severity
            row.original_severity = row.original_severity or before
            row.severity = change_to
            row.is_validated = True
            db.session.commit()
            return {'ok': True, 'before': before, 'after': change_to, 'field': 'severity'}

    elif table == 'ml_cost_data':
        row = MLCostData.query.get(row_id)
        if not row:
            return {'ok': False, 'error': 'Row not found'}

        if change_field == 'action' and change_to == 'exclude_from_training':
            # Mark cost row as excluded by setting source to 'EXCLUDED:<original>'
            # so it survives in DB but training queries can filter it out.
            if not row.source.startswith('EXCLUDED:'):
                before = row.source
                row.source = f'EXCLUDED:{before}'
                db.session.commit()
                return {'ok': True, 'before': before, 'after': row.source, 'field': 'source'}
            return {'ok': True, 'before': row.source, 'after': row.source, 'field': 'source', 'note': 'already excluded'}

    return {'ok': False, 'error': f'Unsupported correction: {table}.{change_field}={change_to}'}
