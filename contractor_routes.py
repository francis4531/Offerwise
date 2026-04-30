"""
OfferWise Contractor Routes Blueprint
=====================================
Extracted from app.py to reduce monolith size.
Contains all /api/contractor/* and /contractor/* page routes.
"""

import os
import re
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from flask import Blueprint, jsonify, request, redirect, send_from_directory, current_app
from flask_login import login_required, current_user
from blueprint_helpers import DeferredDecorator

from models import (
    db, User, Inspector, Contractor, ContractorLead, Analysis, Property,
    Agent, AgentShare, InspectorReport, PropertyWatch,
    ContractorLeadClaim,
)

import os as _os
ADMIN_EMAIL = _os.environ.get('ADMIN_EMAIL', 'hello@getofferwise.ai')

def send_email(*a, **kw):
    from app import send_email as _se; return _se(*a, **kw)


contractor_bp = Blueprint('contractor', __name__)
logger = logging.getLogger(__name__)

# ── Injected dependencies ──────────────────────────────────────────
_login_required     = None
_admin_required     = None
_limiter            = None

_login_req_dec = DeferredDecorator(lambda: _login_required)
_admin_req_dec = DeferredDecorator(lambda: _admin_required)


def init_contractor_blueprint(app, db_ref, login_required_fn, admin_required_fn):
    global _login_required, _admin_required, _limiter
    _login_required = login_required_fn
    _admin_required = admin_required_fn
    app.register_blueprint(contractor_bp)
    logging.info("✅ contractor blueprint registered")


@contractor_bp.route('/api/contractor/signup', methods=['POST'])
def contractor_signup():
    """Public signup — works logged in or out. Uses current_user.email if logged in."""
    data = request.get_json() or {}
    name  = (data.get('name') or data.get('business_name') or '').strip()

    # Use current user's email if logged in, else require it in payload
    from flask_login import current_user as _cu
    if _cu.is_authenticated:
        email = _cu.email.strip().lower()
        # Also pre-fill name from user account if not provided
        if not name:
            name = (_cu.name or '').strip()
    else:
        email = (data.get('email') or '').strip().lower()

    if not name:
        return jsonify({'error': 'Business name is required.'}), 400
    if not email:
        return jsonify({'error': 'Email is required. Please log in or provide your email.'}), 400

    # Deduplicate by email
    existing = Contractor.query.filter_by(email=email).first()
    if existing:
        return jsonify({'success': True, 'already_exists': True, 'contractor_id': existing.id,
                        'message': "You're already in our network. We'll be in touch when leads match your area."})

    # Parse trades — accept comma string or list
    raw_trades = data.get('trades') or ''
    if isinstance(raw_trades, list):
        trades_str = ','.join(t.strip() for t in raw_trades if t.strip())
    else:
        trades_str = raw_trades.strip()

    c = Contractor(
        name            = name,
        business_name   = (data.get('business_name') or name).strip(),
        email           = email,
        phone           = (data.get('phone') or '').strip(),
        website         = (data.get('website') or '').strip(),
        license_number  = (data.get('license_number') or '').strip(),
        license_state   = (data.get('license_state') or 'CA').strip(),
        trades          = trades_str,
        trade_notes     = (data.get('trade_notes') or '').strip(),
        service_cities  = (data.get('service_cities') or '').strip(),
        service_zips    = (data.get('service_zips') or '').strip(),
        service_radius_miles = int(data.get('service_radius_miles') or 25),
        avg_job_size    = int(data.get('avg_job_size') or 0) or None,
        status          = 'active',  # self-service — no admin gate needed
        source          = 'portal' if _cu.is_authenticated else 'website',
    )
    db.session.add(c)
    db.session.commit()
    logging.info(f"🔨 New contractor signup: {c.business_name or c.name} ({c.email}) trades={c.trades}")

    # Notify admin
    try:
        trades_display = c.trades.replace(',', ', ') if c.trades else '—'
        send_email(
            to_email=ADMIN_EMAIL,
            subject=f"🔨 New Contractor Signup: {c.business_name or c.name}",
            html_content=f"""<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;">
            <div style="font-size:20px;font-weight:800;margin-bottom:16px;color:#f97316;">🔨 New Contractor Signup</div>
            <table style="width:100%;border-collapse:collapse;">
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;width:130px;">Name</td><td style="padding:6px 0;font-weight:600;">{c.name}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Business</td><td style="padding:6px 0;">{c.business_name or '—'}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Email</td><td style="padding:6px 0;">{c.email}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Phone</td><td style="padding:6px 0;">{c.phone or '—'}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">License</td><td style="padding:6px 0;">{c.license_number or '—'} ({c.license_state})</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Trades</td><td style="padding:6px 0;color:#f59e0b;font-weight:700;">{trades_display}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Service area</td><td style="padding:6px 0;">{c.service_cities or c.service_zips or '—'}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Avg job size</td><td style="padding:6px 0;">{('$' + str(c.avg_job_size)) if c.avg_job_size else '—'}</td></tr>
            </table>
            {f'<div style="margin-top:12px;padding:12px;background:rgba(255,255,255,.05);border-radius:8px;font-size:13px;color:#94a3b8;">{c.trade_notes}</div>' if c.trade_notes else ''}
            <div style="margin-top:16px;font-size:11px;color:#475569;">Contractor ID #{c.id} · Auto-activated · Can access marketplace immediately</div>
            </div>"""
        )
    except Exception as e:
        logging.error(f"Contractor signup email failed: {e}")

    return jsonify({'success': True, 'contractor_id': c.id})



@contractor_bp.route('/api/contractor/me', methods=['GET', 'POST'])
@_login_req_dec
def contractor_me():
    """Logged-in contractor's own profile — read and update."""
    try:
        c = Contractor.query.filter_by(email=current_user.email).first()
    except Exception as _qe:
        logging.warning(f"contractor_me query failed (migration pending?): {_qe}")
        return jsonify({'registered': False, 'migration_pending': True})
    if request.method == 'POST':
        if not c:
            return jsonify({'error': 'No contractor account found for this email.'}), 404
        data = request.get_json() or {}
        for field in ['name', 'business_name', 'phone', 'website', 'trades',
                      'service_zips', 'service_cities', 'license_number', 'license_state']:
            if field in data:
                setattr(c, field, data[field])
        db.session.commit()
        return jsonify({'success': True})

    if not c:
        return jsonify({'registered': False})

    # Leads claimed (marketplace model)
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    claims_this_month = ContractorLeadClaim.query.filter(
        ContractorLeadClaim.contractor_id == c.id,
        ContractorLeadClaim.created_at >= month_start,
        ContractorLeadClaim.status != 'passed',
    ).count()
    claims_all_time = ContractorLeadClaim.query.filter(
        ContractorLeadClaim.contractor_id == c.id,
        ContractorLeadClaim.status != 'passed',
    ).count()
    leads_this_month = claims_this_month
    leads_all_time   = claims_all_time
    # Recent claimed leads
    recent_claims = ContractorLeadClaim.query.filter_by(contractor_id=c.id)        .order_by(ContractorLeadClaim.created_at.desc()).limit(10).all()
    recent_leads = []
    for claim in recent_claims:
        lead = ContractorLead.query.get(claim.lead_id)
        if lead: recent_leads.append(lead)

    plan_info = {
        'free':                  {'label': 'Free (Pending)',  'color': '#64748b', 'limit': '—'},
        'contractor_starter':    {'label': 'Starter',         'color': '#f97316', 'limit': '5/month'},
        'contractor_pro':        {'label': 'Pro',             'color': '#22c55e', 'limit': 'Unlimited'},
        'contractor_enterprise': {'label': 'Enterprise',      'color': '#a78bfa', 'limit': 'Statewide'},
    }
    plan = getattr(c, 'plan', 'free') or 'free'
    pi = plan_info.get(plan, plan_info['free'])

    return jsonify({
        'registered': True,
        'id': c.id,
        'name': c.name,
        'business_name': c.business_name,
        'email': c.email,
        'phone': c.phone,
        'website': c.website,
        'trades': c.trades_list(),
        'service_zips': c.service_zips,
        'service_cities': c.service_cities,
        'license_number': c.license_number,
        'license_state': c.license_state,
        'status': c.status,
        'plan': plan,
        'plan_label': pi['label'],
        'plan_color': pi['color'],
        'plan_lead_limit': pi['limit'],
        'plan_activated_at': c.plan_activated_at.isoformat() if c.plan_activated_at else None,
        'leads_this_month': leads_this_month,
        'leads_all_time': leads_all_time,
        'recent_leads': [{
            'id': l.id,
            'repair_system': l.repair_system,
            'property_address': l.property_address,
            'contact_timing': l.contact_timing,
            'cost_estimate': l.cost_estimate,
            'sent_at': l.sent_to_contractor_at.isoformat() if l.sent_to_contractor_at else None,
            'status': l.status,
        } for l in recent_leads],
    })



@contractor_bp.route('/api/contractor-lead', methods=['POST'])
@_login_req_dec
def submit_contractor_lead():
    """Save a contractor quote request and notify admin + matching contractors."""
    data = request.get_json() or {}

    # Extract ZIP from address if not provided
    address = data.get('property_address', '')
    zip_code = data.get('property_zip', '')
    if not zip_code:
        import re
        m = re.search(r'\b(\d{5})\b', address)
        zip_code = m.group(1) if m else ''

    repair_system = data.get('repair_system', '')

    # ── Deduplication: same user, same address, same repair within 24h ──
    from datetime import timedelta as _td
    recent_cutoff = datetime.utcnow() - _td(hours=24)
    dupe = ContractorLead.query.filter(
        ContractorLead.user_id == current_user.id,
        ContractorLead.property_address == address,
        ContractorLead.repair_system == repair_system,
        ContractorLead.created_at >= recent_cutoff,
    ).first()
    if dupe:
        logging.info(f"Duplicate lead suppressed for user {current_user.id} ({repair_system} at {address})")
        return jsonify({'success': True, 'lead_id': dupe.id, 'duplicate': True})

    from datetime import timedelta as _td_lead
    _now = datetime.utcnow()
    lead = ContractorLead(
        user_id           = current_user.id,
        user_email        = current_user.email,
        user_name         = data.get('user_name', current_user.name or ''),
        user_phone        = data.get('user_phone', ''),
        property_address  = address,
        property_zip      = zip_code,
        repair_system     = repair_system,
        trade_needed      = data.get('trade_needed', ''),
        cost_estimate     = data.get('cost_estimate', ''),
        issue_description = data.get('issue_description', ''),
        contact_timing    = data.get('contact_timing', 'this_week'),
        status            = 'available',
        available_at      = _now,
        expires_at        = _now + _td_lead(hours=48),
        claim_count       = 0,
    )
    db.session.add(lead)
    db.session.commit()

    logging.info(f"🔧 NEW CONTRACTOR LEAD #{lead.id}: {lead.repair_system} for {lead.user_email} at {lead.property_address}")
    try:
        from funnel_tracker import track_from_request
        track_from_request('contractor_lead_submitted', request,
                           user_id=current_user.id if current_user.is_authenticated else None,
                           metadata={'repair_system': repair_system, 'zip': zip_code})
    except Exception: pass

    timing_labels = {'asap': '🔴 ASAP', 'this_week': 'This week', 'just_exploring': 'Just exploring'}
    timing = timing_labels.get(lead.contact_timing, lead.contact_timing)

    # ── Email admin ──────────────────────────────────────────────────────
    try:
        html = f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;">
          <div style="font-size:22px;font-weight:800;margin-bottom:16px;color:#f97316;">🔧 New Contractor Lead #{lead.id}</div>
          <table style="width:100%;border-collapse:collapse;">
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;width:140px;">Repair needed</td><td style="padding:8px 0;font-weight:700;text-transform:capitalize;">{lead.repair_system}</td></tr>
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;">Trade</td><td style="padding:8px 0;">{lead.trade_needed}</td></tr>
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;">Est. cost</td><td style="padding:8px 0;color:#f59e0b;font-weight:700;">{lead.cost_estimate}</td></tr>
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;">Property</td><td style="padding:8px 0;">{lead.property_address}</td></tr>
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;">ZIP</td><td style="padding:8px 0;">{lead.property_zip or '—'}</td></tr>
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;">User</td><td style="padding:8px 0;">{lead.user_name} · {lead.user_email}</td></tr>
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;">Phone</td><td style="padding:8px 0;font-weight:700;color:#22c55e;">{lead.user_phone or '(not provided)'}</td></tr>
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;">Timing</td><td style="padding:8px 0;font-weight:700;">{timing}</td></tr>
          </table>
          {f'<div style="margin-top:12px;padding:12px;background:rgba(255,255,255,0.05);border-radius:8px;font-size:13px;color:#94a3b8;">{lead.issue_description}</div>' if lead.issue_description else ''}
          <div style="margin-top:16px;font-size:11px;color:#475569;">Lead ID #{lead.id} · {lead.created_at.strftime('%Y-%m-%d %H:%M')} UTC</div>
        </div>
        """
        send_email(
            to_email=ADMIN_EMAIL,
            subject=f"🔧 Contractor Lead #{lead.id}: {lead.repair_system.title()} — {lead.property_address[:40]}",
            html_content=html,
        )
    except Exception as e:
        logging.error(f"Failed to send admin lead email: {e}")

    # ── Marketplace notification: alert eligible contractors ──────────────
    # Lead is posted to marketplace (status='available'). Contractors self-select.
    # We send a heads-up email to matching contractors to check their portal.
    try:
        from datetime import timedelta as _td_match
        visibility_windows = {'contractor_enterprise':72,'contractor_pro':48,'contractor_starter':24,'free':6}
        matching_contractors = Contractor.query.filter(
            Contractor.status == 'active',
            Contractor.available == True,
            Contractor.plan.in_(['contractor_starter', 'contractor_pro', 'contractor_enterprise']),
        ).all()

        notified = 0
        for contractor in matching_contractors:
            my_trades = [t.strip().lower() for t in (contractor.trades or '').split(',') if t.strip()]
            my_zips   = [z.strip() for z in (contractor.service_zips or '').split(',') if z.strip()]
            trade_key = (lead.repair_system or '').lower()
            trade_match = (not my_trades or any(trade_key in t or t in trade_key for t in my_trades) or 'general' in my_trades)
            if not trade_match: continue
            if contractor.plan != 'contractor_enterprise' and my_zips and lead.property_zip:
                if lead.property_zip not in my_zips: continue

            timing_str = timing_labels.get(lead.contact_timing, lead.contact_timing or '—')
            urgency_color = '#ef4444' if lead.contact_timing == 'asap' else '#f59e0b'
            try:
                send_email(
                    to_email=contractor.email,
                    subject=f"🏪 New lead on marketplace: {(lead.repair_system or '').title()} in {lead.property_zip or 'your area'}",
                    html_content=f"""<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;">
                      <div style="font-size:20px;font-weight:800;margin-bottom:4px;color:#f97316;">🏪 New lead on your marketplace</div>
                      <div style="font-size:13px;color:#94a3b8;margin-bottom:20px;">A buyer needs a <strong style="color:#f59e0b;">{lead.repair_system}</strong> contractor in <strong>{lead.property_zip or 'your area'}</strong>. Claim it before another contractor does.</div>
                      <table style="width:100%;border-collapse:collapse;">
                        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;width:130px;">Repair</td><td style="padding:7px 0;font-weight:700;text-transform:capitalize;color:#f59e0b;">{lead.repair_system}</td></tr>
                        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Est. cost</td><td style="padding:7px 0;font-weight:700;">{lead.cost_estimate or '—'}</td></tr>
                        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Area</td><td style="padding:7px 0;">{lead.property_zip or '—'} (California)</td></tr>
                        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Timing</td><td style="padding:7px 0;font-weight:700;color:{urgency_color};">{timing_str}</td></tr>
                        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Expires</td><td style="padding:7px 0;color:#64748b;">48 hours from now</td></tr>
                      </table>
                      <div style="margin-top:20px;padding:14px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:8px;font-size:13px;color:#94a3b8;">
                        Buyer details unlock only after you claim. <strong style="color:#f97316;">Claim this lead in your portal →</strong> getofferwise.ai/contractor-portal
                      </div>
                      <div style="margin-top:12px;font-size:11px;color:#475569;">Lead #{lead.id} · OfferWise Contractor Marketplace</div>
                    </div>"""
                )
                notified += 1
            except Exception as e_mail:
                logging.warning(f"Marketplace notify failed for contractor {contractor.id}: {e_mail}")

        logging.info(f"📢 Lead #{lead.id} posted to marketplace, {notified} contractor(s) notified")

    except Exception as e:
        logging.error(f"Marketplace notification failed for lead #{lead.id}: {e}", exc_info=True)

    # ── Buyer confirmation email ──────────────────────────────────────────
    if lead.user_email:
        try:
            buyer_html = f"""
            <div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:28px;border-radius:12px;">
              <div style="font-size:22px;font-weight:800;margin-bottom:8px;">Your quote request is in ✅</div>
              <div style="font-size:14px;color:#94a3b8;margin-bottom:20px;">Hi {lead.user_name or 'there'} — we've received your request and are matching you with licensed contractors in your area.</div>
              <div style="padding:16px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:10px;margin-bottom:20px;">
                <div style="font-weight:700;color:#f97316;margin-bottom:8px;text-transform:capitalize;">{lead.repair_system} at {lead.property_address}</div>
                <div style="font-size:13px;color:#94a3b8;line-height:1.7;">
                  Estimated cost: <strong style="color:#f59e0b;">{lead.cost_estimate or 'TBD'}</strong><br>
                  Timing: <strong style="color:#f1f5f9;">{timing_labels.get(lead.contact_timing,'—').replace('🔴 ','')}</strong>
                </div>
              </div>
              <div style="font-size:13px;color:#94a3b8;line-height:1.8;">
                <strong style="color:#f1f5f9;">What happens next:</strong><br>
                A licensed contractor in your area will reach out to you directly by phone or email — usually within 24 hours. You'll get 1–3 quotes so you can compare.<br><br>
                No obligation. No middleman fees. You deal directly with the contractor.
              </div>
              <div style="margin-top:20px;font-size:11px;color:#475569;">Reference: Lead #{lead.id} · OfferWise AI</div>
            </div>"""
            send_email(
                to_email=lead.user_email,
                subject=f"Your {lead.repair_system} quote request — contractors will reach out within 24h",
                html_content=buyer_html,
            )
        except Exception as e:
            logging.warning(f"Buyer confirmation email failed for lead #{lead.id}: {e}")

    return jsonify({'success': True, 'lead_id': lead.id})



@contractor_bp.route('/api/contractor/marketplace', methods=['GET'])
@_login_req_dec
def contractor_marketplace():
    """Return leads visible to this contractor. Plan gates visibility window."""
    contractor = Contractor.query.filter_by(email=current_user.email).first()
    if not contractor:
        return jsonify({'error': 'Not registered as a contractor'}), 403

    plan = contractor.plan or 'free'
    visibility_hours = {'contractor_enterprise':72,'contractor_pro':48,'contractor_starter':24,'free':6}.get(plan, 6)

    from datetime import timedelta as _td
    cutoff = datetime.utcnow() - _td(hours=visibility_hours)

    leads = ContractorLead.query.filter(
        ContractorLead.status.in_(['available', 'claimed']),
        ContractorLead.created_at >= cutoff,
    ).order_by(ContractorLead.created_at.desc()).all()

    my_trades = [t.strip().lower() for t in (contractor.trades or '').split(',') if t.strip()]
    my_zips   = [z.strip() for z in (contractor.service_zips or '').split(',') if z.strip()]
    my_claims = {c.lead_id: c.status for c in ContractorLeadClaim.query.filter_by(contractor_id=contractor.id).all()}

    def city_hint(address):
        if not address: return 'California'
        parts = [p.strip() for p in address.split(',')]
        return parts[-2] if len(parts) > 2 else parts[0]

    result = []
    for lead in leads:
        trade_key = (lead.repair_system or '').lower()
        trade_match = (not my_trades or any(trade_key in t or t in trade_key for t in my_trades) or 'general' in my_trades)
        if not trade_match: continue
        if plan != 'contractor_enterprise' and my_zips and lead.property_zip:
            if lead.property_zip not in my_zips: continue

        is_mine = lead.id in my_claims
        result.append({
            'id':              lead.id,
            'repair_system':   lead.repair_system,
            'trade_needed':    lead.trade_needed,
            'cost_estimate':   lead.cost_estimate,
            'property_zip':    lead.property_zip,
            'property_area':   city_hint(lead.property_address),
            'property_address': lead.property_address if is_mine else None,
            'contact_timing':  lead.contact_timing,
            'issue_summary':   (lead.issue_description or '')[:200] if is_mine else None,
            'created_at':      lead.created_at.isoformat(),
            'expires_at':      lead.expires_at.isoformat() if lead.expires_at else None,
            'claim_count':     lead.claim_count or 0,
            'my_claim_status': my_claims.get(lead.id),
            'buyer_name':      lead.user_name  if is_mine else None,
            'buyer_email':     lead.user_email if is_mine else None,
            'buyer_phone':     lead.user_phone if is_mine else None,
        })

    return jsonify({'leads': result, 'contractor': {'plan': plan, 'available': contractor.available, 'visibility_hours': visibility_hours}})



@contractor_bp.route('/api/contractor/leads/<int:lead_id>/claim', methods=['POST'])
@_login_req_dec
def contractor_claim_lead(lead_id):
    """Claim a marketplace lead — unlocks buyer contact info."""
    contractor = Contractor.query.filter_by(email=current_user.email).first()
    if not contractor:
        return jsonify({'error': 'Not registered as a contractor'}), 403
    if contractor.status == 'suspended':
        return jsonify({'error': 'Account suspended.'}), 403
    if not contractor.available:
        return jsonify({'error': 'You are marked unavailable. Toggle availability first.'}), 400

    lead = ContractorLead.query.get(lead_id)
    if not lead or lead.status not in ('available', 'claimed'):
        return jsonify({'error': 'Lead not available'}), 404
    if lead.expires_at and lead.expires_at < datetime.utcnow():
        return jsonify({'error': 'Lead has expired'}), 400

    existing = ContractorLeadClaim.query.filter_by(lead_id=lead_id, contractor_id=contractor.id).first()
    if existing:
        return jsonify({'error': 'Already claimed', 'claim_status': existing.status}), 400

    # ── PROTECTION 1: Per-lead cap — max 3 contractors per lead ──────────
    MAX_CLAIMS_PER_LEAD = 3
    current_claims = lead.claim_count or 0
    if current_claims >= MAX_CLAIMS_PER_LEAD:
        return jsonify({'error': f'This lead already has {MAX_CLAIMS_PER_LEAD} contractors interested. The buyer has enough options.'}), 400

    # ── PROTECTION 2: Monthly claim limit by plan ─────────────────────────
    plan = contractor.plan or 'free'
    monthly_limits = {
        'free':                  0,    # must upgrade (handled by UI gate, enforced here too)
        'contractor_starter':    10,
        'contractor_pro':        50,
        'contractor_enterprise': 9999, # unlimited
    }
    monthly_limit = monthly_limits.get(plan, 0)
    if monthly_limit == 0:
        return jsonify({'error': 'Upgrade your plan to claim leads.'}), 403
    if monthly_limit < 9999:
        from datetime import timedelta as _td_m
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        claims_this_month = ContractorLeadClaim.query.filter(
            ContractorLeadClaim.contractor_id == contractor.id,
            ContractorLeadClaim.created_at >= month_start,
            ContractorLeadClaim.status != 'passed',
        ).count()
        if claims_this_month >= monthly_limit:
            return jsonify({'error': f'Monthly claim limit reached ({monthly_limit} for {plan.replace("contractor_","").title()} plan). Resets on the 1st.'}), 429

    # ── PROTECTION 3: Velocity limit — max 5 claims per hour ─────────────
    from datetime import timedelta as _td_v
    one_hour_ago = datetime.utcnow() - _td_v(hours=1)
    claims_last_hour = ContractorLeadClaim.query.filter(
        ContractorLeadClaim.contractor_id == contractor.id,
        ContractorLeadClaim.created_at >= one_hour_ago,
    ).count()
    if claims_last_hour >= 5:
        return jsonify({'error': 'Slow down — you can claim at most 5 leads per hour. Check back shortly.'}), 429

    claim = ContractorLeadClaim(lead_id=lead_id, contractor_id=contractor.id, status='claimed')
    db.session.add(claim)
    lead.status = 'claimed'
    lead.claim_count = (lead.claim_count or 0) + 1
    # Close to new claims once max reached
    if lead.claim_count >= MAX_CLAIMS_PER_LEAD:
        lead.status = 'claimed'  # already set — just noting it stops accepting new claims
    contractor.leads_sent_month = (contractor.leads_sent_month or 0) + 1
    contractor.leads_sent_total = (contractor.leads_sent_total or 0) + 1
    db.session.commit()
    logging.info(f"🔨 Lead #{lead_id} claimed by contractor #{contractor.id} ({contractor.email})")

    # Auto-create PropertyWatch so contractor gets agentic market alerts
    try:
        from models import PropertyWatch
        _existing_cw = PropertyWatch.query.filter_by(
            contractor_lead_id=lead_id, is_active=True
        ).first()
        if not _existing_cw and lead.property_address:
            _cw = PropertyWatch(
                user_id            = current_user.id,
                address            = lead.property_address,
                contractor_lead_id = lead_id,
                expires_at         = datetime.utcnow() + timedelta(days=45),
                owned_by_professional = True,
            )
            db.session.add(_cw)
            db.session.commit()
            logging.info(f"🔭 Contractor watch created: {lead.property_address}")
    except Exception as _cwe:
        logging.warning(f"Contractor watch creation failed (non-fatal): {_cwe}")

    timing_labels = {'asap':'🔴 ASAP','this_week':'This week','just_exploring':'Just exploring'}
    try:
        send_email(to_email=contractor.email,
            subject=f"✅ Lead claimed: {(lead.repair_system or '').title()} in {lead.property_zip or 'your area'}",
            html_content=f"""<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;">
              <div style="font-size:20px;font-weight:800;color:#22c55e;margin-bottom:16px;">✅ Lead claimed — contact the buyer now</div>
              <table style="width:100%;border-collapse:collapse;">
                <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;width:130px;">Repair</td><td style="padding:7px 0;font-weight:700;text-transform:capitalize;color:#f59e0b;">{lead.repair_system}</td></tr>
                <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Est. cost</td><td style="padding:7px 0;">{lead.cost_estimate or '—'}</td></tr>
                <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Property</td><td style="padding:7px 0;font-weight:600;">{lead.property_address}</td></tr>
                <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Buyer</td><td style="padding:7px 0;">{lead.user_name or '—'}</td></tr>
                <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Phone</td><td style="padding:7px 0;font-weight:700;color:#22c55e;">{lead.user_phone or '—'}</td></tr>
                <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Email</td><td style="padding:7px 0;">{lead.user_email}</td></tr>
                <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Timing</td><td style="padding:7px 0;">{timing_labels.get(lead.contact_timing, lead.contact_timing or '—')}</td></tr>
              </table>
              {f'<div style="margin-top:12px;padding:12px;background:rgba(255,255,255,.05);border-radius:8px;font-size:13px;color:#94a3b8;">{lead.issue_description}</div>' if lead.issue_description else ''}
            </div>""", reply_to=lead.user_email)
    except Exception as e:
        logging.error(f"Claim email failed: {e}")

    try:
        send_email(to_email=lead.user_email,
            subject=f"A contractor is interested in your {(lead.repair_system or '').title()} job",
            html_content=f"""<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;">
              <div style="font-size:20px;font-weight:800;color:#f97316;margin-bottom:12px;">🔨 A contractor wants your job</div>
              <p style="color:#94a3b8;"><strong style="color:#f1f5f9;">{contractor.business_name or contractor.name}</strong> expressed interest in your <strong style="color:#f59e0b;">{lead.repair_system}</strong> job. They will reach out to you directly.{f' You can also reach them at <strong style="color:#22c55e;">{contractor.phone}</strong>.' if contractor.phone else ''}</p>
            </div>""")
    except Exception as e:
        logging.error(f"Buyer notify failed: {e}")

    return jsonify({'success': True, 'lead': {
        'id': lead.id, 'property_address': lead.property_address,
        'buyer_name': lead.user_name, 'buyer_email': lead.user_email,
        'buyer_phone': lead.user_phone, 'repair_system': lead.repair_system,
        'cost_estimate': lead.cost_estimate, 'issue_description': lead.issue_description,
        'contact_timing': lead.contact_timing,
    }})



@contractor_bp.route('/api/contractor/leads/<int:lead_id>/contacted', methods=['POST'])
@_login_req_dec
def contractor_mark_contacted(lead_id):
    """Mark that the contractor has contacted the buyer on this lead."""
    contractor = Contractor.query.filter_by(email=current_user.email).first()
    if not contractor: return jsonify({'error': 'Not a contractor'}), 403
    claim = ContractorLeadClaim.query.filter_by(lead_id=lead_id, contractor_id=contractor.id).first()
    if not claim: return jsonify({'error': 'Lead not in your claims'}), 404
    claim.status = 'contacted'
    claim.contacted_at = datetime.utcnow()
    db.session.commit()

    # Recalculate contact rate for this contractor
    # If contact rate drops below 20% after 10+ claims, auto-suspend
    total_claims = ContractorLeadClaim.query.filter(
        ContractorLeadClaim.contractor_id == contractor.id,
        ContractorLeadClaim.status != 'passed',
    ).count()
    if total_claims >= 10:
        contacted = ContractorLeadClaim.query.filter(
            ContractorLeadClaim.contractor_id == contractor.id,
            ContractorLeadClaim.status.in_(['contacted', 'closed']),
        ).count()
        contact_rate = contacted / total_claims
        if contact_rate < 0.20:
            contractor.status = 'suspended'
            contractor.notes = (contractor.notes or '') + f'\nAuto-suspended {datetime.utcnow().date()}: contact rate {contact_rate:.0%} ({contacted}/{total_claims})'
            db.session.commit()
            logging.warning(f"⚠️ Contractor #{contractor.id} auto-suspended: contact rate {contact_rate:.0%}")
            try:
                send_email(
                    to_email=ADMIN_EMAIL,
                    subject=f"⚠️ Contractor auto-suspended: {contractor.business_name or contractor.name}",
                    html_content=f"""<div style="font-family:sans-serif;padding:20px;background:#0f172a;color:#f1f5f9;">
                        <h2 style="color:#ef4444;">Auto-suspension triggered</h2>
                        <p><strong>{contractor.business_name or contractor.name}</strong> ({contractor.email})</p>
                        <p>Contact rate: <strong style="color:#ef4444;">{contact_rate:.0%}</strong> ({contacted} contacted / {total_claims} claimed)</p>
                        <p>Threshold: 20% minimum after 10 claims.</p>
                        <p>Review in admin panel and reinstate if appropriate.</p>
                    </div>"""
                )
            except Exception: pass
            return jsonify({'success': True, 'warning': 'Low contact rate — account review triggered'})

    db.session.commit()
    return jsonify({'success': True, 'contact_rate': f"{(contacted/total_claims*100):.0f}%" if total_claims >= 10 else 'N/A'})



@contractor_bp.route('/api/contractor/leads/<int:lead_id>/pass', methods=['POST'])
@_login_req_dec
def contractor_pass_lead(lead_id):
    """Pass on a previously claimed lead."""
    contractor = Contractor.query.filter_by(email=current_user.email).first()
    if not contractor: return jsonify({'error': 'Not a contractor'}), 403
    claim = ContractorLeadClaim.query.filter_by(lead_id=lead_id, contractor_id=contractor.id).first()
    if not claim: return jsonify({'error': 'Lead not found in your claims'}), 404
    claim.status = 'passed'
    claim.passed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})



@contractor_bp.route('/api/contractor/leads/<int:lead_id>/close', methods=['POST'])
@_login_req_dec
def contractor_close_lead(lead_id):
    """Mark a lead as won/closed — now delegates to flywheel completion processor."""
    contractor = Contractor.query.filter_by(email=current_user.email).first()
    if not contractor: return jsonify({'error': 'Not a contractor'}), 403
    data = request.get_json() or {}
    from flywheel_notifications import process_contractor_completion
    result = process_contractor_completion(
        lead_id=lead_id,
        contractor_id=contractor.id,
        won_job=True,
        final_price=data.get('job_value') or data.get('final_price'),
        work_completed=data.get('work_completed', ''),
        permit_number=data.get('permit_number'),
    )
    status = 200 if result['success'] else 400
    return jsonify(result), status


@contractor_bp.route('/api/contractor/leads/<int:lead_id>/completion', methods=['POST'])
@_login_req_dec
def contractor_submit_completion(lead_id):
    """
    Full completion form submission — won or lost.
    Feeds real price data back into the estimate engine.
    """
    contractor = Contractor.query.filter_by(email=current_user.email).first()
    if not contractor: return jsonify({'error': 'Not a contractor'}), 403
    data = request.get_json() or {}
    won = str(data.get('won_job', 'true')).lower() in ('true', '1', 'yes')
    from flywheel_notifications import process_contractor_completion
    result = process_contractor_completion(
        lead_id=lead_id,
        contractor_id=contractor.id,
        won_job=won,
        final_price=float(data['final_price']) if data.get('final_price') else None,
        work_completed=data.get('work_completed', ''),
        permit_number=data.get('permit_number'),
    )
    status = 200 if result['success'] else 400
    return jsonify(result), status



@contractor_bp.route('/api/contractor/availability', methods=['POST'])
@_login_req_dec
def contractor_set_availability():
    """Toggle availability for new leads."""
    contractor = Contractor.query.filter_by(email=current_user.email).first()
    if not contractor: return jsonify({'error': 'Not a contractor'}), 403
    data = request.get_json() or {}
    contractor.available = bool(data.get('available', True))
    if not contractor.available and data.get('unavailable_until'):
        try: contractor.unavailable_until = datetime.fromisoformat(data['unavailable_until'])
        except: pass
    else:
        contractor.unavailable_until = None
    db.session.commit()
    return jsonify({'success': True, 'available': contractor.available})



@contractor_bp.route('/api/contractor/my-leads', methods=['GET'])
@_login_req_dec
def contractor_my_leads():
    """All leads this contractor has claimed."""
    contractor = Contractor.query.filter_by(email=current_user.email).first()
    if not contractor: return jsonify({'error': 'Not a contractor'}), 403
    claims = ContractorLeadClaim.query.filter_by(
        contractor_id=contractor.id
    ).order_by(ContractorLeadClaim.created_at.desc()).all()
    result = []
    for claim in claims:
        lead = ContractorLead.query.get(claim.lead_id)
        if not lead: continue
        result.append({
            'claim_id': claim.id, 'claim_status': claim.status,
            'claimed_at': claim.created_at.isoformat(),
            'closed_at': claim.closed_at.isoformat() if claim.closed_at else None,
            'job_value': claim.job_value,
            'lead_id': lead.id, 'repair_system': lead.repair_system,
            'trade_needed': lead.trade_needed, 'cost_estimate': lead.cost_estimate,
            'property_address': lead.property_address, 'property_zip': lead.property_zip,
            'contact_timing': lead.contact_timing, 'issue_description': lead.issue_description,
            'buyer_name': lead.user_name, 'buyer_email': lead.user_email,
            'buyer_phone': lead.user_phone, 'created_at': lead.created_at.isoformat(),
        })
    return jsonify({'leads': result, 'total': len(result)})



@contractor_bp.route('/contractor-onboarding')
@_login_req_dec
def contractor_onboarding():
    """4-step contractor setup wizard."""
    return send_from_directory('static', 'contractor-onboarding.html')


@contractor_bp.route('/contractor-portal')
@_login_req_dec
def contractor_portal():
    return send_from_directory('static', 'contractor-portal.html')

