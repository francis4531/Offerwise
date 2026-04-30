"""
OfferWise Negotiation Routes Blueprint
=====================================
Extracted from app.py to reduce monolith size.
Contains all /api/negotiation/* and /negotiation/* page routes.
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

negotiation_bp = Blueprint('negotiation', __name__)
logger = logging.getLogger(__name__)

# ── Injected dependencies ──────────────────────────────────────────
_login_required     = None
_admin_required     = None
_limiter            = None

_login_req_dec = DeferredDecorator(lambda: _login_required)
_admin_req_dec = DeferredDecorator(lambda: _admin_required)


def init_negotiation_blueprint(app, db_ref, login_required_fn, admin_required_fn):
    global _login_required, _admin_required, _limiter
    _login_required = login_required_fn
    _admin_required = admin_required_fn
    app.register_blueprint(negotiation_bp)
    logging.info("✅ negotiation blueprint registered")


@negotiation_bp.route('/api/negotiation/full-package', methods=['POST'])
@_login_req_dec
def get_negotiation_full_package():
    """
    Generate complete negotiation package with AI strategy + formatted documents.
    
    This is the premium feature combining:
    - AI-powered strategy (leverage points, counter strategies, opening scripts)
    - Professionally formatted documents (offer letter, talking points, agent email)
    """
    try:
        from negotiation_hub import get_negotiation_hub
        
        data = request.get_json()
        analysis = data.get('analysis')
        
        if not analysis:
            return jsonify({'success': False, 'error': 'Missing analysis data'}), 400
        
        buyer_profile = data.get('buyer_profile')
        style = data.get('style', 'balanced')
        
        logging.info(f"🎯 Full negotiation package for user {current_user.id}")
        logging.info(f"📍 Property: {analysis.get('property_address', 'Unknown')}")
        logging.info(f"🎨 Style: {style}")
        
        hub = get_negotiation_hub()
        result = hub.generate_full_package(analysis, buyer_profile, style)
        
        if result.get('success'):
            logging.info(f"✅ Full package generated - AI: {result.get('ai_enabled')}")
        
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"❌ Full package error: {e}")
        return jsonify({'success': False, 'error': 'An internal error occurred. Please try again.'}), 500



@negotiation_bp.route('/api/negotiation/strategy', methods=['POST'])
@_login_req_dec
def get_negotiation_strategy():
    """Generate AI strategy only (no formatted documents)."""
    try:
        from negotiation_hub import get_negotiation_hub
        
        data = request.get_json()
        analysis = data.get('analysis')
        
        if not analysis:
            return jsonify({'success': False, 'error': 'Missing analysis data'}), 400
        
        style = data.get('style', 'balanced')
        
        hub = get_negotiation_hub()
        result = hub.generate_strategy(analysis, data.get('buyer_profile'), style)
        
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"❌ Strategy error: {e}")
        return jsonify({'success': False, 'error': 'An internal error occurred. Please try again.'}), 500



@negotiation_bp.route('/api/negotiation/document', methods=['POST'])
@_login_req_dec
def get_negotiation_document():
    """Generate single document (no AI, instant)."""
    try:
        from negotiation_hub import get_negotiation_hub
        
        data = request.get_json()
        analysis = data.get('analysis')
        doc_type = data.get('document_type', 'offer_letter')
        
        if not analysis:
            return jsonify({'success': False, 'error': 'Missing analysis data'}), 400
        
        hub = get_negotiation_hub()
        result = hub.generate_document(
            analysis,
            doc_type,
            data.get('buyer_name', 'Buyer'),
            data.get('context')
        )
        
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"❌ Document error: {e}")
        return jsonify({'success': False, 'error': 'An internal error occurred. Please try again.'}), 500



@negotiation_bp.route('/api/negotiation/tips', methods=['POST'])
@_login_req_dec
def get_negotiation_tips():
    """Get instant negotiation tips (no AI)."""
    try:
        from negotiation_hub import get_negotiation_hub
        
        data = request.get_json()
        analysis = data.get('analysis')
        
        if not analysis:
            return jsonify({'success': False, 'error': 'Missing analysis data'}), 400
        
        hub = get_negotiation_hub()
        result = hub.get_quick_tips(analysis)
        
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"❌ Tips error: {e}")
        return jsonify({'success': False, 'error': 'An internal error occurred. Please try again.'}), 500


# Legacy endpoint aliases for backwards compatibility
@negotiation_bp.route('/api/negotiation-coach', methods=['POST'])
@_login_req_dec
def get_negotiation_coaching_legacy():
    """Legacy endpoint - redirects to new unified API."""
    return get_negotiation_full_package()





@negotiation_bp.route('/api/negotiation-coach/quick-tips', methods=['POST'])
@_login_req_dec
def get_quick_negotiation_tips_legacy():
    """Legacy endpoint - redirects to new unified API."""
    return get_negotiation_tips()


# ============================================================================
# AGENTIC DOCUMENTS — Counter-Offer Addendum + MCP Actions (v5.76.00)
# ============================================================================


@negotiation_bp.route('/api/objection/draft', methods=['POST'])
@_login_req_dec
def draft_objection_letter():
    """Draft a negotiation objection letter from the buyer's analysis."""
    try:
        import anthropic as _anth
        data = request.get_json(silent=True) or {}
        analysis_id = data.get('analysis_id')
        property_id = data.get('property_id')
        agent_name  = (data.get('agent_name') or 'Agent').strip()
        style       = data.get('style', 'balanced')

        # Resolve analysis record
        if analysis_id:
            try: analysis_id = int(analysis_id)
            except (TypeError, ValueError): pass
            analysis_rec = Analysis.query.filter_by(id=analysis_id, user_id=current_user.id).first()
        elif property_id:
            try: property_id = int(property_id)
            except (TypeError, ValueError): pass
            prop_check = Property.query.filter_by(id=property_id, user_id=current_user.id).first()
            if not prop_check:
                return jsonify({'success': False, 'error': 'Property not found.'}), 404
            analysis_rec = Analysis.query.filter_by(property_id=property_id).order_by(Analysis.created_at.desc()).first()
        else:
            return jsonify({'success': False, 'error': 'analysis_id or property_id is required.'}), 400

        if not analysis_rec:
            return jsonify({'success': False, 'error': 'Analysis not found.'}), 404

        result = json.loads(analysis_rec.result_json or '{}')
        prop = Property.query.get(analysis_rec.property_id)
        address = prop.address if prop else 'the property'
        asking_price = (prop.price if prop else 0) or 0

        repair_estimate = result.get('repair_estimate', {}) or {}
        total_low  = repair_estimate.get('total_low', 0) or 0
        total_high = repair_estimate.get('total_high', 0) or 0
        breakdown  = repair_estimate.get('breakdown', []) or []
        offer_strategy = result.get('offer_strategy', {}) or {}
        rec_offer = offer_strategy.get('recommended_offer') or (asking_price * 0.97 if asking_price else 0) or 0
        deal_breakers = (result.get('risk_score', {}) or {}).get('deal_breakers', []) or []
        leverage_pts  = (result.get('negotiation_strategy', {}) or {}).get('leverage_points', []) or []

        repair_lines = chr(10).join([
            "  - " + item.get("system","").title() + ": $" + format(item.get("cost_low",0), ",.0f") + "-$" + format(item.get("cost_high",0), ",.0f") +
            " (" + ("CRITICAL" if item.get("severity")=="critical" else "Major" if item.get("severity")=="major" else "Minor") + ")"
            for item in breakdown[:10]
        ]) or "  - No itemized repairs available"

        db_lines = chr(10).join([
            "  - " + (d.get("issue", str(d)) if isinstance(d, dict) else str(d))
            for d in deal_breakers[:4]
        ]) or "  - None flagged"

        leverage_lines = chr(10).join([
            "  - " + (lp.get("point", str(lp)) if isinstance(lp, dict) else str(lp))
            for lp in leverage_pts[:4]
        ]) or "  - Not specified"

        tone = {
            "firm": "Firm and assertive. State positions directly. No softening.",
            "balanced": "Professional and evidence-based. Collaborative but clear on the ask.",
            "conciliatory": "Warm and cooperative. Acknowledge seller position, but be clear.",
        }.get(style, "Professional and evidence-based.")

        buyer_name = ((current_user.name or current_user.email or "Buyer") or "Buyer").split("@")[0].strip()

        prompt = (
            "Draft a professional real estate negotiation letter from a buyer to the seller's listing agent.\n\n"
            "FROM: " + buyer_name + " (Buyer)\n"
            "TO: " + agent_name + " (Listing Agent)\n"
            "RE: " + address + "\n"
            "ASKING PRICE: $" + format(asking_price, ",.0f") + "\n"
            "BUYER'S RECOMMENDED OFFER: $" + format(rec_offer, ",.0f") + "\n"
            "TONE: " + tone + "\n\n"
            "DOCUMENTED REPAIR ISSUES:\n" + repair_lines + "\n\n"
            "DEAL-BREAKER ITEMS:\n" + db_lines + "\n\n"
            "BUYER'S LEVERAGE POINTS:\n" + leverage_lines + "\n\n"
            "TOTAL REPAIR ESTIMATE: $" + format(total_low, ",.0f") + " - $" + format(total_high, ",.0f") + "\n\n"
            "Write a complete professional letter that:\n"
            "1. Opens with a professional salutation to " + agent_name + "\n"
            "2. References the property and buyer's interest\n"
            "3. Presents documented repair findings factually\n"
            "4. Makes a specific ask: price reduction OR seller repairs before close\n"
            "5. Flags deal-breaker items as conditions of proceeding\n"
            "6. Closes with buyer name and call to respond within 3 business days\n"
            "7. Is 3-4 paragraphs, professional\n\n"
            "Return ONLY the letter text. No preamble, no explanation."
        )

        from ai_client import get_ai_response as _get_ai
        letter = _get_ai(prompt, max_tokens=1200)

        subject = "Re: " + address + " — Repair Request"
        return jsonify({"success": True, "letter": letter, "subject": subject})

    except Exception as e:
        logging.error("Objection letter error: " + str(e), exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred.", "detail": str(e)}), 500



@negotiation_bp.route('/api/addendum/draft', methods=['POST'])
@_login_req_dec
def draft_repair_addendum():
    """
    AI drafts a state-appropriate Request for Repair / repair addendum
    from the buyer's analysis. This is the counter-offer addendum feature
    from the agentic roadmap — AI makes clause selection decisions, buyer
    gets a signable document.

    POST body:
        analysis_id   (int, required)
        buyer_name    (str)
        buyer_email   (str)
        agent_name    (str, optional)
        style         (str) 'firm' | 'balanced' | 'conciliatory' — default 'balanced'

    Returns:
        { success, document: { title, body, state, form_name, clauses[] } }
    """
    try:
        import anthropic
        data = request.get_json(silent=True) or {}
        analysis_id = data.get('analysis_id')
        property_id = data.get('property_id')

        # Accept either analysis_id (preferred) or property_id as fallback
        # property_id fallback handles analyses loaded from dashboard before analysis_id
        # was included in the response payload
        if analysis_id:
            try:
                analysis_id = int(analysis_id)
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': 'Invalid analysis_id.'}), 400
            analysis_rec = Analysis.query.filter_by(
                id=analysis_id, user_id=current_user.id
            ).first()
        elif property_id:
            try:
                property_id = int(property_id)
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': 'Invalid property_id.'}), 400
            prop_check = Property.query.filter_by(
                id=property_id, user_id=current_user.id
            ).first()
            if not prop_check:
                return jsonify({'success': False, 'error': 'Property not found.'}), 404
            analysis_rec = Analysis.query.filter_by(
                property_id=property_id
            ).order_by(Analysis.created_at.desc()).first()
        else:
            return jsonify({'success': False, 'error': 'analysis_id or property_id is required.'}), 400

        if not analysis_rec:
            return jsonify({'success': False, 'error': 'Analysis not found.'}), 404

        result = json.loads(analysis_rec.result_json or '{}')

        # Addendum requires inspection data — not available for address-only analyses
        if result.get('analysis_depth') == 'address_only':
            return jsonify({
                'success': False,
                'error': 'A repair addendum requires an inspection report. Please upload your inspection report and run a new analysis to use this feature.'
            }), 400

        prop = Property.query.get(analysis_rec.property_id)
        address = prop.address if prop else ''
        asking_price = prop.price if prop else 0
        asking_price = asking_price or 0  # Guard against None

        # Detect state from address ZIP
        import re as _re
        from state_disclosures import detect_state_from_zip, get_state_context
        zip_match = _re.search(r'\b(\d{5})\b', address)
        zip_code = zip_match.group(1) if zip_match else ''
        state_code = detect_state_from_zip(zip_code) or 'CA'
        state_ctx = get_state_context(state_code)

        # Extract repair items and leverage data — all fields may be None for address-only analyses
        repair_estimate = result.get('repair_estimate') or {}
        breakdown  = repair_estimate.get('breakdown', []) or []
        total_low  = repair_estimate.get('total_low', 0) or 0
        total_high = repair_estimate.get('total_high', 0) or 0
        risk_score = result.get('risk_score') or {}
        deal_breakers = risk_score.get('deal_breakers', []) or []
        offer_strategy = result.get('offer_strategy') or {}
        rec_offer = (
            offer_strategy.get('recommended_offer') or
            result.get('recommended_offer') or
            (asking_price * 0.97 if asking_price else 0)
        ) or 0

        buyer_name = (data.get('buyer_name') or current_user.email or 'Buyer').strip()
        agent_name = (data.get('agent_name') or '').strip()
        style = data.get('style', 'balanced')

        # Build AI prompt for state-aware addendum drafting
        repair_list = '\n'.join([
            f"- {item.get('system','').title()}: ${item.get('cost_low',0):,.0f}–${item.get('cost_high',0):,.0f} "
            f"({'CRITICAL' if item.get('severity')=='critical' else 'Major' if item.get('severity')=='major' else 'Minor'})"
            for item in breakdown[:12]
        ]) or '- No itemized repairs available'

        deal_breaker_list = '\n'.join([
            f"- {db.get('issue', db) if isinstance(db, dict) else str(db)}"
            for db in deal_breakers[:5]
        ]) or '- None flagged'

        tone_instruction = {
            'firm': 'Use firm, assertive language. Frame every request as non-negotiable based on documented evidence. Do not soften.',
            'balanced': 'Use professional, reasoned language. Frame requests as fair and evidence-based. Leave room for negotiation.',
            'conciliatory': 'Use cooperative language. Frame requests as reasonable asks while acknowledging the seller\'s position.',
        }.get(style, 'Use professional, reasoned language.')

        prompt = f"""You are a real estate transaction attorney drafting a formal Request for Repair addendum for a buyer in {state_ctx.state_name}.

PROPERTY: {address}
ASKING PRICE: ${asking_price:,.0f} | RECOMMENDED OFFER: ${rec_offer:,.0f}
STATE: {state_ctx.state_name} ({state_code}) | REFERENCE FORM: {state_ctx.primary_form}
TONE: {tone_instruction}

DOCUMENTED REPAIR ITEMS:
{repair_list}

DEAL BREAKERS:
{deal_breaker_list}

TOTAL REPAIR ESTIMATE: ${total_low:,.0f} – ${total_high:,.0f}

STRICT FORMATTING RULES — these are absolute:
- NO markdown whatsoever. No asterisks, no hashes, no underscores, no dashes as bullets, no angle brackets, no backticks.
- Use ONLY plain text with the exact structural markers below.
- Every section header must be on its own line in ALL CAPS followed by a colon, like: RECITALS:
- Every numbered clause starts with the clause number and a period, like: 1.
- Blank lines between clauses for readability.
- Use parentheses for alternatives, like: (repair) or (credit).

DOCUMENT STRUCTURE — follow this exactly:

REQUEST FOR REPAIR
[one blank line]
Property: {address}
Buyer: {buyer_name}
Seller: [SELLER NAME]
Date of Inspection: [DATE]
Inspection Contingency Deadline: [DATE]
[one blank line]

RECITALS:
[One paragraph establishing that Buyer received the inspection report and disclosure documents, and is submitting this request pursuant to the inspection contingency.]
[one blank line]

REPAIR REQUESTS:
[Numbered clauses, one per repair item or group. Each clause: name the defect precisely, state the documented evidence, specify remedy as repair by licensed contractor with permit OR credit at stated dollar amount, give deadline tied to contingency removal.]
[one blank line]

DOCUMENTATION REQUIREMENTS:
[One clause: Seller shall provide Buyer with copies of all contractor invoices, permits obtained, and final inspection sign-offs no fewer than five (5) calendar days prior to Close of Escrow.]
[one blank line]

BUYER'S PREFERRED RESOLUTION:
[One sentence summarizing total credit requested as alternative to repairs: ${total_high:,.0f} as a purchase price reduction or closing cost credit in lieu of all repairs above.]
[one blank line]

SIGNATURES:

Buyer: _________________________ Date: ____________

Seller: _________________________ Date: ____________

[one blank line]
This request is submitted pursuant to the inspection contingency and does not constitute a waiver of any rights thereunder.

Return ONLY the document text. Absolutely no markdown. No preamble. No explanation."""

        from ai_client import get_ai_response as _get_ai
        doc_text = _get_ai(prompt, max_tokens=1400).strip()

        return jsonify({
            'success': True,
            'document': {
                'title': f'Request for Repair — {address}',
                'body': doc_text,
                'state': state_code,
                'state_name': state_ctx.state_name,
                'form_name': state_ctx.primary_form,
                'repair_total_low': total_low,
                'repair_total_high': total_high,
                'style': style,
            }
        })

    except Exception as e:
        logging.error(f"❌ Addendum draft error: {e}", exc_info=True)
        # Include specific error in dev mode so it's visible in browser
        import os as _os2
        err_msg = str(e) if _os2.environ.get('FLASK_ENV') == 'development' else 'An internal error occurred. Please try again.'
        return jsonify({'success': False, 'error': err_msg, 'detail': str(e)}), 500

