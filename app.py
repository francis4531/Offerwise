"""
OfferWise - Complete Application with Authentication and Storage
"""

from flask import Flask, request, jsonify, send_from_directory, render_template, render_template_string, redirect, url_for, flash, session, make_response
from flask_cors import CORS
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from authlib.integrations.flask_client import OAuth
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import base64
import os
import json
import secrets
import logging
import gc  # For memory management
import time
import random
from datetime import datetime, timedelta

# Import intelligence modules
from document_parser import DocumentParser
from risk_scoring_model import BuyerProfile
from offerwise_intelligence import OfferWiseIntelligence
from pdf_handler import PDFHandler
from validation import validate_analysis_output, ValidationError
from analysis_cache import AnalysisCache
from confidence_scorer import ConfidenceScorer
from database_health import DatabaseHealth
from job_manager import job_manager
from pdf_worker import initialize_worker
from negotiation_toolkit import NegotiationToolkit  # 🎯 NEW: Negotiation features
from property_research_agent import PropertyResearchAgent  # 🤖 Property Research Agent
from risk_check_engine import run_risk_check  # 🔍 Risk Check (viral tool)
from security import validate_origin, secure_endpoint, sanitize_input, log_security_event, ALLOWED_ORIGINS
from email_service import (
    send_welcome_email, 
    send_purchase_receipt, 
    send_analysis_complete,
    send_credits_reminder,
    send_negotiation_guide,
    send_email,
    EMAIL_ENABLED
)  # 📧 Transactional emails

# Import database models
from models import db, User, Property, Document, Analysis, UsageRecord, MagicLink, ConsentRecord, EmailRegistry, Referral, ReferralReward, Comparison, TurkSession, Bug, PMFSurvey, ExitSurvey, QuickFeedback, Subscriber, ShareLink, Waitlist, ListingPreference, MarketSnapshot, ContractorLead, Inspector, InspectorReport, Contractor, REFERRAL_TIERS
from auth_config import PRICING_TIERS
from legal_disclaimers import (
    get_disclaimer_text, 
    get_disclaimer_version, 
    get_all_disclaimers,
    ANALYSIS_DISCLAIMER_VERSION
)

# Stripe for payment processing
import stripe

# Stripe configuration - ALL keys must come from environment variables
# NEVER hardcode API keys in source code

# Live Stripe keys (for production)
stripe_secret = os.environ.get('STRIPE_SECRET_KEY', '')
stripe_publishable = os.environ.get('STRIPE_PUBLISHABLE_KEY', '')

# Test Stripe keys (for automated testing) - v5.54.55
stripe_test_secret = os.environ.get('STRIPE_TEST_SECRET_KEY', '')
stripe_test_publishable = os.environ.get('STRIPE_TEST_PUBLISHABLE_KEY', '')

# Validate Stripe configuration
if not stripe_secret:
    if os.environ.get('FLASK_ENV') == 'development':
        logging.warning("⚠️  STRIPE_SECRET_KEY not set. Payment features disabled in development.")
    else:
        logging.error("❌ CRITICAL: STRIPE_SECRET_KEY not set! Payments will not work in production.")

stripe.api_key = stripe_secret

# Log test key availability
if stripe_test_secret:
    logging.info("✅ Stripe TEST keys available for automated testing")
else:
    logging.warning("⚠️ STRIPE_TEST_SECRET_KEY not set - Stripe integration tests will be limited")

# Progress tracking for OCR processing
# Format: {session_id: {'current': 5, 'total': 44, 'status': 'processing'}}
ocr_progress = {}

# ============================================================================
# API-Friendly Authentication Decorator
# ============================================================================

from functools import wraps

def api_login_required(f):
    """
    Login required decorator that returns JSON instead of redirecting to HTML login page.
    
    CRITICAL: Use this for API endpoints (/api/*) instead of @login_required
    Use regular @login_required for HTML routes (dashboard, settings, etc.)
    
    Why: When @login_required fails on API endpoint, Flask redirects to login page (HTML),
    but frontend expects JSON. This causes "Unexpected token '<'" errors when parsing response.
    
    This decorator returns proper JSON 401 response that frontend can handle.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            logging.warning(f"⚠️ Unauthenticated API request to {request.path} from {request.remote_addr}")
            return jsonify({
                'error': 'Authentication required',
                'authenticated': False,
                'message': 'Please log in to continue',
                'redirect': '/login'
            }), 401
        return f(*args, **kwargs)
    return decorated_function


# ============================================================================
# Admin-Only Access (v5.54.53)
# ============================================================================

ADMIN_EMAIL = 'francis@piotnetworks.com'
ADMIN_EMAILS = ['francis@piotnetworks.com', 'francis@getofferwise.ai']

def is_admin():
    """Check if current user is admin. Only authenticated users with admin emails qualify."""
    # Primary check: authenticated user with admin email
    if current_user.is_authenticated and current_user.email in ADMIN_EMAILS:
        return True
    
    # API/page fallback: X-Admin-Key HEADER (preferred, secure)
    admin_key = request.headers.get('X-Admin-Key', '')
    
    # For HTML page access only (not /api/ routes): also accept query param
    # This is needed for initial page load (browser can't set headers on navigation)
    if not admin_key:
        admin_key = request.args.get('admin_key', '')
    
    expected_key = os.environ.get('TURK_ADMIN_KEY')
    if admin_key and expected_key and admin_key == expected_key:
        return True
    
    return False

def admin_required(f):
    """Decorator to restrict access to admin only"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin():
            logging.warning(f"🚫 Unauthorized admin access attempt to {request.path} from {request.remote_addr}")
            return "Unauthorized. Admin access only.", 403
        return f(*args, **kwargs)
    return decorated_function

def api_admin_required(f):
    """API version - returns JSON instead of HTML"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin():
            logging.warning(f"🚫 Unauthorized admin API access attempt to {request.path} from {request.remote_addr}")
            return jsonify({'error': 'Unauthorized. Admin access only.'}), 403
        return f(*args, **kwargs)
    return decorated_function

# ============================================================================
# Onboarding Check Helper
# ============================================================================

def check_user_needs_onboarding(user):
    """
    Check if user needs to complete onboarding.
    
    Returns:
        tuple: (needs_onboarding: bool, redirect_url: str or None)
        
    NEW FLOW (v4.92 - Dedicated Onboarding Wizard):
    - If onboarding not complete → /onboarding (dedicated wizard)
    - Wizard handles: Preferences → Legal → Ready screen
    - After completion, user can access all pages normally
    """
    logging.info("")
    logging.info("🔍" * 50)
    logging.info("🔍 CHECKING IF USER NEEDS ONBOARDING")
    logging.info("🔍" * 50)
    logging.info(f"📧 User Email: {user.email}")
    logging.info(f"🆔 User ID: {user.id}")
    logging.info(f"")
    
    # Check if user has the attribute
    has_attribute = hasattr(user, 'onboarding_completed')
    logging.info(f"📋 Has 'onboarding_completed' attribute? {has_attribute}")
    
    if has_attribute:
        flag_value = user.onboarding_completed
        logging.info(f"📊 onboarding_completed value: {flag_value}")
        logging.info(f"📊 onboarding_completed type: {type(flag_value)}")
        
        if user.onboarding_completed:
            logging.info(f"")
            logging.info(f"✅✅✅ ONBOARDING ALREADY COMPLETED ✅✅✅")
            logging.info(f"🎉 User should go directly to app (no onboarding needed)")
            logging.info(f"🔍" * 50)
            logging.info("")
            return (False, None)
        else:
            logging.info(f"")
            logging.info(f"❌ ONBOARDING NOT COMPLETED")
            logging.info(f"📋 User {user.id} needs onboarding - redirecting to /onboarding")
            logging.info(f"🔍" * 50)
            logging.info("")
            return (True, '/onboarding')
    else:
        logging.error(f"")
        logging.error(f"🚨🚨🚨 CRITICAL: User has no 'onboarding_completed' attribute! 🚨🚨🚨")
        logging.error(f"This should never happen - check User model")
        logging.error(f"🔍" * 50)
        logging.error("")
        return (True, '/onboarding')

# Initialize Sentry error monitoring (free tier: 5K events/month)
# Set SENTRY_DSN in Render environment to enable.
# Get your DSN from: https://sentry.io → Create Project → Flask
_sentry_dsn = os.environ.get('SENTRY_DSN', '')
if _sentry_dsn:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=_sentry_dsn,
            traces_sample_rate=0.1,  # 10% of requests for performance monitoring
            environment=os.environ.get('FLASK_ENV', 'production'),
        )
        logging.info("✅ Sentry error monitoring enabled")
    except Exception as e:
        logging.warning(f"⚠️ Sentry init failed: {e}")

# Initialize Flask app
app = Flask(__name__, static_folder='static')

# GZIP compression — reduces 500KB HTML to ~80KB over the wire
from flask_compress import Compress
app.config['COMPRESS_MIMETYPES'] = [
    'text/html', 'text/css', 'text/xml', 'text/javascript',
    'application/json', 'application/javascript', 'application/xml',
    'image/svg+xml'
]
app.config['COMPRESS_MIN_SIZE'] = 500  # Compress anything > 500 bytes
Compress(app)

# Apply ProxyFix for proper HTTPS detection behind reverse proxy (Render)
# This allows Flask to properly detect HTTPS from X-Forwarded-Proto header
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

CORS(app, supports_credentials=True, origins=[
    'https://www.getofferwise.ai',
    'https://getofferwise.ai',
    'https://offerwise.onrender.com',
    'http://localhost:5000',
    'http://127.0.0.1:5000',
])

# PRODUCTION MODE: Controls verbosity and debug features
PRODUCTION_MODE = os.environ.get('FLASK_ENV') != 'development'

# Dev-only gate: returns 404 in production for debug/test endpoints (admin bypasses)
def dev_only_gate(f):
    """Gate debug/test endpoints in production. Allows access if:
    1. Not production (dev/staging) — always allowed
    2. User is authenticated admin (by email)
    3. Valid admin_key in query params
    Returns 404 in production for everyone else (invisible to attackers).
    """
    import functools
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if PRODUCTION_MODE:
            # Let admins through (handles both login and admin_key)
            if not is_admin():
                return jsonify({'error': 'Not found'}), 404
        return f(*args, **kwargs)
    return decorated

# SECURITY: Block debug/test endpoints in production (v5.62.5)
# Only truly dev-only endpoints go here. Admin endpoints (analytics, bugs,
# turk, surveys, admin pages) are protected by @admin_required decorators
# and must NOT be blocked by this middleware.
_DEV_ONLY_PREFIXES = (
    # Test endpoints
    '/api/test/', '/api/auto-test/',
    # System introspection
    '/api/system-info', '/api/worker/stats',
    # Debug endpoints (note: /api/debug/delete-all-my-data and /api/debug/my-data are
    # user-facing GDPR features and intentionally NOT blocked)
    '/api/debug/ai-status', '/api/debug/memory', '/api/user/debug-data',
)

@app.before_request
def block_dev_endpoints_in_production():
    """Return 404 for debug/test endpoints in production unless admin."""
    if PRODUCTION_MODE and request.path.startswith(_DEV_ONLY_PREFIXES):
        if not is_admin():
            return '', 404

# Configure structured logging (v5.61.0) — JSON in production, human-readable in dev
from structured_logging import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

# Always log startup info regardless of level
logger.setLevel(logging.INFO)

# Read and log version for easy identification in logs
try:
    version_file = os.path.join(os.path.dirname(__file__), 'VERSION')
    with open(version_file, 'r') as f:
        VERSION = f.read().strip()
    logger.info("=" * 80)
    logger.info(f"🚀 OfferWise v{VERSION} Starting Up 🚀")
    logger.info("=" * 80)
except Exception as e:
    VERSION = "unknown"
    logger.warning(f"⚠️  Could not read VERSION file: {e}")
    logger.info("🚀 OfferWise (version unknown) Starting Up 🚀")


# =========================================================================
# AUTO-SEED: Copy document repo to persistent disk on first boot (v5.62.29)
# After first successful seed, enable .dockerignore exclusions for lean deploys
# =========================================================================
def _auto_seed_docrepo():
    """Copy documents from Docker image to persistent disk if disk is empty."""
    import shutil
    disk_path = os.environ.get('DOCREPO_PATH', '/var/data/docrepo')
    local_path = os.path.join(os.path.dirname(__file__), 'document_repo')

    # Only run if persistent disk mount exists (Render production)
    if not os.path.isdir(disk_path):
        logger.info("📄 DocRepo: No persistent disk at %s (dev mode — using local files)", disk_path)
        return

    # Count existing files on disk (skip metadata)
    existing = 0
    for root, dirs, files in os.walk(disk_path):
        existing += len([f for f in files if not f.endswith('.json')])

    if existing > 0:
        logger.info("📄 DocRepo: Persistent disk has %d files — skipping seed", existing)
        return

    # Copy document files from image to persistent disk
    subdirs = ['inspection_reports', 'disclosure_statements', 'reference_docs',
               'html_reports/scribeware', 'html_reports/homegauge', 'html_reports/other']
    copied = 0
    for subdir in subdirs:
        src_dir = os.path.join(local_path, subdir)
        dst_dir = os.path.join(disk_path, subdir)
        if not os.path.isdir(src_dir):
            continue
        os.makedirs(dst_dir, exist_ok=True)
        for fname in os.listdir(src_dir):
            src_file = os.path.join(src_dir, fname)
            if os.path.isfile(src_file):
                shutil.copy2(src_file, os.path.join(dst_dir, fname))
                copied += 1

    if copied > 0:
        logger.info("📄 DocRepo: Auto-seeded %d files to persistent disk at %s", copied, disk_path)
        logger.info("📄 DocRepo: Future deploys can exclude document_repo/ binaries from Docker image")
    else:
        logger.warning("📄 DocRepo: No document files found in image to seed")

try:
    _auto_seed_docrepo()
except Exception as e:
    logger.warning("📄 DocRepo auto-seed failed (non-fatal): %s", e)

# Configuration

# Developer accounts — get 500 credits, enterprise tier, auto-refill
_DEV_EMAILS_DEFAULT = 'francis.kurupacheril@gmail.com,francis@piotnetworks.com'
DEVELOPER_EMAILS = [e.strip().lower() for e in os.environ.get('DEVELOPER_EMAILS', _DEV_EMAILS_DEFAULT).split(',') if e.strip()]

_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    if os.environ.get('FLASK_ENV') == 'development' or os.environ.get('RENDER') is None:
        _secret_key = 'dev-secret-key-local-only-' + secrets.token_hex(16)
        logging.warning("⚠️ SECRET_KEY not set — using random dev key. Sessions will reset on restart.")
    else:
        raise RuntimeError("❌ CRITICAL: SECRET_KEY environment variable must be set in production!")
app.config['SECRET_KEY'] = _secret_key

# Database configuration - default to SQLite, optionally use PostgreSQL
database_url = os.environ.get('DATABASE_URL', 'sqlite:///offerwise.db')

# CRITICAL: Warn if using SQLite in production (ephemeral filesystem = data loss)
if database_url.startswith('sqlite') and PRODUCTION_MODE:
    logging.critical(
        "⚠️  USING SQLITE IN PRODUCTION — DATA WILL BE LOST ON RESTART! "
        "Set DATABASE_URL to a PostgreSQL connection string (e.g., Render Managed PostgreSQL)."
    )

# Handle Render's postgres:// URL (needs to be postgresql://)
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

# SAFETY CHECK (v5.61.0): Warn loudly if using SQLite in production
_is_sqlite = 'sqlite' in database_url
if _is_sqlite and PRODUCTION_MODE:
    logging.critical("=" * 70)
    logging.critical("🚨 CRITICAL: Running SQLite in PRODUCTION!")
    logging.critical("   SQLite data WILL BE LOST on Render deploys/restarts.")
    logging.critical("   Set DATABASE_URL to a PostgreSQL connection string.")
    logging.critical("   Render: Dashboard → Environment → DATABASE_URL")
    logging.critical("=" * 70)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,       # Test connections before use — discards dead ones
    'pool_recycle': 300,         # Recycle connections every 5 min (Render PG drops idle connections)
    'pool_size': 5,              # Keep 5 connections in pool
    'max_overflow': 10,          # Allow 10 more under load
    'pool_timeout': 30,          # Wait up to 30s for a connection from pool
}
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max - handles comprehensive disclosure packages
app.config['UPLOAD_FOLDER'] = 'uploads'

# Force HTTPS for OAuth and production
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.config['SESSION_COOKIE_SECURE'] = True  # Only send cookies over HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Initialize extensions
db.init_app(app)

# Database migrations (v5.61.0) — replaces ad-hoc ALTER TABLE scripts
try:
    from flask_migrate import Migrate
    migrate = Migrate(app, db)
    logger.info("✅ Flask-Migrate initialized — use 'flask db migrate' for schema changes")
except ImportError:
    logger.warning("⚠️ Flask-Migrate not installed — using auto_migrate fallback")
    migrate = None

# Register blueprints
from payment_routes import payment_bp
app.register_blueprint(payment_bp)

from testing_routes import init_testing_blueprint
from bug_routes import init_bugs_blueprint
from docrepo_routes import init_docrepo_blueprint
from survey_routes import init_surveys_blueprint
from waitlist_routes import init_waitlist_blueprint
from sharing_routes import init_sharing_blueprint
from auth_routes import init_auth_blueprint
from analysis_routes import init_analysis_blueprint

# SECURITY: Rate limiting to prevent abuse (limiter instance from extensions.py)
from extensions import limiter as _ext_limiter
limiter = _ext_limiter
app.config['RATELIMIT_DEFAULT'] = "1000 per day;200 per hour"
app.config['RATELIMIT_STORAGE_URI'] = "memory://"
app.config['RATELIMIT_STRATEGY'] = "fixed-window"
limiter.init_app(app)
logger.info("✅ Rate limiting enabled")

_bp_args = (app, admin_required, api_admin_required, api_login_required, dev_only_gate, limiter)
init_testing_blueprint(*_bp_args)
init_bugs_blueprint(*_bp_args)
init_docrepo_blueprint(*_bp_args)
init_surveys_blueprint(*_bp_args)
init_waitlist_blueprint(*_bp_args)
init_sharing_blueprint(*_bp_args)
init_auth_blueprint(*_bp_args)
# NOTE: init_analysis_blueprint is called later (after intelligence/pdf_handler/job_manager are created)

# SECURITY: Add security headers to all responses
@app.after_request
def set_security_headers(response):
    """Add security headers and caching for static assets"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    
    # Cache static files aggressively (HTML pages refresh on deploy via new version)
    if request.path.endswith(('.css', '.js', '.svg', '.woff', '.woff2', '.ttf')):
        response.headers['Cache-Control'] = 'public, max-age=604800, immutable'  # 7 days
    elif request.path.endswith(('.png', '.jpg', '.jpeg', '.gif', '.ico', '.webp')):
        response.headers['Cache-Control'] = 'public, max-age=2592000'  # 30 days
    elif request.path.startswith('/static/') and request.path.endswith('.html'):
        response.headers['Cache-Control'] = 'public, max-age=300'  # 5 min for HTML pages
    elif request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://js.stripe.com https://accounts.google.com https://apis.google.com https://connect.facebook.net "
            "https://cdnjs.cloudflare.com https://unpkg.com https://client.crisp.chat "
            "https://www.googletagmanager.com https://www.google-analytics.com "
            "https://googleads.g.doubleclick.net https://*.doubleclick.net https://www.google.com; "
        "script-src-elem 'self' 'unsafe-inline' https://js.stripe.com https://accounts.google.com https://apis.google.com https://connect.facebook.net "
            "https://cdnjs.cloudflare.com https://unpkg.com https://client.crisp.chat "
            "https://www.googletagmanager.com https://www.google-analytics.com "
            "https://googleads.g.doubleclick.net https://*.doubleclick.net https://www.google.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://client.crisp.chat; "
        "font-src 'self' https://fonts.gstatic.com https://client.crisp.chat; "
        "img-src 'self' data: blob: https://*.googleusercontent.com https://*.facebook.com https://client.crisp.chat https://image.crisp.chat "
            "https://www.googletagmanager.com https://www.google-analytics.com https://www.google.com https://*.doubleclick.net https://www.googleadservices.com https://googleads.g.doubleclick.net; "
        "connect-src 'self' https://api.stripe.com https://accounts.google.com https://client.crisp.chat wss://client.relay.crisp.chat wss://stream.relay.crisp.chat https://unpkg.com "
            "https://www.googletagmanager.com https://www.google-analytics.com https://analytics.google.com https://*.google-analytics.com https://*.analytics.google.com "
            "https://www.google.com https://*.doubleclick.net https://www.googleadservices.com https://googleads.g.doubleclick.net; "
        "frame-src https://js.stripe.com https://accounts.google.com https://www.facebook.com https://game.crisp.chat "
            "https://www.googletagmanager.com https://googleads.g.doubleclick.net https://*.doubleclick.net; "
        "worker-src 'self' blob:; "
        "object-src 'none'; "
        "base-uri 'self'"
    )
    # Only set HSTS in production (HTTPS)
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

# SECURITY: Global CSRF protection for state-changing requests
@app.before_request
def csrf_protection():
    """
    Validate Origin/Referer for all state-changing requests.
    This protects against CSRF attacks on API endpoints.
    """
    # Only check POST, PUT, DELETE, PATCH
    if request.method not in ['POST', 'PUT', 'DELETE', 'PATCH']:
        return
    
    # Skip CSRF check for:
    # - Stripe webhooks (they use signature verification)
    # - OAuth callbacks (they use state parameter)
    # - Health checks
    exempt_paths = [
        '/webhook/stripe',
        '/webhook/resend',
        '/auth/google/callback',
        '/auth/facebook/callback', 
        '/auth/apple/callback',
        '/auth/register',          # Email/password signup
        '/auth/login-email',       # Email/password login
        '/auth/forgot-password',   # Password reset request
        '/auth/reset-password/',   # Password reset form
        '/api/health',
        '/api/auto-test/',     # Admin test endpoints (protected by admin_key)
        '/api/test/',          # Admin test endpoints (protected by admin_key)
        '/api/bugs',           # Admin bug tracker (protected by admin_key)
        '/api/cleanup/',       # Admin cleanup (protected by admin_key)
        '/api/turk/',          # Admin turk (protected by admin_key)
        '/api/system/',        # Admin system (protected by admin_key)
        '/api/survey/',        # Survey endpoints
        '/api/share/',         # Public share/reaction endpoints (no auth needed)
        '/api/contact',        # Public contact form (rate-limited)
        '/api/truth-check',    # Free disclosure scanner (rate-limited, no auth)
        '/api/risk-check',     # Free property risk scanner (rate-limited, no auth)
        '/api/waitlist/',      # Waitlist signup (rate-limited, no auth)
        '/api/docrepo/',       # Document repo (protected by admin_key)
        '/api/docrepo/test/',  # Parser test (protected by admin_key)
        '/api/docrepo/anonymize/',  # Anonymize (protected by admin_key)
        '/api/docrepo/check-sources',  # Source monitor (protected by admin_key)
        '/api/docrepo/seed',  # Seed persistent disk (protected by admin_key)
        '/api/docrepo/disk-status',  # Disk status (protected by admin_key)
        '/api/gtm/',         # GTM endpoints (protected by admin_key)
        '/api/funnel/',      # Public funnel tracking (fire-and-forget)
        '/api/nearby-listings/public',  # Public nearby listings for free tools hub (rate-limited)
    ]
    
    if any(request.path.startswith(path) for path in exempt_paths):
        return
    
    # In development, skip CSRF check
    if os.environ.get('FLASK_ENV') == 'development':
        return
    
    # Get origin from headers
    origin = request.headers.get('Origin')
    referer = request.headers.get('Referer')
    
    check_origin = None
    if origin:
        check_origin = origin
    elif referer:
        from urllib.parse import urlparse
        parsed = urlparse(referer)
        check_origin = f"{parsed.scheme}://{parsed.netloc}"
    
    # Allow requests with valid origin
    if check_origin and check_origin in ALLOWED_ORIGINS:
        return
    
    # Allow same-origin requests (no Origin header but valid session + custom header)
    # Browsers don't send Origin for same-origin, but we require X-Requested-With
    # as an extra CSRF guard since custom headers can't be set by cross-origin forms
    if not check_origin and request.cookies.get('session'):
        # For API endpoints, require X-Requested-With header
        if request.path.startswith('/api/') and not request.headers.get('X-Requested-With'):
            logger.warning(f"🛡️ CSRF: Blocked API request without Origin or X-Requested-With to {request.path}")
            return jsonify({'error': 'Invalid request origin'}), 403
        return
    
    # Block suspicious requests
    if check_origin:
        logger.warning(f"🛡️ CSRF: Blocked request from {check_origin} to {request.path}")
        return jsonify({'error': 'Invalid request origin'}), 403

# Create database tables and run health checks on startup
with app.app_context():
    try:
        # CRITICAL: Create all tables first (if they don't exist)
        logger.info("🔧 Creating database tables...")
        db.create_all()
        logger.info("✅ Database tables created/verified")
        
        # AUTO-MIGRATE: Schema sync now handled by Alembic/Flask-Migrate.
        # Manual column additions for backward compatibility are below.
        
        # Run automatic migrations for new features
        logger.info("🔄 Checking for database migrations...")
        try:
            from sqlalchemy import text, inspect
            
            # Check if referral columns exist
            inspector = inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('users')]
            
            if 'referral_code' not in columns:
                logger.info("🎁 Migrating database for referral system...")
                
                with db.engine.connect() as conn:
                    # Add referral columns
                    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code VARCHAR(50)"))
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)"))
                    
                    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by_code VARCHAR(50)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_referred_by_code ON users(referred_by_code)"))
                    
                    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by_user_id INTEGER REFERENCES users(id)"))
                    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_referrals INTEGER DEFAULT 0"))
                    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_tier INTEGER DEFAULT 0"))
                    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_credits_earned INTEGER DEFAULT 0"))
                    
                    conn.commit()
                
                logger.info("✅ Referral system migration complete")
                
                # Generate referral codes for existing users
                logger.info("🎫 Generating referral codes for existing users...")
                users_without_codes = User.query.filter_by(referral_code=None).all()
                for user in users_without_codes:
                    user.generate_referral_code()
                db.session.commit()
                logger.info(f"✅ Generated {len(users_without_codes)} referral codes")
            else:
                logger.info("✅ Referral system already migrated")
            
            # Check if comparisons table exists
            tables = inspector.get_table_names()
            if 'comparisons' not in tables:
                logger.info("🏆 Creating comparisons table for Property Battle Royale feature...")
                # db.create_all() will create the table since model exists
                db.create_all()
                logger.info("✅ Comparisons table created")
            else:
                logger.info("✅ Comparisons table already exists")
                
        except Exception as e:
            logger.warning(f"⚠️ Migration check failed (non-critical): {e}")
            # Continue anyway - app will work without comparisons
        
        # Drip campaign columns for Waitlist (v5.62.0)
        try:
            from sqlalchemy import text, inspect as sa_inspect
            wl_inspector = sa_inspect(db.engine)
            if 'waitlist' in wl_inspector.get_table_names():
                wl_cols = [c['name'] for c in wl_inspector.get_columns('waitlist')]
                new_cols = {
                    'result_address': 'VARCHAR(500)',
                    'result_grade': 'VARCHAR(5)',
                    'result_exposure': 'INTEGER',
                    'drip_step': 'INTEGER DEFAULT 0',
                    'drip_last_sent_at': 'TIMESTAMP',
                    'drip_completed': 'BOOLEAN DEFAULT FALSE',
                    'unsubscribe_token': 'VARCHAR(64)',
                    'email_unsubscribed': 'BOOLEAN DEFAULT FALSE',
                    'unsubscribed_at': 'TIMESTAMP',
                }
                missing = {k: v for k, v in new_cols.items() if k not in wl_cols}
                if missing:
                    logger.info(f"📧 Migrating waitlist for drip campaign ({len(missing)} columns)...")
                    with db.engine.connect() as conn:
                        for col, dtype in missing.items():
                            conn.execute(text(f"ALTER TABLE waitlist ADD COLUMN IF NOT EXISTS {col} {dtype}"))
                        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_waitlist_unsub_token ON waitlist(unsubscribe_token)"))
                        conn.commit()
                    logger.info("✅ Waitlist drip campaign migration complete")
                else:
                    logger.info("✅ Waitlist drip columns already exist")
                # Nearby-listings columns (v5.62.83)
                wl_cols2 = [c['name'] for c in wl_inspector.get_columns('waitlist')]
                nearby_cols = {
                    'result_zip': 'VARCHAR(10)',
                    'result_city': 'VARCHAR(100)',
                    'result_state': 'VARCHAR(2)',
                }
                nb_missing = {k: v for k, v in nearby_cols.items() if k not in wl_cols2}
                if nb_missing:
                    logger.info(f"🏘️ Migrating waitlist for nearby-listings ({len(nb_missing)} columns)...")
                    with db.engine.connect() as conn:
                        for col, dtype in nb_missing.items():
                            conn.execute(text(f"ALTER TABLE waitlist ADD COLUMN IF NOT EXISTS {col} {dtype}"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_waitlist_result_zip ON waitlist(result_zip)"))
                        conn.commit()
                    logger.info("✅ Waitlist nearby-listings migration complete")
        except Exception as e:
            logger.warning(f"⚠️ Waitlist migration failed (non-critical): {e}")
        
        # GTM platform + posted_url columns (v5.62.54)
        try:
            from sqlalchemy import text, inspect as gtm_inspect
            gtm_inspector = gtm_inspect(db.engine)
            gtm_tables = gtm_inspector.get_table_names()
            
            if 'gtm_scanned_threads' in gtm_tables:
                thread_cols = [c['name'] for c in gtm_inspector.get_columns('gtm_scanned_threads')]
                if 'platform' not in thread_cols:
                    logger.info("🚀 Migrating GTM: adding platform column...")
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE gtm_scanned_threads ADD COLUMN IF NOT EXISTS platform VARCHAR(30) DEFAULT 'reddit'"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_gtm_threads_platform ON gtm_scanned_threads(platform)"))
                        conn.commit()
                    logger.info("✅ GTM platform column added")
            
            if 'gtm_reddit_drafts' in gtm_tables:
                draft_cols = [c['name'] for c in gtm_inspector.get_columns('gtm_reddit_drafts')]
                if 'posted_url' not in draft_cols:
                    logger.info("🚀 Migrating GTM: adding posted_url column...")
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE gtm_reddit_drafts ADD COLUMN IF NOT EXISTS posted_url VARCHAR(500)"))
                        conn.commit()
                    logger.info("✅ GTM posted_url column added")
            
            if 'gtm_target_subreddits' in gtm_tables:
                target_cols = [c['name'] for c in gtm_inspector.get_columns('gtm_target_subreddits')]
                if 'platform' not in target_cols:
                    logger.info("🚀 Migrating GTM: adding platform/url to target communities...")
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE gtm_target_subreddits ADD COLUMN IF NOT EXISTS platform VARCHAR(30) DEFAULT 'reddit'"))
                        conn.execute(text("ALTER TABLE gtm_target_subreddits ADD COLUMN IF NOT EXISTS url VARCHAR(500)"))
                        # Drop old unique constraint on name only, add new one on name+platform
                        conn.execute(text("ALTER TABLE gtm_target_subreddits DROP CONSTRAINT IF EXISTS gtm_target_subreddits_name_key"))
                        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_target_name_platform ON gtm_target_subreddits(name, platform)"))
                        conn.commit()
                    logger.info("✅ GTM target communities platform/url columns added")
            
            # GTMSubredditPost table
            if 'gtm_subreddit_posts' not in gtm_tables:
                from models import GTMSubredditPost
                GTMSubredditPost.__table__.create(db.engine, checkfirst=True)
                logger.info("✅ Created gtm_subreddit_posts table")
            else:
                # Migrate existing table: add topic_key column if missing
                post_cols = [c['name'] for c in gtm_inspector.get_columns('gtm_subreddit_posts')]
                if 'topic_key' not in post_cols:
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE gtm_subreddit_posts ADD COLUMN topic_key VARCHAR(100)"))
                        conn.commit()
                    logger.info("✅ Added topic_key column to gtm_subreddit_posts")
                
        except Exception as e:
            logger.warning(f"⚠️ GTM migration failed (non-critical): {e}")

        # New tables: warranty_leads, insurance_leads, api_keys, inspector_referrals (v5.74.95)
        try:
            from models import WarrantyLead, InsuranceLead, APIKey, InspectorReferral
            for model in [WarrantyLead, InsuranceLead, APIKey, InspectorReferral]:
                model.__table__.create(db.engine, checkfirst=True)
            logger.info("✅ New tables created: warranty_leads, insurance_leads, api_keys, inspector_referrals")
        except Exception as e:
            logger.warning(f"⚠️ New table migration failed (non-critical): {e}")

        # Remove known-dead subreddits (v5.74.90)
        try:
            dead_subreddits = ['RealEstateAgent']
            with db.engine.connect() as conn:
                for name in dead_subreddits:
                    conn.execute(text("DELETE FROM gtm_target_subreddits WHERE name = :name"), {'name': name})
                conn.commit()
            logger.info(f"✅ Removed dead subreddits: {dead_subreddits}")
        except Exception as e:
            logger.warning(f"⚠️ Dead subreddit cleanup failed (non-critical): {e}")

        # contractor_leads new columns (v5.74.87/88)
        try:
            from sqlalchemy import inspect as sa_inspect
            cl_inspector = sa_inspect(db.engine)
            cl_cols = [c['name'] for c in cl_inspector.get_columns('contractor_leads')]
            new_cl_cols = {
                'assigned_contractor_id': 'INTEGER',          # no FK constraint — safer migration
                'sent_to_contractor_at':  'TIMESTAMP',
                'contacted_at':           'TIMESTAMP',
                'job_closed_at':          'TIMESTAMP',
                'job_value':              'DOUBLE PRECISION',
                'referral_fee_pct':       'DOUBLE PRECISION',
                'referral_fee_due':       'DOUBLE PRECISION',
                'referral_paid':          'BOOLEAN DEFAULT FALSE',
                'referral_paid_at':       'TIMESTAMP',
            }
            missing = {k: v for k, v in new_cl_cols.items() if k not in cl_cols}
            if missing:
                logger.info(f"🔧 contractor_leads: adding columns {list(missing.keys())}")
                with db.engine.connect() as conn:
                    for col, dtype in missing.items():
                        try:
                            conn.execute(text(f"ALTER TABLE contractor_leads ADD COLUMN IF NOT EXISTS {col} {dtype}"))
                            logger.info(f"  ✅ Added contractor_leads.{col}")
                        except Exception as col_err:
                            logger.error(f"  ❌ Failed to add contractor_leads.{col}: {col_err}")
                    conn.commit()
                logger.info("✅ contractor_leads migration complete")
            else:
                logger.info("✅ contractor_leads columns already up to date")
        except Exception as e:
            logger.error(f"❌ contractor_leads migration failed: {e}", exc_info=True)

        # warranty_leads / insurance_leads / api_keys commission columns (v5.74.97)
        try:
            from sqlalchemy import inspect as _sa_inspect
            _ins = _sa_inspect(db.engine)
            _migrations = {
                'warranty_leads': {
                    'partner_name':       'VARCHAR(100)',
                    'policy_value':       'DOUBLE PRECISION',
                    'commission_pct':     'DOUBLE PRECISION DEFAULT 0',
                    'commission_paid_at': 'TIMESTAMP',
                    'notes':              'TEXT',
                },
                'insurance_leads': {
                    'partner_name':       'VARCHAR(100)',
                    'annual_premium':     'DOUBLE PRECISION',
                    'commission_pct':     'DOUBLE PRECISION DEFAULT 0',
                    'commission_paid_at': 'TIMESTAMP',
                    'notes':              'TEXT',
                },
                'api_keys': {
                    'price_per_call':  'DOUBLE PRECISION DEFAULT 0',
                    'monthly_fee':     'DOUBLE PRECISION DEFAULT 0',
                    'revenue_month':   'DOUBLE PRECISION DEFAULT 0',
                    'revenue_total':   'DOUBLE PRECISION DEFAULT 0',
                    'invoice_day':     'INTEGER DEFAULT 1',
                    'billing_email':   'VARCHAR(255)',
                },
            }
            for table, cols in _migrations.items():
                try:
                    existing = [c['name'] for c in _ins.get_columns(table)]
                    missing = {k: v for k, v in cols.items() if k not in existing}
                    if missing:
                        with db.engine.connect() as conn:
                            for col, dtype in missing.items():
                                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {dtype}"))
                            conn.commit()
                        logger.info(f"✅ {table} migration: added {list(missing.keys())}")
                    else:
                        logger.info(f"✅ {table} commission columns already up to date")
                except Exception as te:
                    logger.error(f"❌ {table} migration failed: {te}", exc_info=True)
        except Exception as e:
            logger.error(f"❌ commission columns migration failed: {e}", exc_info=True)

        # Now run health checks (these assume tables exist)
        logger.info("🔍 Running automatic database health checks...")
        health_results = DatabaseHealth.check_and_fix_all(db)
        
        # Log results
        if health_results['zero_price_properties']['status'] == 'fixed':
            fixed = health_results['zero_price_properties']['fixed']
            unfixable = health_results['zero_price_properties'].get('unfixable', 0)
            if fixed > 0:
                logger.warning(
                    f"✅ Database auto-fix: Fixed {fixed} properties with $0 price"
                )
            if unfixable > 0:
                logger.warning(
                    f"⚠️ {unfixable} properties with $0 price need manual attention (user will see error)"
                )
        elif health_results['zero_price_properties']['status'] == 'needs_attention':
            unfixable = health_results['zero_price_properties'].get('unfixable', 0)
            logger.warning(
                f"⚠️ {unfixable} properties with $0 price need manual attention (kept for user)"
            )
        elif health_results['zero_price_properties']['status'] == 'healthy':
            logger.info("✅ Database health check: All properties have valid prices")
    except Exception as e:
        # Log the error but don't crash the app
        logger.error(f"❌ Database initialization failed: {e}")
        logger.error("⚠️  This may cause issues with user authentication and property storage")
        # Continue startup - app may still work for basic operations

# Log critical configuration settings
logger.info("=" * 80)
logger.info("📋 Configuration Summary:")
logger.info(f"   Version: {VERSION}")
logger.info(f"   Database: {database_url.split('@')[-1] if '@' in database_url else database_url}")
logger.info(f"   PaddleOCR Disabled: {os.environ.get('DISABLE_PADDLEOCR', 'false')}")
logger.info(f"   OCR Workers: {os.environ.get('OCR_PARALLEL_WORKERS', '2')}")
logger.info(f"   OCR DPI: {os.environ.get('OCR_DPI', '100')}")
logger.info(f"   Gunicorn Timeout: {os.environ.get('GUNICORN_TIMEOUT', '300')}s")
logger.info("=" * 80)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login_page'
login_manager.login_message = 'Please log in to access this page.'

@login_manager.unauthorized_handler
def unauthorized_api():
    """Return JSON 401 for API routes, redirect for HTML pages."""
    if request.path.startswith('/api/') or request.headers.get('Accept') == 'application/json':
        return jsonify({'error': 'Authentication required. Please log in.'}), 401
    return redirect(url_for('auth.login_page', next=request.url))

# Initialize OAuth
oauth = OAuth(app)

# Configure Google OAuth
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

# Configure Apple OAuth
apple_client_id = os.environ.get('APPLE_CLIENT_ID')
apple_client_secret = os.environ.get('APPLE_CLIENT_SECRET')
if apple_client_id and apple_client_secret:
    apple = oauth.register(
        name='apple',
        client_id=apple_client_id,
        client_secret=apple_client_secret,
        server_metadata_url='https://appleid.apple.com/.well-known/openid-configuration',
        client_kwargs={
            'scope': 'openid email name'
        }
    )
else:
    apple = None
    logging.warning("Apple OAuth not configured (missing APPLE_CLIENT_ID or APPLE_CLIENT_SECRET)")

# Configure Facebook OAuth (optional)
facebook_client_id = os.environ.get('FACEBOOK_CLIENT_ID')
facebook_client_secret = os.environ.get('FACEBOOK_CLIENT_SECRET')
if facebook_client_id and facebook_client_secret:
    facebook = oauth.register(
        name='facebook',
        client_id=facebook_client_id,
        client_secret=facebook_client_secret,
        access_token_url='https://graph.facebook.com/oauth/access_token',
        authorize_url='https://www.facebook.com/dialog/oauth',
        api_base_url='https://graph.facebook.com/',
        client_kwargs={
            'scope': 'email public_profile'
        }
    )
else:
    facebook = None
    logging.warning("Facebook OAuth not configured (missing FACEBOOK_CLIENT_ID or FACEBOOK_CLIENT_SECRET)")

# Initialize intelligence
parser = DocumentParser()
intelligence = OfferWiseIntelligence()
pdf_handler = PDFHandler()

# Initialize async PDF worker with memory-optimized settings
# Render free tier: 512MB RAM limit, so we use minimal workers
max_workers = int(os.environ.get('PDF_WORKER_THREADS', '2'))  # Reduced from 10 to 2
pdf_worker = initialize_worker(job_manager, pdf_handler, max_workers=max_workers)
logger.info(f"✅ Async PDF processing enabled with {max_workers} worker threads (memory-optimized)")

# Initialize analysis blueprint (needs intelligence, pdf_handler, pdf_worker, job_manager)
from legal_disclaimers import ANALYSIS_DISCLAIMER_VERSION
init_analysis_blueprint(*_bp_args,
    intelligence=intelligence, pdf_handler=pdf_handler, pdf_worker=pdf_worker,
    job_manager=job_manager, DEVELOPER_EMAILS=DEVELOPER_EMAILS, VERSION=VERSION,
    ANALYSIS_DISCLAIMER_VERSION=ANALYSIS_DISCLAIMER_VERSION)

# Create upload folder
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def detect_and_flag_special_properties(result_dict, disclosure_text, inspection_text):
    """
    Detect special property types and add warnings (Bug #34, #38, #39)
    
    Args:
        result_dict: Analysis result dictionary
        disclosure_text: Seller disclosure text
        inspection_text: Inspection report text
    
    Returns:
        Updated result_dict with special property warnings
    """
    combined_text = (disclosure_text + " " + inspection_text).lower()
    
    warnings = []
    
    # Foreclosure detection
    if any(keyword in combined_text for keyword in [
        'foreclosure', 'bank-owned', 'reo', 'repo', 'short sale',
        'sold as-is where-is', 'as-is sale', 'bank sale'
    ]):
        warnings.append("⚠️ FORECLOSURE/BANK-OWNED PROPERTY: Typically sold AS-IS with no disclosure or warranties. Property may have deferred maintenance. Cash buyers preferred. Extra due diligence required.")
    
    # Manufactured home
    if any(keyword in combined_text for keyword in [
        'manufactured home', 'mobile home', 'hud label', 'manufactured housing'
    ]):
        warnings.append("ℹ️ MANUFACTURED HOME: Different financing requirements apply. Verify permanent foundation status. Check for HUD certification label. Resale market more limited than site-built homes.")
    
    # Hoarder house
    if any(keyword in combined_text for keyword in [
        'hoarder', 'excessive debris', 'unable to inspect due to',
        'limited access due to', 'property filled with', 'access prevented'
    ]):
        warnings.append("⚠️ LIMITED INSPECTION: Full inspection not possible due to access limitations. Actual repair costs likely MUCH HIGHER than estimated. Professional cleaning and debris removal required before accurate assessment possible.")
    
    # Fire damage
    if any(keyword in combined_text for keyword in [
        'fire damage', 'smoke damage', 'burned', 'fire occurred',
        'previous fire', 'fire incident'
    ]):
        warnings.append("⚠️ FIRE DAMAGE HISTORY: Property has previous fire damage. Verify ALL repairs completed properly and to code. Insurance may be difficult or expensive to obtain. Check for smoke damage and structural integrity.")
    
    # Water damage
    if any(keyword in combined_text for keyword in [
        'flood damage', 'water intrusion', 'mold remediation',
        'extensive water damage', 'water damage', 'flood'
    ]):
        warnings.append("⚠️ WATER DAMAGE HISTORY: Property has previous water intrusion. Check thoroughly for mold, structural damage, and proper remediation. Verify all repairs completed by licensed professionals. May affect insurance rates.")
    
    # Unpermitted work (Bug #39)
    if any(keyword in combined_text for keyword in [
        'unpermitted', 'without permit', 'no permit', 'not permitted',
        'illegal addition', 'unapproved', 'code violation'
    ]):
        warnings.append("⚠️ UNPERMITTED WORK DETECTED: Property has work completed without proper permits. May require retroactive permitting (if possible) or removal. Can affect insurability, financing, and resale. Potential fines from city/county.")
    
    # Septic system
    if any(keyword in combined_text for keyword in [
        'septic', 'septic system', 'leach field', 'septic tank'
    ]):
        warnings.append("ℹ️ SEPTIC SYSTEM: Property uses septic (not city sewer). Requires professional inspection and regular maintenance. System failure can cost $25K-$50K to replace. Verify system age and condition.")
    
    # Well water
    if any(keyword in combined_text for keyword in [
        'well water', 'private well', 'well system'
    ]):
        warnings.append("ℹ️ WELL WATER: Property uses well (not city water). Requires water quality testing. Well maintenance and replacement can be costly. Verify adequate flow and water quality.")
    
    # Add warnings to critical_issues
    if warnings:
        if 'critical_issues' not in result_dict:
            result_dict['critical_issues'] = []
        
        # Insert warnings at top
        for warning in reversed(warnings):
            result_dict['critical_issues'].insert(0, warning)
        
        # Also log for debugging
        logging.info(f"Special property type detected: {len(warnings)} warnings added")
    
    return result_dict

# ============================================================================
# HTTPS ENFORCEMENT
# ============================================================================

@app.before_request
def enforce_https():
    """Redirect HTTP to HTTPS in production"""
    # Skip for local development
    if request.is_secure or request.host.startswith('localhost') or request.host.startswith('127.0.0.1'):
        return
    
    # Skip for health check endpoints
    if request.path in ['/health', '/api/health']:
        return
    
    # For production, enforce HTTPS
    if request.headers.get('X-Forwarded-Proto', 'http') != 'https':
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)

# ============================================================================
# AUTHENTICATION ROUTES (OAuth Only)
# ============================================================================

@app.route('/api/oauth-status')
def oauth_status():
    """Check which OAuth providers are configured"""
    return jsonify({
        'google': True,  # Always enabled (primary provider)
        'apple': apple is not None,
        'facebook': facebook is not None
    })

# ============================================================================
# DASHBOARD & PROPERTY MANAGEMENT
# ============================================================================



# ============================================================
# CONTRACTOR ROUTES (v5.74.82)
# ============================================================

# ============================================================
# FEATURE 1: WARRANTY & INSURANCE LEAD CAPTURE (v5.74.95)
# ============================================================

@app.route('/api/warranty-lead', methods=['POST'])
@login_required
def submit_warranty_lead():
    from models import WarrantyLead
    data = request.get_json() or {}

    # Configurable commission rate — default 10% of policy value
    # Set WARRANTY_COMMISSION_PCT in Render env to override (e.g. "15" for 15%)
    commission_pct = float(os.environ.get('WARRANTY_COMMISSION_PCT', '10'))
    # Typical warranty policy: $500–$700/yr. Use $600 as estimate until partner confirms real premium
    estimated_policy_value = float(os.environ.get('WARRANTY_POLICY_VALUE_EST', '600'))
    commission_due = round(estimated_policy_value * commission_pct / 100, 2)

    lead = WarrantyLead(
        user_id=current_user.id,
        user_email=current_user.email,
        user_name=data.get('user_name', current_user.name or ''),
        user_phone=data.get('user_phone', ''),
        property_address=data.get('property_address', ''),
        property_zip=data.get('property_zip', ''),
        repair_cost_est=data.get('repair_cost_est', ''),
        coverage_interest=data.get('coverage_interest', 'buyer_coverage'),
        commission_pct=commission_pct,
        policy_value=estimated_policy_value,
        commission_due=commission_due,
    )
    db.session.add(lead)
    db.session.commit()
    try:
        send_email(
            to_email=ADMIN_EMAIL,
            subject=f"🏠 Warranty Lead: {lead.property_address[:50]}",
            html_content=f"""<div style="font-family:sans-serif;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;max-width:600px;">
            <div style="font-size:18px;font-weight:800;color:#f97316;margin-bottom:12px;">🏠 New Home Warranty Lead</div>
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
              <tr><td style="padding:6px 0;color:#94a3b8;width:140px;">Buyer</td><td style="padding:6px 0;">{lead.user_name} · {lead.user_email}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;">Phone</td><td style="padding:6px 0;font-weight:700;color:#22c55e;">{lead.user_phone or '(not provided)'}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;">Property</td><td style="padding:6px 0;">{lead.property_address}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;">Repair costs</td><td style="padding:6px 0;color:#f59e0b;font-weight:700;">{lead.repair_cost_est}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;">Coverage</td><td style="padding:6px 0;">{lead.coverage_interest.replace('_',' ').title()}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;">Est. commission</td><td style="padding:6px 0;font-weight:700;color:#22c55e;">${commission_due:.2f} ({commission_pct}% of ${estimated_policy_value:.0f} policy)</td></tr>
            </table>
            <div style="margin-top:12px;font-size:11px;color:#475569;">Lead #{lead.id} · Forward to AHS, Choice, or First American</div>
            </div>"""
        )
    except Exception as e:
        logging.warning(f"Warranty lead email failed: {e}")
    return jsonify({'success': True, 'lead_id': lead.id, 'commission_due': commission_due})


@app.route('/api/insurance-lead', methods=['POST'])
@login_required
def submit_insurance_lead():
    from models import InsuranceLead
    import json as _json
    data = request.get_json() or {}

    # Configurable commission rate — default 15% of first-year premium
    # Set INSURANCE_COMMISSION_PCT in Render env to override
    commission_pct = float(os.environ.get('INSURANCE_COMMISSION_PCT', '15'))
    # Estimate premium from property value: ~0.1% of property value/yr is typical HO premium
    property_value = data.get('property_value') or 0
    estimated_premium = round(float(property_value) * 0.001, 2) if property_value else 1200.0
    estimated_premium = max(600.0, min(estimated_premium, 8000.0))  # clamp $600–$8K
    commission_due = round(estimated_premium * commission_pct / 100, 2)

    lead = InsuranceLead(
        user_id=current_user.id,
        user_email=current_user.email,
        user_name=data.get('user_name', current_user.name or ''),
        user_phone=data.get('user_phone', ''),
        property_address=data.get('property_address', ''),
        property_zip=data.get('property_zip', ''),
        property_value=property_value,
        risk_flags=_json.dumps(data.get('risk_flags', [])),
        coverage_type=data.get('coverage_type', 'standard'),
        annual_premium=estimated_premium,
        commission_pct=commission_pct,
        commission_due=commission_due,
    )
    db.session.add(lead)
    db.session.commit()
    try:
        flags = data.get('risk_flags', [])
        flags_html = ''.join(f'<li style="color:#f59e0b;">{f}</li>' for f in flags[:5]) if flags else '<li style="color:#64748b;">None flagged</li>'
        send_email(
            to_email=ADMIN_EMAIL,
            subject=f"🔒 Insurance Lead: {lead.property_address[:50]}",
            html_content=f"""<div style="font-family:sans-serif;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;max-width:600px;">
            <div style="font-size:18px;font-weight:800;color:#60a5fa;margin-bottom:12px;">🔒 New Insurance Lead</div>
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
              <tr><td style="padding:6px 0;color:#94a3b8;width:140px;">Buyer</td><td style="padding:6px 0;">{lead.user_name} · {lead.user_email}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;">Phone</td><td style="padding:6px 0;font-weight:700;color:#22c55e;">{lead.user_phone or '(not provided)'}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;">Property</td><td style="padding:6px 0;">{lead.property_address}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;">Value</td><td style="padding:6px 0;font-weight:700;">${lead.property_value:,.0f}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;">Coverage</td><td style="padding:6px 0;">{lead.coverage_type.replace('_',' ').title()}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;">Est. premium</td><td style="padding:6px 0;color:#f59e0b;font-weight:700;">${estimated_premium:,.0f}/yr</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;">Est. commission</td><td style="padding:6px 0;font-weight:700;color:#22c55e;">${commission_due:.2f} ({commission_pct}% of ${estimated_premium:,.0f})</td></tr>
            </table>
            <div style="margin-top:12px;"><div style="font-size:12px;color:#94a3b8;margin-bottom:4px;">Risk flags from analysis:</div><ul style="margin:0;padding-left:20px;font-size:12px;">{flags_html}</ul></div>
            <div style="margin-top:12px;font-size:11px;color:#475569;">Lead #{lead.id} · Forward to Hippo, Kin, or Policygenius</div>
            </div>"""
        )
    except Exception as e:
        logging.warning(f"Insurance lead email failed: {e}")
    return jsonify({'success': True, 'lead_id': lead.id, 'commission_due': commission_due})


# ============================================================
# FEATURE 4: INSPECTOR INVITE / REFERRAL FLOW (v5.74.95)
# ============================================================

@app.route('/api/inspector/invite', methods=['POST'])
@login_required
def inspector_send_invite():
    from models import InspectorReferral
    import secrets
    data = request.get_json() or {}
    referred_email = (data.get('email') or '').strip().lower()
    referred_name = (data.get('name') or '').strip()
    if not referred_email:
        return jsonify({'error': 'Email required'}), 400

    insp = Inspector.query.filter_by(user_id=current_user.id).first()
    if not insp:
        return jsonify({'error': 'Inspector account not found'}), 403

    # Check not already invited
    existing = InspectorReferral.query.filter_by(
        referrer_id=insp.id, referred_email=referred_email
    ).first()
    if existing:
        return jsonify({'error': 'Already invited this email'}), 409

    token = secrets.token_urlsafe(32)
    ref = InspectorReferral(
        referrer_id=insp.id,
        referred_email=referred_email,
        referred_name=referred_name,
        invite_token=token,
        invite_sent_at=datetime.utcnow(),
    )
    db.session.add(ref)
    db.session.commit()

    invite_url = f"https://www.getofferwise.ai/for-inspectors?invite={token}"
    referrer_name = current_user.name or insp.business_name or 'A colleague'
    try:
        send_email(
            to_email=referred_email,
            subject=f"{referrer_name} invited you to OfferWise Inspector Portal",
            html_content=f"""<div style="font-family:sans-serif;background:#0f172a;color:#f1f5f9;padding:32px;border-radius:16px;max-width:600px;">
            <div style="font-size:22px;font-weight:800;margin-bottom:8px;">You've been invited to OfferWise</div>
            <div style="font-size:14px;color:#94a3b8;margin-bottom:24px;">{referrer_name} thinks you'd find this useful.</div>
            <div style="padding:16px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:10px;margin-bottom:24px;">
              <div style="font-weight:700;color:#f97316;margin-bottom:8px;">What OfferWise does for inspectors:</div>
              <div style="font-size:13px;color:#94a3b8;line-height:1.8;">
                ✓ Your inspection report generates a full AI buyer analysis automatically<br>
                ✓ Buyers get a shareable PDF with your name and branding on it<br>
                ✓ 5 free analyses per month — no credit card required<br>
                ✓ Upgrade to Pro ($49/mo) for unlimited
              </div>
            </div>
            <a href="{invite_url}" style="display:inline-block;padding:14px 28px;background:linear-gradient(90deg,#f97316,#f59e0b);color:white;border-radius:10px;font-weight:700;text-decoration:none;font-size:15px;">Join OfferWise Free →</a>
            <div style="margin-top:20px;font-size:12px;color:#475569;">Both you and {referrer_name} get 2 bonus analyses when you sign up.</div>
            </div>"""
        )
    except Exception as e:
        logging.warning(f"Inspector invite email failed: {e}")
        return jsonify({'error': 'Email failed to send'}), 500

    logging.info(f"Inspector invite sent: {insp.id} → {referred_email}")
    return jsonify({'success': True, 'token': token})


@app.route('/api/inspector/invite/redeem', methods=['POST'])
def inspector_redeem_invite():
    """Called when a new inspector signs up via an invite link."""
    from models import InspectorReferral
    data = request.get_json() or {}
    token = (data.get('token') or '').strip()
    if not token:
        return jsonify({'error': 'Token required'}), 400

    ref = InspectorReferral.query.filter_by(invite_token=token).first()
    if not ref or ref.signed_up_at:
        return jsonify({'error': 'Invalid or already used token'}), 400

    ref.signed_up_at = datetime.utcnow()
    # Give referrer 2 bonus analyses
    referrer_insp = Inspector.query.get(ref.referrer_id)
    if referrer_insp and referrer_insp.monthly_quota != -1:
        referrer_insp.monthly_quota = (referrer_insp.monthly_quota or 5) + 2
    ref.bonus_granted = True
    db.session.commit()

    logging.info(f"Inspector invite redeemed: token={token[:8]}... referrer={ref.referrer_id}")
    return jsonify({'success': True, 'bonus_granted': True})


@app.route('/api/inspector/invites', methods=['GET'])
@login_required
def inspector_get_invites():
    from models import InspectorReferral
    insp = Inspector.query.filter_by(user_id=current_user.id).first()
    if not insp:
        return jsonify({'error': 'Not an inspector'}), 403
    refs = InspectorReferral.query.filter_by(referrer_id=insp.id)\
        .order_by(InspectorReferral.created_at.desc()).all()
    return jsonify({'invites': [{
        'email': r.referred_email,
        'name': r.referred_name,
        'sent_at': r.invite_sent_at.isoformat() if r.invite_sent_at else None,
        'signed_up': bool(r.signed_up_at),
        'bonus_granted': r.bonus_granted,
    } for r in refs]})


# ============================================================
# FEATURE 5: B2B PUBLIC API + KEY MANAGEMENT (v5.74.95)
# ============================================================

def _hash_api_key(key: str) -> str:
    import hashlib
    return hashlib.sha256(key.encode()).hexdigest()


@app.route('/api/v1/analyze', methods=['POST'])
@limiter.limit("100 per hour")
def b2b_analyze():
    """Public B2B API endpoint for external integrations."""
    from models import APIKey
    # Authenticate via Bearer token or X-API-Key header
    auth = request.headers.get('Authorization', '')
    api_key_raw = ''
    if auth.startswith('Bearer '):
        api_key_raw = auth[7:].strip()
    if not api_key_raw:
        api_key_raw = request.headers.get('X-API-Key', '').strip()
    if not api_key_raw:
        return jsonify({'error': 'API key required. Pass as Bearer token or X-API-Key header.'}), 401

    key_hash = _hash_api_key(api_key_raw)
    api_key = APIKey.query.filter_by(key_hash=key_hash, is_active=True).first()
    if not api_key:
        return jsonify({'error': 'Invalid or revoked API key.'}), 401

    # Rate limit per key
    if api_key.calls_month >= api_key.monthly_limit:
        return jsonify({
            'error': 'Monthly limit reached.',
            'calls_used': api_key.calls_month,
            'limit': api_key.monthly_limit
        }), 429

    data = request.get_json() or {}
    disclosure_text = data.get('disclosure_text', '')
    inspection_text = data.get('inspection_text', '')
    property_price = float(data.get('property_price', 0))
    property_address = data.get('property_address', '')

    if not disclosure_text or not inspection_text:
        return jsonify({'error': 'disclosure_text and inspection_text are required.'}), 400
    if property_price <= 0:
        return jsonify({'error': 'property_price must be a positive number.'}), 400

    try:
        from offerwise_intelligence import OfferWiseIntelligence, BuyerProfile
        intelligence = OfferWiseIntelligence(anthropic_api_key=os.environ.get('ANTHROPIC_API_KEY'))
        profile = BuyerProfile(
            max_budget=property_price * 1.1,
            repair_tolerance=data.get('repair_tolerance', 'moderate'),
            ownership_duration=data.get('ownership_duration', '5-10'),
            biggest_regret=data.get('biggest_regret', 'hidden_issues'),
            replaceability=data.get('replaceability', 'replaceable'),
            deal_breakers=data.get('deal_breakers', ['foundation', 'mold', 'electrical']),
        )
        result = intelligence.analyze_property(
            seller_disclosure_text=disclosure_text,
            inspection_report_text=inspection_text,
            property_price=property_price,
            buyer_profile=profile,
            property_address=property_address,
        )
        # Update key usage and accrue revenue
        api_key.calls_total = (api_key.calls_total or 0) + 1
        api_key.calls_month = (api_key.calls_month or 0) + 1
        api_key.last_used_at = datetime.utcnow()
        call_revenue = api_key.accrued_this_call()
        if call_revenue > 0:
            api_key.revenue_month = round((api_key.revenue_month or 0) + call_revenue, 4)
            api_key.revenue_total = round((api_key.revenue_total or 0) + call_revenue, 4)
        db.session.commit()

        return jsonify({
            'offer_score': result.offer_score,
            'risk_level': result.risk_level,
            'risk_score': result.risk_score,
            'deal_breakers': [{'issue': d.issue, 'severity': d.severity,
                               'cost_estimate': d.cost_estimate} for d in (result.deal_breakers or [])],
            'repair_costs': {'total_low': getattr(result.repair_estimate, 'total_low', 0),
                            'total_high': getattr(result.repair_estimate, 'total_high', 0)},
            'negotiation_leverage': result.negotiation_strategy.leverage_points if result.negotiation_strategy else [],
            'recommended_offer': result.offer_strategy.recommended_offer if result.offer_strategy else None,
            'transparency_score': result.transparency_score,
            'analysis_id': f"ow_{api_key.key_prefix}_{api_key.calls_total}",
        })
    except Exception as e:
        logging.error(f"B2B API analysis failed: {e}", exc_info=True)
        return jsonify({'error': 'Analysis failed. Please try again.'}), 500


@app.route('/api/keys', methods=['GET'])
@login_required
def list_api_keys():
    from models import APIKey
    keys = APIKey.query.filter_by(user_id=current_user.id, is_active=True)\
        .order_by(APIKey.created_at.desc()).all()
    return jsonify({'keys': [{
        'id': k.id, 'prefix': k.key_prefix, 'label': k.label,
        'tier': k.tier, 'calls_total': k.calls_total,
        'calls_month': k.calls_month, 'monthly_limit': k.monthly_limit,
        'last_used_at': k.last_used_at.isoformat() if k.last_used_at else None,
        'created_at': k.created_at.isoformat() if k.created_at else None,
    } for k in keys]})


@app.route('/api/keys', methods=['POST'])
@login_required
@limiter.limit("10 per day")
def create_api_key():
    from models import APIKey
    import secrets
    data = request.get_json() or {}
    label = (data.get('label') or 'My Integration').strip()[:100]
    # Check existing key count
    existing = APIKey.query.filter_by(user_id=current_user.id, is_active=True).count()
    if existing >= 5:
        return jsonify({'error': 'Maximum 5 API keys per account'}), 400

    raw_key = 'ow_live_' + secrets.token_urlsafe(32)
    prefix = raw_key[:12]
    key_hash = _hash_api_key(raw_key)

    api_key = APIKey(
        user_id=current_user.id,
        key_hash=key_hash,
        key_prefix=prefix,
        label=label,
        tier='standard',
        monthly_limit=100,
    )
    db.session.add(api_key)
    db.session.commit()

    # Return the raw key ONCE — never stored again
    return jsonify({'key': raw_key, 'prefix': prefix, 'label': label, 'id': api_key.id})


@app.route('/api/keys/<int:key_id>', methods=['DELETE'])
@login_required
def revoke_api_key(key_id):
    from models import APIKey
    key = APIKey.query.filter_by(id=key_id, user_id=current_user.id).first_or_404()
    key.is_active = False
    key.revoked_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/docs')
def api_docs():
    return send_from_directory('static', 'api-docs.html')


# ============================================================
# FEATURE 6: SSE ANALYSIS PROGRESS (v5.74.95)
# ============================================================

_analysis_progress = {}  # job_id → {phase, message, pct}

def set_analysis_progress(job_id: str, phase: str, message: str, pct: int):
    """Called from intelligence pipeline to broadcast progress."""
    _analysis_progress[job_id] = {'phase': phase, 'message': message, 'pct': pct}


@app.route('/api/analysis-progress/<job_id>')
@login_required
def analysis_progress_sse(job_id):
    """SSE endpoint — streams real phase updates during analysis."""
    import time as _time
    def generate():
        last_pct = -1
        for _ in range(180):  # max 90s at 0.5s intervals
            prog = _analysis_progress.get(job_id, {})
            pct = prog.get('pct', 0)
            if pct != last_pct:
                import json as _json
                yield f"data: {_json.dumps(prog)}\n\n"
                last_pct = pct
            if pct >= 100:
                yield "data: {\"phase\":\"complete\",\"pct\":100}\n\n"
                break
            _time.sleep(0.5)
        # Clean up
        _analysis_progress.pop(job_id, None)
    return app.response_class(generate(), mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/for-contractors')
def for_contractors_landing():
    return send_from_directory('static', 'for-contractors.html')


@app.route('/api/contractor/signup', methods=['POST'])
def contractor_signup():
    """Public signup — no login required. Contractor fills in form, we review manually."""
    data = request.get_json() or {}
    name  = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    if not name or not email:
        return jsonify({'error': 'Name and email are required.'}), 400

    # Deduplicate
    existing = Contractor.query.filter_by(email=email).first()
    if existing:
        return jsonify({'success': True, 'already_exists': True,
                        'message': "You're already in our network. We'll be in touch when leads match your area."})

    c = Contractor(
        name            = name,
        business_name   = (data.get('business_name') or '').strip(),
        email           = email,
        phone           = (data.get('phone') or '').strip(),
        website         = (data.get('website') or '').strip(),
        license_number  = (data.get('license_number') or '').strip(),
        license_state   = (data.get('license_state') or 'CA').strip(),
        trades          = ','.join(data.get('trades') or []),
        trade_notes     = (data.get('trade_notes') or '').strip(),
        service_cities  = (data.get('service_cities') or '').strip(),
        service_zips    = (data.get('service_zips') or '').strip(),
        service_radius_miles = int(data.get('service_radius_miles') or 25),
        avg_job_size    = int(data.get('avg_job_size') or 0) or None,
        status          = 'pending',
        source          = 'website',
    )
    db.session.add(c)
    db.session.commit()
    logging.info(f"🔨 New contractor signup: {c.business_name or c.name} ({c.email}) trades={c.trades}")

    # Notify admin
    try:
        trades_display = c.trades.replace(',', ', ') if c.trades else '—'
        send_email(
            to_email=ADMIN_EMAIL,
            subject=f"🔨 New Contractor Signup: {c.business_name or c.name}",
            html_content=f"""<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;">
            <div style="font-size:20px;font-weight:800;margin-bottom:16px;color:#f97316;">🔨 New Contractor Signup</div>
            <table style="width:100%;border-collapse:collapse;">
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;width:130px;">Name</td><td style="padding:6px 0;font-weight:600;">{c.name}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Business</td><td style="padding:6px 0;">{c.business_name or '—'}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Email</td><td style="padding:6px 0;">{c.email}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Phone</td><td style="padding:6px 0;">{c.phone or '—'}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">License</td><td style="padding:6px 0;">{c.license_number or '—'} ({c.license_state})</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Trades</td><td style="padding:6px 0;color:#f59e0b;font-weight:700;">{trades_display}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Service area</td><td style="padding:6px 0;">{c.service_cities or c.service_zips or '—'}</td></tr>
              <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Avg job size</td><td style="padding:6px 0;">{('$' + str(c.avg_job_size)) if c.avg_job_size else '—'}</td></tr>
            </table>
            {f'<div style="margin-top:12px;padding:12px;background:rgba(255,255,255,.05);border-radius:8px;font-size:13px;color:#94a3b8;">{c.trade_notes}</div>' if c.trade_notes else ''}
            <div style="margin-top:16px;font-size:11px;color:#475569;">Contractor ID #{c.id} · Status: pending review</div>
            </div>"""
        )
    except Exception as e:
        logging.error(f"Contractor signup email failed: {e}")

    return jsonify({'success': True, 'contractor_id': c.id})


@app.route('/api/admin/contractors', methods=['GET'])
@login_required
def admin_contractors_list():
    """Admin view of all contractors."""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    status_filter = request.args.get('status', '')
    q = Contractor.query.order_by(Contractor.created_at.desc())
    if status_filter:
        q = q.filter_by(status=status_filter)
    contractors = q.limit(200).all()
    return jsonify({'contractors': [c.to_dict() for c in contractors], 'total': len(contractors)})


@app.route('/api/admin/contractors/<int:contractor_id>', methods=['PATCH'])
@login_required
def admin_contractor_update(contractor_id):
    """Admin updates a contractor's status, notes, etc."""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    c = Contractor.query.get_or_404(contractor_id)
    data = request.get_json() or {}
    for field in ['status', 'notes', 'accepts_leads', 'license_verified',
                  'referral_fee_pct', 'referral_fee_agreed', 'max_leads_month']:
        if field in data:
            setattr(c, field, data[field])
    db.session.commit()
    return jsonify({'success': True, 'contractor': c.to_dict()})


@app.route('/api/admin/inspectors', methods=['GET'])
@login_required
def admin_inspectors_list():
    """Admin view of all inspectors."""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    inspectors = Inspector.query.order_by(Inspector.created_at.desc()).limit(200).all()
    out = []
    for insp in inspectors:
        user = User.query.get(insp.user_id)
        out.append({
            'id': insp.id,
            'user_id': insp.user_id,
            'email': user.email if user else '—',
            'name': user.name if user else '—',
            'business_name': insp.business_name,
            'license_number': insp.license_number,
            'license_state': insp.license_state,
            'phone': insp.phone,
            'plan': insp.plan,
            'monthly_quota': insp.monthly_quota,
            'monthly_used': insp.monthly_used,
            'total_reports': insp.total_reports,
            'total_buyers_converted': insp.total_buyers_converted,
            'is_verified': insp.is_verified,
            'is_active': insp.is_active,
            'created_at': insp.created_at.isoformat() if insp.created_at else None,
        })
    return jsonify({'inspectors': out, 'total': len(out)})


@app.route('/api/admin/inspectors/<int:inspector_id>', methods=['PATCH'])
@login_required
def admin_inspector_update(inspector_id):
    """Admin updates an inspector — verify, activate, change plan."""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    insp = Inspector.query.get_or_404(inspector_id)
    data = request.get_json() or {}
    for field in ['is_verified', 'is_active', 'plan', 'monthly_quota', 'notes']:
        if field in data:
            setattr(insp, field, data[field])
    if data.get('plan') == 'inspector_pro':
        insp.monthly_quota = -1
    db.session.commit()
    return jsonify({'success': True})


def find_matching_contractor(repair_system, property_zip):
    """Find active contractors who handle a given repair type in a given ZIP."""
    trade_key = (repair_system or '').lower()
    contractors = Contractor.query.filter_by(status='active', accepts_leads=True).all()
    matches = []
    for c in contractors:
        trades = [t.strip().lower() for t in (c.trades or '').split(',')]
        # Check trade match
        trade_match = any(trade_key in t or t in trade_key for t in trades) or 'general' in trades
        if not trade_match:
            continue
        # Check area match (ZIP or city)
        area_match = True
        if c.service_zips and property_zip:
            area_match = property_zip in [z.strip() for z in c.service_zips.split(',')]
        matches.append(c)
    return matches[:3]  # Return top 3


@app.route('/api/admin/leads', methods=['GET'])
@login_required
def admin_leads_list():
    """All contractor leads with full detail for admin."""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    status_filter = request.args.get('status', '')
    q = ContractorLead.query.order_by(ContractorLead.created_at.desc())
    if status_filter:
        q = q.filter_by(status=status_filter)
    leads = q.limit(300).all()
    out = []
    for lead in leads:
        contractor = Contractor.query.get(lead.assigned_contractor_id) if lead.assigned_contractor_id else None
        out.append({
            'id': lead.id,
            'created_at': lead.created_at.isoformat() if lead.created_at else None,
            'status': lead.status,
            'user_name': lead.user_name,
            'user_email': lead.user_email,
            'user_phone': lead.user_phone,
            'property_address': lead.property_address,
            'property_zip': lead.property_zip,
            'repair_system': lead.repair_system,
            'trade_needed': lead.trade_needed,
            'cost_estimate': lead.cost_estimate,
            'issue_description': lead.issue_description,
            'contact_timing': lead.contact_timing,
            'notes': lead.notes,
            'assigned_contractor_id': lead.assigned_contractor_id,
            'assigned_contractor_name': (contractor.business_name or contractor.name) if contractor else None,
            'assigned_contractor_email': contractor.email if contractor else None,
            'sent_to_contractor_at': lead.sent_to_contractor_at.isoformat() if lead.sent_to_contractor_at else None,
            'job_closed_at': lead.job_closed_at.isoformat() if lead.job_closed_at else None,
            'job_value': lead.job_value,
            'referral_fee_pct': lead.referral_fee_pct,
            'referral_fee_due': lead.fee_due(),
            'referral_paid': lead.referral_paid,
        })
    # Revenue summary
    closed = [l for l in out if l['status'] == 'closed']
    total_revenue = sum(l['referral_fee_due'] or 0 for l in closed)
    paid_revenue = sum((l['referral_fee_due'] or 0) for l in out if l.get('referral_paid'))
    return jsonify({
        'leads': out,
        'total': len(out),
        'summary': {
            'new': len([l for l in out if l['status'] == 'new']),
            'sent': len([l for l in out if l['status'] == 'sent']),
            'contacted': len([l for l in out if l['status'] == 'contacted']),
            'closed': len(closed),
            'total_revenue_due': round(total_revenue, 2),
            'total_revenue_paid': round(paid_revenue, 2),
        }
    })


@app.route('/api/admin/leads/<int:lead_id>/send', methods=['POST'])
@login_required
def admin_send_lead(lead_id):
    """Admin manually sends a lead to a specific contractor."""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    lead = ContractorLead.query.get_or_404(lead_id)
    data = request.get_json() or {}
    contractor_id = data.get('contractor_id')
    contractor = Contractor.query.get_or_404(contractor_id)

    timing_labels = {'asap': 'ASAP', 'this_week': 'This week', 'just_exploring': 'Just exploring'}
    timing = timing_labels.get(lead.contact_timing, lead.contact_timing or '—')

    html = f"""<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;">
      <div style="font-size:20px;font-weight:800;margin-bottom:4px;color:#f97316;">🔨 New Lead from OfferWise</div>
      <div style="font-size:13px;color:#94a3b8;margin-bottom:20px;">A homebuyer needs a {lead.repair_system} contractor in {lead.property_zip or lead.property_address}.</div>
      <table style="width:100%;border-collapse:collapse;">
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;width:130px;">Property</td><td style="padding:7px 0;font-weight:600;">{lead.property_address}</td></tr>
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Repair</td><td style="padding:7px 0;text-transform:capitalize;color:#f59e0b;font-weight:700;">{lead.repair_system}</td></tr>
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Est. cost</td><td style="padding:7px 0;font-weight:700;">{lead.cost_estimate or '—'}</td></tr>
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Buyer</td><td style="padding:7px 0;">{lead.user_name or '—'}</td></tr>
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Phone</td><td style="padding:7px 0;font-weight:700;color:#22c55e;">{lead.user_phone or '—'}</td></tr>
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Email</td><td style="padding:7px 0;">{lead.user_email}</td></tr>
        <tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Timing</td><td style="padding:7px 0;color:{'#22c55e' if lead.contact_timing == 'asap' else '#f1f5f9'};font-weight:{'700' if lead.contact_timing == 'asap' else '400'};">{timing}</td></tr>
      </table>
      {f'<div style="margin-top:12px;padding:12px;background:rgba(255,255,255,.05);border-radius:8px;font-size:13px;color:#94a3b8;">{lead.issue_description}</div>' if lead.issue_description else ''}
      <div style="margin-top:20px;padding:14px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:8px;font-size:13px;color:#94a3b8;">
        Reply to this email or call the buyer at <strong style="color:#22c55e;">{lead.user_phone or 'the number above'}</strong> to arrange a quote.
      </div>
      <div style="margin-top:12px;font-size:11px;color:#475569;">Sent via OfferWise · Lead #{lead.id}</div>
    </div>"""

    try:
        send_email(
            to_email=contractor.email,
            subject=f"🔨 Lead: {(lead.repair_system or 'Repair').title()} at {(lead.property_address or '')[:40]}",
            html_content=html,
            reply_to=lead.user_email,
        )
    except Exception as e:
        logging.error(f"Failed to send lead to contractor: {e}")
        return jsonify({'error': f'Email failed: {e}'}), 500

    # Update lead
    lead.assigned_contractor_id = contractor.id
    lead.sent_to_contractor_at = datetime.utcnow()
    lead.status = 'sent'
    contractor.leads_sent_total = (contractor.leads_sent_total or 0) + 1
    contractor.leads_sent_month = (contractor.leads_sent_month or 0) + 1
    db.session.commit()

    logging.info(f"📨 Lead #{lead_id} sent to contractor {contractor.email}")
    return jsonify({'success': True, 'contractor_email': contractor.email})


@app.route('/api/admin/leads/<int:lead_id>', methods=['PATCH'])
@login_required
def admin_update_lead(lead_id):
    """Update lead status, record job close, track revenue."""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    lead = ContractorLead.query.get_or_404(lead_id)
    data = request.get_json() or {}

    for field in ['status', 'notes', 'job_value', 'referral_fee_pct', 'referral_paid']:
        if field in data:
            setattr(lead, field, data[field])

    if data.get('status') == 'closed' and not lead.job_closed_at:
        lead.job_closed_at = datetime.utcnow()
        if lead.job_value and lead.referral_fee_pct:
            lead.referral_fee_due = lead.fee_due()

    if data.get('referral_paid') and not lead.referral_paid_at:
        lead.referral_paid_at = datetime.utcnow()

    db.session.commit()
    return jsonify({'success': True, 'referral_fee_due': lead.fee_due()})


# ============================================================
# REVENUE DASHBOARD ADMIN ROUTES (v5.74.97)
# ============================================================

@app.route('/api/admin/revenue', methods=['GET'])
@login_required
def admin_revenue_summary():
    """Unified revenue dashboard — all four streams."""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    from models import ContractorLead, WarrantyLead, InsuranceLead, APIKey, User

    # ── Contractor referral fees ──────────────────────────────
    c_leads = ContractorLead.query.order_by(ContractorLead.created_at.desc()).limit(300).all()
    contractor_data = []
    for l in c_leads:
        contractor_data.append({
            'id': l.id,
            'created_at': l.created_at.isoformat() if l.created_at else None,
            'user_name': l.user_name, 'user_email': l.user_email,
            'property_address': l.property_address,
            'repair_system': l.repair_system,
            'status': l.status,
            'job_value': l.job_value,
            'referral_fee_pct': l.referral_fee_pct,
            'referral_fee_due': l.fee_due(),
            'referral_paid': l.referral_paid,
            'referral_paid_at': l.referral_paid_at.isoformat() if l.referral_paid_at else None,
        })

    # ── Warranty commissions ──────────────────────────────────
    w_leads = WarrantyLead.query.order_by(WarrantyLead.created_at.desc()).limit(300).all()
    warranty_data = []
    for l in w_leads:
        comm = l.commission_calc() if hasattr(l, 'commission_calc') else (l.commission_due or 0)
        warranty_data.append({
            'id': l.id,
            'created_at': l.created_at.isoformat() if l.created_at else None,
            'user_name': l.user_name, 'user_email': l.user_email,
            'property_address': l.property_address,
            'coverage_interest': l.coverage_interest,
            'status': l.status,
            'partner_name': getattr(l, 'partner_name', None),
            'policy_value': getattr(l, 'policy_value', None),
            'commission_pct': getattr(l, 'commission_pct', 0) or 0,
            'commission_due': comm,
            'commission_paid': l.commission_paid,
            'commission_paid_at': getattr(l, 'commission_paid_at', None) and getattr(l, 'commission_paid_at').isoformat(),
        })

    # ── Insurance commissions ─────────────────────────────────
    i_leads = InsuranceLead.query.order_by(InsuranceLead.created_at.desc()).limit(300).all()
    insurance_data = []
    for l in i_leads:
        comm = l.commission_calc() if hasattr(l, 'commission_calc') else (l.commission_due or 0)
        insurance_data.append({
            'id': l.id,
            'created_at': l.created_at.isoformat() if l.created_at else None,
            'user_name': l.user_name, 'user_email': l.user_email,
            'property_address': l.property_address,
            'coverage_type': l.coverage_type,
            'property_value': l.property_value,
            'status': l.status,
            'partner_name': getattr(l, 'partner_name', None),
            'annual_premium': getattr(l, 'annual_premium', None),
            'commission_pct': getattr(l, 'commission_pct', 0) or 0,
            'commission_due': comm,
            'commission_paid': l.commission_paid,
            'commission_paid_at': getattr(l, 'commission_paid_at', None) and getattr(l, 'commission_paid_at').isoformat(),
        })

    # ── B2B API keys ──────────────────────────────────────────
    api_keys = APIKey.query.filter_by(is_active=True).order_by(APIKey.created_at.desc()).all()
    b2b_data = []
    for k in api_keys:
        owner = User.query.get(k.user_id)
        b2b_data.append({
            'id': k.id,
            'label': k.label, 'key_prefix': k.key_prefix, 'tier': k.tier,
            'owner_email': owner.email if owner else '—',
            'calls_month': k.calls_month or 0, 'calls_total': k.calls_total or 0,
            'monthly_limit': k.monthly_limit or 100,
            'price_per_call': getattr(k, 'price_per_call', 0) or 0,
            'monthly_fee': getattr(k, 'monthly_fee', 0) or 0,
            'revenue_month': getattr(k, 'revenue_month', 0) or 0,
            'revenue_total': getattr(k, 'revenue_total', 0) or 0,
            'billing_email': getattr(k, 'billing_email', None),
            'last_used_at': k.last_used_at.isoformat() if k.last_used_at else None,
        })

    # ── Totals ────────────────────────────────────────────────
    total_contractor = sum(l['referral_fee_due'] or 0 for l in contractor_data if l['status'] == 'closed')
    total_warranty   = sum(l['commission_due'] or 0 for l in warranty_data)
    total_insurance  = sum(l['commission_due'] or 0 for l in insurance_data)
    total_b2b        = sum(k['revenue_total'] or 0 for k in b2b_data)
    total_collected  = (
        sum(l['referral_fee_due'] or 0 for l in contractor_data if l.get('referral_paid')) +
        sum(l['commission_due'] or 0 for l in warranty_data if l.get('commission_paid')) +
        sum(l['commission_due'] or 0 for l in insurance_data if l.get('commission_paid'))
    )

    return jsonify({
        'contractor': contractor_data,
        'warranty': warranty_data,
        'insurance': insurance_data,
        'b2b': b2b_data,
        'summary': {
            'contractor': round(total_contractor, 2),
            'warranty':   round(total_warranty, 2),
            'insurance':  round(total_insurance, 2),
            'b2b':        round(total_b2b, 2),
            'total':      round(total_contractor + total_warranty + total_insurance + total_b2b, 2),
            'collected':  round(total_collected, 2),
        }
    })


@app.route('/api/admin/revenue/warranty/<int:lead_id>', methods=['PATCH'])
@login_required
def admin_update_warranty_lead(lead_id):
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    from models import WarrantyLead
    lead = WarrantyLead.query.get_or_404(lead_id)
    data = request.get_json() or {}
    for field in ['partner_name', 'policy_value', 'commission_pct', 'commission_paid', 'notes']:
        if field in data:
            setattr(lead, field, data[field])
    if 'policy_value' in data or 'commission_pct' in data:
        pv = lead.policy_value or 0
        pct = getattr(lead, 'commission_pct', 0) or 0
        lead.commission_due = round(pv * pct / 100, 2)
    if data.get('commission_paid') and not getattr(lead, 'commission_paid_at', None):
        lead.commission_paid_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'commission_due': lead.commission_due})


@app.route('/api/admin/revenue/insurance/<int:lead_id>', methods=['PATCH'])
@login_required
def admin_update_insurance_lead(lead_id):
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    from models import InsuranceLead
    lead = InsuranceLead.query.get_or_404(lead_id)
    data = request.get_json() or {}
    for field in ['partner_name', 'annual_premium', 'commission_pct', 'commission_paid', 'notes']:
        if field in data:
            setattr(lead, field, data[field])
    if 'annual_premium' in data or 'commission_pct' in data:
        ap = getattr(lead, 'annual_premium', 0) or 0
        pct = getattr(lead, 'commission_pct', 0) or 0
        lead.commission_due = round(ap * pct / 100, 2)
    if data.get('commission_paid') and not getattr(lead, 'commission_paid_at', None):
        lead.commission_paid_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'commission_due': lead.commission_due})


@app.route('/api/admin/revenue/b2b/<int:key_id>', methods=['PATCH'])
@login_required
def admin_update_b2b_key(key_id):
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    from models import APIKey
    key = APIKey.query.get_or_404(key_id)
    data = request.get_json() or {}
    for field in ['price_per_call', 'monthly_fee', 'tier', 'monthly_limit', 'billing_email']:
        if field in data:
            setattr(key, field, data[field])
    if data.get('reset_month_revenue'):
        key.revenue_month = 0
        key.calls_month = 0
    db.session.commit()
    return jsonify({'success': True})


# ============================================================
# INSPECTOR PORTAL ROUTES (v5.74.81)
# ============================================================

@app.route('/for-inspectors')
def for_inspectors_landing():
    return send_from_directory('static', 'for-inspectors.html')

@app.route('/inspector-portal')
@login_required
def inspector_portal():
    return send_from_directory('static', 'inspector-portal.html')

@app.route('/api/inspector/register', methods=['POST'])
@login_required
def inspector_register():
    """Register current user as an inspector."""
    data = request.get_json() or {}
    existing = Inspector.query.filter_by(user_id=current_user.id).first()
    if existing:
        return jsonify({'success': True, 'inspector_id': existing.id, 'already_exists': True})

    from datetime import timedelta
    insp = Inspector(
        user_id       = current_user.id,
        business_name = data.get('business_name', ''),
        license_number= data.get('license_number', ''),
        license_state = data.get('license_state', 'CA'),
        phone         = data.get('phone', ''),
        website       = data.get('website', ''),
        service_areas = data.get('service_areas', ''),
        plan          = 'free',
        monthly_quota = 5,
        monthly_used  = 0,
        quota_reset_at= datetime.utcnow() + timedelta(days=30),
    )
    db.session.add(insp)
    db.session.commit()
    logging.info(f"🔍 New inspector registered: {current_user.email} — {insp.business_name}")
    # Notify admin
    try:
        send_email(
            to_email=ADMIN_EMAIL,
            subject=f"🔍 New Inspector Signup: {insp.business_name or current_user.email}",
            html_content=f"""<div style="font-family:sans-serif;padding:20px;">
            <h2 style="color:#f97316;">New Inspector Registered</h2>
            <p><b>Name:</b> {current_user.name}</p>
            <p><b>Email:</b> {current_user.email}</p>
            <p><b>Business:</b> {insp.business_name or '—'}</p>
            <p><b>License:</b> {insp.license_number or '—'} ({insp.license_state})</p>
            <p><b>Phone:</b> {insp.phone or '—'}</p>
            <p><b>Areas:</b> {insp.service_areas or '—'}</p>
            </div>"""
        )
    except Exception as e:
        logging.error(f"Inspector signup email failed: {e}")
    return jsonify({'success': True, 'inspector_id': insp.id})

@app.route('/api/inspector/profile', methods=['GET', 'POST'])
@login_required
def inspector_profile_get():
    insp = Inspector.query.filter_by(user_id=current_user.id).first()
    if request.method == 'POST':
        data = request.get_json() or {}
        if not insp:
            return jsonify({'error': 'Not registered'}), 403
        insp.business_name  = data.get('business_name', insp.business_name)
        insp.license_number = data.get('license_number', insp.license_number)
        insp.license_state  = data.get('license_state', insp.license_state)
        insp.phone          = data.get('phone', insp.phone)
        insp.website        = data.get('website', insp.website)
        insp.service_areas  = data.get('service_areas', insp.service_areas)
        db.session.commit()
        return jsonify({'success': True})
    if not insp:
        return jsonify({'registered': False})
    return jsonify({
        'registered': True,
        'id': insp.id,
        'business_name': insp.business_name,
        'license_number': insp.license_number,
        'license_state': insp.license_state,
        'phone': insp.phone,
        'website': insp.website,
        'service_areas': insp.service_areas,
        'plan': insp.plan,
        'monthly_quota': insp.monthly_quota,
        'monthly_used': insp.monthly_used,
        'total_reports': insp.total_reports,
        'total_buyers_converted': insp.total_buyers_converted,
        'quota_remaining': max(0, insp.monthly_quota - insp.monthly_used) if insp.monthly_quota > 0 else 999,
    })

@app.route('/api/inspector/analyze', methods=['POST'])
@login_required
def inspector_analyze():
    """Inspector uploads a report PDF and generates a buyer-facing analysis."""
    import secrets, json as _json
    from pdf_handler import PDFHandler

    insp = Inspector.query.filter_by(user_id=current_user.id).first()
    if not insp:
        return jsonify({'error': 'Not registered as an inspector. Please register first.'}), 403

    # Quota check
    if insp.monthly_quota > 0 and insp.monthly_used >= insp.monthly_quota:
        return jsonify({
            'error': 'Monthly quota reached',
            'message': f'You have used all {insp.monthly_quota} analyses this month. Upgrade to Inspector Pro for unlimited analyses.',
            'upgrade_url': '/for-inspectors#pricing'
        }), 403

    data = request.get_json() or {}
    inspection_text = data.get('inspection_text', '')
    disclosure_text = data.get('disclosure_text', '')
    property_address = data.get('property_address', '')
    property_price   = data.get('property_price', 0)
    buyer_name       = data.get('buyer_name', '')
    buyer_email      = data.get('buyer_email', '')

    if not inspection_text or len(inspection_text) < 100:
        return jsonify({'error': 'Inspection report text is required (minimum 100 characters).'}), 400
    if not property_address:
        return jsonify({'error': 'Property address is required.'}), 400

    # Run the analysis using the same AI engine
    try:
        from offerwise_intelligence import OfferWiseIntelligence
        intel = OfferWiseIntelligence()
        result = intel.analyze_property(
            seller_disclosure_text=disclosure_text or '',
            inspection_report_text=inspection_text,
            property_price=int(float(property_price)) if property_price else 0,
            property_address=property_address,
            buyer_profile={
                'max_budget': int(float(property_price)) if property_price else 0,
                'repair_tolerance': 'moderate',
                'biggest_regret': 'hidden_issues',
            }
        )
    except Exception as e:
        logging.error(f"Inspector analysis failed: {e}")
        return jsonify({'error': 'Analysis failed. Please try again.'}), 500

    # Create InspectorReport with share token
    token = secrets.token_urlsafe(16)
    report = InspectorReport(
        inspector_id             = insp.id,
        inspector_user_id        = current_user.id,
        property_address         = property_address,
        property_price           = float(property_price) if property_price else 0,
        buyer_name               = buyer_name,
        buyer_email              = buyer_email,
        analysis_json            = _json.dumps(result),
        share_token              = token,
        inspector_name_on_report = current_user.name or insp.business_name,
        inspector_biz_on_report  = insp.business_name,
    )
    db.session.add(report)

    # Update inspector stats
    insp.monthly_used  += 1
    insp.total_reports += 1
    db.session.commit()

    share_url = f"{request.host_url.rstrip('/')}//inspector-report/{token}"
    # Fix double slash
    share_url = f"https://{request.host}/inspector-report/{token}"

    logging.info(f"✅ Inspector report created: {token} for {property_address}")
    return jsonify({
        'success': True,
        'report_id': report.id,
        'share_token': token,
        'share_url': share_url,
        'property_address': property_address,
    })

@app.route('/api/inspector/reports', methods=['GET'])
@login_required
def inspector_reports_list():
    """List all reports for this inspector."""
    import json as _json
    insp = Inspector.query.filter_by(user_id=current_user.id).first()
    if not insp:
        return jsonify({'error': 'Not registered'}), 403
    reports = InspectorReport.query.filter_by(inspector_id=insp.id)\
                .order_by(InspectorReport.created_at.desc()).limit(50).all()
    out = []
    for r in reports:
        result = {}
        try:
            result = _json.loads(r.analysis_json) if r.analysis_json else {}
        except Exception:
            pass
        risk_dna = result.get('risk_dna', {})
        composite = risk_dna.get('composite_score', 0)
        offer_score = round(100 - composite)
        out.append({
            'id': r.id,
            'created_at': r.created_at.isoformat(),
            'property_address': r.property_address,
            'property_price': r.property_price,
            'buyer_name': r.buyer_name,
            'buyer_email': r.buyer_email,
            'share_token': r.share_token,
            'share_url': f"https://{request.host}/inspector-report/{r.share_token}",
            'offer_score': offer_score,
            'view_count': r.view_count,
            'buyer_viewed': bool(r.buyer_viewed_at),
            'buyer_registered': r.buyer_registered,
            'buyer_converted': r.buyer_converted,
        })
    return jsonify({'reports': out, 'total': len(out)})

@app.route('/inspector-report/<token>')
def inspector_report_view(token):
    """Public buyer-facing report page."""
    report = InspectorReport.query.filter_by(share_token=token).first_or_404()
    # Track view
    report.view_count = (report.view_count or 0) + 1
    if not report.buyer_viewed_at:
        report.buyer_viewed_at = datetime.utcnow()
    db.session.commit()
    return send_from_directory('static', 'inspector-report.html')

@app.route('/api/inspector-report/<token>', methods=['GET'])
def inspector_report_data(token):
    """Return report JSON for the buyer-facing page."""
    import json as _json
    report = InspectorReport.query.filter_by(share_token=token).first_or_404()
    result = {}
    try:
        result = _json.loads(report.analysis_json) if report.analysis_json else {}
    except Exception:
        pass
    return jsonify({
        'property_address': report.property_address,
        'property_price': report.property_price,
        'buyer_name': report.buyer_name,
        'inspector_name': report.inspector_name_on_report,
        'inspector_biz': report.inspector_biz_on_report,
        'created_at': report.created_at.isoformat(),
        'result': result,
    })


@app.route('/api/contractor-lead', methods=['POST'])
@login_required
def submit_contractor_lead():
    """Save a contractor quote request and notify admin immediately."""
    data = request.get_json() or {}

    # Extract ZIP from address if not provided
    address = data.get('property_address', '')
    zip_code = data.get('property_zip', '')
    if not zip_code:
        import re
        m = re.search(r'\b(\d{5})\b', address)
        zip_code = m.group(1) if m else ''

    lead = ContractorLead(
        user_id           = current_user.id,
        user_email        = current_user.email,
        user_name         = data.get('user_name', current_user.name or ''),
        user_phone        = data.get('user_phone', ''),
        property_address  = address,
        property_zip      = zip_code,
        repair_system     = data.get('repair_system', ''),
        trade_needed      = data.get('trade_needed', ''),
        cost_estimate     = data.get('cost_estimate', ''),
        issue_description = data.get('issue_description', ''),
        contact_timing    = data.get('contact_timing', 'this_week'),
        status            = 'new',
    )
    db.session.add(lead)
    db.session.commit()

    logging.info(f"🔧 NEW CONTRACTOR LEAD: {lead.repair_system} for {lead.user_email} at {lead.property_address}")

    # Email admin immediately
    try:
        timing_labels = {'asap': 'ASAP', 'this_week': 'This week', 'just_exploring': 'Just exploring'}
        timing = timing_labels.get(lead.contact_timing, lead.contact_timing)
        html = f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;">
          <div style="background:linear-gradient(90deg,#f97316,#f59e0b);-webkit-background-clip:text;-webkit-text-fill-color:transparent;font-size:22px;font-weight:800;margin-bottom:16px;">🔧 New Contractor Lead</div>
          <table style="width:100%;border-collapse:collapse;">
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;width:140px;">Repair needed</td><td style="padding:8px 0;font-weight:700;text-transform:capitalize;">{lead.repair_system}</td></tr>
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;">Trade</td><td style="padding:8px 0;">{lead.trade_needed}</td></tr>
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;">Est. cost</td><td style="padding:8px 0;color:#f59e0b;font-weight:700;">{lead.cost_estimate}</td></tr>
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;">Property</td><td style="padding:8px 0;">{lead.property_address}</td></tr>
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;">User</td><td style="padding:8px 0;">{lead.user_name} · {lead.user_email}</td></tr>
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;">Phone</td><td style="padding:8px 0;">{lead.user_phone or '(not provided)'}</td></tr>
            <tr><td style="padding:8px 0;color:#94a3b8;font-size:13px;">Timing</td><td style="padding:8px 0;color:{'#22c55e' if lead.contact_timing == 'asap' else '#f1f5f9'};font-weight:{'700' if lead.contact_timing == 'asap' else '400'};">{timing}</td></tr>
          </table>
          {f'<div style="margin-top:12px;padding:12px;background:rgba(255,255,255,0.05);border-radius:8px;font-size:13px;color:#94a3b8;">{lead.issue_description}</div>' if lead.issue_description else ''}
          <div style="margin-top:20px;font-size:11px;color:#475569;">Lead ID #{lead.id} · {lead.created_at.strftime('%Y-%m-%d %H:%M')} PT</div>
        </div>
        """
        send_email(
            to_email=ADMIN_EMAIL,
            subject=f"🔧 Contractor Lead: {lead.repair_system.title()} — {lead.property_address[:40]}",
            html_content=html,
        )
    except Exception as e:
        logging.error(f"Failed to send contractor lead email: {e}")

    # Auto-notify any matching active contractors in the network
    try:
        matches = find_matching_contractor(lead.repair_system, lead.property_zip)
        for contractor in matches:
            timing_labels = {'asap': 'ASAP', 'this_week': 'This week', 'just_exploring': 'Just exploring'}
            timing = timing_labels.get(lead.contact_timing, lead.contact_timing)
            contractor_html = f"""
            <div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;">
              <div style="font-size:20px;font-weight:800;margin-bottom:4px;color:#f97316;">🔨 New Lead from OfferWise</div>
              <div style="font-size:13px;color:#94a3b8;margin-bottom:20px;">A homebuyer needs a {lead.repair_system} contractor.</div>
              <table style="width:100%;border-collapse:collapse;">
                <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;width:130px;">Property</td><td style="padding:6px 0;font-weight:600;">{lead.property_address}</td></tr>
                <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Repair type</td><td style="padding:6px 0;text-transform:capitalize;color:#f59e0b;font-weight:700;">{lead.repair_system}</td></tr>
                <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Est. cost</td><td style="padding:6px 0;font-weight:700;">{lead.cost_estimate or '—'}</td></tr>
                <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Buyer name</td><td style="padding:6px 0;">{lead.user_name or '—'}</td></tr>
                <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Buyer phone</td><td style="padding:6px 0;font-weight:700;color:#22c55e;">{lead.user_phone or '—'}</td></tr>
                <tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">Timing</td><td style="padding:6px 0;color:{'#22c55e' if lead.contact_timing == 'asap' else '#f1f5f9'};font-weight:{'700' if lead.contact_timing == 'asap' else '400'};">{timing}</td></tr>
              </table>
              <div style="margin-top:20px;padding:14px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:8px;font-size:13px;color:#94a3b8;">
                This lead was sent to you because your service area and trade match this request. Reply to this email or call the buyer directly at {lead.user_phone or 'the number above'}.
              </div>
              <div style="margin-top:12px;font-size:11px;color:#475569;">OfferWise Contractor Network · Lead #{lead.id}</div>
            </div>"""
            send_email(
                to_email=contractor.email,
                subject=f"🔨 New Lead: {lead.repair_system.title()} at {(lead.property_address or '')[:40]}",
                html_content=contractor_html,
                reply_to=lead.user_email,
            )
            contractor.leads_sent_month = (contractor.leads_sent_month or 0) + 1
            contractor.leads_sent_total = (contractor.leads_sent_total or 0) + 1
        if matches:
            db.session.commit()
            logging.info(f"📨 Notified {len(matches)} contractor(s) for lead #{lead.id}")
    except Exception as e:
        logging.error(f"Contractor matching failed for lead #{lead.id}: {e}")

    return jsonify({'success': True, 'lead_id': lead.id})


@app.route('/api/user/credits')
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
        'total_credits_purchased': current_user.total_credits_purchased or 0,
        'user_id': current_user.id,
        'email': current_user.email,
        'authenticated': True,
        'has_paid': bool(current_user.stripe_customer_id) or is_developer
    }
    
    logging.info(f"📤 Returning: {response_data}")
    logging.info("💳" * 50)
    logging.info("")
    
    return jsonify(response_data)

@app.route('/api/usage')
@login_required
def get_usage():
    """Get user's current usage and limits for settings page"""
    try:
        # Get usage record (creates one if doesn't exist)
        usage = current_user.get_current_usage()
        
        # Get tier limits
        limits = current_user.get_tier_limits()
        
        # Build response
        analyses_used = usage.properties_analyzed if usage else 0
        analyses_limit = limits.get('properties_per_month', 3)
        
        logging.info(f"📊 Usage API: user={current_user.id}, used={analyses_used}, limit={analyses_limit}, tier={current_user.tier}")
        
        return jsonify({
            'analyses_used': analyses_used,
            'analyses_limit': analyses_limit,
            'tier': current_user.tier
        })
    except Exception as e:
        logging.error(f"❌ Error in /api/usage endpoint: {e}")
        import traceback
        logging.error(traceback.format_exc())
        
        # Return safe defaults rather than 500
        return jsonify({
            'analyses_used': 0,
            'analyses_limit': 3,
            'tier': 'free',
            'error': 'An internal error occurred. Please try again.'
        }), 200  # Return 200 with error info rather than 500

@app.route('/api/user/referrals')
@login_required
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

@app.route('/onboarding')
@login_required
def onboarding():
    """Dedicated onboarding wizard for new users"""
    # Check if user has already completed onboarding
    if current_user.onboarding_completed:
        logging.info(f"✅ User {current_user.id} already completed onboarding - redirecting to settings")
        return redirect('/settings?tab=analyses')
    
    logging.info(f"📝 User {current_user.id} starting onboarding wizard")
    return send_from_directory('static', 'onboarding.html')

@app.route('/api/user/complete-onboarding', methods=['POST'])
@login_required
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

@app.route('/dashboard')
@login_required
def dashboard():
    """
    DEPRECATED: Dashboard has been moved to Settings > Analyses tab
    Redirect to /settings for unified experience
    """
    logging.info(f"📍 User {current_user.id} accessed /dashboard - redirecting to /settings?tab=analyses")
    return redirect('/settings?tab=analyses')

@app.route('/api/dashboard/stats')
@login_required
def dashboard_stats():
    """Get dashboard statistics"""
    properties = Property.query.filter_by(user_id=current_user.id).all()
    usage = current_user.get_current_usage()
    limits = current_user.get_tier_limits()
    
    # Calculate storage
    total_storage = db.session.query(db.func.sum(Document.file_size_bytes)).filter(
        Document.property_id.in_([p.id for p in properties])
    ).scalar() or 0
    storage_mb = total_storage / (1024 * 1024)
    
    return jsonify({
        'tier': current_user.tier,
        'tier_name': PRICING_TIERS[current_user.tier]['name'],
        'usage': {
            'properties_analyzed': usage.properties_analyzed,
            'properties_limit': limits['properties_per_month'],
            'storage_mb': round(storage_mb, 2),
            'storage_limit_mb': limits['storage_mb']
        },
        'properties_count': len(properties),
        'recent_properties': [{
            'id': p.id,
            'address': p.address,
            'price': p.price,
            'status': p.status,
            'created_at': p.created_at.isoformat()
        } for p in properties[:5]]
    })

@app.route('/api/properties')
@login_required
def list_properties():
    """List all user properties"""
    properties = Property.query.filter_by(user_id=current_user.id).order_by(Property.created_at.desc()).all()
    
    return jsonify({
        'properties': [{
            'id': p.id,
            'address': p.address,
            'price': p.price,
            'status': p.status,
            'analyzed_at': p.analyzed_at.isoformat() if p.analyzed_at else None,
            'created_at': p.created_at.isoformat(),
            'documents_count': p.documents.count(),
            'has_analysis': p.analyses.count() > 0
        } for p in properties]
    })

# ============================================================================
# 🏘️ Nearby Listings — market intelligence on the dashboard, zero onboarding
# ============================================================================

@app.route('/api/nearby-listings/public', methods=['GET'])
@limiter.limit("10 per hour")
def api_nearby_listings_public():
    """Public nearby listings for free tools hub. Requires explicit ZIP. Rate limited."""
    from nearby_listings import get_nearby_listings
    zip_code = request.args.get('zip', '').strip()
    if not zip_code or len(zip_code) != 5 or not zip_code.isdigit():
        return jsonify({'error': 'Please enter a valid 5-digit ZIP code.'}), 400
    try:
        result = get_nearby_listings(zip_code, limit=5)
        return jsonify(result)
    except Exception as e:
        logging.warning(f"Public nearby listings error: {e}")
        return jsonify({'error': 'Could not fetch listings for this area.', 'listings': []}), 200

@app.route('/api/nearby-listings', methods=['GET'])
@login_required
def api_nearby_listings():
    """
    Return enriched active listings near the user.

    ZIP source priority:
      1. ?zip= query parameter (explicit)
      2. User's most recently analyzed property address (auto-detect)
      3. User's waitlist record from Risk Check (they checked an address before signing up)

    Returns up to 5 listings with offer range, risk tier, leverage score,
    condition flags, and a plain-English briefing.
    """
    from nearby_listings import get_nearby_listings

    # Determine ZIP code
    zip_code = request.args.get('zip', '').strip()

    if not zip_code:
        # Auto-detect from most recent analysis
        latest_prop = (Property.query
                       .filter_by(user_id=current_user.id)
                       .filter(Property.analyzed_at.isnot(None))
                       .order_by(Property.analyzed_at.desc())
                       .first())
        if latest_prop and latest_prop.address:
            import re as _re
            zip_match = _re.search(r'(\d{5})', latest_prop.address)
            if zip_match:
                zip_code = zip_match.group(1)

    if not zip_code:
        # Fallback: check waitlist record (user did a Risk Check before signing up)
        wl = (Waitlist.query
              .filter_by(email=current_user.email)
              .filter(Waitlist.result_zip.isnot(None))
              .order_by(Waitlist.created_at.desc())
              .first())
        if wl and wl.result_zip:
            zip_code = wl.result_zip
            logging.info('Nearby listings: using ZIP %s from waitlist record for %s',
                         zip_code, current_user.email)

    if not zip_code:
        return jsonify({
            'listings': [],
            'message': 'No ZIP code available. Run a Risk Check or analyze a property to see nearby listings.'
        })

    buyer_context = {
        'repair_tolerance': current_user.repair_tolerance,
        'max_budget': current_user.max_budget,
        'biggest_regret': current_user.biggest_regret,
    }

    result = get_nearby_listings(
        zip_code=zip_code,
        limit=int(request.args.get('limit', 5)),
        buyer_context=buyer_context,
    )

    if result.get('error'):
        logging.warning('Nearby listings error: %s', result['error'])
        return jsonify(result)

    logging.info('Nearby listings: ZIP %s -> %d listings (%d API calls, cached=%s)',
                 zip_code, len(result['listings']), result['api_calls'], result['cached'])
    
    # Apply preference learning if user has history
    try:
        from nearby_listings import extract_preferences, apply_preference_boost
        prefs_records = ListingPreference.query.filter_by(
            user_id=current_user.id
        ).order_by(ListingPreference.created_at.desc()).limit(100).all()
        
        if prefs_records:
            prefs = extract_preferences(prefs_records)
            if prefs:
                for listing in result['listings']:
                    listing['score'] = apply_preference_boost(listing, prefs)
                # Re-sort by adjusted score
                result['listings'].sort(key=lambda x: x.get('score', 0), reverse=True)
                result['preferences_applied'] = True
                result['preference_summary'] = {
                    'saved_count': prefs.get('saved_count', 0),
                    'dismissed_count': prefs.get('dismissed_count', 0),
                }
    except Exception as e:
        logging.warning('Preference learning error: %s', e)
    
    return jsonify(result)


@app.route('/api/nearby-listings/save', methods=['POST'])
@login_required
def api_save_listing():
    """Save a listing — signals interest for preference learning."""
    return _record_listing_preference('save')


@app.route('/api/nearby-listings/dismiss', methods=['POST'])
@login_required
def api_dismiss_listing():
    """Dismiss a listing — signals disinterest for preference learning."""
    return _record_listing_preference('dismiss')


def _record_listing_preference(action):
    """Record a save or dismiss action on a listing."""
    from nearby_listings import listing_hash
    data = request.get_json() or {}
    address = data.get('address', '').strip()
    if not address:
        return jsonify({'error': 'Address is required.'}), 400
    
    lhash = listing_hash(address)
    
    # Upsert: update action if already exists
    existing = ListingPreference.query.filter_by(
        user_id=current_user.id, listing_hash=lhash
    ).first()
    
    if existing:
        existing.action = action
    else:
        pref = ListingPreference(
            user_id=current_user.id,
            listing_hash=lhash,
            action=action,
            zip_code=data.get('zip_code'),
            price=data.get('price'),
            bedrooms=data.get('bedrooms'),
            bathrooms=data.get('bathrooms'),
            sqft=data.get('sqft'),
            year_built=data.get('year_built'),
            days_on_market=data.get('days_on_market'),
            risk_tier=data.get('risk_tier'),
            opportunity_score=data.get('score'),
        )
        db.session.add(pref)
    
    db.session.commit()
    
    total_saved = ListingPreference.query.filter_by(
        user_id=current_user.id, action='save').count()
    
    return jsonify({
        'success': True,
        'action': action,
        'address': address,
        'total_saved': total_saved,
    })


@app.route('/api/nearby-listings/saved', methods=['GET'])
@login_required
def api_saved_listings():
    """Get user's saved listings."""
    saved = ListingPreference.query.filter_by(
        user_id=current_user.id, action='save'
    ).order_by(ListingPreference.created_at.desc()).limit(50).all()
    
    return jsonify({
        'saved': [{
            'listing_hash': s.listing_hash,
            'zip_code': s.zip_code,
            'price': s.price,
            'bedrooms': s.bedrooms,
            'bathrooms': s.bathrooms,
            'sqft': s.sqft,
            'year_built': s.year_built,
            'risk_tier': s.risk_tier,
            'saved_at': s.created_at.isoformat() if s.created_at else None,
        } for s in saved],
        'total': len(saved),
    })


# ============================================================================
# 📊 Market Intelligence API (v5.62.92)
# ============================================================================

@app.route('/api/market-pulse', methods=['GET'])
@login_required
def api_market_pulse():
    """Get latest market snapshot for dashboard Market Pulse bar.
    
    Returns current market stats with week-over-week deltas,
    top match info, and comp updates for analysed properties.
    """
    from market_intelligence import get_latest_snapshot, get_comp_updates

    snapshot = get_latest_snapshot(db.session, current_user.id)
    if not snapshot:
        return jsonify({
            'available': False,
            'message': 'Market intelligence is building. Check back tomorrow.'
        })

    # Comp updates for analysis cards
    comps = get_comp_updates(db.session, current_user.id)

    return jsonify({
        'available': True,
        'zip_code': snapshot.zip_code,
        'snapshot_date': snapshot.snapshot_date.isoformat(),
        'median_price': snapshot.median_price,
        'median_price_delta_pct': snapshot.median_price_delta_pct,
        'active_inventory': snapshot.active_inventory,
        'inventory_delta': snapshot.inventory_delta,
        'avg_dom': snapshot.avg_dom,
        'avg_dom_delta': snapshot.avg_dom_delta,
        'new_listings_count': snapshot.new_listings_count,
        'top_match_score': snapshot.top_match_score,
        'top_match_address': snapshot.top_match_address,
        'comp_updates': {
            addr: {
                'count': len(data['comps']),
                'position': data['position'],
                'below_count': data['below_count'],
                'above_count': data['above_count'],
                'comps': data['comps'][:3],
            }
            for addr, data in comps.items()
        },
    })


@app.route('/api/admin/market-intel-stats', methods=['GET'])
@api_admin_required
def api_market_intel_stats():
    """Get market intelligence stats for admin dashboard."""
    from datetime import date
    today = date.today()
    snapshots_today = MarketSnapshot.query.filter_by(snapshot_date=today).count()
    alerts_today = db.session.query(db.func.sum(MarketSnapshot.alerts_generated)).filter_by(snapshot_date=today).scalar() or 0
    users_with_prefs = db.session.query(ListingPreference.user_id).filter_by(action='save').distinct().count()
    return jsonify({
        'snapshots_created': snapshots_today,
        'alerts_generated': alerts_today,
        'users_processed': users_with_prefs,
    })


@app.route('/api/admin/run-market-intel', methods=['POST'])
@api_admin_required
def api_run_market_intel():
    """Manually trigger market intelligence run (admin only)."""
    from market_intelligence import run_nightly_intelligence
    stats = run_nightly_intelligence(db.session)
    return jsonify(stats)


@app.route('/api/admin/google-ads-sync', methods=['POST'])
@api_admin_required
def api_google_ads_sync():
    """Manually trigger Google Ads data sync (admin only)."""
    from google_ads_sync import is_configured, sync_to_db, backfill
    if not is_configured():
        return jsonify({'error': 'Google Ads API not configured. Set GOOGLE_ADS_* env vars.'}), 400
    days = request.args.get('days', type=int)
    if days and days > 0:
        results = backfill(db.session, days=min(days, 90))
        return jsonify({'status': 'backfill_complete', 'days': len(results), 'results': results})
    result = sync_to_db(db.session)
    return jsonify(result)


@app.route('/api/admin/google-ads-status', methods=['GET'])
@api_admin_required
def api_google_ads_status():
    """Check Google Ads integration status with diagnostics."""
    from google_ads_sync import is_configured, CUSTOMER_ID, DEVELOPER_TOKEN, CLIENT_ID, REFRESH_TOKEN
    configured = is_configured()
    
    # Diagnostic: which vars are set?
    diag = {
        'developer_token': bool(DEVELOPER_TOKEN),
        'client_id': bool(CLIENT_ID),
        'refresh_token': bool(REFRESH_TOKEN),
        'customer_id': bool(CUSTOMER_ID),
    }
    
    # Check last sync
    from models import GTMAdPerformance
    last_entry = GTMAdPerformance.query.filter_by(channel='google_ads')\
        .order_by(GTMAdPerformance.date.desc()).first()
    
    total_rows = GTMAdPerformance.query.filter_by(channel='google_ads').count()
    
    return jsonify({
        'configured': configured,
        'env_vars': diag,
        'customer_id': CUSTOMER_ID[:4] + '****' + CUSTOMER_ID[-2:] if configured and len(CUSTOMER_ID) >= 6 else None,
        'last_sync_date': last_entry.date.isoformat() if last_entry else None,
        'last_sync_impressions': last_entry.impressions if last_entry else None,
        'last_sync_spend': float(last_entry.spend) if last_entry else None,
        'total_rows': total_rows,
    })


# ============================================================================
# 🤖 Property Research Agent - Free property research endpoint
# ============================================================================

@app.route('/api/research', methods=['POST'])
@limiter.limit("30 per hour")  # Free but rate-limited
def research_property():
    """
    🤖 Property Research Agent - Autonomous property research.
    
    No login required — this is the free "aha moment" that demonstrates
    the agent's value before the user pays for full analysis.
    """
    try:
        data = request.get_json()
        address = data.get('address', '').strip()
        
        if not address or len(address) < 10:
            return jsonify({'error': 'Please provide a complete property address'}), 400
        
        if len(address) > 300:
            return jsonify({'error': 'Address too long'}), 400
        
        logging.info(f"🤖 Agent research request for: {address[:80]}")
        
        # Initialize agent (AI synthesis uses Anthropic if available)
        ai_client = None
        try:
            import anthropic
            api_key = os.environ.get('ANTHROPIC_API_KEY')
            if api_key:
                ai_client = anthropic.Anthropic(api_key=api_key)
        except Exception:
            pass  # Agent works without AI synthesis
        
        agent = PropertyResearchAgent(ai_client=ai_client)
        result = agent.research(address)
        
        logging.info(f"🤖 Agent research complete: {result.get('tools_succeeded', 0)}/{result.get('tools_succeeded', 0) + result.get('tools_failed', 0)} tools succeeded in {result.get('research_time_ms', 0)}ms")
        
        return jsonify(result)
    
    except Exception as e:
        logging.error(f"🤖 Agent research error: {e}", exc_info=True)
        return jsonify({'error': 'Research failed. Please try again.'}), 500


@app.route('/api/research/cross-check', methods=['POST'])
@api_login_required
@validate_origin
def research_cross_check():
    """
    🤖 Cross-check agent research against uploaded documents.
    Called during analysis to enrich results with external verification.
    """
    try:
        data = request.get_json()
        profile_data = data.get('research_profile', {})
        disclosure_text = data.get('disclosure_text', '')
        inspection_text = data.get('inspection_text', '')
        
        if not profile_data or not disclosure_text:
            return jsonify({'cross_checks': [], 'message': 'Insufficient data for cross-check'}), 200
        
        # Reconstruct PropertyProfile from dict
        from property_research_agent import PropertyProfile
        profile = PropertyProfile(address=profile_data.get('address', ''))
        
        # Populate profile from research data
        for field in ['year_built', 'flood_zone', 'flood_risk_level', 'earthquake_zone',
                      'fire_hazard_zone', 'estimated_value', 'last_sale_price', 'last_sale_date',
                      'latitude', 'longitude', 'county', 'city', 'state', 'zip_code',
                      'bedrooms', 'bathrooms', 'sqft', 'lot_size_sqft', 'property_type',
                      'tax_assessed_value', 'annual_tax']:
            if field in profile_data and profile_data[field] is not None:
                setattr(profile, field, profile_data[field])
        
        # Restore permits list
        profile.permits = profile_data.get('permits', [])
        
        agent = PropertyResearchAgent()
        cross_checks = agent.cross_check_against_documents(
            profile=profile,
            disclosure_text=disclosure_text,
            inspection_text=inspection_text
        )
        
        logging.info(f"🤖 Cross-check found {len(cross_checks)} findings")
        
        return jsonify({'cross_checks': cross_checks})
    
    except Exception as e:
        logging.error(f"🤖 Cross-check error: {e}", exc_info=True)
        return jsonify({'cross_checks': [], 'error': 'Cross-check analysis failed'}), 200

# ============================================================================

@app.route('/api/properties/<int:property_id>/analysis')
@login_required
def get_property_analysis(property_id):
    """Retrieve analysis for a specific property"""
    # Get property and verify ownership
    property = Property.query.filter_by(id=property_id, user_id=current_user.id).first()
    
    if not property:
        return jsonify({'error': 'Property not found'}), 404
    
    # Get most recent analysis
    analysis = Analysis.query.filter_by(property_id=property_id).order_by(Analysis.created_at.desc()).first()
    
    if not analysis:
        return jsonify({'error': 'No analysis found for this property'}), 404
    
    # CRITICAL: Check if property has valid price (Bug #28 - old properties with $0)
    if not property.price or property.price <= 0:
        logging.warning(f"⚠️ Property {property_id} has invalid price: ${property.price}")
        return jsonify({
            'error': 'Missing Property Price',
            'message': 'Your analysis is saved, but the asking price is missing or invalid ($0). Please update the asking price to view this analysis.',
            'property_id': property_id,
            'property_address': property.address,
            'asking_price': property.price or 0,
            'action_required': 'update_price',
            'help_text': 'Click "Update Price" to enter the correct asking price for this property.'
        }), 400
    
    # Parse and return the saved analysis
    import json
    result_json = json.loads(analysis.result_json)
    
    # Add property metadata
    result_json['property_id'] = property.id
    result_json['property_address'] = property.address
    result_json['property_price'] = property.price
    result_json['analyzed_at'] = property.analyzed_at.isoformat() if property.analyzed_at else None
    
    # Generate repair_estimate on-the-fly if missing (old analyses pre v5.72.4)
    if 'repair_estimate' not in result_json or not result_json.get('repair_estimate', {}).get('breakdown'):
        try:
            from repair_cost_estimator import estimate_repair_costs
            import re as _re
            addr = result_json.get('property_address', '')
            zip_m = _re.search(r'\b(\d{5})\b', addr)
            risk_score_data = result_json.get('risk_score', {})
            
            # Try multiple sources for findings/category data
            findings = result_json.get('findings', [])
            category_scores = risk_score_data.get('category_scores', [])
            
            # If category_scores is empty, try to build from risk_score sub-fields
            if not category_scores and not findings:
                # Some analyses store categories differently
                for key in ['categories', 'system_scores', 'risk_categories']:
                    if risk_score_data.get(key):
                        category_scores = risk_score_data[key]
                        break
                # Try building from deal_breakers + critical_issues as findings
                if not category_scores:
                    deal_breakers = risk_score_data.get('deal_breakers', [])
                    critical_issues = result_json.get('critical_issues', [])
                    for db_item in deal_breakers:
                        if isinstance(db_item, dict):
                            findings.append({
                                'category': db_item.get('category', db_item.get('system', 'general')),
                                'severity': 'critical',
                                'description': db_item.get('explanation', db_item.get('description', db_item.get('title', ''))),
                            })
                    for ci in critical_issues:
                        if isinstance(ci, dict):
                            findings.append({
                                'category': ci.get('category', ci.get('system', 'general')),
                                'severity': ci.get('severity', 'major'),
                                'description': ci.get('description', ci.get('finding', '')),
                            })
            
            logging.info(f"💰 On-the-fly repair estimate: ZIP={zip_m.group(1) if zip_m else '?'}, findings={len(findings)}, category_scores={len(category_scores)}, total_low={risk_score_data.get('total_repair_cost_low', 0)}, total_high={risk_score_data.get('total_repair_cost_high', 0)}")
            
            repair_est = estimate_repair_costs(
                zip_code=zip_m.group(1) if zip_m else '',
                findings=findings,
                category_scores=category_scores,
                total_repair_low=risk_score_data.get('total_repair_cost_low', 0),
                total_repair_high=risk_score_data.get('total_repair_cost_high', 0),
                property_year_built=result_json.get('year_built'),
            )
            if repair_est.get('breakdown'):
                result_json['repair_estimate'] = repair_est
                logging.info(f"💰 Generated {len(repair_est['breakdown'])} repair line items on-the-fly")
            else:
                logging.warning(f"💰 On-the-fly estimate produced empty breakdown")
        except Exception as e:
            logging.warning(f"Could not generate repair estimate on load: {e}")
    
    logging.info(f"✅ Retrieved analysis for property {property_id} with price ${property.price:,}")
    
    return jsonify(result_json)

@app.route('/api/dashboard/init', methods=['GET'])
@login_required
def dashboard_init():
    """
    Combined endpoint for dashboard initialization.
    Returns all data needed to render dashboard in ONE call.
    
    Performance: 6 sequential calls → 1 call
    Load time: 1.2-3s → 200-500ms
    
    Returns:
        {
            "user": {...},
            "analyses": [...],
            "credits": {...},
            "consent_status": {...},
            "preferences": {...},
            "onboarding_complete": bool
        }
    """
    try:
        # Get user data
        user_data = {
            'id': current_user.id,
            'email': current_user.email,
            'name': current_user.name,
            'auth_provider': current_user.auth_provider,
            'tier': current_user.tier,
            'created_at': current_user.created_at.isoformat() if current_user.created_at else None
        }
        
        # Get analyses
        properties = Property.query.filter_by(user_id=current_user.id).all()
        analyses = []
        for property in properties:
            analysis = Analysis.query.filter_by(property_id=property.id).order_by(Analysis.created_at.desc()).first()
            if analysis and property.analyzed_at:
                try:
                    result_json = json.loads(analysis.result_json)
                    # CRITICAL FIX v5.49.0: Extract recommended_offer from offer_strategy (where it's stored)
                    # Previously looked at top level which returned property.price (no savings!)
                    offer_strategy = result_json.get('offer_strategy', {})
                    recommended_offer = offer_strategy.get('recommended_offer', property.price)
                    analyses.append({
                        'id': analysis.id,  # FIXED: Use actual DB ID, not timestamp
                        'timestamp_id': str(int(property.analyzed_at.timestamp() * 1000)),  # Keep for backward compat
                        'property_id': property.id,
                        'property_address': property.address or 'Property Analysis',
                        'asking_price': property.price or 0,
                        'recommended_offer': recommended_offer,
                        'risk_score': result_json.get('risk_score', {}),
                        'analyzed_at': property.analyzed_at.isoformat(),
                        'full_result': result_json
                    })
                except Exception:
                    pass
        analyses.sort(key=lambda x: x['analyzed_at'], reverse=True)
        
        # Get usage/credits
        usage = current_user.get_current_usage()
        limits = current_user.get_tier_limits()
        
        # Get consent status
        consent_types = ['analysis_disclaimer', 'terms', 'privacy']
        consents = []
        for consent_type in consent_types:
            required_version = get_disclaimer_version(consent_type)
            if required_version:
                has_consent = ConsentRecord.has_current_consent(
                    user_id=current_user.id,
                    consent_type=consent_type,
                    required_version=required_version
                )
                consents.append({
                    'consent_type': consent_type,
                    'has_consent': has_consent,
                    'required_version': required_version
                })
        
        # Get preferences
        preferences = {
            'max_budget': current_user.max_budget,
            'repair_tolerance': current_user.repair_tolerance,
            'biggest_regret': current_user.biggest_regret
        }
        
        # Return combined data
        return jsonify({
            'user': user_data,
            'analyses': analyses,
            'credits': {
                'used': usage['analyses'],
                'total': limits['analyses'],
                'remaining': limits['analyses'] - usage['analyses']
            },
            'consent_status': {
                'consents': consents,
                'all_accepted': all(c['has_consent'] for c in consents)
            },
            'preferences': preferences,
            'onboarding_complete': current_user.onboarding_completed or False
        })
        
    except Exception as e:
        logging.error(f"❌ Error in dashboard init: {e}")
        return jsonify({
            'error': 'Failed to initialize dashboard',
            'message': 'An internal error occurred. Please try again.'
        }), 500

@app.route('/api/properties/<int:property_id>')
@login_required
def get_property(property_id):
    """Get property details"""
    property = Property.query.filter_by(id=property_id, user_id=current_user.id).first_or_404()
    
    # Get latest analysis
    latest_analysis = property.analyses.order_by(Analysis.created_at.desc()).first()
    
    return jsonify({
        'property': {
            'id': property.id,
            'address': property.address,
            'price': property.price,
            'status': property.status,
            'analyzed_at': property.analyzed_at.isoformat() if property.analyzed_at else None,
            'documents': [{
                'id': d.id,
                'type': d.document_type,
                'filename': d.filename,
                'size_bytes': d.file_size_bytes,
                'uploaded_at': d.uploaded_at.isoformat()
            } for d in property.documents],
            'analysis': json.loads(latest_analysis.result_json) if latest_analysis else None
        }
    })

@app.route('/api/properties/<int:property_id>/price', methods=['PUT'])
@login_required
def update_property_price(property_id):
    """
    Update the asking price for a property
    
    Useful for fixing properties that were saved with $0 price.
    Users can update the price without re-uploading documents.
    """
    property = Property.query.filter_by(id=property_id, user_id=current_user.id).first()
    
    if not property:
        return jsonify({'error': 'Property not found'}), 404
    
    data = request.get_json()
    new_price = data.get('price')
    
    # Validate price
    try:
        new_price = int(float(new_price))
        if new_price <= 0 or new_price > 100000000:
            return jsonify({'error': 'Price must be between $1 and $100M'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid price format. Please enter numbers only.'}), 400
    
    # Update price
    old_price = property.price or 0
    property.price = new_price
    db.session.commit()
    
    logging.info(f"✅ User {current_user.id} updated property {property_id} price: ${old_price} → ${new_price:,}")
    
    return jsonify({
        'success': True,
        'message': 'Price updated successfully! You can now view your analysis.',
        'property_id': property_id,
        'property_address': property.address,
        'old_price': old_price,
        'new_price': new_price
    })

@app.route('/api/user/analyses', methods=['GET'])
@login_required
def get_user_analyses():
    """
    Get all analyses for the current user.
    
    Returns analyses in the format expected by dashboard.html and settings.html:
    {
        "analyses": [
            {
                "id": "timestamp",
                "property_id": 123,
                "property_address": "123 Main St",
                "asking_price": 500000,
                "recommended_offer": 475000,
                "risk_score": {...},
                "analyzed_at": "2026-01-20T10:00:00",
                "full_result": {...}
            }
        ]
    }
    """
    try:
        # Get all properties for current user that have been analyzed
        properties = Property.query.filter_by(user_id=current_user.id).all()
        
        analyses = []
        for property in properties:
            # Get most recent analysis for this property
            analysis = Analysis.query.filter_by(property_id=property.id).order_by(Analysis.created_at.desc()).first()
            
            if analysis and property.analyzed_at:
                # Parse the analysis result
                import json
                try:
                    result_json = json.loads(analysis.result_json)
                    
                    # Extract offer_strategy data (it's nested)
                    offer_strategy = result_json.get('offer_strategy', {})
                    recommended_offer = offer_strategy.get('recommended_offer', property.price)
                    
                    # CRITICAL FIX: Use Risk DNA composite for consistent risk display
                    # Risk DNA focuses on property condition (structural, systems, transparency)
                    risk_dna_data = result_json.get('risk_dna', {})
                    
                    # Try to get Risk DNA composite first (preferred)
                    if risk_dna_data and 'composite_score' in risk_dna_data:
                        risk_score = risk_dna_data.get('composite_score', 0)
                        # Use Risk DNA's own category if available
                        risk_level = risk_dna_data.get('risk_category', '').upper()
                        
                        # If category not provided, calculate using CONSISTENT thresholds
                        if not risk_level:
                            if risk_score >= 90:
                                risk_level = 'CRITICAL'
                            elif risk_score >= 75:
                                risk_level = 'HIGH'
                            elif risk_score >= 60:
                                risk_level = 'ELEVATED'
                            elif risk_score >= 40:
                                risk_level = 'MODERATE'
                            elif risk_score >= 20:
                                risk_level = 'LOW'
                            else:
                                risk_level = 'MINIMAL'
                    else:
                        # Fallback to overall_risk if Risk DNA not available (older analyses)
                        risk_score_data = result_json.get('risk_score', {})
                        risk_score = risk_score_data.get('overall_risk', 0)
                        
                        # Use CONSISTENT thresholds (matching Risk DNA)
                        if risk_score >= 90:
                            risk_level = 'CRITICAL'
                        elif risk_score >= 75:
                            risk_level = 'HIGH'
                        elif risk_score >= 60:
                            risk_level = 'ELEVATED'
                        elif risk_score >= 40:
                            risk_level = 'MODERATE'
                        elif risk_score >= 20:
                            risk_level = 'LOW'
                        else:
                            risk_level = 'MINIMAL'
                    
                    # CRITICAL: Inject property_id into result_json so it's available
                    # when the dashboard stores full_result in sessionStorage
                    result_json['property_id'] = property.id
                    result_json['property_address'] = property.address or 'Property Analysis'
                    result_json['property_price'] = property.price or 0
                    
                    # Create analysis object in format frontend expects
                    # CRITICAL FIX v5.49.0: Use actual database analysis.id for delete operations
                    # Previously used timestamp which caused 404s on delete (ID mismatch)
                    analysis_obj = {
                        'id': analysis.id,  # FIXED: Use actual DB ID, not timestamp
                        'timestamp_id': str(int(property.analyzed_at.timestamp() * 1000)),  # Keep for backward compat
                        'property_id': property.id,
                        'property_address': property.address or 'Property Analysis',
                        'asking_price': property.price or 0,
                        'recommended_offer': recommended_offer,
                        'risk_score': risk_score,
                        'risk_level': risk_level,
                        'analyzed_at': property.analyzed_at.isoformat(),
                        'full_result': result_json
                    }
                    
                    analyses.append(analysis_obj)
                    
                except json.JSONDecodeError as e:
                    logging.error(f"❌ Failed to parse analysis for property {property.id}: {e}")
                    continue
        
        # Sort by analyzed_at (newest first)
        analyses.sort(key=lambda x: x['analyzed_at'], reverse=True)
        
        logging.info(f"✅ Returned {len(analyses)} analyses for user {current_user.id}")
        
        return jsonify({
            'analyses': analyses,
            'count': len(analyses)
        })
        
    except Exception as e:
        logging.error(f"❌ Error fetching user analyses: {e}")
        return jsonify({
            'error': 'Failed to fetch analyses',
            'message': 'An internal error occurred. Please try again.',
            'analyses': []  # Return empty array so frontend can fallback to localStorage
        }), 500

@app.route('/api/user/analyses', methods=['POST'])
@login_required
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

@app.route('/api/properties/<int:property_id>', methods=['DELETE'])
@login_required
def delete_property(property_id):
    """Delete a property and all associated data"""
    property = Property.query.filter_by(id=property_id, user_id=current_user.id).first_or_404()
    
    # Delete files
    property_folder = os.path.join(app.config['UPLOAD_FOLDER'], str(current_user.id), str(property.id))
    if os.path.exists(property_folder):
        import shutil
        shutil.rmtree(property_folder)
    
    # CASCADE delete will handle analyses and documents
    db.session.delete(property)
    db.session.commit()
    
    logging.info(f"✅ Deleted property {property.id} ({property.address}) for user {current_user.email}")
    
    return jsonify({'success': True, 'message': 'Property deleted'})


# =============================================================================
# SHARE / "GET A SECOND OPINION" ROUTES (v5.58.0)
# =============================================================================


# ============================================================================
# 🆘 Support Share — user shares analysis with OfferWise team
# ============================================================================


@app.route('/api/admin/support-shares', methods=['GET'])
@api_admin_required
def admin_list_support_shares():
    """List all support shares. ?status=open|reviewed|resolved&limit=50"""
    from models import SupportShare
    status_filter = request.args.get('status', 'open')
    limit = min(int(request.args.get('limit', 50)), 200)

    query = SupportShare.query
    if status_filter and status_filter != 'all':
        query = query.filter_by(status=status_filter)
    shares = query.order_by(SupportShare.created_at.desc()).limit(limit).all()

    result = []
    for s in shares:
        user = User.query.get(s.user_id)
        snapshot = json.loads(s.snapshot_json or '{}')
        result.append({
            'id': s.id,
            'status': s.status,
            'created_at': s.created_at.isoformat(),
            'reviewed_at': s.reviewed_at.isoformat() if s.reviewed_at else None,
            'user_email': user.email if user else 'unknown',
            'user_name': user.name if user else None,
            'user_tier': user.tier if user else None,
            'property_id': s.property_id,
            'user_message': s.user_message,
            'snapshot': snapshot,
            'admin_notes': s.admin_notes,
        })

    return jsonify({'shares': result, 'total': len(result)})


@app.route('/api/admin/support-shares/<int:share_id>', methods=['GET'])
@api_admin_required
def admin_get_support_share(share_id):
    """Get full detail for a single support share including raw analysis JSON."""
    from models import SupportShare
    share = SupportShare.query.get(share_id)
    if not share:
        return jsonify({'error': 'Not found'}), 404

    user = User.query.get(share.user_id)
    snapshot = json.loads(share.snapshot_json or '{}')
    full_result = json.loads(share.full_result_json or '{}')
    findings = json.loads(share.findings_json or '[]')

    return jsonify({
        'id': share.id,
        'status': share.status,
        'created_at': share.created_at.isoformat(),
        'reviewed_at': share.reviewed_at.isoformat() if share.reviewed_at else None,
        'user': {
            'id': share.user_id,
            'email': user.email if user else 'unknown',
            'name': user.name if user else None,
            'tier': user.tier if user else None,
            'created_at': user.created_at.isoformat() if user else None,
        },
        'property_id': share.property_id,
        'user_message': share.user_message,
        'admin_notes': share.admin_notes,
        'snapshot': snapshot,
        'findings': findings,
        'full_result': full_result,
    })


@app.route('/api/admin/support-shares/<int:share_id>', methods=['PATCH'])
@api_admin_required
def admin_update_support_share(share_id):
    """Update status or admin notes on a support share."""
    from models import SupportShare
    share = SupportShare.query.get(share_id)
    if not share:
        return jsonify({'error': 'Not found'}), 404

    data = request.get_json()
    if 'status' in data and data['status'] in ('open', 'reviewed', 'resolved'):
        share.status = data['status']
        if data['status'] in ('reviewed', 'resolved') and not share.reviewed_at:
            share.reviewed_at = datetime.utcnow()
    if 'admin_notes' in data:
        share.admin_notes = (data['admin_notes'] or '').strip()[:2000] or None

    db.session.commit()
    return jsonify({'success': True, 'status': share.status})


@app.route('/api/debug/delete-all-my-data', methods=['POST'])
@dev_only_gate
@login_required
@limiter.limit("3 per day")  # SECURITY: Prevent abuse
def debug_delete_all_my_data():
    """
    NUCLEAR OPTION - Delete ALL properties and analyses for current user
    
    This is the "I just want it gone" button.
    PRODUCTION: Rate limited to 3 per day per user.
    """
    try:
        logging.info(f"🔥 NUCLEAR DELETE requested by {current_user.email}")
        
        properties = Property.query.filter_by(user_id=current_user.id).all()
        
        deleted_count = 0
        for prop in properties:
            logging.info(f"   Deleting property {prop.id}: {prop.address}")
            db.session.delete(prop)
            deleted_count += 1
        
        db.session.commit()
        
        logging.info(f"   ✅ Deleted {deleted_count} properties (cascade deleted analyses)")
        
        return jsonify({
            'success': True,
            'message': f'Deleted {deleted_count} properties',
            'deleted_count': deleted_count
        }), 200
        
    except Exception as e:
        logging.error(f"   ❌ Nuclear delete failed: {e}")
        db.session.rollback()
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@app.route('/api/debug/my-data', methods=['GET'])
@dev_only_gate
@login_required
def debug_my_data():
    """
    DEBUG ENDPOINT - See exactly what's in the database for current user
    """
    try:
        properties = Property.query.filter_by(user_id=current_user.id).all()
        
        result = {
            'user_id': current_user.id,
            'user_email': current_user.email,
            'properties_count': len(properties),
            'properties': []
        }
        
        for prop in properties:
            analyses = Analysis.query.filter_by(property_id=prop.id).all()
            
            result['properties'].append({
                'property_id': prop.id,
                'address': prop.address,
                'price': prop.price,
                'analyzed_at': prop.analyzed_at.isoformat() if prop.analyzed_at else None,
                'timestamp_id': str(int(prop.analyzed_at.timestamp() * 1000)) if prop.analyzed_at else None,
                'analyses_count': len(analyses),
                'analyses': [
                    {
                        'analysis_id': a.id,
                        'created_at': a.created_at.isoformat()
                    }
                    for a in analyses
                ]
            })
        
        return jsonify(result), 200
        
    except Exception as e:
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@app.route('/api/analyses/by-timestamp/<timestamp_id>', methods=['DELETE'])
@login_required
def delete_analysis_by_timestamp(timestamp_id):
    """
    Delete analysis by timestamp ID (the format frontend uses).
    
    This is the SIMPLE, FOOLPROOF endpoint that just works.
    Frontend sends timestamp like "1769399740604",
    we find the matching property and delete it.
    """
    logging.info(f"=" * 80)
    logging.info(f"🗑️  DELETE BY TIMESTAMP - START")
    logging.info(f"=" * 80)
    logging.info(f"   Timestamp ID: {timestamp_id}")
    logging.info(f"   User: {current_user.email} (ID: {current_user.id})")
    
    try:
        # Convert timestamp string to datetime
        from datetime import datetime
        timestamp_ms = int(timestamp_id)
        timestamp_dt = datetime.fromtimestamp(timestamp_ms / 1000.0)
        
        logging.info(f"   Looking for property analyzed at: {timestamp_dt}")
        
        # Find property by user_id and analyzed_at timestamp
        # Allow 1-second tolerance for rounding
        from datetime import timedelta
        start_time = timestamp_dt - timedelta(seconds=1)
        end_time = timestamp_dt + timedelta(seconds=1)
        
        property = Property.query.filter(
            Property.user_id == current_user.id,
            Property.analyzed_at >= start_time,
            Property.analyzed_at <= end_time
        ).first()
        
        if not property:
            logging.warning(f"   ⚠️  No property found with analyzed_at near {timestamp_dt}")
            logging.info(f"   Checking all user properties...")
            
            # Fallback: List all properties and find closest match
            all_properties = Property.query.filter_by(user_id=current_user.id).all()
            logging.info(f"   User has {len(all_properties)} total properties")
            
            for prop in all_properties:
                if prop.analyzed_at:
                    prop_timestamp = int(prop.analyzed_at.timestamp() * 1000)
                    logging.info(f"     Property {prop.id}: {prop.address} - timestamp {prop_timestamp}")
                    
                    # If timestamps match (within 1 second = 1000ms)
                    if abs(prop_timestamp - timestamp_ms) < 1000:
                        property = prop
                        logging.info(f"   ✅ Found matching property: {property.id}")
                        break
        
        if not property:
            logging.error(f"   ❌ Could not find property for timestamp {timestamp_id}")
            logging.info(f"=" * 80)
            # Return success anyway (idempotent)
            return jsonify({
                'success': True,
                'message': f'Analysis not found (may be already deleted)',
                'already_deleted': True
            }), 200
        
        logging.info(f"   ✅ Found property {property.id}: {property.address}")
        
        # Delete the property (cascade will delete analysis)
        property_id = property.id
        property_address = property.address
        
        logging.info(f"   Deleting property {property_id}...")
        db.session.delete(property)
        db.session.commit()
        
        logging.info(f"   ✅ DELETED property {property_id} ({property_address})")
        logging.info(f"=" * 80)
        
        return jsonify({
            'success': True,
            'message': f'Deleted property {property_address}',
            'property_id': property_id
        }), 200
        
    except Exception as e:
        logging.error(f"   ❌ Error deleting by timestamp: {e}")
        logging.error(f"   {type(e).__name__}: {str(e)}")
        logging.info(f"=" * 80)
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': 'An internal error occurred. Please try again.'
        }), 500


@app.route('/api/analyses/<int:analysis_id>', methods=['DELETE'])
@login_required
def delete_analysis(analysis_id):
    """Delete a specific analysis"""
    logging.info(f"=" * 80)
    logging.info(f"🗑️  DELETE ANALYSIS REQUEST - START")
    logging.info(f"=" * 80)
    logging.info(f"   Analysis ID: {analysis_id}")
    logging.info(f"   User: {current_user.email} (ID: {current_user.id})")
    logging.info(f"   Request method: {request.method}")
    logging.info(f"   Request path: {request.path}")
    
    try:
        # Step 1: Find the analysis
        logging.info(f"Step 1: Looking up analysis {analysis_id}...")
        analysis = Analysis.query.get(analysis_id)
        
        if not analysis:
            logging.info(f"   ℹ️  Analysis {analysis_id} already deleted or doesn't exist")
            logging.info(f"   Treating as successful delete (idempotent operation)")
            logging.info(f"=" * 80)
            # DELETE should be idempotent - deleting something already gone = success
            return jsonify({
                'success': True,
                'message': f'Analysis {analysis_id} already deleted',
                'already_deleted': True
            }), 200  # Return 200, not 404
        
        logging.info(f"   ✅ Found analysis {analysis_id}")
        logging.info(f"   Property ID: {analysis.property_id}")
        
        # Step 2: Verify ownership through property
        logging.info(f"Step 2: Verifying ownership...")
        if not analysis.property:
            logging.error(f"   ❌ Analysis {analysis_id} has NO associated property!")
            logging.info(f"=" * 80)
            return jsonify({
                'success': False,
                'error': 'Analysis has no associated property'
            }), 500
        
        logging.info(f"   Property exists: {analysis.property.address}")
        logging.info(f"   Property owner (user_id): {analysis.property.user_id}")
        logging.info(f"   Current user (user_id): {current_user.id}")
            
        if analysis.property.user_id != current_user.id:
            logging.warning(f"   ⚠️  UNAUTHORIZED: User {current_user.email} (ID: {current_user.id}) tried to delete analysis owned by user {analysis.property.user_id}")
            logging.info(f"=" * 80)
            return jsonify({
                'success': False,
                'error': 'Unauthorized - you do not own this analysis'
            }), 403
        
        logging.info(f"   ✅ Ownership verified")
        
        property_id = analysis.property_id
        property_address = analysis.property.address
        
        # Step 3: Handle foreign key constraints
        logging.info(f"Step 3: Clearing foreign key references...")
        
        # 3a. Clear consent records
        logging.info(f"   Checking consent records with analysis_id={analysis_id}...")
        consent_count = ConsentRecord.query.filter_by(analysis_id=analysis_id).count()
        logging.info(f"   Found {consent_count} consent record(s)")
        
        if consent_count > 0:
            ConsentRecord.query.filter_by(analysis_id=analysis_id).update({'analysis_id': None})
            logging.info(f"   Marked {consent_count} consent record(s) for update")
        else:
            logging.info(f"   No consent records to update")
        
        # 3b. Clear comparison records
        logging.info(f"   Checking comparisons with analysis_id={analysis_id}...")
        from sqlalchemy import or_
        
        comparisons_with_ref = Comparison.query.filter(
            or_(
                Comparison.property1_analysis_id == analysis_id,
                Comparison.property2_analysis_id == analysis_id,
                Comparison.property3_analysis_id == analysis_id
            )
        ).count()
        logging.info(f"   Found {comparisons_with_ref} comparison(s)")
        
        if comparisons_with_ref > 0:
            Comparison.query.filter(
                or_(
                    Comparison.property1_analysis_id == analysis_id,
                    Comparison.property2_analysis_id == analysis_id,
                    Comparison.property3_analysis_id == analysis_id
                )
            ).update({
                'property1_analysis_id': None,
                'property2_analysis_id': None,
                'property3_analysis_id': None
            }, synchronize_session=False)
            logging.info(f"   Marked {comparisons_with_ref} comparison(s) for update")
        else:
            logging.info(f"   No comparisons to update")
        
        # CRITICAL FIX: Commit foreign key cleanup BEFORE deleting analysis
        # This prevents SQLAlchemy constraint issues
        if consent_count > 0 or comparisons_with_ref > 0:
            logging.info(f"   Committing foreign key cleanup...")
            db.session.commit()
            logging.info(f"   ✅ Foreign key cleanup committed")
            
            # CRITICAL FIX #2: Expire all objects so SQLAlchemy forgets about them
            logging.info(f"   Expiring session to clear updated objects from tracking...")
            db.session.expire_all()
            logging.info(f"   ✅ Session cleared")
        
        # Step 4: Delete the analysis
        logging.info(f"Step 4: Deleting analysis {analysis_id}...")
        db.session.delete(analysis)
        
        # Step 5: Commit
        logging.info(f"Step 5: Committing final transaction...")
        db.session.commit()
        
        logging.info(f"   ✅ SUCCESS!")
        logging.info(f"   Deleted analysis {analysis_id} for property {property_id} ({property_address})")
        logging.info(f"   User: {current_user.email}")
        logging.info(f"=" * 80)
        
        return jsonify({
            'success': True, 
            'message': 'Analysis deleted',
            'property_id': property_id
        })
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"   ❌ EXCEPTION CAUGHT!")
        logging.error(f"   Exception type: {type(e).__name__}")
        logging.error(f"   Exception message: {str(e)}")
        logging.exception(e)  # Full stack trace
        logging.info(f"=" * 80)
        return jsonify({
            'success': False,
            'error': 'Failed to delete analysis. Please try again.'
        }), 500


# ============================================================================
# ANALYSIS API (WITH AUTHENTICATION)
# ============================================================================

@app.route('/api/ocr-progress', methods=['GET'])
@login_required  # SECURITY: Require authentication
@limiter.limit("100 per minute")  # SECURITY: Limit polling rate
def get_ocr_progress():
    """
    DEPRECATED: Old OCR progress endpoint
    
    This endpoint is no longer used with async uploads (v4.4+).
    For new uploads, use /api/jobs/{job_id} instead.
    
    Returning empty data to prevent 502 errors from old frontend code.
    """
    # Return empty/idle state to prevent errors
    # Old frontend code may still be polling this
    return jsonify({
        'current': 0,
        'total': 0,
        'status': 'idle',
        'message': 'Use /api/jobs/{job_id} for async uploads',
        'deprecated': True
    })

@app.route('/api/cancel-ocr', methods=['POST'])
@api_login_required
def cancel_ocr():
    """Cancel ongoing OCR processing to save costs when user leaves"""
    # SECURITY: Only cancel own jobs (requires authentication via @api_login_required)
    progress_key = f"user_{current_user.id}"
    
    # Set cancellation flag
    if progress_key in ocr_progress:
        ocr_progress[progress_key]['cancelled'] = True
        ocr_progress[progress_key]['status'] = 'cancelled'
        logger.info(f"🛑 OCR cancellation requested for key '{progress_key}' - will stop Google Vision calls")
    else:
        logger.info(f"⚠️ No active OCR found for key '{progress_key}'")
    
    return jsonify({'success': True, 'message': 'Cancellation signal sent'})

@app.route('/api/worker/stats', methods=['GET'])
@dev_only_gate
@admin_required
def get_worker_stats():
    """Get PDF worker statistics (for monitoring/debugging)"""
    try:
        stats = pdf_worker.get_stats()
        stats['jobs_by_status'] = job_manager.get_job_count()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting worker stats: {e}", exc_info=True)
        return jsonify({'error': 'Failed to get stats'}), 500

# Periodic cleanup of old jobs
import threading
import time

def cleanup_old_jobs_periodically():
    """Background thread to cleanup old jobs - runs frequently to save memory"""
    while True:
        try:
            time.sleep(1800)  # Wait 30 minutes (reduced from 1 hour)
            logger.info("🧹 Running periodic job cleanup...")
            # Clean up jobs older than 2 hours (reduced from 24 hours) to save memory
            job_manager.cleanup_old_jobs(hours=2)
            # Force garbage collection to free memory
            gc.collect()
            logger.info("🧹 Memory cleanup completed")
        except Exception as e:
            logger.error(f"Error in cleanup thread: {e}", exc_info=True)

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_old_jobs_periodically, daemon=True)
cleanup_thread.start()
logger.info("✅ Job cleanup thread started (runs every 30 minutes, cleans jobs >2 hours old)")

# Log startup memory usage for debugging
try:
    import psutil
    process = psutil.Process(os.getpid())
    memory_mb = round(process.memory_info().rss / 1024 / 1024, 2)
    logger.info(f"📊 Startup memory usage: {memory_mb} MB (Limit: 512 MB on free tier)")
    if memory_mb > 400:
        logger.warning(f"⚠️ HIGH startup memory! Using {memory_mb} MB - crashes likely!")
except Exception as e:
    logger.error(f"Could not measure startup memory: {e}")


@app.route('/api/debug/ai-status', methods=['GET'])
@dev_only_gate
@login_required  # SECURITY: Require authentication
def ai_status_debug():
    """
    Debug endpoint to check AI helper status - ADMIN ONLY
    """
    # SECURITY: Only admins can access this
    # You'll need to add is_admin field to User model
    # For now, restrict to specific user IDs or disable entirely
    import os
    import traceback
    
    # OPTION 1: Disable in production
    if os.environ.get('RENDER'):  # If running on Render (production)
        return jsonify({'error': 'Debug endpoint disabled in production'}), 404
    
    # OPTION 2: Admin only (uncomment when you add is_admin field)
    # if not getattr(current_user, 'is_admin', False):
    #     return jsonify({'error': 'Unauthorized'}), 403
    
    import os
    import traceback
    
    # SECURITY: Minimal information only
    status = {
        'timestamp': datetime.now().isoformat(),
        'ai_helper_enabled': False,
        'initialization_status': 'UNKNOWN'
    }
    
    # Try to initialize AI helper
    try:
        from analysis_ai_helper import AnalysisAIHelper
        helper = AnalysisAIHelper()
        
        status['ai_helper_enabled'] = helper.enabled
        status['initialization_status'] = 'SUCCESS' if helper.enabled else 'DISABLED'
        
        # SECURITY: NO sensitive info exposed!
        # NO api_key_prefix
        # NO environment_vars  
        # NO tracebacks
        # NO error details
        
    except Exception as e:
        status['initialization_status'] = 'ERROR'
        # SECURITY: Generic error only, no details
        status['message'] = 'Error initializing AI helper'
        # Log detailed error server-side only
        logging.error(f"AI helper init error: {e}", exc_info=True)
    
    return jsonify(status)

@app.route('/api/debug/memory', methods=['GET'])
@dev_only_gate
@login_required  # SECURITY: Require authentication
def debug_memory():
    """
    Get current memory usage for debugging crashes
    Visit: https://www.getofferwise.ai/api/debug/memory
    """
    try:
        import psutil
        
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        
        # Get detailed memory breakdown
        return jsonify({
            'timestamp': datetime.now().isoformat(),
            'memory': {
                'rss_mb': round(memory_info.rss / 1024 / 1024, 2),  # Resident Set Size (actual RAM used)
                'vms_mb': round(memory_info.vms / 1024 / 1024, 2),  # Virtual Memory Size
                'percent': round(process.memory_percent(), 2),
                'available_system_mb': round(psutil.virtual_memory().available / 1024 / 1024, 2),
                'total_system_mb': round(psutil.virtual_memory().total / 1024 / 1024, 2),
                'limit_mb': 512  # Render free tier limit
            },
            'workers': {
                'pdf_worker_threads': pdf_worker.executor._max_workers if pdf_worker else 0,
                'active_threads': threading.active_count(),
            },
            'jobs': {
                'total': job_manager.get_job_count(),
                'active': len([j for j in job_manager.jobs.values() if j.status in ['queued', 'processing']])
            },
            'system': {
                'cpu_percent': psutil.cpu_percent(interval=0.1),
                'cpu_count': psutil.cpu_count()
            },
            'warning': 'CRASH LIKELY!' if memory_info.rss / 1024 / 1024 > 450 else 'OK'
        })
    except Exception as e:
        logger.error(f"Memory debug error: {e}", exc_info=True)
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500

# ============================================================================
# PROPERTY COMPARISON
# ============================================================================

@app.route('/api/compare', methods=['POST'])
@login_required
def compare_properties():
    """
    Compare 2-3 properties side-by-side
    Cost: 1 credit for comparison of up to 3 properties
    """
    logger.info(f"🏆 COMPARE API called by user {current_user.id}")
    try:
        from comparison_service import comparison_service
        
        data = request.get_json()
        logger.info(f"📥 Compare request data: {data}")
        
        # Extract property URLs and optional prices
        property1_url = data.get('property1_url', '').strip()
        property2_url = data.get('property2_url', '').strip()
        property3_url = data.get('property3_url', '').strip() if data.get('property3_url') else None
        
        logger.info(f"🏠 URLs: prop1={property1_url[:50]}..., prop2={property2_url[:50]}..., prop3={property3_url[:50] if property3_url else 'None'}...")
        
        property1_price = data.get('property1_price')
        property2_price = data.get('property2_price')
        property3_price = data.get('property3_price')
        
        # Validate inputs
        if not property1_url or not property2_url:
            logger.warning(f"⚠️ Comparison validation failed: missing URLs")
            return jsonify({
                'success': False,
                'error': 'At least 2 properties are required for comparison'
            }), 400
        
        # Check credits
        if current_user.analysis_credits < 1:
            logger.warning(f"⚠️ User {current_user.id} has insufficient credits ({current_user.analysis_credits})")
            return jsonify({
                'success': False,
                'error': 'Insufficient credits. You need 1 credit to compare properties.',
                'credits_remaining': current_user.analysis_credits
            }), 402  # Payment Required
        
        logger.info(f"💳 User {current_user.id} has {current_user.analysis_credits} credits available")
        
        # Create comparison record
        comparison = Comparison(
            user_id=current_user.id,
            property1_listing_url=property1_url,
            property1_price=property1_price,
            property2_listing_url=property2_url,
            property2_price=property2_price,
            property3_listing_url=property3_url,
            property3_price=property3_price,
            status='processing',
            credits_used=1
        )
        db.session.add(comparison)
        db.session.flush()  # Get the comparison ID
        
        # Deduct credit — ATOMIC to prevent race conditions
        rows_updated = User.query.filter(
            User.id == current_user.id,
            User.analysis_credits > 0
        ).update(
            {User.analysis_credits: User.analysis_credits - 1},
            synchronize_session=False
        )
        
        if rows_updated == 0:
            db.session.rollback()
            return jsonify({'error': 'No analysis credits remaining'}), 402
        
        # AUTO-REFILL for developer accounts
        # Uses global DEVELOPER_EMAILS
        if current_user.email.lower() in DEVELOPER_EMAILS:
            User.query.filter(
                User.id == current_user.id,
                User.analysis_credits < 50
            ).update(
                {User.analysis_credits: 500},
                synchronize_session=False
            )
            logger.info(f"👑 DEVELOPER ACCOUNT: Auto-refilled credits to 500")
        
        logger.info(f"🏆 User {current_user.id} starting comparison {comparison.id} (credits remaining: {current_user.analysis_credits})")
        
        try:
            # Perform comparison
            result = comparison_service.compare_properties(
                property1_url=property1_url,
                property2_url=property2_url,
                property3_url=property3_url,
                property1_price=property1_price,
                property2_price=property2_price,
                property3_price=property3_price
            )
            
            # Update comparison record
            comparison.property1_address = result.property1.address
            comparison.property2_address = result.property2.address
            if result.property3:
                comparison.property3_address = result.property3.address
            
            comparison.comparison_data = result.to_dict()
            comparison.winner_property = result.winner_property_num
            comparison.rankings = result.rankings
            comparison.status = 'completed'
            comparison.completed_at = datetime.utcnow()
            
            db.session.commit()
            
            logger.info(f"✅ Comparison {comparison.id} completed. Winner: Property {result.winner_property_num}")
            
            return jsonify({
                'success': True,
                'comparison_id': comparison.id,
                'result': result.to_dict(),
                'credits_remaining': current_user.analysis_credits
            })
            
        except Exception as e:
            comparison.status = 'failed'
            comparison.error_message = str(e)
            db.session.commit()
            
            logger.error(f"❌ Comparison {comparison.id} failed: {str(e)}")
            
            return jsonify({
                'success': False,
                'error': 'Comparison failed. Please try again.',
                'comparison_id': comparison.id
            }), 500
        
    except Exception as e:
        logger.error(f"❌ Compare properties error: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': 'An internal error occurred. Please try again.'
        }), 500


@app.route('/api/comparisons/<int:comparison_id>', methods=['GET'])
@login_required
def get_comparison(comparison_id):
    """Get comparison results by ID"""
    try:
        comparison = Comparison.query.filter_by(
            id=comparison_id,
            user_id=current_user.id
        ).first()
        
        if not comparison:
            return jsonify({
                'success': False,
                'error': 'Comparison not found'
            }), 404
        
        return jsonify({
            'success': True,
            'comparison': {
                'id': comparison.id,
                'status': comparison.status,
                'created_at': comparison.created_at.isoformat() if comparison.created_at else None,
                'completed_at': comparison.completed_at.isoformat() if comparison.completed_at else None,
                'result': comparison.comparison_data,
                'error': comparison.error_message
            }
        })
        
    except Exception as e:
        logger.error(f"❌ Get comparison error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'An internal error occurred. Please try again.'
        }), 500


@app.route('/api/user/comparisons', methods=['GET'])
@login_required
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

@app.route('/pricing')
def pricing():
    """Pricing page"""
    try:
        from funnel_tracker import track_from_request
        uid = current_user.id if current_user.is_authenticated else None
        track_from_request('pricing_view', request, user_id=uid)
    except Exception:
        pass
    return send_from_directory('static', 'pricing.html')

@app.route('/sample-analysis')
@app.route('/sample-analysis.html')
def sample_analysis():
    """Sample analysis page"""
    return send_from_directory('static', 'sample-analysis.html')

@app.route('/free-tools')
def free_tools_page():
    """Free tools hub — all free tools in one tabbed dashboard."""
    return send_from_directory('static', 'free-tools.html')

@app.route('/truth-check')
def truth_check_page():
    """Free disclosure truth check - viral tool, no login required.
    When ?r= parameter present, serves dynamic OG tags for social link previews."""
    
    share_param = request.args.get('r')
    if not share_param:
        return send_from_directory('static', 'truth-check.html')
    
    # Decode shared result for OG tags
    try:
        import base64
        decoded = base64.urlsafe_b64decode(share_param + '==').decode('utf-8')
        share_data = json.loads(decoded)
        score = int(share_data.get('s', 50))
        grade = share_data.get('g', 'C')
        flags = int(share_data.get('f', 0))
        concern = share_data.get('c', '')[:120]
    except Exception:
        return send_from_directory('static', 'truth-check.html')
    
    # Generate dynamic OG tags for this specific result
    if score >= 70:
        og_title = f"Seller Transparency: {score}/100 (Grade {grade}) — Looks Clean"
        og_desc = f"AI found {flags} red flag{'s' if flags != 1 else ''}. {concern}" if flags > 0 else "No major red flags detected. Check your own seller disclosure free."
    elif score >= 40:
        og_title = f"Seller Transparency: {score}/100 (Grade {grade}) — Caution Advised"
        og_desc = f"⚠️ AI found {flags} red flag{'s' if flags != 1 else ''}. {concern}"
    else:
        og_title = f"Seller Transparency: {score}/100 (Grade {grade}) — Major Red Flags"
        og_desc = f"🚩 AI found {flags} red flag{'s' if flags != 1 else ''}. {concern}"
    
    # Read the static file and inject OG tags
    html_path = os.path.join(app.root_path, 'static', 'truth-check.html')
    with open(html_path, 'r') as f:
        html = f.read()
    
    # Replace default OG tags with dynamic ones
    html = html.replace(
        '<meta property="og:title" content="Is Your Seller Telling the Truth?">',
        f'<meta property="og:title" content="{og_title}">'
    )
    html = html.replace(
        '<meta property="og:description" content="68% of seller disclosures contain contradictions. Check yours free.">',
        f'<meta property="og:description" content="{og_desc}">'
    )
    # Add Twitter card meta
    html = html.replace(
        '<meta property="og:type" content="website">',
        f'<meta property="og:type" content="website">\n    <meta name="twitter:card" content="summary_large_image">\n    <meta name="twitter:title" content="{og_title}">\n    <meta name="twitter:description" content="{og_desc}">'
    )
    
    return html


# =============================================================================
# RISK CHECK — "What's Hiding at This Address?" (v5.59.66)
# Zero-friction viral tool. No signup. Just an address.
# =============================================================================

@app.route('/risk-check')
def risk_check_page():
    """Free property risk scanner — viral tool, no login required.
    When ?r= param present, serves dynamic OG tags for social previews."""
    try:
        from funnel_tracker import track_from_request
        track_from_request('visit', request, metadata={'page': 'risk_check'})
    except Exception:
        pass

    share_param = request.args.get('r')
    if not share_param:
        return send_from_directory('static', 'risk-check.html')

    # Decode shared result for OG tags
    try:
        import base64
        decoded = base64.b64decode(share_param).decode('utf-8')
        share_data = json.loads(decoded)
        exposure = int(share_data.get('e', 0))
        grade = share_data.get('g', '?')
        count = int(share_data.get('n', 0))
        city = share_data.get('c', '')
        state = share_data.get('s', '')
    except Exception:
        return send_from_directory('static', 'risk-check.html')

    location = f"{city}, {state}" if city and state else city or state or "a US property"

    if exposure >= 40000:
        og_title = f"${exposure:,} in Hidden Risks Found at {location}"
        og_desc = f"Risk Grade {grade} — {count} hidden risk factors sellers don't disclose. Scan YOUR address free."
    elif exposure >= 10000:
        og_title = f"${exposure:,} in Hidden Risks at {location}"
        og_desc = f"Risk Grade {grade} — Check if YOUR address has hidden risks. Free, no signup."
    else:
        og_title = f"Property Risk Scan: Grade {grade} in {location}"
        og_desc = f"Found {count} risk factor{'s' if count != 1 else ''}. Check any US address free."

    # Read the HTML and inject OG tags
    try:
        html_path = os.path.join(app.static_folder, 'risk-check.html')
        with open(html_path, 'r') as f:
            html = f.read()
        html = html.replace(
            '<meta property="og:title" content="What\'s Hiding at This Address? | OfferWise" id="ogTitle">',
            f'<meta property="og:title" content="{og_title}" id="ogTitle">'
        )
        html = html.replace(
            '<meta property="og:description" content="I just scanned an address and found hidden risks the seller won\'t tell you about. Check yours free." id="ogDesc">',
            f'<meta property="og:description" content="{og_desc}" id="ogDesc">'
        )
        return html
    except Exception:
        return send_from_directory('static', 'risk-check.html')


# =====================================================================
# SEO: Zip code risk report pages (25 Bay Area zip codes + index)
# Targets: "is [zip] in flood zone", "earthquake risk [city]", etc.
# =====================================================================
@app.route('/risk')
@app.route('/risk/')
def risk_index():
    """Master ranking page — all 25 Bay Area zip codes."""
    return send_from_directory('static/risk', 'index.html')

@app.route('/risk/<zipcode>')
def risk_zip(zipcode):
    """Individual zip code risk report page."""
    if not zipcode.isdigit() or len(zipcode) != 5:
        return redirect('/risk')
    filename = f'{zipcode}.html'
    filepath = os.path.join(app.static_folder, 'risk', filename)
    if os.path.exists(filepath):
        return send_from_directory('static/risk', filename)
    return redirect('/risk')


@app.route('/api/risk-check', methods=['POST'])
@limiter.limit("20 per hour")
def api_risk_check():
    """Run property risk scan. Free, no auth required. Rate limited."""
    try:
        from funnel_tracker import track_from_request
        data = request.get_json() or {}
        address = data.get('address', '').strip()
        logging.info(f"🔍 Risk check request: '{address}'")

        track_from_request('risk_check_start', request, metadata={'address': address[:100]})

        if not address or len(address) < 5:
            return jsonify({'error': 'Please enter a full street address.'}), 400

        result = run_risk_check(address)

        if result.get('error'):
            logging.warning(f"🔍 Risk check geocode fail: '{address}' → {result['error']}")
            return jsonify(result), 400

        track_from_request('risk_check_complete', request, metadata={'grade': result.get('risk_grade')})
        logging.info(f"🔍 Risk check: {result.get('address')} → ${result.get('risk_exposure', 0):,} (Grade {result.get('risk_grade', '?')}) [{result.get('scan_time_ms')}ms]")
        return jsonify(result)

    except Exception as e:
        import traceback
        logging.error(f"Risk check error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Risk scan failed. Please try again.'}), 500


@app.route('/settings')
@login_required
def settings():
    """User settings page"""
    return send_from_directory('static', 'settings.html')

@app.route('/compare')
@login_required
def compare_page():
    """Property comparison page - Battle Royale feature"""
    return send_from_directory('static', 'compare.html')

@app.route('/compare.js')
@login_required
def compare_js():
    """Serve comparison JavaScript"""
    return send_from_directory('static', 'compare.js')

@app.route('/debug')
@admin_required
def debug_page():
    """Debug diagnostic page - ADMIN ONLY (v5.54.53)"""
    return send_from_directory('static', 'debug.html')

@app.route('/onboarding')
@login_required
def serve_onboarding():
    """
    Redirect to Settings Legal tab.
    
    CONSOLIDATED: We had two different UIs for legal consent acceptance:
    1. /onboarding (simple wizard)
    2. /settings?tab=legal (polished accordion)
    
    User preferred the Settings version, so we consolidated to use only that.
    This redirect ensures all onboarding flows use the same, better UI.
    """
    return redirect('/settings?tab=legal')

@app.route('/api/user/info')
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

@app.route('/api/user', methods=['GET'])
@login_required
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


@app.route('/api/my-account', methods=['GET'])
@login_required
def get_my_account():
    """Get current user account details"""
    try:
        return jsonify({
            'id': current_user.id,
            'name': current_user.name or 'User',
            'email': current_user.email or '',
            'tier': current_user.tier or 'free',
            'credits': current_user.analysis_credits or 0,
            'total_credits_purchased': current_user.total_credits_purchased or 0,
            'auth_provider': current_user.auth_provider if hasattr(current_user, 'auth_provider') else None,
            'created_at': current_user.created_at.isoformat() if current_user.created_at else None
        })
    except Exception as e:
        logging.error(f"❌ Error in /api/my-account: {e}")
        return jsonify({'error': 'An internal error occurred'}), 500

@app.route('/api/user/preferences', methods=['GET', 'POST'])
@login_required
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

@app.route('/api/user/debug-data', methods=['GET'])
@dev_only_gate
@login_required
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

@app.route('/api/version', methods=['GET'])
def get_version():
    """Get the current version - use this to verify deployments!"""
    try:
        with open('VERSION', 'r') as f:
            version = f.read().strip()
        return jsonify({
            'success': True,
            'version': version,
            'timestamp': datetime.utcnow().isoformat()
        })
    except FileNotFoundError:
        return jsonify({
            'success': False,
            'version': 'UNKNOWN - VERSION file not found!',
            'timestamp': datetime.utcnow().isoformat()
        })

@app.route('/api/user/delete', methods=['POST'])
@login_required
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

@app.route('/profile')
def profile():
    """Redirect profile to settings for backward compatibility"""
    return redirect(url_for('settings'))

@app.route('/data-deletion')
def data_deletion():
    """Data deletion instructions page (required for Facebook OAuth)"""
    return send_from_directory('static', 'data-deletion.html')

@app.route('/terms')
def terms():
    """Terms of Service page"""
    return send_from_directory('static', 'terms.html')

@app.route('/api/accept-terms', methods=['POST'])
def accept_terms():
    """Accept Terms of Service"""
    try:
        # Check if user is logged in
        if not current_user.is_authenticated:
            return jsonify({'error': 'Not authenticated', 'redirect': '/login'}), 401
        
        current_user.terms_accepted_at = datetime.utcnow()
        current_user.terms_version = '1.0'
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        logging.error(f"Error accepting terms: {e}")
        return jsonify({'error': 'Failed to accept terms'}), 500

@app.route('/api/check-terms', methods=['GET'])
def check_terms():
    """Check if user has accepted terms"""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return jsonify({'accepted': False, 'error': 'Not authenticated'}), 401
    
    return jsonify({
        'accepted': current_user.terms_accepted_at is not None,
        'accepted_at': current_user.terms_accepted_at.isoformat() if current_user.terms_accepted_at else None
    })

@app.route('/api/delete-account', methods=['DELETE'])
@login_required
@validate_origin  # SECURITY: CSRF protection
@limiter.limit("3 per hour")  # SECURITY: Prevent abuse
def delete_account():
    """Permanently delete user account and all associated data"""
    logging.info(f"🗑️ DELETE ACCOUNT REQUEST - User: {current_user.email}")
    
    try:
        user_id = current_user.id
        user_email = current_user.email
        
        # Count what will be deleted
        properties_count = Property.query.filter_by(user_id=user_id).count()
        comparisons_count = Comparison.query.filter_by(user_id=user_id).count()
        usage_count = UsageRecord.query.filter_by(user_id=user_id).count()
        consent_count = ConsentRecord.query.filter_by(user_id=user_id).count()
        magic_links_count = MagicLink.query.filter_by(email=user_email).count()
        logging.info(f"   Magic links: {magic_links_count}")
        
        # Step 2: Delete comparisons (MUST BE FIRST - user_id NOT NULL)
        logging.info(f"Step 2: Deleting comparisons...")
        if comparisons_count > 0:
            # NUCLEAR OPTION: Use raw SQL to bypass ALL SQLAlchemy ORM tracking
            # This completely avoids relationships, backrefs, session tracking, everything!
            # SQLAlchemy can't track what it doesn't see!
            logging.info(f"   Using raw SQL to delete {comparisons_count} comparison(s)...")
            db.session.execute(
                db.text("DELETE FROM comparisons WHERE user_id = :user_id"),
                {"user_id": user_id}
            )
            
            # CRITICAL: Commit immediately
            logging.info(f"   Committing raw SQL deletion...")
            db.session.commit()
            logging.info(f"   ✅ Committed - comparisons deleted via raw SQL (SQLAlchemy never tracked them!)")
        else:
            logging.info(f"   No comparisons to delete")
        
        # Step 3: Delete magic links
        logging.info(f"Step 3: Deleting magic links...")
        if magic_links_count > 0:
            MagicLink.query.filter_by(email=user_email).delete()
            logging.info(f"   ✅ Deleted {magic_links_count} magic link(s)")
        else:
            logging.info(f"   No magic links to delete")
        
        # Step 4: Track deletion in EmailRegistry (BEFORE deleting user)
        logging.info(f"Step 4: Tracking deletion in EmailRegistry...")
        user_credits = current_user.analysis_credits
        
        # Developer accounts always get full credits restored
        # Uses global DEVELOPER_EMAILS
        if current_user.email.lower() in DEVELOPER_EMAILS:
            user_credits = 500  # Always restore full credits for developers
            logging.info(f"   👑 Developer account - will restore with 500 credits on re-signup")
        
        EmailRegistry.track_deletion(user_email, credits_to_save=user_credits)
        logging.info(f"   ✅ Tracked deletion for {user_email}")
        logging.info(f"   💰 Saved {user_credits} credits for future restoration")
        
        # Step 5: Delete the user (cascades will handle properties, documents, analyses, etc.)
        logging.info(f"Step 5: Deleting user {user_id}...")
        
        # CRITICAL FIX: Use current_user directly instead of querying!
        # Querying User.query.get() might load relationships (including comparisons we just deleted)
        # This causes SQLAlchemy to try updating deleted comparisons!
        logging.info(f"   Using current_user object directly (avoids loading relationships)")
        db.session.delete(current_user)
        logging.info(f"   User marked for deletion")
        
        # Step 6: Commit the transaction
        logging.info(f"Step 6: Committing final transaction...")
        db.session.commit()
        logging.info(f"   ✅ Transaction committed successfully!")
        
        # Step 7: Log out the user
        logging.info(f"Step 7: Logging out user...")
        logout_user()
        logging.info(f"   ✅ User logged out")
        
        logging.info(f"")
        logging.info(f"   🎉 SUCCESS! Account deleted:")
        logging.info(f"   - User: {user_email} (ID: {user_id})")
        logging.info(f"   - Properties: {properties_count}")
        logging.info(f"   - Comparisons: {comparisons_count}")
        logging.info(f"   - Usage records: {usage_count}")
        logging.info(f"   - Consent records: {consent_count}")
        logging.info(f"   - Email tracked in registry (prevents credit abuse)")
        logging.info(f"=" * 80)
        
        return jsonify({
            'success': True,
            'message': 'Account successfully deleted'
        }), 200
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"   ❌ EXCEPTION CAUGHT!")
        logging.error(f"   Exception type: {type(e).__name__}")
        logging.error(f"   Exception message: {str(e)}")
        logging.exception(e)  # Full stack trace
        logging.info(f"=" * 80)
        return jsonify({
            'success': False,
            'error': 'An error occurred while deleting your account. Please try again.'
        }), 500

@app.route('/privacy')
def privacy_policy():
    """Privacy Policy page"""
    return send_from_directory('static', 'privacy.html')

@app.route('/disclaimer')
def analysis_disclaimer():
    """Analysis Disclaimer page"""
    return send_from_directory('static', 'disclaimer.html')

@app.route('/api/get-disclaimer')
def get_disclaimer():
    """API endpoint to get disclaimer text"""
    try:
        from legal_disclaimers import ANALYSIS_DISCLAIMER_TEXT, ANALYSIS_DISCLAIMER_VERSION
        return jsonify({
            'success': True,
            'text': ANALYSIS_DISCLAIMER_TEXT,
            'version': ANALYSIS_DISCLAIMER_VERSION
        })
    except Exception as e:
        return jsonify({'success': False, 'error': 'An internal error occurred. Please try again.'}), 500

@app.route('/contact')
def contact():
    """Contact page"""
    return send_from_directory('static', 'contact.html')


# =============================================================================
# TRUTH CHECK - Free Disclosure Scanner (No Login Required)
# =============================================================================

@app.route('/api/truth-check', methods=['POST'])
@limiter.limit("5 per hour")
def api_truth_check():
    """
    Free disclosure truth check. No login required.
    Sends PDF directly to Anthropic vision API for analysis.
    Rate limited to 5 per hour per IP.
    """
    try:
        from funnel_tracker import track_from_request
        track_from_request('truth_check_start', request)
        data = request.get_json()
        if not data or not data.get('pdf_base64'):
            return jsonify({'error': 'Please upload a PDF file.'}), 400
        
        pdf_base64 = data['pdf_base64']
        
        # Remove data URL prefix
        if ',' in pdf_base64:
            pdf_base64 = pdf_base64.split(',')[1]
        
        # Size check (base64)
        if len(pdf_base64) > 20_971_520:
            return jsonify({'error': 'File too large (max 15MB).'}), 413
        
        # Validate it decodes and is a PDF
        try:
            pdf_bytes = base64.b64decode(pdf_base64)
        except Exception:
            return jsonify({'error': 'Invalid file encoding.'}), 400
        
        if not pdf_bytes.startswith(b'%PDF-'):
            return jsonify({'error': 'Please upload a valid PDF file.'}), 400
        
        if len(pdf_bytes) > 15_728_640:
            return jsonify({'error': 'File too large (max 15MB).'}), 413
        
        # === LIGHTWEIGHT TEXT EXTRACTION FOR GROUNDING (v5.60.3) ===
        # Fast text-only extraction (no OCR) — used to verify evidence strings
        # and evasion phrases actually appear in the document. If extraction
        # fails or returns too little text, grounding checks are just skipped.
        _pdf_text_for_grounding = None
        try:
            import io as _io
            # Try pdfplumber first (fast, handles most digital PDFs)
            try:
                import pdfplumber as _pdfplumber
                _pages_text = []
                with _pdfplumber.open(_io.BytesIO(pdf_bytes)) as _pdf:
                    for _page in _pdf.pages[:20]:  # Cap at 20 pages
                        _pt = _page.extract_text()
                        if _pt:
                            _pages_text.append(_pt)
                if _pages_text:
                    _pdf_text_for_grounding = '\n'.join(_pages_text)
            except Exception:
                pass

            # Fallback to PyPDF2 if pdfplumber got nothing
            if not _pdf_text_for_grounding or len(_pdf_text_for_grounding) < 100:
                try:
                    import PyPDF2 as _PyPDF2
                    _reader = _PyPDF2.PdfReader(_io.BytesIO(pdf_bytes))
                    _pages_text = []
                    for _page in _reader.pages[:20]:
                        _pt = _page.extract_text()
                        if _pt:
                            _pages_text.append(_pt)
                    if _pages_text:
                        _pdf_text_for_grounding = '\n'.join(_pages_text)
                except Exception:
                    pass

            if _pdf_text_for_grounding and len(_pdf_text_for_grounding) < 50:
                _pdf_text_for_grounding = None  # Too little to ground against

            if _pdf_text_for_grounding:
                logging.info(f"Truth Check grounding: extracted {len(_pdf_text_for_grounding)} chars for evidence verification")
            else:
                logging.info("Truth Check grounding: no text extracted (scanned PDF?), skipping evidence checks")
        except Exception as _ext_err:
            logging.warning(f"Truth Check grounding extraction failed (non-fatal): {_ext_err}")
            _pdf_text_for_grounding = None

        # Send PDF directly to Anthropic API (handles scanned, DocuSign, handwritten)
        import anthropic as _anthropic
        _api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not _api_key:
            return jsonify({'error': 'Analysis service temporarily unavailable.'}), 503
        
        client = _anthropic.Anthropic(api_key=_api_key)
        
        prompt = """You are analyzing a US residential real estate seller disclosure document. These vary by state — examples include California's Transfer Disclosure Statement (TDS), Texas's Seller's Disclosure Notice, Florida's Seller's Disclosure, New York's Property Condition Disclosure Statement, and similar forms in other states. Regardless of the specific form, your job is to report ONLY what you can directly observe in the document. Accuracy is paramount. Never speculate or infer.

First, identify the document type and state if possible. Then analyze it using these rules.

STRICT RULES:
1. Every red flag MUST cite specific text, checkbox, or section from the document. If you cannot point to exact evidence, do not include it.
2. NEVER claim the document says something it does not say. If you are uncertain about a checkbox or answer, say "unclear" rather than guessing.
3. Do NOT flag a "No" answer as suspicious just because the issue is common. Only flag it if there is a CONTRADICTION within the document itself.
4. For evasion_phrases, only include phrases that ACTUALLY APPEAR in the document verbatim.
5. Do NOT speculate about the seller's intent, the property's age, neighborhood norms, or what "should" be disclosed.
6. For blank_unknown_count, count ONLY questions you can verify are actually blank, marked unknown, or marked N/A in the document.

Respond ONLY with valid JSON (no markdown, no backticks, no extra text):
{
  "document_type": "<detected form type, e.g. 'California TDS', 'Texas Seller Disclosure', 'Florida Seller Disclosure', 'Unknown'>",
  "state": "<2-letter state code if detectable, or 'unknown'>",
  "trust_score": <number 0-100, where 100 means fully transparent>,
  "grade": "<letter grade A through F>",
  "red_flags": [
    {
      "title": "<short factual title>",
      "detail": "<what the document specifically says, with direct quotes in quotation marks>",
      "severity": "<high|medium|low>",
      "category": "<water|structural|electrical|plumbing|roof|environmental|permits|pest|other>",
      "evidence": "<exact text, checkbox state, or section reference from the document>"
    }
  ],
  "blank_unknown_count": <number of questions verifiably answered as unknown, N/A, blank, or unanswered>,
  "evasion_phrases": ["<phrases that ACTUALLY APPEAR verbatim in the document>"],
  "most_concerning": "<1-2 sentence summary of the single most concerning finding, citing specific evidence>",
  "overall_assessment": "<2-3 sentence plain-English assessment based ONLY on what the document contains>"
}

What constitutes a legitimate red flag:
- A direct contradiction WITHIN the document (e.g., one section marks a system as defective but another section says no awareness of defects)
- A question explicitly answered "Unknown" that the owner would reasonably know after living there
- A section left completely blank where an answer is required
- Disclosed defects with vague or incomplete descriptions
- Specific language in the document that hedges or qualifies disclosure (only if you can quote it exactly)

What is NOT a legitimate red flag:
- A "No" answer that you think should be "Yes" based on assumptions
- Missing information that might exist on a separate form not included here
- Speculation about property age, neighborhood, or market conditions
- Inferences about the seller's knowledge or honesty beyond what the document states"""

        _t0 = time.time()
        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }]
        )
        try:
            from ai_cost_tracker import track_ai_call as _track
            _track(response, "truth-check", (time.time() - _t0) * 1000)
        except Exception:
            pass
        
        import time as _time
        _tc_start = _time.time()
        
        response_text = response.content[0].text.strip()
        
        # Strip markdown fences if present
        if response_text.startswith('```'):
            response_text = response_text.split('\n', 1)[1] if '\n' in response_text else response_text[3:]
            if response_text.endswith('```'):
                response_text = response_text[:-3]
            response_text = response_text.strip()
        
        analysis = json.loads(response_text)
        
        # === AI OUTPUT VALIDATION (v5.60.3) ===
        # Enforce output contracts + evidence grounding before returning to user
        from ai_output_validator import validate_truth_check, log_ai_call
        
        analysis, violations = validate_truth_check(analysis, pdf_text=_pdf_text_for_grounding)
        
        # Ensure required fields (belt + suspenders with validator)
        analysis.setdefault('trust_score', 50)
        analysis.setdefault('grade', 'C')
        analysis.setdefault('red_flags', [])
        analysis.setdefault('blank_unknown_count', 0)
        analysis.setdefault('evasion_phrases', [])
        analysis.setdefault('most_concerning', 'Unable to determine.')
        analysis.setdefault('overall_assessment', 'Analysis complete.')
        
        # Structured audit log — every AI call, every violation
        _tc_latency = (_time.time() - _tc_start) * 1000
        _usage = getattr(response, 'usage', None)
        log_ai_call(
            endpoint='truth-check',
            model='claude-sonnet-4-5-20250929',
            input_summary={'pdf_size_b64': len(pdf_base64),
                           'grounding_text_len': len(_pdf_text_for_grounding) if _pdf_text_for_grounding else 0},
            raw_output=response_text[:2000],
            validated_output=analysis,
            violations=violations,
            latency_ms=_tc_latency,
            tokens_in=getattr(_usage, 'input_tokens', 0) if _usage else 0,
            tokens_out=getattr(_usage, 'output_tokens', 0) if _usage else 0,
        )
        
        if violations:
            logging.warning(f"Truth Check AI violations: {len(violations)} — "
                          + "; ".join(v['code'] for v in violations[:5]))
        
        logging.info(f"Truth Check completed: score={analysis['trust_score']}, "
                    f"flags={len(analysis['red_flags'])}, "
                    f"violations={len(violations)}, "
                    f"latency={_tc_latency:.0f}ms")
        
        track_from_request('truth_check_complete', request, metadata={'score': analysis.get('trust_score')})
        return jsonify(analysis)
        
    except json.JSONDecodeError:
        logging.error("Truth Check JSON parse error")
        return jsonify({'error': 'Analysis failed. Please try again.'}), 500
    except Exception as e:
        logging.error(f"Truth Check error: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return jsonify({'error': 'Analysis failed. Please try again.'}), 500


@app.route('/api/contact', methods=['POST'])
@limiter.limit("5 per hour")
def submit_contact_form():
    """
    Handle contact form submissions.
    Sends email to francis@getofferwise.ai with reply-to set to sender.
    No auth required — public endpoint.
    """
    try:
        data = request.get_json()
        
        sender_name = (data.get('name') or '').strip()[:200]
        sender_email = (data.get('email') or '').strip()[:255]
        category = (data.get('category') or 'general').strip()[:50]
        message = (data.get('message') or '').strip()[:5000]
        
        if not sender_email or not message:
            return jsonify({'error': 'Email and message are required'}), 400
        
        # Basic email format check
        if '@' not in sender_email or '.' not in sender_email:
            return jsonify({'error': 'Please enter a valid email address'}), 400
        
        # Build email HTML
        category_labels = {
            'general': '👋 General Inquiry',
            'billing': '💳 Billing & Payments',
            'support': '🛠️ Technical Support',
            'feedback': '💬 Product Feedback',
            'partnership': '🤝 Partnership',
        }
        cat_label = category_labels.get(category, f'📩 {category}')
        
        html = f"""
        <div style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: #0f172a; padding: 24px; border-radius: 12px 12px 0 0;">
                <h2 style="color: #60a5fa; margin: 0;">OfferWise Contact Form</h2>
            </div>
            <div style="background: #1e293b; padding: 24px; border-radius: 0 0 12px 12px; color: #e2e8f0;">
                <table style="width: 100%; border-collapse: collapse;">
                    <tr>
                        <td style="padding: 8px 0; color: #94a3b8; width: 100px;">Category</td>
                        <td style="padding: 8px 0; font-weight: 600;">{cat_label}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #94a3b8;">From</td>
                        <td style="padding: 8px 0; font-weight: 600;">{sender_name or 'Not provided'}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #94a3b8;">Email</td>
                        <td style="padding: 8px 0;"><a href="mailto:{sender_email}" style="color: #60a5fa;">{sender_email}</a></td>
                    </tr>
                </table>
                <hr style="border: none; border-top: 1px solid #334155; margin: 16px 0;">
                <div style="white-space: pre-wrap; line-height: 1.7; color: #cbd5e1;">{message}</div>
                <hr style="border: none; border-top: 1px solid #334155; margin: 16px 0;">
                <p style="font-size: 12px; color: #64748b; margin: 0;">
                    Reply directly to this email to respond to {sender_email}
                </p>
            </div>
        </div>
        """
        
        subject = f"[OfferWise Contact] {cat_label} — {sender_name or sender_email}"
        
        # Send to Francis with reply-to set to the sender
        sent = send_email(
            to_email='francis@piotnetworks.com',
            subject=subject,
            html_content=html,
            reply_to=sender_email
        )
        
        if sent:
            logging.info(f"📬 Contact form: {category} from {sender_email}")
        else:
            # Even if email fails, log it so nothing is lost
            logging.warning(f"📬 Contact form (email failed): {category} from {sender_email} — {message[:200]}")
        
        return jsonify({'success': True, 'message': 'Message sent! We\'ll get back to you within 24-48 hours.'})
        
    except Exception as e:
        logging.error(f"❌ Contact form error: {e}")
        return jsonify({'error': 'Failed to send message. Please try emailing us directly.'}), 500

@app.route('/googlecfd34de59262ea27.html')
def google_verification():
    """Google Search Console verification"""
    return send_from_directory('static', 'googlecfd34de59262ea27.html')

@app.route('/sitemap.xml')
def sitemap():
    """Sitemap for search engines"""
    return send_from_directory('static', 'sitemap.xml', mimetype='application/xml')

@app.route('/robots.txt')
def robots():
    """Robots.txt for search engines"""
    return send_from_directory('static', 'robots.txt', mimetype='text/plain')

@app.route('/guides')
def guides_index():
    """Guides index page"""
    return send_from_directory('static/guides', 'index.html')

@app.route('/games/<path:game_name>')
def games_page(game_name):
    """Interactive games for engagement and SEO (v5.62.38)"""
    if not game_name.endswith('.html'):
        game_name = game_name + '.html'
    return send_from_directory('static/games', game_name)


@app.route('/guides/<path:guide_name>')
def guides_page(guide_name):
    """Individual guide pages"""
    # Add .html extension if not present
    if not guide_name.endswith('.html'):
        guide_name = guide_name + '.html'
    return send_from_directory('static/guides', guide_name)

@app.route('/checkout')
def checkout():
    """Checkout page"""
    return send_from_directory('static', 'checkout.html')

@app.route('/api/pricing')
def get_pricing():
    """Get pricing tiers"""
    return jsonify({'tiers': PRICING_TIERS})

@app.route('/api/stripe-config')
def get_stripe_config():
    """Get Stripe publishable key"""
    return jsonify({
        'publishableKey': stripe_publishable
    })

@app.route('/api/create-checkout-session', methods=['POST'])
@login_required
@validate_origin  # SECURITY: CSRF protection
@limiter.limit("10 per hour")  # SECURITY: Prevent abuse
def create_checkout_session():
    """Create a Stripe checkout session"""
    try:
        # Check if Stripe is configured
        if not stripe.api_key:
            logging.error("❌ Stripe API key not configured!")
            return jsonify({
                'error': 'Payment system not configured',
                'message': 'Stripe API key missing. Please configure STRIPE_SECRET_KEY environment variable.'
            }), 500
        
        # Log test vs live mode
        is_test_mode = stripe.api_key.startswith('sk_test_')
        logging.info(f"💳 Stripe mode: {'TEST' if is_test_mode else 'LIVE'}")
        
        data = request.get_json()
        plan = data.get('plan', 'bundle_5')
        
        # Inspector Pro — recurring subscription via Stripe Price ID
        if plan == 'inspector_pro':
            from auth_config import PRICING_TIERS
            price_id = PRICING_TIERS['inspector_pro']['stripe_price_id']
            if not price_id:
                return jsonify({'error': 'Inspector Pro not yet configured'}), 500
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{'price': price_id, 'quantity': 1}],
                mode='subscription',
                success_url=url_for('payment_success', session_id='{CHECKOUT_SESSION_ID}', plan='inspector_pro', amount=4900, _external=True),
                cancel_url=url_for('for_inspectors_landing', _external=True),
                client_reference_id=str(current_user.id),
                metadata={
                    'user_id': str(current_user.id),
                    'plan': 'inspector_pro',
                    'credits': 0,
                }
            )
            logging.info(f"✅ Inspector Pro checkout session: {checkout_session.id}")
            return jsonify({'sessionId': checkout_session.id})

        # Define pricing - amounts in cents (v5.73.9 repricing)
        prices = {
            'single': {'amount': 1900, 'credits': 1, 'name': 'Single Analysis'},
            'bundle_5': {'amount': 7900, 'credits': 5, 'name': '5-Analysis Bundle'},
            'bundle_12': {'amount': 14900, 'credits': 12, 'name': 'Investor Pro (12 Analyses)'}
        }
        
        if plan not in prices:
            return jsonify({'error': 'Invalid plan'}), 400
        
        price_info = prices[plan]
        
        logging.info(f"Creating checkout session: {price_info['name']} - ${price_info['amount']/100:.2f}")
        
        # Create Stripe checkout session
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'unit_amount': price_info['amount'],
                    'product_data': {
                        'name': price_info['name'],
                        'description': f"{price_info['credits']} property analysis credits",
                    },
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('payment_success', session_id='{CHECKOUT_SESSION_ID}', plan=plan, amount=price_info['amount'], _external=True),
            cancel_url=url_for('payment_cancel', _external=True),
            client_reference_id=str(current_user.id),
            metadata={
                'user_id': str(current_user.id),
                'plan': plan,
                'credits': price_info['credits']
            }
        )
        
        logging.info(f"✅ Checkout session created: {checkout_session.id}")
        
        return jsonify({'sessionId': checkout_session.id})
        
    except stripe.error.StripeError as e:
        logging.error(f"❌ Stripe error: {type(e).__name__} - {str(e)}")
        return jsonify({
            'error': 'Payment system error',
            'message': 'An internal error occurred. Please try again.'
        }), 500
    except Exception as e:
        logging.error(f"❌ Error creating checkout session: {str(e)}")
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500

@app.route('/payment/success')
@login_required
def payment_success():
    """Payment success page"""
    session_id = request.args.get('session_id')
    return send_from_directory('static', 'payment-success.html')

@app.route('/payment/cancel')
@login_required
def payment_cancel():
    """Payment cancel page"""
    return send_from_directory('static', 'payment-cancel.html')

@app.route('/webhook/stripe', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events"""
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError:
        logging.error("Invalid Stripe webhook payload")
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        logging.error("Invalid Stripe webhook signature")
        return jsonify({'error': 'Invalid signature'}), 400
    
    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']

        user_id = session['metadata'].get('user_id')
        credits = int(session['metadata'].get('credits', 0))
        plan = session['metadata'].get('plan')
        amount_total = session.get('amount_total', 0) / 100

        user = User.query.get(user_id)
        if user:

            # Inspector Pro subscription
            if plan == 'inspector_pro':
                insp = Inspector.query.filter_by(user_id=user.id).first()
                if insp:
                    from datetime import timedelta as _td
                    insp.plan = 'inspector_pro'
                    insp.monthly_quota = -1
                    insp.quota_reset_at = datetime.utcnow() + _td(days=30)
                    db.session.commit()
                    logging.info(f"Star Inspector Pro activated for user {user_id} ({user.email})")
                    try:
                        send_email(
                            to_email=user.email,
                            subject="Inspector Pro is active — unlimited analyses",
                            html_content=(
                                '<div style="font-family:sans-serif;max-width:600px;background:#0f172a;'
                                'color:#f1f5f9;padding:28px;border-radius:12px;">'
                                '<div style="font-size:22px;font-weight:800;margin-bottom:8px;">You\'re on Inspector Pro!</div>'
                                f'<div style="font-size:14px;color:#94a3b8;margin-bottom:20px;">Hi {user.name or "there"} — your Inspector Pro plan is now active.</div>'
                                '<div style="padding:16px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:10px;margin-bottom:20px;">'
                                '<div style="font-weight:700;color:#f97316;margin-bottom:6px;">What\'s unlocked:</div>'
                                '<div style="font-size:13px;color:#94a3b8;line-height:1.7;">Unlimited buyer analyses per month<br>'
                                'Buyer conversion tracking<br>Priority support<br>CSV export</div></div>'
                                '<a href="https://www.getofferwise.ai/inspector-portal" '
                                'style="display:inline-block;padding:12px 24px;background:linear-gradient(90deg,#f97316,#f59e0b);'
                                'color:white;border-radius:10px;font-weight:700;text-decoration:none;">Go to Inspector Portal</a>'
                                '</div>'
                            )
                        )
                    except Exception as e:
                        logging.warning(f"Inspector Pro email failed: {e}")
                else:
                    logging.warning(f"Inspector Pro paid but no Inspector record for user {user_id}")
                try:
                    from funnel_tracker import track
                    track('purchase', source='stripe', user_id=int(user_id), metadata={
                        'plan': plan, 'credits': 0, 'amount': amount_total,
                    })
                except Exception:
                    pass

            else:
                # Standard buyer credit purchase
                user.analysis_credits += credits
                user.stripe_customer_id = user.stripe_customer_id or session.get('customer')
                db.session.commit()
                logging.info(f"Added {credits} credits to user {user_id} for plan {plan}")
                try:
                    from funnel_tracker import track
                    track('purchase', source='stripe', user_id=int(user_id), metadata={
                        'plan': plan, 'credits': credits, 'amount': amount_total,
                    })
                except Exception:
                    pass
                try:
                    plan_names = {
                        'single': 'Single Analysis',
                        'bundle_5': '5-Pack Bundle',
                        'bundle_12': 'Investor Pro (12-Pack)'
                    }
                    plan_name = plan_names.get(plan, plan or 'Credit Pack')
                    send_purchase_receipt(
                        user.email,
                        user.name or 'there',
                        plan_name,
                        credits,
                        amount_total
                    )
                    logging.info(f"Purchase receipt sent to {user.email}")
                except Exception as e:
                    logging.warning(f"Could not send purchase receipt: {e}")
        else:
            logging.error(f"User {user_id} not found for webhook")
    
    return jsonify({'status': 'success'})


# =============================================================================
# RESEND WEBHOOK — Email Engagement Tracking (v5.74.47)
# =============================================================================
# Resend sends POST requests for: delivered, opened, clicked, bounced, complained
# Setup: Resend Dashboard → Webhooks → Add → URL: https://www.getofferwise.ai/webhook/resend
# Select events: email.delivered, email.opened, email.clicked, email.bounced, email.complained

@app.route('/webhook/resend', methods=['POST'])
@limiter.limit("200 per minute")
def resend_webhook():
    """Handle Resend webhook events for email engagement tracking."""
    try:
        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({'error': 'No payload'}), 400

        event_type = payload.get('type', '')
        data = payload.get('data', {})

        # Map Resend event types to our simplified types
        TYPE_MAP = {
            'email.delivered': 'delivered',
            'email.opened': 'opened',
            'email.clicked': 'clicked',
            'email.bounced': 'bounced',
            'email.complained': 'complained',
        }

        simple_type = TYPE_MAP.get(event_type)
        if not simple_type:
            return jsonify({'status': 'ignored', 'type': event_type}), 200

        from models import EmailEvent
        import json as _json

        event = EmailEvent(
            resend_id=data.get('email_id') or data.get('id', ''),
            to_email=(data.get('to', [''])[0] if isinstance(data.get('to'), list) else data.get('to', '')),
            event_type=simple_type,
            link_url=data.get('click', {}).get('link', '') if simple_type == 'clicked' else None,
            user_agent=data.get('click', {}).get('userAgent', '') if simple_type == 'clicked' else None,
            raw_json=_json.dumps(payload)[:2000],
        )
        db.session.add(event)
        db.session.commit()

        logging.info(f"📬 Resend webhook: {simple_type} for {event.to_email}")
        return jsonify({'status': 'ok'}), 200

    except Exception as e:
        logging.error(f"Resend webhook error: {e}")
        db.session.rollback()
        return jsonify({'status': 'error'}), 200  # Return 200 to prevent Resend retries


# =============================================================================
# STRIPE INTEGRATION TESTS (v5.54.55)
# =============================================================================


# ============================================================================
# REFERRAL SYSTEM TESTS (v5.54.66)
# ============================================================================


# ============================================================================
# INTEGRITY TESTS (v5.57.0)
# ============================================================================


# ============================================================================
# ADVERSARIAL PDF TESTS - Production Quality Assurance
# ============================================================================


# ============================================================================
# PDF CORPUS PIPELINE TESTS - Full end-to-end with real PDFs (v5.62.17)
# ============================================================================


# ============================================================================
# CONSENT MANAGEMENT - Legal Protection
# ============================================================================


@app.route('/api/consent/status', methods=['GET'])
@login_required
def get_consent_status():
    """
    Get all consent requirements and their current status.
    Used for onboarding and settings page.
    """
    try:
        # CRITICAL: If user has completed onboarding once, NEVER redirect again
        # This prevents the annoying loop of having to redo onboarding
        if current_user.onboarding_completed:
            logging.info(f"✅ User {current_user.email} already completed onboarding - skipping checks")
            
            # Still return consent statuses for settings page
            # FIXED: Use same names as when recording consents ('terms' not 'terms_of_service')
            consent_types = ['analysis_disclaimer', 'terms', 'privacy']
            statuses = []
            
            for consent_type in consent_types:
                required_version = get_disclaimer_version(consent_type)
                if required_version:
                    has_consent = ConsentRecord.has_current_consent(
                        user_id=current_user.id,
                        consent_type=consent_type,
                        required_version=required_version
                    )
                    
                    # Use display names for frontend
                    display_names = {
                        'analysis_disclaimer': 'Analysis Disclaimer',
                        'terms': 'Terms of Service',
                        'privacy': 'Privacy Policy'
                    }
                    
                    statuses.append({
                        'consent_type': consent_type,
                        'required_version': required_version,
                        'has_consent': has_consent,
                        'display_name': display_names.get(consent_type, consent_type.replace('_', ' ').title())
                    })
            
            return jsonify({
                'statuses': statuses,
                'needs_onboarding': False,  # NEVER redirect if completed once
                'onboarding_completed': True,
                'all_consented': all(s['has_consent'] for s in statuses),
                'has_preferences': True
            })
        
        # For NEW users who haven't completed onboarding yet
        # FIXED: Use same names as when recording consents ('terms' not 'terms_of_service')
        consent_types = ['analysis_disclaimer', 'terms', 'privacy']
        statuses = []
        
        for consent_type in consent_types:
            required_version = get_disclaimer_version(consent_type)
            if required_version:
                has_consent = ConsentRecord.has_current_consent(
                    user_id=current_user.id,
                    consent_type=consent_type,
                    required_version=required_version
                )
                
                # Use display names for frontend
                display_names = {
                    'analysis_disclaimer': 'Analysis Disclaimer',
                    'terms': 'Terms of Service',
                    'privacy': 'Privacy Policy'
                }
                
                statuses.append({
                    'consent_type': consent_type,
                    'required_version': required_version,
                    'has_consent': has_consent,
                    'display_name': display_names.get(consent_type, consent_type.replace('_', ' ').title())
                })
        
        # Check if any consents are missing
        any_consent_missing = any(not s['has_consent'] for s in statuses)
        
        # Check if user has set preferences (indicates completed onboarding)
        has_preferences = (
            current_user.max_budget is not None or
            current_user.repair_tolerance is not None or
            current_user.biggest_regret is not None
        )
        
        # Need onboarding if EITHER consents missing OR preferences missing
        # Only complete when BOTH are done
        needs_onboarding = any_consent_missing or not has_preferences
        
        return jsonify({
            'statuses': statuses,
            'needs_onboarding': needs_onboarding,
            'all_consented': not any_consent_missing,
            'has_preferences': has_preferences
        })
        
    except Exception as e:
        logging.error(f"Error getting consent status: {e}")
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@app.route('/api/consent/check', methods=['POST'])
@login_required
def check_consent():
    """
    Check if user has current consent for a specific type.
    
    Used to determine if we need to show consent dialog.
    """
    try:
        data = request.get_json()
        consent_type = data.get('consent_type', 'analysis_disclaimer')
        
        required_version = get_disclaimer_version(consent_type)
        if not required_version:
            return jsonify({'error': 'Invalid consent type'}), 400
        
        has_consent = ConsentRecord.has_current_consent(
            user_id=current_user.id,
            consent_type=consent_type,
            required_version=required_version
        )
        
        return jsonify({
            'has_consent': has_consent,
            'required_version': required_version,
            'consent_type': consent_type
        })
        
    except Exception as e:
        logging.error(f"Error checking consent: {e}")
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@app.route('/api/consent/record', methods=['POST'])
@login_required
def record_consent():
    """
    Record user consent for a specific disclaimer/terms.
    
    CRITICAL: This provides legal protection.
    Records exactly what they agreed to, when, and from where.
    """
    logging.info("")
    logging.info("📜" * 50)
    logging.info("📜 RECORDING CONSENT")
    logging.info("📜" * 50)
    
    try:
        data = request.get_json()
        consent_type = data.get('consent_type')  # 'analysis_disclaimer', 'terms', 'privacy'
        analysis_id = data.get('analysis_id')  # Optional - if consenting for specific analysis
        
        logging.info(f"📧 User: {current_user.email}")
        logging.info(f"🆔 User ID: {current_user.id}")
        logging.info(f"📋 Consent Type: {consent_type}")
        logging.info(f"🔍 Analysis ID: {analysis_id}")
        
        if not consent_type:
            logging.error(f"❌ No consent_type provided!")
            return jsonify({'error': 'consent_type required'}), 400
        
        # Get the current version and text
        consent_version = get_disclaimer_version(consent_type)
        consent_text = get_disclaimer_text(consent_type)
        
        logging.info(f"📄 Consent Version: {consent_version}")
        logging.info(f"📝 Consent Text Length: {len(consent_text) if consent_text else 0} chars")
        
        if not consent_version or not consent_text:
            logging.error(f"❌ Invalid consent type: {consent_type}")
            return jsonify({'error': 'Invalid consent type'}), 400
        
        # Get audit trail info
        ip_address = request.remote_addr
        user_agent = request.headers.get('User-Agent', '')
        
        logging.info(f"🌐 IP Address: {ip_address}")
        logging.info(f"💻 User Agent: {user_agent[:100]}...")
        logging.info(f"")
        logging.info(f"💾 Recording consent in database...")
        
        # Record the consent
        consent = ConsentRecord.record_consent(
            user_id=current_user.id,
            consent_type=consent_type,
            consent_version=consent_version,
            consent_text=consent_text,
            ip_address=ip_address,
            user_agent=user_agent,
            analysis_id=analysis_id
        )
        
        logging.info(f"")
        logging.info(f"✅✅✅ CONSENT RECORDED SUCCESSFULLY ✅✅✅")
        logging.info(f"📋 Consent ID: {consent.id}")
        logging.info(f"⏰ Consented At: {consent.consented_at}")
        logging.info(f"👤 User: {current_user.email}")
        logging.info(f"📄 Type: {consent_type}")
        logging.info(f"📌 Version: {consent_version}")
        
        # CRITICAL FIX: Auto-complete onboarding when all 3 consents are accepted
        logging.info("")
        logging.info("🔍 CHECKING IF ALL CONSENTS ARE NOW COMPLETE...")
        
        # Check if user now has all 3 required consents
        user_consents = ConsentRecord.query.filter_by(user_id=current_user.id).all()
        consent_types_given = set(c.consent_type for c in user_consents)
        required_consents = {'terms', 'privacy', 'analysis_disclaimer'}
        
        logging.info(f"   User has consents: {consent_types_given}")
        logging.info(f"   Required consents: {required_consents}")
        
        has_all_consents = required_consents.issubset(consent_types_given)
        logging.info(f"   Has all required? {has_all_consents}")
        
        if has_all_consents:
            # Check if onboarding columns exist
            has_onboarding_col = hasattr(current_user, 'onboarding_completed')
            
            if has_onboarding_col:
                if not current_user.onboarding_completed:
                    logging.info("")
                    logging.info("🎉" * 40)
                    logging.info("🎉 ALL CONSENTS COMPLETE - AUTO-MARKING ONBOARDING AS DONE")
                    logging.info("🎉" * 40)
                    
                    # Mark onboarding as complete
                    current_user.onboarding_completed = True
                    current_user.onboarding_completed_at = datetime.utcnow()
                    db.session.commit()
                    
                    logging.info(f"✅ onboarding_completed = True")
                    logging.info(f"✅ onboarding_completed_at = {current_user.onboarding_completed_at}")
                    logging.info(f"✅ User {current_user.email} will NOT see onboarding on next login")
                else:
                    logging.info(f"   ℹ️  User already marked as onboarding complete")
            else:
                logging.error("")
                logging.error("🚨 WARNING: onboarding_completed column does not exist!")
                logging.error("🚨 Cannot auto-complete onboarding - migration needed!")
                logging.error("🔧 Run: python migrate_add_onboarding.py")
        else:
            missing = required_consents - consent_types_given
            logging.info(f"   ℹ️  Still need: {missing}")
        
        logging.info("📜" * 50)
        logging.info("")
        
        return jsonify({
            'success': True,
            'consent_id': consent.id,
            'consented_at': consent.consented_at.isoformat(),
            'onboarding_complete': has_all_consents and has_onboarding_col
        })
        
    except Exception as e:
        logging.error("")
        logging.error("📜" * 50)
        logging.error(f"❌❌❌ ERROR RECORDING CONSENT ❌❌❌")
        logging.error(f"Error: {e}")
        logging.error(f"User: {current_user.email}")
        logging.error("📜" * 50)
        logging.error("")
        logging.exception(e)
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@app.route('/api/consent/accept', methods=['POST'])
@login_required
def accept_consent():
    """
    Accept consent - alias for record_consent for frontend compatibility.
    """
    return record_consent()


@app.route('/api/consent/text', methods=['GET'])
@app.route('/api/consent/get-text', methods=['GET'])
@login_required
def get_consent_text():
    """
    Get the current text and version for a consent type.
    
    Used to display disclaimers to users.
    PERSONALIZED with user's email and account information.
    """
    try:
        consent_type = request.args.get('consent_type', 'analysis_disclaimer')
        
        consent_text = get_disclaimer_text(consent_type)
        consent_version = get_disclaimer_version(consent_type)
        
        if not consent_text:
            return jsonify({'error': 'Invalid consent type'}), 400
        
        # PERSONALIZE with user info
        from datetime import datetime
        
        personalization_header = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGREEMENT PERSONALIZATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This agreement is being presented to and will be accepted by:

👤 Account Email: {current_user.email}
📧 Account ID: {current_user.id}
📅 Account Created: {current_user.created_at.strftime('%B %d, %Y at %I:%M %p UTC') if current_user.created_at else 'N/A'}
🔐 Authentication Method: {current_user.auth_provider.upper() if current_user.auth_provider else 'Email'}
📍 IP Address: {request.remote_addr}
🕒 Viewing Date: {datetime.utcnow().strftime('%B %d, %Y at %I:%M %p UTC')}

By accepting this agreement, YOU ({current_user.email}) acknowledge that you have
read, understood, and agree to be bound by the terms below.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"""
        
        # Prepend personalization to the consent text
        personalized_text = personalization_header + consent_text
        
        return jsonify({
            'consent_type': consent_type,
            'version': consent_version,
            'text': personalized_text,
            'user_email': current_user.email,
            'user_id': current_user.id
        })
        
    except Exception as e:
        logging.error(f"Error getting consent text: {e}")
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@app.route('/api/consent/history', methods=['GET'])
@login_required
def get_consent_history():
    """
    Get user's consent history.
    
    Shows what they've agreed to and when.
    Useful for settings page / transparency.
    """
    try:
        consents = ConsentRecord.query.filter_by(
            user_id=current_user.id,
            revoked=False
        ).order_by(ConsentRecord.consented_at.desc()).all()
        
        consent_list = []
        for consent in consents:
            consent_list.append({
                'id': consent.id,
                'type': consent.consent_type,
                'version': consent.consent_version,
                'consented_at': consent.consented_at.isoformat(),
                'ip_address': consent.ip_address
            })
        
        return jsonify({
            'consents': consent_list
        })
        
    except Exception as e:
        logging.error(f"Error getting consent history: {e}")
        logging.error(f'Internal error: {e}', exc_info=True); return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


# ============================================================================
# REFERRAL SYSTEM - Viral Growth Engine
# ============================================================================

@app.route('/api/referral/stats', methods=['GET'])
@login_required
def get_referral_stats():
    """Get user's referral statistics and dashboard data"""
    try:
        from referral_service import ReferralService
        
        # Check if referral columns exist (backwards compatibility)
        if not hasattr(current_user, 'referral_code'):
            return jsonify({
                'success': False,
                'error': 'Referral system not yet initialized. Please contact support.',
                'migration_needed': True
            }), 503
        
        stats = current_user.get_referral_stats()
        share_text = ReferralService.get_share_text(current_user)
        referral_url = ReferralService.get_referral_url(current_user)
        
        # Get tier information
        current_tier_info = ReferralService.get_tier_info(current_user.referral_tier)
        
        return jsonify({
            'success': True,
            'code': stats['code'],
            'referral_url': referral_url,
            'total_referrals': stats['total_referrals'],
            'current_tier': stats['current_tier'],
            'current_tier_info': current_tier_info,
            'credits_earned': stats['credits_earned'],
            'next_tier': stats['next_tier'],
            'referral_history': stats['referral_history'],
            'share_text': share_text,
            'all_tiers': REFERRAL_TIERS
        })
    except Exception as e:
        logging.error(f"Error getting referral stats: {e}")
        return jsonify({'success': False, 'error': 'An internal error occurred. Please try again.'}), 500


@app.route('/api/referral/validate-code', methods=['POST'])
def validate_referral_code():
    """Validate a referral code (public endpoint for signup flow)"""
    try:
        data = request.get_json()
        code = data.get('code', '').strip().upper()
        
        if not code:
            return jsonify({'valid': False, 'error': 'No code provided'})
        
        # Find user with this code
        referrer = User.query.filter_by(referral_code=code).first()
        
        if referrer:
            return jsonify({
                'valid': True,
                'referrer_name': referrer.name or referrer.email.split('@')[0],
                'bonus_credits': 2  # They'll get 2 free credits
            })
        else:
            return jsonify({'valid': False, 'error': 'Invalid referral code'})
            
    except Exception as e:
        logging.error(f"Error validating referral code: {e}")
        return jsonify({'valid': False, 'error': 'An internal error occurred. Please try again.'})


@app.route('/api/referral/regenerate-code', methods=['POST'])
@login_required
def regenerate_referral_code():
    """Regenerate user's referral code"""
    try:
        # Clear existing code
        current_user.referral_code = None
        db.session.commit()
        
        # Generate new code
        new_code = current_user.generate_referral_code()
        
        if new_code:
            return jsonify({
                'success': True,
                'code': new_code,
                'message': 'Referral code regenerated successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to generate new code'
            }), 500
            
    except Exception as e:
        logging.error(f"Error regenerating referral code: {e}")
        return jsonify({'success': False, 'error': 'An internal error occurred. Please try again.'}), 500


# ============================================================================
# NEGOTIATION TOOLKIT - Generate Professional Documents
# ============================================================================

@app.route('/api/generate-negotiation-document', methods=['POST'])
@login_required
def generate_negotiation_document():
    """
    Generate professional negotiation documents from analysis results.
    
    Transforms data into action:
    - Offer justification letters
    - Counteroffer response strategies  
    - Email templates for agents
    - Talking points
    
    This is what makes OfferWise indispensable - not just data, but action.
    """
    try:
        data = request.get_json()
        
        logging.info(f"🎯 Negotiation document request from user {current_user.id}")
        logging.info(f"📝 Document type: {data.get('document_type')}")
        logging.info(f"📍 Property: {data.get('property_address')}")
        logging.info(f"💰 Asking: ${data.get('asking_price')}, Offer: ${data.get('recommended_offer')}")
        
        # Required fields
        document_type = data.get('document_type')  # 'offer_letter', 'counteroffer', 'email', 'talking_points'
        analysis = data.get('analysis')  # Full analysis results
        
        if not document_type:
            logging.error("❌ Missing document_type")
            return jsonify({'error': 'Missing document_type field'}), 400
            
        if not analysis:
            logging.error("❌ Missing analysis data")
            return jsonify({'error': 'Missing analysis field'}), 400
        
        # Initialize toolkit
        toolkit = NegotiationToolkit()
        
        # Generate appropriate document
        if document_type == 'offer_letter':
            property_address = data.get('property_address', 'Property')
            asking_price = float(data.get('asking_price', 0))
            recommended_offer = float(data.get('recommended_offer', 0))
            buyer_name = data.get('buyer_name', 'Buyer')
            
            logging.info(f"📄 Generating offer letter for {property_address}")
            
            document = toolkit.generate_offer_justification_letter(
                analysis=analysis,
                property_address=property_address,
                asking_price=asking_price,
                recommended_offer=recommended_offer,
                buyer_name=buyer_name
            )
            
            logging.info(f"✅ Offer letter generated: {len(document.content)} chars")
            
        elif document_type == 'counteroffer':
            original_offer = float(data.get('original_offer', 0))
            seller_counteroffer = float(data.get('seller_counteroffer', 0))
            asking_price = float(data.get('asking_price', 0))
            recommended_offer = float(data.get('recommended_offer', 0))
            
            # Get repair costs from analysis
            risk_score = analysis.get('risk_score', {})
            repair_low = risk_score.get('total_repair_cost_low', 0)
            repair_high = risk_score.get('total_repair_cost_high', 0)
            repair_avg = (repair_low + repair_high) / 2
            
            document = toolkit.generate_counteroffer_response(
                original_offer=original_offer,
                seller_counteroffer=seller_counteroffer,
                asking_price=asking_price,
                recommended_offer=recommended_offer,
                repair_costs=repair_avg
            )
            
        elif document_type == 'email':
            property_address = data.get('property_address', 'Property')
            recommended_offer = float(data.get('recommended_offer', 0))
            key_points = data.get('key_points', [
                "Professional inspection identified necessary repairs",
                "Offer reflects fair market value given condition",
                "Ready to close quickly with financing in place"
            ])
            
            logging.info(f"✉️ Generating agent email for {property_address}")
            
            document = toolkit.generate_agent_email_template(
                property_address=property_address,
                recommended_offer=recommended_offer,
                key_points=key_points
            )
            
            logging.info(f"✅ Agent email generated: {len(document.content)} chars")
            
        elif document_type == 'talking_points':
            recommended_offer = float(data.get('recommended_offer', 0))
            
            logging.info(f"💬 Generating talking points")
            
            document = toolkit.generate_talking_points(
                analysis=analysis,
                recommended_offer=recommended_offer
            )
            
            logging.info(f"✅ Talking points generated: {len(document.content)} chars")
            
        else:
            logging.error(f"❌ Unknown document type: {document_type}")
            return jsonify({'error': f'Unknown document type: {document_type}'}), 400
        
        # Return generated document
        logging.info(f"✅ Returning document: {document.title}")
        return jsonify({
            'success': True,
            'document': {
                'title': document.title,
                'content': document.content,
                'type': document.document_type
            }
        })
        
    except Exception as e:
        logging.error(f"❌ Error generating negotiation document: {e}", exc_info=True)
        return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


# ============================================================================
# NEGOTIATION HUB (v5.54.68)
# Unified negotiation system: AI strategy + professional documents
# ============================================================================

@app.route('/api/negotiation/full-package', methods=['POST'])
@login_required
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


@app.route('/api/negotiation/strategy', methods=['POST'])
@login_required
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


@app.route('/api/negotiation/document', methods=['POST'])
@login_required
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


@app.route('/api/negotiation/tips', methods=['POST'])
@login_required
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
@app.route('/api/negotiation-coach', methods=['POST'])
@login_required
def get_negotiation_coaching_legacy():
    """Legacy endpoint - redirects to new unified API."""
    return get_negotiation_full_package()


@app.route('/api/negotiation-coach/quick-tips', methods=['POST'])
@login_required
def get_quick_negotiation_tips_legacy():
    """Legacy endpoint - redirects to new unified API."""
    return get_negotiation_tips()


# ============================================================================
# ADMIN - DATABASE HEALTH
# ============================================================================

@app.route('/api/admin/health-check', methods=['POST'])
@api_admin_required
def manual_health_check():
    """
    Manual database health check (optional)
    
    Automatically runs on startup, but can be triggered manually if needed.
    Only accessible to logged-in users for security.
    """
    try:
        logger.info(f"Manual health check triggered by user {current_user.id}")
        
        health_results = DatabaseHealth.check_and_fix_all(db)
        
        return jsonify({
            'status': 'success',
            'message': 'Health check completed',
            'results': health_results
        })
    except Exception as e:
        logger.error(f"Manual health check failed: {e}")
        return jsonify({
            'status': 'error',
            'message': 'An internal error occurred. Please try again.'
        }), 500

# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(413)
def request_entity_too_large(error):
    """Handle file size limit exceeded"""
    max_size_mb = app.config['MAX_CONTENT_LENGTH'] / (1024 * 1024)
    return jsonify({
        'success': False,
        'error': f'File too large. Maximum file size is {max_size_mb:.0f} MB.',
        'max_size_mb': max_size_mb,
        'suggestion': 'Please upload a smaller file or contact support for assistance with large documents.'
    }), 413

@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 - return JSON for API routes, HTML for pages"""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found', 'path': request.path}), 404
    return send_from_directory('static', '404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 - always return JSON for API routes, HTML for pages"""
    db.session.rollback()
    logging.error(f"500 Error on {request.path}: {error}")
    if request.path.startswith('/api/'):
        return jsonify({
            'error': 'Internal server error',
            'message': 'Something went wrong. Please try again.',
            'path': request.path
        }), 500
    return send_from_directory('static', '500.html'), 500

@app.errorhandler(Exception)
def handle_exception(error):
    """Catch-all exception handler - return JSON for API routes, HTML for pages"""
    import traceback
    db.session.rollback()
    logging.error(f"Unhandled exception on {request.path}: {error}")
    logging.error(traceback.format_exc())
    
    # SECURITY: Don't expose internal errors to users in production
    if request.path.startswith('/api/'):
        # Show real error to admin users for debugging
        admin_key = request.args.get('admin_key', '')
        is_admin_req = admin_key and admin_key == os.environ.get('ADMIN_API_KEY', '')
        error_detail = str(error) if is_admin_req else 'An unexpected error occurred. Please try again.'
        return jsonify({
            'error': 'Server error',
            'message': error_detail,
            'path': request.path
        }), 500
    return send_from_directory('static', '500.html'), 500

# ============================================================================
# STATIC PAGES
# ============================================================================

@app.route('/')
def index():
    """Landing page - captures referral codes from URL"""
    # Capture referral code from URL if present
    referral_code = request.args.get('ref')
    if referral_code:
        session['referral_code'] = referral_code.strip().upper()
        logging.info(f"🎁 Captured referral code from URL: {referral_code}")
    
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return send_from_directory('static', 'index.html')

@app.route('/app')
@login_required
def app_page():
    """Main application (protected)"""
    return send_from_directory('static', 'app.html')

# ============================================================================
# LEGACY ENDPOINTS (for backward compatibility)
# ============================================================================

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'offerwise-api',
        'version': '2.0.0',
        'authenticated': current_user.is_authenticated
    })


@app.route('/api/config/analytics', methods=['GET'])
def api_config_analytics():
    """Return GA4 measurement ID for client-side analytics loading."""
    ga4_id = os.environ.get('GA4_MEASUREMENT_ID', '')
    return jsonify({'ga4_id': ga4_id})


@app.route('/api/system-info', methods=['GET'])
@dev_only_gate
@api_admin_required
def system_info():
    """Check OCR availability and system dependencies"""
    import subprocess
    import sys
    from pdf_handler import OCR_AVAILABLE
    
    info = {
        'python_version': sys.version.split()[0],
        'ocr_python_packages': OCR_AVAILABLE,
        'tesseract_installed': False,
        'poppler_installed': False,
        'dependencies': {}
    }
    
    # Check Python packages
    try:
        import pytesseract
        info['dependencies']['pytesseract'] = 'installed'
    except ImportError:
        info['dependencies']['pytesseract'] = 'missing'
    
    try:
        import pdf2image
        info['dependencies']['pdf2image'] = 'installed'
    except ImportError:
        info['dependencies']['pdf2image'] = 'missing'
    
    try:
        from PIL import Image
        info['dependencies']['Pillow'] = 'installed'
    except ImportError:
        info['dependencies']['Pillow'] = 'missing'
    
    # Check system commands
    try:
        result = subprocess.run(['tesseract', '--version'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            info['tesseract_installed'] = True
            info['tesseract_version'] = result.stdout.split('\n')[0]
    except Exception as e:
        info['tesseract_installed'] = False
        info['tesseract_error'] = str(e)
    
    try:
        result = subprocess.run(['pdfinfo', '-v'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            info['poppler_installed'] = True
            info['poppler_version'] = result.stdout.split('\n')[0]
    except Exception as e:
        info['poppler_installed'] = False
        info['poppler_error'] = str(e)
    
    # Overall OCR availability
    info['ocr_fully_available'] = (
        info['dependencies'].get('pytesseract') == 'installed' and
        info['dependencies'].get('pdf2image') == 'installed' and
        info['dependencies'].get('Pillow') == 'installed' and
        info['tesseract_installed'] and
        info['poppler_installed']
    )
    
    # API configuration flags for cost dashboard
    info['stripe_configured'] = bool(os.environ.get('STRIPE_SECRET_KEY', ''))
    info['stripe_test_configured'] = bool(os.environ.get('STRIPE_TEST_SECRET_KEY', ''))
    info['webhook_configured'] = bool(os.environ.get('STRIPE_WEBHOOK_SECRET', ''))
    info['google_oauth_configured'] = bool(os.environ.get('GOOGLE_CLIENT_ID', ''))
    info['apple_configured'] = bool(os.environ.get('APPLE_CLIENT_ID', ''))
    info['facebook_configured'] = bool(os.environ.get('FACEBOOK_CLIENT_ID', ''))
    info['github_configured'] = bool(os.environ.get('GITHUB_CLIENT_ID', ''))
    info['google_ads_configured'] = bool(os.environ.get('GOOGLE_ADS_DEVELOPER_TOKEN', ''))
    info['ga4_configured'] = bool(os.environ.get('GA4_PROPERTY_ID', '') and os.environ.get('GOOGLE_ANALYTICS_KEY_JSON', ''))
    info['anthropic_configured'] = bool(os.environ.get('ANTHROPIC_API_KEY', ''))
    info['resend_configured'] = bool(os.environ.get('RESEND_API_KEY', ''))
    info['rentcast_configured'] = bool(os.environ.get('RENTCAST_API_KEY', ''))

    info['version'] = VERSION
    info['environment'] = os.environ.get('FLASK_ENV', 'production')
    try:
        db.session.execute(db.text('SELECT 1'))
        info['database_connected'] = True
    except Exception:
        info['database_connected'] = False

    return jsonify(info)


# ============================================================================
# 🧪 TURK TESTING MODE - Crowdsourced QA Tracking (v5.54.2)
# ============================================================================


@app.route('/api/system/analyze', methods=['POST'])
@api_admin_required
def analyze_system():
    """System Health Check - Check for issues and auto-file bugs"""
    
    issues = []
    checks_passed = []
    bugs_filed = []
    
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        
        # ===========================================
        # CHECK 1: Database Schema Consistency
        # ===========================================
        schema_issues = []
        
        # Get all table names (lowercase for comparison)
        all_tables = [t.lower() for t in inspector.get_table_names()]
        
        # Define expected columns for critical tables
        # Note: SQLAlchemy/Postgres may use different table names
        expected_schema = {
            'email_registry': ['saved_credits', 'credits_saved_at', 'has_received_free_credit', 'is_flagged_abuse'],
        }
        
        # Optional tables (don't error if missing, just note)
        optional_tables = {
            'bugs': ['ai_analysis', 'ai_suggested_fix', 'ai_confidence', 'ai_fix_approved'],
        }
        
        for table_name, expected_columns in expected_schema.items():
            # Check case-insensitively
            if table_name.lower() in all_tables:
                try:
                    actual_columns = [col['name'] for col in inspector.get_columns(table_name)]
                    missing = [col for col in expected_columns if col not in actual_columns]
                    if missing:
                        schema_issues.append({
                            'table': table_name,
                            'missing_columns': missing
                        })
                except Exception:
                    pass  # Table exists but can't read columns - not critical
            else:
                schema_issues.append({
                    'table': table_name,
                    'error': 'Table does not exist'
                })
        
        # Check optional tables (just warn, don't mark critical)
        for table_name, expected_columns in optional_tables.items():
            if table_name.lower() not in all_tables:
                issues.append({
                    'type': 'schema',
                    'severity': 'low',
                    'title': f"Optional table missing: {table_name}",
                    'description': f"Table {table_name} does not exist. This feature may not work until migration is run.",
                    'category': 'database'
                })
        
        if schema_issues:
            for issue in schema_issues:
                if 'missing_columns' in issue:
                    issues.append({
                        'type': 'schema',
                        'severity': 'critical',
                        'title': f"Missing columns in {issue['table']}",
                        'description': f"Table {issue['table']} is missing columns: {', '.join(issue['missing_columns'])}. This will cause runtime errors.",
                        'category': 'database'
                    })
                else:
                    issues.append({
                        'type': 'schema',
                        'severity': 'critical', 
                        'title': f"Missing table: {issue['table']}",
                        'description': f"Table {issue['table']} does not exist in database.",
                        'category': 'database'
                    })
        else:
            checks_passed.append('Database schema consistency')
        
        # ===========================================
        # CHECK 2: Environment Variables
        # ===========================================
        critical_env_vars = [
            ('GOOGLE_CLIENT_ID', 'Google OAuth won\'t work'),
            ('GOOGLE_CLIENT_SECRET', 'Google OAuth won\'t work'),
            ('DATABASE_URL', 'Database connection will fail'),
            ('SECRET_KEY', 'Sessions will be insecure'),
        ]
        
        optional_env_vars = [
            ('STRIPE_SECRET_KEY', 'Payments won\'t work'),
            ('ANTHROPIC_API_KEY', 'AI analysis won\'t work'),
        ]
        
        missing_critical = []
        missing_optional = []
        
        for var, impact in critical_env_vars:
            if not os.environ.get(var):
                missing_critical.append((var, impact))
        
        for var, impact in optional_env_vars:
            if not os.environ.get(var):
                missing_optional.append((var, impact))
        
        if missing_critical:
            for var, impact in missing_critical:
                issues.append({
                    'type': 'config',
                    'severity': 'critical',
                    'title': f"Missing env var: {var}",
                    'description': f"Environment variable {var} is not set. Impact: {impact}",
                    'category': 'api'
                })
        else:
            checks_passed.append('Critical environment variables')
        
        if missing_optional:
            for var, impact in missing_optional:
                issues.append({
                    'type': 'config',
                    'severity': 'medium',
                    'title': f"Missing optional env var: {var}",
                    'description': f"Environment variable {var} is not set. Impact: {impact}",
                    'category': 'api'
                })
        
        # ===========================================
        # CHECK 3: Database Connectivity
        # ===========================================
        try:
            result = db.session.execute(text("SELECT 1"))
            result.fetchone()
            checks_passed.append('Database connectivity')
        except Exception as e:
            issues.append({
                'type': 'database',
                'severity': 'critical',
                'title': 'Database connection failed',
                'description': f"Cannot connect to database: {str(e)}",
                'category': 'database'
            })
        
        # ===========================================
        # CHECK 4: Critical Tables Have Data
        # ===========================================
        try:
            user_count = User.query.count()
            if user_count == 0:
                issues.append({
                    'type': 'data',
                    'severity': 'low',
                    'title': 'No users in database',
                    'description': 'User table is empty. This may be expected for new deployments.',
                    'category': 'database'
                })
            else:
                checks_passed.append(f'User table has {user_count} users')
        except Exception as e:
            issues.append({
                'type': 'data',
                'severity': 'high',
                'title': 'Cannot query User table',
                'description': f"Error querying users: {str(e)}",
                'category': 'database'
            })
        
        # ===========================================
        # CHECK 5: Static Files Exist
        # ===========================================
        critical_static_files = [
            'static/login.html',
            'static/app.html',
            'static/dashboard.html',
            'static/onboarding.html',
        ]
        
        missing_files = []
        for filepath in critical_static_files:
            full_path = os.path.join(os.path.dirname(__file__), filepath)
            if not os.path.exists(full_path):
                missing_files.append(filepath)
        
        if missing_files:
            issues.append({
                'type': 'files',
                'severity': 'critical',
                'title': 'Missing static files',
                'description': f"Critical static files missing: {', '.join(missing_files)}",
                'category': 'ui'
            })
        else:
            checks_passed.append('Critical static files exist')
        
        # ===========================================
        # CHECK 6: Recent Errors in Bug Tracker
        # ===========================================
        try:
            critical_bugs = Bug.query.filter(
                Bug.status != 'fixed',
                Bug.severity == 'critical'
            ).count()
            
            if critical_bugs > 0:
                issues.append({
                    'type': 'bugs',
                    'severity': 'high',
                    'title': f'{critical_bugs} unfixed critical bugs',
                    'description': f"There are {critical_bugs} critical bugs that haven't been fixed yet.",
                    'category': 'other'
                })
            else:
                checks_passed.append('No unfixed critical bugs')
        except Exception:
            pass  # Bug table might not exist
        
        # ===========================================
        # AUTO-FILE BUGS for critical/high issues
        # ===========================================
        bug_table_exists = 'bugs' in all_tables
        
        for issue in issues:
            if issue['severity'] in ['critical', 'high'] and bug_table_exists:
                try:
                    # Check if similar bug already exists
                    existing = Bug.query.filter(
                        Bug.title == issue['title'],
                        Bug.status != 'fixed'
                    ).first()
                    
                    if not existing:
                        bug = Bug(
                            title=issue['title'],
                            description=issue['description'],
                            severity=issue['severity'],
                            category=issue.get('category', 'other'),
                            status='open',
                            reported_by='system_analysis'
                        )
                        db.session.add(bug)
                        db.session.commit()
                        bugs_filed.append({
                            'id': bug.id,
                            'title': bug.title,
                            'severity': bug.severity
                        })
                except Exception as e:
                    logging.error(f"Could not file bug '{issue['title']}': {e}")
                    db.session.rollback()
        
        # Note if bugs couldn't be filed due to missing table
        if not bug_table_exists and any(i['severity'] in ['critical', 'high'] for i in issues):
            issues.append({
                'type': 'system',
                'severity': 'medium',
                'title': 'Cannot auto-file bugs',
                'description': 'Bug table does not exist. Run database migration to enable bug tracking.',
                'category': 'database'
            })
        
        # ===========================================
        # SUMMARY
        # ===========================================
        critical_count = len([i for i in issues if i['severity'] == 'critical'])
        high_count = len([i for i in issues if i['severity'] == 'high'])
        
        return jsonify({
            'success': True,
            'summary': {
                'total_checks': len(checks_passed) + len(issues),
                'passed': len(checks_passed),
                'issues_found': len(issues),
                'critical': critical_count,
                'high': high_count,
                'bugs_filed': len(bugs_filed)
            },
            'checks_passed': checks_passed,
            'issues': issues,
            'bugs_filed': bugs_filed,
            'health': 'critical' if critical_count > 0 else 'warning' if high_count > 0 else 'healthy'
        })
        
    except Exception as e:
        logging.error(f"System analysis error: {e}")
        return jsonify({
            'success': False,
            'error': 'An internal error occurred. Please try again.'
        }), 500
@app.route('/admin-agent')
@admin_required
def admin_agent_page():
    """Legacy redirect — Reddit Agent merged into GTM dashboard"""
    admin_key = request.args.get('admin_key', '')
    return redirect(f'/admin/gtm?admin_key={admin_key}')


# ============================================================
# GTM (Go-To-Market) — Routes extracted to gtm/routes.py blueprint
# ============================================================
from gtm.routes import init_gtm_blueprint
init_gtm_blueprint(app, db, admin_required, api_admin_required)


@app.route('/auto-test-admin')
@app.route('/test-admin')
@app.route('/admin')
@admin_required
def test_admin_page():
    """Master Admin Dashboard — serves static/admin.html (v5.74.44)"""
    resp = send_from_directory(app.static_folder, 'admin.html')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp


@app.route('/bugs')
@app.route('/bug-tracker')
@admin_required
def bug_tracker_page():
    """Redirect to unified Test Admin page (v5.54.46)"""
    admin_key = request.args.get('admin_key', '')
    suffix = f'?admin_key={admin_key}' if admin_key else ''
    return redirect(f'/admin{suffix}#bugs')

# AUTOMATED TEST RUNNER (v5.54.16)
# =============================================================================

class SyntheticPropertyGenerator:
    """Generates synthetic property data for automated testing"""
    
    CALIFORNIA_CITIES = [
        ("San Jose", "95123"), ("San Francisco", "94102"), ("Los Angeles", "90001"),
        ("San Diego", "92101"), ("Palo Alto", "94301"), ("Mountain View", "94040"),
        ("Oakland", "94612"), ("Fremont", "94538"), ("Irvine", "92602"),
    ]
    
    STREET_NAMES = ["Oak", "Maple", "Cedar", "Pine", "Elm", "Main", "First", "Park", "Lake", "Hill"]
    STREET_TYPES = ["Street", "Avenue", "Drive", "Lane", "Court", "Way"]
    
    STRUCTURAL_ISSUES = [
        ("Foundation crack on north wall - structural damage", "critical", 12000),
        ("Minor settling damage in garage floor", "minor", 2500),
        ("Hairline cracks in drywall - cosmetic damage", "minor", 500),
    ]
    
    ROOF_ISSUES = [
        ("Roof shingles deteriorated and showing wear damage", "moderate", 3000),
        ("Missing shingles - roof damage evident", "major", 6000),
        ("Roof past useful life - needs replacement", "critical", 18000),
    ]
    
    PLUMBING_ISSUES = [
        ("Slow drainage in master bath - possible blockage", "minor", 300),
        ("Water heater deteriorated - 12 years old, needs replacement", "moderate", 2500),
        ("Galvanized pipes corroded - requires replacement", "major", 12000),
    ]
    
    ELECTRICAL_ISSUES = [
        ("Some outlets not grounded - electrical hazard", "moderate", 1500),
        ("Panel at capacity - inadequate for current needs", "moderate", 3500),
        ("Federal Pacific panel - known safety hazard, dangerous", "critical", 4500),
    ]
    
    HVAC_ISSUES = [
        ("HVAC system deteriorated - 15 years old, needs replacement", "moderate", 3000),
        ("AC not cooling efficiently - system failing", "minor", 500),
        ("Ductwork blockage - needs cleaning due to debris buildup", "minor", 400),
    ]
    
    DISCLOSED_ITEMS = [
        "Roof replaced in 2019",
        "Water heater replaced 2021", 
        "Foundation repair 2018 with warranty",
        "Previous termite treatment",
        "HVAC serviced annually",
        "Kitchen remodeled 2020 with permits",
    ]

    @classmethod
    def generate(cls, scenario="random"):
        """Generate a synthetic property"""
        import random
        
        city, zip_code = random.choice(cls.CALIFORNIA_CITIES)
        address = f"{random.randint(100, 9999)} {random.choice(cls.STREET_NAMES)} {random.choice(cls.STREET_TYPES)}, {city}, CA {zip_code}"
        
        base_prices = {"San Francisco": 1500000, "Palo Alto": 2500000, "Los Angeles": 1200000}
        base = base_prices.get(city, 1000000)
        price = base + random.randint(-200000, 400000)
        
        year_built = random.randint(1960, 2015)
        sqft = random.randint(1200, 3500)
        bedrooms = random.randint(2, 5)
        bathrooms = random.randint(1, 3)
        
        # Categorize issues by severity
        all_issues = cls.STRUCTURAL_ISSUES + cls.ROOF_ISSUES + cls.PLUMBING_ISSUES + cls.ELECTRICAL_ISSUES + cls.HVAC_ISSUES
        minor_issues = [i for i in all_issues if i[1] == 'minor']
        moderate_issues = [i for i in all_issues if i[1] == 'moderate']
        major_issues = [i for i in all_issues if i[1] == 'major']
        critical_issues = [i for i in all_issues if i[1] == 'critical']
        
        # Generate issues based on scenario - SELECT APPROPRIATE SEVERITY
        selected_issues = []
        
        if scenario == "clean":
            # Clean: 0-2 minor issues only, NO critical/major
            num_issues = random.randint(0, 2)
            pool = minor_issues + moderate_issues[:1]  # Mostly minor
            selected_issues = random.sample(pool, min(num_issues, len(pool)))
            
        elif scenario == "moderate":
            # Moderate: 2-4 minor/moderate issues, maybe 1 major, NO critical
            num_minor = random.randint(1, 3)
            num_major = random.randint(0, 1)
            selected_issues = random.sample(minor_issues + moderate_issues, min(num_minor, len(minor_issues + moderate_issues)))
            if num_major > 0 and major_issues:
                selected_issues += random.sample(major_issues, min(num_major, len(major_issues)))
                
        elif scenario == "problematic":
            # Problematic: mix of issues including 1-2 major, maybe 1 critical
            num_minor = random.randint(1, 2)
            num_major = random.randint(1, 2)
            num_critical = random.randint(0, 1)
            selected_issues = random.sample(minor_issues + moderate_issues, min(num_minor, len(minor_issues + moderate_issues)))
            if major_issues:
                selected_issues += random.sample(major_issues, min(num_major, len(major_issues)))
            if num_critical > 0 and critical_issues:
                selected_issues += random.sample(critical_issues, min(num_critical, len(critical_issues)))
                
        elif scenario == "nightmare":
            # Nightmare: 2+ critical issues, multiple major issues
            num_major = random.randint(2, 3)
            num_critical = random.randint(2, min(3, len(critical_issues)))
            selected_issues = random.sample(critical_issues, min(num_critical, len(critical_issues)))
            if major_issues:
                selected_issues += random.sample(major_issues, min(num_major, len(major_issues)))
            # Add some minor for realism
            if minor_issues:
                selected_issues += random.sample(minor_issues, min(2, len(minor_issues)))
        else:
            # Random: old behavior
            num_issues = random.randint(2, 6)
            selected_issues = random.sample(all_issues, min(num_issues, len(all_issues)))
        
        disclosed = random.sample(cls.DISCLOSED_ITEMS, random.randint(2, 5))
        
        return {
            "address": address,
            "price": price,
            "year_built": year_built,
            "sqft": sqft,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "issues": selected_issues,
            "disclosed": disclosed,
            "scenario": scenario,
        }
    
    @classmethod
    def generate_disclosure_text(cls, prop):
        """Generate synthetic disclosure document text"""
        lines = [
            "SELLER PROPERTY DISCLOSURE STATEMENT",
            "=" * 40,
            "",
            f"Property Address: {prop['address']}",
            f"Year Built: {prop['year_built']}",
            f"Square Footage: {prop['sqft']}",
            f"Bedrooms: {prop['bedrooms']} Bathrooms: {prop['bathrooms']}",
            "",
            "STRUCTURAL DISCLOSURE",
            "-" * 20,
            "Foundation issues: " + ("Yes - see inspection" if any("Foundation" in i[0] for i in prop['issues']) else "No known issues"),
            "Roof condition: " + ("Some wear noted" if any("Roof" in i[0] or "shingle" in i[0] for i in prop['issues']) else "Good condition"),
            "Water intrusion history: No",
            "",
            "SYSTEMS DISCLOSURE",
            "-" * 20,
            "HVAC: Operational, serviced regularly",
            "Plumbing: " + ("Minor issues noted" if any("Plumbing" in i[0] or "drain" in i[0] for i in prop['issues']) else "No known issues"),
            "Electrical: " + ("See inspection report" if any("Electrical" in i[0] or "outlet" in i[0] for i in prop['issues']) else "Up to code"),
            "",
            "KNOWN DEFECTS AND REPAIRS",
            "-" * 20,
        ]
        
        for item in prop['disclosed']:
            lines.append(f"- {item}")
        
        lines.extend([
            "",
            "ADDITIONAL DISCLOSURES",
            "-" * 20,
            "Property sold AS-IS",
            "Natural Hazard Zone: Zone X (minimal flood risk)",
            "No HOA",
            "",
            "Seller certifies the above information is true and correct.",
            f"Date: {datetime.now().strftime('%m/%d/%Y')}",
        ])
        
        return "\n".join(lines)
    
    @classmethod
    def generate_inspection_text(cls, prop):
        """Generate synthetic inspection report text"""
        total_cost = sum(i[2] for i in prop['issues'])
        critical = len([i for i in prop['issues'] if i[1] == 'critical'])
        major = len([i for i in prop['issues'] if i[1] == 'major'])
        
        lines = [
            "PROPERTY INSPECTION REPORT",
            "=" * 40,
            "",
            f"Property: {prop['address']}",
            f"Inspection Date: {datetime.now().strftime('%B %d, %Y')}",
            f"Report #: INS-{random.randint(10000, 99999)}",
            "",
            "EXECUTIVE SUMMARY",
            "-" * 20,
            f"Total items found: {len(prop['issues'])}",
            f"Critical issues: {critical}",
            f"Major issues: {major}",
            f"Estimated repair costs: ${total_cost:,}",
            "",
            "DETAILED FINDINGS",
            "-" * 20,
        ]
        
        for desc, severity, cost in prop['issues']:
            lines.append(f"[{severity.upper()}] {desc}")
            lines.append(f"  Estimated cost: ${cost:,}")
            lines.append(f"  Recommendation: {'Immediate repair required' if severity == 'critical' else 'Address before closing' if severity == 'major' else 'Monitor or repair as needed'}")
            lines.append("")
        
        if not prop['issues']:
            lines.append("No significant issues found.")
            lines.append("")
        
        lines.extend([
            "GENERAL OBSERVATIONS",
            "-" * 20,
            f"Property appears to be in {'poor' if critical > 1 else 'fair' if major > 0 else 'good'} overall condition.",
            f"Year built ({prop['year_built']}) is consistent with observed conditions.",
            "Recommend standard maintenance and monitoring.",
            "",
            "This report is based on visual inspection of accessible areas only.",
            f"Inspector License #: {random.randint(10000, 99999)}",
        ])
        
        return "\n".join(lines)


# =============================================================================
# ANALYTICS API (v5.54.48)
# =============================================================================

@app.route('/api/analytics')
@api_admin_required
def get_analytics():
    """Get analytics data for admin dashboard"""
    # Admin check handled by @api_admin_required decorator
    
    try:
        from sqlalchemy import func
        
        # User metrics
        total_users = User.query.count()
        
        # Users with analyses (have properties or usage records)
        users_with_properties = db.session.query(Property.user_id).distinct().count()
        
        # Count analyses per user via UsageRecord
        usage_stats = db.session.query(
            UsageRecord.user_id,
            func.sum(UsageRecord.properties_analyzed).label('total_analyses')
        ).group_by(UsageRecord.user_id).all()
        
        users_with_analyses = len([u for u in usage_stats if u.total_analyses and u.total_analyses > 0])
        repeat_users = len([u for u in usage_stats if u.total_analyses and u.total_analyses >= 2])
        power_users_count = len([u for u in usage_stats if u.total_analyses and u.total_analyses >= 3])
        
        # Total analyses
        total_analyses = db.session.query(func.sum(UsageRecord.properties_analyzed)).scalar() or 0
        avg_analyses = total_analyses / max(1, users_with_analyses) if users_with_analyses > 0 else 0
        
        # Users who actually completed a purchase (from CreditTransaction table)
        users_purchased = 0
        estimated_revenue = 0.0
        paying_users = 0
        paying_user_ids = set()
        try:
            from models import CreditTransaction
            completed_txns = CreditTransaction.query.filter(
                CreditTransaction.status == 'completed',
                CreditTransaction.credits > 0
            ).all()
            paying_user_ids = set(t.user_id for t in completed_txns)
            users_purchased = len(paying_user_ids)
            paying_users = users_purchased
            estimated_revenue = sum(t.amount or 0 for t in completed_txns)
        except Exception:
            # Fallback: stripe_customer_id presence (less accurate)
            stripe_fallback = User.query.filter(User.stripe_customer_id.isnot(None)).all()
            paying_user_ids = set(u.id for u in stripe_fallback)
            paying_users = len(paying_user_ids)
            users_purchased = paying_users
            estimated_revenue = 0.0

        # Active users (last 7 days)
        week_ago = datetime.now() - timedelta(days=7)
        active_7d = User.query.filter(User.last_login >= week_ago).count()

        # Power users list (3+ analyses OR actual paying customers)
        power_user_ids_list = [u.user_id for u in usage_stats if u.total_analyses and u.total_analyses >= 3]
        all_power_user_ids = set(power_user_ids_list) | paying_user_ids
        
        power_users_data = []
        for user_id in list(all_power_user_ids)[:20]:  # Limit to 20
            user = User.query.get(user_id)
            if user:
                # Get their analysis count
                user_usage = next((u for u in usage_stats if u.user_id == user_id), None)
                analyses_count = user_usage.total_analyses if user_usage and user_usage.total_analyses else 0
                
                power_users_data.append({
                    'email': user.email,
                    'analyses': analyses_count,
                    'credits': user.analysis_credits or 0,
                    'purchased': user.id in paying_user_ids,
                    'joined': user.created_at.isoformat() if user.created_at else None,
                    'last_login': user.last_login.isoformat() if user.last_login else None
                })
        
        # Sort by analyses count
        power_users_data.sort(key=lambda x: x['analyses'], reverse=True)
        
        # All users list
        all_users_data = []
        all_users = User.query.order_by(User.created_at.desc()).limit(100).all()
        for user in all_users:
            user_usage = next((u for u in usage_stats if u.user_id == user.id), None)
            analyses_count = user_usage.total_analyses if user_usage and user_usage.total_analyses else 0
            all_users_data.append({
                'email': user.email,
                'analyses': analyses_count,
                'credits': user.analysis_credits or 0,
                'tier': user.tier or 'free',
                'referral_code': getattr(user, 'referral_code', None) or '',
                'joined': user.created_at.isoformat() if user.created_at else None,
                'last_login': user.last_login.isoformat() if user.last_login else None
            })
        
        # Recent activity (last 7 days) - count by Analysis creation
        recent_activity = []
        for i in range(7):
            day = datetime.now() - timedelta(days=i)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            
            # Count properties created on this day
            try:
                day_analyses = Property.query.filter(
                    Property.created_at >= day_start,
                    Property.created_at < day_end
                ).count()
            except Exception:
                day_analyses = 0
            
            recent_activity.append({
                'date': day.strftime('%a %m/%d'),
                'analyses': day_analyses
            })
        
        recent_activity.reverse()  # Oldest to newest
        
        return jsonify({
            'users': {
                'total': total_users,
                'with_analyses': users_with_analyses or users_with_properties,
                'repeat': repeat_users,
                'power': power_users_count,
                'purchased': users_purchased,
                'active_7d': active_7d
            },
            'analyses': {
                'total': total_analyses,
                'avg_per_user': avg_analyses
            },
            'revenue': {
                'total': round(estimated_revenue, 2),
                'transactions': paying_users,
                'credits_purchased': 0,
                'avg_transaction': round(estimated_revenue / paying_users, 2) if paying_users else 0,
                'arpu': round(estimated_revenue / max(1, total_users), 2)
            },
            'power_users': power_users_data,
            'all_users': all_users_data,
            'recent_activity': recent_activity
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            'error': 'An internal error occurred. Please try again.',
            'trace': 'See server logs',
            'users': {'total': 0, 'with_analyses': 0, 'repeat': 0, 'power': 0, 'purchased': 0, 'active_7d': 0},
            'analyses': {'total': 0, 'avg_per_user': 0},
            'revenue': {'total': 0, 'transactions': 0, 'credits_purchased': 0, 'avg_transaction': 0, 'arpu': 0},
            'power_users': [],
            'all_users': [],
            'recent_activity': []
        })


# =============================================================================

# DEEP INSIGHTS PAGE (v5.62.74)
# =============================================================================

@app.route('/admin/insights')
@admin_required
def admin_insights_page():
    """Deep Insights Dashboard — advanced analytics from all telemetry"""
    admin_key = request.args.get('admin_key') or os.environ.get('TURK_ADMIN_KEY', '')
    return send_from_directory('static', 'admin-insights.html')

@app.route('/api/insights')
@api_admin_required
def get_insights():
    """Deep insights API — cohorts, funnels, patterns, system health"""
    try:
        from sqlalchemy import func, text
        from collections import defaultdict
        
        now = datetime.now()
        
        # ── Cohort Analysis: signups by week → retention ──────────────
        cohorts = []
        for weeks_ago in range(8):
            week_start = (now - timedelta(weeks=weeks_ago+1)).replace(hour=0, minute=0, second=0, microsecond=0)
            week_end = (now - timedelta(weeks=weeks_ago)).replace(hour=0, minute=0, second=0, microsecond=0)
            
            week_users = User.query.filter(
                User.created_at >= week_start,
                User.created_at < week_end
            ).all()
            
            signups = len(week_users)
            analyzed = 0
            returned = 0
            converted = 0
            
            for u in week_users:
                props = Property.query.filter_by(user_id=u.id).count()
                if props > 0:
                    analyzed += 1
                if props >= 2:
                    returned += 1
                if u.stripe_customer_id:
                    converted += 1
            
            cohorts.append({
                'week': week_start.strftime('%m/%d'),
                'signups': signups,
                'analyzed': analyzed,
                'returned': returned,
                'converted': converted,
                'activation_pct': round(analyzed / max(1, signups) * 100, 1),
                'retention_pct': round(returned / max(1, signups) * 100, 1),
                'conversion_pct': round(converted / max(1, signups) * 100, 1),
            })
        cohorts.reverse()
        
        # ── Analysis Patterns ─────────────────────────────────────────
        # Price distribution of analyzed properties
        price_buckets = defaultdict(int)
        all_props = Property.query.filter(Property.price.isnot(None), Property.price > 0).all()
        for p in all_props:
            if p.price < 200000:
                price_buckets['< $200K'] += 1
            elif p.price < 400000:
                price_buckets['$200-400K'] += 1
            elif p.price < 600000:
                price_buckets['$400-600K'] += 1
            elif p.price < 800000:
                price_buckets['$600-800K'] += 1
            elif p.price < 1000000:
                price_buckets['$800K-1M'] += 1
            else:
                price_buckets['$1M+'] += 1
        
        # Analysis success rate
        total_analyses = Analysis.query.count()
        completed = Analysis.query.filter_by(status='completed').count()
        failed = Analysis.query.filter_by(status='failed').count()
        
        # Risk tier distribution
        risk_tiers = defaultdict(int)
        for a in Analysis.query.filter(Analysis.risk_tier.isnot(None)).all():
            risk_tiers[a.risk_tier] += 1
        
        # ── Daily Activity (30 days) ─────────────────────────────────
        daily = []
        for days_ago in range(30):
            day = now - timedelta(days=days_ago)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            
            signups = User.query.filter(User.created_at >= day_start, User.created_at < day_end).count()
            analyses = Property.query.filter(Property.created_at >= day_start, Property.created_at < day_end).count()
            logins = User.query.filter(User.last_login >= day_start, User.last_login < day_end).count()
            
            daily.append({
                'date': day.strftime('%m/%d'),
                'signups': signups,
                'analyses': analyses,
                'logins': logins,
            })
        daily.reverse()
        
        # ── Document Stats ────────────────────────────────────────────
        total_docs = Document.query.count()
        doc_types = defaultdict(int)
        for d in Document.query.all():
            doc_types[d.document_type] += 1
        
        # ── Credit Economy ────────────────────────────────────────────
        total_credits_out = db.session.query(func.sum(User.analysis_credits)).scalar() or 0
        users_with_credits = User.query.filter(User.analysis_credits > 0).count()
        
        # ── System Health ─────────────────────────────────────────────
        bug_count = Bug.query.filter_by(status='open').count()
        
        # Integrity test status
        integrity_status = 'unknown'
        try:
            from integrity_tests import IntegrityTestEngine
            engine = IntegrityTestEngine(app=app, db=db)
            results = engine.run_all()
            s = results.get('summary', {})
            passed = s.get('passed', 0)
            total_tests = s.get('total', 0)
            integrity_status = f'{passed}/{total_tests}'
        except Exception:
            integrity_status = 'error'
        
        # ── Referral Network ──────────────────────────────────────────
        users_referred = User.query.filter(User.referred_by_code.isnot(None)).count()
        total_referral_credits = db.session.query(func.sum(User.referral_credits_earned)).scalar() or 0
        
        # ── Funnel: signup → upload → analyze → pay ───────────────────
        total_users = User.query.count()
        users_with_props = db.session.query(Property.user_id).distinct().count()
        users_with_analysis = db.session.query(Analysis.user_id).filter(Analysis.user_id.isnot(None)).distinct().count()
        users_paid = User.query.filter(User.stripe_customer_id.isnot(None)).count()
        
        funnel = {
            'signup': total_users,
            'upload': users_with_props,
            'analyze': users_with_analysis,
            'pay': users_paid,
            'upload_pct': round(users_with_props / max(1, total_users) * 100, 1),
            'analyze_pct': round(users_with_analysis / max(1, total_users) * 100, 1),
            'pay_pct': round(users_paid / max(1, total_users) * 100, 1),
        }
        
        return jsonify({
            'cohorts': cohorts,
            'daily': daily,
            'funnel': funnel,
            'analysis_patterns': {
                'price_distribution': dict(price_buckets),
                'total': total_analyses,
                'completed': completed,
                'failed': failed,
                'success_rate': round(completed / max(1, total_analyses) * 100, 1),
                'risk_tiers': dict(risk_tiers),
            },
            'documents': {
                'total': total_docs,
                'by_type': dict(doc_types),
            },
            'credits': {
                'total_outstanding': total_credits_out,
                'users_with_credits': users_with_credits,
            },
            'referrals': {
                'users_referred': users_referred,
                'credits_earned': total_referral_credits,
            },
            'system': {
                'open_bugs': bug_count,
                'integrity_tests': integrity_status,
            },
        })
    except Exception as e:
        import traceback
        logging.error(f"Insights API error: {traceback.format_exc()}")
        return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


# =============================================================================
# =============================================================================
# PMF SURVEY PAGE (v5.54.50)
# =============================================================================

@app.route('/survey')
@app.route('/feedback')
def survey_page():
    """PMF Survey page - standalone survey form"""
    return render_template_string('''
<!DOCTYPE html>
<html>
<head>
    <title>Quick Feedback - OfferWise</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); min-height: 100vh; color: #e2e8f0; padding: 20px; }
        .container { max-width: 600px; margin: 40px auto; }
        .card { background: #1e293b; padding: 32px; border-radius: 16px; box-shadow: 0 4px 24px rgba(0,0,0,0.3); }
        h1 { color: #60a5fa; margin-bottom: 8px; font-size: 28px; }
        .subtitle { color: #94a3b8; margin-bottom: 32px; }
        .question { margin-bottom: 28px; }
        .question-label { font-weight: 600; color: #f1f5f9; margin-bottom: 12px; display: block; font-size: 16px; }
        .required { color: #ef4444; }
        
        .radio-group { display: flex; flex-direction: column; gap: 10px; }
        .radio-option { display: flex; align-items: center; padding: 14px 16px; background: #334155; border-radius: 8px; cursor: pointer; transition: all 0.2s; border: 2px solid transparent; }
        .radio-option:hover { background: #475569; }
        .radio-option.selected { border-color: #60a5fa; background: rgba(96, 165, 250, 0.1); }
        .radio-option input { display: none; }
        .radio-dot { width: 20px; height: 20px; border: 2px solid #64748b; border-radius: 50%; margin-right: 12px; display: flex; align-items: center; justify-content: center; }
        .radio-option.selected .radio-dot { border-color: #60a5fa; }
        .radio-option.selected .radio-dot::after { content: ''; width: 10px; height: 10px; background: #60a5fa; border-radius: 50%; }
        .radio-text { flex: 1; }
        .radio-emoji { font-size: 24px; margin-right: 12px; }
        
        textarea { width: 100%; background: #0f172a; border: 1px solid #334155; color: #e2e8f0; padding: 14px; border-radius: 8px; font-size: 15px; resize: vertical; min-height: 80px; }
        textarea:focus { outline: none; border-color: #60a5fa; }
        
        .btn { width: 100%; padding: 16px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 16px; transition: all 0.2s; }
        .btn-primary { background: linear-gradient(135deg, #3b82f6, #8b5cf6); color: white; }
        .btn-primary:hover { opacity: 0.9; transform: translateY(-1px); }
        .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
        
        .success-message { text-align: center; padding: 40px 20px; }
        .success-icon { font-size: 64px; margin-bottom: 16px; }
        .success-title { font-size: 24px; color: #22c55e; margin-bottom: 8px; }
        .success-text { color: #94a3b8; }
        
        .hidden { display: none; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <div id="surveyForm">
                <h1>🎯 Quick Feedback</h1>
                <p class="subtitle">Help us make OfferWise better (takes 30 seconds)</p>
                
                <div class="question">
                    <label class="question-label">How would you feel if you could no longer use OfferWise? <span class="required">*</span></label>
                    <div class="radio-group" id="disappointmentGroup">
                        <label class="radio-option" onclick="selectOption(this, 'very')">
                            <input type="radio" name="disappointment" value="very">
                            <span class="radio-emoji">😢</span>
                            <div class="radio-dot"></div>
                            <span class="radio-text">Very disappointed</span>
                        </label>
                        <label class="radio-option" onclick="selectOption(this, 'somewhat')">
                            <input type="radio" name="disappointment" value="somewhat">
                            <span class="radio-emoji">😐</span>
                            <div class="radio-dot"></div>
                            <span class="radio-text">Somewhat disappointed</span>
                        </label>
                        <label class="radio-option" onclick="selectOption(this, 'not')">
                            <input type="radio" name="disappointment" value="not">
                            <span class="radio-emoji">😊</span>
                            <div class="radio-dot"></div>
                            <span class="radio-text">Not disappointed</span>
                        </label>
                    </div>
                </div>
                
                <div class="question">
                    <label class="question-label">What is the main benefit you get from OfferWise?</label>
                    <textarea id="mainBenefit" placeholder="e.g., Saves time reviewing documents, helps me negotiate better..."></textarea>
                </div>
                
                <div class="question">
                    <label class="question-label">How can we improve OfferWise for you?</label>
                    <textarea id="improvement" placeholder="What features would make it even better?"></textarea>
                </div>
                
                <button class="btn btn-primary" onclick="submitSurvey()" id="submitBtn">Submit Feedback</button>
            </div>
            
            <div id="successMessage" class="success-message hidden">
                <div class="success-icon">🙏</div>
                <div class="success-title">Thank you!</div>
                <p class="success-text">Your feedback helps us build a better product.</p>
            </div>
        </div>
    </div>
    
    <script>
        var selectedDisappointment = null;
        
        function selectOption(el, value) {
            document.querySelectorAll('.radio-option').forEach(function(opt) {
                opt.classList.remove('selected');
            });
            el.classList.add('selected');
            el.querySelector('input').checked = true;
            selectedDisappointment = value;
        }
        
        async function submitSurvey() {
            if (!selectedDisappointment) {
                alert('Please select how disappointed you would be.');
                return;
            }
            
            var btn = document.getElementById('submitBtn');
            btn.disabled = true;
            btn.textContent = 'Submitting...';
            
            try {
                var response = await fetch('/api/survey/pmf', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        disappointment: selectedDisappointment,
                        main_benefit: document.getElementById('mainBenefit').value,
                        improvement: document.getElementById('improvement').value,
                        trigger: 'standalone'
                    })
                });
                
                var data = await response.json();
                
                if (data.success) {
                    document.getElementById('surveyForm').classList.add('hidden');
                    document.getElementById('successMessage').classList.remove('hidden');
                } else {
                    alert('Error submitting survey. Please try again.');
                    btn.disabled = false;
                    btn.textContent = 'Submit Feedback';
                }
            } catch (err) {
                alert('Error: ' + err.message);
                btn.disabled = false;
                btn.textContent = 'Submit Feedback';
            }
        }
    </script>
</body>
</html>
''')


@app.route('/exit-survey')
def exit_survey_page():
    """Exit Survey page - for users who don't complete"""
    return render_template_string('''
<!DOCTYPE html>
<html>
<head>
    <title>Quick Question - OfferWise</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); min-height: 100vh; color: #e2e8f0; padding: 20px; }
        .container { max-width: 500px; margin: 40px auto; }
        .card { background: #1e293b; padding: 32px; border-radius: 16px; box-shadow: 0 4px 24px rgba(0,0,0,0.3); }
        h1 { color: #f59e0b; margin-bottom: 8px; font-size: 24px; }
        .subtitle { color: #94a3b8; margin-bottom: 28px; font-size: 15px; }
        
        .question { margin-bottom: 24px; }
        .question-label { font-weight: 600; color: #f1f5f9; margin-bottom: 12px; display: block; }
        
        .radio-group { display: flex; flex-direction: column; gap: 8px; }
        .radio-option { display: flex; align-items: center; padding: 12px 14px; background: #334155; border-radius: 8px; cursor: pointer; transition: all 0.2s; border: 2px solid transparent; }
        .radio-option:hover { background: #475569; }
        .radio-option.selected { border-color: #f59e0b; background: rgba(245, 158, 11, 0.1); }
        .radio-option input { display: none; }
        .radio-dot { width: 18px; height: 18px; border: 2px solid #64748b; border-radius: 50%; margin-right: 12px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
        .radio-option.selected .radio-dot { border-color: #f59e0b; }
        .radio-option.selected .radio-dot::after { content: ''; width: 8px; height: 8px; background: #f59e0b; border-radius: 50%; }
        .radio-emoji { font-size: 18px; margin-right: 10px; }
        
        textarea { width: 100%; background: #0f172a; border: 1px solid #334155; color: #e2e8f0; padding: 12px; border-radius: 8px; font-size: 14px; resize: vertical; min-height: 70px; }
        textarea:focus { outline: none; border-color: #f59e0b; }
        
        .btn { width: 100%; padding: 14px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 15px; transition: all 0.2s; margin-top: 8px; }
        .btn-primary { background: linear-gradient(135deg, #f59e0b, #d97706); color: white; }
        .btn-primary:hover { opacity: 0.9; }
        .btn-secondary { background: transparent; color: #64748b; border: 1px solid #334155; }
        .btn-secondary:hover { background: #334155; }
        
        .success-message { text-align: center; padding: 30px 20px; }
        .success-icon { font-size: 48px; margin-bottom: 12px; }
        .success-title { font-size: 20px; color: #22c55e; margin-bottom: 8px; }
        
        .hidden { display: none; }
        #otherReason { margin-top: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <div id="surveyForm">
                <h1>👋 Before you go...</h1>
                <p class="subtitle">Help us improve with a quick 15-second survey</p>
                
                <div class="question">
                    <label class="question-label">What stopped you from completing your analysis?</label>
                    <div class="radio-group" id="reasonGroup">
                        <label class="radio-option" onclick="selectReason(this, 'no_documents')">
                            <input type="radio" name="reason" value="no_documents">
                            <span class="radio-emoji">📄</span>
                            <div class="radio-dot"></div>
                            <span>I don't have my documents ready yet</span>
                        </label>
                        <label class="radio-option" onclick="selectReason(this, 'too_expensive')">
                            <input type="radio" name="reason" value="too_expensive">
                            <span class="radio-emoji">💰</span>
                            <div class="radio-dot"></div>
                            <span>The pricing doesn't work for me</span>
                        </label>
                        <label class="radio-option" onclick="selectReason(this, 'confusing')">
                            <input type="radio" name="reason" value="confusing">
                            <span class="radio-emoji">😕</span>
                            <div class="radio-dot"></div>
                            <span>I found it confusing to use</span>
                        </label>
                        <label class="radio-option" onclick="selectReason(this, 'found_alternative')">
                            <input type="radio" name="reason" value="found_alternative">
                            <span class="radio-emoji">🔄</span>
                            <div class="radio-dot"></div>
                            <span>I found another solution</span>
                        </label>
                        <label class="radio-option" onclick="selectReason(this, 'just_browsing')">
                            <input type="radio" name="reason" value="just_browsing">
                            <span class="radio-emoji">👀</span>
                            <div class="radio-dot"></div>
                            <span>Just browsing, not ready to buy</span>
                        </label>
                        <label class="radio-option" onclick="selectReason(this, 'other')">
                            <input type="radio" name="reason" value="other">
                            <span class="radio-emoji">💬</span>
                            <div class="radio-dot"></div>
                            <span>Other reason</span>
                        </label>
                    </div>
                    <textarea id="otherReason" class="hidden" placeholder="Please tell us more..."></textarea>
                </div>
                
                <div class="question">
                    <label class="question-label">What would bring you back?</label>
                    <textarea id="whatWouldHelp" placeholder="e.g., Lower price, easier upload, different features..."></textarea>
                </div>
                
                <button class="btn btn-primary" onclick="submitSurvey()" id="submitBtn">Submit & Continue</button>
                <button class="btn btn-secondary" onclick="skipSurvey()">Skip</button>
            </div>
            
            <div id="successMessage" class="success-message hidden">
                <div class="success-icon">🙏</div>
                <div class="success-title">Thanks for the feedback!</div>
                <p style="color: #94a3b8; margin-top: 12px;">We'll use this to make OfferWise better.</p>
            </div>
        </div>
    </div>
    
    <script>
        var selectedReason = null;
        
        function selectReason(el, value) {
            document.querySelectorAll('.radio-option').forEach(function(opt) {
                opt.classList.remove('selected');
            });
            el.classList.add('selected');
            el.querySelector('input').checked = true;
            selectedReason = value;
            
            // Show/hide other text field
            document.getElementById('otherReason').classList.toggle('hidden', value !== 'other');
        }
        
        async function submitSurvey() {
            var btn = document.getElementById('submitBtn');
            btn.disabled = true;
            btn.textContent = 'Submitting...';
            
            try {
                await fetch('/api/survey/exit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        exit_reason: selectedReason,
                        exit_reason_other: document.getElementById('otherReason').value,
                        exit_page: new URLSearchParams(window.location.search).get('from') || 'unknown',
                        what_would_help: document.getElementById('whatWouldHelp').value
                    })
                });
                
                document.getElementById('surveyForm').classList.add('hidden');
                document.getElementById('successMessage').classList.remove('hidden');
                
                setTimeout(function() {
                    window.location.href = '/';
                }, 2000);
                
            } catch (err) {
                btn.disabled = false;
                btn.textContent = 'Submit & Continue';
            }
        }
        
        function skipSurvey() {
            window.location.href = '/';
        }
    </script>
</body>
</html>
''')


# PMF SURVEY API (v5.54.50)
# =============================================================================

@app.route('/api/subscribe', methods=['POST'])
@limiter.limit("10 per hour")  # Prevent spam
def subscribe_email():
    """
    Email subscription for lead capture.
    Used for landing page email capture (free guide, updates, etc.)
    """
    try:
        data = request.get_json() or {}
        email = data.get('email', '').strip().lower()
        source = data.get('source', 'unknown')
        
        # Track email capture
        try:
            from funnel_tracker import track_from_request
            track_from_request('email_capture', request, metadata={'source': source})
        except Exception:
            pass
        
        if not email or '@' not in email:
            return jsonify({'error': 'Invalid email'}), 400
        
        # Check if already subscribed
        existing = Subscriber.query.filter_by(email=email).first()
        if existing:
            # Already subscribed — resend the guide
            try:
                send_negotiation_guide(email)
            except Exception as e:
                logging.warning(f"📧 Could not resend negotiation guide to {email}: {e}")
            return jsonify({'success': True, 'message': 'Subscribed'})
        
        # Add new subscriber
        subscriber = Subscriber(
            email=email,
            source=source
        )
        db.session.add(subscriber)
        db.session.commit()
        
        logging.info(f"📧 New subscriber: {email} from {source}")
        
        # Send negotiation guide email
        try:
            send_negotiation_guide(email)
        except Exception as e:
            logging.warning(f"📧 Could not send negotiation guide to {email}: {e}")
        
        return jsonify({'success': True, 'message': 'Subscribed'})
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Subscribe error: {e}")
        return jsonify({'error': 'Subscription failed'}), 500


# =============================================================================
# COMMUNITY WAITLIST (v5.59.65)
# =============================================================================


# =============================================================================
# UNSUBSCRIBE ROUTES (v5.61.0 — CAN-SPAM / CCPA compliant)
# =============================================================================


# =============================================================================
# WAITLIST ZIP BACKFILL (v5.62.84)
# =============================================================================


@app.route('/api/admin/backfill-waitlist-zips', methods=['POST'])
@api_admin_required
def backfill_waitlist_zips():
    """One-time backfill: extract ZIP from result_address for existing entries.

    Entries created before v5.62.83 have result_address but no result_zip.
    This parses the ZIP from the address string and updates the record so
    monthly nearby-listings drip emails can work for existing users.
    """
    import re as re_mod
    entries = Waitlist.query.filter(
        Waitlist.result_address.isnot(None),
        Waitlist.result_address != '',
        (Waitlist.result_zip.is_(None)) | (Waitlist.result_zip == '')
    ).all()

    updated = 0
    for entry in entries:
        m = re_mod.search(r'(\d{5})(?:-\d{4})?', entry.result_address or '')
        if m:
            entry.result_zip = m.group(1)
            parts = entry.result_address.split(',')
            if len(parts) >= 3:
                state_zip = parts[-1].strip()
                state_match = re_mod.match(r'([A-Z]{2})\s+\d{5}', state_zip)
                if state_match:
                    entry.result_state = state_match.group(1)
                entry.result_city = parts[-2].strip()
            elif len(parts) >= 2:
                state_zip = parts[-1].strip()
                state_match = re_mod.match(r'([A-Z]{2})\s+\d{5}', state_zip)
                if state_match:
                    entry.result_state = state_match.group(1)
            updated += 1

    if updated:
        db.session.commit()

    return jsonify({
        'total_missing': len(entries),
        'updated': updated,
        'message': f'Backfilled {updated} entries with ZIP codes from addresses.'
    })


@app.route('/api/admin/test-drip', methods=['POST'])
@api_admin_required
def test_drip_email():
    """Send a test drip email to the admin's own waitlist entry.
    
    Creates a waitlist entry for the admin if one doesn't exist,
    then sends the specified step (default: 1) immediately.
    Useful for verifying the email pipeline works end-to-end.
    """
    data = request.get_json() or {}
    step = data.get('step', 1)
    email = current_user.email
    
    entry = Waitlist.query.filter_by(email=email, feature='community').first()
    if not entry:
        from drip_campaign import generate_unsubscribe_token
        entry = Waitlist(
            email=email,
            feature='community',
            source='admin-test',
            had_result=True,
            result_address=data.get('address', ''),
            result_zip=data.get('zip', ''),
            result_city=data.get('city', ''),
            result_state=data.get('state', 'CA'),
        )
        entry.unsubscribe_token = generate_unsubscribe_token()
        db.session.add(entry)
        db.session.commit()
    
    from drip_campaign import send_drip_email
    success = send_drip_email(entry, step)
    db.session.commit()
    
    return jsonify({
        'success': success,
        'email': email,
        'step': step,
        'entry_id': entry.id,
        'message': f'Step {step} {"sent" if success else "failed"} to {email}'
    })


# =============================================================================
# DRIP CAMPAIGN CRON (v5.61.0)
# =============================================================================

@app.route('/api/cron/drip', methods=['POST'])
def run_drip_cron():
    """
    Process pending drip emails. Call every 15-30 minutes via:
    - Render Cron Job (recommended)
    - External cron service (e.g., cron-job.org) with shared secret
    - Manual trigger from admin panel

    Auth: Requires CRON_SECRET header or admin session.
    """
    # Authenticate: either cron secret or admin
    cron_secret = os.environ.get('CRON_SECRET', '')
    req_secret = request.headers.get('X-Cron-Secret', '')

    is_admin_user = current_user.is_authenticated and current_user.email in [
        'francis@piotnetworks.com', 'francis@getofferwise.ai'
    ]

    if not (cron_secret and req_secret == cron_secret) and not is_admin_user:
        return jsonify({'error': 'Unauthorized'}), 401

    from drip_campaign import run_drip_scheduler
    stats = run_drip_scheduler(db.session)

    return jsonify({'success': True, 'stats': stats})


# =============================================================================
# BUG TRACKER (v5.54.20)
# =============================================================================


@app.route('/api/cleanup/stale', methods=['POST'])
@api_admin_required
def cleanup_stale():
    """Clean up stale in-progress items (v5.54.59)"""
    try:
        from datetime import timedelta
        
        results = {
            'sessions_cleaned': 0,
            'bugs_cleaned': 0
        }
        
        # 1. Clean up stale test sessions (in progress > 1 hour)
        cutoff_sessions = datetime.now() - timedelta(hours=1)
        stale_sessions = TurkSession.query.filter(
            TurkSession.is_complete == False,
            TurkSession.started_at < cutoff_sessions
        ).all()
        
        for session in stale_sessions:
            session.is_complete = True
            session.completion_code = "STALE-CLEANUP"
            session.completed_at = datetime.now()
            results['sessions_cleaned'] += 1
        
        # 2. Clean up stale bugs (in progress > 7 days)
        cutoff_bugs = datetime.now() - timedelta(days=7)
        stale_bugs = Bug.query.filter(
            Bug.status == 'in_progress',
            Bug.created_at < cutoff_bugs
        ).all()
        
        for bug in stale_bugs:
            bug.status = 'fixed'
            bug.fix_notes = 'Auto-closed: stale in-progress > 7 days'
            bug.fixed_at = datetime.now()
            results['bugs_cleaned'] += 1
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "results": results,
            "message": f"Cleaned {results['sessions_cleaned']} sessions and {results['bugs_cleaned']} bugs"
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred. Please try again."}), 500


# =============================================================================
# AI BUG FIXER (v5.54.24)
# =============================================================================

def analyze_bug_with_ai(bug):
    """Use Claude API to analyze a bug and suggest a fix"""
    import anthropic
    
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY not configured"}
    
    client = anthropic.Anthropic(api_key=api_key)
    
    # Build context about the bug
    bug_context = f"""
BUG #{bug.id}: {bug.title}
Severity: {bug.severity}
Category: {bug.category}
Status: {bug.status}
Version: {bug.version_reported}
Reported by: {bug.reported_by}

DESCRIPTION:
{bug.description or 'No description provided'}

ERROR MESSAGE:
{bug.error_message or 'No error message'}

STACK TRACE:
{bug.stack_trace or 'No stack trace'}

STEPS TO REPRODUCE:
{bug.steps_to_reproduce or 'No steps provided'}
"""
    
    prompt = f"""You are an expert Python/Flask developer analyzing a bug report for OfferWise, a real estate analysis platform.

{bug_context}

Please analyze this bug and provide:

1. **ROOT CAUSE ANALYSIS**: What's likely causing this bug? Be specific.

2. **SUGGESTED FIX**: Provide the actual code fix. If you need to modify existing code, show the before/after. Be specific with file names and line numbers if you can infer them from the stack trace.

3. **CONFIDENCE LEVEL**: Rate your confidence in this fix as HIGH, MEDIUM, or LOW.

4. **TESTING RECOMMENDATION**: How should this fix be tested?

Format your response as:
---ANALYSIS---
[Your root cause analysis]

---FIX---
[Your code fix with file names]

---CONFIDENCE---
[HIGH/MEDIUM/LOW]

---TESTING---
[Testing recommendations]
"""
    
    try:
        _t0 = time.time()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        try:
            from ai_cost_tracker import track_ai_call as _track
            _track(response, "bug-ai-fix", (time.time() - _t0) * 1000)
        except Exception:
            pass
        
        response_text = response.content[0].text
        
        # Parse the response
        analysis = ""
        fix = ""
        confidence = "medium"
        
        if "---ANALYSIS---" in response_text:
            parts = response_text.split("---")
            for i, part in enumerate(parts):
                if "ANALYSIS" in part and i + 1 < len(parts):
                    analysis = parts[i + 1].strip()
                elif "FIX" in part and i + 1 < len(parts):
                    fix = parts[i + 1].strip()
                elif "CONFIDENCE" in part and i + 1 < len(parts):
                    conf_text = parts[i + 1].strip().upper()
                    if "HIGH" in conf_text:
                        confidence = "high"
                    elif "LOW" in conf_text:
                        confidence = "low"
                    else:
                        confidence = "medium"
        else:
            # Fallback if format not followed
            analysis = response_text
            fix = "See analysis above"
            confidence = "low"
        
        return {
            "success": True,
            "analysis": analysis,
            "fix": fix,
            "confidence": confidence
        }
        
    except Exception as e:
        return {"error": "An internal error occurred. Please try again."}


# Initialize database
with app.app_context():
    db.create_all()
    logging.info("Database initialized!")
    
    # Auto-migrate: Add missing columns to email_registry table
    # This ensures the database schema matches the model
    try:
        from sqlalchemy import text, inspect
        
        # Only run if we have a valid database connection
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        
        if tables and 'email_registry' in tables:
            existing_columns = [col['name'] for col in inspector.get_columns('email_registry')]
            
            migrations_run = []
            
            # Add saved_credits column if missing
            if 'saved_credits' not in existing_columns:
                try:
                    db.session.execute(text("ALTER TABLE email_registry ADD COLUMN saved_credits INTEGER DEFAULT 0 NOT NULL;"))
                    migrations_run.append('saved_credits')
                except Exception as col_err:
                    if 'already exists' not in str(col_err).lower():
                        logging.warning(f"Could not add saved_credits: {col_err}")
            
            # Add credits_saved_at column if missing
            if 'credits_saved_at' not in existing_columns:
                try:
                    db.session.execute(text("ALTER TABLE email_registry ADD COLUMN credits_saved_at TIMESTAMP;"))
                    migrations_run.append('credits_saved_at')
                except Exception as col_err:
                    if 'already exists' not in str(col_err).lower():
                        logging.warning(f"Could not add credits_saved_at: {col_err}")
            
            if migrations_run:
                db.session.commit()
                logging.info(f"Auto-migration: Added columns to email_registry: {migrations_run}")
        
        # Bug table AI columns migration
        if tables and 'bugs' in tables:
            bug_columns = [col['name'] for col in inspector.get_columns('bugs')]
            bug_migrations = []
            
            required_columns = [
                ("stack_trace", "TEXT"),
                ("ai_analysis", "TEXT"),
                ("ai_suggested_fix", "TEXT"),
                ("ai_confidence", "VARCHAR(20)"),
                ("ai_analyzed_at", "TIMESTAMP"),
                ("ai_fix_approved", "BOOLEAN DEFAULT FALSE"),
            ]
            
            for col_name, col_type in required_columns:
                if col_name not in bug_columns:
                    try:
                        db.session.execute(text(f"ALTER TABLE bugs ADD COLUMN {col_name} {col_type};"))
                        bug_migrations.append(col_name)
                    except Exception as col_err:
                        if 'already exists' not in str(col_err).lower():
                            logging.warning(f"Could not add {col_name}: {col_err}")
            
            if bug_migrations:
                db.session.commit()
                logging.info(f"Auto-migration: Added AI columns to bug: {bug_migrations}")
            
    except Exception as e:
        logging.warning(f"Auto-migration check (non-fatal): {e}")
        try:
            db.session.rollback()
        except Exception:
            pass

    # Seed repair cost database if empty
    try:
        from models import RepairCostZone
        zone_count = RepairCostZone.query.count()
        if zone_count == 0:
            from seed_repair_costs import seed_repair_cost_data
            seed_repair_cost_data(app)
            logging.info("💰 Repair cost database seeded on first run")
        else:
            logging.info(f"💰 Repair cost database: {zone_count} zones loaded")
    except Exception as e:
        logging.warning(f"💰 Repair cost seed skipped (non-fatal): {e}")

    # Ensure developer accounts have credits on startup
    try:
        # Uses global DEVELOPER_EMAILS
        dev_emails = DEVELOPER_EMAILS
        for dev_email in dev_emails:
            dev_user = User.query.filter_by(email=dev_email).first()
            if dev_user and dev_user.analysis_credits < 500:
                old = dev_user.analysis_credits
                dev_user.analysis_credits = 500
                dev_user.tier = 'enterprise'
                db.session.commit()
                logging.info(f"👑 Developer {dev_email}: credits {old} → 500")
    except Exception as e:
        logging.warning(f"👑 Developer credit boost skipped: {e}")

# ===== REPAIR COST ADMIN API =====

@app.route('/api/admin/repair-costs/zones')
@api_admin_required
def repair_cost_zones():
    """List all repair cost zones with optional state filter."""
    from models import RepairCostZone
    state = request.args.get('state')
    query = RepairCostZone.query
    if state:
        query = query.filter_by(state=state.upper())
    zones = query.order_by(RepairCostZone.zip_prefix).all()
    return jsonify({
        'count': len(zones),
        'zones': [z.to_dict() for z in zones],
    })

@app.route('/api/admin/repair-costs/zones/<zip_prefix>', methods=['PUT'])
@api_admin_required
def update_repair_cost_zone(zip_prefix):
    """Update a single zone's multiplier or metro name."""
    from models import RepairCostZone
    zone = RepairCostZone.query.filter_by(zip_prefix=zip_prefix).first()
    if not zone:
        return jsonify({'error': f'Zone {zip_prefix} not found'}), 404
    data = request.get_json(silent=True) or {}
    if 'cost_multiplier' in data:
        zone.cost_multiplier = float(data['cost_multiplier'])
    if 'metro_name' in data:
        zone.metro_name = str(data['metro_name'])[:100]
    db.session.commit()
    return jsonify({'updated': zone.to_dict()})

@app.route('/api/admin/repair-costs/baselines')
@api_admin_required
def repair_cost_baselines():
    """List all baseline repair costs."""
    from models import RepairCostBaseline
    baselines = RepairCostBaseline.query.order_by(
        RepairCostBaseline.category, RepairCostBaseline.severity
    ).all()
    return jsonify({
        'count': len(baselines),
        'baselines': [b.to_dict() for b in baselines],
    })

@app.route('/api/admin/repair-costs/baselines/<category>/<severity>', methods=['PUT'])
@api_admin_required
def update_repair_cost_baseline(category, severity):
    """Update a baseline cost range."""
    from models import RepairCostBaseline
    baseline = RepairCostBaseline.query.filter_by(
        category=category, severity=severity
    ).first()
    if not baseline:
        return jsonify({'error': f'Baseline {category}/{severity} not found'}), 404
    data = request.get_json(silent=True) or {}
    if 'cost_low' in data:
        baseline.cost_low = int(data['cost_low'])
    if 'cost_high' in data:
        baseline.cost_high = int(data['cost_high'])
    db.session.commit()
    return jsonify({'updated': baseline.to_dict()})

@app.route('/api/admin/set-credits', methods=['POST'])
@api_admin_required
def admin_set_credits():
    """Manually set credits for a user. POST { email, credits }"""
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    credits = data.get('credits', 0)
    if not email:
        return jsonify({'error': 'Email required'}), 400
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'error': f'User {email} not found'}), 404
    old = user.analysis_credits
    user.analysis_credits = int(credits)
    db.session.commit()
    logging.info(f"👑 Admin set credits: {email} {old} → {credits}")
    return jsonify({'success': True, 'email': email, 'old_credits': old, 'new_credits': int(credits)})

@app.route('/api/admin/inspect-analysis/<int:property_id>', methods=['GET'])
@app.route('/api/admin/inspect-analysis', methods=['GET'])
@api_admin_required
def admin_inspect_analysis(property_id=None):
    """Inspect raw saved analysis data for debugging. Use ?address=381+Tina to search by address."""
    if not property_id:
        addr_search = request.args.get('address', '')
        if addr_search:
            prop = Property.query.filter(Property.address.ilike(f'%{addr_search}%')).order_by(Property.created_at.desc()).first()
        else:
            # Show most recent analysis
            prop = Property.query.filter(Property.analyzed_at.isnot(None)).order_by(Property.analyzed_at.desc()).first()
    else:
        prop = Property.query.get(property_id)
    if not prop:
        return jsonify({'error': 'Property not found'}), 404
    analysis = Analysis.query.filter_by(property_id=property_id).order_by(Analysis.created_at.desc()).first()
    if not analysis:
        return jsonify({'error': 'No analysis found'}), 404
    result = json.loads(analysis.result_json)
    risk_score = result.get('risk_score', {})
    return jsonify({
        'property_id': property_id,
        'address': prop.address,
        'price': prop.price,
        'analyzed_at': prop.analyzed_at.isoformat() if prop.analyzed_at else None,
        'top_level_keys': list(result.keys()),
        'has_repair_estimate': 'repair_estimate' in result,
        'repair_estimate_keys': list(result.get('repair_estimate', {}).keys()) if 'repair_estimate' in result else [],
        'repair_breakdown_count': len(result.get('repair_estimate', {}).get('breakdown', [])),
        'has_findings': 'findings' in result,
        'findings_count': len(result.get('findings', [])),
        'has_category_scores': 'category_scores' in risk_score,
        'category_scores_count': len(risk_score.get('category_scores', [])),
        'category_scores_sample': risk_score.get('category_scores', [])[:3],
        'has_deal_breakers': 'deal_breakers' in risk_score,
        'deal_breakers_count': len(risk_score.get('deal_breakers', [])),
        'deal_breakers_sample': [{'cat': d.get('category', d.get('system', '?')), 'desc': str(d.get('explanation', d.get('description', '')))[:50]} for d in risk_score.get('deal_breakers', [])[:3]],
        'has_critical_issues': 'critical_issues' in result,
        'critical_issues_count': len(result.get('critical_issues', [])),
        'risk_score_keys': list(risk_score.keys()),
        'total_repair_low': risk_score.get('total_repair_cost_low', 0),
        'total_repair_high': risk_score.get('total_repair_cost_high', 0),
        'year_built': result.get('year_built'),
    })

@app.route('/api/admin/shared-analyses')
@api_admin_required
def admin_shared_analyses():
    """List all share links ever created, with user info and snapshot data.
    Supports ?search=<email|address>&days=<int>&page=<int>&per_page=<int>"""
    import json as json_mod
    from sqlalchemy import or_

    search = request.args.get('search', '').strip()
    days   = int(request.args.get('days', 0) or 0)
    page   = max(1, int(request.args.get('page', 1) or 1))
    per_pg = min(100, max(1, int(request.args.get('per_page', 50) or 50)))

    q = ShareLink.query.join(User, ShareLink.user_id == User.id)\
            .join(Property, ShareLink.property_id == Property.id)\
            .order_by(ShareLink.created_at.desc())

    if search:
        q = q.filter(or_(
            User.email.ilike(f'%{search}%'),
            Property.address.ilike(f'%{search}%'),
        ))
    if days > 0:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        q = q.filter(ShareLink.created_at >= cutoff)

    total  = q.count()
    links  = q.offset((page - 1) * per_pg).limit(per_pg).all()

    base_url = os.environ.get('BASE_URL', 'https://getofferwise.ai')
    results  = []
    for lnk in links:
        try:
            snap = json_mod.loads(lnk.snapshot_json)
        except Exception:
            snap = {}
        reactions = json_mod.loads(lnk.reactions_json) if lnk.reactions_json else []
        results.append({
            'token':          lnk.token,
            'share_url':      f"{base_url}/opinion/{lnk.token}",
            'is_active':      lnk.is_active,
            'is_expired':     not lnk.is_valid(),
            'user_email':     lnk.user.email,
            'user_id':        lnk.user_id,
            'property_id':    lnk.property_id,
            'address':        lnk.property.address,
            'sharer_name':    lnk.sharer_name,
            'recipient_name': lnk.recipient_name,
            'personal_note':  lnk.personal_note,
            'view_count':     lnk.view_count or 0,
            'reactions':      reactions,
            'snapshot':       snap,
            'created_at':     lnk.created_at.isoformat(),
            'expires_at':     lnk.expires_at.isoformat() if lnk.expires_at else None,
        })

    return jsonify({
        'total':    total,
        'page':     page,
        'per_page': per_pg,
        'pages':    max(1, (total + per_pg - 1) // per_pg),
        'links':    results,
    })


@app.route('/admin/shared-analyses')
@app.route('/admin/support-shares')
@admin_required
def admin_shared_analyses_page():
    """Admin support inbox — user-shared analyses with consent."""
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    return send_from_directory(static_dir, 'admin-support-shares.html')


@app.route('/admin/api-costs')
@admin_required
def admin_api_costs_page():
    """Admin API cost dashboard — all external API spend at a glance."""
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    return send_from_directory(static_dir, 'admin-api-costs.html')


@app.route('/admin/infra-costs')
@admin_required
def admin_infra_costs_page():
    """Admin infrastructure & dev costs — Francis's own monthly spend to build the platform."""
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    return send_from_directory(static_dir, 'admin-infra-costs.html')


@app.route('/api/admin/ai-costs')
@api_admin_required
def admin_ai_costs():
    """AI cost data — reads from DB (preferred) then falls back to flat log.
    ?days=1|7|30|0 (0=this month)."""
    from ai_cost_tracker import AICostTracker, get_cost_summary_from_db
    from datetime import date, timedelta
    import calendar

    days = int(request.args.get('days', 1) or 1)
    tracker = AICostTracker()

    # Try DB first — it survives Render restarts and captures all endpoints
    try:
        db_summary = get_cost_summary_from_db(days if days > 0 else 30)
        if db_summary and db_summary.get('total_calls', 0) > 0:
            db_summary['alerts'] = tracker.check_cost_alerts()
            db_summary['period'] = 'month' if days == 0 else (f'{days}d' if days > 1 else 'day')
            db_summary['data_source'] = 'database'
            return jsonify(db_summary)
    except Exception as db_e:
        logging.warning(f'DB cost summary failed, falling back to log: {db_e}')

    try:
        if days == 0:
            # This calendar month
            today = datetime.utcnow().date()
            data = tracker.get_monthly_cost(today.year, today.month)
            summary = {
                'total_calls': data['total_calls'],
                'total_cost': data['total_cost'],
                'total_input_tokens': 0,
                'total_output_tokens': 0,
                'avg_latency_ms': 0,
                'by_endpoint': {},
                'alerts': tracker.check_cost_alerts(),
                'period': 'month',
            }
        elif days == 1:
            summary = tracker.get_daily_summary()
            summary['alerts'] = tracker.check_cost_alerts()
            summary['period'] = 'day'
        else:
            # Aggregate multiple days
            today = datetime.utcnow().date()
            agg = {
                'total_calls': 0, 'total_cost': 0.0,
                'total_input_tokens': 0, 'total_output_tokens': 0,
                'latencies': [], 'by_endpoint': {}, 'violations': 0,
            }
            for i in range(days):
                d = today - timedelta(days=i)
                day_data = tracker.get_daily_summary(d)
                agg['total_calls'] += day_data['total_calls']
                agg['total_cost'] += day_data['total_cost']
                agg['total_input_tokens'] += day_data['total_input_tokens']
                agg['total_output_tokens'] += day_data['total_output_tokens']
                agg['violations'] += day_data.get('violations', 0)
                for ep, ep_data in day_data.get('by_endpoint', {}).items():
                    if ep not in agg['by_endpoint']:
                        agg['by_endpoint'][ep] = {'calls': 0, 'cost': 0.0, 'tokens': 0,
                                                   'input_tokens': 0, 'output_tokens': 0}
                    agg['by_endpoint'][ep]['calls'] += ep_data.get('calls', 0)
                    agg['by_endpoint'][ep]['cost'] += ep_data.get('cost', 0.0)
                    agg['by_endpoint'][ep]['tokens'] += ep_data.get('tokens', 0)
                    agg['by_endpoint'][ep]['input_tokens'] += ep_data.get('input_tokens', 0)
                    agg['by_endpoint'][ep]['output_tokens'] += ep_data.get('output_tokens', 0)
            summary = {
                'total_calls': agg['total_calls'],
                'total_cost': round(agg['total_cost'], 6),
                'total_input_tokens': agg['total_input_tokens'],
                'total_output_tokens': agg['total_output_tokens'],
                'avg_latency_ms': 0,
                'by_endpoint': agg['by_endpoint'],
                'violations': agg['violations'],
                'alerts': tracker.check_cost_alerts(),
                'period': f'{days}d',
            }
        return jsonify(summary)
    except Exception as e:
        logging.error(f'admin_ai_costs error: {e}')
        return jsonify({'error': 'Failed to load AI cost data.', 'total_cost': 0, 'total_calls': 0,
                        'by_endpoint': {}, 'alerts': []}), 200


@app.route('/api/admin/email-stats')
@api_admin_required
def admin_email_stats():
    """Email send counts from EmailSendLog (real per-send rows). ?days=1|7|30|0"""
    from datetime import timedelta
    days = int(request.args.get('days', 1) or 1)
    since = datetime.utcnow() - timedelta(days=days if days > 0 else 30)

    resend_configured = bool(os.environ.get('RESEND_API_KEY', ''))

    try:
        from models import EmailSendLog

        rows = EmailSendLog.query.filter(
            EmailSendLog.ts >= since,
            EmailSendLog.success == True
        ).all()

        total        = len(rows)
        welcome_sent = sum(1 for r in rows if r.email_type == 'welcome')
        drip_sent    = sum(1 for r in rows if (r.email_type or '').startswith('drip'))
        market_sent  = sum(1 for r in rows if r.email_type == 'market_intel')
        receipt_sent = sum(1 for r in rows if r.email_type == 'receipt')
        analysis_sent= sum(1 for r in rows if r.email_type == 'analysis_complete')
        other_sent   = total - welcome_sent - drip_sent - market_sent - receipt_sent - analysis_sent
        failed_total = EmailSendLog.query.filter(
            EmailSendLog.ts >= since,
            EmailSendLog.success == False
        ).count()

        return jsonify({
            'emails_sent':       total,
            'welcome_sent':      welcome_sent,
            'drip_sent':         drip_sent,
            'market_intel_sent': market_sent,
            'receipts_sent':     receipt_sent,
            'analysis_sent':     analysis_sent,
            'other_sent':        other_sent,
            'failed_total':      failed_total,
            'resend_configured': resend_configured,
            'data_source':       'EmailSendLog',
            'period_days':       days,
        })
    except Exception as e:
        logging.warning(f'EmailSendLog query failed ({e}), falling back to zero counts')
        return jsonify({
            'emails_sent': 0, 'welcome_sent': 0, 'drip_sent': 0,
            'market_intel_sent': 0, 'receipts_sent': 0, 'failed_total': 0,
            'resend_configured': resend_configured,
            'data_source': 'none — EmailSendLog table not yet migrated',
            'period_days': days,
        })


@app.route('/api/admin/email-engagement')
@api_admin_required
def admin_email_engagement():
    """Email open/click rates per drip step. Powers the drip effectiveness dashboard."""
    try:
        from models import EmailEvent, EmailSendLog
        from sqlalchemy import func

        # Get send counts per drip step
        sends = db.session.query(
            EmailSendLog.email_type,
            func.count(EmailSendLog.id).label('sent')
        ).filter(
            EmailSendLog.email_type.like('drip_%'),
            EmailSendLog.success == True
        ).group_by(EmailSendLog.email_type).all()

        send_map = {s.email_type: s.sent for s in sends}

        # Get event counts per resend_id, then join to email_type
        # Since EmailEvent has resend_id, we join to EmailSendLog to get email_type
        events = db.session.query(
            EmailSendLog.email_type,
            EmailEvent.event_type,
            func.count(func.distinct(EmailEvent.to_email)).label('unique_count')
        ).join(
            EmailEvent, EmailEvent.resend_id == EmailSendLog.resend_id
        ).filter(
            EmailSendLog.email_type.like('drip_%')
        ).group_by(
            EmailSendLog.email_type, EmailEvent.event_type
        ).all()

        # Build per-step engagement data
        steps = {}
        for email_type, event_type, count in events:
            if email_type not in steps:
                steps[email_type] = {'sent': send_map.get(email_type, 0)}
            steps[email_type][event_type] = count

        # Add steps with no events yet
        for email_type, sent in send_map.items():
            if email_type not in steps:
                steps[email_type] = {'sent': sent}

        # Calculate rates
        result = []
        for step_name in sorted(steps.keys()):
            data = steps[step_name]
            sent = data.get('sent', 0) or 1
            result.append({
                'step': step_name,
                'sent': data.get('sent', 0),
                'delivered': data.get('delivered', 0),
                'opened': data.get('opened', 0),
                'clicked': data.get('clicked', 0),
                'bounced': data.get('bounced', 0),
                'open_rate': round(data.get('opened', 0) / sent * 100, 1),
                'click_rate': round(data.get('clicked', 0) / sent * 100, 1),
            })

        # Overall stats
        total_sent = sum(d.get('sent', 0) for d in steps.values())
        total_opened = sum(d.get('opened', 0) for d in steps.values())
        total_clicked = sum(d.get('clicked', 0) for d in steps.values())

        return jsonify({
            'steps': result,
            'totals': {
                'sent': total_sent,
                'opened': total_opened,
                'clicked': total_clicked,
                'open_rate': round(total_opened / max(1, total_sent) * 100, 1),
                'click_rate': round(total_clicked / max(1, total_sent) * 100, 1),
            }
        })

    except Exception as e:
        logging.error(f"Email engagement stats error: {e}")
        return jsonify({'steps': [], 'totals': {'sent': 0, 'opened': 0, 'clicked': 0, 'open_rate': 0, 'click_rate': 0}})


@app.route('/api/admin/analysis-stats')
@api_admin_required
def admin_analysis_stats():
    """Analysis counts, per-API call estimates, Stripe revenue. ?days=1|7|30|0"""
    from datetime import timedelta
    days = int(request.args.get('days', 1) or 1)
    since = datetime.utcnow() - timedelta(days=days if days > 0 else 30)

    try:
        completed = Analysis.query.filter(
            Analysis.created_at >= since,
            Analysis.status == 'completed'
        ).count()

        total_analyses = Analysis.query.filter(
            Analysis.created_at >= since
        ).count()

        # RentCast: Nearby Listings = 2 calls/snapshot (listings + markets)
        #           Full analysis via property_research_agent = 1 AVM call each
        rentcast_configured = bool(os.environ.get('RENTCAST_API_KEY', ''))
        rentcast_nearby_calls = 0
        try:
            from models import MarketSnapshot
            snapshots = MarketSnapshot.query.filter(
                MarketSnapshot.created_at >= since
            ).count()
            rentcast_nearby_calls = snapshots * 2  # listings + markets per snapshot
        except Exception:
            pass
        # Each completed analysis runs 2 RentCast calls:
        #   RentCastTool → /avm/value (property details + valuation + comps)
        #   MarketStatsTool → /markets (ZIP-level market stats)
        rentcast_analysis_calls = completed * 2
        rentcast_total_calls = rentcast_nearby_calls + rentcast_analysis_calls
        # RentCast API pricing tiers (as of 2026):
        #   Foundation $74/mo → 1k included, $0.20/req overage
        #   Growth $199/mo → 5k included, $0.06/req overage
        #   Scale $449/mo → 25k included, $0.03/req overage
        # We estimate at Growth overage rate ($0.06) as a reasonable assumption
        rentcast_cost_est = round(rentcast_total_calls * 0.06, 2)

        # Google Maps Geocoding: called ONLY from risk_check_engine (viral Risk Check tool).
        # The full analysis pipeline (property_research_agent) uses the Census Bureau geocoder (FREE).
        # So we count Risk Check completions from GTMFunnelEvent, not total_analyses.
        # Pricing: $5 per 1000 calls, $200/mo free credit (~40,000 free calls/mo)
        google_maps_configured = bool(os.environ.get('GOOGLE_MAPS_API_KEY', ''))
        google_maps_calls = 0
        try:
            from models import GTMFunnelEvent
            google_maps_calls = GTMFunnelEvent.query.filter(
                GTMFunnelEvent.stage == 'risk_check_complete',
                GTMFunnelEvent.created_at >= since,
            ).count()
        except Exception:
            pass
        google_maps_cost_est = round(max(0, google_maps_calls - 40000) * 0.005, 4)

        # WalkScore: called once per analysis via property_research_agent
        # Pricing: depends on plan — Professional ~$0.0025/call
        walkscore_configured = bool(os.environ.get('WALKSCORE_API_KEY', ''))
        walkscore_calls = completed  # only on completed analyses
        walkscore_cost_est = round(walkscore_calls * 0.0025, 4)

        # Google Cloud Vision: called for scanned PDFs when USE_GOOGLE_VISION=true
        # Pricing: $1.50 per 1000 pages
        google_vision_enabled = os.environ.get('USE_GOOGLE_VISION', 'false').lower() == 'true'
        google_vision_configured = bool(os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', ''))
        # Can't know exact page counts without a log — estimate conservatively
        # Average CA real estate doc packet: ~15 pages, ~60% are scanned
        vision_pages_est = int(completed * 15 * 0.60) if google_vision_enabled else 0
        google_vision_cost_est = round(vision_pages_est / 1000 * 1.50, 4)

        # AirNow (EPA): FREE — government API, no billing.
        # Called from BOTH: property_research_agent (1x per completed analysis)
        # AND risk_check_engine (1x per Risk Check run).
        airnow_configured = bool(os.environ.get('AIRNOW_API_KEY', ''))
        airnow_analysis_calls = completed  # 1 per completed analysis
        airnow_riskcheck_calls = google_maps_calls  # same count as Risk Check runs
        airnow_total_calls = airnow_analysis_calls + airnow_riskcheck_calls

        # GreatSchools API: called once per completed analysis via property_research_agent
        # Pricing: varies by plan — often ~$0/mo on legacy/educational; paid plans ~$0.25/call
        greatschools_configured = bool(os.environ.get('GREATSCHOOLS_API_KEY', ''))
        greatschools_calls = completed  # 1 per completed analysis (skipped if not configured)
        # Cost unknown without plan details — shown as configured/missing status only

        # Mailgun: used ONLY for market intelligence drip emails (monthly nearby listings emails)
        # This is a SEPARATE email provider from Resend — Resend handles all other emails.
        # EmailSendLog only tracks Resend sends; Mailgun sends are tracked separately here.
        # Pricing: Flex plan $0.80/1k after first 1k free/mo
        mailgun_configured = bool(os.environ.get('MAILGUN_API_KEY', ''))
        mailgun_calls = 0
        try:
            from models import EmailSendLog
            mailgun_calls = EmailSendLog.query.filter(
                EmailSendLog.ts >= since,
                EmailSendLog.email_type == 'market_intel',
                EmailSendLog.success == True,
            ).count()
        except Exception:
            pass
        # Note: market_intel emails sent via Mailgun are also logged to EmailSendLog
        # if the drip campaign was updated to use email_service.send_email().
        # Otherwise mailgun_calls is 0 (Mailgun sends bypass EmailSendLog).
        mailgun_cost_est = round(max(0, mailgun_calls - 1000) * 0.0008, 4)

        # Reddit OAuth: free API used for GTM content posting
        reddit_configured = bool(os.environ.get('REDDIT_CLIENT_ID', '') and os.environ.get('REDDIT_CLIENT_SECRET', ''))

        # Stripe revenue — try live API first, fall back to CreditTransaction DB
        stripe_revenue = None
        stripe_charges = None
        stripe_data_source = 'none'
        try:
            if stripe.api_key:
                import time as _time
                since_ts = int(since.timestamp())
                charges_list = stripe.Charge.list(
                    created={'gte': since_ts},
                    limit=100,
                )
                paid = [c for c in charges_list.auto_paging_iter()
                        if c.paid and not c.refunded and c.status == 'succeeded']
                stripe_revenue = sum(c.amount for c in paid) / 100.0  # cents → dollars
                stripe_charges = len(paid)
                stripe_data_source = 'stripe_api'
        except Exception as _se:
            logging.warning(f'Stripe API revenue query failed: {_se}')

        if stripe_data_source == 'none':
            try:
                from models import CreditTransaction
                txns = CreditTransaction.query.filter(
                    CreditTransaction.created_at >= since,
                    CreditTransaction.status == 'completed',
                    CreditTransaction.credits > 0
                ).all()
                stripe_revenue = sum(t.amount or 0 for t in txns)
                stripe_charges = len(txns)
                stripe_data_source = 'credit_transactions_db'
            except Exception:
                pass

        # Google Ads real spend from GTMAdPerformance (synced from Google Ads API)
        google_ads_spend = None
        google_ads_clicks = None
        google_ads_impressions = None
        google_ads_data_source = 'none'
        try:
            from models import GTMAdPerformance
            from sqlalchemy import func as _func
            ads_rows = GTMAdPerformance.query.filter(
                GTMAdPerformance.date >= since.date(),
                GTMAdPerformance.channel == 'google_ads'
            ).all()
            if ads_rows:
                google_ads_spend       = float(sum(r.spend or 0 for r in ads_rows))
                google_ads_clicks      = sum(r.clicks or 0 for r in ads_rows)
                google_ads_impressions = sum(r.impressions or 0 for r in ads_rows)
                google_ads_data_source = 'gtm_ad_performance_db'
        except Exception as _ae:
            logging.warning(f'Google Ads spend query failed: {_ae}')

        # Reddit Ads real spend from GTMAdPerformance
        reddit_ads_spend = None
        reddit_ads_clicks = None
        try:
            from models import GTMAdPerformance
            reddit_rows = GTMAdPerformance.query.filter(
                GTMAdPerformance.date >= since.date(),
                GTMAdPerformance.channel == 'reddit_ads'
            ).all()
            if reddit_rows:
                reddit_ads_spend  = float(sum(r.spend or 0 for r in reddit_rows))
                reddit_ads_clicks = sum(r.clicks or 0 for r in reddit_rows)
        except Exception:
            pass

        return jsonify({
            'completed_analyses': completed,
            'total_analyses': total_analyses,
            # RentCast
            'rentcast_configured': rentcast_configured,
            'rentcast_total_calls': rentcast_total_calls,
            'rentcast_nearby_calls': rentcast_nearby_calls,
            'rentcast_analysis_calls': rentcast_analysis_calls,
            'rentcast_cost_est': rentcast_cost_est,
            # Google Maps
            'google_maps_configured': google_maps_configured,
            'google_maps_calls': google_maps_calls,
            'google_maps_cost_est': google_maps_cost_est,
            # WalkScore
            'walkscore_configured': walkscore_configured,
            'walkscore_calls': walkscore_calls,
            'walkscore_cost_est': walkscore_cost_est,
            # Google Cloud Vision
            'google_vision_enabled': google_vision_enabled,
            'google_vision_configured': google_vision_configured,
            'vision_pages_est': vision_pages_est,
            'google_vision_cost_est': google_vision_cost_est,
            # AirNow (free — but track call volume from both pipelines)
            'airnow_configured': airnow_configured,
            'airnow_total_calls': airnow_total_calls,
            'airnow_analysis_calls': airnow_analysis_calls,
            'airnow_riskcheck_calls': airnow_riskcheck_calls,
            # GreatSchools
            'greatschools_configured': greatschools_configured,
            'greatschools_calls': greatschools_calls,
            # Mailgun (market intel drip emails only)
            'mailgun_configured': mailgun_configured,
            'mailgun_calls': mailgun_calls,
            'mailgun_cost_est': mailgun_cost_est,
            # Reddit OAuth
            'reddit_configured': reddit_configured,
            # Stripe (real API or DB fallback)
            'stripe_revenue': stripe_revenue,
            'stripe_charges': stripe_charges,
            'stripe_data_source': stripe_data_source,
            # Google Ads (real data from GTMAdPerformance)
            'google_ads_spend': google_ads_spend,
            'google_ads_clicks': google_ads_clicks,
            'google_ads_impressions': google_ads_impressions,
            'google_ads_data_source': google_ads_data_source,
            # Reddit Ads (real data from GTMAdPerformance)
            'reddit_ads_spend': reddit_ads_spend,
            'reddit_ads_clicks': reddit_ads_clicks,
            'period_days': days,
        })
    except Exception as e:
        logging.error(f'admin_analysis_stats error: {e}')
        return jsonify({'completed_analyses': 0, 'error': 'Failed to load analysis stats.'}), 200


@app.route('/api/admin/system-info')
@api_admin_required
def admin_system_info():
    """Environment config flags for the API cost dashboard."""
    return jsonify({
        'stripe_configured':        bool(os.environ.get('STRIPE_SECRET_KEY', '')),
        'stripe_test_configured':   bool(os.environ.get('STRIPE_TEST_SECRET_KEY', '')),
        'webhook_configured':       bool(os.environ.get('STRIPE_WEBHOOK_SECRET', '')),
        'resend_configured':        bool(os.environ.get('RESEND_API_KEY', '')),
        'rentcast_configured':      bool(os.environ.get('RENTCAST_API_KEY', '')),
        'anthropic_configured':     bool(os.environ.get('ANTHROPIC_API_KEY', '')),
        'google_oauth_configured':  bool(os.environ.get('GOOGLE_CLIENT_ID', '')),
        'google_ads_configured':    bool(os.environ.get('GOOGLE_ADS_DEVELOPER_TOKEN', '')),
        'ga4_configured':           bool(os.environ.get('GA4_PROPERTY_ID', '') and os.environ.get('GOOGLE_ANALYTICS_KEY_JSON', '')),
        'apple_configured':         bool(os.environ.get('APPLE_CLIENT_ID', '')),
        'facebook_configured':      bool(os.environ.get('FACEBOOK_CLIENT_ID', '')),
        'github_configured':        bool(os.environ.get('GITHUB_CLIENT_ID', '')),
        'walkscore_configured':     bool(os.environ.get('WALKSCORE_API_KEY', '')),
        'airnow_configured':        bool(os.environ.get('AIRNOW_API_KEY', '')),
        'google_maps_configured':   bool(os.environ.get('GOOGLE_MAPS_API_KEY', '')),
        'greatschools_configured':  bool(os.environ.get('GREATSCHOOLS_API_KEY', '')),
        'mailgun_configured':       bool(os.environ.get('MAILGUN_API_KEY', '')),
        'reddit_configured':        bool(os.environ.get('REDDIT_CLIENT_ID', '') and os.environ.get('REDDIT_CLIENT_SECRET', '')),
    })


# ---------------------------------------------------------------------------
# INFRASTRUCTURE & DEV COSTS — vendor + invoice CRUD
# ---------------------------------------------------------------------------

# Default vendors seeded on first access
_INFRA_DEFAULT_VENDORS = [
    dict(name='Render',          category='hosting',  logo_emoji='🚀', notes='Web service + PostgreSQL DB'),
    dict(name='Anthropic',       category='ai',       logo_emoji='🤖', notes='claude.ai subscription + Console API (dev usage)'),
    dict(name='Google Cloud',    category='platform', logo_emoji='☁️',  notes='Maps, Vision, GA4, Ads platform costs'),
    dict(name='Resend',          category='email',    logo_emoji='📧', notes='Transactional email — above free tier'),
    dict(name='RentCast',        category='data',     logo_emoji='🏠', notes='Property data API subscription'),
    dict(name='GitHub',          category='tooling',  logo_emoji='🐙', notes='Private repos / Teams plan'),
    dict(name='Namecheap',       category='domain',   logo_emoji='🌐', notes='Domain + DNS'),
    dict(name='WalkScore',       category='data',     logo_emoji='🚶', notes='Walk/Transit/Bike score API'),
    dict(name='Other',           category='other',    logo_emoji='💼', notes='Miscellaneous infrastructure'),
]

def _ensure_infra_vendors():
    """Seed default vendors if the table is empty."""
    from models import InfraVendor
    if InfraVendor.query.count() == 0:
        for v in _INFRA_DEFAULT_VENDORS:
            db.session.add(InfraVendor(**v))
        db.session.commit()


@app.route('/api/admin/infra/vendors', methods=['GET'])
@api_admin_required
def infra_vendors_list():
    """List all infra vendors."""
    from models import InfraVendor
    _ensure_infra_vendors()
    vendors = InfraVendor.query.order_by(InfraVendor.name).all()
    return jsonify([v.to_dict() for v in vendors])


@app.route('/api/admin/infra/vendors', methods=['POST'])
@api_admin_required
def infra_vendors_create():
    """Create a new vendor."""
    from models import InfraVendor
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Vendor name is required.'}), 400
    if InfraVendor.query.filter_by(name=name).first():
        return jsonify({'error': f'Vendor "{name}" already exists.'}), 409
    v = InfraVendor(
        name=name,
        category=data.get('category', 'other'),
        logo_emoji=data.get('logo_emoji', '💼'),
        notes=data.get('notes', ''),
    )
    db.session.add(v)
    db.session.commit()
    return jsonify(v.to_dict()), 201


@app.route('/api/admin/infra/vendors/<int:vid>', methods=['DELETE'])
@api_admin_required
def infra_vendors_delete(vid):
    """Delete a vendor (and all its invoices)."""
    from models import InfraVendor, InfraInvoice
    v = InfraVendor.query.get_or_404(vid)
    InfraInvoice.query.filter_by(vendor_id=vid).delete()
    db.session.delete(v)
    db.session.commit()
    return jsonify({'deleted': True})


@app.route('/api/admin/infra/invoices', methods=['GET'])
@api_admin_required
def infra_invoices_list():
    """List invoices. ?vendor_id=&year=&months=12"""
    from models import InfraInvoice, InfraVendor
    from datetime import date, timedelta
    _ensure_infra_vendors()
    q = InfraInvoice.query.join(InfraVendor)
    vid = request.args.get('vendor_id')
    if vid:
        q = q.filter(InfraInvoice.vendor_id == int(vid))
    months = int(request.args.get('months', 12))
    cutoff = date.today().replace(day=1)
    for _ in range(months - 1):
        cutoff = (cutoff - timedelta(days=1)).replace(day=1)
    q = q.filter(InfraInvoice.period_start >= cutoff)
    invoices = q.order_by(InfraInvoice.period_start.desc()).all()
    return jsonify([inv.to_dict() for inv in invoices])


@app.route('/api/admin/infra/invoices/summary', methods=['GET'])
@api_admin_required
def infra_invoices_summary():
    """Monthly totals + per-vendor breakdown. ?months=12"""
    from models import InfraInvoice, InfraVendor
    from datetime import date, timedelta
    from collections import defaultdict
    _ensure_infra_vendors()
    months = int(request.args.get('months', 12))
    cutoff = date.today().replace(day=1)
    for _ in range(months - 1):
        cutoff = (cutoff - timedelta(days=1)).replace(day=1)
    invoices = (InfraInvoice.query
                .join(InfraVendor)
                .filter(InfraInvoice.period_start >= cutoff)
                .order_by(InfraInvoice.period_start)
                .all())
    # Group by month
    by_month = defaultdict(lambda: {'total': 0.0, 'vendors': {}})
    vendor_totals = defaultdict(float)
    grand_total = 0.0
    for inv in invoices:
        month_key = inv.period_start.strftime('%Y-%m')
        by_month[month_key]['total'] += inv.amount_usd
        vname = inv.vendor.name
        by_month[month_key]['vendors'][vname] = by_month[month_key]['vendors'].get(vname, 0.0) + inv.amount_usd
        vendor_totals[vname] += inv.amount_usd
        grand_total += inv.amount_usd
    avg_monthly = grand_total / months if months > 0 else 0
    return jsonify({
        'by_month': dict(by_month),
        'vendor_totals': dict(vendor_totals),
        'grand_total': round(grand_total, 2),
        'avg_monthly': round(avg_monthly, 2),
        'months': months,
    })


@app.route('/api/admin/infra/invoices', methods=['POST'])
@api_admin_required
def infra_invoices_create():
    """Create or update an invoice. Accepts multipart/form-data (with optional PDF) or JSON."""
    from models import InfraInvoice
    import base64
    from datetime import date

    # Support both JSON and multipart
    if request.content_type and 'multipart' in request.content_type:
        vendor_id   = int(request.form.get('vendor_id', 0))
        period_start= request.form.get('period_start', '')
        period_end  = request.form.get('period_end', '')
        amount_usd  = float(request.form.get('amount_usd', 0))
        description = request.form.get('description', '')
        invoice_ref = request.form.get('invoice_ref', '')
    else:
        data        = request.get_json() or {}
        vendor_id   = int(data.get('vendor_id', 0))
        period_start= data.get('period_start', '')
        period_end  = data.get('period_end', '')
        amount_usd  = float(data.get('amount_usd', 0))
        description = data.get('description', '')
        invoice_ref = data.get('invoice_ref', '')

    if not vendor_id or not period_start or amount_usd <= 0:
        return jsonify({'error': 'vendor_id, period_start, and amount_usd > 0 are required.'}), 400

    try:
        ps = date.fromisoformat(period_start)
        pe = date.fromisoformat(period_end) if period_end else ps.replace(day=28)
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD.'}), 400

    # Upsert: if same vendor+period_start exists, update it
    existing = InfraInvoice.query.filter_by(vendor_id=vendor_id, period_start=ps).first()
    inv = existing or InfraInvoice(vendor_id=vendor_id, period_start=ps)
    inv.period_end  = pe
    inv.amount_usd  = round(amount_usd, 2)
    inv.description = description
    inv.invoice_ref = invoice_ref

    # Handle file upload
    if request.content_type and 'multipart' in request.content_type:
        f = request.files.get('invoice_file')
        if f and f.filename:
            raw = f.read()
            if len(raw) > 10 * 1024 * 1024:  # 10 MB limit
                return jsonify({'error': 'File too large (max 10 MB).'}), 400
            inv.pdf_data     = base64.b64encode(raw).decode('utf-8')
            inv.pdf_filename = secure_filename(f.filename)
            inv.pdf_mime     = f.content_type or 'application/octet-stream'

    if not existing:
        db.session.add(inv)
    db.session.commit()
    return jsonify(inv.to_dict()), 201


@app.route('/api/admin/infra/invoices/<int:iid>', methods=['DELETE'])
@api_admin_required
def infra_invoices_delete(iid):
    """Delete an invoice."""
    from models import InfraInvoice
    inv = InfraInvoice.query.get_or_404(iid)
    db.session.delete(inv)
    db.session.commit()
    return jsonify({'deleted': True})


@app.route('/api/admin/infra/invoices/<int:iid>/file', methods=['GET'])
@api_admin_required
def infra_invoices_file(iid):
    """Download/view the uploaded invoice file."""
    from models import InfraInvoice
    import base64
    from flask import Response
    inv = InfraInvoice.query.get_or_404(iid)
    if not inv.pdf_data:
        return jsonify({'error': 'No file attached to this invoice.'}), 404
    raw = base64.b64decode(inv.pdf_data)
    return Response(
        raw,
        mimetype=inv.pdf_mime or 'application/octet-stream',
        headers={'Content-Disposition': f'inline; filename="{inv.pdf_filename or "invoice"}"'}
    )


@app.route('/api/admin/repair-costs/seed', methods=['POST'])
@api_admin_required
def repair_cost_reseed():
    """Re-seed repair cost data from hardcoded values (overwrites DB)."""
    from seed_repair_costs import seed_repair_cost_data
    zones, baselines = seed_repair_cost_data(app)
    return jsonify({'seeded': True, 'zones': zones, 'baselines': baselines})

@app.route('/api/admin/repair-costs/estimate')
@api_admin_required
def repair_cost_test_estimate():
    """Test endpoint — generate a repair estimate for any ZIP."""
    from repair_cost_estimator import estimate_repair_costs
    zip_code = request.args.get('zip', '95120')
    result = estimate_repair_costs(
        zip_code=zip_code,
        findings=[
            {'category': 'foundation', 'severity': 'major', 'description': 'Test — foundation cracks'},
            {'category': 'hvac', 'severity': 'major', 'description': 'Test — aging HVAC'},
            {'category': 'plumbing', 'severity': 'moderate', 'description': 'Test — slow drains'},
            {'category': 'electrical', 'severity': 'minor', 'description': 'Test — old outlets'},
            {'category': 'roof', 'severity': 'moderate', 'description': 'Test — aging roof'},
        ],
        property_year_built=1975,
    )
    return jsonify(result)


# =============================================================================
# PHASE 4: PARSER TEST, ANONYMIZATION, DRE MONITOR (v5.62.25)
# =============================================================================

# Document repository: persistent disk in production, local fallback for dev
DOCREPO_DISK_PATH = os.environ.get('DOCREPO_PATH', '/var/data/docrepo')
DOCREPO_LOCAL_PATH = os.path.join(os.path.dirname(__file__), 'document_repo')


def _resolve_doc_path(doc):
    """
    Resolve the filesystem path for a document repo entry.
    Checks persistent disk first (production), then local repo (dev).
    """
    cat_dirs = {
        'inspection_report': 'inspection_reports',
        'disclosure_statement': 'disclosure_statements',
        'reference': 'reference_docs'
    }
    subdir = cat_dirs.get(doc.get('category'), '')
    filename = doc.get('filename', '')

    # Build candidate paths: persistent disk first, then local
    candidates = []
    for base in [DOCREPO_DISK_PATH, DOCREPO_LOCAL_PATH]:
        candidates.append(os.path.join(base, subdir, filename))
        candidates.append(os.path.join(base, 'html_reports', filename))
        # Check scribeware/homegauge/other subdirs
        for sub in ['scribeware', 'homegauge', 'other']:
            candidates.append(os.path.join(base, 'html_reports', sub, filename))
        candidates.append(os.path.join(base, filename))

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def _extract_text_from_html(filepath):
    """Extract readable text from an HTML inspection report."""
    import re as re_mod
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        html = f.read()
    # Remove script and style blocks
    html = re_mod.sub(r'<script[^>]*>.*?</script>', '', html, flags=re_mod.DOTALL | re_mod.IGNORECASE)
    html = re_mod.sub(r'<style[^>]*>.*?</style>', '', html, flags=re_mod.DOTALL | re_mod.IGNORECASE)
    # Remove tags, decode entities
    text = re_mod.sub(r'<[^>]+>', ' ', html)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
    text = text.replace('&#x27;', "'").replace('&quot;', '"')
    # Collapse whitespace
    text = re_mod.sub(r'\s+', ' ', text).strip()
    return text


# ================================================================
# PUBLIC DOCUMENT CRAWLER — Scan / Crawl / Corpus (v5.62.35)
# ================================================================


# =============================================================================
# BACKGROUND SCHEDULERS (v5.62.92)
# =============================================================================
# Two background jobs:
#   1. Drip campaign: every 15 minutes, process pending drip emails
#   2. Market intelligence: once daily, generate snapshots for active users
# Both run in daemon threads. Safe for single-worker Gunicorn.
# =============================================================================

def _start_background_schedulers():
    """Start recurring background tasks for drip emails, market intelligence, and ad sync."""
    import threading

    DRIP_INTERVAL = 15 * 60       # 15 minutes
    INTEL_INTERVAL = 6 * 60 * 60  # 6 hours (runs ~4x/day, deduped by date)
    ADS_INTERVAL = 6 * 60 * 60    # 6 hours (syncs yesterday's data, deduped by date)

    def _drip_tick():
        try:
            with app.app_context():
                from drip_campaign import run_drip_scheduler
                stats = run_drip_scheduler(db.session)
                sent = stats.get('sent', 0)
                if sent > 0:
                    logging.info(f"📧 Drip background: sent={sent} checked={stats.get('checked', 0)}")
        except Exception as e:
            logging.error(f"Drip background error: {e}")
        finally:
            t = threading.Timer(DRIP_INTERVAL, _drip_tick)
            t.daemon = True
            t.start()

    def _intel_tick():
        try:
            with app.app_context():
                from market_intelligence import run_nightly_intelligence
                stats = run_nightly_intelligence(db.session)
                logging.info(f"📊 Market intel: users={stats.get('users_processed', 0)} "
                             f"snapshots={stats.get('snapshots_created', 0)} "
                             f"alerts={stats.get('alerts_generated', 0)}")
        except Exception as e:
            logging.error(f"Market intel background error: {e}")
        finally:
            t = threading.Timer(INTEL_INTERVAL, _intel_tick)
            t.daemon = True
            t.start()

    def _ads_tick():
        try:
            with app.app_context():
                from google_ads_sync import is_configured, sync_to_db
                if is_configured():
                    result = sync_to_db(db.session)
                    logging.info(f"💰 Google Ads sync: {result.get('status')} "
                                 f"— {result.get('impressions', 0)} imp, "
                                 f"${result.get('spend', 0)} spend")
        except Exception as e:
            logging.error(f"Google Ads sync error: {e}")
        finally:
            t = threading.Timer(ADS_INTERVAL, _ads_tick)
            t.daemon = True
            t.start()

    # Drip: start after 60 seconds
    t1 = threading.Timer(60, _drip_tick)
    t1.daemon = True
    t1.start()
    logging.info("📧 Drip background scheduler started (every 15 min)")

    # Market intel: start after 5 minutes (give app time to warm up)
    # Market intel: PAUSED (v5.74.45) — biggest RentCast API consumer (~360 calls/month)
    # Generates snapshots and alerts, but alerts weren't being delivered (Mailgun bug).
    # Re-enable once there are paying users who want market alerts.
    # To re-enable: uncomment the 3 lines below.
    # t2 = threading.Timer(300, _intel_tick)
    # t2.daemon = True
    # t2.start()
    logging.info("📊 Market intelligence scheduler PAUSED (RentCast quota conservation)")

    # Google Ads sync: start after 10 minutes
    t3 = threading.Timer(600, _ads_tick)
    t3.daemon = True
    t3.start()
    logging.info("💰 Google Ads sync scheduler started (every 6 hours)")

    # Reddit auto-poster: check once per hour, posts at most once/day
    REDDIT_INTERVAL = 3600  # 1 hour

    def _reddit_post_tick():
        try:
            with app.app_context():
                from reddit_poster import is_configured, post_next_approved
                if is_configured():
                    result = post_next_approved(db.session)
                    if result and 'error' not in result:
                        logging.info(f"📮 Reddit auto-posted: {result.get('title', '?')}")
                    elif result and 'error' in result:
                        logging.warning(f"📮 Reddit auto-post failed: {result['error']}")
        except Exception as e:
            logging.error(f"Reddit auto-post error: {e}")
        finally:
            t = threading.Timer(REDDIT_INTERVAL, _reddit_post_tick)
            t.daemon = True
            t.start()

    t4 = threading.Timer(900, _reddit_post_tick)  # Start after 15 min
    t4.daemon = True
    t4.start()
    logging.info("📮 Reddit auto-poster scheduler started (hourly check)")


# Only start in production (not during testing or imports)
if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' or not app.debug:
    try:
        _start_background_schedulers()
    except Exception as e:
        logging.warning(f"Could not start background schedulers: {e}")


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
