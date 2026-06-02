"""
outreach_campaign_service.py — v5.88.42

Cohort-style outreach for the Buyer Users tab. Wraps the existing
single-send infrastructure (admin_routes.outreach_send + EmailSendLog +
OutreachLog) with batch send + template variables + campaign grouping.

Design notes:
  - Sender identity comes from email_service.OUTREACH_FROM_EMAIL
    ('Francis Anthony <francis@getofferwise.ai>')
  - Replies route to FRANCIS_REPLY_TO (defaults to same address) so the
    customer sees a real person reply if they hit Reply
  - Unsubscribe is honored: every send is gated on the email NOT being in
    outreach_unsubscribes. Skipped emails are recorded with status='skipped'
    on the OutreachLog so the audit trail is complete
  - This module deliberately does NOT take on the B2B prospect path; that
    flow has its own send endpoint and works as-is

Variable substitution:
  See OutreachTemplate docstring for the supported variables.
  Unresolved variables are left in place (same pattern as ticket templates
  in v5.88.37) and the UI flags them before send.
"""
import calendar
import hmac
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from models import (
    db, User, Property, OutreachLog, OutreachCampaign,
    OutreachTemplate, OutreachUnsubscribe,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Variable substitution
# ---------------------------------------------------------------------------

# {variable} — same pattern as v5.88.37 ticket templates. Lowercase + underscores only.
_TEMPLATE_VAR_RE = re.compile(r'\{([a-z_]+)\}')


def _first_name_from_user(user: User) -> str:
    """Extract a sensible first-name token from User.name, falling back to
    the email local-part with light capitalization. If both fail, 'there'.
    """
    name = (user.name or '').strip()
    if name:
        first = name.split()[0]
        return first.capitalize() if first.islower() else first

    # Fallback: email local-part, stripped of digits/dots, capitalized.
    # 'francis.kurupacheril@gmail.com' -> 'Francis'
    # 'jnoel1234@gmail.com'           -> 'Jnoel'
    # 'test12@gmail.com'              -> 'Test'
    local = (user.email or '').split('@')[0]
    cleaned = re.sub(r'[\d._+-]+', ' ', local).strip().split()
    if cleaned:
        return cleaned[0].capitalize()
    return 'there'


def _month_joined(user: User) -> str:
    """'February 2026' style."""
    if not user.created_at:
        return '(unknown)'
    return user.created_at.strftime('%B %Y')


def _last_property_address(user: User) -> str:
    """Most recent property address the user analyzed, or '(no property)'."""
    p = (Property.query
         .filter_by(user_id=user.id)
         .order_by(Property.id.desc())
         .first())
    if p and p.address:
        return p.address
    return '(no property)'


def _stage_for_user(user: User) -> str:
    """Same stage classification logic as admin_routes.api_funnel_debug.
    Replicated here to avoid a circular import via admin_routes.
    """
    has_stripe = bool(getattr(user, 'stripe_customer_id', None))
    plan = (getattr(user, 'subscription_plan', 'free') or 'free').lower()
    tier = (getattr(user, 'tier', 'free') or 'free').lower()
    is_paid = plan not in ('free', '', 'inspector_free') and tier not in ('free', '', None)

    # Has paid via CreditTransaction?
    purchased = False
    try:
        from models import CreditTransaction
        purchased = CreditTransaction.query.filter_by(
            user_id=user.id, status='completed'
        ).first() is not None
    except Exception:
        pass

    analyses = Property.query.filter_by(user_id=user.id).count()
    onboarded = bool(getattr(user, 'onboarding_completed', False))

    if purchased or (is_paid and tier != 'free'):
        return 'CONVERTED'
    if has_stripe:
        return 'CHECKOUT_STARTED'
    if analyses > 0:
        return 'USED_PRODUCT'
    if onboarded:
        return 'ONBOARDED'
    return 'SIGNED_UP_ONLY'


def _unsubscribe_link(email: str, campaign_id: Optional[int] = None) -> str:
    """Generate a signed unsubscribe link for the given email.

    Token is HMAC-SHA256 of (email + campaign_id) using OUTREACH_UNSUB_SECRET
    env var (falling back to SECRET_KEY). Tokens don't expire — once
    unsubscribed, always unsubscribed.
    """
    secret = os.environ.get('OUTREACH_UNSUB_SECRET') or os.environ.get('SECRET_KEY', 'fallback-key')
    payload = f'{email.lower()}:{campaign_id or 0}'
    sig = hmac.new(secret.encode('utf-8'), payload.encode('utf-8'),
                   hashlib.sha256).hexdigest()[:32]  # 32 char prefix is plenty

    # Base URL — same env vars admin uses elsewhere
    base = os.environ.get('APP_URL') or os.environ.get('PUBLIC_URL') or 'https://www.getofferwise.ai'
    base = base.rstrip('/')
    # email and campaign_id in URL as query params for easy parsing
    import urllib.parse as _up
    return f'{base}/unsubscribe?e={_up.quote(email)}&c={campaign_id or 0}&t={sig}'


def verify_unsubscribe_token(email: str, campaign_id: int, token: str) -> bool:
    """Verify an unsubscribe token. Constant-time compare."""
    secret = os.environ.get('OUTREACH_UNSUB_SECRET') or os.environ.get('SECRET_KEY', 'fallback-key')
    payload = f'{(email or "").lower()}:{campaign_id or 0}'
    expected = hmac.new(secret.encode('utf-8'), payload.encode('utf-8'),
                        hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(expected, token or '')


def build_variable_context(user: User, campaign_id: Optional[int] = None) -> Dict[str, str]:
    """Construct the variable substitution map for one user.

    Pulls user fields, derived fields (first_name, month_joined), and
    the per-user unsubscribe link. Used by render_for_user().
    """
    return {
        'first_name':            _first_name_from_user(user),
        'email':                 user.email or '',
        'month_joined':          _month_joined(user),
        'days_since_signup':     str((datetime.utcnow() - user.created_at).days)
                                  if user.created_at else '0',
        'last_property_address': _last_property_address(user),
        'stage':                 _stage_for_user(user),
        'unsubscribe_link':      _unsubscribe_link(user.email or '', campaign_id),
    }


def render_for_user(template_text: str, user: User,
                    campaign_id: Optional[int] = None) -> Tuple[str, List[str]]:
    """Substitute variables into a template. Returns (rendered, unresolved).

    Unresolved variables are LEFT IN PLACE so a typo like {custmer_name}
    is visible to the admin in the preview instead of silently dropping
    content.
    """
    if not template_text:
        return '', []
    ctx = build_variable_context(user, campaign_id)
    unresolved: List[str] = []

    def _sub(match: re.Match) -> str:
        var = match.group(1)
        if var in ctx:
            return ctx[var]
        if var not in unresolved:
            unresolved.append(var)
        return match.group(0)  # leave as-is

    rendered = _TEMPLATE_VAR_RE.sub(_sub, template_text)
    return rendered, unresolved


# ---------------------------------------------------------------------------
# Unsubscribe helpers
# ---------------------------------------------------------------------------

def is_unsubscribed(email: str) -> bool:
    """Check the global outreach unsubscribe list."""
    if not email:
        return False
    return OutreachUnsubscribe.query.filter(
        db.func.lower(OutreachUnsubscribe.email) == email.lower()
    ).first() is not None


def add_unsubscribe(email: str, reason: str = 'manual',
                    campaign_id: Optional[int] = None,
                    notes: str = '') -> Optional[OutreachUnsubscribe]:
    """Add an email to the unsubscribe list. Idempotent."""
    if not email:
        return None
    email = email.strip().lower()
    existing = OutreachUnsubscribe.query.filter(
        db.func.lower(OutreachUnsubscribe.email) == email
    ).first()
    if existing:
        return existing
    row = OutreachUnsubscribe(
        email=email, reason=reason,
        campaign_id=campaign_id,
        notes=(notes or '')[:500],
    )
    db.session.add(row)
    db.session.commit()
    return row


# ---------------------------------------------------------------------------
# Cohort filtering — pure SQL composition, no Python loops
# ---------------------------------------------------------------------------

# Email patterns that identify test/internal accounts. Anything matching
# any of these is excluded when 'exclude_test=true' (default for outreach).
_TEST_EMAIL_PATTERNS = (
    '@e2e-cassette.test.',
    '@test.offerwise.ai',
    '@example.com',           # often used in test data
    'persona.',
)

# Internal admin/founder emails that should never be in outreach lists,
# regardless of exclude_test flag.
_INTERNAL_EMAIL_PATTERNS = (
    '@getofferwise.ai',
)


def filter_users_for_cohort(
    stages: Optional[List[str]] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    exclude_test: bool = True,
    inactive_days_min: Optional[int] = None,
    source: Optional[str] = None,
    limit: int = 500,
) -> List[User]:
    """Compute the user cohort matching the given filters.

    All filters are AND'd. Stage filter applies in Python (post-query)
    because stage is derived, not a column.

    Args:
      stages: list of stage names e.g. ['SIGNED_UP_ONLY', 'ONBOARDED'].
              None = all stages.
      from_date / to_date: created_at range. Inclusive bounds.
      exclude_test: drop @e2e-cassette / persona.* / @example.com / etc.
      inactive_days_min: filter to users with last_login at least N days ago
                         (or never logged in).
      source: substring match against User.source / first GTMFunnelEvent
              metadata source. Case-insensitive 'contains'.
      limit: hard cap. Default 500.

    Returns User instances, ordered by created_at desc.
    """
    q = User.query

    # Date range
    if from_date:
        q = q.filter(User.created_at >= from_date)
    if to_date:
        q = q.filter(User.created_at <= to_date)

    # Test/internal exclusions (always exclude internal)
    for pat in _INTERNAL_EMAIL_PATTERNS:
        q = q.filter(~db.func.lower(User.email).contains(pat))
    if exclude_test:
        for pat in _TEST_EMAIL_PATTERNS:
            q = q.filter(~db.func.lower(User.email).contains(pat))

    # Activity
    if inactive_days_min is not None:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=inactive_days_min)
        # last_login is nullable. Include both 'never logged in' and 'logged in before cutoff'.
        q = q.filter(db.or_(User.last_login == None,    # noqa: E711
                            User.last_login < cutoff))

    # Source — accept either User.source or User.source_tracking_id contains
    if source:
        s_like = f'%{source.lower()}%'
        # User model uses .source for the tracking string ('FRANCISK-MDGL' etc.)
        if hasattr(User, 'source'):
            q = q.filter(db.func.lower(User.source).contains(source.lower()))

    # Order + limit at SQL level
    q = q.order_by(User.created_at.desc())
    users = q.limit(limit * 2 if stages else limit).all()  # over-fetch if we'll filter

    # Stage filter (Python, since stage is derived)
    if stages:
        wanted = set(s.upper() for s in stages)
        users = [u for u in users if _stage_for_user(u) in wanted]

    return users[:limit]


# ---------------------------------------------------------------------------
# Campaign creation + send
# ---------------------------------------------------------------------------

def create_campaign(name: str, subject_template: str, body_template: str,
                    cohort_filter: dict, cohort: str = 'buyer',
                    template_id: Optional[int] = None,
                    from_email: Optional[str] = None,
                    reply_to_email: Optional[str] = None) -> OutreachCampaign:
    """Create a draft campaign. Send must be triggered separately.

    cohort_filter: dict of the filter criteria used to compute recipients.
                   Stored as JSON for audit (so you can later answer
                   'who got the May 2026 USED_PRODUCT batch?').
    """
    from email_service import OUTREACH_FROM_EMAIL
    fe = from_email or OUTREACH_FROM_EMAIL
    rt = reply_to_email or fe  # default: replies go back to sender

    campaign = OutreachCampaign(
        name=name[:200],
        cohort=cohort,
        template_id=template_id,
        subject_template=subject_template[:500],
        body_template=body_template,
        cohort_filter_json=json.dumps(cohort_filter) if cohort_filter else None,
        from_email=fe[:255],
        reply_to_email=rt[:255],
        status='draft',
    )
    db.session.add(campaign)
    db.session.commit()
    return campaign


def send_campaign(campaign_id: int, dry_run: bool = False,
                  rate_limit_seconds: float = 0.2) -> dict:
    """Send a campaign to its computed cohort. Synchronous, sequential.

    Recipients are computed at send time from cohort_filter_json. This is
    intentional: the cohort may have grown since the campaign was drafted
    (new signups, status changes). If you want to lock the cohort to a
    point-in-time list, snapshot it into cohort_filter_json explicitly.

    Args:
      campaign_id: ID of the OutreachCampaign
      dry_run: if True, render bodies but don't actually send. Returns
               the would-have-been recipient list with rendered content.
      rate_limit_seconds: delay between sends. 0.2s = 5/sec, well under
                          Resend's defaults. Set higher for first-time
                          domain sends.

    Returns dict with: sent_count, failed_count, skipped_count, errors[],
    sample_recipients[] (first 5 for confirmation).
    """
    from email_service import send_email

    campaign = OutreachCampaign.query.get(campaign_id)
    if not campaign:
        return {'error': 'campaign not found'}

    if campaign.status in ('sending', 'completed'):
        # Idempotency guard — don't double-send
        return {'error': f'campaign already in status {campaign.status}'}

    # Resolve cohort
    filt = json.loads(campaign.cohort_filter_json) if campaign.cohort_filter_json else {}
    from_date = datetime.fromisoformat(filt['from_date']) if filt.get('from_date') else None
    to_date = datetime.fromisoformat(filt['to_date']) if filt.get('to_date') else None

    recipients = filter_users_for_cohort(
        stages=filt.get('stages'),
        from_date=from_date,
        to_date=to_date,
        exclude_test=filt.get('exclude_test', True),
        inactive_days_min=filt.get('inactive_days_min'),
        source=filt.get('source'),
        limit=filt.get('limit', 500),
    )

    if dry_run:
        # Preview only — return rendered previews for the first few users
        previews = []
        for u in recipients[:10]:
            subject_rendered, sub_unresolved = render_for_user(
                campaign.subject_template, u, campaign_id=campaign.id)
            body_rendered, body_unresolved = render_for_user(
                campaign.body_template, u, campaign_id=campaign.id)
            previews.append({
                'user_id': u.id,
                'to_email': u.email,
                'subject': subject_rendered,
                'body': body_rendered,
                'unresolved_variables': list(set(sub_unresolved + body_unresolved)),
                'will_skip_unsubscribed': is_unsubscribed(u.email or ''),
            })
        return {
            'dry_run': True,
            'recipient_count': len(recipients),
            'previews': previews,
        }

    # Mark campaign as sending
    campaign.send_started_at = datetime.utcnow()
    campaign.status = 'sending'
    campaign.recipient_count = len(recipients)
    db.session.commit()

    sent = 0
    failed = 0
    skipped = 0
    errors: List[str] = []

    for u in recipients:
        to_email = (u.email or '').strip()
        if not to_email:
            skipped += 1
            continue

        # Honor unsubscribe list
        if is_unsubscribed(to_email):
            skipped += 1
            # Log the skip so audit shows we tried
            db.session.add(OutreachLog(
                cohort=campaign.cohort,
                user_id=u.id,
                to_email=to_email,
                subject=campaign.subject_template[:500],
                body='(skipped: unsubscribed)',
                reply_to=campaign.reply_to_email,
                success=False,
                error='unsubscribed',
                campaign_id=campaign.id,
            ))
            continue

        # Render
        subject_rendered, _ = render_for_user(
            campaign.subject_template, u, campaign_id=campaign.id)
        body_rendered, _ = render_for_user(
            campaign.body_template, u, campaign_id=campaign.id)

        # Auto-append unsubscribe link if template author forgot
        if '{unsubscribe_link}' not in campaign.body_template and 'unsubscribe' not in body_rendered.lower():
            unsub_url = _unsubscribe_link(to_email, campaign.id)
            body_rendered += (
                f'\n\n---\n'
                f'You\'re receiving this because you signed up on OfferWise. '
                f'To unsubscribe, visit: {unsub_url}'
            )

        # Wrap plain text in minimal HTML
        html_body = (
            f'<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;'
            f'font-size:15px;line-height:1.55;color:#222;max-width:600px;margin:0 auto;padding:16px">'
            f'<div style="white-space:pre-wrap">{_html_escape(body_rendered)}</div>'
            f'</div>'
        )

        # Send via Resend
        try:
            ok = send_email(
                to_email=to_email,
                subject=subject_rendered[:255],
                html_content=html_body,
                reply_to=campaign.reply_to_email,
                from_email=campaign.from_email,
                email_type='outreach_campaign',
                user_id=u.id,
            )
            log = OutreachLog(
                cohort=campaign.cohort,
                user_id=u.id,
                to_email=to_email,
                subject=subject_rendered[:500],
                body=body_rendered,
                reply_to=campaign.reply_to_email,
                success=bool(ok),
                error=None if ok else 'send_email returned False',
                campaign_id=campaign.id,
            )
            db.session.add(log)

            if ok:
                sent += 1
            else:
                failed += 1
                errors.append(f'{to_email}: send returned False')
        except Exception as e:
            failed += 1
            errors.append(f'{to_email}: {e}')
            db.session.add(OutreachLog(
                cohort=campaign.cohort,
                user_id=u.id,
                to_email=to_email,
                subject=subject_rendered[:500],
                body=body_rendered,
                reply_to=campaign.reply_to_email,
                success=False,
                error=str(e)[:500],
                campaign_id=campaign.id,
            ))

        # Commit periodically so partial sends are durable
        if (sent + failed + skipped) % 10 == 0:
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

        # Rate-limit
        if rate_limit_seconds > 0:
            time.sleep(rate_limit_seconds)

    # Finalize
    campaign.sent_count = sent
    campaign.failed_count = failed
    campaign.skipped_count = skipped
    campaign.send_completed_at = datetime.utcnow()
    if failed == 0 and skipped == 0:
        campaign.status = 'completed'
    elif sent > 0:
        campaign.status = 'partial'
    else:
        campaign.status = 'failed'
    db.session.commit()

    return {
        'campaign_id': campaign.id,
        'sent_count': sent,
        'failed_count': failed,
        'skipped_count': skipped,
        'errors': errors[:20],  # first 20
    }


# ---------------------------------------------------------------------------
# Default templates seeding
# ---------------------------------------------------------------------------

# Three starter templates — one per stage from the v5.88.41 analysis
DEFAULT_OUTREACH_TEMPLATES = [
    {
        'name': 'Used product, never returned',
        'cohort': 'buyer',
        'sort_order': 10,
        'subject_template': 'You used OfferWise once. What happened? (Francis, founder)',
        'body_template': (
            'Hi {first_name},\n\n'
            'You ran a property analysis on OfferWise back in {month_joined} '
            'and didn\'t come back. That\'s the pattern I\'m seeing across nearly '
            'every user, and I\'d love to understand why.\n\n'
            'Hit reply with whichever applies. One letter is enough:\n\n'
            '  A) The analysis was useful, but I bought (or didn\'t buy) the house and don\'t need it again\n'
            '  B) The analysis wasn\'t useful enough to come back\n'
            '  C) I forgot OfferWise existed\n'
            '  D) I tried to use it again but ran into a problem\n'
            '  E) The pricing wasn\'t clear or felt too high\n'
            '  F) Something else (please tell me)\n\n'
            'No pitch coming. I\'m a solo founder trying to figure out if this '
            'product solves a real problem, and your honest answer means more than '
            'another 100 signups.\n\n'
            'Francis\nOfferWise'
        ),
    },
    {
        'name': 'Onboarded, never analyzed',
        'cohort': 'buyer',
        'sort_order': 20,
        'subject_template': 'You got close but didn\'t finish (Francis at OfferWise)',
        'body_template': (
            'Hi {first_name},\n\n'
            'You signed up for OfferWise in {month_joined} and completed onboarding, '
            'but never actually ran a property analysis. I\'m trying to understand why, '
            'because you got further than most people and then stopped.\n\n'
            'Hit reply with whichever applies. One letter is enough:\n\n'
            '  A) I didn\'t have a specific property in mind yet\n'
            '  B) I wasn\'t sure what to upload or how to start\n'
            '  C) I didn\'t have the documents the tool was asking for\n'
            '  D) I lost interest before I got to it\n'
            '  E) I tried but the analysis tool wasn\'t clear how to use\n'
            '  F) I\'m no longer planning to buy a house\n'
            '  G) Something else (please tell me)\n\n'
            'No pitch coming. I\'m a solo founder and I need to understand why '
            'people sign up but never analyze a property.\n\n'
            'Francis\nOfferWise'
        ),
    },
    {
        'name': 'Signed up, never used',
        'cohort': 'buyer',
        'sort_order': 30,
        'subject_template': 'OfferWise question, one letter reply (Francis, founder)',
        'body_template': (
            'Hi {first_name},\n\n'
            'You signed up for OfferWise back in {month_joined} and didn\'t end up using it. '
            'That\'s fine, but I\'m a solo founder trying to figure out what\'s not working, '
            'and your honest answer is more useful to me than any other data I have.\n\n'
            'Hit reply with whichever applies. One letter is enough:\n\n'
            '  A) I forgot I signed up\n'
            '  B) I signed up out of curiosity, didn\'t actually need a property analysis\n'
            '  C) The signup process itself was confusing or annoying\n'
            '  D) I tried to use it but got stuck or confused\n'
            '  E) I changed my mind about buying a house\n'
            '  F) Something else (please tell me)\n\n'
            'No pitch coming. Not going to ask you to upgrade. Not going to add you to a '
            'newsletter. Just trying to understand what made you stop.\n\n'
            'Francis\nOfferWise'
        ),
    },
]


def seed_default_outreach_templates() -> int:
    """Ensure default outreach templates exist. Idempotent.

    Returns count inserted. Admin-edited rows are not touched.
    """
    inserted = 0
    for tpl in DEFAULT_OUTREACH_TEMPLATES:
        existing = OutreachTemplate.query.filter_by(name=tpl['name']).first()
        if existing is None:
            row = OutreachTemplate(
                name=tpl['name'],
                cohort=tpl['cohort'],
                subject_template=tpl['subject_template'],
                body_template=tpl['body_template'],
                sort_order=tpl['sort_order'],
                is_seeded=True,
            )
            db.session.add(row)
            inserted += 1
    if inserted:
        try:
            db.session.commit()
            logger.info(f'🌱 Seeded {inserted} default outreach templates')
        except Exception as e:
            db.session.rollback()
            logger.warning(f'Outreach template seeding failed: {e}')
            return 0
    return inserted


# ---------------------------------------------------------------------------
# Small util
# ---------------------------------------------------------------------------

def _html_escape(s: str) -> str:
    return (s.replace('&', '&amp;')
             .replace('<', '&lt;')
             .replace('>', '&gt;')
             .replace('"', '&quot;'))
