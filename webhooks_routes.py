"""
webhooks_routes.py — v5.88.36

Inbound webhook endpoints. Currently just Resend Inbound (support@
emails), but factored as its own blueprint so future webhook integrations
(Stripe, etc. already live elsewhere — fine, but new ones land here)
have a natural home.

Design notes:
  - The route reads the RAW request body BEFORE Flask parses it as JSON,
    because the signature is over the raw bytes. Any re-encoding (e.g.,
    json.dumps(request.get_json())) breaks verification.
  - Signature verification is mandatory in production. Without
    RESEND_WEBHOOK_SECRET set, the route returns 503 (not configured) so
    test traffic can be distinguished from misconfiguration.
  - The route returns 200 quickly even for ignored messages (spam,
    duplicates). Returning non-200 makes Resend retry, which would just
    re-trigger the same skip logic. Idempotency by inbound_message_id
    means actually-duplicate webhook deliveries from real retries also
    no-op safely.
  - Body content is fetched via Resend's Emails.get(email_id) AFTER
    signature verification, since the webhook payload itself only carries
    metadata.
"""
import json
import logging
import os
from flask import Blueprint, request, jsonify

from support_service import (
    create_ticket_from_inbound_email,
    extract_body_text,
    fetch_inbound_email_body,
    has_inbound_message_been_processed,
    is_likely_spam_or_autoresponder,
    verify_resend_signature,
)


webhooks_bp = Blueprint('webhooks', __name__)


def init_webhooks_blueprint(app, *unused):
    """Register the webhooks blueprint. Called from app.py.

    Signature matches the standard init_*_blueprint pattern used by the
    other route modules so it can be registered in the same loop.
    Extra positional args (decorators, limiter, etc.) are accepted and
    ignored — webhooks are unauthenticated by design (verified by
    signature, not user session).
    """
    app.register_blueprint(webhooks_bp)
    logging.info('✅ Webhooks blueprint registered')


# ---------------------------------------------------------------------------
# POST /api/webhooks/resend/inbound
# ---------------------------------------------------------------------------

@webhooks_bp.route('/api/webhooks/resend/inbound', methods=['POST'])
def resend_inbound():
    """Receive email.received events from Resend Inbound.

    Flow:
      1. Read raw body (signature is over raw bytes)
      2. Verify signature against RESEND_WEBHOOK_SECRET
      3. Parse JSON. Skip non-email.received events.
      4. Spam/autoresponder filter — silently drop
      5. Idempotency check on inbound email_id
      6. Fetch the actual body via Resend Emails.get
      7. Threading + ticket creation via support_service helper

    Always returns 200 for ignored/skipped messages so Resend doesn't
    retry. Returns 401 only when signature is invalid (real misconfig).
    """
    secret = os.environ.get('RESEND_WEBHOOK_SECRET', '').strip()
    if not secret:
        logging.error('Resend inbound webhook hit but RESEND_WEBHOOK_SECRET not set')
        return jsonify({'error': 'webhook secret not configured'}), 503

    raw_body = request.get_data() or b''

    # Signature verification BEFORE doing anything else
    headers = dict(request.headers.items()) if request.headers else {}
    if not verify_resend_signature(raw_body, headers, secret):
        # 401 is the right code — caller didn't authenticate
        logging.warning('Resend inbound: invalid signature from %s',
                        request.headers.get('User-Agent', '?'))
        return jsonify({'error': 'invalid signature'}), 401

    # Parse JSON (now that signature is verified)
    try:
        event = json.loads(raw_body.decode('utf-8'))
    except Exception as e:
        logging.warning(f'Resend inbound: bad JSON: {e}')
        return jsonify({'error': 'bad json'}), 400

    event_type = event.get('type', '')
    if event_type != 'email.received':
        # Other event types may exist; we don't care. 200 so no retries.
        logging.info(f'Resend inbound: ignored event type {event_type!r}')
        return jsonify({'ignored': True, 'reason': 'event type'}), 200

    data = event.get('data') or {}
    email_id = data.get('email_id') or data.get('id')
    from_email = data.get('from') or ''
    to_field = data.get('to')
    subject = data.get('subject') or ''

    # `to` may be a list or a string depending on Resend version
    if isinstance(to_field, list):
        to_addresses = [str(x).strip().lower() for x in to_field if x]
    else:
        to_addresses = [str(to_field or '').strip().lower()] if to_field else []

    logging.info(
        f'📨 Resend inbound: id={email_id} from={from_email!r} '
        f'to={to_addresses!r} subject={subject!r}'
    )

    # Idempotency: did we already process this exact inbound email?
    # Resend retries failed webhooks; we don't want to double-thread.
    if email_id and has_inbound_message_been_processed(email_id):
        logging.info(f'Resend inbound: {email_id} already processed, skipping')
        return jsonify({'ignored': True, 'reason': 'duplicate'}), 200

    # Recipient filter — only handle mail to support@ (or env override).
    # Resend can deliver to any address on the domain, so we have to be
    # explicit. Defensive: also accept the legacy address pattern.
    support_addr = os.environ.get(
        'SUPPORT_EMAIL', 'support@getofferwise.ai'
    ).strip().lower()
    if support_addr and not any(addr == support_addr for addr in to_addresses):
        logging.info(
            f'Resend inbound: {email_id} not addressed to support '
            f'(to={to_addresses}, expected={support_addr})'
        )
        return jsonify({'ignored': True, 'reason': 'wrong recipient'}), 200

    # Fetch the actual body via the Receiving API — webhook only has metadata
    text_body, html_body = fetch_inbound_email_body(email_id)
    body = extract_body_text(text_body, html_body)

    # Spam / autoresponder check (after we have the body for context)
    is_spam, spam_reason = is_likely_spam_or_autoresponder(
        from_email, subject, body
    )
    if is_spam:
        logging.info(
            f'Resend inbound: {email_id} dropped as spam/autoresponder '
            f'({spam_reason})'
        )
        return jsonify({'ignored': True, 'reason': spam_reason}), 200

    # Hand off to the service helper (handles threading + ticket create)
    try:
        ticket, is_new = create_ticket_from_inbound_email(
            from_email=from_email,
            subject=subject,
            body=body or '(no body extracted)',
            inbound_message_id=email_id,
        )
        return jsonify({
            'success': True,
            'ticket_id': ticket.id,
            'is_new': is_new,
        }), 200
    except Exception as e:
        # Log the error AND return 200 — retrying the webhook won't help
        # if our ticket creation is broken; just record the failure.
        logging.exception(
            f'Resend inbound: ticket creation failed for {email_id}: {e}'
        )
        return jsonify({'error': 'ticket creation failed', 'detail': str(e)}), 200
