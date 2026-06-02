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
    # v5.88.16: tightened from "@ in email" to is_valid_email. The loose
    # check accepted "@nodomain" and similar malformed addresses.
    from blueprint_helpers import is_valid_email
    if not is_valid_email(buyer_email):
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



# ─────────────────────────────────────────────────────────────────────
# v5.88.75 — Agent Briefing v0 (Release 1)
# ─────────────────────────────────────────────────────────────────────
# Distinct from /api/agent/share (the legacy "forward a link" flow).
# Briefings are the new agent-as-analyst product: agent uploads a PDF,
# enters their client's budget tiers + their own commentary, and gets
# back a one-page deliverable styled as the agent's work.
#
# Release 1 ships inputs only — the row is created and stored. Analysis
# pipeline (Release 2/3) and seller-side framing (Release 4) ship in
# follow-up releases. After this release, the agent will see a "briefing
# created" confirmation but the output page will show a placeholder
# until R2 wires the repair-cost section.
#
# Quota: counts against the existing Agent.monthly_quota the same way
# AgentShare does. Briefings and shares are interchangeable for billing
# purposes in v0; that may split out later if pricing changes.

@agent_bp.route('/api/agent/extract-pdf', methods=['POST'])
@_login_req_dec
def agent_extract_pdf():
    """Extract text from an uploaded inspection or disclosure PDF.

    Parallel to /api/inspector/extract-pdf but gated on Agent registration.
    Same underlying pdf_handler.extract_text_from_bytes; we don't share
    one endpoint because the role check should reflect which portal the
    user is operating in (defense-in-depth — an inspector account
    shouldn't accidentally consume agent quota or vice versa).
    """
    from models import Agent
    agent = Agent.query.filter_by(user_id=current_user.id).first()
    if not agent:
        return jsonify({'error': 'Not registered as an agent.'}), 403

    f = request.files.get('pdf')
    if not f:
        return jsonify({'error': 'No PDF file uploaded.'}), 400

    try:
        # Lazy import — same pattern as inspector_routes uses.
        from app import pdf_handler
        pdf_bytes = f.read()
        if len(pdf_bytes) > 50 * 1024 * 1024:
            return jsonify({'error': 'PDF too large (max 50MB).'}), 400

        result = pdf_handler.extract_text_from_bytes(pdf_bytes)
        text = (result.get('text') or '').strip()

        if not text or len(text) < 100:
            return jsonify({
                'error': 'Could not extract text from this PDF. If it is a scanned image, try a text-based PDF instead, or paste the text manually.'
            }), 422

        return jsonify({
            'success': True,
            'text': text,
            'page_count': result.get('page_count', 0),
            'method': result.get('method', 'unknown'),
            'char_count': len(text),
            'filename': f.filename or '',
        })
    except Exception as e:
        logging.error(f"Agent PDF extraction failed: {e}", exc_info=True)
        return jsonify({
            'error': f'PDF extraction failed: {type(e).__name__}. Please try again or paste the text manually.'
        }), 500


@agent_bp.route('/api/agent/briefing', methods=['POST'])
@_login_req_dec
def agent_create_briefing():
    """Create a new agent briefing — Release 1: inputs only.

    Body (JSON):
      property_address   str   required
      representing       str   required, must be 'buyer' or 'seller'
      inspection_text    str   required (extracted from PDF by client-side
                                or by a separate /api/agent/extract-pdf
                                endpoint mirroring the inspector flow;
                                this endpoint just persists whatever text
                                is provided)
      inspection_pdf_filename str optional (display only)
      agent_commentary   str   required, min 100 chars
      property_price     num   optional
      client_name        str   optional (rendered in report header)
      client_email       str   optional (only used for send-to-client UI)
      budget_qualified   num   required when representing='buyer'
      budget_comfortable num   required when representing='buyer'
      budget_preferred   num   required when representing='buyer'

    Returns:
      {
        briefing_id: int,
        share_token: str,
        share_url: str (absolute URL),
        message: str
      }
    """
    import secrets as _sec
    from models import Agent, AgentBriefing, db as _db

    agent = Agent.query.filter_by(user_id=current_user.id).first()
    if not agent:
        return jsonify({'error': 'Not registered as an agent.'}), 403

    # Quota — briefings share the existing share quota in v0.
    if (agent.monthly_quota or 0) > 0 and (agent.monthly_used or 0) >= agent.monthly_quota:
        return jsonify({
            'error': 'Monthly limit reached',
            'message': f'You have used all {agent.monthly_quota} briefings + shares this month. Upgrade to Agent Pro for unlimited.',
            'upgrade_url': '/for-agents#pricing',
        }), 403

    data = request.get_json() or {}

    # Required: property_address
    property_address = (data.get('property_address') or '').strip()
    if not property_address:
        return jsonify({'error': 'Property address is required.'}), 400

    # Required: representing
    representing = (data.get('representing') or '').strip().lower()
    if representing not in ('buyer', 'seller'):
        return jsonify({'error': 'You must indicate whether you represent the buyer or the seller.'}), 400

    # Required: inspection_text (extracted from PDF before this call)
    inspection_text = (data.get('inspection_text') or '').replace('\x00', '').strip()
    if not inspection_text or len(inspection_text) < 200:
        return jsonify({
            'error': 'A disclosure or inspection report is required. Upload the PDF — the briefing needs the report content to produce repair costs and offer strategy.'
        }), 400

    # Required: agent_commentary, min 100 chars. This is the central
    # product decision — the briefing's value comes from the agent's
    # voice being woven through the deliverable, so the input is required.
    agent_commentary = (data.get('agent_commentary') or '').strip()
    if len(agent_commentary) < 100:
        return jsonify({
            'error': "Your commentary is required and should be at least 100 characters. Your buyer or seller is paying for your judgment, not just data. Even 3-4 sentences about the property's context, the seller's situation, or your read on the deal makes the briefing meaningfully more valuable."
        }), 400

    # Budget tiers: required when representing buyer, ignored when seller.
    budget_qualified = budget_comfortable = budget_preferred = None
    if representing == 'buyer':
        try:
            budget_qualified  = float(data.get('budget_qualified') or 0)
            budget_comfortable = float(data.get('budget_comfortable') or 0)
            budget_preferred  = float(data.get('budget_preferred') or 0)
        except (TypeError, ValueError):
            return jsonify({'error': 'Budget tiers must be numbers.'}), 400
        if not (budget_qualified > 0 and budget_comfortable > 0 and budget_preferred > 0):
            return jsonify({
                'error': "Your buyer's budget tiers are required (qualified, comfortable, preferred). The offer strategy section can't be useful without them."
            }), 400
        # Sanity: tiers should be ordered preferred ≤ comfortable ≤ qualified.
        # Don't enforce strict ordering — some buyers have unusual constraints
        # — but warn if obviously inverted.
        if budget_preferred > budget_qualified:
            return jsonify({
                'error': "Your buyer's preferred budget is higher than their qualified amount. That's unusual — please re-check the numbers."
            }), 400

    # Optional fields
    try:
        property_price = float(data.get('property_price') or 0) or None
    except (TypeError, ValueError):
        property_price = None

    client_name  = (data.get('client_name') or '').strip()[:255]
    client_email = (data.get('client_email') or '').strip()[:255]
    inspection_pdf_filename = (data.get('inspection_pdf_filename') or '').strip()[:500]

    if client_email:
        from blueprint_helpers import is_valid_email
        if not is_valid_email(client_email):
            return jsonify({'error': "Your client's email looks malformed."}), 400

    # Mint a unique share token. Loop on collision (vanishingly rare with
    # 32 hex chars, but defensive).
    for _attempt in range(5):
        token = _sec.token_hex(16)
        if not AgentBriefing.query.filter_by(share_token=token).first():
            break
    else:
        return jsonify({'error': 'Could not generate a unique share token. Please retry.'}), 500

    briefing = AgentBriefing(
        agent_id            = agent.id,
        agent_user_id       = current_user.id,
        property_address    = property_address[:500],
        property_price      = property_price,
        representing        = representing,
        client_name         = client_name or None,
        client_email        = client_email or None,
        inspection_text     = inspection_text,
        inspection_pdf_filename = inspection_pdf_filename or None,
        budget_qualified    = budget_qualified,
        budget_comfortable  = budget_comfortable,
        budget_preferred    = budget_preferred,
        agent_commentary    = agent_commentary,
        # analysis_json populated below; offer_strategy_json + bottom_line stay
        # null at R2 — they ship in R3 with the offer-strategy LLM call.
        share_token         = token,
        agent_name_on_report = agent.agent_name or '',
        agent_biz_on_report  = agent.business_name or '',
    )

    # v5.88.76 R2: run the repair-cost analysis pipeline synchronously at
    # create-time. Same engine the inspector flow uses (OfferWiseIntelligence)
    # — proven, well-tested. ~10-25s for typical disclosure/inspection PDFs.
    #
    # Why sync (not async): agents expect a short wait after submit
    # (matches the inspector flow's UX). Async + polling adds infra we
    # don't need yet. If real-world PDFs start timing out at Render's
    # 30s edge limit, we'll revisit in R4.
    #
    # If analysis fails, we still persist the briefing (so the agent
    # doesn't lose their commentary + budget inputs) but flag it so the
    # output page can show a "re-run analysis" affordance later. The
    # briefing row is committed unconditionally; analysis success is
    # advisory.
    try:
        from offerwise_intelligence import OfferWiseIntelligence
        from risk_scoring_model import BuyerProfile
        import json as _json
        from dataclasses import asdict
        import datetime as _dt
        from enum import Enum

        # Optional numpy — only used for ndarray serialization.
        try:
            import numpy as _np
            _has_np = True
        except ImportError:
            _np = None
            _has_np = False

        # Use the buyer's preferred budget as the buyer-profile max_budget
        # for buyer-side briefings. For seller-side, use the property price.
        if representing == 'buyer' and budget_preferred:
            max_b = int(budget_preferred)
        elif property_price:
            max_b = int(property_price)
        else:
            max_b = 1000000  # generic fallback for engine input

        # v5.88.77: also use max_b as a non-zero floor for the engine's
        # property_price input. The downstream _generate_negotiation_strategy
        # in offerwise_intelligence.py has a `repair_high / property_price`
        # division that crashes with ZeroDivisionError when price is 0.
        # Seller-side briefings legitimately omit property_price (e.g.,
        # pre-listing analyses), so we coerce to a reasonable non-zero
        # value here rather than asking the agent to invent a number.
        # If we don't have a real price, use max_b (preferred budget or
        # generic fallback). The cost analysis itself is price-agnostic;
        # only certain percentage-of-price renderings need a divisor.
        engine_price = int(property_price) if property_price else max_b

        intel = OfferWiseIntelligence()
        bp = BuyerProfile(
            max_budget=max_b,
            repair_tolerance='moderate',
            ownership_duration='5-10',
            biggest_regret='hidden_issues',
            replaceability='somewhat_unique',
            deal_breakers=['foundation', 'mold', 'electrical'],
        )
        result = intel.analyze_property(
            seller_disclosure_text='',  # agent uploads ONE PDF; we don't split
            inspection_report_text=inspection_text,
            property_price=engine_price,
            property_address=property_address,
            buyer_profile=bp,
        )

        # Same convert/clean pass the inspector flow uses for the
        # dataclass → JSON dance. Copied verbatim from inspector_routes
        # to keep the agent flow independent (no shared private helper).
        def _convert(obj):
            if isinstance(obj, (_dt.datetime, _dt.date)):
                return obj.isoformat()
            if isinstance(obj, Enum):
                return obj.value
            if _has_np and isinstance(obj, _np.ndarray):
                return obj.tolist()
            if hasattr(obj, 'to_dict') and callable(obj.to_dict):
                return obj.to_dict()
            if hasattr(obj, '__dataclass_fields__'):
                return asdict(obj, dict_factory=_dict_factory)
            return obj

        def _dict_factory(fields):
            return {k: _convert(v) for k, v in fields}

        result_dict = asdict(result, dict_factory=_dict_factory)

        def _clean(obj):
            if _has_np and isinstance(obj, _np.ndarray):
                return obj.tolist()
            if isinstance(obj, dict):
                return {k: _clean(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_clean(item) for item in obj]
            if isinstance(obj, Enum):
                return obj.value
            if isinstance(obj, (_dt.datetime, _dt.date)):
                return obj.isoformat()
            if hasattr(obj, '__dataclass_fields__'):
                return {k: _clean(v) for k, v in vars(obj).items()}
            if hasattr(obj, 'to_dict') and callable(obj.to_dict):
                return _clean(obj.to_dict())
            return obj

        result_dict = _clean(result_dict)
        briefing.analysis_json = _json.dumps(result_dict)
        analysis_ok = True
    except Exception as e:
        logging.error(f"Briefing analysis failed for agent {agent.id}: {e}", exc_info=True)
        # Persist the briefing anyway — analysis_json stays null. The
        # output page will detect this and show an error state with a
        # "Re-run analysis" button (R4 polish; for now it just shows
        # the agent's commentary + a note that analysis didn't complete).
        analysis_ok = False
        result_dict = None

    # v5.88.77 R3 / v5.88.78 R4: offer strategy + bottom line.
    # Buyer-side: runs `generate_buyer_offer_strategy` with budget tiers.
    # Seller-side (R4): runs `generate_seller_offer_strategy` with list
    # price as the anchor. Both require successful analysis (no point
    # recommending strategy without cost findings). Failures here don't
    # block the briefing — strategy fields stay null and the share page
    # shows a "strategy not available" state.
    if analysis_ok and result_dict is not None:
        try:
            from agent_briefing_strategy import (
                generate_buyer_offer_strategy,
                generate_seller_offer_strategy,
                build_findings_summary,
            )
            risk = (result_dict.get('risk_score') or {})
            repair_low_val = float(risk.get('total_repair_cost_low') or 0)
            repair_high_val = float(risk.get('total_repair_cost_high') or 0)
            findings_summary = build_findings_summary(result_dict)

            if representing == 'buyer':
                strategy = generate_buyer_offer_strategy(
                    property_price=float(property_price or 0),
                    repair_low=repair_low_val,
                    repair_high=repair_high_val,
                    budget_qualified=float(budget_qualified or 0),
                    budget_comfortable=float(budget_comfortable or 0),
                    budget_preferred=float(budget_preferred or 0),
                    agent_commentary=agent_commentary,
                    property_address=property_address,
                    findings_summary=findings_summary,
                )
            else:  # seller
                strategy = generate_seller_offer_strategy(
                    property_price=float(property_price or 0),
                    repair_low=repair_low_val,
                    repair_high=repair_high_val,
                    agent_commentary=agent_commentary,
                    property_address=property_address,
                    findings_summary=findings_summary,
                )

            # Persist if we got scenarios (no_list_price seller-side
            # case returns empty scenarios + ok=False — store nothing
            # so the UI shows "needs list price" gracefully).
            scenarios = strategy.get('scenarios') or []
            if scenarios:
                briefing.offer_strategy_json = _json.dumps({
                    'scenarios': scenarios,
                    'source': strategy.get('source') or 'unknown',
                    'side': representing,
                })
                briefing.bottom_line = (strategy.get('bottom_line') or '').strip() or None
        except Exception as e:
            logging.error(
                f"Briefing offer strategy generation failed for agent {agent.id}: {e}",
                exc_info=True,
            )
            # Leave offer_strategy_json + bottom_line null; the share page
            # will show "Offer strategy unavailable for this briefing."

    _db.session.add(briefing)

    # Increment quota usage.
    agent.monthly_used = (agent.monthly_used or 0) + 1
    agent.total_shares = (agent.total_shares or 0) + 1   # reuse the existing counter

    _db.session.commit()

    # v5.88.85: auto-start a PropertyWatch on briefing creation, mirroring
    # the legacy-share pattern. This puts the briefing on the OfferWatch
    # surface — the agent sees their briefed property in the OfferWatch tab
    # and gets alerts when the market shifts. Without this, briefings
    # don't feed the daily monitoring jobs (because comps/price/permit
    # watchers iterate PropertyWatch rows, not briefings or shares
    # directly). Failure is non-fatal: the briefing itself is already
    # committed, the watch is an additive layer.
    if client_email or property_address:
        try:
            from models import PropertyWatch, User as _AUser
            from datetime import timedelta as _td
            buyer_email_norm = (client_email or '').strip().lower() if client_email else ''
            buyer_user = _AUser.query.filter_by(email=buyer_email_norm).first() if buyer_email_norm else None
            existing = PropertyWatch.query.filter_by(
                agent_briefing_id=briefing.id, is_active=True
            ).first()
            if not existing:
                if buyer_user:
                    # Buyer has an account — watch is buyer-owned, linked to briefing
                    pw = PropertyWatch(
                        user_id           = buyer_user.id,
                        address           = property_address,
                        asking_price      = float(property_price) if property_price else None,
                        agent_briefing_id = briefing.id,
                        expires_at        = datetime.utcnow() + _td(days=45),
                    )
                    _db.session.add(pw)
                    _db.session.commit()
                    logging.info(f"🔭 Buyer watch created from agent briefing: {property_address}")
                else:
                    # Buyer has no account (or no email given) — ghost watch
                    # owned by the agent. When buyer signs up matching the
                    # ghost_buyer_email, the existing re-link path on signup
                    # transfers ownership to them automatically.
                    pw = PropertyWatch(
                        user_id               = current_user.id,
                        address               = property_address,
                        asking_price          = float(property_price) if property_price else None,
                        agent_briefing_id     = briefing.id,
                        ghost_buyer_email     = buyer_email_norm or None,
                        owned_by_professional = True,
                        expires_at            = datetime.utcnow() + _td(days=45),
                    )
                    _db.session.add(pw)
                    _db.session.commit()
                    logging.info(f"🔭 Ghost watch created for agent briefing: {property_address}")
        except Exception as _bw:
            logging.warning(f"Briefing auto-watch creation failed (non-fatal): {_bw}")

    share_url = request.host_url.rstrip('/') + briefing.share_url
    return jsonify({
        'briefing_id': briefing.id,
        'share_token': token,
        'share_url': share_url,
        'analysis_ok': analysis_ok,
        'message': (
            'Briefing ready.' if analysis_ok else
            'Briefing saved, but analysis did not complete. You can view it, but the repair section will show an error state.'
        ),
    })


@agent_bp.route('/api/agent/briefings', methods=['GET'])
@_login_req_dec
def agent_briefings_list():
    """List all briefings created by the current agent."""
    from models import Agent, AgentBriefing
    agent = Agent.query.filter_by(user_id=current_user.id).first()
    if not agent:
        return jsonify({'briefings': []})
    briefings = AgentBriefing.query.filter_by(agent_id=agent.id) \
        .order_by(AgentBriefing.created_at.desc()).limit(200).all()
    return jsonify({'briefings': [{
        'id': b.id,
        'property_address': b.property_address,
        'representing': b.representing,
        'client_name': b.client_name,
        'client_email': b.client_email,
        'share_token': b.share_token,
        'share_url': request.host_url.rstrip('/') + b.share_url,
        'created_at': b.created_at.isoformat() if b.created_at else None,
        'view_count': b.view_count or 0,
        'client_viewed_at': b.client_viewed_at.isoformat() if b.client_viewed_at else None,
        'sent_to_client_at': b.sent_to_client_at.isoformat() if b.sent_to_client_at else None,
    } for b in briefings]})


@agent_bp.route('/api/agent/briefing/<int:briefing_id>', methods=['GET'])
@_login_req_dec
def agent_briefing_detail(briefing_id):
    """Fetch a single briefing's details (agent-owned only)."""
    from models import Agent, AgentBriefing
    agent = Agent.query.filter_by(user_id=current_user.id).first()
    if not agent:
        return jsonify({'error': 'Not registered as an agent.'}), 403
    b = AgentBriefing.query.filter_by(id=briefing_id, agent_id=agent.id).first()
    if not b:
        return jsonify({'error': 'Briefing not found.'}), 404
    return jsonify({
        'id': b.id,
        'property_address': b.property_address,
        'property_price': b.property_price,
        'representing': b.representing,
        'client_name': b.client_name,
        'client_email': b.client_email,
        'inspection_pdf_filename': b.inspection_pdf_filename,
        'agent_commentary': b.agent_commentary,
        'budget_qualified': b.budget_qualified,
        'budget_comfortable': b.budget_comfortable,
        'budget_preferred': b.budget_preferred,
        'share_token': b.share_token,
        'share_url': request.host_url.rstrip('/') + b.share_url,
        'created_at': b.created_at.isoformat() if b.created_at else None,
        'analysis_ready': bool(b.analysis_json),  # false at R1, true after R2 pipeline runs
    })


# v5.88.78 R4: send the briefing link to the client by email.
# Server-side send via send_email() — engagement tracking applies
# automatically (the Resend tracking subdomain wraps links + injects
# the open pixel). Agent's name shows in the From; their email in
# Reply-To so client replies go to them, not OfferWise.
@agent_bp.route('/api/agent/briefing/<int:briefing_id>/send', methods=['POST'])
@_login_req_dec
def agent_briefing_send(briefing_id):
    """Send the briefing link to the agent's client by email.

    Body (JSON):
      to_email          str   required (or pulled from briefing.client_email)
      subject           str   optional (default generated)
      message           str   optional (default generated; agent's personal note)

    The share URL is appended to the message automatically; the agent
    doesn't have to remember to include it.

    Returns:
      { ok: true, sent_at: ISO } on success
      { error: ... } on failure
    """
    import datetime as _dt
    from models import Agent, AgentBriefing, User, db as _db

    agent = Agent.query.filter_by(user_id=current_user.id).first()
    if not agent:
        return jsonify({'error': 'Not registered as an agent.'}), 403

    b = AgentBriefing.query.filter_by(id=briefing_id, agent_id=agent.id).first()
    if not b:
        return jsonify({'error': 'Briefing not found.'}), 404

    data = request.get_json() or {}
    to_email = (data.get('to_email') or b.client_email or '').strip()
    if not to_email:
        return jsonify({
            'error': "No client email on file for this briefing. Please add one to the briefing or pass `to_email` in the request."
        }), 400

    from blueprint_helpers import is_valid_email
    if not is_valid_email(to_email):
        return jsonify({'error': "Client email looks malformed."}), 400

    # Defaults are intentionally short and personal — they sound like
    # the agent wrote them, not OfferWise.
    client_first = (b.client_name or '').split()[0] if b.client_name else ''
    addr = b.property_address or 'the property'

    default_subject = f"Your briefing — {addr}"
    default_message = (
        f"Hi{(' ' + client_first) if client_first else ''},\n\n"
        f"I put together a quick briefing on {addr} — repair cost estimate, "
        f"offer strategy, and my thinking on the deal.\n\n"
        f"Take a look when you have a few minutes."
    )

    subject = (data.get('subject') or default_subject).strip()[:200]
    message = (data.get('message') or default_message).strip()

    # Build the share URL and append it on its own line. Doing this
    # server-side guarantees the link is always present even if the
    # agent edits the message and forgets to include it.
    share_url = request.host_url.rstrip('/') + b.share_url

    # HTML body. Plain personal note + the share link as a single
    # button-styled anchor. No OfferWise branding here — the briefing
    # itself has the footer attribution.
    safe_message_html = (
        message.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        .replace('\n', '<br>')
    )
    html = f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1a1a1a;line-height:1.55;max-width:560px;margin:24px auto;padding:0 16px;">
<div style="font-size:15px;color:#1a1a1a;">{safe_message_html}</div>
<div style="margin:24px 0;">
  <a href="{share_url}" style="display:inline-block;background:#f97316;color:#fff;text-decoration:none;padding:12px 20px;border-radius:8px;font-weight:600;font-size:14px;">View briefing →</a>
</div>
<div style="font-size:13px;color:#666;margin-top:18px;">
  Or paste this into your browser:<br>
  <a href="{share_url}" style="color:#666;">{share_url}</a>
</div>
<div style="margin-top:32px;font-size:13px;color:#333;">
  — {agent.agent_name or 'Your agent'}{(', ' + agent.business_name) if agent.business_name else ''}
</div>
</body></html>"""

    # Pull the agent's user email for Reply-To. Replies should go to the
    # agent, not to noreply@.
    agent_user = User.query.get(agent.user_id)
    agent_email = (agent_user.email if agent_user else '') or ''

    try:
        from email_service import send_email
        ok = send_email(
            to_email=to_email,
            subject=subject,
            html_content=html,
            reply_to=agent_email or None,
            email_type='agent_briefing_send',
            user_id=current_user.id,
        )
    except Exception as e:
        logging.error(f"Briefing send failed for briefing {b.id}: {e}", exc_info=True)
        return jsonify({'error': f"Send failed: {type(e).__name__}. Please try again."}), 500

    if not ok:
        return jsonify({
            'error': "Email service is unavailable. The briefing was not sent. The shareable link still works — you can copy it and send it yourself."
        }), 502

    # Stamp the send timestamp. Best-effort; success doesn't require it.
    try:
        b.sent_to_client_at = _dt.datetime.utcnow()
        # If this is the first time we've ever had a client email for
        # this briefing, persist it so future sends can default to it.
        if not b.client_email and to_email:
            b.client_email = to_email
        _db.session.commit()
    except Exception as e:
        logging.warning(f"Briefing send: timestamp commit failed: {e}")
        _db.session.rollback()

    return jsonify({
        'ok': True,
        'sent_at': b.sent_to_client_at.isoformat() if b.sent_to_client_at else None,
        'to': to_email,
    })


# v5.88.76 R2: public endpoint that returns the briefing payload for
# rendering at /agent-briefing/<token>. No auth check — the token IS
# the access credential, same pattern as the inspector report public
# endpoint. Increments view_count and stamps client_viewed_at on first
# view. Does NOT return inspection_text (too large, not needed for
# rendering — only the parsed analysis_json is rendered client-side).
@agent_bp.route('/api/agent/briefing/public/<token>', methods=['GET'])
def agent_briefing_public(token):
    """Public-by-token briefing payload, used by /agent-briefing/<token>."""
    from models import AgentBriefing, db as _db
    import json as _json
    import datetime as _dt

    b = AgentBriefing.query.filter_by(share_token=token).first()
    if not b:
        return jsonify({'error': 'Briefing not found.'}), 404

    # Track view. First view stamps client_viewed_at; every view
    # increments view_count. Best-effort — if the commit fails (e.g.,
    # transient DB hiccup), we still return the payload.
    try:
        if not b.client_viewed_at:
            b.client_viewed_at = _dt.datetime.utcnow()
        b.view_count = (b.view_count or 0) + 1
        _db.session.commit()
    except Exception as e:
        logging.warning(f"agent_briefing_public: view tracking commit failed for token={token[:8]}...: {e}")
        _db.session.rollback()

    # Parse analysis_json once; the template renders directly from it.
    analysis = None
    if b.analysis_json:
        try:
            analysis = _json.loads(b.analysis_json)
        except Exception as e:
            logging.warning(f"agent_briefing_public: analysis_json parse failed for token={token[:8]}...: {e}")

    # v5.88.77 R3: parse offer strategy. Returns null on parse failure
    # or when null (e.g., seller-side briefings don't have one yet, or
    # the strategy LLM call failed).
    offer_strategy = None
    if b.offer_strategy_json:
        try:
            offer_strategy = _json.loads(b.offer_strategy_json)
        except Exception as e:
            logging.warning(f"agent_briefing_public: offer_strategy_json parse failed for token={token[:8]}...: {e}")

    return jsonify({
        'property_address': b.property_address,
        'property_price': b.property_price,
        'representing': b.representing,
        'client_name': b.client_name,
        # NOT exposing client_email publicly — privacy
        'agent_commentary': b.agent_commentary,
        'agent_name': b.agent_name_on_report,
        'agent_business': b.agent_biz_on_report,
        # Budget tiers rendered only on the agent's view of their own
        # briefing — buyer/seller-facing pages don't need to see them.
        'budget': None,
        'analysis': analysis,
        'analysis_ready': analysis is not None,
        # v5.88.77 R3: offer strategy + bottom line. Both may be null
        # (seller-side, or LLM strategy generation failed).
        'offer_strategy': offer_strategy,
        'bottom_line': b.bottom_line,
        'created_at': b.created_at.isoformat() if b.created_at else None,
    })


# v5.88.76 R2: page route that serves the static briefing template.
# Mirrors the /agent-report/<token> pattern from v5.88.69 — the static
# HTML reads the token from window.location and fetches data via the
# public API. Flask just serves the static file; no server-side
# rendering needed.
@agent_bp.route('/agent-briefing/<token>')
def agent_briefing_page(token):
    """Public buyer/seller-facing briefing page.

    The token is read client-side from window.location.pathname by
    static/agent-briefing.html, which then fetches the briefing payload
    via /api/agent/briefing/public/<token>. Defensive: if the static
    file is missing, Flask's send_from_directory returns 404 cleanly.
    """
    from flask import send_from_directory
    return send_from_directory('static', 'agent-briefing.html')



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


# v5.88.69: Missing HTML page route — the frontend's "View" button in
# the agent portal links to `/agent-report/<token>`, but only the JSON
# API at `/api/agent/report/<token>` existed. Result: clicking View on
# any share returned 404. This is the agent-side equivalent of the
# inspector flow's `/inspector-report/<token>` page route. The static
# template `static/agent-report.html` already existed and fetches the
# JSON itself; it just needed a Flask route to serve it.
@agent_bp.route('/agent-report/<token>')
def agent_report_page(token):
    """Serve the buyer-facing agent-share report viewer page.

    The token is read client-side from the URL by static/agent-report.html,
    which then calls /api/agent/report/<token> for the actual data.
    """
    from flask import send_from_directory
    return send_from_directory('static', 'agent-report.html')


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
