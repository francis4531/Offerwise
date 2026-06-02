"""
OfferWise Waitlist Routes Blueprint
Extracted from app.py v5.74.44 for architecture cleanup.
"""

import os
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, send_from_directory
from models import db, Waitlist, Subscriber

logger = logging.getLogger(__name__)

waitlist_bp = Blueprint('waitlist', __name__)

from blueprint_helpers import DeferredDecorator, make_deferred_limiter

_admin_required_ref = [None]
_api_admin_required_ref = [None]
_api_login_required_ref = [None]
_dev_only_gate_ref = [None]
_limiter_ref = [None]

_admin_required = DeferredDecorator(lambda: _admin_required_ref[0])
_api_admin_required = DeferredDecorator(lambda: _api_admin_required_ref[0])
_api_login_required = DeferredDecorator(lambda: _api_login_required_ref[0])
_dev_only_gate = DeferredDecorator(lambda: _dev_only_gate_ref[0])
_limiter = make_deferred_limiter(lambda: _limiter_ref[0])


def init_waitlist_blueprint(app, admin_required_fn, api_admin_required_fn,
                       api_login_required_fn, dev_only_gate_fn, limiter):
    _admin_required_ref[0] = admin_required_fn
    _api_admin_required_ref[0] = api_admin_required_fn
    _api_login_required_ref[0] = api_login_required_fn
    _dev_only_gate_ref[0] = dev_only_gate_fn
    _limiter_ref[0] = limiter
    app.register_blueprint(waitlist_bp)
    logger.info("✅ Waitlist Routes blueprint registered")



@waitlist_bp.route('/api/waitlist/community', methods=['POST'])
@_limiter.limit("10 per hour")
def join_community_waitlist():
    """Join the buyer community waitlist."""
    try:
        # v5.88.13: silent=True so missing body returns clean 400, not 500.
        # Same pattern as v5.88.09 (auth), v5.88.11 (analyses save),
        # v5.88.13 (consent record).
        data = request.get_json(silent=True) or {}
        email = data.get('email', '').strip().lower()
        
        # v5.88.13: tightened email validation. Previously '@' in email
        # and '.' in email accepted '@nodomain.com' and other malformed
        # addresses. Same pattern as v5.88.09 auth fix.
        if not email:
            return jsonify({'error': 'Invalid email'}), 400
        _at = email.find('@')
        if _at <= 0 or _at == len(email) - 1:
            return jsonify({'error': 'Invalid email'}), 400
        _local, _domain = email[:_at], email[_at+1:]
        if not _local or '.' not in _domain or _domain.startswith('.') or _domain.endswith('.'):
            return jsonify({'error': 'Invalid email'}), 400
        
        # Check for duplicate
        existing = Waitlist.query.filter_by(email=email, feature='community').first()
        if existing:
            return jsonify({'success': False, 'error': 'already_joined'}), 200
        
        entry = Waitlist(
            email=email,
            feature='community',
            source=data.get('source', 'unknown'),
            referrer=data.get('referrer', ''),
            had_result=data.get('had_result', False),
            result_score=data.get('result_score'),
            result_address=data.get('result_address'),
            result_grade=data.get('result_grade'),
            result_exposure=data.get('result_exposure'),
            result_zip=data.get('result_zip'),
            result_city=data.get('result_city'),
            result_state=data.get('result_state'),
        )
        # Generate unsubscribe token for drip campaign
        from drip_campaign import generate_unsubscribe_token
        entry.unsubscribe_token = generate_unsubscribe_token()
        db.session.add(entry)
        db.session.commit()
        
        # Get position
        position = Waitlist.query.filter_by(feature='community').count()
        
        logging.info(f"🏘️ Community waitlist signup #{position}: {email} (source: {data.get('source', '?')})")
        
        # Immediately send step 1 (welcome email) in background
        import threading
        from flask import current_app as _current_app
        _app = _current_app._get_current_object()
        def _send_welcome():
            try:
                with _app.app_context():
                    from drip_campaign import send_drip_email
                    # Re-fetch entry inside app context
                    fresh_entry = Waitlist.query.get(entry.id)
                    if fresh_entry and fresh_entry.drip_step == 0:
                        send_drip_email(fresh_entry, 1)
                        db.session.commit()
            except Exception as e:
                logging.error(f"Welcome email error for {email}: {e}")
        t = threading.Timer(10, _send_welcome)  # 10 second delay
        t.daemon = True
        t.start()
        
        return jsonify({'success': True, 'position': position})
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Waitlist error: {e}")
        return jsonify({'error': 'Failed to join waitlist'}), 500


@waitlist_bp.route('/api/waitlist/stats')
@_api_admin_required
def waitlist_stats():
    """Admin: get waitlist + drip campaign statistics for admin dashboard."""
    try:
        from models import EmailSendLog, User
        from sqlalchemy import func as _func

        all_entries = Waitlist.query.filter_by(feature='community').all()
        total       = len(all_entries)
        active      = sum(1 for w in all_entries if not w.email_unsubscribed)
        unsub       = sum(1 for w in all_entries if w.email_unsubscribed)
        completed   = sum(1 for w in all_entries if w.drip_completed)

        # "Converted" = waitlist email matches a User account
        all_emails = set(w.email.lower() for w in all_entries)
        user_emails = set(
            u.email.lower()
            for u in User.query.filter(User.email.in_(list(all_emails))).all()
        )
        converted = len(all_emails & user_emails)

        # Funnel: how many reached each drip step (cumulative)
        # step 0 = captured, steps 1-5 = emails sent, step 6 = monthly listings
        funnel = []
        for step in range(7):
            if step == 0:
                reached = total
            elif step <= 5:
                reached = sum(1 for w in all_entries if (w.drip_step or 0) >= step)
            else:
                # Monthly listings = drip_completed
                reached = completed
            funnel.append({'step': step, 'reached': reached})

        # Per-step send counts from EmailSendLog
        step_sends = {}
        for i in range(1, 6):
            key = f'drip_{i}'
            count = EmailSendLog.query.filter_by(email_type=key, success=True).count()
            step_sends[key] = count

        # Recent signups for the table
        recent = sorted(all_entries, key=lambda w: w.created_at or datetime.utcnow(), reverse=True)[:50]
        signups = [{
            'email':          w.email,
            'source':         w.source or 'direct',
            'drip_step':      w.drip_step or 0,
            'drip_completed': w.drip_completed or False,
            'unsubscribed':   w.email_unsubscribed or False,
            'had_result':     w.had_result or False,
            'result_score':   w.result_score,
            'result_grade':   w.result_grade or '',
            'result_zip':     w.result_zip or '',
            'result_address': w.result_address or '',
            'converted':      w.email.lower() in user_emails,
            'created_at':     w.created_at.isoformat() if w.created_at else None,
        } for w in recent]

        return jsonify({
            'total':      total,
            'active':     active,
            'unsubscribed': unsub,
            'drip_completed': completed,
            'converted':  converted,
            'funnel':     funnel,
            'step_sends': step_sends,
            'signups':    signups,
            # legacy fields kept for backward compat
            'with_results': sum(1 for w in all_entries if w.had_result),
        })
    except Exception as e:
        logging.error(f"Waitlist stats error: {e}")
        return jsonify({
            'total': 0, 'active': 0, 'unsubscribed': 0,
            'drip_completed': 0, 'converted': 0,
            'funnel': [], 'step_sends': {}, 'signups': []
        })


@waitlist_bp.route('/unsubscribe/<token>')
def unsubscribe_page(token):
    """Render the unsubscribe confirmation page (GET from email link)."""
    return send_from_directory('static', 'unsubscribe.html')


@waitlist_bp.route('/api/unsubscribe/<token>/status')
def unsubscribe_status(token):
    """Check if a token is valid and already unsubscribed."""
    entry = Waitlist.query.filter_by(unsubscribe_token=token).first()
    if not entry:
        return jsonify({'error': 'Invalid token'}), 404
    # Mask email: f***@gmail.com
    parts = entry.email.split('@')
    masked = parts[0][0] + '***@' + parts[1] if len(parts) == 2 else '***'
    return jsonify({
        'email': masked,
        'already_unsubscribed': entry.email_unsubscribed or False
    })


@waitlist_bp.route('/api/unsubscribe/<token>', methods=['POST'])
def unsubscribe_confirm(token):
    """
    Process unsubscribe — handles both:
    1. One-click List-Unsubscribe-Post from Gmail/Yahoo (RFC 8058)
    2. Manual confirmation from unsubscribe page
    """
    entry = Waitlist.query.filter_by(unsubscribe_token=token).first()
    if not entry:
        return jsonify({'error': 'Invalid token'}), 404

    if entry.email_unsubscribed:
        return jsonify({'success': True, 'already_unsubscribed': True})

    entry.email_unsubscribed = True
    entry.unsubscribed_at = datetime.utcnow()
    entry.drip_completed = True  # Stop drip sequence

    # Also unsubscribe from Subscriber table if they're there
    sub = Subscriber.query.filter_by(email=entry.email).first()
    if sub:
        sub.is_active = False

    db.session.commit()
    logging.info(f"📭 Unsubscribed: {entry.email} (token: {token[:8]}...)")

    return jsonify({'success': True})


# v5.88.68: A `/api/webhooks/resend` handler used to live below this line.
# It was dead code — Resend was configured (and still is) to POST to
# `/webhook/resend` (defined in app.py), not `/api/webhooks/resend`. The
# dead handler was a duplicate that never received traffic. Removing it
# prevents future confusion about which handler is real. The remaining
# webhook in this file (resend/inbound — for receiving replies as inbound
# mail) is unrelated and lives in webhooks_routes.py.
