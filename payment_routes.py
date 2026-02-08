"""
OfferWise Payment Routes - Credit Management
Handles credit deduction and purchase history.
Core Stripe payment routes (checkout, webhook) are in app.py.
"""

import os
import logging
from flask import Blueprint, request, jsonify
from datetime import datetime
from models import db, User, CreditTransaction
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

# Create Blueprint
payment_bp = Blueprint('payment', __name__)

# Pricing configuration (shared with frontend)
PRICING = {
    'single': {
        'name': 'Single Analysis',
        'price': 15,
        'credits': 1,
        'description': '1 complete property analysis'
    },
    'bundle_5': {
        'name': '5-Analysis Bundle',
        'price': 50,
        'credits': 5,
        'description': '5 property analyses - Save 33%'
    },
    'bundle_10': {
        'name': '10-Analysis Bundle',
        'price': 75,
        'credits': 10,
        'description': '10 property analyses - Save 50%'
    }
}


@payment_bp.route('/api/user-credits', methods=['GET'])
@login_required
def get_user_credits():
    """Get current user's credit balance"""
    return jsonify({
        'credits': current_user.analysis_credits or 0,
        'total_purchased': getattr(current_user, 'total_credits_purchased', 0) or 0,
        'analyses_done': getattr(current_user, 'analyses_completed', 0) or 0
    })


@payment_bp.route('/api/deduct-credit', methods=['POST'])
@login_required
def deduct_credit():
    """Deduct one credit from user (called after successful analysis)"""
    try:
        # ATOMIC: SQL-level WHERE guard prevents race conditions and negative credits
        # Use column objects via getattr to reference the correct model columns
        credit_col = getattr(User, 'analysis_credits')
        completed_col = getattr(User, 'analyses_completed', None)

        update_vals = {credit_col: credit_col - 1}
        if completed_col is not None:
            update_vals[completed_col] = completed_col + 1

        rows_updated = User.query.filter(
            User.id == current_user.id,
            credit_col >= 1
        ).update(update_vals)

        if rows_updated == 0:
            return jsonify({'error': 'Insufficient credits'}), 402

        # Log usage transaction
        try:
            usage = CreditTransaction(
                user_id=current_user.id,
                credits=-1,
                amount=0,
                plan_id='usage',
                status='completed',
                completed_at=datetime.utcnow()
            )
            db.session.add(usage)
        except Exception as tx_err:
            logger.warning(f"Could not log credit transaction: {tx_err}")

        db.session.commit()
        db.session.refresh(current_user)

        return jsonify({
            'success': True,
            'remaining_credits': current_user.analysis_credits
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Credit deduction error: {e}")
        return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@payment_bp.route('/api/purchase-history', methods=['GET'])
@login_required
def purchase_history():
    """Get user's purchase history"""
    try:
        transactions = CreditTransaction.query.filter_by(
            user_id=current_user.id,
            status='completed'
        ).filter(
            CreditTransaction.credits > 0
        ).order_by(
            CreditTransaction.created_at.desc()
        ).all()

        return jsonify([{
            'id': t.id,
            'plan': PRICING.get(t.plan_id, {}).get('name', t.plan_id),
            'credits': t.credits,
            'amount': t.amount,
            'date': t.completed_at.isoformat() if t.completed_at else t.created_at.isoformat()
        } for t in transactions])
    except Exception as e:
        logger.error(f"Purchase history error: {e}")
        return jsonify([])
