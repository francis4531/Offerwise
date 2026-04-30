"""
OfferWise Agent Routes Blueprint
=====================================
Extracted from app.py to reduce monolith size.
Contains all /api/agent/* and /agent/* page routes.
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
)

import os as _os
ADMIN_EMAIL = _os.environ.get('ADMIN_EMAIL', 'hello@getofferwise.ai')

def send_email(*a, **kw):
    from app import send_email as _se; return _se(*a, **kw)


agent_bp = Blueprint('agent', __name__)
logger = logging.getLogger(__name__)

# ── Injected dependencies ──────────────────────────────────────────
_login_required     = None
_admin_required     = None
_limiter            = None

_login_req_dec = DeferredDecorator(lambda: _login_required)
_admin_req_dec = DeferredDecorator(lambda: _admin_required)


def init_agent_blueprint(app, db_ref, login_required_fn, admin_required_fn):
    global _login_required, _admin_required, _limiter
    _login_required = login_required_fn
    _admin_required = admin_required_fn
    app.register_blueprint(agent_bp)
    logging.info("✅ agent blueprint registered")


@agent_bp.route('/api/agent/register', methods=['POST'])
@_login_req_dec
def agent_register():
    from models import Agent
    existing = Agent.query.filter_by(user_id=current_user.id).first()
    if existing:
        return jsonify({'success': True, 'agent_id': existing.id, 'already_exists': True})

    data = request.get_json() or {}
    from datetime import timedelta as _td
    agent = Agent(
        user_id       = current_user.id,
        agent_name    = data.get('agent_name', '').strip() or current_user.name or '',
        business_name = data.get('business_name', '').strip(),
        license_number= data.get('license_number', '').strip(),
        license_state = (data.get('license_state') or 'XX').strip().upper()[:2],
        phone         = data.get('phone', '').strip(),
        website       = data.get('website', '').strip(),
        service_areas = data.get('service_areas', '').strip(),
        plan          = 'free',
        monthly_quota = 10,
        monthly_used  = 0,
        quota_reset_at= datetime.utcnow() + _td(days=30),
    )
    db.session.add(agent)
    db.session.commit()
    logging.info(f"🏡 New agent registered: {current_user.email} — {agent.agent_name} @ {agent.business_name}")
    try:
        send_email(
            to_email=ADMIN_EMAIL,
            subject=f"🏡 New Agent Signup: {agent.agent_name or current_user.email}",
            html_content=f"""<div style="font-family:sans-serif;padding:20px;">
            <h2 style="color:#f97316;">New Agent Registered</h2>
            <p><b>Name:</b> {agent.agent_name}</p>
            <p><b>Email:</b> {current_user.email}</p>
            <p><b>Brokerage:</b> {agent.business_name or '—'}</p>
            <p><b>License:</b> {agent.license_number or '—'} ({agent.license_state})</p>
            <p><b>Phone:</b> {agent.phone or '—'}</p>
            <p><b>Areas:</b> {agent.service_areas or '—'}</p>
            </div>"""
        )
    except Exception as e:
        logging.error(f"Agent signup email failed: {e}")
    return jsonify({'success': True, 'agent_id': agent.id})



@agent_bp.route('/api/agent/profile', methods=['GET', 'POST'])
@_login_req_dec
def agent_profile():
    from models import Agent
    agent = Agent.query.filter_by(user_id=current_user.id).first()
    if request.method == 'GET':
        if not agent:
            return jsonify({'registered': False})
        return jsonify({
            'registered': True,
            'id': agent.id,
            'agent_name': agent.agent_name,
            'business_name': agent.business_name,
            'license_number': agent.license_number,
            'license_state': agent.license_state,
            'phone': agent.phone,
            'website': agent.website,
            'service_areas': agent.service_areas,
            'plan': agent.plan,
            'monthly_quota': agent.monthly_quota,
            'monthly_used': agent.monthly_used,
            'total_shares': agent.total_shares or 0,
            'total_buyers_converted': agent.total_buyers_converted or 0,
            'is_verified': agent.is_verified,
            'quota_remaining': max(0, (agent.monthly_quota or 10) - (agent.monthly_used or 0)),
            'email': current_user.email,
            'name': current_user.name,
        })
    # POST — update profile
    data = request.get_json() or {}
    if not agent:
        return jsonify({'error': 'Not registered as an agent.'}), 404
    for field in ['agent_name', 'business_name', 'license_number', 'license_state',
                  'phone', 'website', 'service_areas']:
        if field in data:
            setattr(agent, field, data[field])
    db.session.commit()
    return jsonify({'success': True})



@agent_bp.route('/api/agent/share', methods=['POST'])
@_login_req_dec
def agent_create_share():
    """Agent creates a shareable analysis link for a buyer client.

    Unlike inspectors, agents don't upload PDFs — they provide a property address
    and the buyer receives a branded link to run their own free analysis.
    Optionally the agent can attach a quick AI summary.
    """
    import secrets as _sec, json as _json
    from models import Agent, AgentShare

    agent = Agent.query.filter_by(user_id=current_user.id).first()
    if not agent:
        return jsonify({'error': 'Not registered as an agent.'}), 403

    # Quota check
    if (agent.monthly_quota or 0) > 0 and (agent.monthly_used or 0) >= agent.monthly_quota:
        return jsonify({
            'error': 'Monthly share limit reached',
            'message': f'You have used all {agent.monthly_quota} shares this month. Upgrade to Agent Pro for unlimited shares.',
            'upgrade_url': '/for-agents#pricing'
        }), 403

    data = request.get_json() or {}
    property_address = (data.get('property_address') or '').strip()
    property_price   = data.get('property_price', 0)
    buyer_name       = (data.get('buyer_name') or '').strip()
    buyer_email      = (data.get('buyer_email') or '').strip()
    notes            = (data.get('notes') or '').strip()

    if not property_address:
        return jsonify({'error': 'Property address is required.'}), 400
    if not buyer_email or '@' not in buyer_email:
        return jsonify({'error': 'Buyer email is required — they need it to receive the link.'}), 400

    # Build a lightweight summary the agent can personalize
    # Agent shares give the buyer a pre-populated "start your analysis" link
    # plus any notes the agent attached
    summary = {
        'property_address': property_address,
        'property_price': property_price,
        'agent_notes': notes,
        'agent_name': agent.agent_name,
        'agent_biz': agent.business_name,
        'message': f"Your agent {agent.agent_name or 'your agent'} has prepared this OfferWise analysis for you.",
    }

    token = _sec.token_hex(16)
    share = AgentShare(
        agent_id             = agent.id,
        agent_user_id        = current_user.id,
        property_address     = property_address,
        property_price       = float(property_price) if property_price else None,
        buyer_name           = buyer_name,
        buyer_email          = buyer_email,
        analysis_json        = _json.dumps(summary),
        share_token          = token,
        agent_name_on_report = agent.agent_name or current_user.name or '',
        agent_biz_on_report  = agent.business_name or '',
        has_text             = bool(notes),
    )
    db.session.add(share)
    agent.monthly_used  = (agent.monthly_used or 0) + 1
    agent.total_shares  = (agent.total_shares or 0) + 1
    db.session.commit()

    # Auto-watch: create PropertyWatch for buyer, linked to this agent share
    if buyer_email:
        try:
            from models import PropertyWatch, User as _AUser
            _abuyer = _AUser.query.filter_by(email=buyer_email.strip().lower()).first()
            if _abuyer:
                _aw_ex = PropertyWatch.query.filter_by(
                    user_id=_abuyer.id, address=property_address, is_active=True
                ).first()
                if not _aw_ex:
                    _aw = PropertyWatch(
                        user_id        = _abuyer.id,
                        address        = property_address,
                        asking_price   = float(property_price) if property_price else None,
                        agent_share_id = share.id,
                        expires_at     = datetime.utcnow() + timedelta(days=45),
                    )
                    db.session.add(_aw)
                    db.session.commit()
                    logging.info(f"🔭 Buyer watch created from agent share: {property_address}")
            else:
                # Buyer has no account yet — ghost watch owned by agent
                _ag_ghost_ex = PropertyWatch.query.filter_by(
                    agent_share_id=share.id, is_active=True
                ).first()
                if not _ag_ghost_ex and property_address:
                    _ag_ghost = PropertyWatch(
                        user_id               = current_user.id,  # agent's user_id
                        address               = property_address,
                        asking_price          = float(property_price) if property_price else None,
                        agent_share_id        = share.id,
                        ghost_buyer_email     = buyer_email.strip().lower() if buyer_email else None,
                        owned_by_professional = True,
                        expires_at            = datetime.utcnow() + timedelta(days=45),
                    )
                    db.session.add(_ag_ghost)
                    db.session.commit()
                    logging.info(f"🔭 Ghost watch created for agent (buyer not yet registered): {property_address}")
        except Exception as _agw:
            logging.warning(f"Agent buyer-watch creation failed (non-fatal): {_agw}")

    share_url = f"{request.host_url.rstrip('/')}agent-report/{token}"
    logging.info(f"🏡 Agent share created: {property_address} by {current_user.email}")

    # Auto-send email to buyer if provided
    if buyer_email:
        try:
            agent_display = agent.agent_name or 'Your Agent'
            biz_display   = f" at {agent.business_name}" if agent.business_name else ''
            send_email(
                to_email=buyer_email,
                subject=f"Your OfferWise property analysis — {property_address}",
                html_content=f"""<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:28px;border-radius:12px;">
                  <div style="font-size:20px;font-weight:800;color:#f59e0b;margin-bottom:8px;">🏡 Property Analysis Ready</div>
                  <p style="color:#94a3b8;margin-bottom:16px;">{agent_display}{biz_display} has prepared an OfferWise analysis for you.</p>
                  <p style="font-size:15px;font-weight:700;margin-bottom:8px;">{property_address}</p>
                  {f'<p style="color:#94a3b8;margin-bottom:16px;">{notes}</p>' if notes else ''}
                  <a href="{share_url}" style="display:inline-block;padding:14px 28px;background:linear-gradient(90deg,#f97316,#f59e0b);color:white;border-radius:10px;font-weight:700;text-decoration:none;margin-bottom:16px;">View Your Analysis →</a>
                  <p style="color:#475569;font-size:12px;">This analysis was prepared by {agent_display}{biz_display} using OfferWise AI.</p>
                </div>"""
            )
        except Exception as e:
            logging.error(f"Agent share email failed: {e}")

    return jsonify({
        'success': True,
        'share_token': token,
        'share_url': share_url,
        'buyer_emailed': bool(buyer_email),
    })



@agent_bp.route('/api/agent/shares', methods=['GET'])
@_login_req_dec
def agent_shares_list():
    from models import Agent, AgentShare
    agent = Agent.query.filter_by(user_id=current_user.id).first()
    if not agent:
        return jsonify({'shares': []})
    shares = AgentShare.query.filter_by(agent_id=agent.id)        .order_by(AgentShare.created_at.desc()).limit(200).all()
    return jsonify({'shares': [{
        'id': s.id,
        'property_address': s.property_address,
        'property_price': s.property_price,
        'buyer_name': s.buyer_name,
        'buyer_email': s.buyer_email,
        'share_token': s.share_token,
        'share_url': request.host_url.rstrip('/') + s.share_url,
        'created_at': s.created_at.isoformat() if s.created_at else None,
        'view_count': s.view_count or 0,
        'buyer_viewed_at': s.buyer_viewed_at.isoformat() if s.buyer_viewed_at else None,
        'buyer_registered': s.buyer_registered or False,
        'buyer_converted': s.buyer_converted or False,
        'has_text': s.has_text or False,
        'agent_notes': '',
    } for s in shares]})



@agent_bp.route('/a/<slug>')
def agent_personal_landing(slug):
    """Personal co-branded agent landing page — getofferwise.ai/a/sarah-johnson"""
    import os as _os
    from models import Agent, User as _U
    from flask import current_app as _app
    agent = Agent.query.filter_by(vanity_slug=slug.lower(), is_active=True).first()
    if not agent:
        return redirect('/app')
    with open(_os.path.join(_app.static_folder, 'agent-landing.html'), 'r') as _f:
        html = _f.read()
    _name = agent.agent_name or 'Your Agent'
    _biz  = agent.business_name or ''
    _initial = _name[0].upper() if _name else 'A'
    _biz_suffix = f', {_biz}' if _biz else ''
    html = html.replace('{{AGENT_NAME}}', _name)
    html = html.replace('{{AGENT_BIZ}}', _biz)
    html = html.replace('{{AGENT_BIZ_SUFFIX}}', _biz_suffix)
    html = html.replace('{{AGENT_INITIAL}}', _initial)
    html = html.replace('{{AGENT_STATE}}', agent.license_state or '')
    html = html.replace('{{AGENT_PHONE}}', agent.phone or '')
    html = html.replace('{{AGENT_PHOTO}}', agent.photo_url or '')
    html = html.replace('{{AGENT_SLUG}}', slug)
    html = html.replace('{{AGENT_ID}}', str(agent.id))
    return html, 200, {'Content-Type': 'text/html'}

@agent_bp.route('/api/agent/my-link', methods=['GET'])
@_login_req_dec
def agent_get_my_link():
    """Return or generate the agent's personal vanity link."""
    import re as _re, secrets as _sec
    agent = Agent.query.filter_by(user_id=current_user.id).first()
    if not agent:
        return jsonify({'error': 'Not registered as an agent'}), 403
    if not agent.vanity_slug:
        # Auto-generate from agent name
        raw = (agent.agent_name or current_user.name or 'agent').lower()
        base = _re.sub(r'[^a-z0-9]+', '-', raw).strip('-')[:40]
        slug = base
        attempt = 0
        while Agent.query.filter_by(vanity_slug=slug).first():
            attempt += 1
            slug = f'{base}-{attempt}'
        agent.vanity_slug = slug
        db.session.commit()
    from flask import request as _req
    base_url = _req.host_url.rstrip('/')
    return jsonify({
        'slug': agent.vanity_slug,
        'url': f'{base_url}/a/{agent.vanity_slug}',
        'qr_url': f'https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={base_url}/a/{agent.vanity_slug}',
    })

@agent_bp.route('/api/agent/my-link', methods=['POST'])
@_login_req_dec
def agent_set_my_link():
    """Let agent customize their vanity slug."""
    import re as _re
    agent = Agent.query.filter_by(user_id=current_user.id).first()
    if not agent:
        return jsonify({'error': 'Not registered'}), 403
    data = request.get_json() or {}
    new_slug = _re.sub(r'[^a-z0-9-]+', '', (data.get('slug') or '').lower().strip())[:50]
    if len(new_slug) < 3:
        return jsonify({'error': 'Slug must be at least 3 characters'}), 400
    existing = Agent.query.filter_by(vanity_slug=new_slug).first()
    if existing and existing.id != agent.id:
        return jsonify({'error': 'That link is already taken. Try another.'}), 409
    agent.vanity_slug = new_slug
    db.session.commit()
    from flask import request as _req
    return jsonify({'slug': new_slug, 'url': f'{_req.host_url.rstrip("/")}/a/{new_slug}'})

@agent_bp.route('/api/agent/report/<token>', methods=['GET'])
def agent_report_data(token):
    """Public endpoint — returns report data for the branded buyer page."""
    from models import AgentShare, Agent, User
    import json as _json
    share = AgentShare.query.filter_by(share_token=token).first_or_404()

    # Track view
    if not share.buyer_viewed_at:
        share.buyer_viewed_at = datetime.utcnow()
    share.view_count = (share.view_count or 0) + 1
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Agentic concern signal — notify agent on 1st and 3rd buyer view
    try:
        from agentic_monitor import fire_buyer_concern_signal
        _share_data = json.loads(share.analysis_json) if share.analysis_json else {}
        fire_buyer_concern_signal(
            report_type='agent',
            report_id=share.id,
            buyer_name=share.buyer_name or 'Your client',
            buyer_email=share.buyer_email or '',
            address=share.property_address or '',
            view_count=share.view_count,
            top_findings=_share_data.get('findings', []),
        )
    except Exception as _cs:
        logging.warning(f"Agent concern signal failed: {_cs}")

    agent = Agent.query.get(share.agent_id)
    summary = {}
    if share.analysis_json:
        try:
            summary = _json.loads(share.analysis_json)
        except Exception:
            pass

    return jsonify({
        'property_address': share.property_address,
        'property_price': share.property_price,
        'buyer_name': share.buyer_name,
        'agent_name': share.agent_name_on_report or (agent.agent_name if agent else ''),
        'agent_biz': share.agent_biz_on_report or (agent.business_name if agent else ''),
        'agent_phone': agent.phone if agent else '',
        'agent_website': agent.website if agent else '',
        'agent_notes': summary.get('agent_notes', ''),
        'message': summary.get('message', ''),
        'share_token': token,
        'created_at': share.created_at.isoformat() if share.created_at else None,
    })
@agent_bp.route('/api/billing-portal', methods=['POST'])
@_login_req_dec
def billing_portal():
    """Redirect user to Stripe Customer Portal to manage subscription, cancel, update card."""
    import stripe as _stripe
    _stripe.api_key = os.environ.get('STRIPE_SECRET_KEY', '')
    if not _stripe.api_key:
        return jsonify({'error': 'Billing not configured.'}), 503

    customer_id = getattr(current_user, 'stripe_customer_id', None)
    if not customer_id:
        return jsonify({'error': 'No billing account found. Please subscribe first.'}), 404

    try:
        session = _stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=request.referrer or request.host_url + 'settings',
        )
        return jsonify({'url': session.url})
    except Exception as e:
        logging.error(f"Stripe billing portal error: {e}")
        return jsonify({'error': 'Could not open billing portal. Please try again.'}), 500


# ============================================================================
# AGENTIC MONITORING API ROUTES (v5.75.92)
# ============================================================================


@agent_bp.route('/agent-onboarding')
@_login_req_dec
def agent_onboarding_page():
    """4-step agent setup wizard."""
    return send_from_directory('static', 'agent-onboarding.html')


# ============================================================================
# AGENT PORTAL ROUTES (v5.75.78)
# Real estate agents share OfferWise analyses with buyer clients.
# Nationwide — state license number collected but not CA-specific.
# ============================================================================


@agent_bp.route('/agent-portal')
@_login_req_dec
def agent_portal():
    return send_from_directory('static', 'agent-portal.html')


@agent_bp.route('/for-agents')
def for_agents_landing():
    return send_from_directory('static', 'for-agents.html')


# ── Agent post-close signal (flywheel v5.80.86) ─────────────────────────────

@agent_bp.route('/api/agent/shares/<int:share_id>/close', methods=['POST'])
@_login_req_dec
def agent_mark_deal_closed(share_id):
    """
    Mark an agent share's deal as closed and trigger the post-close email.
    Called by: admin trigger, future Stripe escrow webhook, or agent self-report.
    """
    from models import AgentShare, Agent
    share = AgentShare.query.get_or_404(share_id)
    agent = Agent.query.filter_by(user_id=current_user.id).first()
    if not agent or share.agent_id != agent.id:
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json() or {}
    final_price = float(data['final_sale_price']) if data.get('final_sale_price') else None
    from flywheel_notifications import send_agent_postclose_email
    sent = send_agent_postclose_email(share_id, final_sale_price=final_price)
    return jsonify({'success': True, 'email_sent': sent})


@agent_bp.route('/api/agent/pipeline', methods=['GET'])
@_login_req_dec
def agent_pipeline_stats():
    """Return pipeline stats for the agent portal dashboard."""
    from models import Agent
    agent = Agent.query.filter_by(user_id=current_user.id).first()
    if not agent:
        return jsonify({'error': 'Not an agent'}), 403
    from flywheel_notifications import get_agent_pipeline_stats
    stats = get_agent_pipeline_stats(agent.id)
    return jsonify(stats)
