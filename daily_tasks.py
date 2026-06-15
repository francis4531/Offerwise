"""
Daily Tasks — founder daily-driver reminder (v5.89.128)
========================================================
A permanent, reusable daily checklist that pairs your standing recurring
tasks (outreach, follow-ups, shipping) with LIVE numbers computed fresh each
morning, so the reminder tells you what actually needs doing today.

Two delivery surfaces, one data source (build_daily_tasks_data):
  - Email each morning at 08:00 America/Los_Angeles (agentic_monitor cron job
    _job_daily_tasks_email -> send_daily_tasks_email).
  - Dashboard panel on /admin (Today view) via /api/admin/daily-tasks.

Storage uses the existing SystemSetting KV store (no migration):
  daily_tasks_extra            JSON list[str]  — admin-added custom tasks
  daily_tasks_done:<YYYYMMDD>  JSON list[str]  — completed task ids for that day
                                                  (date-scoped, so checkoff
                                                  naturally resets each day)
  daily_tasks_email_to         str             — recipient (default ADMIN_EMAIL)
  daily_tasks_email_enabled    '1' | '0'       — morning email on/off (default on)

Nothing here raises to the caller; every metric degrades to None on error so a
single bad query can't break the panel or the email.
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'hello@getofferwise.ai')

# Daily-tasks reminder goes to the founder by default. Env-overridable
# (DAILY_TASKS_EMAIL_TO / FOUNDER_EMAIL), and a runtime SystemSetting override
# still wins over this (see get_email_to).
DEFAULT_EMAIL_TO = (os.environ.get('DAILY_TASKS_EMAIL_TO')
                    or os.environ.get('FOUNDER_EMAIL')
                    or 'francis@getofferwise.ai')

# Fixed recurring tasks. {metric} placeholders are filled from live numbers at
# build time; missing/failed metrics fall back to '?' so a label still renders.
# Task labels + scoring now live in _customer_tasks() and _product_tasks()
# (v5.89.179): the daily list is a ranked two-lane mix, not a fixed template list.

# Where each task is actually executed in the admin app. The panel turns these
# into a "Go →" control (in-app showView for views, new tab for pages) and the
# email turns them into deep links. Custom tasks have no destination.
#   view  -> admin.html showView() target (same SPA)
#   anchor-> element id to scroll to after the view loads
#   page  -> a standalone admin page (opened directly)
#   hash  -> in-page anchor on that page
TASK_DESTS = {
    'drip':     {'view': 'analytics', 'anchor': 'dripCampaignCard'},
    'outreach': {'view': 'outreach'},
    'followup': {'page': '/admin/insights', 'hash': 'journeys'},
    'insights': {'page': '/admin/insights'},
    'ads':      {'view': 'adperf'},
    'ship':     None,
}


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"daily_tasks metric error (non-fatal): {e}")
        return default


def _today_key(when=None):
    return (when or datetime.utcnow()).strftime('%Y%m%d')


def _extra_id(text):
    return 'extra:' + hashlib.sha1(text.strip().encode('utf-8')).hexdigest()[:8]


# ── persisted bits (SystemSetting) ───────────────────────────────────────────
def get_extra_tasks():
    from models import SystemSetting
    raw = SystemSetting.get('daily_tasks_extra', None)
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return [str(t).strip() for t in val if str(t).strip()]
    except Exception:
        return []


def set_extra_tasks(tasks, updated_by=None):
    """Persist the custom task list. Caps total serialized length so it stays
    inside the SystemSetting value column (String(500))."""
    from models import SystemSetting
    clean = [str(t).strip()[:120] for t in tasks if str(t).strip()][:8]
    payload = json.dumps(clean)
    if len(payload) > 480:
        # Trim from the end until it fits.
        while clean and len(json.dumps(clean)) > 480:
            clean.pop()
        payload = json.dumps(clean)
    SystemSetting.set('daily_tasks_extra', payload, updated_by=updated_by)
    return clean


def get_done_ids(when=None):
    from models import SystemSetting
    raw = SystemSetting.get('daily_tasks_done:' + _today_key(when), None)
    if not raw:
        return []
    try:
        return [str(x) for x in json.loads(raw)]
    except Exception:
        return []


def set_task_done(task_id, done, when=None, updated_by=None):
    from models import SystemSetting
    ids = set(get_done_ids(when))
    if done:
        ids.add(str(task_id))
    else:
        ids.discard(str(task_id))
    SystemSetting.set('daily_tasks_done:' + _today_key(when),
                      json.dumps(sorted(ids)), updated_by=updated_by)
    return sorted(ids)


def get_email_to():
    from models import SystemSetting
    return SystemSetting.get('daily_tasks_email_to', None) or DEFAULT_EMAIL_TO


def email_enabled():
    from models import SystemSetting
    return SystemSetting.get('daily_tasks_email_enabled', '1') != '0'


# ── live metrics ─────────────────────────────────────────────────────────────
def _compute_metrics():
    """Return a dict of live numbers. Each is computed independently and
    defensively so one failure doesn't sink the rest."""
    from models import db, User, Analysis, EmailSendLog
    from funnel_tracker import is_test_account
    now = datetime.utcnow()

    def _drip_due():
        from drip_campaign import _drip_min_hours, MAX_DRIP_STEP
        users = User.query.filter(
            ~User.email.endswith('@persona.offerwise.ai'),
            ~User.email.endswith('@test.offerwise.ai'),
        ).all()
        due = 0
        for u in users:
            if is_test_account(u):
                continue
            if bool(getattr(u, 'drip_completed', False)):
                continue
            if getattr(u, 'email_unsubscribed', False):
                continue
            step = getattr(u, 'drip_step', 0) or 0
            if step >= MAX_DRIP_STEP or not u.created_at:
                continue
            try:
                min_hours = _drip_min_hours(step + 1)
            except Exception:
                continue
            if u.created_at + timedelta(hours=min_hours) <= now:
                due += 1
        return due

    def _new_signups():
        users = User.query.filter(User.created_at >= now - timedelta(hours=24)).all()
        return sum(1 for u in users if not is_test_account(u))

    def _active_7d():
        users = User.query.filter(User.last_login >= now - timedelta(days=7)).all()
        return sum(1 for u in users if not is_test_account(u))

    def _one_and_done():
        counts = {}
        for (uid,) in db.session.query(Analysis.user_id).filter(
                Analysis.user_id.isnot(None)).all():
            counts[uid] = counts.get(uid, 0) + 1
        single_ids = [uid for uid, c in counts.items() if c == 1]
        if not single_ids:
            return 0
        users = User.query.filter(User.id.in_(single_ids)).all()
        return sum(1 for u in users if not is_test_account(u))

    def _mail_24h():
        since = now - timedelta(hours=24)
        rows = EmailSendLog.query.filter(EmailSendLog.ts >= since).all()
        sent = len(rows)
        failed = sum(1 for r in rows if not r.success)
        return sent, failed

    sent, failed = _safe(_mail_24h, (None, None)) or (None, None)
    return {
        'drip_due': _safe(_drip_due),
        'new_signups': _safe(_new_signups),
        'active_7d': _safe(_active_7d),
        'one_and_done': _safe(_one_and_done),
        'mail_sent': sent,
        'mail_failed': failed,
    }


def _fmt(v):
    return '?' if v is None else str(v)


_STAGE_LABELS = {
    'try_landed': 'landed on /try',
    'try_started': 'started a document',
    'try_findings_shown': 'saw findings',
    'risk_check_start': 'started a scan',
    'risk_check_complete': 'got a scan result',
    'signup': 'signed up',
    'purchase': 'paid',
}

# Ordered funnel ladders, real stage names only. The leak detector walks each
# and surfaces the single worst consecutive drop that has enough upstream volume.
_FUNNEL_LADDERS = [
    ('on-ramp', ['try_landed', 'try_started', 'try_findings_shown']),
    ('risk-check', ['risk_check_start', 'risk_check_complete']),
]


def _stage_label(stage):
    return _STAGE_LABELS.get(stage, stage.replace('_', ' '))


def _compute_product_signals():
    """Live product-health numbers — where the funnel leaks, what's broken, and
    how the share loop converts. Each is computed defensively so one failure
    doesn't sink the rest. Mirrors _compute_metrics for the build side."""
    from sqlalchemy import func
    from models import db, GTMFunnelEvent, Bug, SharedRiskCheck
    now = datetime.utcnow()
    since = now - timedelta(days=7)

    def _leak():
        rows = (db.session.query(
                    GTMFunnelEvent.stage,
                    func.count(func.distinct(GTMFunnelEvent.session_id)))
                .filter(GTMFunnelEvent.created_at >= since,
                        GTMFunnelEvent.session_id.isnot(None))
                .group_by(GTMFunnelEvent.stage).all())
        counts = {s: int(c or 0) for s, c in rows}
        MIN_N = 15
        worst = None  # (drop_pct, from_stage, to_stage, upstream_n, downstream_n)
        for _name, ladder in _FUNNEL_LADDERS:
            for a, b in zip(ladder, ladder[1:]):
                na, nb = counts.get(a, 0), counts.get(b, 0)
                if na < MIN_N or nb > na:
                    continue
                drop = (na - nb) / na * 100.0
                if drop <= 0:
                    continue
                if worst is None or drop > worst[0]:
                    worst = (drop, a, b, na, nb)
        return worst

    def _bugs():
        q = Bug.query.filter(Bug.status == 'open')
        total = q.count()
        if not total:
            return (0, None, 0, None)
        oldest = q.order_by(Bug.created_at.asc()).first()
        age_d = (now - oldest.created_at).days if oldest and oldest.created_at else 0
        return (total, (oldest.title if oldest else None), age_d,
                (oldest.severity if oldest else None))

    def _loop():
        shares = SharedRiskCheck.query.filter(
            SharedRiskCheck.created_at >= since).all()
        created = len(shares)
        views = sum(int(getattr(s, 'view_count', 0) or 0) for s in shares)
        return (created, views)

    bugs = _safe(_bugs, (0, None, 0, None)) or (0, None, 0, None)
    created, views = _safe(_loop, (0, 0)) or (0, 0)
    return {
        'leak': _safe(_leak),
        'open_bugs': bugs[0], 'bug_oldest_title': bugs[1],
        'bug_oldest_age': bugs[2], 'bug_oldest_sev': bugs[3],
        'share_created_7d': created, 'share_views_7d': views,
    }


def _customer_tasks(fill, m):
    """Customer-lane chores you do and tick off (the 'do' zone), scored by live
    magnitude so the most pressing rise. Drip and insights are intentionally not
    line items — their live numbers already show in the metric chips above the
    list, so repeating them as tasks just added noise."""
    def n(x):
        try:
            return int(x)
        except Exception:
            return 0
    defs = [
        ('outreach', "Reach out to today's lead batch", 60),
        ('followup', "Follow up with used-product users who never ran a 2nd "
                     "analysis — {one_and_done} in the pool",
         38 + min(n(m.get('one_and_done')), 30)),
        ('ads', "Check Google + Reddit ad spend vs results", 30),
    ]
    out = []
    for tid, tmpl, score in defs:
        try:
            label = tmpl.format(**fill)
        except Exception:
            label = tmpl
        out.append({'id': tid, 'label': label, 'dest': TASK_DESTS.get(tid),
                    'lane': 'customer', 'zone': 'do', 'score': float(score)})
    return out


def _product_tasks(ps):
    """Product-lane tasks computed from live signals. Falls back to a generic
    ship task only when there is no signal at all, so the day is never entirely
    customer-facing."""
    out = []
    leak = ps.get('leak')
    if leak:
        drop, frm, to, na, _nb = leak
        out.append({
            'id': 'leak',
            'label': (f"Biggest funnel leak: {drop:.0f}% drop between "
                      f"{_stage_label(frm)} and {_stage_label(to)} "
                      f"({na} reached '{_stage_label(frm)}' this week). "
                      f"Highest-leverage product fix today."),
            'dest': {'view': 'analytics'},
            'lane': 'product', 'zone': 'watch', 'score': 70.0 + min(drop, 60.0) * 0.5,
        })
    nbugs = ps.get('open_bugs') or 0
    if nbugs:
        title = ps.get('bug_oldest_title') or 'see the tracker'
        age = ps.get('bug_oldest_age') or 0
        sev = (ps.get('bug_oldest_sev') or '').strip()
        sev_txt = (sev + ' ') if sev else ''
        out.append({
            'id': 'bugs',
            'label': (f"{nbugs} open bug{'s' if nbugs != 1 else ''} — oldest is "
                      f"{age} day{'s' if age != 1 else ''} old: {sev_txt}{title}."),
            'dest': {'view': 'tests', 'anchor': 'allBugsSection'},
            'lane': 'product', 'zone': 'watch', 'score': 35.0 + min(nbugs * 5, 25) + min(age, 20),
        })
    created = ps.get('share_created_7d') or 0
    views = ps.get('share_views_7d') or 0
    if created:
        if views == 0:
            tail = ("no one has clicked through yet, so the share card isn't "
                    "pulling visitors back.")
        else:
            tail = (f"{views} view{'s' if views != 1 else ''} so far — watch the "
                    f"view-to-signup step before scaling the loop.")
        out.append({
            'id': 'loop',
            'label': (f"Risk-share loop: {created} share"
                      f"{'s' if created != 1 else ''} created this week, {tail}"),
            'dest': {'view': 'analytics'},
            'lane': 'product', 'zone': 'watch', 'score': 25.0 + min(created, 20),
        })
    if not out:
        out.append({'id': 'ship', 'label': "Ship today's product change",
                    'dest': None, 'lane': 'product', 'zone': 'watch', 'score': 40.0})
    return out


def build_daily_tasks_data(when=None):
    """Assemble the full payload used by both the panel and the email.

    The list is split into two zones. The 'do' zone is the customer chores you
    complete and tick off (outreach, follow-up, ad check) plus any custom tasks;
    only these count toward done/total. The 'watch' zone is product signals you
    watch or build toward (biggest funnel leak, open bugs, the share loop) — no
    checkbox, capped, always at least one (a generic ship task when nothing is
    flagged). Drip and insights are not line items; their numbers live in the
    metric chips. Task shape adds an optional 'zone' but is otherwise unchanged."""
    when = when or datetime.utcnow()
    metrics = _compute_metrics()
    psig = _safe(_compute_product_signals, {}) or {}
    fill = {k: _fmt(v) for k, v in metrics.items()}
    done = set(get_done_ids(when))

    do_src = sorted(_customer_tasks(fill, metrics),
                    key=lambda t: t.get('score', 0), reverse=True)
    WATCH_CAP = 5
    watch_src = sorted(_product_tasks(psig),
                       key=lambda t: t.get('score', 0), reverse=True)[:WATCH_CAP]

    tasks = []
    for t in do_src:
        tasks.append({'id': t['id'], 'label': t['label'], 'done': t['id'] in done,
                      'custom': False, 'dest': t.get('dest'),
                      'lane': t.get('lane'), 'zone': 'do'})
    for text in get_extra_tasks():
        eid = _extra_id(text)
        tasks.append({'id': eid, 'label': text, 'done': eid in done,
                      'custom': True, 'dest': None, 'lane': 'custom', 'zone': 'do'})
    for t in watch_src:
        tasks.append({'id': t['id'], 'label': t['label'], 'done': False,
                      'custom': False, 'dest': t.get('dest'),
                      'lane': t.get('lane'), 'zone': 'watch'})

    do_items = [t for t in tasks if t['zone'] == 'do']
    completed = sum(1 for t in do_items if t['done'])
    return {
        'date': when.strftime('%Y-%m-%d'),
        'metrics': metrics,
        'product_signals': psig,
        'tasks': tasks,
        'completed': completed,
        'total': len(do_items),
        'email_to': get_email_to(),
        'email_enabled': email_enabled(),
    }


# ── email rendering + send ───────────────────────────────────────────────────
def _dest_url(dest, base, key):
    """Turn a task destination into an absolute admin URL (for email links)."""
    if not dest:
        return None
    keyq = ('?admin_key=' + key) if key else '?'
    if dest.get('view'):
        u = base + '/admin' + keyq
        if dest.get('anchor'):
            u += '&go=' + dest['anchor']
        return u + '#' + dest['view']
    if dest.get('page'):
        u = base + dest['page'] + keyq
        if dest.get('hash'):
            u += '#' + dest['hash']
        return u
    return None


def render_daily_tasks_email_html(data, dashboard_url=None):
    m = data['metrics']
    base = os.environ.get('PUBLIC_BASE_URL', 'https://www.getofferwise.ai')
    key = os.environ.get('ADMIN_KEY', '')
    dashboard_url = dashboard_url or (base + '/admin' + (('?admin_key=' + key) if key else ''))

    def chip(label, val):
        v = '?' if val is None else val
        return (f'<td style="padding:8px 12px;background:#131720;border:1px solid #232a3b;'
                f'border-radius:8px;text-align:center;">'
                f'<div style="font-family:monospace;font-size:20px;font-weight:700;color:#e6edf3;">{v}</div>'
                f'<div style="font-size:11px;color:#6b7b8d;margin-top:2px;">{label}</div></td>')

    chips = (
        '<table cellspacing="8" cellpadding="0" style="margin:0 0 20px;width:100%;"><tr>'
        + chip('Drip due', m.get('drip_due'))
        + chip('New 24h', m.get('new_signups'))
        + chip('Active 7d', m.get('active_7d'))
        + chip('1-and-done', m.get('one_and_done'))
        + '</tr></table>'
    )

    def zone_header(txt):
        return (f'<tr><td style="padding:16px 0 4px;">'
                f'<span style="font-size:11px;font-weight:700;letter-spacing:.06em;'
                f'text-transform:uppercase;color:#6b7b8d;">{txt}</span></td></tr>')

    do_rows = ''
    watch_rows = ''
    for t in data['tasks']:
        url = _dest_url(t.get('dest'), base, key)
        label = t['label']
        if t.get('zone') == 'watch':
            dot = {'leak': '#d29922', 'bugs': '#f85149', 'loop': '#58a6ff'}.get(t['id'], '#6b7b8d')
            if url:
                label = (f'<a href="{url}" style="color:#58a6ff;text-decoration:none;">'
                         f'{label} <span style="color:#6b7b8d;">→</span></a>')
            watch_rows += (f'<tr><td style="padding:9px 0;border-bottom:1px solid #1b2130;">'
                           f'<span style="color:{dot};font-size:16px;line-height:1;">&bull;</span> '
                           f'<span style="color:#c9d1d9;font-size:14px;">{label}</span></td></tr>')
        else:
            box = '✅' if t['done'] else '⬜️'
            color = '#6b7b8d' if t['done'] else '#c9d1d9'
            deco = 'line-through' if t['done'] else 'none'
            if url and not t['done']:
                label = (f'<a href="{url}" style="color:#58a6ff;text-decoration:none;">'
                         f'{label} <span style="color:#6b7b8d;">→</span></a>')
            do_rows += (f'<tr><td style="padding:9px 0;border-bottom:1px solid #1b2130;">'
                        f'<span style="font-size:15px;">{box}</span> '
                        f'<span style="color:{color};font-size:14px;text-decoration:{deco};">{label}</span>'
                        f'</td></tr>')

    if not watch_rows:
        watch_rows = ('<tr><td style="padding:9px 0;color:#6b7b8d;font-size:13px;">'
                      'Nothing flagged — funnel, bugs, and share loop are all quiet.</td></tr>')

    rows = (zone_header('✅ Do today') + do_rows
            + zone_header('👀 Watch / build') + watch_rows)

    return f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                max-width:620px;margin:0 auto;background:#0b0e14;padding:28px;border-radius:12px;color:#c9d1d9;">
      <div style="font-family:monospace;font-size:18px;font-weight:700;color:#e6edf3;margin-bottom:4px;">📋 Daily Tasks</div>
      <div style="font-size:12px;color:#6b7b8d;margin-bottom:18px;">{data['date']} · {data['completed']}/{data['total']} done</div>
      {chips}
      <table cellspacing="0" cellpadding="0" style="width:100%;">{rows}</table>
      <div style="margin-top:22px;text-align:center;">
        <a href="{dashboard_url}" style="display:inline-block;background:#1f6feb;color:#fff;
           text-decoration:none;padding:10px 20px;border-radius:8px;font-size:13px;font-weight:600;">
           Open the dashboard →</a>
      </div>
      <p style="font-size:11px;color:#475569;margin:20px 0 0;text-align:center;">
        OfferWise founder daily reminder · turn off from the Daily Tasks panel.</p>
    </div>"""


def send_daily_tasks_email(force=False):
    """Build today's data and email it to the configured recipient.
    Returns True on success, False on skip/failure. Never raises."""
    try:
        if not force and not email_enabled():
            logger.info("daily_tasks email disabled — skipping")
            return False
        data = build_daily_tasks_data()
        html = render_daily_tasks_email_html(data)
        to_email = get_email_to()
        from email_service import send_email
        ok = send_email(
            to_email=to_email,
            subject=f"📋 OfferWise daily tasks — {data['date']} ({data['completed']}/{data['total']} done)",
            html_content=html,
            reply_to=ADMIN_EMAIL,
            email_type='daily_tasks',
        )
        logger.info(f"daily_tasks email -> {to_email}: {'sent' if ok else 'failed'}")
        return bool(ok)
    except Exception as e:
        logger.warning(f"send_daily_tasks_email failed: {e}")
        return False
