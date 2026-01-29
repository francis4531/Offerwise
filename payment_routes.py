"""
OfferWise Payment Routes - Stripe Integration
Handles checkout, payment processing, and credit management
"""

import os
import stripe
from flask import Blueprint, request, jsonify, session, redirect, url_for
from datetime import datetime
from database import db, User, CreditTransaction
from flask_login import login_required, current_user

# Create Blueprint
payment_bp = Blueprint('payment', __name__)

# Initialize Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY')

# Pricing configuration
PRICING = {
    'single': {
        'name': 'Single Analysis',
        'price': 15,  # $15
        'credits': 1,
        'description': '1 complete property analysis'
    },
    'bundle_5': {
        'name': '5-Analysis Bundle',
        'price': 50,  # $50 ($10 each)
        'credits': 5,
        'description': '5 property analyses - Save 33%'
    },
    'bundle_10': {
        'name': '10-Analysis Bundle',
        'price': 75,  # $75 ($7.50 each)
        'credits': 10,
        'description': '10 property analyses - Save 50%'
    }
}


@payment_bp.route('/api/stripe-config', methods=['GET'])
def get_stripe_config():
    """Return Stripe publishable key"""
    return jsonify({
        'publishableKey': STRIPE_PUBLISHABLE_KEY
    })


@payment_bp.route('/create-payment-intent', methods=['POST'])
@login_required
def create_payment_intent():
    """Create a Stripe Payment Intent"""
    try:
        data = request.get_json()
        plan_id = data.get('plan')
        email = data.get('email')
        
        # Validate plan
        if plan_id not in PRICING:
            return jsonify({'error': 'Invalid plan'}), 400
        
        plan = PRICING[plan_id]
        amount = plan['price'] * 100  # Convert to cents
        
        # Create Payment Intent
        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency='usd',
            metadata={
                'user_id': current_user.id,
                'user_email': email,
                'plan_id': plan_id,
                'credits': plan['credits']
            },
            description=f"OfferWise - {plan['name']}",
            receipt_email=email
        )
        
        # Store pending transaction
        transaction = CreditTransaction(
            user_id=current_user.id,
            payment_intent_id=intent.id,
            plan_id=plan_id,
            amount=plan['price'],
            credits=plan['credits'],
            status='pending'
        )
        db.session.add(transaction)
        db.session.commit()
        
        return jsonify({
            'clientSecret': intent.client_secret,
            'sessionId': transaction.id
        })
        
    except Exception as e:
        print(f"Payment intent error: {e}")
        return jsonify({'error': str(e)}), 500


@payment_bp.route('/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhooks for payment confirmations"""
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400
    
    # Handle successful payment
    if event['type'] == 'payment_intent.succeeded':
        payment_intent = event['data']['object']
        
        # Get transaction
        transaction = CreditTransaction.query.filter_by(
            payment_intent_id=payment_intent['id']
        ).first()
        
        if transaction and transaction.status == 'pending':
            # Update transaction
            transaction.status = 'completed'
            transaction.completed_at = datetime.utcnow()
            
            # Add credits to user
            user = User.query.get(transaction.user_id)
            if user:
                user.credits = (user.credits or 0) + transaction.credits
                user.total_credits_purchased = (user.total_credits_purchased or 0) + transaction.credits
            
            db.session.commit()
            
            print(f"Payment successful! Added {transaction.credits} credits to user {user.email}")
    
    # Handle failed payment
    elif event['type'] == 'payment_intent.payment_failed':
        payment_intent = event['data']['object']
        
        transaction = CreditTransaction.query.filter_by(
            payment_intent_id=payment_intent['id']
        ).first()
        
        if transaction:
            transaction.status = 'failed'
            transaction.failure_reason = payment_intent.get('last_payment_error', {}).get('message', 'Unknown error')
            db.session.commit()
    
    return jsonify({'status': 'success'})


@payment_bp.route('/verify-payment/<session_id>', methods=['GET'])
@login_required
def verify_payment(session_id):
    """Verify payment completion and return status"""
    try:
        transaction = CreditTransaction.query.get(session_id)
        
        if not transaction:
            return jsonify({'error': 'Transaction not found'}), 404
        
        if transaction.user_id != current_user.id:
            return jsonify({'error': 'Unauthorized'}), 403
        
        return jsonify({
            'status': transaction.status,
            'credits': transaction.credits,
            'amount': transaction.amount,
            'plan_id': transaction.plan_id
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@payment_bp.route('/api/user-credits', methods=['GET'])
@login_required
def get_user_credits():
    """Get current user's credit balance"""
    return jsonify({
        'credits': current_user.credits or 0,
        'total_purchased': current_user.total_credits_purchased or 0,
        'analyses_completed': current_user.analyses_completed or 0
    })


@payment_bp.route('/api/deduct-credit', methods=['POST'])
@login_required
def deduct_credit():
    """Deduct one credit from user (called after successful analysis)"""
    try:
        if not current_user.credits or current_user.credits < 1:
            return jsonify({'error': 'Insufficient credits'}), 402  # 402 Payment Required
        
        # Deduct credit
        current_user.credits -= 1
        current_user.analyses_completed = (current_user.analyses_completed or 0) + 1
        
        # Log usage
        usage = CreditTransaction(
            user_id=current_user.id,
            credits=-1,  # Negative for usage
            amount=0,
            plan_id='usage',
            status='completed'
        )
        db.session.add(usage)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'remaining_credits': current_user.credits
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@payment_bp.route('/api/purchase-history', methods=['GET'])
@login_required
def purchase_history():
    """Get user's purchase history"""
    transactions = CreditTransaction.query.filter_by(
        user_id=current_user.id,
        status='completed'
    ).filter(
        CreditTransaction.credits > 0  # Only purchases, not usage
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
