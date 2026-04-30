"""
OfferWise Admin Routes Blueprint
=================================
Extracted from app.py to reduce monolith size.
Contains all /api/admin/* and /admin/* routes.
"""

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
    InspectorReport, MarketSnapshot, ShareLink, SupportShare, Waitlist,
    APIKey, GTMAdPerformance, GTMFunnelEvent, InfraVendor, InfraInvoice,
    RepairCostBaseline, RepairCostZone, Agent, AgentShare, CreditTransaction,
    PropertyWatch, EmailSendLog, FeatureEvent, ListingPreference,
)

admin_bp = Blueprint('admin', __name__)
logger = logging.getLogger(__name__)

def _send_email(*args, **kwargs):
    """Lazy wrapper for app.send_email to avoid circular imports."""
    from app import send_email
    return send_email(*args, **kwargs)

# Lazy import helpers from app
def __send_email(*args, **kwargs):
    from app import send_email
    return _send_email(*args, **kwargs)

from blueprint_helpers import DeferredDecorator

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
        out = []
        for insp in inspectors:
            try:
                user = User.query.get(insp.user_id)
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

    # ── Inspector subscriptions ────────────────────────────────
    INSPECTOR_PRO_MRR = 49.0
    # Require a real Stripe subscription — excludes test accounts that were
    # flipped to 'inspector_pro' manually via admin/DB without paying.
    inspectors_pro = Inspector.query.filter_by(plan='inspector_pro').join(
        User, Inspector.user_id == User.id
    ).filter(
        ~User.email.endswith('@persona.offerwise.ai'),
        ~User.email.endswith('@test.offerwise.ai'),
        ~User.email.endswith('@persona.ai'),
        ~User.email.endswith('@getofferwise.ai'),
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
            ~User.email.endswith('@persona.offerwise.ai'),
            ~User.email.endswith('@test.offerwise.ai'),
            ~User.email.endswith('@getofferwise.ai'),
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



@admin_bp.route('/api/admin/run-market-intel', methods=['POST'])
@_api_admin_req_dec
def api_run_market_intel():
    """Manually trigger market intelligence run (admin only)."""
    from market_intelligence import run_nightly_intelligence
    stats = run_nightly_intelligence(db.session)
    return jsonify(stats)



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

    valid_channels = {'zillow_ads', 'google_ads', 'reddit_ads', 'facebook_ads', 'nextdoor'}
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
def admin_list_support_shares():
    """List all support shares. ?status=open|reviewed|resolved&limit=50"""
    from models import SupportShare
    status_filter = request.args.get('status', 'open')
    limit = min(int(request.args.get('limit', 50)), 200)

    query = SupportShare.query
    if status_filter and status_filter != 'all':
        query = query.filter_by(status=status_filter)
    shares = query.order_by(SupportShare.created_at.desc()).limit(limit).all()

    result = []
    for s in shares:
        user = User.query.get(s.user_id)
        snapshot = json.loads(s.snapshot_json or '{}')
        result.append({
            'id': s.id,
            'status': s.status,
            'created_at': s.created_at.isoformat(),
            'reviewed_at': s.reviewed_at.isoformat() if s.reviewed_at else None,
            'user_email': user.email if user else 'unknown',
            'user_name': user.name if user else None,
            'user_tier': user.tier if user else None,
            'property_id': s.property_id,
            'user_message': s.user_message,
            'snapshot': snapshot,
            'admin_notes': s.admin_notes,
        })

    return jsonify({'shares': result, 'total': len(result)})



@admin_bp.route('/api/admin/support-shares/<int:share_id>', methods=['GET'])
@_api_admin_req_dec
def admin_get_support_share(share_id):
    """Get full detail for a single support share including raw analysis JSON."""
    from models import SupportShare
    share = SupportShare.query.get(share_id)
    if not share:
        return jsonify({'error': 'Not found'}), 404

    user = User.query.get(share.user_id)
    snapshot = json.loads(share.snapshot_json or '{}')
    full_result = json.loads(share.full_result_json or '{}')
    findings = json.loads(share.findings_json or '[]')

    return jsonify({
        'id': share.id,
        'status': share.status,
        'created_at': share.created_at.isoformat(),
        'reviewed_at': share.reviewed_at.isoformat() if share.reviewed_at else None,
        'user': {
            'id': share.user_id,
            'email': user.email if user else 'unknown',
            'name': user.name if user else None,
            'tier': user.tier if user else None,
            'created_at': user.created_at.isoformat() if user else None,
        },
        'property_id': share.property_id,
        'user_message': share.user_message,
        'admin_notes': share.admin_notes,
        'snapshot': snapshot,
        'findings': findings,
        'full_result': full_result,
    })



@admin_bp.route('/api/admin/support-shares/<int:share_id>', methods=['PATCH'])
@_api_admin_req_dec
def admin_update_support_share(share_id):
    """Update status or admin notes on a support share."""
    from models import SupportShare
    share = SupportShare.query.get(share_id)
    if not share:
        return jsonify({'error': 'Not found'}), 404

    data = request.get_json()
    if 'status' in data and data['status'] in ('open', 'reviewed', 'resolved'):
        share.status = data['status']
        if data['status'] in ('reviewed', 'resolved') and not share.reviewed_at:
            share.reviewed_at = datetime.utcnow()
    if 'admin_notes' in data:
        share.admin_notes = (data['admin_notes'] or '').strip()[:2000] or None

    db.session.commit()
    return jsonify({'success': True, 'status': share.status})



@admin_bp.route('/api/admin/health-check', methods=['POST'])
@_api_admin_req_dec
def manual_health_check():
    """
    Manual database health check (optional)
    
    Automatically runs on startup, but can be triggered manually if needed.
    Only accessible to logged-in users for security.
    """
    try:
        logger.info(f"Manual health check triggered by user {current_user.id}")
        
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
        db_summary = get_cost_summary_from_db(days if days > 0 else 30)
        if db_summary and db_summary.get('total_calls', 0) > 0:
            db_summary['alerts'] = tracker.check_cost_alerts()
            db_summary['period'] = 'month' if days == 0 else (f'{days}d' if days > 1 else 'day')
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
    since = datetime.utcnow() - timedelta(days=days if days > 0 else 30)

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
    since = datetime.utcnow() - timedelta(days=days if days > 0 else 30)

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
        rentcast_total_calls = rentcast_nearby_calls + rentcast_analysis_calls
        # RentCast API pricing tiers (as of 2026):
        #   Foundation $74/mo → 1k included, $0.20/req overage
        #   Growth $199/mo → 5k included, $0.06/req overage
        #   Scale $449/mo → 25k included, $0.03/req overage
        # We estimate at Growth overage rate ($0.06) as a reasonable assumption
        rentcast_cost_est = round(rentcast_total_calls * 0.06, 2)

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
        reddit_ads_spend = None
        reddit_ads_clicks = None
        try:
            from models import GTMAdPerformance
            reddit_rows = GTMAdPerformance.query.filter(
                GTMAdPerformance.date >= since.date(),
                GTMAdPerformance.channel == 'reddit_ads'
            ).all()
            if reddit_rows:
                reddit_ads_spend  = float(sum(r.spend or 0 for r in reddit_rows))
                reddit_ads_clicks = sum(r.clicks or 0 for r in reddit_rows)
        except Exception:
            pass

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
_INFRA_DEFAULT_VENDORS = [
    dict(name='Render',          category='hosting',  logo_emoji='🚀', notes='Web service + PostgreSQL DB'),
    dict(name='Anthropic',       category='ai',       logo_emoji='🤖', notes='claude.ai subscription + Console API (dev usage)'),
    dict(name='Google Cloud',    category='platform', logo_emoji='☁️',  notes='Maps, Vision, GA4, Ads platform costs'),
    dict(name='Resend',          category='email',    logo_emoji='📧', notes='Transactional email — above free tier'),
    dict(name='RentCast',        category='data',     logo_emoji='🏠', notes='Property data API subscription'),
    dict(name='GitHub',          category='tooling',  logo_emoji='🐙', notes='Private repos / Teams plan'),
    dict(name='Porkbun',         category='domain',   logo_emoji='🌐', notes='Domain registration + DNS'),
    dict(name='WalkScore',       category='data',     logo_emoji='🚶', notes='Walk/Transit/Bike score API'),
    dict(name='Other',           category='other',    logo_emoji='💼', notes='Miscellaneous infrastructure'),
]



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



@admin_bp.route('/api/admin/infra/invoices', methods=['GET'])
@_api_admin_req_dec
def infra_invoices_list():
    """List invoices. ?vendor_id=&year=&months=12"""
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
    months = int(request.args.get('months', 12))
    cutoff = date.today().replace(day=1)
    for _ in range(months - 1):
        cutoff = (cutoff - timedelta(days=1)).replace(day=1)
    q = q.filter(InfraInvoice.period_start >= cutoff)
    invoices = q.order_by(InfraInvoice.period_start.desc()).all()
    return jsonify([inv.to_dict() for inv in invoices])



@admin_bp.route('/api/admin/infra/invoices/summary', methods=['GET'])
@_api_admin_req_dec
def infra_invoices_summary():
    """Monthly totals + per-vendor breakdown. ?months=12"""
    from models import InfraInvoice, InfraVendor
    from datetime import date, timedelta
    from collections import defaultdict
    try:
        from app import _ensure_infra_vendors as _eiv; _eiv()
    except Exception: pass
    months = int(request.args.get('months', 12))
    cutoff = date.today().replace(day=1)
    for _ in range(months - 1):
        cutoff = (cutoff - timedelta(days=1)).replace(day=1)
    invoices = (InfraInvoice.query
                .join(InfraVendor)
                .filter(InfraInvoice.period_start >= cutoff)
                .order_by(InfraInvoice.period_start)
                .all())
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
    avg_monthly = grand_total / months if months > 0 else 0
    return jsonify({
        'by_month': dict(by_month),
        'vendor_totals': dict(vendor_totals),
        'grand_total': round(grand_total, 2),
        'avg_monthly': round(avg_monthly, 2),
        'months': months,
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
    channel_map = {'google_ads': 'google', 'reddit_ads': 'reddit', 'zillow': 'zillow', 'zillow_display': 'zillow', 'nextdoor': 'nextdoor', 'internachi': 'internachi'}
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
    for paid_src in ('google', 'reddit', 'zillow', 'nextdoor', 'internachi'):
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

    # All buyer users ordered by signup date
    users = User.query.filter(
        User.tier != None
    ).order_by(User.created_at.desc()).limit(200).all()

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
    """List all buyer accounts eligible for the drip campaign."""
    from models import User
    EXCLUDE = {'francis@piotnetworks.com', 'francis.kurupacheril@gmail.com'}
    # Known bot/dummy patterns
    BOT_PATTERNS = ['test@', 'example.com', 'mailinator', 'guerrilla', 'tempmail',
                    'throwaway', 'fakeinbox', 'sharklasers', 'dispostable', 'yopmail']

    users = User.query.filter(
        ~User.email.endswith('@persona.offerwise.ai'),
        ~User.email.endswith('@test.offerwise.ai')
    ).order_by(User.created_at.desc()).limit(300).all()
    result = []
    for u in users:
        if u.email in EXCLUDE:
            continue
        email_lower = u.email.lower()
        likely_bot = any(p in email_lower for p in BOT_PATTERNS)
        result.append({
            'id': u.id,
            'email': u.email,
            'name': u.name or '',
            'joined': u.created_at.isoformat() if u.created_at else None,
            'last_login': u.last_login.isoformat() if u.last_login else None,
            'auth': u.auth_provider or 'email',
            'plan': getattr(u, 'subscription_plan', 'free') or 'free',
            'likely_bot': likely_bot,
        })
    return jsonify({'users': result, 'total': len(result)})


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
        users = User.query.filter(
        ~User.email.endswith('@persona.offerwise.ai'),
        ~User.email.endswith('@test.offerwise.ai')
    ).order_by(User.created_at.desc()).limit(300).all()
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
    data = request.get_json() or {}
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
    data = request.get_json() or {}
    to_email = (data.get('to_email') or '').strip()
    subject  = (data.get('subject')  or '').strip()
    body     = (data.get('body')     or '').strip()
    import os as _os
    reply_to = (data.get('reply_to') or _os.environ.get('ADMIN_EMAIL', 'hello@getofferwise.ai')).strip()

    if not to_email or not subject or not body:
        return jsonify({'error': 'to_email, subject, and body are required.'}), 400

    # Wrap plain-text body in minimal HTML
    html_lines = ''.join(
        f'<p style="margin:0 0 12px;color:#e2e8f0;font-size:14px;line-height:1.65;">{line}</p>'
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
    """List B2B prospects with engagement on most-recent send."""
    from models import OutreachContact, OutreachLog
    from sqlalchemy import func as _f, desc as _desc

    wedge = (request.args.get('wedge') or '').strip()
    status = (request.args.get('status') or '').strip()

    q = OutreachContact.query.filter_by(cohort='b2b')
    if wedge:
        q = q.filter_by(wedge=wedge)
    if status:
        q = q.filter_by(status=status)
    q = q.order_by(_desc(OutreachContact.created_at))
    contacts = q.limit(500).all()

    if not contacts:
        return jsonify({'b2b': [], 'count': 0})

    contact_ids = [c.id for c in contacts]

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

    rows = []
    for c in contacts:
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
            'last_contacted_at': c.last_contacted_at.isoformat() if c.last_contacted_at else None,
            'replied_at': c.replied_at.isoformat() if c.replied_at else None,
            'last_reply_summary': c.last_reply_summary or '',
            'created_at': c.created_at.isoformat() if c.created_at else None,
            'last_send': last_send_obj,
        })

    return jsonify({'b2b': rows, 'count': len(rows)})


@admin_bp.route('/api/admin/outreach/b2b', methods=['POST'])
@_api_admin_req_dec
def outreach_add_b2b():
    """Create a new B2B prospect."""
    from models import OutreachContact

    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    if not email or '@' not in email:
        return jsonify({'error': 'Valid email required'}), 400

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

    reply_to = _founder_reply_to()

    # Wrap plain-text body in light HTML. We deliberately keep this very plain
    # so the email reads as a real personal note, not a marketing campaign.
    # Personal-feeling emails get higher reply rates.
    html_lines = ''.join(
        f'<p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#1f2937;">{line}</p>'
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
    try:
        ok = send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            reply_to=reply_to,
            email_type='founder_outreach',
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
    'other':
        'this space',
    '':
        'this space',
}


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
            ok = send_email(
                to_email=c.email,
                subject=rendered_subj,
                html_content=html_content,
                reply_to=reply_to,
                email_type='founder_outreach_bulk',
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
                model='claude-sonnet-4-20250514', max_tokens=8000,
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

    models_dir = os.path.join(os.path.dirname(__file__), 'models')
    os.makedirs(models_dir, exist_ok=True)

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


@admin_bp.route('/api/admin/ml-model-status', methods=['GET'])
@_api_admin_req_dec
def admin_ml_model_status():
    """Check which model files are deployed."""
    import os

    models_dir = os.path.join(os.path.dirname(__file__), 'models')
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
    models_dir = os.path.join(base_dir, 'models')
    os.makedirs(models_dir, exist_ok=True)

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
        rows = MLFindingLabel.query.all()
        _log(f'Loaded {len(rows)} raw finding labels from database')
        data = []
        for r in rows:
            cat = (r.category or '').strip().lower()
            sev = (r.severity or '').strip().lower()
            text = (r.finding_text or '').strip()
            if cat and sev and len(text) > 10 and sev in ('critical','major','moderate','minor'):
                cat_map = {"foundation": "foundation_structure", "exterior": "roof_exterior",
                           "foundation & structure": "foundation_structure", "roof & exterior": "roof_exterior",
                           "hvac & systems": "hvac_systems", "hvac": "hvac_systems",
                           "roof": "roof_exterior", "general": "general",
                           "water_damage": "environmental", "pest": "environmental",
                           "safety": "electrical", "permits": "general", "legal & title": "general"}
                cat = cat_map.get(cat, cat)
                data.append({'text': text, 'category': cat, 'severity': sev})

        seen = set()
        deduped = []
        for d in data:
            if d['text'] not in seen:
                seen.add(d['text'])
                deduped.append(d)
        data = deduped
        _log(f'After cleaning + dedup: {len(data)} unique findings')

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
                        resp = client.messages.create(model='claude-sonnet-4-20250514', max_tokens=8000,
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

            # Memory-aware cap: check available RAM and limit data to avoid OOM
            import psutil
            avail_mb = psutil.virtual_memory().available / (1024 * 1024)
            # Each text needs ~1.5KB for embedding (384 floats * 4 bytes) + working memory
            # Reserve 150MB for XGBoost training + app overhead
            max_rows_for_ram = int((avail_mb - 150) * 1024 / 1.5) if avail_mb > 200 else 20000
            max_rows_for_ram = max(max_rows_for_ram, 5000)  # always train on at least 5K
            if len(data) > max_rows_for_ram:
                _log(f'⚠ Available RAM: {avail_mb:.0f}MB — capping training data to {max_rows_for_ram:,} rows (from {len(data):,})', 'warn')
                _log(f'  Upgrade Render to 2GB+ RAM to train on all {len(data):,} rows', 'warn')
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
    models_dir = os.path.join(base_dir, 'models')

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
    else:
        models['_last_trained'] = None

    # Data pipeline counts
    # v5.86.80: include v2 label coverage + effective training rows so the
    # UI can render "62,684 total → 28,000 real findings → 100% relabeled"
    _total_findings = MLFindingLabel.query.count()
    _v2_labeled = MLFindingLabel.query.filter(MLFindingLabel.category_v2.isnot(None)).count()
    _junk_flagged = MLFindingLabel.query.filter(MLFindingLabel.is_real_finding.is_(False)).count()
    _real_findings = MLFindingLabel.query.filter(MLFindingLabel.is_real_finding.is_(True)).count()
    models['_data'] = {
        'findings': _total_findings,
        'findings_v2_labeled': _v2_labeled,
        'findings_junk_flagged': _junk_flagged,
        'findings_real_findings': _real_findings,
        'findings_effective_training': _total_findings - _junk_flagged,
        'contradictions': MLContradictionPair.query.count(),
        'cooccurrence': MLCooccurrenceBucket.query.count(),
        'surveys': PostCloseSurvey.query.count() if hasattr(PostCloseSurvey, 'query') else 0,
    }

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
        models_dir = os.path.join(base_dir, 'models')
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
    """
    from ml_ingestion import CRAWLER_REGISTRY
    crawlers = []
    for key, label, cls in CRAWLER_REGISTRY:
        crawlers.append({
            'key': key,
            'label': label,
            'source_name': getattr(cls, 'SOURCE_NAME', ''),
            'state': getattr(cls, 'STATE_CODE', ''),
            'status': getattr(cls, 'STATUS', 'scaffold'),
            'max_rows': getattr(cls, 'MAX_ROWS', 0),
            'dataset_id': getattr(cls, 'DATASET_ID', ''),
            'domain': getattr(cls, 'DOMAIN', ''),
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
    """
    import time
    import traceback
    from models import db as _db
    from datetime import datetime as _dt

    job.status = 'running'
    job.started_at = _dt.utcnow()
    _db.session.commit()

    crawler._job_id = job.id
    crawler._t_start = time.time()
    crawler._log(f'Starting {crawler.JOB_TYPE} job: {crawler.SOURCE_NAME}')

    try:
        crawler.run_job()
        job.status = 'succeeded'
        crawler._log(f'Job succeeded: added={crawler._rows_added}, rejected={crawler._rows_rejected}')
    except Exception as e:
        job.status = 'failed'
        job.error = f'{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}'
        crawler._log(f'Job FAILED: {e}', level='error')

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
                return {'verdict': 'fail', 'reason': d['error'].get('message', '')[:120]}
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
                models_dir = os.path.join(base_dir, 'models')
                backup_dir = os.path.join(base_dir, 'models_backup')
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
                model='claude-sonnet-4-20250514',
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
