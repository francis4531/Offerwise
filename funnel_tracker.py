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
          session_id=None, metadata=None):
    """Record a funnel event. Fire-and-forget — never raises."""
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
    """Extract traffic source from request (utm params, referer, etc.)."""
    source = request.args.get('utm_source', '')
    medium = request.args.get('utm_medium', '')

    if not source:
        referer = request.headers.get('Referer', '')
        if 'google' in referer:
            source = 'google'
            medium = 'organic'
        elif 'reddit' in referer:
            source = 'reddit'
            medium = 'organic'
        elif 'facebook' in referer or 'fb.com' in referer:
            source = 'facebook'
            medium = 'social'
        elif referer and 'getofferwise' not in referer:
            source = 'referral'
            medium = 'referral'
        else:
            source = 'direct'
            medium = 'none'

    return source, medium


def track_from_request(stage, request, user_id=None, metadata=None):
    """Record a funnel event, auto-extracting source from the request."""
    # Bot filter — skip known crawlers
    ua = (request.headers.get('User-Agent', '') or '').lower()
    bot_signals = ['bot', 'crawl', 'spider', 'slurp', 'semrush', 'ahrefs',
                   'bytespider', 'googlebot', 'bingbot', 'yandex', 'baidu',
                   'facebookexternalhit', 'twitterbot', 'linkedinbot',
                   'curl', 'wget', 'python-requests', 'scrapy', 'headless']
    if any(sig in ua for sig in bot_signals):
        logger.debug(f"Funnel bot filter: skipping {stage} from {ua[:50]}")
        return

    source, medium = _extract_source(request)

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
