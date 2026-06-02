"""
support_service.py — v5.88.34

Centralized helpers for the Support workstation:
  - Ticket creation (used by in-product share + inbound email + manual)
  - Admin notification when tickets arrive
  - Status transition helpers
  - Email parsing helpers (subject normalization, ticket ID extraction)

This is the seam between the Ticket data model and everything that
creates/modifies tickets. Keeping it centralized means inbound email
(v5.88.36) plugs in here without touching the in-product code path,
and vice versa.

Design choices documented inline.
"""
import json
import logging
import os
import re
from datetime import datetime
from typing import Optional, Tuple

from models import db, Ticket, TicketMessage, User


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Where ticket notifications go. Reads from env at call time (not import
# time) so tests can override via patch.dict(os.environ, ...).
def _admin_notify_email() -> str:
    return (os.environ.get('SUPPORT_ADMIN_EMAIL')
            or os.environ.get('FOUNDER_EMAIL')
            or 'hello@getofferwise.ai')


# Subject prefix used to identify ticket ID in inbound email subjects.
# v5.88.36 inbound webhook will use this for thread matching.
TICKET_SUBJECT_PREFIX_RE = re.compile(r'\[Ticket\s+#(\d+)\]', re.IGNORECASE)


def format_ticket_subject(ticket_id: int, raw_subject: str) -> str:
    """Format a subject with the [Ticket #N] prefix for outbound replies.
    Idempotent — if the subject already has the prefix, returns unchanged."""
    if TICKET_SUBJECT_PREFIX_RE.search(raw_subject):
        return raw_subject
    return f'[Ticket #{ticket_id}] {raw_subject}'


def extract_ticket_id(subject: str) -> Optional[int]:
    """Pull a ticket ID out of an inbound email subject if present.
    Returns None if no [Ticket #N] prefix found."""
    m = TICKET_SUBJECT_PREFIX_RE.search(subject or '')
    if not m:
        return None
    try:
        return int(m.group(1))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Ticket creation
# ---------------------------------------------------------------------------

def create_ticket_from_in_product_share(
    user: User,
    property_obj,
    user_message: str,
    snapshot: dict,
    findings: list,
    full_result_json: str,
) -> Ticket:
    """Create a new Ticket from the in-product 'Share with support' button.

    Caller must already have validated:
      - user is logged in
      - property belongs to user
      - analysis is complete

    The first TicketMessage carries the analysis snapshot so admins can
    see exactly what the user was looking at when they asked for help.
    """
    # Subject: derived from property address. Truncated to fit DB column.
    address = (property_obj.address or 'Property analysis')[:200]
    raw_subject = f'Help with analysis: {address}'

    ticket = Ticket(
        user_id=user.id,
        subject=raw_subject[:255],
        property_id=property_obj.id,
        source='in_product_share',
        status='open',
    )
    db.session.add(ticket)
    db.session.flush()  # need ticket.id for the message FK

    # First message = the user's contact + the analysis context
    body = (user_message or '').strip() or '(No message provided)'
    msg = TicketMessage(
        ticket_id=ticket.id,
        author_kind='user',
        author_user_id=user.id,
        body=body,
        snapshot_json=json.dumps(snapshot) if snapshot else None,
        findings_json=json.dumps(findings) if findings else None,
        full_result_json=full_result_json,
    )
    db.session.add(msg)
    db.session.commit()

    logging.info(f'🎫 Ticket #{ticket.id} created from in-product share '
                 f'by user {user.id} ({user.email})')

    # Fire-and-forget admin notification. Failures here don't break the
    # ticket creation — the ticket is already saved.
    try:
        send_admin_notification(ticket, msg)
    except Exception as e:
        logging.warning(f'Failed to send admin notification for ticket #{ticket.id}: {e}')

    return ticket


def create_ticket_from_inbound_email(
    from_email: str,
    subject: str,
    body: str,
    inbound_message_id: Optional[str] = None,
) -> Tuple[Ticket, bool]:
    """Create a Ticket (or append to existing) from an inbound email.

    Used by the v5.88.36 inbound email webhook. Returns (ticket, is_new).

    Threading logic:
      1. If subject has [Ticket #N] prefix, look up that ticket and
         append this message to it.
      2. Otherwise, create a new ticket.

    If the from_email matches an existing user, the ticket is linked to
    that user. Otherwise the ticket is anonymous (email_for_anonymous).

    v5.88.34: scaffolding only.
    v5.88.36: now called by the /api/webhooks/resend/inbound route.
              Idempotency is enforced by the webhook BEFORE calling this
              (via has_inbound_message_been_processed), so this helper
              assumes inbound_message_id is fresh if provided.
    """
    from_email = (from_email or '').strip().lower()
    if not from_email or '@' not in from_email:
        raise ValueError(f'Invalid from_email: {from_email!r}')

    # Try to match an existing user by email
    user = User.query.filter(User.email.ilike(from_email)).first()

    # Threading: look for an existing ticket by subject prefix
    existing_ticket_id = extract_ticket_id(subject)
    if existing_ticket_id:
        ticket = Ticket.query.get(existing_ticket_id)
        if ticket:
            # Append to existing thread
            msg = TicketMessage(
                ticket_id=ticket.id,
                author_kind='user',
                author_user_id=user.id if user else None,
                author_email=None if user else from_email,
                body=body or '(Empty message body)',
                inbound_message_id=inbound_message_id,
            )
            db.session.add(msg)

            ticket.last_user_reply_at = datetime.utcnow()
            # If admin had closed it, auto-reopen
            if ticket.status == 'resolved':
                ticket.status = 'reopened'
            elif ticket.status == 'waiting_on_user':
                ticket.status = 'in_progress'

            db.session.commit()
            logging.info(f'🎫 Inbound email appended to ticket #{ticket.id}')
            return ticket, False

    # New ticket
    ticket = Ticket(
        user_id=user.id if user else None,
        email_for_anonymous=None if user else from_email,
        subject=(subject or '(No subject)')[:255],
        source='inbound_email',
        status='open',
    )
    db.session.add(ticket)
    db.session.flush()

    msg = TicketMessage(
        ticket_id=ticket.id,
        author_kind='user',
        author_user_id=user.id if user else None,
        author_email=None if user else from_email,
        body=body or '(Empty message body)',
        inbound_message_id=inbound_message_id,
    )
    db.session.add(msg)
    db.session.commit()

    logging.info(f'🎫 Ticket #{ticket.id} created from inbound email ({from_email})')

    try:
        send_admin_notification(ticket, msg)
    except Exception as e:
        logging.warning(f'Failed to send admin notification for ticket #{ticket.id}: {e}')

    return ticket, True


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

VALID_STATUSES = {'open', 'in_progress', 'waiting_on_user', 'resolved', 'reopened'}

# Encodes the allowed transitions. Any not listed is rejected.
# Reading: "from this state, you may transition to..."
ALLOWED_TRANSITIONS = {
    'open':            {'in_progress', 'resolved'},
    'in_progress':     {'waiting_on_user', 'resolved'},
    'waiting_on_user': {'in_progress', 'resolved'},
    'resolved':        {'reopened'},
    'reopened':        {'in_progress', 'resolved'},
}


# ---------------------------------------------------------------------------
# v5.88.39: Manual ticket creation (admin pastes details from their inbox)
# ---------------------------------------------------------------------------

def create_ticket_manually(
    from_email: str,
    subject: str,
    body: str,
    from_name: Optional[str] = None,
    linked_property_id: Optional[int] = None,
    actor_admin_id: Optional[int] = None,
) -> Ticket:
    """Create a new ticket from admin-pasted data.

    Used when the founder reads a customer email in their Gmail inbox
    and wants to log it as a ticket for tracking, history, and replies
    via the workstation. Bypasses the inbound webhook entirely — there
    is no inbound_message_id since this didn't come through Resend.

    Returns the new Ticket. Always creates new (no threading to existing
    tickets) — if the admin wants to thread into an existing ticket,
    they'd use the reply composer on that ticket instead.

    Args:
        from_email: customer's email address (required). If it matches
                    an existing user, the ticket is linked to that user.
        subject:    ticket subject (required, max 255 chars enforced by
                    the model).
        body:       first message body (required, but '(no body)' is
                    accepted so admins can log a 'they called me' note).
        from_name:  customer's display name (optional). Not stored
                    separately — falls back to the user record if linked,
                    or to the email local-part otherwise.
        linked_property_id: optional Property to link the ticket to.
                    Useful when the customer is asking about a specific
                    analysis. The admin UI can populate this from a
                    dropdown of recent properties.
        actor_admin_id: the admin user creating this ticket. Logged on
                    the system message for audit.

    Raises:
        ValueError: invalid email format, missing subject, missing body.
    """
    from_email = (from_email or '').strip().lower()
    if not from_email or '@' not in from_email:
        raise ValueError(f'Invalid from_email: {from_email!r}')

    subject = (subject or '').strip()
    if not subject:
        raise ValueError('subject is required')
    # Truncate to model's 255 char limit
    if len(subject) > 255:
        subject = subject[:252] + '...'

    body = (body or '').strip()
    if not body:
        raise ValueError('body is required (use "(no body)" if logging a phone call)')

    # Resolve linked user
    user = User.query.filter(User.email.ilike(from_email)).first()

    # Resolve linked property (only if provided AND it exists)
    linked_property = None
    if linked_property_id is not None:
        from models import Property
        linked_property = Property.query.get(linked_property_id)
        # Silently ignore bad property ID rather than failing the whole
        # ticket creation — the admin can fix the link later.

    # Create the ticket
    ticket = Ticket(
        user_id=user.id if user else None,
        email_for_anonymous=None if user else from_email,
        subject=subject,
        property_id=linked_property.id if linked_property else None,
        source='manual',
        status='open',
    )
    db.session.add(ticket)
    db.session.flush()  # get ticket.id

    # First message — author_kind='user' since this represents the
    # customer's original email, even though an admin typed it in.
    # Store the from_name on author_email field by hijacking the format,
    # OR more cleanly: leave it implicit. The ticket.display_name property
    # already handles user.name fallback to email local-part, which is
    # the right behavior. If from_name was provided and we have no user,
    # we just don't capture it — the admin can put it in the body or as
    # an internal note. Keeping the schema simple beats premature flexibility.
    first_msg = TicketMessage(
        ticket_id=ticket.id,
        author_kind='user',
        author_user_id=user.id if user else None,
        author_email=None if user else from_email,
        body=body,
    )
    db.session.add(first_msg)

    # System note recording who created this ticket and why. Helps later
    # audit ("why does this ticket have no email_id?" → "manual entry").
    actor_note = f'Ticket created manually by admin (id={actor_admin_id})'
    if from_name:
        actor_note += f'. Customer name as provided: {from_name!r}'
    if linked_property_id is not None and not linked_property:
        actor_note += f'. Property #{linked_property_id} link was invalid, skipped.'
    sys_msg = TicketMessage(
        ticket_id=ticket.id,
        author_kind='system',
        author_user_id=actor_admin_id,
        body=actor_note,
    )
    db.session.add(sys_msg)

    db.session.commit()
    logging.info(
        f'✏️  Ticket #{ticket.id} created manually by admin {actor_admin_id} '
        f'from {from_email!r} (subject: {subject!r})'
    )
    return ticket


def transition_ticket_status(ticket: Ticket, new_status: str,
                             actor_user_id: Optional[int] = None) -> bool:
    """Transition a ticket to a new status. Returns True if the
    transition was applied, False if it was disallowed.

    Records status changes as 'system' messages so the conversation
    history captures the workflow alongside the actual content."""
    if new_status not in VALID_STATUSES:
        logging.warning(f'Refused invalid status {new_status!r} for ticket #{ticket.id}')
        return False

    current = ticket.status
    if current == new_status:
        return True  # no-op, OK

    if new_status not in ALLOWED_TRANSITIONS.get(current, set()):
        logging.warning(f'Refused transition {current} -> {new_status} '
                        f'for ticket #{ticket.id}')
        return False

    ticket.status = new_status
    if new_status == 'resolved':
        ticket.resolved_at = datetime.utcnow()

    # System message in the thread
    msg = TicketMessage(
        ticket_id=ticket.id,
        author_kind='system',
        author_user_id=actor_user_id,
        body=f'Status changed: {current} -> {new_status}',
    )
    db.session.add(msg)
    db.session.commit()
    return True


# ---------------------------------------------------------------------------
# Admin reply (v5.88.35)
# ---------------------------------------------------------------------------

def send_admin_reply(
    ticket: Ticket,
    body: str,
    admin_user: Optional[User] = None,
    internal_note: bool = False,
) -> Tuple[Optional[TicketMessage], Optional[str]]:
    """Send (or save) an admin reply on a ticket.

    Args:
        ticket: the ticket being replied to
        body: the message body (plain text — we'll wrap in basic HTML)
        admin_user: the admin user composing the reply (for attribution).
                    May be None when admin is authenticated via admin_key
                    (no user session); the message still goes through but
                    author_user_id is null.
        internal_note: if True, the message is saved as an INTERNAL note
                       (author_kind='note') and NO email is sent. Useful
                       for "talked to user on phone, here's what we
                       discussed" or "remember to follow up next week".

    Returns:
        (message, error). On success: (TicketMessage, None). On failure:
        (None, error_string).

    Behavior:
        - Body is required and trimmed; empty body -> error.
        - For real replies (internal_note=False): email sent via Resend
          from SUPPORT_FROM_EMAIL, subject prefixed with [Ticket #N],
          Reply-To set to SUPPORT_EMAIL so customer replies bounce to
          the inbound webhook (when v5.88.36 ships).
        - For real replies, ticket status transitions to 'waiting_on_user'
          (from open / in_progress / reopened). If it was already in
          'waiting_on_user' or 'resolved', leave it alone.
        - first_admin_reply_at and last_admin_reply_at timestamps updated.
        - Notes don't change status or timestamps.
    """
    body = (body or '').strip()
    if not body:
        return None, 'Empty body — nothing to send.'

    # Save the message first (so even if email fails, we have a record).
    msg = TicketMessage(
        ticket_id=ticket.id,
        author_kind='note' if internal_note else 'admin',
        author_user_id=admin_user.id if admin_user else None,
        body=body,
    )
    db.session.add(msg)
    db.session.flush()  # get msg.id for logging

    # Internal note? No email, no status change. Save and done.
    if internal_note:
        db.session.commit()
        logging.info(f'📝 Internal note #{msg.id} added to ticket #{ticket.id}')
        return msg, None

    # Real reply: send email + transition status + update timestamps.
    to_email = ticket.reply_email
    if not to_email:
        # Save the message anyway (admin's text isn't lost), but flag.
        db.session.commit()
        logging.warning(f'Ticket #{ticket.id} has no reply email — message saved but not emailed')
        return msg, 'No customer email on file — reply saved but not sent.'

    # Compose email
    subject = format_ticket_subject(ticket.id, ticket.subject)
    sender_name = (admin_user.name if admin_user and admin_user.name
                   else 'OfferWise Support')

    html = _build_reply_html(body, sender_name, ticket.id)

    try:
        from email_service import send_email, SUPPORT_FROM_EMAIL, SUPPORT_EMAIL
        ok = send_email(
            to_email=to_email,
            subject=subject,
            html_content=html,
            reply_to=SUPPORT_EMAIL,       # customer replies to support@, not noreply@
            from_email=SUPPORT_FROM_EMAIL,
            email_type='support_reply',
            user_id=ticket.user_id,
        )
    except Exception as e:
        # Don't lose the message — commit it as already saved, but report error.
        db.session.commit()
        logging.error(f'Reply send failed for ticket #{ticket.id}: {e}')
        return msg, f'Email send failed: {e}'

    if not ok:
        db.session.commit()
        logging.warning(f'Reply for ticket #{ticket.id}: send_email returned falsy')
        return msg, 'Email service returned failure (check Resend config).'

    # Email sent. Update ticket timestamps and status.
    now = datetime.utcnow()
    if ticket.first_admin_reply_at is None:
        ticket.first_admin_reply_at = now
    ticket.last_admin_reply_at = now

    # Transition: an admin reply means we've engaged AND we're waiting for
    # the user's response. Collapse open / in_progress / reopened all to
    # 'waiting_on_user'. The transition_ticket_status workflow would only
    # allow this from in_progress or reopened; since the admin reply itself
    # is implicit proof of work, we move directly to waiting_on_user from
    # any of those entry states.
    if ticket.status in ('open', 'in_progress', 'reopened'):
        ticket.status = 'waiting_on_user'
    # If already 'waiting_on_user' or 'resolved', leave it (admin can
    # follow up on a waiting ticket; replying to a resolved ticket adds
    # context without re-opening — separate explicit reopen action exists).

    db.session.commit()
    logging.info(f'📨 Admin reply #{msg.id} sent on ticket #{ticket.id} to {to_email}')
    return msg, None


def _build_reply_html(body: str, sender_name: str, ticket_id: int) -> str:
    """Wrap an admin's plain-text reply body in a simple branded HTML email.

    Kept basic on purpose — no logos, no fancy CSS. Goal: looks like a
    person typed a reply, not a marketing blast. Inline styles only so
    Gmail/Outlook render consistently.
    """
    # Preserve line breaks
    body_html = _html_escape(body).replace('\n', '<br>')
    return f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;\
font-size:15px;line-height:1.55;color:#222;max-width:600px;margin:0 auto;padding:16px">
  <div style="white-space:pre-wrap">{body_html}</div>
  <div style="margin-top:24px;padding-top:14px;border-top:1px solid #e5e7eb;color:#6b7280;font-size:12px">
    — {_html_escape(sender_name)}<br>
    OfferWise Support
  </div>
  <div style="margin-top:8px;color:#9ca3af;font-size:10px">
    Reply to this email to continue the conversation (Ref: ticket #{ticket_id}).
  </div>
</div>
"""


# ---------------------------------------------------------------------------
# Admin notification
# ---------------------------------------------------------------------------

def send_admin_notification(ticket: Ticket, first_message: TicketMessage) -> bool:
    """Email the admin when a new ticket arrives.

    Returns True if dispatched, False if the email send failed or was
    skipped (e.g. RESEND_API_KEY not configured in test env)."""
    admin_email = _admin_notify_email()
    if not admin_email:
        return False

    try:
        from email_service import send_email
    except ImportError:
        logging.warning('email_service unavailable — admin notification skipped')
        return False

    subject = f'[Ticket #{ticket.id}] New {ticket.source.replace("_", " ")} from {ticket.display_name}'

    # Construct a useful body. Plain-ish HTML — Resend renders it nicely.
    body_preview = (first_message.body or '')[:500]
    if len(first_message.body or '') > 500:
        body_preview += '...'

    property_line = ''
    if ticket.linked_property:
        addr = ticket.linked_property.address or 'Unknown address'
        property_line = f'<p><strong>Property:</strong> {_html_escape(addr)}</p>'

    html = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:600px">
      <h2 style="color:#1a73e8;margin-bottom:8px">New support ticket #{ticket.id}</h2>
      <p style="color:#666;margin-top:0;font-size:13px">
        Source: {_html_escape(ticket.source.replace('_', ' '))}
      </p>
      <p><strong>From:</strong> {_html_escape(ticket.display_name)}
         &lt;{_html_escape(ticket.reply_email or 'no-email')}&gt;</p>
      {property_line}
      <div style="background:#f5f5f5;padding:14px;border-radius:6px;margin:14px 0">
        <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Message</div>
        <div style="white-space:pre-wrap;font-size:14px">{_html_escape(body_preview)}</div>
      </div>
      <p style="margin-top:18px">
        <a href="https://www.getofferwise.ai/admin?admin_key=___YOUR_KEY___#view-support"
           style="background:#1a73e8;color:#fff;padding:10px 16px;border-radius:6px;text-decoration:none;display:inline-block">
          Open in admin
        </a>
      </p>
      <p style="color:#888;font-size:11px;margin-top:18px">
        Ticket created at {ticket.created_at.strftime('%Y-%m-%d %H:%M UTC')}.
        Reply to this email — your reply will be sent to the user.
        (Note: inbound email handling ships in v5.88.36; until then, reply
         manually from the admin UI when the composer ships in v5.88.35.)
      </p>
    </div>
    """

    try:
        # send_email signature: (to_email, subject, html_content, reply_to=None, ...)
        # We pass reply_to as the user's actual email so the founder can hit
        # Reply directly to respond (manual workflow for v5.88.34).
        result = send_email(
            to_email=admin_email,
            subject=subject,
            html_content=html,
            reply_to=ticket.reply_email,
        )
        if result:
            logging.info(f'📨 Admin notification sent for ticket #{ticket.id}')
            return True
        else:
            logging.warning(f'Admin notification for #{ticket.id} returned falsy: {result!r}')
            return False
    except Exception as e:
        logging.warning(f'Admin notification for #{ticket.id} raised: {e}')
        return False


def _html_escape(s: str) -> str:
    """Minimal HTML escape so user input doesn't break our notification body."""
    return (str(s or '')
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


# ---------------------------------------------------------------------------
# v5.88.36: Inbound email helpers (Resend Inbound webhook)
# ---------------------------------------------------------------------------

# Spam patterns. These are deliberately conservative — we want to catch
# obvious noise without dropping legitimate support requests. The cost of a
# false negative (noise creates a ticket) is one wasted admin click; the
# cost of a false positive (legit user gets silently ignored) is a lost
# customer. So: lean toward accepting borderline cases.

# Senders whose mail is almost always automated. Match against the local
# part OR the lowercase from address.
_AUTOMATED_SENDER_PATTERNS = (
    'mailer-daemon', 'postmaster', 'noreply', 'no-reply', 'do-not-reply',
    'donotreply', 'auto-reply', 'autoreply', 'notifications@',
    'bounce', 'delivery-failure',
)

# Subjects that are almost always autoresponders or system mail.
_AUTORESPONDER_SUBJECT_PATTERNS = (
    'out of office', 'auto reply', 'auto-reply', 'automatic reply',
    'autoreply',
    'delivery status notification', 'undeliverable',
    'mail delivery failed', 'failure notice', 'returned mail',
    'message could not be delivered',
)


def is_likely_spam_or_autoresponder(from_email: str, subject: str,
                                    body: str = '') -> Tuple[bool, str]:
    """Decide whether an inbound message should be silently dropped.

    Returns (is_spam, reason). reason is human-readable for logging only.

    This filter runs BEFORE ticket creation. If it returns True, the
    webhook acknowledges the message and discards it. No ticket created.

    Conservative design — we'd rather create a ticket for borderline
    automated mail than drop a legit customer reply.
    """
    fe = (from_email or '').strip().lower()
    su = (subject or '').strip().lower()

    if not fe or '@' not in fe:
        return True, 'no valid from address'

    # Self-loop protection: if we somehow sent mail back to ourselves,
    # don't create a ticket from it. Otherwise an admin reply that bounces
    # back through forwarding rules could create infinite tickets.
    own_domain_marks = (
        'getofferwise.ai', '@offerwise.', 'noreply@', 'support@getofferwise',
    )
    if any(m in fe for m in own_domain_marks):
        return True, f'from own/related address ({fe})'

    # Automated sender patterns
    for pat in _AUTOMATED_SENDER_PATTERNS:
        if pat in fe:
            return True, f'automated sender pattern: {pat}'

    # Autoresponder subject patterns
    for pat in _AUTORESPONDER_SUBJECT_PATTERNS:
        if pat in su:
            return True, f'autoresponder subject pattern: {pat}'

    # Empty subject + empty body = nothing to ticket on
    body_stripped = (body or '').strip()
    if not su and not body_stripped:
        return True, 'empty subject and body'

    return False, ''


def verify_resend_signature(raw_body: bytes, headers: dict,
                            secret: str) -> bool:
    """Verify a Resend webhook signature.

    Resend uses Svix-style HMAC-SHA256 signing. The signed string is:
      f"{msg_id}.{msg_timestamp}.{raw_body}"
    The signature header carries one or more space-separated entries of
    the form "v1,<base64-hmac>". A match on any of them passes.

    Args:
        raw_body: the EXACT request body bytes (no re-serialization —
                  Resend signs the raw bytes; JSON re-encoding changes
                  whitespace and breaks the signature).
        headers: dict of HTTP headers. Case-insensitive; we lower-case
                 keys before lookup.
        secret: the webhook secret string from Resend's dashboard.
                Format: 'whsec_<base64>'. The base64 part is the actual
                HMAC key after stripping the prefix.

    Returns:
        True if any signature in the header matches.
    """
    import hmac
    import hashlib
    import base64

    if not secret:
        # In test environments without a secret configured, we don't
        # accept anything — fail closed.
        logging.warning('verify_resend_signature: no secret configured')
        return False

    # Normalize header keys to lowercase
    h = {(k or '').lower(): v for k, v in (headers or {}).items()}
    msg_id = h.get('svix-id') or h.get('webhook-id')
    msg_ts = h.get('svix-timestamp') or h.get('webhook-timestamp')
    sig_hdr = h.get('svix-signature') or h.get('webhook-signature')

    if not (msg_id and msg_ts and sig_hdr):
        logging.warning('verify_resend_signature: missing svix headers')
        return False

    # Strip 'whsec_' prefix if present and base64-decode the key
    raw_secret = secret
    if raw_secret.startswith('whsec_'):
        raw_secret = raw_secret[len('whsec_'):]
    try:
        key = base64.b64decode(raw_secret)
    except Exception:
        # Some setups use the raw string (not base64). Fall back to that.
        key = raw_secret.encode('utf-8')

    # Build the signed string
    signed = f'{msg_id}.{msg_ts}.{raw_body.decode("utf-8", errors="replace")}'
    expected = base64.b64encode(
        hmac.new(key, signed.encode('utf-8'), hashlib.sha256).digest()
    ).decode('ascii')

    # The signature header carries entries like "v1,<sig> v1,<sig2>"
    # (space-separated). Check each. Use constant-time comparison.
    for entry in sig_hdr.split(' '):
        entry = entry.strip()
        if not entry or ',' not in entry:
            continue
        version, sig = entry.split(',', 1)
        if version.lower() != 'v1':
            continue
        if hmac.compare_digest(sig.strip(), expected):
            return True

    return False


def fetch_inbound_email_body(email_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Fetch the (text, html) body of an inbound email by Resend's email_id.

    Resend's webhook only includes metadata. The body lives behind a
    separate API call: resend.Emails.get(email_id).

    Returns (text_body, html_body). Either or both can be None if the
    email has no body in that format (e.g. text-only emails have no html).

    On any error (network, auth, missing), returns (None, None) and logs.
    """
    if not email_id:
        return None, None

    try:
        import resend
        from email_service import RESEND_API_KEY
        if not RESEND_API_KEY:
            logging.warning('fetch_inbound_email_body: no RESEND_API_KEY')
            return None, None
        resend.api_key = RESEND_API_KEY

        email_obj = resend.Emails.get(email_id=email_id)
        # The SDK returns a dict-like object. Field names vary by SDK
        # version; try the most common shapes.
        if isinstance(email_obj, dict):
            text = email_obj.get('text') or email_obj.get('text_body')
            html = email_obj.get('html') or email_obj.get('html_body')
        else:
            text = getattr(email_obj, 'text', None) or getattr(email_obj, 'text_body', None)
            html = getattr(email_obj, 'html', None) or getattr(email_obj, 'html_body', None)
        return text, html
    except Exception as e:
        logging.warning(f'fetch_inbound_email_body: failed for {email_id}: {e}')
        return None, None


def extract_body_text(text_body: Optional[str], html_body: Optional[str]) -> str:
    """Get the best plain-text representation of an email body.

    Prefers text/plain. Falls back to a crude strip of HTML tags from
    text/html. Returns empty string if neither has content.

    Quote-stripping (removing the "> On <date>, X wrote:" tail) is NOT
    done here — we keep the full body so the admin can see what the user
    quoted from previous messages. v5.88.37 may add quote collapse in
    the UI.
    """
    if text_body and text_body.strip():
        return text_body.strip()
    if html_body and html_body.strip():
        # Crude HTML strip. Good enough for body preview; not a full
        # parser. Replaces <br>/<p> with newlines, then drops other tags.
        import re as _re
        cleaned = _re.sub(r'<br\s*/?>', '\n', html_body, flags=_re.IGNORECASE)
        cleaned = _re.sub(r'</p>', '\n', cleaned, flags=_re.IGNORECASE)
        cleaned = _re.sub(r'<[^>]+>', '', cleaned)
        # Collapse runs of whitespace
        cleaned = _re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()
    return ''


def has_inbound_message_been_processed(inbound_message_id: str) -> bool:
    """Idempotency check: did we already create a TicketMessage from this
    inbound email_id? Resend retries failed webhooks; without this guard,
    a slow response could duplicate the message in the thread."""
    if not inbound_message_id:
        return False
    existing = TicketMessage.query.filter_by(
        inbound_message_id=inbound_message_id
    ).first()
    return existing is not None


# ---------------------------------------------------------------------------
# v5.88.37: Aging indicators
# ---------------------------------------------------------------------------

# Threshold: how long a ticket can sit in 'open' or 'waiting_on_user'
# before the UI flags it as stale. 24h matches the founder's directive.
# 'open' = no admin engagement yet → counts from created_at
# 'waiting_on_user' = admin replied, customer hasn't → counts from last_admin_reply_at
# Other statuses ('in_progress', 'resolved', 'reopened') don't age.
STALE_THRESHOLD_HOURS = 24


def ticket_age_info(ticket: Ticket) -> dict:
    """Compute aging metadata for a ticket. Used in the list endpoint
    so the UI doesn't have to re-derive on every render.

    Returns a dict:
      {
        'age_hours': float,   # hours since the relevant timestamp
        'is_stale': bool,     # True if age_hours > STALE_THRESHOLD_HOURS
                              # and the status is one that ages
        'aging_basis': str,   # which timestamp we measured from, or 'n/a'
      }
    """
    if not ticket:
        return {'age_hours': 0.0, 'is_stale': False, 'aging_basis': 'n/a'}

    now = datetime.utcnow()
    basis_ts = None
    basis_name = 'n/a'

    if ticket.status == 'open':
        basis_ts = ticket.created_at
        basis_name = 'open since'
    elif ticket.status == 'waiting_on_user':
        # Time we've been waiting for the customer. If somehow no admin
        # reply was recorded (data inconsistency), fall back to updated_at.
        basis_ts = ticket.last_admin_reply_at or ticket.updated_at
        basis_name = 'waiting since admin reply'
    elif ticket.status == 'reopened':
        # Reopened ages from when it was reopened, which is updated_at
        # since the transition writes that timestamp. Reasonable
        # approximation; v5.88.38 could add a dedicated reopened_at column.
        basis_ts = ticket.updated_at
        basis_name = 'reopened since'

    if basis_ts is None:
        return {'age_hours': 0.0, 'is_stale': False, 'aging_basis': basis_name}

    age_seconds = (now - basis_ts).total_seconds()
    age_hours = age_seconds / 3600.0
    is_stale = (age_hours > STALE_THRESHOLD_HOURS) and ticket.status in (
        'open', 'waiting_on_user', 'reopened'
    )
    return {
        'age_hours': round(age_hours, 1),
        'is_stale': is_stale,
        'aging_basis': basis_name,
    }


# ---------------------------------------------------------------------------
# v5.88.37: Templates
# ---------------------------------------------------------------------------

# Default templates seeded into the DB on first boot. Names should be
# short — they're displayed as buttons in the composer. Bodies are
# starting points; the admin tweaks them before sending.
#
# Variables resolved: {user_name}, {user_email}, {ticket_id},
# {property_address}, {offerscore}, {risk_tier}
DEFAULT_TEMPLATES = [
    {
        'name': 'Thanks, looking into it',
        'sort_order': 10,
        'body': (
            "Hi {user_name},\n\n"
            "Thanks for reaching out. I'm looking into this and will get "
            "back to you within the next business day.\n\n"
            "Just to confirm I have the right details: the property is "
            "{property_address}, and your OfferScore came in at "
            "{offerscore}. Let me know if anything there is off.\n\n"
            "Talk soon."
        ),
    },
    {
        'name': 'Followup needed',
        'sort_order': 20,
        'body': (
            "Hi {user_name},\n\n"
            "Quick followup on your earlier note. A few questions to help "
            "me dig in:\n\n"
            "  1. Are you working with an inspector already, or still "
            "deciding?\n"
            "  2. What's your timeline for making an offer?\n"
            "  3. Anything you noticed in the analysis that didn't match "
            "what you saw on the walkthrough?\n\n"
            "Reply when you have a minute and I'll go from there."
        ),
    },
    {
        'name': 'Refund / credit issue',
        'sort_order': 30,
        'body': (
            "Hi {user_name},\n\n"
            "Sorry about the trouble with credits. I can see the analysis "
            "didn't complete the way it should have. I'll restore your "
            "credits in the next few minutes and confirm here once it's "
            "done.\n\n"
            "If you'd like to re-run the analysis on {property_address}, "
            "you can do that whenever — no charge."
        ),
    },
    {
        'name': 'Closing the loop',
        'sort_order': 40,
        'body': (
            "Hi {user_name},\n\n"
            "Glad we got that sorted. I'm going to mark this ticket "
            "resolved — but if anything else comes up about "
            "{property_address} (or any other property), just reply to "
            "this thread and it'll come right back to me."
        ),
    },
]


def seed_default_templates() -> int:
    """Ensure default templates exist. Called once per app boot.

    Returns the number of new templates inserted (0 if all already present).

    Idempotent: matches by name. Templates with is_seeded=True can be
    overwritten on subsequent boots only if their body has changed.
    Admin-edited templates (is_seeded=False) are never touched.
    """
    from models import TicketTemplate
    inserted = 0
    for tpl in DEFAULT_TEMPLATES:
        existing = TicketTemplate.query.filter_by(name=tpl['name']).first()
        if existing is None:
            t = TicketTemplate(
                name=tpl['name'],
                body=tpl['body'],
                sort_order=tpl['sort_order'],
                is_seeded=True,
            )
            db.session.add(t)
            inserted += 1
        # Don't overwrite admin edits. If admin renamed/edited a template,
        # they own it now.

    if inserted:
        try:
            db.session.commit()
            logging.info(f'🌱 Seeded {inserted} default ticket templates')
        except Exception as e:
            db.session.rollback()
            logging.warning(f'Template seeding failed: {e}')
            return 0
    return inserted


# Pattern matches {variable} where variable is [a-z_]+ — keeps the
# substitution surface small and predictable. Anything not in the
# supported set is left as-is, and the helper reports it.
_TEMPLATE_VAR_RE = re.compile(r'\{([a-z_]+)\}')


def render_template(body: str, ticket: Ticket) -> Tuple[str, list]:
    """Substitute {var} placeholders using ticket context.

    Returns (rendered_text, unresolved_var_names).

    Unresolved variables are LEFT IN PLACE in the body. The caller
    (UI) can decide whether to warn the admin or send anyway. This
    prevents silent data loss if someone typos {custmer_name}.

    Snapshot data is pulled from the first TicketMessage. For
    inbound_email tickets there's no snapshot, so {offerscore},
    {risk_tier}, and {property_address} resolve to '(not available)'.
    """
    if not body or not ticket:
        return body or '', []

    # Build the context dict
    ctx = {
        'user_name': ticket.display_name or '(unknown)',
        'user_email': ticket.reply_email or '(unknown)',
        'ticket_id': str(ticket.id),
        'property_address': '(not available)',
        'offerscore': '(not available)',
        'risk_tier': '(not available)',
    }

    # Try to pull snapshot data from the first TicketMessage
    first_msg = ticket.messages.order_by(None).order_by('id').first()
    if first_msg and first_msg.snapshot_json:
        try:
            snap = json.loads(first_msg.snapshot_json)
            if isinstance(snap, dict):
                if snap.get('address'):
                    ctx['property_address'] = str(snap['address'])
                if snap.get('offerscore') is not None:
                    ctx['offerscore'] = str(snap['offerscore'])
                if snap.get('risk_tier'):
                    ctx['risk_tier'] = str(snap['risk_tier'])
        except Exception:
            pass

    # If the ticket has a linked_property but no snapshot was on the
    # first message, use the property address directly.
    if (ctx['property_address'] == '(not available)'
            and ticket.linked_property
            and ticket.linked_property.address):
        ctx['property_address'] = ticket.linked_property.address

    unresolved = []
    def _sub(match):
        var = match.group(1)
        if var in ctx:
            return ctx[var]
        if var not in unresolved:
            unresolved.append(var)
        return match.group(0)  # leave as-is

    rendered = _TEMPLATE_VAR_RE.sub(_sub, body)
    return rendered, unresolved
