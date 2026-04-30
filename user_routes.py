"""
OfferWise User Routes Blueprint
=====================================
Extracted from app.py to reduce monolith size.
Contains all /api/user/* and /user/* page routes.
"""

import os
import re
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from flask import Blueprint, jsonify, request, redirect, send_from_directory, current_app
from flask_login import login_required, current_user, logout_user
from blueprint_helpers import DeferredDecorator

from models import (
    db, User, Inspector, Contractor, ContractorLead, Analysis, Property,
    Agent, AgentShare, InspectorReport, PropertyWatch,
    Comparison, ConsentRecord, Document, Referral, UsageRecord,
)

DEVELOPER_EMAILS = []

def api_login_required(f):
    from app import api_login_required as _alr; return _alr(f)

def dev_only_gate(f):
    from app import dev_only_gate as _dog; return _dog(f)

def _is_admin():
    from app import is_admin; return is_admin()


user_bp = Blueprint('user', __name__)
logger = logging.getLogger(__name__)

from flask_login import logout_user

# ── Injected dependencies ──────────────────────────────────────────
_login_required     = None
_admin_required     = None
_limiter            = None

_login_req_dec = DeferredDecorator(lambda: _login_required)
_admin_req_dec = DeferredDecorator(lambda: _admin_required)


def init_user_blueprint(app, db_ref, login_required_fn, admin_required_fn):
    global _login_required, _admin_required, _limiter
    _login_required = login_required_fn
    _admin_required = admin_required_fn
    app.register_blueprint(user_bp)
    logging.info("✅ user blueprint registered")


@user_bp.route('/api/user/roles')
@_login_req_dec
def get_user_roles():
    """Return all roles this user has — used by the nav role switcher."""
    roles = []
    logging.info(f"🔀 /api/user/roles called for user {current_user.email} (id={current_user.id})")

    # Always has buyer role
    credits = getattr(current_user, 'analysis_credits', 0) or 0
    roles.append({
        'role':   'buyer',
        'label':  'Buyer Dashboard',
        'icon':   '🏠',
        'url':    '/dashboard',
        'badge':  f"{credits} credit{'s' if credits != 1 else ''}",
        'active': True,
    })

    # Inspector role
    insp = Inspector.query.filter_by(user_id=current_user.id).first()
    logging.info(f"🔀   inspector record: {insp}")
    if insp:
        plan_label = 'Pro ⭐' if insp.plan == 'inspector_pro' else 'Free'
        quota_str  = 'Unlimited' if insp.monthly_quota == -1 else f"{insp.monthly_used}/{insp.monthly_quota} used"
        roles.append({
            'role':   'inspector',
            'label':  'Inspector Portal',
            'icon':   '🔍',
            'url':    '/inspector-portal',
            'badge':  f"{plan_label} · {quota_str}",
            'active': True,
        })

    # Contractor role
    has_contractor = False
    try:
        contractor = Contractor.query.filter_by(email=current_user.email).first()
    except Exception as _ce:
        logging.warning(f"🔀   contractor query failed (migration pending?): {_ce}")
        contractor = None
    logging.info(f"🔀   contractor record: {contractor}")
    if contractor:
        has_contractor = True
        plan_labels = {
            'contractor_starter':    'Starter',
            'contractor_pro':        'Pro',
            'contractor_enterprise': 'Enterprise',
        }
        plan_label = plan_labels.get(contractor.plan, 'Free')
        status_str = contractor.status or 'pending'
        roles.append({
            'role':   'contractor',
            'label':  'Contractor Portal',
            'icon':   '🔧',
            'url':    '/contractor-portal',
            'badge':  f"{plan_label} · {status_str.capitalize()}",
            'active': contractor.status == 'active',
        })

    # Agent role — added v5.75.83
    try:
        from models import Agent as _Agent
        agent = _Agent.query.filter_by(user_id=current_user.id).first()
    except Exception:
        agent = None
    logging.info(f"🔀   agent record: {agent}")
    if agent:
        ag_plan  = 'Pro ⭐' if agent.plan == 'agent_pro' else 'Free'
        ag_quota = f"{agent.monthly_used or 0}/{agent.monthly_quota or 10} used"
        roles.append({
            'role':   'agent',
            'label':  'Agent Portal',
            'icon':   '🏡',
            'url':    '/agent-portal',
            'badge':  f"{ag_plan} · {ag_quota}",
            'active': True,
        })

    # Fix buyer URL — buyer portal is /settings not /dashboard
    for r in roles:
        if r['role'] == 'buyer':
            r['url'] = '/settings'

    # Determine primary role (smart login redirect)
    primary = 'buyer'
    if insp and credits == 0:
        primary = 'inspector'
    elif agent and credits == 0:
        primary = 'agent'
    elif has_contractor and contractor and contractor.status == 'active' and credits == 0:
        primary = 'contractor'

    return jsonify({'roles': roles, 'primary_role': primary})



@user_bp.route('/api/user/credits')
@api_login_required  # Use API-friendly decorator
def get_user_credits():
    """Get current user's credit balance"""
    logging.info("")
    logging.info("💳" * 50)
    logging.info("💳 API: GET /api/user/credits")
    logging.info("💳" * 50)
    logging.info(f"📧 User Email: {current_user.email}")
    logging.info(f"🆔 User ID: {current_user.id}")
    logging.info(f"🎫 Tier: {current_user.tier}")
    logging.info(f"💰 Credits in DB: {current_user.analysis_credits}")
    logging.info(f"🔐 Authenticated: {current_user.is_authenticated}")
    
    # Check if user is a developer (unlimited credits)
    # Uses global DEVELOPER_EMAILS
    dev_emails = DEVELOPER_EMAILS
    is_developer = current_user.email.lower() in dev_emails
    
    response_data = {
        'credits': current_user.analysis_credits,
        'subscription_plan': getattr(current_user, 'subscription_plan', 'free') or 'free',
        'analyses_this_month': getattr(current_user, 'analyses_this_month', 0) or 0,
        'analyses_reset_at': current_user.analyses_reset_at.isoformat() if getattr(current_user, 'analyses_reset_at', None) else None,
        'total_credits_purchased': current_user.total_credits_purchased or 0,
        'user_id': current_user.id,
        'email': current_user.email,
        'authenticated': True,
        'has_paid': bool(current_user.stripe_customer_id) or is_developer or (current_user.analysis_credits >= 100),
        'is_developer': is_developer,  # lets frontend show ∞ instead of a credit count
    }
    
    logging.info(f"📤 Returning: {response_data}")
    logging.info("💳" * 50)
    logging.info("")
    
    return jsonify(response_data)


@user_bp.route('/api/user/referrals')
@_login_req_dec
def get_user_referrals():
    """Get user's referral stats"""
    try:
        from referral_service import ReferralService
        
        # Get user's referrals
        referrals = Referral.query.filter_by(referrer_id=current_user.id).all()
        
        # Count completed vs pending
        completed = sum(1 for r in referrals if r.status == 'completed')
        pending = sum(1 for r in referrals if r.status == 'pending')
        
        # Calculate total earned
        total_earned = ReferralService.calculate_total_earnings(current_user)
        
        # Get current tier
        tier_info = ReferralService.get_tier_info(current_user.referral_tier or 0)
        
        return jsonify({
            'total_referrals': len(referrals),
            'completed_referrals': completed,
            'pending_referrals': pending,
            'total_earned': total_earned,
            'current_tier': current_user.referral_tier or 0,
            'tier_name': tier_info.get('name', 'Starter'),
            'referral_code': current_user.referral_code,
            'referral_url': ReferralService.get_referral_url(current_user)
        })
    except Exception as e:
        logging.error(f"Referral stats error: {e}")
        return jsonify({
            'total_referrals': 0,
            'total_earned': 0,
            'pending_referrals': 0,
            'error': 'An internal error occurred. Please try again.'
        })


@user_bp.route('/api/user/complete-onboarding', methods=['POST'])
@_login_req_dec
def complete_onboarding():
    """Mark user's onboarding as complete"""
    logging.info("")
    logging.info("=" * 100)
    logging.info("🎯 COMPLETE ONBOARDING ENDPOINT CALLED")
    logging.info("=" * 100)
    logging.info(f"📧 User Email: {current_user.email}")
    logging.info(f"🆔 User ID: {current_user.id}")
    
    # CRITICAL: Check if columns exist
    logging.info("")
    logging.info("🔍 CHECKING DATABASE SCHEMA...")
    has_onboarding_completed = hasattr(current_user, 'onboarding_completed')
    has_onboarding_completed_at = hasattr(current_user, 'onboarding_completed_at')
    
    logging.info(f"   Has 'onboarding_completed' attribute? {has_onboarding_completed}")
    logging.info(f"   Has 'onboarding_completed_at' attribute? {has_onboarding_completed_at}")
    
    if not has_onboarding_completed or not has_onboarding_completed_at:
        logging.error("")
        logging.error("🚨🚨🚨 CRITICAL DATABASE SCHEMA ERROR 🚨🚨🚨")
        logging.error("❌ Required columns are MISSING from users table!")
        logging.error("❌ Database migration was NEVER RUN!")
        logging.error("")
        logging.error("🔧 TO FIX:")
        logging.error("   1. Run: python migrate_add_onboarding.py")
        logging.error("   2. Or manually add columns:")
        logging.error("      ALTER TABLE users ADD COLUMN onboarding_completed BOOLEAN DEFAULT FALSE;")
        logging.error("      ALTER TABLE users ADD COLUMN onboarding_completed_at TIMESTAMP;")
        logging.error("")
        logging.error("⚠️  ONBOARDING WILL REPEAT UNTIL MIGRATION IS RUN!")
        logging.error("=" * 100)
        
        return jsonify({
            'success': False,
            'error': 'Database schema error: onboarding columns missing. Migration required.',
            'migration_needed': True
        }), 500
    
    logging.info(f"📊 BEFORE UPDATE:")
    logging.info(f"   onboarding_completed: {current_user.onboarding_completed}")
    logging.info(f"   onboarding_completed_at: {current_user.onboarding_completed_at}")
    
    try:
        current_user.onboarding_completed = True
        current_user.onboarding_completed_at = datetime.utcnow()

        
        logging.info(f"")
        logging.info(f"✏️  SETTING FLAGS:")
        logging.info(f"   onboarding_completed = True")
        logging.info(f"   onboarding_completed_at = {current_user.onboarding_completed_at}")
        logging.info(f"")
        logging.info(f"💾 COMMITTING TO DATABASE...")
        
        db.session.commit()
        
        logging.info(f"✅ DATABASE COMMIT SUCCESSFUL")
        logging.info(f"")
        logging.info(f"🔍 VERIFYING (reading from DB)...")
        db.session.refresh(current_user)
        
        logging.info(f"📊 AFTER UPDATE (from database):")
        logging.info(f"   onboarding_completed: {current_user.onboarding_completed}")
        logging.info(f"   onboarding_completed_at: {current_user.onboarding_completed_at}")
        
        if current_user.onboarding_completed:
            logging.info(f"")
            logging.info(f"✅✅✅ ONBOARDING COMPLETED SUCCESSFULLY ✅✅✅")
            logging.info(f"🎉 User {current_user.email} should NOT see onboarding on next login")
        else:
            logging.error(f"")
            logging.error(f"❌❌❌ CRITICAL: FLAG NOT SET IN DATABASE ❌❌❌")
            logging.error(f"🚨 Something went wrong with the database commit!")
        
        logging.info("=" * 100)
        logging.info("")
        
        return jsonify({'success': True, 'message': 'Onboarding completed'})
    except Exception as e:
        logging.error("")
        logging.error("=" * 100)
        logging.error(f"❌❌❌ ERROR COMPLETING ONBOARDING ❌❌❌")
        logging.error(f"Error: {e}")
        logging.error(f"User: {current_user.email}")
        logging.error("=" * 100)
        logging.error("")
        logging.exception(e)
        db.session.rollback()
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@user_bp.route('/api/user/analyses', methods=['GET'])
@_login_req_dec
def get_user_analyses():
    """
    Get all analyses for the current user.
    Returns analyses in the format expected by dashboard.html and settings.html.
    On transient SSL errors, retries once with a fresh database connection.
    """
    for _attempt in range(2):
        try:
            # Get all properties for current user that have been analyzed
            properties = Property.query.filter_by(user_id=current_user.id).all()

            analyses = []
            for prop in properties:
                # Get most recent analysis for this property
                analysis = Analysis.query.filter_by(
                    property_id=prop.id
                ).order_by(Analysis.created_at.desc()).first()

                if analysis and prop.analyzed_at:
                    import json as _json
                    try:
                        result_json = _json.loads(analysis.result_json or '{}')
                        offer_strategy    = result_json.get('offer_strategy', {})
                        recommended_offer = offer_strategy.get('recommended_offer', prop.price)
                        risk_dna_data     = result_json.get('risk_dna', {})
                        risk_composite    = risk_dna_data.get('composite_score') if risk_dna_data else None
                        if risk_composite is None:
                            risk_score_data = result_json.get('risk_assessment', {})
                            risk_composite  = risk_score_data.get('overall_risk_score', 50)

                        # Compute numeric offer_score: stored column first, then derive from composite
                        numeric_risk  = float(risk_composite or 50)
                        offer_score_n = float(analysis.offer_score) if analysis.offer_score is not None \
                                        else round(100 - numeric_risk)

                        # Inject analysis_id into full_result so agentic actions
                        # (addendum, objection letter) work when loaded from history.
                        # result_json is stored before analysis_id was appended, so
                        # window._owAnalysisId would be null without this injection.
                        result_json['analysis_id'] = analysis.id
                        result_json['property_id'] = prop.id

                        analyses.append({
                            'id':                 analysis.id,   # always integer — timestamp ID caused float mismatch in viewAnalysis
                            'analysis_id':        analysis.id,
                            'property_id':        prop.id,
                            'property_address':   prop.address or '',
                            'asking_price':       prop.price or 0,
                            'recommended_offer':  recommended_offer,
                            'risk_score':         numeric_risk,   # always a number
                            'offer_score':        offer_score_n,  # always a number
                            'analyzed_at':        analysis.created_at.isoformat() if analysis.created_at else '',
                            'status':             analysis.status or 'completed',
                            'full_result':        result_json,
                        })
                    except Exception as parse_err:
                        logging.warning(f"Could not parse analysis {analysis.id}: {parse_err}")
                        continue

            analyses.sort(key=lambda x: x['analyzed_at'], reverse=True)
            logging.info(f"✅ Returned {len(analyses)} analyses for user {current_user.id}")
            return jsonify({'analyses': analyses, 'count': len(analyses)})

        except Exception as e:
            err_str = str(e)
            is_ssl = ('SSL' in err_str or 'decryption' in err_str or
                      'bad record mac' in err_str or 'OperationalError' in type(e).__name__)
            if _attempt == 0 and is_ssl:
                logging.warning(f"⚠️ SSL/connection error on analyses fetch, retrying: {e}")
                try:
                    db.session.remove()
                except Exception:
                    pass
                continue
            logging.error(f"❌ Error fetching user analyses: {e}")
            return jsonify({
                'error':   'Failed to fetch analyses',
                'message': 'An internal error occurred. Please try again.',
                'analyses': [],
            }), 500


@user_bp.route('/api/user/analyses', methods=['POST'])
@_login_req_dec
def save_user_analysis():
    """
    Save an analysis from frontend (localStorage sync to backend).
    
    Frontend sends analysis in this format:
    {
        "id": "timestamp",
        "property_address": "123 Main St",
        "asking_price": 500000,
        "recommended_offer": 475000,
        "risk_score": {...},
        "analyzed_at": "2026-01-20T10:00:00",
        "full_result": {...}
    }
    
    This is called when user completes an analysis in app.html to sync to backend.
    Note: The main analysis is already saved via /api/analyze, this is just for
    localStorage -> backend sync and ensuring cross-device consistency.
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Extract analysis details
        property_address = data.get('property_address', 'Property Analysis')
        asking_price = data.get('asking_price', 0)
        full_result = data.get('full_result', {})
        analyzed_at_str = data.get('analyzed_at')
        
        # Parse analyzed_at timestamp
        if analyzed_at_str:
            try:
                from dateutil.parser import parse
                analyzed_at = parse(analyzed_at_str)
            except Exception:
                analyzed_at = datetime.utcnow()
        else:
            analyzed_at = datetime.utcnow()
        
        # Check if property already exists (by address and price)
        existing_property = Property.query.filter_by(
            user_id=current_user.id,
            address=property_address,
            price=asking_price
        ).first()
        
        if existing_property:
            logging.info(f"✅ Analysis already exists for property {existing_property.id}, skipping duplicate save")
            return jsonify({
                'success': True,
                'message': 'Analysis already saved',
                'property_id': existing_property.id
            })
        
        # Create new property record
        property = Property(
            user_id=current_user.id,
            address=property_address,
            price=asking_price,
            status='analyzed',
            analyzed_at=analyzed_at
        )
        db.session.add(property)
        db.session.flush()  # Get property.id
        
        # Create analysis record
        import json
        analysis = Analysis(
            property_id=property.id,
            result_json=json.dumps(full_result),
            created_at=analyzed_at
        )
        db.session.add(analysis)
        db.session.commit()

        # Auto-activate agentic property watch
        try:
            from models import PropertyWatch
            _rj = full_result if isinstance(full_result, dict) else {}
            _rp = _rj.get('research_profile', {}) or {}
            _existing_watch = PropertyWatch.query.filter_by(
                user_id=current_user.id, address=property.address, is_active=True
            ).first()
            if not _existing_watch:
                _watch = PropertyWatch(
                    user_id         = current_user.id,
                    analysis_id     = analysis.id,
                    address         = property.address,
                    asking_price    = property.price,
                    latitude        = _rp.get('latitude'),
                    longitude       = _rp.get('longitude'),
                    avm_at_analysis = _rp.get('avm_price') or _rp.get('rentcast_avm'),
                    expires_at      = datetime.utcnow() + timedelta(days=45),
                )
                db.session.add(_watch)
                db.session.commit()
                logging.info(f"🔭 Auto-watch created for {property.address}")
        except Exception as _we:
            logging.warning(f"Auto-watch creation failed (non-fatal): {_we}")

        logging.info(f"✅ Saved analysis from localStorage sync for property {property.id}")
        
        return jsonify({
            'success': True,
            'message': 'Analysis saved successfully',
            'property_id': property.id
        })
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"❌ Error saving user analysis: {e}")
        return jsonify({
            'error': 'Failed to save analysis',
            'message': 'An internal error occurred. Please try again.'
        }), 500


@user_bp.route('/api/user/comparisons', methods=['GET'])
@_login_req_dec
def get_user_comparisons():
    """Get all comparisons for current user"""
    try:
        comparisons = Comparison.query.filter_by(
            user_id=current_user.id
        ).order_by(Comparison.created_at.desc()).limit(50).all()
        
        results = []
        for comp in comparisons:
            results.append({
                'id': comp.id,
                'property1_address': comp.property1_address,
                'property2_address': comp.property2_address,
                'property3_address': comp.property3_address,
                'winner_property': comp.winner_property,
                'status': comp.status,
                'created_at': comp.created_at.isoformat() if comp.created_at else None,
                'completed_at': comp.completed_at.isoformat() if comp.completed_at else None
            })
        
        return jsonify({
            'success': True,
            'comparisons': results
        })
        
    except Exception as e:
        logger.error(f"❌ Get user comparisons error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'An internal error occurred. Please try again.'
        }), 500


# ============================================================================
# PRICING & SUBSCRIPTION
# ============================================================================


@user_bp.route('/api/user/info')
def get_user_info():
    """Get current user information including auth provider"""
    try:
        # Check authentication
        if not current_user.is_authenticated:
            return jsonify({'error': 'Not authenticated', 'redirect': '/login'}), 401
        
        user_data = {
            'email': current_user.email if current_user.email else 'No email',
            'name': current_user.name if current_user.name else None,
            'auth_provider': current_user.auth_provider if current_user.auth_provider else None,
            'tier': current_user.tier if current_user.tier else 'free',
            'created_at': current_user.created_at.isoformat() if current_user.created_at else None
        }
        logging.info(f"✅ User info API called successfully for {current_user.email}")
        return jsonify(user_data)
    except Exception as e:
        logging.error(f"❌ Error in get_user_info: {str(e)}")
        logging.exception(e)
        return jsonify({
            'error': 'An internal error occurred. Please try again.',
            'email': 'Error loading',
            'tier': 'free',
            'auth_provider': None
        }), 500


@user_bp.route('/api/user', methods=['GET'])
@_login_req_dec
def get_user():
    """Get current user info - for settings page"""
    try:
        # Safely get tier limits
        try:
            limits = current_user.get_tier_limits()
        except Exception as e:
            logging.error(f"Error getting tier limits: {e}")
            limits = {}
        
        # Safely get can_analyze
        try:
            can_analyze = current_user.can_analyze_property()
        except Exception as e:
            logging.error(f"Error checking can_analyze: {e}")
            can_analyze = False
        
        return jsonify({
            'id': current_user.id,
            'name': current_user.name or 'User',
            'email': current_user.email or 'No email',
            'tier': current_user.tier or 'free',
            'credits': current_user.analysis_credits or 0,  # Use analysis_credits, not usage_count
            'total_credits_purchased': current_user.total_credits_purchased or 0,
            'limits': limits,
            'can_analyze': can_analyze
        })
    except Exception as e:
        logging.error(f"❌ Error in /api/user endpoint: {e}")
        logging.exception(e)
        return jsonify({
            'error': 'An internal error occurred',
            'id': current_user.id if current_user else None,
            'email': current_user.email if current_user else 'Error',
            'tier': 'free',
            'name': 'User',
            'credits': 0
        }), 500



@user_bp.route('/api/user/preferences', methods=['GET', 'POST'])
@_login_req_dec
def user_preferences():
    """Get or update user buyer preferences"""
    if request.method == 'GET':
        logging.info(f"📖 LOADING PREFERENCES FOR: {current_user.email}")
        logging.info(f"   max_budget: {current_user.max_budget}")
        logging.info(f"   repair_tolerance: {current_user.repair_tolerance}")
        logging.info(f"   biggest_regret: {current_user.biggest_regret}")
        
        return jsonify({
            'max_budget': current_user.max_budget,
            'repair_tolerance': current_user.repair_tolerance,
            'biggest_regret': current_user.biggest_regret
        })
    
    # POST - Update preferences
    data = request.get_json()
    
    logging.info(f"")
    logging.info(f"=" * 80)
    logging.info(f"💾 SAVING PREFERENCES FOR: {current_user.email}")
    logging.info(f"=" * 80)
    logging.info(f"📥 Received data: {data}")
    logging.info(f"")
    logging.info(f"📊 BEFORE UPDATE:")
    logging.info(f"   max_budget: {current_user.max_budget}")
    logging.info(f"   repair_tolerance: {current_user.repair_tolerance}")
    logging.info(f"   biggest_regret: {current_user.biggest_regret}")
    logging.info(f"")
    
    try:
        # Handle max_budget (can be None/empty)
        if 'max_budget' in data:
            old_value = current_user.max_budget
            budget_value = data['max_budget']
            
            # Handle None, empty string, or valid number
            if budget_value is None or budget_value == '' or budget_value == 'None':
                current_user.max_budget = None
                logging.info(f"✏️  Updating max_budget: {old_value} → None (empty)")
            else:
                try:
                    current_user.max_budget = int(float(budget_value))  # float() handles decimals
                    logging.info(f"✏️  Updating max_budget: {old_value} → ${current_user.max_budget:,}")
                except (ValueError, TypeError) as e:
                    logging.error(f"❌ Invalid max_budget value: {budget_value} ({type(budget_value)})")
                    return jsonify({
                        'success': False,
                        'error': f'Invalid budget format: {budget_value}'
                    }), 400
        
        # Handle repair_tolerance (can be None/empty)
        if 'repair_tolerance' in data:
            old_value = current_user.repair_tolerance
            tolerance_value = data['repair_tolerance']
            
            if tolerance_value is None or tolerance_value == '' or tolerance_value == 'None':
                current_user.repair_tolerance = None
                logging.info(f"✏️  Updating repair_tolerance: {old_value} → None (empty)")
            else:
                current_user.repair_tolerance = tolerance_value
                logging.info(f"✏️  Updating repair_tolerance: {old_value} → {current_user.repair_tolerance}")
        
        # Handle biggest_regret (can be None/empty)
        if 'biggest_regret' in data:
            old_value = current_user.biggest_regret
            regret_value = data['biggest_regret']
            
            if regret_value is None or regret_value == '' or regret_value == 'None':
                current_user.biggest_regret = None
                logging.info(f"✏️  Updating biggest_regret: {old_value} → None (empty)")
            else:
                current_user.biggest_regret = regret_value
                logging.info(f"✏️  Updating biggest_regret: {old_value} → {current_user.biggest_regret}")
        
        logging.info(f"")
        logging.info(f"💾 COMMITTING TO DATABASE...")
        
        # Note: onboarding_completed is now only set via /api/user/complete-onboarding
        # Users must complete the dedicated onboarding wizard
        
        db.session.commit()
        
        logging.info(f"✅ DATABASE COMMIT SUCCESSFUL")
        logging.info(f"")
        logging.info(f"📊 AFTER UPDATE (from database):")
        
        # Refresh from database to verify
        db.session.refresh(current_user)
        logging.info(f"   max_budget: {current_user.max_budget}")
        logging.info(f"   repair_tolerance: {current_user.repair_tolerance}")
        logging.info(f"   biggest_regret: {current_user.biggest_regret}")
        logging.info(f"")
        logging.info(f"=" * 80)
        logging.info(f"✅ PREFERENCES SAVED SUCCESSFULLY")
        logging.info(f"=" * 80)
        logging.info(f"")
        
        return jsonify({
            'success': True,
            'message': 'Preferences saved successfully',
            'preferences': {
                'max_budget': current_user.max_budget,
                'repair_tolerance': current_user.repair_tolerance,
                'biggest_regret': current_user.biggest_regret
            }
        })
    
    except Exception as e:
        logging.error(f"")
        logging.error(f"=" * 80)
        logging.error(f"❌ ERROR SAVING PREFERENCES")
        logging.error(f"=" * 80)
        logging.error(f"Error: {str(e)}")
        logging.exception(e)
        logging.error(f"=" * 80)
        logging.error(f"")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': 'Error saving preferences. Please try again.'
        }), 500


@user_bp.route('/api/user/debug-data', methods=['GET'])
@dev_only_gate
@_login_req_dec
def debug_user_data():
    """DEBUG: Show all data for current user"""
    try:
        user_id = current_user.id
        user_email = current_user.email
        
        # Count all data
        properties = Property.query.filter_by(user_id=user_id).all()
        usage_records = UsageRecord.query.filter_by(user_id=user_id).all()
        consent_records = ConsentRecord.query.filter_by(user_id=user_id).all()
        
        # Get detailed property data
        properties_data = []
        for prop in properties:
            documents = Document.query.filter_by(property_id=prop.id).all()
            analyses = Analysis.query.filter_by(property_id=prop.id).all()
            
            properties_data.append({
                'id': prop.id,
                'address': prop.address,
                'created_at': prop.created_at.isoformat() if prop.created_at else None,
                'documents_count': len(documents),
                'analyses_count': len(analyses),
                'documents': [{'id': d.id, 'filename': d.filename} for d in documents],
                'analyses': [{'id': a.id, 'created_at': a.created_at.isoformat() if a.created_at else None} for a in analyses]
            })
        
        return jsonify({
            'user': {
                'id': user_id,
                'email': user_email,
                'name': current_user.name,
                'tier': current_user.tier,
                'auth_provider': current_user.auth_provider,
                'created_at': current_user.created_at.isoformat() if current_user.created_at else None
            },
            'properties': properties_data,
            'usage_records_count': len(usage_records),
            'consent_records_count': len(consent_records),
            'consent_records': [
                {
                    'id': c.id,
                    'consent_type': c.consent_type,
                    'consent_version': c.consent_version,
                    'consented_at': c.consented_at.isoformat() if c.consented_at else None
                } for c in consent_records
            ],
            'summary': {
                'total_properties': len(properties),
                'total_documents': db.session.query(db.func.count(Document.id)).filter(
                    Document.property_id.in_([p.id for p in properties])).scalar() if properties else 0,
                'total_analyses': db.session.query(db.func.count(Analysis.id)).filter(
                    Analysis.property_id.in_([p.id for p in properties])).scalar() if properties else 0,
                'total_usage_records': len(usage_records),
                'total_consent_records': len(consent_records)
            }
        })
        
    except Exception as e:
        logging.error(f"Error getting debug data: {e}")
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@user_bp.route('/api/user/delete', methods=['POST'])
@_login_req_dec
def delete_user_account():
    """Delete user account and all associated data"""
    user_id = current_user.id
    user_email = current_user.email
    
    logging.info(f"🗑️ ACCOUNT DELETION for {user_email} (ID: {user_id})")
    
    try:
        # Count before deletion for the response
        properties_count = Property.query.filter_by(user_id=user_id).count()
        analyses_count = db.session.query(Analysis).join(Property).filter(Property.user_id == user_id).count()
        
        # Delete ALL child tables via raw SQL to avoid FK constraint issues.
        # Order matters: delete deepest children first, then parents, then user.
        child_tables = [
            # Legacy Scout tables (orphaned, kept for migration safety)
            ("scout_matches", "user_id"),
            ("scout_matches", "profile_id", "scout_profiles"),
            ("scout_profiles", "user_id"),
            # Preference learning (v5.62.87+)
            ("listing_preferences", "user_id"),
            # Market intelligence (v5.62.92+)
            ("market_snapshots", "user_id"),
            # Repair cost logs (v5.68.2+) — FK to properties
            ("repair_cost_logs", "property_id", "properties"),
            # Credit transactions (v5.60+)
            ("credit_transactions", "user_id"),
            # Funnel events (v5.64.0+)
            ("gtm_funnel_events", "user_id"),
            # Analysis chain (documents → analyses → properties)
            ("analyses", "property_id", "properties"),
            ("documents", "property_id", "properties"),
            ("properties", "user_id"),
            # User activity
            ("usage_records", "user_id"),
            ("consent_records", "user_id"),
            ("comparisons", "user_id"),
            # Feedback & surveys
            ("pmf_surveys", "user_id"),
            ("exit_surveys", "user_id"),
            ("quick_feedback", "user_id"),
            # Referrals (both directions)
            ("referral_rewards", "user_id"),
            ("referrals", "referrer_id"),
            ("referrals", "referee_id"),
            # Sharing & sessions
            ("share_links", "user_id"),
            ("turk_sessions", "user_id"),
            ("bugs", "user_id"),
            ("email_registry", "user_id"),
            # Magic links (by email, not user_id)
            ("magic_links", "email"),
        ]
        
        total_deleted = 0
        for entry in child_tables:
            table = entry[0]
            col = entry[1]
            
            try:
                if col == 'email':
                    # Email-based delete (magic_links uses email, not user_id)
                    sql = db.text(f"DELETE FROM {table} WHERE {col} = :email")
                    result = db.session.execute(sql, {"email": user_email})
                elif len(entry) == 3:
                    # Join-based delete: e.g. delete from analyses via property_id in properties
                    parent_table = entry[2]
                    sql = db.text(f"DELETE FROM {table} WHERE {col} IN (SELECT id FROM {parent_table} WHERE user_id = :uid)")
                    result = db.session.execute(sql, {"uid": user_id})
                else:
                    sql = db.text(f"DELETE FROM {table} WHERE {col} = :uid")
                    result = db.session.execute(sql, {"uid": user_id})
                
                count = result.rowcount
                if count > 0:
                    logging.info(f"   Deleted {count} rows from {table}")
                    total_deleted += count
                # Commit each table individually so failures don't rollback prior work
                db.session.commit()
            except Exception as table_err:
                # Table might not exist yet (pre-migration) — skip gracefully
                logging.warning(f"   Skipping {table}: {table_err}")
                try:
                    db.session.rollback()
                except Exception:
                    pass
        
        # Now delete the user itself
        db.session.execute(db.text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        db.session.commit()
        
        logging.info(f"✅ Account deleted: {user_email} ({total_deleted} child records removed)")
        
        logout_user()
        
        return jsonify({
            'success': True,
            'message': 'Account and all data deleted successfully',
            'deleted': {
                'properties': properties_count,
                'documents': 0,
                'analyses': analyses_count,
                'usage_records': 0,
                'consent_records': 0,
                'total_records': total_deleted
            }
        })
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"❌ ACCOUNT DELETION FAILED for {user_email}: {e}")
        logging.exception(e)
        return jsonify({
            'success': False,
            'message': 'Error deleting account. Please try again.'
        }), 500


@user_bp.route('/api/user/repair-jobs', methods=['GET'])
@_login_req_dec
def get_user_repair_jobs():
    """Get all contractor lead requests (repair jobs) for the current user."""
    from models import ContractorLead, ContractorLeadClaim, Contractor
    
    leads = ContractorLead.query.filter_by(
        user_id=current_user.id
    ).order_by(ContractorLead.created_at.desc()).all()
    
    result = []
    for lead in leads:
        # Get claims/interested contractors (anonymised until claimed)
        claims = ContractorLeadClaim.query.filter_by(lead_id=lead.id).all()
        contractors_info = []
        for claim in claims:
            c = Contractor.query.get(claim.contractor_id)
            if c:
                contractors_info.append({
                    'name':   c.business_name or c.name or 'Contractor',
                    'phone':  c.phone if claim.status in ('active', 'closed') else None,
                    'email':  c.email if claim.status in ('active', 'closed') else None,
                    'status': claim.status,
                })

        result.append({
            'id':               lead.id,
            'created_at':       lead.created_at.isoformat() if lead.created_at else None,
            'repair_system':    lead.repair_system,
            'trade_needed':     lead.trade_needed,
            'cost_estimate':    lead.cost_estimate,
            'issue_description':lead.issue_description,
            'property_address': lead.property_address,
            'status':           lead.status,
            'claim_count':      lead.claim_count or 0,
            'contact_timing':   lead.contact_timing,
            'sent_to_contractor_at': lead.sent_to_contractor_at.isoformat() if lead.sent_to_contractor_at else None,
            'contacted_at':     lead.contacted_at.isoformat() if lead.contacted_at else None,
            'job_closed_at':    lead.job_closed_at.isoformat() if lead.job_closed_at else None,
            'contractors':      contractors_info,
        })
    
    return jsonify({'jobs': result, 'total': len(result)})

