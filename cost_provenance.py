"""
cost_provenance.py — measure the repair-cost baseline-fallback rate by category.

The repair-cost engine prices each inspection finding with the ML cost model
only when its confidence clears the live threshold (COST_CONFIDENCE_THRESHOLD,
0.85). Below that it "defers to baseline" — a category prior, which is exactly
what a too-wide range is. So every wide line item is a finding the model could
not price confidently. This module records that decision per finding (write
side) and aggregates it by category (read side) so we can see WHICH defect
classes the model is blind on — the ranked list of what to fix first.

Write side: record_pricing_provenance() is called once per analysis with the
per-finding outcomes. It never raises — telemetry must not break analysis.

Read side: baseline_fallback_by_category() powers the admin panel.

Provenance is forward-looking: it reflects analyses that ran after the
instrumentation shipped. Findings aren't persisted with a cost source, so there
is no honest way to backfill older analyses.
"""

import logging

logger = logging.getLogger(__name__)

# Outcomes that represent an actual model pricing decision (the denominator of
# the fallback rate). 'doc' and 'preset' are real outcomes but not model
# decisions, so they're recorded yet excluded from the rate.
_DECISION_SOURCES = ('ml', 'baseline_lowconf', 'baseline_noml')
_BASELINE_SOURCES = ('baseline_lowconf', 'baseline_noml')
_VALID_SOURCES = _DECISION_SOURCES + ('doc', 'preset')


def record_pricing_provenance(records, analysis_id=None):
    """Persist a batch of per-finding pricing outcomes. Safe by construction.

    records: iterable of dicts, each with keys:
        category   (str|None), severity (str|None),
        source     (one of _VALID_SOURCES),
        confidence (float|None), threshold (float|None)

    Returns the number of rows written (0 on any failure). Never raises.
    """
    if not records:
        return 0
    try:
        from app import db
        from models import CostPricingProvenance
    except Exception as e:
        logger.debug(f"[cost_provenance] db/model unavailable, skipping: {e}")
        return 0

    rows = []
    for r in records:
        try:
            source = (r.get('source') or '').strip()
            if source not in _VALID_SOURCES:
                continue
            cat = r.get('category')
            cat = (cat[:50] if isinstance(cat, str) else None)
            sev = r.get('severity')
            sev = (sev[:20] if isinstance(sev, str) else None)
            conf = r.get('confidence')
            conf = float(conf) if conf is not None else None
            thr = r.get('threshold')
            thr = float(thr) if thr is not None else None
            rows.append(CostPricingProvenance(
                analysis_id=analysis_id,
                category=cat, severity=sev, source=source,
                confidence=conf, threshold=thr,
            ))
        except Exception:
            continue

    if not rows:
        return 0
    try:
        db.session.bulk_save_objects(rows)
        db.session.commit()
        return len(rows)
    except Exception as e:
        logger.warning(f"[cost_provenance] write failed (non-fatal): {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return 0


def aggregate_provenance(records, window_days=None):
    """Pure aggregation over provenance records — no DB, no Flask.

    records: iterable of objects with attributes .category, .severity, .source,
    .confidence, .threshold, .created_at. (Real model rows or test stubs.)

    Returns the report dict described on baseline_fallback_by_category.
    """
    out = {
        'window_days': window_days,
        'instrumented': False,
        'threshold': None,
        'totals': {'priced': 0, 'ml': 0, 'baseline': 0,
                   'fallback_rate': None, 'doc': 0, 'preset': 0},
        'by_category': [],
    }
    records = list(records or [])
    if not records:
        return out

    out['instrumented'] = True
    cats = {}
    tot = out['totals']
    latest_threshold = None
    latest_at = None

    for r in records:
        cat = getattr(r, 'category', None) or '(uncategorized)'
        c = cats.setdefault(cat, {'ml': 0, 'baseline': 0, 'doc': 0,
                                  'preset': 0, 'conf_sum': 0.0, 'conf_n': 0})
        thr = getattr(r, 'threshold', None)
        cat_at = getattr(r, 'created_at', None)
        if thr is not None and (latest_at is None or (cat_at and cat_at >= latest_at)):
            latest_threshold = thr
            latest_at = cat_at
        source = getattr(r, 'source', None)
        conf = getattr(r, 'confidence', None)
        if source == 'ml':
            c['ml'] += 1
            tot['ml'] += 1
            if conf is not None:
                c['conf_sum'] += conf
                c['conf_n'] += 1
        elif source in _BASELINE_SOURCES:
            c['baseline'] += 1
            tot['baseline'] += 1
        elif source == 'doc':
            c['doc'] += 1
            tot['doc'] += 1
        elif source == 'preset':
            c['preset'] += 1
            tot['preset'] += 1

    out['threshold'] = latest_threshold
    tot['priced'] = tot['ml'] + tot['baseline']
    tot['fallback_rate'] = (tot['baseline'] / tot['priced']) if tot['priced'] else None

    rows = []
    for cat, c in cats.items():
        priced = c['ml'] + c['baseline']
        if priced == 0:
            continue  # only doc/preset for this category — no model decision to rate
        rows.append({
            'category': cat,
            'priced': priced,
            'ml': c['ml'],
            'baseline': c['baseline'],
            'fallback_rate': c['baseline'] / priced,
            'avg_ml_confidence': round(c['conf_sum'] / c['conf_n'], 3) if c['conf_n'] else None,
        })

    # Worst first: highest fallback rate, then highest volume — the ranked
    # "fix these categories first" list.
    rows.sort(key=lambda x: (-x['fallback_rate'], -x['priced']))
    out['by_category'] = rows
    return out


def baseline_fallback_by_category(db_session=None, window_days=90):
    """Aggregate the fallback rate by category (DB-backed).

    Returns a dict:
      {
        'window_days': int | None,         # None = all time
        'instrumented': bool,              # any rows at all?
        'threshold': float | None,         # most recent threshold seen
        'totals': {
            'priced': int,                 # ml + baseline (decision rows)
            'ml': int, 'baseline': int,
            'fallback_rate': float | None, # baseline / priced
            'doc': int, 'preset': int,     # informational, excluded from rate
        },
        'by_category': [                   # worst (highest fallback) first
            {'category', 'priced', 'ml', 'baseline', 'fallback_rate',
             'avg_ml_confidence' | None}, ...
        ],
      }
    """
    empty = aggregate_provenance([], window_days=window_days)
    try:
        from models import CostPricingProvenance
        if db_session is None:
            from app import db
            db_session = db.session
    except Exception as e:
        logger.debug(f"[cost_provenance] aggregate unavailable: {e}")
        return empty

    try:
        q = db_session.query(CostPricingProvenance)
        if window_days:
            from datetime import datetime, timedelta
            cutoff = datetime.utcnow() - timedelta(days=int(window_days))
            q = q.filter(CostPricingProvenance.created_at >= cutoff)
        records = q.all()
    except Exception as e:
        logger.warning(f"[cost_provenance] query failed: {e}")
        return empty

    return aggregate_provenance(records, window_days=window_days)
