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
        data = request.get_json() or {}
        email = data.get('email', '').strip().lower()
        
        if not email or '@' not in email or '.' not in email:
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
    """Admin: get waitlist statistics."""
    try:
        total = Waitlist.query.filter_by(feature='community').count()
        with_results = Waitlist.query.filter_by(feature='community', had_result=True).count()
        
        # Recent signups
        recent = Waitlist.query.filter_by(feature='community').order_by(
            Waitlist.created_at.desc()).limit(20).all()
        
        signups = [{
            'email': w.email,
            'source': w.source,
            'had_result': w.had_result,
            'result_score': w.result_score,
            'created_at': w.created_at.isoformat() if w.created_at else None
        } for w in recent]
        
        return jsonify({
            'total': total,
            'with_results': with_results,
            'signups': signups
        })
    except Exception as e:
        logging.error(f"Waitlist stats error: {e}")
        return jsonify({'total': 0, 'with_results': 0, 'signups': []})


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


# ═══════════════════════════════════════════════════════════════════
# RESEND WEBHOOKS — email open/click tracking for drip effectiveness
# ═══════════════════════════════════════════════════════════════════

@waitlist_bp.route('/api/webhooks/resend', methods=['POST'])
def resend_webhook():
    """
    Receive Resend webhook events for email engagement tracking.
    
    Setup: Resend Dashboard → Webhooks → Add Endpoint:
      URL: https://www.getofferwise.ai/api/webhooks/resend
      Events: email.opened, email.clicked, email.bounced, email.complained
    
    Stores events in EmailEngagement table for drip effectiveness dashboard.
    """
    import hmac, hashlib
    
    # Verify webhook signature if secret is configured
    webhook_secret = os.environ.get('RESEND_WEBHOOK_SECRET', '')
    if webhook_secret:
        signature = request.headers.get('resend-signature', '')
        payload = request.get_data()
        expected = hmac.new(webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            logger.warning("⚠️ Resend webhook signature mismatch")
            return jsonify({'error': 'Invalid signature'}), 401
    
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'No data'}), 400
    
    event_type = data.get('type', '')
    event_data = data.get('data', {})
    
    # Extract email info
    to_email = ''
    if isinstance(event_data.get('to'), list) and event_data['to']:
        to_email = event_data['to'][0]
    elif isinstance(event_data.get('to'), str):
        to_email = event_data['to']
    
    subject = event_data.get('subject', '')
    email_id = event_data.get('email_id', '')
    
    # Determine drip step from subject line
    drip_step = None
    if 'OfferWise' in subject:
        import re as _re
        step_match = _re.search(r'drip[_\s]?(\d+)', subject, _re.IGNORECASE)
        if step_match:
            drip_step = int(step_match.group(1))
    
    # Log the event
    logger.info(f"📧 Resend webhook: {event_type} for {to_email} (step={drip_step})")
    
    # Store in DB
    try:
        
        # For now, log to the structured logger — can add EmailEngagement table later
        # when there's enough volume to warrant a dedicated table
        import json as _json
        logger.info(_json.dumps({
            'event': 'email_engagement',
            'type': event_type,
            'to': to_email,
            'subject': subject,
            'email_id': email_id,
            'drip_step': drip_step,
            'timestamp': event_data.get('created_at'),
        }))
    except Exception as e:
        logger.error(f"Resend webhook processing error: {e}")
    
    return jsonify({'received': True}), 200
