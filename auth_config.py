"""
Authentication Configuration
OAuth providers, email settings, and auth helpers
"""

import os
from functools import wraps
from flask import redirect, url_for, flash
from flask_login import current_user

# OAuth Configuration
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

GITHUB_CLIENT_ID = os.environ.get('GITHUB_CLIENT_ID', '')
GITHUB_CLIENT_SECRET = os.environ.get('GITHUB_CLIENT_SECRET', '')

# Email Configuration (for magic links)
MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@offerwise.com')

# Stripe Configuration (for payments)
STRIPE_PUBLIC_KEY = os.environ.get('STRIPE_PUBLIC_KEY', '')
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

# Pricing Tiers
PRICING_TIERS = {
    'free': {
        'name': 'Free',
        'price': 0,
        'properties_per_month': 3,
        'storage_mb': 50,
        'features': [
            '3 property analyses per month',
            'Basic risk assessment',
            '50MB file storage',
            'Email support'
        ]
    },
    'starter': {
        'name': 'Starter',
        'price': 29,
        'stripe_price_id': os.environ.get('STRIPE_STARTER_PRICE_ID', ''),
        'properties_per_month': 10,
        'storage_mb': 200,
        'features': [
            '10 property analyses per month',
            'Advanced risk assessment',
            '200MB file storage',
            'Priority email support',
            'Export to PDF'
        ]
    },
    'professional': {
        'name': 'Professional',
        'price': 99,
        'stripe_price_id': os.environ.get('STRIPE_PRO_PRICE_ID', ''),
        'properties_per_month': 50,
        'storage_mb': 1000,
        'features': [
            '50 property analyses per month',
            'Premium risk assessment',
            '1GB file storage',
            'Priority support',
            'API access',
            'Team collaboration',
            'Custom reports'
        ]
    },
    'enterprise': {
        'name': 'Enterprise',
        'price': 299,
        'stripe_price_id': os.environ.get('STRIPE_ENTERPRISE_PRICE_ID', ''),
        'properties_per_month': -1,  # Unlimited
        'storage_mb': -1,  # Unlimited
        'features': [
            'Unlimited property analyses',
            'Premium risk assessment',
            'Unlimited file storage',
            '24/7 phone support',
            'API access',
            'White-label options',
            'Custom integrations',
            'Dedicated account manager'
        ]
    }
}

# Decorators
def login_required(f):
    """Require user to be logged in"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def tier_required(min_tier):
    """Require minimum subscription tier"""
    tier_hierarchy = ['free', 'starter', 'professional', 'enterprise']
    
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('Please log in to access this feature.', 'warning')
                return redirect(url_for('login'))
            
            user_tier_index = tier_hierarchy.index(current_user.tier)
            required_tier_index = tier_hierarchy.index(min_tier)
            
            if user_tier_index < required_tier_index:
                flash(f'This feature requires {PRICING_TIERS[min_tier]["name"]} plan or higher.', 'warning')
                return redirect(url_for('pricing'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def check_usage_limit(f):
    """Check if user has exceeded usage limit"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in to continue.', 'warning')
            return redirect(url_for('login'))
        
        if not current_user.can_analyze_property():
            limits = current_user.get_tier_limits()
            flash(f'You have reached your monthly limit of {limits["properties_per_month"]} properties. Please upgrade your plan.', 'warning')
            return redirect(url_for('pricing'))
        
        return f(*args, **kwargs)
    return decorated_function
