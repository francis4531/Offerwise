"""
OfferWise Sharing Routes Blueprint
Extracted from app.py v5.74.44 for architecture cleanup.
"""

import os
import json
import logging
import time
import re
import secrets
import base64
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, send_from_directory, redirect, url_for, render_template, render_template_string, current_app, make_response
from flask_login import login_required, current_user
from models import db

logger = logging.getLogger(__name__)

sharing_bp = Blueprint('sharing', __name__)

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


def init_sharing_blueprint(app, admin_required_fn, api_admin_required_fn,
                       api_login_required_fn, dev_only_gate_fn, limiter):
    _admin_required_ref[0] = admin_required_fn
    _api_admin_required_ref[0] = api_admin_required_fn
    _api_login_required_ref[0] = api_login_required_fn
    _dev_only_gate_ref[0] = dev_only_gate_fn
    _limiter_ref[0] = limiter
    app.register_blueprint(sharing_bp)
    logger.info("✅ Sharing Routes blueprint registered")



@sharing_bp.route('/api/share/create', methods=['POST'])
@login_required
def create_share_link():
    """Generate a shareable analysis summary link"""
    try:
        data = request.get_json()
        property_id = data.get('property_id')
        sharer_name = (data.get('sharer_name') or '').strip()[:100]
        recipient_name = (data.get('recipient_name') or '').strip()[:100]
        personal_note = (data.get('personal_note') or '').strip()[:280]
        
        if not property_id:
            return jsonify({'error': 'property_id required'}), 400
        
        # Verify ownership
        prop = Property.query.filter_by(id=property_id, user_id=current_user.id).first()
        if not prop:
            return jsonify({'error': 'Property not found'}), 404
        
        # Get latest analysis
        analysis = Analysis.query.filter_by(property_id=property_id).order_by(Analysis.created_at.desc()).first()
        if not analysis:
            return jsonify({'error': 'No analysis found for this property'}), 404
        
        # Build snapshot with only the fields we want to expose
        import json as json_mod
        import traceback
        
        try:
            full_result = json_mod.loads(analysis.result_json)
        except Exception as parse_err:
            logging.error(f"❌ Share: Failed to parse analysis JSON for property {property_id}: {parse_err}")
            return jsonify({'error': 'Analysis data is corrupted'}), 500
        
        logging.info(f"🤝 Share: Building snapshot for property {property_id}, keys: {list(full_result.keys())}")
        
        risk_score = full_result.get('risk_score', {})
        if not isinstance(risk_score, dict):
            risk_score = {}
        offer_strategy = full_result.get('offer_strategy', {})
        if not isinstance(offer_strategy, dict):
            offer_strategy = {}
        transparency = full_result.get('transparency_report', {})
        if not isinstance(transparency, dict):
            transparency = {}
        
        # Top 3 findings: deal_breakers first, then red flags, then highest-risk categories
        top_findings = []
        try:
            deal_breakers = risk_score.get('deal_breakers') or []
            if isinstance(deal_breakers, list):
                for db_item in deal_breakers[:3]:
                    if isinstance(db_item, dict):
                        # Match frontend: db.issue || db.title || db.description
                        text = db_item.get('issue') or db_item.get('title') or db_item.get('description') or str(db_item)
                        top_findings.append({
                            'text': text,
                            'category': db_item.get('category', 'Critical'),
                            'severity': 'critical'
                        })
                    elif isinstance(db_item, str):
                        top_findings.append({
                            'text': db_item,
                            'category': 'Critical',
                            'severity': 'critical'
                        })
            
            red_flags = transparency.get('red_flags') or []
            if len(top_findings) < 3 and isinstance(red_flags, list):
                for rf in red_flags[:3 - len(top_findings)]:
                    # Match frontend: rf.flag || rf.issue || rf.title || rf.description
                    if isinstance(rf, str):
                        text = rf
                    elif isinstance(rf, dict):
                        text = rf.get('flag') or rf.get('issue') or rf.get('title') or rf.get('description') or str(rf)
                    else:
                        text = str(rf)
                    top_findings.append({
                        'text': text,
                        'category': 'Transparency',
                        'severity': 'elevated'
                    })
            
            cat_scores = risk_score.get('category_scores') or []
            if len(top_findings) < 3 and isinstance(cat_scores, list):
                # Match frontend: filter score > 50, sort descending
                cats = [c for c in cat_scores if isinstance(c, dict) and (c.get('score', 0) or 0) > 50]
                cats.sort(key=lambda c: c.get('score', 0) or 0, reverse=True)
                for cat in cats[:3 - len(top_findings)]:
                    score_val = round(cat.get('score', 0) or 0)
                    cat_name = cat.get('category', 'Unknown')
                    top_findings.append({
                        # Match frontend format: "{name} risk elevated ({score}%)"
                        'text': f"{cat_name} risk elevated ({score_val}%)",
                        'category': cat_name,
                        'severity': 'critical' if score_val > 70 else 'elevated'
                    })
        except Exception as findings_err:
            logging.warning(f"⚠️ Share: Error building top_findings: {findings_err}")
            # Continue without findings - not critical
        
        # Build the frozen snapshot
        # CRITICAL: OfferScore = 100 - risk_dna.composite_score (matching main analysis display)
        # risk_dna.composite_score is the RISK score (higher = worse)
        # OfferScore is the QUALITY score (higher = better) shown to users
        risk_dna = full_result.get('risk_dna', {})
        if not isinstance(risk_dna, dict):
            risk_dna = {}
        composite_score = float(risk_dna.get('composite_score', 0) or 0)
        offerscore = round(100 - composite_score)
        
        # Risk tier from composite_score using same thresholds as frontend getRiskTierFromComposite()
        if composite_score >= 90:
            risk_tier = 'CRITICAL'
        elif composite_score >= 75:
            risk_tier = 'HIGH'
        elif composite_score >= 60:
            risk_tier = 'ELEVATED'
        elif composite_score >= 40:
            risk_tier = 'MODERATE'
        elif composite_score >= 20:
            risk_tier = 'LOW'
        else:
            risk_tier = 'MINIMAL'
        
        snapshot = {
            'address': prop.address or 'Property',
            'price': prop.price or 0,
            'offerscore': offerscore,
            'risk_tier': risk_tier,
            'top_findings': top_findings[:3],
            'repair_cost_low': risk_score.get('total_repair_cost_low', 0) or 0,
            'repair_cost_high': risk_score.get('total_repair_cost_high', 0) or 0,
            'recommended_offer': offer_strategy.get('recommended_offer', 0) or 0,
            'offer_range_low': offer_strategy.get('offer_range_low', offer_strategy.get('recommended_offer', 0)) or 0,
            'offer_range_high': offer_strategy.get('offer_range_high', offer_strategy.get('recommended_offer', 0)) or 0,
            'discount_percentage': offer_strategy.get('discount_percentage', 0) or 0,
            'transparency_score': transparency.get('transparency_score', transparency.get('trust_score', None)),
            'contradictions_count': len(transparency.get('contradictions', transparency.get('red_flags', [])) or []),
            'analyzed_at': prop.analyzed_at.isoformat() if prop.analyzed_at else None
        }
        
        snapshot_str = json_mod.dumps(snapshot)
        
        # Create the share link
        sharer_display = sharer_name or (current_user.name if current_user.name else None) or current_user.email.split('@')[0]
        
        share = ShareLink.create_link(
            user_id=current_user.id,
            property_id=property_id,
            snapshot=snapshot_str,
            sharer_name=sharer_display,
            recipient_name=recipient_name or None,
            personal_note=personal_note or None
        )
        
        base_url = os.environ.get('BASE_URL', 'https://getofferwise.ai')
        share_url = f"{base_url}/opinion/{share.token}"
        
        logging.info(f"🤝 Share link created: {share.token} for property {property_id} by user {current_user.email}")
        
        return jsonify({
            'success': True,
            'share_url': share_url,
            'token': share.token,
            'expires_at': share.expires_at.isoformat() if share.expires_at else None
        })
        
    except Exception as e:
        import traceback
        logging.error(f"❌ Error creating share link: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Failed to create share link. Please try again.'}), 500


@sharing_bp.route('/opinion/<token>')
def view_shared_opinion(token):
    """Render the shared opinion page (public, no auth required)"""
    import json as json_mod
    
    share = ShareLink.query.filter_by(token=token).first()
    
    if not share or not share.is_valid():
        return render_template('shared_opinion_expired.html'), 404
    
    # Increment view counter
    share.record_view()
    
    # Parse snapshot
    snapshot = json_mod.loads(share.snapshot_json)
    
    return render_template('shared_opinion.html',
        token=token,
        sharer_name=share.sharer_name or 'Someone',
        recipient_name=share.recipient_name,
        personal_note=share.personal_note,
        snapshot=snapshot,
        share_url=f"{os.environ.get('BASE_URL', 'https://getofferwise.ai')}/opinion/{token}"
    )


@sharing_bp.route('/api/share/<token>/react', methods=['POST'])
@_limiter.limit("5 per hour")
def react_to_share(token):
    """Submit a reaction to a shared analysis (public, rate-limited)"""
    try:
        import json as json_mod
        import hashlib
        
        share = ShareLink.query.filter_by(token=token).first()
        
        if not share or not share.is_valid():
            return jsonify({'error': 'Share link not found or expired'}), 404
        
        data = request.get_json()
        reaction = data.get('reaction')
        
        if reaction not in ['good_deal', 'fair_price', 'walk_away']:
            return jsonify({'error': 'Invalid reaction'}), 400
        
        # Hash the IP for rate-limit dedup (don't store raw IP)
        ip_raw = request.headers.get('X-Forwarded-For', request.remote_addr) or 'unknown'
        ip_hash = hashlib.sha256(ip_raw.encode()).hexdigest()[:16]
        
        # Check if this IP already reacted to this token
        existing = json_mod.loads(share.reactions_json) if share.reactions_json else []
        if any(r.get('ip_hash') == ip_hash for r in existing):
            return jsonify({'error': 'Already submitted a reaction', 'already_reacted': True}), 409
        
        share.add_reaction(reaction, ip_hash)
        
        logging.info(f"🤝 Reaction '{reaction}' on share {token}")
        
        return jsonify({'success': True, 'reaction': reaction})
        
    except Exception as e:
        logging.error(f"❌ Error recording reaction: {e}")
        return jsonify({'error': 'Failed to record reaction'}), 500


@sharing_bp.route('/api/share/my-links')
@login_required
def get_my_share_links():
    """Get all share links created by the current user"""
    import json as json_mod
    
    links = ShareLink.query.filter_by(user_id=current_user.id, is_active=True)\
        .order_by(ShareLink.created_at.desc()).all()
    
    base_url = os.environ.get('BASE_URL', 'https://getofferwise.ai')
    
    result = []
    for link in links:
        reactions = json_mod.loads(link.reactions_json) if link.reactions_json else []
        result.append({
            'token': link.token,
            'share_url': f"{base_url}/opinion/{link.token}",
            'property_id': link.property_id,
            'sharer_name': link.sharer_name,
            'recipient_name': link.recipient_name,
            'view_count': link.view_count or 0,
            'reactions': reactions,
            'reaction_count': len(reactions),
            'created_at': link.created_at.isoformat(),
            'expires_at': link.expires_at.isoformat() if link.expires_at else None
        })
    
    return jsonify({'share_links': result})


@sharing_bp.route('/api/support/share', methods=['POST'])
@login_required
def submit_support_share():
    """User explicitly shares an analysis with the OfferWise support team.
    Only available to users who have completed a full paid analysis.
    """
    try:
        from models import SupportShare
        data = request.get_json()
        property_id = data.get('property_id')
        user_message = (data.get('message') or '').strip()[:1000]

        if not property_id:
            return jsonify({'error': 'property_id required'}), 400

        prop = Property.query.filter_by(id=property_id, user_id=current_user.id).first()
        if not prop:
            return jsonify({'error': 'Property not found'}), 404

        analysis = Analysis.query.filter_by(property_id=property_id)\
            .order_by(Analysis.created_at.desc()).first()
        if not analysis:
            return jsonify({'error': 'No analysis found for this property'}), 404

        # Only users who have completed a full paid analysis may contact support this way
        if analysis.status != 'completed':
            return jsonify({'error': 'Support sharing is only available for completed analyses.'}), 403

        full_result = json.loads(analysis.result_json or '{}')

        # Build the same summary snapshot used by ShareLink
        risk_score = full_result.get('risk_score', {}) if isinstance(full_result.get('risk_score'), dict) else {}
        offer_strategy = full_result.get('offer_strategy', {}) if isinstance(full_result.get('offer_strategy'), dict) else {}
        transparency = full_result.get('transparency_report', {}) if isinstance(full_result.get('transparency_report'), dict) else {}
        risk_dna = full_result.get('risk_dna', {}) if isinstance(full_result.get('risk_dna'), dict) else {}

        composite_score = float(risk_dna.get('composite_score', 0) or 0)
        offerscore = round(100 - composite_score)

        snapshot = {
            'address': prop.address or 'Property',
            'price': prop.price or 0,
            'offerscore': offerscore,
            'risk_tier': risk_score.get('risk_tier', analysis.risk_tier or 'UNKNOWN'),
            'repair_cost_low': risk_score.get('total_repair_cost_low', 0) or 0,
            'repair_cost_high': risk_score.get('total_repair_cost_high', 0) or 0,
            'recommended_offer': offer_strategy.get('recommended_offer', 0) or 0,
            'discount_percentage': offer_strategy.get('discount_percentage', 0) or 0,
            'transparency_score': transparency.get('transparency_score', transparency.get('trust_score')),
            'analyzed_at': prop.analyzed_at.isoformat() if prop.analyzed_at else None,
        }

        # Collect parsed findings summary (no raw file paths)
        findings_summary = []
        for f in full_result.get('findings', [])[:50]:
            if isinstance(f, dict):
                findings_summary.append({
                    'category': f.get('category', ''),
                    'severity': f.get('severity', ''),
                    'description': f.get('description', '')[:200],
                    'estimated_cost_low': f.get('estimated_cost_low'),
                    'estimated_cost_high': f.get('estimated_cost_high'),
                })

        share = SupportShare(
            user_id=current_user.id,
            property_id=property_id,
            user_message=user_message or None,
            snapshot_json=json.dumps(snapshot),
            full_result_json=analysis.result_json,
            findings_json=json.dumps(findings_summary),
            status='open',
        )
        db.session.add(share)
        db.session.commit()

        logging.info(f"🆘 Support share #{share.id} from {current_user.email} for property {property_id}")
        return jsonify({'success': True, 'share_id': share.id})

    except Exception as e:
        logging.error(f"❌ Error creating support share: {e}")
        return jsonify({'error': 'Failed to share analysis. Please try again.'}), 500
