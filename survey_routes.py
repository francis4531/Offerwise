"""
OfferWise Survey Routes Blueprint
Extracted from app.py v5.74.44 for architecture cleanup.
"""

import logging
from flask import Blueprint, request, jsonify, session
from flask_login import current_user
from models import db, UsageRecord, PMFSurvey, QuickFeedback, ExitSurvey

logger = logging.getLogger(__name__)

surveys_bp = Blueprint('surveys', __name__)

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


def init_surveys_blueprint(app, admin_required_fn, api_admin_required_fn,
                       api_login_required_fn, dev_only_gate_fn, limiter):
    _admin_required_ref[0] = admin_required_fn
    _api_admin_required_ref[0] = api_admin_required_fn
    _api_login_required_ref[0] = api_login_required_fn
    _dev_only_gate_ref[0] = dev_only_gate_fn
    _limiter_ref[0] = limiter
    app.register_blueprint(surveys_bp)
    logger.info("✅ Survey Routes blueprint registered")



@surveys_bp.route('/api/survey/pmf', methods=['POST'])
@_limiter.limit("10 per hour")  # SECURITY: Prevent spam submissions
def submit_pmf_survey():
    """Submit PMF (Sean Ellis) survey response"""
    try:
        data = request.get_json() or {}
        
        # Get user if logged in
        user_id = None
        email = data.get('email')
        analyses_count = 0
        
        if current_user.is_authenticated:
            user_id = current_user.id
            email = current_user.email
            # Count their analyses
            usage = UsageRecord.query.filter_by(user_id=user_id).first()
            if usage:
                analyses_count = usage.properties_analyzed or 0
        
        survey = PMFSurvey(
            user_id=user_id,
            email=email,
            disappointment=data.get('disappointment'),  # 'very', 'somewhat', 'not'
            main_benefit=data.get('main_benefit'),
            improvement=data.get('improvement'),
            use_case=data.get('use_case'),
            would_recommend=data.get('would_recommend'),
            recommend_to=data.get('recommend_to'),
            analyses_at_survey=analyses_count,
            trigger=data.get('trigger', 'manual')
        )
        
        db.session.add(survey)
        db.session.commit()
        
        return jsonify({'success': True, 'id': survey.id})
        
    except Exception as e:
        db.session.rollback()
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@surveys_bp.route('/api/feedback/quick', methods=['POST'])
@_limiter.limit("20 per hour")  # SECURITY: Prevent spam submissions
def submit_quick_feedback():
    """Submit lightweight in-app feedback from the feedback tab"""
    try:
        data = request.get_json() or {}
        
        reaction = data.get('reaction', '')
        message = (data.get('message') or '')[:2000]  # Cap at 2000 chars
        page = (data.get('page') or '')[:100]
        
        if reaction not in ('love', 'like', 'meh', 'dislike'):
            return jsonify({'error': 'Invalid reaction'}), 400
        
        user_id = None
        email = None
        if current_user.is_authenticated:
            user_id = current_user.id
            email = current_user.email
        
        feedback = QuickFeedback(
            user_id=user_id,
            email=email,
            reaction=reaction,
            message=message,
            page=page
        )
        
        db.session.add(feedback)
        db.session.commit()
        
        return jsonify({'success': True, 'id': feedback.id})
        
    except Exception as e:
        db.session.rollback()
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@surveys_bp.route('/api/survey/exit', methods=['POST'])
@_limiter.limit("10 per hour")  # SECURITY: Prevent spam submissions
def submit_exit_survey():
    """Submit exit survey for users who don't complete"""
    try:
        data = request.get_json() or {}
        
        user_id = None
        if current_user.is_authenticated:
            user_id = current_user.id
        
        survey = ExitSurvey(
            user_id=user_id,
            session_id=data.get('session_id') or session.get('session_id'),
            exit_reason=data.get('exit_reason'),
            exit_reason_other=data.get('exit_reason_other'),
            exit_page=data.get('exit_page'),
            would_return=data.get('would_return'),
            what_would_help=data.get('what_would_help')
        )
        
        db.session.add(survey)
        db.session.commit()
        
        return jsonify({'success': True, 'id': survey.id})
        
    except Exception as e:
        db.session.rollback()
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@surveys_bp.route('/api/survey/stats')
@_api_admin_required
def get_survey_stats():
    """Get survey and feedback statistics for admin dashboard"""
    # Admin check handled by @api_admin_required decorator
    
    try:
        # PMF Survey stats
        pmf_total = PMFSurvey.query.count()
        pmf_very = PMFSurvey.query.filter_by(disappointment='very').count()
        pmf_somewhat = PMFSurvey.query.filter_by(disappointment='somewhat').count()
        pmf_not = PMFSurvey.query.filter_by(disappointment='not').count()
        
        pmf_score = (pmf_very / pmf_total * 100) if pmf_total > 0 else 0
        
        # Exit Survey stats
        exit_total = ExitSurvey.query.count()
        exit_reasons = db.session.query(
            ExitSurvey.exit_reason,
            db.func.count(ExitSurvey.id)
        ).group_by(ExitSurvey.exit_reason).all()
        
        exit_reason_counts = {reason: count for reason, count in exit_reasons if reason}
        
        # Quick Feedback stats
        qf_total = QuickFeedback.query.count()
        qf_reactions = db.session.query(
            QuickFeedback.reaction,
            db.func.count(QuickFeedback.id)
        ).group_by(QuickFeedback.reaction).all()
        qf_reaction_counts = {reaction: count for reaction, count in qf_reactions if reaction}
        
        # Recent entries from all sources
        recent_pmf = PMFSurvey.query.order_by(PMFSurvey.created_at.desc()).limit(20).all()
        recent_exit = ExitSurvey.query.order_by(ExitSurvey.created_at.desc()).limit(20).all()
        recent_quick = QuickFeedback.query.order_by(QuickFeedback.created_at.desc()).limit(20).all()
        
        return jsonify({
            'pmf': {
                'total': pmf_total,
                'very_disappointed': pmf_very,
                'somewhat_disappointed': pmf_somewhat,
                'not_disappointed': pmf_not,
                'score': round(pmf_score, 1),
                'threshold': 40,  # PMF threshold
                'has_pmf': pmf_score >= 40
            },
            'exit': {
                'total': exit_total,
                'reasons': exit_reason_counts
            },
            'quick_feedback': {
                'total': qf_total,
                'reactions': qf_reaction_counts
            },
            'recent_pmf': [s.to_dict() for s in recent_pmf],
            'recent_exit': [s.to_dict() for s in recent_exit],
            'recent_quick': [f.to_dict() for f in recent_quick]
        })
        
    except Exception:
        return jsonify({
            'error': 'An internal error occurred. Please try again.',
            'trace': 'See server logs',
            'pmf': {'total': 0, 'score': 0},
            'exit': {'total': 0, 'reasons': {}},
            'recent_pmf': [],
            'recent_exit': []
        })
