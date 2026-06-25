"""
telemetry_integrity.py — v5.89.210

In-DB integrity checks for the funnel event store (GTMFunnelEvent). These prove
whether the numbers the admin funnel shows are internally consistent — WITHOUT
any external connector (no GA4, no Stripe, no Reddit/Google APIs). They catch the
class of bug that a self-referential event store cannot see in itself: a missing
or double-firing track() call, attribution that doesn't bucket, test/internal
traffic leaking past exclusion, and impossible funnel shapes.

Design: the core is a PURE function, build_integrity_report(rows, test_user_ids),
that takes raw in-window events and returns a structured report. No Flask, no DB,
no I/O — so it is exhaustively unit-testable with synthetic rows. The admin route
does the one DB query and hands the rows here.

Why these specific checks (each grounded in the real schema, not invented):
- Start→complete pairs counted by DISTINCT SESSION are the only ironclad
  monotonic invariant. Raw event counts are NOT monotonic across the funnel
  because multi-fire stages (chat messages) legitimately exceed their parent, so
  we never assert raw-count monotonicity. A session that COMPLETED without a
  recorded START is impossible and proves a broken/mis-ordered emitter.
- "Once per session" stages firing twice for the same session = double-count.
  On purchase/signup that is a revenue/identity error, not a cosmetic one.
- NULL/empty source = an event that buckets to no channel, i.e. invisible in
  every channel report — silent undercount of whatever channel drove it.
- Events from a test user_id present in the raw table = the WRITE-time skip
  leaked (read-time still excludes them, but the gap is real drift to fix).
- Internal/tooling sources on anonymous events (tagassistant, staging, localhost)
  can't be excluded by user_id, so they inflate 'visit' — the exact pollution
  seen in GA4.
- A CRITICAL stage at zero over a real window is the "1 key event" symptom: the
  hook almost certainly isn't firing.
"""
from datetime import datetime

# --- Product-grounded stage facts (from the real track() emitters) -----------
START_COMPLETE_PAIRS = [
    ('risk_check_start', 'risk_check_complete'),
    ('analysis_started', 'analysis_complete'),
    ('truth_check_start', 'truth_check_complete'),
    ('quick_check_start', 'quick_check_complete'),
]

ONCE_PER_SESSION_STAGES = {
    'signup', 'purchase',
    'risk_check_complete', 'analysis_complete',
    'truth_check_complete', 'quick_check_complete',
    'risk_share_created',
}

# Zero of these over a real window almost certainly means a broken hook.
CRITICAL_STAGES = ('signup', 'purchase', 'analysis_complete')

KNOWN_STAGES = [
    'visit', 'risk_check_start', 'risk_check_complete', 'risk_chat_message',
    'risk_share_created', 'risk_share_view', 'signup', 'address_entered',
    'analysis_started', 'analysis_complete', 'comparison_started', 'pricing_view',
    'purchase', 'try_landed', 'try_started', 'try_findings_shown',
    'try_chat_message', 'truth_check_start', 'truth_check_complete',
    'quick_check_start', 'quick_check_complete', 'email_capture',
    'negotiation_doc_generated', 'contractor_lead_submitted',
]

# Substrings that mark a source as internal tooling / non-user traffic. These ride
# in on anonymous events (user_id NULL) so the user_id-based exclusion can't catch
# them — they have to be caught here.
INTERNAL_SOURCE_SIGNALS = (
    'tagassistant', 'lightning.force', 'localhost', '127.0.0.1',
    'ngrok', 'offerwise-staging', '.local', 'gtm-msr',
)

_STATUS_RANK = {'pass': 0, 'info': 0, 'warn': 1, 'fail': 2}


def _g(row, key):
    """Read a field from a row that may be a dict or an ORM object."""
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _check(cid, title, status, summary, detail=None):
    return {'id': cid, 'title': title, 'status': status,
            'summary': summary, 'detail': detail or {}}


def _start_complete_check(rows):
    """Sessions that completed a sub-funnel with no recorded start = impossible."""
    by_stage_sessions = {}
    for r in rows:
        sid = _g(r, 'session_id')
        if sid is None:
            continue
        by_stage_sessions.setdefault(_g(r, 'stage'), set()).add(sid)

    offenders = []
    for start, complete in START_COMPLETE_PAIRS:
        starts = by_stage_sessions.get(start, set())
        completes = by_stage_sessions.get(complete, set())
        orphans = completes - starts
        if orphans:
            offenders.append({
                'pair': f'{start} -> {complete}',
                'orphan_sessions': len(orphans),
                'started_sessions': len(starts),
                'completed_sessions': len(completes),
                'examples': sorted(str(s) for s in orphans)[:5],
            })
    if offenders:
        worst = max(o['orphan_sessions'] for o in offenders)
        return _check(
            'start_complete', 'Start before complete',
            'fail',
            f'{len(offenders)} sub-funnel(s) have completions with no matching '
            f'start (worst: {worst} sessions). A completion without a start means '
            'an emitter is missing, mis-ordered, or attributing to the wrong session.',
            {'violations': offenders})
    return _check('start_complete', 'Start before complete', 'pass',
                  'Every recorded completion has a matching start (by session).')


def _duplicate_check(rows):
    """Once-per-session stages firing >1x for the same session = double-count."""
    seen = {}  # (stage, key) -> count, where key is session_id or 'user:<id>'
    for r in rows:
        stage = _g(r, 'stage')
        if stage not in ONCE_PER_SESSION_STAGES:
            continue
        sid = _g(r, 'session_id')
        uid = _g(r, 'user_id')
        key = sid if sid is not None else (f'user:{uid}' if uid is not None else None)
        if key is None:
            continue
        seen[(stage, key)] = seen.get((stage, key), 0) + 1

    dupes = {k: c for k, c in seen.items() if c > 1}
    if not dupes:
        return _check('duplicates', 'No double-fired events', 'pass',
                      'No once-per-session stage fired twice for the same session.')

    per_stage = {}
    for (stage, _key), c in dupes.items():
        s = per_stage.setdefault(stage, {'groups': 0, 'extra_events': 0})
        s['groups'] += 1
        s['extra_events'] += c - 1
    revenue_hit = any(st in ('purchase', 'signup') for st in per_stage)
    return _check(
        'duplicates', 'No double-fired events',
        'fail' if revenue_hit else 'warn',
        ('Double-firing on ' + ', '.join(sorted(per_stage)) + '. '
         + ('purchase/signup duplicates inflate revenue and user counts.'
            if revenue_hit else 'These over-count their stage.')),
        {'per_stage': per_stage})


def _source_integrity_check(rows):
    """Events whose source buckets to nothing are invisible in channel reports."""
    total = 0
    null_src = 0
    for r in rows:
        total += 1
        src = (_g(r, 'source') or '').strip()
        if not src:
            null_src += 1
    if null_src == 0:
        return _check('source_bucketing', 'Every event has a channel', 'pass',
                      'All events carry a non-empty source.')
    pct = round(null_src / total * 100, 1) if total else 0
    return _check(
        'source_bucketing', 'Every event has a channel',
        'warn',
        f'{null_src} of {total} events ({pct}%) have no source — they vanish from '
        'every channel breakdown, undercounting whatever drove them.',
        {'null_source_events': null_src, 'total_events': total, 'pct': pct})


def _exclusion_leak_check(rows, test_user_ids):
    """Events from a test user that the WRITE-time skip should have dropped."""
    test_ids = set(test_user_ids or ())
    leaked = [r for r in rows if _g(r, 'user_id') in test_ids and _g(r, 'user_id') is not None]
    if not leaked:
        return _check('test_write_leak', 'Test accounts skipped at write', 'pass',
                      'No events from known test accounts are present in the raw table.')
    users = {_g(r, 'user_id') for r in leaked}
    return _check(
        'test_write_leak', 'Test accounts skipped at write',
        'warn',
        f'{len(leaked)} raw events from {len(users)} test account(s) reached the '
        'table. The read-time funnel still excludes them, but the write-time skip '
        'is leaking — the two exclusion paths have drifted.',
        {'leaked_events': len(leaked), 'test_users': len(users)})


def _internal_source_check(rows):
    """Anonymous events from internal tooling can't be excluded by user_id."""
    hits = {}
    for r in rows:
        if _g(r, 'user_id') is not None:
            continue
        src = (_g(r, 'source') or '').lower()
        for sig in INTERNAL_SOURCE_SIGNALS:
            if sig in src:
                hits[src] = hits.get(src, 0) + 1
                break
    if not hits:
        return _check('internal_source', 'No internal traffic in funnel', 'pass',
                      'No anonymous events match internal/tooling sources.')
    total = sum(hits.values())
    return _check(
        'internal_source', 'No internal traffic in funnel',
        'warn',
        f'{total} anonymous events come from internal/tooling sources '
        f'({", ".join(sorted(hits))}). These inflate visit counts and cannot be '
        'removed by the user_id-based exclusion — they need a source-level filter.',
        {'by_source': hits, 'total': total})


def _coverage_check(rows):
    """Known stages at zero — especially CRITICAL ones (the '1 key event' symptom)."""
    counts = {}
    for r in rows:
        st = _g(r, 'stage')
        counts[st] = counts.get(st, 0) + 1
    zero_stages = [s for s in KNOWN_STAGES if counts.get(s, 0) == 0]
    zero_critical = [s for s in CRITICAL_STAGES if counts.get(s, 0) == 0]
    if zero_critical:
        return _check(
            'coverage', 'Critical stages are firing',
            'fail',
            'Critical stage(s) at ZERO over the window: ' + ', '.join(zero_critical)
            + '. With real traffic this means the hook is not firing — the same '
            'failure mode as GA4 recording one key event in six months.',
            {'zero_critical': zero_critical, 'zero_stages': zero_stages,
             'stage_counts': counts})
    status = 'warn' if zero_stages else 'pass'
    return _check(
        'coverage', 'Critical stages are firing', status,
        ('All critical stages fired.' if not zero_stages
         else 'Critical stages fired, but these known stages are silent: '
              + ', '.join(zero_stages) + ' (no traffic, or no emitter).'),
        {'zero_stages': zero_stages, 'stage_counts': counts})


def _fanout_check(rows):
    """Raw events vs distinct sessions per stage — a high ratio on a non-repeatable
    stage suggests over-firing. Informational; multi-fire stages are expected."""
    raw = {}
    sess = {}
    for r in rows:
        st = _g(r, 'stage')
        raw[st] = raw.get(st, 0) + 1
        sid = _g(r, 'session_id')
        if sid is not None:
            sess.setdefault(st, set()).add(sid)
    ratios = {}
    for st in ONCE_PER_SESSION_STAGES:
        s = len(sess.get(st, set()))
        if s and raw.get(st, 0) / s >= 1.5:
            ratios[st] = {'events': raw[st], 'sessions': s,
                          'ratio': round(raw[st] / s, 2)}
    if ratios:
        return _check(
            'fanout', 'Events-per-session sane', 'warn',
            'Once-per-session stages with >1.5 events per session (possible '
            'over-firing): ' + ', '.join(sorted(ratios)) + '.',
            {'ratios': ratios})
    return _check('fanout', 'Events-per-session sane', 'pass',
                  'No once-per-session stage shows event fan-out.')


def build_integrity_report(rows, test_user_ids=None, *, days=None, now=None):
    """Run all in-DB integrity checks over raw in-window events.

    rows: iterable of GTMFunnelEvent rows (ORM objects or dicts) — the RAW events
          in the window, with NO exclusion applied (we measure the leaks).
    test_user_ids: ids known to be test/persona/e2e (by email domain).
    Returns: {period, event_count, overall, checks: [...]}.
    """
    rows = list(rows)
    checks = [
        _coverage_check(rows),
        _start_complete_check(rows),
        _duplicate_check(rows),
        _source_integrity_check(rows),
        _exclusion_leak_check(rows, test_user_ids),
        _internal_source_check(rows),
        _fanout_check(rows),
    ]
    overall = max((c['status'] for c in checks), key=lambda s: _STATUS_RANK[s])
    return {
        'period': {'days': days, 'generated_at': (now or datetime.utcnow()).isoformat()},
        'event_count': len(rows),
        'overall': overall,
        'checks': checks,
    }
