"""Investor-materials access gate — v5.89.39.

Centralizes the gating logic for /architecture and /thesis. The flow:

  1. Visitor hits a gated page → has_valid_cookie() checks the cookie
     → if valid, mark access and let the route render normally
     → if not, route shows the request form
  2. Form submission → record_request() creates an AccessRequest row
     → notify_operator() emails the admin with approve/deny links
     → submitter sees a "you'll hear back" page
  3. Operator clicks the approve link from the email → approve_request()
     → generates magic_token, emails the submitter the magic link
  4. Submitter clicks magic link → consume_magic_token()
     → sets cookie, redirects to the page they originally wanted

All token generation uses secrets.token_urlsafe for cryptographic
unguessability. Cookies are HTTP-only and SameSite=Lax to prevent
CSRF leakage. The cookie value is the cookie_token, which is checked
against the AccessRequest row on every gated-page request.

Trusted email domains are auto-approved (status='auto_approved'),
operator still gets notified but doesn't need to take action.
"""
from __future__ import annotations
import os
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

# Trusted email domain suffixes → auto-approved. Set ACCESS_GATE_TRUSTED_DOMAINS
# env var as comma-separated list to extend (e.g. ".edu,ycombinator.com").
_DEFAULT_TRUSTED_DOMAINS = []  # empty by default — operator opts in via env

# Cookie config
COOKIE_NAME = 'investor_access'
COOKIE_TTL_DAYS = 90

# Magic-link config — single-use, short-lived
MAGIC_LINK_TTL_HOURS = 72  # 3 days to click

# Which pages are gated
GATED_PATHS = {'/architecture', '/thesis', '/architecture.html', '/thesis.html'}


def trusted_domains() -> list:
    """Return the list of trusted email domain suffixes (auto-approval)."""
    raw = os.environ.get('ACCESS_GATE_TRUSTED_DOMAINS', '').strip()
    if raw:
        return [d.strip().lower() for d in raw.split(',') if d.strip()]
    return _DEFAULT_TRUSTED_DOMAINS


def is_trusted_email(email: str) -> bool:
    """True if the email's domain matches any trusted suffix."""
    if not email or '@' not in email:
        return False
    domain = email.split('@', 1)[1].lower().strip()
    for suffix in trusted_domains():
        # ".edu" matches any .edu address; "ycombinator.com" matches exactly
        if suffix.startswith('.'):
            if domain.endswith(suffix):
                return True
        else:
            if domain == suffix or domain.endswith('.' + suffix):
                return True
    return False


def has_valid_cookie(request, db) -> Optional['AccessRequest']:  # type: ignore # noqa
    """Return the AccessRequest row associated with the visitor's cookie,
    or None. Updates last_accessed_at + access_count on hit."""
    from models import AccessRequest
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None

    row = AccessRequest.query.filter_by(cookie_token=token).first()
    if row is None:
        return None

    # Check revocation + expiry
    if row.cookie_revoked:
        return None
    if row.cookie_expires_at and datetime.utcnow() > row.cookie_expires_at:
        return None

    # Hit — update audit fields. We use a separate try/except so a write
    # failure can't deny access; gate is read-only on failure.
    try:
        row.last_accessed_at = datetime.utcnow()
        row.access_count = (row.access_count or 0) + 1
        db.session.commit()
    except Exception as e:
        logging.warning(f'access_gate: failed to update access count: {e}')
        db.session.rollback()
    return row


def create_request(
    db,
    name: str,
    email: str,
    company: str,
    role: str,
    reason: str,
    page_requested: str,
    ip_address: str,
    user_agent: str,
) -> Tuple['AccessRequest', bool]:  # type: ignore # noqa
    """Create a new AccessRequest row. Returns (row, auto_approved).
    If email matches a trusted domain, auto-approves and issues a magic
    token + cookie token immediately.
    """
    from models import AccessRequest

    auto = is_trusted_email(email)
    status = 'auto_approved' if auto else 'pending'

    row = AccessRequest(
        name=name[:150],
        email=email[:255].lower().strip(),
        company=(company or '')[:200],
        role=(role or '')[:100],
        reason=(reason or '')[:5000],
        page_requested=page_requested[:50],
        ip_address=(ip_address or '')[:45],
        user_agent=(user_agent or '')[:500],
        status=status,
    )

    if auto:
        # Pre-mint the tokens so the email handler can send the magic link
        # immediately. Same code path as manual approval.
        row.reviewed_at = datetime.utcnow()
        row.reviewed_by = 'auto (trusted domain)'
        row.magic_token = secrets.token_urlsafe(32)
        row.magic_sent_at = datetime.utcnow()

    db.session.add(row)
    db.session.commit()
    return row, auto


def approve_request(db, request_id: int, reviewer: str = 'admin') -> Optional['AccessRequest']:  # type: ignore # noqa
    """Mark a pending request approved. Generates the magic token. The
    caller is responsible for sending the email (see send_magic_link_email).
    Returns the row if approved, None if not found / already actioned.
    """
    from models import AccessRequest
    row = AccessRequest.query.get(request_id)
    if not row:
        return None
    if row.status not in ('pending',):
        # Already approved/denied — idempotent return
        return row

    row.status = 'approved'
    row.reviewed_at = datetime.utcnow()
    row.reviewed_by = reviewer[:200] if reviewer else 'admin'
    row.magic_token = secrets.token_urlsafe(32)
    row.magic_sent_at = datetime.utcnow()
    db.session.commit()
    return row


def deny_request(db, request_id: int, reviewer: str = 'admin', note: str = '') -> Optional['AccessRequest']:  # type: ignore # noqa
    """Mark a request denied. No email is sent — the submitter just
    doesn't hear back, which is intentional (don't tip off prospectors)."""
    from models import AccessRequest
    row = AccessRequest.query.get(request_id)
    if not row:
        return None
    if row.status not in ('pending',):
        return row

    row.status = 'denied'
    row.reviewed_at = datetime.utcnow()
    row.reviewed_by = reviewer[:200] if reviewer else 'admin'
    if note:
        row.review_note = note[:5000]
    db.session.commit()
    return row


def consume_magic_token(db, token: str) -> Optional['AccessRequest']:  # type: ignore # noqa
    """Validate a magic link click. Single-use: sets magic_consumed_at on
    first call and generates the long-lived cookie_token. Subsequent calls
    with the same token check magic_consumed_at and ignore if already used.

    Returns the AccessRequest row on success (caller sets the cookie),
    None on failure (invalid token, expired, etc.).
    """
    from models import AccessRequest
    if not token:
        return None

    row = AccessRequest.query.filter_by(magic_token=token).first()
    if not row:
        return None

    if row.status not in ('approved', 'auto_approved'):
        return None

    # Already consumed? Re-issue is OK if the cookie_token still exists
    # (so a visitor who clicks the magic link twice doesn't break).
    # But ONLY if cookie hasn't been revoked and is still valid.
    if row.magic_consumed_at:
        if (row.cookie_token and not row.cookie_revoked
                and row.cookie_expires_at and datetime.utcnow() < row.cookie_expires_at):
            return row  # caller will re-set the cookie
        return None

    # Check magic link expiry
    if row.magic_sent_at:
        expires = row.magic_sent_at + timedelta(hours=MAGIC_LINK_TTL_HOURS)
        if datetime.utcnow() > expires:
            return None

    # Consume — generate the long-lived cookie token
    row.magic_consumed_at = datetime.utcnow()
    row.cookie_token = secrets.token_urlsafe(32)
    row.cookie_issued_at = datetime.utcnow()
    row.cookie_expires_at = datetime.utcnow() + timedelta(days=COOKIE_TTL_DAYS)
    row.cookie_revoked = False
    db.session.commit()
    return row


def revoke_cookie(db, request_id: int) -> bool:
    """Revoke a previously-granted cookie. Visitor would need to re-request."""
    from models import AccessRequest
    row = AccessRequest.query.get(request_id)
    if not row:
        return False
    row.cookie_revoked = True
    db.session.commit()
    return True


# ─────────────────────────────────────────────────────────────────────
# Email senders. Both use the same Resend pattern that exists elsewhere
# in app.py for consistency. Failures are logged but don't break the
# user-facing flow — operator gets a notification gap, but the request
# is still recorded.
# ─────────────────────────────────────────────────────────────────────

def _admin_email() -> str:
    """Where notification emails go. Falls back to a sensible default."""
    return os.environ.get('ADMIN_NOTIFY_EMAIL', 'hello@getofferwise.ai')


def _public_origin(request) -> str:
    """Build https://www.getofferwise.ai or whatever the canonical origin
    is. Used for constructing the magic links + approve/deny URLs."""
    # Prefer explicit env var if set (handles reverse-proxy edge cases)
    env = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')
    if env:
        return env
    # Fall back to request.host_url (Flask-built)
    return request.host_url.rstrip('/')


def notify_operator_of_request(request_flask, row) -> bool:
    """Send the admin a one-click approve/deny email. Returns True on
    success. Failure is logged but not raised — visitor still sees the
    submitted confirmation page.
    """
    try:
        import resend
        api_key = os.environ.get('RESEND_API_KEY', '').strip()
        if not api_key:
            logging.warning('access_gate: RESEND_API_KEY not set, skipping notification')
            return False
        resend.api_key = api_key

        origin = _public_origin(request_flask)
        approve_url = f'{origin}/admin/access-requests/{row.id}/approve'
        deny_url = f'{origin}/admin/access-requests/{row.id}/deny'
        review_url = f'{origin}/admin/access-requests'

        subj = f'[OfferWise] Access request: {row.name} ({row.email})'
        body = f"""
<div style="font-family:system-ui,sans-serif;max-width:560px;color:#111">
  <h2 style="margin:0 0 12px">Access request</h2>
  <p style="color:#666;margin:0 0 18px;font-size:13px">Submitted via {row.page_requested} at {row.created_at.strftime('%Y-%m-%d %H:%M UTC')}</p>

  <table style="border-collapse:collapse;width:100%;margin-bottom:18px;font-size:14px">
    <tr><td style="padding:6px 0;color:#666;width:90px">Name</td><td style="padding:6px 0"><strong>{row.name}</strong></td></tr>
    <tr><td style="padding:6px 0;color:#666">Email</td><td style="padding:6px 0">{row.email}</td></tr>
    <tr><td style="padding:6px 0;color:#666">Company</td><td style="padding:6px 0">{row.company or '<em style="color:#999">—</em>'}</td></tr>
    <tr><td style="padding:6px 0;color:#666">Role</td><td style="padding:6px 0">{row.role or '<em style="color:#999">—</em>'}</td></tr>
    <tr><td style="padding:6px 0;color:#666;vertical-align:top">Reason</td><td style="padding:6px 0">{row.reason or '<em style="color:#999">—</em>'}</td></tr>
    <tr><td style="padding:6px 0;color:#666">IP</td><td style="padding:6px 0;font-family:ui-monospace,monospace;font-size:12px;color:#999">{row.ip_address}</td></tr>
  </table>

  <div style="margin:20px 0">
    <a href="{approve_url}" style="display:inline-block;background:#22c55e;color:white;padding:11px 22px;text-decoration:none;border-radius:6px;font-weight:600;margin-right:8px">✓ Approve &amp; send magic link</a>
    <a href="{deny_url}" style="display:inline-block;background:#374151;color:#cbd5e1;padding:11px 22px;text-decoration:none;border-radius:6px;font-weight:600">✗ Deny</a>
  </div>

  <p style="color:#999;font-size:12px;margin-top:24px">
    Or review all pending requests: <a href="{review_url}" style="color:#f97316">{review_url}</a>
  </p>
</div>
"""
        resend.Emails.send({
            'from': os.environ.get('FROM_EMAIL', 'OfferWise <noreply@getofferwise.ai>'),
            'to': [_admin_email()],
            'subject': subj,
            'html': body,
        })
        return True
    except Exception as e:
        logging.warning(f'access_gate: failed to send admin notification: {e}')
        return False


def send_magic_link_email(request_flask, row) -> bool:
    """Email the magic link to the requester. Called after approve_request()
    or after create_request() if auto-approved."""
    try:
        if not row.magic_token:
            logging.warning(f'access_gate: tried to send magic link for #{row.id} but no token set')
            return False

        import resend
        api_key = os.environ.get('RESEND_API_KEY', '').strip()
        if not api_key:
            logging.warning('access_gate: RESEND_API_KEY not set, skipping magic email')
            return False
        resend.api_key = api_key

        origin = _public_origin(request_flask)
        magic_url = f'{origin}/access/grant?token={row.magic_token}'

        # Friendly page-name for the email body
        page_name = 'Architecture deep-dive' if 'architecture' in (row.page_requested or '') else 'Investment thesis'

        subj = f'[OfferWise] Your access to {page_name}'
        body = f"""
<div style="font-family:system-ui,sans-serif;max-width:560px;color:#111;line-height:1.5">
  <h2 style="margin:0 0 14px">Hi {row.name.split()[0] if row.name else ''},</h2>
  <p>Thanks for your interest in OfferWise. Your access has been approved.</p>
  <p style="margin:20px 0">
    <a href="{magic_url}" style="display:inline-block;background:linear-gradient(90deg,#f97316,#f59e0b);color:white;padding:13px 28px;text-decoration:none;border-radius:6px;font-weight:600;font-size:15px">Open OfferWise &rarr;</a>
  </p>
  <p style="color:#666;font-size:13px">This link is single-use and expires in {MAGIC_LINK_TTL_HOURS} hours. After you click, your browser will remember the grant for {COOKIE_TTL_DAYS} days.</p>
  <p style="color:#666;font-size:13px">Once you're in, you'll have access to both our <strong>Architecture deep-dive</strong> and our <strong>Investment Thesis</strong>.</p>
  <p style="margin-top:28px;color:#666;font-size:12px">If you didn't request this, ignore this email.</p>
  <p style="color:#999;font-size:12px;margin-top:18px">— Francis<br>OfferWise</p>
</div>
"""
        resend.Emails.send({
            'from': os.environ.get('FROM_EMAIL', 'OfferWise <noreply@getofferwise.ai>'),
            'to': [row.email],
            'subject': subj,
            'html': body,
        })
        return True
    except Exception as e:
        logging.warning(f'access_gate: failed to send magic link to {row.email}: {e}')
        return False
