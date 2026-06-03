"""b2b_followup.py — v5.89.136 automated B2B follow-up sequence ("drip for B2B").

Context
-------
The first touch to a B2B prospect is sent manually by the founder (a reviewed,
research-personalized draft via the admin outreach UI). Until now that was the
ONLY touch — the system sent one beautiful email and never followed up. In cold
B2B, the majority of replies come from touches 2 through 4, not the first email,
so a one-shot send structurally caps reply rate near the floor.

This module adds touches 2, 3, and 4 on a spaced schedule, and tracks which
prospect is on which touch — a drip campaign for the B2B side.

Design (no schema change)
-------------------------
OutreachLog already records one row per send, so it IS the touch ledger. The
current touch number for a contact = count of successful b2b OutreachLog rows.
We do not denormalize a counter onto OutreachContact; OutreachLog is the single
source of truth (and the admin list already aggregates from it).

Stop conditions (all checked every run):
  - status != 'contacted'  — the founder flags 'replied' / 'meeting_set' /
    'design_partner' / 'passed' the moment a prospect responds or is closed,
    which immediately and automatically halts the sequence.
  - email unsubscribed (is_unsubscribed) — honored every touch.
  - touch number already at MAX_TOUCHES (4).
  - not enough time elapsed since the last contact for the next touch's gap.

The scheduler is gated OFF on staging by the same APP_ENV guard that gates the
consumer drip (it is registered inside the same _start_background_schedulers),
so staging stays side-effect-free.

Idempotent and safe to run on any interval. Sends at most one touch per contact
per run; spacing is enforced off OutreachContact.last_contacted_at.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# First touch is #1 (sent manually). Follow-ups are touches 2, 3, 4.
MAX_TOUCHES = 4

# Hours that must elapse since the LAST contact before the given touch fires.
# Touch 2 lands ~3 days after the first email, touch 3 ~4 days after touch 2,
# touch 4 ~7 days after touch 3. Tunable via env without a code change.
FOLLOWUP_GAP_HOURS = {
    2: int(os.environ.get('B2B_FOLLOWUP_GAP_2_HOURS', '72')),
    3: int(os.environ.get('B2B_FOLLOWUP_GAP_3_HOURS', '96')),
    4: int(os.environ.get('B2B_FOLLOWUP_GAP_4_HOURS', '168')),
}

# Local URL regex + trailing-punctuation-safe linkify (mirrors the fixed
# admin_routes._linkify_line so follow-up links never 404 on a trailing period).
_URL_RE = re.compile(r'(https?://[^\s<>"\')]+)')
_TRAIL = '.,;:!?'


def _linkify(text: str) -> str:
    def _sub(mo):
        url = mo.group(1)
        trail = ''
        while url and url[-1] in _TRAIL:
            trail = url[-1] + trail
            url = url[:-1]
        if not url:
            return mo.group(0)
        return (
            f'<a href="{url}" style="color:#2563eb;text-decoration:underline">'
            f'{url}</a>'
        ) + trail
    return _URL_RE.sub(_sub, text)


def _founder_reply_to() -> str:
    """Where replies route — mirrors admin_routes._founder_reply_to()."""
    return (os.environ.get('ADMIN_REPLY_TO')
            or os.environ.get('ADMIN_EMAIL')
            or 'francis@getofferwise.ai')


# ─── Follow-up copy (touches 2-4) ─────────────────────────────────────────
# Short, human, no em-dashes, no bullet lists. Each references the first note
# and offers an easy reply. {first}, {role}, {wedge_pain}, {url} are filled in.

_FOLLOWUP_BODIES = {
    2: (
        "Greetings {first},\n\n"
        "Floating my note from earlier back to the top of your inbox in case it "
        "got buried. The short version: OfferWise reads seller disclosures and "
        "inspection reports the way a careful buyer's agent would, and flags the "
        "risks and repair costs before your buyers write an offer.\n\n"
        "If it is worth a look, here is the one-pager for {role} folks: {url}\n\n"
        "No worries if the timing is off. Just reply and tell me to circle back "
        "later.\n\n"
        "-Francis"
    ),
    3: (
        "Greetings {first},\n\n"
        "One more try, then I will get out of your inbox. The reason I thought of "
        "your team specifically is {wedge_pain}. That is the exact gap OfferWise "
        "was built to close.\n\n"
        "Happy to run one of your current listings through it and send you back "
        "the analysis your buyer would see. No charge and no pitch. Just reply "
        "with an address and I will turn it around.\n\n"
        "{url}\n\n"
        "-Francis"
    ),
    4: (
        "Greetings {first},\n\n"
        "Last note from me on this, I promise. I do not want to keep cluttering "
        "your inbox.\n\n"
        "If giving your buyers data-backed offer analysis is something you want "
        "to explore down the road, the door is open. Just reply any time and we "
        "can pick it up.\n\n"
        "Either way, thanks for the read and good luck out there.\n\n"
        "-Francis"
    ),
}


def _build_followup(contact, step):
    """Return (subject, html_body) for the given follow-up step.

    Subject threads off the original first-touch subject ('Re: ...') so it
    lands in the same Gmail conversation as the first email.
    """
    from models import OutreachLog
    from prospect_research_service import (
        _extract_first_name, get_landing_url_for_wedge, WEDGE_PAIN_LOOKUP,
    )
    from outreach_campaign_service import _unsubscribe_link

    first = _extract_first_name(contact.name or '') or 'there'
    url, role = get_landing_url_for_wedge(contact.wedge or '')
    wedge_pain = WEDGE_PAIN_LOOKUP.get(
        contact.wedge or '', WEDGE_PAIN_LOOKUP.get('', 'the overlap with your work')
    )

    body = _FOLLOWUP_BODIES[step].format(
        first=first, role=role, wedge_pain=wedge_pain, url=url,
    )

    # Thread off the most recent subject we sent this contact.
    last = (OutreachLog.query
            .filter(OutreachLog.cohort == 'b2b',
                    OutreachLog.contact_id == contact.id)
            .order_by(OutreachLog.sent_at.desc())
            .first())
    orig_subject = (last.subject if last and last.subject else
                    f'OfferWise + {contact.company or "your team"}')
    base_subject = re.sub(r'^(re:\s*)+', '', orig_subject, flags=re.IGNORECASE).strip()
    subject = f'Re: {base_subject}'[:500]

    unsub_url = _unsubscribe_link(contact.email, 0)
    paragraphs = ''.join(
        f'<p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#1f2937;">'
        f'{_linkify(p)}</p>'
        for p in body.split('\n\n')
    )
    html = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,'
        'sans-serif;max-width:600px;margin:0 auto;padding:16px">'
        f'{paragraphs}'
        '<p style="margin:18px 0 0;font-size:11px;color:#9ca3af;line-height:1.5;'
        'border-top:1px solid #e5e7eb;padding-top:10px;">'
        'You are receiving this because I reached out about OfferWise. '
        f'If you would rather not hear from me, <a href="{unsub_url}" '
        'style="color:#9ca3af;">unsubscribe here</a> and I will stop.'
        '</p>'
        '</div>'
    )
    return subject, html, body


def send_followup(contact, step, db_session) -> bool:
    """Send one follow-up touch to a contact and record it in OutreachLog.

    Returns True on a successful send. Writes an OutreachLog row either way so
    the touch ledger and the audit trail stay accurate. Advances the contact's
    last_contacted_at so the next touch is spaced correctly.
    """
    from models import OutreachLog, EmailSendLog
    from email_service import send_email, OUTREACH_FROM_EMAIL, EMAIL_ENABLED

    if not EMAIL_ENABLED:
        logger.info("B2B follow-up %s skipped (email disabled): %s", step, contact.email)
        return False

    subject, html, body = _build_followup(contact, step)
    reply_to = _founder_reply_to()
    pre_send_at = datetime.utcnow()

    ok = False
    try:
        ok = send_email(
            to_email=contact.email,
            subject=subject,
            html_content=html,
            reply_to=reply_to,
            email_type='founder_outreach',
            from_email=OUTREACH_FROM_EMAIL,
        )
    except Exception as e:
        logger.exception("B2B follow-up send failed for %s", contact.email)
        ok = False

    # Pull the resend_id send_email just wrote (mirrors outreach_send).
    resend_id = None
    try:
        log_row = (EmailSendLog.query
                   .filter(EmailSendLog.to_email == contact.email)
                   .filter(EmailSendLog.ts >= pre_send_at)
                   .order_by(EmailSendLog.ts.desc())
                   .first())
        resend_id = log_row.resend_id if log_row else None
    except Exception:
        resend_id = None

    db_session.add(OutreachLog(
        cohort='b2b',
        contact_id=contact.id,
        to_email=contact.email,
        subject=subject[:500],
        body=body,
        reply_to=reply_to,
        resend_id=resend_id,
        success=bool(ok),
        error=None if ok else 'follow-up send_email returned False',
    ))

    if ok:
        contact.last_contacted_at = datetime.utcnow()
        logger.info("B2B follow-up touch %s sent to %s", step, contact.email)
    else:
        logger.error("B2B follow-up touch %s FAILED for %s", step, contact.email)

    return ok


def _touch_count(contact_id, db_session) -> int:
    """Number of successful b2b sends to this contact (the touch ledger)."""
    from models import OutreachLog
    from sqlalchemy import func as _f
    n = (db_session.query(_f.count(OutreachLog.id))
         .filter(OutreachLog.cohort == 'b2b',
                 OutreachLog.contact_id == contact_id,
                 OutreachLog.success == True)  # noqa: E712
         .scalar())
    return int(n or 0)


def run_b2b_followup_scheduler(db_session, batch_size=50):
    """Advance contacted-but-unanswered B2B prospects to their next touch.

    Returns: dict of counts (sent, skipped, errors, checked).
    """
    from models import OutreachContact
    from outreach_campaign_service import is_unsubscribed

    now = datetime.utcnow()
    stats = {'sent': 0, 'skipped': 0, 'errors': 0, 'checked': 0}

    # Only 'contacted' prospects are in the sequence. Any other status
    # (replied / meeting_set / design_partner / passed / not_contacted)
    # is excluded, so a reply or a close halts follow-ups automatically.
    candidates = (OutreachContact.query
                  .filter(OutreachContact.cohort == 'b2b',
                          OutreachContact.status == 'contacted',
                          OutreachContact.last_contacted_at.isnot(None))
                  .order_by(OutreachContact.last_contacted_at.asc())
                  .limit(batch_size)
                  .all())
    stats['checked'] = len(candidates)

    for c in candidates:
        try:
            if is_unsubscribed(c.email or ''):
                stats['skipped'] += 1
                continue

            # Touch ledger. 'contacted' implies at least the first touch
            # happened, so floor at 1 even if a legacy first send was never
            # logged in OutreachLog.
            sent_so_far = max(_touch_count(c.id, db_session), 1)
            next_step = sent_so_far + 1
            if next_step > MAX_TOUCHES:
                stats['skipped'] += 1
                continue

            last = c.last_contacted_at
            if last is not None and last.tzinfo is not None:
                last = last.replace(tzinfo=None)
            hours_since_last = (now - last).total_seconds() / 3600
            if hours_since_last < FOLLOWUP_GAP_HOURS[next_step]:
                stats['skipped'] += 1
                continue

            if send_followup(c, next_step, db_session):
                stats['sent'] += 1
            else:
                stats['errors'] += 1
        except Exception:
            logger.exception("B2B follow-up scheduler error for contact %s", getattr(c, 'id', '?'))
            stats['errors'] += 1

    if stats['sent'] or stats['errors']:
        db_session.commit()
    return stats
