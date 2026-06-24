"""
Funnel Tracker — Lightweight event recording for the OfferWise user journey.
=============================================================================
Call track() from anywhere in app.py to record a funnel event.
Events are written to GTMFunnelEvent table and available in the admin dashboard.

Usage:
    from funnel_tracker import track
    track('risk_check_start', source='google_ads', session_id=sid)
    track('signup', user_id=user.id, source='organic')
    track('analysis_started', user_id=user.id, metadata={'address': '123 Main St'})
"""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def track(stage, source='direct', medium='none', user_id=None,
          session_id=None, metadata=None, amount_usd=None):
    """Record a funnel event. Fire-and-forget — never raises.

    Pass amount_usd for purchase events to enable CAC:LTV calculation by channel.
    """
    try:
        from models import db, GTMFunnelEvent

        # Session dedup: skip if same session + same stage within 30 minutes
        if session_id:
            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
            existing = GTMFunnelEvent.query.filter(
                GTMFunnelEvent.session_id == session_id,
                GTMFunnelEvent.stage == stage,
                GTMFunnelEvent.created_at > cutoff,
            ).first()
            if existing:
                logger.debug(f"Funnel dedup: {stage} for session {session_id[:20]} (skip)")
                return

        event = GTMFunnelEvent(
            stage=stage,
            source=source,
            medium=medium,
            user_id=user_id,
            session_id=session_id,
            metadata_json=json.dumps(metadata) if metadata else None,
            amount_usd=float(amount_usd) if amount_usd is not None else None,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(event)
        db.session.commit()
    except Exception as e:
        logger.debug(f"Funnel track error (non-critical): {e}")
        try:
            from models import db
            db.session.rollback()
        except Exception:
            pass


def _extract_source(request):
    """Extract traffic source from request (utm params, referer, etc.).
    Returns (source, medium, referrer_url) where referrer_url is the raw page URL."""
    source = request.args.get('utm_source', '')
    medium = request.args.get('utm_medium', '')
    raw_referrer = request.headers.get('Referer', '') or ''
    # Truncate to 500 chars for storage
    referrer_url = raw_referrer[:500] if raw_referrer else ''

    # Google Ads auto-tagging sends ?gclid (or gbraid/wbraid on iOS) with NO utm_*.
    # Without this the paid click has no utm_source and a stripped referer, so it
    # falls through to 'direct'/'organic' and the ad spend is invisible at the
    # visit level (signup attribution still worked via the session gclid stash,
    # which is why visits.google_ads was 0 while signups.google_ads was not).
    if not source and (request.args.get('gclid') or request.args.get('gbraid')
                       or request.args.get('wbraid')):
        source, medium = 'google', 'cpc'

    if not source:
        referer = raw_referrer.lower()
        if 'google' in referer:
            source = 'google'
            medium = medium or ('cpc' if request.args.get('utm_medium') == 'cpc' else 'organic')
        elif 'reddit' in referer:
            source = 'reddit'
            medium = 'social'
        elif 'facebook' in referer or 'fb.com' in referer:
            source = 'facebook'
            medium = 'social'
        elif 'zillow' in referer or 'trulia' in referer or 'hotpads' in referer:
            source = 'zillow'
            medium = 'referral'
        elif 'nachi.org' in referer or 'internachi' in referer:
            source = 'internachi'
            medium = 'referral'
        elif 'nextdoor' in referer:
            source = 'nextdoor'
            medium = 'social'
        elif 'biggerpockets' in referer:
            source = 'biggerpockets'
            medium = 'referral'
        elif 'yelp' in referer:
            source = 'yelp'
            medium = 'referral'
        elif referer and 'getofferwise' not in referer:
            # Unknown referrer — extract the domain
            try:
                from urllib.parse import urlparse
                parsed = urlparse(raw_referrer)
                source = parsed.netloc.replace('www.', '') or 'referral'
            except Exception:
                source = 'referral'
            medium = 'referral'
        else:
            source = 'direct'
            medium = 'none'

    return source, medium, referrer_url


# (The canonical test/persona/e2e domain list lives in app.TEST_EMAIL_DOMAINS;
# track_from_request reads it directly so there is no second copy to drift.
# is_test_account below is the richer detector used by the Buyers admin view.)

# v5.88.25: Comprehensive test-account detection. Used by the Buyers
# admin view (and elsewhere) to filter out test users so the view
# represents REAL prospects.
#
# Patterns considered test accounts:
#   - @e2e-*.test.example.com    Path B test users (auth_signup, credits, etc)
#   - @test.offerwise.ai          your persona infrastructure (legacy)
#   - @persona.offerwise.ai       your persona infrastructure (legacy)
#   - @piotnetworks.com           founder's own account
#   - @getofferwise.ai            company accounts (hello@, billing@, etc.)
#   - test*@gmail.com             manually-created gmail test accounts
#   - +test in the local part     gmail plus-addressing for testing
#   - name == 'Cassette Recorder' or starts with 'Persona ' (catches users
#     whose email doesn't match a pattern but who were programmatically
#     created for testing)
#
# Returns True if the user looks like a test account; False for real prospects.
def is_test_account(email_or_user):
    """Detect test accounts comprehensively.

    Accepts either a User object or an email string. Returns True if
    the account matches any test pattern. Safe for None/missing values
    (returns True so callers don't accidentally surface broken records).
    """
    # Extract email + name from input
    if email_or_user is None:
        return True
    if isinstance(email_or_user, str):
        email = email_or_user
        name = None
    else:
        # Assume User-like object
        email = getattr(email_or_user, 'email', None)
        name = getattr(email_or_user, 'name', None)

    if not email:
        return True

    email_lower = email.lower().strip()

    # Domain-based patterns (suffix matches)
    domain_patterns = (
        '@test.offerwise.ai',
        '@persona.offerwise.ai',
        '@piotnetworks.com',
        '@getofferwise.ai',
    )
    if any(email_lower.endswith(d) for d in domain_patterns):
        return True

    # @e2e-*.test.example.com — Path B test users (more flexible than suffix)
    if '@e2e-' in email_lower and email_lower.endswith('.test.example.com'):
        return True

    # test*@gmail.com — manually-created gmail test accounts
    if email_lower.endswith('@gmail.com'):
        local = email_lower.split('@', 1)[0]
        if local.startswith('test'):
            return True

    # +test in the local part — gmail plus-addressing convention
    local = email_lower.split('@', 1)[0]
    if '+test' in local:
        return True

    # Name-based patterns (catches programmatically-created users whose
    # email doesn't match)
    if name:
        if name == 'Cassette Recorder':
            return True
        if name.startswith('Persona '):
            return True

    return False

def track_from_request(stage, request, user_id=None, metadata=None, source=None, medium=None):
    """Record a funnel event, auto-extracting source from the request.

    source/medium can be passed explicitly to force attribution — used by vanity
    entry routes (e.g. /reddit) where the visitor types the URL with no utm_*, so
    the referer/utm auto-detection would otherwise mislabel the visit 'direct'."""
    # Skip persona/test accounts — they pollute funnel metrics
    if user_id:
        try:
            from models import User as _User
            # Single source of truth: read the canonical exclusion list from app
            # (lazy import — app is already loaded at request time; same pattern
            # admin_routes uses) so write-time skip and the read-time funnel
            # exclusion can never drift. NULL-safe on email.
            from app import TEST_EMAIL_DOMAINS as _TEST_DOMAINS
            _u = _User.query.get(user_id)
            if _u and _u.email and any(_u.email.endswith(d) for d in _TEST_DOMAINS):
                return
        except Exception:
            pass

    # Bot filter — skip known crawlers
    ua = (request.headers.get('User-Agent', '') or '').lower()
    bot_signals = ['bot', 'crawl', 'spider', 'slurp', 'semrush', 'ahrefs',
                   'bytespider', 'googlebot', 'bingbot', 'yandex', 'baidu',
                   'facebookexternalhit', 'twitterbot', 'linkedinbot',
                   'curl', 'wget', 'python-requests', 'scrapy', 'headless']
    if any(sig in ua for sig in bot_signals):
        logger.debug(f"Funnel bot filter: skipping {stage} from {ua[:50]}")
        return

    ext_source, ext_medium, referrer_url = _extract_source(request)
    source = source or ext_source
    medium = medium or ext_medium
    # Merge referrer_url into metadata
    if referrer_url:
        metadata = dict(metadata or {})
        metadata['referrer_url'] = referrer_url

    # Try to get session ID from cookie or generate one
    session_id = request.cookies.get('session', '') or request.cookies.get('_ga', '')

    track(
        stage=stage,
        source=source,
        medium=medium,
        user_id=user_id,
        session_id=session_id[:100] if session_id else None,
        metadata=metadata,
    )
