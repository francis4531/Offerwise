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
    'inspector_free': {
        'name': 'Inspector Free',
        'price': 0,
        'monthly_quota': 5,
        'features': [
            '5 buyer analyses per month',
            'Branded "Powered by OfferWise" report',
            'Shareable buyer link',
            'PDF attachment',
            'Inspector dashboard',
        ]
    },
    'inspector_pro': {
        'name': 'Inspector Pro',
        'price': 49,
        'stripe_price_id': os.environ.get('STRIPE_INSPECTOR_PRO_PRICE_ID', ''),
        'monthly_quota': -1,  # unlimited
        # v5.88.95: features list trimmed to what actually differs from
        # the Free plan. White-label branding, buyer conversion tracking,
        # priority support, and CSV export were listed as Pro features
        # but none of them are gated to Pro in code — Free inspectors
        # get the same branded reports and the same v5.88.91 referral
        # attribution view. Volume is the only real difference right
        # now. When Pro-exclusive features are built, add them here.
        'features': [
            'Unlimited buyer analyses (Free is 5/month)',
            'All Free-tier features included',
        ]
    },
    # ── Contractor subscription tiers ────────────────────────────────────────
    # No referral fees. No liability. Flat subscription for lead access.
    # Disclaimer: OfferWise makes no guarantee of lead quality or job outcomes.
    #
    # v5.88.94: limits aligned with contractor_routes.py monthly_limits dict
    # (the source of enforcement). Previously declared 5/unlimited but
    # the route enforced 10/50 — contractors paying $99/mo Pro were told
    # "unlimited" but capped at 50. Marketing features that don't exist
    # in code (Priority matching, Verified badge, Featured placement,
    # Dedicated account manager) were removed; only listing real features.
    # When those features are built, they can be added back here.
    'contractor_starter': {
        'name': 'Contractor Starter',
        'price': 49,
        'stripe_price_id': os.environ.get('STRIPE_CONTRACTOR_STARTER_PRICE_ID', ''),
        'monthly_lead_limit': 10,
        'zip_limit': 3,
        'features': [
            'Up to 10 lead claims per month',
            'Listed in up to 3 ZIP codes',
            '24-hour exclusive window on each lead',
            'Instant email notification on new matches',
            'No referral fees — ever',
        ]
    },
    'contractor_pro': {
        'name': 'Contractor Pro',
        'price': 99,
        'stripe_price_id': os.environ.get('STRIPE_CONTRACTOR_PRO_PRICE_ID', ''),
        'monthly_lead_limit': 50,
        'zip_limit': 10,
        'features': [
            'Up to 50 lead claims per month',
            'Listed in up to 10 ZIP codes',
            '48-hour exclusive window on each lead',
            'Instant email notification on new matches',
            'No referral fees — ever',
        ]
    },
    'contractor_enterprise': {
        'name': 'Contractor Enterprise',
        'price': 199,
        'stripe_price_id': os.environ.get('STRIPE_CONTRACTOR_ENTERPRISE_PRICE_ID', ''),
        'monthly_lead_limit': -1,  # unlimited
        'zip_limit': -1,  # statewide
        'features': [
            'Unlimited lead claims',
            'All ZIP codes in your license state',
            '72-hour exclusive window on each lead',
            'Instant email notification on new matches',
            'No referral fees — ever',
        ]
    },
    # ── Buyer subscription tiers ─────────────────────────────────────────────
    # Replaces one-time credit bundles. Credits still exist as reward currency
    # (referrals, promotions) but buyers access analyses via monthly subscription.
    # v5.88.95: Buyer tier features rewritten. Previously listed
    # tier-exclusive features (AI Negotiation Coach for Pro+, Bulk
    # upload / API access / Investor-grade reports / Dedicated support
    # for Unlimited). None of these are actually tier-gated in code —
    # every buyer including free has access to the same product
    # features. Three of them (Bulk upload, user-facing API access,
    # Investor-grade reports) don't exist in the codebase at all.
    # The only real differentiator across buyer tiers is the monthly
    # analysis quota. Feature list is now identical per tier so the
    # buyer can pick on volume alone — which is the honest pitch.
    'free': {
        'name': 'Free',
        'price': 0,
        'analyses_per_month': 1,
        'stripe_price_id': '',
        'features': [
            '1 property analysis',
            'Full OfferScore™ Report',
            'Property Risk DNA™',
            'Seller Transparency Report™',
            'AI Negotiation Coach',
            'Property comparisons',
            'Repair addendum drafting',
        ]
    },
    'buyer_starter': {
        'name': 'Starter',
        'price': 9,
        'analyses_per_month': 10,
        'stripe_price_id': os.environ.get('STRIPE_BUYER_STARTER_PRICE_ID', ''),
        'features': [
            '10 analyses per month',
            'Full OfferScore™ Report',
            'Property Risk DNA™',
            'Seller Transparency Report™',
            'AI Negotiation Coach',
            'Property comparisons',
            'Repair addendum drafting',
        ]
    },
    'buyer_pro': {
        'name': 'Pro',
        'price': 19,
        'analyses_per_month': 30,
        'stripe_price_id': os.environ.get('STRIPE_BUYER_PRO_PRICE_ID', ''),
        'features': [
            '30 analyses per month',
            'Full OfferScore™ Report',
            'Property Risk DNA™',
            'Seller Transparency Report™',
            'AI Negotiation Coach',
            'Property comparisons',
            'Repair addendum drafting',
        ]
    },
    'buyer_unlimited': {
        'name': 'Unlimited',
        'price': 49,
        'analyses_per_month': -1,
        'stripe_price_id': os.environ.get('STRIPE_BUYER_UNLIMITED_PRICE_ID', ''),
        'features': [
            'Unlimited analyses',
            'Full OfferScore™ Report',
            'Property Risk DNA™',
            'Seller Transparency Report™',
            'AI Negotiation Coach',
            'Property comparisons',
            'Repair addendum drafting',
        ]
    },
    # ── API Tiers (B2B developer access) ──────────────────────────────
    'api_starter': {
        'name': 'API Starter',
        'price': 99,
        'calls_per_month': 500,
        'stripe_price_id': os.environ.get('STRIPE_API_STARTER_PRICE_ID', ''),
        'monthly_limit': 500,
        'features': [
            '500 analyses/month',
            'Full PropertyAnalysis JSON',
            'Risk score + deal breakers',
            'Repair cost breakdown',
            'Offer price recommendation',
            'Standard support (48h)',
        ]
    },
    'api_growth': {
        'name': 'API Growth',
        'price': 299,
        'calls_per_month': 2000,
        'stripe_price_id': os.environ.get('STRIPE_API_GROWTH_PRICE_ID', ''),
        'monthly_limit': 2000,
        'features': [
            '2,000 analyses/month',
            'Everything in Starter',
            'Webhook delivery',
            'Priority processing',
            'Dedicated Slack channel',
            'SLA 99.5% uptime',
        ]
    },
    'api_enterprise': {
        'name': 'API Enterprise',
        'price': 0,  # custom
        'calls_per_month': -1,
        'stripe_price_id': '',
        'monthly_limit': -1,
        'features': [
            'Unlimited analyses',
            'Everything in Growth',
            'Custom contract + SLA',
            'White-label option',
            'On-call support',
            'Volume discounts',
        ]
    },
    # Legacy tiers — kept for backward compat, not shown in UI
    'starter': {'name': 'Starter (legacy)', 'price': 29, 'analyses_per_month': 10,
                'stripe_price_id': os.environ.get('STRIPE_STARTER_PRICE_ID', '')},
    'professional': {'name': 'Professional (legacy)', 'price': 99, 'analyses_per_month': 50,
                     'stripe_price_id': os.environ.get('STRIPE_PRO_PRICE_ID', '')},
    'enterprise': {'name': 'Enterprise (legacy)', 'price': 299, 'analyses_per_month': -1,
                   'stripe_price_id': os.environ.get('STRIPE_ENTERPRISE_PRICE_ID', '')},
}

# Decorators
def login_required(f):
    """Require user to be logged in"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('auth.login_page'))
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
                return redirect(url_for('auth.login_page'))
            
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
            return redirect(url_for('auth.login_page'))
        
        if not current_user.can_analyze_property():
            limits = current_user.get_tier_limits()
            flash(f'You have reached your monthly limit of {limits["properties_per_month"]} properties. Please upgrade your plan.', 'warning')
            return redirect(url_for('pricing'))
        
        return f(*args, **kwargs)
    return decorated_function
