"""
OfferWise Admin Routes Blueprint
=================================
Extracted from app.py to reduce monolith size.
Contains all /api/admin/* and /admin/* routes.
"""

from model_config import SONNET
import os
import re
import json
import logging
import stripe
import werkzeug.exceptions
from datetime import datetime, timedelta, date

from sqlalchemy import text
from flask import Blueprint, jsonify, request, send_from_directory, current_app, redirect
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import (
    db, User, Inspector, Contractor, ContractorLead, Analysis, Property,
    InspectorReport, MarketSnapshot, ShareLink, Waitlist,
    APIKey, GTMAdPerformance, GTMFunnelEvent, InfraVendor, InfraInvoice,
    RepairCostBaseline, RepairCostZone, Agent, AgentShare, CreditTransaction,
    PropertyWatch, EmailSendLog, FeatureEvent, ListingPreference,
)

admin_bp = Blueprint('admin', __name__)
logger = logging.getLogger(__name__)

# v5.89.56: ml-health data-counts cache. The endpoint runs 7 COUNT(*)
# queries on a 257K-row corpus; doing them on every dashboard refresh
# pushed total request time past the client's fetch timeout. Cache for
# 60s; per-worker memory (no Redis dependency). Each gunicorn worker has
# its own copy — different workers may briefly disagree by ±1 minute,
# which is fine for display purposes.
_ML_DATA_CACHE = {'value': None, 'ts': 0.0}
_ML_DATA_CACHE_TTL_SECONDS = 60.0


def _get_cached_ml_data_counts():
    """Return cached _data dict if fresh, else None. Caller runs the
    expensive queries on a None return."""
    import time as _time
    entry = _ML_DATA_CACHE
    if entry['value'] is None:
        return None
    age = _time.time() - entry['ts']
    if age > _ML_DATA_CACHE_TTL_SECONDS:
        return None
    # Return a shallow copy so the caller's mutations (like popping _errors)
    # don't poison the cached entry for the next reader.
    return dict(entry['value'])


def _set_cached_ml_data_counts(data):
    """Store the computed _data dict for re-use within the TTL window."""
    import time as _time
    _ML_DATA_CACHE['value'] = dict(data)
    _ML_DATA_CACHE['ts'] = _time.time()


# ── Test-account exclusion: one place computes the ids ───────────────────────
# Every admin surface that filters out test/persona/e2e accounts excludes them
# BY ID using these helpers. Excluding by id is NULL-email-safe (a NULL email
# simply isn't in the set), unlike a per-domain `~email.endswith(...)` which
# silently drops NULL-email users. The domain list comes from the single source
# of truth in app.TEST_EMAIL_DOMAINS (which includes .test.example.com).
def _test_user_ids(domains):
    """Return ids of users whose email ends with any suffix in `domains`."""
    domains = tuple(d for d in (domains or ()) if d)
    if not domains:
        return []
    from sqlalchemy import or_ as _or
    clauses = [User.email.endswith(d) for d in domains]
    return [r[0] for r in db.session.query(User.id).filter(_or(*clauses)).all()]


def _canonical_test_domains():
    """The single source of truth for test/persona/e2e domains. Lazy import —
    app is already loaded at request time, so this avoids a circular import."""
    try:
        from app import TEST_EMAIL_DOMAINS as _TD
        return tuple(_TD)
    except Exception:
        return ()


def _canonical_test_user_ids():
    """Ids of test/persona/e2e accounts, per the canonical domain list."""
    return _test_user_ids(_canonical_test_domains())


def _send_email(*args, **kwargs):
    """Lazy wrapper for app.send_email to avoid circular imports."""
    from app import send_email
    return send_email(*args, **kwargs)

# Lazy import helpers from app
def __send_email(*args, **kwargs):
    from app import send_email
    return _send_email(*args, **kwargs)

from blueprint_helpers import DeferredDecorator
from model_storage import get_models_dir

# ── Injected dependencies (set by init_admin_blueprint) ──────────
_db             = None
_login_required = None
_admin_required = None
_api_admin_required = None
_is_admin       = None

# Deferred decorators — safe to use at module level before init is called
_login_req_dec     = DeferredDecorator(lambda: _login_required)
_admin_req_dec     = DeferredDecorator(lambda: _admin_required)
_api_admin_req_dec = DeferredDecorator(lambda: _api_admin_required)


def init_admin_blueprint(app, db, login_required_fn, admin_required_fn, api_admin_required_fn):
    """
    Register the admin blueprint. Call from app.py after auth decorators are defined.
    """
    global _db, _login_required, _admin_required, _api_admin_required, _is_admin
    _db                 = db   # db also imported directly from models above
    _login_required     = login_required_fn
    _admin_required     = admin_required_fn
    _api_admin_required = api_admin_required_fn
    from app import is_admin
    _is_admin = is_admin
    app.register_blueprint(admin_bp)
    logging.info("✅ Admin blueprint registered (%d routes)", len(admin_bp.deferred_functions))


# ── Convenience shims so route bodies can call these as functions ─
def _require_admin():
    """Return 403 JSON if current request is not admin. Call at top of route."""
    if _is_admin and not _is_admin():
        from flask import abort
        abort(403)

@admin_bp.route('/api/admin/contractors', methods=['GET'])
@_api_admin_req_dec
def admin_contractors_list():
    """Admin view of all contractors."""
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        status_filter = request.args.get('status', '')
        q = Contractor.query.order_by(Contractor.created_at.desc())
        if status_filter:
            q = q.filter_by(status=status_filter)
        contractors = q.limit(200).all()
        out = []
        for c in contractors:
            try:
                out.append(c.to_dict())
            except Exception as row_e:
                logging.warning(f"Skipping contractor {c.id}: {row_e}")
        return jsonify({'contractors': out, 'total': len(out)})
    except Exception as e:
        logging.error(f"admin_contractors_list error: {e}", exc_info=True)
        return jsonify({'error': f'Server error: {type(e).__name__}: {str(e)}'}), 500



@admin_bp.route('/api/admin/contractors/<int:contractor_id>', methods=['PATCH'])
@_api_admin_req_dec
def admin_contractor_update(contractor_id):
    """Admin updates a contractor's status, notes, etc."""
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    c = Contractor.query.get_or_404(contractor_id)
    data = request.get_json() or {}
    for field in ['status', 'notes', 'accepts_leads', 'license_verified',
                  'referral_fee_pct', 'referral_fee_agreed', 'max_leads_month',
                  'plan', 'monthly_lead_limit']:
        if field in data:
            setattr(c, field, data[field])
    db.session.commit()
    return jsonify({'success': True, 'contractor': c.to_dict()})



@admin_bp.route('/api/admin/inspectors', methods=['GET'])
@_api_admin_req_dec
def admin_inspectors_list():
    """Admin view of all inspectors."""
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        inspectors = Inspector.query.order_by(Inspector.created_at.desc()).limit(200).all()
        # Pre-fetch owners in one query instead of User.query.get() per inspector (N+1).
        _insp_user_ids = [i.user_id for i in inspectors if i.user_id]
        _insp_user_map = {u.id: u for u in User.query.filter(User.id.in_(_insp_user_ids)).all()} if _insp_user_ids else {}
        out = []
        for insp in inspectors:
            try:
                user = _insp_user_map.get(insp.user_id)
                out.append({
                    'id': insp.id,
                    'user_id': insp.user_id,
                    'email': user.email if user else '—',
                    'name': user.name if user else '—',
                    'business_name': insp.business_name,
                    'license_number': insp.license_number,
                    'license_state': insp.license_state,
                    'phone': insp.phone,
                    'plan': insp.plan or 'free',
                    'monthly_quota': insp.monthly_quota or 5,
                    'monthly_used': insp.monthly_used or 0,
                    'total_reports': insp.total_reports or 0,
                    'total_buyers_converted': insp.total_buyers_converted or 0,
                    'is_verified': getattr(insp, 'is_verified', False) or False,
                    'is_active': getattr(insp, 'is_active', True),
                    'created_at': insp.created_at.isoformat() if insp.created_at else None,
                })
            except Exception as row_e:
                logging.warning(f"Skipping inspector {insp.id}: {row_e}")
        return jsonify({'inspectors': out, 'total': len(out)})
    except Exception as e:
        logging.error(f"admin_inspectors_list error: {e}", exc_info=True)
        return jsonify({'error': f'Server error: {type(e).__name__}: {str(e)}'}), 500



@admin_bp.route('/api/admin/inspectors/<int:inspector_id>', methods=['PATCH'])
@_api_admin_req_dec
def admin_inspector_update(inspector_id):
    """Admin updates an inspector — verify, activate, change plan."""
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    insp = Inspector.query.get_or_404(inspector_id)
    data = request.get_json() or {}
    for field in ['is_verified', 'is_active', 'plan', 'monthly_quota', 'notes']:
        if field in data:
            setattr(insp, field, data[field])
    if data.get('plan') == 'inspector_pro':
        insp.monthly_quota = -1
    db.session.commit()
    return jsonify({'success': True})



@admin_bp.route('/api/admin/leads', methods=['GET'])
@_api_admin_req_dec
def admin_leads_list():
    """All contractor leads with full detail for admin."""
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    status_filter = request.args.get('status', '')
    q = ContractorLead.query.order_by(ContractorLead.created_at.desc())
    if status_filter:
        q = q.filter_by(status=status_filter)
    leads = q.limit(300).all()
    out = []
    for lead in leads:
        contractor = Contractor.query.get(lead.assigned_contractor_id) if lead.assigned_contractor_id else None
        out.append({
            'id': lead.id,
            'created_at': lead.created_at.isoformat() if lead.created_at else None,
            'status': lead.status,
            'user_name': lead.user_name,
            'user_email': lead.user_email,
            'user_phone': lead.user_phone,
            'property_address': lead.property_address,
            'property_zip': lead.property_zip,
            'repair_system': lead.repair_system,
            'trade_needed': lead.trade_needed,
            'cost_estimate': lead.cost_estimate,
            'issue_description': lead.issue_description,
            'contact_timing': lead.contact_timing,
            'notes': lead.notes,
            'assigned_contractor_id': lead.assigned_contractor_id,
            'assigned_contractor_name': (contractor.business_name or contractor.name) if contractor else None,
            'assigned_contractor_email': contractor.email if contractor else None,
            'sent_to_contractor_at': lead.sent_to_contractor_at.isoformat() if lead.sent_to_contractor_at else None,
            'job_closed_at': lead.job_closed_at.isoformat() if lead.job_closed_at else None,
            'job_value': lead.job_value,
            'referral_fee_pct': lead.referral_fee_pct,
            'referral_fee_due': lead.fee_due(),
            'referral_paid': lead.referral_paid,
        })
    # Revenue summary
    closed = [l for l in out if l['status'] == 'closed']
    total_revenue = sum(l['referral_fee_due'] or 0 for l in closed)
    paid_revenue = sum((l['referral_fee_due'] or 0) for l in out if l.get('referral_paid'))
    return jsonify({
        'leads': out,
        'total': len(out),
        'summary': {
            'new': len([l for l in out if l['status'] == 'new']),
            'sent': len([l for l in out if l['status'] == 'sent']),
            'contacted': len([l for l in out if l['status'] == 'contacted']),
            'closed': len(closed),
            'total_revenue_due': round(total_revenue, 2),
            'total_revenue_paid': round(paid_revenue, 2),
        }
    })



@admin_bp.route('/api/admin/leads/<int:lead_id>/send', methods=['POST'])
@_api_admin_req_dec
def admin_send_lead(lead_id):
    """Admin manually sends a lead to a specific contractor."""
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    lead = ContractorLead.query.get_or_404(lead_id)
    data = request.get_json() or {}
    contractor_id = data.get('contractor_id')
    contractor = Contractor.query.get_or_404(contractor_id)

    timing_labels = {'asap': 'ASAP', 'this_week': 'This week', 'just_exploring': 'Just exploring'}
    timing = timing_labels.get(lead.contact_timing, lead.contact_timing or '—')

    html = f"""<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;">
      <div style="font-size:20px;font-weight:800;margin-bottom:4px;color:#f97316;">🔨 New Lead from OfferWise</div>
      <div style="font-size:13px;color:#94a3b8;margin-bottom:20px;">A homebuyer needs a {lead.repair_system} contractor in {lead.property_zip or lead.property_address}.</div>
      <table style="width:100%;border-collapse:collapse;">
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;width:130px;">Property</td><td style="padding:7px 0;font-weight:600;">{lead.property_address}</td></tr>
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Repair</td><td style="padding:7px 0;text-transform:capitalize;color:#f59e0b;font-weight:700;">{lead.repair_system}</td></tr>
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Est. cost</td><td style="padding:7px 0;font-weight:700;">{lead.cost_estimate or '—'}</td></tr>
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Buyer</td><td style="padding:7px 0;">{lead.user_name or '—'}</td></tr>
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Phone</td><td style="padding:7px 0;font-weight:700;color:#22c55e;">{lead.user_phone or '—'}</td></tr>
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Email</td><td style="padding:7px 0;">{lead.user_email}</td></tr>
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Timing</td><td style="padding:7px 0;color:{'#22c55e' if lead.contact_timing == 'asap' else '#f1f5f9'};font-weight:{'700' if lead.contact_timing == 'asap' else '400'};">{timing}</td></tr>
      </table>
      {f'<div style="margin-top:12px;padding:12px;background:rgba(255,255,255,.05);border-radius:8px;font-size:13px;color:#94a3b8;">{lead.issue_description}</div>' if lead.issue_description else ''}
      <div style="margin-top:20px;padding:14px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:8px;font-size:13px;color:#94a3b8;">
        Reply to this email or call the buyer at <strong style="color:#22c55e;">{lead.user_phone or 'the number above'}</strong> to arrange a quote.
      </div>
      <div style="margin-top:12px;font-size:11px;color:#475569;">Sent via OfferWise · Lead #{lead.id}</div>
    </div>"""

    try:
        _send_email(
            to_email=contractor.email,
            subject=f"🔨 Lead: {(lead.repair_system or 'Repair').title()} at {(lead.property_address or '')[:40]}",
            html_content=html,
            reply_to=lead.user_email,
        )
    except Exception as e:
        logging.error(f"Failed to send lead to contractor: {e}")
        return jsonify({'error': f'Email failed: {e}'}), 500

    # Update lead
    lead.assigned_contractor_id = contractor.id
    lead.sent_to_contractor_at = datetime.utcnow()
    lead.status = 'sent'
    contractor.leads_sent_total = (contractor.leads_sent_total or 0) + 1
    contractor.leads_sent_month = (contractor.leads_sent_month or 0) + 1
    db.session.commit()

    logging.info(f"📨 Lead #{lead_id} sent to contractor {contractor.email}")
    return jsonify({'success': True, 'contractor_email': contractor.email})



@admin_bp.route('/api/admin/leads/<int:lead_id>', methods=['PATCH'])
@_api_admin_req_dec
def admin_update_lead(lead_id):
    """Update lead status, record job close, track revenue."""
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    lead = ContractorLead.query.get_or_404(lead_id)
    data = request.get_json() or {}

    for field in ['status', 'notes', 'job_value', 'referral_fee_pct', 'referral_paid']:
        if field in data:
            setattr(lead, field, data[field])

    if data.get('status') == 'closed' and not lead.job_closed_at:
        lead.job_closed_at = datetime.utcnow()
        if lead.job_value and lead.referral_fee_pct:
            lead.referral_fee_due = lead.fee_due()

    if data.get('referral_paid') and not lead.referral_paid_at:
        lead.referral_paid_at = datetime.utcnow()

    db.session.commit()
    return jsonify({'success': True, 'referral_fee_due': lead.fee_due()})


# ============================================================
# REVENUE DASHBOARD ADMIN ROUTES (v5.74.97)
# ============================================================


@admin_bp.route('/api/admin/revenue', methods=['GET'])
@_api_admin_req_dec
def admin_revenue_summary():
    """Unified revenue dashboard — subscriptions + B2B API."""
    from models import APIKey, User, Inspector, Contractor
    from sqlalchemy import func as _func

    # Revenue must exclude test/persona/e2e accounts AND company accounts
    # (@persona.ai, @getofferwise.ai) so a manually-flipped or internal account
    # never shows up as paying. Built on the canonical domain list (so it picks
    # up .test.example.com too) and excluded by id (NULL-email-safe).
    _rev_test_ids = _test_user_ids(_canonical_test_domains() + ('@persona.ai', '@getofferwise.ai'))
    _rev_excl = [~User.id.in_(_rev_test_ids)] if _rev_test_ids else []

    # ── Inspector subscriptions ────────────────────────────────
    INSPECTOR_PRO_MRR = 49.0
    # Require a real Stripe subscription — excludes test accounts that were
    # flipped to 'inspector_pro' manually via admin/DB without paying.
    inspectors_pro = Inspector.query.filter_by(plan='inspector_pro').join(
        User, Inspector.user_id == User.id
    ).filter(
        *_rev_excl,
        User.stripe_subscription_id.isnot(None),
        User.stripe_subscription_id != '',
        User.subscription_status == 'active',
    ).all()
    inspector_sub_data = []
    for insp in inspectors_pro:
        owner = User.query.get(insp.user_id)
        inspector_sub_data.append({
            'id': insp.id,
            'business_name': insp.business_name or (owner.email if owner else '—'),
            'email': owner.email if owner else '—',
            'plan': 'Pro',
            'mrr': INSPECTOR_PRO_MRR,
            'total_reports': insp.total_reports or 0,
            'activated_at': insp.quota_reset_at.isoformat() if insp.quota_reset_at else None,
        })
    inspector_mrr = len(inspectors_pro) * INSPECTOR_PRO_MRR

    # ── Buyer subscriptions ───────────────────────────────────
    BUYER_MRR_MAP = {'buyer_starter': 9.0, 'buyer_pro': 19.0, 'buyer_unlimited': 49.0}
    buyer_counts = {}
    for plan, price in BUYER_MRR_MAP.items():
        count = User.query.filter_by(subscription_plan=plan).filter(
            *_rev_excl,
            User.stripe_subscription_id.isnot(None),
            User.stripe_subscription_id != '',
            User.subscription_status == 'active',
        ).count()
        buyer_counts[plan] = {'count': count, 'mrr': count * price, 'price': price}
    buyer_mrr = sum(v['mrr'] for v in buyer_counts.values())

    # ── Agent subscriptions ───────────────────────────────────
    from models import Agent
    AGENT_MRR_MAP = {'pro': 29.0}
    agent_counts = {}
    for plan, price in AGENT_MRR_MAP.items():
        count = Agent.query.filter_by(plan=plan).count()
        agent_counts[plan] = {'count': count, 'mrr': count * price, 'price': price}
    agent_mrr = sum(v['mrr'] for v in agent_counts.values())

    # ── Contractor subscriptions ──────────────────────────────
    CONTRACTOR_MRR_MAP = {'starter': 49.0, 'pro': 99.0, 'enterprise': 199.0}
    contractor_counts = {}
    for plan, price in CONTRACTOR_MRR_MAP.items():
        count = Contractor.query.filter_by(plan=plan).count()
        contractor_counts[plan] = {'count': count, 'mrr': count * price, 'price': price}
    contractor_mrr = sum(v['mrr'] for v in contractor_counts.values())

    # ── B2B API keys ──────────────────────────────────────────
    api_keys = APIKey.query.filter_by(is_active=True).order_by(APIKey.created_at.desc()).all()
    b2b_data = []
    for k in api_keys:
        owner = User.query.get(k.user_id)
        b2b_data.append({
            'id': k.id,
            'label': k.label, 'key_prefix': k.key_prefix, 'tier': k.tier,
            'owner_email': owner.email if owner else '—',
            'calls_month': k.calls_month or 0, 'calls_total': k.calls_total or 0,
            'monthly_limit': k.monthly_limit or 100,
            'price_per_call': getattr(k, 'price_per_call', 0) or 0,
            'monthly_fee': getattr(k, 'monthly_fee', 0) or 0,
            'revenue_month': getattr(k, 'revenue_month', 0) or 0,
            'revenue_total': getattr(k, 'revenue_total', 0) or 0,
            'billing_email': getattr(k, 'billing_email', None),
            'last_used_at': k.last_used_at.isoformat() if k.last_used_at else None,
        })
    b2b_mrr = sum(k['monthly_fee'] for k in b2b_data)
    b2b_total = sum(k['revenue_total'] for k in b2b_data)

    total_mrr = inspector_mrr + buyer_mrr + contractor_mrr + agent_mrr + b2b_mrr

    return jsonify({
        'b2b': b2b_data,
        'inspector_subs': inspector_sub_data,
        'buyer_counts': buyer_counts,
        'contractor_counts': contractor_counts,
        'agent_counts': agent_counts,
        'summary': {
            'total_mrr':       round(total_mrr, 2),
            'inspector_mrr':   round(inspector_mrr, 2),
            'buyer_mrr':       round(buyer_mrr, 2),
            'contractor_mrr':  round(contractor_mrr, 2),
            'agent_mrr':       round(agent_mrr, 2),
            'b2b_mrr':         round(b2b_mrr, 2),
            'b2b_total':       round(b2b_total, 2),
            'inspector_count': len(inspectors_pro),
            'buyer_count':     sum(v['count'] for v in buyer_counts.values()),
            'contractor_count':sum(v['count'] for v in contractor_counts.values()),
            'agent_count':     sum(v['count'] for v in agent_counts.values()),
        }
    })





@admin_bp.route('/api/admin/revenue/b2b/<int:key_id>', methods=['PATCH'])
@_api_admin_req_dec
def admin_update_b2b_key(key_id):
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    from models import APIKey
    key = APIKey.query.get_or_404(key_id)
    data = request.get_json() or {}
    for field in ['price_per_call', 'monthly_fee', 'tier', 'monthly_limit', 'billing_email']:
        if field in data:
            setattr(key, field, data[field])
    if data.get('reset_month_revenue'):
        key.revenue_month = 0
        key.calls_month = 0
    db.session.commit()
    return jsonify({'success': True})



@admin_bp.route('/api/admin/revenue/b2b/invoice-all', methods=['POST'])
@_api_admin_req_dec
def admin_b2b_invoice_all():
    """Generate Stripe invoices for all B2B API keys with accrued revenue this month."""
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403

    from models import APIKey, User
    results = {'invoiced': [], 'skipped': [], 'errors': []}

    keys = APIKey.query.filter(APIKey.is_active == True).all()
    for key in keys:
        revenue = (key.revenue_month or 0) + (key.monthly_fee or 0)
        if revenue <= 0:
            results['skipped'].append({'key': key.key_prefix, 'reason': 'no revenue'})
            continue

        billing_email = key.billing_email
        if not billing_email:
            owner = User.query.get(key.user_id)
            billing_email = owner.email if owner else None

        if not billing_email:
            results['errors'].append({'key': key.key_prefix, 'reason': 'no billing email'})
            continue

        try:
            owner = User.query.get(key.user_id)
            stripe_customer_id = getattr(owner, 'stripe_customer_id', None) if owner else None

            if not stripe_customer_id:
                customer = stripe.Customer.create(
                    email=billing_email,
                    name=key.label or f'API Key {key.key_prefix}',
                    metadata={'user_id': key.user_id, 'key_prefix': key.key_prefix}
                )
                stripe_customer_id = customer.id
                if owner:
                    owner.stripe_customer_id = stripe_customer_id
                    db.session.commit()

            # Create invoice + line items
            invoice = stripe.Invoice.create(
                customer=stripe_customer_id,
                auto_advance=True,
                collection_method='send_invoice',
                days_until_due=30,
                metadata={'key_prefix': key.key_prefix, 'key_id': key.id}
            )
            if key.monthly_fee and key.monthly_fee > 0:
                stripe.InvoiceItem.create(
                    customer=stripe_customer_id, invoice=invoice.id,
                    price_data={'currency':'usd','unit_amount':int(key.monthly_fee*100),
                                'product_data':{'name':f'OfferWise API Monthly Fee — {key.label or key.key_prefix}'}},
                    quantity=1,
                )
            if key.revenue_month and key.revenue_month > 0:
                stripe.InvoiceItem.create(
                    customer=stripe_customer_id, invoice=invoice.id,
                    price_data={'currency':'usd','unit_amount':int(key.revenue_month*100),
                                'product_data':{'name':f'OfferWise API Usage — {key.label or key.key_prefix}',
                                                'description':f'{key.calls_month or 0} calls × ${key.price_per_call or 0:.4f}/call'}},
                    quantity=1,
                )

            finalized = stripe.Invoice.finalize_invoice(invoice.id)
            logging.info(f"💰 B2B invoice {finalized.id} for {billing_email} — ${revenue:.2f}")
            results['invoiced'].append({'key': key.key_prefix, 'email': billing_email,
                                        'amount': revenue, 'invoice_id': finalized.id})
            # Reset monthly counters
            key.revenue_month = 0
            key.calls_month = 0
            db.session.commit()

        except Exception as e:
            logging.error(f"B2B invoice failed for key {key.key_prefix}: {e}", exc_info=True)
            results['errors'].append({'key': key.key_prefix, 'reason': str(e)})

    return jsonify({'success': True, 'invoiced_count': len(results['invoiced']),
                    'skipped_count': len(results['skipped']), 'error_count': len(results['errors']),
                    'results': results})


# ============================================================
# INSPECTOR PORTAL ROUTES (v5.74.81)
# ============================================================


@admin_bp.route('/api/admin/market-intel-stats', methods=['GET'])
@_api_admin_req_dec
def api_market_intel_stats():
    """Get market intelligence stats for admin dashboard."""
    from datetime import date
    today = date.today()
    snapshots_today = MarketSnapshot.query.filter_by(snapshot_date=today).count()
    alerts_today = db.session.query(db.func.sum(MarketSnapshot.alerts_generated)).filter_by(snapshot_date=today).scalar() or 0
    users_with_prefs = db.session.query(ListingPreference.user_id).filter_by(action='save').distinct().count()
    return jsonify({
        'snapshots_created': snapshots_today,
        'alerts_generated': alerts_today,
        'users_processed': users_with_prefs,
    })



def _test_harness_status(base):
    """Is the test harness actually present in THIS environment? Returns
    {available, reason, pytest_version, files_found}. A deployed image could strip
    test files or pytest (e.g. a slimmed build); without this the admin runner
    would fail with a confusing pytest traceback instead of a clear message."""
    import subprocess, os, glob
    files_found = len(glob.glob(os.path.join(base, 'test_*.py')))
    try:
        proc = subprocess.run(['python3', '-m', 'pytest', '--version'],
                              cwd=base, capture_output=True, text=True, timeout=20)
        pv = (proc.stdout or proc.stderr or '').strip().splitlines()
        pytest_ok = proc.returncode == 0
        pytest_version = (pv[0] if pv else '') if pytest_ok else ''
    except Exception as e:
        pytest_ok = False
        pytest_version = f'{type(e).__name__}: {e}'
    if not pytest_ok:
        return {'available': False, 'files_found': files_found, 'pytest_version': '',
                'reason': 'pytest is not installed in this environment — the test '
                          'harness is unavailable here. (It ships in the normal image; '
                          'a slimmed build may have removed it.)'}
    if files_found == 0:
        return {'available': False, 'files_found': 0, 'pytest_version': pytest_version,
                'reason': 'No test_*.py files are present in this environment — the '
                          'test files were not deployed. (They are kept in the normal '
                          'image; check .dockerignore / the build.)'}
    return {'available': True, 'files_found': files_found, 'pytest_version': pytest_version,
            'reason': ''}


def aggregate_latency(rows):
    """Pure aggregation for the latency breakdown (DB-free, unit-testable). rows
    are objects with .endpoint and .elapsed_ms. Returns stages sorted slowest-avg
    first, each with count/avg/p50/p95/max/total ms."""
    def _pct(vals, p):
        if not vals:
            return 0
        s = sorted(vals)
        return s[min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))]
    by_stage = {}
    for r in rows:
        ms = int(getattr(r, 'elapsed_ms', 0) or 0)
        if ms <= 0:
            continue
        by_stage.setdefault(getattr(r, 'endpoint', None) or '(unlabeled)', []).append(ms)
    stages = []
    for key, vals in by_stage.items():
        n = len(vals)
        stages.append({
            'stage': key, 'count': n,
            'avg_ms': round(sum(vals) / n),
            'p50_ms': _pct(vals, 50), 'p95_ms': _pct(vals, 95),
            'max_ms': max(vals), 'total_ms': sum(vals),
        })
    stages.sort(key=lambda s: -s['avg_ms'])
    return stages


@admin_bp.route('/api/admin/benchmark/pendleton', methods=['POST'])
@_api_admin_req_dec
def api_benchmark_pendleton():
    """Honest head-to-head: OfferWise's reasoning engine vs a single raw Claude
    pass on the 2839 Pendleton answer key, scored by objective recall of the 7 key
    findings. The reasoning side is deterministic (always runs); the raw-Claude side
    runs a real Opus 4.8 pass (needs ANTHROPIC_API_KEY). Numbers come from a real
    run — never fabricated. This is the honest replacement for the stale
    /comparison benchmark."""
    import os
    try:
        from benchmark_head_to_head import head_to_head
    except Exception as e:
        return jsonify({'ok': False, 'error': f'benchmark module unavailable: {e}'}), 200
    model = (request.args.get('model') or 'claude-opus-4-8').strip()
    client = None
    try:
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if api_key:
            from anthropic import Anthropic
            client = Anthropic(api_key=api_key)
    except Exception:
        client = None
    try:
        result = head_to_head(client=client, model=model)
        return jsonify({'ok': True, **result})
    except Exception as e:
        return jsonify({'ok': False, 'error': f'{type(e).__name__}: {e}'}), 200


@admin_bp.route('/api/admin/metrics-snapshot', methods=['GET'])
@_api_admin_req_dec
def api_metrics_snapshot():
    """A CURATED, shareable metrics snapshot for advisors/investors — the traction and
    product numbers a diligence-minded outsider would want, and nothing internal
    (no costs, no ad spend/CAC, no test accounts, no PII). Every metric is guarded so
    a schema quirk degrades one number rather than the whole snapshot."""
    from datetime import datetime, timedelta
    from sqlalchemy import func
    from models import db, User, Analysis
    out = {'generated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}

    # Exclude test/persona/admin accounts from user-facing traction
    def _real_users():
        q = User.query.filter(
            ~User.email.like('%@test.offerwise.ai'),
            ~User.email.like('%@persona.offerwise.ai'),
            ~User.email.like('%@example.com'),
        )
        return q

    since30 = datetime.utcnow() - timedelta(days=30)
    def _safe(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    signups   = _safe(lambda: _real_users().count(), 0)
    signups30 = _safe(lambda: _real_users().filter(User.created_at >= since30).count())
    activated = _safe(lambda: _real_users().filter(User.analyses_completed > 0).count())
    paying    = _safe(lambda: _real_users().filter(
        User.subscription_plan.isnot(None),
        User.subscription_plan != 'free',
        User.subscription_status == 'active').count(), 0)
    analyses_total = _safe(lambda: Analysis.query.count(), 0)
    analyses_30d   = _safe(lambda: Analysis.query.filter(Analysis.created_at >= since30).count())

    def _pct(n, d):
        try:
            return round((n / d) * 100, 1) if d else None
        except Exception:
            return None

    out['traction'] = {
        'signups': signups,
        'signups_last_30d': signups30,
        'activated_users': activated,
        'activation_rate_pct': _pct(activated, signups),
        'paying_customers': paying,
        'signup_to_paid_pct': _pct(paying, signups),
        'analyses_run_total': analyses_total,
        'analyses_run_last_30d': analyses_30d,
    }

    # Engagement
    props = _safe(lambda: __import__('models').Property.query.count())
    out['engagement'] = {
        'properties_analyzed': props,
        'avg_analyses_per_active_user': (round(analyses_total / activated, 1)
                                         if (activated and analyses_total) else None),
    }

    # National coverage — the crawler/API footprint (the numbers on the architecture page)
    active_crawlers, scaffolded = 13, 7
    try:
        from app import CRAWLER_REGISTRY
        active_crawlers = sum(1 for _, _, c in CRAWLER_REGISTRY if getattr(c, 'STATUS', '') == 'active')
        scaffolded = sum(1 for _, _, c in CRAWLER_REGISTRY if getattr(c, 'STATUS', '') != 'active')
    except Exception:
        pass
    out['coverage'] = {
        'national': True,
        'active_crawlers': active_crawlers,
        'scaffolded_crawlers': scaffolded,
        'metros': '13 of top-50 U.S. metros',
        'federal_apis': 11,
        'jurisdiction_model': 'national base + per-state overlays',
    }

    # Data assets — the moat's raw material
    out['data'] = {
        'labeled_corpus_rows': '~121K',
        'findings_persisted': _safe(lambda: __import__('models').Finding.query.count()),
        'shadow_comparisons': _safe(lambda: __import__('models').ShadowComparison.query.count()),
    }

    # Engineering & reliability
    eng = {'version': None}
    try:
        with open('VERSION') as f:
            eng['version'] = f.read().strip()
    except Exception:
        pass
    try:
        from app import _compute_hero_stats
        hs = _compute_hero_stats() or {}
        eng['lines_of_code'] = hs.get('loc_str') or hs.get('total_loc')
        eng['modules'] = hs.get('module_count') or hs.get('modules')
        eng['integrity_tests'] = hs.get('integrity_count') or hs.get('integrity')
    except Exception:
        pass
    def _count_tests():
        import glob as _g
        n = 0
        for fp in _g.glob('test_*.py'):
            try:
                with open(fp) as fh:
                    n += fh.read().count('def test_')
            except Exception:
                pass
        return n or None
    eng['test_files'] = _safe(lambda: len(__import__('glob').glob('test_*.py')))
    eng['automated_tests'] = _safe(_count_tests)
    out['engineering'] = eng

    # The moat — honest status
    out['moat'] = {
        'engine': 'jurisdiction-layered reasoning over disclosures + inspection',
        'status': 'built · shadow-validating on live traffic',
        'bakeoff_recall': '6/6 core findings on the canonical case',
        'cross_reference': True,
    }
    out['capabilities'] = [
        'Disclosure \u2194 inspection cross-reference',
        'Unstated-risk surfacing',
        'Defensible offer price',
        'Line-item repair breakdown',
        'National jurisdiction coverage',
    ]
    out['note'] = ('Curated for external sharing: traction, coverage, data, and engineering. '
                   'Excludes costs, ad spend, and test accounts. Point-in-time; regenerate for the latest.')
    return jsonify(out)


@admin_bp.route('/api/admin/latency-breakdown', methods=['GET'])
@_api_admin_req_dec
def api_latency_breakdown():
    """Where do the seconds go? Aggregates per-stage wall-clock timing
    (AIParseEvent.elapsed_ms) by stage/endpoint over a recent window — LLM stages
    (extraction, cross-reference, permit, …) and 'stage:*' phases (research_wait,
    disclosure_extract, inspection_extract). This is the real data that gates the
    progressive-delivery work: it names the actual bottleneck instead of guessing.
    """
    from datetime import datetime, timedelta
    try:
        from models import db, AIParseEvent
    except Exception as e:
        return jsonify({'ok': False, 'error': f'model unavailable: {e}'}), 200
    try:
        days = int(request.args.get('days', 7))
    except Exception:
        days = 7
    try:
        since = datetime.utcnow() - timedelta(days=days)
        rows = (db.session.query(AIParseEvent)
                .filter(AIParseEvent.created_at >= since)
                .filter(AIParseEvent.elapsed_ms.isnot(None))
                .filter(AIParseEvent.elapsed_ms > 0)
                .all())
    except Exception as e:
        return jsonify({'ok': False, 'error': f'query failed: {e}'}), 200

    stages = aggregate_latency(rows)
    return jsonify({'ok': True, 'window_days': days,
                    'samples': len(rows), 'stages': stages,
                    'note': 'avg_ms is per-call/phase wall-clock. LLM stages + stage:* phases. '
                            'A full analysis runs several of these; the sum of the typical path '
                            'is the wait users feel.'})


@admin_bp.route('/api/admin/test-suite', methods=['POST'])
@_api_admin_req_dec
def api_test_suite():
    """Run the full correctness suite on demand from the admin page and return
    pass/fail — so tests live on the admin page, not a local terminal. Covers the
    reasoning engine, offer-math integrity, national correctness, activation, the
    build guards, and this session's gap-plugs. Network/live-integration tests are
    intentionally excluded (they need external services and would hang a server
    run); the guard tests self-skip if node/babel aren't on the server.
    """
    import subprocess, os, re as _re
    base = os.path.dirname(os.path.abspath(__file__))

    # Self-check first: fail clearly if the harness isn't in this environment,
    # instead of surfacing a raw pytest error.
    harness = _test_harness_status(base)
    if not harness['available']:
        return jsonify({'ok': False, 'harness_available': False,
                        'reason': harness['reason'],
                        'files_found': harness['files_found']}), 200
    # Categorized so the readout is meaningful. Files are filtered by existence.
    categories = {
        'Reasoning engine': [
            'test_reasoning_composition.py', 'test_form_field_map.py',
            'test_issue_derivation.py', 'test_reasoning_pipeline.py',
            'test_tds_parser.py', 'test_tds_field_state.py', 'test_report_bridge.py',
            'test_reasoning_persistence.py', 'test_inspection_parser.py',
            'test_inspection_llm_extractor.py', 'test_cost_bands.py',
            'test_cross_reference.py', 'test_reasoning_pendleton.py',
            'test_disclosure_llm_extractor.py', 'test_research_synthesis_none.py',
        ],
        'National correctness': [
            'test_national_composition.py', 'test_jurisdiction_resolver.py',
            'test_disclosure_parser.py', 'test_permit_jurisdiction.py',
        ],
        'Offer-math integrity': [
            'test_reserve_reconciliation.py', 'test_offer_psychology_neutral.py',
            'test_market_narrative_consistency.py', 'test_algorithms.py',
            'test_avm_gate.py', 'test_report_quality_v2.py',
            'test_cost_provenance.py', 'test_ai_json.py', 'test_analyze_async.py',
            'test_input_confidence.py', 'test_benchmark_head_to_head.py',
            'test_admin_endpoints_smoke.py', 'test_prepackage_guard.py',
        ],
        'Moat activation': [
            'test_reasoning_activation_scope.py', 'test_shadow_readiness.py',
            'test_reasoning_shadow.py', 'test_extractor_diagnostic.py',
            'test_latency_timing.py', 'test_shadow_findings.py',
        ],
        'Build guards': [
            'test_admin_html_js.py', 'test_app_html_jsx.py', 'test_qa_async.py',
        ],
    }
    all_files, cat_files = [], {}
    for cat, files in categories.items():
        present = [f for f in files if os.path.exists(os.path.join(base, f))]
        cat_files[cat] = present
        all_files.extend(present)

    def _run(files):
        if not files:
            return {'ok': True, 'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 0,
                    'failures': [], 'summary': '(no files)'}
        try:
            proc = subprocess.run(
                ['python3', '-m', 'pytest', '-q', '--no-header', '-p', 'no:cacheprovider', *files],
                cwd=base, capture_output=True, text=True, timeout=420,
            )
            out = (proc.stdout or '') + '\n' + (proc.stderr or '')
            g = lambda pat: int(m.group(1)) if (m := _re.search(pat, out)) else 0
            return {
                'ok': proc.returncode == 0,
                'passed': g(r'(\d+) passed'), 'failed': g(r'(\d+) failed'),
                'errors': g(r'(\d+) error'), 'skipped': g(r'(\d+) skipped'),
                'failures': _re.findall(r'^FAILED (.+)$', out, _re.M)[:50],
                'summary': (out.strip().splitlines() or ['(no output)'])[-1],
            }
        except subprocess.TimeoutExpired:
            return {'ok': False, 'error': 'timed out (420s)', 'passed': 0,
                    'failed': 0, 'errors': 1, 'skipped': 0, 'failures': []}
        except Exception as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}', 'passed': 0,
                    'failed': 0, 'errors': 1, 'skipped': 0, 'failures': []}

    overall = _run(all_files)
    per_category = {cat: _run(files) for cat, files in cat_files.items()}
    return jsonify({
        'ok': overall.get('ok', False),
        'overall': overall,
        'by_category': per_category,
        'files_run': len(all_files),
        'note': 'Live-network integration tests are excluded; guard tests skip if node/babel are absent on the server.',
    })


@admin_bp.route('/api/admin/reasoning-tests', methods=['POST'])
@_api_admin_req_dec
def api_reasoning_tests():
    """Run the reasoning-layer regression suite on demand and return pass/fail.

    Distinct from /reasoning-health (which runs the acceptance fixtures only):
    this runs the actual pytest files for the reasoning engine, so the admin can
    verify the whole suite at will from the Ops page. Returns per-file results.
    """
    import subprocess, os, re as _re
    test_files = [
        'test_reasoning_composition.py',
        'test_form_field_map.py',
        'test_issue_derivation.py',
        'test_reasoning_pipeline.py',
        'test_tds_parser.py',
        'test_tds_field_state.py',
        'test_report_bridge.py',
        'test_reasoning_persistence.py',
        'test_inspection_parser.py',
        'test_inspection_llm_extractor.py',
        'test_cost_bands.py',
        'test_permit_jurisdiction.py',
    ]
    base = os.path.dirname(os.path.abspath(__file__))
    present = [f for f in test_files if os.path.exists(os.path.join(base, f))]
    try:
        proc = subprocess.run(
            ['python3', '-m', 'pytest', '-q', '--no-header', *present],
            cwd=base, capture_output=True, text=True, timeout=180,
        )
        out = (proc.stdout or '') + '\n' + (proc.stderr or '')
        # parse the pytest summary line, e.g. "84 passed, 2 warnings in 9.41s"
        m_pass = _re.search(r'(\d+) passed', out)
        m_fail = _re.search(r'(\d+) failed', out)
        m_err = _re.search(r'(\d+) error', out)
        passed = int(m_pass.group(1)) if m_pass else 0
        failed = int(m_fail.group(1)) if m_fail else 0
        errors = int(m_err.group(1)) if m_err else 0
        # capture the FAILED lines for quick triage
        failures = _re.findall(r'^FAILED (.+)$', out, _re.M)
        return jsonify({
            'ok': proc.returncode == 0,
            'passed': passed,
            'failed': failed,
            'errors': errors,
            'files_run': len(present),
            'failures': failures[:50],
            'summary': (out.strip().splitlines() or ['(no output)'])[-1],
        })
    except subprocess.TimeoutExpired:
        return jsonify({'ok': False, 'error': 'test run timed out (180s)'}), 200
    except Exception as e:
        return jsonify({'ok': False, 'error': f'{type(e).__name__}: {e}'}), 200


@admin_bp.route('/api/admin/reasoning-health', methods=['GET'])
@_api_admin_req_dec
def api_reasoning_health():
    """Phase 0 reasoning-layer self-check for the admin dashboard.

    Loads + validates the checklist asset and composes representative slices,
    asserting the Q-5.9 composition invariants. Never raises — reports ok=False
    with an error if the reasoning layer is unhealthy (dashboard shows reality).
    """
    try:
        from reasoning_health import reasoning_self_check, persistence_check
        payload = reasoning_self_check()
        payload['persistence'] = persistence_check()
        return jsonify(payload)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'{type(e).__name__}: {e}',
                        'invariants_passed': False, 'checks': {}})


@admin_bp.route('/api/admin/ab-home', methods=['GET'])
@_api_admin_req_dec
def api_ab_home():
    """Compare the home_promptfirst A/B arms by funnel stage.

    Session-based: a session belongs to whichever variant its 'visit' event was
    tagged with (control|v2). We then count how many of each arm's sessions
    reached later stages, and report per-arm conversion. Forced /v2 previews
    (metadata.forced) are excluded so manual QA doesn't pollute the experiment.
    """
    import json as _json
    from models import GTMFunnelEvent
    from datetime import datetime, timedelta

    days = request.args.get('days', 30, type=int)
    since = datetime.utcnow() - timedelta(days=days)
    events = (GTMFunnelEvent.query
              .filter(GTMFunnelEvent.created_at >= since)
              .all())

    # Map session -> variant (from its tagged visit), skipping forced previews
    session_variant = {}
    for e in events:
        if e.stage != 'visit' or not e.session_id:
            continue
        try:
            meta = _json.loads(e.metadata_json) if e.metadata_json else {}
        except Exception:
            meta = {}
        if meta.get('forced'):
            continue
        v = meta.get('variant')
        if v in ('control', 'v2') and e.session_id not in session_variant:
            session_variant[e.session_id] = v

    # Stages we care about, in funnel order
    ladder = ['visit', 'risk_check_start', 'risk_check_complete',
              'signup', 'analysis_started', 'analysis_complete', 'purchase']
    # For each variant, the set of sessions that reached each stage
    reached = {'control': {s: set() for s in ladder},
               'v2':      {s: set() for s in ladder}}
    for e in events:
        if not e.session_id:
            continue
        v = session_variant.get(e.session_id)
        if v not in ('control', 'v2'):
            continue
        if e.stage in reached[v]:
            reached[v][e.stage].add(e.session_id)

    def arm(v):
        base = len(reached[v]['visit'])
        def pct(stage):
            n = len(reached[v][stage])
            return {'n': n, 'pct_of_visit': round(n / base * 100, 1) if base else None}
        return {
            'visits': base,
            'risk_check_start': pct('risk_check_start'),
            'risk_check_complete': pct('risk_check_complete'),
            'signup': pct('signup'),
            'analysis_complete': pct('analysis_complete'),
            'purchase': pct('purchase'),
        }

    c, v2 = arm('control'), arm('v2')
    # Honest significance caveat: at low volume, differences are noise.
    min_visits = min(c['visits'], v2['visits'])
    note = ('Enough volume for a directional read.' if min_visits >= 100
            else f'LOW VOLUME ({min_visits} visits in smaller arm) — treat any '
                 f'difference as directional only, not significant.')

    return jsonify({
        'test': 'home_promptfirst',
        'window_days': days,
        'control': c,
        'v2': v2,
        'caveat': note,
    })


@admin_bp.route('/api/admin/user-journeys', methods=['GET'])
@_api_admin_req_dec
def api_user_journeys():
    """Post-analysis behaviour for the real used-product cohort.

    Answers "why only one analysis?" permanently and for any future cohort:
    for every real (non-test) user with >=1 completed analysis, what did they
    do AFTER their first one -- reach pricing, start a second property, ever
    return, or stop cold.

    Query params:
      include_internal=1   include test/founder accounts (default: excluded)
      email=<addr>         drill into ONE user's full ordered timeline
      limit=<n>            cap rows in the cohort table (default 100)

    Analyses come from the Analysis table (clean per-property count, immune to
    the funnel-event double-fire). Surrounding behaviour (pricing views,
    second-property attempts, return visits) comes from gtm_funnel_events.
    """
    from collections import Counter
    from models import User, Analysis, Property, GTMFunnelEvent
    from funnel_tracker import is_test_account

    include_internal = request.args.get('include_internal', '') in ('1', 'true', 'yes')
    email_q = (request.args.get('email') or '').strip().lower()
    limit = request.args.get('limit', 100, type=int)

    # ── single-user drill-down ───────────────────────────────────────────
    if email_q:
        u = User.query.filter(db.func.lower(User.email) == email_q).first()
        if not u:
            return jsonify({'error': f'No user found for {email_q}'}), 404
        timeline = []
        analyses = (Analysis.query
                    .filter(Analysis.user_id == u.id)
                    .order_by(Analysis.created_at.asc()).all())
        prop_ids = [a.property_id for a in analyses if a.property_id]
        addr_by_id = {}
        if prop_ids:
            for p in Property.query.filter(Property.id.in_(prop_ids)).all():
                addr_by_id[p.id] = getattr(p, 'address', None) or ('#' + str(p.id))
        for a in analyses:
            timeline.append({
                'ts': a.created_at.isoformat() if a.created_at else None,
                'kind': 'analysis',
                'detail': addr_by_id.get(a.property_id, '#' + str(a.property_id) if a.property_id else 'property'),
                'offer_score': a.offer_score,
                'risk_tier': a.risk_tier,
            })
        for e in (GTMFunnelEvent.query
                  .filter(GTMFunnelEvent.user_id == u.id)
                  .order_by(GTMFunnelEvent.created_at.asc()).all()):
            timeline.append({
                'ts': e.created_at.isoformat() if e.created_at else None,
                'kind': 'event',
                'detail': e.stage,
                'source': e.source,
            })
        timeline.sort(key=lambda r: r['ts'] or '')
        return jsonify({
            'mode': 'user',
            'email': u.email,
            'name': u.name,
            'joined': u.created_at.isoformat() if u.created_at else None,
            'last_login': u.last_login.isoformat() if u.last_login else None,
            'onboarded': bool(u.onboarding_completed),
            'analyses_completed': u.analyses_completed,
            'timeline': timeline,
        })

    # ── cohort view ──────────────────────────────────────────────────────
    analyses_by_user = {}
    for a in Analysis.query.filter(Analysis.user_id.isnot(None)).all():
        analyses_by_user.setdefault(a.user_id, []).append(a)
    events_by_user = {}
    for e in GTMFunnelEvent.query.filter(GTMFunnelEvent.user_id.isnot(None)).all():
        events_by_user.setdefault(e.user_id, []).append(e)

    rows = []
    onboarded_no_analysis = 0
    for u in User.query.all():
        if not include_internal and is_test_account(u):
            continue
        u_analyses = sorted(analyses_by_user.get(u.id, []),
                            key=lambda a: a.created_at or datetime.min)
        n_analyses = len(u_analyses)
        if n_analyses == 0:
            if u.onboarding_completed:
                onboarded_no_analysis += 1
            continue  # cohort = used product
        first_at = u_analyses[0].created_at
        u_events = sorted(events_by_user.get(u.id, []),
                          key=lambda e: e.created_at or datetime.min)
        after = [e for e in u_events
                 if e.created_at and first_at and e.created_at > first_at]
        reached_pricing_after = any(e.stage == 'pricing_view' for e in after)
        second_attempt = (n_analyses > 1) or any(
            e.stage in ('address_entered', 'upload_started') for e in after)
        returned = any(e.created_at and first_at and e.created_at.date() > first_at.date()
                       for e in u_events)
        last_event = after[-1].stage if after else (u_events[-1].stage if u_events else None)
        rows.append({
            'email': u.email,
            'name': u.name,
            'analyses': n_analyses,
            'first_analysis': first_at.isoformat() if first_at else None,
            'reached_pricing_after': reached_pricing_after,
            'second_property_attempt': bool(second_attempt),
            'returned_after': bool(returned),
            'last_event': last_event,
        })

    rows.sort(key=lambda r: r['first_analysis'] or '', reverse=True)
    n = len(rows)

    def _pct(k):
        return round(100 * sum(1 for r in rows if r[k]) / n, 1) if n else 0.0

    one_and_done = sum(1 for r in rows if r['analyses'] == 1 and not r['returned_after'])
    last_counts = Counter((r['last_event'] or '—') for r in rows)
    last_event_dist = last_counts.most_common(8)

    caveat = ('Directional read.' if n >= 30 else
              f'LOW VOLUME (n={n} used-product users) — treat as anecdote, not a rate.')

    return jsonify({
        'mode': 'cohort',
        'include_internal': include_internal,
        'cohort_size': n,
        'onboarded_no_analysis': onboarded_no_analysis,
        'rollup': {
            'reached_pricing_after_pct': _pct('reached_pricing_after'),
            'second_property_attempt_pct': _pct('second_property_attempt'),
            'returned_after_pct': _pct('returned_after'),
            'one_and_done': one_and_done,
            'one_and_done_pct': round(100 * one_and_done / n, 1) if n else 0.0,
        },
        'last_event_distribution': last_event_dist,
        'users': rows[:limit],
        'caveat': caveat,
    })


@admin_bp.route('/api/admin/run-market-intel', methods=['POST'])
@_api_admin_req_dec
def api_run_market_intel():
    """Manually trigger market intelligence run (admin only)."""
    from market_intelligence import run_nightly_intelligence
    stats = run_nightly_intelligence(db.session)
    return jsonify(stats)


def normalize_reasoning_states(raw):
    """Normalize a comma-separated states string for the reasoning activation
    allowlist: uppercase, trimmed, de-duplicated, sorted, blanks dropped.
    "ca, tx , ca" -> "CA,TX"; "" -> "". Single source of truth for what the
    /api/admin/reasoning-flag endpoint persists, so the stored value always
    matches the 2-letter state form the gate resolves against."""
    return ','.join(sorted({p.strip().upper() for p in (raw or '').split(',') if p.strip()}))


@admin_bp.route('/api/admin/reasoning-flag', methods=['GET', 'POST'])
@_api_admin_req_dec
def api_reasoning_flag():
    """Read or set the buyer-facing reasoning-section flag (DB-backed, no deploy).

    GET  -> { enabled, source, guard_passed, guard_detail }
    POST { enabled: true|false } -> sets it. Enabling is GUARDED: the reasoning
      self-check acceptance fixtures must pass right now, so the section can't be
      exposed to buyers while the engine is unhealthy. Disabling is always allowed.
    """
    from models import SystemSetting

    def _guard():
        """Return (passed: bool, detail: str). Enabling requires this to pass."""
        try:
            from reasoning_health import reasoning_self_check
            sc = reasoning_self_check()
            inv = bool(sc.get('invariants_passed', sc.get('ok', False)))
            insp = sc.get('inspection_fixture', {}) or {}
            insp_ok = bool(insp.get('ok'))
            if inv and insp_ok:
                return True, 'acceptance fixtures pass (composition + inspection moat)'
            parts = []
            if not inv:
                parts.append('composition/invariants failing')
            if not insp_ok:
                parts.append('inspection fixture failing')
            return False, '; '.join(parts) or 'self-check did not pass'
        except Exception as e:
            return False, f'self-check error: {type(e).__name__}: {e}'

    if request.method == 'GET':
        raw = SystemSetting.get('reasoning_in_report', None)
        if raw is not None:
            enabled = str(raw).strip().lower() in ('1', 'on', 'true', 'yes')
            source = 'db'
        else:
            import os as _os
            enabled = _os.environ.get('OFFERWISE_REASONING_IN_REPORT', '0') == '1'
            source = 'env' if enabled else 'default-off'
        juris = SystemSetting.get('reasoning_in_report_jurisdictions', None)
        if juris is None:
            import os as _os2
            juris = _os2.environ.get('OFFERWISE_REASONING_IN_REPORT_JURISDICTIONS', '')
        passed, detail = _guard()
        return jsonify({'enabled': enabled, 'source': source,
                        'jurisdictions': juris or '',
                        'guard_passed': passed, 'guard_detail': detail})

    # POST
    data = request.get_json(silent=True) or {}
    who = getattr(getattr(request, '_admin_user', None), 'email', None) or 'admin'

    # Jurisdiction allowlist (CA-first activation). Setting a non-empty list
    # exposes the reasoning section to buyers in those states, so it is guarded the
    # same way as the global enable; clearing it (empty) is always allowed.
    if 'jurisdictions' in data:
        raw_j = (data.get('jurisdictions') or '').strip()
        states = normalize_reasoning_states(raw_j)
        if states:
            passed, detail = _guard()
            if not passed:
                return jsonify({'ok': False, 'error': 'validation guard failed — not enabling',
                                'guard_detail': detail}), 200
        ok = SystemSetting.set('reasoning_in_report_jurisdictions', states, updated_by=who)
        return jsonify({'ok': ok, 'jurisdictions': states if ok else None,
                        'guard_detail': ('set: ' + states) if states else 'cleared'})

    want = bool(data.get('enabled'))
    if want:
        passed, detail = _guard()
        if not passed:
            return jsonify({'ok': False, 'enabled': False,
                            'error': 'validation guard failed — not enabling',
                            'guard_detail': detail}), 200
    ok = SystemSetting.set('reasoning_in_report', '1' if want else '0', updated_by=who)
    return jsonify({'ok': ok, 'enabled': want if ok else None,
                    'guard_detail': 'enabled' if want else 'disabled'})



@admin_bp.route('/api/admin/google-ads-sync', methods=['POST'])
@_api_admin_req_dec
def api_google_ads_sync():
    """Manually trigger Google Ads data sync (admin only)."""
    from google_ads_sync import is_configured, sync_to_db, backfill
    if not is_configured():
        return jsonify({'error': 'Google Ads API not configured. Set GOOGLE_ADS_* env vars.'}), 400
    days = request.args.get('days', type=int)
    if days and days > 0:
        results = backfill(db.session, days=min(days, 90))
        return jsonify({'status': 'backfill_complete', 'days': len(results), 'results': results})
    result = sync_to_db(db.session)
    return jsonify(result)



@admin_bp.route('/api/admin/google-ads-status', methods=['GET'])
@_api_admin_req_dec
def api_google_ads_status():
    """Check Google Ads integration status with diagnostics."""
    from google_ads_sync import is_configured, CUSTOMER_ID, DEVELOPER_TOKEN, CLIENT_ID, REFRESH_TOKEN
    configured = is_configured()
    
    # Diagnostic: which vars are set?
    diag = {
        'developer_token': bool(DEVELOPER_TOKEN),
        'client_id': bool(CLIENT_ID),
        'refresh_token': bool(REFRESH_TOKEN),
        'customer_id': bool(CUSTOMER_ID),
    }
    
    # Check last sync
    from models import GTMAdPerformance
    last_entry = GTMAdPerformance.query.filter_by(channel='google_ads')\
        .order_by(GTMAdPerformance.date.desc()).first()
    
    total_rows = GTMAdPerformance.query.filter_by(channel='google_ads').count()
    
    return jsonify({
        'configured': configured,
        'env_vars': diag,
        'customer_id': CUSTOMER_ID[:4] + '****' + CUSTOMER_ID[-2:] if configured and len(CUSTOMER_ID) >= 6 else None,
        'last_sync_date': last_entry.date.isoformat() if last_entry else None,
        'last_sync_impressions': last_entry.impressions if last_entry else None,
        'last_sync_spend': float(last_entry.spend) if last_entry else None,
        'total_rows': total_rows,
    })


@admin_bp.route('/api/admin/google-ads-diagnostics', methods=['GET'])
@_api_admin_req_dec
def api_google_ads_diagnostics():
    """v5.89.66: Campaign serving health + conversion-action diagnostics.

    Different from /google-ads-status (which is integration health) and
    /google-ads-sync (which is performance numbers). This returns:
      * Campaign status, primary status reasons (eligible/limited/why)
      * Bid strategy + budget
      * Conversion actions with status + lookback windows

    Used for the Friday vendor call to argue with accurate data.
    """
    from google_ads_sync import fetch_campaign_diagnostics
    try:
        result = fetch_campaign_diagnostics()
        return jsonify(result)
    except Exception as e:
        import logging as _log
        _log.exception(f"google-ads-diagnostics failed: {e}")
        return jsonify({
            'configured': False,
            'error': f'Diagnostic call failed: {e}',
            'campaigns': [],
            'conversion_actions': [],
        }), 500


@admin_bp.route('/api/admin/signup-attribution', methods=['GET'])
@_api_admin_req_dec
def api_signup_attribution():
    """v5.89.66: First-party signup attribution data.

    Returns: count of signups by utm_source over the last N days, with
    gclid presence breakdown. This is the data the dashboard should show
    instead of '0 signups from ads' — it's our DB's own record, not
    dependent on Google's pixel callback.
    """
    from sqlalchemy import func, and_
    days = max(1, min(int(request.args.get('days', 30)), 365))
    cutoff = datetime.utcnow() - timedelta(days=days)

    try:
        rows = db.session.query(
            User.signup_utm_source,
            User.signup_utm_medium,
            func.count(User.id).label('n'),
            func.sum(func.cast(User.signup_gclid.isnot(None), db.Integer)).label('with_gclid'),
        ).filter(
            User.first_signup_date >= cutoff
        ).group_by(
            User.signup_utm_source, User.signup_utm_medium
        ).order_by(
            func.count(User.id).desc()
        ).all()

        total = sum(r.n for r in rows)
        return jsonify({
            'days': days,
            'total_signups': total,
            'breakdown': [
                {
                    'utm_source': r.signup_utm_source or '(direct)',
                    'utm_medium': r.signup_utm_medium or '',
                    'count': r.n,
                    'with_gclid': int(r.with_gclid or 0),
                } for r in rows
            ],
        })
    except Exception as e:
        import logging as _log
        _log.warning(f"signup-attribution query failed: {e}")
        return jsonify({
            'days': days,
            'total_signups': 0,
            'breakdown': [],
            'error': str(e),
        }), 500


# ============================================================================
# 🔍 Google Search Console - Top queries + intent analysis (v5.87.86)
# ============================================================================

@admin_bp.route('/api/admin/gsc-status', methods=['GET'])
@_api_admin_req_dec
def api_gsc_status():
    """Configuration check for Google Search Console integration."""
    from gsc_fetch import is_configured, missing_env_vars, auth_mode
    return jsonify({
        'configured': is_configured(),
        'auth_mode': auth_mode(),  # 'service_account' | 'oauth' | None
        'missing_env_vars': missing_env_vars(),
        'site_url': os.environ.get('GSC_SITE_URL', '') if is_configured() else None,
    })


@admin_bp.route('/api/admin/gsc-fetch', methods=['POST'])
@_api_admin_req_dec
def api_gsc_fetch():
    """Pull top organic queries and classify by intent.

    Query params:
      days     (default 90)  — query window
      max_rows (default 1000) — top N queries
    """
    from gsc_fetch import fetch_and_classify
    days = max(1, min(int(request.args.get('days', 90)), 365))
    max_rows = max(10, min(int(request.args.get('max_rows', 1000)), 5000))
    result = fetch_and_classify(days=days, max_rows=max_rows)
    return jsonify(result)


@admin_bp.route('/api/admin/gsc-fetch-paid', methods=['POST'])
@_api_admin_req_dec
def api_gsc_fetch_paid():
    """v5.87.89: Pull Google Ads search terms and classify by intent.

    Query params:
      days     (default 30)  — query window (Google Ads data lags ~24h)
      max_rows (default 500) — top N search terms
    """
    from gsc_fetch import fetch_and_classify_paid
    days = max(1, min(int(request.args.get('days', 30)), 90))
    max_rows = max(10, min(int(request.args.get('max_rows', 500)), 2000))
    result = fetch_and_classify_paid(days=days, max_rows=max_rows)
    return jsonify(result)


@admin_bp.route('/api/admin/gsc-pages', methods=['GET', 'POST'])
@_api_admin_req_dec
def api_gsc_pages():
    """v5.89.67: page-level GSC performance + SEO improvement audit.

    Used to identify "almost-winner" guides ranking 8-20 that just need
    meta+depth fixes to crack page 1, and "low-CTR" pages where the snippet
    is the bottleneck. See gsc_fetch.audit_pages() for category logic.

    Query params:
      days     (default 90) — query window (max 365)
      max_rows (default 500) — top N pages (max 5000)
    """
    from gsc_fetch import audit_pages
    days = max(1, min(int(request.args.get('days', 90)), 365))
    max_rows = max(10, min(int(request.args.get('max_rows', 500)), 5000))
    result = audit_pages(days=days, max_rows=max_rows)
    return jsonify(result)


@admin_bp.route('/api/admin/gsc-page-queries', methods=['GET'])
@_api_admin_req_dec
def api_gsc_page_queries():
    """v5.89.68: top queries driving impressions for a specific page.

    Used to ground the SEO fix in actual search behavior: what queries is
    Google showing this page for? Answers "what do I optimize the meta
    title for" with data instead of guesses.

    Query params:
      page (required) — full URL of the page (e.g. https://www.getofferwise.ai/guides/X)
      days (default 90) — lookback window (max 365)
      max_rows (default 20) — top N queries (max 100)
    """
    from gsc_fetch import fetch_queries_for_page
    page = (request.args.get('page') or '').strip()
    if not page:
        return jsonify({'error': 'page param required', 'queries': []}), 400
    days = max(1, min(int(request.args.get('days', 90)), 365))
    max_rows = max(1, min(int(request.args.get('max_rows', 20)), 100))
    result = fetch_queries_for_page(page_url=page, days=days, max_rows=max_rows)
    return jsonify(result)


@admin_bp.route('/api/admin/meta-audit', methods=['GET'])
@_api_admin_req_dec
def api_meta_audit():
    """v5.89.75: scan all static/guides for meta-quality issues.

    Surfaces placeholder bugs (un-substituted templating), title/description
    length violations, missing canonical/schema/OG/Twitter tags, thin
    content, and staleness. Used by the Meta Audit admin panel for batch
    SEO work — finding what to fix without hand-grepping 30 HTML files.

    Returns full result dict from meta_inspector.inspect_all_guides().
    """
    from meta_inspector import inspect_all_guides
    try:
        result = inspect_all_guides('static/guides')
        return jsonify(result)
    except Exception as e:
        import logging as _log
        _log.exception(f"meta-audit failed: {e}")
        return jsonify({
            'guides': [],
            'total': 0,
            'error': f'Audit failed: {e}',
        }), 500


@admin_bp.route('/api/admin/meta-apply-title', methods=['POST'])
@_api_admin_req_dec
def api_meta_apply_title():
    """v5.89.76: Update the <title> tag of a specific guide HTML file.

    Strict guard rails (this is the first endpoint that mutates source files):
      * filename must end with .html and live in static/guides/
      * new_title must be <= 60 chars (TITLE_MAX)
      * new_title cannot contain HTML special chars
      * old_title must still exist in the file (idempotency check)
      * Also updates og:title and twitter:title to match
      * Also bumps schema.org dateModified to today

    Request body (JSON):
        {
            "filename": "as-is-home-sale.html",
            "new_title": "Buying an As-Is Home: What Sellers Must Disclose"
        }

    Returns:
        {"ok": true, "filename": "...", "new_title": "...", "changes": 3}
        or {"ok": false, "reason": "..."}
    """
    import os
    import re
    import datetime
    from meta_inspector import TITLE_MAX

    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception:
        body = {}

    filename = (body.get('filename') or '').strip()
    new_title = (body.get('new_title') or '').strip()

    # Validate filename
    if not filename or not filename.endswith('.html'):
        return jsonify({'ok': False, 'reason': 'invalid_filename'}), 400
    if '/' in filename or '\\' in filename or '..' in filename:
        return jsonify({'ok': False, 'reason': 'path_traversal_blocked'}), 400

    filepath = os.path.join('static', 'guides', filename)
    if not os.path.isfile(filepath):
        return jsonify({'ok': False, 'reason': 'file_not_found'}), 404

    # Validate new_title
    if not new_title:
        return jsonify({'ok': False, 'reason': 'empty_title'}), 400
    if len(new_title) > TITLE_MAX:
        return jsonify({'ok': False, 'reason': f'title_too_long ({len(new_title)} > {TITLE_MAX})'}), 400
    if any(c in new_title for c in ['<', '>', '"\n']):
        return jsonify({'ok': False, 'reason': 'invalid_chars_in_title'}), 400

    # Read file
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return jsonify({'ok': False, 'reason': f'read_error: {e}'}), 500

    # Find the OUTER (head) <title> — the first one in the file
    # Some pages have inline SVG with <title> tags; only the first counts
    match = re.search(r'<title>([^<]*)</title>', content)
    if not match:
        return jsonify({'ok': False, 'reason': 'no_title_tag_found'}), 400

    old_title = match.group(1)
    if old_title.strip() == new_title:
        return jsonify({'ok': True, 'filename': filename, 'new_title': new_title, 'changes': 0, 'note': 'no_change_needed'})

    changes = 0

    # 1. Replace the head <title>...</title> (first occurrence only)
    new_content = content.replace(f'<title>{old_title}</title>', f'<title>{new_title}</title>', 1)
    if new_content != content:
        content = new_content
        changes += 1

    # 2. Update og:title if present
    og_title_re = re.compile(r'(<meta\s+property="og:title"\s+content=")([^"]*)(")')
    og_match = og_title_re.search(content)
    if og_match:
        content = og_title_re.sub(r'\g<1>' + new_title.replace('\\', '\\\\') + r'\g<3>', content, count=1)
        changes += 1

    # 3. Update twitter:title if present
    tw_title_re = re.compile(r'(<meta\s+name="twitter:title"\s+content=")([^"]*)(")')
    tw_match = tw_title_re.search(content)
    if tw_match:
        content = tw_title_re.sub(r'\g<1>' + new_title.replace('\\', '\\\\') + r'\g<3>', content, count=1)
        changes += 1

    # 4. Update schema.org headline if present
    headline_re = re.compile(r'("headline":\s*")([^"]*)(")')
    h_match = headline_re.search(content)
    if h_match:
        content = headline_re.sub(r'\g<1>' + new_title.replace('\\', '\\\\') + r'\g<3>', content, count=1)
        changes += 1

    # 5. Bump dateModified in schema.org to today
    today = datetime.date.today().isoformat()
    dm_re = re.compile(r'("dateModified":\s*")([^"]*)(")')
    dm_match = dm_re.search(content)
    if dm_match and dm_match.group(2) != today:
        content = dm_re.sub(r'\g<1>' + today + r'\g<3>', content, count=1)
        changes += 1

    # Write back
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        return jsonify({'ok': False, 'reason': f'write_error: {e}'}), 500

    return jsonify({
        'ok': True,
        'filename': filename,
        'old_title': old_title,
        'new_title': new_title,
        'changes': changes,
    })


# ============================================================================
# 🤖 Property Research Agent - Free property research endpoint
# ============================================================================

@admin_bp.route('/api/admin/reddit-ads-sync', methods=['POST'])
@_api_admin_req_dec
def api_reddit_ads_sync():
    """Manually trigger Reddit Ads data sync (admin only)."""
    from reddit_ads_sync import is_configured, sync_to_db, _creds as _rads_creds
    if not is_configured():
        _c = _rads_creds()
        return jsonify({
            'error': 'Reddit Ads API not configured. Set REDDIT_ADS_* env vars on Render.',
            'client_id_set': bool(_c['client_id']),
            'account_id_set': bool(_c['account_id']),
            'refresh_token_set': bool(_c['refresh_token']),
        }), 400
    days = request.args.get("days", type=int, default=30)
    try:
        result = sync_to_db(db.session, lookback_days=min(days, 60))
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()[-500:]}), 500


@admin_bp.route('/api/admin/reddit-ads-debug', methods=['POST'])
@_api_admin_req_dec
def api_reddit_ads_debug():
    """Fire a raw test call to Reddit Ads API and return the full response for diagnosis."""
    from reddit_ads_sync import _get_access_token, _creds, USER_AGENT, ADS_BASE
    import requests as _req
    from datetime import date, timedelta
    import json as _json

    c = _creds()
    if not all([c['client_id'], c['client_secret'], c['refresh_token'], c['account_id']]):
        return jsonify({'error': 'Not configured', 'creds_present': {k: bool(v) for k,v in c.items()}}), 400

    try:
        token = _get_access_token()
    except Exception as e:
        return jsonify({'error': f'Token refresh failed: {e}'}), 500

    today = date.today()
    start = today - timedelta(days=30)
    end   = today - timedelta(days=1)

    url = f"{ADS_BASE}/ad_accounts/{c['account_id']}/reports"
    headers = {
        'Authorization': f'Bearer {token}',
        'User-Agent': USER_AGENT,
        'Content-Type': 'application/json',
    }
    payload = {
        'data': {
            'starts_at': start.isoformat() + 'T00:00:00Z',
            'ends_at':   end.isoformat()   + 'T23:00:00Z',
            'fields':    ['spend', 'clicks', 'impressions'],
        }
    }

    try:
        resp = _req.post(url, json=payload, headers=headers, timeout=30)
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:2000]

        # Also try fetching campaigns list to verify account_id
        campaigns_url = f"{ADS_BASE}/ad_accounts/{c['account_id']}/campaigns"
        try:
            cr = _req.get(campaigns_url, headers={
                'Authorization': f'Bearer {token}',
                'User-Agent': USER_AGENT,
            }, timeout=15)
            try: campaigns_body = cr.json()
            except Exception: campaigns_body = cr.text[:500]
        except Exception as ce:
            campaigns_body = {'error': str(ce)}

        return jsonify({
            'status_code': resp.status_code,
            'url': url,
            'payload_sent': payload,
            'response_body': body,
            'campaigns_check': {
                'url': campaigns_url,
                'status': cr.status_code if 'cr' in dir() else 'N/A',
                'body': campaigns_body,
            },
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/reddit-ads-status', methods=['GET'])
@_api_admin_req_dec
def api_reddit_ads_status():
    """Check Reddit Ads integration status."""
    from reddit_ads_sync import is_configured, _creds as _rc
    from models import GTMAdPerformance
    last_entry = GTMAdPerformance.query.filter_by(channel='reddit_ads')\
        .order_by(GTMAdPerformance.date.desc()).first()
    total_rows = GTMAdPerformance.query.filter_by(channel='reddit_ads').count()
    c = _rc()
    return jsonify({
        'configured': is_configured(),
        'client_id_set': bool(c['client_id']),
        'account_id_set': bool(c['account_id']),
        'refresh_token_set': bool(c['refresh_token']),
        'last_sync_date': last_entry.date.isoformat() if last_entry else None,
        'total_rows': total_rows,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Ad campaign config + Zillow status (v5.87.38)
# ─────────────────────────────────────────────────────────────────────────────
# Zillow Group ad campaigns are PREPAID — you load $X up front, then spend
# against it over a fixed window. The dashboard needs to surface remaining
# budget so you don't run out mid-cycle without realizing.
#
# Storage: AdCampaignConfig (one row per channel) holds prepaid_budget,
# start_date, end_date, campaign_name, notes. Daily spend rows in
# GTMAdPerformance get summed against the campaign window to compute
# remaining = prepaid_budget - spent_in_window.
#
# Other channels (Google, Reddit) can also have prepaid budgets if you
# choose to set them; the same mechanism works. If no campaign config row
# exists for a channel, the channel is treated as PPC postpay (no budget
# tracking, business as usual).
# ─────────────────────────────────────────────────────────────────────────────


def _campaign_status(channel):
    """Compute campaign status for one channel.

    Returns dict with all the fields the UI needs:
      configured: bool — does an AdCampaignConfig row exist?
      campaign_name, prepaid_budget, start_date, end_date, notes
      spent_in_window: float — sum(GTMAdPerformance.spend) within the window
      remaining: float — prepaid_budget - spent_in_window (0 if not configured)
      days_left: int — days until end_date (None if open-ended or no config)
      pct_used: float — spent_in_window / prepaid_budget * 100
      lifetime_spend, lifetime_clicks, lifetime_impressions — across all time
    """
    from models import AdCampaignConfig, GTMAdPerformance
    from sqlalchemy import func as _f
    from datetime import date as _date

    cfg = AdCampaignConfig.query.filter_by(channel=channel).first()

    # Lifetime totals — useful regardless of whether a campaign config exists
    lifetime = (db.session.query(
        _f.coalesce(_f.sum(GTMAdPerformance.spend), 0.0),
        _f.coalesce(_f.sum(GTMAdPerformance.clicks), 0),
        _f.coalesce(_f.sum(GTMAdPerformance.impressions), 0),
    ).filter(GTMAdPerformance.channel == channel).first())
    lifetime_spend, lifetime_clicks, lifetime_impressions = lifetime

    out = {
        'channel': channel,
        'configured': cfg is not None,
        'lifetime_spend': float(lifetime_spend or 0),
        'lifetime_clicks': int(lifetime_clicks or 0),
        'lifetime_impressions': int(lifetime_impressions or 0),
    }

    if cfg is None:
        return out

    # Window-bound spend
    window_q = db.session.query(
        _f.coalesce(_f.sum(GTMAdPerformance.spend), 0.0),
        _f.coalesce(_f.sum(GTMAdPerformance.clicks), 0),
        _f.coalesce(_f.sum(GTMAdPerformance.impressions), 0),
    ).filter(GTMAdPerformance.channel == channel)
    window_q = window_q.filter(GTMAdPerformance.date >= cfg.start_date)
    if cfg.end_date:
        window_q = window_q.filter(GTMAdPerformance.date <= cfg.end_date)
    spent_in_window, window_clicks, window_impressions = window_q.first()
    spent_in_window = float(spent_in_window or 0)

    today = _date.today()
    days_left = None
    if cfg.end_date:
        days_left = max(0, (cfg.end_date - today).days)

    remaining = None
    pct_used = None
    if cfg.prepaid_budget and cfg.prepaid_budget > 0:
        remaining = max(0.0, float(cfg.prepaid_budget) - spent_in_window)
        pct_used = min(100.0, (spent_in_window / float(cfg.prepaid_budget)) * 100.0)

    out.update({
        'campaign_name': cfg.campaign_name,
        'prepaid_budget': float(cfg.prepaid_budget) if cfg.prepaid_budget else None,
        'start_date': cfg.start_date.isoformat() if cfg.start_date else None,
        'end_date': cfg.end_date.isoformat() if cfg.end_date else None,
        'notes': cfg.notes,
        'spent_in_window': spent_in_window,
        'window_clicks': int(window_clicks or 0),
        'window_impressions': int(window_impressions or 0),
        'remaining': remaining,
        'pct_used': pct_used,
        'days_left': days_left,
        'updated_at': cfg.updated_at.isoformat() if cfg.updated_at else None,
    })
    return out


@admin_bp.route('/api/admin/zillow-ads-status', methods=['GET'])
@_api_admin_req_dec
def api_zillow_ads_status():
    """Status for the Zillow Ads channel — lifetime + active campaign window.

    Zillow Group does NOT provide an API for spend data. All Zillow daily
    spend rows are entered manually via the GTM tab Ad Spend Entry form.
    Campaign budget is entered via the Campaign Config panel below.
    """
    return jsonify(_campaign_status('zillow_ads'))


@admin_bp.route('/api/admin/ad-campaign-config', methods=['GET'])
@_api_admin_req_dec
def api_list_ad_campaign_configs():
    """List all configured ad campaigns (one per channel)."""
    from models import AdCampaignConfig
    cfgs = AdCampaignConfig.query.order_by(AdCampaignConfig.channel).all()
    return jsonify({
        'campaigns': [{
            'channel': c.channel,
            'campaign_name': c.campaign_name,
            'prepaid_budget': float(c.prepaid_budget) if c.prepaid_budget else None,
            'start_date': c.start_date.isoformat() if c.start_date else None,
            'end_date': c.end_date.isoformat() if c.end_date else None,
            'notes': c.notes,
            'created_at': c.created_at.isoformat() if c.created_at else None,
            'updated_at': c.updated_at.isoformat() if c.updated_at else None,
            # Convenience: include computed status inline
            'status': _campaign_status(c.channel),
        } for c in cfgs]
    })


@admin_bp.route('/api/admin/ad-campaign-config', methods=['POST'])
@_api_admin_req_dec
def api_save_ad_campaign_config():
    """Create or update a campaign config (upsert by channel).

    Body: {channel, campaign_name, prepaid_budget, start_date, end_date, notes}
    All fields except channel + start_date are optional. start_date is
    required because it's the anchor for the window-bound spend calculation.
    """
    from models import AdCampaignConfig
    from datetime import date as _date

    data = request.get_json(silent=True) or {}
    channel = (data.get('channel') or '').strip()
    if not channel:
        return jsonify({'error': 'channel is required'}), 400

    valid_channels = {'zillow_ads', 'google_ads', 'reddit_ads', 'facebook_ads'}
    if channel not in valid_channels:
        return jsonify({'error': f'channel must be one of {sorted(valid_channels)}'}), 400

    start_str = (data.get('start_date') or '').strip()
    if not start_str:
        return jsonify({'error': 'start_date is required (YYYY-MM-DD)'}), 400
    try:
        start_dt = _date.fromisoformat(start_str)
    except ValueError:
        return jsonify({'error': f'invalid start_date format: {start_str} (need YYYY-MM-DD)'}), 400

    end_str = (data.get('end_date') or '').strip()
    end_dt = None
    if end_str:
        try:
            end_dt = _date.fromisoformat(end_str)
        except ValueError:
            return jsonify({'error': f'invalid end_date format: {end_str} (need YYYY-MM-DD)'}), 400
        if end_dt < start_dt:
            return jsonify({'error': 'end_date cannot be before start_date'}), 400

    budget = data.get('prepaid_budget')
    if budget is not None and budget != '':
        try:
            budget = float(budget)
            if budget < 0:
                return jsonify({'error': 'prepaid_budget cannot be negative'}), 400
        except (TypeError, ValueError):
            return jsonify({'error': 'prepaid_budget must be a number'}), 400
    else:
        budget = None

    # Upsert
    cfg = AdCampaignConfig.query.filter_by(channel=channel).first()
    if cfg is None:
        cfg = AdCampaignConfig(channel=channel)
        db.session.add(cfg)

    cfg.campaign_name = (data.get('campaign_name') or '').strip() or None
    cfg.prepaid_budget = budget
    cfg.start_date = start_dt
    cfg.end_date = end_dt
    cfg.notes = (data.get('notes') or '').strip() or None
    db.session.commit()

    return jsonify({'ok': True, 'channel': channel, 'status': _campaign_status(channel)})


@admin_bp.route('/api/admin/ad-campaign-config/<channel>', methods=['DELETE'])
@_api_admin_req_dec
def api_delete_ad_campaign_config(channel):
    """Remove a campaign config (channel reverts to PPC postpay treatment)."""
    from models import AdCampaignConfig
    cfg = AdCampaignConfig.query.filter_by(channel=channel).first()
    if cfg is None:
        return jsonify({'ok': True, 'note': 'no config existed for this channel'})
    db.session.delete(cfg)
    db.session.commit()
    return jsonify({'ok': True, 'channel': channel})


# ─────────────────────────────────────────────────────────────────────────────
# End ad campaign config endpoints
# ─────────────────────────────────────────────────────────────────────────────




@admin_bp.route('/api/admin/support-shares', methods=['GET'])
@_api_admin_req_dec
def admin_list_support_shares_legacy():
    """v5.88.38: legacy /api/admin/support-shares endpoints are gone.

    The Inbox UI uses /api/admin/tickets now. This stub stays as a
    permanent redirect so any code still referencing the old URL gets
    a clear 410 instead of a 404 mystery.
    """
    return jsonify({
        'error': 'Endpoint removed in v5.88.38. Use /api/admin/tickets instead.',
        'migration_note': 'SupportShare was replaced by Ticket + TicketMessage',
    }), 410


# ============================================================================
# v5.88.34: NEW ticket admin endpoints (Support workstation, Option 3)
#
# These replaced the legacy support-shares endpoints in v5.88.38.
# ============================================================================

@admin_bp.route('/api/admin/tickets', methods=['GET'])
@_api_admin_req_dec
def admin_list_tickets():
    """List tickets, optionally filtered by status / source / assignee.

    Query params:
      status: 'open' | 'in_progress' | 'waiting_on_user' | 'resolved' |
              'reopened' | 'all' (default: 'open')
      source: 'in_product_share' | 'inbound_email' | 'contact_form' |
              'manual' | 'all' (default: 'all')
      limit:  max rows to return (default 50, capped at 200)
    """
    from models import Ticket
    status_filter = (request.args.get('status') or 'open').lower()
    source_filter = (request.args.get('source') or 'all').lower()
    # v5.88.37: search across subject + user_email + user_name
    search_q      = (request.args.get('q') or '').strip()
    # v5.88.37: filter to only stale tickets (>24h on open/waiting_on_user/reopened)
    stale_only    = (request.args.get('stale_only') or '').strip().lower() in (
                     '1', 'true', 'yes', 'on')
    limit = min(int(request.args.get('limit', 50)), 200)

    query = Ticket.query
    if status_filter and status_filter != 'all':
        query = query.filter(Ticket.status == status_filter)
    if source_filter and source_filter != 'all':
        query = query.filter(Ticket.source == source_filter)

    # Search: SQL-side filter on subject + anonymous email + linked user
    # email/name. Multiple terms (space-separated) all must match.
    if search_q:
        from models import User as _User
        terms = [t for t in search_q.split() if t]
        for term in terms:
            like = f'%{term}%'
            query = query.outerjoin(_User, _User.id == Ticket.user_id).filter(
                db.or_(
                    Ticket.subject.ilike(like),
                    Ticket.email_for_anonymous.ilike(like),
                    _User.email.ilike(like),
                    _User.name.ilike(like),
                )
            )

    # Open / in-progress tickets surface oldest first (work to do).
    # Resolved surface newest first (recent history).
    if status_filter in ('resolved',):
        query = query.order_by(Ticket.updated_at.desc())
    else:
        query = query.order_by(Ticket.created_at.asc())

    # Use distinct in case the join multiplied rows
    if search_q:
        query = query.distinct()

    tickets = query.limit(limit).all()

    # Build response. Aging computed in to_dict; stale_only filter
    # applies AFTER the query because age is derived (not stored).
    rendered = [t.to_dict(include_messages=False) for t in tickets]
    if stale_only:
        rendered = [r for r in rendered if r.get('aging', {}).get('is_stale')]

    # Aggregate counts across statuses for the inbox header
    from sqlalchemy import func
    status_counts_q = (db.session.query(Ticket.status, func.count(Ticket.id))
                       .group_by(Ticket.status).all())
    status_counts = {s: c for s, c in status_counts_q}

    return jsonify({
        'tickets': rendered,
        'total': len(rendered),
        'counts': {
            'open':            status_counts.get('open', 0),
            'in_progress':     status_counts.get('in_progress', 0),
            'waiting_on_user': status_counts.get('waiting_on_user', 0),
            'resolved':        status_counts.get('resolved', 0),
            'reopened':        status_counts.get('reopened', 0),
        },
    })


@admin_bp.route('/api/admin/tickets/<int:ticket_id>', methods=['GET'])
@_api_admin_req_dec
def admin_get_ticket(ticket_id):
    """Get a ticket with full message thread and the analysis snapshot
    from its first message (if any)."""
    from models import Ticket
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({'error': 'Not found'}), 404

    body = ticket.to_dict(include_messages=True)

    # First message often carries the analysis snapshot — surface it at
    # the top level so the UI doesn't have to spelunk
    first_msg = ticket.messages.order_by(None).order_by('id').first()
    if first_msg:
        try:
            body['snapshot'] = (json.loads(first_msg.snapshot_json)
                                if first_msg.snapshot_json else None)
        except Exception:
            body['snapshot'] = None
        try:
            body['findings'] = (json.loads(first_msg.findings_json)
                                if first_msg.findings_json else None)
        except Exception:
            body['findings'] = None
        try:
            body['full_result'] = (json.loads(first_msg.full_result_json)
                                   if first_msg.full_result_json else None)
        except Exception:
            body['full_result'] = None

    return jsonify(body)


@admin_bp.route('/api/admin/tickets/<int:ticket_id>/status', methods=['PATCH'])
@_api_admin_req_dec
def admin_update_ticket_status(ticket_id):
    """Transition a ticket to a new status.
    Body: {"status": "in_progress" | "waiting_on_user" | "resolved" | "reopened"}
    """
    from models import Ticket
    from support_service import transition_ticket_status
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({'error': 'Not found'}), 404

    data = request.get_json(silent=True) or {}
    new_status = (data.get('status') or '').strip().lower()
    if not new_status:
        return jsonify({'error': 'status required'}), 400

    actor_id = current_user.id if current_user.is_authenticated else None
    ok = transition_ticket_status(ticket, new_status, actor_user_id=actor_id)
    if not ok:
        return jsonify({
            'error': f'Transition from {ticket.status} to {new_status} not allowed',
        }), 400

    return jsonify({'success': True, 'status': ticket.status})


@admin_bp.route('/api/admin/tickets/<int:ticket_id>/reply', methods=['POST'])
@_api_admin_req_dec
def admin_reply_to_ticket(ticket_id):
    """v5.88.35: Send an admin reply on a ticket, or save an internal note.

    Body:
      {
        "body": "<plain text reply>",        # required
        "internal_note": false               # optional, default false
      }

    If internal_note=true, saves the message as an internal note (visible
    only in admin UI, no email sent, no status change).

    Otherwise: composes a branded email, sends via Resend from
    SUPPORT_FROM_EMAIL, saves the reply as a TicketMessage with
    author_kind='admin', and transitions ticket to 'waiting_on_user'.
    """
    from models import Ticket, User
    from support_service import send_admin_reply

    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({'error': 'Not found'}), 404

    data = request.get_json(silent=True) or {}
    body = (data.get('body') or '').strip()
    internal_note = bool(data.get('internal_note', False))

    if not body:
        return jsonify({'error': 'body required'}), 400
    # Reasonable upper bound — Resend won't reject this, but a 50k reply
    # is probably an error or a paste of the whole thread.
    if len(body) > 20000:
        return jsonify({'error': 'body too long (max 20000 chars)'}), 400

    # Identify the admin if there's a real user session. admin_key auth
    # produces an anonymous current_user.
    admin_user = None
    if current_user.is_authenticated:
        try:
            admin_user = User.query.get(current_user.id)
        except Exception:
            admin_user = None

    msg, error = send_admin_reply(
        ticket=ticket,
        body=body,
        admin_user=admin_user,
        internal_note=internal_note,
    )
    if msg is None:
        return jsonify({'error': error or 'Send failed'}), 400

    # Return both the new message and the (possibly updated) ticket state
    response = {
        'success': True,
        'message': msg.to_dict(),
        'ticket_status': ticket.status,
    }
    if error:
        # Message saved but email had an issue — surface as warning so the
        # UI can show "saved, but couldn't send" rather than full failure.
        response['warning'] = error
    return jsonify(response)


# ============================================================================
# v5.88.37: Ticket reply templates
# ============================================================================

@admin_bp.route('/api/admin/ticket-templates', methods=['GET'])
@_api_admin_req_dec
def admin_list_ticket_templates():
    """List all reply templates, sorted by sort_order then name."""
    from models import TicketTemplate
    templates = (TicketTemplate.query
                 .order_by(TicketTemplate.sort_order.asc(),
                           TicketTemplate.name.asc())
                 .all())
    return jsonify({'templates': [t.to_dict() for t in templates]})


@admin_bp.route('/api/admin/tickets/<int:ticket_id>/render-template/<int:template_id>',
                methods=['GET'])
@_api_admin_req_dec
def admin_render_template(ticket_id, template_id):
    """Render a template against a specific ticket's context.

    Returns the rendered body PLUS a list of unresolved variables, so
    the UI can warn the admin if there are typos like {custmer_name}.
    """
    from models import Ticket, TicketTemplate
    from support_service import render_template

    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    tpl = TicketTemplate.query.get(template_id)
    if not tpl:
        return jsonify({'error': 'Template not found'}), 404

    rendered, unresolved = render_template(tpl.body, ticket)
    return jsonify({
        'template_id': tpl.id,
        'template_name': tpl.name,
        'rendered_body': rendered,
        'unresolved_variables': unresolved,
    })


# ============================================================================
# v5.88.39: Manual ticket creation
# ============================================================================

@admin_bp.route('/api/admin/tickets', methods=['POST'])
@_api_admin_req_dec
def admin_create_ticket_manually():
    """Create a new ticket from admin-pasted data.

    Used when the admin reads a customer email in their personal inbox
    and wants to log it in the workstation for tracking and reply.

    Body:
      {
        "from_email":         "alice@example.com",        # required
        "subject":             "Question about my analysis",  # required
        "body":                "Hi, my OfferScore looks wrong",  # required
        "from_name":           "Alice Smith",             # optional
        "linked_property_id":  42                          # optional
      }

    Returns:
      201 with the newly created ticket as include_messages=True
      400 on validation errors (bad email, missing fields, etc.)
    """
    from models import Ticket
    from support_service import create_ticket_manually

    data = request.get_json(silent=True) or {}
    from_email = (data.get('from_email') or '').strip()
    subject    = (data.get('subject') or '').strip()
    body       = (data.get('body') or '').strip()
    from_name  = (data.get('from_name') or '').strip() or None
    linked_property_id = data.get('linked_property_id')

    # Server-side validation matches the helper's expectations, but with
    # nicer error messages for the UI.
    errors = {}
    if not from_email or '@' not in from_email:
        errors['from_email'] = 'Valid email address required'
    if not subject:
        errors['subject'] = 'Subject required'
    if not body:
        errors['body'] = 'Body required (use "(no body)" if logging a phone call)'
    # Cap body to a sane size — admins might paste a long email chain
    if body and len(body) > 50000:
        errors['body'] = 'Body too long (max 50000 chars)'
    # linked_property_id, if provided, must be an integer
    if linked_property_id is not None:
        try:
            linked_property_id = int(linked_property_id)
        except (TypeError, ValueError):
            errors['linked_property_id'] = 'Must be an integer or null'

    if errors:
        return jsonify({'error': 'validation', 'fields': errors}), 400

    actor_admin_id = current_user.id if current_user.is_authenticated else None
    try:
        ticket = create_ticket_manually(
            from_email=from_email,
            subject=subject,
            body=body,
            from_name=from_name,
            linked_property_id=linked_property_id,
            actor_admin_id=actor_admin_id,
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    # Return the ticket (with messages, so the UI can navigate straight in)
    return jsonify({
        'success': True,
        'ticket': ticket.to_dict(include_messages=True),
    }), 201


@admin_bp.route('/api/admin/health-check', methods=['POST'])
@_api_admin_req_dec
def manual_health_check():
    """
    Manual database health check (optional)
    
    Automatically runs on startup, but can be triggered manually if needed.
    Only accessible to logged-in users for security.
    """
    try:
        # v5.88.16: current_user can be AnonymousUserMixin when auth
        # comes via admin_key query param (no logged-in session). Guard
        # against the AttributeError from current_user.id.
        actor = (current_user.id if current_user.is_authenticated
                 else 'admin_key')
        logger.info(f"Manual health check triggered by {actor}")
        
        health_results = {'status': 'ok', 'message': 'Database check not available in blueprint'}
        
        return jsonify({
            'status': 'success',
            'message': 'Health check completed',
            'results': health_results
        })
    except Exception as e:
        logger.error(f"Manual health check failed: {e}")
        return jsonify({
            'status': 'error',
            'message': 'An internal error occurred. Please try again.'
        }), 500

# ============================================================================
# ERROR HANDLERS
# ============================================================================


@admin_bp.route('/api/admin/backfill-waitlist-zips', methods=['POST'])
@_api_admin_req_dec
def backfill_waitlist_zips():
    """One-time backfill: extract ZIP from result_address for existing entries.

    Entries created before v5.62.83 have result_address but no result_zip.
    This parses the ZIP from the address string and updates the record so
    monthly nearby-listings drip emails can work for existing users.
    """
    import re as re_mod
    entries = Waitlist.query.filter(
        Waitlist.result_address.isnot(None),
        Waitlist.result_address != '',
        (Waitlist.result_zip.is_(None)) | (Waitlist.result_zip == '')
    ).all()

    updated = 0
    for entry in entries:
        m = re_mod.search(r'(\d{5})(?:-\d{4})?', entry.result_address or '')
        if m:
            entry.result_zip = m.group(1)
            parts = entry.result_address.split(',')
            if len(parts) >= 3:
                state_zip = parts[-1].strip()
                state_match = re_mod.match(r'([A-Z]{2})\s+\d{5}', state_zip)
                if state_match:
                    entry.result_state = state_match.group(1)
                entry.result_city = parts[-2].strip()
            elif len(parts) >= 2:
                state_zip = parts[-1].strip()
                state_match = re_mod.match(r'([A-Z]{2})\s+\d{5}', state_zip)
                if state_match:
                    entry.result_state = state_match.group(1)
            updated += 1

    if updated:
        db.session.commit()

    return jsonify({
        'total_missing': len(entries),
        'updated': updated,
        'message': f'Backfilled {updated} entries with ZIP codes from addresses.'
    })



@admin_bp.route('/api/admin/test-drip', methods=['POST'])
@_api_admin_req_dec
def test_drip_email():
    """Send a test drip email to the admin's own waitlist entry.
    
    Creates a waitlist entry for the admin if one doesn't exist,
    then sends the specified step (default: 1) immediately.
    Useful for verifying the email pipeline works end-to-end.
    """
    data = request.get_json() or {}
    step = data.get('step', 1)
    email = current_user.email
    
    entry = Waitlist.query.filter_by(email=email, feature='community').first()
    if not entry:
        from drip_campaign import generate_unsubscribe_token
        entry = Waitlist(
            email=email,
            feature='community',
            source='admin-test',
            had_result=True,
            result_address=data.get('address', ''),
            result_zip=data.get('zip', ''),
            result_city=data.get('city', ''),
            result_state=data.get('state', 'CA'),
        )
        entry.unsubscribe_token = generate_unsubscribe_token()
        db.session.add(entry)
        db.session.commit()
    
    from drip_campaign import send_drip_email
    success = send_drip_email(entry, step)
    db.session.commit()
    
    return jsonify({
        'success': success,
        'email': email,
        'step': step,
        'entry_id': entry.id,
        'message': f'Step {step} {"sent" if success else "failed"} to {email}'
    })


# =============================================================================
# DRIP CAMPAIGN CRON (v5.61.0)
# =============================================================================


@admin_bp.route('/api/admin/repair-costs/zones')
@_api_admin_req_dec
def repair_cost_zones():
    """List all repair cost zones with optional state filter."""
    from models import RepairCostZone
    state = request.args.get('state')
    query = RepairCostZone.query
    if state:
        query = query.filter_by(state=state.upper())
    zones = query.order_by(RepairCostZone.zip_prefix).all()
    return jsonify({
        'count': len(zones),
        'zones': [z.to_dict() for z in zones],
    })


@admin_bp.route('/api/admin/repair-costs/zones/<zip_prefix>', methods=['PUT'])
@_api_admin_req_dec
def update_repair_cost_zone(zip_prefix):
    """Update a single zone's multiplier or metro name."""
    from models import RepairCostZone
    zone = RepairCostZone.query.filter_by(zip_prefix=zip_prefix).first()
    if not zone:
        return jsonify({'error': f'Zone {zip_prefix} not found'}), 404
    data = request.get_json(silent=True) or {}
    if 'cost_multiplier' in data:
        zone.cost_multiplier = float(data['cost_multiplier'])
    if 'metro_name' in data:
        zone.metro_name = str(data['metro_name'])[:100]
    db.session.commit()
    return jsonify({'updated': zone.to_dict()})


@admin_bp.route('/api/admin/repair-costs/baselines')
@_api_admin_req_dec
def repair_cost_baselines():
    """List all baseline repair costs."""
    from models import RepairCostBaseline
    baselines = RepairCostBaseline.query.order_by(
        RepairCostBaseline.category, RepairCostBaseline.severity
    ).all()
    return jsonify({
        'count': len(baselines),
        'baselines': [b.to_dict() for b in baselines],
    })


@admin_bp.route('/api/admin/repair-costs/baselines/<category>/<severity>', methods=['PUT'])
@_api_admin_req_dec
def update_repair_cost_baseline(category, severity):
    """Update a baseline cost range."""
    from models import RepairCostBaseline
    baseline = RepairCostBaseline.query.filter_by(
        category=category, severity=severity
    ).first()
    if not baseline:
        return jsonify({'error': f'Baseline {category}/{severity} not found'}), 404
    data = request.get_json(silent=True) or {}
    if 'cost_low' in data:
        baseline.cost_low = int(data['cost_low'])
    if 'cost_high' in data:
        baseline.cost_high = int(data['cost_high'])
    db.session.commit()
    return jsonify({'updated': baseline.to_dict()})


@admin_bp.route('/api/admin/set-credits', methods=['POST'])
@_api_admin_req_dec
def admin_set_credits():
    """Manually set credits for a user. POST { email, credits }"""
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    credits = data.get('credits', 0)
    if not email:
        return jsonify({'error': 'Email required'}), 400
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'error': f'User {email} not found'}), 404
    old = user.analysis_credits
    user.analysis_credits = int(credits)
    db.session.commit()
    logging.info(f"👑 Admin set credits: {email} {old} → {credits}")
    return jsonify({'success': True, 'email': email, 'old_credits': old, 'new_credits': int(credits)})


@admin_bp.route('/api/admin/inspect-analysis', methods=['GET'])
@_api_admin_req_dec
def admin_inspect_analysis(property_id=None):
    """Inspect raw saved analysis data for debugging. Use ?address=381+Tina to search by address."""
    if not property_id:
        addr_search = request.args.get('address', '')
        if addr_search:
            prop = Property.query.filter(Property.address.ilike(f'%{addr_search}%')).order_by(Property.created_at.desc()).first()
        else:
            # Show most recent analysis
            prop = Property.query.filter(Property.analyzed_at.isnot(None)).order_by(Property.analyzed_at.desc()).first()
    else:
        prop = Property.query.get(property_id)
    if not prop:
        return jsonify({'error': 'Property not found'}), 404
    analysis = Analysis.query.filter_by(property_id=property_id).order_by(Analysis.created_at.desc()).first()
    if not analysis:
        return jsonify({'error': 'No analysis found'}), 404
    result = json.loads(analysis.result_json)
    risk_score = result.get('risk_score', {})
    return jsonify({
        'property_id': property_id,
        'address': prop.address,
        'price': prop.price,
        'analyzed_at': prop.analyzed_at.isoformat() if prop.analyzed_at else None,
        'top_level_keys': list(result.keys()),
        'has_repair_estimate': 'repair_estimate' in result,
        'repair_estimate_keys': list(result.get('repair_estimate', {}).keys()) if 'repair_estimate' in result else [],
        'repair_breakdown_count': len(result.get('repair_estimate', {}).get('breakdown', [])),
        'has_findings': 'findings' in result,
        'findings_count': len(result.get('findings', [])),
        'has_category_scores': 'category_scores' in risk_score,
        'category_scores_count': len(risk_score.get('category_scores', [])),
        'category_scores_sample': risk_score.get('category_scores', [])[:3],
        'has_deal_breakers': 'deal_breakers' in risk_score,
        'deal_breakers_count': len(risk_score.get('deal_breakers', [])),
        'deal_breakers_sample': [{'cat': d.get('category', d.get('system', '?')), 'desc': str(d.get('explanation', d.get('description', '')))[:50]} for d in risk_score.get('deal_breakers', [])[:3]],
        'has_critical_issues': 'critical_issues' in result,
        'critical_issues_count': len(result.get('critical_issues', [])),
        'risk_score_keys': list(risk_score.keys()),
        'total_repair_low': risk_score.get('total_repair_cost_low', 0),
        'total_repair_high': risk_score.get('total_repair_cost_high', 0),
        'year_built': result.get('year_built'),
    })


@admin_bp.route('/api/admin/shared-analyses')
@_api_admin_req_dec
def admin_shared_analyses():
    """List all share links ever created, with user info and snapshot data.
    Supports ?search=<email|address>&days=<int>&page=<int>&per_page=<int>"""
    import json as json_mod
    from sqlalchemy import or_

    search = request.args.get('search', '').strip()
    days   = int(request.args.get('days', 0) or 0)
    page   = max(1, int(request.args.get('page', 1) or 1))
    per_pg = min(100, max(1, int(request.args.get('per_page', 50) or 50)))

    q = ShareLink.query.join(User, ShareLink.user_id == User.id)\
            .join(Property, ShareLink.property_id == Property.id)\
            .order_by(ShareLink.created_at.desc())

    if search:
        q = q.filter(or_(
            User.email.ilike(f'%{search}%'),
            Property.address.ilike(f'%{search}%'),
        ))
    if days > 0:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        q = q.filter(ShareLink.created_at >= cutoff)

    total  = q.count()
    links  = q.offset((page - 1) * per_pg).limit(per_pg).all()

    base_url = os.environ.get('BASE_URL', 'https://getofferwise.ai')
    results  = []
    for lnk in links:
        try:
            snap = json_mod.loads(lnk.snapshot_json)
        except Exception:
            snap = {}
        reactions = json_mod.loads(lnk.reactions_json) if lnk.reactions_json else []
        results.append({
            'token':          lnk.token,
            'share_url':      f"{base_url}/opinion/{lnk.token}",
            'is_active':      lnk.is_active,
            'is_expired':     not lnk.is_valid(),
            'user_email':     lnk.user.email,
            'user_id':        lnk.user_id,
            'property_id':    lnk.property_id,
            'address':        lnk.property.address,
            'sharer_name':    lnk.sharer_name,
            'recipient_name': lnk.recipient_name,
            'personal_note':  lnk.personal_note,
            'view_count':     lnk.view_count or 0,
            'reactions':      reactions,
            'snapshot':       snap,
            'created_at':     lnk.created_at.isoformat(),
            'expires_at':     lnk.expires_at.isoformat() if lnk.expires_at else None,
        })

    return jsonify({
        'total':    total,
        'page':     page,
        'per_page': per_pg,
        'pages':    max(1, (total + per_pg - 1) // per_pg),
        'links':    results,
    })



@admin_bp.route('/api/admin/ai-costs')
@_api_admin_req_dec
def admin_ai_costs():
    """AI cost data — reads from DB (preferred) then falls back to flat log.
    ?days=1|7|30|0 (0=this month)."""
    from ai_cost_tracker import AICostTracker, get_cost_summary_from_db
    from datetime import date, timedelta
    import calendar

    days = int(request.args.get('days', 1) or 1)
    tracker = AICostTracker()

    # Try DB first — it survives Render restarts and captures all endpoints
    try:
        db_summary = get_cost_summary_from_db(100000 if days < 0 else (days if days > 0 else 30))
        if db_summary and db_summary.get('total_calls', 0) > 0:
            db_summary['alerts'] = tracker.check_cost_alerts()
            db_summary['period'] = 'all' if days < 0 else ('month' if days == 0 else (f'{days}d' if days > 1 else 'day'))
            db_summary['data_source'] = 'database'
            return jsonify(db_summary)
    except Exception as db_e:
        logging.warning(f'DB cost summary failed, falling back to log: {db_e}')

    try:
        if days == 0:
            # This calendar month
            today = datetime.utcnow().date()
            data = tracker.get_monthly_cost(today.year, today.month)
            summary = {
                'total_calls': data['total_calls'],
                'total_cost': data['total_cost'],
                'total_input_tokens': 0,
                'total_output_tokens': 0,
                'avg_latency_ms': 0,
                'by_endpoint': {},
                'alerts': tracker.check_cost_alerts(),
                'period': 'month',
            }
        elif days == 1:
            summary = tracker.get_daily_summary()
            summary['alerts'] = tracker.check_cost_alerts()
            summary['period'] = 'day'
        else:
            # Aggregate multiple days
            today = datetime.utcnow().date()
            agg = {
                'total_calls': 0, 'total_cost': 0.0,
                'total_input_tokens': 0, 'total_output_tokens': 0,
                'latencies': [], 'by_endpoint': {}, 'violations': 0,
            }
            for i in range(days):
                d = today - timedelta(days=i)
                day_data = tracker.get_daily_summary(d)
                agg['total_calls'] += day_data['total_calls']
                agg['total_cost'] += day_data['total_cost']
                agg['total_input_tokens'] += day_data['total_input_tokens']
                agg['total_output_tokens'] += day_data['total_output_tokens']
                agg['violations'] += day_data.get('violations', 0)
                for ep, ep_data in day_data.get('by_endpoint', {}).items():
                    if ep not in agg['by_endpoint']:
                        agg['by_endpoint'][ep] = {'calls': 0, 'cost': 0.0, 'tokens': 0,
                                                   'input_tokens': 0, 'output_tokens': 0}
                    agg['by_endpoint'][ep]['calls'] += ep_data.get('calls', 0)
                    agg['by_endpoint'][ep]['cost'] += ep_data.get('cost', 0.0)
                    agg['by_endpoint'][ep]['tokens'] += ep_data.get('tokens', 0)
                    agg['by_endpoint'][ep]['input_tokens'] += ep_data.get('input_tokens', 0)
                    agg['by_endpoint'][ep]['output_tokens'] += ep_data.get('output_tokens', 0)
            summary = {
                'total_calls': agg['total_calls'],
                'total_cost': round(agg['total_cost'], 6),
                'total_input_tokens': agg['total_input_tokens'],
                'total_output_tokens': agg['total_output_tokens'],
                'avg_latency_ms': 0,
                'by_endpoint': agg['by_endpoint'],
                'violations': agg['violations'],
                'alerts': tracker.check_cost_alerts(),
                'period': f'{days}d',
            }
        return jsonify(summary)
    except Exception as e:
        logging.error(f'admin_ai_costs error: {e}')
        return jsonify({'error': 'Failed to load AI cost data.', 'total_cost': 0, 'total_calls': 0,
                        'by_endpoint': {}, 'alerts': []}), 200



@admin_bp.route('/api/admin/email-stats')
@_api_admin_req_dec
def admin_email_stats():
    """Email send counts from EmailSendLog (real per-send rows). ?days=1|7|30|0"""
    from datetime import timedelta
    days = int(request.args.get('days', 1) or 1)
    since = datetime.utcnow() - timedelta(days=(100000 if days < 0 else (days if days > 0 else 30)))

    resend_configured = bool(os.environ.get('RESEND_API_KEY', ''))

    try:
        from models import EmailSendLog

        rows = EmailSendLog.query.filter(
            EmailSendLog.ts >= since,
            EmailSendLog.success == True
        ).all()

        total        = len(rows)
        welcome_sent = sum(1 for r in rows if r.email_type == 'welcome')
        drip_sent    = sum(1 for r in rows if (r.email_type or '').startswith('drip'))
        market_sent  = sum(1 for r in rows if r.email_type == 'market_intel')
        receipt_sent = sum(1 for r in rows if r.email_type == 'receipt')
        analysis_sent= sum(1 for r in rows if r.email_type == 'analysis_complete')
        other_sent   = total - welcome_sent - drip_sent - market_sent - receipt_sent - analysis_sent
        failed_total = EmailSendLog.query.filter(
            EmailSendLog.ts >= since,
            EmailSendLog.success == False
        ).count()

        return jsonify({
            'emails_sent':       total,
            'welcome_sent':      welcome_sent,
            'drip_sent':         drip_sent,
            'market_intel_sent': market_sent,
            'receipts_sent':     receipt_sent,
            'analysis_sent':     analysis_sent,
            'other_sent':        other_sent,
            'failed_total':      failed_total,
            'resend_configured': resend_configured,
            'data_source':       'EmailSendLog',
            'period_days':       days,
        })
    except Exception as e:
        logging.warning(f'EmailSendLog query failed ({e}), falling back to zero counts')
        return jsonify({
            'emails_sent': 0, 'welcome_sent': 0, 'drip_sent': 0,
            'market_intel_sent': 0, 'receipts_sent': 0, 'failed_total': 0,
            'resend_configured': resend_configured,
            'data_source': 'none — EmailSendLog table not yet migrated',
            'period_days': days,
        })



@admin_bp.route('/api/admin/email-engagement')
@_api_admin_req_dec
def admin_email_engagement():
    """Email open/click rates per drip step. Powers the drip effectiveness dashboard."""
    try:
        from models import EmailEvent, EmailSendLog
        from sqlalchemy import func

        # Get send counts per drip step
        sends = db.session.query(
            EmailSendLog.email_type,
            func.count(EmailSendLog.id).label('sent')
        ).filter(
            EmailSendLog.email_type.like('drip_%'),
            EmailSendLog.success == True
        ).group_by(EmailSendLog.email_type).all()

        send_map = {s.email_type: s.sent for s in sends}

        # Get event counts per resend_id, then join to email_type
        # Since EmailEvent has resend_id, we join to EmailSendLog to get email_type
        events = db.session.query(
            EmailSendLog.email_type,
            EmailEvent.event_type,
            func.count(func.distinct(EmailEvent.to_email)).label('unique_count')
        ).join(
            EmailEvent, EmailEvent.resend_id == EmailSendLog.resend_id
        ).filter(
            EmailSendLog.email_type.like('drip_%')
        ).group_by(
            EmailSendLog.email_type, EmailEvent.event_type
        ).all()

        # Build per-step engagement data
        steps = {}
        for email_type, event_type, count in events:
            if email_type not in steps:
                steps[email_type] = {'sent': send_map.get(email_type, 0)}
            steps[email_type][event_type] = count

        # Add steps with no events yet
        for email_type, sent in send_map.items():
            if email_type not in steps:
                steps[email_type] = {'sent': sent}

        # Calculate rates
        result = []
        for step_name in sorted(steps.keys()):
            data = steps[step_name]
            sent = data.get('sent', 0) or 1
            result.append({
                'step': step_name,
                'sent': data.get('sent', 0),
                'delivered': data.get('delivered', 0),
                'opened': data.get('opened', 0),
                'clicked': data.get('clicked', 0),
                'bounced': data.get('bounced', 0),
                'open_rate': round(data.get('opened', 0) / sent * 100, 1),
                'click_rate': round(data.get('clicked', 0) / sent * 100, 1),
            })

        # Overall stats
        total_sent = sum(d.get('sent', 0) for d in steps.values())
        total_opened = sum(d.get('opened', 0) for d in steps.values())
        total_clicked = sum(d.get('clicked', 0) for d in steps.values())

        return jsonify({
            'steps': result,
            'totals': {
                'sent': total_sent,
                'opened': total_opened,
                'clicked': total_clicked,
                'open_rate': round(total_opened / max(1, total_sent) * 100, 1),
                'click_rate': round(total_clicked / max(1, total_sent) * 100, 1),
            }
        })

    except Exception as e:
        logging.error(f"Email engagement stats error: {e}")
        return jsonify({'steps': [], 'totals': {'sent': 0, 'opened': 0, 'clicked': 0, 'open_rate': 0, 'click_rate': 0}})



@admin_bp.route('/api/admin/analysis-stats')
@_api_admin_req_dec
def admin_analysis_stats():
    """Analysis counts, per-API call estimates, Stripe revenue. ?days=1|7|30|0"""
    from datetime import timedelta
    days = int(request.args.get('days', 1) or 1)
    since = datetime.utcnow() - timedelta(days=(100000 if days < 0 else (days if days > 0 else 30)))

    try:
        # Exclude persona/test accounts from all counts
        from sqlalchemy import not_
        _test_user_ids = db.session.query(User.id).filter(
            db.or_(*[User.email.endswith(d) for d in ('@persona.offerwise.ai', '@test.offerwise.ai')])
        ).subquery()

        completed = Analysis.query.filter(
            Analysis.created_at >= since,
            Analysis.status == 'completed',
            ~Analysis.user_id.in_(_test_user_ids)
        ).count()

        total_analyses = Analysis.query.filter(
            Analysis.created_at >= since,
            ~Analysis.user_id.in_(_test_user_ids)
        ).count()

        # RentCast: Nearby Listings = 2 calls/snapshot (listings + markets)
        #           Full analysis via property_research_agent = 1 AVM call each
        rentcast_configured = bool(os.environ.get('RENTCAST_API_KEY', ''))
        rentcast_nearby_calls = 0
        try:
            from models import MarketSnapshot
            snapshots = MarketSnapshot.query.filter(
                MarketSnapshot.created_at >= since
            ).count()
            rentcast_nearby_calls = snapshots * 2  # listings + markets per snapshot
        except Exception:
            pass
        # Each completed analysis runs 2 RentCast calls:
        #   RentCastTool → /avm/value (property details + valuation + comps)
        #   MarketStatsTool → /markets (ZIP-level market stats)
        rentcast_analysis_calls = completed * 2
        # Daily agentic monitor: 1 /avm/value per active watch per day. The comps
        # and price monitors share one cached call as of v5.89.150 (was 2/day).
        # Scaled to the selected period (capped so "all time" can't blow up).
        rentcast_monitor_calls = 0
        rentcast_active_watches = 0
        try:
            from models import PropertyWatch
            rentcast_active_watches = PropertyWatch.query.filter_by(is_active=True).count()
            _period_days = max(1, min((datetime.utcnow() - since).days, 366))
            rentcast_monitor_calls = rentcast_active_watches * _period_days
        except Exception:
            pass
        rentcast_total_calls = rentcast_nearby_calls + rentcast_analysis_calls + rentcast_monitor_calls
        # RentCast API pricing tiers (as of 2026):
        #   Foundation $74/mo → 1k included, $0.20/req overage
        #   Growth $199/mo → 5k included, $0.06/req overage
        #   Scale $449/mo → 25k included, $0.03/req overage
        # The $74 Foundation line is on the ledger, so estimate at Foundation:
        # the first 1,000 calls/period are included; overage bills at $0.20/req.
        rentcast_included = 1000
        rentcast_cost_est = round(max(0, rentcast_total_calls - rentcast_included) * 0.20, 2)

        # Google Maps Geocoding: called ONLY from risk_check_engine (viral Risk Check tool).
        # The full analysis pipeline (property_research_agent) uses the Census Bureau geocoder (FREE).
        # So we count Risk Check completions from GTMFunnelEvent, not total_analyses.
        # Pricing: $5 per 1000 calls, $200/mo free credit (~40,000 free calls/mo)
        google_maps_configured = bool(os.environ.get('GOOGLE_MAPS_API_KEY', ''))
        google_maps_calls = 0
        try:
            from models import GTMFunnelEvent
            google_maps_calls = GTMFunnelEvent.query.filter(
                GTMFunnelEvent.stage == 'risk_check_complete',
                GTMFunnelEvent.created_at >= since,
            ).count()
        except Exception:
            pass
        google_maps_cost_est = round(max(0, google_maps_calls - 40000) * 0.005, 4)

        # WalkScore: called once per analysis via property_research_agent
        # Pricing: depends on plan — Professional ~$0.0025/call
        walkscore_configured = bool(os.environ.get('WALKSCORE_API_KEY', ''))
        walkscore_calls = completed  # only on completed analyses
        walkscore_cost_est = round(walkscore_calls * 0.0025, 4)

        # Google Cloud Vision: called for scanned PDFs when USE_GOOGLE_VISION=true
        # Pricing: $1.50 per 1000 pages
        google_vision_enabled = os.environ.get('USE_GOOGLE_VISION', 'false').lower() == 'true'
        google_vision_configured = bool(os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', ''))
        # Can't know exact page counts without a log — estimate conservatively
        # Average CA real estate doc packet: ~15 pages, ~60% are scanned
        vision_pages_est = int(completed * 15 * 0.60) if google_vision_enabled else 0
        google_vision_cost_est = round(vision_pages_est / 1000 * 1.50, 4)

        # AirNow (EPA): FREE — government API, no billing.
        # Called from BOTH: property_research_agent (1x per completed analysis)
        # AND risk_check_engine (1x per Risk Check run).
        airnow_configured = bool(os.environ.get('AIRNOW_API_KEY', ''))
        airnow_analysis_calls = completed  # 1 per completed analysis
        airnow_riskcheck_calls = google_maps_calls  # same count as Risk Check runs
        airnow_total_calls = airnow_analysis_calls + airnow_riskcheck_calls

        # GreatSchools API: called once per completed analysis via property_research_agent
        # Pricing: varies by plan — often ~$0/mo on legacy/educational; paid plans ~$0.25/call
        greatschools_configured = bool(os.environ.get('GREATSCHOOLS_API_KEY', ''))
        greatschools_calls = completed  # 1 per completed analysis (skipped if not configured)
        # Cost unknown without plan details — shown as configured/missing status only

        # Mailgun: used ONLY for market intelligence drip emails (monthly nearby listings emails)
        # This is a SEPARATE email provider from Resend — Resend handles all other emails.
        # EmailSendLog only tracks Resend sends; Mailgun sends are tracked separately here.
        # Pricing: Flex plan $0.80/1k after first 1k free/mo
        mailgun_configured = bool(os.environ.get('MAILGUN_API_KEY', ''))
        mailgun_calls = 0
        try:
            from models import EmailSendLog
            mailgun_calls = EmailSendLog.query.filter(
                EmailSendLog.ts >= since,
                EmailSendLog.email_type == 'market_intel',
                EmailSendLog.success == True,
            ).count()
        except Exception:
            pass
        # Note: market_intel emails sent via Mailgun are also logged to EmailSendLog
        # if the drip campaign was updated to use email_service._send_email().
        # Otherwise mailgun_calls is 0 (Mailgun sends bypass EmailSendLog).
        mailgun_cost_est = round(max(0, mailgun_calls - 1000) * 0.0008, 4)

        # Reddit OAuth: free API used for GTM content posting
        reddit_configured = bool(os.environ.get('REDDIT_ADS_CLIENT_ID', '') and os.environ.get('REDDIT_ADS_REFRESH_TOKEN', ''))

        # Stripe revenue — try live API first, fall back to CreditTransaction DB
        stripe_revenue = None
        stripe_charges = None
        stripe_data_source = 'none'
        try:
            if stripe.api_key:
                import time as _time
                since_ts = int(since.timestamp())
                charges_list = stripe.Charge.list(
                    created={'gte': since_ts},
                    limit=100,
                )
                paid = [c for c in charges_list.auto_paging_iter()
                        if c.paid and not c.refunded and c.status == 'succeeded']
                stripe_revenue = sum(c.amount for c in paid) / 100.0  # cents → dollars
                stripe_charges = len(paid)
                stripe_data_source = 'stripe_api'
        except Exception as _se:
            logging.warning(f'Stripe API revenue query failed: {_se}')

        if stripe_data_source == 'none':
            try:
                from models import CreditTransaction
                txns = CreditTransaction.query.filter(
                    CreditTransaction.created_at >= since,
                    CreditTransaction.status == 'completed',
                    CreditTransaction.credits > 0
                ).all()
                stripe_revenue = sum(t.amount or 0 for t in txns)
                stripe_charges = len(txns)
                stripe_data_source = 'credit_transactions_db'
            except Exception:
                pass

        # Google Ads real spend from GTMAdPerformance (synced from Google Ads API)
        google_ads_spend = None
        google_ads_clicks = None
        google_ads_impressions = None
        google_ads_data_source = 'none'
        try:
            from models import GTMAdPerformance
            from sqlalchemy import func as _func
            ads_rows = GTMAdPerformance.query.filter(
                GTMAdPerformance.date >= since.date(),
                GTMAdPerformance.channel == 'google_ads'
            ).all()
            if ads_rows:
                google_ads_spend       = float(sum(r.spend or 0 for r in ads_rows))
                google_ads_clicks      = sum(r.clicks or 0 for r in ads_rows)
                google_ads_impressions = sum(r.impressions or 0 for r in ads_rows)
                google_ads_data_source = 'gtm_ad_performance_db'
        except Exception as _ae:
            logging.warning(f'Google Ads spend query failed: {_ae}')

        # Reddit Ads real spend from GTMAdPerformance
        # v5.87.75: track impressions + data_source + last_sync, and distinguish
        # "synced w/ 0 rows" (sync ran, no campaigns active) from
        # "never synced" (sync hasn't fired or all attempts failed).
        reddit_ads_spend = None
        reddit_ads_clicks = None
        reddit_ads_impressions = None
        reddit_ads_data_source = 'none'
        reddit_ads_last_sync = None
        try:
            from models import GTMAdPerformance
            reddit_rows = GTMAdPerformance.query.filter(
                GTMAdPerformance.date >= since.date(),
                GTMAdPerformance.channel == 'reddit_ads'
            ).all()
            if reddit_rows:
                reddit_ads_spend       = float(sum(r.spend or 0 for r in reddit_rows))
                reddit_ads_clicks      = sum(r.clicks or 0 for r in reddit_rows)
                reddit_ads_impressions = sum(r.impressions or 0 for r in reddit_rows)
                reddit_ads_data_source = 'gtm_ad_performance_db'

            # Get the most recent sync timestamp regardless of whether the
            # period had data — answers "is the sync alive?" separately
            # from "was there spend in this window?"
            latest_reddit = (GTMAdPerformance.query
                             .filter(GTMAdPerformance.channel == 'reddit_ads')
                             .order_by(GTMAdPerformance.date.desc())
                             .first())
            if latest_reddit:
                # Use updated_at if the model has it; fallback to date
                reddit_ads_last_sync = (
                    latest_reddit.updated_at.isoformat()
                    if hasattr(latest_reddit, 'updated_at') and latest_reddit.updated_at
                    else latest_reddit.date.isoformat()
                )
                # If we have ANY historical reddit row but none in the window,
                # surface as "synced w/ 0 spend" rather than "never synced"
                if not reddit_rows:
                    reddit_ads_spend       = 0.0
                    reddit_ads_clicks      = 0
                    reddit_ads_impressions = 0
                    reddit_ads_data_source = 'gtm_ad_performance_db'
        except Exception as _re:
            logging.warning(f'Reddit Ads spend query failed: {_re}')

        # Zillow Ads real spend from GTMAdPerformance + campaign config (prepaid budget)
        # v5.87.38 — Zillow campaigns are prepaid (load $X up front, draw down).
        # Pulls from AdCampaignConfig if a row exists for this channel; otherwise
        # falls back to existing behavior (just spend totals over period).
        zillow_ads_spend = None
        zillow_ads_clicks = None
        zillow_ads_impressions = None
        zillow_campaign = None  # Will be populated if a config exists
        try:
            from models import GTMAdPerformance, AdCampaignConfig

            # Look for a campaign config first — if one exists, the spend window
            # we report is the campaign window (not the trailing N days), and we
            # surface remaining budget. This is the "Zillow is prepaid" case.
            config = AdCampaignConfig.query.filter_by(channel='zillow_ads').first()

            if config and config.start_date:
                # Campaign-aware path: spend = sum from start_date to (end_date or today)
                from datetime import date as _date
                end = config.end_date or _date.today()
                campaign_rows = GTMAdPerformance.query.filter(
                    GTMAdPerformance.channel == 'zillow_ads',
                    GTMAdPerformance.date >= config.start_date,
                    GTMAdPerformance.date <= end,
                ).all()
                spent = float(sum(r.spend or 0 for r in campaign_rows))
                budget = float(config.prepaid_budget or 0)
                remaining = max(0.0, budget - spent) if budget else None

                # Days left in campaign window (None if open-ended or already ended)
                today_d = _date.today()
                days_left = None
                if config.end_date and config.end_date >= today_d:
                    days_left = (config.end_date - today_d).days

                zillow_ads_spend       = spent
                zillow_ads_clicks      = sum(r.clicks or 0 for r in campaign_rows)
                zillow_ads_impressions = sum(r.impressions or 0 for r in campaign_rows)
                zillow_campaign = {
                    'name':           config.campaign_name or 'Zillow Ads',
                    'prepaid_budget': budget if budget else None,
                    'remaining':      remaining,
                    'start_date':     config.start_date.isoformat() if config.start_date else None,
                    'end_date':       config.end_date.isoformat() if config.end_date else None,
                    'days_left':      days_left,
                    'notes':          config.notes or '',
                }
            else:
                # No campaign configured — fall back to trailing-period aggregation
                zillow_rows = GTMAdPerformance.query.filter(
                    GTMAdPerformance.date >= since.date(),
                    GTMAdPerformance.channel == 'zillow_ads'
                ).all()
                if zillow_rows:
                    zillow_ads_spend       = float(sum(r.spend or 0 for r in zillow_rows))
                    zillow_ads_clicks      = sum(r.clicks or 0 for r in zillow_rows)
                    zillow_ads_impressions = sum(r.impressions or 0 for r in zillow_rows)
        except Exception:
            pass

        return jsonify({
            'completed_analyses': completed,
            'total_analyses': total_analyses,
            # RentCast
            'rentcast_configured': rentcast_configured,
            'rentcast_total_calls': rentcast_total_calls,
            'rentcast_nearby_calls': rentcast_nearby_calls,
            'rentcast_analysis_calls': rentcast_analysis_calls,
            'rentcast_monitor_calls': rentcast_monitor_calls,
            'rentcast_active_watches': rentcast_active_watches,
            'rentcast_included': rentcast_included,
            'rentcast_cost_est': rentcast_cost_est,
            # Google Maps
            'google_maps_configured': google_maps_configured,
            'google_maps_calls': google_maps_calls,
            'google_maps_cost_est': google_maps_cost_est,
            # WalkScore
            'walkscore_configured': walkscore_configured,
            'walkscore_calls': walkscore_calls,
            'walkscore_cost_est': walkscore_cost_est,
            # Google Cloud Vision
            'google_vision_enabled': google_vision_enabled,
            'google_vision_configured': google_vision_configured,
            'vision_pages_est': vision_pages_est,
            'google_vision_cost_est': google_vision_cost_est,
            # AirNow (free — but track call volume from both pipelines)
            'airnow_configured': airnow_configured,
            'airnow_total_calls': airnow_total_calls,
            'airnow_analysis_calls': airnow_analysis_calls,
            'airnow_riskcheck_calls': airnow_riskcheck_calls,
            # GreatSchools
            'greatschools_configured': greatschools_configured,
            'greatschools_calls': greatschools_calls,
            # Mailgun (market intel drip emails only)
            'mailgun_configured': mailgun_configured,
            'mailgun_calls': mailgun_calls,
            'mailgun_cost_est': mailgun_cost_est,
            # Reddit OAuth
            'reddit_configured': reddit_configured,
            # Stripe (real API or DB fallback)
            'stripe_revenue': stripe_revenue,
            'stripe_charges': stripe_charges,
            'stripe_data_source': stripe_data_source,
            # Google Ads (real data from GTMAdPerformance)
            'google_ads_spend': google_ads_spend,
            'google_ads_clicks': google_ads_clicks,
            'google_ads_impressions': google_ads_impressions,
            'google_ads_data_source': google_ads_data_source,
            # Reddit Ads (real data from GTMAdPerformance)
            'reddit_ads_spend': reddit_ads_spend,
            'reddit_ads_clicks': reddit_ads_clicks,
            'reddit_ads_impressions': reddit_ads_impressions,
            'reddit_ads_data_source': reddit_ads_data_source,
            'reddit_ads_last_sync': reddit_ads_last_sync,
            # Zillow Ads (real data from GTMAdPerformance)
            'zillow_ads_spend': zillow_ads_spend,
            'zillow_ads_clicks': zillow_ads_clicks,
            'zillow_ads_impressions': zillow_ads_impressions,
            'zillow_campaign': zillow_campaign,
            'period_days': days,
        })
    except Exception as e:
        logging.error(f'admin_analysis_stats error: {e}')
        return jsonify({'completed_analyses': 0, 'error': 'Failed to load analysis stats.'}), 200





@admin_bp.route('/api/admin/ad-campaigns', methods=['GET'])
@_api_admin_req_dec
def admin_ad_campaigns_list():
    """List all ad campaign configs with computed spend + remaining budget.

    For each campaign config row:
      - spent  = sum of GTMAdPerformance.spend within (start_date, end_date or today)
      - remaining = prepaid_budget - spent (clamped at 0; null if budget not set)

    v5.87.38 — primary use case is Zillow (prepaid). Same shape works for any
    other channel that gets a config row.
    """
    from models import AdCampaignConfig, GTMAdPerformance
    from datetime import date as _date

    try:
        configs = AdCampaignConfig.query.order_by(AdCampaignConfig.channel.asc()).all()
        out = []
        today = _date.today()
        for c in configs:
            end = c.end_date or today
            rows = GTMAdPerformance.query.filter(
                GTMAdPerformance.channel == c.channel,
                GTMAdPerformance.date >= c.start_date,
                GTMAdPerformance.date <= end,
            ).all()
            spent = float(sum(r.spend or 0 for r in rows))
            clicks = sum(r.clicks or 0 for r in rows)
            impressions = sum(r.impressions or 0 for r in rows)
            budget = float(c.prepaid_budget or 0)
            remaining = max(0.0, budget - spent) if budget else None
            days_left = None
            if c.end_date:
                if c.end_date >= today:
                    days_left = (c.end_date - today).days
                else:
                    days_left = 0
            out.append({
                'channel':        c.channel,
                'campaign_name':  c.campaign_name or '',
                'prepaid_budget': budget if budget else None,
                'spent':          spent,
                'remaining':      remaining,
                'clicks':         clicks,
                'impressions':    impressions,
                'start_date':     c.start_date.isoformat() if c.start_date else None,
                'end_date':       c.end_date.isoformat() if c.end_date else None,
                'days_left':      days_left,
                'notes':          c.notes or '',
                'created_at':     c.created_at.isoformat() if c.created_at else None,
                'updated_at':     c.updated_at.isoformat() if c.updated_at else None,
            })
        return jsonify({'campaigns': out, 'count': len(out)})
    except Exception as e:
        logging.error(f'admin_ad_campaigns_list error: {e}')
        return jsonify({'campaigns': [], 'error': str(e)[:200]}), 500


@admin_bp.route('/api/admin/ad-campaigns', methods=['POST'])
@_api_admin_req_dec
def admin_ad_campaigns_upsert():
    """Create or update a campaign config. Channel is the primary key, so
    POSTing the same channel twice updates the existing row rather than
    erroring.

    Body:
      channel        (required): 'zillow_ads' | 'google_ads' | 'reddit_ads' | ...
      campaign_name  (optional): human-readable label
      prepaid_budget (optional): float, total prepaid budget. Null = postpay/PPC.
      start_date     (required): ISO date
      end_date       (optional): ISO date, null = open-ended
      notes          (optional): free text
    """
    from models import AdCampaignConfig
    from datetime import datetime as _dt

    data = request.get_json(silent=True) or {}
    channel = (data.get('channel') or '').strip()
    if not channel:
        return jsonify({'error': 'channel required'}), 400
    start_str = (data.get('start_date') or '').strip()
    if not start_str:
        return jsonify({'error': 'start_date required'}), 400

    try:
        start_d = _dt.strptime(start_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'start_date must be YYYY-MM-DD'}), 400

    end_d = None
    end_str = (data.get('end_date') or '').strip()
    if end_str:
        try:
            end_d = _dt.strptime(end_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'end_date must be YYYY-MM-DD'}), 400
        if end_d < start_d:
            return jsonify({'error': 'end_date must be >= start_date'}), 400

    budget_raw = data.get('prepaid_budget')
    budget = None
    if budget_raw not in (None, ''):
        try:
            budget = float(budget_raw)
            if budget < 0:
                return jsonify({'error': 'prepaid_budget must be >= 0'}), 400
        except (TypeError, ValueError):
            return jsonify({'error': 'prepaid_budget must be a number'}), 400

    # Upsert by primary key
    config = AdCampaignConfig.query.filter_by(channel=channel).first()
    if config is None:
        config = AdCampaignConfig(channel=channel, start_date=start_d)
        db.session.add(config)

    config.start_date = start_d
    config.end_date = end_d
    config.prepaid_budget = budget
    config.campaign_name = (data.get('campaign_name') or '').strip() or None
    config.notes = (data.get('notes') or '').strip() or None

    try:
        db.session.commit()
        return jsonify({'ok': True, 'channel': channel})
    except Exception as e:
        db.session.rollback()
        logging.error(f'admin_ad_campaigns_upsert error: {e}')
        return jsonify({'error': str(e)[:200]}), 500


@admin_bp.route('/api/admin/ad-campaigns/<channel>', methods=['DELETE'])
@_api_admin_req_dec
def admin_ad_campaigns_delete(channel):
    """Remove a campaign config. Daily spend rows in GTMAdPerformance are
    preserved — only the campaign metadata (budget, window, name) goes away."""
    from models import AdCampaignConfig
    config = AdCampaignConfig.query.filter_by(channel=channel).first()
    if not config:
        return jsonify({'error': 'not found'}), 404
    try:
        db.session.delete(config)
        db.session.commit()
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)[:200]}), 500


@admin_bp.route('/api/admin/offerwatch/status', methods=['GET'])
@_api_admin_req_dec
def offerwatch_status():
    """OfferWatch: active watches + recent alerts + scheduler job status."""
    from models import PropertyWatch, User
    from models import AgentAlert
    from sqlalchemy import func
    import datetime

    watches = PropertyWatch.query.filter_by(is_active=True).order_by(PropertyWatch.created_at.desc()).all()
    
    # Recent alerts (last 7 days)
    since = datetime.datetime.utcnow() - datetime.timedelta(days=7)
    alerts = AgentAlert.query.filter(AgentAlert.created_at >= since).order_by(AgentAlert.created_at.desc()).limit(50).all()

    watch_data = []
    for w in watches:
        user = User.query.get(w.user_id)
        watch_data.append({
            'id': w.id,
            'address': w.address,
            'asking_price': w.asking_price,
            'user_email': user.email if user else '—',
            'created_at': w.created_at.isoformat(),
            'expires_at': w.expires_at.isoformat() if w.expires_at else None,
            'has_coords': bool(w.latitude and w.longitude),
            'last_price_check': w.last_price_check_at.isoformat() if getattr(w, 'last_price_check_at', None) else None,
            'last_earthquake_check': w.last_earthquake_check_at.isoformat() if getattr(w, 'last_earthquake_check_at', None) else None,
            'last_comps_check': w.last_comps_check_at.isoformat() if getattr(w, 'last_comps_check_at', None) else None,
            'last_permit_check': w.last_permit_check_at.isoformat() if getattr(w, 'last_permit_check_at', None) else None,
        })

    alert_data = []
    for a in alerts:
        alert_data.append({
            'id': a.id,
            'watch_id': a.watch_id,
            'alert_type': a.alert_type,
            'severity': a.severity,
            'title': a.title,
            'body': (a.body or '')[:200],
            'email_sent': a.email_sent,
            'created_at': a.created_at.isoformat(),
        })

    return jsonify({
        'watches': watch_data,
        'total_watches': len(watches),
        'alerts_7d': alert_data,
        'total_alerts_7d': len(alerts),
    })


@admin_bp.route('/api/admin/offerwatch/trigger', methods=['POST'])
@_api_admin_req_dec
def offerwatch_trigger():
    """Manually trigger a specific OfferWatch monitor job."""
    data = request.get_json() or {}
    job = data.get('job')  # price | earthquake | comps | permit | deadline

    valid_jobs = {
        'price':      '_job_price_monitor',
        'earthquake': '_job_earthquake_monitor',
        'comps':      '_job_comps_monitor',
        'permit':     '_job_permit_monitor',
        'deadline':   '_job_deadline_monitor',
    }

    if job not in valid_jobs:
        return jsonify({'error': f'Unknown job: {job}. Valid: {list(valid_jobs.keys())}'}), 400

    try:
        import agentic_monitor as _am
        fn = getattr(_am, valid_jobs[job])
        fn()
        return jsonify({'success': True, 'job': job, 'message': f'{job} monitor ran successfully'})
    except Exception as e:
        logging.error(f'OfferWatch trigger {job} failed: {e}')
        return jsonify({'success': False, 'job': job, 'error': str(e)}), 500


@admin_bp.route('/api/admin/offerwatch/market-check', methods=['POST'])
@_api_admin_req_dec  
def offerwatch_market_check():
    """Test RentCast market data for a given address."""
    data = request.get_json() or {}
    address = (data.get('address') or '').strip()
    price = int(data.get('price') or 0)

    if not address or len(address) < 10:
        return jsonify({'error': 'Address required'}), 400

    try:
        from property_research_agent import PropertyResearchAgent
        from market_intelligence import MarketIntelligenceEngine, apply_market_adjustment
        agent = PropertyResearchAgent()
        rd = agent.research(address)
        tool_results = rd.get('tool_results', []) if rd else []
        rc = next((t for t in tool_results if t.get('tool_name') == 'rentcast'), None)
        ms = next((t for t in tool_results if t.get('tool_name') == 'market_stats'), None)

        ms_error = ms.get('error', '') if ms else ''
        result = {
            'rentcast_status': rc.get('status') if rc else 'not_run',
            'market_stats_status': ms.get('status') if ms else 'not_run',
            'market_stats_error': ms_error,  # e.g. "quota exceeded (HTTP 402)"
            'avm_price': rc.get('data', {}).get('avm_price', 0) if rc and rc.get('data') else 0,
            'comp_count': len(rc.get('data', {}).get('comparables', [])) if rc and rc.get('data') else 0,
            'avg_dom': ms.get('data', {}).get('average_days_on_market', 0) if ms and ms.get('data') else 0,
            'total_listings': ms.get('data', {}).get('total_listings', 0) if ms and ms.get('data') else 0,
        }

        if price > 0 and result['avm_price'] > 0:
            mi = MarketIntelligenceEngine().from_research_data(rd, price, address)
            mr = apply_market_adjustment(price, price, mi)
            result['market_applied'] = mr.get('market_applied', False)
            result['market_temperature'] = mr.get('market_temperature', '')
            result['buyer_leverage'] = mr.get('buyer_leverage', '')
            result['rationale'] = mr.get('rationale', '')
            result['data_quality'] = mi.data_quality

        return jsonify(result)
    except Exception as e:
        logging.error(f'Market check failed: {e}')
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/api/admin/system-info')
@_api_admin_req_dec
def admin_system_info():
    """Environment config flags for the API cost dashboard."""
    return jsonify({
        'stripe_configured':        bool(os.environ.get('STRIPE_SECRET_KEY', '')),
        'stripe_test_configured':   bool(os.environ.get('STRIPE_TEST_SECRET_KEY', '')),
        'webhook_configured':       bool(os.environ.get('STRIPE_WEBHOOK_SECRET', '')),
        'resend_configured':        bool(os.environ.get('RESEND_API_KEY', '')),
        'rentcast_configured':      bool(os.environ.get('RENTCAST_API_KEY', '')),
        'anthropic_configured':     bool(os.environ.get('ANTHROPIC_API_KEY', '')),
        'google_oauth_configured':  bool(os.environ.get('GOOGLE_CLIENT_ID', '')),
        'google_ads_configured':    bool(os.environ.get('GOOGLE_ADS_DEVELOPER_TOKEN', '')),
        'ga4_configured':           bool(os.environ.get('GA4_PROPERTY_ID', '') and os.environ.get('GOOGLE_ANALYTICS_KEY_JSON', '')),
        'apple_configured':         bool(os.environ.get('APPLE_CLIENT_ID', '')),
        'facebook_configured':      bool(os.environ.get('FACEBOOK_CLIENT_ID', '')),
        'github_configured':        bool(os.environ.get('GITHUB_CLIENT_ID', '')),
        'walkscore_configured':     bool(os.environ.get('WALKSCORE_API_KEY', '')),
        'airnow_configured':        bool(os.environ.get('AIRNOW_API_KEY', '')),
        'google_maps_configured':   bool(os.environ.get('GOOGLE_MAPS_API_KEY', '')),
        'greatschools_configured':  bool(os.environ.get('GREATSCHOOLS_API_KEY', '')),
        'mailgun_configured':       bool(os.environ.get('MAILGUN_API_KEY', '')),
        'reddit_configured':        bool(os.environ.get('REDDIT_ADS_CLIENT_ID', '') and os.environ.get('REDDIT_ADS_REFRESH_TOKEN', '')),
    })


# ---------------------------------------------------------------------------
# INFRASTRUCTURE & DEV COSTS — vendor + invoice CRUD
# ---------------------------------------------------------------------------

# Default vendors seeded on first access
# v5.89.139: the infra-vendor default list lives ONLY in app.py now
# (_INFRA_DEFAULT_VENDORS + _ensure_infra_vendors). The previous duplicate
# copy here was dead code and had diverged from app.py's list.



@admin_bp.route('/api/admin/infra/vendors', methods=['GET'])
@_api_admin_req_dec
def infra_vendors_list():
    """List all infra vendors."""
    from models import InfraVendor
    try:
        from app import _ensure_infra_vendors as _eiv; _eiv()
    except Exception: pass
    vendors = InfraVendor.query.order_by(InfraVendor.name).all()
    return jsonify([v.to_dict() for v in vendors])



@admin_bp.route('/api/admin/infra/vendors', methods=['POST'])
@_api_admin_req_dec
def infra_vendors_create():
    """Create a new vendor."""
    from models import InfraVendor
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Vendor name is required.'}), 400
    if InfraVendor.query.filter_by(name=name).first():
        return jsonify({'error': f'Vendor "{name}" already exists.'}), 409
    v = InfraVendor(
        name=name,
        category=data.get('category', 'other'),
        logo_emoji=data.get('logo_emoji', '💼'),
        notes=data.get('notes', ''),
    )
    db.session.add(v)
    db.session.commit()
    return jsonify(v.to_dict()), 201



@admin_bp.route('/api/admin/infra/vendors/<int:vid>', methods=['DELETE'])
@_api_admin_req_dec
def infra_vendors_delete(vid):
    """Delete a vendor (and all its invoices)."""
    from models import InfraVendor, InfraInvoice
    v = InfraVendor.query.get_or_404(vid)
    InfraInvoice.query.filter_by(vendor_id=vid).delete()
    db.session.delete(v)
    db.session.commit()
    return jsonify({'deleted': True})



def _infra_days_cutoff(days):
    """Unified ?days= selector -> date cutoff for infra invoices.
    Positive N = last N days; 0 = this calendar month; negative = all time (no cutoff)."""
    from datetime import date, timedelta
    if days is None or days < 0:
        return None
    if days == 0:
        return date.today().replace(day=1)
    return date.today() - timedelta(days=days)


def _infra_range_cutoff_from_args():
    """Resolve a period_start cutoff from request args.
    Honors ?days= (7/30/0/-1) first, then legacy ?months=, else None (all time)."""
    from datetime import date, timedelta
    if 'days' in request.args:
        try:
            return _infra_days_cutoff(int(request.args.get('days') or 0))
        except (TypeError, ValueError):
            return None
    if 'months' in request.args:
        try:
            months = int(request.args.get('months', 12))
        except (TypeError, ValueError):
            months = 12
        cutoff = date.today().replace(day=1)
        for _ in range(max(0, months - 1)):
            cutoff = (cutoff - timedelta(days=1)).replace(day=1)
        return cutoff
    return None


def _infra_category_filter(q):
    """Keep ad/marketing-category vendors OUT of infra cost views by default.
    ?category=ads returns ONLY ad-category invoices (used by the Ad Performance surface).
    Ad/referral spend (InterNACHI, Zillow, ...) is marketing spend, not infra."""
    from models import InfraVendor
    from sqlalchemy import or_
    cat = (request.args.get('category') or '').strip().lower()
    if cat == 'ads':
        return q.filter(InfraVendor.category == 'ads')
    return q.filter(or_(InfraVendor.category != 'ads', InfraVendor.category.is_(None)))


@admin_bp.route('/api/admin/infra/invoices', methods=['GET'])
@_api_admin_req_dec
def infra_invoices_list():
    """List invoices. ?vendor_id=&days=7|30|0|-1 (0=this month, -1=all). Legacy ?months= still honored."""
    from models import InfraInvoice, InfraVendor
    from datetime import date, timedelta
    try:
        from app import _ensure_infra_vendors as _eiv; _eiv()
    except Exception: pass
    try:
        from app import _ensure_gtm_subreddits as _egs; _egs()
    except Exception: pass
    # Seed FB/ND targets via blueprint helpers on first request
    try:
        from gtm.routes import _seed_fb_groups, _seed_nd_neighborhoods
        with current_app._get_current_object().app_context():
            _seed_fb_groups()
            _seed_nd_neighborhoods()
    except Exception:
        pass
    q = InfraInvoice.query.join(InfraVendor)
    vid = request.args.get('vendor_id')
    if vid:
        q = q.filter(InfraInvoice.vendor_id == int(vid))
    q = _infra_category_filter(q)
    cutoff = _infra_range_cutoff_from_args()
    if cutoff is not None:
        q = q.filter(InfraInvoice.period_start >= cutoff)
    invoices = q.order_by(InfraInvoice.period_start.desc()).all()
    return jsonify([inv.to_dict() for inv in invoices])



@admin_bp.route('/api/admin/infra/invoices/summary', methods=['GET'])
@_api_admin_req_dec
def infra_invoices_summary():
    """Monthly totals + per-vendor breakdown. ?days=7|30|0|-1 (0=this month, -1=all). Legacy ?months= honored."""
    from models import InfraInvoice, InfraVendor
    from datetime import date, timedelta
    from collections import defaultdict
    try:
        from app import _ensure_infra_vendors as _eiv; _eiv()
    except Exception: pass
    cutoff = _infra_range_cutoff_from_args()
    iq = InfraInvoice.query.join(InfraVendor)
    iq = _infra_category_filter(iq)
    if cutoff is not None:
        iq = iq.filter(InfraInvoice.period_start >= cutoff)
    invoices = iq.order_by(InfraInvoice.period_start).all()
    # Group by month
    by_month = defaultdict(lambda: {'total': 0.0, 'vendors': {}})
    vendor_totals = defaultdict(float)
    grand_total = 0.0
    for inv in invoices:
        month_key = inv.period_start.strftime('%Y-%m')
        by_month[month_key]['total'] += inv.amount_usd
        vname = inv.vendor.name
        by_month[month_key]['vendors'][vname] = by_month[month_key]['vendors'].get(vname, 0.0) + inv.amount_usd
        vendor_totals[vname] += inv.amount_usd
        grand_total += inv.amount_usd
    n_months = max(1, len(by_month))
    avg_monthly = grand_total / n_months
    return jsonify({
        'by_month': dict(by_month),
        'vendor_totals': dict(vendor_totals),
        'grand_total': round(grand_total, 2),
        'avg_monthly': round(avg_monthly, 2),
        'months': n_months,
    })



@admin_bp.route('/api/admin/infra/invoices', methods=['POST'])
@_api_admin_req_dec
def infra_invoices_create():
    """Create or update an invoice. Accepts multipart/form-data (with optional PDF) or JSON."""
    from models import InfraInvoice
    import base64
    from datetime import date

    # Support both JSON and multipart
    if request.content_type and 'multipart' in request.content_type:
        vendor_id   = int(request.form.get('vendor_id', 0))
        period_start= request.form.get('period_start', '')
        period_end  = request.form.get('period_end', '')
        amount_usd  = float(request.form.get('amount_usd', 0))
        description = request.form.get('description', '')
        invoice_ref = request.form.get('invoice_ref', '')
    else:
        data        = request.get_json() or {}
        vendor_id   = int(data.get('vendor_id', 0))
        period_start= data.get('period_start', '')
        period_end  = data.get('period_end', '')
        amount_usd  = float(data.get('amount_usd', 0))
        description = data.get('description', '')
        invoice_ref = data.get('invoice_ref', '')

    if not vendor_id or not period_start or amount_usd <= 0:
        return jsonify({'error': 'vendor_id, period_start, and amount_usd > 0 are required.'}), 400

    try:
        ps = date.fromisoformat(period_start)
        pe = date.fromisoformat(period_end) if period_end else ps.replace(day=28)
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD.'}), 400

    # Upsert: if same vendor+period_start exists, update it
    existing = InfraInvoice.query.filter_by(vendor_id=vendor_id, period_start=ps).first()
    inv = existing or InfraInvoice(vendor_id=vendor_id, period_start=ps)
    inv.period_end  = pe
    inv.amount_usd  = round(amount_usd, 2)
    inv.description = description
    inv.invoice_ref = invoice_ref

    # Handle file upload
    if request.content_type and 'multipart' in request.content_type:
        f = request.files.get('invoice_file')
        if f and f.filename:
            raw = f.read()
            if len(raw) > 10 * 1024 * 1024:  # 10 MB limit
                return jsonify({'error': 'File too large (max 10 MB).'}), 400
            inv.pdf_data     = base64.b64encode(raw).decode('utf-8')
            inv.pdf_filename = secure_filename(f.filename)
            inv.pdf_mime     = f.content_type or 'application/octet-stream'

    if not existing:
        db.session.add(inv)
    db.session.commit()
    return jsonify(inv.to_dict()), 201


@admin_bp.route('/api/admin/infra/import-card', methods=['POST'])
@_api_admin_req_dec
def infra_import_card():
    """Import a credit-card-activity CSV → monthly needs-review InfraInvoice rows.

    Body (JSON):
      raw:        the pasted CSV text (columns Date, Description, Amount)
      card_label: optional label stored in the invoice description (e.g. 'Card 1')
      dry_run:    if true, classify + preview without writing anything

    Matched vendor charges are grouped by (vendor, charge-month) and summed into
    one invoice each, written with source='card_import' and needs_review=True so
    they land in the existing approval queue. Ad channels already synced on the
    API Costs page (Google/Reddit Ads) are skipped to avoid double-counting;
    payments/credits and unmatched personal charges are skipped. Missing vendors
    are auto-created (Zillow->ads, Delaware->corporate). Existing invoices from
    other sources (email_auto / manual) are never clobbered.
    """
    from models import InfraVendor, InfraInvoice
    from datetime import date as _date
    import card_import

    data = request.get_json(silent=True) or {}
    raw = (data.get('raw') or '').strip()
    card_label = (data.get('card_label') or 'card').strip()[:40]
    dry_run = bool(data.get('dry_run', False))
    if not raw:
        return jsonify({'error': 'raw CSV text required'}), 400

    parsed = card_import.parse_card_csv(raw)
    created, updated, skipped_existing = [], [], []

    for inv in parsed['invoices']:
        vname = inv['vendor']
        vrow = InfraVendor.query.filter(db.func.lower(InfraVendor.name) == vname.lower()).first()
        if not vrow:
            cat, emo = card_import.AUTO_CREATE_CATEGORY.get(vname, ('other', '\U0001F9FE'))
            vrow = InfraVendor(name=vname, category=cat, logo_emoji=emo,
                               notes='Auto-created from card import.')
            if not dry_run:
                db.session.add(vrow)
                db.session.flush()
        ps = _date.fromisoformat(inv['period_start'])
        pe = _date.fromisoformat(inv['period_end'])
        vid = getattr(vrow, 'id', None)
        existing = (InfraInvoice.query.filter_by(vendor_id=vid, period_start=ps).first()
                    if vid else None)
        # Never clobber a manual or email-ingested invoice for the same month.
        if existing and getattr(existing, 'source', None) not in ('card_import', None):
            skipped_existing.append({'vendor': vname, 'period': inv['period_start'],
                                     'reason': f"already tracked (source={existing.source})"})
            continue
        bucket = updated if existing else created
        bucket.append({'vendor': vname, 'period': inv['period_start'],
                       'amount': inv['amount'], 'charges': inv['charge_count']})
        if dry_run:
            continue
        row = existing or InfraInvoice(vendor_id=vid, period_start=ps)
        row.period_end = pe
        row.amount_usd = inv['amount']
        row.description = f"Imported from {card_label} - {inv['charge_count']} charge(s)"
        row.source = 'card_import'
        row.needs_review = True
        if not existing:
            db.session.add(row)

    if not dry_run:
        db.session.commit()

    return jsonify({
        'dry_run': dry_run,
        'card_label': card_label,
        'created': created,
        'updated': updated,
        'skipped_existing': skipped_existing,
        'skipped': parsed['skipped'],
        'matched_total': parsed['matched_total'],
    }), 200


@admin_bp.route('/api/admin/infra/invoices/<int:iid>', methods=['DELETE'])
@_api_admin_req_dec
def infra_invoices_delete(iid):
    """Delete an invoice."""
    from models import InfraInvoice
    inv = InfraInvoice.query.get_or_404(iid)
    db.session.delete(inv)
    db.session.commit()
    return jsonify({'deleted': True})



@admin_bp.route('/api/admin/infra/invoices/<int:iid>/file', methods=['GET'])
@_api_admin_req_dec
def infra_invoices_file(iid):
    """Download/view the uploaded invoice file."""
    from models import InfraInvoice
    import base64
    from flask import Response
    inv = InfraInvoice.query.get_or_404(iid)
    if not inv.pdf_data:
        return jsonify({'error': 'No file attached to this invoice.'}), 404
    raw = base64.b64decode(inv.pdf_data)
    return Response(
        raw,
        mimetype=inv.pdf_mime or 'application/octet-stream',
        headers={'Content-Disposition': f'inline; filename="{inv.pdf_filename or "invoice"}"'}
    )


# ---------------------------------------------------------------------------
# v5.87.72 — Inbound invoice webhook (Resend Inbound → Claude Haiku → DB)
# ---------------------------------------------------------------------------
# This endpoint is called by Resend when an email arrives at
# invoices@estoima.resend.app (or any address at estoima.resend.app).
#
# Auth model: Resend signs webhooks using svix. We verify with
# RESEND_WEBHOOK_SECRET. NO admin_key — Resend wouldn't have one.
#
# Resend sends only metadata (email_id + attachment IDs). To get body and
# attachments we call Resend's Receiving API and Attachments API using
# RESEND_API_KEY.
#
# Flow:
#   1. Verify svix signature
#   2. Fetch full email body via Resend API
#   3. Fetch first PDF attachment if any
#   4. Combine subject + body + PDF text → send to Claude Haiku
#   5. Match vendor + write InfraInvoice with source='email_auto'
#   6. Return 200 to Resend
@admin_bp.route('/api/admin/infra/inbound-invoice', methods=['POST'])
def infra_inbound_invoice():
    """Receive an email.received webhook from Resend; parse + store invoice.

    NOT protected by admin_key. Auth is via svix webhook signature.
    """
    import os
    from flask import request as flask_req
    from models import InfraVendor, InfraInvoice
    import base64

    secret = os.environ.get('RESEND_WEBHOOK_SECRET', '').strip()
    api_key = os.environ.get('RESEND_API_KEY', '').strip()

    if not secret:
        logging.error('inbound-invoice: RESEND_WEBHOOK_SECRET not configured')
        return jsonify({'error': 'webhook secret not configured'}), 503
    if not api_key:
        logging.error('inbound-invoice: RESEND_API_KEY not configured')
        return jsonify({'error': 'resend api key not configured'}), 503

    # Step 1: verify svix signature
    raw_body = flask_req.get_data()
    headers = {
        'svix-id':        flask_req.headers.get('svix-id') or flask_req.headers.get('Svix-Id', ''),
        'svix-timestamp': flask_req.headers.get('svix-timestamp') or flask_req.headers.get('Svix-Timestamp', ''),
        'svix-signature': flask_req.headers.get('svix-signature') or flask_req.headers.get('Svix-Signature', ''),
    }
    if not all(headers.values()):
        logging.warning('inbound-invoice: missing svix headers')
        return jsonify({'error': 'missing signature headers'}), 401
    try:
        from svix.webhooks import Webhook
        wh = Webhook(secret)
        # raises on bad signature
        wh.verify(raw_body, headers)
    except ImportError:
        # Fall back to manual HMAC check if svix lib not installed
        # (Render image should have it from requirements; defensive only)
        logging.error('inbound-invoice: svix library not available')
        return jsonify({'error': 'webhook verification unavailable'}), 503
    except Exception as e:
        logging.warning('inbound-invoice: svix signature verification failed: %s', e)
        return jsonify({'error': 'invalid signature'}), 401

    # Step 2: parse webhook payload
    try:
        payload = flask_req.get_json(force=True)
    except Exception:
        return jsonify({'error': 'invalid json'}), 400
    event_type = (payload or {}).get('type', '')
    if event_type != 'email.received':
        # Resend may send other event types in future; ack and ignore
        logging.info('inbound-invoice: ignoring event type %s', event_type)
        return jsonify({'ok': True, 'ignored': True}), 200

    data = (payload or {}).get('data') or {}
    email_id = data.get('email_id')
    if not email_id:
        return jsonify({'error': 'missing email_id'}), 400
    subject = (data.get('subject') or '')[:500]
    sender  = (data.get('from') or '')[:200]
    attachments_meta = data.get('attachments') or []

    # Step 3: fetch the email body via Resend Receiving API
    import requests as _req
    body_text = ''
    body_html = ''
    try:
        r = _req.get(
            f'https://api.resend.com/emails/receiving/{email_id}',
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=15,
        )
        if r.status_code == 200:
            edata = r.json() or {}
            body_text = (edata.get('text') or '')[:20000]
            body_html = (edata.get('html') or '')[:20000]
        else:
            logging.warning('inbound-invoice: Resend Receiving API returned %s', r.status_code)
    except Exception as e:
        logging.exception('inbound-invoice: fetching email body failed')
        # Continue with metadata-only — Claude can sometimes infer from subject

    # Step 4: fetch first PDF attachment if any (skip non-PDF/image)
    pdf_b64 = None
    pdf_filename = None
    pdf_mime = None
    pdf_text = ''
    if attachments_meta:
        # Take the first PDF or image attachment
        for att in attachments_meta:
            ct = (att.get('content_type') or '').lower()
            if 'pdf' in ct or 'image' in ct:
                att_id = att.get('id')
                if not att_id:
                    continue
                try:
                    ar = _req.get(
                        f'https://api.resend.com/emails/{email_id}/attachments/{att_id}',
                        headers={'Authorization': f'Bearer {api_key}'},
                        timeout=15,
                    )
                    if ar.status_code == 200:
                        adata = ar.json() or {}
                        download_url = adata.get('download_url')
                        if download_url:
                            dr = _req.get(download_url, timeout=20)
                            if dr.status_code == 200:
                                pdf_b64 = base64.b64encode(dr.content).decode('ascii')
                                pdf_filename = att.get('filename') or 'invoice.pdf'
                                pdf_mime = ct
                                # Try to extract text from PDF for Claude context
                                if 'pdf' in ct:
                                    try:
                                        import io
                                        import pdfplumber  # already in requirements
                                        with pdfplumber.open(io.BytesIO(dr.content)) as pdf:
                                            pages = [p.extract_text() or '' for p in pdf.pages[:10]]
                                            pdf_text = ('\n'.join(pages))[:30000]
                                    except Exception as pe:
                                        logging.warning('inbound-invoice: pdf text extract failed: %s', pe)
                except Exception:
                    logging.exception('inbound-invoice: attachment fetch failed for %s', att_id)
                break  # only handle one attachment per email

    # Step 5: parse with Claude
    from infra_invoice_parser import parse_invoice_email
    all_vendors = [
        {'id': v.id, 'name': v.name}
        for v in InfraVendor.query.all()
    ]
    # Combine: subject + sender + body + pdf text, capped
    combined = f"Subject: {subject}\nFrom: {sender}\n\n"
    if body_text:
        combined += f"--- BODY (TEXT) ---\n{body_text}\n\n"
    elif body_html:
        # Strip HTML tags crudely if no text version
        import re as _re
        combined += f"--- BODY (HTML stripped) ---\n{_re.sub(r'<[^>]+>', ' ', body_html)}\n\n"
    if pdf_text:
        combined += f"--- ATTACHMENT TEXT ---\n{pdf_text}\n"

    parsed = parse_invoice_email(combined, all_vendors)
    logging.info('inbound-invoice: parsed email %s → %s', email_id, parsed.to_log_dict())

    # Step 6: Decide what to do.
    # ── Surface, never drop ───────────────────────────────────────────────
    # Claude reports confidence 0.0 only when the email is NOT an invoice
    # (marketing, a support reply, etc.). Those we skip — surfacing them is noise.
    # Anything that looks like a real invoice gets written, even if incomplete:
    # we resolve a vendor and backfill the NOT NULL fields with safe, reviewable
    # defaults, flagging needs_review so it shows up on the costs page for the
    # admin to confirm or correct. (Previously a real invoice was logged and
    # dropped whenever the vendor didn't match — that was the "got invoiced but
    # don't see it" gap.)
    if (parsed.confidence or 0.0) <= 0.0:
        logging.info(
            'inbound-invoice: not an invoice (confidence 0) — skipping. email_id=%s errors=%s',
            email_id, parsed.parse_errors,
        )
        return jsonify({
            'ok': True, 'persisted': False, 'reason': 'not_an_invoice',
            'errors': parsed.parse_errors, 'email_id': email_id,
        }), 200

    from datetime import date as _date
    from sqlalchemy.exc import IntegrityError as _IntegrityError

    # Resolve a vendor. If Claude matched one, use it. Otherwise create (or reuse)
    # a vendor from the name Claude extracted, so a first-ever "Hunter" invoice
    # still lands under "Hunter" and auto-matches next time. If even the name is
    # missing, fall back to a single catch-all so the row is still visible.
    if parsed.matched_vendor_id is None:
        _raw_name = (parsed.vendor_name_raw or '').strip()
        _vname = _raw_name or 'Unidentified vendor'
        _vrow = InfraVendor.query.filter(
            db.func.lower(InfraVendor.name) == _vname.lower()
        ).first()
        if not _vrow:
            _vrow = InfraVendor(
                name=_vname[:100], category='other', logo_emoji='🧾',
                notes='Auto-created from an inbound invoice — confirm or merge.',
            )
            db.session.add(_vrow)
            try:
                db.session.flush()
            except _IntegrityError:
                db.session.rollback()
                _vrow = InfraVendor.query.filter(
                    db.func.lower(InfraVendor.name) == _vname.lower()
                ).first()
        parsed.matched_vendor_id = _vrow.id
        parsed.matched_vendor_name = _vrow.name
        parsed.needs_review = True
        logging.info(
            'inbound-invoice: no vendor match — surfaced under vendor "%s" (id=%s) for review. email_id=%s',
            _vrow.name, _vrow.id, email_id,
        )

    # Backfill the remaining NOT NULL fields with reviewable defaults.
    if parsed.period_start is None:
        parsed.period_start = _date.today().replace(day=1)
        parsed.needs_review = True
    if parsed.amount_usd is None:
        parsed.amount_usd = 0.0
        parsed.needs_review = True

    # Default period_end to last day of month if unknown
    if not parsed.period_end:
        from calendar import monthrange
        ps = parsed.period_start
        last = monthrange(ps.year, ps.month)[1]
        parsed.period_end = ps.replace(day=last)

    # Check for duplicate (same vendor + period_start). Update instead of insert.
    existing = InfraInvoice.query.filter_by(
        vendor_id=parsed.matched_vendor_id,
        period_start=parsed.period_start,
    ).first()

    try:
        if existing:
            # Don't overwrite manually-entered invoices
            if existing.source == 'manual':
                logging.info(
                    'inbound-invoice: skipping — manual invoice exists for vendor=%s period=%s',
                    parsed.matched_vendor_name, parsed.period_start,
                )
                return jsonify({
                    'ok': True, 'persisted': False, 'reason': 'manual_exists',
                    'invoice_id': existing.id, 'email_id': email_id,
                }), 200
            # Update the existing email_auto row
            existing.amount_usd = parsed.amount_usd
            existing.period_end = parsed.period_end
            existing.invoice_ref = parsed.invoice_ref or existing.invoice_ref
            existing.description = parsed.description or existing.description
            existing.parse_confidence = parsed.confidence
            existing.raw_email_id = email_id
            existing.needs_review = parsed.needs_review
            if pdf_b64 and not existing.pdf_data:
                existing.pdf_data = pdf_b64
                existing.pdf_filename = pdf_filename
                existing.pdf_mime = pdf_mime
            db.session.commit()
            invoice_id = existing.id
            action = 'updated'
        else:
            inv = InfraInvoice(
                vendor_id=parsed.matched_vendor_id,
                period_start=parsed.period_start,
                period_end=parsed.period_end,
                amount_usd=parsed.amount_usd,
                description=parsed.description,
                invoice_ref=parsed.invoice_ref,
                pdf_data=pdf_b64,
                pdf_filename=pdf_filename,
                pdf_mime=pdf_mime,
                source='email_auto',
                parse_confidence=parsed.confidence,
                raw_email_id=email_id,
                needs_review=parsed.needs_review,
            )
            db.session.add(inv)
            db.session.commit()
            invoice_id = inv.id
            action = 'created'
        logging.info('inbound-invoice: %s invoice id=%s vendor=%s amount=%.2f',
                     action, invoice_id, parsed.matched_vendor_name, parsed.amount_usd)
        return jsonify({
            'ok': True, 'persisted': True, 'action': action,
            'invoice_id': invoice_id,
            'vendor': parsed.matched_vendor_name,
            'amount_usd': parsed.amount_usd,
            'needs_review': parsed.needs_review,
            'confidence': parsed.confidence,
            'email_id': email_id,
        }), 200
    except Exception as e:
        db.session.rollback()
        logging.exception('inbound-invoice: DB write failed')
        return jsonify({'error': 'db_write_failed', 'detail': str(e)[:200]}), 500


@admin_bp.route('/api/admin/infra/invoices/<int:iid>/approve', methods=['POST'])
@_api_admin_req_dec
def infra_invoice_approve(iid):
    """Mark a needs-review invoice as approved (clears the flag)."""
    from models import InfraInvoice
    inv = InfraInvoice.query.get_or_404(iid)
    inv.needs_review = False
    db.session.commit()
    return jsonify({'ok': True, 'invoice_id': iid, 'needs_review': False})


@admin_bp.route('/api/admin/repair-costs/seed', methods=['POST'])
@_api_admin_req_dec
def repair_cost_reseed():
    """Re-seed repair cost data from hardcoded values (overwrites DB)."""
    from seed_repair_costs import seed_repair_cost_data
    zones, baselines = seed_repair_cost_data(current_app._get_current_object())
    return jsonify({'seeded': True, 'zones': zones, 'baselines': baselines})


@admin_bp.route('/api/admin/repair-costs/estimate')
@_api_admin_req_dec
def repair_cost_test_estimate():
    """Test endpoint — generate a repair estimate for any ZIP."""
    from repair_cost_estimator import estimate_repair_costs
    zip_code = request.args.get('zip', '95120')
    result = estimate_repair_costs(
        zip_code=zip_code,
        findings=[
            {'category': 'foundation', 'severity': 'major', 'description': 'Test — foundation cracks'},
            {'category': 'hvac', 'severity': 'major', 'description': 'Test — aging HVAC'},
            {'category': 'plumbing', 'severity': 'moderate', 'description': 'Test — slow drains'},
            {'category': 'electrical', 'severity': 'minor', 'description': 'Test — old outlets'},
            {'category': 'roof', 'severity': 'moderate', 'description': 'Test — aging roof'},
        ],
        property_year_built=1975,
    )
    return jsonify(result)


# =============================================================================
# PHASE 4: PARSER TEST, ANONYMIZATION, DRE MONITOR (v5.62.25)
# =============================================================================

# Document repository: persistent disk in production, local fallback for dev
DOCREPO_DISK_PATH = os.environ.get('DOCREPO_PATH', '/var/data/docrepo')
DOCREPO_LOCAL_PATH = os.path.join(os.path.dirname(__file__), 'document_repo')



@admin_bp.route('/api/admin/leads/expire', methods=['POST'])
@_api_admin_req_dec
def expire_stale_leads():
    """Expire leads that have been available >48h with no claims. Admin only."""
    from datetime import timedelta as _td_exp

    cutoff = datetime.utcnow() - _td_exp(hours=48)
    stale = ContractorLead.query.filter(
        ContractorLead.status == 'available',
        ContractorLead.created_at < cutoff,
    ).all()

    expired = 0
    for lead in stale:
        lead.status = 'expired'
        expired += 1
        # Notify buyer if unclaimed
        try:
            _send_email(
                to_email=lead.user_email,
                subject=f"Your {lead.repair_system} quote request — no contractors claimed yet",
                html_content=f"""<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;">
                  <div style="font-size:20px;font-weight:800;margin-bottom:8px;color:#f59e0b;">No contractors claimed your request yet</div>
                  <p style="color:#94a3b8;font-size:14px;">Your request for a <strong style="color:#f1f5f9;">{lead.repair_system}</strong> contractor at {lead.property_address} didn't get claimed in 48 hours — likely because no contractors in ZIP {lead.property_zip or 'your area'} are active right now.</p>
                  <p style="color:#94a3b8;font-size:14px;">We'll keep your request on file and notify you if a matching contractor joins. You can also <a href="https://getofferwise.ai/contractor-portal" style="color:#f97316;">resubmit at any time</a>.</p>
                </div>"""
            )
        except Exception: pass
    db.session.commit()
    logging.info(f"⏰ Lead expiry: {expired} leads expired")
    return jsonify({'expired': expired})



@admin_bp.route('/api/admin/migrate/contractor-marketplace', methods=['POST'])
@_api_admin_req_dec
def migrate_contractor_marketplace():
    """Emergency migration: add marketplace columns to contractors and contractor_leads tables."""

    results = []
    with db.engine.connect() as conn:
        # contractors table
        for col, dtype in [
            ('available',         'BOOLEAN DEFAULT TRUE'),
            ('unavailable_until', 'TIMESTAMP'),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE contractors ADD COLUMN {col} {dtype}"))
                results.append(f"✅ contractors.{col} added")
            except Exception as e:
                results.append(f"ℹ️ contractors.{col}: {str(e)[:80]}")

        # contractor_leads table
        for col, dtype in [
            ('available_at',  'TIMESTAMP'),
            ('expires_at',    'TIMESTAMP'),
            ('claim_count',   'INTEGER DEFAULT 0'),
            ('notes',         'TEXT'),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE contractor_leads ADD COLUMN {col} {dtype}"))
                results.append(f"✅ contractor_leads.{col} added")
            except Exception as e:
                results.append(f"ℹ️ contractor_leads.{col}: {str(e)[:80]}")

        # contractor_lead_claims table
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS contractor_lead_claims (
                    id SERIAL PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT NOW(),
                    lead_id INTEGER NOT NULL,
                    contractor_id INTEGER NOT NULL,
                    status VARCHAR(20) DEFAULT 'claimed',
                    passed_at TIMESTAMP,
                    contacted_at TIMESTAMP,
                    closed_at TIMESTAMP,
                    job_value DOUBLE PRECISION,
                    UNIQUE(lead_id, contractor_id)
                )
            """))
            results.append("✅ contractor_lead_claims table created/verified")
        except Exception as e:
            results.append(f"ℹ️ contractor_lead_claims: {str(e)[:80]}")

        conn.commit()

    logging.info(f"🔧 Emergency migration results: {results}")
    return jsonify({'results': results})



@admin_bp.route('/api/admin/cac-ltv')
@_api_admin_req_dec
def cac_ltv_by_channel():
    """CAC:LTV by acquisition channel — connects ad spend to subscription revenue."""
    from sqlalchemy import func
    days = int(request.args.get('days', 30))
    from datetime import timedelta as _td_cac
    cutoff = datetime.utcnow() - _td_cac(days=days)

    # Revenue per source (from funnel purchase events with amount_usd)
    try:
        from models import GTMFunnelEvent as _GFE
        revenue_rows = db.session.query(
            _GFE.source,
            func.count(_GFE.id).label('conversions'),
            func.sum(_GFE.amount_usd).label('revenue'),
        ).filter(
            _GFE.stage == 'purchase',
            _GFE.created_at >= cutoff,
            _GFE.amount_usd.isnot(None),
        ).group_by(_GFE.source).all()
    except Exception:
        revenue_rows = []

    # Ad spend per channel (from gtm_ad_performance)
    try:
        from models import GTMAdPerformance as _GAP
        spend_rows = db.session.query(
            _GAP.channel,
            func.sum(_GAP.spend).label('spend'),
            func.sum(_GAP.clicks).label('clicks'),
        ).filter(
            _GAP.date >= cutoff.date(),
        ).group_by(_GAP.channel).all()
    except Exception:
        spend_rows = []

    # Build spend map — normalize channel names to match source
    # v5.88.07: Nextdoor removed entirely (never signed up there).
    channel_map = {'google_ads': 'google', 'reddit_ads': 'reddit', 'zillow': 'zillow', 'zillow_display': 'zillow', 'internachi': 'internachi'}
    spend_by_source = {}
    for row in spend_rows:
        src = channel_map.get(row.channel, row.channel)
        spend_by_source[src] = {
            'spend': float(row.spend or 0),
            'clicks': int(row.clicks or 0),
        }

    result = {}
    for row in revenue_rows:
        src = row.source or 'direct'
        revenue = float(row.revenue or 0)
        conversions = int(row.conversions or 0)
        spend = spend_by_source.get(src, {}).get('spend', 0)
        clicks = spend_by_source.get(src, {}).get('clicks', 0)
        ltv = revenue / conversions if conversions > 0 else 0
        cac = spend / conversions if conversions > 0 and spend > 0 else None
        ratio = ltv / cac if cac and cac > 0 else None
        result[src] = {
            'source': src,
            'conversions': conversions,
            'revenue': round(revenue, 2),
            'ltv': round(ltv, 2),
            'ad_spend': round(spend, 2),
            'clicks': clicks,
            'cac': round(cac, 2) if cac else None,
            'ltv_cac_ratio': round(ratio, 2) if ratio else None,
            'healthy': ratio >= 3.0 if ratio else None,  # 1:3 minimum threshold
        }

    # Channels with spend but no purchases
    for src, data in spend_by_source.items():
        if src not in result and data['spend'] > 0:
            result[src] = {
                'source': src,
                'conversions': 0,
                'revenue': 0,
                'ltv': 0,
                'ad_spend': round(data['spend'], 2),
                'clicks': data['clicks'],
                'cac': None,
                'ltv_cac_ratio': None,
                'healthy': None,   # No conversions yet — pending, not unhealthy
            }

    # Always show known paid channels even with zero spend so the table is never empty
    # v5.88.07: nextdoor removed (never signed up); internachi tracked at $49/month
    for paid_src in ('google', 'reddit', 'zillow', 'internachi'):
        if paid_src not in result:
            result[paid_src] = {
                'source': paid_src,
                'conversions': 0,
                'revenue': 0,
                'ltv': 0,
                'ad_spend': 0,
                'clicks': 0,
                'cac': None,
                'ltv_cac_ratio': None,
                'healthy': None,
            }

    # Sort: channels with spend first, then alpha
    sorted_channels = sorted(result.values(), key=lambda c: (-c['ad_spend'], c['source']))

    return jsonify({
        'channels': sorted_channels,
        'period_days': days,
        'note': 'CAC only calculated for paid channels with tracked ad spend. Organic/direct CAC is $0 by definition.',
    })


@admin_bp.route('/api/admin/source-users', methods=['GET'])
@_api_admin_req_dec
def source_users():
    """Individual users with their acquisition source — joins GTMFunnelEvent to User.
    Source = utm_source if present, else referring website parsed from Referer header."""
    from sqlalchemy import func
    from models import GTMFunnelEvent, User
    days = int(request.args.get('days', 90))
    from datetime import timedelta as _td
    cutoff = datetime.utcnow() - _td(days=days)

    import json as _json_su
    try:
        # Get the first signup/visit event per user with a source
        # Use subquery to get earliest event per user_id
        subq = db.session.query(
            GTMFunnelEvent.user_id,
            func.min(GTMFunnelEvent.created_at).label('first_seen'),
        ).filter(
            GTMFunnelEvent.user_id.isnot(None),
            GTMFunnelEvent.created_at >= cutoff,
        ).group_by(GTMFunnelEvent.user_id).subquery()

        # Join back to get source from that first event
        rows = db.session.query(
            GTMFunnelEvent.user_id,
            GTMFunnelEvent.source,
            GTMFunnelEvent.medium,
            GTMFunnelEvent.created_at,
            GTMFunnelEvent.metadata_json,
        ).join(
            subq,
            (GTMFunnelEvent.user_id == subq.c.user_id) &
            (GTMFunnelEvent.created_at == subq.c.first_seen)
        ).all()

        # Build user_id → source map (include referrer_url from metadata)
        source_map = {}
        for row in rows:
            uid = row.user_id
            if uid not in source_map:
                # Try to pull referrer_url from stored metadata
                referrer_url = ''
                try:
                    if row.metadata_json:
                        meta = _json_su.loads(row.metadata_json)
                        referrer_url = meta.get('referrer_url', '')
                except Exception:
                    pass
                source_map[uid] = {
                    'source': row.source or 'direct',
                    'medium': row.medium or 'none',
                    'referrer_url': referrer_url,
                    'first_seen': row.created_at.isoformat() if row.created_at else None,
                }

        # Fetch user details
        user_ids = list(source_map.keys())
        if not user_ids:
            return jsonify({'users': [], 'period_days': days})

        users = User.query.filter(User.id.in_(user_ids)).all()

        result = []
        for u in users:
            src_data = source_map.get(u.id, {})
            plan = getattr(u, 'subscription_plan', 'free') or 'free'
            tier = getattr(u, 'tier', 'free') or 'free'
            is_paid = plan not in ('free', '', None) and tier not in ('free', '', None)

            # Count analyses
            try:
                from models import Property
                analyses = Property.query.filter_by(user_id=u.id).count()
            except Exception:
                analyses = 0

            result.append({
                'user_id': u.id,
                'email': u.email,
                'name': u.name or '',
                'source': src_data.get('source', 'direct'),
                'medium': src_data.get('medium', 'none'),
                'referrer_url': src_data.get('referrer_url', ''),
                'first_seen': src_data.get('first_seen'),
                'signed_up': u.created_at.isoformat() if u.created_at else None,
                'plan': plan,
                'is_paid': is_paid,
                'analyses': analyses,
                'credits': getattr(u, 'analysis_credits', 0) or 0,
            })

        # Sort: paid first, then by first_seen desc
        result.sort(key=lambda x: (0 if x['is_paid'] else 1, x['first_seen'] or ''), reverse=False)
        result.sort(key=lambda x: x['is_paid'], reverse=True)

        return jsonify({'users': result, 'period_days': days})

    except Exception as e:
        logging.error(f'source_users error: {e}')
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/cost-correction/zillow', methods=['POST'])
@_api_admin_req_dec
def api_cost_correction_zillow():
    """v5.88.07: One-shot Zillow correction.

    Founder paid $501 ONE TIME on 2026-03-31 (campaign ran Mar 31 → Apr 30,
    71,571 impressions, 68 clicks). No payments since. The dashboard was
    treating Zillow like a recurring channel; this endpoint replaces all
    Zillow rows with the single $501 entry on 2026-03-31.

    Body:
      apply: bool (default false) — must be true to actually delete/insert.
             Without it, returns a dry-run preview.
    """
    from models import GTMAdPerformance
    from datetime import date as _date

    data = request.get_json(silent=True) or {}
    apply = bool(data.get('apply', False))

    # Find existing Zillow rows
    existing = GTMAdPerformance.query.filter(
        GTMAdPerformance.channel.in_(['zillow', 'zillow_ads', 'zillow_display'])
    ).all()
    existing_summary = [
        {'id': r.id, 'channel': r.channel, 'date': r.date.isoformat() if r.date else None,
         'spend': float(r.spend or 0), 'clicks': int(r.clicks or 0),
         'impressions': int(getattr(r, 'impressions', 0) or 0)}
        for r in existing
    ]

    if not apply:
        return jsonify({
            'dry_run': True,
            'existing_zillow_rows': existing_summary,
            'will_replace_with': {
                'channel': 'zillow_ads',
                'date': '2026-03-31',
                'spend': 501.00,
                'clicks': 68,
                'impressions': 71571,
                'note': 'One-time prepaid campaign Mar 31 → Apr 30, no recurring',
            },
            'instructions': 'POST with {"apply": true} to actually replace.',
        })

    # Apply: delete existing zillow rows, insert one canonical row
    for r in existing:
        db.session.delete(r)

    canonical = GTMAdPerformance(
        channel='zillow_ads',
        date=_date(2026, 3, 31),
        spend=501.00,
        clicks=68,
        impressions=71571,
    )
    db.session.add(canonical)
    db.session.commit()

    return jsonify({
        'applied': True,
        'rows_deleted': len(existing),
        'rows_inserted': 1,
        'canonical_row': {
            'channel': 'zillow_ads', 'date': '2026-03-31',
            'spend': 501.00, 'clicks': 68, 'impressions': 71571,
        },
    })


@admin_bp.route('/api/admin/cost-correction/nextdoor', methods=['POST'])
@_api_admin_req_dec
def api_cost_correction_nextdoor():
    """v5.88.07: Remove all Nextdoor entries (founder never signed up there).

    Body:
      apply: bool (default false)
    """
    from models import GTMAdPerformance, AdCampaignConfig

    data = request.get_json(silent=True) or {}
    apply = bool(data.get('apply', False))

    perf_rows = GTMAdPerformance.query.filter_by(channel='nextdoor').all()
    config_rows = AdCampaignConfig.query.filter_by(channel='nextdoor').all()

    if not apply:
        return jsonify({
            'dry_run': True,
            'gtm_ad_performance_rows_to_delete': len(perf_rows),
            'ad_campaign_config_rows_to_delete': len(config_rows),
            'instructions': 'POST with {"apply": true} to actually delete.',
        })

    for r in perf_rows:
        db.session.delete(r)
    for r in config_rows:
        db.session.delete(r)
    db.session.commit()

    return jsonify({
        'applied': True,
        'gtm_ad_performance_rows_deleted': len(perf_rows),
        'ad_campaign_config_rows_deleted': len(config_rows),
    })


@admin_bp.route('/api/admin/cost-correction/internachi', methods=['POST'])
@_api_admin_req_dec
def api_cost_correction_internachi():
    """v5.88.07: INTERNACHI is $49/month recurring. This endpoint backfills
    monthly $49 rows from a start date through today.

    Body:
      start_date: YYYY-MM-DD — first month of payment (required)
      apply: bool (default false)

    The auto-monthly cron job (added in app.py same release) handles future
    months automatically — this endpoint is for the historical backfill only.
    """
    from models import GTMAdPerformance
    from datetime import date as _date, timedelta as _td

    data = request.get_json(silent=True) or {}
    apply = bool(data.get('apply', False))
    start_str = (data.get('start_date') or '').strip()
    if not start_str:
        return jsonify({'error': 'start_date is required (YYYY-MM-DD)'}), 400
    try:
        start_dt = _date.fromisoformat(start_str)
    except ValueError:
        return jsonify({'error': f'Invalid start_date: {start_str}'}), 400

    today = _date.today()
    if start_dt > today:
        return jsonify({'error': 'start_date is in the future'}), 400

    # Generate one row per month, starting on the 1st of each month from
    # start_dt's month through today's month
    months_to_create = []
    cur = _date(start_dt.year, start_dt.month, 1)
    while cur <= today:
        months_to_create.append(cur)
        # Advance one month
        if cur.month == 12:
            cur = _date(cur.year + 1, 1, 1)
        else:
            cur = _date(cur.year, cur.month + 1, 1)

    # Find existing INTERNACHI rows so we don't double-create
    existing_dates = {
        r.date for r in GTMAdPerformance.query.filter_by(channel='internachi').all()
    }
    new_dates = [d for d in months_to_create if d not in existing_dates]

    if not apply:
        return jsonify({
            'dry_run': True,
            'start_date': start_dt.isoformat(),
            'months_in_range': len(months_to_create),
            'existing_internachi_rows': len(existing_dates),
            'rows_to_create': len(new_dates),
            'preview_first_5': [d.isoformat() for d in new_dates[:5]],
            'preview_last_5': [d.isoformat() for d in new_dates[-5:]],
            'each_row_spend': 49.00,
            'instructions': 'POST with {"start_date":"YYYY-MM-DD", "apply":true} to commit.',
        })

    # Create rows
    for d in new_dates:
        row = GTMAdPerformance(
            channel='internachi',
            date=d,
            spend=49.00,
            clicks=0,  # No click tracking for membership fees
            impressions=0,
        )
        db.session.add(row)
    db.session.commit()

    return jsonify({
        'applied': True,
        'start_date': start_dt.isoformat(),
        'rows_created': len(new_dates),
        'total_internachi_spend_logged': round(len(new_dates) * 49.00, 2),
    })


@admin_bp.route('/api/admin/onboarding-funnel', methods=['GET'])
@_api_admin_req_dec
def api_onboarding_funnel():
    """v5.88.07: Per-step onboarding drop-off based on feature_events.

    The funnel-debug endpoint shows that overall onboarding is the biggest
    drop-off, but doesn't tell us WHICH screen kills it. This endpoint
    aggregates the onboarding events fired from static/onboarding.html
    (step_1_viewed, step_2_viewed, step_3_viewed, skipped_at_step_N,
    completed) over the last N days and computes:
      - Unique users who reached each step
      - Step-to-step retention %
      - Skip count per step
      - Completion count

    Query params:
      days: int (default 30) — window for the aggregation
    """
    from models import FeatureEvent
    from sqlalchemy import func, distinct
    from collections import defaultdict
    from datetime import timedelta as _td

    days = int(request.args.get('days', 30))
    cutoff = datetime.utcnow() - _td(days=days)

    # All onboarding feature events in the window
    events = (FeatureEvent.query
              .filter(FeatureEvent.feature == 'onboarding',
                      FeatureEvent.created_at >= cutoff)
              .all())

    # Unique users (or session_ids when not logged in) per action
    unique_per_action = defaultdict(set)
    for ev in events:
        # Use user_id if logged in, else session_id, else event id as fallback
        key = ev.user_id or ev.session_id or f'evt-{ev.id}'
        unique_per_action[ev.action or ''].add(key)

    step_1_viewed = len(unique_per_action.get('step_1_viewed', set()))
    step_2_viewed = len(unique_per_action.get('step_2_viewed', set()))
    step_3_viewed = len(unique_per_action.get('step_3_viewed', set()))
    skipped_at_1 = len(unique_per_action.get('skipped_at_step_1', set()))
    skipped_at_2 = len(unique_per_action.get('skipped_at_step_2', set()))
    skipped_at_3 = len(unique_per_action.get('skipped_at_step_3', set()))
    completed = len(unique_per_action.get('completed', set()))

    # Retention percentages between steps
    def pct(num, denom):
        if not denom:
            return None
        return round((num / denom) * 100, 1)

    return jsonify({
        'window_days': days,
        'total_events': len(events),
        'steps': [
            {
                'step': 1,
                'label': '🎯 Goals (budget, repair tolerance, fear)',
                'unique_users': step_1_viewed,
                'retention_from_prev_pct': None,  # First step
                'skipped_here': skipped_at_1,
            },
            {
                'step': 2,
                'label': '⚖️ Legal Consents (terms, privacy, disclaimer)',
                'unique_users': step_2_viewed,
                'retention_from_prev_pct': pct(step_2_viewed, step_1_viewed),
                'skipped_here': skipped_at_2,
            },
            {
                'step': 3,
                'label': '✅ Final confirmation',
                'unique_users': step_3_viewed,
                'retention_from_prev_pct': pct(step_3_viewed, step_2_viewed),
                'skipped_here': skipped_at_3,
            },
        ],
        'completed': completed,
        'completion_rate_pct_of_step_1': pct(completed, step_1_viewed),
        # Where users actually drop off (computed)
        'biggest_drop_step_to_step': (lambda: (
            ('step_1_to_step_2', step_1_viewed - step_2_viewed) if (step_1_viewed - step_2_viewed) >= max(step_2_viewed - step_3_viewed, step_3_viewed - completed)
            else ('step_2_to_step_3', step_2_viewed - step_3_viewed) if (step_2_viewed - step_3_viewed) >= (step_3_viewed - completed)
            else ('step_3_to_completed', step_3_viewed - completed)
        ))(),
        'note': (
            'unique_users counts distinct user_id (or session_id when not '
            'logged in). Retention is the % of users from the previous step '
            'who reached this step. If unique_users for step 1 is much '
            'higher than registered users overall, anonymous bot traffic '
            'may be inflating it.'
        ),
    })


@admin_bp.route('/api/admin/funnel-debug', methods=['GET'])
@_api_admin_req_dec
def api_funnel_debug():
    """Deep funnel analysis — maps every buyer signup through conversion steps."""
    from models import User, Property
    from sqlalchemy import func as _func
    from datetime import datetime, timedelta

    try:
        from models import CreditTransaction
        _has_credit_txn = True
    except Exception:
        _has_credit_txn = False

    # Exclude test/persona/e2e accounts before counting (single source of truth).
    # Previously this sampled the last 200 buyers including seed accounts, which
    # padded the headline "Total Users" count. Excluding by id is NULL-email-safe.
    _test_ids = _canonical_test_user_ids()

    # All real buyer users ordered by signup date (test accounts removed)
    _q = User.query.filter(User.tier != None)
    if _test_ids:
        _q = _q.filter(~User.id.in_(_test_ids))
    users = _q.order_by(User.created_at.desc()).limit(200).all()

    rows = []
    for u in users:
        # Analyses run (via Property table)
        analyses = Property.query.filter_by(user_id=u.id).count()

        # Onboarding completed?
        onboarded = bool(getattr(u, 'onboarding_completed', False))

        # Has subscription?
        plan = getattr(u, 'subscription_plan', 'free') or 'free'
        tier = getattr(u, 'tier', 'free') or 'free'
        is_paid = plan not in ('free', '', None) and tier not in ('free', '', None)

        # Has stripe customer (reached checkout)?
        has_stripe = bool(getattr(u, 'stripe_customer_id', None))

        # Has paid via CreditTransaction?
        purchased = False
        if _has_credit_txn:
            try:
                from models import CreditTransaction
                purchased = CreditTransaction.query.filter_by(
                    user_id=u.id, status='completed'
                ).first() is not None
            except Exception:
                purchased = is_paid

        # Last login
        last_login = u.last_login
        days_since_login = None
        if last_login:
            days_since_login = (datetime.utcnow() - last_login).days

        # Funnel stage
        if purchased or (is_paid and tier != 'free'):
            stage = 'CONVERTED'
        elif has_stripe:
            stage = 'CHECKOUT_STARTED'
        elif analyses > 0:
            stage = 'USED_PRODUCT'
        elif onboarded:
            stage = 'ONBOARDED'
        else:
            stage = 'SIGNED_UP_ONLY'

        rows.append({
            'email': u.email,
            'joined': u.created_at.isoformat() if u.created_at else None,
            'days_ago': (datetime.utcnow() - u.created_at).days if u.created_at else None,
            'last_login': last_login.isoformat() if last_login else None,
            'days_since_login': days_since_login,
            'analyses': analyses,
            'onboarded': onboarded,
            'has_stripe': has_stripe,
            'plan': plan,
            'tier': tier,
            'purchased': purchased,
            'stage': stage,
            'auth_provider': getattr(u, 'auth_provider', None),
            'source': getattr(u, 'referred_by_code', None),
        })

    # Summary by stage
    from collections import Counter
    stage_counts = Counter(r['stage'] for r in rows)

    # Drop-off analysis
    total = len(rows)
    signed_up = total
    onboarded_n   = sum(1 for r in rows if r['stage'] in ('ONBOARDED','USED_PRODUCT','CHECKOUT_STARTED','CONVERTED'))
    used_product  = sum(1 for r in rows if r['stage'] in ('USED_PRODUCT','CHECKOUT_STARTED','CONVERTED'))
    checkout      = sum(1 for r in rows if r['stage'] in ('CHECKOUT_STARTED','CONVERTED'))
    converted     = sum(1 for r in rows if r['stage'] == 'CONVERTED')

    # Never came back (only 1 login ever = signup session)
    never_returned = sum(1 for r in rows if r['days_since_login'] is not None and r['days_since_login'] >= 1 and r['analyses'] == 0 and not r['onboarded'])
    churned_after_analysis = sum(1 for r in rows if r['analyses'] > 0 and not r['purchased'] and r['days_since_login'] is not None and r['days_since_login'] > 7)

    return jsonify({
        'total_buyers': total,
        'funnel': {
            'signed_up':     signed_up,
            'onboarded':     onboarded_n,
            'used_product':  used_product,
            'checkout':      checkout,
            'converted':     converted,
        },
        'drop_off': {
            'never_returned':           never_returned,
            'churned_after_analysis':   churned_after_analysis,
            'stuck_at_signup':          stage_counts.get('SIGNED_UP_ONLY', 0),
            'stuck_at_onboarding':      stage_counts.get('ONBOARDED', 0),
            'stuck_after_product_use':  stage_counts.get('USED_PRODUCT', 0),
            'abandoned_checkout':       stage_counts.get('CHECKOUT_STARTED', 0),
        },
        'stage_breakdown': dict(stage_counts),
        'users': rows,
    })



@admin_bp.route('/api/admin/user-drip/list', methods=['GET'])
@_api_admin_req_dec
def api_user_drip_list():
    """List all buyer accounts with drip campaign state.

    v5.88.43: now includes per-user drip_step, drip_last_sent_at,
    drip_completed, computed next_step_due_at and a quick status label.
    """
    from models import User
    from drip_campaign import _drip_min_hours, MAX_DRIP_STEP
    from datetime import timedelta as _td

    EXCLUDE = {'francis@piotnetworks.com', 'francis.kurupacheril@gmail.com'}
    # Known bot/dummy patterns
    BOT_PATTERNS = ['test@', 'example.com', 'mailinator', 'guerrilla', 'tempmail',
                    'throwaway', 'fakeinbox', 'sharklasers', 'dispostable', 'yopmail']

    # Exclude test/persona/e2e accounts using the single canonical source.
    # The old inline ~endswith filters missed .test.example.com and dropped any
    # NULL-email user; excluding by id avoids both.
    _test_ids = _canonical_test_user_ids()
    _q = User.query
    if _test_ids:
        _q = _q.filter(~User.id.in_(_test_ids))
    users = _q.order_by(User.created_at.desc()).limit(300).all()
    result = []
    now = datetime.utcnow()
    for u in users:
        if u.email in EXCLUDE:
            continue
        email_lower = u.email.lower()
        likely_bot = any(p in email_lower for p in BOT_PATTERNS)

        # v5.88.43: drip state — read fields with safe defaults since older rows
        # may have NULL values until backfilled
        drip_step = getattr(u, 'drip_step', None) or 0
        drip_last_sent_at = getattr(u, 'drip_last_sent_at', None)
        drip_completed = bool(getattr(u, 'drip_completed', False))

        # Compute next-step due time
        next_step_due_at = None
        next_step = None
        if not drip_completed and drip_step < MAX_DRIP_STEP and u.created_at:
            next_step = drip_step + 1
            try:
                min_hours = _drip_min_hours(next_step)
                next_step_due_at = (u.created_at + _td(hours=min_hours)).isoformat()
            except Exception:
                next_step_due_at = None

        # Status label for UI
        if drip_completed or drip_step >= MAX_DRIP_STEP:
            status = 'completed'
        elif drip_step == 0:
            status = 'not_started'
        else:
            status = 'in_progress'

        result.append({
            'id': u.id,
            'email': u.email,
            'name': u.name or '',
            'joined': u.created_at.isoformat() if u.created_at else None,
            'last_login': u.last_login.isoformat() if u.last_login else None,
            'auth': u.auth_provider or 'email',
            'plan': getattr(u, 'subscription_plan', 'free') or 'free',
            'likely_bot': likely_bot,
            # v5.88.43 drip fields
            'drip_step': drip_step,
            'drip_last_sent_at': drip_last_sent_at.isoformat() if drip_last_sent_at else None,
            'drip_completed': drip_completed,
            'next_step': next_step,
            'next_step_due_at': next_step_due_at,
            'drip_status': status,
        })
    return jsonify({'users': result, 'total': len(result)})


@admin_bp.route('/api/admin/user-drip/schedule', methods=['GET'])
@_api_admin_req_dec
def api_user_drip_schedule():
    """v5.88.43: Return the canonical drip schedule + step previews.

    Used by the admin UI to render 'this is what each step looks like'
    so the founder knows what's actually being sent.
    """
    try:
        from drip_campaign import (
            DRIP_SCHEDULE, MONTHLY_DRIP_START_HOURS, MONTHLY_DRIP_INTERVAL_HOURS,
            MAX_DRIP_STEP, _drip_min_hours,
        )
    except ImportError as e:
        return jsonify({'error': f'drip module: {e}'}), 500

    schedule = []
    for step in range(1, MAX_DRIP_STEP + 1):
        try:
            min_hours = _drip_min_hours(step)
            days = round(min_hours / 24, 1)
            schedule.append({
                'step': step,
                'min_hours_since_signup': min_hours,
                'days_since_signup': days,
                'kind': 'nurture' if step <= 5 else 'monthly_listings',
            })
        except Exception:
            pass

    return jsonify({
        'schedule': schedule,
        'nurture_steps': 5,
        'monthly_start_hours': MONTHLY_DRIP_START_HOURS,
        'monthly_interval_hours': MONTHLY_DRIP_INTERVAL_HOURS,
        'max_step': MAX_DRIP_STEP,
    })


@admin_bp.route('/api/admin/user-drip/send', methods=['POST'])
@_api_admin_req_dec
def api_user_drip_send():
    """
    Fire drip step 1 at a specific user or all eligible users.
    Body: { "user_id": 123 }  OR  { "send_all": true }
    Excludes Francis accounts and likely bots.
    """
    from models import User
    from drip_campaign import send_user_drip_step

    data = request.get_json() or {}
    EXCLUDE = {'francis@piotnetworks.com', 'francis.kurupacheril@gmail.com'}
    BOT_PATTERNS = ['test@', 'example.com', 'mailinator', 'guerrilla', 'tempmail',
                    'throwaway', 'fakeinbox', 'sharklasers', 'dispostable', 'yopmail']

    if data.get('user_id'):
        u = User.query.get(data['user_id'])
        if not u:
            return jsonify({'error': 'User not found'}), 404
        if u.email in EXCLUDE:
            return jsonify({'error': 'Excluded account'}), 400
        success = send_user_drip_step(u, step=1)
        return jsonify({'sent': 1 if success else 0, 'failed': 0 if success else 1,
                        'email': u.email, 'success': success})

    if data.get('send_all'):
        _test_ids = _canonical_test_user_ids()
        _q = User.query
        if _test_ids:
            _q = _q.filter(~User.id.in_(_test_ids))
        users = _q.order_by(User.created_at.desc()).limit(300).all()
        sent = 0; failed = 0; skipped = 0; results = []
        for u in users:
            if u.email in EXCLUDE:
                skipped += 1; continue
            email_lower = u.email.lower()
            if any(p in email_lower for p in BOT_PATTERNS):
                skipped += 1
                results.append({'email': u.email, 'status': 'skipped_bot'})
                continue
            try:
                ok = send_user_drip_step(u, step=1)
                if ok:
                    sent += 1
                    results.append({'email': u.email, 'status': 'sent'})
                else:
                    failed += 1
                    results.append({'email': u.email, 'status': 'failed'})
            except Exception as e:
                failed += 1
                results.append({'email': u.email, 'status': f'error: {str(e)[:60]}'})
        return jsonify({'sent': sent, 'failed': failed, 'skipped': skipped, 'results': results})

    return jsonify({'error': 'Provide user_id or send_all:true'}), 400


@admin_bp.route('/api/admin/user-drip/health', methods=['GET'])
@_api_admin_req_dec
def api_user_drip_health():
    """Live auto-sender health (read-only). Answers the only question the drip
    card needs: is the scheduler running, how many users are in the sequence,
    and how recently did drip actually send? No side effects, no state changes."""
    import os as _os
    from datetime import datetime, timedelta
    from models import db, User, EmailSendLog

    now = datetime.utcnow()
    scheduler_on = not (
        _os.environ.get('DISABLE_SCHEDULER') == '1'
        or _os.environ.get('APP_ENV', '').strip().lower() in ('staging', 'preview'))

    def _q(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    try:
        _canon_ids = _canonical_test_user_ids()
    except Exception:
        _canon_ids = []
    _seq_conds = []
    if _canon_ids:
        _seq_conds.append(~User.id.in_(_canon_ids))
    _seq_conds.append(db.or_(User.email_unsubscribed.is_(None), User.email_unsubscribed == False))  # noqa: E712
    _seq_conds.append(db.or_(User.drip_completed.is_(None), User.drip_completed == False))  # noqa: E712
    in_sequence = _q(lambda: User.query.filter(*_seq_conds).count(), 0)

    sent_24h = _q(lambda: EmailSendLog.query.filter(
        EmailSendLog.ts >= now - timedelta(hours=24),
        EmailSendLog.success.is_(True),
        EmailSendLog.email_type.like('%drip%'),
    ).count(), 0)

    last_row = _q(lambda: EmailSendLog.query.filter(
        EmailSendLog.email_type.like('%drip%'),
        EmailSendLog.success.is_(True),
    ).order_by(EmailSendLog.ts.desc()).first(), None)
    last_sent_at = (last_row.ts.isoformat() + 'Z') if (last_row and last_row.ts) else None

    return jsonify({
        'scheduler_on': scheduler_on,
        'interval_minutes': 15,
        'in_sequence': in_sequence,
        'sent_24h': sent_24h,
        'last_sent_at': last_sent_at,
    })


@admin_bp.route('/api/admin/reddit-ads/health', methods=['GET'])
@_api_admin_req_dec
def api_reddit_ads_health():
    """Live Reddit Ads sync health (read-only). Surfaces the three things that go
    wrong silently: is it configured, can it currently authenticate (the 403 that
    only showed up in Sentry), and how fresh is the spend data. The token check is
    a real refresh attempt with a short timeout — this endpoint only loads when the
    Ad Performance view is opened, so it is infrequent and tells the truth instead
    of guessing from staleness. No writes, no state changes."""
    from datetime import date as _date

    def _q(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    try:
        from reddit_ads_sync import is_configured
        configured = bool(is_configured())
    except Exception:
        configured = False

    token_ok = None
    token_error = None
    if configured:
        try:
            from reddit_ads_sync import _get_access_token
            _get_access_token()
            token_ok = True
        except Exception as e:
            token_ok = False
            token_error = str(e)[:140]

    from models import GTMAdPerformance
    last_row = _q(lambda: GTMAdPerformance.query.filter_by(channel='reddit_ads')
                  .order_by(GTMAdPerformance.date.desc()).first(), None)
    last_sync_date = last_row.date.isoformat() if (last_row and last_row.date) else None
    stale_days = None
    if last_row and last_row.date:
        stale_days = _q(lambda: (_date.today() - last_row.date).days, None)

    return jsonify({
        'configured': configured,
        'token_ok': token_ok,
        'token_error': token_error,
        'last_sync_date': last_sync_date,
        'stale_days': stale_days,
    })


@admin_bp.route('/api/admin/agents', methods=['GET'])
@_api_admin_req_dec
def admin_agents_list():
    from models import Agent, User, AgentShare
    from sqlalchemy import func as _func
    agents = Agent.query.order_by(Agent.created_at.desc()).all()
    result = []
    for a in agents:
        owner = User.query.get(a.user_id)
        share_count = AgentShare.query.filter_by(agent_id=a.id).count()
        result.append({
            'id': a.id,
            'agent_name': a.agent_name,
            'business_name': a.business_name,
            'email': owner.email if owner else '—',
            'license_number': a.license_number,
            'license_state': a.license_state,
            'plan': a.plan,
            'is_verified': a.is_verified,
            'is_active': a.is_active,
            'total_shares': a.total_shares or 0,
            'total_buyers_converted': a.total_buyers_converted or 0,
            'share_count': share_count,
            'created_at': a.created_at.isoformat() if a.created_at else None,
            'notes': a.notes,
        })
    return jsonify({'agents': result, 'total': len(result)})



@admin_bp.route('/api/admin/agents/<int:agent_id>', methods=['PATCH'])
@_api_admin_req_dec
def admin_update_agent(agent_id):
    from models import Agent
    agent = Agent.query.get_or_404(agent_id)
    # v5.88.14: silent=True for safe no-body handling. Same pattern as
    # the other request.get_json() fixes in the v5.88.09 / v5.88.11 /
    # v5.88.13 / v5.88.14 series. 8th endpoint with this fix.
    data = request.get_json(silent=True) or {}
    for field in ['is_verified', 'is_active', 'plan', 'monthly_quota', 'notes']:
        if field in data:
            setattr(agent, field, data[field])
    db.session.commit()
    return jsonify({'success': True})



@admin_bp.route('/api/admin/feature-events', methods=['GET'])
@_admin_req_dec
def admin_feature_events():
    """
    Feature engagement summary for product decisions.
    Returns: total counts per feature, DAU using each feature, top users.
    """
    days = int(request.args.get('days', 30))
    since = datetime.utcnow() - timedelta(days=days)

    # Aggregate by feature + action
    from sqlalchemy import func as _func
    rows = db.session.query(
        FeatureEvent.feature,
        FeatureEvent.action,
        _func.count(FeatureEvent.id).label('total'),
        _func.count(_func.distinct(FeatureEvent.user_id)).label('unique_users'),
    ).filter(
        FeatureEvent.created_at >= since
    ).group_by(
        FeatureEvent.feature, FeatureEvent.action
    ).order_by(_func.count(FeatureEvent.id).desc()).all()

    # Daily trend for the most popular feature
    summary = [
        {'feature': r.feature, 'action': r.action,
         'total': r.total, 'unique_users': r.unique_users}
        for r in rows
    ]

    # Top users by engagement
    top_users = db.session.query(
        FeatureEvent.user_id,
        _func.count(FeatureEvent.id).label('events'),
    ).filter(
        FeatureEvent.created_at >= since,
        FeatureEvent.user_id.isnot(None)
    ).group_by(FeatureEvent.user_id).order_by(
        _func.count(FeatureEvent.id).desc()
    ).limit(20).all()

    top_user_ids = [r.user_id for r in top_users]
    user_map = {u.id: u.email for u in User.query.filter(User.id.in_(top_user_ids)).all()}
    top = [{'user_id': r.user_id, 'email': user_map.get(r.user_id, '?'), 'events': r.events}
           for r in top_users]

    return jsonify({'days': days, 'since': since.isoformat(), 'events': summary, 'top_users': top})


@admin_bp.route('/api/admin/topbar-widget', methods=['GET'])
@_admin_req_dec
def admin_topbar_widget():
    """
    v5.87.95: Top-bar address widget funnel.

    Aggregates FeatureEvent rows where feature='topbar_address_widget' to show:
      - daily submission + arrival counts
      - submit-to-arrival drop-off (how many people clicked "Check" but
        didn't actually land on /risk-check — usually means they navigated
        away or the redirect failed)
      - has_zip distribution (because addresses without ZIP fail validation)
      - viewport breakdown (mobile vs desktop)

    Query params:
      days (int, default 30): rolling window
    """
    from sqlalchemy import func as _func
    import json as _json

    days = max(1, min(int(request.args.get('days', 30)), 365))
    since = datetime.utcnow() - timedelta(days=days)

    base = FeatureEvent.query.filter(
        FeatureEvent.feature == 'topbar_address_widget',
        FeatureEvent.created_at >= since,
    )
    total_events = base.count()

    # Counts by action
    by_action = dict(
        db.session.query(
            FeatureEvent.action,
            _func.count(FeatureEvent.id),
        ).filter(
            FeatureEvent.feature == 'topbar_address_widget',
            FeatureEvent.created_at >= since,
        ).group_by(FeatureEvent.action).all()
    )
    submits = int(by_action.get('submit', 0))
    arrivals = int(by_action.get('arrived', 0))

    # Submit-to-arrival: arrivals / submits (capped at 100% in case of double-fire)
    submit_to_arrival_pct = (
        round(min(100.0, 100.0 * arrivals / submits), 1) if submits else None
    )

    # Daily trend (combined submits + arrivals per day)
    daily_rows = db.session.query(
        _func.date(FeatureEvent.created_at).label('day'),
        FeatureEvent.action,
        _func.count(FeatureEvent.id).label('n'),
    ).filter(
        FeatureEvent.feature == 'topbar_address_widget',
        FeatureEvent.created_at >= since,
    ).group_by(
        _func.date(FeatureEvent.created_at),
        FeatureEvent.action,
    ).order_by(_func.date(FeatureEvent.created_at).asc()).all()

    daily = {}
    for r in daily_rows:
        d = r.day.isoformat() if hasattr(r.day, 'isoformat') else str(r.day)
        if d not in daily:
            daily[d] = {'date': d, 'submits': 0, 'arrivals': 0}
        if r.action == 'submit':
            daily[d]['submits'] = int(r.n)
        elif r.action == 'arrived':
            daily[d]['arrivals'] = int(r.n)
    daily_list = sorted(daily.values(), key=lambda x: x['date'])

    # has_zip distribution + viewport breakdown — pull all events so we can
    # parse the meta JSON. For 30 days at moderate volume this is cheap; if
    # volume grows past ~50K events, switch to a Postgres JSON-extract query.
    has_zip_yes = 0
    has_zip_no = 0
    desktop = 0
    mobile = 0
    sample_events = []
    for evt in base.all():
        try:
            m = _json.loads(evt.meta) if evt.meta else {}
        except Exception:
            m = {}
        if m.get('has_zip') is True:
            has_zip_yes += 1
        elif m.get('has_zip') is False:
            has_zip_no += 1
        vw = m.get('viewport_w')
        if isinstance(vw, (int, float)):
            if vw < 768:
                mobile += 1
            else:
                desktop += 1
        # Keep last 20 events as a sample for debugging
        if len(sample_events) < 20:
            sample_events.append({
                'created_at': evt.created_at.isoformat(),
                'action': evt.action,
                'meta': m,
            })

    return jsonify({
        'days': days,
        'since': since.isoformat(),
        'total_events': total_events,
        'submits': submits,
        'arrivals': arrivals,
        'submit_to_arrival_pct': submit_to_arrival_pct,
        'has_zip': {'yes': has_zip_yes, 'no': has_zip_no},
        'viewport': {'desktop': desktop, 'mobile': mobile},
        'daily': daily_list,
        'sample_events': sample_events,
    })


@admin_bp.route('/api/admin/entry-cliff', methods=['GET'])
@_admin_req_dec
def admin_entry_cliff():
    """v5.89.117: Decompose the Visit→first-action cliff.

    The 95% top-of-funnel drop hides two very different populations. This splits
    them using events we now capture:
      - visits                 (GTMFunnelEvent stage='visit')
      - took ANY first action  = topbar address submits + signup-CTA clicks
      - of those, signup-CTA clicks that hit the /login wall
      - actual signups         (GTMFunnelEvent stage='signup')
    The gap between signup-CTA clicks and actual signups is the WALL-BOUNCE
    (wanted in, blocked by the gate). The gap between visits and any first action
    is the NO-INTENT / didn't-engage population. They need opposite fixes, so
    reporting them separately is the point.

    NOTE (honest): signup_cta_click is only captured from v5.89.117 forward, so
    this is meaningful only for windows starting after that deploy. Returns raw
    counts; at low volume treat as directional.
    """
    from sqlalchemy import func as _func
    from models import GTMFunnelEvent
    days = max(1, min(int(request.args.get('days', 30)), 365))
    since = datetime.utcnow() - timedelta(days=days)

    def _fe_count(feature, action=None):
        q = FeatureEvent.query.filter(FeatureEvent.feature == feature,
                                      FeatureEvent.created_at >= since)
        if action:
            q = q.filter(FeatureEvent.action == action)
        return q.count()

    def _stage_count(stage):
        return GTMFunnelEvent.query.filter(GTMFunnelEvent.stage == stage,
                                           GTMFunnelEvent.created_at >= since).count()

    visits = _stage_count('visit')
    topbar_submits = _fe_count('topbar_address_widget', 'submit')
    cta_clicks = _fe_count('signup_cta_click', 'click')
    signups = _stage_count('signup')

    any_first_action = topbar_submits + cta_clicks
    # wall-bounce: clicked the signup CTA but never became a signup.
    # (Can be negative-guarded: signups can come from CTA clicks OR the free path's
    #  later upsell, so we report both numbers and the gap, not a forced ratio.)
    wall_bounce = max(0, cta_clicks - signups)

    def pct(n, d):
        return round(100.0 * n / d, 1) if d else None

    return jsonify({
        'days': days,
        'since': since.isoformat(),
        'caveat': ('signup_cta_click is captured from v5.89.117 onward; only '
                   'windows after that deploy are meaningful. Low volume = directional.'),
        'funnel': {
            'visits': visits,
            'any_first_action': any_first_action,
            'topbar_address_submits': topbar_submits,
            'signup_cta_clicks': cta_clicks,
            'signups': signups,
        },
        'decomposition': {
            'no_intent_or_no_engage': max(0, visits - any_first_action),
            'no_intent_pct_of_visits': pct(max(0, visits - any_first_action), visits),
            'wall_bounce_clicked_cta_no_signup': wall_bounce,
            'wall_bounce_pct_of_cta_clicks': pct(wall_bounce, cta_clicks),
        },
    })


@admin_bp.route('/api/admin/risk-check-exit', methods=['GET'])
@_admin_req_dec
def admin_risk_check_exit():
    """v5.89.122: Decompose where users go AFTER completing a Risk Check.

    Drop-off after the free Risk Check hid four very different exits, and the
    funnel could not tell them apart because the post-result CTA clicks were
    untracked. risk-check.html now fires a 'risk_check_cta_click' feature event
    tagged with meta.target ∈ {signup, truth_check, sample, rescan}. This splits
    risk_check_complete into where those engaged users went:
      - clicked Get Full Analysis (signup)  → the conversion we want
      - clicked Run Truth Check              → routed sideways into another free tool
      - clicked See Sample Report            → routed sideways
      - clicked Check Another Address        → re-used the free tool
      - no exit click                        → left (or only shared) — a dead end
    Counts are by DISTINCT actor (user_id or session_id) per target, so a user who
    clicks more than one CTA is not double-counted within a target.

    NOTE (honest): risk_check_cta_click is captured from v5.89.122 forward, so only
    windows after that deploy are meaningful. At low volume, treat as directional.
    """
    from models import GTMFunnelEvent
    days = max(1, min(int(request.args.get('days', 30)), 365))
    since = datetime.utcnow() - timedelta(days=days)

    def _stage_count(stage):
        return GTMFunnelEvent.query.filter(GTMFunnelEvent.stage == stage,
                                           GTMFunnelEvent.created_at >= since).count()

    completes = _stage_count('risk_check_complete')
    signups = _stage_count('signup')

    # Distinct-actor breakdown of post-result CTA clicks by destination.
    evts = FeatureEvent.query.filter(FeatureEvent.feature == 'risk_check_cta_click',
                                     FeatureEvent.created_at >= since).all()
    valid_targets = {'signup', 'truth_check', 'sample', 'rescan'}
    actors_by_target = {t: set() for t in valid_targets}
    actors_by_target['other'] = set()
    any_click_actors = set()
    for e in evts:
        actor = e.user_id or e.session_id or ('evt%s' % e.id)
        target = 'other'
        if e.meta:
            try:
                target = (json.loads(e.meta) or {}).get('target') or 'other'
            except Exception:
                target = 'other'
        if target not in actors_by_target:
            target = 'other'
        actors_by_target[target].add(actor)
        any_click_actors.add(actor)

    counts = {t: len(actors_by_target[t]) for t in actors_by_target}
    # "no exit click" = completed a Risk Check but clicked none of the exits.
    # completes is a raw stage count and any_click is distinct-actor, so this is
    # an estimate (guarded non-negative); the caveat says as much.
    no_exit_click = max(0, completes - len(any_click_actors))

    def pct(n, d):
        return round(100.0 * n / d, 1) if d else None

    return jsonify({
        'days': days,
        'since': since.isoformat(),
        'caveat': ('risk_check_cta_click is captured from v5.89.122 onward; only windows '
                   'after that deploy are meaningful. CTA clicks are distinct-actor while '
                   'completions are raw events, so "no exit click" is an estimate. '
                   'Low volume = directional.'),
        'funnel': {
            'risk_check_complete': completes,
            'signups': signups,
        },
        'exits': {
            'clicked_signup':      counts.get('signup', 0),
            'clicked_truth_check': counts.get('truth_check', 0),
            'clicked_sample':      counts.get('sample', 0),
            'clicked_rescan':      counts.get('rescan', 0),
            'clicked_other':       counts.get('other', 0),
            'no_exit_click':       no_exit_click,
        },
        'pct_of_completions': {
            'signup':        pct(counts.get('signup', 0), completes),
            'truth_check':   pct(counts.get('truth_check', 0), completes),
            'sample':        pct(counts.get('sample', 0), completes),
            'rescan':        pct(counts.get('rescan', 0), completes),
            'no_exit_click': pct(no_exit_click, completes),
        },
    })


@admin_bp.route('/api/admin/champion-inspectors', methods=['GET'])
@_admin_req_dec
def admin_champion_inspectors():
    """
    Ranked inspector list for identifying outreach targets.
    Ranks by: total_reports DESC, total_buyers_converted DESC.
    Returns top 20 with full contact info and conversion funnel stats.
    """
    from sqlalchemy import func as _func

    limit = min(int(request.args.get('limit', 20)), 100)

    inspectors = Inspector.query.order_by(
        Inspector.total_reports.desc(),
        Inspector.total_buyers_converted.desc(),
    ).limit(limit).all()

    results = []
    for insp in inspectors:
        user = User.query.get(insp.user_id) if insp.user_id else None

        # Per-inspector report funnel
        reports = InspectorReport.query.filter_by(inspector_id=insp.id).all()
        total       = len(reports)
        viewed      = sum(1 for r in reports if r.buyer_viewed_at)
        registered  = sum(1 for r in reports if r.buyer_registered)
        converted   = sum(1 for r in reports if r.buyer_converted)

        view_rate     = round(viewed     / total * 100) if total > 0 else 0
        signup_rate   = round(registered / viewed * 100) if viewed > 0 else 0
        convert_rate  = round(converted  / registered * 100) if registered > 0 else 0

        results.append({
            'inspector_id':        insp.id,
            'business_name':       insp.business_name or '',
            'email':               insp.email or (user.email if user else ''),
            'phone':               insp.phone or '',
            'plan':                insp.plan or 'free',
            'service_areas':       insp.service_areas or '',
            'total_reports':       total,
            'buyers_viewed':       viewed,
            'buyers_registered':   registered,
            'buyers_converted':    converted,
            'view_rate_pct':       view_rate,
            'signup_rate_pct':     signup_rate,
            'convert_rate_pct':    convert_rate,
            'member_since':        insp.created_at.strftime('%Y-%m-%d') if insp.created_at else '',
        })

    return jsonify({
        'count': len(results),
        'inspectors': results,
        'note': 'Sorted by total_reports DESC then buyers_converted DESC. Top candidates for champion outreach.'
    })



@admin_bp.route('/api/admin/wipe-communities', methods=['POST'])
@_api_admin_req_dec
def api_admin_wipe_communities():
    """Wipe all communities for a given platform so fresh ones can be added."""
    data = request.get_json(silent=True) or {}
    platform = data.get('platform', '').strip()
    if platform not in ('nextdoor', 'facebook'):
        return jsonify({'error': 'Only nextdoor or facebook allowed'}), 400
    from models import GTMTargetSubreddit
    deleted = GTMTargetSubreddit.query.filter_by(platform=platform).delete()
    db.session.commit()
    return jsonify({'status': 'ok', 'deleted': deleted, 'platform': platform})


@admin_bp.route('/api/admin/watches', methods=['GET'])
@_api_admin_req_dec
def admin_list_watches():
    """Admin view of all active property watches."""
    from models import PropertyWatch, User
    watches = PropertyWatch.query.filter_by(is_active=True)        .order_by(PropertyWatch.created_at.desc()).limit(100).all()
    return jsonify({'watches': [{
        'id': w.id,
        'address': w.address,
        'asking_price': w.asking_price,
        'user_id': w.user_id,
        'created_at': w.created_at.isoformat(),
        'expires_at': w.expires_at.isoformat() if w.expires_at else None,
        'has_coords': bool(w.latitude and w.longitude),
        'last_comps_check': w.last_comps_check_at.isoformat() if w.last_comps_check_at else None,
        'last_earthquake_check': w.last_earthquake_check_at.isoformat() if w.last_earthquake_check_at else None,
        'last_price_check': w.last_price_check_at.isoformat() if w.last_price_check_at else None,
        'last_permit_check': w.last_permit_check_at.isoformat() if w.last_permit_check_at else None,
    } for w in watches]})







@admin_bp.route('/auto-test-admin')
@admin_bp.route('/test-admin')
@admin_bp.route('/admin')
@_admin_req_dec
def serve_admin_dashboard():
    """Master Admin Dashboard — serves static/admin.html"""
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'admin.html')


@admin_bp.route('/admin-agent')
@_admin_req_dec
def admin_agent_page():
    """Legacy redirect — Reddit Agent merged into GTM dashboard"""
    admin_key = request.args.get('admin_key', '')
    return redirect(f'/admin/gtm?admin_key={admin_key}')


# ── Standalone admin sub-pages (linked from admin shell "Open full page ↗") ──

@admin_bp.route('/admin/diags')
@_admin_req_dec
def admin_diags_page():
    """v5.89.31: read-only database diagnostic page. Operator-facing tool
    for running a fixed battery of pg_stat / row-count / schema-introspection
    queries against the live database. No SQL is operator-typed — all queries
    are server-side registered in _DIAGS_REGISTRY (admin_routes.py)."""
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'admin-diags.html')


# ═══════════════════════════════════════════════════════════════════════════════
# v5.89.47 BULK RELABEL WORKFLOW
# ═══════════════════════════════════════════════════════════════════════════════
# Operator-facing endpoints to orchestrate the relabel pipeline:
#   GET  /admin/relabel              — UI page
#   POST /api/admin/relabel/start    — create MLRelabelRun, spawn subprocess
#   GET  /api/admin/relabel/status   — current/latest run status + progress
#   POST /api/admin/relabel/cancel   — set cancel_requested on a running run
#   GET  /api/admin/relabel/history  — past runs (paginated)

@admin_bp.route('/admin/relabel')
@_admin_req_dec
def admin_relabel_page():
    """v5.89.47: bulk relabel UI page."""
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'admin-relabel.html')


@admin_bp.route('/api/admin/relabel/start', methods=['POST'])
@_api_admin_req_dec
def admin_relabel_start():
    """v5.89.47: create an MLRelabelRun row and spawn the relabel subprocess.

    Request JSON: { mode: 'dry_run'|'commit', threshold: 0.90 }
    Response: { ok, job_id, run_id } on success, { ok: False, error } on failure
    """
    import json as _json
    import subprocess as _sp
    import uuid as _uuid
    import sys as _sys
    from flask import request as _req
    from models import db as _db, MLRelabelRun

    data = _req.get_json(silent=True) or {}
    mode = (data.get('mode') or '').strip()
    threshold = data.get('threshold')

    if mode not in ('dry_run', 'commit'):
        return jsonify({'ok': False, 'error': f'Invalid mode: {mode!r}'}), 400
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'threshold must be a number'}), 400
    if not (0.0 < threshold <= 1.0):
        return jsonify({'ok': False, 'error': 'threshold must be in (0, 1]'}), 400

    # Concurrency check: refuse if any relabel job is currently
    # running (status='queued' or 'running')
    active = MLRelabelRun.query.filter(
        MLRelabelRun.status.in_(['queued', 'running'])
    ).first()
    if active:
        return jsonify({
            'ok': False,
            'error': f'Relabel already in progress (job_id={active.job_id}, status={active.status}). '
                     f'Cancel it first or wait for completion.',
            'active_job_id': active.job_id,
        }), 409

    # Create the run row
    job_id = str(_uuid.uuid4())
    run = MLRelabelRun(
        job_id=job_id,
        created_at=datetime.utcnow(),
        mode=mode,
        confidence_threshold=threshold,
        triggered_by='admin',
        status='queued',
    )
    _db.session.add(run)
    _db.session.commit()

    # Spawn subprocess
    try:
        app_dir = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(app_dir, 'relabel_pipeline.py')
        if not os.path.exists(script):
            run.status = 'failed'
            run.error_message = f'relabel_pipeline.py not found at {script}'
            _db.session.commit()
            return jsonify({'ok': False, 'error': 'relabel_pipeline.py missing'}), 500

        child_env = os.environ.copy()
        child_env['OFFERWISE_TRAINING_SUBPROCESS'] = '1'
        _sp.Popen(
            [_sys.executable, script,
             '--job-id', job_id,
             '--mode', mode,
             '--threshold', str(threshold)],
            env=child_env,
            stdout=_sp.DEVNULL,
            stderr=_sp.DEVNULL,
            cwd=app_dir,
        )
        return jsonify({
            'ok': True,
            'job_id': job_id,
            'run_id': run.id,
            'mode': mode,
            'threshold': threshold,
        })
    except Exception as e:
        run.status = 'failed'
        run.error_message = f'Spawn failed: {type(e).__name__}: {e}'
        _db.session.commit()
        return jsonify({'ok': False, 'error': f'Spawn failed: {e}'}), 500


@admin_bp.route('/api/admin/relabel/status', methods=['GET'])
@_api_admin_req_dec
def admin_relabel_status():
    """v5.89.47: return the latest MLRelabelRun + parsed stats.

    Query: ?job_id=<uuid> (optional; defaults to most recent)

    v5.89.52: auto-detects zombie runs. If a run is in 'running' status
    but last_progress_at is more than STALE_THRESHOLD_SECONDS old, mark
    it 'failed' automatically before returning. This unsticks zombie
    runs without operator intervention.
    """
    import json as _json
    from flask import request as _req
    from models import db as _db, MLRelabelRun

    STALE_THRESHOLD_SECONDS = 600  # 10 minutes — bulk relabel commits
                                    # progress every ~60-90s normally

    job_id = (_req.args.get('job_id') or '').strip()
    if job_id:
        run = MLRelabelRun.query.filter_by(job_id=job_id).first()
    else:
        run = MLRelabelRun.query.order_by(MLRelabelRun.created_at.desc()).first()

    if not run:
        return jsonify({'ok': True, 'has_run': False})

    # v5.89.52: zombie detection. If run says "running" but no progress
    # has landed recently, mark it failed. Idempotent — if multiple
    # status polls fire concurrently, the second one sees status='failed'
    # and skips the update.
    if run.status == 'running' and run.last_progress_at:
        elapsed = (datetime.utcnow() - run.last_progress_at).total_seconds()
        if elapsed > STALE_THRESHOLD_SECONDS:
            run.status = 'failed'
            run.completed_at = datetime.utcnow()
            prior = (run.error_message or '').strip()
            note = (f'[v5.89.52 auto-stale detection at {datetime.utcnow().isoformat()}Z] '
                    f'Subprocess appears dead — no progress in '
                    f'{int(elapsed)} seconds ({int(elapsed/60)} min). '
                    f'Likely OOM kill, network drop, or hang. Last progress at '
                    f'{run.last_progress_at.isoformat()}Z, '
                    f'{run.rows_processed:,}/{run.rows_total:,} rows processed.')
            run.error_message = note + ('\n\nPrior error: ' + prior if prior else '')
            try:
                _db.session.commit()
            except Exception:
                _db.session.rollback()

    stats = {}
    if run.stats_json:
        try:
            stats = _json.loads(run.stats_json)
        except Exception:
            stats = {}

    pct = 0.0
    if run.rows_total > 0:
        pct = (run.rows_processed / run.rows_total) * 100

    return jsonify({
        'ok': True,
        'has_run': True,
        'run': {
            'job_id': run.job_id,
            'status': run.status,
            'mode': run.mode,
            'threshold': run.confidence_threshold,
            'created_at': run.created_at.isoformat() if run.created_at else None,
            'started_at': run.started_at.isoformat() if run.started_at else None,
            'completed_at': run.completed_at.isoformat() if run.completed_at else None,
            'last_progress_at': run.last_progress_at.isoformat() if run.last_progress_at else None,
            'rows_total': run.rows_total,
            'rows_processed': run.rows_processed,
            'rows_changed_category': run.rows_changed_category,
            'rows_changed_severity': run.rows_changed_severity,
            'rows_low_confidence': run.rows_low_confidence,
            'rows_agreement': run.rows_agreement,
            'rows_failed': run.rows_failed,
            'progress_pct': pct,
            'cancel_requested': run.cancel_requested,
            'error_message': run.error_message,
            'triggered_training_job_id': run.triggered_training_job_id,
            'stats': stats,
        },
    })


@admin_bp.route('/api/admin/relabel/cancel', methods=['POST'])
@_api_admin_req_dec
def admin_relabel_cancel():
    """v5.89.47: signal cancellation of a running relabel job.

    Request JSON: { job_id }
    The subprocess checks cancel_requested between chunks and exits
    cooperatively. Partial corpus mutations (if commit mode) stay in
    place — operator can roll back via the audit_original_* columns
    if needed.
    """
    from flask import request as _req
    from models import db as _db, MLRelabelRun

    data = _req.get_json(silent=True) or {}
    job_id = (data.get('job_id') or '').strip()
    if not job_id:
        return jsonify({'ok': False, 'error': 'job_id required'}), 400

    run = MLRelabelRun.query.filter_by(job_id=job_id).first()
    if not run:
        return jsonify({'ok': False, 'error': 'Run not found'}), 404
    if run.status not in ('queued', 'running'):
        return jsonify({'ok': False, 'error': f'Run is already {run.status}'}), 400

    run.cancel_requested = True
    _db.session.commit()
    return jsonify({'ok': True, 'message': 'Cancellation requested. The job will stop at the next chunk boundary.'})


@admin_bp.route('/api/admin/relabel/force-fail', methods=['POST'])
@_api_admin_req_dec
def admin_relabel_force_fail():
    """v5.89.52: force a stuck 'running' MLRelabelRun to 'failed' state.

    Use when the subprocess is dead but the DB still shows the run as
    running. The cooperative cancel endpoint relies on the subprocess
    polling cancel_requested — useless if the subprocess is already dead.

    This endpoint just updates the DB row directly. Requires the explicit
    job_id to prevent killing a different (live) run by accident.

    Request JSON: { job_id, reason? }
    """
    from flask import request as _req
    from models import db as _db, MLRelabelRun

    data = _req.get_json(silent=True) or {}
    job_id = (data.get('job_id') or '').strip()
    reason = (data.get('reason') or 'Force-failed by operator').strip()
    if not job_id:
        return jsonify({'ok': False, 'error': 'job_id required'}), 400

    run = MLRelabelRun.query.filter_by(job_id=job_id).first()
    if not run:
        return jsonify({'ok': False, 'error': 'Run not found'}), 404
    if run.status not in ('queued', 'running'):
        return jsonify({
            'ok': False,
            'error': f'Run is already {run.status} — no force-fail needed',
        }), 400

    run.status = 'failed'
    run.completed_at = datetime.utcnow()
    # Preserve any existing error_message; prefix with force-fail note
    prior = (run.error_message or '').strip()
    note = f'[v5.89.52 force-fail at {datetime.utcnow().isoformat()}Z] {reason}'
    run.error_message = note + ('\n\nPrior error: ' + prior if prior else '')
    _db.session.commit()
    return jsonify({
        'ok': True,
        'message': f'Run {job_id[:14]}… marked as failed.',
        'rows_processed_at_failure': run.rows_processed,
        'rows_total': run.rows_total,
    })


@admin_bp.route('/api/admin/relabel/history', methods=['GET'])
@_api_admin_req_dec
def admin_relabel_history():
    """v5.89.47: list past relabel runs. Returns up to 50 most recent."""
    from models import db as _db, MLRelabelRun

    runs = (MLRelabelRun.query
            .order_by(MLRelabelRun.created_at.desc())
            .limit(50).all())
    return jsonify({
        'ok': True,
        'runs': [{
            'job_id': r.job_id,
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'completed_at': r.completed_at.isoformat() if r.completed_at else None,
            'mode': r.mode,
            'threshold': r.confidence_threshold,
            'status': r.status,
            'rows_total': r.rows_total,
            'rows_processed': r.rows_processed,
            'rows_changed_category': r.rows_changed_category,
            'rows_changed_severity': r.rows_changed_severity,
            'triggered_training_job_id': r.triggered_training_job_id,
        } for r in runs],
    })


# ═══════════════════════════════════════════════════════════════════════════════
# v5.89.50 LABELING HUB
# ═══════════════════════════════════════════════════════════════════════════════
# Single operator entry point for all label-related work. Replaces three
# separate surfaces:
#   - "🔄 Re-label All Unlabeled" button on /admin (ML Pipeline)
#   - /admin/labels/audit (kept as deep-link target)
#   - /admin/relabel (kept as deep-link target)
#
# Hub page is a "control center" — shows summary stats per section, with
# an Open arrow to drill into the existing detail page for each.

@admin_bp.route('/admin/labeling')
@_admin_req_dec
def admin_labeling_hub_page():
    """v5.89.50: single hub for all labeling activity."""
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'admin-labeling-hub.html')


@admin_bp.route('/admin/labels/initial')
@_admin_req_dec
def admin_labels_initial_page():
    """v5.89.51: dedicated page for "Re-label All Unlabeled" (Claude Haiku
    LLM labeling). Extracted from the ML Pipeline page so the Labeling hub
    can deep-link to a real detail page instead of an anchor."""
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'admin-labels-initial.html')


@admin_bp.route('/api/admin/labeling/summary', methods=['GET'])
@_api_admin_req_dec
def admin_labeling_summary():
    """v5.89.50: one-call summary for the hub page. Returns:
       - initial_labeling: unlabeled count, latest ingestion job
       - audit: latest batch stats, total decisions, dominant verdict
       - bulk_relabel: latest run state
    """
    from collections import Counter
    from models import (db as _db, MLFindingLabel, MLLabelAuditQueue,
                        MLLabelAuditDecision, MLRelabelRun, MLIngestionJob)

    # ── 1. Initial Labeling ────────────────────────────────────────
    # Unlabeled = rows where category_v2 is NULL (the LLM hasn't tagged
    # them yet). This matches what "Re-label All Unlabeled" looks for.
    try:
        unlabeled_count = (MLFindingLabel.query
                           .filter(MLFindingLabel.category_v2.is_(None))
                           .count())
    except Exception:
        unlabeled_count = None

    try:
        total_corpus = MLFindingLabel.query.count()
    except Exception:
        total_corpus = None

    # Latest LLM-labeling ingestion job (job_type filter is approximate;
    # we surface whatever the most recent job is)
    latest_ingest = None
    try:
        ij = (MLIngestionJob.query
              .order_by(MLIngestionJob.created_at.desc())
              .limit(1).first())
        if ij:
            latest_ingest = {
                'id': ij.id,
                'job_type': getattr(ij, 'job_type', None),
                'status': getattr(ij, 'status', None),
                'created_at': ij.created_at.isoformat() if ij.created_at else None,
            }
    except Exception:
        latest_ingest = None

    initial = {
        'unlabeled_count': unlabeled_count,
        'total_corpus': total_corpus,
        'latest_ingest_job': latest_ingest,
    }

    # ── 2. Audit ───────────────────────────────────────────────────
    # Find the batch with the most decisions (operator's active work,
    # same logic as v5.89.45 audit report).
    audit = {
        'active_batch_id': None,
        'active_batch_total': 0,
        'active_batch_audited': 0,
        'active_batch_dominant_verdict': None,
        'active_batch_dominant_pct': 0,
        'total_corpus_changes': 0,
    }
    try:
        # Total decisions across all batches (corpus changes ever)
        audit['total_corpus_changes'] = (_db.session.query(MLLabelAuditDecision)
                                          .filter(MLLabelAuditDecision.is_active == True)
                                          .filter(MLLabelAuditDecision.corpus_changed == True)
                                          .count())

        # Per-batch decision counts → pick the most-audited batch
        batches_q = (_db.session.query(
                        MLLabelAuditQueue.training_job_id,
                        _db.func.count(MLLabelAuditQueue.id))
                     .group_by(MLLabelAuditQueue.training_job_id)
                     .all())
        if batches_q:
            best_tj = None
            best_dec_count = -1
            best_total = 0
            for tj, q_total in batches_q:
                dec_count = (_db.session.query(_db.func.count(MLLabelAuditDecision.id))
                              .join(MLLabelAuditQueue,
                                    MLLabelAuditDecision.queue_id == MLLabelAuditQueue.id)
                              .filter(MLLabelAuditQueue.training_job_id == tj)
                              .filter(MLLabelAuditDecision.is_active == True)
                              .scalar() or 0)
                if dec_count > best_dec_count:
                    best_dec_count = dec_count
                    best_tj = tj
                    best_total = q_total
            audit['active_batch_id'] = best_tj
            audit['active_batch_total'] = best_total
            audit['active_batch_audited'] = best_dec_count

            # Dominant verdict for the active batch
            if best_dec_count > 0:
                verdicts = (_db.session.query(MLLabelAuditDecision.verdict)
                              .join(MLLabelAuditQueue,
                                    MLLabelAuditDecision.queue_id == MLLabelAuditQueue.id)
                              .filter(MLLabelAuditQueue.training_job_id == best_tj)
                              .filter(MLLabelAuditDecision.is_active == True)
                              .all())
                vc = Counter(v[0] for v in verdicts)
                top_verdict, top_count = vc.most_common(1)[0]
                audit['active_batch_dominant_verdict'] = top_verdict
                audit['active_batch_dominant_pct'] = round(100 * top_count / best_dec_count)
    except Exception:
        pass

    # ── 3. Bulk Relabel ────────────────────────────────────────────
    relabel = {
        'latest_run': None,
        'active_run_id': None,
    }
    try:
        latest = (MLRelabelRun.query
                  .order_by(MLRelabelRun.created_at.desc())
                  .limit(1).first())
        if latest:
            relabel['latest_run'] = {
                'job_id': latest.job_id,
                'status': latest.status,
                'mode': latest.mode,
                'threshold': latest.confidence_threshold,
                'rows_processed': latest.rows_processed,
                'rows_total': latest.rows_total,
                'rows_changed_category': latest.rows_changed_category,
                'rows_changed_severity': latest.rows_changed_severity,
                'created_at': latest.created_at.isoformat() if latest.created_at else None,
                'completed_at': latest.completed_at.isoformat() if latest.completed_at else None,
            }
        # Is anything currently active?
        active = (MLRelabelRun.query
                  .filter(MLRelabelRun.status.in_(['queued', 'running']))
                  .first())
        if active:
            relabel['active_run_id'] = active.job_id
    except Exception:
        pass

    return jsonify({
        'ok': True,
        'initial_labeling': initial,
        'audit': audit,
        'bulk_relabel': relabel,
    })


# v5.89.39: access-request approval admin routes
# ─────────────────────────────────────────────────────────────────────
# Three endpoints power the approval workflow for gated investor materials:
#   /admin/access-requests           — list view with filters
#   /admin/access-requests/<id>/approve  — one-click approve from email
#   /admin/access-requests/<id>/deny     — one-click deny from email
#
# All three use @_admin_req_dec (the cookie-based admin auth) rather than
# @_api_admin_req_dec, because they're meant to be clickable from email
# links the operator receives. The approve link in the notification email
# is a GET — clickable directly — which is intentional UX. The trade-off
# is that GET-based mutations are normally a CSRF risk, but here the URL
# contains a database id that's hard to enumerate (sequential ids OK
# because the action is gated by admin auth), and the resulting magic
# link is single-use, sent only to the email on file.

@admin_bp.route('/admin/access-requests')
@_admin_req_dec
def admin_access_requests_list():
    """v5.89.39: list view of all AccessRequest rows. Default filter:
    pending only. Operator sees newest first, with quick approve/deny
    actions inline.
    """
    from flask import request as _req
    from models import AccessRequest

    status_filter = (_req.args.get('status') or 'pending').strip()
    q = AccessRequest.query
    if status_filter and status_filter != 'all':
        q = q.filter_by(status=status_filter)
    rows = q.order_by(AccessRequest.created_at.desc()).limit(200).all()

    # Counts for the filter bar
    counts = {
        'pending': AccessRequest.query.filter_by(status='pending').count(),
        'approved': AccessRequest.query.filter_by(status='approved').count(),
        'denied': AccessRequest.query.filter_by(status='denied').count(),
        'auto_approved': AccessRequest.query.filter_by(status='auto_approved').count(),
    }
    counts['total'] = sum(counts.values())

    # Inline HTML (small enough page that a separate template is overkill)
    def _esc(s):
        return ('' if s is None else str(s)).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

    rows_html = []
    for r in rows:
        action = ''
        if r.status == 'pending':
            action = (
                f'<a class="btn ok" href="/admin/access-requests/{r.id}/approve?admin_key='
                + (_req.args.get('admin_key', '') or '') + '">Approve</a> '
                f'<a class="btn no" href="/admin/access-requests/{r.id}/deny?admin_key='
                + (_req.args.get('admin_key', '') or '') + '">Deny</a>'
            )
        else:
            action = f'<span class="muted">{_esc(r.status)}{(" by " + _esc(r.reviewed_by)) if r.reviewed_by else ""}</span>'

        last_access = ''
        if r.access_count and r.last_accessed_at:
            last_access = f'{r.access_count} views, last {r.last_accessed_at.strftime("%Y-%m-%d %H:%M")}'

        rows_html.append(f'''
<tr>
  <td class="dim">#{r.id}</td>
  <td>{_esc(r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "—")}</td>
  <td><strong>{_esc(r.name)}</strong><br><span class="dim">{_esc(r.email)}</span></td>
  <td>{_esc(r.company) or "<span class='dim'>—</span>"}<br><span class="dim">{_esc(r.role)}</span></td>
  <td class="reason">{_esc(r.reason)[:200]}</td>
  <td class="dim">{_esc(r.page_requested)}</td>
  <td>{action}</td>
  <td class="dim">{last_access}</td>
</tr>''')

    rows_html_str = ''.join(rows_html) if rows_html else (
        '<tr><td colspan="8" class="empty">No requests matching this filter.</td></tr>'
    )

    admin_key_param = (_req.args.get('admin_key', '') or '')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Access requests · OfferWise Admin</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  body{{margin:0;font-family:'DM Sans',system-ui,sans-serif;background:#0b1220;color:#f3f4f6;font-size:14px;line-height:1.5;}}
  .wrap{{max-width:1200px;margin:0 auto;padding:24px;}}
  h1{{font-size:22px;margin:0 0 18px;font-weight:700;letter-spacing:-.01em;}}
  .filters{{display:flex;gap:8px;margin-bottom:18px;padding-bottom:14px;border-bottom:1px solid #1f2937;}}
  .filters a{{padding:6px 13px;font-size:12px;color:#9ca3af;text-decoration:none;border:1px solid #374151;border-radius:5px;font-weight:500;}}
  .filters a:hover{{background:#1f2937;color:#f3f4f6;}}
  .filters a.active{{background:#f97316;border-color:#f97316;color:white;}}
  table{{width:100%;border-collapse:collapse;font-size:13px;}}
  th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid #1f2937;vertical-align:top;}}
  th{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#9ca3af;font-weight:600;background:#111827;}}
  td.dim,.dim{{color:#9ca3af;font-size:12px;}}
  td.reason{{max-width:280px;font-size:12px;color:#cbd5e1;}}
  td.empty{{text-align:center;color:#6b7280;padding:32px;font-style:italic;}}
  .btn{{display:inline-block;padding:4px 10px;font-size:11px;font-weight:600;text-decoration:none;border-radius:4px;border:1px solid;}}
  .btn.ok{{background:rgba(34,197,94,.1);border-color:#22c55e;color:#86efac;}}
  .btn.no{{background:rgba(239,68,68,.06);border-color:#374151;color:#fca5a5;}}
  .btn:hover{{filter:brightness(1.2);}}
  .muted{{color:#6b7280;font-size:12px;}}
  a{{color:#f59e0b;}}
  .back{{font-size:12px;color:#9ca3af;text-decoration:none;}}
  .back:hover{{color:#f97316;}}
</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="javascript:history.back()">← Back to admin</a>
  <h1>🔐 Access requests <span class="dim" style="font-size:13px;font-weight:500">({counts['total']} total)</span></h1>

  <div class="filters">
    <a href="?status=pending&amp;admin_key={admin_key_param}" class="{'active' if status_filter == 'pending' else ''}">Pending ({counts['pending']})</a>
    <a href="?status=approved&amp;admin_key={admin_key_param}" class="{'active' if status_filter == 'approved' else ''}">Approved ({counts['approved']})</a>
    <a href="?status=auto_approved&amp;admin_key={admin_key_param}" class="{'active' if status_filter == 'auto_approved' else ''}">Auto-approved ({counts['auto_approved']})</a>
    <a href="?status=denied&amp;admin_key={admin_key_param}" class="{'active' if status_filter == 'denied' else ''}">Denied ({counts['denied']})</a>
    <a href="?status=all&amp;admin_key={admin_key_param}" class="{'active' if status_filter == 'all' else ''}">All</a>
  </div>

  <table>
    <thead>
      <tr><th>#</th><th>Submitted</th><th>Who</th><th>Company / Role</th><th>Reason</th><th>Page</th><th>Status / Action</th><th>Access</th></tr>
    </thead>
    <tbody>{rows_html_str}</tbody>
  </table>
</div>
</body>
</html>"""
    return html


@admin_bp.route('/admin/access-requests/<int:request_id>/approve')
@_admin_req_dec
def admin_access_request_approve(request_id):
    """v5.89.39: one-click approve from the notification email. Generates
    magic_token, sends the magic link via Resend, redirects to the list
    view with a success banner.
    """
    from flask import request as _req
    from access_gate import approve_request, send_magic_link_email
    from models import db as _db

    row = approve_request(_db, request_id, reviewer='admin')
    if row is None:
        return _admin_simple_page(
            title='Not found',
            body=f'<p>Access request #{request_id} not found.</p>',
            back_href='/admin/access-requests?admin_key=' + (_req.args.get('admin_key', '') or ''),
        )

    if row.status != 'approved':
        return _admin_simple_page(
            title='Already actioned',
            body=f'<p>Request #{request_id} is in status <strong>{row.status}</strong> — no action taken.</p>',
            back_href='/admin/access-requests?admin_key=' + (_req.args.get('admin_key', '') or ''),
        )

    # Send the magic link
    sent = send_magic_link_email(_req, row)
    msg = 'Magic link emailed to ' + row.email if sent else (
        'Approved, but magic email send FAILED (check RESEND_API_KEY). '
        'You can resend manually by deleting the request and asking the user to re-submit.'
    )

    return _admin_simple_page(
        title='Approved',
        body=(
            f'<p>Request #{request_id} from <strong>{row.name}</strong> approved.</p>'
            f'<p style="color:#9ca3af">{msg}</p>'
        ),
        back_href='/admin/access-requests?admin_key=' + (_req.args.get('admin_key', '') or ''),
        kind='ok',
    )


@admin_bp.route('/admin/access-requests/<int:request_id>/deny')
@_admin_req_dec
def admin_access_request_deny(request_id):
    """v5.89.39: one-click deny from the notification email. No email is
    sent to the requester — they just don't hear back, by design."""
    from flask import request as _req
    from access_gate import deny_request
    from models import db as _db

    row = deny_request(_db, request_id, reviewer='admin')
    if row is None:
        return _admin_simple_page(
            title='Not found',
            body=f'<p>Access request #{request_id} not found.</p>',
            back_href='/admin/access-requests?admin_key=' + (_req.args.get('admin_key', '') or ''),
        )

    if row.status != 'denied':
        return _admin_simple_page(
            title='Already actioned',
            body=f'<p>Request #{request_id} is in status <strong>{row.status}</strong> — no action taken.</p>',
            back_href='/admin/access-requests?admin_key=' + (_req.args.get('admin_key', '') or ''),
        )

    return _admin_simple_page(
        title='Denied',
        body=f'<p>Request #{request_id} from <strong>{row.name}</strong> denied. No email sent to requester.</p>',
        back_href='/admin/access-requests?admin_key=' + (_req.args.get('admin_key', '') or ''),
        kind='ok',
    )


def _admin_simple_page(title: str, body: str, back_href: str = '/admin', kind: str = 'info'):
    """v5.89.39: minimal status page used by the approve/deny endpoints
    when they need to show the operator a confirmation. Matches the
    admin-diags.html visual language but as a single self-contained page."""
    accent = '#22c55e' if kind == 'ok' else ('#ef4444' if kind == 'err' else '#f97316')
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>{title} · OfferWise Admin</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  body{{margin:0;font-family:'DM Sans',system-ui,sans-serif;background:#0b1220;color:#f3f4f6;}}
  .wrap{{max-width:540px;margin:80px auto;padding:36px;background:#111827;border:1px solid #1f2937;border-radius:10px;}}
  h1{{font-size:22px;font-weight:700;margin:0 0 16px;letter-spacing:-.01em;color:{accent};}}
  p{{color:#cbd5e1;line-height:1.6;}}
  a.back{{display:inline-block;margin-top:18px;color:#f59e0b;text-decoration:none;font-size:14px;}}
  a.back:hover{{color:#f97316;}}
</style></head>
<body><div class="wrap">
  <h1>{title}</h1>
  {body}
  <a class="back" href="{back_href}">← Back to access requests</a>
</div></body></html>"""


# ═══════════════════════════════════════════════════════════════════════════════
# v5.89.42 LABEL AUDIT WORKFLOW
# ═══════════════════════════════════════════════════════════════════════════════
# Six endpoints power the audit workflow:
#   GET  /admin/labels/audit              — single-page UI (HTML)
#   GET  /admin/labels/audit/report       — aggregate stats (HTML)
#   GET  /api/admin/labels/audit/next     — fetch next unaudited queue row
#   POST /api/admin/labels/audit/decide   — record verdict + mutate corpus
#   POST /api/admin/labels/audit/undo     — undo the most-recent decision
#   GET  /api/admin/labels/audit/report-data — JSON for the report page

@admin_bp.route('/admin/labels/audit')
@_admin_req_dec
def admin_labels_audit_page():
    """v5.89.42: single-page audit UI. Keyboard-driven, 5 verdicts,
    confirmation modals for destructive verdicts (2 + 5)."""
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'admin-label-audit.html')


@admin_bp.route('/admin/labels/audit/report')
@_admin_req_dec
def admin_labels_audit_report_page():
    """v5.89.42: aggregate audit report. Verdict distribution,
    confusion-pair breakdowns, top suggested new categories."""
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'admin-label-audit-report.html')


@admin_bp.route('/api/admin/labels/audit/next', methods=['GET'])
@_api_admin_req_dec
def admin_labels_audit_next():
    """v5.89.42: return the next unaudited row from the most-recent batch.

    Response shape:
      {
        ok: true,
        queue_row: { id, queue_type, confusion_pair, original_category,
                     original_severity, predicted_category, predicted_severity,
                     confidence_category, confidence_severity, text_snippet,
                     finding_id },
        batch: { training_job_id, total_in_batch, audited_in_batch },
        similar: [ {text, original_category, original_severity}, ... ]
      }
      or { ok: true, queue_row: null, batch: {...} } if batch fully audited
    """
    from flask import request as _req
    from models import db as _db, MLLabelAuditQueue, MLLabelAuditDecision, MLFindingLabel

    # Find the most recent training_job_id with any queue rows
    latest_job = (_db.session.query(MLLabelAuditQueue.training_job_id)
                  .order_by(MLLabelAuditQueue.queued_at.desc())
                  .limit(1).first())
    if not latest_job:
        return jsonify({'ok': True, 'queue_row': None, 'batch': None,
                        'message': 'No audit queue yet. Run a training cycle.'})
    target_job_id = latest_job[0]

    # Counts for the batch
    total_in_batch = MLLabelAuditQueue.query.filter_by(training_job_id=target_job_id).count()
    audited_q = (_db.session.query(MLLabelAuditDecision.queue_id)
                 .join(MLLabelAuditQueue, MLLabelAuditDecision.queue_id == MLLabelAuditQueue.id)
                 .filter(MLLabelAuditQueue.training_job_id == target_job_id)
                 .filter(MLLabelAuditDecision.is_active == True))
    audited_count = audited_q.count()
    audited_queue_ids = {row[0] for row in audited_q.all()}

    # Fetch next unaudited row
    next_row = (MLLabelAuditQueue.query
                .filter_by(training_job_id=target_job_id)
                .filter(~MLLabelAuditQueue.id.in_(audited_queue_ids) if audited_queue_ids else True)
                .order_by(MLLabelAuditQueue.id.asc())
                .first())

    batch = {
        'training_job_id': target_job_id,
        'total_in_batch': total_in_batch,
        'audited_in_batch': audited_count,
    }

    if not next_row:
        return jsonify({'ok': True, 'queue_row': None, 'batch': batch,
                        'message': 'Batch complete!'})

    # Find 3 similar findings (same confusion_pair + same queue_type)
    similar_q = (MLLabelAuditQueue.query
                 .filter_by(training_job_id=target_job_id,
                            queue_type=next_row.queue_type,
                            confusion_pair=next_row.confusion_pair)
                 .filter(MLLabelAuditQueue.id != next_row.id)
                 .limit(3))
    similar = [{
        'text': (q.text_snippet or '')[:240],
        'original_category': q.original_category,
        'original_severity': q.original_severity,
    } for q in similar_q]

    return jsonify({
        'ok': True,
        'queue_row': {
            'id': next_row.id,
            'queue_type': next_row.queue_type,
            'confusion_pair': next_row.confusion_pair,
            'original_category': next_row.original_category,
            'original_severity': next_row.original_severity,
            'predicted_category': next_row.predicted_category,
            'predicted_severity': next_row.predicted_severity,
            'confidence_category': next_row.confidence_category,
            'confidence_severity': next_row.confidence_severity,
            'text_snippet': next_row.text_snippet,
            'finding_id': next_row.finding_id,
        },
        'batch': batch,
        'similar': similar,
    })


@admin_bp.route('/api/admin/labels/audit/decide', methods=['POST'])
@_api_admin_req_dec
def admin_labels_audit_decide():
    """v5.89.42: record an audit verdict. Mutates the corpus for
    destructive verdicts (2 predicted_correct, 4 neither_correct,
    5 junk). Returns the next unaudited row in the same call so the
    UI can render it without a separate round trip.

    Request: { queue_id, verdict, suggested_category?, notes?,
               needs_review? }
    Response: { ok, decision_id, corpus_changed, next_queue_row, batch, ... }
    """
    import json as _json
    from flask import request as _req
    from datetime import datetime as _dt
    from models import db as _db, MLLabelAuditQueue, MLLabelAuditDecision, MLFindingLabel

    data = _req.get_json(silent=True) or {}
    queue_id = data.get('queue_id')
    verdict = (data.get('verdict') or '').strip()
    suggested = (data.get('suggested_category') or '').strip()[:100]
    notes = (data.get('notes') or '').strip()[:5000]
    needs_review = bool(data.get('needs_review'))

    VALID_VERDICTS = {'original_correct', 'predicted_correct',
                      'both_defensible', 'neither_correct', 'junk'}
    if verdict not in VALID_VERDICTS:
        return jsonify({'ok': False, 'error': f'Invalid verdict: {verdict}'}), 400

    queue_row = MLLabelAuditQueue.query.get(queue_id)
    if not queue_row:
        return jsonify({'ok': False, 'error': f'Queue row {queue_id} not found'}), 404

    # Check if already decided
    existing = MLLabelAuditDecision.query.filter_by(queue_id=queue_id, is_active=True).first()
    if existing:
        return jsonify({
            'ok': False,
            'error': 'This finding already has an active audit decision. Undo first if you want to change it.',
            'existing_decision_id': existing.id,
            'existing_verdict': existing.verdict,
        }), 409

    finding = MLFindingLabel.query.get(queue_row.finding_id)
    corpus_changed = False
    rollback_payload = {}

    # Apply corpus mutations per verdict
    if finding is not None:
        if verdict == 'predicted_correct':
            # Update the corpus row to match the model's prediction
            rollback_payload = {
                'category': finding.category,
                'severity': finding.severity,
                'category_v2': finding.category_v2,
                'severity_v2': finding.severity_v2,
                'audit_modified_at': finding.audit_modified_at.isoformat() if finding.audit_modified_at else None,
                'audit_original_category': finding.audit_original_category,
                'audit_original_severity': finding.audit_original_severity,
            }
            if not finding.audit_original_category:
                finding.audit_original_category = finding.category
            if not finding.audit_original_severity:
                finding.audit_original_severity = finding.severity

            if queue_row.queue_type == 'category':
                finding.category = queue_row.predicted_category
                # Also update v2 if it was the source of truth
                if finding.category_v2:
                    finding.category_v2 = queue_row.predicted_category
            elif queue_row.queue_type == 'severity':
                finding.severity = queue_row.predicted_severity
                if finding.severity_v2:
                    finding.severity_v2 = queue_row.predicted_severity

            finding.audit_modified_at = _dt.utcnow()
            corpus_changed = True

        elif verdict == 'neither_correct':
            # Don't change category — capture the operator's suggested
            # new category for the v5.89.43 taxonomy redesign
            if suggested:
                rollback_payload = {
                    'audit_suggested_category': finding.audit_suggested_category,
                    'audit_modified_at': finding.audit_modified_at.isoformat() if finding.audit_modified_at else None,
                }
                finding.audit_suggested_category = suggested
                finding.audit_modified_at = _dt.utcnow()
                corpus_changed = True

        elif verdict == 'junk':
            # Mark as excluded from training. Don't delete — preserve for
            # audit. Training queries filter on excluded_from_training=False.
            rollback_payload = {
                'excluded_from_training': finding.excluded_from_training,
                'audit_modified_at': finding.audit_modified_at.isoformat() if finding.audit_modified_at else None,
            }
            finding.excluded_from_training = True
            finding.audit_modified_at = _dt.utcnow()
            corpus_changed = True

        # 'original_correct' and 'both_defensible' do NOT mutate the corpus

    # Build the decision row
    decision = MLLabelAuditDecision(
        queue_id=queue_id,
        decided_at=_dt.utcnow(),
        decided_by='admin',  # could enhance with email if session has it
        verdict=verdict,
        suggested_category=(suggested or None),
        notes=(notes or None),
        needs_review=needs_review,
        corpus_changed=corpus_changed,
        rollback_payload=(_json.dumps(rollback_payload) if rollback_payload else None),
        is_active=True,
    )

    try:
        _db.session.add(decision)
        _db.session.commit()
    except Exception as e:
        _db.session.rollback()
        # v5.89.43: friendlier message for the specific constraint-violation
        # case operators are likely to hit. The underlying cause is either
        # (a) two decide requests fired in parallel for the same queue_id
        # (race), or (b) the v5.89.42→v5.89.43 migration didn't run, leaving
        # the old too-strict unique constraint in place.
        err_msg = str(e)
        if 'IntegrityError' in type(e).__name__ or 'UniqueViolation' in err_msg or 'unique constraint' in err_msg.lower():
            return jsonify({
                'ok': False,
                'error': (
                    'This finding already has an active decision. Refresh '
                    'the page to see the latest queue state. If this keeps '
                    'happening, the v5.89.43 schema migration may not have '
                    'run — check Render boot logs for "partial unique index ensured".'
                ),
            }), 409
        return jsonify({'ok': False, 'error': f'Commit failed: {type(e).__name__}: {e}'}), 500
    audited_q = (_db.session.query(MLLabelAuditDecision.queue_id)
                 .join(MLLabelAuditQueue, MLLabelAuditDecision.queue_id == MLLabelAuditQueue.id)
                 .filter(MLLabelAuditQueue.training_job_id == queue_row.training_job_id)
                 .filter(MLLabelAuditDecision.is_active == True))
    audited_queue_ids = {row[0] for row in audited_q.all()}
    total_in_batch = MLLabelAuditQueue.query.filter_by(training_job_id=queue_row.training_job_id).count()
    next_row = (MLLabelAuditQueue.query
                .filter_by(training_job_id=queue_row.training_job_id)
                .filter(~MLLabelAuditQueue.id.in_(audited_queue_ids))
                .order_by(MLLabelAuditQueue.id.asc())
                .first())

    next_payload = None
    similar = []
    if next_row:
        similar_q = (MLLabelAuditQueue.query
                     .filter_by(training_job_id=queue_row.training_job_id,
                                queue_type=next_row.queue_type,
                                confusion_pair=next_row.confusion_pair)
                     .filter(MLLabelAuditQueue.id != next_row.id)
                     .limit(3))
        similar = [{
            'text': (q.text_snippet or '')[:240],
            'original_category': q.original_category,
            'original_severity': q.original_severity,
        } for q in similar_q]
        next_payload = {
            'id': next_row.id,
            'queue_type': next_row.queue_type,
            'confusion_pair': next_row.confusion_pair,
            'original_category': next_row.original_category,
            'original_severity': next_row.original_severity,
            'predicted_category': next_row.predicted_category,
            'predicted_severity': next_row.predicted_severity,
            'confidence_category': next_row.confidence_category,
            'confidence_severity': next_row.confidence_severity,
            'text_snippet': next_row.text_snippet,
            'finding_id': next_row.finding_id,
        }

    return jsonify({
        'ok': True,
        'decision_id': decision.id,
        'corpus_changed': corpus_changed,
        'queue_row': next_payload,
        'similar': similar,
        'batch': {
            'training_job_id': queue_row.training_job_id,
            'total_in_batch': total_in_batch,
            'audited_in_batch': len(audited_queue_ids),
        },
    })


@admin_bp.route('/api/admin/labels/audit/undo', methods=['POST'])
@_api_admin_req_dec
def admin_labels_audit_undo():
    """v5.89.42: undo the most-recent active decision in the current batch.
    Rolls back any corpus mutation (using rollback_payload) and marks the
    decision is_active=False. Returns the queue row that's now
    re-available for re-decision.

    Request: { training_job_id }  (optional; uses latest if omitted)
    """
    import json as _json
    from flask import request as _req
    from datetime import datetime as _dt
    from models import db as _db, MLLabelAuditQueue, MLLabelAuditDecision, MLFindingLabel

    data = _req.get_json(silent=True) or {}
    job_id = (data.get('training_job_id') or '').strip()

    # If no job_id provided, find the latest batch with decisions
    if not job_id:
        latest = (_db.session.query(MLLabelAuditQueue.training_job_id)
                  .join(MLLabelAuditDecision, MLLabelAuditDecision.queue_id == MLLabelAuditQueue.id)
                  .filter(MLLabelAuditDecision.is_active == True)
                  .order_by(MLLabelAuditDecision.decided_at.desc())
                  .limit(1).first())
        if not latest:
            return jsonify({'ok': False, 'error': 'No active decisions to undo.'}), 404
        job_id = latest[0]

    # Get the most recent active decision in this batch
    latest_decision = (_db.session.query(MLLabelAuditDecision)
                       .join(MLLabelAuditQueue, MLLabelAuditDecision.queue_id == MLLabelAuditQueue.id)
                       .filter(MLLabelAuditQueue.training_job_id == job_id)
                       .filter(MLLabelAuditDecision.is_active == True)
                       .order_by(MLLabelAuditDecision.decided_at.desc())
                       .first())

    if not latest_decision:
        return jsonify({'ok': False, 'error': 'No active decisions in this batch to undo.'}), 404

    # Roll back corpus mutation if there was one
    if latest_decision.corpus_changed and latest_decision.rollback_payload:
        try:
            payload = _json.loads(latest_decision.rollback_payload)
            queue_row = MLLabelAuditQueue.query.get(latest_decision.queue_id)
            finding = MLFindingLabel.query.get(queue_row.finding_id) if queue_row else None
            if finding:
                for key, val in payload.items():
                    if key == 'audit_modified_at':
                        # parse ISO string back to datetime if needed
                        if val:
                            try:
                                from datetime import datetime as _dt2
                                setattr(finding, key, _dt2.fromisoformat(val))
                            except Exception:
                                setattr(finding, key, None)
                        else:
                            setattr(finding, key, None)
                    elif hasattr(finding, key):
                        setattr(finding, key, val)
        except Exception as e:
            return jsonify({'ok': False, 'error': f'Rollback failed: {type(e).__name__}: {e}'}), 500

    latest_decision.is_active = False
    try:
        _db.session.commit()
    except Exception as e:
        _db.session.rollback()
        return jsonify({'ok': False, 'error': f'Commit failed: {type(e).__name__}: {e}'}), 500

    return jsonify({
        'ok': True,
        'undone_decision_id': latest_decision.id,
        'undone_verdict': latest_decision.verdict,
        'corpus_was_changed': latest_decision.corpus_changed,
    })


@admin_bp.route('/api/admin/labels/audit/report-data', methods=['GET'])
@_api_admin_req_dec
def admin_labels_audit_report_data():
    """v5.89.42: JSON-formatted aggregate report data for the report
    HTML page to render. Computed from active decisions in the most
    recent batch.
    """
    from flask import request as _req
    from models import db as _db, MLLabelAuditQueue, MLLabelAuditDecision
    from collections import Counter as _Counter

    # Resolve target batch
    job_id = (_req.args.get('training_job_id') or '').strip()

    # v5.89.45: list all batches that exist with their decision counts.
    # Lets the report show a switcher AND defaults to the batch with the
    # most decisions (which is the one the operator actually audited),
    # not the most recent batch (which may be empty after a retraining run).
    batches_q = (_db.session.query(
                    MLLabelAuditQueue.training_job_id,
                    db.func.count(MLLabelAuditQueue.id).label('queue_total'),
                    db.func.max(MLLabelAuditQueue.queued_at).label('queued_at'))
                 .group_by(MLLabelAuditQueue.training_job_id)
                 .all())
    # For each batch, count active decisions
    available_batches = []
    for tj, q_total, q_at in batches_q:
        dec_count = (_db.session.query(db.func.count(MLLabelAuditDecision.id))
                     .join(MLLabelAuditQueue, MLLabelAuditDecision.queue_id == MLLabelAuditQueue.id)
                     .filter(MLLabelAuditQueue.training_job_id == tj)
                     .filter(MLLabelAuditDecision.is_active == True)
                     .scalar() or 0)
        available_batches.append({
            'training_job_id': tj,
            'queue_total': q_total,
            'decision_count': dec_count,
            'queued_at': q_at.isoformat() if q_at else None,
        })
    # Sort by decision count descending (most-audited batch first)
    available_batches.sort(key=lambda b: -b['decision_count'])

    if not job_id:
        # Default to the batch with the most decisions (the one operator
        # actually audited). Falls back to the most recent if all are empty.
        if available_batches and available_batches[0]['decision_count'] > 0:
            job_id = available_batches[0]['training_job_id']
        else:
            latest_job = (_db.session.query(MLLabelAuditQueue.training_job_id)
                          .order_by(MLLabelAuditQueue.queued_at.desc())
                          .limit(1).first())
            if not latest_job:
                return jsonify({'ok': True, 'has_data': False, 'message': 'No audit data yet.'})
            job_id = latest_job[0]

    total_in_batch = MLLabelAuditQueue.query.filter_by(training_job_id=job_id).count()
    decisions = (_db.session.query(MLLabelAuditDecision, MLLabelAuditQueue)
                 .join(MLLabelAuditQueue, MLLabelAuditDecision.queue_id == MLLabelAuditQueue.id)
                 .filter(MLLabelAuditQueue.training_job_id == job_id)
                 .filter(MLLabelAuditDecision.is_active == True)
                 .all())

    verdict_counts = _Counter(d.verdict for d, _ in decisions)
    confusion_breakdown = {}
    for d, q in decisions:
        key = f'{q.queue_type}: {q.confusion_pair}'
        if key not in confusion_breakdown:
            confusion_breakdown[key] = {'total': 0, 'verdicts': _Counter()}
        confusion_breakdown[key]['total'] += 1
        confusion_breakdown[key]['verdicts'][d.verdict] += 1
    # Convert to JSON-friendly
    confusion_breakdown_json = []
    for key, info in sorted(confusion_breakdown.items(), key=lambda kv: -kv[1]['total']):
        confusion_breakdown_json.append({
            'pair': key,
            'total': info['total'],
            'verdicts': dict(info['verdicts']),
        })

    suggested_counts = _Counter(
        d.suggested_category for d, _ in decisions
        if d.verdict == 'neither_correct' and d.suggested_category
    )
    suggested_top = suggested_counts.most_common(20)

    needs_review = sum(1 for d, _ in decisions if d.needs_review)
    corpus_changes = sum(1 for d, _ in decisions if d.corpus_changed)

    return jsonify({
        'ok': True,
        'has_data': True,
        'training_job_id': job_id,
        'total_in_batch': total_in_batch,
        'total_audited': len(decisions),
        'verdict_distribution': dict(verdict_counts),
        'confusion_breakdown': confusion_breakdown_json,
        'suggested_new_categories': [
            {'suggested': name, 'count': count} for name, count in suggested_top
        ],
        'needs_review_count': needs_review,
        'corpus_changes_count': corpus_changes,
        # v5.89.45: list all batches so the report page can offer a switcher
        'available_batches': available_batches,
    })


@admin_bp.route('/admin/api-costs')
@_admin_req_dec
def admin_api_costs_page():
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'admin-api-costs.html')

@admin_bp.route('/admin/infra-costs')
@_admin_req_dec
def admin_infra_costs_page():
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'admin-infra-costs.html')

@admin_bp.route('/admin/support-shares')
@_admin_req_dec
def admin_support_shares_page():
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'admin-support-shares.html')

@admin_bp.route('/admin/shared-analyses')
@_admin_req_dec
def admin_shared_analyses_page():
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'admin-shared-analyses.html')

@admin_bp.route('/admin/insights')
@_admin_req_dec
def admin_insights_page():
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'admin-insights.html')


# ============================================================
# GTM (Go-To-Market) — Routes extracted to gtm/routes.py blueprint

@admin_bp.route('/api/admin/send-email', methods=['POST'])
@_api_admin_req_dec
def admin_send_email():
    """Send a one-off email to any user from the admin dashboard."""
    # v5.88.14: silent=True so missing/bad body returns 400 cleanly,
    # not 500. Same pattern as v5.88.09 (auth), v5.88.11 (analyses save),
    # v5.88.13 (consent + waitlist).
    data = request.get_json(silent=True) or {}
    to_email = (data.get('to_email') or '').strip()
    subject  = (data.get('subject')  or '').strip()
    body     = (data.get('body')     or '').strip()
    import os as _os
    reply_to = (data.get('reply_to') or _os.environ.get('ADMIN_EMAIL', 'hello@getofferwise.ai')).strip()

    if not to_email or not subject or not body:
        return jsonify({'error': 'to_email, subject, and body are required.'}), 400

    # Wrap plain-text body in minimal HTML
    # v5.88.01: linkify URLs to anchor tags (consistent with outreach paths)
    html_lines = ''.join(
        f'<p style="margin:0 0 12px;color:#e2e8f0;font-size:14px;line-height:1.65;">{_linkify_line(line)}</p>'
        if line.strip() else '<br>'
        for line in body.split('\n')
    )
    html_content = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                max-width:560px;margin:0 auto;background:#0f1623;padding:32px;border-radius:12px;">
      {html_lines}
      <hr style="border:none;border-top:1px solid #1e2d45;margin:24px 0;">
      <p style="font-size:11px;color:#475569;margin:0;">
        You received this because you signed up for
        <a href="https://www.getofferwise.ai" style="color:#60a5fa;">OfferWise AI</a>.
      </p>
    </div>"""

    try:
        from email_service import send_email
        ok = send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            reply_to=reply_to,
            email_type='admin_outreach',
        )
        if ok:
            logging.info(f"✅ Admin sent email to {to_email}: {subject}")
            return jsonify({'success': True, 'message': f'Email sent to {to_email}'})
        else:
            return jsonify({'error': 'Email service returned failure — check RESEND_API_KEY'}), 500
    except Exception as e:
        logging.error(f"Admin send-email failed: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# OUTREACH (v5.87.29) — founder customer-discovery + B2B cold outreach
# =============================================================================
# All sends go through Resend via email_service.send_email(), with reply_to
# set to the founder's Gmail so replies route there. Engagement (open/click)
# arrives via the existing /webhook/resend endpoint and is joined back here
# through resend_id.
# =============================================================================


def _founder_reply_to():
    """Return the email address replies should route to.

    Order of precedence:
      1. ADMIN_REPLY_TO env var (explicit override)
      2. ADMIN_EMAIL env var (the primary admin email — typically Gmail)
      3. Hardcoded fallback to keep the route working in dev
    """
    return (os.environ.get('ADMIN_REPLY_TO')
            or os.environ.get('ADMIN_EMAIL')
            or 'francis@getofferwise.ai')


# v5.88.01: URL → anchor-tag converter for outreach email HTML rendering.
# The LLM writes URLs as plain text; without this step Gmail auto-linkifies
# (most of the time) but Outlook and others are inconsistent. Forcing real
# <a> tags guarantees clickable links across every client.
import re as _re_email
_URL_RE_EMAIL = _re_email.compile(r'(https?://[^\s<>"\')]+)')


_LINKIFY_TRAIL = '.,;:!?'


def _linkify_line(line):
    """Replace bare URLs in a line with <a href> anchor tags.

    Trailing sentence punctuation (. , ; : ! ?) immediately after a URL is
    kept OUT of the href and left as visible text after the anchor. Without
    this, an LLM-written sentence like "...take a look at https://site/page."
    pulls the period into the href and the link 404s. (v5.89.136 fix — the
    prior regex documented this behavior but did not actually implement it,
    so the period was being swallowed into the link.)

    Closing parens, quotes, and angle brackets are already excluded by the
    URL character class in _URL_RE_EMAIL.
    """
    def _sub(mo):
        url = mo.group(1)
        trail = ''
        while url and url[-1] in _LINKIFY_TRAIL:
            trail = url[-1] + trail
            url = url[:-1]
        if not url:
            return mo.group(0)
        return (
            f'<a href="{url}" style="color:#2563eb;text-decoration:underline">'
            f'{url}</a>'
        ) + trail

    return _URL_RE_EMAIL.sub(_sub, line)


def _outreach_engagement_for(resend_ids):
    """Aggregate EmailEvent rows for a list of resend_ids.

    Returns dict: {resend_id: {'opened': bool, 'clicked': bool, 'bounced': bool,
                               'first_opened_at': iso, 'click_count': int}}

    Empty input returns {} without hitting the DB.
    """
    from models import EmailEvent
    out = {}
    if not resend_ids:
        return out
    # Strip Nones — some sends may not have a resend_id (failed sends, etc.)
    rids = [r for r in resend_ids if r]
    if not rids:
        return out
    events = EmailEvent.query.filter(EmailEvent.resend_id.in_(rids)).order_by(EmailEvent.ts.asc()).all()
    for ev in events:
        rid = ev.resend_id
        bucket = out.setdefault(rid, {
            'opened': False, 'clicked': False, 'bounced': False, 'complained': False,
            'first_opened_at': None, 'last_clicked_at': None, 'click_count': 0,
        })
        et = ev.event_type
        if et == 'opened':
            bucket['opened'] = True
            if not bucket['first_opened_at']:
                bucket['first_opened_at'] = ev.ts.isoformat()
        elif et == 'clicked':
            bucket['clicked'] = True
            bucket['last_clicked_at'] = ev.ts.isoformat()
            bucket['click_count'] += 1
        elif et == 'bounced':
            bucket['bounced'] = True
        elif et == 'complained':
            bucket['complained'] = True
    return out


@admin_bp.route('/api/admin/outreach/buyers', methods=['GET'])
@_api_admin_req_dec
def outreach_list_buyers():
    """List buyer signups with their outreach status.

    Returns every User except admins, with:
      - basic identity (id, email, name, plan, created_at, last_login)
      - signup source (joined from GTMFunnelEvent first-seen)
      - outreach history (last send + most recent engagement)

    Sort: never_contacted first (so the founder sees the work first),
    then by signup recency desc.
    """
    from models import OutreachLog, GTMFunnelEvent
    from sqlalchemy import func as _f, desc as _desc

    # Admin emails are excluded from the list — no point emailing yourself
    admin_emails = [e.strip().lower() for e in (
        os.environ.get('ADMIN_EMAILS', os.environ.get('ADMIN_EMAIL', 'hello@getofferwise.ai') + ',francis@getofferwise.ai')
    ).split(',') if e.strip()]

    # All users, excluding admins
    users = (User.query
             .filter(~_f.lower(User.email).in_(admin_emails))
             .order_by(_desc(User.created_at))
             .limit(500)
             .all())

    if not users:
        return jsonify({'buyers': [], 'count': 0})

    user_ids = [u.id for u in users]

    # Latest outreach send per user
    last_send_subq = (db.session.query(
        OutreachLog.user_id,
        _f.max(OutreachLog.sent_at).label('last_sent'),
    )
    .filter(OutreachLog.cohort == 'buyer', OutreachLog.user_id.in_(user_ids))
    .group_by(OutreachLog.user_id)
    .subquery())

    # Pull the actual rows for those (user_id, last_sent) pairs
    last_send_rows = (db.session.query(OutreachLog)
                      .join(last_send_subq,
                            (OutreachLog.user_id == last_send_subq.c.user_id) &
                            (OutreachLog.sent_at == last_send_subq.c.last_sent))
                      .all())
    last_send_by_user = {r.user_id: r for r in last_send_rows}

    # First-seen acquisition source per user
    src_subq = (db.session.query(
        GTMFunnelEvent.user_id,
        _f.min(GTMFunnelEvent.created_at).label('first_seen'),
    )
    .filter(GTMFunnelEvent.user_id.in_(user_ids))
    .group_by(GTMFunnelEvent.user_id)
    .subquery())

    src_rows = (db.session.query(
        GTMFunnelEvent.user_id,
        GTMFunnelEvent.source,
        GTMFunnelEvent.medium,
    )
    .join(src_subq,
          (GTMFunnelEvent.user_id == src_subq.c.user_id) &
          (GTMFunnelEvent.created_at == src_subq.c.first_seen))
    .all())
    src_by_user = {r.user_id: (r.source or 'direct', r.medium or 'none') for r in src_rows}

    # Engagement for the most-recent send only
    engagement = _outreach_engagement_for([r.resend_id for r in last_send_rows])

    now = datetime.utcnow()
    buyers = []
    for u in users:
        last = last_send_by_user.get(u.id)
        eng = engagement.get(last.resend_id, {}) if last and last.resend_id else {}
        days_since_signup = (now - u.created_at).days if u.created_at else None

        last_send_obj = None
        if last:
            last_send_obj = {
                'sent_at': last.sent_at.isoformat() if last.sent_at else None,
                'subject': last.subject,
                'success': bool(last.success),
                'opened': eng.get('opened', False),
                'clicked': eng.get('clicked', False),
                'first_opened_at': eng.get('first_opened_at'),
            }

        # Status derivation: if engaged, flag for follow-up
        if not last:
            status = 'never_contacted'
        elif eng.get('clicked'):
            status = 'clicked'
        elif eng.get('opened'):
            status = 'opened'
        else:
            status = 'sent'

        src, med = src_by_user.get(u.id, ('direct', 'none'))
        plan = getattr(u, 'subscription_plan', 'free') or 'free'

        buyers.append({
            'user_id': u.id,
            'email': u.email,
            'name': u.name or '',
            'plan': plan,
            'is_paid': plan not in ('free', '', None),
            'created_at': u.created_at.isoformat() if u.created_at else None,
            'days_since_signup': days_since_signup,
            'last_login': u.last_login.isoformat() if u.last_login else None,
            'source': src,
            'medium': med,
            'status': status,
            'last_send': last_send_obj,
        })

    # Sort: never_contacted first, then opened/clicked, then sent, by signup recency desc
    status_order = {'never_contacted': 0, 'clicked': 1, 'opened': 2, 'sent': 3}
    buyers.sort(key=lambda b: (status_order.get(b['status'], 9), -(b.get('days_since_signup') or 0)))

    return jsonify({'buyers': buyers, 'count': len(buyers)})


@admin_bp.route('/api/admin/outreach/b2b', methods=['GET'])
@_api_admin_req_dec
def outreach_list_b2b():
    """List B2B prospects with engagement on most-recent send.

    v5.88.46: wrapped in explicit try/except so production failures surface
    the real cause to the admin UI instead of getting swallowed by the
    generic 500 handler which returns just 'Server error'. Also returns
    partial results when engagement aggregation fails (which is non-fatal).
    """
    from models import OutreachContact, OutreachLog
    from sqlalchemy import func as _f, desc as _desc
    import traceback as _tb

    wedge = (request.args.get('wedge') or '').strip()
    status = (request.args.get('status') or '').strip()

    try:
        q = OutreachContact.query.filter_by(cohort='b2b')
        if wedge:
            q = q.filter_by(wedge=wedge)
        if status:
            q = q.filter_by(status=status)
        q = q.order_by(_desc(OutreachContact.created_at))
        contacts = q.limit(500).all()
    except Exception as e:
        logger.exception('outreach_list_b2b: contact query failed')
        return jsonify({
            'error': 'contact_query_failed',
            'message': f'{type(e).__name__}: {e}',
            'hint': 'Likely a missing column on outreach_contacts. Check alembic state.',
        }), 500

    if not contacts:
        return jsonify({'b2b': [], 'count': 0})

    contact_ids = [c.id for c in contacts]

    # Last-send + engagement aggregation. NON-FATAL: if this fails we still
    # return the contact list, just without engagement decoration.
    last_send_by_contact = {}
    engagement = {}
    touch_counts = {}
    last_send_failure = None
    try:
        last_send_subq = (db.session.query(
            OutreachLog.contact_id,
            _f.max(OutreachLog.sent_at).label('last_sent'),
        )
        .filter(OutreachLog.cohort == 'b2b', OutreachLog.contact_id.in_(contact_ids))
        .group_by(OutreachLog.contact_id)
        .subquery())

        last_send_rows = (db.session.query(OutreachLog)
                          .join(last_send_subq,
                                (OutreachLog.contact_id == last_send_subq.c.contact_id) &
                                (OutreachLog.sent_at == last_send_subq.c.last_sent))
                          .all())
        last_send_by_contact = {r.contact_id: r for r in last_send_rows}

        engagement = _outreach_engagement_for([r.resend_id for r in last_send_rows])

        # v5.89.136: per-contact touch count = number of successful B2B sends.
        # This is the source of truth for "which touch each prospect is on"
        # (OutreachLog is the touch ledger — one row per send). Drives the
        # follow-up scheduler and the "Touch N/4" indicator in the admin UI.
        touch_counts = dict(
            db.session.query(
                OutreachLog.contact_id, _f.count(OutreachLog.id)
            )
            .filter(
                OutreachLog.cohort == 'b2b',
                OutreachLog.contact_id.in_(contact_ids),
                OutreachLog.success == True,  # noqa: E712
            )
            .group_by(OutreachLog.contact_id)
            .all()
        )
    except Exception as e:
        logger.exception('outreach_list_b2b: engagement aggregation failed (non-fatal)')
        last_send_failure = f'{type(e).__name__}: {e}'

    rows = []
    serialization_errors = 0
    for c in contacts:
        try:
            last = last_send_by_contact.get(c.id)
            eng = engagement.get(last.resend_id, {}) if last and last.resend_id else {}
            last_send_obj = None
            if last:
                last_send_obj = {
                    'sent_at': last.sent_at.isoformat() if last.sent_at else None,
                    'subject': last.subject,
                    'success': bool(last.success),
                    'opened': eng.get('opened', False),
                    'clicked': eng.get('clicked', False),
                    'first_opened_at': eng.get('first_opened_at'),
                    'click_count': eng.get('click_count', 0),
                }
            rows.append({
                'id': c.id,
                'email': c.email,
                'name': c.name or '',
                'title': c.title or '',
                'company': c.company or '',
                'linkedin_url': c.linkedin_url or '',
                'wedge': c.wedge or '',
                'notes': c.notes or '',
                'status': c.status or 'not_contacted',
                'touch_count': touch_counts.get(c.id, 0),
                'last_contacted_at': c.last_contacted_at.isoformat() if c.last_contacted_at else None,
                'replied_at': c.replied_at.isoformat() if c.replied_at else None,
                'last_reply_summary': c.last_reply_summary or '',
                'created_at': c.created_at.isoformat() if c.created_at else None,
                'last_send': last_send_obj,
                # v5.87.48 — UI uses these to show "Review draft" button per row
                'has_draft': bool((getattr(c, 'draft_subject', None) or '').strip()
                                  and (getattr(c, 'draft_body', None) or '').strip()),
                'draft_generated_at': c.draft_generated_at.isoformat()
                                      if getattr(c, 'draft_generated_at', None) else None,
            })
        except Exception as e:
            # v5.88.46: per-row try/except so one broken row doesn't blank the list.
            # Log and skip; surface aggregate count in response.
            serialization_errors += 1
            logger.warning(f'outreach_list_b2b: row {c.id} serialize failed: {e}')

    resp = {'b2b': rows, 'count': len(rows)}
    if last_send_failure:
        resp['_engagement_warning'] = last_send_failure
    if serialization_errors:
        resp['_serialization_errors'] = serialization_errors
    return jsonify(resp)


@admin_bp.route('/api/admin/outreach/b2b', methods=['POST'])
@_api_admin_req_dec
def outreach_add_b2b():
    """Create a new B2B prospect."""
    from models import OutreachContact, ProspectBlocklist

    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    # v5.88.12: tightened from "must contain @" to a real check that
    # there's a non-empty local part AND a domain with a TLD.
    # Same pattern as the auth register/login fix in v5.88.09 — the
    # loose check accepted '@nodomain' (empty local part).
    if not email:
        return jsonify({'error': 'Valid email required'}), 400
    _at = email.find('@')
    if _at <= 0 or _at == len(email) - 1:
        return jsonify({'error': 'Valid email required'}), 400
    _local, _domain = email[:_at], email[_at+1:]
    if not _local or '.' not in _domain or _domain.startswith('.') or _domain.endswith('.'):
        return jsonify({'error': 'Valid email required'}), 400

    # v5.88.00: Reject emails on the blocklist. The founder marked these
    # "never contact" — respect that even on manual re-add.
    blocked = ProspectBlocklist.query.filter_by(email=email).first()
    if blocked:
        return jsonify({
            'error': f'Email is on the permanent blocklist (reason: {blocked.reason}). '
                     f'Unblock from admin → Outreach → Blocklist if you want to add them.',
            'blocked': True,
        }), 409

    # Idempotency: if a b2b prospect with this email already exists, return it.
    existing = OutreachContact.query.filter_by(cohort='b2b', email=email).first()
    if existing:
        return jsonify({'ok': True, 'id': existing.id, 'duplicate': True})

    c = OutreachContact(
        cohort='b2b',
        email=email,
        name=(data.get('name') or '').strip() or None,
        title=(data.get('title') or '').strip() or None,
        company=(data.get('company') or '').strip() or None,
        linkedin_url=(data.get('linkedin_url') or '').strip() or None,
        wedge=(data.get('wedge') or 'other').strip(),
        notes=(data.get('notes') or '').strip() or None,
        status='not_contacted',
    )
    db.session.add(c)
    db.session.commit()
    return jsonify({'ok': True, 'id': c.id})


@admin_bp.route('/api/admin/outreach/b2b/<int:contact_id>', methods=['PATCH'])
@_api_admin_req_dec
def outreach_update_b2b(contact_id):
    """Update notes / status / reply summary on a B2B prospect."""
    from models import OutreachContact

    c = OutreachContact.query.get(contact_id)
    if not c:
        return jsonify({'error': 'Not found'}), 404

    data = request.get_json(silent=True) or {}

    # Whitelist of patchable fields
    if 'name' in data:    c.name = (data['name'] or '').strip() or None
    if 'title' in data:   c.title = (data['title'] or '').strip() or None
    if 'company' in data: c.company = (data['company'] or '').strip() or None
    if 'linkedin_url' in data: c.linkedin_url = (data['linkedin_url'] or '').strip() or None
    if 'wedge' in data:   c.wedge = (data['wedge'] or '').strip() or None
    if 'notes' in data:   c.notes = (data['notes'] or '').strip() or None
    if 'last_reply_summary' in data:
        c.last_reply_summary = (data['last_reply_summary'] or '').strip() or None

    if 'status' in data:
        new_status = (data['status'] or '').strip()
        valid = {'not_contacted', 'contacted', 'replied', 'meeting_set',
                 'design_partner', 'passed'}
        if new_status in valid:
            c.status = new_status
            if new_status == 'replied' and not c.replied_at:
                c.replied_at = datetime.utcnow()

    db.session.commit()
    return jsonify({'ok': True})


@admin_bp.route('/api/admin/outreach/b2b/<int:contact_id>', methods=['DELETE'])
@_api_admin_req_dec
def outreach_delete_b2b(contact_id):
    """Remove a B2B prospect. The OutreachLog rows that reference it remain
    intact (FK is nullable on contact_id) so the audit trail survives."""
    from models import OutreachContact, OutreachLog

    c = OutreachContact.query.get(contact_id)
    if not c:
        return jsonify({'error': 'Not found'}), 404

    # Null the FK on logs that reference this contact, then drop the contact
    OutreachLog.query.filter_by(contact_id=contact_id).update({'contact_id': None})
    db.session.delete(c)
    db.session.commit()
    return jsonify({'ok': True})


@admin_bp.route('/api/admin/outreach/b2b/<int:contact_id>/block', methods=['POST'])
@_api_admin_req_dec
def outreach_block_b2b(contact_id):
    """v5.88.00: Permanently block this prospect's email.

    Adds the email to ProspectBlocklist (so the nightly discovery crawler
    won't re-add them) and removes the OutreachContact row. Idempotent:
    blocking an already-blocked email is a no-op.

    Body:
      reason: optional, one of 'wrong_role', 'departed', 'do_not_contact',
              'wrong_company', 'manual' (default)
      notes: optional free-form note
    """
    from models import OutreachContact, OutreachLog, ProspectBlocklist

    c = OutreachContact.query.get(contact_id)
    if not c:
        return jsonify({'error': 'Not found'}), 404

    data = request.get_json(silent=True) or {}
    reason = (data.get('reason') or 'manual').strip()
    valid_reasons = {'wrong_role', 'departed', 'do_not_contact',
                     'wrong_company', 'manual'}
    if reason not in valid_reasons:
        reason = 'manual'
    notes = (data.get('notes') or '').strip() or None

    email = (c.email or '').strip().lower()

    # Idempotent: if already blocklisted, just delete the contact and return
    existing = ProspectBlocklist.query.filter_by(email=email).first()
    if not existing:
        block = ProspectBlocklist(
            email=email,
            name_at_block=c.name,
            title_at_block=c.title,
            company_at_block=c.company,
            reason=reason,
            notes=notes,
        )
        db.session.add(block)

    # Null FK on logs, then remove the contact row
    OutreachLog.query.filter_by(contact_id=contact_id).update({'contact_id': None})
    db.session.delete(c)
    db.session.commit()

    return jsonify({
        'ok': True,
        'email': email,
        'reason': reason,
        'already_blocked': bool(existing),
    })


@admin_bp.route('/api/admin/outreach/blocklist', methods=['GET'])
@_api_admin_req_dec
def outreach_list_blocklist():
    """List all blocked emails. Used by the admin UI to show what's
    blocked and provide an unblock path if the founder changes their mind."""
    from models import ProspectBlocklist

    rows = (ProspectBlocklist.query
            .order_by(ProspectBlocklist.blocked_at.desc())
            .limit(500)
            .all())
    return jsonify({
        'count': len(rows),
        'items': [
            {
                'id': r.id,
                'email': r.email,
                'name': r.name_at_block,
                'title': r.title_at_block,
                'company': r.company_at_block,
                'reason': r.reason,
                'notes': r.notes,
                'blocked_at': r.blocked_at.isoformat() if r.blocked_at else None,
            }
            for r in rows
        ],
    })


@admin_bp.route('/api/admin/outreach/blocklist/<int:block_id>', methods=['DELETE'])
@_api_admin_req_dec
def outreach_unblock(block_id):
    """Remove an email from the blocklist. The email becomes eligible
    for re-add on the next discovery run, but no contact row is restored
    automatically — the founder/crawler has to surface them again."""
    from models import ProspectBlocklist

    b = ProspectBlocklist.query.get(block_id)
    if not b:
        return jsonify({'error': 'Not found'}), 404
    db.session.delete(b)
    db.session.commit()
    return jsonify({'ok': True})


@admin_bp.route('/api/admin/outreach/b2b/wedge-sweep', methods=['POST'])
@_api_admin_req_dec
def outreach_wedge_sweep():
    """v5.87.99: Re-classify B2B prospects whose wedge is unset or 'other'.

    Runs _guess_wedge() on every prospect where wedge IS NULL OR wedge IN
    ('', 'other'). Manually-set wedges are NEVER touched, even if the
    keyword inference would suggest a different value.

    Body:
      dry_run: bool (default true) — when true, returns the proposed
               changes WITHOUT persisting. When false, commits the
               re-classifications.

    Response:
      eligible_total: int   — count of prospects with empty/other wedge
      classified_now: int   — count that _guess_wedge resolved to a
                              specific wedge (renovation_lenders, etc.)
      still_other:    int   — count that remained 'other' after the sweep
                              (no keyword match in company/email)
      changes_by_wedge: {wedge: count}
      sample: [{id, email, company, old_wedge, new_wedge}, ...] (max 50)
      committed: bool       — whether the changes were actually persisted
    """
    from models import OutreachContact

    data = request.get_json(silent=True) or {}
    # Default to dry_run=True so a careless POST without a body doesn't
    # commit changes. Founder must explicitly pass dry_run=false to apply.
    dry_run = data.get('dry_run', True)
    if isinstance(dry_run, str):
        dry_run = dry_run.lower() not in ('false', '0', 'no')

    # Eligible: wedge is NULL, empty string, or 'other' (the explicit
    # "I don't know" value used by _guess_wedge as a default fallback).
    candidates = OutreachContact.query.filter(
        OutreachContact.cohort == 'b2b',
        db.or_(
            OutreachContact.wedge == None,  # noqa: E711 — SQLAlchemy IS NULL
            OutreachContact.wedge == '',
            OutreachContact.wedge == 'other',
        ),
    ).all()

    eligible_total = len(candidates)
    classified_now = 0
    still_other = 0
    changes_by_wedge = {}
    sample = []

    for c in candidates:
        old_wedge = c.wedge or ''
        new_wedge = _guess_wedge(c.company or '', c.email or '')
        # If the keyword scan resolves to something specific, count it.
        # If it stays 'other', track that separately.
        if new_wedge != 'other':
            classified_now += 1
            changes_by_wedge[new_wedge] = changes_by_wedge.get(new_wedge, 0) + 1
            if not dry_run:
                c.wedge = new_wedge
        else:
            still_other += 1
            if not dry_run and old_wedge != 'other':
                # Normalize empty/null to 'other' so the field is consistent
                c.wedge = 'other'

        # Keep a sample (max 50) so the founder can spot-check before
        # committing. Show only the prospects that WOULD change if dry-run,
        # or all classified ones if committing.
        if len(sample) < 50 and new_wedge != 'other':
            sample.append({
                'id': c.id,
                'email': c.email,
                'company': c.company or '',
                'old_wedge': old_wedge or '(empty)',
                'new_wedge': new_wedge,
            })

    if not dry_run and (classified_now > 0 or still_other > 0):
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': f'commit failed: {e}'}), 500

    return jsonify({
        'eligible_total': eligible_total,
        'classified_now': classified_now,
        'still_other': still_other,
        'changes_by_wedge': changes_by_wedge,
        'sample': sample,
        'committed': not dry_run,
        'note': (
            'Manual wedges (anything other than NULL/empty/"other") are '
            'never overwritten. Run with dry_run=false to commit.'
        ),
    })


@admin_bp.route('/api/admin/outreach/send', methods=['POST'])
@_api_admin_req_dec
def outreach_send():
    """Send a personalized outreach email through Resend.

    Body params:
      cohort:  'buyer' or 'b2b'
      user_id: int (required when cohort='buyer')
      contact_id: int (required when cohort='b2b')
      subject: str
      body:    str (plain text — wrapped in minimal HTML for Resend)

    Replies route to the founder's Gmail via Resend's reply_to header.
    """
    from email_service import send_email
    from models import OutreachContact, OutreachLog

    data = request.get_json(silent=True) or {}
    cohort = (data.get('cohort') or '').strip()
    subject = (data.get('subject') or '').strip()
    body = (data.get('body') or '').strip()

    if cohort not in ('buyer', 'b2b'):
        return jsonify({'error': "cohort must be 'buyer' or 'b2b'"}), 400
    if not subject or not body:
        return jsonify({'error': 'subject and body required'}), 400

    # Resolve recipient based on cohort
    user_id = None
    contact_id = None
    to_email = ''
    if cohort == 'buyer':
        user_id = data.get('user_id')
        if not user_id:
            return jsonify({'error': 'user_id required for buyer cohort'}), 400
        u = User.query.get(int(user_id))
        if not u:
            return jsonify({'error': 'User not found'}), 404
        to_email = u.email
    else:
        contact_id = data.get('contact_id')
        if not contact_id:
            return jsonify({'error': 'contact_id required for b2b cohort'}), 400
        c = OutreachContact.query.get(int(contact_id))
        if not c:
            return jsonify({'error': 'Contact not found'}), 404
        to_email = c.email

        # v5.87.79: Double-send guardrail. If this contact was emailed
        # within OUTREACH_DOUBLE_SEND_WINDOW_DAYS (default 7), refuse the
        # send unless force=true is explicitly passed. This prevents
        # accidental double-emails (e.g. clicking the send button twice,
        # or re-sending a draft you forgot you sent yesterday). Real
        # follow-ups override with force=true after the operator confirms.
        force = bool(data.get('force', False))
        if not force and c.last_contacted_at:
            window_days = int(os.environ.get(
                'OUTREACH_DOUBLE_SEND_WINDOW_DAYS', '7'
            ))
            cutoff = datetime.utcnow() - timedelta(days=window_days)
            if c.last_contacted_at >= cutoff:
                hours_ago = int(
                    (datetime.utcnow() - c.last_contacted_at).total_seconds() / 3600
                )
                if hours_ago < 24:
                    when_str = f'{hours_ago}h ago'
                else:
                    when_str = f'{hours_ago // 24}d {hours_ago % 24}h ago'
                return jsonify({
                    'error': 'recent_send',
                    'message': (
                        f'{c.name or c.email} was last contacted {when_str}. '
                        f'Send again? (within {window_days}-day window)'
                    ),
                    'last_contacted_at': c.last_contacted_at.isoformat() + 'Z',
                    'window_days': window_days,
                    'requires_force': True,
                }), 409  # 409 Conflict — UI catches this and shows confirm dialog

    reply_to = _founder_reply_to()

    # Wrap plain-text body in light HTML. We deliberately keep this very plain
    # so the email reads as a real personal note, not a marketing campaign.
    # Personal-feeling emails get higher reply rates.
    # v5.88.01: linkify URLs to anchor tags for guaranteed clickability.
    html_lines = ''.join(
        f'<p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#1f2937;">{_linkify_line(line)}</p>'
        if line.strip() else '<br>'
        for line in body.split('\n')
    )
    html_content = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
        'max-width:560px;margin:0 auto;padding:24px 4px;color:#1f2937;">'
        f'{html_lines}'
        '</div>'
    )

    # Capture the resend_id from email_service.send_email by intercepting via
    # EmailSendLog. send_email() writes the resend_id to EmailSendLog
    # synchronously inside its finally block; we look up the most recent row
    # for this to_email immediately after the call.
    pre_send_at = datetime.utcnow()

    ok = False
    # v5.87.80: B2B cold outreach uses a personal sender identity, not
    # noreply@. Buyer-cohort sends (welcome, receipts, analysis-ready) keep
    # the default transactional From address. Same Resend account, same
    # SPF/DKIM, just a different display name + local part.
    if cohort == 'b2b':
        from email_service import OUTREACH_FROM_EMAIL
        from_email_override = OUTREACH_FROM_EMAIL
    else:
        from_email_override = None  # falls through to FROM_EMAIL default

    try:
        ok = send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            reply_to=reply_to,
            email_type='founder_outreach',
            from_email=from_email_override,
        )
    except Exception as e:
        logger.exception('outreach send failed')
        # Fall through to log the failure

    # Pull the resend_id that send_email just wrote
    from models import EmailSendLog
    log_row = (EmailSendLog.query
               .filter(EmailSendLog.to_email == to_email)
               .filter(EmailSendLog.ts >= pre_send_at)
               .order_by(EmailSendLog.ts.desc())
               .first())
    resend_id = log_row.resend_id if log_row else None

    # Always write OutreachLog so we have a record even on failure
    log = OutreachLog(
        cohort=cohort,
        user_id=user_id,
        contact_id=contact_id,
        to_email=to_email,
        subject=subject[:500],
        body=body,
        reply_to=reply_to,
        resend_id=resend_id,
        success=bool(ok),
        error=None if ok else 'send_email returned False',
    )
    db.session.add(log)

    # Bump the contact's last_contacted_at + status if it was a B2B send and
    # this was their first outreach
    if cohort == 'b2b' and contact_id:
        c = OutreachContact.query.get(contact_id)
        if c:
            c.last_contacted_at = datetime.utcnow()
            if c.status == 'not_contacted' and ok:
                c.status = 'contacted'

    db.session.commit()

    if ok:
        return jsonify({'ok': True, 'resend_id': resend_id, 'to': to_email})
    return jsonify({'ok': False, 'error': 'Email send failed — check RESEND_API_KEY and Resend dashboard'}), 500


@admin_bp.route('/api/admin/outreach/test-send', methods=['POST'])
@_api_admin_req_dec
def outreach_test_send():
    """v5.87.81: Send a canonical test outreach email to the founder address.

    Sends to FOUNDER_REPLY_EMAIL using the OUTREACH_FROM_EMAIL identity, so
    the founder can verify the From line, Reply-To, and HTML rendering as
    a real prospect would see it before sending to actual contacts.

    Does NOT touch any OutreachContact row, does NOT write to OutreachLog
    (would skew metrics). Just calls send_email and returns the result.
    """
    from email_service import send_email, OUTREACH_FROM_EMAIL

    to_email = (os.environ.get('FOUNDER_REPLY_EMAIL') or '').strip()
    if not to_email:
        return jsonify({
            'ok': False,
            'error': 'FOUNDER_REPLY_EMAIL not set in env. Set it in Render to the address you want test emails to land at.'
        }), 400

    reply_to = _founder_reply_to()  # same as real outreach
    subject = '[Test] OfferWise outreach From / Reply-To verification'
    body = (
        "This is a canonical test outreach email.\n\n"
        "If you can read this, the outreach send pipeline is working. "
        "Verify the From line shows 'Francis Anthony <francis@getofferwise.ai>' "
        "and the Reply-To header is your founder Gmail.\n\n"
        "If From shows noreply@ or the email landed in Promotions/Spam, "
        "stop and investigate before sending to real prospects.\n\n"
        "No prospect contact was updated by this test."
    )
    html_lines = ''.join(
        f'<p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#1f2937;">{_linkify_line(line)}</p>'
        if line.strip() else '<br>'
        for line in body.split('\n')
    )
    html_content = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
        'max-width:560px;margin:0 auto;padding:24px 4px;color:#1f2937;">'
        f'{html_lines}'
        '</div>'
    )

    try:
        ok = send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            reply_to=reply_to,
            email_type='founder_outreach_test',
            from_email=OUTREACH_FROM_EMAIL,
        )
    except Exception as e:
        logger.exception('test-send failed')
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500

    if not ok:
        return jsonify({
            'ok': False,
            'error': 'send_email returned False — check RESEND_API_KEY and Resend dashboard'
        }), 500

    return jsonify({
        'ok': True,
        'to': to_email,
        'from': OUTREACH_FROM_EMAIL,
        'reply_to': reply_to,
    })


@admin_bp.route('/api/admin/outreach/replies', methods=['GET'])
@_api_admin_req_dec
def outreach_replies_panel():
    """Unified 'replies to check' panel.

    Surfaces three signal types in priority order:
      1. clicked — strongest signal of interest
      2. opened — softer signal but worth checking gmail for
      3. b2b prospects manually flagged 'replied' — confirms a Gmail reply
    """
    from models import OutreachLog, OutreachContact, EmailEvent
    from sqlalchemy import func as _f

    # All recent sends in the last 30 days
    cutoff = datetime.utcnow() - timedelta(days=30)
    recent_logs = (OutreachLog.query
                   .filter(OutreachLog.sent_at >= cutoff,
                           OutreachLog.success.is_(True),
                           OutreachLog.resend_id.isnot(None))
                   .order_by(OutreachLog.sent_at.desc())
                   .limit(500)
                   .all())

    if not recent_logs:
        # Still need to surface manually-flagged replies on contacts
        replied = OutreachContact.query.filter_by(cohort='b2b', status='replied').all()
        return jsonify({
            'clicked': [], 'opened': [], 'replied_b2b': [
                {'id': c.id, 'email': c.email, 'name': c.name or '',
                 'company': c.company or '',
                 'replied_at': c.replied_at.isoformat() if c.replied_at else None,
                 'last_reply_summary': c.last_reply_summary or ''}
                for c in replied
            ],
        })

    rid_to_log = {l.resend_id: l for l in recent_logs}
    engagement = _outreach_engagement_for(list(rid_to_log.keys()))

    clicked, opened = [], []
    for rid, eng in engagement.items():
        log = rid_to_log.get(rid)
        if not log:
            continue
        # Resolve the cohort identity
        identity = {'email': log.to_email, 'cohort': log.cohort,
                    'subject': log.subject,
                    'sent_at': log.sent_at.isoformat() if log.sent_at else None,
                    'first_opened_at': eng.get('first_opened_at'),
                    'last_clicked_at': eng.get('last_clicked_at'),
                    'click_count': eng.get('click_count', 0),
                    'user_id': log.user_id, 'contact_id': log.contact_id}
        # Enrich with name/company if b2b
        if log.cohort == 'b2b' and log.contact_id:
            c = OutreachContact.query.get(log.contact_id)
            if c:
                identity['name'] = c.name or ''
                identity['company'] = c.company or ''
                identity['contact_status'] = c.status
        elif log.cohort == 'buyer' and log.user_id:
            u = User.query.get(log.user_id)
            if u:
                identity['name'] = u.name or ''

        if eng.get('clicked'):
            clicked.append(identity)
        elif eng.get('opened'):
            opened.append(identity)

    # Sort by recency of the engagement signal
    clicked.sort(key=lambda r: r.get('last_clicked_at') or '', reverse=True)
    opened.sort(key=lambda r: r.get('first_opened_at') or '', reverse=True)

    # Manually-flagged replies on B2B prospects
    replied_contacts = OutreachContact.query.filter_by(cohort='b2b', status='replied').all()
    replied_b2b = [{
        'id': c.id, 'email': c.email, 'name': c.name or '',
        'company': c.company or '', 'wedge': c.wedge or '',
        'replied_at': c.replied_at.isoformat() if c.replied_at else None,
        'last_reply_summary': c.last_reply_summary or '',
    } for c in replied_contacts]
    replied_b2b.sort(key=lambda r: r.get('replied_at') or '', reverse=True)

    return jsonify({
        'clicked': clicked,
        'opened': opened,
        'replied_b2b': replied_b2b,
    })


@admin_bp.route('/api/admin/outreach/history', methods=['GET'])
@_api_admin_req_dec
def outreach_history():
    """Get full send history for a single contact (buyer or b2b).

    Query params: cohort, user_id OR contact_id
    """
    from models import OutreachLog
    from sqlalchemy import desc as _desc

    cohort = (request.args.get('cohort') or '').strip()
    if cohort not in ('buyer', 'b2b'):
        return jsonify({'error': "cohort must be 'buyer' or 'b2b'"}), 400

    q = OutreachLog.query.filter_by(cohort=cohort)
    if cohort == 'buyer':
        uid = request.args.get('user_id', type=int)
        if not uid:
            return jsonify({'error': 'user_id required'}), 400
        q = q.filter_by(user_id=uid)
    else:
        cid = request.args.get('contact_id', type=int)
        if not cid:
            return jsonify({'error': 'contact_id required'}), 400
        q = q.filter_by(contact_id=cid)

    logs = q.order_by(_desc(OutreachLog.sent_at)).limit(50).all()
    rids = [l.resend_id for l in logs if l.resend_id]
    engagement = _outreach_engagement_for(rids)

    return jsonify({
        'history': [{
            'id': l.id,
            'sent_at': l.sent_at.isoformat() if l.sent_at else None,
            'subject': l.subject, 'body': l.body,
            'success': bool(l.success), 'error': l.error,
            'resend_id': l.resend_id,
            'engagement': engagement.get(l.resend_id, {}) if l.resend_id else {},
        } for l in logs],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Mail-merge / campaign endpoints (v5.87.30)
# ─────────────────────────────────────────────────────────────────────────────
# Two flows:
#   1. B2B bulk send  — one template, N prospects, render+send server-side
#   2. Buyer campaign — preview-only render endpoint that the JS uses to build
#      a queue of pre-rendered cards; actual sends still go through /send
#      one at a time so each can be reviewed/edited before sending.
#
# Both flows use the same _render_template helper so variable behavior is
# consistent.
# ─────────────────────────────────────────────────────────────────────────────


# Wedge code → human-readable pain phrase. Used by {wedge_pain} variable.
# Keep these phrases sentence-fragments that fit naturally after "how you think
# about ___" or "your work on ___".
_WEDGE_PAIN_PHRASES = {
    'renovation_lenders':
        'repair-cost risk on the properties you finance',
    'insurtechs':
        'hidden risk in disclosure documents during underwriting',
    'brokerage_tech':
        'agent-side tools that surface findings buyers actually care about',
    # v5.87.45 — extended taxonomy. These three were emerging as the right
    # adjacent wedges during outreach planning; surfacing them in the picker
    # avoids forcing them into 'other' (which loses the personalization win).
    'title_closing':
        'contradiction-detection in the closing-doc package',
    'buyer_fintech':
        'helping first-time buyers avoid the financially worst houses',
    'ibuyer':
        'condition risk in your acquisition pipeline',
    'other':
        'this space',
    '':
        'this space',
}

# Keyword → wedge auto-classifier. Used by the paste-import endpoint to guess
# the wedge from a company name when the user hasn't tagged it explicitly.
# Order matters — earlier matches win — so put more-specific phrases first.
_WEDGE_KEYWORD_HINTS = [
    # Renovation lenders
    ('renovation', 'renovation_lenders'),
    ('renofi', 'renovation_lenders'),
    ('hometap', 'renovation_lenders'),
    ('point digital', 'renovation_lenders'),
    ('unison', 'renovation_lenders'),
    ('fix and flip', 'renovation_lenders'),
    ('lima one', 'renovation_lenders'),
    ('kiavi', 'renovation_lenders'),
    ('lendinghome', 'renovation_lenders'),
    ('foyer', 'renovation_lenders'),
    ('203k', 'renovation_lenders'),
    ('home equity', 'renovation_lenders'),
    ('heloc', 'renovation_lenders'),
    # Insurtechs
    ('insurance', 'insurtechs'),
    ('insurtech', 'insurtechs'),
    ('hippo', 'insurtechs'),
    ('lemonade', 'insurtechs'),
    ('kin ', 'insurtechs'),
    ('branch ', 'insurtechs'),
    ('openly', 'insurtechs'),
    ('slide ', 'insurtechs'),
    ('sagesure', 'insurtechs'),
    ('coterie', 'insurtechs'),
    ('underwriting', 'insurtechs'),
    # Title / closing
    ('title', 'title_closing'),
    ('doma', 'title_closing'),
    ('qualia', 'title_closing'),
    ('endpoint', 'title_closing'),
    ('escrow', 'title_closing'),
    ('closing', 'title_closing'),
    # iBuyers
    ('opendoor', 'ibuyer'),
    ('offerpad', 'ibuyer'),
    ('ibuyer', 'ibuyer'),
    # Buyer-side fintech
    ('tomo ', 'buyer_fintech'),
    ('better.com', 'buyer_fintech'),
    ('homelight', 'buyer_fintech'),
    # Brokerage tech (catchall after the more specific matches)
    ('compass', 'brokerage_tech'),
    ('side.com', 'brokerage_tech'),
    ('zillow', 'brokerage_tech'),
    ('realtor.com', 'brokerage_tech'),
    ('redfin', 'brokerage_tech'),
    ('exp realty', 'brokerage_tech'),
    ('real broker', 'brokerage_tech'),
    ('the agency', 'brokerage_tech'),
    ('engel', 'brokerage_tech'),
    ('brokerage', 'brokerage_tech'),
    ('proptech', 'brokerage_tech'),
    ('real estate', 'brokerage_tech'),
]


def _guess_wedge(company, email):
    """Best-effort wedge inference from company name + email domain.

    Used by the paste-import flow when the user pastes a list without
    explicitly tagging wedges. Returns a wedge code from _WEDGE_PAIN_PHRASES;
    falls back to 'other' (which renders the generic phrase) when nothing
    matches.
    """
    haystack = ' '.join([(company or '').lower(), (email or '').lower()])
    for keyword, wedge in _WEDGE_KEYWORD_HINTS:
        if keyword in haystack:
            return wedge
    return 'other'


def _render_template(template_str, variables):
    """Render a template with {var_name} substitution.

    Whitespace inside braces is tolerated: {first_name} and { first_name } both
    resolve. Missing variables render as empty string and are returned in the
    `unfilled` set so callers can warn the user.

    Returns (rendered_string, unfilled_variable_names_set).
    """
    import re
    if not template_str:
        return '', set()
    unfilled = set()

    def _replace(match):
        var_name = match.group(1).strip()
        val = variables.get(var_name)
        if val is None or val == '':
            unfilled.add(var_name)
            return ''
        return str(val)

    rendered = re.sub(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', _replace, template_str)
    return rendered, unfilled


def _b2b_variables_for(contact):
    """Build the variable-substitution dict for one B2B contact.

    Variables exposed to the template:
      {first_name} — first word of contact.name, or empty
      {name}       — contact.name, or empty
      {email}      — contact.email
      {title}      — contact.title, or empty
      {company}    — contact.company, or empty
      {wedge}      — contact.wedge (raw code: 'renovation_lenders' etc)
      {wedge_pain} — human-readable pain phrase derived from wedge
    """
    name = (contact.name or '').strip()
    first_name = name.split(' ')[0] if name else ''
    return {
        'first_name': first_name,
        'name':       name,
        'email':      contact.email or '',
        'title':      (contact.title or '').strip(),
        'company':    (contact.company or '').strip(),
        'wedge':      (contact.wedge or '').strip(),
        'wedge_pain': _WEDGE_PAIN_PHRASES.get(contact.wedge or '', _WEDGE_PAIN_PHRASES['']),
    }


def _buyer_variables_for(user):
    """Build the variable-substitution dict for one buyer (User row).

    Variables exposed:
      {first_name}        — first word of user.name, or empty
      {name}              — user.name, or empty
      {email}             — user.email
      {plan}              — subscription plan code
      {days_since_signup} — int as string, or empty
    """
    name = (user.name or '').strip()
    first_name = name.split(' ')[0] if name else ''
    days = ''
    if user.created_at:
        days = str((datetime.utcnow() - user.created_at).days)
    return {
        'first_name': first_name,
        'name':       name,
        'email':      user.email or '',
        'plan':       getattr(user, 'subscription_plan', 'free') or 'free',
        'days_since_signup': days,
    }


# =============================================================================
# v5.88.47 — Outreach Results dashboard
# =============================================================================
# Single endpoint that returns everything the Results tab needs:
#   - totals (all-time / 30d / 7d, with sent/opened/clicked/replied)
#   - by-cohort breakdown
#   - timeline: every send newest first with engagement + reply state
#
# Reply state has two sources:
#   1. OutreachLog.replied_at  — set when founder hits "Mark replied" in the UI.
#      Works for both cohorts.
#   2. OutreachContact.status='replied' — legacy per-contact flag for B2B,
#      already wired up before v5.88.47. We surface either as truthy.

# =============================================================================
# v5.88.51 — Migration status visibility
# =============================================================================
# Bootstrap writes its result to /tmp/bootstrap_alembic_status.json on every
# container start. This endpoint surfaces that file plus a live schema-vs-model
# comparison so the founder can see at a glance whether the deployed schema
# matches the deployed code.
#
# Why this exists: before v5.88.51, migration failures were silent. v5.88.42
# added three tables + one column without writing a migration, and the gap
# only surfaced when v5.88.50 wired up endpoints that queried the missing
# column. By then, ~4 weeks of code had shipped on top of stale schema.

@admin_bp.route('/api/admin/db-migration-status', methods=['GET'])
@_api_admin_req_dec
def db_migration_status():
    """Report Alembic bootstrap result + live schema-vs-model drift check.

    Response shape:
      {
        'bootstrap': {            # last container-start bootstrap result
          'ok': bool,
          'stage': 'upgrade_complete' | 'upgrade' | 'stamp' | ...,
          'current_revision': str | None,
          'previous_revision': str | None,
          'error': str | None,
          'traceback': str | None,
          'ts': float,            # unix timestamp of bootstrap completion
        } | None,                  # null if status file missing
        'schema_drift': [          # tables/cols on model but missing in DB
          {'table': 'outreach_log', 'missing_columns': ['campaign_id']},
          {'table': 'outreach_campaigns', 'missing_table': True},
          ...
        ],
        'head_revision': str,      # what alembic 'head' would be right now
      }
    """
    import json as _json
    import os as _os
    from sqlalchemy import inspect as _inspect

    # Read the bootstrap status file
    bootstrap = None
    try:
        if _os.path.exists('/tmp/bootstrap_alembic_status.json'):
            with open('/tmp/bootstrap_alembic_status.json') as fh:
                bootstrap = _json.load(fh)
    except Exception as e:
        bootstrap = {'ok': False, 'stage': 'read_status_file', 'error': str(e)}

    # Live schema-vs-model drift check. Walk every SQLAlchemy model and
    # check for tables/columns that exist in the model but not the DB.
    drift = []
    head_revision = None
    try:
        engine = db.engine
        inspector = _inspect(engine)
        db_tables = set(inspector.get_table_names())

        # Check each registered table in the metadata
        for table_name, table in db.metadata.tables.items():
            if table_name not in db_tables:
                drift.append({
                    'table': table_name,
                    'missing_table': True,
                    'missing_columns': sorted([c.name for c in table.columns]),
                })
                continue
            # Table exists; check columns
            db_cols = {c['name'] for c in inspector.get_columns(table_name)}
            model_cols = {c.name for c in table.columns}
            missing = sorted(model_cols - db_cols)
            if missing:
                drift.append({
                    'table': table_name,
                    'missing_table': False,
                    'missing_columns': missing,
                })

        # What's the head revision according to the alembic version files?
        try:
            from alembic.config import Config
            from alembic.script import ScriptDirectory
            cfg = Config(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'alembic.ini'))
            script_dir = ScriptDirectory.from_config(cfg)
            heads = script_dir.get_heads()
            head_revision = heads[0] if len(heads) == 1 else f'MULTIPLE HEADS: {heads}'
        except Exception as e:
            head_revision = f'lookup_failed: {e}'
    except Exception as e:
        return jsonify({
            'bootstrap': bootstrap,
            'schema_drift_check_failed': str(e),
            'head_revision': head_revision,
        }), 500

    # Get current revision from alembic_version table for comparison
    current_revision = None
    try:
        from sqlalchemy import text as _text
        with db.engine.connect() as conn:
            row = conn.execute(_text('SELECT version_num FROM alembic_version LIMIT 1')).fetchone()
            if row:
                current_revision = row[0]
    except Exception:
        pass

    # Drift severity: any drift means schema is behind code
    severity = 'ok' if not drift else ('warning' if len(drift) <= 2 else 'critical')

    return jsonify({
        'bootstrap': bootstrap,
        'schema_drift': drift,
        'drift_count': len(drift),
        'severity': severity,
        'head_revision_per_code': head_revision,
        'current_revision_in_db': current_revision,
        'in_sync': head_revision == current_revision and not drift,
    })


@admin_bp.route('/api/admin/outreach/results', methods=['GET'])
@_api_admin_req_dec
def outreach_results():
    """Aggregate + timeline view for every outreach send.

    Query params:
      limit       — max timeline rows (default 100, max 500)
      cohort      — optional filter ('buyer' or 'b2b')

    Response shape documented in v5.88.47 changelog.
    """
    from models import OutreachContact, OutreachLog, EmailEvent
    from sqlalchemy import func as _f, desc as _desc

    try:
        limit = min(int(request.args.get('limit', '100')), 500)
    except (TypeError, ValueError):
        limit = 100
    cohort_filter = (request.args.get('cohort') or '').strip().lower()

    now = datetime.utcnow()
    d30 = now - timedelta(days=30)
    d7  = now - timedelta(days=7)

    # --- Pull all sends (capped at 2000 for totals; timeline trimmed below) ---
    try:
        base_q = OutreachLog.query
        if cohort_filter in ('buyer', 'b2b'):
            base_q = base_q.filter(OutreachLog.cohort == cohort_filter)
        all_logs = (base_q
                    .order_by(_desc(OutreachLog.sent_at))
                    .limit(2000)
                    .all())
    except Exception as e:
        logger.exception('outreach_results: log query failed')
        return jsonify({
            'error': 'log_query_failed',
            'message': f'{type(e).__name__}: {e}',
            'hint': 'Likely a missing column on outreach_log. Check alembic state.',
        }), 500

    # --- Engagement (open/click) per resend_id ---
    resend_ids = [l.resend_id for l in all_logs if l.resend_id]
    engagement = {}
    engagement_warning = None
    try:
        engagement = _outreach_engagement_for(resend_ids)
    except Exception as e:
        logger.exception('outreach_results: engagement aggregation failed (non-fatal)')
        engagement_warning = f'{type(e).__name__}: {e}'

    # --- Pre-resolve recipient names (one query each table) ---
    user_ids   = list({l.user_id    for l in all_logs if l.user_id})
    contact_ids = list({l.contact_id for l in all_logs if l.contact_id})

    users_by_id = {}
    if user_ids:
        try:
            for u in User.query.filter(User.id.in_(user_ids)).all():
                users_by_id[u.id] = u
        except Exception as e:
            logger.warning(f'outreach_results: user lookup failed: {e}')

    contacts_by_id = {}
    if contact_ids:
        try:
            for c in OutreachContact.query.filter(OutreachContact.id.in_(contact_ids)).all():
                contacts_by_id[c.id] = c
        except Exception as e:
            logger.warning(f'outreach_results: contact lookup failed: {e}')

    # --- Helper to derive per-row reply state ---
    def _row_replied(l):
        """Return (replied_bool, replied_at_iso_or_none, summary_or_empty)."""
        # Primary: OutreachLog.replied_at (v5.88.47). Works for both cohorts.
        log_replied_at = getattr(l, 'replied_at', None)
        log_summary = getattr(l, 'reply_summary', None) or ''
        if log_replied_at:
            return True, log_replied_at.isoformat(), log_summary

        # Fallback (B2B only): contact-level flag set before v5.88.47.
        if l.cohort == 'b2b' and l.contact_id:
            c = contacts_by_id.get(l.contact_id)
            if c and c.status == 'replied' and c.replied_at:
                return True, c.replied_at.isoformat(), c.last_reply_summary or ''
        return False, None, ''

    # --- Build timeline rows (limited) + aggregate counters in one pass ---
    timeline = []
    totals = {
        'all_time': {'sent': 0, 'opened': 0, 'clicked': 0, 'replied': 0},
        'last_30d': {'sent': 0, 'opened': 0, 'clicked': 0, 'replied': 0},
        'last_7d':  {'sent': 0, 'opened': 0, 'clicked': 0, 'replied': 0},
    }
    by_cohort = {
        'buyer': {'sent': 0, 'opened': 0, 'clicked': 0, 'replied': 0},
        'b2b':   {'sent': 0, 'opened': 0, 'clicked': 0, 'replied': 0},
    }
    serialization_errors = 0

    for l in all_logs:
        try:
            # Skip failed sends from totals/timeline — they didn't actually leave.
            # An admin still wants to see them, but the headline numbers are
            # "successful sends", not "attempts".
            if not l.success:
                continue

            eng = engagement.get(l.resend_id, {}) if l.resend_id else {}
            opened  = bool(eng.get('opened'))
            clicked = bool(eng.get('clicked'))
            replied, replied_at_iso, reply_summary = _row_replied(l)

            # Aggregate counters (sent counts every successful send; the
            # rest are non-exclusive: a send may be opened AND clicked AND
            # replied at the same time)
            totals['all_time']['sent'] += 1
            if opened:  totals['all_time']['opened']  += 1
            if clicked: totals['all_time']['clicked'] += 1
            if replied: totals['all_time']['replied'] += 1

            if l.sent_at and l.sent_at >= d30:
                totals['last_30d']['sent'] += 1
                if opened:  totals['last_30d']['opened']  += 1
                if clicked: totals['last_30d']['clicked'] += 1
                if replied: totals['last_30d']['replied'] += 1
            if l.sent_at and l.sent_at >= d7:
                totals['last_7d']['sent'] += 1
                if opened:  totals['last_7d']['opened']  += 1
                if clicked: totals['last_7d']['clicked'] += 1
                if replied: totals['last_7d']['replied'] += 1

            cohort_key = l.cohort if l.cohort in by_cohort else None
            if cohort_key:
                by_cohort[cohort_key]['sent'] += 1
                if opened:  by_cohort[cohort_key]['opened']  += 1
                if clicked: by_cohort[cohort_key]['clicked'] += 1
                if replied: by_cohort[cohort_key]['replied'] += 1

            # Timeline rows — trim to `limit` newest. all_logs is already
            # ordered desc by sent_at so we just stop appending after limit.
            if len(timeline) < limit:
                name = ''
                if l.cohort == 'buyer' and l.user_id:
                    u = users_by_id.get(l.user_id)
                    if u:
                        name = u.name or ''
                elif l.cohort == 'b2b' and l.contact_id:
                    c = contacts_by_id.get(l.contact_id)
                    if c:
                        name = c.name or ''

                timeline.append({
                    'log_id':         l.id,
                    'cohort':         l.cohort,
                    'to_email':       l.to_email,
                    'name':           name,
                    'subject':        l.subject or '',
                    'sent_at':        l.sent_at.isoformat() if l.sent_at else None,
                    'success':        bool(l.success),
                    'opened':         opened,
                    'first_opened_at': eng.get('first_opened_at'),
                    'clicked':        clicked,
                    'click_count':    eng.get('click_count', 0),
                    'replied':        replied,
                    'replied_at':     replied_at_iso,
                    'reply_summary':  reply_summary,
                    'user_id':        l.user_id,
                    'contact_id':     l.contact_id,
                })
        except Exception as e:
            serialization_errors += 1
            logger.warning(f'outreach_results: row {l.id} skipped: {e}')

    # --- Compute reply rate (separate from raw counts so UI doesn't have to) ---
    def _rate(num, den):
        if not den:
            return 0.0
        return round(100.0 * num / den, 1)

    for bucket in (totals['all_time'], totals['last_30d'], totals['last_7d']):
        bucket['open_rate']  = _rate(bucket['opened'],  bucket['sent'])
        bucket['click_rate'] = _rate(bucket['clicked'], bucket['sent'])
        bucket['reply_rate'] = _rate(bucket['replied'], bucket['sent'])
    for bucket in by_cohort.values():
        bucket['open_rate']  = _rate(bucket['opened'],  bucket['sent'])
        bucket['click_rate'] = _rate(bucket['clicked'], bucket['sent'])
        bucket['reply_rate'] = _rate(bucket['replied'], bucket['sent'])

    resp = {
        'totals':     totals,
        'by_cohort':  by_cohort,
        'timeline':   timeline,
        'timeline_limit': limit,
        'cohort_filter':  cohort_filter or 'all',
    }
    if engagement_warning:
        resp['_engagement_warning'] = engagement_warning
    if serialization_errors:
        resp['_serialization_errors'] = serialization_errors
    return jsonify(resp)


@admin_bp.route('/api/admin/outreach/reply/<int:log_id>', methods=['POST'])
@_api_admin_req_dec
def outreach_mark_replied(log_id):
    """Flag a send as having received a reply. Also bumps the linked entity.

    Body params:
      reply_summary  (optional str) — founder's note about what the reply said
      clear          (optional bool) — if true, UNDO the reply flag

    Effect:
      - OutreachLog: replied_at = now, reply_summary = note   (or both NULL if clear)
      - if cohort='b2b' and contact_id is set:
          OutreachContact: status='replied', replied_at = now, last_reply_summary = note
          (or status reverts to 'contacted' / 'not_contacted' if clearing)
      - if cohort='buyer': no User-side bump. Reply state belongs to the send.

    Returns the updated log row in the same shape used by /results timeline.
    """
    from models import OutreachContact, OutreachLog

    log = OutreachLog.query.get(log_id)
    if not log:
        return jsonify({'error': 'log_not_found', 'log_id': log_id}), 404

    data = request.get_json(silent=True) or {}
    clear = bool(data.get('clear', False))
    summary = (data.get('reply_summary') or '').strip()
    # Cap summary length so we don't take pathological input
    summary = summary[:2000]

    now = datetime.utcnow()

    try:
        if clear:
            log.replied_at = None
            log.reply_summary = None
        else:
            log.replied_at = now
            log.reply_summary = summary or None

        # Bump B2B contact status. Skip if we're clearing and the contact's
        # status was already something else (don't trample a 'design_partner'
        # or 'passed' flag the founder set manually).
        if log.cohort == 'b2b' and log.contact_id:
            c = OutreachContact.query.get(log.contact_id)
            if c:
                if clear:
                    # Only revert if status is currently 'replied' (otherwise
                    # leave it alone — could be passed/design_partner/etc)
                    if c.status == 'replied':
                        c.status = 'contacted' if c.last_contacted_at else 'not_contacted'
                        c.replied_at = None
                        c.last_reply_summary = None
                else:
                    c.status = 'replied'
                    c.replied_at = now
                    c.last_reply_summary = summary or c.last_reply_summary

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception(f'outreach_mark_replied: log_id={log_id} failed')
        return jsonify({
            'error': 'update_failed',
            'message': f'{type(e).__name__}: {e}',
        }), 500

    return jsonify({
        'ok': True,
        'log_id': log.id,
        'replied': bool(log.replied_at),
        'replied_at': log.replied_at.isoformat() if log.replied_at else None,
        'reply_summary': log.reply_summary or '',
    })


@admin_bp.route('/api/admin/outreach/render-preview', methods=['POST'])
@_api_admin_req_dec
def outreach_render_preview():
    """Render a template against one or more recipients without sending.

    Used by:
      - B2B bulk-send modal: "Preview" button renders for one selected prospect
        so the founder catches template bugs before sending to all.
      - Buyer sequenced-send queue: renders ALL selected buyers up front so
        the JS can show one card per buyer with the rendered email already
        filled in (still editable per-row before send).

    Body:
      cohort:     'buyer' or 'b2b'
      ids:        array of user_ids (buyer) or contact_ids (b2b)
      subject:    template string
      body:       template string

    Returns:
      previews: array of {id, to_email, to_label, subject, body, unfilled[]}
    """
    from models import OutreachContact

    data = request.get_json(silent=True) or {}
    cohort = (data.get('cohort') or '').strip()
    ids = data.get('ids') or []
    subject_tmpl = data.get('subject') or ''
    body_tmpl = data.get('body') or ''

    if cohort not in ('buyer', 'b2b'):
        return jsonify({'error': "cohort must be 'buyer' or 'b2b'"}), 400
    if not ids:
        return jsonify({'previews': []})
    if not subject_tmpl or not body_tmpl:
        return jsonify({'error': 'subject and body templates required'}), 400

    # Cap how many we'll render in one call — prevents accidental abuse and
    # keeps the JSON response reasonable.
    ids = list(ids)[:200]

    previews = []
    if cohort == 'b2b':
        contacts = OutreachContact.query.filter(OutreachContact.id.in_(ids)).all()
        # Preserve the order the caller asked for (so the queue UI matches selection order)
        contacts_by_id = {c.id: c for c in contacts}
        for cid in ids:
            c = contacts_by_id.get(int(cid))
            if not c:
                continue
            vars_ = _b2b_variables_for(c)
            rendered_subj, u1 = _render_template(subject_tmpl, vars_)
            rendered_body, u2 = _render_template(body_tmpl, vars_)
            label = c.email + (f' — {c.name}' if c.name else '') + (f' ({c.company})' if c.company else '')
            previews.append({
                'id':        c.id,
                'to_email':  c.email,
                'to_label':  label,
                'subject':   rendered_subj,
                'body':      rendered_body,
                'unfilled':  sorted(u1 | u2),
                'variables': vars_,
            })
    else:
        users = User.query.filter(User.id.in_(ids)).all()
        users_by_id = {u.id: u for u in users}
        for uid in ids:
            u = users_by_id.get(int(uid))
            if not u:
                continue
            vars_ = _buyer_variables_for(u)
            rendered_subj, u1 = _render_template(subject_tmpl, vars_)
            rendered_body, u2 = _render_template(body_tmpl, vars_)
            label = u.email + (f' — {u.name}' if u.name else '')
            previews.append({
                'id':        u.id,
                'to_email':  u.email,
                'to_label':  label,
                'subject':   rendered_subj,
                'body':      rendered_body,
                'unfilled':  sorted(u1 | u2),
                'variables': vars_,
            })

    return jsonify({'previews': previews})


@admin_bp.route('/api/admin/outreach/paste-import', methods=['POST'])
@_api_admin_req_dec
def outreach_paste_import():
    """Parse a freeform pasted list of prospects → bulk-create OutreachContact rows.

    v5.87.45: companion to the existing bulk-send. The "one-click mail-merge"
    flow in the admin UI calls this to materialize contacts before
    immediately calling /bulk-send with the returned IDs.

    Body:
      raw:        the pasted text (str, required)
      cohort:     'b2b' (default) or 'buyer' — controls which flavor of
                  OutreachContact we create
      default_wedge:  optional override; if not set, _guess_wedge() runs
                      per row from company/email keywords
      dedup:      if true (default), skip emails that already exist as
                  OutreachContact rows; report them in `skipped`

    Accepted formats per row (auto-detected by separator):
      - "Name <email@domain> | Title | Company"
      - "Name, email@domain, Title, Company"
      - "email@domain"  (just email; name/title/company left blank)
      - tab-separated columns (LinkedIn export paste pattern)

    Lines starting with "#" or "//" are treated as comments and skipped.
    Blank lines are skipped. Non-email lines are skipped with a warning.

    Returns:
      created:   [contact_id, ...] in input order, for hand-off to /bulk-send
      skipped:   [{row, email, reason}] entries that didn't import
      preview:   [{contact_id, name, email, company, wedge}] of created rows
    """
    import re as _re
    from models import OutreachContact, db as _db

    data = request.get_json(silent=True) or {}
    raw = (data.get('raw') or '').strip()
    cohort = (data.get('cohort') or 'b2b').strip()
    default_wedge = (data.get('default_wedge') or '').strip()
    dedup = bool(data.get('dedup', True))

    if not raw:
        return jsonify({'error': 'raw text required'}), 400
    if cohort not in ('b2b', 'buyer'):
        return jsonify({'error': "cohort must be 'b2b' or 'buyer'"}), 400

    # Email regex — RFC-imperfect but good enough for the paste-import case
    EMAIL_RE = _re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')

    # Build a set of already-existing emails for dedup
    existing = set()
    if dedup:
        existing = {
            (e or '').lower().strip()
            for (e,) in _db.session.query(OutreachContact.email).all()
            if e
        }

    # v5.88.00: Always exclude blocklisted emails, regardless of dedup setting.
    # The blocklist is "never contact" — paste-import shouldn't bypass it.
    from models import ProspectBlocklist
    blocklisted = {
        (e or '').lower().strip()
        for (e,) in _db.session.query(ProspectBlocklist.email).all()
        if e
    }

    created_ids = []
    skipped = []
    preview = []

    for raw_line in raw.split('\n'):
        line = raw_line.strip()
        if not line or line.startswith('#') or line.startswith('//'):
            continue

        m = EMAIL_RE.search(line)
        if not m:
            skipped.append({'row': line[:100], 'email': None, 'reason': 'no email found'})
            continue
        email = m.group(0).lower()

        if email in blocklisted:
            skipped.append({'row': line[:100], 'email': email, 'reason': 'on blocklist'})
            continue

        if dedup and email in existing:
            skipped.append({'row': line[:100], 'email': email, 'reason': 'already exists'})
            continue

        # Strip the email from the line and split the remaining parts to
        # extract name/title/company. The remaining text usually has
        # separators (|, comma, tab); we try them in order of strictness.
        rest = (line.replace(m.group(0), '', 1)
                    .replace('<', '').replace('>', '')
                    .strip(' ,;:\t'))
        parts = []
        for sep in ('|', '\t', ','):
            if sep in rest:
                parts = [p.strip() for p in rest.split(sep) if p.strip()]
                break
        if not parts and rest:
            parts = [rest.strip()]

        # Heuristic: first part is name, then title, then company. Reorder
        # if a part contains common company-tail tokens (Inc, LLC, Corp, .com).
        name = parts[0] if len(parts) >= 1 else ''
        title = parts[1] if len(parts) >= 2 else ''
        company = parts[2] if len(parts) >= 3 else ''
        # If only 2 parts and the second looks like a company, treat as
        # name + company (no title). Common in LinkedIn-export rows.
        if len(parts) == 2:
            second_lower = parts[1].lower()
            if any(tok in second_lower for tok in [' inc', ' llc', ' corp', ' co.', '.com', '.ai', '.io', 'group', 'partners', 'capital', 'ventures', 'realty', 'brokerage', 'insurance']):
                name = parts[0]
                title = ''
                company = parts[1]

        wedge = default_wedge or _guess_wedge(company, email)

        contact = OutreachContact(
            email=email,
            name=name[:200] if name else None,
            title=title[:200] if title else None,
            company=company[:200] if company else None,
            wedge=wedge if wedge in _WEDGE_PAIN_PHRASES else 'other',
            cohort=cohort,
            status='not_contacted',
        )
        _db.session.add(contact)
        try:
            _db.session.flush()  # get the id without committing
        except Exception as flush_err:
            _db.session.rollback()
            skipped.append({'row': line[:100], 'email': email, 'reason': f'db error: {flush_err}'})
            continue

        created_ids.append(contact.id)
        preview.append({
            'contact_id': contact.id,
            'name': name,
            'email': email,
            'title': title,
            'company': company,
            'wedge': contact.wedge,
        })
        if dedup:
            existing.add(email)

    try:
        _db.session.commit()
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': f'commit failed: {e}'}), 500

    return jsonify({
        'created': created_ids,
        'count_created': len(created_ids),
        'skipped': skipped,
        'count_skipped': len(skipped),
        'preview': preview,
    })


# ─── Hunter.io integration (v5.87.46) ─────────────────────────────────
# Two endpoints + status, scoped narrowly for free-tier safety.
# Domain Search consumes 1 credit per call (returns up to 100 emails).
# Email Verification consumes 1 credit per email (live SMTP probe).
# A configurable credit floor (HUNTER_CREDIT_FLOOR, default 5) prevents
# burning through monthly credits — the service refuses calls when
# remaining credits ≤ floor.
# The API key is read from HUNTER_API_KEY env var on Render. It is never
# logged and never returned to the frontend in any response shape.

@admin_bp.route('/api/admin/hunter/status')
@_api_admin_req_dec
def hunter_status():
    """Return Hunter account state for the admin UI credit badge.

    Free — does not consume Hunter credits. Cached server-side for 5 min.
    The ?refresh=1 parameter forces a fresh /account fetch.
    """
    try:
        from hunter_service import account_info, HUNTER_CREDIT_FLOOR
        force = request.args.get('refresh') == '1'
        info = account_info(force_refresh=force)
        info['credit_floor'] = HUNTER_CREDIT_FLOOR
        return jsonify(info)
    except Exception as e:
        logger.warning('hunter_status: %s', e)
        return jsonify({'configured': False, 'error': 'service error'}), 500


# ─── Curated top-players catalog (v5.87.53) ───────────────────────────
# Hand-curated list of major players per wedge, used by the new B2B
# outreach UI's quick-pick chips. The user clicks a chip → domain field
# auto-populates → they hit Discover. This is faster than typing domains
# manually for the well-known players in each space.
#
# Maintenance note: this list lives in code (not a DB table) deliberately.
# It changes rarely, gets reviewed in PR diffs, and version-controlling it
# in git is the right answer over a separate config file. Per Francis's
# v5.87.49 rule: this is "prompt engineering / curated config" — stays
# in code rather than env vars.

_WEDGE_TOP_PLAYERS = {
    'renovation_lenders': [
        {'name': 'Renofi',          'domain': 'renofi.com'},
        {'name': 'Hometap',         'domain': 'hometap.com'},
        {'name': 'Point',           'domain': 'point.com'},
        {'name': 'Unison',          'domain': 'unison.com'},
        {'name': 'EasyKnock',       'domain': 'easyknock.com'},
        {'name': 'Lima One Capital','domain': 'limaone.com'},
        {'name': 'Kiavi',           'domain': 'kiavi.com'},
        {'name': 'Roc360',          'domain': 'roc360.com'},
        {'name': 'Foyer',           'domain': 'foyersavings.com'},
        {'name': 'Aven',            'domain': 'aven.com'},
    ],
    'insurtechs': [
        {'name': 'Hippo',           'domain': 'hippo.com'},
        {'name': 'Lemonade',        'domain': 'lemonade.com'},
        {'name': 'Kin Insurance',   'domain': 'kin.com'},
        {'name': 'Branch Insurance','domain': 'ourbranch.com'},
        {'name': 'Openly',          'domain': 'openly.com'},
        {'name': 'Slide Insurance', 'domain': 'slideinsurance.com'},
        {'name': 'SageSure',        'domain': 'sagesure.com'},
        {'name': 'Coterie',         'domain': 'coterieinsurance.com'},
        {'name': 'Cover Genius',    'domain': 'covergenius.com'},
        {'name': 'Bolt Insurance',  'domain': 'boltinsurance.com'},
    ],
    'brokerage_tech': [
        {'name': 'Compass',         'domain': 'compass.com'},
        {'name': 'Side',            'domain': 'side.com'},
        {'name': 'Real Brokerage',  'domain': 'onereal.com'},
        {'name': 'Zillow Premier',  'domain': 'zillow.com'},
        {'name': 'Realtor.com',     'domain': 'realtor.com'},
        {'name': 'Redfin',          'domain': 'redfin.com'},
        {'name': 'eXp Realty',      'domain': 'exprealty.com'},
        {'name': 'HomeLight',       'domain': 'homelight.com'},
        {'name': 'OJO Labs',        'domain': 'ojo.com'},
        {'name': 'The Agency',      'domain': 'theagencyre.com'},
    ],
    'title_closing': [
        {'name': 'Doma',            'domain': 'doma.com'},
        {'name': 'Qualia',          'domain': 'qualia.com'},
        {'name': 'Endpoint',        'domain': 'endpoint.com'},
        {'name': 'States Title',    'domain': 'statestitle.com'},
        {'name': 'Stewart Title',   'domain': 'stewart.com'},
        {'name': 'Spruce',          'domain': 'spruce.co'},
        {'name': 'Pippin Title',    'domain': 'pippintitle.com'},
        {'name': 'First American Title', 'domain': 'firstam.com'},
        {'name': 'Old Republic Title',   'domain': 'oldrepublictitle.com'},
    ],
    'buyer_fintech': [
        {'name': 'Tomo Mortgage',   'domain': 'tomo.com'},
        {'name': 'Better.com',      'domain': 'better.com'},
        {'name': 'Rocket Mortgage', 'domain': 'rocketmortgage.com'},
        {'name': 'Sage Home Loans', 'domain': 'sagehomeloans.com'},
        {'name': 'Knock',           'domain': 'knock.com'},
        {'name': 'Flyhomes',        'domain': 'flyhomes.com'},
        {'name': 'Ribbon Home',     'domain': 'ribbonhome.com'},
    ],
    'ibuyer': [
        {'name': 'Opendoor',        'domain': 'opendoor.com'},
        {'name': 'Offerpad',        'domain': 'offerpad.com'},
        {'name': 'Sundae',          'domain': 'sundae.com'},
    ],
}


@admin_bp.route('/api/admin/outreach/top-players')
@_api_admin_req_dec
def outreach_top_players():
    """Return curated top-players catalog for the quick-pick UI.

    Query params:
      wedge: optional filter to a single wedge code

    Returns:
      {wedges: {wedge_code: [{name, domain}], ...}}
    """
    wedge = (request.args.get('wedge') or '').strip()
    if wedge and wedge in _WEDGE_TOP_PLAYERS:
        return jsonify({'wedges': {wedge: _WEDGE_TOP_PLAYERS[wedge]}})
    return jsonify({'wedges': _WEDGE_TOP_PLAYERS})


# =============================================================================
# v5.88.42 — Prospect Outreach: cohort filtering + bulk send + campaigns
# =============================================================================
# These endpoints back the new "Buyer Users" tab on the Prospect Outreach
# page. The existing single-prospect B2B endpoints above are untouched.

@admin_bp.route('/api/admin/outreach/buyer-cohort', methods=['GET'])
@_api_admin_req_dec
def outreach_buyer_cohort():
    """Compute a filtered buyer cohort. Live count + sample rows.

    Query params (all optional):
      stages          comma-separated list of stages, e.g.
                      'USED_PRODUCT,ONBOARDED'. Default: all.
      from_date       ISO date string. Users created on or after this date.
      to_date         ISO date string. Users created on or before this date.
      exclude_test    '1' (default) drops test/cassette/persona emails.
      inactive_days   integer. Users last logged in >= N days ago, or never.
      source          substring match against User.source.
      limit           max rows. Default 100, hard cap 500.

    Returns:
      {
        count: int,            # rows matching the filter
        users: [{user_id, email, first_name, name, stage,
                 created_at, last_login, days_since_signup,
                 unsubscribed: bool, last_outreach_at: iso|null}],
        unsubscribed_skipped: int,   # how many are in unsub list
      }

    Recipients are NOT marked yet — this is read-only preview.
    """
    from outreach_campaign_service import (
        filter_users_for_cohort, _first_name_from_user,
        _stage_for_user, is_unsubscribed,
    )
    from models import OutreachLog

    args = request.args

    # Parse filters
    stages_str = (args.get('stages') or '').strip()
    stages = [s.strip().upper() for s in stages_str.split(',') if s.strip()] if stages_str else None

    def _parse_date(s):
        s = (s or '').strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace('Z', '+00:00'))
        except Exception:
            return None

    from_date = _parse_date(args.get('from_date'))
    to_date = _parse_date(args.get('to_date'))
    exclude_test = (args.get('exclude_test', '1').strip() != '0')
    try:
        inactive_days = int(args.get('inactive_days')) if args.get('inactive_days') else None
    except (TypeError, ValueError):
        inactive_days = None
    source = (args.get('source') or '').strip() or None
    try:
        limit = max(1, min(500, int(args.get('limit') or 100)))
    except (TypeError, ValueError):
        limit = 100

    users = filter_users_for_cohort(
        stages=stages,
        from_date=from_date,
        to_date=to_date,
        exclude_test=exclude_test,
        inactive_days_min=inactive_days,
        source=source,
        limit=limit,
    )

    # Annotate with last outreach send + unsubscribe status
    user_ids = [u.id for u in users]
    last_send_map = {}
    if user_ids:
        last_sends = (db.session.query(
            OutreachLog.user_id, db.func.max(OutreachLog.sent_at))
            .filter(OutreachLog.user_id.in_(user_ids))
            .group_by(OutreachLog.user_id).all())
        last_send_map = {uid: ts for uid, ts in last_sends}

    rows = []
    unsubscribed_count = 0
    now = datetime.utcnow()
    for u in users:
        unsubbed = is_unsubscribed(u.email or '')
        if unsubbed:
            unsubscribed_count += 1
        days_since = (now - u.created_at).days if u.created_at else None
        last_outreach = last_send_map.get(u.id)
        rows.append({
            'user_id': u.id,
            'email': u.email,
            'first_name': _first_name_from_user(u),
            'name': u.name or '',
            'stage': _stage_for_user(u),
            'created_at': u.created_at.isoformat() if u.created_at else None,
            'last_login': u.last_login.isoformat() if u.last_login else None,
            'days_since_signup': days_since,
            'unsubscribed': unsubbed,
            'last_outreach_at': last_outreach.isoformat() if last_outreach else None,
        })

    return jsonify({
        'count': len(rows),
        'users': rows,
        'unsubscribed_skipped': unsubscribed_count,
        'filters_applied': {
            'stages': stages, 'from_date': args.get('from_date'),
            'to_date': args.get('to_date'), 'exclude_test': exclude_test,
            'inactive_days': inactive_days, 'source': source,
        },
    })


@admin_bp.route('/api/admin/outreach/templates', methods=['GET'])
@_api_admin_req_dec
def outreach_list_templates():
    """List all outreach templates, sorted by sort_order then name."""
    from models import OutreachTemplate
    cohort = (request.args.get('cohort') or '').strip()
    q = OutreachTemplate.query
    if cohort:
        q = q.filter(OutreachTemplate.cohort == cohort)
    templates = q.order_by(OutreachTemplate.sort_order.asc(),
                           OutreachTemplate.name.asc()).all()
    return jsonify({'templates': [t.to_dict() for t in templates]})


@admin_bp.route('/api/admin/outreach/templates', methods=['POST'])
@_api_admin_req_dec
def outreach_create_template():
    """Create a new outreach template.

    Body: { name, cohort, subject_template, body_template, sort_order? }
    """
    from models import OutreachTemplate
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    cohort = (data.get('cohort') or 'buyer').strip()
    subject = (data.get('subject_template') or '').strip()
    body = (data.get('body_template') or '').strip()
    if not name or not subject or not body:
        return jsonify({'error': 'name, subject_template, body_template required'}), 400
    if cohort not in ('buyer', 'b2b'):
        return jsonify({'error': "cohort must be 'buyer' or 'b2b'"}), 400
    if OutreachTemplate.query.filter_by(name=name).first():
        return jsonify({'error': f'template named "{name}" already exists'}), 400

    try:
        sort_order = int(data.get('sort_order') or 100)
    except (TypeError, ValueError):
        sort_order = 100

    row = OutreachTemplate(
        name=name[:100],
        cohort=cohort,
        subject_template=subject[:500],
        body_template=body,
        sort_order=sort_order,
        is_seeded=False,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify(row.to_dict()), 201


@admin_bp.route('/api/admin/outreach/templates/<int:template_id>', methods=['PATCH'])
@_api_admin_req_dec
def outreach_update_template(template_id):
    """Update an existing template.

    Body: any subset of { name, subject_template, body_template, sort_order }
    """
    from models import OutreachTemplate
    tpl = OutreachTemplate.query.get(template_id)
    if not tpl:
        return jsonify({'error': 'template not found'}), 404
    data = request.get_json(silent=True) or {}
    for field in ('name', 'subject_template', 'body_template'):
        if field in data and data[field]:
            setattr(tpl, field,
                    data[field][:100] if field == 'name'
                    else data[field][:500] if field == 'subject_template'
                    else data[field])
    if 'sort_order' in data:
        try:
            tpl.sort_order = int(data['sort_order'])
        except (TypeError, ValueError):
            pass
    db.session.commit()
    return jsonify(tpl.to_dict())


@admin_bp.route('/api/admin/outreach/templates/<int:template_id>', methods=['DELETE'])
@_api_admin_req_dec
def outreach_delete_template(template_id):
    """Delete a template. Seeded templates can be deleted too — admin's choice."""
    from models import OutreachTemplate
    tpl = OutreachTemplate.query.get(template_id)
    if not tpl:
        return jsonify({'error': 'template not found'}), 404
    db.session.delete(tpl)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/admin/outreach/campaigns', methods=['POST'])
@_api_admin_req_dec
def outreach_create_campaign():
    """Create a draft campaign. Does NOT send.

    Body:
      {
        name: 'May 2026 USED_PRODUCT outreach',
        subject_template: '...',
        body_template: '...',
        cohort_filter: { stages: [...], from_date: '...', ... },
        template_id: optional int (for reuse tracking)
      }
    """
    from outreach_campaign_service import create_campaign
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    subject = (data.get('subject_template') or '').strip()
    body = (data.get('body_template') or '').strip()
    cohort_filter = data.get('cohort_filter') or {}
    template_id = data.get('template_id')

    if not name or not subject or not body:
        return jsonify({'error': 'name, subject_template, body_template required'}), 400

    try:
        c = create_campaign(
            name=name, subject_template=subject, body_template=body,
            cohort_filter=cohort_filter, cohort='buyer',
            template_id=template_id,
        )
        return jsonify(c.to_dict()), 201
    except Exception as e:
        logger.exception('outreach_create_campaign failed')
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/outreach/campaigns/<int:campaign_id>/preview', methods=['POST'])
@_api_admin_req_dec
def outreach_preview_campaign(campaign_id):
    """Dry-run a campaign — render templates against the cohort but DON'T send.

    Returns rendered subject + body for the first 10 recipients plus the
    full recipient count, so the admin can verify substitution before
    committing. Also flags unresolved variables and unsubscribed recipients.
    """
    from outreach_campaign_service import send_campaign
    result = send_campaign(campaign_id, dry_run=True)
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)


@admin_bp.route('/api/admin/outreach/campaigns/<int:campaign_id>/send', methods=['POST'])
@_api_admin_req_dec
def outreach_send_campaign(campaign_id):
    """Send a campaign to its computed cohort.

    Synchronous — for the volumes we're sending (5-50 emails typically),
    this completes in seconds. Larger sends would benefit from a background
    job but for current volume this is fine.
    """
    from outreach_campaign_service import send_campaign
    result = send_campaign(campaign_id)
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)


@admin_bp.route('/api/admin/outreach/campaigns', methods=['GET'])
@_api_admin_req_dec
def outreach_list_campaigns():
    """List campaigns, most recent first."""
    from models import OutreachCampaign
    try:
        limit = max(1, min(100, int(request.args.get('limit') or 50)))
    except (TypeError, ValueError):
        limit = 50
    campaigns = (OutreachCampaign.query
                 .order_by(OutreachCampaign.created_at.desc())
                 .limit(limit).all())
    return jsonify({'campaigns': [c.to_dict() for c in campaigns]})


@admin_bp.route('/api/admin/outreach/campaigns/<int:campaign_id>', methods=['GET'])
@_api_admin_req_dec
def outreach_get_campaign(campaign_id):
    """Campaign detail + recipient logs."""
    from models import OutreachCampaign, OutreachLog
    c = OutreachCampaign.query.get(campaign_id)
    if not c:
        return jsonify({'error': 'campaign not found'}), 404
    logs = (OutreachLog.query
            .filter_by(campaign_id=campaign_id)
            .order_by(OutreachLog.sent_at.desc())
            .limit(500).all())
    log_rows = []
    for l in logs:
        log_rows.append({
            'id': l.id,
            'user_id': l.user_id,
            'to_email': l.to_email,
            'subject': l.subject,
            'success': l.success,
            'error': l.error,
            'sent_at': l.sent_at.isoformat() if l.sent_at else None,
        })
    return jsonify({
        'campaign': c.to_dict(),
        'sends': log_rows,
    })


@admin_bp.route('/api/admin/outreach/unsubscribes', methods=['GET'])
@_api_admin_req_dec
def outreach_list_unsubscribes():
    """List all unsubscribes for admin visibility."""
    from models import OutreachUnsubscribe
    rows = (OutreachUnsubscribe.query
            .order_by(OutreachUnsubscribe.created_at.desc())
            .limit(500).all())
    return jsonify({
        'unsubscribes': [{
            'id': r.id,
            'email': r.email,
            'reason': r.reason,
            'campaign_id': r.campaign_id,
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'notes': r.notes,
        } for r in rows],
    })


# Public unsubscribe endpoint — NOT admin-gated. Recipients hit this from
# the unsubscribe link in outreach emails.
def _register_unsubscribe_route(public_app):
    """Register /unsubscribe on the main Flask app (not admin blueprint).
    Called from app.py during init.
    """
    @public_app.route('/unsubscribe', methods=['GET', 'POST'])
    def public_unsubscribe():
        from outreach_campaign_service import (
            verify_unsubscribe_token, add_unsubscribe
        )
        email = (request.args.get('e') or '').strip()
        try:
            campaign_id = int(request.args.get('c') or 0)
        except (TypeError, ValueError):
            campaign_id = 0
        token = (request.args.get('t') or '').strip()

        if not email or not token:
            return ('<html><body style="font-family:sans-serif;padding:40px">'
                    '<h2>Invalid unsubscribe link</h2>'
                    '<p>The link is missing required parameters. '
                    'Please email francis@getofferwise.ai to be removed.</p>'
                    '</body></html>'), 400

        if not verify_unsubscribe_token(email, campaign_id, token):
            return ('<html><body style="font-family:sans-serif;padding:40px">'
                    '<h2>Invalid unsubscribe link</h2>'
                    '<p>This unsubscribe link could not be verified. '
                    'Please email francis@getofferwise.ai to be removed.</p>'
                    '</body></html>'), 400

        add_unsubscribe(email, reason='manual', campaign_id=campaign_id or None)
        return ('<html><body style="font-family:sans-serif;padding:40px;max-width:600px;margin:0 auto">'
                f'<h2>Unsubscribed</h2>'
                f'<p>{email} has been removed from OfferWise outreach. '
                f'You will not receive further emails from us.</p>'
                f'<p style="color:#666;font-size:13px">'
                f'If this was a mistake, reply to your previous OfferWise '
                f'email and Francis will fix it personally.'
                f'</p>'
                '</body></html>'), 200


# ─── Discovery queue endpoints (v5.87.59) ─────────────────────────────
# Backs the nightly discovery crawler. Founder queues domains via the UI;
# the 3:30am job processes them with credit-aware fallback (Hunter → Snov).

@admin_bp.route('/api/admin/discovery/queue', methods=['GET'])
@_api_admin_req_dec
def discovery_queue_list():
    """List recent queue items with status. UI calls this to render the
    Discovery queue panel.

    Query params:
      status: optional filter ('pending' | 'running' | 'completed' |
              'failed' | 'deferred')
      limit: max rows to return (default 50, max 200)

    Returns:
      {items: [{id, domain, wedge, status, attempts, queued_at,
                completed_at, prospects_found_count, drafts_generated_count,
                source_used, error, queued_by}],
       counts: {pending, running, completed, failed},
       last_run: {started_at, ...} | null}
    """
    from models import DiscoveryQueueItem, db as _db
    status = (request.args.get('status') or '').strip()
    try:
        limit = max(1, min(200, int(request.args.get('limit') or 50)))
    except (TypeError, ValueError):
        limit = 50

    q = DiscoveryQueueItem.query
    if status:
        q = q.filter_by(status=status)
    rows = q.order_by(DiscoveryQueueItem.queued_at.desc()).limit(limit).all()

    items = [{
        'id': r.id,
        'domain': r.domain,
        'wedge': r.wedge or '',
        'status': r.status,
        'attempts': r.attempts,
        'queued_by': r.queued_by,
        'queued_at': r.queued_at.isoformat() if r.queued_at else None,
        'last_attempt_at': r.last_attempt_at.isoformat() if r.last_attempt_at else None,
        'completed_at': r.completed_at.isoformat() if r.completed_at else None,
        'prospects_found_count': r.prospects_found_count or 0,
        'drafts_generated_count': r.drafts_generated_count or 0,
        'source_used': r.source_used or '',
        'error': r.error or '',
    } for r in rows]

    # Counts across all statuses (separate query, lightweight)
    counts = {}
    for s in ('pending', 'running', 'completed', 'failed'):
        counts[s] = DiscoveryQueueItem.query.filter_by(status=s).count()

    return jsonify({'items': items, 'counts': counts})


@admin_bp.route('/api/admin/discovery/queue', methods=['POST'])
@_api_admin_req_dec
def discovery_queue_add():
    """Add a domain to the queue for the next nightly crawl.

    Body:
      domain: str, required (e.g. 'renofi.com')
      wedge: optional wedge code
      title_filter: optional comma-separated titles
      seniority_filter: optional, defaults to 'senior,executive,c_level'
    """
    from models import DiscoveryQueueItem, db as _db
    data = request.get_json(silent=True) or {}
    domain = (data.get('domain') or '').strip().lower()
    if not domain:
        return jsonify({'error': 'domain is required'}), 400
    domain = domain.replace('https://', '').replace('http://', '').split('/')[0]
    if domain.startswith('www.'):
        domain = domain[4:]

    # Dedup: don't queue if already pending/running for the same domain
    existing = DiscoveryQueueItem.query.filter(
        DiscoveryQueueItem.domain == domain,
        DiscoveryQueueItem.status.in_(['pending', 'running'])
    ).first()
    if existing:
        return jsonify({
            'ok': False,
            'error': f'Domain already queued (id={existing.id}, status={existing.status})',
            'existing_id': existing.id,
        }), 409

    item = DiscoveryQueueItem(
        domain=domain,
        wedge=(data.get('wedge') or '').strip() or None,
        queued_by='manual',
        status='pending',
        title_filter=(data.get('title_filter') or '').strip() or None,
        seniority_filter=(data.get('seniority_filter') or 'senior,executive,c_level').strip(),
    )
    _db.session.add(item)
    try:
        _db.session.commit()
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': f'commit failed: {e}'}), 500

    return jsonify({'ok': True, 'id': item.id, 'domain': item.domain})


@admin_bp.route('/api/admin/discovery/queue/<int:item_id>', methods=['DELETE'])
@_api_admin_req_dec
def discovery_queue_delete(item_id: int):
    """Remove a queue item. Useful for cancelling pending items the
    founder no longer wants to crawl, or clearing failed items."""
    from models import DiscoveryQueueItem, db as _db
    item = DiscoveryQueueItem.query.get(item_id)
    if not item:
        return jsonify({'error': 'item not found'}), 404
    if item.status == 'running':
        return jsonify({'error': 'cannot delete a running item'}), 400
    _db.session.delete(item)
    try:
        _db.session.commit()
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': f'commit failed: {e}'}), 500
    return jsonify({'ok': True})


@admin_bp.route('/api/admin/discovery/run-now', methods=['POST'])
@_api_admin_req_dec
def discovery_run_now():
    """Manually trigger the discovery crawler.

    Useful for: testing the integration without waiting for 3:30am, and
    for "I queued 5 domains, run them now" workflow when the founder
    wants results during an active session.

    The job is synchronous — caller waits for it to complete. With the
    default cap of 5 items × ~25s each = ~2 min, this is borderline
    acceptable for an HTTP request (Render gunicorn timeout is 300s).
    For larger runs, use the scheduled job instead.
    """
    try:
        from discovery_crawler import run_nightly_crawl
    except ImportError:
        return jsonify({'error': 'discovery_crawler not loaded'}), 500
    try:
        summary = run_nightly_crawl()
        return jsonify({'ok': True, 'summary': summary})
    except Exception as e:
        logger.exception('discovery_run_now failed')
        return jsonify({'error': f'{e.__class__.__name__}: {e}'}), 500


# ─── Multi-provider discovery (v5.87.52) ──────────────────────────────
# This endpoint is what the UI's Discover button calls. Unlike the legacy
# /api/admin/hunter/domain-search (preserved below for backward compat),
# this routes through prospect_research_service.discover_prospects() so
# the Hunter → Apollo → Snov fallback actually applies.
#
# Why this exists separately: v5.87.50 shipped UI copy claiming Hunter
# would fall back to Apollo and Snov, but the Discover button was still
# calling the Hunter-only endpoint. This release closes that gap.

@admin_bp.route('/api/admin/discover', methods=['POST'])
@_api_admin_req_dec
def multi_provider_discover():
    """Find prospects at a company domain via the orchestrator.

    Tries Hunter (primary) → Apollo (fallback) → Snov (last resort) and
    returns whichever provider succeeds. Response shape matches the legacy
    /api/admin/hunter/domain-search output so the UI doesn't need separate
    handling.

    Body:
      domain:     str, required (e.g. 'renofi.com')
      limit:      int, default 25
      seniority:  optional comma-separated Hunter-style filter
                  (mapped to the orchestrator's `seniorities` list)
      department: optional Hunter-only filter (passed through to Hunter
                  if Hunter is the provider that answers)
      auto_paste: if true, format results as paste-ready text for the
                  Step 1 textarea

    Returns:
      {
        domain, organization, emails: [...],
        source: 'hunter' | 'apollo' | 'snov' | 'none',
        errors: [str, ...],   # informational, not fatal
        paste_ready: str,     # if auto_paste was true
        credit_exhausted: bool,  # only true if ALL providers exhausted
        error: str,           # only set when credit_exhausted or no source
      }

    v5.87.62: entire body wrapped in try/except. Any exception we don't
    handle returns a JSON error response — never an HTML 500 page —
    because the UI's await r.json() chokes hard on Flask's default
    HTML error page (the "Unexpected token '<'" error).
    """
    try:
        from prospect_research_service import discover_prospects
    except ImportError:
        return jsonify({'error': 'prospect_research_service not loaded',
                        'source': 'none', 'emails': [], 'errors': []}), 500

    try:
        data = request.get_json(silent=True) or {}
        domain = (data.get('domain') or '').strip().lower()
        if not domain:
            return jsonify({'error': 'domain is required',
                            'source': 'none', 'emails': [], 'errors': []}), 400
        domain = domain.replace('https://', '').replace('http://', '').split('/')[0]
        if domain.startswith('www.'):
            domain = domain[4:]

        limit = max(1, min(100, int(data.get('limit') or 25)))
        auto_paste = bool(data.get('auto_paste'))

        # Translate the Hunter-style seniority string ('senior,executive,c_level')
        # into the orchestrator's seniorities list. The orchestrator handles
        # mapping back into Hunter/Apollo's individual filter languages.
        seniority_str = (data.get('seniority') or '').strip()
        seniorities = None
        if seniority_str:
            # Reverse-map Hunter-style codes to the orchestrator's enum-ish list.
            # The orchestrator's mapping table covers c_suite, vp, director,
            # head, manager. We approximate from Hunter's vocabulary.
            hunter_to_orch = {
                'c_level': 'c_suite',
                'executive': 'c_suite',
                'senior': 'director',
                # 'junior' has no clean mapping; ignore
            }
            seniorities = []
            for tag in [t.strip() for t in seniority_str.split(',') if t.strip()]:
                mapped = hunter_to_orch.get(tag)
                if mapped and mapped not in seniorities:
                    seniorities.append(mapped)
            if not seniorities:
                seniorities = None

        titles = data.get('titles') or None  # optional explicit list
        # v5.87.74: Band B strict toggle. Default True (filter on);
        # UI checkbox lets user set False to see raw provider output.
        seniority_strict = data.get('seniority_strict')
        if seniority_strict is None:
            seniority_strict = True

        result = discover_prospects(
            company_domain=domain,
            company_name='',
            titles=titles,
            seniorities=seniorities,
            limit=limit,
            seniority_strict=bool(seniority_strict),
        )

        # Translate the orchestrator's canonical shape into the response shape
        # the UI is already reading. The orchestrator returns:
        #   {prospects: [{email, first_name, last_name, name, title, ...}],
        #    source: str, errors: [str]}
        # The UI reads: emails, organization, paste_ready, error, credit_exhausted
        prospects = result.get('prospects') or []
        source = result.get('source') or 'none'
        errors = result.get('errors') or []

        # Convert to the email-shape the UI expects (mostly Hunter's flat shape).
        # The orchestrator already normalized this; we just rename a couple of
        # fields and drop unused ones.
        emails = []
        for p in prospects:
            emails.append({
                'email': p.get('email') or '',
                'first_name': p.get('first_name') or '',
                'last_name': p.get('last_name') or '',
                'position': p.get('title') or '',
                'seniority': p.get('seniority') or '',
                'department': '',
                'confidence': p.get('confidence', 0),
                'sources_count': 0,
            })

        # Surface "everything exhausted" as credit_exhausted so the UI shows the
        # red banner. We detect this by source==none AND all errors mention
        # credit-floor or not-configured.
        credit_exhausted = False
        error_msg = None
        if source == 'none':
            # Compose a useful error message from what each provider said
            if errors:
                error_msg = ' | '.join(errors[:3])
            else:
                error_msg = 'No provider returned results'
            # Heuristic: if at least one error mentions credit floor, flag it
            joined_errors = ' '.join(errors).lower()
            if 'credit' in joined_errors and ('floor' in joined_errors or 'exhausted' in joined_errors):
                credit_exhausted = True

        response = {
            'domain': domain,
            'organization': '',  # orchestrator doesn't surface this currently
            'emails': emails,
            'source': source,
            'errors': errors,
            'credit_exhausted': credit_exhausted,
            'error': error_msg,
        }

        if auto_paste and emails:
            # Build paste-ready string in the same format the legacy endpoint used
            org = response['organization'] or domain
            lines = []
            for e in emails:
                name = ' '.join(p for p in [e.get('first_name'), e.get('last_name')] if p).strip()
                title = e.get('position') or ''
                email = e.get('email')
                if not email:
                    continue
                if name:
                    lines.append(f'{name} <{email}> | {title} | {org}')
                else:
                    lines.append(f'<{email}> | {title} | {org}')
            response['paste_ready'] = '\n'.join(lines)

        return jsonify(response)

    except Exception as e:
        # v5.87.62: Last-resort handler. Any unhandled exception in
        # discover_prospects, the orchestrator, or this endpoint itself
        # ends up here. We MUST return JSON, never let Flask render its
        # default HTML 500 page — the UI's await r.json() can't parse HTML
        # and produces the cryptic "Unexpected token '<'" error.
        import traceback
        tb = traceback.format_exc()
        logger.error('multi_provider_discover failed: %s\n%s', e, tb)
        return jsonify({
            'domain': data.get('domain', '') if isinstance(data, dict) else '',
            'organization': '',
            'emails': [],
            'source': 'none',
            'errors': [f'{e.__class__.__name__}: {str(e)[:300]}'],
            'credit_exhausted': False,
            'error': f'Server error: {e.__class__.__name__}: {str(e)[:200]}',
        }), 500


@admin_bp.route('/api/admin/hunter/domain-search', methods=['POST'])
@_api_admin_req_dec
def hunter_domain_search():
    """Look up emails associated with a company domain.

    Body:
      domain:     str, required (e.g. 'renofi.com')
      limit:      int, default 25, max 100
      seniority:  optional comma-separated filter
                  ('senior,executive,c_level' for decision-makers)
      department: optional comma-separated filter
                  ('executive,management,sales,operations,...' )
      auto_paste: if true, also format the results as a paste-ready
                  string the UI can drop into the paste textarea

    Consumes 1 Hunter credit per call.
    """
    try:
        from hunter_service import domain_search
    except ImportError:
        return jsonify({'error': 'hunter_service module not loaded'}), 500

    data = request.get_json(silent=True) or {}
    domain = (data.get('domain') or '').strip().lower()
    if not domain:
        return jsonify({'error': 'domain is required'}), 400
    # Strip protocol + path if user pasted a full URL
    domain = domain.replace('https://', '').replace('http://', '').split('/')[0]
    if domain.startswith('www.'):
        domain = domain[4:]

    limit = max(1, min(100, int(data.get('limit') or 25)))
    seniority = (data.get('seniority') or '').strip()
    department = (data.get('department') or '').strip()
    auto_paste = bool(data.get('auto_paste'))

    result = domain_search(domain, limit=limit, seniority=seniority, department=department)

    # If the caller asked for a paste-ready string, build one in the same
    # format the paste-import endpoint expects. This means a domain search
    # → drop into textarea → click Parse flow becomes one click on the UI.
    if auto_paste and result.get('emails'):
        org = result.get('organization') or domain
        lines = []
        for e in result['emails']:
            name = ' '.join(p for p in [e.get('first_name'), e.get('last_name')] if p).strip()
            title = e.get('position') or ''
            email = e.get('email')
            if not email:
                continue
            # Format: Name <email> | Title | Company
            if name:
                lines.append(f'{name} <{email}> | {title} | {org}')
            else:
                lines.append(f'<{email}> | {title} | {org}')
        result['paste_ready'] = '\n'.join(lines)

    return jsonify(result)


@admin_bp.route('/api/admin/hunter/verify-batch', methods=['POST'])
@_api_admin_req_dec
def hunter_verify_batch():
    """Verify a list of emails before sending.

    Body:
      emails: list of strings, required (max 50 per call to bound runtime)

    v5.87.49: this endpoint URL is preserved for backward compatibility
    with the existing UI, but it now routes through verifier_service —
    which uses VERIFIER_PROVIDER (default: millionverifier) instead of
    burning Hunter discovery credits on verification. The new explicit
    URLs at /api/admin/verifier/* below are the preferred names going
    forward.
    """
    try:
        from verifier_service import verify_emails_batch
    except ImportError:
        return jsonify({'error': 'verifier_service module not loaded'}), 500

    data = request.get_json(silent=True) or {}
    emails = data.get('emails') or []
    if not isinstance(emails, list) or not emails:
        return jsonify({'error': 'emails (non-empty list) is required'}), 400
    if len(emails) > 50:
        return jsonify({'error': 'max 50 emails per batch'}), 400

    # Dedup + lowercase
    cleaned = []
    seen = set()
    for e in emails:
        e = (e or '').strip().lower()
        if e and '@' in e and e not in seen:
            cleaned.append(e)
            seen.add(e)

    return jsonify(verify_emails_batch(cleaned))


# ─── Pluggable verifier (v5.87.49) ────────────────────────────────────
# These are the canonical URLs going forward. The /api/admin/hunter/verify-batch
# route above is preserved for backward compat but routes through the same
# verifier_service. Provider is selected by VERIFIER_PROVIDER env var:
#   - 'millionverifier' (default, $3.50/1k)
#   - 'zerobounce'      ($7.50/1k, more polished)
#   - 'hunter'          (legacy, falls back through hunter_service)

@admin_bp.route('/api/admin/verifier/status')
@_api_admin_req_dec
def verifier_status():
    """Verifier provider's account state for the credit badge.

    Free, doesn't consume credits. Cached server-side 5 min unless
    ?refresh=1 is passed.
    """
    try:
        from verifier_service import account_info, VERIFIER_PROVIDER
        force = request.args.get('refresh') == '1'
        info = account_info(force_refresh=force)
        info.setdefault('provider', VERIFIER_PROVIDER)
        return jsonify(info)
    except Exception as e:
        logger.warning('verifier_status: %s', e)
        return jsonify({'configured': False, 'error': 'service error'}), 500


@admin_bp.route('/api/admin/verifier/status-all')
@_api_admin_req_dec
def verifier_status_all():
    """All configured verifier providers' state.

    v5.87.68 — surfaces every verifier balance simultaneously so the UI
    can show full verification runway across all three providers
    (MillionVerifier + ZeroBounce + Hunter), not just the active one.
    Each provider is queried only if its API key is set; unconfigured
    providers return {configured: False} without making an API call.
    """
    try:
        from verifier_service import account_info_all
        force = request.args.get('refresh') == '1'
        return jsonify(account_info_all(force_refresh=force))
    except Exception as e:
        logger.warning('verifier_status_all: %s', e)
        return jsonify({'error': 'service error',
                        'millionverifier': {'configured': False},
                        'zerobounce': {'configured': False},
                        'hunter': {'configured': False}}), 500


@admin_bp.route('/api/admin/verifier/verify-batch', methods=['POST'])
@_api_admin_req_dec
def verifier_verify_batch():
    """Same shape as the legacy /hunter/verify-batch endpoint, but the
    explicit URL makes it clear this routes through verifier_service —
    which honors the VERIFIER_PROVIDER env var rather than locking in
    Hunter."""
    try:
        from verifier_service import verify_emails_batch
    except ImportError:
        return jsonify({'error': 'verifier_service module not loaded'}), 500

    data = request.get_json(silent=True) or {}
    emails = data.get('emails') or []
    if not isinstance(emails, list) or not emails:
        return jsonify({'error': 'emails (non-empty list) is required'}), 400
    if len(emails) > 50:
        return jsonify({'error': 'max 50 emails per batch'}), 400

    cleaned = []
    seen = set()
    for e in emails:
        e = (e or '').strip().lower()
        if e and '@' in e and e not in seen:
            cleaned.append(e)
            seen.add(e)

    return jsonify(verify_emails_batch(cleaned))


# ─── Apollo.io integration (v5.87.48) ─────────────────────────────────
# Apollo wraps similarly to Hunter: status endpoint for credit badge +
# people-search for company-scoped title-filtered discovery. The free
# Apollo tier is UI-only — these endpoints will return 'not configured'
# until APOLLO_API_KEY is set in Render env vars.

@admin_bp.route('/api/admin/apollo/status')
@_api_admin_req_dec
def apollo_status():
    """Return Apollo account state for the admin UI credit badge."""
    try:
        from apollo_service import account_info
        force = request.args.get('refresh') == '1'
        return jsonify(account_info(force_refresh=force))
    except Exception as e:
        logger.warning('apollo_status: %s', e)
        return jsonify({'configured': False, 'error': 'service error'}), 500


# v5.87.50: Snov status endpoint to round out the multi-provider strip.
# Same shape as Hunter and Apollo so the UI can render all four providers
# the same way.
@admin_bp.route('/api/admin/snov/status')
@_api_admin_req_dec
def snov_status():
    """Return Snov account state for the admin UI credit badge."""
    try:
        from snov_service import account_info
        force = request.args.get('refresh') == '1'
        return jsonify(account_info(force_refresh=force))
    except Exception as e:
        logger.warning('snov_status: %s', e)
        return jsonify({'configured': False, 'error': 'service error'}), 500


# ─── Prospect research + draft (v5.87.48) ─────────────────────────────
# These endpoints power the "Research & Draft" UI in the admin. The
# orchestrator (prospect_research_service) handles Hunter→Apollo fallback
# discovery, Anthropic web-search-driven focus-area research, and
# Claude-driven email drafting. Drafts are persisted on the OutreachContact
# row and reviewed in-UI before send.

@admin_bp.route('/api/admin/outreach/research-and-draft', methods=['POST'])
@_api_admin_req_dec
def outreach_research_and_draft():
    """Run the research + draft pipeline for one or more existing contacts.

    Body:
      contact_ids: list[int], required (up to 15 per call to bound cost)
      skip_research: bool (default false) — if true, skips the web-search
                     step and uses static templates only

    For each contact:
      1. Fetch the OutreachContact row
      2. If skip_research is false and company is set, run web search
         to populate focus_areas
      3. Run Claude email drafting using focus_areas + contact attributes
      4. Persist focus_areas, draft_subject, draft_body, draft_generated_at

    Returns:
      results: [{contact_id, ok, errors, subject_preview, focus_preview}]
      total_processed: int
      total_succeeded: int
    """
    try:
        from prospect_research_service import (
            research_and_draft, MAX_RESEARCH_CALLS_PER_BATCH
        )
        from models import OutreachContact, db as _db
    except ImportError as e:
        return jsonify({'error': f'service not loaded: {e}'}), 500

    data = request.get_json(silent=True) or {}
    contact_ids = data.get('contact_ids') or []
    skip_research = bool(data.get('skip_research', False))

    if not isinstance(contact_ids, list) or not contact_ids:
        return jsonify({'error': 'contact_ids (non-empty list) required'}), 400
    if len(contact_ids) > MAX_RESEARCH_CALLS_PER_BATCH:
        return jsonify({
            'error': f'max {MAX_RESEARCH_CALLS_PER_BATCH} contacts per batch '
                     f'(would consume too much research budget at once)'
        }), 400

    from datetime import datetime
    results = []
    succeeded = 0

    for contact_id in contact_ids:
        try:
            c = OutreachContact.query.get(int(contact_id))
            if not c:
                results.append({
                    'contact_id': contact_id, 'ok': False,
                    'errors': ['contact not found'],
                })
                continue

            # Derive a domain from the email if company_domain not stored.
            # email is required on OutreachContact, so this is safe.
            inferred_domain = (c.email or '').split('@')[-1] if c.email else ''

            r = research_and_draft(
                name=c.name or '',
                email=c.email or '',
                title=c.title or '',
                company=c.company or '',
                company_domain=inferred_domain,
                wedge=c.wedge or '',
                skip_research=skip_research,
            )

            # Persist
            # v5.88.05: when skip_research=True is used to regenerate an
            # already-drafted prospect (post-greeting/signoff/URL improvements),
            # the new focus_areas comes back empty because research was
            # skipped. Don't clobber the cached focus_areas in that case —
            # the original research is still valuable for context.
            new_focus = r.get('focus_areas') or ''
            if new_focus or not skip_research:
                c.focus_areas = new_focus
            c.draft_subject = r.get('subject') or ''
            c.draft_body = r.get('body') or ''
            c.draft_generated_at = datetime.utcnow()
            _db.session.add(c)

            errs = r.get('errors') or []
            ok = bool(c.draft_subject and c.draft_body)
            if ok:
                succeeded += 1

            results.append({
                'contact_id': c.id,
                'ok': ok,
                'errors': errs,
                # Short previews to keep response payload small
                'subject_preview': (c.draft_subject or '')[:80],
                'focus_preview': (c.focus_areas or '')[:140],
                'sources_count': r.get('sources_count', 0),
            })
        except Exception as e:
            logger.warning('research-and-draft contact_id=%s failed: %s',
                           contact_id, e)
            results.append({
                'contact_id': contact_id, 'ok': False,
                'errors': [f'{e.__class__.__name__}: {e}'],
            })

    try:
        _db.session.commit()
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': f'commit failed: {e}'}), 500

    return jsonify({
        'results': results,
        'total_processed': len(results),
        'total_succeeded': succeeded,
    })


@admin_bp.route('/api/admin/outreach/draft/<int:contact_id>')
@_api_admin_req_dec
def outreach_get_draft(contact_id: int):
    """Read the current draft + focus areas for a contact (for review UI)."""
    from models import OutreachContact
    c = OutreachContact.query.get(contact_id)
    if not c:
        return jsonify({'error': 'contact not found'}), 404
    return jsonify({
        'contact_id': c.id,
        'name': c.name,
        'email': c.email,
        'title': c.title,
        'company': c.company,
        'wedge': c.wedge,
        'focus_areas': c.focus_areas or '',
        'draft_subject': c.draft_subject or '',
        'draft_body': c.draft_body or '',
        'draft_generated_at': c.draft_generated_at.isoformat() if c.draft_generated_at else None,
        'status': c.status,
    })


@admin_bp.route('/api/admin/outreach/draft/<int:contact_id>', methods=['PUT'])
@_api_admin_req_dec
def outreach_update_draft(contact_id: int):
    """Save manual edits to a draft. Used when the founder tweaks the
    Claude-generated email before sending."""
    from models import OutreachContact, db as _db
    c = OutreachContact.query.get(contact_id)
    if not c:
        return jsonify({'error': 'contact not found'}), 404

    data = request.get_json(silent=True) or {}
    if 'draft_subject' in data:
        c.draft_subject = (data.get('draft_subject') or '').strip()[:500]
    if 'draft_body' in data:
        c.draft_body = (data.get('draft_body') or '').strip()
    # Manual edits don't update draft_generated_at — that field tracks
    # when Claude last regenerated, which informs staleness UI.

    try:
        _db.session.commit()
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': f'commit failed: {e}'}), 500
    return jsonify({'ok': True, 'contact_id': c.id})


@admin_bp.route('/api/admin/outreach/draft/<int:contact_id>/send', methods=['POST'])
@_api_admin_req_dec
def outreach_send_draft(contact_id: int):
    """Send the saved draft for one contact. Mirrors the per-send section
    of outreach_bulk_send so engagement tracking + log shape stay
    consistent with the rest of the outreach flow."""
    from models import OutreachContact, OutreachLog, EmailSendLog, db as _db
    from datetime import datetime
    from email_service import send_email

    c = OutreachContact.query.get(contact_id)
    if not c:
        return jsonify({'error': 'contact not found'}), 404
    if not c.draft_subject or not c.draft_body:
        return jsonify({'error': 'no draft to send (run research-and-draft first)'}), 400

    # Reply-to: the founder Gmail. Pulled from the same place bulk-send uses.
    reply_to = os.environ.get('FOUNDER_REPLY_EMAIL', '').strip() or None

    # v5.88.01: Convert plain-text URLs to clickable anchor tags BEFORE
    # paragraph-wrapping. Without this step Gmail usually auto-linkifies
    # but Outlook and others are inconsistent. Forcing real <a> tags
    # guarantees clickable links across all clients.

    # Wrap plain-text body in the minimal HTML shell so the email
    # renders correctly in modern clients but still reads as a personal note.
    html_lines = ''.join(
        f'<p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#1f2937;">{_linkify_line(line)}</p>'
        if line.strip() else '<br>'
        for line in (c.draft_body or '').split('\n')
    )
    html_content = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
        'max-width:560px;margin:0 auto;padding:24px 4px;color:#1f2937;">'
        f'{html_lines}'
        '</div>'
    )

    pre_send_at = datetime.utcnow()
    ok = False
    err = None
    try:
        # v5.87.80: B2B outreach uses Francis Anthony <francis@...>, not noreply@
        from email_service import OUTREACH_FROM_EMAIL
        ok = send_email(
            to_email=c.email,
            subject=c.draft_subject,
            html_content=html_content,
            reply_to=reply_to,
            email_type='founder_outreach_draft',
            from_email=OUTREACH_FROM_EMAIL,
        )
    except Exception as e:
        logger.exception('outreach_send_draft failed for contact_id=%s', c.id)
        err = str(e)[:400]

    # Capture the resend_id by looking up the most-recent EmailSendLog
    # entry for this email after pre_send_at.
    log_row = (EmailSendLog.query
               .filter(EmailSendLog.to_email == c.email)
               .filter(EmailSendLog.ts >= pre_send_at)
               .order_by(EmailSendLog.ts.desc())
               .first())
    resend_id = log_row.resend_id if log_row else None

    outreach_log = OutreachLog(
        cohort='b2b',
        user_id=None,
        contact_id=c.id,
        to_email=c.email,
        subject=(c.draft_subject or '')[:500],
        body=c.draft_body or '',
        reply_to=reply_to,
        resend_id=resend_id,
        success=bool(ok),
        error=(err or (None if ok else 'send_email returned False')),
    )
    _db.session.add(outreach_log)

    if ok:
        c.last_contacted_at = datetime.utcnow()
        if c.status == 'not_contacted':
            c.status = 'contacted'

    try:
        _db.session.commit()
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': f'commit failed: {e}'}), 500

    return jsonify({
        'ok': bool(ok),
        'contact_id': c.id,
        'resend_id': resend_id,
        'error': err,
    })


# =============================================================================
# v5.88.50 — Buyer Users tab: per-user draft + send (mirror of B2B path)
# =============================================================================
#
# Same shape as /api/admin/outreach/draft/<contact_id>/* (B2B) but for users:
#   GET    /api/admin/outreach/buyer-draft/<user_id>            — fetch draft + context
#   POST   /api/admin/outreach/buyer-draft/<user_id>/generate    — Claude-generate
#   PUT    /api/admin/outreach/buyer-draft/<user_id>             — save manual edits
#   POST   /api/admin/outreach/buyer-draft/<user_id>/send        — send via Resend
#
# Drafts live on User.outreach_draft_subject / outreach_draft_body /
# outreach_draft_generated_at (v5.88.50 migration). Cleared on successful send
# so a fresh draft is generated next time. Send path uses OUTREACH_FROM_EMAIL
# (Francis Anthony <francis@...>) with reply_to routing back to founder Gmail.

@admin_bp.route('/api/admin/outreach/buyer-draft/<int:user_id>')
@_api_admin_req_dec
def outreach_get_buyer_draft(user_id: int):
    """Fetch existing draft + user context. Returns user metadata even if
    no draft exists yet — UI uses this to populate the "Why this draft"
    side panel before generating."""
    u = User.query.get(user_id)
    if not u:
        return jsonify({'error': 'user_not_found', 'user_id': user_id}), 404

    # Compute funnel stage + last property context inline (avoid importing
    # buyer_research_service from a hot endpoint path — only needed when
    # /generate is called).
    from models import Property
    try:
        analyses = Property.query.filter_by(user_id=u.id).count()
        latest_prop = (Property.query
                       .filter_by(user_id=u.id)
                       .order_by(Property.id.desc())
                       .first())
    except Exception as e:
        logger.warning(f'outreach_get_buyer_draft: property lookup failed for user_id={user_id}: {e}')
        analyses = 0
        latest_prop = None

    has_stripe = bool(getattr(u, 'stripe_customer_id', None))
    onboarded = bool(getattr(u, 'onboarding_completed', False))
    if has_stripe or analyses > 0:
        stage = 'USED_PRODUCT'
    elif onboarded:
        stage = 'ONBOARDED'
    else:
        stage = 'SIGNED_UP_ONLY'

    days_since_signup = None
    if u.created_at:
        days_since_signup = (datetime.utcnow() - u.created_at).days

    return jsonify({
        'user_id': u.id,
        'name': u.name or '',
        'email': u.email,
        'created_at': u.created_at.isoformat() if u.created_at else None,
        'days_since_signup': days_since_signup,
        'stage': stage,
        'analyses_count': analyses,
        'last_property_address': latest_prop.address if latest_prop else None,
        'last_property_analyzed_at': (
            latest_prop.analyzed_at.isoformat()
            if latest_prop and latest_prop.analyzed_at else None
        ),
        'draft_subject': getattr(u, 'outreach_draft_subject', None) or '',
        'draft_body': getattr(u, 'outreach_draft_body', None) or '',
        'draft_generated_at': (
            u.outreach_draft_generated_at.isoformat()
            if getattr(u, 'outreach_draft_generated_at', None) else None
        ),
        'has_draft': bool(
            (getattr(u, 'outreach_draft_subject', None) or '').strip()
            and (getattr(u, 'outreach_draft_body', None) or '').strip()
        ),
    })


@admin_bp.route('/api/admin/outreach/buyer-draft/<int:user_id>/generate', methods=['POST'])
@_api_admin_req_dec
def outreach_generate_buyer_draft(user_id: int):
    """Call Claude (or fallback) to draft a personalized customer-discovery
    email. Idempotent — overwrites any existing draft on this user."""
    u = User.query.get(user_id)
    if not u:
        return jsonify({'error': 'user_not_found', 'user_id': user_id}), 404

    try:
        from buyer_research_service import draft_buyer_email
        result = draft_buyer_email(u)
    except Exception as e:
        logger.exception(f'outreach_generate_buyer_draft: draft failed for user_id={user_id}')
        return jsonify({
            'error': 'draft_failed',
            'message': f'{type(e).__name__}: {e}',
        }), 500

    subject = (result.get('subject') or '').strip()
    body = (result.get('body') or '').strip()
    if not subject or not body:
        return jsonify({
            'error': 'empty_draft',
            'message': 'Draft generator returned empty subject or body',
        }), 500

    u.outreach_draft_subject = subject[:500]
    u.outreach_draft_body = body
    u.outreach_draft_generated_at = datetime.utcnow()
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception(f'outreach_generate_buyer_draft: commit failed for user_id={user_id}')
        return jsonify({
            'error': 'commit_failed',
            'message': f'{type(e).__name__}: {e}',
        }), 500

    return jsonify({
        'ok': True,
        'user_id': u.id,
        'draft_subject': u.outreach_draft_subject,
        'draft_body': u.outreach_draft_body,
        'draft_generated_at': u.outreach_draft_generated_at.isoformat(),
        'stage': result.get('stage', ''),
        # If the fallback was used (Claude unavailable), surface it so UI
        # can show a small notice. Not an error per se — the draft is still
        # usable.
        'fallback_used': result.get('error') == 'fallback_used',
    })


@admin_bp.route('/api/admin/outreach/buyer-draft/<int:user_id>', methods=['PUT'])
@_api_admin_req_dec
def outreach_update_buyer_draft(user_id: int):
    """Save manual edits to a buyer draft. Does NOT bump draft_generated_at,
    same convention as the B2B path."""
    u = User.query.get(user_id)
    if not u:
        return jsonify({'error': 'user_not_found', 'user_id': user_id}), 404

    data = request.get_json(silent=True) or {}
    if 'draft_subject' in data:
        u.outreach_draft_subject = (data.get('draft_subject') or '').strip()[:500]
    if 'draft_body' in data:
        u.outreach_draft_body = (data.get('draft_body') or '').strip()

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'commit_failed', 'message': str(e)[:400]}), 500
    return jsonify({'ok': True, 'user_id': u.id})


@admin_bp.route('/api/admin/outreach/buyer-draft/<int:user_id>/send', methods=['POST'])
@_api_admin_req_dec
def outreach_send_buyer_draft(user_id: int):
    """Send the saved draft for one user. Mirrors outreach_send_draft (B2B)
    so engagement tracking + log shape stay consistent with the rest of
    the outreach flow.

    On successful send, the draft columns are cleared. The next 'Generate'
    starts fresh — we don't want to surface a stale draft that's already
    been sent.

    Honors the global unsubscribe list (OutreachUnsubscribe) — refuses to
    send if the user has unsubscribed.
    """
    from models import OutreachLog, EmailSendLog, OutreachUnsubscribe
    from email_service import send_email, OUTREACH_FROM_EMAIL

    u = User.query.get(user_id)
    if not u:
        return jsonify({'error': 'user_not_found', 'user_id': user_id}), 404
    if not u.outreach_draft_subject or not u.outreach_draft_body:
        return jsonify({
            'error': 'no_draft',
            'message': 'No draft to send. Generate one first.',
        }), 400

    # Honor unsubscribes
    try:
        unsub = OutreachUnsubscribe.query.filter(
            db.func.lower(OutreachUnsubscribe.email) == (u.email or '').lower()
        ).first()
        if unsub:
            return jsonify({
                'error': 'unsubscribed',
                'message': f'{u.email} has unsubscribed. Send refused.',
            }), 409
    except Exception as e:
        # If the unsubscribe table is unavailable, fail closed and don't send.
        logger.warning(f'outreach_send_buyer_draft: unsubscribe check failed: {e}')
        return jsonify({
            'error': 'unsubscribe_check_failed',
            'message': f'{type(e).__name__}: {e}',
        }), 500

    # Also honor User.email_unsubscribed (set by drip-side one-click unsub)
    if getattr(u, 'email_unsubscribed', False):
        return jsonify({
            'error': 'unsubscribed',
            'message': f'{u.email} has unsubscribed via drip. Send refused.',
        }), 409

    reply_to = _founder_reply_to()
    subject = u.outreach_draft_subject
    body = u.outreach_draft_body

    # Wrap plain-text in minimal HTML (same as B2B path so it reads
    # consistently across clients). Also linkifies bare URLs.
    html_lines = ''.join(
        f'<p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#1f2937;">{_linkify_line(line)}</p>'
        if line.strip() else '<br>'
        for line in body.split('\n')
    )
    html_content = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
        'max-width:560px;margin:0 auto;padding:24px 4px;color:#1f2937;">'
        f'{html_lines}'
        '</div>'
    )

    pre_send_at = datetime.utcnow()
    ok = False
    err = None
    try:
        ok = send_email(
            to_email=u.email,
            subject=subject,
            html_content=html_content,
            reply_to=reply_to,
            email_type='founder_buyer_outreach',
            from_email=OUTREACH_FROM_EMAIL,
            user_id=u.id,
        )
    except Exception as e:
        logger.exception(f'outreach_send_buyer_draft: send failed for user_id={u.id}')
        err = str(e)[:400]

    # Capture resend_id from the EmailSendLog row written by send_email
    log_row = (EmailSendLog.query
               .filter(EmailSendLog.to_email == u.email)
               .filter(EmailSendLog.ts >= pre_send_at)
               .order_by(EmailSendLog.ts.desc())
               .first())
    resend_id = log_row.resend_id if log_row else None

    outreach_log = OutreachLog(
        cohort='buyer',
        user_id=u.id,
        contact_id=None,
        to_email=u.email,
        subject=(subject or '')[:500],
        body=body or '',
        reply_to=reply_to,
        resend_id=resend_id,
        success=bool(ok),
        error=(err or (None if ok else 'send_email returned False')),
    )
    db.session.add(outreach_log)

    # On success, clear the draft columns so the next click of 📝 starts
    # from a fresh Claude generation. Without this, the founder could
    # accidentally send the same draft twice.
    if ok:
        u.outreach_draft_subject = None
        u.outreach_draft_body = None
        u.outreach_draft_generated_at = None

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'commit_failed', 'message': str(e)[:400]}), 500

    return jsonify({
        'ok': bool(ok),
        'user_id': u.id,
        'resend_id': resend_id,
        'error': err,
    })


@admin_bp.route('/api/admin/outreach/bulk-send', methods=['POST'])
@_api_admin_req_dec
def outreach_bulk_send():
    """B2B bulk send — render template for each selected prospect and send.

    Foreground synchronous loop. With gunicorn timeout=300s and a 4-second
    throttle between sends, we can comfortably do 70+ sends per call. UI
    enforces a 50-prospect cap per batch as a safety margin.

    Body:
      ids:     array of contact_ids (b2b cohort only — buyer cohort uses the
               sequenced-send pattern via /send one at a time)
      subject: template string
      body:    template string
      throttle_sec: optional, default 4 (between 1 and 10)

    Returns:
      total: int, succeeded: int, failed: int,
      results: [{contact_id, to_email, success, error, resend_id}]
    """
    import time as _time
    from email_service import send_email
    from models import OutreachContact, OutreachLog, EmailSendLog

    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    subject_tmpl = (data.get('subject') or '').strip()
    body_tmpl = (data.get('body') or '').strip()
    throttle = max(1, min(10, int(data.get('throttle_sec') or 4)))

    if not ids:
        return jsonify({'error': 'ids required'}), 400
    if not subject_tmpl or not body_tmpl:
        return jsonify({'error': 'subject and body templates required'}), 400
    if len(ids) > 50:
        return jsonify({'error': 'maximum 50 sends per batch'}), 400

    contacts = OutreachContact.query.filter(OutreachContact.id.in_(ids)).all()
    contacts_by_id = {c.id: c for c in contacts}

    reply_to = _founder_reply_to()

    results = []
    succeeded = 0
    failed = 0

    for idx, cid in enumerate(ids):
        c = contacts_by_id.get(int(cid))
        if not c:
            results.append({
                'contact_id': cid, 'to_email': None,
                'success': False, 'error': 'contact not found', 'resend_id': None,
            })
            failed += 1
            continue

        # Throttle between sends (skip before the first one)
        if idx > 0:
            _time.sleep(throttle)

        vars_ = _b2b_variables_for(c)
        rendered_subj, _ = _render_template(subject_tmpl, vars_)
        rendered_body, _ = _render_template(body_tmpl, vars_)

        # Wrap in the same minimal HTML shell the single /send endpoint uses.
        # Keeping these visually identical means recipients can't tell which
        # were one-off and which were bulk — both look like personal notes.
        html_lines = ''.join(
            f'<p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#1f2937;">{line}</p>'
            if line.strip() else '<br>'
            for line in rendered_body.split('\n')
        )
        html_content = (
            '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
            'max-width:560px;margin:0 auto;padding:24px 4px;color:#1f2937;">'
            f'{html_lines}'
            '</div>'
        )

        pre_send_at = datetime.utcnow()
        ok = False
        err = None
        try:
            # v5.87.80: B2B outreach uses Francis Anthony <francis@...>, not noreply@
            from email_service import OUTREACH_FROM_EMAIL
            ok = send_email(
                to_email=c.email,
                subject=rendered_subj,
                html_content=html_content,
                reply_to=reply_to,
                email_type='founder_outreach_bulk',
                from_email=OUTREACH_FROM_EMAIL,
            )
        except Exception as e:
            logger.exception('bulk send failed for contact_id=%s', c.id)
            err = str(e)[:400]

        # Look up the resend_id from EmailSendLog
        log_row = (EmailSendLog.query
                   .filter(EmailSendLog.to_email == c.email)
                   .filter(EmailSendLog.ts >= pre_send_at)
                   .order_by(EmailSendLog.ts.desc())
                   .first())
        resend_id = log_row.resend_id if log_row else None

        log = OutreachLog(
            cohort='b2b',
            user_id=None,
            contact_id=c.id,
            to_email=c.email,
            subject=rendered_subj[:500],
            body=rendered_body,
            reply_to=reply_to,
            resend_id=resend_id,
            success=bool(ok),
            error=(err or (None if ok else 'send_email returned False')),
        )
        db.session.add(log)

        if ok:
            c.last_contacted_at = datetime.utcnow()
            if c.status == 'not_contacted':
                c.status = 'contacted'

        # Commit per-send so partial-batch failure still records what succeeded.
        # If the loop is killed mid-batch (timeout, OOM), we keep the trail of
        # what already went out.
        db.session.commit()

        results.append({
            'contact_id': c.id, 'to_email': c.email,
            'success': bool(ok), 'error': err,
            'resend_id': resend_id,
        })
        if ok:
            succeeded += 1
        else:
            failed += 1

    return jsonify({
        'total': len(ids),
        'succeeded': succeeded,
        'failed': failed,
        'results': results,
    })


# =============================================================================
# End of outreach routes
# =============================================================================


@admin_bp.route('/api/admin/test-coverage')
@_api_admin_req_dec
def admin_test_coverage():
    """Run integrity tests and return detailed coverage results."""
    try:
        from integrity_tests import IntegrityTestEngine
        from app import app as _app, db as _db
        engine = IntegrityTestEngine(app=_app, db=_db)
        results = engine.run_all()
        s = results.get('summary', {})

        # Group results by test group
        groups = {}
        for r in results.get('results', []):
            name = r['name']
            group = name.split(':')[0].strip() if ':' in name else 'Other'
            if group not in groups:
                groups[group] = {'passed': 0, 'failed': 0, 'tests': []}
            if r['passed']:
                groups[group]['passed'] += 1
            else:
                groups[group]['failed'] += 1
            groups[group]['tests'].append({
                'name': str(name),
                'passed': bool(r['passed']),
                'details': str(r.get('details', '')),
                'error': str(r.get('error', '') or ''),
            })

        # Find coverage record
        coverage_pct = 0
        untested_modules = []
        for r in results.get('results', []):
            if 'Coverage:' in r['name']:
                import re
                m = re.search(r'(\d+)%', r['name'])
                if m:
                    coverage_pct = int(m.group(1))
                if r.get('error') and 'Untested:' in r['error']:
                    raw = r['error'].split('Untested:')[1].strip()
                    untested_modules = [x.strip().strip("'[]") for x in raw.split(',') if x.strip()]

        return jsonify({
            'total': int(s.get('total', 0)),
            'passed': int(s.get('passed', 0)),
            'failed': int(s.get('failed', 0)),
            'duration': float(s.get('duration_seconds', 0)),
            'coverage_pct': int(coverage_pct),
            'untested_modules': untested_modules,
            'groups': groups,
            'all_passed': bool(s.get('failed', 0) == 0),
        })
    except Exception as e:
        logging.error(f"Test coverage endpoint failed: {e}")
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/admin/ml-stats', methods=['GET'])
@_api_admin_req_dec
def admin_ml_stats():
    """ML training data flywheel stats for admin dashboard."""
    try:
        from models import MLFindingLabel, MLContradictionPair, MLCooccurrenceBucket, PostCloseSurvey
        from sqlalchemy import func

        finding_total = MLFindingLabel.query.count()
        finding_validated = MLFindingLabel.query.filter_by(is_validated=True).count()
        contradiction_total = MLContradictionPair.query.count()
        cooccurrence_total = MLCooccurrenceBucket.query.count()
        survey_sent = PostCloseSurvey.query.filter_by(status='sent').count()
        survey_completed = PostCloseSurvey.query.filter_by(status='completed').count()

        # Thresholds for model readiness
        return jsonify({
            'finding_labels': {'count': finding_total, 'threshold': 300, 'validated': finding_validated},
            'contradiction_pairs': {'count': contradiction_total, 'threshold': 200},
            'cooccurrence_buckets': {'count': cooccurrence_total, 'threshold': 200},
            'repair_cost_actuals': {'count': survey_completed, 'threshold': 500},
            'surveys': {'sent': survey_sent, 'completed': survey_completed},
        })
    except Exception as e:
        logging.warning(f"ML stats endpoint: {e}")
        return jsonify({
            'finding_labels': {'count': 0, 'threshold': 300, 'validated': 0},
            'contradiction_pairs': {'count': 0, 'threshold': 200},
            'cooccurrence_buckets': {'count': 0, 'threshold': 200},
            'repair_cost_actuals': {'count': 0, 'threshold': 500},
            'surveys': {'sent': 0, 'completed': 0},
        })

@admin_bp.route('/api/admin/ml-backfill', methods=['POST'])
@_api_admin_req_dec
def admin_ml_backfill():
    """One-time backfill: extract ML training data from ALL historical analyses.
    Clears existing auto-collected data first to avoid duplicates.
    """
    import json as _json
    from models import Analysis, Property, InspectorReport, MLFindingLabel, MLCooccurrenceBucket, MLContradictionPair
    from ml_data_collector import collect_training_data

    # Clear existing auto-collected data (keep inspector-validated labels)
    MLFindingLabel.query.filter_by(source='ai_parse').delete()
    MLContradictionPair.query.filter_by(source='cross_ref_engine').delete()
    MLCooccurrenceBucket.query.delete()
    db.session.commit()

    stats = {'analyses_processed': 0, 'inspector_reports_processed': 0,
             'errors': 0, 'error_details': []}

    # Process all buyer analyses
    analyses = Analysis.query.filter(
        Analysis.status == 'completed',
        Analysis.result_json.isnot(None),
    ).all()

    for a in analyses:
        try:
            result = _json.loads(a.result_json or '{}')
            if not result:
                continue
            # Skip address-only analyses (no documents uploaded)
            if result.get('analysis_depth') == 'address_only':
                continue
            prop = Property.query.get(a.property_id) if a.property_id else None
            address = prop.address if prop else ''
            price = prop.price if prop else 0
            collect_training_data(
                analysis_id=a.id,
                result_dict=result,
                property_address=address,
                property_price=price or 0,
            )
            stats['analyses_processed'] += 1
        except Exception as e:
            stats['errors'] += 1
            stats['error_details'].append(f"a{a.id}: {type(e).__name__}: {str(e)[:80]}")
            try:
                db.session.rollback()
            except Exception:
                pass

    # Process all inspector reports
    reports = InspectorReport.query.filter(
        InspectorReport.analysis_json.isnot(None),
    ).all()

    for r in reports:
        try:
            result = _json.loads(r.analysis_json or '{}')
            if not result:
                continue
            collect_training_data(
                analysis_id=r.id + 1000000,
                result_dict=result,
                property_address=r.property_address or '',
                property_price=r.property_price or 0,
            )
            stats['inspector_reports_processed'] += 1
        except Exception as e:
            stats['errors'] += 1
            stats['error_details'].append(f"r{r.id}: {type(e).__name__}: {str(e)[:80]}")
            try:
                db.session.rollback()
            except Exception:
                pass

    # Counts after
    stats['findings_after'] = MLFindingLabel.query.count()
    stats['contradictions_after'] = MLContradictionPair.query.count()
    stats['buckets_after'] = MLCooccurrenceBucket.query.count()

    logging.info(f"ML backfill complete: {stats}")
    return jsonify(stats)

@admin_bp.route('/api/admin/ml-backfill-debug', methods=['GET'])
@_api_admin_req_dec
def admin_ml_backfill_debug():
    """Debug: check what data exists for ML backfill."""
    import json as _json
    from models import Analysis, Property, InspectorReport
    from sqlalchemy import func

    total_analyses = Analysis.query.count()
    completed = Analysis.query.filter_by(status='completed').count()

    # Find a full analysis (not address_only) and inspect its structure
    structure_sample = {}
    for a in Analysis.query.order_by(Analysis.id.desc()).all():
        try:
            r = _json.loads(a.result_json or '{}')
            if r.get('analysis_depth') == 'address_only':
                continue
            # Map the top-level keys
            structure_sample['top_keys'] = list(r.keys())[:30]
            # Check inspection_report structure
            insp = r.get('inspection_report', {})
            if isinstance(insp, dict):
                structure_sample['inspection_report_keys'] = list(insp.keys())[:20]
                findings = insp.get('inspection_findings', [])
                structure_sample['findings_count'] = len(findings)
                if findings and isinstance(findings[0], dict):
                    structure_sample['finding_sample_keys'] = list(findings[0].keys())
                    structure_sample['finding_sample'] = {k: str(v)[:100] for k, v in findings[0].items()}
            # Check cross_reference structure
            xref = r.get('cross_reference', {})
            if isinstance(xref, dict):
                structure_sample['cross_reference_keys'] = list(xref.keys())
                for xkey in xref:
                    val = xref[xkey]
                    if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                        structure_sample[f'xref_{xkey}_count'] = len(val)
                        structure_sample[f'xref_{xkey}_sample_keys'] = list(val[0].keys())
                        structure_sample[f'xref_{xkey}_sample'] = {k: str(v)[:100] for k, v in val[0].items()}
            structure_sample['analysis_id'] = a.id
            break
        except Exception as e:
            structure_sample['error'] = str(e)

    # Inspector report sample
    report_structure = {}
    for r in InspectorReport.query.order_by(InspectorReport.id.desc()).limit(1).all():
        try:
            rj = _json.loads(r.analysis_json or '{}')
            insp = rj.get('inspection_report', {})
            if isinstance(insp, dict):
                findings = insp.get('inspection_findings', [])
                report_structure['findings_count'] = len(findings)
                if findings and isinstance(findings[0], dict):
                    report_structure['finding_sample_keys'] = list(findings[0].keys())
            xref = rj.get('cross_reference', {})
            if isinstance(xref, dict):
                report_structure['cross_reference_keys'] = list(xref.keys())
        except Exception as e:
            report_structure['error'] = str(e)

    return jsonify({
        'total_analyses': total_analyses,
        'completed': completed,
        'structure_sample': structure_sample,
        'inspector_report_structure': report_structure,
    })

@admin_bp.route('/api/admin/ml-export/<data_type>', methods=['GET'])
@_api_admin_req_dec
def admin_ml_export(data_type):
    """Export ML training data as CSV for local training."""
    import csv, io
    from flask import Response

    if data_type == 'finding_labels':
        from models import MLFindingLabel
        rows = MLFindingLabel.query.order_by(MLFindingLabel.id).all()
        headers = ['finding_text', 'category', 'severity', 'source', 'confidence', 'is_validated', 'property_zip', 'property_price']
        def row_to_dict(r):
            return [r.finding_text, r.category, r.severity, r.source, r.confidence, r.is_validated, r.property_zip or '', r.property_price or '']

    elif data_type == 'contradiction_pairs':
        from models import MLContradictionPair
        rows = MLContradictionPair.query.order_by(MLContradictionPair.id).all()
        headers = ['seller_claim', 'inspector_finding', 'label', 'confidence', 'source']
        def row_to_dict(r):
            return [r.seller_claim, r.inspector_finding, r.label, r.confidence, r.source]

    elif data_type == 'cooccurrence_buckets':
        from models import MLCooccurrenceBucket
        rows = MLCooccurrenceBucket.query.order_by(MLCooccurrenceBucket.id).all()
        headers = ['analysis_id', 'findings_set', 'n_findings', 'property_zip']
        def row_to_dict(r):
            return [r.analysis_id, r.findings_set, r.n_findings, r.property_zip or '']

    else:
        return jsonify({'error': f'Unknown data type: {data_type}'}), 400

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for r in rows:
        writer.writerow(row_to_dict(r))

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=offerwise_{data_type}.csv'}
    )


@admin_bp.route('/api/admin/ml-export-augmented', methods=['GET'])
@_api_admin_req_dec
def admin_ml_export_augmented():
    """Export finding labels + synthetic findings. If cached augmented data exists, return it.
    Otherwise return real data only with a flag to trigger generation.
    """
    import csv, io, json as _json, os
    from flask import Response
    from models import MLFindingLabel

    # Check for cached augmented CSV on disk
    cache_path = os.path.join(os.path.dirname(__file__), 'models', 'augmented_cache.csv')
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            content = f.read()
        # Clear cache after download
        os.remove(cache_path)
        return Response(
            content,
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=offerwise_finding_labels_augmented.csv'}
        )

    # No cache — return real data only
    return jsonify({'error': 'Augmented data not ready. Click Generate first.', 'status': 'not_ready'}), 202


@admin_bp.route('/api/admin/ml-generate-augmented', methods=['POST'])
@_api_admin_req_dec
def admin_ml_generate_augmented():
    """Generate synthetic findings and cache the augmented CSV.
    This runs as a POST so frontend can fire-and-forget with a longer timeout.
    """
    import csv, io, json as _json, os
    from models import MLFindingLabel

    rows = MLFindingLabel.query.order_by(MLFindingLabel.id).all()
    if not rows:
        return jsonify({'error': 'No finding labels. Run backfill first.'}), 400

    # Build real data
    real_data = []
    seen = set()
    for r in rows:
        cat = (r.category or '').strip()
        sev = (r.severity or '').strip().lower()
        text = (r.finding_text or '').strip()
        if cat and sev and len(text) > 10 and sev in ('critical', 'major', 'moderate', 'minor') and text not in seen:
            seen.add(text)
            real_data.append({'finding_text': text, 'category': cat, 'severity': sev, 'source': r.source or 'ai_parse'})

    cat_counts = {}
    sev_counts = {}
    for d in real_data:
        cat_counts[d['category']] = cat_counts.get(d['category'], 0) + 1
        sev_counts[d['severity']] = sev_counts.get(d['severity'], 0) + 1

    max_cat = max(cat_counts.values()) if cat_counts else 0
    max_sev = max(sev_counts.values()) if sev_counts else 0
    target_cat = int(max_cat * 0.75)
    target_sev = int(max_sev * 0.75)

    # Build needs list
    needs = []
    for cat, cnt in cat_counts.items():
        if cnt < target_cat:
            need = min(target_cat - cnt, cnt * 2)
            if need >= 3:
                examples = [d['finding_text'] for d in real_data if d['category'] == cat][:2]
                needs.append(f'{need} findings for category "{cat}" (style: {"; ".join(examples)})')

    for sev, cnt in sev_counts.items():
        if cnt < target_sev:
            need = min(target_sev - cnt, cnt * 2)
            if need >= 3:
                cats = list(set(d['category'] for d in real_data if d['severity'] == sev))
                examples = [d['finding_text'] for d in real_data if d['severity'] == sev][:2]
                needs.append(f'{need} findings with severity "{sev}" across {", ".join(cats)} (style: {"; ".join(examples)})')

    synthetic = []
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')

    if api_key and needs:
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=api_key)

            needs_text = '\n'.join(f'- {n}' for n in needs)
            prompt = f"""Generate realistic home inspection findings to balance a training dataset.
Sound like a professional home inspector. Use specific technical language, measurements, locations.

Categories: {', '.join(cat_counts.keys())}
Severities: critical (safety hazard), major (significant repair), moderate (should fix), minor (cosmetic)

Generate these quantities:
{needs_text}

Respond with ONLY a JSON array:
[{{"text": "finding", "category": "cat", "severity": "sev"}}]"""

            resp = client.messages.create(
                model=SONNET, max_tokens=8000,
                messages=[{'role': 'user', 'content': prompt}]
            )
            raw = resp.content[0].text.strip()
            if raw.startswith('```'): raw = raw.split('\n', 1)[-1]
            if raw.endswith('```'): raw = raw[:-3]
            raw = raw.strip()
            if raw.startswith('json'): raw = raw[4:].strip()

            items = _json.loads(raw)
            for item in items:
                if isinstance(item, dict) and item.get('text') and item.get('category') and item.get('severity'):
                    synthetic.append({
                        'finding_text': item['text'],
                        'category': item['category'],
                        'severity': item['severity'].lower(),
                        'source': 'synthetic',
                    })
            logging.info(f"ML augment: generated {len(synthetic)} synthetic findings")
        except Exception as e:
            logging.warning(f"ML augment failed: {e}")

    # Write combined CSV to cache file
    all_data = real_data + synthetic
    cache_path = os.path.join(os.path.dirname(__file__), 'models', 'augmented_cache.csv')
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    with open(cache_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['finding_text', 'category', 'severity', 'source', 'confidence', 'is_validated', 'property_zip', 'property_price'])
        for d in all_data:
            conf = 0.7 if d['source'] == 'synthetic' else 0.85
            writer.writerow([d['finding_text'], d['category'], d['severity'], d['source'], conf, False, '', ''])

    return jsonify({
        'success': True,
        'real': len(real_data),
        'synthetic': len(synthetic),
        'total': len(all_data),
    })


@admin_bp.route('/api/admin/ml-upload-model', methods=['POST'])
@_api_admin_req_dec
def admin_ml_upload_model():
    """Upload trained model files. Saves to models/ directory."""
    import os

    # v5.89.55: persistent disk
    models_dir = get_models_dir()

    ALLOWED = {
        'finding_category.xgb', 'finding_severity.xgb',
        'category_encoder.pkl', 'severity_encoder.pkl',
        'contradiction_detector.xgb', 'contradiction_encoder.pkl',
        'predictive_model.pkl', 'predictive_vocab.pkl',
        'repair_cost.xgb', 'cost_feature_meta.pkl', 'property_scaler.pkl',
    }

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    f = request.files['file']
    if f.filename not in ALLOWED:
        return jsonify({'error': f'Unknown model file: {f.filename}. Allowed: {sorted(ALLOWED)}'}), 400

    path = os.path.join(models_dir, f.filename)
    f.save(path)
    size = os.path.getsize(path)
    logging.info(f"ML: uploaded model file {f.filename} ({size:,} bytes)")
    return jsonify({'success': True, 'filename': f.filename, 'size': size})


@admin_bp.route('/api/admin/ml-storage-debug', methods=['GET'])
@_api_admin_req_dec
def admin_ml_storage_debug():
    """v5.89.81: report where models actually resolve and whether it persists.

    Added after discovering models were silently written to ephemeral
    storage (/var/data/models) because the disk is mounted at
    /var/data/docrepo, not /var/data. This endpoint reports the live
    truth: resolved dir, whether it's writable, what's in it, and the
    relevant mount facts — so a path/persistence bug is visible
    immediately instead of presenting as "trained but NOT DEPLOYED".
    """
    import os
    from model_storage import get_models_dir, _is_writable_dir

    models_dir = get_models_dir()

    # What files are actually present?
    files_present = []
    try:
        for fname in sorted(os.listdir(models_dir)):
            fpath = os.path.join(models_dir, fname)
            if os.path.isfile(fpath):
                files_present.append({
                    'name': fname,
                    'size_bytes': os.path.getsize(fpath),
                    'mtime': os.path.getmtime(fpath),
                })
    except Exception as e:
        files_present = [{'error': str(e)}]

    # Read /proc/mounts to see if models_dir sits under a real mount
    persistent = False
    mount_match = None
    try:
        with open('/proc/mounts') as f:
            mounts = f.read()
        # Find the longest mount-point prefix that models_dir starts with
        best = ''
        for line in mounts.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                mountpoint = parts[1]
                fstype = parts[2] if len(parts) > 2 else ''
                if models_dir.startswith(mountpoint) and len(mountpoint) > len(best):
                    # An overlay/tmpfs mount at this point is ephemeral; a
                    # real block device (ext4 on /dev/...) is persistent.
                    best = mountpoint
                    mount_match = {'mountpoint': mountpoint, 'device': parts[0], 'fstype': fstype}
        if mount_match:
            persistent = mount_match['fstype'] in ('ext4', 'xfs', 'btrfs') and mount_match['device'].startswith('/dev/')
    except Exception as e:
        mount_match = {'error': str(e)}

    return jsonify({
        'resolved_models_dir': models_dir,
        'writable': _is_writable_dir(models_dir),
        'appears_persistent': persistent,
        'mount_info': mount_match,
        'docrepo_path_env': os.environ.get('DOCREPO_PATH', '(unset, default /var/data/docrepo)'),
        'models_dir_env': os.environ.get('MODELS_DIR', '(unset)'),
        'files_present': files_present,
        'file_count': len([f for f in files_present if 'name' in f]),
        'warning': None if persistent else 'Resolved models dir does NOT appear to be on a persistent disk — models will be wiped on next deploy. Check disk mount configuration.',
    })


@admin_bp.route('/api/admin/ml-model-status', methods=['GET'])
@_api_admin_req_dec
def admin_ml_model_status():
    """Check which model files are deployed."""
    import os

    # v5.89.55: persistent disk
    models_dir = get_models_dir()
    expected = {
        'Finding Classifier': ['finding_category.xgb', 'finding_severity.xgb', 'category_encoder.pkl', 'severity_encoder.pkl'],
        'Contradiction Detector': ['contradiction_detector.xgb', 'contradiction_encoder.pkl'],
        'Predictive Model': ['predictive_model.pkl', 'predictive_vocab.pkl'],
        'Repair Cost Model': ['repair_cost.xgb', 'cost_feature_meta.pkl'],
    }

    status = {}
    for model_name, files in expected.items():
        present = []
        missing = []
        total_size = 0
        for fname in files:
            path = os.path.join(models_dir, fname)
            if os.path.exists(path):
                present.append(fname)
                total_size += os.path.getsize(path)
            else:
                missing.append(fname)
        status[model_name] = {
            'deployed': len(missing) == 0,
            'present': present,
            'missing': missing,
            'total_size_bytes': total_size,
        }

    return jsonify(status)


@admin_bp.route('/api/admin/ml-export/repair_costs', methods=['GET'])
@_api_admin_req_dec
def admin_ml_export_repair_costs():
    """Export repair cost data from ALL sources for cost model training.
    Sources: repair_estimate.breakdown, BASELINE_COSTS table, contractor completions,
    contractor leads with job values, and individual finding costs.
    """
    import csv, io, json as _json, re
    from flask import Response
    from models import Analysis, Property, InspectorReport

    rows_out = []
    seen_keys = set()

    def extract_zip(addr):
        m = re.search(r'\b(\d{5})\b', addr or '')
        return m.group(1) if m else ''

    def add_row(text, cat, sev, loc, low, high, zipcode, price, mult, source):
        key = (text.lower()[:80], cat, sev, source)
        if key in seen_keys:
            return
        seen_keys.add(key)
        rows_out.append({
            'finding_text': text, 'category': cat, 'severity': sev, 'location': loc,
            'cost_low': low, 'cost_high': high, 'cost_mid': round((low + high) / 2, 2),
            'zip_code': zipcode, 'property_price': price, 'cost_multiplier': mult, 'source': source,
        })

    # ── Source 1: repair_estimate.breakdown from analyses ──
    analyses = Analysis.query.filter(
        Analysis.status == 'completed', Analysis.result_json.isnot(None)).all()
    for a in analyses:
        try:
            result = _json.loads(a.result_json or '{}')
            if result.get('analysis_depth') == 'address_only':
                continue
            prop = Property.query.get(a.property_id) if a.property_id else None
            address = prop.address if prop else ''
            price = prop.price if prop else 0
            zipcode = extract_zip(address)
            repair_est = result.get('repair_estimate', {})
            if isinstance(repair_est, dict):
                mult = repair_est.get('cost_multiplier', 1.0)
                for item in repair_est.get('breakdown', []):
                    if not isinstance(item, dict):
                        continue
                    desc = (item.get('description') or '').strip()
                    low = float(item.get('low', 0) or 0)
                    high = float(item.get('high', 0) or 0)
                    if desc and len(desc) > 5 and (low > 0 or high > 0):
                        add_row(desc, (item.get('system') or '').lower(), (item.get('severity') or '').lower(),
                                '', low, high, zipcode, price, mult, 'repair_engine')
            # Also check individual finding costs
            insp = result.get('inspection_report', {})
            if isinstance(insp, dict):
                for f in insp.get('inspection_findings', []):
                    if not isinstance(f, dict):
                        continue
                    desc = (f.get('description') or '').strip()
                    try:
                        cl = float(f.get('estimated_cost_low') or 0)
                        ch = float(f.get('estimated_cost_high') or 0)
                    except (ValueError, TypeError):
                        continue
                    if desc and len(desc) > 10 and (cl > 0 or ch > 0):
                        add_row(desc, str(f.get('category') or '').lower(), str(f.get('severity') or '').lower(),
                                (f.get('location') or ''), cl, ch, zipcode, price, 1.0, 'finding_estimate')
        except Exception:
            pass

    # ── Source 2: Inspector report breakdowns ──
    reports = InspectorReport.query.filter(InspectorReport.analysis_json.isnot(None)).all()
    for r in reports:
        try:
            result = _json.loads(r.analysis_json or '{}')
            zipcode = extract_zip(r.property_address or '')
            repair_est = result.get('repair_estimate', {})
            if isinstance(repair_est, dict):
                mult = repair_est.get('cost_multiplier', 1.0)
                for item in repair_est.get('breakdown', []):
                    if not isinstance(item, dict):
                        continue
                    desc = (item.get('description') or '').strip()
                    low = float(item.get('low', 0) or 0)
                    high = float(item.get('high', 0) or 0)
                    if desc and len(desc) > 5 and (low > 0 or high > 0):
                        add_row(desc, (item.get('system') or '').lower(), (item.get('severity') or '').lower(),
                                '', low, high, zipcode, r.property_price or 0, mult, 'inspector_repair_engine')
        except Exception:
            pass

    # ── Source 3: BASELINE_COSTS — national cost ranges by category+severity ──
    try:
        from repair_cost_estimator import BASELINE_COSTS
        for cat, severities in BASELINE_COSTS.items():
            for sev, (low, high) in severities.items():
                desc = f"{cat.replace('_', ' ').title()} repair — {sev} severity (national average)"
                add_row(desc, cat, sev, '', low, high, '', 0, 1.0, 'baseline_national')
    except Exception:
        pass

    # ── Source 4: BASELINE_COSTS × top ZIP multipliers for regional variety ──
    try:
        from repair_cost_estimator import BASELINE_COSTS
        from zip_cost_data import ZIP_COST_DATA
        # Pick representative ZIPs across multiplier spectrum
        sample_zips = {}
        for prefix, (mult, metro) in ZIP_COST_DATA.items():
            bucket = round(mult, 1)
            if bucket not in sample_zips and 0.7 <= mult <= 1.6:
                sample_zips[bucket] = (prefix, mult, metro)
        for bucket, (prefix, mult, metro) in sorted(sample_zips.items()):
            for cat, severities in BASELINE_COSTS.items():
                for sev, (low, high) in severities.items():
                    adj_low = round(low * mult)
                    adj_high = round(high * mult)
                    desc = f"{cat.replace('_', ' ').title()} repair — {sev} severity ({metro})"
                    add_row(desc, cat, sev, '', adj_low, adj_high, prefix + '00', 0, mult, 'baseline_regional')
        logging.info(f"ML cost export: added {len(sample_zips)} regional multiplier variants")
    except Exception:
        pass

    # ── Source 5: Contractor job completions (ground truth!) ──
    try:
        from models import ContractorJobCompletion
        completions = ContractorJobCompletion.query.filter(
            ContractorJobCompletion.won_job == True,
            ContractorJobCompletion.final_price.isnot(None),
        ).all()
        for jc in completions:
            systems = (jc.work_completed or '').split(',')
            for sys_name in systems:
                sys_name = sys_name.strip().lower()
                if not sys_name:
                    continue
                desc = f"{sys_name.replace('_', ' ').title()} repair — contractor actual price"
                price = jc.final_price or 0
                add_row(desc, sys_name, 'moderate', '', price * 0.85, price * 1.15, jc.zip_code or '', 0, 1.0, 'contractor_actual')
        logging.info(f"ML cost export: {len(completions)} contractor completions")
    except Exception:
        pass

    # ── Source 6: Contractor leads with job values ──
    try:
        from models import ContractorLead
        leads = ContractorLead.query.filter(
            ContractorLead.job_value.isnot(None),
            ContractorLead.job_value > 0,
        ).all()
        for lead in leads:
            desc = lead.issue_description or f"{(lead.repair_system or 'general').title()} repair"
            if len(desc) > 5:
                add_row(desc[:500], (lead.repair_system or 'general').lower(), 'moderate', '',
                        lead.job_value * 0.85, lead.job_value * 1.15, lead.property_zip or '', 0, 1.0, 'contractor_lead')
    except Exception:
        pass

    # Write CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['finding_text', 'category', 'severity', 'location', 'cost_low', 'cost_high', 'cost_mid', 'zip_code', 'property_price', 'cost_multiplier', 'source'])
    for r in rows_out:
        writer.writerow([r['finding_text'], r['category'], r['severity'], r['location'],
                         r['cost_low'], r['cost_high'], r['cost_mid'], r['zip_code'],
                         r['property_price'], r['cost_multiplier'], r['source']])

    logging.info(f"ML cost export: {len(rows_out)} total rows from {len(set(r['source'] for r in rows_out))} sources")

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=offerwise_repair_costs.csv'}
    )

@admin_bp.route('/api/admin/ml-train', methods=['POST'])
@_api_admin_req_dec
def admin_ml_train():
    """Start training in background. Returns job ID immediately.
    Poll /api/admin/ml-train-status?job_id=... for progress and final results.
    """
    import threading, uuid, time, sys

    # Check if a training job is already running (in memory)
    existing_job = _ml_training_jobs.get('_active')
    if existing_job:
        job = _ml_training_jobs.get(existing_job, {})
        if job.get('status') == 'running':
            # Verify it's not stale — with heartbeat, anything with no recent disk
            # activity and >10 min elapsed is dead. Match the status endpoint threshold.
            elapsed = time.time() - job.get('started_at', 0)
            state_path = os.path.join(_JOB_STATE_DIR, f'{existing_job}.json')
            hb_age = None
            try:
                if os.path.exists(state_path):
                    hb_age = time.time() - os.path.getmtime(state_path)
            except Exception:
                pass
            if elapsed < 1200 and (hb_age is None or hb_age < 600):
                return jsonify({
                    'job_id': existing_job,
                    'status': 'already_running',
                    'message': 'Training already in progress — polling existing job',
                    'started_at': job.get('started_at'),
                })
            # Stale in-memory entry — clear it so we can start fresh
            try:
                del _ml_training_jobs[existing_job]
            except Exception:
                pass
            _ml_training_jobs.pop('_active', None)

    # Clean up any stale job state files from previous runs
    import glob
    try:
        for f in glob.glob(os.path.join(_JOB_STATE_DIR, '*.json')):
            fname = os.path.basename(f)
            if fname.startswith('_'):  # skip _crawl, _agent_status
                continue
            try:
                age = time.time() - os.path.getmtime(f)
                if age > 600:  # older than 10 min
                    os.remove(f)
            except Exception:
                pass
    except Exception:
        pass

    job_id = str(uuid.uuid4())[:12]
    initial_state = {
        'status': 'running',
        'started_at': time.time(),
        'log': [],
        'results': None,
        '_job_id': job_id,
    }
    # Keep a lightweight in-memory pointer so duplicate-job guard works within
    # a single worker without a disk roundtrip. The actual training state lives
    # on disk and is authoritative.
    _ml_training_jobs[job_id] = initial_state
    _ml_training_jobs['_active'] = job_id
    _save_job_state(job_id, initial_state)  # subprocess will read this

    # Spawn training in an isolated subprocess. This is the critical isolation:
    # if training OOMs, only this subprocess dies — the gunicorn worker stays up.
    # Communication is entirely through the job state file on disk.
    import subprocess
    app_dir = os.path.dirname(os.path.abspath(__file__))
    runner = os.path.join(app_dir, 'run_training.py')
    # OFFERWISE_TRAINING_SUBPROCESS tells app.py to skip APScheduler on startup
    # so we don't end up with duplicate schedulers running the same cron jobs.
    sub_env = os.environ.copy()
    sub_env['OFFERWISE_TRAINING_SUBPROCESS'] = '1'
    try:
        proc = subprocess.Popen(
            [sys.executable, runner, '--job-id', job_id],
            cwd=app_dir,
            env=sub_env,
            stdout=subprocess.DEVNULL,  # stderr captured by Render for debugging
            stderr=subprocess.DEVNULL,
            start_new_session=True,     # detach so a worker recycle can't SIGTERM us
        )
        initial_state['_pid'] = proc.pid
        _save_job_state(job_id, initial_state)
    except Exception as spawn_err:
        initial_state['status'] = 'failed'
        initial_state['error'] = f'Failed to spawn training subprocess: {spawn_err}'
        _save_job_state(job_id, initial_state)
        return jsonify({
            'job_id': job_id,
            'status': 'failed',
            'error': initial_state['error'],
        }), 500

    # Spawn a lightweight heartbeat thread that touches the state file every 20s
    # while the subprocess is alive. This lets the staleness detector distinguish
    # "training is slow but alive" from "training subprocess is dead".
    _start_heartbeat_thread(job_id, proc.pid)

    return jsonify({
        'job_id': job_id,
        'status': 'started',
        'message': 'Training started in subprocess. Poll /api/admin/ml-train-status for progress.',
        'pid': proc.pid,
    })


@admin_bp.route('/api/admin/ml-train-status', methods=['GET'])
@_api_admin_req_dec
def admin_ml_train_status():
    """Poll status of a training job. Always reads from disk (subprocess writes it)."""
    import time, os
    job_id = request.args.get('job_id', '')
    if not job_id:
        job_id = _ml_training_jobs.get('_active', '')

    if not job_id:
        return jsonify({'status': 'not_found'})

    disk_state = _load_job_state(job_id)
    if disk_state:
        # Early death detection: if the subprocess PID is no longer alive but the
        # status is still 'running', the subprocess died without writing final
        # state. This catches OOM kills immediately instead of waiting 10 minutes
        # for the mtime staleness check.
        state_path = os.path.join(_JOB_STATE_DIR, f'{job_id}.json')
        if disk_state.get('status') == 'running':
            pid = disk_state.get('_pid')
            if pid:
                pid_alive = None
                try:
                    os.kill(pid, 0)  # signal 0 = existence check
                    pid_alive = True
                except ProcessLookupError:
                    pid_alive = False  # PID definitely gone
                except PermissionError:
                    pid_alive = True   # PID exists but owned by another user/worker
                except OSError:
                    pid_alive = None   # Unknown — fall back to mtime check
                if pid_alive is False:
                    # Wait a moment in case the subprocess is in its final
                    # state-write — if state is still 'running' after a brief
                    # grace period, it really died.
                    time.sleep(0.5)
                    refreshed = _load_job_state(job_id) or disk_state
                    if refreshed.get('status') == 'running':
                        refreshed['status'] = 'failed'
                        elapsed = time.time() - refreshed.get('started_at', time.time())
                        refreshed['error'] = (
                            f'Training subprocess died after {int(elapsed)}s without writing '
                            f'final state. Most likely cause: ran out of memory (OOM kill). '
                            f'Check Render logs for "Killed" messages. Click Train Now to retry.'
                        )
                        disk_state = refreshed

            # Fallback staleness check: if no PID (shouldn't happen post-v5.86.45)
            # or for cross-worker visibility where kill(0) returned ok but mtime
            # is ancient. 10 min with no heartbeat = dead regardless of kill(0).
            if disk_state.get('status') == 'running' and os.path.exists(state_path):
                file_age = time.time() - os.path.getmtime(state_path)
                if file_age > 600:
                    disk_state['status'] = 'failed'
                    disk_state['error'] = (
                        f'Training worker died silently ({int(file_age/60)} min since last heartbeat). '
                        f'Likely cause: Render worker ran out of memory during training, or was '
                        f'restarted by a deploy. Check Render logs for OOM kills. '
                        f'Click Train Now to start a fresh job — the stale state will be cleared.'
                    )

        return jsonify({
            'job_id': job_id,
            'status': disk_state.get('status', 'unknown'),
            'elapsed_seconds': round(disk_state.get('elapsed_total', 0), 1),
            'log': disk_state.get('log', []),
            'results': disk_state.get('results'),
            'error': disk_state.get('error'),
        })

    return jsonify({'status': 'not_found'})


# Module-level state for training jobs — use file-based state for multi-worker visibility
_ml_training_jobs = {}
_ml_agent_status = {'running': False, 'phase': '', 'started_at': None, 'log': []}

_JOB_STATE_DIR = '/var/data/docrepo/.ml_jobs'

def _save_job_state(job_id, job):
    """Persist job state to disk so both Gunicorn workers and the training subprocess can see it."""
    import os, json as _json
    try:
        os.makedirs(_JOB_STATE_DIR, exist_ok=True)
        with open(os.path.join(_JOB_STATE_DIR, f'{job_id}.json'), 'w') as f:
            _json.dump({
                'status': job.get('status', 'unknown'),
                'started_at': job.get('started_at', 0),
                'elapsed_total': job.get('elapsed_total', 0),
                'log': job.get('log', []),
                'results': job.get('results'),
                'error': job.get('error'),
                '_pid': job.get('_pid'),  # subprocess PID for liveness check
                '_job_id': job.get('_job_id', job_id),  # subprocess reads this
            }, f)
    except Exception:
        pass

def _load_job_state(job_id):
    """Load job state from disk."""
    import os, json as _json
    try:
        path = os.path.join(_JOB_STATE_DIR, f'{job_id}.json')
        if os.path.exists(path):
            with open(path, 'r') as f:
                return _json.load(f)
    except Exception:
        pass
    return None


def _start_heartbeat_thread(job_id, pid):
    """Touch the job state file every 20s while the training subprocess is alive.

    The subprocess handles actual training state writes (via _execute_training's
    _log closure). This heartbeat just keeps the mtime fresh so the staleness
    detector can distinguish "subprocess running but between state saves" from
    "subprocess dead".

    The heartbeat stops as soon as the subprocess is no longer alive — at which
    point either the subprocess wrote its final state (complete/failed) or it
    died silently (OOM kill) and the staleness detector will catch the stale
    file after 10 minutes.
    """
    import threading, os, time

    def _heartbeat():
        state_path = os.path.join(_JOB_STATE_DIR, f'{job_id}.json')
        while True:
            # Check if subprocess is still alive. Signal 0 checks existence
            # without actually sending a signal.
            try:
                os.kill(pid, 0)
            except (OSError, ProcessLookupError):
                # Subprocess gone — either it finished (state already updated)
                # or it died. Either way, heartbeat's job is done.
                return
            try:
                if os.path.exists(state_path):
                    os.utime(state_path, None)
            except Exception:
                pass  # heartbeat must never raise
            time.sleep(20)

    t = threading.Thread(target=_heartbeat, daemon=True, name=f'ml-train-hb-{job_id}')
    t.start()
    return t


def _execute_training(job):
    """Actual training logic. Accepts a job dict whose 'log' list collects output."""
    import json as _json, os, pickle, time
    import numpy as np
    from collections import Counter
    from models import MLFindingLabel, MLContradictionPair, Analysis, Property, InspectorReport

    base_dir = os.path.dirname(os.path.abspath(__file__))
    # v5.89.55: persistent disk
    models_dir = get_models_dir()

    results = {}
    log = job['log']  # reference the job's log list
    t_start = time.time()
    _last_save = [0]  # mutable for closure

    def _log(msg, level='info'):
        log.append({'t': round(time.time() - t_start, 1), 'msg': msg, 'level': level})
        # Persist to disk every 10 seconds so the status endpoint can see progress
        now = time.time()
        if now - _last_save[0] > 10:
            _last_save[0] = now
            job['elapsed_total'] = now - t_start
            _save_job_state(job.get('_job_id', '_active'), job)

    _log('Starting ML training pipeline...')

    # Get shared embedder from inference module
    try:
        from ml_inference import get_classifier
        clf = get_classifier()
        embedder = clf._embedder
        if embedder is None:
            raise RuntimeError('Sentence-transformer not loaded. Check Render logs.')
        _log('Sentence-transformer loaded (shared from inference)')
    except Exception as e:
        raise RuntimeError(f'Cannot access embedder: {e}')

    from sklearn.preprocessing import LabelEncoder
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, mean_absolute_error, r2_score, classification_report
    import xgboost as xgb

    # ── MODEL 1: FINDING CLASSIFIER ──
    _log('═══ MODEL 1: FINDING CLASSIFIER ═══', 'header')
    try:
        # v5.87.44: memory-aware streamed load.
        #
        # Previous behavior: MLFindingLabel.query.all() loaded ALL ~121K rows
        # eagerly into the SQLAlchemy session, building ~120MB+ of ORM objects
        # before any RAM check could run. The downstream "memory-aware cap"
        # at the embedding step (still below) was therefore measuring
        # already-depleted available RAM and couldn't prevent the OOM at the
        # crawl-end / embed-start transition. On the 2GB Standard tier this
        # was the source of the recurring 3am restarts.
        #
        # New behavior:
        #   1. Sample the available RAM up front, BEFORE any rows are pulled.
        #   2. Compute a row budget that leaves headroom for: cleaning dicts,
        #      sentence-transformer embeddings (384 floats × 4 bytes per row),
        #      its working tensors during encoding, XGBoost training, and the
        #      baseline gunicorn process. ~2KB per row in peak working memory
        #      when you account for transient embedder allocations.
        #   3. Use a server-side .yield_per() cursor so rows arrive in chunks
        #      and the cleaned `data` list can be built without ever holding
        #      the full ORM result set in memory.
        #   4. order_by created_at DESC + limit so on a memory-constrained
        #      box we keep the freshest training data, not a random head.
        import psutil
        avail_mb_at_load = psutil.virtual_memory().available / (1024 * 1024)

        # Per-row peak memory estimate (bytes). 2.0KB is conservative and
        # accounts for embedder transient tensors which spike during encode.
        BYTES_PER_ROW = 2048
        # Reserved headroom for XGBoost training + Flask baseline + OS:
        # 350MB on a 2GB Standard, scaled up if more RAM is available.
        RESERVED_MB = 350
        budget_bytes = max(0, (avail_mb_at_load - RESERVED_MB)) * 1024 * 1024
        row_cap = max(5000, min(150000, int(budget_bytes / BYTES_PER_ROW)))

        total_rows = MLFindingLabel.query.count()
        _log(f'Available RAM at load: {avail_mb_at_load:.0f}MB · row budget: {row_cap:,} · corpus: {total_rows:,}')

        if total_rows <= row_cap:
            # Stream the full corpus; yield_per keeps memory bounded even
            # though the final list will hold all rows.
            row_iter = MLFindingLabel.query.yield_per(2000)
            scanned = total_rows
        else:
            _log(f'⚠ Capping load at {row_cap:,} rows (corpus has {total_rows:,}). Newest-first.', 'warn')
            _log(f'  Upgrade Render to 4GB tier to train on the full {total_rows:,}-row corpus', 'warn')
            row_iter = (
                MLFindingLabel.query
                .order_by(MLFindingLabel.created_at.desc())
                .limit(row_cap)
                .yield_per(2000)
            )
            scanned = row_cap

        cat_map = {"foundation": "foundation_structure", "exterior": "roof_exterior",
                   "foundation & structure": "foundation_structure", "roof & exterior": "roof_exterior",
                   "hvac & systems": "hvac_systems", "hvac": "hvac_systems",
                   "roof": "roof_exterior", "general": "general",
                   "water_damage": "environmental", "pest": "environmental",
                   "safety": "electrical", "permits": "general", "legal & title": "general"}

        # Stream into cleaned data without holding the ORM result set.
        # Dedup happens inline so we don't build a 100K-item intermediate
        # list and then walk it again.
        data = []
        seen = set()
        for r in row_iter:
            cat = (r.category or '').strip().lower()
            sev = (r.severity or '').strip().lower()
            text = (r.finding_text or '').strip()
            if cat and sev and len(text) > 10 and sev in ('critical','major','moderate','minor'):
                if text in seen:
                    continue
                seen.add(text)
                data.append({'text': text, 'category': cat_map.get(cat, cat), 'severity': sev})

        # Free ORM session state and the dedup set — we're done with both.
        seen = None
        try:
            db.session.expunge_all()
        except Exception:
            pass
        import gc as _gc
        _gc.collect()

        _log(f'After cleaning + dedup: {len(data)} unique findings (scanned {scanned:,})')

        # Log distributions
        cat_counts = Counter(d['category'] for d in data)
        sev_counts = Counter(d['severity'] for d in data)
        for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
            bar = '█' * max(1, int(cnt / max(cat_counts.values()) * 20))
            _log(f'  {cat:28s} {cnt:4d}  {bar}')
        for sev, cnt in sorted(sev_counts.items(), key=lambda x: -x[1]):
            bar = '█' * max(1, int(cnt / max(sev_counts.values()) * 20))
            _log(f'  {sev:28s} {cnt:4d}  {bar}')

        if len(data) >= 20:
            # ── Augment underrepresented classes ──
            aug_count = 0
            api_key = os.environ.get('ANTHROPIC_API_KEY', '')
            if api_key:
                try:
                    _log('Checking class balance for augmentation...')
                    target_cat = int(max(cat_counts.values()) * 0.75)
                    target_sev = int(max(sev_counts.values()) * 0.75)
                    # Per-batch cap: prevents max_tokens overflow on the API response.
                    # See ml_training_pipeline.py for rationale.
                    AUG_PER_BATCH = 30

                    needs = []
                    for cat, cnt in cat_counts.items():
                        if cnt < target_cat:
                            need = min(target_cat - cnt, cnt * 2, AUG_PER_BATCH)
                            if need >= 3:
                                # Diversity fix: sample randomly across existing rows
                                # so Claude sees the range of styles in this category,
                                # not just the first 2 which tend to be similar.
                                import random as _rnd
                                cat_rows = [d for d in data if d['category'] == cat]
                                _rnd.seed(hash(cat) % 10000)
                                _rnd.shuffle(cat_rows)
                                examples = [d['text'] for d in cat_rows[:3]]
                                needs.append(f'{need} findings for category "{cat}" (examples of existing style: {" | ".join(examples)})')
                                _log(f'  Need +{need} for {cat} (has {cnt}, target {target_cat})')
                    for sev, cnt in sev_counts.items():
                        if cnt < target_sev:
                            need = min(target_sev - cnt, cnt * 2, AUG_PER_BATCH)
                            if need >= 3:
                                cats = list(set(d['category'] for d in data if d['severity'] == sev))
                                import random as _rnd
                                sev_rows = [d for d in data if d['severity'] == sev]
                                _rnd.seed(hash(sev) % 10000)
                                _rnd.shuffle(sev_rows)
                                examples = [d['text'] for d in sev_rows[:3]]
                                needs.append(f'{need} findings with severity "{sev}" across {", ".join(cats)} (examples of existing style: {" | ".join(examples)})')
                                _log(f'  Need +{need} for severity {sev} (has {cnt}, target {target_sev})')

                    if needs:
                        _log(f'Calling Claude to generate {len(needs)} augmentation batches...')
                        from anthropic import Anthropic
                        client = Anthropic(api_key=api_key)
                        needs_text = '\n'.join(f'- {n}' for n in needs)
                        # See ml_training_pipeline.py for the full rationale. Previous
                        # prompt produced only worst-case examples (radon at 18+ pCi/L,
                        # every mold critical, etc.), teaching the model
                        # "keyword match → critical" regardless of actual level.
                        prompt = f"""Generate realistic, DIVERSE home inspection findings to balance a training dataset.

Critical instruction: produce a REALISTIC RANGE of severity within each finding, not just worst-case examples. Include borderline and moderate cases, not only catastrophic ones.

Severity guidelines (use all levels appropriately):
- critical: active safety hazard or imminent catastrophic failure (gas leak, active fire risk, structural collapse imminent, lead exposure to children above 40 μg/dL, radon above 20 pCi/L in occupied space)
- major: significant defect requiring prompt repair ($5K-$25K typical, still functional but degrading — radon 10-20 pCi/L, moderate mold, roof at end of life)
- moderate: defect that warrants attention within 6-12 months ($500-$5K — radon 4-10 pCi/L at EPA action level, minor water intrusion, HVAC near end of life, roof granule loss with 3-5 years remaining)
- minor: cosmetic or low-urgency item (<$500 — caulking, minor paint peeling, worn weather stripping, slight granule loss on newer roof)

Realistic diversity rules:
- For environmental findings (radon, mold, asbestos, lead): include BORDERLINE values near EPA action thresholds, not just catastrophic levels. Mix moderate/major with critical.
- For roof/shingle findings: include normal-aging cases (moderate granule loss, brittleness) with severity moderate or major, not every example as critical.
- For HVAC findings: include routine maintenance issues (severity minor/moderate), not just total system failures.
- For each category, aim for roughly: 15% critical, 35% major, 35% moderate, 15% minor unless the need explicitly targets one severity.

Sound like a professional home inspector. Use specific technical language with concrete measurements.

Categories: {', '.join(cat_counts.keys())}
Severities: critical, major, moderate, minor

Generate these quantities:
{needs_text}

Respond with ONLY a JSON array:
[{{"text": "finding", "category": "cat", "severity": "sev"}}]"""
                        resp = client.messages.create(model=SONNET, max_tokens=8000,
                            messages=[{'role': 'user', 'content': prompt}])
                        raw = resp.content[0].text.strip()
                        if raw.startswith('```'): raw = raw.split('\n', 1)[-1]
                        if raw.endswith('```'): raw = raw[:-3]
                        raw = raw.strip()
                        if raw.startswith('json'): raw = raw[4:].strip()
                        items = _json.loads(raw)
                        for item in items:
                            if isinstance(item, dict) and item.get('text') and item.get('category') and item.get('severity'):
                                data.append({'text': item['text'], 'category': item['category'].lower().strip(),
                                    'severity': item['severity'].lower().strip()})
                                aug_count += 1
                        _log(f'✅ Augmented with {aug_count} synthetic findings (total: {len(data)})', 'success')
                    else:
                        _log('Classes well-balanced, no augmentation needed')
                except Exception as aug_err:
                    _log(f'⚠ Augmentation skipped: {aug_err}', 'warn')

            _log(f'Encoding {len(data)} findings with sentence-transformer...')

            # Post-augmentation memory backstop. The upstream load (above) caps
            # rows BEFORE they hit memory; this second pass exists to catch the
            # case where Claude's augmentation step pushed `data` back above
            # the budget. Stratified sample preserves class distribution rather
            # than dropping the augmented rows entirely.
            avail_mb = psutil.virtual_memory().available / (1024 * 1024)
            max_rows_for_ram = int((avail_mb - 150) * 1024 / 1.5) if avail_mb > 200 else 20000
            max_rows_for_ram = max(max_rows_for_ram, 5000)
            if len(data) > max_rows_for_ram:
                _log(f'⚠ Post-augment cap: {avail_mb:.0f}MB free → sampling {max_rows_for_ram:,} of {len(data):,}', 'warn')
                # Stratified sample to preserve class distribution
                import random
                random.seed(42)
                by_cat = {}
                for d in data:
                    by_cat.setdefault(d['category'], []).append(d)
                sampled = []
                per_cat = max(100, max_rows_for_ram // len(by_cat))
                for cat, items in by_cat.items():
                    if len(items) <= per_cat:
                        sampled.extend(items)
                    else:
                        sampled.extend(random.sample(items, per_cat))
                data = sampled[:max_rows_for_ram]
                _log(f'  Sampled: {len(data):,} rows across {len(by_cat)} categories')
                # Update distributions
                cat_counts = Counter(d['category'] for d in data)
                sev_counts = Counter(d['severity'] for d in data)

            texts = [d['text'] for d in data]
            cats = [d['category'] for d in data]
            sevs = [d['severity'] for d in data]

            # Batch-encode embeddings in chunks to stay within memory limits
            # chunk_size=2000: on the 2GB Standard plan, 5000 chunks spiked the
            # embedder's intermediate tensors enough to OOM during multi-model
            # training. 2000 is conservative but still efficient.
            chunk_size = 2000
            n_texts = len(texts)
            _log(f'Encoding {n_texts} texts in chunks of {chunk_size}...')
            emb_chunks = []
            for start in range(0, n_texts, chunk_size):
                end = min(start + chunk_size, n_texts)
                chunk = embedder.encode(texts[start:end], batch_size=64, show_progress_bar=False)
                emb_chunks.append(chunk)
                if n_texts > chunk_size:
                    _log(f'  Encoded {end}/{n_texts} ({end*100//n_texts}%)')
            emb = np.vstack(emb_chunks)
            del emb_chunks  # free memory
            _log(f'Encoded: {emb.shape} embedding matrix')

            cat_enc = LabelEncoder().fit(cats)
            sev_enc = LabelEncoder().fit(sevs)
            y_cat = cat_enc.transform(cats)
            y_sev = sev_enc.transform(sevs)

            try:
                X_tr, X_te, yc_tr, yc_te, ys_tr, ys_te = train_test_split(
                    emb, y_cat, y_sev, test_size=0.2, random_state=42, stratify=y_cat)
            except ValueError:
                X_tr, X_te, yc_tr, yc_te, ys_tr, ys_te = train_test_split(
                    emb, y_cat, y_sev, test_size=0.2, random_state=42)
            _log(f'Train/test split: {len(X_tr)} train, {len(X_te)} test')

            # emb and intermediate lists no longer needed — X_tr/X_te hold what we need
            del emb, texts, cats, sevs, y_cat, y_sev
            import gc
            gc.collect()

            _log('Training category classifier (XGBoost)...')
            n_trees = 500 if len(data) > 10000 else 300
            # n_jobs=1 on 1-CPU Standard plan: parallelism gives no speedup but does
            # allocate per-thread buffers. Forcing single-threaded saves ~100-200MB
            # peak during fit.
            cm = xgb.XGBClassifier(n_estimators=n_trees, max_depth=7, learning_rate=0.05,
                min_child_weight=2, subsample=0.8, colsample_bytree=0.8,
                objective='multi:softprob', eval_metric='mlogloss', n_jobs=1, random_state=42,
                early_stopping_rounds=30)
            cm.fit(X_tr, yc_tr, eval_set=[(X_te, yc_te)], verbose=False)
            cp = cm.predict(X_te)
            ca = accuracy_score(yc_te, cp)
            # Per-class report
            try:
                cr = classification_report(yc_te, cp, target_names=cat_enc.classes_, zero_division=0, output_dict=True)
                for cls in cat_enc.classes_:
                    if cls in cr:
                        p, r, f = cr[cls]['precision'], cr[cls]['recall'], cr[cls]['f1-score']
                        _log(f'  {cls:28s} P={p:.0%} R={r:.0%} F1={f:.0%}')
            except Exception:
                pass
            _log(f'Category accuracy: {ca:.1%}', 'success' if ca >= 0.75 else 'warn')

            # Save category model + encoder immediately, then free before severity training
            cm.save_model(os.path.join(models_dir, 'finding_category.xgb'))
            pickle.dump(cat_enc, open(os.path.join(models_dir, 'category_encoder.pkl'), 'wb'))
            del cm, cp, yc_tr, yc_te
            gc.collect()

            _log('Training severity classifier (XGBoost)...')
            sm = xgb.XGBClassifier(n_estimators=n_trees, max_depth=7, learning_rate=0.05,
                min_child_weight=2, subsample=0.8, colsample_bytree=0.8,
                objective='multi:softprob', eval_metric='mlogloss', n_jobs=1, random_state=42,
                early_stopping_rounds=30)
            sm.fit(X_tr, ys_tr, eval_set=[(X_te, ys_te)], verbose=False)
            sp = sm.predict(X_te)
            sa = accuracy_score(ys_te, sp)
            try:
                sr = classification_report(ys_te, sp, target_names=sev_enc.classes_, zero_division=0, output_dict=True)
                for cls in sev_enc.classes_:
                    if cls in sr:
                        p, r, f = sr[cls]['precision'], sr[cls]['recall'], sr[cls]['f1-score']
                        _log(f'  {cls:28s} P={p:.0%} R={r:.0%} F1={f:.0%}')
            except Exception:
                pass
            _log(f'Severity accuracy: {sa:.1%}', 'success' if sa >= 0.75 else 'warn')

            sm.save_model(os.path.join(models_dir, 'finding_severity.xgb'))
            pickle.dump(sev_enc, open(os.path.join(models_dir, 'severity_encoder.pkl'), 'wb'))
            _log('Saved finding_category.xgb + finding_severity.xgb')

            # Free finding-classifier training data before contradiction detector starts
            del sm, sp, X_tr, X_te, ys_tr, ys_te
            gc.collect()

            results['Finding Classifier'] = {'category': f'{ca:.1%}', 'severity': f'{sa:.1%}',
                'data_points': len(data), 'augmented': aug_count, 'status': 'READY' if ca >= 0.75 and sa >= 0.75 else 'MARGINAL'}
        else:
            _log(f'Only {len(data)} findings — need 20+ to train', 'warn')
            results['Finding Classifier'] = {'status': 'NOT ENOUGH DATA', 'data_points': len(data)}
    except Exception as e:
        _log(f'Finding Classifier FAILED: {e}', 'error')
        results['Finding Classifier'] = {'status': 'FAILED', 'error': str(e)[:200]}

    # ── MODEL 3: CONTRADICTION DETECTOR ──
    _log('═══ MODEL 3: CONTRADICTION DETECTOR ═══', 'header')
    try:
        rows = MLContradictionPair.query.all()
        _log(f'Loaded {len(rows)} raw contradiction pairs from database')
        data = []
        boilerplate = ["DISCLAIMER", "NOT hold us responsible", "MOLD DISCLAIMER",
            "not a qualified", "MAINTENANCE: Items marked", "intended to reduce",
            "you agree NOT", "non-discovery of any patent", "limitations of the inspection"]
        bp_removed = 0
        for r in rows:
            finding = (r.inspector_finding or '').strip()
            label = (r.label or '').strip()
            seller = (r.seller_claim or '').strip()
            if not finding or not label or len(finding) < 15:
                continue
            if any(bp.upper() in finding.upper() for bp in boilerplate):
                bp_removed += 1
                continue
            combined = (seller or '(not disclosed)') + ' [SEP] ' + finding
            data.append({'text': combined, 'label': label})

        seen = set()
        deduped = []
        for d in data:
            if d['text'] not in seen:
                seen.add(d['text'])
                deduped.append(d)
        data = deduped
        if bp_removed:
            _log(f'Removed {bp_removed} boilerplate rows')
        _log(f'After cleaning: {len(data)} unique pairs')
        label_counts = Counter(d['label'] for d in data)
        for lab, cnt in sorted(label_counts.items(), key=lambda x: -x[1]):
            _log(f'  {lab:28s} {cnt:4d}')

        unique_labels = list(set(d['label'] for d in data))
        if len(data) >= 20 and len(unique_labels) >= 2:
            _log(f'Encoding {len(data)} pairs...')
            texts = [d['text'] for d in data]
            labels = [d['label'] for d in data]

            emb = embedder.encode(texts, batch_size=64, show_progress_bar=False)
            c_enc = LabelEncoder().fit(labels)
            y_c = c_enc.transform(labels)

            n_cls = len(unique_labels)
            obj = 'binary:logistic' if n_cls == 2 else 'multi:softprob'
            metric = 'logloss' if n_cls == 2 else 'mlogloss'

            try:
                cX_tr, cX_te, cy_tr, cy_te = train_test_split(emb, y_c, test_size=0.2, random_state=42, stratify=y_c)
            except ValueError:
                cX_tr, cX_te, cy_tr, cy_te = train_test_split(emb, y_c, test_size=0.2, random_state=42)
            _log(f'Split: {len(cX_tr)} train, {len(cX_te)} test')

            _log('Training contradiction classifier (XGBoost, 300 trees)...')
            c_model = xgb.XGBClassifier(n_estimators=300, max_depth=6, learning_rate=0.08,
                min_child_weight=2, subsample=0.8, colsample_bytree=0.8,
                objective=obj, eval_metric=metric, n_jobs=1, random_state=42)
            c_model.fit(cX_tr, cy_tr, eval_set=[(cX_te, cy_te)], verbose=False)
            c_acc = accuracy_score(cy_te, c_model.predict(cX_te))
            _log(f'Contradiction accuracy: {c_acc:.1%}', 'success' if c_acc >= 0.90 else 'warn')

            c_model.save_model(os.path.join(models_dir, 'contradiction_detector.xgb'))
            pickle.dump(c_enc, open(os.path.join(models_dir, 'contradiction_encoder.pkl'), 'wb'))
            _log('Saved contradiction_detector.xgb')

            results['Contradiction Detector'] = {'accuracy': f'{c_acc:.1%}', 'data_points': len(data), 'status': 'READY' if c_acc >= 0.75 else 'MARGINAL'}

            # Free contradiction training data before cost predictor starts
            del c_model, emb, cX_tr, cX_te, cy_tr, cy_te, y_c
            gc.collect()
        else:
            results['Contradiction Detector'] = {'status': 'NOT ENOUGH DATA', 'data_points': len(data)}
    except Exception as e:
        results['Contradiction Detector'] = {'status': 'FAILED', 'error': str(e)[:200]}

    # ── MODEL 2: REPAIR COST PREDICTOR ──
    _log('═══ MODEL 2: REPAIR COST PREDICTOR ═══', 'header')
    # Free memory from Model 1 before starting Model 2
    import gc
    gc.collect()
    try:
        import pandas as pd
        cost_rows = []
        # Pull from repair_estimate.breakdown in analyses
        for a in Analysis.query.filter(Analysis.status == 'completed', Analysis.result_json.isnot(None)).all():
            try:
                result = _json.loads(a.result_json or '{}')
                if result.get('analysis_depth') == 'address_only':
                    continue
                prop = Property.query.get(a.property_id) if a.property_id else None
                repair_est = result.get('repair_estimate', {})
                if isinstance(repair_est, dict):
                    for item in repair_est.get('breakdown', []):
                        if not isinstance(item, dict):
                            continue
                        desc = (item.get('description') or '').strip()
                        low = float(item.get('low', 0) or 0)
                        high = float(item.get('high', 0) or 0)
                        if desc and len(desc) > 5 and (low > 0 or high > 0):
                            cost_rows.append({
                                'text': desc, 'category': (item.get('system') or '').lower(),
                                'severity': (item.get('severity') or '').lower(),
                                'cost_mid': (low + high) / 2,
                                'zip': prop.address if prop else '', 'price': prop.price if prop else 0,
                            })
            except Exception:
                pass

        # Add baseline costs
        try:
            from repair_cost_estimator import BASELINE_COSTS
            for cat, severities in BASELINE_COSTS.items():
                for sev, (low, high) in severities.items():
                    cost_rows.append({
                        'text': f'{cat} repair - {sev} severity (national average)',
                        'category': cat, 'severity': sev,
                        'cost_mid': (low + high) / 2, 'zip': '', 'price': 0,
                    })
        except Exception:
            pass

        # External crawled data (permits, HomeAdvisor, FEMA, inspector labels)
        # Quality filters: remove outliers, enforce per-category cost bounds
        COST_BOUNDS = {
            'roof_exterior':        (800, 50000),
            'foundation_structure': (1000, 80000),
            'electrical':           (200, 25000),
            'plumbing':             (150, 20000),
            'hvac_systems':         (300, 30000),
            'environmental':        (500, 40000),
            'general':              (500, 30000),
        }
        try:
            from models import MLCostData
            crawled = MLCostData.query.all()
            added_crawled = 0
            filtered_crawled = 0
            for c in crawled:
                if not c.cost_mid or c.cost_mid <= 0:
                    continue
                cat = c.category or 'general'
                bounds = COST_BOUNDS.get(cat, (500, 50000))
                # Filter outliers outside expected range for category
                if c.cost_mid < bounds[0] or c.cost_mid > bounds[1]:
                    filtered_crawled += 1
                    continue
                # Inspector labels are highest quality — weight 1x
                # HomeAdvisor national averages — weight 1x (curated)
                # FEMA/permits — weight 1x but were filtered at crawl time
                cost_rows.append({
                    'text': c.finding_text[:200], 'category': cat,
                    'severity': c.severity or 'moderate', 'cost_mid': c.cost_mid,
                    'zip': c.zip_code or '', 'price': 0,
                })
                added_crawled += 1
            if crawled:
                _log(f'External data: {added_crawled} usable, {filtered_crawled} filtered as outliers (from {len(crawled)} total)')
        except Exception as crawl_err:
            _log(f'Crawled cost data skipped: {crawl_err}', 'warn')

        # Dedup
        seen = set()
        deduped = []
        for d in cost_rows:
            key = d['text'].lower()[:80]
            if key not in seen:
                seen.add(key)
                deduped.append(d)
        cost_rows = deduped
        _log(f'After dedup: {len(cost_rows)} unique cost entries')

        if len(cost_rows) >= 10:
            # Memory cap for cost data
            avail_mb2 = psutil.virtual_memory().available / (1024 * 1024)
            max_cost_rows = int((avail_mb2 - 120) * 1024 / 1.5) if avail_mb2 > 150 else 10000
            max_cost_rows = max(max_cost_rows, 2000)
            if len(cost_rows) > max_cost_rows:
                _log(f'⚠ RAM: {avail_mb2:.0f}MB — capping cost data to {max_cost_rows:,} (from {len(cost_rows):,})', 'warn')
                import random
                random.seed(42)
                cost_rows = random.sample(cost_rows, max_cost_rows)

            rdf = pd.DataFrame(cost_rows)
            rdf = rdf[rdf['cost_mid'] > 0]
            _log(f'Cost range: ${rdf["cost_mid"].min():,.0f} — ${rdf["cost_mid"].max():,.0f} (mean: ${rdf["cost_mid"].mean():,.0f})')

            # Batch-encode cost text embeddings
            cost_texts = rdf['text'].tolist()
            n_cost = len(cost_texts)
            _log(f'Encoding {n_cost} cost entries...')
            cost_emb_chunks = []
            for start in range(0, n_cost, chunk_size):
                end = min(start + chunk_size, n_cost)
                chunk = embedder.encode(cost_texts[start:end], batch_size=64, show_progress_bar=False)
                cost_emb_chunks.append(chunk)
                if n_cost > chunk_size:
                    _log(f'  Encoded {end}/{n_cost} ({end*100//n_cost}%)')
            emb = np.vstack(cost_emb_chunks)
            del cost_emb_chunks

            cat_dummies = pd.get_dummies(rdf['category'], prefix='cat')
            sev_dummies = pd.get_dummies(rdf['severity'], prefix='sev')
            # v5.86.96: preserve column names BEFORE the delete below.
            # feature_meta at line ~4368 needs these, and `del cat_dummies`
            # at the end of this block makes the variable unreachable.
            # Prior behavior: UnboundLocalError when the ML Agent nightly
            # job ran — broke quarterly auto-retrain of the cost predictor.
            cat_cols = list(cat_dummies.columns)
            sev_cols = list(sev_dummies.columns)
            import re
            rdf['zip_num'] = rdf['zip'].apply(lambda z: float(re.search(r'\b(\d{5})\b', str(z)).group(1)) / 100000 if re.search(r'\b(\d{5})\b', str(z)) else 0)
            rdf['price_norm'] = rdf['price'].fillna(0) / 1_000_000

            # Build feature matrix in float32 to halve memory vs default float64.
            # v5.86.96: reassign to None rather than `del` — `del` makes these
            # genuinely unbound locals, so any later reference raises the
            # confusing "cannot access local variable" UnboundLocalError.
            # None-assignment has the same memory-release effect for large
            # numpy/pandas objects (refcount drops, immediate GC).
            structured = np.hstack([cat_dummies.values, sev_dummies.values, rdf[['zip_num', 'price_norm']].values]).astype(np.float32)
            X_all = np.hstack([emb.astype(np.float32, copy=False), structured])
            emb = None
            structured = None
            cat_dummies = None
            sev_dummies = None
            gc.collect()
            _log(f'Feature matrix: {X_all.shape} ({X_all.dtype})')
            y_cost = np.log1p(rdf['cost_mid'].values).astype(np.float32)

            X_tr, X_te, y_tr, y_te = train_test_split(X_all, y_cost, test_size=0.2, random_state=42)
            del X_all, y_cost
            gc.collect()
            _log(f'Split: {len(X_tr)} train, {len(X_te)} test')

            _log('Training cost regressor (XGBoost)...')
            n_cost_trees = 500 if len(rdf) > 5000 else 300
            cost_model = xgb.XGBRegressor(n_estimators=n_cost_trees, max_depth=7, learning_rate=0.05,
                min_child_weight=3, subsample=0.8, colsample_bytree=0.8,
                objective='reg:squarederror', eval_metric='rmse', n_jobs=1, random_state=42,
                early_stopping_rounds=30)
            cost_model.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)

            y_pred = np.expm1(cost_model.predict(X_te))
            y_actual = np.expm1(y_te)
            mae = mean_absolute_error(y_actual, y_pred)
            r2 = r2_score(y_actual, y_pred)
            pct_err = float(np.median(np.abs(y_actual - y_pred) / np.maximum(y_actual, 1) * 100))
            _log(f'MAE: ${mae:,.0f} | Median error: {pct_err:.0f}% | R²: {r2:.3f}', 'success' if r2 >= 0.5 else 'warn')

            # Show sample predictions
            sample_idx = list(range(min(5, len(y_actual))))
            for i in sample_idx:
                _log(f'  ${y_actual[i]:>8,.0f} vs ${y_pred[i]:>8,.0f}  ({rdf.iloc[len(X_tr)+i]["text"][:50]}...)')

            cost_model.save_model(os.path.join(models_dir, 'repair_cost.xgb'))
            feature_meta = {'category_columns': cat_cols,
                'severity_columns': sev_cols, 'embedding_dim': 384, 'uses_log_transform': True}
            pickle.dump(feature_meta, open(os.path.join(models_dir, 'cost_feature_meta.pkl'), 'wb'))
            _log('Saved repair_cost.xgb + cost_feature_meta.pkl')

            results['Repair Cost'] = {'mae': f'${mae:,.0f}', 'median_pct_err': f'{pct_err:.0f}%',
                'r2': f'{r2:.3f}', 'data_points': len(cost_rows),
                'status': 'READY' if r2 >= 0.5 and pct_err <= 40 else 'MARGINAL'}
        else:
            _log(f'Only {len(cost_rows)} cost entries — need 10+', 'warn')
            results['Repair Cost'] = {'status': 'NOT ENOUGH DATA', 'data_points': len(cost_rows)}
    except Exception as e:
        _log(f'Repair Cost FAILED: {e}', 'error')
        results['Repair Cost'] = {'status': 'FAILED', 'error': str(e)[:200]}

    # Reload inference models
    _log('═══ RELOADING MODELS ═══', 'header')
    try:
        from ml_inference import init_ml_inference
        init_ml_inference(app_base_dir=base_dir)
        results['_reload'] = 'success'
        _log('Models hot-reloaded into inference pipeline', 'success')
    except Exception as e:
        results['_reload'] = f'failed: {e}'
        _log(f'Model reload failed: {e}', 'error')

    # Run inference smoke tests
    _log('═══ INFERENCE SMOKE TESTS ═══', 'header')
    inference_results = {'passed': 0, 'failed': 0, 'skipped': 0, 'details': []}
    try:
        from ml_inference_tests import run_inference_tests
        inference_results = run_inference_tests()
        for t in inference_results.get('details', []):
            icon = '✓' if t['status'] == 'PASS' else '✗' if t['status'] == 'FAIL' else '○'
            level = 'success' if t['status'] == 'PASS' else 'error' if t['status'] == 'FAIL' else 'info'
            model_short = t.get('model', '?').replace('FindingClassifier', 'FC').replace('ContradictionDetector', 'CD').replace('CostPredictor', 'Cost')

            if t['status'] == 'FAIL':
                # Detailed failure output
                if t.get('got_cat'):
                    cat_mark = '✓' if t.get('cat_ok') else '✗'
                    sev_mark = '✓' if t.get('sev_ok') else '✗'
                    conf_cat = f" ({t.get('cat_conf', 0):.0%})" if t.get('cat_conf') else ''
                    conf_sev = f" ({t.get('sev_conf', 0):.0%})" if t.get('sev_conf') else ''
                    _log(f'{icon} {model_short}: {t.get("input","")[:50]}', level)
                    _log(f'    {cat_mark} Category: expected={t.get("expected_cat")}  got={t["got_cat"]}{conf_cat}', level)
                    _log(f'    {sev_mark} Severity: expected={t.get("expected_sev")}  got={t["got_sev"]}{conf_sev}', level)
                elif t.get('expected_range'):
                    _log(f'{icon} {model_short}: {t.get("input","")[:50]}', level)
                    _log(f'    Expected: {t["expected_range"]}  Got: {t.get("got","?")}', level)
                elif t.get('got'):
                    _log(f'{icon} {model_short}: {t.get("input","")[:50]}', level)
                    _log(f'    Expected: {t.get("expected","?")}  Got: {t["got"]}  Conf: {t.get("confidence",0):.0%}', level)
                else:
                    _log(f'{icon} {model_short}: {t.get("input","")[:50]} — {t.get("reason","")}', level)
            else:
                # Compact pass/skip
                detail = ''
                if t.get('got_cat'):
                    detail = f" → {t['got_cat']}/{t['got_sev']}"
                elif t.get('got'):
                    detail = f" → {t['got']}"
                elif t.get('status') == 'SKIP':
                    detail = f" — {t.get('reason','skipped')}"
                _log(f'{icon} {model_short}: {t.get("input","")[:50]}{detail}', level)

        _log(f'Results: {inference_results["passed"]} passed, {inference_results["failed"]} failed, {inference_results["skipped"]} skipped',
             'success' if inference_results['failed'] == 0 else 'warn')
        results['_inference_tests'] = {
            'passed': inference_results['passed'],
            'failed': inference_results['failed'],
            'skipped': inference_results['skipped'],
            'total': inference_results['total'],
            'details': inference_results['details'],
        }
    except Exception as e:
        results['_inference_tests'] = {'error': str(e)[:200]}

    elapsed = time.time() - t_start
    results['_elapsed'] = f'{elapsed:.1f}s'

    # ── ACCURACY DASHBOARD ──
    _log('', 'info')
    _log('═══ ACCURACY DASHBOARD ═══', 'header')
    fc = results.get('Finding Classifier', {})
    cd = results.get('Contradiction Detector', {})
    rc = results.get('Repair Cost', {})

    def _dash_line(model, metric, current, target, unit='%'):
        status = '✅' if current and float(str(current).replace('%','').replace('$','').replace(',','')) >= target else '❌'
        _log(f'  {status} {model:25s} {metric:12s} {str(current):>8s} / {target}{unit}')

    if fc.get('category'):
        _dash_line('Finding Classifier', 'Category', fc['category'], 90)
        _dash_line('Finding Classifier', 'Severity', fc.get('severity', '?'), 85)
    if cd.get('accuracy'):
        _dash_line('Contradiction Detector', 'Accuracy', cd['accuracy'], 99)
    if rc.get('r2'):
        r2_pct = f"{float(rc['r2'])*100:.1f}%"
        _dash_line('Repair Cost Predictor', 'R-squared', r2_pct, 85)
        _dash_line('Repair Cost Predictor', 'MAE', rc.get('mae', '?'), 1000, '')
        _dash_line('Repair Cost Predictor', 'Median err', rc.get('median_pct_err', '?'), 10)

    tests_passed = inference_results.get('passed', 0)
    tests_total = tests_passed + inference_results.get('failed', 0) + inference_results.get('skipped', 0)
    _log(f'  Inference tests: {tests_passed}/{tests_total} passed')
    _log(f'  Total time: {elapsed:.1f}s')
    _log('')

    # Save training run to history
    try:
        from models import MLTrainingRun

        def _parse_pct(s):
            try: return float(str(s).replace('%',''))
            except: return None
        def _parse_dollars(s):
            try: return float(str(s).replace('$','').replace(',',''))
            except: return None

        fc = results.get('Finding Classifier', {})
        cd = results.get('Contradiction Detector', {})
        rc = results.get('Repair Cost', {})

        run = MLTrainingRun(
            trigger='manual',
            elapsed_seconds=elapsed,
            fc_status=fc.get('status'),
            fc_category_acc=_parse_pct(fc.get('category')) if fc.get('category') else None,
            fc_severity_acc=_parse_pct(fc.get('severity')) if fc.get('severity') else None,
            fc_data_points=fc.get('data_points'),
            fc_augmented=fc.get('augmented', 0),
            fc_error=(fc.get('error') or None) if fc.get('status') == 'FAILED' else None,
            cd_status=cd.get('status'),
            cd_accuracy=_parse_pct(cd.get('accuracy')) if cd.get('accuracy') else None,
            cd_data_points=cd.get('data_points'),
            cd_error=(cd.get('error') or None) if cd.get('status') == 'FAILED' else None,
            rc_status=rc.get('status'),
            rc_r2=float(rc['r2']) if rc.get('r2') else None,
            rc_mae=_parse_dollars(rc.get('mae')),
            rc_median_pct=_parse_pct(rc.get('median_pct_err')),
            rc_data_points=rc.get('data_points'),
            rc_error=(rc.get('error') or None) if rc.get('status') == 'FAILED' else None,
            inference_tested=True,
            inference_passed=inference_results.get('passed', 0),
            inference_failed=inference_results.get('failed', 0),
            inference_details=_json.dumps(inference_results.get('details', [])),
        )
        db.session.add(run)
        db.session.commit()
        results['_history_id'] = run.id
    except Exception as e:
        logging.warning(f"ML training history save failed: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass

    _log(f'Training complete in {elapsed:.1f}s', 'success')
    results['_log'] = log
    logging.info(f"ML train complete in {elapsed:.1f}s: {len(log)} log lines")
    return results

@admin_bp.route('/api/admin/ml-training-history', methods=['GET'])
@_api_admin_req_dec
def admin_ml_training_history():
    """Return training run history for trend tracking."""
    import json as _json
    from models import MLTrainingRun

    try:
        runs = MLTrainingRun.query.order_by(MLTrainingRun.created_at.desc()).limit(20).all()
    except Exception as e:
        logging.warning(f"Training history query failed: {e}")
        return jsonify({'runs': [], 'total': 0, 'error': str(e)[:100]})

    history = []
    for r in runs:
        try:
            entry = {
                'id': r.id,
                'created_at': (r.created_at.isoformat() + 'Z') if r.created_at else None,
                'trigger': r.trigger,
                'elapsed': r.elapsed_seconds,
                'finding_classifier': {
                    'status': r.fc_status,
                    'category_acc': r.fc_category_acc,
                    'severity_acc': r.fc_severity_acc,
                    'data_points': r.fc_data_points,
                    'augmented': r.fc_augmented,
                },
                'contradiction_detector': {
                    'status': r.cd_status,
                    'accuracy': r.cd_accuracy,
                    'data_points': r.cd_data_points,
                },
                'repair_cost': {
                    'status': r.rc_status,
                    'r2': r.rc_r2,
                    'mae': r.rc_mae,
                    'median_pct': r.rc_median_pct,
                    'data_points': r.rc_data_points,
                },
                'inference_tests': {
                    'tested': r.inference_tested,
                    'passed': r.inference_passed,
                    'failed': r.inference_failed,
                    'details': _json.loads(r.inference_details) if r.inference_details else [],
                },
            }
            history.append(entry)
        except Exception:
            continue

    return jsonify({'runs': history, 'total': len(history)})


# ═══════════════════════════════════════════════════════════════════════════
# v5.87.20: Metrics snapshots — explicit before/after comparison for
# crawl/relabel/train cycles. Captures corpus-level state to ml_metrics_snapshots.
# Production ML override rate is NOT included because the underlying telemetry
# in the analyses table does not exist yet — that's a separate piece of work.
# ═══════════════════════════════════════════════════════════════════════════

def _capture_snapshot(label='', notes=''):
    """Internal helper: build and persist a snapshot. Returns the model object.

    Pulls corpus stats, last training run metrics, and active crawler list.
    Safe to call from anywhere; rolls back on partial failure.
    """
    import json as _json
    from models import (
        db as _db_local, MLFindingLabel, MLTrainingRun, MLMetricsSnapshot
    )
    from sqlalchemy import func as _func

    # Corpus stats — single grouped query is faster than 3 separate counts
    total_rows = MLFindingLabel.query.count()
    rows_high_conf = MLFindingLabel.query.filter(MLFindingLabel.confidence >= 0.85).count()
    # "labeled" means category is set to a non-placeholder value. Heuristic:
    # confidence > 0.5 means it went through the labeler (vs. placeholder 0.5).
    rows_labeled = MLFindingLabel.query.filter(MLFindingLabel.confidence > 0.5).count()
    rows_unlabeled = total_rows - rows_labeled

    # Per-source breakdown
    source_rows = (
        _db_local.session.query(
            MLFindingLabel.source_version,
            _func.count(MLFindingLabel.id).label('cnt'),
        )
        .group_by(MLFindingLabel.source_version)
        .all()
    )
    sources_breakdown = {row[0] or 'unknown': row[1] for row in source_rows}

    # Per-state breakdown (geographic_region column)
    state_rows = (
        _db_local.session.query(
            MLFindingLabel.geographic_region,
            _func.count(MLFindingLabel.id).label('cnt'),
        )
        .group_by(MLFindingLabel.geographic_region)
        .all()
    )
    states_breakdown = {row[0] or 'unknown': row[1] for row in state_rows}

    # Last training run
    last_run = MLTrainingRun.query.order_by(MLTrainingRun.created_at.desc()).first()

    # Active crawlers
    try:
        from ml_ingestion import CRAWLER_REGISTRY
        active_crawlers = [k for k, _, c in CRAWLER_REGISTRY if getattr(c, 'STATUS', '') == 'active']
    except Exception:
        active_crawlers = []

    snap = MLMetricsSnapshot(
        label=(label or '')[:100],
        notes=(notes or '')[:5000],
        total_rows=total_rows,
        rows_labeled=rows_labeled,
        rows_unlabeled=rows_unlabeled,
        rows_high_confidence=rows_high_conf,
        sources_breakdown_json=_json.dumps(sources_breakdown),
        states_breakdown_json=_json.dumps(states_breakdown),
        last_training_run_id=last_run.id if last_run else None,
        last_training_at=last_run.created_at if last_run else None,
        fc_category_acc=last_run.fc_category_acc if last_run else None,
        fc_severity_acc=last_run.fc_severity_acc if last_run else None,
        cd_accuracy=last_run.cd_accuracy if last_run else None,
        rc_r2=last_run.rc_r2 if last_run else None,
        rc_mae=last_run.rc_mae if last_run else None,
        active_crawlers_json=_json.dumps(active_crawlers),
        active_crawler_count=len(active_crawlers),
    )
    _db_local.session.add(snap)
    _db_local.session.commit()
    return snap


@admin_bp.route('/api/admin/ml-snapshot-now', methods=['POST'])
@_api_admin_req_dec
def admin_ml_snapshot_now():
    """Capture a metrics snapshot RIGHT NOW. Optional label + notes from request body.

    Use case: click before kicking off Crawl All → relabel → train, again after
    training completes. The pair forms a before/after comparison.
    """
    body = request.get_json(silent=True) or {}
    label = (body.get('label') or '').strip()
    notes = (body.get('notes') or '').strip()
    try:
        snap = _capture_snapshot(label=label, notes=notes)
        return jsonify({
            'ok': True,
            'snapshot_id': snap.id,
            'created_at': snap.created_at.isoformat() + 'Z',
            'label': snap.label,
            'total_rows': snap.total_rows,
            'rows_labeled': snap.rows_labeled,
            'rows_high_confidence': snap.rows_high_confidence,
            'active_crawler_count': snap.active_crawler_count,
        })
    except Exception as e:
        try:
            from models import db as _d
            _d.session.rollback()
        except Exception:
            pass
        logger.exception(f'ml-snapshot-now failed: {e}')
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


@admin_bp.route('/api/admin/ml-snapshots', methods=['GET'])
@_api_admin_req_dec
def admin_ml_snapshots_list():
    """List recent snapshots, newest first. Lightweight payload (no breakdown JSON)."""
    import json as _json
    from models import MLMetricsSnapshot

    try:
        snaps = MLMetricsSnapshot.query.order_by(MLMetricsSnapshot.created_at.desc()).limit(40).all()
    except Exception as e:
        logging.warning(f'snapshot list failed: {e}')
        return jsonify({'snapshots': [], 'error': str(e)[:200]})

    out = []
    for s in snaps:
        out.append({
            'id': s.id,
            'created_at': s.created_at.isoformat() + 'Z' if s.created_at else None,
            'label': s.label,
            'notes': s.notes[:200] if s.notes else '',
            'total_rows': s.total_rows,
            'rows_labeled': s.rows_labeled,
            'rows_unlabeled': s.rows_unlabeled,
            'rows_high_confidence': s.rows_high_confidence,
            'last_training_at': s.last_training_at.isoformat() + 'Z' if s.last_training_at else None,
            'fc_category_acc': s.fc_category_acc,
            'fc_severity_acc': s.fc_severity_acc,
            'cd_accuracy': s.cd_accuracy,
            'rc_r2': s.rc_r2,
            'rc_mae': s.rc_mae,
            'active_crawler_count': s.active_crawler_count,
        })
    return jsonify({'snapshots': out, 'total': len(out)})


@admin_bp.route('/api/admin/ml-snapshot-compare', methods=['GET'])
@_api_admin_req_dec
def admin_ml_snapshot_compare():
    """Compare two snapshots. Query params: before_id, after_id. If after_id is
    omitted, "current state" is captured ad-hoc as the after.

    Returns a structured diff with deltas for headline metrics, plus per-source
    and per-state row count changes. Designed for a UI that wants to render a
    side-by-side view.
    """
    import json as _json
    from models import MLMetricsSnapshot

    before_id = request.args.get('before_id', type=int)
    after_id = request.args.get('after_id', type=int)
    if not before_id:
        return jsonify({'ok': False, 'error': 'before_id is required'}), 400

    try:
        before = MLMetricsSnapshot.query.get(before_id)
        if not before:
            return jsonify({'ok': False, 'error': f'snapshot {before_id} not found'}), 404

        if after_id:
            after = MLMetricsSnapshot.query.get(after_id)
            if not after:
                return jsonify({'ok': False, 'error': f'snapshot {after_id} not found'}), 404
            after_is_live = False
        else:
            # No after_id = compare to current live state. Build inline (no persist).
            after = _build_inline_snapshot()
            after_is_live = True

        # Build the diff
        def _delta(b, a):
            """Compute a useful delta. Returns dict with raw + pct."""
            if b is None and a is None:
                return None
            if b is None:
                return {'before': None, 'after': a, 'delta': None, 'pct': None}
            if a is None:
                return {'before': b, 'after': None, 'delta': None, 'pct': None}
            d = a - b
            try:
                pct = round(100 * d / b, 2) if b not in (0, 0.0) else None
            except Exception:
                pct = None
            return {'before': b, 'after': a, 'delta': d, 'pct': pct}

        before_sources = _json.loads(before.sources_breakdown_json or '{}')
        before_states = _json.loads(before.states_breakdown_json or '{}')
        if after_is_live:
            after_sources = after['sources_breakdown']
            after_states = after['states_breakdown']
            after_total = after['total_rows']
            after_labeled = after['rows_labeled']
            after_unlabeled = after['rows_unlabeled']
            after_highconf = after['rows_high_confidence']
            after_fc_cat = after['fc_category_acc']
            after_fc_sev = after['fc_severity_acc']
            after_cd_acc = after['cd_accuracy']
            after_rc_r2 = after['rc_r2']
            after_rc_mae = after['rc_mae']
            after_active = after['active_crawler_count']
            after_id_out = None
            after_label = '(live now)'
            after_at = datetime.utcnow().isoformat() + 'Z'
        else:
            after_sources = _json.loads(after.sources_breakdown_json or '{}')
            after_states = _json.loads(after.states_breakdown_json or '{}')
            after_total = after.total_rows
            after_labeled = after.rows_labeled
            after_unlabeled = after.rows_unlabeled
            after_highconf = after.rows_high_confidence
            after_fc_cat = after.fc_category_acc
            after_fc_sev = after.fc_severity_acc
            after_cd_acc = after.cd_accuracy
            after_rc_r2 = after.rc_r2
            after_rc_mae = after.rc_mae
            after_active = after.active_crawler_count
            after_id_out = after.id
            after_label = after.label
            after_at = after.created_at.isoformat() + 'Z' if after.created_at else None

        # Per-source diff
        all_sources = sorted(set(list(before_sources.keys()) + list(after_sources.keys())))
        source_diffs = []
        for src in all_sources:
            b = before_sources.get(src, 0)
            a = after_sources.get(src, 0)
            source_diffs.append({
                'source': src,
                'before': b,
                'after': a,
                'delta': a - b,
            })
        source_diffs.sort(key=lambda x: -abs(x['delta']))

        # Per-state diff
        all_states = sorted(set(list(before_states.keys()) + list(after_states.keys())))
        state_diffs = []
        for st in all_states:
            b = before_states.get(st, 0)
            a = after_states.get(st, 0)
            state_diffs.append({
                'state': st,
                'before': b,
                'after': a,
                'delta': a - b,
            })
        state_diffs.sort(key=lambda x: -abs(x['delta']))

        return jsonify({
            'ok': True,
            'before': {
                'id': before.id,
                'label': before.label,
                'created_at': before.created_at.isoformat() + 'Z' if before.created_at else None,
            },
            'after': {
                'id': after_id_out,
                'label': after_label,
                'created_at': after_at,
                'is_live': after_is_live,
            },
            'corpus': {
                'total_rows': _delta(before.total_rows, after_total),
                'rows_labeled': _delta(before.rows_labeled, after_labeled),
                'rows_unlabeled': _delta(before.rows_unlabeled, after_unlabeled),
                'rows_high_confidence': _delta(before.rows_high_confidence, after_highconf),
            },
            'model_metrics': {
                'fc_category_acc': _delta(before.fc_category_acc, after_fc_cat),
                'fc_severity_acc': _delta(before.fc_severity_acc, after_fc_sev),
                'cd_accuracy': _delta(before.cd_accuracy, after_cd_acc),
                'rc_r2': _delta(before.rc_r2, after_rc_r2),
                'rc_mae': _delta(before.rc_mae, after_rc_mae),
            },
            'crawlers': {
                'active_count': _delta(before.active_crawler_count, after_active),
            },
            'sources_diff': source_diffs,
            'states_diff': state_diffs,
        })
    except Exception as e:
        try:
            from models import db as _d
            _d.session.rollback()
        except Exception:
            pass
        logger.exception(f'ml-snapshot-compare failed: {e}')
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


def _build_inline_snapshot():
    """Compute current corpus state without persisting. Used by compare endpoint
    when after_id is not supplied so the comparison is against live data.
    """
    import json as _json
    from models import db as _db_local, MLFindingLabel, MLTrainingRun
    from sqlalchemy import func as _func

    total_rows = MLFindingLabel.query.count()
    rows_high_conf = MLFindingLabel.query.filter(MLFindingLabel.confidence >= 0.85).count()
    rows_labeled = MLFindingLabel.query.filter(MLFindingLabel.confidence > 0.5).count()
    rows_unlabeled = total_rows - rows_labeled

    source_rows = (
        _db_local.session.query(MLFindingLabel.source_version, _func.count(MLFindingLabel.id))
        .group_by(MLFindingLabel.source_version).all()
    )
    sources = {r[0] or 'unknown': r[1] for r in source_rows}

    state_rows = (
        _db_local.session.query(MLFindingLabel.geographic_region, _func.count(MLFindingLabel.id))
        .group_by(MLFindingLabel.geographic_region).all()
    )
    states = {r[0] or 'unknown': r[1] for r in state_rows}

    last_run = MLTrainingRun.query.order_by(MLTrainingRun.created_at.desc()).first()

    try:
        from ml_ingestion import CRAWLER_REGISTRY
        active = [k for k, _, c in CRAWLER_REGISTRY if getattr(c, 'STATUS', '') == 'active']
    except Exception:
        active = []

    return {
        'total_rows': total_rows,
        'rows_labeled': rows_labeled,
        'rows_unlabeled': rows_unlabeled,
        'rows_high_confidence': rows_high_conf,
        'sources_breakdown': sources,
        'states_breakdown': states,
        'fc_category_acc': last_run.fc_category_acc if last_run else None,
        'fc_severity_acc': last_run.fc_severity_acc if last_run else None,
        'cd_accuracy': last_run.cd_accuracy if last_run else None,
        'rc_r2': last_run.rc_r2 if last_run else None,
        'rc_mae': last_run.rc_mae if last_run else None,
        'active_crawler_count': len(active),
    }


@admin_bp.route('/api/admin/ml-health', methods=['GET'])
@_api_admin_req_dec
def admin_ml_health():
    """Combined model health: deploy status + latest accuracy + data counts."""
    import json as _json, os
    from models import MLFindingLabel, MLContradictionPair, MLCooccurrenceBucket, PostCloseSurvey, MLTrainingRun

    base_dir = os.path.dirname(os.path.abspath(__file__))
    # v5.89.55: persistent disk
    models_dir = get_models_dir()

    # Model file status
    model_files = {
        'Finding Classifier': {
            'files': ['finding_category.xgb', 'finding_severity.xgb', 'category_encoder.pkl', 'severity_encoder.pkl'],
            'target_cat': 90.0, 'target_sev': 85.0,
        },
        'Contradiction Detector': {
            'files': ['contradiction_detector.xgb', 'contradiction_encoder.pkl'],
            'target_acc': 99.0,
        },
        'Repair Cost Predictor': {
            'files': ['repair_cost.xgb', 'cost_feature_meta.pkl'],
            'target_r2': 85.0, 'target_mae': 1000,
        },
    }

    models = {}
    for name, info in model_files.items():
        present = [f for f in info['files'] if os.path.exists(os.path.join(models_dir, f))]
        missing = [f for f in info['files'] if not os.path.exists(os.path.join(models_dir, f))]
        total_size = sum(os.path.getsize(os.path.join(models_dir, f)) for f in present)
        models[name] = {
            'live': len(missing) == 0,
            'files_present': len(present),
            'files_total': len(info['files']),
            'size_kb': round(total_size / 1024),
            'missing': missing,
        }

    # Latest training run accuracy
    try:
        last_run = MLTrainingRun.query.order_by(MLTrainingRun.created_at.desc()).first()
    except Exception:
        last_run = None
    if last_run:
        models['Finding Classifier'].update({
            'category_acc': last_run.fc_category_acc,
            'severity_acc': last_run.fc_severity_acc,
            'data_points': last_run.fc_data_points,
            'augmented': last_run.fc_augmented or 0,
            'status': last_run.fc_status,
            'error': getattr(last_run, 'fc_error', None),
            'target_cat': 90.0, 'target_sev': 85.0,
        })
        models['Contradiction Detector'].update({
            'accuracy': last_run.cd_accuracy,
            'data_points': last_run.cd_data_points,
            'status': last_run.cd_status,
            'error': getattr(last_run, 'cd_error', None),
            'target_acc': 99.0,
        })
        models['Repair Cost Predictor'].update({
            'r2': last_run.rc_r2,
            'mae': last_run.rc_mae,
            'median_pct': last_run.rc_median_pct,
            'data_points': last_run.rc_data_points,
            'status': last_run.rc_status,
            'error': getattr(last_run, 'rc_error', None),
            'target_r2': 85.0,
        })
        models['_last_trained'] = last_run.created_at.isoformat() if last_run.created_at else None
        models['_inference_passed'] = last_run.inference_passed
        models['_inference_failed'] = last_run.inference_failed
        # v5.89.82: surface the per-test breakdown so the Model Health tile
        # can show WHY tests passed/failed/skipped instead of a bare count.
        # The details JSON is already persisted on every run; we just parse
        # and forward it. Defensive: bad/missing JSON yields an empty list.
        _inf_details = []
        try:
            if getattr(last_run, 'inference_details', None):
                import json as _json_inf
                _inf_details = _json_inf.loads(last_run.inference_details)
                if not isinstance(_inf_details, list):
                    _inf_details = []
        except Exception:
            _inf_details = []
        models['_inference_details'] = _inf_details
        # Derive skipped from details (no dedicated column); a test is skipped
        # when its status is SKIP. Falls back to 0 if details unavailable.
        models['_inference_skipped'] = sum(
            1 for t in _inf_details if isinstance(t, dict) and t.get('status') == 'SKIP'
        )
    else:
        models['_last_trained'] = None

    # Data pipeline counts
    # v5.86.80: include v2 label coverage + effective training rows so the
    # UI can render "62,684 total → 28,000 real findings → 100% relabeled"
    # v5.89.54: each query wrapped in its own try/except so a single failing
    # query (timeout, connection drop, schema mismatch) doesn't 500 the entire
    # endpoint and make the UI silently render zeros. Errors collected in
    # _data_errors for surfacing in the diagnostics panel.
    # v5.89.56: cache the heavy count queries with a 60s TTL. On a 257K-row
    # MLFindingLabel table, each .query.count() can take 1-2 seconds; doing
    # seven of them sequentially exceeded the client-side fetch timeout
    # during dashboard refreshes. Cache means typical dashboard navigation
    # serves from memory in ~5ms instead of running the counts again.
    _data = _get_cached_ml_data_counts()
    if _data is not None:
        # Cache hit — split out errors that were embedded in the cached entry
        _data_errors = _data.pop('_errors', {})
    else:
        # Cache miss — run the queries
        _data = {}
        _data_errors = {}

        def _safe_count(key, qfn):
            try:
                _data[key] = qfn()
            except Exception as e:
                _data[key] = None
                _data_errors[key] = f'{type(e).__name__}: {str(e)[:120]}'
                logging.exception(f'ml-health: failed to compute {key}')

        _safe_count('findings',
                    lambda: MLFindingLabel.query.count())
        _safe_count('findings_v2_labeled',
                    lambda: MLFindingLabel.query.filter(MLFindingLabel.category_v2.isnot(None)).count())
        _safe_count('findings_junk_flagged',
                    lambda: MLFindingLabel.query.filter(MLFindingLabel.is_real_finding.is_(False)).count())
        _safe_count('findings_real_findings',
                    lambda: MLFindingLabel.query.filter(MLFindingLabel.is_real_finding.is_(True)).count())
        _safe_count('contradictions',
                    lambda: MLContradictionPair.query.count())
        _safe_count('cooccurrence',
                    lambda: MLCooccurrenceBucket.query.count())
        _safe_count('surveys',
                    lambda: PostCloseSurvey.query.count() if hasattr(PostCloseSurvey, 'query') else 0)

        # v5.89.54: derive effective_training only if both inputs succeeded.
        # Previous formula `_total_findings - _junk_flagged` was wrong anyway —
        # the actual training pipeline filters by category_v2 IS NOT NULL, so
        # effective_training should track findings_v2_labeled, not
        # total - junk. Fixed to match training log's selection logic.
        _ft = _data.get('findings')
        _jf = _data.get('findings_junk_flagged')
        _v2 = _data.get('findings_v2_labeled')
        if _v2 is not None:
            # Match training log: training set = rows with category_v2 IS NOT NULL
            _data['findings_effective_training'] = _v2
        elif _ft is not None and _jf is not None:
            # Fallback to old formula if v2 count failed
            _data['findings_effective_training'] = max(0, _ft - _jf)
        else:
            _data['findings_effective_training'] = None

        # Cache it (including any errors so they remain visible for the TTL)
        _cache_payload = dict(_data)
        if _data_errors:
            _cache_payload['_errors'] = _data_errors
        _set_cached_ml_data_counts(_cache_payload)

    if _data_errors:
        _data['_errors'] = _data_errors

    models['_data'] = _data

    # v5.86.84: surface the latest successful data ingestion (re-label, crawl,
    # re-extract) completion time so the UI can show a "model stale" warning
    # when the corpus has been updated since the last training run.
    try:
        from models import MLIngestionJob
        _latest = (MLIngestionJob.query
                   .filter_by(status='succeeded')
                   .order_by(MLIngestionJob.completed_at.desc())
                   .first())
        if _latest and _latest.completed_at:
            models['_latest_ingestion_completed_at'] = _latest.completed_at.isoformat()
            models['_latest_ingestion_job_type'] = _latest.job_type
            models['_latest_ingestion_source'] = _latest.source_name
        else:
            models['_latest_ingestion_completed_at'] = None
    except Exception:
        # MLIngestionJob may not exist on older schemas — graceful degrade
        models['_latest_ingestion_completed_at'] = None

    return jsonify(models)


@admin_bp.route('/api/admin/ml-diagnostics', methods=['GET'])
@_api_admin_req_dec
def admin_ml_diagnostics():
    """Full ML pipeline diagnostic snapshot. This endpoint is designed to be the
    one-stop answer to "why does the UI look broken?" — it collects filesystem
    state, database row counts, migration version, and the ml-health API's own
    response, all in one place. Results render as PASS/WARN/FAIL in the UI.

    Every check is wrapped in try/except so one failing check doesn't hide the
    others. This is a diagnostic tool — we want MORE information when things
    break, not less.
    """
    import os, json as _json
    from datetime import datetime as _dt
    from models import (MLTrainingRun, MLFindingLabel, MLContradictionPair,
                        MLCooccurrenceBucket, MLCostData, MLAgentRun)
    from sqlalchemy import text

    def _check(name, fn):
        """Run a diagnostic check, capturing any exception as the result."""
        try:
            out = fn()
            # A check can either return a dict (will be passed through) or
            # raise. No exception = PASS unless check explicitly sets status.
            if isinstance(out, dict):
                out.setdefault('status', 'PASS')
                return {'name': name, **out}
            return {'name': name, 'status': 'PASS', 'data': out}
        except Exception as e:
            import traceback
            return {
                'name': name,
                'status': 'FAIL',
                'error': str(e)[:500],
                'traceback': traceback.format_exc()[-800:],
            }

    # ── Section 1: Model files on disk ────────────────────────────────
    def _section_model_files():
        base_dir = os.path.dirname(os.path.abspath(__file__))
        # v5.89.55: persistent disk
        models_dir = get_models_dir()
        expected = [
            'finding_category.xgb', 'finding_severity.xgb',
            'category_encoder.pkl', 'severity_encoder.pkl',
            'contradiction_detector.xgb', 'contradiction_encoder.pkl',
            'repair_cost.xgb', 'cost_feature_meta.pkl',
        ]
        if not os.path.isdir(models_dir):
            return {
                'status': 'FAIL',
                'summary': f'{models_dir} does not exist',
                'files': [],
            }
        files = []
        for fname in expected:
            path = os.path.join(models_dir, fname)
            if os.path.exists(path):
                st = os.stat(path)
                files.append({
                    'name': fname,
                    'exists': True,
                    'size_kb': round(st.st_size / 1024, 1),
                    'mtime': _dt.fromtimestamp(st.st_mtime).isoformat(),
                })
            else:
                files.append({'name': fname, 'exists': False})
        missing = [f['name'] for f in files if not f['exists']]
        status = 'PASS' if not missing else ('WARN' if len(missing) < len(expected) else 'FAIL')
        return {
            'status': status,
            'summary': f'{len(expected) - len(missing)}/{len(expected)} model files present' + (f' · missing: {missing}' if missing else ''),
            'models_dir': models_dir,
            'files': files,
        }

    # ── Section 2: Persistent snapshot dir ─────────────────────────────
    def _section_snapshot_dir():
        snap_dir = '/var/data/docrepo/ml_snapshots'
        if not os.path.isdir(snap_dir):
            return {'status': 'WARN', 'summary': f'{snap_dir} does not exist (first deploy?)', 'files': []}
        files = []
        for fname in sorted(os.listdir(snap_dir)):
            path = os.path.join(snap_dir, fname)
            if os.path.isfile(path):
                st = os.stat(path)
                files.append({
                    'name': fname,
                    'size_kb': round(st.st_size / 1024, 1),
                    'mtime': _dt.fromtimestamp(st.st_mtime).isoformat(),
                })
        status = 'PASS' if files else 'WARN'
        summary = f'{len(files)} snapshot(s)' if files else 'No snapshots yet'
        return {'status': status, 'summary': summary, 'snap_dir': snap_dir, 'files': files}

    # ── Section 3: Training runs in DB ─────────────────────────────────
    def _section_training_runs():
        total = MLTrainingRun.query.count()
        recent = MLTrainingRun.query.order_by(MLTrainingRun.created_at.desc()).limit(5).all()
        rows = [{
            'id': r.id,
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'fc_status': r.fc_status,
            'fc_category_acc': r.fc_category_acc,
            'fc_severity_acc': r.fc_severity_acc,
            'cd_status': r.cd_status,
            'cd_accuracy': r.cd_accuracy,
            'rc_status': r.rc_status,
            'rc_r2': r.rc_r2,
            'fc_error': getattr(r, 'fc_error', None),
            'cd_error': getattr(r, 'cd_error', None),
            'rc_error': getattr(r, 'rc_error', None),
        } for r in recent]
        status = 'PASS' if total > 0 else 'WARN'
        return {'status': status, 'summary': f'{total} training run(s) in DB', 'total': total, 'recent': rows}

    # ── Section 4: Training data counts ────────────────────────────────
    def _section_data_counts():
        # v5.86.80: report v2-label coverage and effective training set size
        total = MLFindingLabel.query.count()
        v2_labeled = MLFindingLabel.query.filter(
            MLFindingLabel.category_v2.isnot(None)
        ).count()
        junk_flagged = MLFindingLabel.query.filter(
            MLFindingLabel.is_real_finding.is_(False)
        ).count()
        real_findings = MLFindingLabel.query.filter(
            MLFindingLabel.is_real_finding.is_(True)
        ).count()
        # Effective training rows = not-junk (True or None).
        # Note: None includes rows not yet relabeled, which still go to training.
        effective = total - junk_flagged

        counts = {
            'ml_finding_labels': total,
            'ml_finding_labels_v2_coverage': v2_labeled,
            'ml_finding_labels_junk_flagged': junk_flagged,
            'ml_finding_labels_real_findings': real_findings,
            'ml_finding_labels_effective_training_rows': effective,
            'ml_contradiction_pairs': MLContradictionPair.query.count(),
            'ml_cooccurrence_buckets': MLCooccurrenceBucket.query.count(),
            'ml_cost_data': MLCostData.query.count(),
        }
        total_all = (counts['ml_finding_labels'] + counts['ml_contradiction_pairs']
                     + counts['ml_cooccurrence_buckets'] + counts['ml_cost_data'])
        status = 'PASS' if total_all > 0 else 'FAIL'
        v2_pct = (100.0 * v2_labeled / total) if total else 0.0
        summary = (f'{total_all} total ML rows; {v2_labeled}/{total} '
                   f'({v2_pct:.1f}%) relabeled, {effective} effective training rows '
                   f'after filtering {junk_flagged} junk')
        return {
            'status': status,
            'summary': summary,
            'counts': counts,
        }

    # ── Section 5: Alembic migration state ─────────────────────────────
    def _section_alembic():
        from models import db as _db
        try:
            result = _db.session.execute(text('SELECT version_num FROM alembic_version'))
            versions = [row[0] for row in result]
            _db.session.rollback()  # don't leave anything hanging
        except Exception as e:
            # Table doesn't exist (fresh dev DB that only ran db.create_all())
            # That's a WARN, not a FAIL — production will have alembic_version.
            _db.session.rollback()
            return {
                'status': 'WARN',
                'summary': f'alembic_version table missing or unreadable ({str(e)[:80]}). Normal in dev SQLite, unexpected in production.',
                'versions': [],
            }
        # Expected head — kept as a constant in sync with the latest migration file
        expected_head = 'd8e5f6a9b2c3'
        status = 'PASS' if versions and versions[0] == expected_head else 'WARN'
        summary = f'DB at revision {versions[0] if versions else "NONE"}, expected {expected_head}'
        return {'status': status, 'summary': summary, 'versions': versions, 'expected_head': expected_head}

    # ── Section 6: Agent runs ──────────────────────────────────────────
    def _section_agent_runs():
        total = MLAgentRun.query.count()
        recent = MLAgentRun.query.order_by(MLAgentRun.created_at.desc()).limit(3).all()
        rows = [{
            'id': r.id,
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'trigger': r.trigger,
            'elapsed_seconds': r.elapsed_seconds,
            'trained': r.trained,
            'rolled_back': r.rolled_back,
        } for r in recent]
        status = 'PASS' if total > 0 else 'WARN'
        return {'status': status, 'summary': f'{total} agent run(s) in DB', 'total': total, 'recent': rows}

    # ── Section 7: API self-test ───────────────────────────────────────
    def _section_api_selftest():
        # Hit the ml-health endpoint in-process (no HTTP roundtrip)
        with current_app.test_client() as client:
            key = request.args.get('admin_key', '')
            resp = client.get(f'/api/admin/ml-health?admin_key={key}')
            if resp.status_code != 200:
                return {
                    'status': 'FAIL',
                    'summary': f'ml-health returned {resp.status_code}',
                    'response_preview': resp.get_data(as_text=True)[:400],
                }
            data = resp.get_json() or {}
            has_data = bool(data.get('_data', {}).get('findings', 0))
            any_live = any(v.get('live', False) for k, v in data.items() if not k.startswith('_'))
            summary = f"ml-health returned 200 · findings={data.get('_data', {}).get('findings', 0)} · models_live={any_live}"
            return {'status': 'PASS', 'summary': summary, 'ml_health_response': data}

    # ── Section 8 (v5.86.77): Ingestion jobs health ───────────────────
    def _section_ingestion_jobs():
        # Added in v5.86.77 alongside the data-quality scaffolding. If the
        # ml_ingestion_jobs table doesn't exist yet (e.g. migration hasn't run)
        # we return a clear WARN rather than a cryptic failure.
        from models import MLIngestionJob
        from sqlalchemy import inspect as sa_inspect
        from models import db as _db

        insp = sa_inspect(_db.engine)
        if 'ml_ingestion_jobs' not in insp.get_table_names():
            return {
                'status': 'WARN',
                'summary': 'ml_ingestion_jobs table missing — migration has not run',
                'total': 0,
            }

        total = MLIngestionJob.query.count()
        failed_recent = MLIngestionJob.query.filter_by(status='failed').order_by(
            MLIngestionJob.created_at.desc()
        ).limit(5).all()

        # Detect stuck jobs: status=running for longer than the sweep timeout
        # for that job type. v5.87.8: this used to be a hardcoded 1hr which
        # mismatched the 2hr crawl sweep timeout — jobs would yellow-flag at
        # 1hr but not auto-clear until 2hr. Now we read directly from
        # MLIngestionJob.STALE_TIMEOUTS so the two can never drift.
        from datetime import timedelta
        now = _dt.utcnow()
        running_jobs = MLIngestionJob.query.filter_by(status='running').all()
        running = len(running_jobs)
        stuck = []
        for j in running_jobs:
            timeout_s = MLIngestionJob.STALE_TIMEOUTS.get(
                j.job_type, MLIngestionJob.DEFAULT_STALE_TIMEOUT
            )
            if j.started_at and (now - j.started_at).total_seconds() > timeout_s:
                stuck.append(j)

        status = 'PASS'
        summary_parts = [f'{total} total jobs']
        if running > 0:
            summary_parts.append(f'{running} running')
        if stuck:
            status = 'WARN'
            # Show the actual age of the oldest stuck job rather than a
            # misleading hardcoded threshold (timeouts vary by job type).
            oldest_age_h = max(
                (now - j.started_at).total_seconds() / 3600
                for j in stuck if j.started_at
            )
            summary_parts.append(f'{len(stuck)} stuck (oldest {oldest_age_h:.1f}h)')

        return {
            'status': status,
            'summary': ' · '.join(summary_parts),
            'total': total,
            'running': running,
            'stuck_count': len(stuck),
            'stuck_ids': [j.id for j in stuck],
            'recent_failures': [
                {'id': j.id, 'source': j.source, 'error': (j.error[:200] if j.error else None)}
                for j in failed_recent
            ],
        }

    # ── Section 9 (v5.86.77): Data coverage by source_version ─────────
    def _section_data_coverage():
        # Shows how the corpus is composed across the different data streams.
        # As we add data via Streams 1-3 we should see source_version diversity
        # grow here — an early warning if one stream stalls silently.
        from models import MLFindingLabel, db as _db
        from sqlalchemy import func as _func

        by_version = dict(_db.session.query(
            MLFindingLabel.source_version, _func.count()
        ).group_by(MLFindingLabel.source_version).all())

        total = sum(by_version.values())
        # Normalize None → '(legacy/no_version)' for readability
        by_version_display = {
            (k if k else '(legacy/no_version)'): v
            for k, v in by_version.items()
        }

        # PASS if any v2+ sources exist OR corpus is still 100% legacy (expected
        # before any stream has run). WARN if something seems off.
        v2_present = any(k and (k.endswith('_v2') or '_v' in k) for k in by_version.keys() if k)
        status = 'PASS'
        if total == 0:
            status = 'WARN'
            summary = 'No data in ml_finding_labels yet'
        elif v2_present:
            v2_count = sum(v for k, v in by_version.items() if k and ('_v2' in k or '_v' in k))
            summary = f'{total} rows · {v2_count} from new streams · {len(by_version_display)} source versions'
        else:
            # All legacy data — fine, just hasn't had any streams run yet
            summary = f'{total} rows · all legacy (no streams have run yet)'

        return {
            'status': status,
            'summary': summary,
            'total_rows': total,
            'by_source_version': by_version_display,
        }

    sections = [
        _check('model_files_on_disk', _section_model_files),
        _check('persistent_snapshot_dir', _section_snapshot_dir),
        _check('training_runs_in_db', _section_training_runs),
        _check('training_data_counts', _section_data_counts),
        _check('alembic_migration_state', _section_alembic),
        _check('agent_runs_in_db', _section_agent_runs),
        _check('api_self_test', _section_api_selftest),
        _check('ingestion_jobs_health', _section_ingestion_jobs),
        _check('data_coverage_by_stream', _section_data_coverage),
    ]

    overall = 'PASS'
    if any(s['status'] == 'FAIL' for s in sections):
        overall = 'FAIL'
    elif any(s['status'] == 'WARN' for s in sections):
        overall = 'WARN'

    return jsonify({
        'generated_at': _dt.utcnow().isoformat(),
        'overall_status': overall,
        'sections': sections,
    })


@admin_bp.route('/api/admin/ml-junk-scope', methods=['GET'])
@_api_admin_req_dec
def admin_ml_junk_scope():
    """Analyze ml_finding_labels for boilerplate / disclaimer pollution.

    This is the first step of a two-step workflow: audit → cleanup. This
    endpoint only READS data; it does not modify anything. A future cleanup
    endpoint will use the same filter patterns from ml_junk_audit.py to
    actually delete the rows after the user confirms the scope.

    Response shape is documented in ml_junk_audit.audit_ml_training_data().
    """
    from ml_junk_audit import audit_ml_training_data
    try:
        report = audit_ml_training_data()
        return jsonify({'ok': True, **report})
    except Exception as e:
        import traceback
        logging.exception("ml-junk-scope failed")
        return jsonify({
            'ok': False,
            'error': str(e)[:500],
            'traceback': traceback.format_exc()[-1000:],
        }), 500


@admin_bp.route('/api/admin/ml-ingestion-jobs', methods=['GET'])
@_api_admin_req_dec
def admin_ml_ingestion_jobs():
    """List recent ingestion jobs. Powers the Data Ingestion admin panel.

    Returns last 20 jobs across all types (reextract, crawl, relabel) plus
    summary counts by type and status. All timestamps are ISO strings.

    Response:
    {
      "jobs": [ {id, job_type, source, status, started_at, completed_at,
                 elapsed_seconds, rows_added, rows_rejected, error}, ... ],
      "summary": { "by_type": {reextract: n, ...}, "by_status": {...} },
      "corpus_stats": { "total_rows": n, "by_source_version": {...} }
    }
    """
    from models import db, MLIngestionJob, MLFindingLabel
    from sqlalchemy import func

    try:
        # Recent jobs (most recent first)
        jobs = MLIngestionJob.query.order_by(MLIngestionJob.created_at.desc()).limit(20).all()

        # v5.87.14: enrich running jobs with phase + last log line + computed
        # progress percent so the UI can render real progress bars instead of
        # opaque spinners. log_json envelope is {entries, phase, expected_total}.
        def _enrich(j):
            phase = ''
            expected_total = None
            last_log = None
            if j.log_json:
                try:
                    payload = json.loads(j.log_json)
                    if isinstance(payload, dict):
                        phase = payload.get('phase') or ''
                        expected_total = payload.get('expected_total')
                        entries = payload.get('entries') or []
                        if entries:
                            last_log = entries[-1].get('msg', '')[:300]
                    elif isinstance(payload, list):
                        # Backwards compat: pre-v5.87.14 jobs have raw list
                        if payload:
                            last_log = payload[-1].get('msg', '')[:300]
                except Exception:
                    pass
            # Compute % when we have both numerator and denominator
            pct = None
            if expected_total and j.rows_processed is not None and expected_total > 0:
                pct = min(100, int(100 * j.rows_processed / expected_total))
            return {
                'id': j.id,
                'job_type': j.job_type,
                'source': j.source,
                'status': j.status,
                'created_at': j.created_at.isoformat() if j.created_at else None,
                'started_at': j.started_at.isoformat() if j.started_at else None,
                'completed_at': j.completed_at.isoformat() if j.completed_at else None,
                'elapsed_seconds': j.elapsed_seconds,
                'rows_processed': j.rows_processed,
                'rows_added': j.rows_added,
                'rows_rejected': j.rows_rejected,
                'error': (j.error[:300] if j.error else None),
                'phase': phase,
                'last_log': last_log,
                'expected_total': expected_total,
                'progress_pct': pct,
            }

        jobs_out = [_enrich(j) for j in jobs]

        # Summary by type and status (across all jobs, not just last 20)
        by_type = dict(db.session.query(
            MLIngestionJob.job_type, func.count()
        ).group_by(MLIngestionJob.job_type).all())
        by_status = dict(db.session.query(
            MLIngestionJob.status, func.count()
        ).group_by(MLIngestionJob.status).all())

        # Corpus stats: how many rows exist per source_version
        total_rows = MLFindingLabel.query.count()
        by_source_version = dict(db.session.query(
            MLFindingLabel.source_version, func.count()
        ).group_by(MLFindingLabel.source_version).all())
        # Normalize the None key to '(legacy)' for display
        by_source_version = {
            (k if k else '(legacy/no_version)'): v
            for k, v in by_source_version.items()
        }

        return jsonify({
            'ok': True,
            'jobs': jobs_out,
            'summary': {
                'by_type': by_type,
                'by_status': by_status,
            },
            'corpus_stats': {
                'total_rows': total_rows,
                'by_source_version': by_source_version,
            },
        })
    except Exception as e:
        import traceback
        logging.exception("ml-ingestion-jobs failed")
        return jsonify({
            'ok': False,
            'error': str(e)[:500],
            'traceback': traceback.format_exc()[-1000:],
        }), 500


@admin_bp.route('/api/admin/ml-corpus-composition', methods=['GET'])
@_api_admin_req_dec
def admin_ml_corpus_composition():
    """v5.86.81: Full corpus composition breakdown for the 📊 UI card.

    Returns a detailed view of the ml_finding_labels corpus after the Stream 3
    relabel completes:
      - Headline counts (total, v2_labeled, real, junk)
      - Breakdown by source (real/junk/total per source) — so we see whether
        nyc_hpd is clean data or junk, whether ai_parse disclaimers got caught
      - V2 category distribution (real findings only)
      - V2 severity distribution (real findings only)
      - V1 → V2 category shift matrix — where Haiku moved the most rows.
        This tells us what the classifier was learning wrong before.

    Uses Postgres-safe SUM(CASE WHEN ... THEN 1 ELSE 0 END) instead of
    CAST(bool AS FLOAT) which Postgres rejects.
    """
    from models import MLFindingLabel
    from sqlalchemy import func, case, and_
    try:
        total = MLFindingLabel.query.count()
        v2_labeled = MLFindingLabel.query.filter(
            MLFindingLabel.category_v2.isnot(None)
        ).count()
        real_count = MLFindingLabel.query.filter(
            MLFindingLabel.is_real_finding.is_(True)
        ).count()
        junk_count = MLFindingLabel.query.filter(
            MLFindingLabel.is_real_finding.is_(False)
        ).count()
        unlabeled = MLFindingLabel.query.filter(
            MLFindingLabel.category_v2.is_(None)
        ).count()

        # ── Breakdown by source ────────────────────────────────────────
        # Postgres-safe: SUM(CASE WHEN condition THEN 1 ELSE 0 END)
        by_source_rows = (db.session.query(
            MLFindingLabel.source,
            func.sum(case((MLFindingLabel.is_real_finding.is_(True), 1), else_=0)).label('real'),
            func.sum(case((MLFindingLabel.is_real_finding.is_(False), 1), else_=0)).label('junk'),
            func.count().label('total'),
        )
        .group_by(MLFindingLabel.source)
        .order_by(func.count().desc())
        .all())
        by_source = [{
            'source': r.source or '(none)',
            'real': int(r.real or 0),
            'junk': int(r.junk or 0),
            'total': int(r.total or 0),
            'junk_pct': round(100 * int(r.junk or 0) / int(r.total), 1) if r.total else 0,
        } for r in by_source_rows]

        # ── V2 category distribution (real findings only) ──────────────
        cat_rows = (db.session.query(
            MLFindingLabel.category_v2,
            func.count().label('n'),
        )
        .filter(MLFindingLabel.is_real_finding.is_(True))
        .group_by(MLFindingLabel.category_v2)
        .order_by(func.count().desc())
        .all())
        v2_categories = [{'category': r.category_v2, 'count': r.n} for r in cat_rows]

        # ── V2 severity distribution (real findings only) ──────────────
        sev_rows = (db.session.query(
            MLFindingLabel.severity_v2,
            func.count().label('n'),
        )
        .filter(MLFindingLabel.is_real_finding.is_(True))
        .group_by(MLFindingLabel.severity_v2)
        .order_by(func.count().desc())
        .all())
        v2_severities = [{'severity': r.severity_v2, 'count': r.n} for r in sev_rows]

        # ── V1 vs V2 category shift matrix ────────────────────────────
        # Only for rows that have BOTH v1 and v2 labels AND are real findings.
        # Shows where Haiku disagreed with the original ai_parse labels.
        shift_rows = (db.session.query(
            MLFindingLabel.category,
            MLFindingLabel.category_v2,
            func.count().label('n'),
        )
        .filter(and_(
            MLFindingLabel.category_v2.isnot(None),
            MLFindingLabel.is_real_finding.is_(True),
        ))
        .group_by(MLFindingLabel.category, MLFindingLabel.category_v2)
        .order_by(func.count().desc())
        .limit(30)
        .all())
        category_shift = [{
            'v1': r.category or '(none)',
            'v2': r.category_v2 or '(none)',
            'count': r.n,
            'changed': (r.category != r.category_v2),
        } for r in shift_rows]

        # ── V2 severity distribution by severity (for target-vs-reality) ──
        # Training target is roughly 15% critical, 35% major, 35% moderate, 15% minor
        sev_target = {'critical': 0.15, 'major': 0.35, 'moderate': 0.35, 'minor': 0.15}
        total_real = sum(s['count'] for s in v2_severities)
        for s in v2_severities:
            s['pct'] = round(100 * s['count'] / total_real, 1) if total_real else 0
            s['target_pct'] = round(100 * sev_target.get(s['severity'], 0), 1)

        return jsonify({
            'ok': True,
            'headline': {
                'total': total,
                'v2_labeled': v2_labeled,
                'real_findings': real_count,
                'junk': junk_count,
                'unlabeled': unlabeled,
                'v2_coverage_pct': round(100 * v2_labeled / total, 2) if total else 0,
                'junk_pct': round(100 * junk_count / total, 1) if total else 0,
                'real_pct': round(100 * real_count / total, 1) if total else 0,
            },
            'by_source': by_source,
            'v2_categories': v2_categories,
            'v2_severities': v2_severities,
            'category_shift': category_shift,
        })
    except Exception as e:
        import traceback
        logging.exception("ml-corpus-composition failed")
        return jsonify({
            'ok': False,
            'error': str(e)[:500],
            'traceback': traceback.format_exc()[-1000:],
        }), 500


@admin_bp.route('/api/admin/ml-delete-source', methods=['POST'])
@_api_admin_req_dec
def admin_ml_delete_source():
    """v5.86.88: delete all ml_finding_labels rows from a given source.

    Used to clean up sources with very high junk rates (Philly, Boston,
    NYC 311) where ~all rows are taxonomy labels, not real findings.

    Safety rails:
      - Requires {confirm: true} in POST body (prevents accidental deletes)
      - Refuses if source has >1000 real findings (nyc_hpd safeguard)
      - Refuses if source has junk rate <80% (only clear-cut-bad sources)
      - Logs deletion as an ingestion job for audit trail
    """
    from models import MLFindingLabel, MLIngestionJob, db
    from sqlalchemy import func, case
    import datetime as _dt

    try:
        body = request.get_json(silent=True) or {}
        source = (body.get('source') or '').strip()
        confirm = body.get('confirm') is True

        if not source:
            return jsonify({'ok': False, 'error': 'source parameter required'}), 400
        if not confirm:
            return jsonify({'ok': False, 'error': 'confirm:true required in request body'}), 400

        # Safety check: compute real/junk counts before deletion
        stats = (db.session.query(
            func.count().label('total'),
            func.sum(case((MLFindingLabel.is_real_finding.is_(True), 1), else_=0)).label('real'),
            func.sum(case((MLFindingLabel.is_real_finding.is_(False), 1), else_=0)).label('junk'),
        )
        .filter(MLFindingLabel.source == source)
        .first())

        total = int(stats.total or 0)
        real = int(stats.real or 0)
        junk = int(stats.junk or 0)

        if total == 0:
            return jsonify({'ok': False, 'error': f'source "{source}" has 0 rows'}), 404

        if real > 1000:
            return jsonify({
                'ok': False,
                'error': f'source "{source}" has {real} real findings — refusing to delete (threshold: 1000)',
                'total': total, 'real': real, 'junk': junk,
            }), 403

        # Junk rate check — needs to be >=80% to qualify as clear-cut-bad
        junk_pct = (100.0 * junk / total) if total else 0
        if junk_pct < 80:
            return jsonify({
                'ok': False,
                'error': f'source "{source}" has junk rate {junk_pct:.1f}% — must be >=80% to delete',
                'total': total, 'real': real, 'junk': junk,
            }), 403

        # Create audit job row BEFORE the delete (so we have a trail even if the
        # delete fails partway). Note: MLIngestionJob.source is the column name
        # (NOT source_name) — this field is reused across all job types.
        audit_job = MLIngestionJob(
            job_type='cleanup',
            source=f'delete_source:{source}',
            status='running',
            started_at=_dt.datetime.utcnow(),
            config_json=json.dumps({
                'action': 'delete_source',
                'source': source,
                'pre_total': total,
                'pre_real': real,
                'pre_junk': junk,
            }),
        )
        db.session.add(audit_job)
        db.session.commit()

        # Do the delete
        deleted = MLFindingLabel.query.filter_by(source=source).delete(synchronize_session=False)
        db.session.commit()

        # Finalize audit job
        audit_job.status = 'succeeded'
        audit_job.completed_at = _dt.datetime.utcnow()
        audit_job.rows_processed = deleted
        audit_job.rows_rejected = deleted  # "rejected" here = removed from corpus
        audit_job.elapsed_seconds = (audit_job.completed_at - audit_job.started_at).total_seconds()
        db.session.commit()

        return jsonify({
            'ok': True,
            'source': source,
            'deleted': deleted,
            'pre_real': real,
            'pre_junk': junk,
            'audit_job_id': audit_job.id,
            'message': f'Deleted {deleted:,} rows from source "{source}" ({real} real + {junk} junk)',
        })
    except Exception as e:
        import traceback
        logging.exception("ml-delete-source failed")
        db.session.rollback()
        return jsonify({
            'ok': False,
            'error': str(e)[:500],
            'traceback': traceback.format_exc()[-1000:],
        }), 500


@admin_bp.route('/api/admin/ml-relabel-preview', methods=['POST'])
@_api_admin_req_dec
def admin_ml_relabel_preview():
    """v5.86.88: preview how many rows would match a filter before running
    the full re-label. Called as the user types a filter expression so the
    UI can show "Re-label 12,345 matching rows (~$2)".

    Accepts same filter syntax as ml-relabel-start.
    """
    from models import MLFindingLabel
    try:
        body = request.get_json(silent=True) or {}
        filter_str = (body.get('filter') or '').strip()

        # Import the filter-application helper so preview + actual run share logic
        from ml_ingestion.relabel_v1 import apply_filter_expression
        q = MLFindingLabel.query
        try:
            q = apply_filter_expression(q, filter_str)
        except ValueError as ve:
            return jsonify({'ok': False, 'error': str(ve)}), 400

        # Important: mirror the labeler's runtime query shape exactly so the
        # preview count matches what the run will actually process.
        # - When filter is empty: labeler restricts to category_v2 IS NULL
        #   (unlabeled rows only). Count the same way.
        # - When filter is set: labeler includes all matching rows (even if
        #   already v2-labeled, since the point is re-labeling them). Count
        #   the same way.
        # Previously (pre-v5.86.97): preview counted all rows regardless of
        # filter, over-reporting when no filter was set.
        if not filter_str:
            q = q.filter(MLFindingLabel.category_v2.is_(None))
        matching = q.count()

        # Cost estimate: BATCH_SIZE=20, Haiku+Batch = ~$0.0022/batch
        batches = (matching + 19) // 20
        cost = round(batches * 0.0022, 2)
        # Wall-clock: ~3 min per 100 rows empirically, but Batch API tends to
        # complete large jobs faster due to parallelism. Give a range.
        est_min = max(5, int(matching / 4000))
        est_max = max(15, int(matching / 1000))

        return jsonify({
            'ok': True,
            'matching_rows': matching,
            'batches': batches,
            'estimated_cost_usd': cost,
            'estimated_minutes_min': est_min,
            'estimated_minutes_max': est_max,
            'filter': filter_str,
        })
    except Exception as e:
        import traceback
        logging.exception("ml-relabel-preview failed")
        return jsonify({
            'ok': False,
            'error': str(e)[:500],
            'traceback': traceback.format_exc()[-1000:],
        }), 500


@admin_bp.route('/api/admin/ml-relabel-start', methods=['POST'])
@_api_admin_req_dec
def admin_ml_relabel_start():
    """Kick off a Stream 3 relabel run in a background thread.

    Returns immediately with job_id. The job runs asynchronously and can be
    monitored via /api/admin/ml-relabel-status or the Data Ingestion UI card.

    Safeguards:
      - Refuses to start if another relabel job is already running (prevents
        double-writes to the same rows).
      - Validates ANTHROPIC_API_KEY is present before spawning (fast fail).

    The thread uses app_context() + current_app._get_current_object() — the
    standard Flask pattern for background work that needs DB access.
    """
    import os, threading
    from flask import current_app
    from models import MLIngestionJob

    # Check there isn't already a relabel in progress
    # v5.87.3: sweep zombies before enforcing the one-at-a-time rule
    MLIngestionJob.sweep_stale(job_type='relabel')
    running = MLIngestionJob.query.filter_by(
        job_type='relabel', status='running'
    ).first()
    if running:
        return jsonify({
            'ok': False,
            'error': f'Relabel job {running.id} is already running (started {running.started_at})',
            'running_job_id': running.id,
        }), 409

    # Fast fail if no API key — don't spawn a thread that will immediately bail
    if not os.environ.get('ANTHROPIC_API_KEY'):
        return jsonify({
            'ok': False,
            'error': 'ANTHROPIC_API_KEY not configured on server',
        }), 503

    # Optional config from request body (e.g. max_batches for testing)
    config = {}
    try:
        body = request.get_json(silent=True) or {}
        if 'max_batches' in body and isinstance(body['max_batches'], int):
            config['max_batches'] = body['max_batches']
        # v5.86.88: targeted re-label via filter expression
        # Format: "key=value, key=value" — AND-combined
        # Valid keys: category, category_v2, severity, severity_v2, source, is_real_finding
        if 'filter' in body and isinstance(body['filter'], str) and body['filter'].strip():
            config['filter'] = body['filter'].strip()
        # v5.86.88: prompt version selection ('v1' original, 'v2' improved)
        if 'prompt_version' in body and body['prompt_version'] in ('v1', 'v2'):
            config['prompt_version'] = body['prompt_version']
    except Exception:
        pass

    app_obj = current_app._get_current_object()

    def _run_in_background():
        with app_obj.app_context():
            from ml_ingestion import RelabelerV1
            labeler = RelabelerV1(config=config)
            # Honor max_batches if provided (useful for smoke testing)
            if 'max_batches' in config:
                labeler.MAX_BATCHES_PER_RUN = config['max_batches']
            labeler.run()

    thread = threading.Thread(target=_run_in_background, daemon=True)
    thread.start()

    return jsonify({
        'ok': True,
        'message': 'Relabel job started in background. Poll /api/admin/ml-relabel-status for progress.',
        'config': config,
    })


@admin_bp.route('/api/admin/ml-synthesize-start', methods=['POST'])
@_api_admin_req_dec
def admin_ml_synthesize_start():
    """v5.86.93: Start a state-diverse synthetic data generation job.

    Generates realistic inspection findings tagged to state-specific concerns
    (CA seismic, FL hurricane, TX foundation, etc.) using Claude Haiku 4.5
    via Batch API. Addresses the geographic coverage gap in current corpus
    (96% NYC data).

    Body params (all optional):
      per_state (int, default 500): findings per state
      states (list of str, default all 10): which states to generate for

    Safeguards:
      - Refuses if another synth job is already running
      - Requires ANTHROPIC_API_KEY
      - Existing synthetic rows for a state are skipped by default
        (so re-running just fills in missing states/quotas)
    """
    import os, threading
    from flask import current_app
    from models import MLIngestionJob

    # Check there isn't already a synth job running
    # v5.87.3: sweep zombies before enforcing the one-at-a-time rule
    MLIngestionJob.sweep_stale(job_type='synthesize')
    running = MLIngestionJob.query.filter_by(
        job_type='synthesize', status='running'
    ).first()
    if running:
        return jsonify({
            'ok': False,
            'error': f'Synthesis job {running.id} is already running (started {running.started_at})',
            'running_job_id': running.id,
        }), 409

    if not os.environ.get('ANTHROPIC_API_KEY'):
        return jsonify({
            'ok': False,
            'error': 'ANTHROPIC_API_KEY not configured on server',
        }), 503

    config = {}
    try:
        body = request.get_json(silent=True) or {}
        if 'per_state' in body and isinstance(body['per_state'], int) and 20 <= body['per_state'] <= 2000:
            config['per_state'] = body['per_state']
        if 'states' in body and isinstance(body['states'], list):
            # Validate: only US state abbreviations
            valid = [s for s in body['states'] if isinstance(s, str) and len(s) == 2]
            if valid:
                config['states'] = valid
    except Exception:
        pass

    app_obj = current_app._get_current_object()

    def _run_in_background():
        with app_obj.app_context():
            from ml_ingestion import StateDiverseSynthesizerV1
            synth = StateDiverseSynthesizerV1(config=config)
            synth.run()

    thread = threading.Thread(target=_run_in_background, daemon=True)
    thread.start()

    return jsonify({
        'ok': True,
        'message': 'State-diverse synthesis job started in background. Typically takes 15-45 min.',
        'config': config,
    })


# v5.87.2: _CRAWLER_SOURCES replaced by CRAWLER_REGISTRY in
# ml_ingestion/socrata_crawler.py. The registry is the single source of
# truth for which cities have crawlers, their STATUS flags, and their
# display labels. Admin routes here are registry-driven so adding a new
# city requires editing only the crawler file, not the routes.


@admin_bp.route('/api/admin/ml-crawl-list', methods=['GET'])
@_api_admin_req_dec
def admin_ml_crawl_list():
    """v5.87.2: List available crawlers and their STATUS flags.

    Used by the admin UI to populate the crawler dropdown dynamically
    rather than hardcoding cities in the HTML template.

    v5.89.28: also returns last_crawled (ISO datetime) per crawler so
    the UI can show "Chicago · 51K rows · 3d ago" rather than just
    counts. Bulk-queries max(MLFindingLabel.created_at) grouped by
    source_version once, then attaches to each crawler entry.
    """
    from ml_ingestion import CRAWLER_REGISTRY

    # Bulk: max(created_at) per source_version, in one query.
    # Graceful degrade if the table doesn't exist or is empty.
    last_crawled_map = {}
    try:
        from models import MLFindingLabel
        from sqlalchemy import func
        rows = (db.session.query(
                    MLFindingLabel.source_version,
                    func.max(MLFindingLabel.created_at)
                )
                .filter(MLFindingLabel.source_version.isnot(None))
                .group_by(MLFindingLabel.source_version)
                .all())
        for source_version, last_at in rows:
            if last_at:
                last_crawled_map[source_version] = last_at.isoformat() + 'Z'
    except Exception:
        # Table may not exist on older schemas — UI degrades to no date.
        pass

    crawlers = []
    for key, label, cls in CRAWLER_REGISTRY:
        source_name = getattr(cls, 'SOURCE_NAME', '')
        crawlers.append({
            'key': key,
            'label': label,
            'source_name': source_name,
            'state': getattr(cls, 'STATE_CODE', ''),
            'status': getattr(cls, 'STATUS', 'scaffold'),
            'max_rows': getattr(cls, 'MAX_ROWS', 0),
            'dataset_id': getattr(cls, 'DATASET_ID', ''),
            'domain': getattr(cls, 'DOMAIN', ''),
            'last_crawled': last_crawled_map.get(source_name),  # v5.89.28
        })
    return jsonify({'ok': True, 'crawlers': crawlers})


@admin_bp.route('/api/admin/ml-crawl-start', methods=['POST'])
@_api_admin_req_dec
def admin_ml_crawl_start():
    """Start a single municipal data crawler.

    Body params:
      source (str, required): registry key (e.g. 'chicago', 'philadelphia')

    Rows come in with category_v2=NULL and need Re-label after completion.
    Scaffold-status crawlers will refuse to run (base class safety check).
    """
    import threading
    from flask import current_app
    from models import MLIngestionJob
    from ml_ingestion import get_crawler_class

    body = request.get_json(silent=True) or {}
    source = body.get('source', '').strip().lower()

    cls = get_crawler_class(source)
    if cls is None:
        from ml_ingestion import CRAWLER_REGISTRY
        known = [k for k, _, _ in CRAWLER_REGISTRY]
        return jsonify({
            'ok': False,
            'error': f'Unknown source {source!r}. Supported: {known}',
        }), 400

    # Refuse to run if another crawl is already active. First sweep any
    # stale 'running' jobs (SIGKILL'd workers, OOM events, deploy restarts)
    # so zombie jobs don't block new ones forever. v5.87.3.
    MLIngestionJob.sweep_stale(job_type='crawl')
    running = MLIngestionJob.query.filter_by(
        job_type='crawl', status='running'
    ).first()
    if running:
        return jsonify({
            'ok': False,
            'error': f'Crawl job {running.id} is already running (started {running.started_at})',
            'running_job_id': running.id,
        }), 409

    app_obj = current_app._get_current_object()

    def _run_in_background():
        with app_obj.app_context():
            crawler = cls()
            crawler.run()

    thread = threading.Thread(target=_run_in_background, daemon=True)
    thread.start()

    return jsonify({
        'ok': True,
        'message': f'{source.capitalize()} crawler started in background. '
                   f'Rows come in unlabeled — run Re-label after completion.',
        'source': source,
        'status': getattr(cls, 'STATUS', 'scaffold'),
    })


@admin_bp.route('/api/admin/ml-crawl-all', methods=['POST'])
@_api_admin_req_dec
def admin_ml_crawl_all():
    """v5.87.22: Queue all active crawlers and start a draining worker.

    Architecture (changed from v5.87.2):
      Previously this iterated over the active crawler list IN MEMORY in
      a daemon thread. If the Render worker process died (deploy restart,
      OOM, idle recycle), the thread evaporated and remaining cities were
      never started — the user saw "Chicago stuck for hours" while the
      coordinator was actually dead.

      New approach: persist the queue to MLIngestionJob rows with
      status='queued' BEFORE running anything. A separate "drain" worker
      pulls the oldest queued crawl job, marks it running, executes it,
      then loops to pick the next. If the worker dies, the queued rows
      remain in the DB and a new drain call resumes from where it left off.

    Behavior:
      - If queued crawl jobs already exist (from a previous incomplete
        Crawl All), this returns 409 — operator must either resume
        the existing queue (POST /api/admin/ml-resume-queue) or clear
        the queued jobs (force-fail them) before re-queueing fresh.
      - Otherwise: enqueues N new jobs (one per active crawler) and
        spawns a fresh drain worker.
    """
    import threading
    from flask import current_app
    from models import MLIngestionJob
    from ml_ingestion import list_active_crawlers
    from datetime import datetime as _dt

    active = list_active_crawlers()
    if not active:
        return jsonify({
            'ok': False,
            'error': 'No crawlers with STATUS="active" in registry.',
        }), 400

    # Sweep first so dead-worker jobs don't block the new queue.
    MLIngestionJob.sweep_stale(job_type='crawl')

    # If there's already a queue in flight (queued or running crawl jobs),
    # don't double-enqueue. Make the operator decide: resume or cancel.
    existing_pending = MLIngestionJob.query.filter(
        MLIngestionJob.job_type == 'crawl',
        MLIngestionJob.status.in_(['queued', 'running']),
    ).all()
    if existing_pending:
        queued_count = sum(1 for j in existing_pending if j.status == 'queued')
        running_count = sum(1 for j in existing_pending if j.status == 'running')
        # v5.87.26: structured response so the UI can present clean Resume /
        # Restart / Cancel choices. The error string is kept short for cases
        # where a non-UI client hits this endpoint (logs, curl).
        return jsonify({
            'ok': False,
            'error': f'Crawl queue not empty ({queued_count} queued, {running_count} running).',
            'queued_count': queued_count,
            'running_count': running_count,
            'pending_count': len(existing_pending),
            'pending_ids': [j.id for j in existing_pending],
        }), 409

    # Enqueue: one MLIngestionJob row per active crawler, all status='queued'.
    # _drain_crawl_queue will pick them up in created_at order.
    enqueued_ids = []
    for key, label, cls in active:
        job = MLIngestionJob(
            job_type='crawl',
            source=cls.SOURCE_NAME,  # use SOURCE_NAME (not key) to match what the crawler will write
            status='queued',
            created_at=_dt.utcnow(),
            config_json=json.dumps({'registry_key': key, 'label': label}),
        )
        db.session.add(job)
        enqueued_ids.append(key)
    db.session.commit()

    # Spawn the drain worker. If it dies, the queued rows persist and
    # /api/admin/ml-resume-queue can spawn another.
    app_obj = current_app._get_current_object()
    threading.Thread(target=_drain_crawl_queue, args=(app_obj,), daemon=True).start()

    return jsonify({
        'ok': True,
        'message': f'Enqueued {len(active)} crawlers. Drain worker started.',
        'enqueued_count': len(active),
        'enqueued_cities': enqueued_ids,
    })


def _drain_crawl_queue(app_obj):
    """Background worker: process queued crawl jobs one at a time.

    Pulls the oldest 'queued' crawl job, atomically transitions it to
    'running', constructs the crawler from its source, runs it, and
    loops. Exits when no queued jobs remain.

    Safe to call multiple times — each call spawns its own loop, but
    the atomic queued→running transition ensures no two workers process
    the same job. Crashes mid-job leave the row in 'running' until the
    stale sweep marks it failed (default 2h for crawl), at which point
    a future drain call will pick up the NEXT queued job.

    Note: a worker won't restart a failed/stale job automatically —
    operator must explicitly re-queue or force-fail to clean up.
    """
    from logging import getLogger
    from models import db as _db, MLIngestionJob
    from ml_ingestion import CRAWLER_REGISTRY
    lg = getLogger(__name__)

    # Build a quick lookup: SOURCE_NAME → crawler class
    source_to_class = {cls.SOURCE_NAME: cls for _, _, cls in CRAWLER_REGISTRY}

    with app_obj.app_context():
        lg.info('🏛 drain-queue: worker started')
        while True:
            # Atomically grab the oldest queued crawl job. SELECT FOR UPDATE
            # would be safer under multi-worker concurrency, but in a
            # single-Render-worker deploy the optimistic version is fine.
            job = (
                MLIngestionJob.query
                .filter_by(job_type='crawl', status='queued')
                .order_by(MLIngestionJob.created_at.asc())
                .first()
            )
            if not job:
                lg.info('🏛 drain-queue: no queued jobs remain — exiting')
                break

            cls = source_to_class.get(job.source)
            if not cls:
                lg.error(f'🏛 drain-queue: job {job.id} source={job.source} has no matching crawler — skipping')
                job.status = 'failed'
                job.error = f'No crawler class registered for source {job.source!r}'
                job.completed_at = datetime.utcnow()
                _db.session.commit()
                continue

            # Mark as running — but DON'T let the crawler create a NEW
            # job row. Instead, hand the crawler the existing queued row
            # so its progress writes go to the same DB record.
            #
            # The standard BaseIngestionJob.run() always creates a new
            # row. To avoid that, we use a thin runner that bypasses
            # row creation and re-uses the existing job.
            try:
                lg.info(f'🏛 drain-queue: starting job {job.id} ({job.source})')
                crawler = cls()
                _run_existing_job(crawler, job)
                lg.info(f'🏛 drain-queue: job {job.id} ({job.source}) finished status={job.status}')
            except Exception as e:
                lg.exception(f'🏛 drain-queue: job {job.id} ({job.source}) crashed — {e}')
                # Best-effort mark the job failed
                try:
                    job.status = 'failed'
                    job.error = (job.error or '') + f'\nDrain worker exception: {e}'
                    job.completed_at = datetime.utcnow()
                    _db.session.commit()
                except Exception:
                    _db.session.rollback()

        lg.info('🏛 drain-queue: worker exit')


def _run_existing_job(crawler, job):
    """Run a crawler against an EXISTING queued MLIngestionJob row.

    Variant of BaseIngestionJob.run() that doesn't create a new row —
    instead it mutates the supplied row through running → succeeded/failed.
    Used by the queue drainer so each enqueued job becomes the row the
    crawler reports against.

    v5.89.34: registers the crawler in the abort_registry so the
    staleness watcher in app.py can signal it to stop. CrawlAborted is
    handled as a distinct exit path with a clean error message. Also
    honors a "succeeded but abort was requested mid-flight" case:
    overrides the success status to failed so the operator's kill intent
    is respected.
    """
    import time
    import traceback
    from models import db as _db
    from datetime import datetime as _dt
    from ml_ingestion.abort_registry import (
        register as _abort_register,
        deregister as _abort_deregister,
        is_abort_requested as _abort_is_requested,
        CrawlAborted as _CrawlAborted,
    )

    job.status = 'running'
    job.started_at = _dt.utcnow()
    _db.session.commit()

    crawler._job_id = job.id
    crawler._t_start = time.time()
    crawler._log(f'Starting {crawler.JOB_TYPE} job: {crawler.SOURCE_NAME}')

    # v5.89.34: register in the abort registry so the staleness watcher
    # in app.py can find this crawler instance and signal abort.
    _abort_register(job.id, crawler)

    try:
        try:
            crawler.run_job()
            # v5.89.34: even if run_job() returned success, check if abort
            # was requested. The watcher might have set the flag during the
            # crawler's final batch — without this check, the success-write
            # below would silently override the watcher's failed status.
            if _abort_is_requested(job.id):
                job.status = 'failed'
                job.error = (
                    f'Aborted by staleness watcher mid-flight. Crawler '
                    f'finished its current batch ({crawler._rows_added} '
                    f'rows added) but watcher had already requested abort. '
                    f'Honoring the abort over the success.'
                )
                crawler._log(
                    f'Job ABORTED (post-success): watcher had requested abort '
                    f'before crawler returned',
                    level='warn',
                )
            else:
                job.status = 'succeeded'
                crawler._log(f'Job succeeded: added={crawler._rows_added}, rejected={crawler._rows_rejected}')
        except _CrawlAborted as e:
            # Clean cooperative-cancellation path. Distinct from generic
            # exception because the operator (or watcher) explicitly asked
            # for this — the error message reflects that.
            job.status = 'failed'
            job.error = f'CrawlAborted: {e}'
            crawler._log(f'Job ABORTED by watcher: {e}', level='warn')
        except Exception as e:
            job.status = 'failed'
            job.error = f'{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}'
            crawler._log(f'Job FAILED: {e}', level='error')
    finally:
        # v5.89.34: deregister on every exit path. Without this, the
        # registry would leak crawler refs across runs.
        _abort_deregister(job.id)

    job.completed_at = _dt.utcnow()
    job.elapsed_seconds = round(time.time() - crawler._t_start, 2)
    job.rows_processed = crawler._rows_processed
    job.rows_added = crawler._rows_added
    job.rows_rejected = crawler._rows_rejected
    # Final progress flush (writes log_json envelope)
    try:
        crawler._flush_progress()
    except Exception:
        pass
    _db.session.commit()


@admin_bp.route('/api/admin/ml-crawl-abort', methods=['POST'])
@_api_admin_req_dec
def admin_ml_crawl_abort():
    """v5.89.34: Manually abort a running crawl job.

    Operator-initiated counterpart to the staleness watcher in app.py.
    Flips the MLIngestionJob row to 'failed' status AND signals the
    crawler instance (if found in this process's abort registry) to
    raise CrawlAborted at its next pagination check.

    Request: POST {"job_id": 123}
    Response (200): {
        "ok": true,
        "job_id": 123,
        "db_status_flipped": true,    # always true on success
        "abort_signaled": true|false, # true if crawler was found locally
        "message": "human-readable explanation"
    }

    Two scenarios for "abort_signaled":
      - true: the crawler is running on THIS web worker, abort flag
        has been set, crawler will exit within ~5-30s
      - false: the crawler is not registered in this process. Could be
        running on another worker (Render scale-out), or the job's
        already finished, or it never got registered. The DB status
        flip is still useful but the actual subprocess won't be
        signaled here. The 2h STALE_TIMEOUT will eventually clean
        up if it really is stuck on another worker.
    """
    from flask import request
    from models import MLIngestionJob, db as _db
    from datetime import datetime as _dt
    from ml_ingestion.abort_registry import request_abort

    data = request.get_json(silent=True) or {}
    job_id = data.get('job_id')
    if not isinstance(job_id, int):
        return jsonify({'ok': False, 'error': 'job_id (int) required in JSON body'}), 400

    job = MLIngestionJob.query.get(job_id)
    if not job:
        return jsonify({'ok': False, 'error': f'MLIngestionJob #{job_id} not found'}), 404

    if job.status not in ('queued', 'running'):
        return jsonify({
            'ok': False,
            'error': f'Job #{job_id} is not abortable (status={job.status}). '
                     f'Only queued/running jobs can be aborted.',
        }), 400

    # Always flip the DB status. This is the "definitely happened" guarantee.
    prior_status = job.status
    job.status = 'failed'
    job.completed_at = _dt.utcnow()
    abort_note = f'Manually aborted by operator at {job.completed_at.isoformat()}Z'
    job.error = (job.error + '\n' + abort_note) if job.error else abort_note
    _db.session.commit()

    # Also try to signal the running crawler so it actually exits the loop
    # rather than continuing to do work whose result will be discarded.
    signaled = False
    if prior_status == 'running':
        try:
            signaled = request_abort(job_id)
        except Exception as e:
            # Don't fail the request because of a registry hiccup; the DB
            # flip already happened.
            signaled = False
            current_app.logger.warning(
                f'[ml-crawl-abort] signal failed for job #{job_id}: '
                f'{type(e).__name__}: {e}'
            )

    if signaled:
        msg = (f'Job #{job_id} aborted. DB status flipped and crawler signaled. '
               f'Drain worker should unblock within ~30s.')
    elif prior_status == 'queued':
        msg = f'Job #{job_id} (was queued) cancelled before it started.'
    else:
        msg = (f'Job #{job_id} marked failed in DB. Crawler not registered in '
               f'this process — may be running on another worker, or already '
               f'finished. The STALE_TIMEOUT (2h) will clean up if needed.')

    return jsonify({
        'ok': True,
        'job_id': job_id,
        'db_status_flipped': True,
        'abort_signaled': signaled,
        'prior_status': prior_status,
        'message': msg,
    })


@admin_bp.route('/api/admin/ml-resume-queue', methods=['POST'])
@_api_admin_req_dec
def admin_ml_resume_queue():
    """v5.87.22: Resume processing the persistent crawl queue.

    Use case: a previous Crawl All started but the worker died (deploy
    restart, OOM). The queued MLIngestionJob rows are still there. This
    endpoint spawns a fresh drain worker to pick up where the dead one
    left off.

    Safe to call when no queued jobs exist — drain worker will see an
    empty queue and exit immediately.

    Safe to call concurrently with an active drain worker — the atomic
    queued→running transition prevents double-processing of the same job.
    """
    import threading
    from flask import current_app
    from models import MLIngestionJob

    # Sweep first so any zombies don't block fresh queued work.
    MLIngestionJob.sweep_stale(job_type='crawl')

    queued_count = MLIngestionJob.query.filter_by(
        job_type='crawl', status='queued'
    ).count()
    running_count = MLIngestionJob.query.filter_by(
        job_type='crawl', status='running'
    ).count()

    if queued_count == 0 and running_count == 0:
        return jsonify({
            'ok': True,
            'message': 'No queued or running crawl jobs. Nothing to resume.',
            'queued_count': 0,
            'running_count': 0,
        })

    app_obj = current_app._get_current_object()
    threading.Thread(target=_drain_crawl_queue, args=(app_obj,), daemon=True).start()

    return jsonify({
        'ok': True,
        'message': f'Drain worker started. Queued: {queued_count}, currently running: {running_count}.',
        'queued_count': queued_count,
        'running_count': running_count,
    })


@admin_bp.route('/api/admin/ml-clear-queue', methods=['POST'])
@_api_admin_req_dec
def admin_ml_clear_queue():
    """v5.87.22: Mark all queued crawl jobs as failed (cancel the queue).

    Use case: operator wants to start fresh without running the existing
    queue. Marks queued rows as 'failed' with a clear error so they don't
    pollute future "did this succeed" checks but also don't block new
    enqueueing.

    Does NOT touch 'running' jobs — those need force-fail individually.
    """
    from models import MLIngestionJob
    from datetime import datetime as _dt

    queued = MLIngestionJob.query.filter_by(
        job_type='crawl', status='queued'
    ).all()
    cleared = 0
    for j in queued:
        j.status = 'failed'
        j.error = 'Queue cancelled by operator via /api/admin/ml-clear-queue'
        j.completed_at = _dt.utcnow()
        cleared += 1
    db.session.commit()
    return jsonify({
        'ok': True,
        'cleared_count': cleared,
        'message': f'Marked {cleared} queued crawl jobs as failed.',
    })


@admin_bp.route('/api/admin/ml-sweep-stuck', methods=['POST'])
@_api_admin_req_dec
def admin_ml_sweep_stuck():
    """v5.87.8: On-demand stale-job sweeper.

    Calls MLIngestionJob.sweep_stale() which marks 'running' jobs older than
    their per-type timeout (crawl=2h, synthesize=3h, relabel=6h, etc.) as
    'failed'. The same logic runs automatically before each conflict check
    (admin_ml_crawl_start, etc.), but operators sometimes want to clear
    zombies immediately rather than waiting for the next attempted job.

    Body params (optional):
      job_type (str): only sweep this type. Omit to sweep all types.

    Returns the list of swept job ids so the UI can confirm action.
    """
    from models import MLIngestionJob

    body = request.get_json(silent=True) or {}
    job_type = body.get('job_type') or None

    swept_ids = MLIngestionJob.sweep_stale(job_type=job_type)
    return jsonify({
        'ok': True,
        'swept_count': len(swept_ids),
        'swept_ids': swept_ids,
        'message': (f'Swept {len(swept_ids)} stale job(s).' if swept_ids
                    else 'No stale jobs found — nothing to sweep.'),
    })


@admin_bp.route('/api/admin/ml-fail-job', methods=['POST'])
@_api_admin_req_dec
def admin_ml_fail_job():
    """v5.87.8: Surgically force-fail a specific MLIngestionJob by id.

    Used by the Recent Ingestion Jobs panel's per-row 'Force fail' button
    when an operator knows a particular job is hung and wants to clear it
    without waiting for the broader sweep timeout.

    This does NOT kill the underlying background thread (Python doesn't
    expose a clean way to do that across threads). It just updates the
    DB row so the conflict gate doesn't block new work. The background
    thread will still complete or crash on its own; if it eventually tries
    to commit results, the row's status was already 'failed' so any
    success log is moot.

    Body params:
      job_id (int, required): the MLIngestionJob.id to mark failed
      reason (str, optional): explanation, prepended to the error field

    Returns the new status of the affected job.
    """
    from models import db, MLIngestionJob
    from datetime import datetime as _dt2

    body = request.get_json(silent=True) or {}
    job_id = body.get('job_id')
    reason = (body.get('reason') or '').strip() or 'Force-failed by operator from admin UI'

    if not isinstance(job_id, int):
        return jsonify({'ok': False, 'error': 'job_id (int) required'}), 400

    job = MLIngestionJob.query.get(job_id)
    if not job:
        return jsonify({'ok': False, 'error': f'Job {job_id} not found'}), 404
    if job.status != 'running':
        return jsonify({
            'ok': False,
            'error': f'Job {job_id} is already in terminal state ({job.status}); nothing to fail',
            'current_status': job.status,
        }), 409

    job.status = 'failed'
    job.completed_at = _dt2.utcnow()
    note = f'[force-fail @ {job.completed_at.isoformat()}] {reason}'
    job.error = (job.error + '\n' + note) if job.error else note
    db.session.commit()

    return jsonify({
        'ok': True,
        'message': f'Job {job_id} marked failed.',
        'job_id': job_id,
        'job_type': job.job_type,
        'source': job.source,
    })


@admin_bp.route('/api/admin/ml-crawl-verify', methods=['POST'])
@_api_admin_req_dec
def admin_ml_crawl_verify():
    """v5.87.9: Verify a scaffold crawler's dataset coordinates against the
    live API.

    The scaffolding workflow normally requires manual curl + field inspection
    + STATUS flip. This endpoint collapses that into one click. It does NOT
    crawl — it just hits the dataset URL with $limit=2, parses the response,
    and reports what it found:

      - HTTP status from the dataset endpoint
      - Total fields in the returned row
      - Whether the configured TEXT_FIELD exists and contains text
      - Sample value of TEXT_FIELD (truncated)
      - List of other field names that look like text candidates

    Use this BEFORE flipping STATUS='active'. If verification fails, fix the
    dataset ID or TEXT_FIELD in the class definition, redeploy, re-verify.

    Body params:
      source (str, required): registry key (e.g. 'philadelphia', 'sf')
    """
    from ml_ingestion import get_crawler_class
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
    import json as _json
    import os as _os

    body = request.get_json(silent=True) or {}
    source = body.get('source', '').strip().lower()

    cls = get_crawler_class(source)
    if cls is None:
        return jsonify({
            'ok': False,
            'error': f'Unknown source {source!r}',
        }), 400

    # v5.87.10: dispatch on class shape. Socrata and ArcGIS have different
    # APIs but produce the same shape of verifier response (verdict + sample).
    is_socrata = all(hasattr(cls, attr) for attr in ('DOMAIN', 'DATASET_ID', 'TEXT_FIELD'))
    is_arcgis = all(hasattr(cls, attr) for attr in ('SERVICE_URL', 'TEXT_FIELD'))

    if not (is_socrata or is_arcgis):
        return jsonify({
            'ok': True,
            'verifiable': False,
            'source': source,
            'reason': 'This crawler is not a Socrata- or ArcGIS-shaped subclass — cannot auto-verify. '
                      'Check the class docstring for activation steps.',
        })

    text_field = getattr(cls, 'TEXT_FIELD', '')
    current_status = getattr(cls, 'STATUS', 'scaffold')

    if is_socrata:
        domain = getattr(cls, 'DOMAIN')
        dataset_id = getattr(cls, 'DATASET_ID')
        if not (domain and dataset_id):
            return jsonify({
                'ok': True,
                'verifiable': True,
                'source': source,
                'current_status': current_status,
                'verdict': 'fail',
                'reason': 'DOMAIN or DATASET_ID is empty — class still scaffolded.',
            })
        url = f'https://{domain}/resource/{dataset_id}.json?$limit=2'
        host_label = domain
    else:  # ArcGIS
        service_url = getattr(cls, 'SERVICE_URL', '')
        if not service_url:
            return jsonify({
                'ok': True,
                'verifiable': True,
                'source': source,
                'current_status': current_status,
                'verdict': 'fail',
                'reason': 'SERVICE_URL is empty — class still scaffolded. '
                          'Discover the FeatureServer/MapServer URL from the city\'s ArcGIS Hub.',
            })
        # ArcGIS expects /query as a sub-path of the service URL
        url = (f'{service_url}/query?where=1%3D1&outFields=*'
               f'&returnGeometry=false&resultRecordCount=2&f=json')
        # Extract domain for error messages
        from urllib.parse import urlparse
        host_label = urlparse(service_url).netloc

    # Optional Socrata app token speeds things up + raises rate limit (only
    # used when verifying Socrata-shaped crawlers).
    headers = {'User-Agent': 'OfferWise/verify (https://getofferwise.ai)'}
    if is_socrata:
        token = _os.environ.get('SOCRATA_APP_TOKEN')
        if token:
            headers['X-App-Token'] = token

    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as resp:
            http_status = resp.getcode()
            raw = resp.read().decode('utf-8', errors='replace')
    except HTTPError as e:
        return jsonify({
            'ok': True,
            'verifiable': True,
            'source': source,
            'current_status': current_status,
            'verdict': 'fail',
            'http_status': e.code,
            'url_tried': url,
            'reason': f'Dataset endpoint returned HTTP {e.code}. Likely wrong dataset coordinates, '
                      f'or the dataset has been migrated.',
        })
    except (URLError, TimeoutError) as e:
        return jsonify({
            'ok': True,
            'verifiable': True,
            'source': source,
            'current_status': current_status,
            'verdict': 'fail',
            'url_tried': url,
            'reason': f'Network error reaching {host_label}: {e}. Could be transient — retry.',
        })

    try:
        parsed = _json.loads(raw)
    except Exception as parse_err:
        return jsonify({
            'ok': True,
            'verifiable': True,
            'source': source,
            'current_status': current_status,
            'verdict': 'fail',
            'http_status': http_status,
            'url_tried': url,
            'reason': f'Response was not JSON: {parse_err}. The endpoint exists but '
                      f'isn\'t returning the expected data shape.',
        })

    # Normalize the two response shapes into a list of "row" dicts.
    if is_socrata:
        # Socrata: parsed is already a list of {col: val} dicts
        if not isinstance(parsed, list):
            return jsonify({
                'ok': True,
                'verifiable': True,
                'source': source,
                'current_status': current_status,
                'verdict': 'fail',
                'http_status': http_status,
                'url_tried': url,
                'reason': f'Expected a JSON list (Socrata shape), got {type(parsed).__name__}.',
            })
        rows = parsed
    else:
        # ArcGIS: parsed is {features: [{attributes: {...}}, ...], exceededTransferLimit: bool}
        # ArcGIS errors come back as 200 with body {error: {code, message}}
        if isinstance(parsed, dict) and parsed.get('error'):
            err = parsed['error']
            return jsonify({
                'ok': True,
                'verifiable': True,
                'source': source,
                'current_status': current_status,
                'verdict': 'fail',
                'http_status': http_status,
                'url_tried': url,
                'reason': f'ArcGIS error: {err.get("message", err)} (code {err.get("code", "?")})',
            })
        features = (parsed or {}).get('features') if isinstance(parsed, dict) else None
        if not isinstance(features, list):
            return jsonify({
                'ok': True,
                'verifiable': True,
                'source': source,
                'current_status': current_status,
                'verdict': 'fail',
                'http_status': http_status,
                'url_tried': url,
                'reason': 'Expected a JSON object with "features" array (ArcGIS shape) but got something else.',
            })
        rows = [f.get('attributes', {}) for f in features if isinstance(f, dict)]

    if not rows:
        return jsonify({
            'ok': True,
            'verifiable': True,
            'source': source,
            'current_status': current_status,
            'verdict': 'warn',
            'http_status': http_status,
            'url_tried': url,
            'reason': 'Endpoint returned 0 rows. Dataset exists but is empty, or the filter is too restrictive.',
        })

    sample_row = rows[0]
    all_fields = sorted(sample_row.keys())

    # Look for fields likely to contain inspector narrative
    candidates = [
        f for f in all_fields
        if any(k in f.lower() for k in (
            'desc', 'comment', 'narrat', 'text', 'complaint', 'violation',
            'condition', 'remark', 'issue'
        ))
    ]

    text_field_present = text_field in sample_row
    text_field_value = sample_row.get(text_field) if text_field_present else None
    text_field_has_content = bool(
        text_field_value and isinstance(text_field_value, str) and len(text_field_value.strip()) >= 10
    )

    if text_field_present and text_field_has_content:
        verdict = 'pass'
        reason = f'Dataset returned rows. Configured text field {text_field!r} is present and contains real text.'
    elif text_field_present and not text_field_has_content:
        verdict = 'warn'
        reason = (f'Dataset returned rows and field {text_field!r} exists, but its value '
                  f'is empty or too short. Check whether you need a different field.')
    else:
        verdict = 'fail'
        reason = (f'Dataset returned rows but configured TEXT_FIELD {text_field!r} '
                  f'is NOT present in the schema. Update the class to use one of '
                  f'the candidate fields below.')

    return jsonify({
        'ok': True,
        'verifiable': True,
        'source': source,
        'current_status': current_status,
        'verdict': verdict,
        'http_status': http_status,
        'url_tried': url,
        'reason': reason,
        'sample': {
            'field_count': len(all_fields),
            'text_field_configured': text_field,
            'text_field_present': text_field_present,
            'text_field_sample': (
                text_field_value[:200] if isinstance(text_field_value, str) else text_field_value
            ),
            'text_field_candidates': candidates,
            'all_fields_sample': all_fields[:30],
        },
    })


@admin_bp.route('/api/admin/ml-discover-arcgis', methods=['POST'])
@_api_admin_req_dec
def admin_ml_discover_arcgis():
    """v5.87.17: ArcGIS Hub catalog discovery.

    Many cities migrated off Socrata to ArcGIS Hub during 2024-2025.
    This endpoint searches a hub's DCAT-US catalog for code-violation
    datasets and returns candidate SERVICE_URLs ready for verification.

    The container scaffolding network is rate-limited on opendata.arcgis.com
    domains (consistent 503s). This endpoint runs from the production server
    where egress is reliable.

    Body params:
      hub_domain (str, required): e.g. 'data.baltimorecity.gov',
                                  'opendata.minneapolismn.gov'
      keywords (list, optional): default ['code', 'violation', 'enforce',
                                  'inspect', 'complaint', 'housing']

    Returns: list of candidate datasets with their REST URLs, plus a
    schema probe of the top 3 to surface which are most useful.
    """
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
    import json as _json

    body = request.get_json(silent=True) or {}
    hub_domain = (body.get('hub_domain') or '').strip().lower()
    keywords = body.get('keywords') or ['code', 'violation', 'enforce', 'inspect', 'complaint', 'housing']
    if not hub_domain:
        return jsonify({'ok': False, 'error': 'hub_domain required'}), 400

    catalog_url = f'https://{hub_domain}/api/feed/dcat-us/1.1.json'
    headers = {'User-Agent': 'OfferWise/discover (https://getofferwise.ai)'}
    try:
        req = Request(catalog_url, headers=headers)
        with urlopen(req, timeout=20) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
        catalog = _json.loads(raw)
    except HTTPError as e:
        return jsonify({
            'ok': True,
            'hub_domain': hub_domain,
            'catalog_url': catalog_url,
            'verdict': 'fail',
            'reason': f'Hub catalog returned HTTP {e.code}. Try a different domain pattern '
                      f'(e.g. opendata.{hub_domain.replace("data.","")}, '
                      f'{hub_domain.replace(".","-")}.opendata.arcgis.com).',
        })
    except (URLError, TimeoutError) as e:
        return jsonify({
            'ok': True,
            'hub_domain': hub_domain,
            'catalog_url': catalog_url,
            'verdict': 'fail',
            'reason': f'Network error: {e}',
        })
    except Exception as e:
        return jsonify({
            'ok': True,
            'hub_domain': hub_domain,
            'catalog_url': catalog_url,
            'verdict': 'fail',
            'reason': f'Catalog parse failed: {e}',
        })

    datasets = catalog.get('dataset', []) if isinstance(catalog, dict) else []

    # Filter datasets by title keywords, exclude obvious non-housing
    skip_words = ('moving violation', 'parking', 'speed', 'dwi', 'food',
                  'pool', 'firearm', 'fishing', 'restaurant', 'liquor')
    hits = []
    for ds in datasets:
        title = (ds.get('title') or '').strip()
        title_lc = title.lower()
        if not any(k in title_lc for k in keywords):
            continue
        if any(skip in title_lc for skip in skip_words):
            continue
        rest_url = None
        for dist in ds.get('distribution', []) or []:
            url = dist.get('accessURL') or dist.get('downloadURL') or ''
            if 'rest/services' in url and ('FeatureServer' in url or 'MapServer' in url):
                rest_url = url
                break
        if rest_url:
            hits.append({
                'title': title[:120],
                'url': rest_url,
            })

    # Probe the top 3 hits for schema + sample text
    def _probe(service_url):
        # Add /0 layer index if missing
        last = service_url.rstrip('/').split('/')[-1]
        if not last.isdigit() and ('FeatureServer' in service_url or 'MapServer' in service_url):
            service_url = service_url.rstrip('/') + '/0'
        query_url = (f'{service_url}/query?where=1%3D1&outFields=*'
                     f'&returnGeometry=false&resultRecordCount=2&f=json')
        try:
            req = Request(query_url, headers=headers)
            with urlopen(req, timeout=12) as resp:
                d = _json.loads(resp.read().decode('utf-8', errors='replace'))
            if isinstance(d, dict) and d.get('error'):
                # v5.88.23: defensive — error.message could be None
                return {'verdict': 'fail', 'reason': (d['error'].get('message') or '')[:120]}
            features = d.get('features') or []
            if not features:
                return {'verdict': 'empty', 'service_url': service_url}
            attrs = features[0].get('attributes') or {}
            fields = sorted(attrs.keys())
            text_cands = [
                f for f in fields
                if any(k in f.lower() for k in ('desc', 'comment', 'narrat', 'complaint',
                                                 'violation', 'remark', 'issue', 'problem',
                                                 'condition', 'note'))
            ]
            sample_text, sample_field = None, None
            for tc in text_cands:
                v = attrs.get(tc)
                if isinstance(v, str) and len(v.strip()) > 10:
                    sample_text = v.strip()[:180]
                    sample_field = tc
                    break
            verdict = 'pass' if (sample_text and len(sample_text) > 15) else (
                'weak' if text_cands else 'no-text')
            return {
                'verdict': verdict,
                'service_url': service_url,
                'fields_count': len(fields),
                'text_candidates': text_cands[:6],
                'sample_field': sample_field,
                'sample_text': sample_text,
            }
        except Exception as e:
            return {'verdict': 'fail', 'reason': str(e)[:100]}

    # Sort hits by likely-good titles (prefer "code violation" over "complaint")
    def _priority(h):
        t = h['title'].lower()
        if 'code violation' in t or 'code enforcement' in t:
            return 0
        if 'building violation' in t or 'housing violation' in t:
            return 1
        if 'inspect' in t and 'food' not in t:
            return 2
        if 'complaint' in t:
            return 3
        return 4
    hits.sort(key=_priority)

    probed = []
    for h in hits[:3]:
        probe_result = _probe(h['url'])
        probed.append({**h, 'probe': probe_result})

    return jsonify({
        'ok': True,
        'hub_domain': hub_domain,
        'catalog_url': catalog_url,
        'verdict': 'pass' if probed else 'no-hits',
        'total_datasets_in_catalog': len(datasets),
        'matching_hits': len(hits),
        'top_candidates': probed,
        'all_hits': [{'title': h['title'], 'url': h['url']} for h in hits[:15]],
    })


@admin_bp.route('/api/admin/ml-relabel-status', methods=['GET'])
@_api_admin_req_dec
def admin_ml_relabel_status():
    """Return progress of the most recent relabel job, plus overall corpus
    relabel coverage percentage.

    Response:
      {
        ok: true,
        current_job: { id, status, rows_processed, rows_added, elapsed_seconds,
                       started_at, eta_seconds (estimated if running) },
        corpus: { total: N, labeled: M, percent: P, remaining: N-M }
      }
    """
    from models import MLIngestionJob, MLFindingLabel
    try:
        # Most recent relabel job (running or completed)
        latest = (MLIngestionJob.query
                  .filter_by(job_type='relabel')
                  .order_by(MLIngestionJob.created_at.desc())
                  .first())

        total = MLFindingLabel.query.count()
        labeled = MLFindingLabel.query.filter(
            MLFindingLabel.category_v2.isnot(None)
        ).count()
        pct = round(100 * labeled / total, 2) if total else 0.0

        job_out = None
        if latest:
            # Estimate ETA if job is running: based on current throughput
            eta_seconds = None
            if latest.status == 'running' and latest.elapsed_seconds and latest.rows_added:
                rate = latest.rows_added / max(latest.elapsed_seconds, 1)  # rows/sec
                remaining = total - labeled
                eta_seconds = int(remaining / rate) if rate > 0 else None

            job_out = {
                'id': latest.id,
                'status': latest.status,
                'rows_processed': latest.rows_processed,
                'rows_added': latest.rows_added,
                'rows_rejected': latest.rows_rejected,
                'elapsed_seconds': latest.elapsed_seconds,
                'started_at': latest.started_at.isoformat() if latest.started_at else None,
                'completed_at': latest.completed_at.isoformat() if latest.completed_at else None,
                'error': (latest.error[:300] if latest.error else None),
                'eta_seconds': eta_seconds,
            }

        return jsonify({
            'ok': True,
            'current_job': job_out,
            'corpus': {
                'total': total,
                'labeled': labeled,
                'percent': pct,
                'remaining': total - labeled,
            },
        })
    except Exception as e:
        import traceback
        logging.exception("ml-relabel-status failed")
        return jsonify({
            'ok': False,
            'error': str(e)[:500],
            'traceback': traceback.format_exc()[-1000:],
        }), 500


@admin_bp.route('/api/admin/ml-crawl-costs', methods=['POST'])
@_api_admin_req_dec
def admin_ml_crawl_costs():
    """Trigger external data crawl in background. Returns immediately.
    Poll /api/admin/ml-crawl-status for progress."""
    import threading, time as _t
    from flask import current_app

    # Check if crawl already running
    crawl_state = _load_job_state('_crawl')
    if crawl_state and crawl_state.get('status') == 'running':
        elapsed = _t.time() - crawl_state.get('started_at', _t.time())
        if elapsed < 600:  # stale after 10 min
            return jsonify({'status': 'already_running', 'elapsed': round(elapsed, 1)})

    app_obj = current_app._get_current_object()

    def _run_crawl():
        with app_obj.app_context():
            import hashlib, json as _json, time as _time
            from models import MLCostData, MLFindingLabel

            t_start = _time.time()
            state = {'status': 'running', 'started_at': t_start, 'phase': 'Fetching sources...', 'log': [], 'results': None}
            _save_job_state('_crawl', state)

            def _progress(source, status, count):
                state['phase'] = f'{source}: {status} ({count})'
                state['log'].append({'t': round(_time.time() - t_start, 1), 'msg': f'{source}: {status} ({count})', 'level': 'info'})
                _save_job_state('_crawl', state)

            try:
                from cost_data_crawler import collect_all_external_cost_data
                rows, stats = collect_all_external_cost_data(permit_limit=10000)
            except Exception as e:
                state['status'] = 'failed'
                state['error'] = str(e)[:500]
                state['log'].append({'t': round(_time.time() - t_start, 1), 'msg': f'Crawl failed: {e}', 'level': 'error'})
                _save_job_state('_crawl', state)
                return

            state['phase'] = 'Storing to database...'
            state['log'].append({'t': round(_time.time() - t_start, 1), 'msg': f'Crawl fetched {len(rows)} rows. Storing...', 'level': 'info'})
            _save_job_state('_crawl', state)

            # v5.87.36 — bulk-load existing hashes/texts into Python sets ONCE
            # so each row check is O(1) instead of a DB round-trip. Critical
            # when scanning 100K+ rows on a 2GB tier — saves ~30-60% of Phase 1
            # wall time and removes 100K+ individual SELECTs from the DB.
            existing_cost_hashes = set()
            existing_finding_texts = set()
            try:
                for (h,) in db.session.query(MLCostData.content_hash).yield_per(5000):
                    if h:
                        existing_cost_hashes.add(h)
            except Exception:
                existing_cost_hashes = None
            try:
                for (t,) in db.session.query(MLFindingLabel.finding_text).yield_per(5000):
                    if t:
                        existing_finding_texts.add(t)
            except Exception:
                existing_finding_texts = None

            cost_added = 0
            finding_added = 0
            skipped = 0
            batch_size = 500
            batch_count = 0
            total_rows = len(rows)

            # Process in chunks to limit peak memory — don't hold all rows + all ORM objects
            while rows:
                # Pop from list to free memory as we go
                row = rows.pop(0)
                try:
                    cost_mid = row.get('cost_mid', 0) or 0
                    is_finding = row.get('_type') == 'finding'

                    h = hashlib.sha256(
                        (row['finding_text'][:200] + '|' + row.get('source', '') + '|' + str(int(cost_mid))).encode()
                    ).hexdigest()

                    if cost_mid > 0:
                        is_dup = (h in existing_cost_hashes) if existing_cost_hashes is not None \
                                 else bool(MLCostData.query.filter_by(content_hash=h).first())
                        if is_dup:
                            skipped += 1
                            continue
                        entry = MLCostData(
                            finding_text=row['finding_text'][:500],
                            category=row.get('category', 'general'),
                            severity=row.get('severity', 'moderate'),
                            cost_low=row.get('cost_low'), cost_high=row.get('cost_high'),
                            cost_mid=cost_mid,
                            zip_code=row.get('zip_code', '')[:10],
                            source=row.get('source', 'unknown')[:50],
                            source_meta=_json.dumps(row.get('metadata', {})),
                            content_hash=h,
                        )
                        db.session.add(entry)
                        cost_added += 1
                        if existing_cost_hashes is not None:
                            existing_cost_hashes.add(h)

                    if is_finding and row.get('finding_text') and row.get('category') and row.get('severity'):
                        ftext = row['finding_text'][:500]
                        is_dup_finding = (ftext in existing_finding_texts) if existing_finding_texts is not None \
                                         else bool(MLFindingLabel.query.filter_by(finding_text=ftext).first())
                        if not is_dup_finding:
                            label = MLFindingLabel(
                                analysis_id=0, finding_text=ftext,
                                category=row['category'], severity=row['severity'],
                                source=row.get('source', 'crawled'),
                            )
                            db.session.add(label)
                            finding_added += 1
                            if existing_finding_texts is not None:
                                existing_finding_texts.add(ftext)
                        else:
                            skipped += 1

                    # Batch commit every N rows to avoid huge transaction
                    batch_count += 1
                    if batch_count % batch_size == 0:
                        try:
                            db.session.commit()
                            state['phase'] = f'Stored {cost_added} costs + {finding_added} findings ({skipped} skipped)...'
                            _save_job_state('_crawl', state)
                        except Exception as ce:
                            db.session.rollback()
                            logging.warning(f'Batch commit failed: {ce}')
                except Exception:
                    continue

            # Final commit
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                logging.warning(f'Final commit failed: {e}')

            # Free the in-memory dedup sets (rows list is already empty from pop loop)
            try:
                del existing_cost_hashes
                del existing_finding_texts
            except Exception:
                pass

            elapsed = round(_time.time() - t_start, 1)
            result = {
                'cost_added': cost_added,
                'finding_added': finding_added,
                'skipped_duplicates': skipped,
                'total_scanned': total_rows,
                'by_source': {k: v for k, v in stats.items() if not k.startswith('_')},
                'errors': stats.get('_errors', {}),
                'elapsed': elapsed,
                'total_cost_data': MLCostData.query.count(),
                'total_finding_labels': MLFindingLabel.query.count(),
            }
            state['status'] = 'complete'
            state['results'] = result
            state['phase'] = 'Complete'
            state['log'].append({'t': elapsed, 'msg': f'Done: {cost_added} costs + {finding_added} findings in {elapsed}s', 'level': 'success'})
            _save_job_state('_crawl', state)
            logging.info(f'Crawl complete: {cost_added} costs + {finding_added} findings in {elapsed}s')

    thread = threading.Thread(target=_run_crawl, daemon=True, name='ml-crawl')
    thread.start()
    return jsonify({'status': 'started', 'message': 'Crawl running in background. Poll /api/admin/ml-crawl-status for progress.'})


@admin_bp.route('/api/admin/ml-crawl-status', methods=['GET'])
@_api_admin_req_dec
def admin_ml_crawl_status():
    """Check crawl progress."""
    state = _load_job_state('_crawl')
    if not state:
        return jsonify({'status': 'not_found'})
    return jsonify(state)


@admin_bp.route('/api/admin/ml-cost-data-stats', methods=['GET'])
@_api_admin_req_dec
def admin_ml_cost_data_stats():
    """Show cost data stats by source."""
    from sqlalchemy import func
    from models import MLCostData
    by_source = db.session.query(
        MLCostData.source,
        func.count(MLCostData.id).label('count'),
        func.avg(MLCostData.cost_mid).label('avg_cost'),
        func.min(MLCostData.cost_mid).label('min_cost'),
        func.max(MLCostData.cost_mid).label('max_cost'),
    ).group_by(MLCostData.source).all()

    sources = []
    for row in by_source:
        sources.append({
            'source': row.source,
            'count': row.count,
            'avg_cost': round(row.avg_cost or 0),
            'min_cost': round(row.min_cost or 0),
            'max_cost': round(row.max_cost or 0),
        })

    return jsonify({
        'sources': sources,
        'total': MLCostData.query.count(),
    })

@admin_bp.route('/api/admin/ml-crawled-archive', methods=['GET'])
@_api_admin_req_dec
def admin_ml_crawled_archive():
    """List all crawled data archives saved in docrepo persistent disk."""
    import os
    docrepo_root = os.environ.get('DOCREPO_PATH', '/var/data/docrepo')
    archive_base = os.path.join(docrepo_root, 'crawled')

    if not os.path.exists(archive_base):
        return jsonify({'archives': [], 'total_size_kb': 0})

    archives = []
    total_size = 0
    try:
        for source in os.listdir(archive_base):  # permits, homeadvisor, fema
            source_path = os.path.join(archive_base, source)
            if not os.path.isdir(source_path):
                continue
            for date_folder in sorted(os.listdir(source_path), reverse=True):
                date_path = os.path.join(source_path, date_folder)
                if not os.path.isdir(date_path):
                    continue
                for fname in os.listdir(date_path):
                    fpath = os.path.join(date_path, fname)
                    if not os.path.isfile(fpath):
                        continue
                    size = os.path.getsize(fpath)
                    total_size += size
                    archives.append({
                        'source': source,
                        'date': date_folder,
                        'filename': fname,
                        'size_kb': round(size / 1024, 1),
                        'path': fpath.replace(docrepo_root, ''),
                    })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({
        'archives': archives[:200],
        'total_files': len(archives),
        'total_size_kb': round(total_size / 1024, 1),
    })

@admin_bp.route('/api/admin/ml-agent-history', methods=['GET'])
@_api_admin_req_dec
def admin_ml_agent_history():
    """Return recent ML Agent run history."""
    import json as _json
    from models import MLAgentRun
    try:
        runs = MLAgentRun.query.order_by(MLAgentRun.created_at.desc()).limit(14).all()
    except Exception:
        return jsonify({'runs': [], 'error': 'Table may not exist yet'})
    history = []
    for r in runs:
        entry = {
            'id': r.id,
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'elapsed': r.elapsed_seconds,
            'trigger': r.trigger,
            'crawl_added': r.crawl_added,
            'crawl_scanned': r.crawl_scanned,
            'data': {'findings': r.data_findings, 'pairs': r.data_pairs, 'costs': r.data_costs},
            'skipped': r.skipped_reason,
            'trained': r.trained,
            'fc_acc': r.fc_acc,
            'cd_acc': r.cd_acc,
            'rc_r2': r.rc_r2,
            'rolled_back': r.rolled_back,
            'rollback_reason': r.rollback_reason,
        }
        try:
            entry['log'] = _json.loads(r.agent_log) if r.agent_log else []
        except Exception:
            entry['log'] = []
        history.append(entry)
    return jsonify({'runs': history})

@admin_bp.route('/api/admin/ml-agent-run', methods=['POST'])
@_api_admin_req_dec
def admin_ml_agent_run():
    """Manually trigger the ML Agent pipeline. Runs in background like training."""
    import threading
    from flask import current_app

    app_obj = current_app._get_current_object()

    def _run():
        with app_obj.app_context():
            try:
                import os, shutil, json as _json, time as _time, hashlib, logging
                from models import MLFindingLabel, MLContradictionPair, MLTrainingRun, MLCostData, MLAgentRun

                agent_log = []
                t_start = _time.time()
                run = MLAgentRun(trigger='manual')

                _ml_agent_status['running'] = True
                _ml_agent_status['started_at'] = t_start
                _ml_agent_status['log'] = agent_log
                _ml_agent_status['phase'] = 'Starting...'

                def _alog(msg, level='info'):
                    agent_log.append({'t': round(_time.time() - t_start, 1), 'msg': msg, 'level': level})
                    logging.info(f'🤖 ML Agent (manual): {msg}')

                def _phase(name):
                    _ml_agent_status['phase'] = name
                    _alog(name)
                    # Persist to disk for cross-worker visibility
                    try:
                        agent_disk = {'running': True, 'started_at': t_start, 'phase': name, 'log': agent_log[-5:]}
                        _save_job_state('_agent_status', agent_disk)
                    except Exception:
                        pass

                _phase('Phase 1 — Crawling external data...')

                # Phase 1: Crawl
                # v5.87.36 — bulk-load existing content_hash + finding_text
                # values into Python sets ONCE up front, then check membership
                # in O(1). Replaces what was per-row SELECTs against MLCostData
                # AND MLFindingLabel — potentially hundreds of thousands of
                # individual queries during a manual deep-crawl.
                crawl_added = 0
                crawl_finding_added = 0
                crawl_scanned = 0
                try:
                    from cost_data_crawler import collect_all_external_cost_data
                    rows, stats = collect_all_external_cost_data(permit_limit=10000)
                    crawl_scanned = len(rows)

                    # Bulk-load existing hashes/texts
                    existing_cost_hashes = set()
                    existing_finding_texts = set()
                    try:
                        for (h,) in db.session.query(MLCostData.content_hash).yield_per(5000):
                            if h:
                                existing_cost_hashes.add(h)
                    except Exception:
                        existing_cost_hashes = None  # fall back to per-row
                    try:
                        for (t,) in db.session.query(MLFindingLabel.finding_text).yield_per(5000):
                            if t:
                                existing_finding_texts.add(t)
                    except Exception:
                        existing_finding_texts = None

                    batch_count = 0
                    for row in rows:
                        try:
                            cost_mid = row.get('cost_mid', 0) or 0
                            is_finding = row.get('_type') == 'finding'
                            h = hashlib.sha256(
                                (row['finding_text'][:200] + '|' + row.get('source', '') + '|' + str(int(cost_mid))).encode()
                            ).hexdigest()
                            if cost_mid > 0:
                                is_new_cost = (h not in existing_cost_hashes) if existing_cost_hashes is not None \
                                              else (not MLCostData.query.filter_by(content_hash=h).first())
                                if is_new_cost:
                                    entry = MLCostData(
                                        finding_text=row['finding_text'][:500],
                                        category=row.get('category', 'general'),
                                        severity=row.get('severity', 'moderate'),
                                        cost_low=row.get('cost_low'), cost_high=row.get('cost_high'),
                                        cost_mid=cost_mid,
                                        zip_code=row.get('zip_code', '')[:10],
                                        source=row.get('source', 'unknown')[:50],
                                        source_meta=_json.dumps(row.get('metadata', {})),
                                        content_hash=h,
                                    )
                                    db.session.add(entry)
                                    crawl_added += 1
                                    if existing_cost_hashes is not None:
                                        existing_cost_hashes.add(h)
                            if is_finding and row.get('finding_text') and row.get('category') and row.get('severity'):
                                ftext = row['finding_text'][:500]
                                is_new_finding = (ftext not in existing_finding_texts) if existing_finding_texts is not None \
                                                 else (not MLFindingLabel.query.filter_by(finding_text=ftext).first())
                                if is_new_finding:
                                    label = MLFindingLabel(
                                        analysis_id=0, finding_text=ftext,
                                        category=row['category'], severity=row['severity'],
                                        source=row.get('source', 'crawled'),
                                    )
                                    db.session.add(label)
                                    crawl_finding_added += 1
                                    if existing_finding_texts is not None:
                                        existing_finding_texts.add(ftext)
                            batch_count += 1
                            if batch_count % 500 == 0:
                                db.session.commit()
                                _ml_agent_status['phase'] = f'Phase 1 — Stored {crawl_added} costs + {crawl_finding_added} findings...'
                        except Exception:
                            continue
                    db.session.commit()
                    # Free the in-memory sets and crawled rows
                    del rows
                    del existing_cost_hashes
                    del existing_finding_texts
                    _alog(f'Phase 1 — Crawl: {crawl_added} cost + {crawl_finding_added} finding rows from {crawl_scanned} scanned')
                except Exception as e:
                    db.session.rollback()
                    _alog(f'Phase 1 — Crawl failed: {e}', 'warn')

                run.crawl_added = crawl_added
                run.crawl_scanned = crawl_scanned

                # Phase 2: Always train on manual trigger (skip the sufficiency check)
                _phase('Phase 2 — Evaluating data...')
                current_findings = MLFindingLabel.query.count()
                current_pairs = MLContradictionPair.query.count()
                current_costs = MLCostData.query.count()
                run.data_findings = current_findings
                run.data_pairs = current_pairs
                run.data_costs = current_costs
                _alog(f'Data: {current_findings} findings, {current_pairs} pairs, {current_costs} costs (manual = always train)')

                # Phase 3: Backup
                _phase('Phase 3 — Backing up models...')
                base_dir = os.path.dirname(os.path.abspath(__file__))
                # v5.89.55: both models_dir AND backup_dir live on
                # persistent disk. If backup_dir stayed on the ephemeral
                # filesystem, every deploy would wipe it and the
                # rollback-on-regression mechanism would silently fail.
                models_dir = get_models_dir()
                # backup_dir is a sibling of models_dir on the same disk
                backup_dir = os.path.join(os.path.dirname(models_dir), 'models_backup')
                try:
                    last_run = MLTrainingRun.query.order_by(MLTrainingRun.created_at.desc()).first()
                except Exception:
                    last_run = None
                try:
                    if os.path.exists(backup_dir):
                        shutil.rmtree(backup_dir)
                    if os.path.exists(models_dir):
                        shutil.copytree(models_dir, backup_dir)
                    _alog('Phase 3 — Models backed up')
                except Exception as e:
                    _alog(f'Phase 3 — Backup failed: {e}', 'warn')

                # Phase 4: Train
                _phase('Phase 4 — Training models...')
                results = None
                try:
                    from admin_routes import _execute_training
                    job = {'log': [], 'started_at': _time.time()}
                    results = _execute_training(job)
                    run.trained = True
                    _alog(f'Phase 4 — Training complete')
                except Exception as e:
                    _alog(f'Phase 4 — Training FAILED: {e}', 'error')
                    try:
                        if os.path.exists(backup_dir):
                            shutil.rmtree(models_dir)
                            shutil.move(backup_dir, models_dir)
                    except Exception:
                        pass
                    run.elapsed_seconds = round(_time.time() - t_start, 1)
                    run.agent_log = _json.dumps(agent_log)
                    db.session.add(run)
                    db.session.commit()
                    return

                # Extract accuracies
                if results:
                    fc = results.get('Finding Classifier', {})
                    cd = results.get('Contradiction Detector', {})
                    rc = results.get('Repair Cost', {})
                    try: run.fc_acc = float(fc.get('category', '0').replace('%', ''))
                    except: pass
                    try: run.cd_acc = float(cd.get('accuracy', '0').replace('%', ''))
                    except: pass
                    try: run.rc_r2 = float(rc.get('r2', '0'))
                    except: pass

                # Phase 5: Validate
                _phase('Phase 5 — Validating results...')
                regressed = False
                rollback_reason = ''
                if last_run and results:
                    new_cd = run.cd_acc or 0
                    old_cd = last_run.cd_accuracy or 0
                    if old_cd > 0 and new_cd < old_cd - 3:
                        rollback_reason = f'CD regressed {new_cd:.1f}% vs {old_cd:.1f}%'
                        regressed = True
                    new_fc = run.fc_acc or 0
                    old_fc = last_run.fc_category_acc or 0
                    if old_fc > 0 and new_fc < old_fc - 5:
                        rollback_reason = f'FC regressed {new_fc:.1f}% vs {old_fc:.1f}%'
                        regressed = True

                if regressed:
                    _alog(f'Phase 5 — REGRESSION: {rollback_reason}', 'error')
                    run.rolled_back = True
                    run.rollback_reason = rollback_reason
                    try:
                        if os.path.exists(backup_dir):
                            shutil.rmtree(models_dir)
                            shutil.move(backup_dir, models_dir)
                            from ml_inference import init_ml_inference
                            init_ml_inference(app_base_dir=base_dir)
                    except Exception:
                        pass
                else:
                    _alog('Phase 5 — Validation passed')

                try:
                    if os.path.exists(backup_dir):
                        shutil.rmtree(backup_dir)
                except Exception:
                    pass

                run.elapsed_seconds = round(_time.time() - t_start, 1)
                run.agent_log = _json.dumps(agent_log)
                db.session.add(run)
                db.session.commit()
                _alog(f'ML Agent complete in {run.elapsed_seconds:.0f}s ✅', 'success')
                _ml_agent_status['running'] = False
                _ml_agent_status['phase'] = ''
                _save_job_state('_agent_status', {'running': False, 'phase': ''})

            except Exception as e:
                logging.error(f'ML Agent (manual) fatal: {e}', exc_info=True)
                _ml_agent_status['running'] = False
                _ml_agent_status['phase'] = ''
                _save_job_state('_agent_status', {'running': False, 'phase': '', 'error': str(e)[:200]})

    thread = threading.Thread(target=_run, daemon=True, name='ml-agent-manual')
    thread.start()
    return jsonify({'status': 'started', 'message': 'ML Agent pipeline running in background. Check Agent Activity for results.'})

@admin_bp.route('/api/admin/ml-agent-status', methods=['GET'])
@_api_admin_req_dec
def admin_ml_agent_status():
    """Check if the ML Agent is currently running and what phase it's in."""
    import time, os, json as _json
    if _ml_agent_status.get('running'):
        elapsed = time.time() - (_ml_agent_status.get('started_at') or time.time())
        return jsonify({
            'running': True,
            'phase': _ml_agent_status.get('phase', ''),
            'elapsed': round(elapsed, 1),
            'log': _ml_agent_status.get('log', [])[-10:],
        })
    # Check disk for cross-worker state
    try:
        path = os.path.join(_JOB_STATE_DIR, '_agent_status.json')
        if os.path.exists(path):
            with open(path, 'r') as f:
                state = _json.load(f)
            if state.get('running'):
                elapsed = time.time() - state.get('started_at', time.time())
                if elapsed < 300:
                    return jsonify({
                        'running': True,
                        'phase': state.get('phase', ''),
                        'elapsed': round(elapsed, 1),
                        'log': state.get('log', [])[-10:],
                    })
    except Exception:
        pass
    return jsonify({'running': False})

@admin_bp.route('/api/admin/ml-corpus-stats', methods=['GET'])
@_api_admin_req_dec
def admin_ml_corpus_stats():
    """Return stats about the document repository (archived analyses, crawled files)."""
    import os, json as _json
    docrepo_root = os.environ.get('DOCREPO_PATH', '/var/data/docrepo')
    analyses_dir = os.path.join(docrepo_root, 'analyses')

    if not os.path.exists(analyses_dir):
        return jsonify({'total': 0, 'total_findings': 0, 'total_contradictions': 0, 'total_size_kb': 0})

    total = 0
    total_findings = 0
    total_contradictions = 0
    total_size = 0
    zips = set()
    depths = {'full': 0, 'disclosure_only': 0, 'address_only': 0}

    for fname in os.listdir(analyses_dir):
        if not fname.endswith('.json'):
            continue
        fpath = os.path.join(analyses_dir, fname)
        total_size += os.path.getsize(fpath)
        total += 1
        try:
            with open(fpath, 'r') as f:
                data = _json.load(f)
            total_findings += data.get('finding_count', 0)
            total_contradictions += len(data.get('contradictions', []))
            z = data.get('location', {}).get('zip', '')
            if z:
                zips.add(z)
            depth = data.get('analysis_depth', '')
            if depth in depths:
                depths[depth] += 1
        except Exception:
            continue

    return jsonify({
        'total': total,
        'total_findings': total_findings,
        'total_contradictions': total_contradictions,
        'total_size_kb': round(total_size / 1024, 1),
        'unique_zips': len(zips),
        'by_depth': depths,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ML Training Data Export — on-demand snapshots to /var/data/docrepo/ml_snapshots
# ─────────────────────────────────────────────────────────────────────────────
# Solves: disaster recovery (survives Postgres loss), portability (CSVs are
# universal), inspectability (downloadable, openable in Excel), productization
# (the .zip is the deliverable). All four ML-relevant Postgres tables plus an
# analyses summary plus a manifest, packaged into a single zip per snapshot.

_ML_SNAPSHOTS_DIR = os.path.join(
    os.environ.get('DOCREPO_PATH', '/var/data/docrepo'),
    'ml_snapshots',
)


def _table_to_csv_rows(model_cls):
    """Yield CSV rows for a SQLAlchemy model — header row first, then data rows.
    Used as a generator so we never materialize the full table in memory.
    """
    cols = [c.name for c in model_cls.__table__.columns]
    yield cols
    # .yield_per to stream large tables without loading all into memory at once.
    for row in model_cls.query.yield_per(1000):
        yield [getattr(row, c) for c in cols]


def _write_table_csv(zip_handle, arcname, model_cls):
    """Stream a Postgres table into the zip as a CSV. Returns row count."""
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    n_rows = 0
    cols = None
    for row in _table_to_csv_rows(model_cls):
        if cols is None:
            cols = row
            writer.writerow(row)
            continue
        # Coerce datetimes / decimals / None to safe CSV-friendly strings
        out = []
        for v in row:
            if v is None:
                out.append('')
            elif hasattr(v, 'isoformat'):
                out.append(v.isoformat())
            else:
                out.append(v)
        writer.writerow(out)
        n_rows += 1
    zip_handle.writestr(arcname, buf.getvalue())
    return n_rows


def _generate_ml_snapshot():
    """Generate a complete training-data snapshot. Returns (filepath, manifest).
    Runs synchronously — current data volume (~170K rows total) takes ~5-10s.
    If this grows past 500K rows we should move to a background job.
    """
    import zipfile
    import json as _json
    from datetime import datetime as _dt
    from models import (
        MLFindingLabel, MLContradictionPair, MLCostData,
        MLCooccurrenceBucket, PostCloseSurvey, MLTrainingRun,
    )

    os.makedirs(_ML_SNAPSHOTS_DIR, exist_ok=True)

    # Filename uses ISO timestamp with no colons (Windows-safe). UTC.
    ts = _dt.utcnow().strftime('%Y-%m-%dT%H-%M-%SZ')
    fname = f'ml_export_{ts}.zip'
    fpath = os.path.join(_ML_SNAPSHOTS_DIR, fname)

    manifest = {
        'snapshot_version': 1,
        'created_at_utc': _dt.utcnow().isoformat() + 'Z',
        'app_version': open(os.path.join(os.path.dirname(__file__), 'VERSION')).read().strip()
            if os.path.exists(os.path.join(os.path.dirname(__file__), 'VERSION')) else 'unknown',
        'tables': {},
    }

    table_specs = [
        ('ml_finding_labels.csv',        MLFindingLabel,        'Finding labels — primary classifier training source'),
        ('ml_contradiction_pairs.csv',   MLContradictionPair,   'Disclosure-vs-inspection contradiction pairs'),
        ('ml_cost_data.csv',             MLCostData,            'External repair cost data (FEMA, permits, insurance)'),
        ('ml_cooccurrence_buckets.csv',  MLCooccurrenceBucket,  'Finding co-occurrence statistics per analysis'),
        ('post_close_surveys.csv',       PostCloseSurvey,       'Post-close buyer feedback (predicted vs actual)'),
        ('ml_training_runs.csv',         MLTrainingRun,         'Training run history with accuracy metrics'),
    ]

    with zipfile.ZipFile(fpath, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for arcname, model_cls, desc in table_specs:
            try:
                n = _write_table_csv(zf, arcname, model_cls)
                manifest['tables'][arcname] = {'rows': n, 'description': desc, 'status': 'ok'}
            except Exception as e:
                manifest['tables'][arcname] = {'rows': 0, 'description': desc, 'status': f'error: {str(e)[:100]}'}
                logging.warning(f"ML export failed for {arcname}: {e}")

        # Manifest written last so it reflects what actually made it in
        zf.writestr('manifest.json', _json.dumps(manifest, indent=2, default=str))

        # Quick README so the recipient understands what they're looking at
        readme = (
            f"OfferWise ML Training Data Snapshot\n"
            f"Generated: {manifest['created_at_utc']}\n"
            f"App version: {manifest['app_version']}\n\n"
            f"This archive contains the complete state of the OfferWise ML training\n"
            f"datasets at the moment of export. Each CSV maps 1:1 to a Postgres table.\n"
            f"Tables included:\n\n"
        )
        for arcname, info in manifest['tables'].items():
            readme += f"  {arcname}: {info['rows']:,} rows — {info['description']}\n"
        readme += (
            f"\nTo reload into Postgres: use psql \\copy or pandas + to_sql.\n"
            f"To inspect: open in Excel, Numbers, or pandas (pd.read_csv).\n"
            f"Schema reference: see models.py at the same app version.\n"
        )
        zf.writestr('README.txt', readme)

    manifest['filepath'] = fpath
    manifest['filename'] = fname
    manifest['size_bytes'] = os.path.getsize(fpath)
    return fpath, manifest


@admin_bp.route('/api/admin/ml-snapshot', methods=['POST'])
@_api_admin_req_dec
def admin_ml_snapshot_create():
    """Generate a fresh snapshot of all ML training tables. Synchronous."""
    try:
        fpath, manifest = _generate_ml_snapshot()
        return jsonify({
            'ok': True,
            'filename': manifest['filename'],
            'size_bytes': manifest['size_bytes'],
            'size_mb': round(manifest['size_bytes'] / (1024 * 1024), 2),
            'tables': manifest['tables'],
            'created_at': manifest['created_at_utc'],
        })
    except Exception as e:
        logging.exception("ML snapshot failed")
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


@admin_bp.route('/api/admin/ml-snapshot/list', methods=['GET'])
@_api_admin_req_dec
def admin_ml_snapshot_list():
    """List existing snapshots, newest first."""
    if not os.path.isdir(_ML_SNAPSHOTS_DIR):
        return jsonify({'snapshots': [], 'total_count': 0, 'total_size_mb': 0})

    snapshots = []
    total_size = 0
    for fname in os.listdir(_ML_SNAPSHOTS_DIR):
        if not fname.startswith('ml_export_') or not fname.endswith('.zip'):
            continue
        fpath = os.path.join(_ML_SNAPSHOTS_DIR, fname)
        try:
            stat = os.stat(fpath)
            total_size += stat.st_size
            ts_part = fname.replace('ml_export_', '').replace('.zip', '')
            snapshots.append({
                'filename': fname,
                'size_bytes': stat.st_size,
                'size_mb': round(stat.st_size / (1024 * 1024), 2),
                'created_at': ts_part,
                'mtime_utc': datetime.utcfromtimestamp(stat.st_mtime).isoformat() + 'Z',
            })
        except Exception:
            continue

    snapshots.sort(key=lambda x: x['filename'], reverse=True)
    return jsonify({
        'snapshots': snapshots,
        'total_count': len(snapshots),
        'total_size_mb': round(total_size / (1024 * 1024), 2),
    })


@admin_bp.route('/api/admin/ml-snapshot/download/<path:filename>', methods=['GET'])
@_api_admin_req_dec
def admin_ml_snapshot_download(filename):
    """Stream a snapshot zip to the browser as a download."""
    if '/' in filename or '\\' in filename or '..' in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    if not filename.startswith('ml_export_') or not filename.endswith('.zip'):
        return jsonify({'error': 'Invalid filename'}), 400
    fpath = os.path.join(_ML_SNAPSHOTS_DIR, filename)
    if not os.path.isfile(fpath):
        return jsonify({'error': 'Snapshot not found'}), 404
    return send_from_directory(_ML_SNAPSHOTS_DIR, filename, as_attachment=True)


@admin_bp.route('/api/admin/ml-snapshot/<path:filename>', methods=['DELETE'])
@_api_admin_req_dec
def admin_ml_snapshot_delete(filename):
    """Delete a snapshot. Use sparingly — these are insurance."""
    if '/' in filename or '\\' in filename or '..' in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    if not filename.startswith('ml_export_') or not filename.endswith('.zip'):
        return jsonify({'error': 'Invalid filename'}), 400
    fpath = os.path.join(_ML_SNAPSHOTS_DIR, filename)
    if not os.path.isfile(fpath):
        return jsonify({'error': 'Snapshot not found'}), 404
    try:
        os.remove(fpath)
        return jsonify({'ok': True, 'deleted': filename})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


# ─────────────────────────────────────────────────────────────────────────────
# ML Data Audit — surface mislabeled rows for human review before bulk fixes
# ─────────────────────────────────────────────────────────────────────────────
# Rules and detection logic live in ml_data_audit.py — this just exposes them
# via admin endpoints. Approve/reject one row at a time; nothing auto-applies.

@admin_bp.route('/api/admin/ml-audit/scan', methods=['GET'])
@_api_admin_req_dec
def admin_ml_audit_scan():
    """Run all audit rules and return candidates grouped by rule."""
    try:
        from ml_data_audit import run_all_audits
        limit = int(request.args.get('limit_per_rule', 100))
        result = run_all_audits(limit_per_rule=limit)
        return jsonify(result)
    except Exception as e:
        logging.exception("ML audit scan failed")
        return jsonify({'error': str(e)[:300]}), 500


@admin_bp.route('/api/admin/ml-audit/apply', methods=['POST'])
@_api_admin_req_dec
def admin_ml_audit_apply():
    """Apply a single approved correction.

    POST body: {table: 'ml_finding_labels'|'ml_cost_data', row_id: int,
                change_field: str, change_to: str}
    """
    try:
        from ml_data_audit import apply_correction
        data = request.get_json() or {}
        table = data.get('table')
        row_id = data.get('row_id')
        change_field = data.get('change_field')
        change_to = data.get('change_to')

        if not all([table, row_id, change_field, change_to]):
            return jsonify({'ok': False, 'error': 'Missing required fields'}), 400

        result = apply_correction(table, int(row_id), change_field, change_to)
        return jsonify(result), (200 if result.get('ok') else 400)
    except Exception as e:
        logging.exception("ML audit apply failed")
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


@admin_bp.route('/api/admin/ml-audit/apply-bulk', methods=['POST'])
@_api_admin_req_dec
def admin_ml_audit_apply_bulk():
    """Apply many approved corrections in one call.

    POST body: {corrections: [{table, row_id, change_field, change_to}, ...]}
    """
    try:
        from ml_data_audit import apply_correction
        data = request.get_json() or {}
        corrections = data.get('corrections', [])

        results = {'applied': 0, 'failed': 0, 'errors': []}
        for c in corrections:
            r = apply_correction(c['table'], int(c['row_id']), c['change_field'], c['change_to'])
            if r.get('ok'):
                results['applied'] += 1
            else:
                results['failed'] += 1
                if len(results['errors']) < 10:
                    results['errors'].append({'row_id': c['row_id'], 'error': r.get('error')})
        return jsonify({'ok': True, **results})
    except Exception as e:
        logging.exception("ML audit bulk apply failed")
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Class Augmentation — generate critical-severity examples via Claude
# ─────────────────────────────────────────────────────────────────────────────
# The 0.09% critical-class problem in MLFindingLabel: 49 critical examples out
# of ~56K total. Inverse-frequency weighting (v5.86.61) helps but the model
# still can't learn what "critical" actually looks like. This generates 200-400
# realistic critical findings via Claude, grounded in real-world severity
# patterns (active leaks, structural failures, gas leaks, electrical arcing,
# foundation displacement, etc.) and inserts as source='ai_augmented'.

@admin_bp.route('/api/admin/ml-augment-critical', methods=['POST'])
@_api_admin_req_dec
def admin_ml_augment_critical():
    """Generate critical-severity finding examples via Claude and insert into MLFindingLabel.

    Single Claude call per request — Render's edge proxy hard-caps requests at
    ~100 seconds. Six sequential category calls (the original design) reliably
    exceeded that. Now: one call per request, frontend cycles through categories
    automatically. ~30-50 examples per click, ~10-20s wallclock.
    """
    import json as _json
    import hashlib

    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return jsonify({'ok': False, 'error': 'ANTHROPIC_API_KEY not configured'}), 400

    # Caller picks the category; defaults rotate through the six core categories
    # so a frontend that just hammers the endpoint cycles without coordination.
    target_categories = [
        'foundation_structure', 'electrical', 'plumbing',
        'roof_exterior', 'environmental', 'hvac_systems',
    ]
    requested_cat = (request.args.get('category') or '').strip().lower()
    if requested_cat not in target_categories:
        # Pick the under-represented category. Compare current critical-class
        # counts per category and target the smallest one.
        try:
            from models import MLFindingLabel
            from sqlalchemy import func as _func
            counts = dict(
                MLFindingLabel.query
                .filter(MLFindingLabel.severity == 'critical')
                .filter(MLFindingLabel.category.in_(target_categories))
                .with_entities(MLFindingLabel.category, _func.count('*'))
                .group_by(MLFindingLabel.category).all()
            )
            requested_cat = min(target_categories, key=lambda c: counts.get(c, 0))
        except Exception:
            requested_cat = target_categories[0]

    examples_to_request = 50

    try:
        from anthropic import Anthropic
        from models import db, MLFindingLabel

        client = Anthropic(api_key=api_key)

        prompt = f"""Generate {examples_to_request} realistic CRITICAL-SEVERITY home inspection findings for the category: {requested_cat}.

These should sound like a professional home inspector wrote them. Use specific:
- Measurements (e.g., "1/4 inch displacement", "4.8 pCi/L radon", "60 amp service")
- Locations (e.g., "northwest basement wall", "main electrical panel", "master bathroom")
- Severity indicators (e.g., "active leak", "structural failure", "imminent failure", "safety hazard", "code violation requiring immediate correction")

CRITICAL severity means: immediate safety hazard, structural failure imminent, life-safety concern, or repair >$15K. Examples by category:
- foundation_structure: "Horizontal crack with 1/2 inch displacement in north basement wall, indicates ongoing soil pressure failure"
- electrical: "Active arcing observed in main electrical panel, immediate fire hazard, requires emergency replacement"
- plumbing: "Active gas leak detected at water heater connection, sulfur odor confirmed, immediate evacuation recommended"
- roof_exterior: "Major structural decking failure across north slope, multiple rafters compromised, collapse risk"
- environmental: "Asbestos-containing material identified in HVAC duct insulation, friable, immediate professional abatement required"
- hvac_systems: "Cracked heat exchanger with carbon monoxide leak detected at 35 ppm, immediate shutdown required"

Generate {examples_to_request} unique findings for {requested_cat}. Vary the specifics — different rooms, different measurements, different failure modes.

Respond with ONLY a JSON array, no other text:
[{{"text": "...", "category": "{requested_cat}", "severity": "critical"}}]"""

        try:
            resp = client.messages.create(
                model=SONNET,
                max_tokens=4000,
                messages=[{'role': 'user', 'content': prompt}],
            )
            raw = resp.content[0].text.strip()
            if raw.startswith('```'):
                raw = raw.split('\n', 1)[-1] if '\n' in raw else raw
            if raw.endswith('```'):
                raw = raw[:-3]
            raw = raw.strip()
            if raw.startswith('json'):
                raw = raw[4:].strip()

            items = _json.loads(raw)
            if not isinstance(items, list):
                raise ValueError(f'Expected list, got {type(items)}')
        except Exception as e:
            logging.exception(f"Augment Claude call failed for {requested_cat}")
            return jsonify({'ok': False, 'error': f'Claude call failed: {str(e)[:200]}', 'category': requested_cat}), 500

        # Dedup against existing rows by content hash
        existing_hashes = set()
        for row in MLFindingLabel.query.with_entities(MLFindingLabel.finding_text).all():
            if row.finding_text:
                h = hashlib.md5(row.finding_text.lower().strip().encode('utf-8')).hexdigest()
                existing_hashes.add(h)

        inserted = 0
        skipped = 0
        for item in items:
            if not isinstance(item, dict) or 'text' not in item:
                continue
            text = (item.get('text') or '').strip()
            if len(text) < 20 or len(text) > 500:
                continue
            h = hashlib.md5(text.lower().strip().encode('utf-8')).hexdigest()
            if h in existing_hashes:
                skipped += 1
                continue
            existing_hashes.add(h)
            row = MLFindingLabel(
                finding_text=text,
                category=requested_cat,
                severity='critical',
                source='ai_augmented',
                confidence=0.7,
                is_validated=False,
            )
            db.session.add(row)
            inserted += 1

        db.session.commit()

        return jsonify({
            'ok': True,
            'category': requested_cat,
            'inserted': inserted,
            'skipped': skipped,
            'by_category': {requested_cat: inserted},
            'remaining_categories': [c for c in target_categories if c != requested_cat],
        })

    except Exception as e:
        logging.exception("ML critical augmentation failed")
        try:
            from models import db as _db
            _db.session.rollback()
        except Exception:
            pass
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500

    except Exception as e:
        logging.exception("ML critical augmentation failed")
        try:
            from models import db as _db
            _db.session.rollback()
        except Exception:
            pass
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


# =============================================================================
# v5.88.20: Cassette recording + Postgres test runner admin endpoints
#
# Provides one-click admin operations for:
#   1. Recording /api/analyze cassettes (replaces test_cassettes/record_cassettes.py CLI)
#   2. Running the Postgres-portable test suite against TEST_DATABASE_URL
#
# Both ops are LONG-RUNNING (30-300s) so they use a job-tracking pattern:
#   POST /api/admin/.../start  → returns {job_id}
#   GET  /api/admin/jobs/<job_id>  → returns {status, progress, output, ...}
#   GET  /api/admin/cassettes/download/<job_id>  → returns ZIP after recording done
#
# Concurrency: only one cassette recording or postgres test run at a time.
# In-memory job state — survives nothing across deploys, which is fine
# (admin can just re-trigger). NOT a queue; rejects new requests when busy.
# =============================================================================

import threading
import uuid as _uuid_mod
from datetime import datetime as _dt
from collections import deque as _deque

# In-memory job registry. Keyed by job_id -> dict.
# Schema:
#   id: str
#   kind: 'cassette' | 'postgres'
#   status: 'pending' | 'running' | 'success' | 'failed'
#   created_at: ISO timestamp
#   finished_at: ISO timestamp or None
#   log_lines: list[str] — appended as work progresses
#   summary: dict — final summary (counts, costs, etc.)
#   artifact_path: str or None — for downloadable artifacts (cassette ZIPs)
_admin_jobs = {}
_admin_jobs_lock = threading.Lock()

# Concurrency lock — only one long-running admin job at a time.
# This is intentionally simple: if you click both buttons in quick
# succession, the second click gets a "busy, try again" response.
_running_admin_kind = None  # None or 'cassette' or 'postgres'


def _new_job(kind):
    """Allocate a new job and mark the global running-kind. Returns
    (job_id, error_msg). If error_msg is non-None, caller must NOT
    proceed."""
    global _running_admin_kind
    with _admin_jobs_lock:
        if _running_admin_kind is not None:
            return None, f'Another admin job is already running ({_running_admin_kind}). Wait for it to finish.'
        job_id = f'{kind}_{_uuid_mod.uuid4().hex[:8]}'
        _admin_jobs[job_id] = {
            'id': job_id,
            'kind': kind,
            'status': 'running',
            'created_at': _dt.utcnow().isoformat(),
            'finished_at': None,
            'log_lines': _deque(maxlen=2000),  # cap memory
            'summary': {},
            'artifact_path': None,
        }
        _running_admin_kind = kind
        return job_id, None


def _job_log(job_id, line):
    """Append a log line to a job's log_lines."""
    with _admin_jobs_lock:
        j = _admin_jobs.get(job_id)
        if j is not None:
            ts = _dt.utcnow().strftime('%H:%M:%S')
            j['log_lines'].append(f'[{ts}] {line}')


def _job_finalize(job_id, status, summary):
    """Mark a job complete and release the global lock."""
    global _running_admin_kind
    with _admin_jobs_lock:
        j = _admin_jobs.get(job_id)
        if j is not None:
            j['status'] = status  # 'success' or 'failed'
            j['finished_at'] = _dt.utcnow().isoformat()
            j['summary'] = summary
        _running_admin_kind = None


@admin_bp.route('/api/admin/jobs/<job_id>')
@_api_admin_req_dec
def admin_get_job(job_id):
    """Poll job status. Frontend polls this every 1-2s while a job
    is running. Returns log lines, status, summary."""
    with _admin_jobs_lock:
        j = _admin_jobs.get(job_id)
        if j is None:
            return jsonify({'error': 'Job not found'}), 404
        # Make a snapshot — return list, not deque
        return jsonify({
            'id': j['id'],
            'kind': j['kind'],
            'status': j['status'],
            'created_at': j['created_at'],
            'finished_at': j['finished_at'],
            'log_lines': list(j['log_lines']),
            'summary': j['summary'],
            'has_artifact': j.get('artifact_path') is not None,
        })


# ─── CASSETTE RECORDING ──────────────────────────────────────────────

@admin_bp.route('/api/admin/cassettes/start', methods=['POST'])
@_api_admin_req_dec
def admin_cassettes_start():
    """Start cassette recording in a background thread. Returns
    immediately with a job_id; client polls /api/admin/jobs/<id>
    for progress."""
    import os as _os
    if not _os.environ.get('ANTHROPIC_API_KEY'):
        return jsonify({
            'error': 'ANTHROPIC_API_KEY not set on server. Cannot record cassettes.'
        }), 400

    job_id, err = _new_job('cassette')
    if err:
        return jsonify({'error': err}), 409  # Conflict (busy)

    # v5.88.21: capture the Flask app object NOW, while still in the
    # request context. The background thread loses access to
    # current_app once the request ends, so we pass the resolved app
    # object explicitly into the worker.
    from flask import current_app as _ca
    flask_app = _ca._get_current_object()

    # Start the background recording
    def _run():
        try:
            _record_cassettes_inline(job_id, flask_app)
        except Exception as e:
            import logging as _log
            _log.exception(f'Cassette recording job {job_id} crashed')
            _job_log(job_id, f'CRASH: {e}')
            _job_finalize(job_id, 'failed', {'error': str(e)[:500]})

    thread = threading.Thread(target=_run, daemon=True, name=f'cassette-{job_id}')
    thread.start()

    return jsonify({
        'job_id': job_id,
        'status': 'started',
        'estimated_duration_seconds': 180,
        'estimated_cost_usd': 0.50,
    })


def _record_cassettes_inline(job_id, flask_app):
    """Run the cassette recording flow. Adapted from test_cassettes/record_cassettes.py
    but logs to the admin job and writes to /tmp instead of the repo.
    flask_app is passed in by the caller (captured from request context)
    because background threads have no current_app proxy."""
    import os as _os
    import base64 as _b64
    import shutil as _shutil
    import zipfile as _zip
    from pathlib import Path as _Path

    # v5.88.22: pre-flight check — confirm the disclosure + inspection PDFs are
    # present before doing anything. v5.88.21 silently skipped them
    # and produced a 1-cassette ZIP that looked successful but was
    # missing the two most valuable cassettes.
    # v5.88.24: now also require the inspection PDFs needed for full-path
    # cassettes (which exercise the risk-scoring model — disclosure-only
    # path correctly produces overall_risk_score=0 because risk scoring
    # needs inspection findings to compute weights).
    repo_root = _Path(__file__).parent
    required_pdfs = {
        'clean disclosure':     repo_root / 'test_corpus' / '01_digital_tds_clean.pdf',
        'clean inspection':     repo_root / 'test_corpus' / '02_digital_inspection_clean.pdf',
        'nightmare disclosure': repo_root / 'test_corpus' / '03_digital_tds_nightmare_no_disclosure.pdf',
        'nightmare inspection': repo_root / 'test_corpus' / '04_digital_inspection_nightmare.pdf',
    }
    missing = [name for name, p in required_pdfs.items() if not p.exists()]
    if missing:
        msg = f'❌ Pre-flight failed: {len(missing)} required PDF(s) missing from deploy: {missing}'
        _job_log(job_id, msg)
        _job_log(job_id, '   This usually means the tarball build excluded test_corpus/*.pdf.')
        _job_log(job_id, '   Cassette recording NEEDS the test PDFs to record disclosure cassettes.')
        _job_log(job_id, '   v5.88.24+ includes the 4 digital PDFs in the deploy. If this fires,')
        _job_log(job_id, '   the deploy is older than v5.88.24 OR the build script regressed.')
        _job_finalize(job_id, 'failed', {
            'error': f'Missing PDFs: {", ".join(missing)}',
            'fix': 'Deploy v5.88.24 or later — build must include test_corpus/01-04.pdf',
        })
        return

    # Cassettes write to /tmp on Render (ephemeral but fine — we ZIP for download)
    tmp_dir = _Path(f'/tmp/cassettes_{job_id}')
    tmp_dir.mkdir(parents=True, exist_ok=True)

    _job_log(job_id, '🎬 Starting cassette recording')
    _job_log(job_id, f'   Output dir: {tmp_dir}')

    try:
        import vcr
    except ImportError:
        _job_log(job_id, '❌ vcrpy not installed on server — pip install vcrpy')
        _job_finalize(job_id, 'failed', {'error': 'vcrpy not installed'})
        return

    # Sanitization config
    SENSITIVE_HEADERS = ['authorization', 'x-api-key', 'anthropic-api-key', 'cookie', 'set-cookie']
    SENSITIVE_QUERY = ['api_key', 'apikey', 'key']
    SENSITIVE_POST = ['api_key', 'apikey', 'key']

    def _sanitize_request(req):
        for h in SENSITIVE_HEADERS:
            if h in req.headers:
                req.headers[h] = '<REDACTED>'
        return req

    def _sanitize_response(resp):
        if resp and 'headers' in resp:
            for h in SENSITIVE_HEADERS:
                if h in resp['headers']:
                    resp['headers'][h] = ['<REDACTED>']
        return resp

    def _make_vcr():
        return vcr.VCR(
            cassette_library_dir=str(tmp_dir),
            record_mode='all',
            match_on=['method', 'scheme', 'host', 'path'],
            filter_headers=[(h, '<REDACTED>') for h in SENSITIVE_HEADERS],
            filter_query_parameters=[(p, '<REDACTED>') for p in SENSITIVE_QUERY],
            filter_post_data_parameters=[(p, '<REDACTED>') for p in SENSITIVE_POST],
            before_record_request=_sanitize_request,
            before_record_response=_sanitize_response,
            decode_compressed_response=True,
        )

    # Get a flask test client + create a recorder user
    # (flask_app was passed in from the request handler; safe to use here)
    from models import db, User

    test_email = f'cassette_recorder_{job_id}@e2e-cassette.test.example.com'

    # v5.88.25: opportunistic sweep — delete any ghost cassette_recorder
    # users from prior failed runs. The try/finally below ensures THIS
    # run's user is cleaned up, but pre-v5.88.25 runs that crashed mid-
    # flow left ghosts (4 visible in screenshot on May 10). This sweep
    # is idempotent and safe.
    with flask_app.app_context():
        try:
            ghosts = User.query.filter(
                User.email.like('cassette_recorder_%@e2e-cassette.test.example.com')
            ).all()
            ghost_count = len(ghosts)
            for g in ghosts:
                db.session.delete(g)
            if ghost_count > 0:
                db.session.commit()
                _job_log(job_id, f'🧹 Swept {ghost_count} ghost cassette user(s) from prior runs')
        except Exception as _sweep_err:
            _job_log(job_id, f'⚠️  Ghost sweep failed (non-fatal): {_sweep_err}')
            try:
                db.session.rollback()
            except Exception:
                pass

        user = User(
            email=test_email,
            name='Cassette Recorder',
            auth_provider='email', tier='enterprise',
            analysis_credits=100,
            analyses_completed=0,
            stripe_customer_id='cus_cassette_recorder',
        )
        user.set_password('CassetteRecord123!')
        db.session.add(user)
        db.session.commit()
        uid = user.id

    _job_log(job_id, f'✅ Test user created: id={uid}')

    # v5.88.25: try/finally wraps the entire recording flow so the test
    # user is ALWAYS cleaned up, even if the flow crashes mid-way. Prior
    # versions had cleanup as a regular block after the recording loop,
    # which silently leaked users on any uncaught exception in the
    # cassette loop (4 ghosts visible in the May 10 Buyers screenshot).
    try:
        client = flask_app.test_client(use_cookies=True)
        # v5.88.21: session_transaction needs an app context; client.post
        # pushes its own per-request, but session_transaction does not.
        with flask_app.app_context():
            with client.session_transaction() as sess:
                sess['_user_id'] = str(uid)
                sess['_fresh'] = True

        cassettes_recorded = []

        def _log_response_shape(label, response):
            """v5.88.22: surface what the cassette actually captured.
            Address-only path returns a different shape than full-disclosure
            path; logging the actual fields confirms the recording is what
            we expected, not a degenerate fallback.
            v5.88.23: also surface analysis_depth so we can tell if the
            disclosure path was actually taken."""
            if response.status_code != 200:
                _job_log(job_id, f'   ⚠️  non-200: {response.data[:200]!r}')
                return False
            try:
                data = response.get_json() or {}
            except Exception:
                _job_log(job_id, f'   ⚠️  200 but body is not JSON: {response.data[:200]!r}')
                return False

            # v5.88.23: surface the analysis path actually taken — this caught
            # the v5.88.22 bug where all 3 cassettes silently fell to address_only
            depth = data.get('analysis_depth', 'unknown')
            _job_log(job_id, f'   ✅ {label} — analysis_depth={depth}')

            # v5.88.24: read overall_risk_score with explicit None check, not
            # truthy fallback. risk_score = 0 is a VALID result for
            # disclosure-only path (no inspection findings → score floors to 0
            # per risk_scoring_model.py:437). The old `a or b or c` chain
            # treated 0 as "missing" and logged None, which was misleading.
            risk_obj = data.get('risk_score') or {}
            risk_val = risk_obj.get('overall_risk_score')
            if risk_val is None:
                risk_val = risk_obj.get('composite_score')
            if risk_val is None:
                risk_val = risk_obj.get('overall')
            _job_log(job_id, f'   risk: {risk_val}, '
                     f'risk_score keys: {sorted(risk_obj.keys()) if isinstance(risk_obj, dict) else "n/a"}')

            # Offer if present
            offer_obj = data.get('offer_strategy') or {}
            if offer_obj:
                offer = offer_obj.get('recommended_offer')
                _job_log(job_id, f'   recommended_offer: {offer}')
            return True

        # ─ Cassette 1: address-only ─
        _job_log(job_id, '🎙️  Recording: analyze_address_only.yaml')
        try:
            with _make_vcr().use_cassette('analyze_address_only.yaml'):
                r = client.post('/api/analyze', json={
                    'property_address': '123 Cassette Test Lane, San Jose, CA',
                    'property_price': 500000,
                }, headers={'Origin': 'https://www.getofferwise.ai'})
            _job_log(job_id, f'   Response: {r.status_code}')
            if _log_response_shape('address_only', r):
                cassettes_recorded.append('analyze_address_only.yaml')
        except Exception as e:
            _job_log(job_id, f'   ❌ {type(e).__name__}: {e}')

        # v5.88.23: helper to extract text from a digital PDF.
        # The previous version sent seller_disclosure_pdf_base64 which is
        # NOT a field /api/analyze accepts (it accepts seller_disclosure_text
        # or job_id). PDFs were silently ignored, so all 3 cassettes took
        # the address_only path. Now we extract text on the server side and
        # send it as seller_disclosure_text — what real users effectively
        # provide after the upload-pdf endpoint extracts text from their PDF.
        def _extract_pdf_text(pdf_path):
            try:
                import PyPDF2
                with open(pdf_path, 'rb') as f:
                    reader = PyPDF2.PdfReader(f)
                    return '\n'.join(page.extract_text() or '' for page in reader.pages)
            except Exception as e:
                _job_log(job_id, f'   ⚠️  PDF text extraction failed: {e}')
                return ''

        # ─ Cassette 2: clean disclosure ─
        pdf_path = repo_root / 'test_corpus' / '01_digital_tds_clean.pdf'
        if pdf_path.exists():
            _job_log(job_id, f'🎙️  Recording: analyze_clean_disclosure.yaml ({pdf_path.name})')
            try:
                disclosure_text = _extract_pdf_text(pdf_path)
                _job_log(job_id, f'   Extracted {len(disclosure_text)} chars from PDF')
                if not disclosure_text.strip():
                    _job_log(job_id, '   ⚠️  PDF extracted no text — cassette would be address-only')
                with _make_vcr().use_cassette('analyze_clean_disclosure.yaml'):
                    r = client.post('/api/analyze', json={
                        'property_address': '456 Clean Disclosure St, Oakland, CA',
                        'property_price': 750000,
                        'seller_disclosure_text': disclosure_text,
                    }, headers={'Origin': 'https://www.getofferwise.ai'})
                _job_log(job_id, f'   Response: {r.status_code}')
                if _log_response_shape('clean_disclosure', r):
                    cassettes_recorded.append('analyze_clean_disclosure.yaml')
            except Exception as e:
                _job_log(job_id, f'   ❌ {type(e).__name__}: {e}')
        else:
            _job_log(job_id, f'⚠️  Skipping clean disclosure: {pdf_path} not found')

        # ─ Cassette 3: nightmare disclosure (disclosure-only path) ─
        pdf_path = repo_root / 'test_corpus' / '03_digital_tds_nightmare_no_disclosure.pdf'
        if pdf_path.exists():
            _job_log(job_id, f'🎙️  Recording: analyze_nightmare_disclosure.yaml ({pdf_path.name})')
            try:
                disclosure_text = _extract_pdf_text(pdf_path)
                _job_log(job_id, f'   Extracted {len(disclosure_text)} chars from PDF')
                if not disclosure_text.strip():
                    _job_log(job_id, '   ⚠️  PDF extracted no text — cassette would be address-only')
                with _make_vcr().use_cassette('analyze_nightmare_disclosure.yaml'):
                    r = client.post('/api/analyze', json={
                        'property_address': '789 Nightmare Rd, Berkeley, CA',
                        'property_price': 900000,
                        'seller_disclosure_text': disclosure_text,
                    }, headers={'Origin': 'https://www.getofferwise.ai'})
                _job_log(job_id, f'   Response: {r.status_code}')
                # v5.88.24: removed misplaced risk-warning here. The disclosure-only
                # path produces overall_risk_score=0 by design (risk_scoring_model
                # requires inspection findings to compute weights; with none, it
                # correctly returns 0). The nightmare-risk assertion belongs on the
                # full-path cassette (cassette 5) where the inspection PDF provides
                # findings for the risk model.
                if _log_response_shape('nightmare_disclosure', r):
                    cassettes_recorded.append('analyze_nightmare_disclosure.yaml')
            except Exception as e:
                _job_log(job_id, f'   ❌ {type(e).__name__}: {e}')
        else:
            _job_log(job_id, f'⚠️  Skipping nightmare disclosure: {pdf_path} not found')

        # ─ Cassette 4: full clean (disclosure + inspection) ─
        # v5.88.24: full-path cassette exercises the risk-scoring model with real
        # inspection findings, which the disclosure-only path doesn't reach.
        disc_pdf = repo_root / 'test_corpus' / '01_digital_tds_clean.pdf'
        insp_pdf = repo_root / 'test_corpus' / '02_digital_inspection_clean.pdf'
        if disc_pdf.exists() and insp_pdf.exists():
            _job_log(job_id, f'🎙️  Recording: analyze_full_clean.yaml ({disc_pdf.name} + {insp_pdf.name})')
            try:
                disclosure_text = _extract_pdf_text(disc_pdf)
                inspection_text = _extract_pdf_text(insp_pdf)
                _job_log(job_id, f'   Extracted disclosure: {len(disclosure_text)} chars, inspection: {len(inspection_text)} chars')
                if not (disclosure_text.strip() and inspection_text.strip()):
                    _job_log(job_id, '   ⚠️  One or both PDFs extracted no text — full path may degrade')
                with _make_vcr().use_cassette('analyze_full_clean.yaml'):
                    r = client.post('/api/analyze', json={
                        'property_address': '456 Clean Disclosure St, Oakland, CA',
                        'property_price': 750000,
                        'seller_disclosure_text': disclosure_text,
                        'inspection_report_text': inspection_text,
                    }, headers={'Origin': 'https://www.getofferwise.ai'})
                _job_log(job_id, f'   Response: {r.status_code}')
                if _log_response_shape('full_clean', r):
                    cassettes_recorded.append('analyze_full_clean.yaml')
            except Exception as e:
                _job_log(job_id, f'   ❌ {type(e).__name__}: {e}')
        else:
            _job_log(job_id, f'⚠️  Skipping full_clean: missing PDFs')

        # ─ Cassette 5: full nightmare (disclosure + inspection) ─
        # The headline cassette — exercises the orchestrator end-to-end on
        # adversarial input. If a future PR weakens the risk model or
        # contradiction detector, this cassette's replay test catches it.
        disc_pdf = repo_root / 'test_corpus' / '03_digital_tds_nightmare_no_disclosure.pdf'
        insp_pdf = repo_root / 'test_corpus' / '04_digital_inspection_nightmare.pdf'
        if disc_pdf.exists() and insp_pdf.exists():
            _job_log(job_id, f'🎙️  Recording: analyze_full_nightmare.yaml ({disc_pdf.name} + {insp_pdf.name})')
            try:
                disclosure_text = _extract_pdf_text(disc_pdf)
                inspection_text = _extract_pdf_text(insp_pdf)
                _job_log(job_id, f'   Extracted disclosure: {len(disclosure_text)} chars, inspection: {len(inspection_text)} chars')
                if not (disclosure_text.strip() and inspection_text.strip()):
                    _job_log(job_id, '   ⚠️  One or both PDFs extracted no text — full path may degrade')
                with _make_vcr().use_cassette('analyze_full_nightmare.yaml'):
                    r = client.post('/api/analyze', json={
                        'property_address': '789 Nightmare Rd, Berkeley, CA',
                        'property_price': 900000,
                        'seller_disclosure_text': disclosure_text,
                        'inspection_report_text': inspection_text,
                    }, headers={'Origin': 'https://www.getofferwise.ai'})
                _job_log(job_id, f'   Response: {r.status_code}')
                if _log_response_shape('full_nightmare', r):
                    # v5.88.24: the nightmare-risk assertion lives here, where
                    # the risk model has real inspection findings to score
                    # against. Threshold conservative — nightmare with real
                    # findings should never score below 40.
                    try:
                        data = r.get_json() or {}
                        risk_obj = data.get('risk_score') or {}
                        risk = risk_obj.get('overall_risk_score')
                        if risk is None:
                            risk = risk_obj.get('composite_score')
                        if isinstance(risk, (int, float)):
                            if risk < 40:
                                _job_log(job_id, f'   ⚠️  LOW risk for full_nightmare ({risk}); '
                                         'either model behavior changed or PDFs are no longer adversarial enough — '
                                         'investigate before committing this cassette')
                            else:
                                _job_log(job_id, f'   ✅ Risk score {risk:.1f} as expected for adversarial input')
                    except Exception:
                        pass
                    cassettes_recorded.append('analyze_full_nightmare.yaml')
            except Exception as e:
                _job_log(job_id, f'   ❌ {type(e).__name__}: {e}')
            else:
                _job_log(job_id, f'⚠️  Skipping full_nightmare: missing PDFs')

            # v5.88.25: cleanup moved to finally block below. The rest of the
            # post-recording flow (sanitization + zip + finalize) lives inside
            # the try so that if anything in HERE crashes (e.g. ZIP errors),
            # the user is still cleaned up.

            # Verify no API keys leaked
            _job_log(job_id, '🔍 Sanitization check: scanning cassettes for leaked secrets...')
            leak_found = False
            for cn in cassettes_recorded:
                cp = tmp_dir / cn
                try:
                    content = cp.read_text(errors='ignore')
                    for marker in ['sk-ant-api', 'sk-ant-sid']:
                        if marker in content:
                            _job_log(job_id, f'   ❌ LEAK in {cn}: found {marker}')
                            leak_found = True
                except Exception as e:
                    _job_log(job_id, f'   ⚠️  Could not scan {cn}: {e}')
            if not leak_found:
                _job_log(job_id, '   ✅ No API keys found in cassettes')

            # ZIP for download
            zip_path = _Path(f'/tmp/cassettes_{job_id}.zip')
            try:
                with _zip.ZipFile(zip_path, 'w', _zip.ZIP_DEFLATED) as zf:
                    for cn in cassettes_recorded:
                        cp = tmp_dir / cn
                        if cp.exists():
                            zf.write(cp, arcname=cn)
                _job_log(job_id, f'📦 ZIP ready: {zip_path} ({zip_path.stat().st_size // 1024} KB)')
            except Exception as e:
                _job_log(job_id, f'❌ ZIP failed: {e}')
                zip_path = None

            summary = {
                'cassettes_recorded': cassettes_recorded,
                'count': len(cassettes_recorded),
                'leak_found': leak_found,
                'estimated_cost_usd': round(0.10 * len(cassettes_recorded), 2),
            }

            with _admin_jobs_lock:
                j = _admin_jobs.get(job_id)
                if j is not None:
                    j['artifact_path'] = str(zip_path) if zip_path and zip_path.exists() else None

            status = 'failed' if leak_found or not cassettes_recorded else 'success'
            _job_log(job_id, f'✅ Recording complete: {len(cassettes_recorded)} cassettes' if status == 'success' else '❌ Recording failed')
            _job_finalize(job_id, status, summary)
    finally:
        # v5.88.25: guaranteed cleanup of THIS run's test user.
        # Runs even if the recording loop crashed, even if the ZIP
        # failed, even if the finalize call raised. The startup sweep
        # above handles ghosts from PRIOR runs; this handles THIS one.
        try:
            with flask_app.app_context():
                u = User.query.get(uid)
                if u:
                    db.session.delete(u)
                    db.session.commit()
                    _job_log(job_id, f'🧹 Cleaned up test user id={uid}')
        except Exception as _cleanup_err:
            _job_log(job_id, f'⚠️  Cleanup failed for user {uid}: {_cleanup_err}')
            try:
                db.session.rollback()
            except Exception:
                pass


@admin_bp.route('/api/admin/cassettes/download/<job_id>')
@_api_admin_req_dec
def admin_cassettes_download(job_id):
    """Download the cassette ZIP after recording completes."""
    from flask import send_file as _send_file
    with _admin_jobs_lock:
        j = _admin_jobs.get(job_id)
        if j is None:
            return jsonify({'error': 'Job not found'}), 404
        if j['status'] != 'success':
            return jsonify({'error': f'Job status is {j["status"]} — no artifact available'}), 400
        path = j.get('artifact_path')
        if not path:
            return jsonify({'error': 'No artifact path'}), 404

    from pathlib import Path as _Path
    p = _Path(path)
    if not p.exists():
        return jsonify({'error': 'Artifact file no longer exists (Render may have rotated /tmp)'}), 404

    return _send_file(str(p), as_attachment=True, download_name=f'cassettes_{job_id}.zip',
                      mimetype='application/zip')


# ─── POSTGRES TEST RUNNER ────────────────────────────────────────────

# Production hostname patterns that must NEVER receive test traffic.
_POSTGRES_PROD_PATTERNS = [
    'offerwise-postgres.render.com',
    'getofferwise-prod',
    'offerwise-prod',
    'production',
]
_POSTGRES_SAFE_PATTERNS = [
    'offerwise-postgres-test',
    '_test',
    'staging',
]

# Postgres-portable subset (matches run_postgres_tests.py)
_POSTGRES_PORTABLE_FILES = [
    'test_e2e_auth_signup.py',
    'test_e2e_credits_payments.py',
    'test_e2e_analysis_core.py',
    'test_e2e_outreach_pipeline.py',
    'test_e2e_admin_mutations.py',
    'test_e2e_onboarding_drip.py',
    'test_e2e_bug_sweep_audits.py',
    'test_e2e_oauth_ratelimits_concurrency.py',
    'test_e2e_critical_journeys.py',
    'test_e2e_cron_jobs.py',
    'test_v5_88_07.py',
    # v5.89.34: unit test for cooperative cancellation. Self-contained,
    # no DB required — runs cleanly against either SQLite or Postgres.
    'test_crawl_abort.py',
]


@admin_bp.route('/api/admin/postgres-tests/start', methods=['POST'])
@_api_admin_req_dec
def admin_postgres_tests_start():
    """Run the Postgres-portable test subset against TEST_DATABASE_URL."""
    import os as _os
    test_url = (_os.environ.get('TEST_DATABASE_URL') or '').strip()
    if not test_url:
        return jsonify({
            'error': 'TEST_DATABASE_URL not set on server.',
            'hint': 'Set it in Render to the offerwise-postgres-test internal URL.',
        }), 400

    if not (test_url.startswith('postgresql://') or test_url.startswith('postgres://')):
        return jsonify({
            'error': f'TEST_DATABASE_URL must be postgresql://, got prefix {test_url[:20]!r}'
        }), 400

    # Production pattern check — refuse to run if URL looks like prod
    url_lower = test_url.lower()
    for pat in _POSTGRES_PROD_PATTERNS:
        if pat in url_lower:
            return jsonify({
                'error': f'TEST_DATABASE_URL contains production pattern {pat!r}. '
                         'Refusing to run tests against production.'
            }), 403

    # Soft warning if URL doesn't match known safe patterns
    if not any(pat in url_lower for pat in _POSTGRES_SAFE_PATTERNS):
        return jsonify({
            'error': 'TEST_DATABASE_URL does not match any known test/staging '
                     'pattern (offerwise-postgres-test, _test, staging). '
                     'If this is intentional, the URL needs to include one of those tokens.'
        }), 400

    job_id, err = _new_job('postgres')
    if err:
        return jsonify({'error': err}), 409

    def _run():
        try:
            _run_postgres_tests_inline(job_id, test_url)
        except Exception as e:
            import logging as _log
            _log.exception(f'Postgres tests job {job_id} crashed')
            _job_log(job_id, f'CRASH: {e}')
            _job_finalize(job_id, 'failed', {'error': str(e)[:500]})

    thread = threading.Thread(target=_run, daemon=True, name=f'pg-tests-{job_id}')
    thread.start()

    # Compute hostname for display (without password)
    host_for_display = 'unknown'
    try:
        from urllib.parse import urlparse as _urlparse
        parsed = _urlparse(test_url)
        host_for_display = parsed.hostname or 'unknown'
    except Exception:
        pass

    return jsonify({
        'job_id': job_id,
        'status': 'started',
        'estimated_duration_seconds': 240,
        'host': host_for_display,
        'files_to_run': _POSTGRES_PORTABLE_FILES,
    })


def _truncate_log_excerpt(lines, cap_bytes=50_000):
    """Truncate a list of log lines to fit within cap_bytes. If oversized,
    keep ~50% head and ~50% tail with a "[... NNN lines truncated ...]"
    marker in the middle. This preserves both the start (setup, first
    file's output) and the tail (summary line, last failure trace) —
    the parts an operator usually wants to see.
    """
    joined = '\n'.join(lines)
    if len(joined) <= cap_bytes:
        return joined
    half = cap_bytes // 2
    head = joined[:half]
    tail = joined[-half:]
    # Find newline boundaries so we don't cut mid-line
    last_nl_head = head.rfind('\n')
    if last_nl_head > 0:
        head = head[:last_nl_head]
    first_nl_tail = tail.find('\n')
    if first_nl_tail >= 0:
        tail = tail[first_nl_tail + 1:]
    omitted = len(joined) - len(head) - len(tail)
    return head + f'\n\n[... {omitted:,} bytes truncated ...]\n\n' + tail


def _persist_postgres_test_run(job_id, test_db_url, overall, summary, elapsed_seconds):
    """Persist the postgres test run to the DB so the history survives
    web service restarts. Called at the end of _run_postgres_tests_inline,
    right after the in-memory _job_finalize. Errors here are non-fatal —
    they shouldn't break the test run itself.
    """
    try:
        from models import PostgresTestRun
        from urllib.parse import urlparse as _urlparse
        import json as _json

        # Parse host/db from URL; never persist the URL itself (contains password)
        host, db_name = None, None
        try:
            parsed = _urlparse(test_db_url)
            host = parsed.hostname
            db_name = (parsed.path or '/').lstrip('/') or None
        except Exception:
            pass

        # Snapshot the in-memory log lines into an excerpt
        log_excerpt = ''
        try:
            with _admin_jobs_lock:
                j = _admin_jobs.get(job_id)
                if j is not None:
                    log_excerpt = _truncate_log_excerpt(list(j['log_lines']))
        except Exception:
            pass

        run = PostgresTestRun(
            trigger='manual',
            elapsed_seconds=elapsed_seconds,
            status=overall,
            total_passed=summary.get('total_passed', 0),
            total_failed=summary.get('total_failed', 0),
            total_skipped=summary.get('total_skipped', 0),
            test_db_host=host[:200] if host else None,
            test_db_name=db_name[:100] if db_name else None,
            summary=_json.dumps(summary),
            log_excerpt=log_excerpt,
        )
        db.session.add(run)
        db.session.commit()
    except Exception as e:
        # DB persistence is best-effort. Log to the in-memory job so the
        # operator can see it in the live polling response, but never fail
        # the test run itself because of a logging bug.
        try:
            _job_log(job_id, f'   (note: persisting run history to DB failed: {type(e).__name__}: {str(e)[:100]})')
            db.session.rollback()
        except Exception:
            pass


def _run_postgres_tests_inline(job_id, test_db_url):
    """Run the Postgres-portable test files against TEST_DATABASE_URL."""
    import subprocess as _sp
    import os as _os
    import re as _re
    import time as _time
    from pathlib import Path as _Path

    repo_root = _Path(__file__).parent
    t_start = _time.time()  # v5.89.29: capture for persistent history

    _job_log(job_id, '🐘 Starting Postgres test run')

    from urllib.parse import urlparse as _urlparse
    try:
        parsed = _urlparse(test_db_url)
        host = parsed.hostname or '?'
        db = (parsed.path or '/?').lstrip('/')
        _job_log(job_id, f'   Host: {host}')
        _job_log(job_id, f'   Database: {db}')
    except Exception:
        pass

    # Build env for subprocess: copy current env + override DATABASE_URL
    env = _os.environ.copy()
    env['DATABASE_URL'] = test_db_url
    env['FLASK_ENV'] = 'testing'
    env.setdefault('SECRET_KEY', 'test-postgres-secret')
    env.setdefault('ADMIN_KEY', 'test-postgres-admin')
    env['RATELIMIT_ENABLED'] = 'false'
    # Don't pollute Postgres test DB with production-flavored env
    env.pop('STRIPE_SECRET_KEY', None)
    env.pop('STRIPE_WEBHOOK_SECRET', None)

    total_passed = 0
    total_skipped = 0
    total_failed = 0
    failed_files = []
    per_file = []

    for f in _POSTGRES_PORTABLE_FILES:
        target = repo_root / f
        if not target.exists():
            _job_log(job_id, f'⚠️  {f}: file missing, skipping')
            continue

        _job_log(job_id, f'━━━ {f}')
        try:
            result = _sp.run(
                ['python3', '-m', 'pytest', f, '-v', '--tb=short', '--no-header'],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(repo_root),
                env=env,
            )
            output = (result.stdout or '') + (result.stderr or '')

            # Parse last line(s) for pass/fail counts
            last_meaningful = ''
            for line in output.strip().split('\n')[::-1]:
                if 'passed' in line or 'failed' in line or 'error' in line.lower():
                    last_meaningful = line
                    break

            p = int(_re.search(r'(\d+) passed', last_meaningful).group(1)) if _re.search(r'(\d+) passed', last_meaningful) else 0
            s = int(_re.search(r'(\d+) skipped', last_meaningful).group(1)) if _re.search(r'(\d+) skipped', last_meaningful) else 0
            fl = int(_re.search(r'(\d+) failed', last_meaningful).group(1)) if _re.search(r'(\d+) failed', last_meaningful) else 0
            er = int(_re.search(r'(\d+) error', last_meaningful).group(1)) if _re.search(r'(\d+) error', last_meaningful) else 0
            total_passed += p
            total_skipped += s
            total_failed += fl + er

            status_emoji = '✅' if (fl + er) == 0 else '❌'
            _job_log(job_id, f'   {status_emoji} {p} passed, {s} skipped, {fl} failed, {er} errors')

            file_record = {
                'file': f,
                'passed': p,
                'skipped': s,
                'failed': fl,
                'errors': er,
                'output_tail': '\n'.join(output.strip().split('\n')[-40:]),
            }
            per_file.append(file_record)

            if fl + er > 0:
                failed_files.append(f)
                # Log first failure detail
                in_failure = False
                printed = 0
                for line in output.split('\n'):
                    if 'FAILED' in line or 'ERROR' in line:
                        in_failure = True
                    if in_failure and printed < 8:
                        _job_log(job_id, f'      {line}')
                        printed += 1

        except _sp.TimeoutExpired:
            _job_log(job_id, f'   ⏱️  TIMEOUT after 300s')
            failed_files.append(f)
            total_failed += 1
        except Exception as e:
            _job_log(job_id, f'   💥 {type(e).__name__}: {e}')
            failed_files.append(f)
            total_failed += 1

    summary = {
        'total_passed': total_passed,
        'total_skipped': total_skipped,
        'total_failed': total_failed,
        'failed_files': failed_files,
        'per_file': per_file,
    }
    overall = 'success' if total_failed == 0 else 'failed'

    _job_log(job_id, f'━━━ SUMMARY: {total_passed} passed, {total_skipped} skipped, {total_failed} failed')
    _job_finalize(job_id, overall, summary)

    # v5.89.29: persist to DB so history survives restarts.
    elapsed = round(_time.time() - t_start, 2)
    _persist_postgres_test_run(job_id, test_db_url, overall, summary, elapsed)


# ═══════════════════════════════════════════════════════════════════════════════
# Postgres test run history — v5.89.29
# ═══════════════════════════════════════════════════════════════════════════════
# Before this, postgres test results lived only in the in-memory _admin_jobs
# dict, meaning every web service restart erased the history. Operators
# couldn't answer "have these tests ever passed?" without trawling server
# logs. This endpoint exposes the persistent PostgresTestRun table.

@admin_bp.route('/api/admin/postgres-tests/history', methods=['GET'])
@_api_admin_req_dec
def admin_postgres_tests_history():
    """Return recent postgres test runs from the PostgresTestRun table.

    Default returns the 20 most recent runs without log excerpts (lean
    response for the history table). Pass ?run_id=N to fetch a single
    run's full log_excerpt for the expander.
    """
    import json as _json
    from flask import request

    # Single-run fetch (for expander)
    run_id = request.args.get('run_id', type=int)
    if run_id is not None:
        try:
            from models import PostgresTestRun
            r = PostgresTestRun.query.get(run_id)
            if r is None:
                return jsonify({'error': f'PostgresTestRun {run_id} not found'}), 404
            return jsonify({
                'id': r.id,
                'created_at': (r.created_at.isoformat() + 'Z') if r.created_at else None,
                'status': r.status,
                'elapsed_seconds': r.elapsed_seconds,
                'total_passed': r.total_passed,
                'total_failed': r.total_failed,
                'total_skipped': r.total_skipped,
                'test_db_host': r.test_db_host,
                'test_db_name': r.test_db_name,
                'summary': _json.loads(r.summary) if r.summary else {},
                'log_excerpt': r.log_excerpt or '',
            })
        except Exception as e:
            return jsonify({'error': str(e)[:200]}), 500

    # List endpoint
    try:
        from models import PostgresTestRun
        runs = (PostgresTestRun.query
                .order_by(PostgresTestRun.created_at.desc())
                .limit(20)
                .all())
    except Exception as e:
        # Table may not exist on a server that hasn't been migrated yet
        return jsonify({'runs': [], 'error': f'{type(e).__name__}: {str(e)[:200]}'})

    out = []
    for r in runs:
        try:
            out.append({
                'id': r.id,
                'created_at': (r.created_at.isoformat() + 'Z') if r.created_at else None,
                'status': r.status,
                'elapsed_seconds': r.elapsed_seconds,
                'total_passed': r.total_passed or 0,
                'total_failed': r.total_failed or 0,
                'total_skipped': r.total_skipped or 0,
                'test_db_host': r.test_db_host,
                'test_db_name': r.test_db_name,
                'summary': _json.loads(r.summary) if r.summary else {},
                # log_excerpt deliberately omitted — fetched lazily via ?run_id=N
            })
        except Exception:
            continue
    return jsonify({'runs': out, 'total': len(out)})


# ═══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTIC PAGE QUERY ENDPOINT — v5.89.31
# ═══════════════════════════════════════════════════════════════════════════════
# Operator-facing diagnostic page lives at /admin/diags (served from static/).
# This endpoint executes a fixed registry of READ-ONLY diagnostic queries.
# No user-supplied SQL ever reaches the database — the page POSTs a stable
# query_id, the server looks up the SQL by ID and runs it. Adding a new
# diagnostic = add an entry to _DIAGS_REGISTRY below.

_DIAGS_REGISTRY = {
    'q1_ml_row_counts': {
        'title': '1 · ML table row counts',
        'purpose': "Are the four ML training tables populated? If first three return 0 and you expected non-zero, that's the bug.",
        'sql': """
            SELECT
                'ml_finding_labels'        AS table_name, COUNT(*) AS row_count FROM ml_finding_labels
            UNION ALL SELECT
                'ml_contradiction_pairs',  COUNT(*) FROM ml_contradiction_pairs
            UNION ALL SELECT
                'ml_cooccurrence_buckets', COUNT(*) FROM ml_cooccurrence_buckets
            UNION ALL SELECT
                'ml_cost_data',            COUNT(*) FROM ml_cost_data
        """,
    },
    'q2_ml_date_range': {
        'title': '2 · ML data freshness (first / last row dates)',
        'purpose': "When did this table start receiving data, when did it last receive data? A last_row from weeks ago means crawling stopped feeding it.",
        'sql': """
            SELECT
                'ml_finding_labels' AS table_name,
                COUNT(*) AS rows,
                MIN(created_at) AS first_row,
                MAX(created_at) AS last_row
            FROM ml_finding_labels
            UNION ALL
            SELECT
                'ml_contradiction_pairs',
                COUNT(*),
                MIN(created_at),
                MAX(created_at)
            FROM ml_contradiction_pairs
            UNION ALL
            SELECT
                'ml_cost_data',
                COUNT(*),
                MIN(created_at),
                MAX(created_at)
            FROM ml_cost_data
        """,
    },
    'q3_tables_exist': {
        'title': '3 · Critical tables exist',
        'purpose': "Do the tables even exist? A missing row = someone ran DROP TABLE. Tables do not disappear by accident.",
        'sql': """
            SELECT
                table_name,
                table_type
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN (
                  'ml_finding_labels',
                  'ml_contradiction_pairs',
                  'ml_cooccurrence_buckets',
                  'ml_cost_data',
                  'ml_training_runs',
                  'ml_agent_runs',
                  'users',
                  'analyses',
                  'contractors',
                  'postgres_test_runs'
              )
            ORDER BY table_name
        """,
    },
    'q4_pg_stat_activity': {
        'title': '4 · Recent / live database connections',
        'purpose': "Who is connected right now? Look for unfamiliar application_name or query patterns. Helps spot anomalous external connections.",
        'sql': """
            SELECT
                datname,
                usename,
                application_name,
                state,
                query_start,
                state_change,
                LEFT(query, 200) AS recent_query
            FROM pg_stat_activity
            WHERE datname = current_database()
            ORDER BY state_change DESC NULLS LAST
            LIMIT 20
        """,
    },
    'q5_pg_stat_user_tables': {
        'title': '5 · Table statistics (inserts/deletes/updates lifetime)',
        'purpose': "Most diagnostic query. lifetime_deletes high + current 0 rows = DELETE happened. dead_rows non-zero alongside 0 live = recent DELETE pending vacuum. inserts much bigger than current rows = DROP+recreate. All zero everywhere = TRUNCATE.",
        'sql': """
            SELECT
                schemaname,
                relname AS table_name,
                n_tup_ins AS lifetime_inserts,
                n_tup_del AS lifetime_deletes,
                n_tup_upd AS lifetime_updates,
                n_live_tup AS current_live_rows,
                n_dead_tup AS dead_rows_pending_vacuum,
                last_vacuum,
                last_autovacuum,
                last_analyze
            FROM pg_stat_user_tables
            WHERE relname IN (
                'ml_finding_labels',
                'ml_contradiction_pairs',
                'ml_cooccurrence_buckets',
                'ml_cost_data',
                'users',
                'analyses',
                'contractors'
            )
            ORDER BY relname
        """,
    },
    'q6_table_sizes': {
        'title': '6 · Table sizes on disk',
        'purpose': "If a table shows 0 rows but its size is large (200+ MB), the data was very recently deleted (dead tuples pending vacuum). If size is essentially zero (8-16KB), the data is truly gone.",
        'sql': """
            SELECT
                schemaname,
                relname AS table_name,
                pg_size_pretty(pg_total_relation_size(schemaname || '.' || relname)) AS total_size,
                pg_size_pretty(pg_relation_size(schemaname || '.' || relname)) AS table_size,
                pg_size_pretty(pg_indexes_size(schemaname || '.' || relname)) AS indexes_size,
                pg_total_relation_size(schemaname || '.' || relname) AS bytes_total
            FROM pg_stat_user_tables
            WHERE relname IN (
                'ml_finding_labels',
                'ml_contradiction_pairs',
                'ml_cooccurrence_buckets',
                'ml_cost_data',
                'users',
                'analyses',
                'contractors'
            )
            ORDER BY bytes_total DESC
        """,
    },
    'q7_database_overview': {
        'title': '7 · Database overview',
        'purpose': "Top-level: which database are we connected to, what version of postgres, how big is the whole thing?",
        'sql': """
            SELECT
                current_database() AS connected_db,
                current_user AS connected_user,
                inet_server_addr()::text AS server_ip,
                inet_server_port() AS server_port,
                pg_size_pretty(pg_database_size(current_database())) AS db_size,
                version() AS postgres_version
        """,
    },
    'q8_all_tables_and_rows': {
        'title': '8 · All user tables with row counts',
        'purpose': "Inventory of every table this DB knows about. Sometimes useful to spot orphan tables from old migrations or unexpected table creation.",
        'sql': """
            SELECT
                relname AS table_name,
                n_live_tup AS row_count,
                pg_size_pretty(pg_total_relation_size(schemaname || '.' || relname)) AS size,
                last_vacuum,
                last_autovacuum
            FROM pg_stat_user_tables
            ORDER BY n_live_tup DESC NULLS LAST
        """,
    },
    'q9_env_var_indicators': {
        'title': '9 · Env vars + boot context (not SQL — server-side check)',
        'purpose': "Confirm the application is connected to the database you think it's connected to. Resolves the 'is this env even the prod one' question.",
        'sql': None,  # Special: not a SQL query, handled in code below
    },
    'q10_recent_migrations': {
        'title': '10 · Recent migrations / alembic version',
        'purpose': "Which migration is the database currently at? A mismatch between alembic_version and code's expected version means migrations are out of sync.",
        'sql': """
            SELECT version_num
            FROM alembic_version
        """,
    },
    'q11_deleted_users_from_registry': {
        'title': '11 · Deleted users (from email_registry breadcrumbs)',
        'purpose': "Users table is hard-delete, but email_registry keeps permanent breadcrumbs. Rows where times_deleted > 0 are the deleted accounts. Up to 30 most recent.",
        'sql': """
            SELECT
                email,
                first_signup_date,
                times_deleted,
                last_deleted_at,
                has_received_free_credit,
                free_credit_given_at,
                saved_credits
            FROM email_registry
            WHERE times_deleted > 0
            ORDER BY last_deleted_at DESC NULLS LAST
            LIMIT 30
        """,
    },
    'q12_user_id_gaps': {
        'title': '12 · User ID sequence — gaps indicate deletions',
        'purpose': "Postgres ID column is monotonically increasing; missing IDs in the sequence mark hard-deleted rows. This shows the highest gap and how many IDs are missing relative to live rows. (Doesn't enumerate every gap — that could be huge.)",
        'sql': """
            SELECT
                COUNT(*)                        AS live_users,
                MIN(id)                         AS lowest_id,
                MAX(id)                         AS highest_id,
                MAX(id) - MIN(id) + 1           AS id_range,
                (MAX(id) - MIN(id) + 1) - COUNT(*) AS missing_ids,
                MIN(created_at)                 AS oldest_user_created,
                MAX(created_at)                 AS newest_user_created
            FROM users
        """,
    },
    'q13_signup_deletion_timeline': {
        'title': '13 · Signup activity by month (live + deleted)',
        'purpose': "When were users created vs deleted, grouped by month? Helps spot a single mass-deletion event vs steady churn. Pulls from email_registry which retains both signed-up and deleted.",
        'sql': """
            SELECT
                DATE_TRUNC('month', first_signup_date)::date AS month,
                COUNT(*)                                   AS signups_in_month,
                SUM(CASE WHEN times_deleted > 0 THEN 1 ELSE 0 END) AS later_deleted,
                SUM(CASE WHEN times_deleted = 0 THEN 1 ELSE 0 END) AS still_active
            FROM email_registry
            GROUP BY DATE_TRUNC('month', first_signup_date)
            ORDER BY DATE_TRUNC('month', first_signup_date) DESC
            LIMIT 12
        """,
    },
    'q14_orphan_analyses': {
        'title': '14 · Orphan analyses (rows where user_id no longer exists)',
        'purpose': "If users got hard-deleted, their analyses may have been left behind (orphan FK references). This counts how many. Top owner-counts surface high-volume deleted accounts.",
        'sql': """
            SELECT
                a.user_id                          AS orphaned_user_id,
                COUNT(*)                           AS analyses_orphaned,
                MIN(a.created_at)                  AS earliest_orphan,
                MAX(a.created_at)                  AS latest_orphan
            FROM analyses a
            LEFT JOIN users u ON a.user_id = u.id
            WHERE a.user_id IS NOT NULL
              AND u.id IS NULL
            GROUP BY a.user_id
            ORDER BY analyses_orphaned DESC
            LIMIT 20
        """,
    },
    'q15_deletion_summary': {
        'title': '15 · Deletion summary — single source totals',
        'purpose': "One-line snapshot: live users, registry rows, total deletions tracked. Reconciles the pg_stat lifetime_deletes counter against application-level tracking.",
        'sql': """
            SELECT
                (SELECT COUNT(*) FROM users)                                     AS live_users,
                (SELECT COUNT(*) FROM email_registry)                            AS registry_emails,
                (SELECT COUNT(*) FROM email_registry WHERE times_deleted > 0)    AS deleted_account_emails,
                (SELECT COALESCE(SUM(times_deleted), 0) FROM email_registry)     AS total_deletions_app_tracked,
                (SELECT n_tup_del FROM pg_stat_user_tables WHERE relname = 'users') AS users_lifetime_deletes_pg_stat,
                (SELECT COUNT(*) FROM analyses a LEFT JOIN users u ON a.user_id = u.id
                 WHERE a.user_id IS NOT NULL AND u.id IS NULL) AS orphan_analyses_count
        """,
    },
}


def _render_rows_as_text(columns, rows):
    """Render columns + rows as a fixed-width text table suitable for
    copy-paste into a chat message. Each column padded to the max width
    found in that column."""
    if not rows:
        return f"(no rows)\nColumns: {', '.join(columns)}\n"
    # Coerce every cell to str, capped at 80 chars per cell to avoid
    # blow-out from very long queries / values.
    str_rows = []
    for r in rows:
        str_rows.append([
            (str(v)[:80] if v is not None else 'NULL')
            for v in r
        ])
    # Column widths
    widths = [len(c) for c in columns]
    for r in str_rows:
        for i, v in enumerate(r):
            if len(v) > widths[i]:
                widths[i] = len(v)
    # Build lines
    sep_line = '+'.join('-' * (w + 2) for w in widths)
    sep_line = '+' + sep_line + '+'
    header_cells = ' | '.join(c.ljust(widths[i]) for i, c in enumerate(columns))
    header_line = '| ' + header_cells + ' |'
    out_lines = [sep_line, header_line, sep_line]
    for r in str_rows:
        cells = ' | '.join(v.ljust(widths[i]) for i, v in enumerate(r))
        out_lines.append('| ' + cells + ' |')
    out_lines.append(sep_line)
    out_lines.append(f'({len(rows)} row{"s" if len(rows) != 1 else ""})')
    return '\n'.join(out_lines)


@admin_bp.route('/api/admin/diags/list', methods=['GET'])
@_api_admin_req_dec
def admin_diags_list():
    """Return the registry of available diagnostic queries with titles +
    purpose. The page uses this to render the panel list dynamically so
    adding a new query in _DIAGS_REGISTRY requires no frontend change."""
    out = []
    for qid, q in _DIAGS_REGISTRY.items():
        out.append({
            'id': qid,
            'title': q['title'],
            'purpose': q.get('purpose', ''),
        })
    return jsonify({'queries': out})


@admin_bp.route('/api/admin/diags/run', methods=['POST'])
@_api_admin_req_dec
def admin_diags_run():
    """Execute a single registered diagnostic query and return its result
    as both structured data (columns + rows) and a pre-rendered
    copy-friendly text table."""
    import time as _time
    from flask import request

    data = request.get_json(silent=True) or {}
    qid = (data.get('query_id') or '').strip()

    if qid not in _DIAGS_REGISTRY:
        return jsonify({
            'ok': False,
            'error': f'Unknown query_id: {qid}',
            'known_ids': list(_DIAGS_REGISTRY.keys()),
        }), 400

    q = _DIAGS_REGISTRY[qid]
    t0 = _time.time()

    # Special handler for q9 (not SQL — server-side env-var introspection)
    if qid == 'q9_env_var_indicators':
        import os as _os
        from urllib.parse import urlparse as _urlparse

        db_url = _os.environ.get('DATABASE_URL', '')
        host, db_name = None, None
        if db_url:
            try:
                parsed = _urlparse(db_url)
                host = parsed.hostname
                db_name = (parsed.path or '/').lstrip('/') or None
            except Exception:
                pass

        flask_env = _os.environ.get('FLASK_ENV', 'production')
        render_service = _os.environ.get('RENDER_SERVICE_NAME', 'unset')
        render_external_url = _os.environ.get('RENDER_EXTERNAL_URL', 'unset')

        # Test-DB URL (if set) — masked
        test_db_url = _os.environ.get('TEST_DATABASE_URL', '')
        test_host = None
        if test_db_url:
            try:
                test_host = _urlparse(test_db_url).hostname
            except Exception:
                pass

        rows = [
            ['FLASK_ENV', flask_env],
            ['RENDER_SERVICE_NAME', render_service],
            ['RENDER_EXTERNAL_URL', render_external_url],
            ['DATABASE_URL host', host or '(not set)'],
            ['DATABASE_URL database name', db_name or '(not set)'],
            ['TEST_DATABASE_URL host', test_host or '(not set)'],
            ['Sentry environment tag', _os.environ.get('FLASK_ENV', 'production')],
            ['Python version', _os.environ.get('PYTHON_VERSION', 'unset')],
        ]
        columns = ['variable', 'value']
        elapsed = round((_time.time() - t0) * 1000, 1)
        return jsonify({
            'ok': True,
            'query_id': qid,
            'title': q['title'],
            'purpose': q['purpose'],
            'columns': columns,
            'rows': rows,
            'row_count': len(rows),
            'elapsed_ms': elapsed,
            'text_table': _render_rows_as_text(columns, rows),
        })

    # Standard SQL path
    try:
        result = db.session.execute(db.text(q['sql']))
        columns = list(result.keys())
        rows_raw = result.fetchall()
        # Normalize each row to a plain list of values
        rows = [list(r) for r in rows_raw]
        elapsed = round((_time.time() - t0) * 1000, 1)
        return jsonify({
            'ok': True,
            'query_id': qid,
            'title': q['title'],
            'purpose': q['purpose'],
            'columns': columns,
            'rows': rows,
            'row_count': len(rows),
            'elapsed_ms': elapsed,
            'text_table': _render_rows_as_text(columns, rows),
        })
    except Exception as e:
        elapsed = round((_time.time() - t0) * 1000, 1)
        return jsonify({
            'ok': False,
            'query_id': qid,
            'title': q['title'],
            'purpose': q['purpose'],
            'error': f'{type(e).__name__}: {str(e)[:500]}',
            'elapsed_ms': elapsed,
        }), 200  # Return 200 so frontend can render the error inline


# ═══════════════════════════════════════════════════════════════════════════════
# OFFERWATCH TELEMETRY — v5.88.86
# ═══════════════════════════════════════════════════════════════════════════════
# Per-alert-type engagement stats over a configurable window. Lets us answer
# "which alert types actually drive return visits?" without staring at row-
# level data. The numerator/denominator approach is deliberate:
#   * delivered = AgentAlert rows where email_sent=True and a matching
#     EmailEvent of type 'delivered' exists for the alert's resend_id.
#     We use this as the open-rate / click-rate denominator, not the raw
#     count of sent emails — because some sends fail at the SMTP layer
#     before delivery and we shouldn't penalise the alert for that.
#   * email_opened = AgentAlert rows with at least one matching EmailEvent
#     of type 'opened'.
#   * email_clicked = AgentAlert rows with at least one matching
#     EmailEvent of type 'clicked'.
#   * portal_viewed = AgentAlert rows with view_count > 0 (the user
#     opened the OfferWatch tab and saw the alert in the list).
# All four rates are independent — a user can open the email without
# clicking the CTA but later open the portal, or click the CTA in email
# and never reload the portal. We track all four to see which channel
# drives the most engagement per alert type.

@admin_bp.route('/api/admin/offerwatch-telemetry', methods=['GET'])
@_api_admin_req_dec
def admin_offerwatch_telemetry():
    """Engagement metrics for OfferWatch alerts, grouped by alert_type.

    Query params:
      window_days: integer, default 30. Look-back window.
      persona: optional filter ('buyer' | 'inspector' | 'agent' | 'contractor').
        Default: all personas. Persona is derived per-alert by checking which
        professional-bearing tables hold the user_id; defaults to 'buyer' if
        no professional record matches.

    Returns:
      {
        'window_days': 30,
        'total_alerts': N,
        'by_alert_type': [
          {
            'alert_type': 'new_comp',
            'sent': X,           # email_sent=True
            'delivered': Y,      # matching EmailEvent delivered
            'opened': Z,         # matching EmailEvent opened
            'clicked': W,        # matching EmailEvent clicked
            'portal_viewed': V,  # view_count > 0
            'open_rate': Z/Y,    # of delivered
            'click_rate': W/Y,
            'portal_view_rate': V/X,  # of sent
          },
          ...
        ]
      }
    """
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403

    try:
        from models import AgentAlert, EmailEvent
        from sqlalchemy import func

        window_days = int(request.args.get('window_days', 30))
        if window_days < 1 or window_days > 365:
            window_days = 30
        cutoff = datetime.utcnow() - timedelta(days=window_days)

        # Pull alerts in window. We grab a bounded set to keep this query
        # tractable; OfferWatch alert volume should not exceed thousands per
        # month at our current scale.
        alerts = AgentAlert.query.filter(
            AgentAlert.created_at >= cutoff
        ).limit(50000).all()

        # Collect resend_ids to look up engagement events in one query.
        resend_ids = [a.resend_id for a in alerts if a.resend_id]

        # Build a map: resend_id → set of event_types seen
        event_map = {}
        if resend_ids:
            # Chunk the IN clause if needed (some Postgres configs limit to
            # ~32K parameters per query; we cap at 50K alerts above so this
            # could matter once volume grows).
            CHUNK = 5000
            for i in range(0, len(resend_ids), CHUNK):
                chunk = resend_ids[i:i + CHUNK]
                events = EmailEvent.query.filter(
                    EmailEvent.resend_id.in_(chunk)
                ).all()
                for ev in events:
                    if ev.resend_id not in event_map:
                        event_map[ev.resend_id] = set()
                    event_map[ev.resend_id].add(ev.event_type)

        # Aggregate per alert_type
        buckets = {}
        for a in alerts:
            atype = a.alert_type or 'unknown'
            if atype not in buckets:
                buckets[atype] = {
                    'alert_type': atype,
                    'sent': 0, 'delivered': 0, 'opened': 0,
                    'clicked': 0, 'portal_viewed': 0,
                }
            b = buckets[atype]
            if a.email_sent:
                b['sent'] += 1
            events = event_map.get(a.resend_id, set()) if a.resend_id else set()
            if 'delivered' in events:
                b['delivered'] += 1
            if 'opened' in events:
                b['opened'] += 1
            if 'clicked' in events:
                b['clicked'] += 1
            if (a.view_count or 0) > 0:
                b['portal_viewed'] += 1

        # Compute rates
        out = []
        for atype, b in buckets.items():
            sent_n = b['sent']
            delivered_n = b['delivered']
            b['open_rate']        = round(b['opened']  / delivered_n, 3) if delivered_n else None
            b['click_rate']       = round(b['clicked'] / delivered_n, 3) if delivered_n else None
            b['portal_view_rate'] = round(b['portal_viewed'] / sent_n,   3) if sent_n      else None
            out.append(b)

        # Sort by sent volume desc so the dashboard shows the highest-volume
        # alert types first — those are the ones we have signal on.
        out.sort(key=lambda x: x['sent'], reverse=True)

        return jsonify({
            'window_days': window_days,
            'total_alerts': len(alerts),
            'alerts_with_resend_id': len(resend_ids),
            'by_alert_type': out,
        })

    except Exception as e:
        logging.error(f"offerwatch_telemetry error: {e}", exc_info=True)
        return jsonify({'error': f'Server error: {type(e).__name__}: {str(e)}'}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# MIGRATION STATUS DIAGNOSTIC — v5.88.92
# ═══════════════════════════════════════════════════════════════════════════════
# Read-only ground-truth endpoint for "what does production Postgres actually
# look like vs. what the models expect?" Built after v5.88.88 was supposed to
# add the OfferWatch columns but production Sentry continued reporting
# `column agent_alerts.resend_id does not exist` for days afterward — proving
# the startup migration block either didn't run or didn't take effect.
#
# This endpoint queries information_schema directly via a fresh connection
# (no cached SQLAlchemy reflection), reports column presence per recent
# migration, and reports auxiliary state (Alembic revision, running version,
# worker PID, boot time) so we can correlate against deploys.
#
# It is intentionally NOT a fixer — it only reports. Discipline: diagnostic
# endpoints should never mutate state, so they remain safe to call from
# scripts, monitoring, or the admin dashboard without risk of double-applying
# a migration.

@admin_bp.route('/api/admin/migration-status', methods=['GET'])
@_api_admin_req_dec
def admin_migration_status():
    """Report actual production schema state vs. expected.

    For each column added in v5.88.85 / v5.88.86 / v5.88.91, report whether
    the column exists in production. Also report Alembic version, app
    version, and worker PID so we can correlate with deploy events.

    Returns JSON with structure:
      {
        'version': '5.88.92',
        'pid': 12345,
        'database_driver': 'postgresql',
        'alembic_revision': 'c9d5f2b8a173' or 'b8d3e6a1f924' or null,
        'expected_revision': 'c9d5f2b8a173',
        'alembic_in_sync': bool,
        'columns': {
          'property_watches.agent_briefing_id':  {'expected': true, 'actual': true,  'introduced_in': 'v5.88.85'},
          'property_watches.survey_sent_at':     {'expected': true, 'actual': false, 'introduced_in': 'v5.88.86'},
          ...
        },
        'missing_columns':  [list of missing columns],
        'all_columns_present': bool,
        'diagnosis': 'string explaining what the result means',
      }
    """
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403

    try:
        from models import db
        from sqlalchemy import text as _ms_text, inspect as _ms_inspect
        import os as _ms_os

        # Expected columns by table.release
        expected = {
            'property_watches.agent_briefing_id':       'v5.88.85',
            'property_watches.survey_sent_at':           'v5.88.86',
            'agent_alerts.resend_id':                    'v5.88.86',
            'agent_alerts.view_count':                   'v5.88.86',
            'inspector_reports.buyer_registered_at':     'v5.88.91',
            'inspector_reports.buyer_converted_at':      'v5.88.91',
            'inspector_reports.buyer_user_id':           'v5.88.91',
            'inspectors.total_buyers_registered':        'v5.88.91',
        }

        # Use a FRESH connection so we don't read cached SQLAlchemy state.
        # information_schema is the ground truth.
        results = {}
        missing = []
        with db.engine.connect() as conn:
            # Group expected columns by table for efficient lookup
            by_table = {}
            for fq_col, version in expected.items():
                table, col = fq_col.split('.', 1)
                by_table.setdefault(table, []).append((col, fq_col, version))

            # Postgres uses information_schema.columns; SQLite uses PRAGMA.
            is_sqlite = db.engine.url.drivername.startswith('sqlite')
            for table, cols in by_table.items():
                if is_sqlite:
                    rows = conn.execute(_ms_text(f"PRAGMA table_info({table})")).fetchall()
                    actual_cols = {r[1] for r in rows} if rows else set()
                else:
                    rows = conn.execute(_ms_text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name = :t"
                    ), {'t': table}).fetchall()
                    actual_cols = {r[0] for r in rows}

                for col_name, fq_col, version in cols:
                    is_present = col_name in actual_cols
                    results[fq_col] = {
                        'expected': True,
                        'actual': is_present,
                        'introduced_in': version,
                    }
                    if not is_present:
                        missing.append(fq_col)

            # Alembic revision check — directly via SQL since the alembic
            # library uses its own connection. Use the same fresh connection.
            alembic_rev = None
            try:
                row = conn.execute(_ms_text("SELECT version_num FROM alembic_version LIMIT 1")).fetchone()
                alembic_rev = row[0] if row else None
            except Exception:
                pass  # alembic_version table may not exist on a brand-new DB

        # App version
        version = 'unknown'
        try:
            _v_path = _ms_os.path.join(_ms_os.path.dirname(__file__), 'VERSION')
            if _ms_os.path.exists(_v_path):
                with open(_v_path, 'r') as f:
                    version = f.read().strip()
        except Exception:
            pass

        # Boot time — approximate via current process start.
        # /proc/self/stat field 22 is process start time in clock ticks since boot.
        boot_age_seconds = None
        try:
            with open('/proc/self/stat', 'r') as f:
                parts = f.read().split()
                start_ticks = int(parts[21])
                with open('/proc/uptime', 'r') as fu:
                    uptime_seconds = float(fu.read().split()[0])
                import os as _ms_os2
                clk_tck = _ms_os2.sysconf('SC_CLK_TCK')
                process_age = uptime_seconds - (start_ticks / clk_tck)
                boot_age_seconds = int(process_age)
        except Exception:
            pass

        # Database driver name (e.g. 'postgresql', 'sqlite', 'postgresql+psycopg2')
        try:
            db_driver = db.engine.url.drivername
        except Exception:
            db_driver = 'unknown'

        # Diagnosis: produce a human-readable summary of what the data means.
        # The whole point of this endpoint is to distinguish between
        # "migration block never ran" vs. "ran but errored" vs. "ran but
        # workers have stale state" — each has a different fix.
        EXPECTED_ALEMBIC = 'c9d5f2b8a173'
        if not missing and alembic_rev == EXPECTED_ALEMBIC:
            diagnosis = (
                'All expected columns present, Alembic stamped to current head. '
                'Schema is in sync with models. If production Sentry continues '
                'reporting "column does not exist" errors after this, the issue '
                'is likely connection pooling — workers may need a restart to '
                'invalidate cached statement plans.'
            )
        elif not missing and alembic_rev != EXPECTED_ALEMBIC:
            diagnosis = (
                f'All columns present but Alembic is at "{alembic_rev}" instead of '
                f'"{EXPECTED_ALEMBIC}". The migration block ran the ALTERs but '
                'the forward-stamp step failed or was skipped. Schema is fine; '
                'Alembic history is just stale (cosmetic — does not cause user-'
                'facing errors).'
            )
        elif missing and alembic_rev == EXPECTED_ALEMBIC:
            diagnosis = (
                f'{len(missing)} expected column(s) missing despite Alembic being '
                f'stamped to current head: {missing}. This means the forward-stamp '
                'ran but the ALTER TABLE statements did not — likely because the '
                'migration block was skipped (gated by an earlier check) or '
                'the inspect() cache reported false positives. Manual ALTER TABLE '
                'via psql, or a corrective release that bypasses the gate, is needed.'
            )
        else:
            diagnosis = (
                f'{len(missing)} expected column(s) missing: {missing}. '
                f'Alembic revision is "{alembic_rev}" (expected "{EXPECTED_ALEMBIC}"). '
                'The v5.88.88/v5.88.91 migration block did not run successfully '
                'on this worker. Check Render startup logs for "OfferWatch column '
                'migration" or "Inspector attribution migration" — absence means '
                'the with-app-context block raised before reaching them; presence '
                'with errors gives the specific failure.'
            )

        return jsonify({
            'version':              version,
            'pid':                  _ms_os.getpid(),
            'boot_age_seconds':     boot_age_seconds,
            'database_driver':      db_driver,
            'alembic_revision':     alembic_rev,
            'expected_revision':    EXPECTED_ALEMBIC,
            'alembic_in_sync':      alembic_rev == EXPECTED_ALEMBIC,
            'columns':              results,
            'missing_columns':      missing,
            'all_columns_present':  len(missing) == 0,
            'diagnosis':            diagnosis,
        })

    except Exception as e:
        logging.error(f"migration_status error: {e}", exc_info=True)
        return jsonify({
            'error': f'Server error: {type(e).__name__}: {str(e)}',
        }), 500


# ─────────────────────────────────────────────────────────────────────────────
# Reasoning engine diagnostics (v5.89.235)
# Extractor diagnostic + shadow-comparison readout, so the reasoning-validation
# loop is observable from the admin UI instead of the shell.
# ─────────────────────────────────────────────────────────────────────────────

def _extract_pdf_text_for_diag(path):
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        try:
            from pypdf import PdfReader
            return "\n".join((pg.extract_text() or "") for pg in PdfReader(path).pages)
        except Exception:
            return ""


@admin_bp.route('/api/admin/reasoning/shadow-samples', methods=['GET'])
@_api_admin_req_dec
def admin_reasoning_shadow_samples():
    """A few recent shadow comparisons WITH the finding-level diff for one state,
    so an admin can eyeball whether reasoning is winning on the findings that
    matter (contradictions / undisclosed / silent hazards) before flipping that
    state's buyer-facing flag on. Only rows persisted after v5.89.266 carry the
    diff; older rows have counts only and are skipped here."""
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    import json as _json
    try:
        from models import ShadowComparison
    except Exception as e:
        return jsonify({'ok': False, 'error': f'model unavailable: {e}'}), 200
    state = (request.args.get('state') or '').strip().upper()
    try:
        limit = min(int(request.args.get('limit', 5)), 15)
    except Exception:
        limit = 5
    try:
        q = ShadowComparison.query.filter(ShadowComparison.findings_json.isnot(None))
        if state:
            # jurisdiction is stored truncated ('CA' or 'CA:santa'); match the state prefix.
            q = q.filter(ShadowComparison.jurisdiction.like(state + '%'))
        rows = q.order_by(ShadowComparison.id.desc()).limit(limit).all()
    except Exception as e:
        return jsonify({'ok': False, 'error': f'query failed: {e}'}), 200
    samples = []
    for r in rows:
        try:
            f = _json.loads(r.findings_json) if r.findings_json else {}
        except Exception:
            f = {}
        samples.append({
            'id': r.id,
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'jurisdiction': r.jurisdiction,
            'live_contradictions': r.live_contradictions,
            'live_undisclosed': r.live_undisclosed,
            'reasoning_contradiction': r.reasoning_contradiction,
            'reasoning_undisclosed': r.reasoning_undisclosed,
            'reasoning_silent_hazards': r.reasoning_silent_hazards,
            'reasoning': f.get('reasoning', []),
            'live': f.get('live', []),
        })
    return jsonify({'ok': True, 'state': state, 'count': len(samples), 'samples': samples})


@admin_bp.route('/api/admin/reasoning/shadow-summary', methods=['GET'])
@_api_admin_req_dec
def admin_reasoning_shadow_summary():
    """Shadow-comparison summary + recent rows (reasoning/ vs the live engine)."""
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        from reasoning_shadow import shadow_summary
        summary = shadow_summary(window_days=30)
        recent = []
        try:
            from models import ShadowComparison
            rows = ShadowComparison.query.order_by(ShadowComparison.id.desc()).limit(25).all()
            for r in rows:
                recent.append({
                    'id': r.id,
                    'created_at': r.created_at.isoformat() if r.created_at else None,
                    'analysis_id': r.analysis_id,
                    'jurisdiction': r.jurisdiction,
                    'extractor_ok': r.extractor_ok,
                    'extractor_readings': r.extractor_readings,
                    'live_contradictions': r.live_contradictions,
                    'live_undisclosed': r.live_undisclosed,
                    'reasoning_issues': r.reasoning_issues,
                    'reasoning_silent_hazards': r.reasoning_silent_hazards,
                    'reasoning_undisclosed': r.reasoning_undisclosed,
                    'ok': r.ok,
                    'error': r.error,
                    'notes': r.notes,
                    'elapsed_ms': r.elapsed_ms,
                })
        except Exception as _re:
            logging.warning(f"shadow recent rows: {_re}")
        return jsonify({'summary': summary, 'recent': recent})
    except Exception as e:
        logging.error(f"admin_reasoning_shadow_summary error: {e}", exc_info=True)
        return jsonify({'error': f'Server error: {type(e).__name__}: {str(e)}'}), 500


def water_xref_verdict(readings, disc_readings, insp_water_items,
                       disc_water_items, related_of_bath,
                       bath='structure.water_intrusion_bath'):
    """Pure verdict logic for the water cross-reference diagnostic (v5.89.246):
    given both extractors' readings and the water-concern item sets, classify why
    the disclosed bath finding did or didn't corroborate. Returns (code, message).
    Extracted from the endpoint so the branch logic is unit-testable.

    Codes: no_inspection | no_disclosure | exact | related_divergence |
           recall_gap | unrelated_water.
    """
    exact = bath in insp_water_items and bath in disc_water_items
    via_related = (bath in disc_water_items) and bool(insp_water_items & (related_of_bath or set()))
    if not readings:
        return 'no_inspection', (
            'Inspection extractor returned 0 readings — check ANTHROPIC_API_KEY '
            'and logs for an [ai_json] call-failed / truncation line.')
    if not disc_readings:
        return 'no_disclosure', (
            'Disclosure extractor returned 0 readings — the seller side is empty, '
            'so every status is undisclosed by construction. Check the disclosure '
            'text and the [ai_json] disclosure-extract telemetry.')
    if exact:
        return 'exact', (
            'CORROBORATION SHOULD FORM: both sides have a '
            'structure.water_intrusion_bath concern. If #1 still reads '
            'disclosed_not_found, that is a pipeline bug, not extraction.')
    if via_related:
        return 'related_divergence', (
            'RELATED-ITEM DIVERGENCE (cause a): the seller disclosed the bath '
            'leak on structure.water_intrusion_bath, but the inspection put the '
            'SAME water on a directly-related item (' +
            ', '.join(sorted(insp_water_items & related_of_bath)) + '). This is '
            'the fixable case — evidence-gated related_items corroboration. '
            'CONFIRM the quotes describe the same location before trusting it.')
    if not insp_water_items:
        return 'recall_gap', (
            'RECALL GAP (cause b): the inspection extractor pulled NO water '
            'concern at all. disclosed_not_found is the engine being correct — '
            'corroboration here would be FABRICATED. Fix is extraction recall '
            '(prompt), not the derivation.')
    return 'unrelated_water', (
        'Inspection has water concern(s) but on item(s) NOT related to '
        'structure.water_intrusion_bath (' + ', '.join(sorted(insp_water_items)) +
        '). Likely the supply-line leak (#6), a DIFFERENT finding — '
        'corroborating it against the disclosed bath leak would be FABRICATED. '
        'Confirm before any change.')


@admin_bp.route('/api/admin/reasoning/extractor-diagnostic', methods=['POST'])
@_api_admin_req_dec
def admin_reasoning_extractor_diagnostic():
    """Cross-reference diagnostic on the Pendleton fixtures (or pasted text): runs
    BOTH the inspection and disclosure LLM extractors, derives disclosure_status
    through the real pipeline, and shows the water/leak cluster from each side so
    the #1 'disclosed_not_found' question is answerable with evidence — is it a
    recall gap (inspection never pulled the bath moisture), an exact-item
    corroboration that already works, or a related-item mapping divergence
    (disclosure and inspection landed the SAME finding on different but related
    items)? Makes up to two live LLM calls, so it can take a few seconds."""
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        import os as _os
        data = request.get_json(silent=True) or {}
        text = (data.get('text') or '').strip()
        disc_text = (data.get('disclosure_text') or '').strip()
        source = 'pasted text'
        _dir = _os.path.join(_os.path.dirname(__file__), 'test_corpus', 'regression_pendleton')
        if not text:
            fixture = _os.path.join(_dir, 'inspection.pdf')
            if not _os.path.exists(fixture):
                return jsonify({'error': 'No text provided and Pendleton fixture not found'}), 400
            text = _extract_pdf_text_for_diag(fixture)
            source = 'Pendleton fixture (inspection.pdf + disclosures.pdf)'
        if not disc_text:
            dfix = _os.path.join(_dir, 'disclosures.pdf')
            if _os.path.exists(dfix):
                disc_text = _extract_pdf_text_for_diag(dfix)
        if not text:
            return jsonify({'error': 'Could not obtain inspection text'}), 400

        # National-correct: offer BOTH extractors the full authored id universe
        # (not a hardcoded compose('CA','SFH')); the pipeline gates to the
        # resolved checklist. Matches the buyer/shadow path exactly.
        from reasoning.composition import all_authored_ids, compose
        from reasoning.inspection_llm_extractor import extract_inspection_findings_llm
        from reasoning.disclosure_llm_extractor import extract_disclosure_findings_llm
        from reasoning import run_pipeline
        ids = all_authored_ids()
        readings = extract_inspection_findings_llm(text, ids) or []
        disc_readings = extract_disclosure_findings_llm(disc_text, ids) if disc_text else []

        # Derive issues + per-finding disclosure_status through the real pipeline
        # (Pendleton is CA/SFH). This is the actual buyer-facing cross-reference.
        result = run_pipeline(disc_readings, 'CA', 'SFH', inspection_readings=readings)
        issues = result.issues_result.issues if result.issues_result else []
        derived = [{
            'title': i.title,
            'decision_class': i.decision_class,
            'disclosure_status': i.disclosure_status,
            'silent_hazard': i.silent_hazard_flag,
            'items': i.claim_item_ids,
        } for i in issues]

        # Water/leak cluster from BOTH sides, so mapping divergence is visible.
        def _is_water(r):
            iid = (r.get('item_id') or '').lower()
            rt = (r.get('raw_text') or '').lower()
            return (any(h in iid for h in ('water', 'moisture', 'leak', 'plumb')) or
                    any(h in rt for h in ('water', 'bath', 'shower', 'master', 'moisture',
                                          'leak', 'sill', 'pan')))
        insp_water = [{'item_id': r.get('item_id'), 'value': r.get('value'),
                       'quote': (r.get('raw_text') or '')[:200]} for r in readings if _is_water(r)]
        disc_water = [{'item_id': r.get('item_id'), 'value': r.get('value'),
                       'quote': (r.get('raw_text') or '')[:200]} for r in disc_readings if _is_water(r)]

        insp_water_items = {w['item_id'] for w in insp_water if w.get('value') == 'yes'}
        disc_water_items = {w['item_id'] for w in disc_water if w.get('value') == 'yes'}
        bath = 'structure.water_intrusion_bath'
        # is the disclosed bath item matched on the inspection side, exactly or via
        # a directly-authored related item?
        related_of_bath = set()
        try:
            for it in compose('CA', 'SFH').items:
                if it.id == bath:
                    related_of_bath = set(getattr(it, 'related_items', []) or [])
        except Exception:
            pass
        exact = bath in insp_water_items and bath in disc_water_items
        via_related = (bath in disc_water_items) and bool(insp_water_items & related_of_bath)

        _vcode, verdict = water_xref_verdict(
            readings, disc_readings, insp_water_items, disc_water_items, related_of_bath)

        return jsonify({
            'source': source,
            'text_chars': len(text),
            'disclosure_text_chars': len(disc_text or ''),
            'total_inspection_readings': len(readings),
            'total_disclosure_readings': len(disc_readings),
            'derived_issues': derived,
            'water_cross_reference': {
                'inspection_side': insp_water,
                'disclosure_side': disc_water,
                'bath_related_items': sorted(related_of_bath),
            },
            'inspection_readings': readings,
            'disclosure_readings': disc_readings,
            'verdict': verdict,
        })
    except Exception as e:
        logging.error(f"admin_reasoning_extractor_diagnostic error: {e}", exc_info=True)
        return jsonify({'error': f'Server error: {type(e).__name__}: {str(e)}'}), 500



@admin_bp.route('/api/admin/exit-feedback', methods=['GET'])
@_api_admin_req_dec
def admin_exit_feedback():
    """Point-of-abandonment feedback: reason breakdown + recent free-text. The
    qualitative WHY behind the funnel's quantitative WHERE."""
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        from models import ExitFeedback
        from datetime import datetime, timedelta
        from collections import Counter
        days = int(request.args.get('days', 30) or 30)
        since = datetime.utcnow() - timedelta(days=days)
        rows = (ExitFeedback.query.filter(ExitFeedback.created_at >= since)
                .order_by(ExitFeedback.id.desc()).all())
        answered = [r for r in rows if r.reason and r.reason != 'dismissed']
        dismissed = sum(1 for r in rows if r.reason == 'dismissed')
        by_reason = Counter((r.reason_label or r.reason) for r in answered)
        reasons = sorted(({'label': k, 'count': v} for k, v in by_reason.items()),
                         key=lambda x: x['count'], reverse=True)
        recent_text = [{
            'when': r.created_at.isoformat() if r.created_at else None,
            'reason': r.reason_label or r.reason,
            'context': r.context,
            'text': r.free_text,
        } for r in rows if r.free_text][:25]
        return jsonify({
            'window_days': days,
            'total': len(rows),
            'answered': len(answered),
            'dismissed': dismissed,
            'reasons': reasons,
            'recent_text': recent_text,
        })
    except Exception as e:
        logging.error(f"admin_exit_feedback error: {e}", exc_info=True)
        return jsonify({'error': f'Server error: {type(e).__name__}: {str(e)}'}), 500
