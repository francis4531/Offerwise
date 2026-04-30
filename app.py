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
from models import db, User, Property, Document, Analysis, UsageRecord, FeatureEvent, IssueConfirmation, MagicLink, ConsentRecord, EmailRegistry, Referral, ReferralReward, Comparison, TurkSession, Bug, PMFSurvey, ExitSurvey, QuickFeedback, Subscriber, ShareLink, Waitlist, ListingPreference, MarketSnapshot, ContractorLead, ContractorLeadClaim, Inspector, InspectorReport, Contractor, REFERRAL_TIERS
from models import MLFindingLabel, MLContradictionPair, MLCooccurrenceBucket, PostCloseSurvey, MLTrainingRun  # ML training data tables
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
import threading as _threading
ocr_progress = {}
_ocr_progress_lock = _threading.Lock()


def _ocr_set(key, value):
    """Thread-safe write to ocr_progress."""
    with _ocr_progress_lock:
        ocr_progress[key] = value


def _ocr_update(key, updates):
    """Thread-safe update of a single ocr_progress entry."""
    with _ocr_progress_lock:
        if key in ocr_progress:
            ocr_progress[key].update(updates)


def _ocr_get(key, default=None):
    """Thread-safe read from ocr_progress."""
    with _ocr_progress_lock:
        return ocr_progress.get(key, default)


def _ocr_delete(key):
    """Thread-safe delete from ocr_progress."""
    with _ocr_progress_lock:
        ocr_progress.pop(key, None)

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

ADMIN_EMAIL  = os.environ.get('ADMIN_EMAIL',  'hello@getofferwise.ai')
ADMIN_EMAILS = [e.strip() for e in os.environ.get('ADMIN_EMAILS', ADMIN_EMAIL + ',francis@getofferwise.ai').split(',') if e.strip()]

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
    
    # Accept either TURK_ADMIN_KEY (legacy) or ADMIN_KEY (primary)
    expected_keys = [
        k for k in [
            os.environ.get('TURK_ADMIN_KEY'),
            os.environ.get('ADMIN_KEY'),
        ] if k
    ]
    # Only accept key if there are configured keys AND the key matches
    if admin_key and expected_keys and any(admin_key == k for k in expected_keys):
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
            # Smart redirect: inspector-only → inspector portal, contractor-only → contractor portal
            try:
                insp = Inspector.query.filter_by(user_id=user.id).first()
                credits = getattr(user, 'analysis_credits', 0) or 0
                if insp and credits == 0:
                    return (False, '/inspector-portal')
                contractor = Contractor.query.filter_by(email=user.email).first()
                if contractor and contractor.status == 'active' and credits == 0:
                    return (False, '/contractor-portal')
            except Exception:
                pass
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
_DEV_EMAILS_DEFAULT = os.environ.get('ADMIN_EMAILS', ADMIN_EMAIL)
# Persona / test account domains — excluded from all analytics and telemetry
TEST_EMAIL_DOMAINS = ('@persona.offerwise.ai', '@test.offerwise.ai')

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
    'pool_recycle': 180,         # Recycle connections every 3 min — reduces SSL stale-conn errors on Render
    'pool_size': 5,              # Keep 5 connections in pool
    'max_overflow': 10,          # Allow 10 more under load
    'pool_timeout': 30,          # Wait up to 30s for a connection from pool,
    # TCP keepalives applied only on PostgreSQL (not SQLite dev)
    **({'connect_args': {
        'connect_timeout': 10,
        'keepalives': 1,
        'keepalives_idle': 30,
        'keepalives_interval': 5,
        'keepalives_count': 3,
    }} if database_url.startswith('postgresql') else {})
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
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
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
        "script-src 'self' 'unsafe-inline' https://js.stripe.com https://accounts.google.com https://apis.google.com https://connect.facebook.net https://cdn.jsdelivr.net "
            "https://cdnjs.cloudflare.com https://unpkg.com https://client.crisp.chat "
            "https://www.googletagmanager.com https://www.google-analytics.com "
            "https://googleads.g.doubleclick.net https://*.doubleclick.net https://www.google.com "
            "https://www.redditstatic.com; "
        "script-src-elem 'self' 'unsafe-inline' https://js.stripe.com https://accounts.google.com https://apis.google.com https://connect.facebook.net https://cdn.jsdelivr.net "
            "https://cdnjs.cloudflare.com https://unpkg.com https://client.crisp.chat "
            "https://www.googletagmanager.com https://www.google-analytics.com "
            "https://googleads.g.doubleclick.net https://*.doubleclick.net https://www.google.com "
            "https://www.redditstatic.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://client.crisp.chat; "
        "font-src 'self' https://fonts.gstatic.com https://client.crisp.chat; "
        "img-src 'self' data: blob: https://*.googleusercontent.com https://*.facebook.com https://client.crisp.chat https://image.crisp.chat https://api.qrserver.com "
            "https://www.googletagmanager.com https://www.google-analytics.com https://www.google.com https://*.doubleclick.net https://www.googleadservices.com https://googleads.g.doubleclick.net "
            "https://www.redditstatic.com https://alb.reddit.com; "
        "connect-src 'self' https://api.stripe.com https://accounts.google.com https://client.crisp.chat wss://client.relay.crisp.chat wss://stream.relay.crisp.chat https://unpkg.com "
            "https://www.googletagmanager.com https://www.google-analytics.com https://analytics.google.com https://*.google-analytics.com https://*.analytics.google.com "
            "https://www.google.com https://*.doubleclick.net https://www.googleadservices.com https://googleads.g.doubleclick.net "
            "https://www.redditstatic.com https://alb.reddit.com https://*.reddit.com; "
        "frame-src 'self' https://js.stripe.com https://accounts.google.com https://www.facebook.com https://game.crisp.chat "
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
        '/api/auth/magic-link',  # Passwordless login (InterNACHI signup)
        '/api/paywall/reason',   # Paywall exit survey (anonymous-friendly)
        '/api/nearby-listings/public',  # Public nearby listings for free tools hub (rate-limited)
        '/api/quick-check',          # Public address check (rate-limited, no auth)
    ]
    
    if any(request.path.startswith(path) for path in exempt_paths):
        return
    
    # In development or testing, skip CSRF check
    if os.environ.get('FLASK_ENV') in ('development', 'testing'):
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

        # v5.80.49: Force-sync FB/ND community targets on every deploy
        try:
            from gtm.routes import _seed_fb_groups as _sfb, _seed_nd_neighborhoods as _snd
            _sfb()
            _snd()
            logger.info("✅ FB/ND community targets synced")
        except Exception as _e:
            logger.warning(f"FB/ND community sync skipped: {_e}")

        # v5.80.50: Wipe polluted reddit records (city/FB/ND names that leaked in)
        try:
            from models import GTMTargetSubreddit as _GTS
            from sqlalchemy import or_ as _or_sql
            _bad = _GTS.query.filter_by(platform='reddit').filter(
                _or_sql(
                    _GTS.name.like('%, CA%'),
                    _GTS.name.like('%Bay Area Real Estate%'),
                    _GTS.name.like('%Home Buyers%'),
                    _GTS.name.like('%Home Buyer%'),
                    _GTS.name.like('%California%'),
                    _GTS.name.like('%Los Angeles%'),
                    _GTS.name.like('%San Diego%'),
                    _GTS.name.like('%Investors%'),
                    _GTS.name.like('%Tips & Advice%'),
                    _GTS.name.like('%First-Time Home Buyer%'),
                    _GTS.name.like('%Deal Analysis%'),
                    _GTS.name.like('%Starting Out%'),
                    _GTS.name.like('%California RE%'),
                    _GTS.name.like('%Home Inspections%'),  # BP name, not reddit
                )
            ).all()
            if _bad:
                for _b in _bad:
                    db.session.delete(_b)
                db.session.commit()
                logger.info(f"✅ Removed {len(_bad)} polluted reddit community records")
            # Ensure reddit entries have URLs
            _reddit_subs = _GTS.query.filter_by(platform='reddit').all()
            _url_map = {
                'OfferWiseAI':      'https://www.reddit.com/r/OfferWiseAI',
                'HomeInspections':  'https://www.reddit.com/r/HomeInspections',
                'homebuying':       'https://www.reddit.com/r/homebuying',
                'RealEstateAdvice': 'https://www.reddit.com/r/RealEstateAdvice',
                'RealEstate':       'https://www.reddit.com/r/RealEstate',
                'bayarea':          'https://www.reddit.com/r/bayarea',
                'SanJose':          'https://www.reddit.com/r/SanJose',
            }
            for _s in _reddit_subs:
                if not _s.url and _s.name in _url_map:
                    _s.url = _url_map[_s.name]
            db.session.commit()
            logger.info("✅ Reddit community URLs updated")
        except Exception as _e:
            db.session.rollback()
            logger.warning(f"Reddit community cleanup skipped: {_e}")

        # CRITICAL: Stamp alembic immediately after create_all so the migration
        # script never runs on an existing DB (it would time out Render's 5-min limit)
        try:
            from alembic.config import Config as _AlembicConfig
            from alembic import command as _alembic_cmd
            from sqlalchemy import text as _stamp_text
            import os as _stamp_os
            _acfg_path = _stamp_os.path.join(_stamp_os.path.dirname(__file__), 'alembic.ini')
            if _stamp_os.path.exists(_acfg_path):
                _acfg = _AlembicConfig(_acfg_path)
                _db_url = _stamp_os.environ.get('DATABASE_URL','').replace('postgres://','postgresql://')
                if _db_url:
                    _acfg.set_main_option('sqlalchemy.url', _db_url)
                # Check if already stamped
                try:
                    with db.engine.connect() as _sc:
                        _sv = _sc.execute(_stamp_text("SELECT version_num FROM alembic_version LIMIT 1")).fetchone()
                    if _sv:
                        logger.info(f"✅ Alembic already stamped at {_sv[0]} — skipping")
                    else:
                        _alembic_cmd.stamp(_acfg, 'head')
                        logger.info("✅ Alembic stamped to head immediately after create_all")
                except Exception:
                    # alembic_version table doesn't exist yet — stamp it
                    _alembic_cmd.stamp(_acfg, 'head')
                    logger.info("✅ Alembic stamped to head (fresh DB)")
        except Exception as _se:
            logger.warning(f"⚠️ Early alembic stamp failed (non-fatal): {_se}")

        # ── MIGRATION FAST-PATH + SINGLE-WORKER LOCK ───────────────────────────
        # Stamp file on persistent disk: written after first successful migration.
        # File lock: ensures only ONE worker runs migrations even with preload_app=False.
        # Second worker waits up to 30s for stamp to appear, then skips safely.
        _MIGRATION_STAMP_PATH = '/var/data/db_migrated_v5.80.15'
        _MIGRATION_LOCK_PATH  = '/var/data/db_migration.lock'
        _skip_migrations = os.path.exists(_MIGRATION_STAMP_PATH)

        if _skip_migrations:
            logger.info("✅ DB migration stamp found — skipping ALTER TABLE checks")
        else:
            import fcntl as _fcntl, time as _mtime
            _lock_file = None
            _got_lock  = False
            try:
                os.makedirs('/var/data', exist_ok=True)
                _lock_file = open(_MIGRATION_LOCK_PATH, 'w')
                _fcntl.flock(_lock_file, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                _got_lock = True
                logger.info("🔄 Migration lock acquired — this worker will run schema checks")
            except (IOError, OSError):
                # Another worker has the lock — wait for stamp file
                logger.info("⏳ Migration lock busy — waiting for other worker to finish...")
                for _ in range(30):
                    _mtime.sleep(1)
                    if os.path.exists(_MIGRATION_STAMP_PATH):
                        break
                _skip_migrations = os.path.exists(_MIGRATION_STAMP_PATH)
                if _skip_migrations:
                    logger.info("✅ Migration stamp appeared — skipping ALTER TABLE checks")
                else:
                    logger.info("⚠️ Migration stamp not written by other worker — skipping to avoid deadlock")
                    _skip_migrations = True  # Skip rather than risk deadlock

        if not _skip_migrations:
            # Agentic monitoring tables (v5.75.92)
            try:
                from sqlalchemy import inspect as _sqlinsp
                _tables = _sqlinsp(db.engine).get_table_names()
                if 'property_watches' not in _tables:
                    from models import PropertyWatch
                    PropertyWatch.__table__.create(db.engine, checkfirst=True)
                    logger.info("✅ Created property_watches table")
                if 'agent_alerts' not in _tables:
                    from models import AgentAlert
                    AgentAlert.__table__.create(db.engine, checkfirst=True)
                    logger.info("✅ Created agent_alerts table")
            except Exception as _ate:
                logger.warning(f"Agentic table migration: {_ate}")

            # v5.76.47: feature_events table for engagement instrumentation
            try:
                from sqlalchemy import inspect as _sqlinsp_fe
                _fe_tables = _sqlinsp_fe(db.engine).get_table_names()
                if 'feature_events' not in _fe_tables:
                    from models import FeatureEvent, IssueConfirmation
                    FeatureEvent.__table__.create(db.engine, checkfirst=True)
                    logger.info("✅ Created feature_events table")
                    IssueConfirmation.__table__.create(db.engine, checkfirst=True)
                    logger.info("✅ Created issue_confirmations table")
            except Exception as _fe_err:
                logger.warning(f"feature_events table migration: {_fe_err}")

            # v5.80.40: feature_events — add meta + session_id columns if missing
            try:
                from sqlalchemy import inspect as _sqlinsp_fe2, text as _txt_fe2
                _fe2_cols = {c['name'] for c in _sqlinsp_fe2(db.engine).get_columns('feature_events')}
                _fe2_added = []
                if 'meta' not in _fe2_cols:
                    with db.engine.connect() as _conn:
                        _conn.execute(_txt_fe2(
                            "ALTER TABLE feature_events ADD COLUMN IF NOT EXISTS meta TEXT"
                        ))
                        _conn.commit()
                    _fe2_added.append('meta')
                if 'session_id' not in _fe2_cols:
                    with db.engine.connect() as _conn:
                        _conn.execute(_txt_fe2(
                            "ALTER TABLE feature_events ADD COLUMN IF NOT EXISTS session_id VARCHAR(128)"
                        ))
                        _conn.commit()
                    _fe2_added.append('session_id')
                if _fe2_added:
                    logger.info(f"✅ feature_events: added columns {_fe2_added}")
            except Exception as _fe2_err:
                logger.warning(f"feature_events column migration: {_fe2_err}")

            # v5.76.77: Add platform + target_group to gtm_subreddit_posts (multi-channel)
            try:
                from sqlalchemy import inspect as _sqli_gtm, text as _txt_gtm
                _gtm_cols = {c['name'] for c in _sqli_gtm(db.engine).get_columns('gtm_subreddit_posts')}
                _gtm_added = []
                if 'platform' not in _gtm_cols:
                    with db.engine.connect() as _conn:
                        _conn.execute(_txt_gtm(
                            "ALTER TABLE gtm_subreddit_posts ADD COLUMN IF NOT EXISTS platform VARCHAR(20) DEFAULT 'reddit'"
                        ))
                        _conn.execute(_txt_gtm(
                            "CREATE INDEX IF NOT EXISTS idx_gtm_posts_platform ON gtm_subreddit_posts(platform)"
                        ))
                        _conn.execute(_txt_gtm(
                            "UPDATE gtm_subreddit_posts SET platform = 'reddit' WHERE platform IS NULL"
                        ))
                        _conn.commit()
                    _gtm_added.append('platform')
                if 'target_group' not in _gtm_cols:
                    with db.engine.connect() as _conn:
                        _conn.execute(_txt_gtm(
                            "ALTER TABLE gtm_subreddit_posts ADD COLUMN IF NOT EXISTS target_group VARCHAR(200)"
                        ))
                        _conn.commit()
                    _gtm_added.append('target_group')
                if _gtm_added:
                    logger.info(f"✅ gtm_subreddit_posts: added columns {_gtm_added}")
            except Exception as _gtm_err:
                logger.warning(f"gtm_subreddit_posts migration: {_gtm_err}")

            # v5.75.93: Add new columns to property_watches
            try:
                from sqlalchemy import inspect as _sqli2, text as _txt2
                _cols = {c['name'] for c in _sqli2(db.engine).get_columns('property_watches')}
                _new_cols = {
                    'contractor_lead_id':    'INTEGER REFERENCES contractor_leads(id)',
                    'ghost_buyer_email':     'VARCHAR(255)',
                    'owned_by_professional': 'BOOLEAN DEFAULT FALSE',
                }
                with db.engine.connect() as _conn2:
                    for _col, _typedef in _new_cols.items():
                        if _col not in _cols:
                            _conn2.execute(_txt2(f'ALTER TABLE property_watches ADD COLUMN {_col} {_typedef}'))
                            _conn2.commit()
                            logger.info(f"✅ Added property_watches.{_col}")
            except Exception as _wm:
                logger.warning(f"PropertyWatch column migration: {_wm}")

            # v5.76+: Add agentic monitoring columns to property_watches
            try:
                from sqlalchemy import inspect as _sqli3, text as _txt3
                _pw_cols = {c['name'] for c in _sqli3(db.engine).get_columns('property_watches')}
                _agentic_cols = {
                    'last_comps_check_at':        'TIMESTAMP',
                    'last_permit_check_at':        'TIMESTAMP',
                    'last_earthquake_check_at':    'TIMESTAMP',
                    'last_price_check_at':         'TIMESTAMP',
                    'last_deadline_check_at':      'TIMESTAMP',
                    'offer_accepted_date':         'DATE',
                    'inspection_contingency_date': 'DATE',
                    'loan_contingency_date':       'DATE',
                    'appraisal_contingency_date':  'DATE',
                    'seller_response_deadline':    'DATE',
                    'repair_completion_deadline':  'DATE',
                    'close_of_escrow_date':        'DATE',
                }
                _agentic_added = []
                with db.engine.connect() as _conn3:
                    for _col, _typedef in _agentic_cols.items():
                        if _col not in _pw_cols:
                            _conn3.execute(_txt3(f'ALTER TABLE property_watches ADD COLUMN IF NOT EXISTS {_col} {_typedef}'))
                            _conn3.commit()
                            _agentic_added.append(_col)
                if _agentic_added:
                    logger.info(f"✅ property_watches agentic columns added: {_agentic_added}")
                else:
                    logger.info("✅ property_watches agentic columns already up to date")
            except Exception as _am:
                logger.warning(f"PropertyWatch agentic column migration: {_am}")
        
            # AUTO-MIGRATE: Schema sync now handled by Alembic/Flask-Migrate.
            # Manual column additions for backward compatibility are below.
        
            # Run automatic migrations for new features
            logger.info("🔄 Checking for database migrations...")

            # ── Alembic: migrations run via scripts/bootstrap_alembic.py in the
            # Dockerfile CMD, BEFORE gunicorn starts. That's the correct place
            # because migrations must run once, synchronously, before any
            # worker serves traffic.
            #
            # Previously this block stamped at head without running upgrade(),
            # which silently skipped every migration that added columns. That
            # caused the v5.86.67 fc_error/cd_error/rc_error column drift.
            #
            # If you need to run migrations from a local dev shell, use:
            #   DATABASE_URL=... python scripts/bootstrap_alembic.py
            # Or directly:
            #   alembic upgrade head
            logger.info("🔄 Alembic migrations are handled by bootstrap_alembic.py (pre-gunicorn)")

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

            # Agent + AgentShare tables (v5.75.78)
            try:
                from models import Agent, AgentShare
                Agent.__table__.create(db.engine, checkfirst=True)
                AgentShare.__table__.create(db.engine, checkfirst=True)
                logger.info("✅ Agent / AgentShare tables ready")
            except Exception as e:
                logger.error(f"❌ Agent table migration failed: {e}", exc_info=True)

            # v5.80.86: Flywheel schema — analyses.inspector_report_id, agent_shares.deal_closed_at,
            # contractor_job_completions table
            try:
                from sqlalchemy import inspect as _fw_insp, text as _fw_txt
                _fw_tables = _fw_insp(db.engine).get_table_names()

                # analyses: add inspector_report_id for loop-back notifications
                _a_cols = {c['name'] for c in _fw_insp(db.engine).get_columns('analyses')}
                with db.engine.connect() as _fw_conn:
                    if 'inspector_report_id' not in _a_cols:
                        _fw_conn.execute(_fw_txt(
                            'ALTER TABLE analyses ADD COLUMN IF NOT EXISTS inspector_report_id INTEGER'
                        ))
                        logger.info('✅ analyses.inspector_report_id added')
                    if 'agent_shares' in _fw_tables:
                        _as_cols = {c['name'] for c in _fw_insp(db.engine).get_columns('agent_shares')}
                        if 'deal_closed_at' not in _as_cols:
                            _fw_conn.execute(_fw_txt(
                                'ALTER TABLE agent_shares ADD COLUMN IF NOT EXISTS deal_closed_at TIMESTAMP'
                            ))
                            _fw_conn.execute(_fw_txt(
                                'ALTER TABLE agent_shares ADD COLUMN IF NOT EXISTS final_sale_price DOUBLE PRECISION'
                            ))
                            logger.info('✅ agent_shares.deal_closed_at + final_sale_price added')
                    _fw_conn.commit()

                # vanity_slug + photo_url on agents table
                with db.engine.connect() as _ag_conn:
                    try:
                        _ag_insp = _fw_insp(db.engine)
                        if 'agents' in _ag_insp.get_table_names():
                            _ag_cols = {col['name'] for col in _ag_insp.get_columns('agents')}
                            if 'vanity_slug' not in _ag_cols:
                                _ag_conn.execute(_fw_txt('ALTER TABLE agents ADD COLUMN IF NOT EXISTS vanity_slug VARCHAR(60)'))
                                _ag_conn.execute(_fw_txt('ALTER TABLE agents ADD COLUMN IF NOT EXISTS photo_url VARCHAR(500)'))
                                _ag_conn.commit()
                                logger.info('✅ agents.vanity_slug + photo_url added')
                    except Exception as _agme:
                        logger.warning(f'Agent migration skipped: {_agme}')

                from models import ContractorJobCompletion
                from sqlalchemy import inspect as _cjc_insp, text as _cjc_text
                _cjc_tables = _cjc_insp(db.engine).get_table_names()
                if 'contractor_job_completions' not in _cjc_tables:
                    with db.engine.connect() as _cjc_conn:
                        _cjc_conn.execute(_cjc_text("""
                            CREATE TABLE IF NOT EXISTS contractor_job_completions (
                                id SERIAL PRIMARY KEY,
                                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                                lead_id INTEGER REFERENCES contractor_leads(id),
                                claim_id INTEGER REFERENCES contractor_lead_claims(id),
                                contractor_id INTEGER NOT NULL REFERENCES contractors(id),
                                property_address VARCHAR(500),
                                zip_code VARCHAR(10),
                                won_job BOOLEAN NOT NULL,
                                final_price FLOAT,
                                work_completed VARCHAR(500),
                                permit_uploaded BOOLEAN DEFAULT FALSE,
                                permit_number VARCHAR(100),
                                original_estimate_low FLOAT,
                                original_estimate_high FLOAT,
                                variance_pct FLOAT
                            )
                        """))
                        _cjc_conn.execute(_cjc_text(
                            'CREATE INDEX IF NOT EXISTS ix_cjc_contractor ON contractor_job_completions(contractor_id)'
                        ))
                        _cjc_conn.execute(_cjc_text(
                            'CREATE INDEX IF NOT EXISTS ix_cjc_zip ON contractor_job_completions(zip_code)'
                        ))
                        _cjc_conn.commit()
                    logger.info('✅ contractor_job_completions table created')
                else:
                    logger.info('✅ contractor_job_completions table already exists')
            except Exception as _fw_err:
                logger.warning(f'Flywheel migration (non-fatal): {_fw_err}')

            # Inspector internachi columns (v5.80.4) — fixed commit in v5.80.15
            try:
                from sqlalchemy import inspect as _iw_insp, text as _iw_text
                _iw_cols = [c['name'] for c in _iw_insp(db.engine).get_columns('inspectors')]
                _iw_added = []
                with db.engine.connect() as _iw_conn:
                    for _col, _def in [('internachi_member_id','VARCHAR(50)'), ('internachi_verified','BOOLEAN DEFAULT FALSE')]:
                        if _col not in _iw_cols:
                            _iw_conn.execute(_iw_text(f'ALTER TABLE inspectors ADD COLUMN IF NOT EXISTS {_col} {_def}'))
                            _iw_added.append(_col)
                    _iw_conn.commit()
                if _iw_added:
                    logger.info(f'✅ inspectors columns added: {_iw_added}')
                else:
                    logger.info('✅ inspectors internachi columns already up to date')
            except Exception as _e:
                logger.warning(f'Inspector internachi migration: {_e}')

            # GTM: Fix wrong BiggerPockets URLs + ensure correct homebuying forums exist (v5.84.59)
            try:
                from models import GTMTargetSubreddit
                # Fix wrong forum URLs in existing rows
                wrong_bp = GTMTargetSubreddit.query.filter(
                    GTMTargetSubreddit.platform == 'biggerpockets'
                ).all()
                for t in wrong_bp:
                    if t.url and 'forums/52' in t.url:
                        t.url = 'https://www.biggerpockets.com/forums/903'
                        t.name = 'First-Time Home Buyer'
                        t.notes = 'Highest intent BP forum — first time buyers'
                        logger.info(f'Fixed BP target URL: {t.name}')
                # Upsert correct forums — update URL if exists, insert if not
                correct_bp = [
                    dict(name='First-Time Home Buyer', platform='biggerpockets', priority=1,
                         url='https://www.biggerpockets.com/forums/903',
                         notes='Highest intent BP forum — first time buyers'),
                    dict(name='Real Estate Deal Analysis', platform='biggerpockets', priority=2,
                         url='https://www.biggerpockets.com/forums/88',
                         notes='Deal analysis — buyers asking about property condition'),
                ]
                for bp in correct_bp:
                    existing = GTMTargetSubreddit.query.filter_by(
                        name=bp['name'], platform='biggerpockets'
                    ).first()
                    if existing:
                        existing.url = bp['url']
                        existing.notes = bp['notes']
                    else:
                        db.session.add(GTMTargetSubreddit(**bp))
                        logger.info(f"Added BP target: {bp['name']}")
                db.session.commit()
                logger.info('✅ GTM BiggerPockets targets verified')
            except Exception as _gtm_e:
                db.session.rollback()
                logger.warning(f'GTM BP migration: {_gtm_e}')

            # GTMFunnelEvent amount_usd column (v5.75.71 — CAC:LTV tracking)
            try:
                from sqlalchemy import inspect as _fe_insp
                _fe_cols = [c['name'] for c in _fe_insp(db.engine).get_columns('gtm_funnel_events')]
                if 'amount_usd' not in _fe_cols:
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE gtm_funnel_events ADD COLUMN amount_usd DOUBLE PRECISION"))
                        conn.commit()
                    logger.info("✅ gtm_funnel_events.amount_usd added")
                else:
                    logger.info("✅ gtm_funnel_events.amount_usd already exists")
            except Exception as e:
                logger.error(f"❌ gtm_funnel_events migration failed: {e}", exc_info=True)

            # Buyer subscription columns (v5.75.68)
            try:
                from sqlalchemy import inspect as _sub_insp
                _sub_cols = [c['name'] for c in _sub_insp(db.engine).get_columns('users')]
                _sub_needed = {
                    'subscription_plan':   "VARCHAR(50) DEFAULT 'free'",
                    'analyses_this_month': 'INTEGER DEFAULT 0',
                    'analyses_reset_at':   'TIMESTAMP',
                }
                _sub_missing = {k: v for k, v in _sub_needed.items() if k not in _sub_cols}
                if _sub_missing:
                    with db.engine.connect() as conn:
                        for col, dtype in _sub_missing.items():
                            try:
                                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {dtype}"))
                                logger.info(f"  ✅ users.{col} added")
                            except Exception as _ce:
                                logger.warning(f"  ⚠️ users.{col}: {str(_ce)[:80]}")
                        conn.commit()
                    db.engine.dispose()
                    logger.info(f"✅ Buyer subscription columns added: {list(_sub_missing.keys())}")
                else:
                    logger.info("✅ Buyer subscription columns already up to date")
            except Exception as e:
                logger.error(f"❌ Buyer subscription migration failed: {e}", exc_info=True)

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

            # New tables: api_keys, inspector_referrals (v5.74.95)
            try:
                from models import APIKey, InspectorReferral
                for model in [APIKey, InspectorReferral]:
                    model.__table__.create(db.engine, checkfirst=True)
                logger.info("✅ New tables created: api_keys, inspector_referrals")
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

            # ── CONTRACTORS + CONTRACTOR_LEADS + CONTRACTOR_LEAD_CLAIMS ──────────
            # Single consolidated migration block — runs on every startup, safe to re-run.
            # Uses raw SQL column presence check so it works regardless of SQLAlchemy model state.
            try:
                from sqlalchemy import inspect as _ci
                _insp = _ci(db.engine)

                # 1. contractors table
                _c_existing = [c['name'] for c in _insp.get_columns('contractors')]
                _c_needed = {
                    'plan':               "VARCHAR(30) DEFAULT 'free'",
                    'stripe_customer_id': 'VARCHAR(100)',
                    'subscription_id':    'VARCHAR(100)',
                    'plan_activated_at':  'TIMESTAMP',
                    'monthly_lead_limit': 'INTEGER DEFAULT 0',
                    'available':          'BOOLEAN DEFAULT TRUE',
                    'unavailable_until':  'TIMESTAMP',
                }
                _c_add = {k: v for k, v in _c_needed.items() if k not in _c_existing}
                if _c_add:
                    with db.engine.connect() as _conn:
                        for _col, _dtype in _c_add.items():
                            try:
                                _conn.execute(text(f"ALTER TABLE contractors ADD COLUMN {_col} {_dtype}"))
                                logger.info(f"  ✅ contractors.{_col} added")
                            except Exception as _ce:
                                logger.warning(f"  ⚠️ contractors.{_col}: {str(_ce)[:100]}")
                        _conn.commit()
                    logger.info(f"✅ contractors: added {list(_c_add.keys())}")
                else:
                    logger.info("✅ contractors columns up to date")

                # 2. contractor_leads table
                _cl_existing = [c['name'] for c in _insp.get_columns('contractor_leads')]
                _cl_needed = {
                    'assigned_contractor_id': 'INTEGER',
                    'sent_to_contractor_at':  'TIMESTAMP',
                    'contacted_at':           'TIMESTAMP',
                    'job_closed_at':          'TIMESTAMP',
                    'job_value':              'DOUBLE PRECISION',
                    'referral_fee_pct':       'DOUBLE PRECISION',
                    'referral_fee_due':       'DOUBLE PRECISION',
                    'referral_paid':          'BOOLEAN DEFAULT FALSE',
                    'referral_paid_at':       'TIMESTAMP',
                    'available_at':           'TIMESTAMP',
                    'expires_at':             'TIMESTAMP',
                    'claim_count':            'INTEGER DEFAULT 0',
                    'notes':                  'TEXT',
                }
                _cl_add = {k: v for k, v in _cl_needed.items() if k not in _cl_existing}
                if _cl_add:
                    with db.engine.connect() as _conn:
                        for _col, _dtype in _cl_add.items():
                            try:
                                _conn.execute(text(f"ALTER TABLE contractor_leads ADD COLUMN {_col} {_dtype}"))
                                logger.info(f"  ✅ contractor_leads.{_col} added")
                            except Exception as _ce:
                                logger.warning(f"  ⚠️ contractor_leads.{_col}: {str(_ce)[:100]}")
                        _conn.commit()
                    logger.info(f"✅ contractor_leads: added {list(_cl_add.keys())}")
                else:
                    logger.info("✅ contractor_leads columns up to date")

                # 3. contractor_lead_claims table (create if missing)
                if 'contractor_lead_claims' not in _insp.get_table_names():
                    with db.engine.connect() as _conn:
                        _conn.execute(text("""
                            CREATE TABLE contractor_lead_claims (
                                id SERIAL PRIMARY KEY,
                                created_at TIMESTAMP DEFAULT NOW(),
                                lead_id INTEGER NOT NULL,
                                contractor_id INTEGER NOT NULL,
                                status VARCHAR(20) DEFAULT 'claimed',
                                passed_at TIMESTAMP,
                                contacted_at TIMESTAMP,
                                closed_at TIMESTAMP,
                                job_value DOUBLE PRECISION,
                                UNIQUE(lead_id, contractor_id)
                            )
                        """))
                        _conn.commit()
                    logger.info("✅ contractor_lead_claims table created")
                else:
                    logger.info("✅ contractor_lead_claims table exists")

            except Exception as e:
                logger.error(f"❌ Contractor marketplace migration failed: {e}", exc_info=True)
            finally:
                # Invalidate connection pool so subsequent queries see new columns
                try:
                    db.engine.dispose()
                    logger.info("✅ Connection pool refreshed after contractor migration")
                except Exception:
                    pass


            # inspector_reports: add inspection_text column (v5.75.5)
            try:
                from sqlalchemy import inspect as _sa_insp3
                _ir_cols = {c['name'] for c in _sa_insp3(db.engine).get_columns('inspector_reports')}
                if 'inspection_text' not in _ir_cols:
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE inspector_reports ADD COLUMN IF NOT EXISTS inspection_text TEXT"))
                        conn.commit()
                    logger.info("✅ inspector_reports: added inspection_text column")
                else:
                    logger.info("✅ inspector_reports: inspection_text already present")
            except Exception as e:
                logger.error(f"❌ inspector_reports migration failed: {e}", exc_info=True)

            # inspectors table: add missing columns (v5.75.9)
            try:
                from sqlalchemy import inspect as _sa_insp4
                _insp_cols = {c['name'] for c in _sa_insp4(db.engine).get_columns('inspectors')}
                _insp_missing = {
                    'is_verified': 'BOOLEAN DEFAULT FALSE',
                    'is_active':   'BOOLEAN DEFAULT TRUE',
                    'notes':       'TEXT',
                }
                _insp_add = {k: v for k, v in _insp_missing.items() if k not in _insp_cols}
                if _insp_add:
                    with db.engine.connect() as conn:
                        for col, dtype in _insp_add.items():
                            conn.execute(text(f"ALTER TABLE inspectors ADD COLUMN IF NOT EXISTS {col} {dtype}"))
                        conn.commit()
                    logger.info(f"✅ inspectors migration: added {list(_insp_add.keys())}")
                else:
                    logger.info("✅ inspectors columns already up to date")
            except Exception as e:
                logger.error(f"❌ inspectors migration failed: {e}", exc_info=True)

            # contractor subscription columns (v5.74.98)
            try:
                from sqlalchemy import inspect as _sa_insp2
                _ci = _sa_insp2(db.engine)
                _contractor_cols = {c['name'] for c in _ci.get_columns('contractors')}
                _new_c = {
                    'plan':               "VARCHAR(30) DEFAULT 'free'",
                    'stripe_customer_id': 'VARCHAR(100)',
                    'subscription_id':    'VARCHAR(100)',
                    'plan_activated_at':  'TIMESTAMP',
                    'monthly_lead_limit': 'INTEGER DEFAULT 0',
                }
                _missing_c = {k: v for k, v in _new_c.items() if k not in _contractor_cols}
                if _missing_c:
                    with db.engine.connect() as conn:
                        for col, dtype in _missing_c.items():
                            conn.execute(text(f"ALTER TABLE contractors ADD COLUMN IF NOT EXISTS {col} {dtype}"))
                        conn.commit()
                    logger.info(f"✅ contractors migration: added {list(_missing_c.keys())}")
                else:
                    logger.info("✅ contractors subscription columns already up to date")
            except Exception as e:
                logger.error(f"❌ contractors migration failed: {e}", exc_info=True)

            # api_keys revenue columns (v5.74.97)
            try:
                from sqlalchemy import inspect as _ai
                _ak_cols = [c['name'] for c in _ai(db.engine).get_columns('api_keys')]
                _new_ak_cols = {
                    'price_per_call':  'DOUBLE PRECISION DEFAULT 0',
                    'monthly_fee':     'DOUBLE PRECISION DEFAULT 0',
                    'revenue_month':   'DOUBLE PRECISION DEFAULT 0',
                    'revenue_total':   'DOUBLE PRECISION DEFAULT 0',
                    'invoice_day':     'INTEGER DEFAULT 1',
                    'billing_email':   'VARCHAR(255)',
                }
                _missing_ak = {k: v for k, v in _new_ak_cols.items() if k not in _ak_cols}
                if _missing_ak:
                    with db.engine.connect() as conn:
                        for col, dtype in _missing_ak.items():
                            conn.execute(text(f"ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS {col} {dtype}"))
                        conn.commit()
                    logger.info(f"✅ api_keys revenue columns added: {list(_missing_ak.keys())}")
                else:
                    logger.info("✅ api_keys revenue columns already up to date")
            except Exception as e:
                logger.error(f"❌ api_keys migration failed: {e}", exc_info=True)

            # ml_training_runs + ml_agent_runs tables (v5.86.8+)
            try:
                from sqlalchemy import inspect as _mli
                _ml_tables = _mli(db.engine).get_table_names()
                _needed = ['ml_training_runs', 'ml_cost_data', 'ml_agent_runs']
                _missing = [t for t in _needed if t not in _ml_tables]
                if _missing:
                    db.create_all()
                    logger.info(f"✅ ML tables created: {_missing}")
                else:
                    logger.info("✅ ML tables exist")
            except Exception as e:
                logger.warning(f"ML tables migration: {e}")


            # Write stamp file + release lock after successful migration
            try:
                os.makedirs("/var/data", exist_ok=True)
                open(_MIGRATION_STAMP_PATH, "w").write("migrated\n")
                logger.info("✅ DB migration stamp written — future deploys will skip ALTER TABLE checks")
            except Exception as _stamp_err:
                logger.warning(f"Could not write migration stamp (non-fatal): {_stamp_err}")
            finally:
                try:
                    if _got_lock and _lock_file:
                        _fcntl.flock(_lock_file, _fcntl.LOCK_UN)
                        _lock_file.close()
                except Exception:
                    pass

        # Clean up integrity test users left over from test runs
        try:
            from sqlalchemy import text as _cleanup_text
            with db.engine.connect() as _cleanup_conn:
                result = _cleanup_conn.execute(_cleanup_text(
                    "DELETE FROM users WHERE email LIKE 'integrity\\_%@test.offerwise.ai'"
                ))
                _cleanup_conn.commit()
                if result.rowcount > 0:
                    logger.info(f"✅ Cleaned up {result.rowcount} integrity test users")
        except Exception as _ce:
            logger.warning(f"Test user cleanup (non-fatal): {_ce}")

        # Auto-activate any contractor leads stuck in 'new' status
        try:
            from datetime import timedelta as _td_activate
            _stuck = ContractorLead.query.filter_by(status='new').all()
            for _lead in _stuck:
                _lead.status = 'available'
                if not _lead.available_at:
                    _lead.available_at = datetime.utcnow()
                if not _lead.expires_at:
                    _lead.expires_at = datetime.utcnow() + _td_activate(hours=48)
            if _stuck:
                db.session.commit()
                logger.info(f"✅ Auto-activated {len(_stuck)} contractor leads from 'new' → 'available'")
        except Exception as _ae:
            logger.warning(f"Lead auto-activation: {_ae}")

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
_ga4_prop = os.environ.get('GA4_PROPERTY_ID', '')
_ga4_key  = os.environ.get('GOOGLE_ANALYTICS_KEY_JSON', '')
_ga4_mid  = os.environ.get('GA4_MEASUREMENT_ID', '')
if _ga4_prop and _ga4_key:
    logger.info(f"   GA4: ✅ ACTIVE (property={_ga4_prop}, measurement_id={_ga4_mid or 'not set'})")
else:
    _missing_ga4 = [v for v, k in [('GA4_PROPERTY_ID', _ga4_prop), ('GOOGLE_ANALYTICS_KEY_JSON', _ga4_key), ('GA4_MEASUREMENT_ID', _ga4_mid)] if not k]
    logger.warning(f"   GA4: ⚠️  NOT ACTIVE — missing env vars: {', '.join(_missing_ga4)}")
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
    """Redirect HTTP to HTTPS and bare domain to www in production"""
    # Skip for local development and testing
    if os.environ.get('FLASK_ENV') in ('development', 'testing'):
        return
    if request.host.startswith('localhost') or request.host.startswith('127.0.0.1'):
        return
    
    # Skip for health check endpoints
    if request.path in ['/health', '/api/health']:
        return
    
    # Redirect bare domain to www (fixes POST→GET 301 issues with API calls)
    host = request.headers.get('Host', '') or request.host
    if host == 'getofferwise.ai' or host == 'getofferwise.ai:443':
        url = f"https://www.getofferwise.ai{request.full_path}"
        if url.endswith('?'):
            url = url[:-1]
        return redirect(url, code=301)
    
    # For production, enforce HTTPS
    if not request.is_secure and request.headers.get('X-Forwarded-Proto', 'http') != 'https':
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


# ============================================================
# FEATURE 4: INSPECTOR INVITE / REFERRAL FLOW (v5.74.95)
# ============================================================


def _hash_api_key(key: str) -> str:
    import hashlib
    return hashlib.sha256(key.encode()).hexdigest()


def _authenticate_api_key():
    """Authenticate via Bearer token or X-API-Key header. Returns (api_key, error_response)."""
    from models import APIKey
    auth = request.headers.get('Authorization', '')
    api_key_raw = ''
    if auth.startswith('Bearer '):
        api_key_raw = auth[7:].strip()
    if not api_key_raw:
        api_key_raw = request.headers.get('X-API-Key', '').strip()
    if not api_key_raw:
        return None, (jsonify({'error': 'API key required. Pass as Bearer token or X-API-Key header.'}), 401)

    key_hash = _hash_api_key(api_key_raw)
    api_key = APIKey.query.filter_by(key_hash=key_hash, is_active=True).first()
    if not api_key:
        return None, (jsonify({'error': 'Invalid or revoked API key.'}), 401)

    if api_key.calls_month >= api_key.monthly_limit:
        return None, (jsonify({
            'error': 'Monthly limit reached.',
            'calls_used': api_key.calls_month,
            'limit': api_key.monthly_limit,
            'upgrade': 'Contact support@getofferwise.ai to increase your limit.'
        }), 429)

    return api_key, None


def _track_api_usage(api_key):
    """Increment usage counters and accrue revenue for an API key."""
    api_key.calls_total = (api_key.calls_total or 0) + 1
    api_key.calls_month = (api_key.calls_month or 0) + 1
    api_key.last_used_at = datetime.utcnow()
    call_revenue = api_key.accrued_this_call()
    if call_revenue > 0:
        api_key.revenue_month = round((api_key.revenue_month or 0) + call_revenue, 4)
        api_key.revenue_total = round((api_key.revenue_total or 0) + call_revenue, 4)
    db.session.commit()


# ── Enterprise API v1 ─────────────────────────────────────────────────────────

@app.route('/api/v1/analyze', methods=['POST'])
@limiter.limit("100 per hour")
def b2b_analyze():
    """
    Full property analysis with document intelligence.

    Required: disclosure_text, inspection_text, property_price, property_address
    Optional: repair_tolerance, ownership_duration, deal_breakers
    Returns: risk score, offer strategy, deal breakers, repair costs, transparency score
    """
    api_key, err = _authenticate_api_key()
    if err:
        return err

    data = request.get_json(silent=True) or {}
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
        _track_api_usage(api_key)

        return jsonify({
            'success': True,
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
            'usage': {
                'calls_used': api_key.calls_month,
                'calls_remaining': max(0, api_key.monthly_limit - api_key.calls_month),
            },
        })
    except Exception as e:
        logging.error(f"B2B API analysis failed: {e}", exc_info=True)
        return jsonify({'error': 'Analysis failed. Please try again.'}), 500


@app.route('/api/v1/research', methods=['POST'])
@limiter.limit("200 per hour")
def b2b_research():
    """
    Property research — market data, hazards, schools, permits, environmental.
    No documents needed — just an address.

    Required: address
    Returns: AVM, comps, flood zone, seismic, schools, walk score, permits, air quality
    """
    api_key, err = _authenticate_api_key()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    address = (data.get('address') or '').strip()

    if not address or len(address) < 10:
        return jsonify({'error': 'Please provide a complete property address.'}), 400

    try:
        import anthropic as _anth
        ai_client = _anth.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    except Exception:
        ai_client = None

    try:
        agent = PropertyResearchAgent(ai_client=ai_client)
        result = agent.research(address)
        _track_api_usage(api_key)

        # Structure the response cleanly
        profile = result.get('profile', {})
        tool_data = {}
        for t in (result.get('tool_results') or []):
            tool_data[t.get('tool_name', 'unknown')] = t.get('data')

        return jsonify({
            'success': True,
            'address': address,
            'avm': {
                'estimated_value': profile.get('avm_price'),
                'price_range_low': profile.get('avm_low'),
                'price_range_high': profile.get('avm_high'),
                'confidence': profile.get('avm_confidence'),
            },
            'market': {
                'days_on_market': profile.get('days_on_market'),
                'price_per_sqft': profile.get('price_per_sqft'),
                'comparable_sales': profile.get('comparable_sales'),
            },
            'hazards': {
                'flood_zone': profile.get('flood_zone'),
                'flood_risk': profile.get('flood_risk'),
                'seismic_risk': profile.get('seismic_risk'),
                'wildfire_risk': profile.get('wildfire_risk'),
                'air_quality_index': profile.get('air_quality_index'),
            },
            'neighborhood': {
                'walk_score': profile.get('walk_score'),
                'transit_score': profile.get('transit_score'),
                'bike_score': profile.get('bike_score'),
                'school_ratings': profile.get('school_ratings'),
            },
            'permits': profile.get('permit_history'),
            'synthesis': result.get('synthesis'),
            'tools_succeeded': result.get('tools_succeeded', 0),
            'tools_failed': result.get('tools_failed', 0),
            'research_time_ms': result.get('research_time_ms', 0),
            'usage': {
                'calls_used': api_key.calls_month,
                'calls_remaining': max(0, api_key.monthly_limit - api_key.calls_month),
            },
        })
    except Exception as e:
        logging.error(f"B2B API research failed: {e}", exc_info=True)
        return jsonify({'error': 'Research failed. Please try again.'}), 500


@app.route('/api/v1/screen', methods=['POST'])
@limiter.limit("500 per hour")
def b2b_screen():
    """
    Lightweight property screening — fast risk assessment without documents.
    Designed for batch/portfolio screening by institutional buyers and lenders.

    Required: address
    Optional: asking_price
    Returns: property data, risk flags, estimated value, hazard summary
    """
    api_key, err = _authenticate_api_key()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    address = (data.get('address') or '').strip()
    asking_price = float(data.get('asking_price', 0))

    if not address or len(address) < 10:
        return jsonify({'error': 'Please provide a complete property address.'}), 400

    try:
        agent = PropertyResearchAgent(ai_client=None)
        result = agent.research(address)
        _track_api_usage(api_key)

        profile = result.get('profile', {})
        avm = profile.get('avm_price') or 0
        risk_flags = []

        # Auto-generate risk flags from data
        if profile.get('flood_zone') and profile.get('flood_zone') not in ('X', 'None', 'Minimal'):
            risk_flags.append({'flag': 'flood_zone', 'severity': 'high', 'detail': f"FEMA zone: {profile.get('flood_zone')}"})
        if profile.get('seismic_risk') and 'high' in str(profile.get('seismic_risk', '')).lower():
            risk_flags.append({'flag': 'seismic', 'severity': 'high', 'detail': str(profile.get('seismic_risk'))})
        if profile.get('wildfire_risk') and 'high' in str(profile.get('wildfire_risk', '')).lower():
            risk_flags.append({'flag': 'wildfire', 'severity': 'high', 'detail': str(profile.get('wildfire_risk'))})
        if asking_price and avm and asking_price > avm * 1.15:
            risk_flags.append({'flag': 'overpriced', 'severity': 'medium', 'detail': f"Asking ${asking_price:,.0f} is {((asking_price/avm)-1)*100:.0f}% above AVM ${avm:,.0f}"})
        if profile.get('days_on_market') and profile.get('days_on_market') > 90:
            risk_flags.append({'flag': 'stale_listing', 'severity': 'low', 'detail': f"{profile.get('days_on_market')} days on market"})

        return jsonify({
            'success': True,
            'address': address,
            'estimated_value': avm,
            'asking_price': asking_price or None,
            'price_gap_pct': round(((asking_price / avm) - 1) * 100, 1) if asking_price and avm else None,
            'risk_flags': risk_flags,
            'risk_flag_count': len(risk_flags),
            'flood_zone': profile.get('flood_zone'),
            'walk_score': profile.get('walk_score'),
            'days_on_market': profile.get('days_on_market'),
            'tools_succeeded': result.get('tools_succeeded', 0),
            'research_time_ms': result.get('research_time_ms', 0),
            'usage': {
                'calls_used': api_key.calls_month,
                'calls_remaining': max(0, api_key.monthly_limit - api_key.calls_month),
            },
        })
    except Exception as e:
        logging.error(f"B2B API screen failed: {e}", exc_info=True)
        return jsonify({'error': 'Screening failed. Please try again.'}), 500


@app.route('/api/v1/usage', methods=['GET'])
def b2b_usage():
    """Check API key usage and limits."""
    api_key, err = _authenticate_api_key()
    if err:
        return err

    return jsonify({
        'key_prefix': api_key.key_prefix,
        'tier': api_key.tier,
        'calls_month': api_key.calls_month,
        'monthly_limit': api_key.monthly_limit,
        'calls_remaining': max(0, api_key.monthly_limit - api_key.calls_month),
        'calls_total': api_key.calls_total,
        'last_used': api_key.last_used_at.isoformat() if api_key.last_used_at else None,
    })


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
    data = request.get_json(silent=True) or {}
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

@app.route('/api-pricing')
def api_pricing_page():
    # Redirect to merged docs page
    suffix = '?activated=1' if request.args.get('activated') == '1' else ''
    plan = request.args.get('plan', '')
    return redirect(f'/api/docs{suffix}')

@app.route('/api/docs')
def api_docs_page():
    with open(os.path.join(app.static_folder, 'api-docs.html'), 'r') as f:
        html = f.read()
    pub_key = stripe_publishable or os.environ.get('STRIPE_PUBLISHABLE_KEY', '')
    html = html.replace("{{ stripe_publishable_key }}", pub_key)
    return html, 200, {'Content-Type': 'text/html'}

@app.route('/api/checkout/api-plan', methods=['POST'])
@login_required
def api_plan_checkout():
    """Stripe checkout for API subscription plans."""
    from auth_config import PRICING_TIERS
    data = request.get_json(silent=True) or {}
    plan = data.get('plan', '')

    API_PLANS = ['api_starter', 'api_growth']
    if plan not in API_PLANS:
        return jsonify({'error': 'Invalid API plan'}), 400

    tier = PRICING_TIERS.get(plan, {})
    price_id = tier.get('stripe_price_id', '')
    if not price_id:
        return jsonify({'error': f'Plan not yet configured. Email support@getofferwise.ai to get access.'}), 400

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            success_url=request.host_url.rstrip('/') + '/api-pricing?activated=1&plan=' + plan,
            cancel_url=request.host_url.rstrip('/') + '/api-pricing',
            client_reference_id=str(current_user.id),
            metadata={
                'user_id': str(current_user.id),
                'plan': plan,
                'type': 'api_subscription',
                'monthly_limit': str(tier.get('monthly_limit', 500)),
            }
        )
        return jsonify({'sessionId': checkout_session.id})
    except Exception as e:
        logging.error(f'API plan checkout error: {e}')
        return jsonify({'error': 'Payment system error. Please try again.'}), 500

@app.route('/api/checkout/api-enterprise', methods=['POST'])
@login_required
def api_enterprise_inquiry():
    """Enterprise inquiry — sends notification email and returns contact info."""
    data = request.get_json(silent=True) or {}
    company = (data.get('company') or '').strip()
    volume = (data.get('volume') or '').strip()
    use_case = (data.get('use_case') or '').strip()
    try:
        from email_service import send_email
        send_email(
            to_email='support@getofferwise.ai',
            subject=f'Enterprise API Inquiry: {company or current_user.email}',
            html_content=f'<p><b>From:</b> {current_user.email}</p>'
                        f'<p><b>Company:</b> {company}</p>'
                        f'<p><b>Monthly volume:</b> {volume}</p>'
                        f'<p><b>Use case:</b> {use_case}</p>'
        )
    except Exception as e:
        logging.warning(f'Enterprise inquiry email failed: {e}')
    return jsonify({'success': True})


# api/docs now served by api_docs_page() above


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

@app.route('/for-lenders')
def for_lenders_landing():
    return send_from_directory('static', 'for-lenders.html')

@app.route('/for-title-companies')
def for_title_companies_landing():
    return send_from_directory('static', 'for-title-companies.html')

@app.route('/for-insurance')
def for_insurance_landing():
    return send_from_directory('static', 'for-insurance.html')

@app.route('/for-appraisers')
def for_appraisers_landing():
    return send_from_directory('static', 'for-appraisers.html')

@app.route('/enterprise')
def enterprise_landing():
    return send_from_directory('static', 'enterprise.html')

@app.route('/architecture')
def architecture_page():
    """v5.87.28: serve architecture.html with live stats interpolated from
    Postgres. Public page — must never 500 even if DB is down. The
    _get_architecture_stats helper has TTL cache + fail-safe defaults so
    a search bot scraping at 1000 req/s won't hammer the DB and a DB
    outage won't break the page.
    """
    import os
    arch_path = os.path.join(app.static_folder, 'architecture.html')
    try:
        with open(arch_path, 'r', encoding='utf-8') as f:
            html = f.read()
    except Exception:
        # If the file is somehow missing, fall back to send_from_directory's
        # behavior (404-style)
        return send_from_directory('static', 'architecture.html')

    stats = _get_architecture_stats()
    # String replacements — keyed on stable SVG/HTML markers in the file.
    # I keep this as plain string replacement rather than Jinja templating
    # because the file already contains thousands of curly braces inside
    # SVG `style` blocks that would conflict with Jinja's `{` syntax.
    for marker, value in stats.get('replacements', {}).items():
        html = html.replace(marker, value)
    return html


# In-process TTL cache for the architecture stats. 5 min is a good balance:
# fresh enough that a deploy or training run is reflected quickly, slow
# enough that scraping/bot traffic doesn't pound Postgres.
_ARCH_STATS_CACHE = {'data': None, 'fetched_at': 0}
_ARCH_STATS_TTL_SECONDS = 300


def _get_architecture_stats():
    """Return a dict of current stats for the architecture page, with a
    TTL cache + fail-safe defaults.

    Layout:
      {
        'replacements': {marker_string: replacement_string, ...},
        'fetched_at': float epoch seconds,
        'is_stale': bool (True if returned from fallback because DB query failed),
      }

    Markers are placeholder substrings the architecture.html file contains.
    On replacement, the page renders with current numbers. If DB is down
    or the query throws, we return last-known-good cached data, or if
    nothing has ever been cached, baseline defaults that match the
    pre-dynamic page's static numbers.
    """
    import time
    now = time.time()
    cached = _ARCH_STATS_CACHE.get('data')
    if cached and (now - _ARCH_STATS_CACHE.get('fetched_at', 0)) < _ARCH_STATS_TTL_SECONDS:
        return cached

    # Try to refresh from DB. On any failure, return the last-known-good
    # cache (if we have one) or static fallback values.
    try:
        from models import MLFindingLabel, MLTrainingRun, db
        from sqlalchemy import func

        total_rows = MLFindingLabel.query.count()
        last_run = MLTrainingRun.query.order_by(MLTrainingRun.created_at.desc()).first()

        # Active crawler count from the registry (no DB query — just code state)
        try:
            from ml_ingestion import CRAWLER_REGISTRY
            active_count = sum(1 for _, _, c in CRAWLER_REGISTRY if getattr(c, 'STATUS', '') == 'active')
            scaffold_count = sum(1 for _, _, c in CRAWLER_REGISTRY if getattr(c, 'STATUS', '') != 'active')
        except Exception:
            active_count, scaffold_count = 13, 7

        # Round corpus size to nearest 1K for display ("121K" not "121,206").
        # Numbers below 10K render with one decimal ("9.5K"); 10K+ rounds to integer K.
        def _fmt_k(n):
            if n is None or n < 0:
                return '?'
            if n < 1000:
                return str(n)
            if n < 10000:
                return f'{n/1000:.1f}K'
            return f'{round(n/1000)}K'

        rows_str = _fmt_k(total_rows) if total_rows else '~121K'  # fallback to known value if query returns 0

        # Markers we'll replace — these strings exist verbatim in architecture.html
        # and were chosen because they are unique (won't collide with other
        # text). When we update architecture.html, keep these markers stable.
        #
        # Note: I deliberately do NOT make the "13 of top-50 metros (26%)"
        # phrase dynamic. Computing it from active_count is wrong — the
        # registry includes Federal (national aggregate, not a metro), Adams
        # County (suburban, not top-50), and Augusta (small SE metro,
        # not top-50). A precise dynamic calculation would need a per-crawler
        # `is_top50_metro: bool` flag on each registry entry, which is
        # metadata that doesn't exist yet. Until that flag is added, the
        # top-50 framing stays as static text — verifiable against the
        # Coverage Tracker table further down the page.
        replacements = {
            # Corpus paragraph row count
            '~121K rows in <code>ml_finding_labels</code> as of v5.87.21':
                f'~{rows_str} rows in <code>ml_finding_labels</code> (live count)',
            # SVG corpus tag
            '~121K rows &middot; text + category + severity':
                f'~{rows_str} rows &middot; text + category + severity',
            # Memory paragraph
            '121K ORM objects':
                f'{rows_str} ORM objects',
            # SVG header crawler count — replace ONLY the active/scaffold
            # numbers, leave the "13 of top-50 metros (26%)" curated phrase
            # intact (it's accurate and shouldn't auto-derive from active_count
            # because the registry includes Federal/Adams/Augusta which aren't
            # top-50 metros).
            '13 active crawlers · 7 scaffolded':
                f'{active_count} active crawlers · {scaffold_count} scaffolded',
        }

        data = {
            'replacements': replacements,
            'fetched_at': now,
            'is_stale': False,
            'total_rows': total_rows,
            'active_crawlers': active_count,
            'scaffold_crawlers': scaffold_count,
        }
        _ARCH_STATS_CACHE['data'] = data
        _ARCH_STATS_CACHE['fetched_at'] = now
        return data
    except Exception as e:
        # DB query failed. Use last-known-good cache if we have one.
        if cached:
            cached['is_stale'] = True
            return cached
        # No cache — return empty replacements dict so static text in the
        # page is preserved as-is. The page renders, just doesn't update.
        try:
            from logging import getLogger
            getLogger(__name__).warning(f'architecture stats query failed: {e}')
        except Exception:
            pass
        return {'replacements': {}, 'fetched_at': now, 'is_stale': True}


@app.route('/comparison')
def comparison_page():
    # Public head-to-head results page. Linked from architecture page and from
    # cold outreach; meant to be a shareable URL.
    return send_from_directory('static', 'comparison.html')


def find_matching_contractor(repair_system, property_zip):
    """
    Find and rank active contractors for a lead.

    Priority order (within each tier, tiebreak by fewest leads this month, then loyalty):
      1. contractor_enterprise  — paid for statewide + top priority
      2. contractor_pro         — paid for priority matching + verified badge
      3. contractor_starter     — paid for leads, capped at monthly_lead_limit (default 5)
      Free / pending / paused  — never auto-routed

    Returns up to 3 contractors (one per tier when possible, to give the buyer options).
    """
    PLAN_PRIORITY = {
        'contractor_enterprise': 0,
        'contractor_pro':        1,
        'contractor_starter':    2,
    }
    PAID_PLANS = set(PLAN_PRIORITY.keys())

    trade_key = (repair_system or '').lower()
    property_zip = (property_zip or '').strip()

    candidates = Contractor.query.filter(
        Contractor.status == 'active',
        Contractor.accepts_leads == True,
        Contractor.plan.in_(list(PAID_PLANS)),
    ).all()

    scored = []
    for c in candidates:
        plan = getattr(c, 'plan', 'free') or 'free'

        # ── Monthly lead limit check ───────────────────────────────────
        limit = getattr(c, 'monthly_lead_limit', None)
        if limit and limit > 0:
            sent_this_month = getattr(c, 'leads_sent_month', 0) or 0
            if sent_this_month >= limit:
                logging.info(f"Contractor {c.id} ({c.email}) at monthly limit ({sent_this_month}/{limit}) — skipped")
                continue

        # ── Trade match ────────────────────────────────────────────────
        trades = [t.strip().lower() for t in (c.trades or '').split(',') if t.strip()]
        trade_match = any(trade_key in t or t in trade_key for t in trades) or 'general' in trades
        if not trade_match:
            continue

        # ── Area match ─────────────────────────────────────────────────
        # Enterprise: statewide (zip_limit = -1) → always matches
        # Pro: up to 10 ZIPs → check list
        # Starter: up to 3 ZIPs → check list
        if plan == 'contractor_enterprise':
            area_match = True
        elif property_zip and c.service_zips:
            zip_list = [z.strip() for z in c.service_zips.split(',') if z.strip()]
            area_match = property_zip in zip_list
        else:
            # No ZIP filter configured → match all
            area_match = True

        if not area_match:
            continue

        # ── Score: lower = better ──────────────────────────────────────
        plan_rank    = PLAN_PRIORITY.get(plan, 99)
        leads_month  = getattr(c, 'leads_sent_month', 0) or 0
        # Loyalty tiebreak: earlier activation = more loyal
        from datetime import datetime as _dt
        activated = c.plan_activated_at or c.created_at or _dt.utcnow()
        loyalty_days = (datetime.utcnow() - activated).days if activated else 0

        scored.append((plan_rank, leads_month, -loyalty_days, c))

    # Sort: best plan first, then fewest leads this month, then most loyal
    scored.sort(key=lambda x: (x[0], x[1], x[2]))

    # Return up to 3, preferring one from each tier when possible
    result = []
    seen_plans = set()
    # First pass: one per tier
    for plan_rank, _, _, c in scored:
        plan = c.plan
        if plan not in seen_plans:
            result.append(c)
            seen_plans.add(plan)
        if len(result) >= 3:
            break
    # Second pass: fill remaining slots from same tier if < 3
    if len(result) < 3:
        for plan_rank, _, _, c in scored:
            if c not in result:
                result.append(c)
            if len(result) >= 3:
                break

    logging.info(f"Lead matching: {len(scored)} candidates → {len(result)} selected for {repair_system} in ZIP {property_zip}")
    for c in result:
        logging.info(f"  → {c.plan} | {c.email} | {getattr(c,'leads_sent_month',0)} leads this month")

    return result


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
    data = request.get_json(silent=True) or {}
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


@app.route('/api/research', methods=['POST'])
@limiter.limit("30 per hour")  # Free but rate-limited
def research_property():
    """
    🤖 Property Research Agent - Autonomous property research.
    
    No login required — this is the free "aha moment" that demonstrates
    the agent's value before the user pays for full analysis.
    """
    try:
        data = request.get_json(silent=True)
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
        data = request.get_json(silent=True)
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
    
    # v5.85.13: Inject is_free_tier so the frontend can ungate the report for free-tier users
    # who already spent their free credit on this analysis
    if 'is_free_tier' not in result_json:
        is_developer = current_user.email.lower() in DEVELOPER_EMAILS
        result_json['is_free_tier'] = not bool(current_user.stripe_customer_id) and not is_developer
    
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

                    # Patch market data if missing (pre-market-intelligence analyses)
                    # Runs synchronously but only once — persists to DB so never runs again
                    _mc = (offer_strategy or {}).get('market_context') or {}
                    _mi_stored = result_json.get('market_intelligence') or {}
                    if not _mc.get('market_applied') and not _mi_stored.get('avm_price'):
                        result_json['_needs_market_refresh'] = True

                    # v5.85.13: Inject is_free_tier so frontend ungates the report
                    if 'is_free_tier' not in result_json:
                        _is_dev = current_user.email.lower() in DEVELOPER_EMAILS
                        result_json['is_free_tier'] = not bool(current_user.stripe_customer_id) and not _is_dev

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
    
    data = request.get_json(silent=True)
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
    
    # Invalidate analysis cache so re-runs get fresh results
    try:
        from analysis_cache import AnalysisCache
        _cache = AnalysisCache()
        _deleted = _cache.delete_by_address(property.address)
        logging.info(f"🗑️ Cache: {_deleted} entr{'y' if _deleted==1 else 'ies'} cleared for {property.address}")
    except Exception as _ce:
        logging.warning(f"Cache invalidation skipped: {_ce}")

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

        # 3c. Clear issue_confirmations
        try:
            from models import IssueConfirmation
            ic_count = IssueConfirmation.query.filter_by(analysis_id=analysis_id).count()
            if ic_count > 0:
                IssueConfirmation.query.filter_by(analysis_id=analysis_id).update({'analysis_id': None})
                logging.info(f"   Cleared {ic_count} issue_confirmation(s)")
        except Exception as _ice:
            logging.warning(f"   issue_confirmations cleanup skipped: {_ice}")

        # 3d. Clear PropertyWatch references
        try:
            from models import PropertyWatch
            pw_count = PropertyWatch.query.filter_by(analysis_id=analysis_id).count()
            if pw_count > 0:
                PropertyWatch.query.filter_by(analysis_id=analysis_id).update({'analysis_id': None})
                logging.info(f"   Cleared {pw_count} PropertyWatch reference(s)")
        except Exception as _pwe:
            logging.warning(f"   PropertyWatch cleanup skipped: {_pwe}")
        
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
        
        # Step 4: Invalidate cache for this property address so re-runs get fresh results
        logging.info(f"Step 4: Invalidating analysis cache for {property_address}...")
        try:
            from analysis_cache import AnalysisCache
            _cache = AnalysisCache()
            _deleted = _cache.delete_by_address(property_address)
            logging.info(f"   🗑️ Cache: {_deleted} entr{'y' if _deleted==1 else 'ies'} cleared for {property_address}")
        except Exception as _ce:
            logging.warning(f"   Cache invalidation skipped: {_ce}")

        # Step 5: Delete the analysis
        logging.info(f"Step 5: Deleting analysis {analysis_id}...")
        db.session.delete(analysis)
        
        # Step 6: Commit
        logging.info(f"Step 6: Committing final transaction...")
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
    if _ocr_get(progress_key) is not None:
        _ocr_update(progress_key, {'cancelled': True, 'status': 'cancelled'})
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
    total_mb = psutil.virtual_memory().total / (1024 * 1024)
    logger.info(f"📊 Startup memory usage: {memory_mb} MB (Total: {total_mb:.0f} MB)")
    if memory_mb > 400:
        logger.warning(f"⚠️ HIGH startup memory! Using {memory_mb} MB - crashes likely!")
except Exception as e:
    logger.error(f"Could not measure startup memory: {e}")


@app.route('/api/debug/market-test', methods=['GET'])
@login_required
def market_test_debug():
    """Debug endpoint: test RentCast directly and return raw result"""
    try:
        import os, requests as _req
        address = request.args.get('address', '381 Tina Dr, Hollister, CA 95023')
        api_key = os.environ.get('RENTCAST_API_KEY', '')
        
        result = {
            'rentcast_key_set': bool(api_key),
            'rentcast_key_length': len(api_key),
            'address_tested': address,
        }
        
        if api_key:
            # Test AVM endpoint
            try:
                resp = _req.get(
                    'https://api.rentcast.io/v1/avm/value',
                    params={'address': address, 'compCount': 5},
                    headers={'X-Api-Key': api_key, 'Accept': 'application/json'},
                    timeout=15
                )
                result['avm_status_code'] = resp.status_code
                result['avm_response'] = resp.json() if resp.status_code == 200 else resp.text[:500]
            except Exception as e:
                result['avm_error'] = str(e)
            
            # Test market stats endpoint
            try:
                resp2 = _req.get(
                    'https://api.rentcast.io/v1/markets',
                    params={'zipCode': '95023', 'dataType': 'Sale'},
                    headers={'X-Api-Key': api_key, 'Accept': 'application/json'},
                    timeout=10
                )
                result['markets_status_code'] = resp2.status_code
                result['markets_response'] = resp2.json() if resp2.status_code == 200 else resp2.text[:500]
            except Exception as e:
                result['markets_error'] = str(e)
        
        return jsonify(result)
    except Exception as e:
        logging.error(f'Market test error: {e}')
        return jsonify({'error': 'Internal server error'}), 500


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
        
        data = request.get_json(silent=True)
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
        
        # ── Analysis access gate ─────────────────────────────────────────────
        # Access = subscription allowance OR reward credits (referrals/promos)
        from auth_config import PRICING_TIERS
        from datetime import timedelta as _td_gate

        _plan = getattr(current_user, 'subscription_plan', 'free') or 'free'
        _tier = PRICING_TIERS.get(_plan, PRICING_TIERS['free'])
        _limit = _tier.get('analyses_per_month', 1)
        _used  = getattr(current_user, 'analyses_this_month', 0) or 0
        _reset = getattr(current_user, 'analyses_reset_at', None)
        _credits = getattr(current_user, 'analysis_credits', 0) or 0

        # Auto-reset monthly counter if billing period has rolled over
        if _reset and datetime.utcnow() > _reset:
            current_user.analyses_this_month = 0
            current_user.analyses_reset_at = datetime.utcnow() + _td_gate(days=30)
            db.session.commit()
            _used = 0

        # Check if user has subscription allowance left OR reward credits
        _sub_ok     = (_limit == -1) or (_used < _limit)   # -1 = unlimited
        _credits_ok = _credits > 0

        if not _sub_ok and not _credits_ok:
            _plan_name = _tier.get('name', 'Free')
            if _plan == 'free':
                _msg = 'Your free analysis has been used. Subscribe to continue analyzing properties.'
            else:
                _msg = f'You have used all {_limit} analyses in your {_plan_name} plan this month. Upgrade or wait for your plan to reset.'
            logger.warning(f"⚠️ User {current_user.id} blocked: plan={_plan}, used={_used}/{_limit}, credits={_credits}")
            return jsonify({
                'success': False,
                'error': _msg,
                'plan': _plan,
                'analyses_used': _used,
                'analyses_limit': _limit,
                'credits_remaining': _credits,
                'upgrade_url': '/pricing',
            }), 402

        # Per-user AI cost cap for free tier
        try:
            from ai_cost_tracker import is_user_over_ai_budget
            if is_user_over_ai_budget(current_user.id):
                return jsonify({
                    'success': False,
                    'error': 'Your free analysis has been used. Subscribe to continue.',
                    'upgrade_url': '/pricing',
                }), 402
        except Exception:
            pass

        logger.info(f"💳 User {current_user.id} plan={_plan} used={_used}/{_limit} credits={_credits}")
        
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
        
        # Increment usage counter — prefer subscription, fall back to reward credits
        if _sub_ok:
            # Use subscription allowance
            User.query.filter(User.id == current_user.id).update(
                {User.analyses_this_month: (current_user.analyses_this_month or 0) + 1},
                synchronize_session=False
            )
        else:
            # Use reward credit (referral/promo)
            rows_updated = User.query.filter(
                User.id == current_user.id,
                User.analysis_credits > 0
            ).update(
                {User.analysis_credits: User.analysis_credits - 1},
                synchronize_session=False
            )
            if rows_updated == 0:
                db.session.rollback()
                return jsonify({'error': 'No analyses remaining'}), 402

        # AUTO-REFILL for developer accounts
        if current_user.email.lower() in DEVELOPER_EMAILS:
            User.query.filter(User.id == current_user.id).update(
                {User.analysis_credits: 500, User.analyses_this_month: 0},
                synchronize_session=False
            )
            logger.info(f"👑 DEVELOPER ACCOUNT: Auto-refilled")
        
        logger.info(f"🏆 User {current_user.id} starting comparison {comparison.id}")
        try:
            from funnel_tracker import track_from_request
            track_from_request('comparison_started', request, user_id=current_user.id,
                               metadata={'plan': _plan})
        except Exception: pass
        
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


@app.route('/analyze')
def analyze_landing():
    """Direct-response landing page for ad traffic — value-first funnel."""
    try:
        from funnel_tracker import track_from_request
        track_from_request('visit', request)
        # Persist UTM params so signup can attribute correctly even if done later
        for _k in ('utm_source','utm_medium','utm_campaign','utm_content','utm_term'):
            _v = request.args.get(_k)
            if _v:
                session[_k] = _v
    except Exception:
        pass
    return send_from_directory('static', 'analyze.html')


@app.route('/pricing')
def pricing():
    """Pricing page"""
    try:
        from funnel_tracker import track_from_request
        uid = current_user.id if current_user.is_authenticated else None
        track_from_request('pricing_view', request, user_id=uid)
        for _k in ('utm_source','utm_medium','utm_campaign','utm_content','utm_term'):
            _v = request.args.get(_k)
            if _v:
                session[_k] = _v
    except Exception:
        pass
    return send_from_directory('static', 'pricing.html')

@app.route('/thesis')
@app.route('/thesis.html')
def thesis():
    """Investor thesis page — not linked from anywhere, noindexed."""
    return send_from_directory('static', 'thesis.html')

@app.route('/sample-analysis')
@app.route('/sample-analysis.html')
def sample_analysis():
    """Sample analysis page"""
    return send_from_directory('static', 'sample-analysis.html')

@app.route('/zillow')
@app.route('/from/zillow')
def zillow_landing():
    """Zillow ad landing page — tracks utm params for attribution."""
    try:
        from funnel_tracker import track_from_request
        track_from_request('visit', request, metadata={'landing': 'zillow'})
        for _k in ('utm_source','utm_medium','utm_campaign','utm_content','utm_term'):
            _v = request.args.get(_k)
            if _v:
                session[_k] = _v
        # Default UTM if none provided (user typed URL directly)
        if not session.get('utm_source'):
            session['utm_source'] = 'zillow'
            session['utm_medium'] = 'display'
    except Exception:
        pass
    return send_from_directory('static', 'zillow-landing.html')

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
        data = request.get_json(silent=True) or {}
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
    from flask import make_response
    resp = make_response(send_from_directory('static', 'settings.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@app.route('/api/quick-check', methods=['POST'])
@limiter.limit("30 per hour")
def api_quick_check():
    """Public address-only property check. No auth, no credits, no AI calls.
    Runs the PropertyResearchAgent to fetch market data, flood, walk score, etc."""
    try:
        data = request.get_json(silent=True) or {}
        address = (data.get('address') or '').strip()

        if not address or len(address) < 10:
            return jsonify({'error': 'Please enter a full street address including city, state, and ZIP.'}), 400

        # Require ZIP
        import re
        if not re.search(r'\b\d{5}\b', address):
            return jsonify({'error': 'Please include a 5-digit ZIP code for accurate results.'}), 400

        logging.info(f"📍 Quick check (public): '{address[:60]}'")

        try:
            from funnel_tracker import track_from_request
            track_from_request('quick_check_start', request, metadata={'address': address[:100]})
        except Exception:
            pass

        # Run research agent — no Anthropic API key needed for tool-only research
        agent = PropertyResearchAgent(ai_client=None)
        research_data = agent.research(address)

        if not research_data or not research_data.get('tool_results'):
            return jsonify({'error': 'Unable to fetch data for this address. Please check the address and try again.'}), 400

        # Extract structured data from tool results
        _rc = _ms = _flood = _env = _walk = _schools = _permits = _eq = _air = None
        for _t in (research_data.get('tool_results') or []):
            _tn = _t.get('tool_name', '')
            _td = _t.get('data') or {}
            if not isinstance(_td, dict):
                _td = {}
            if _tn == 'rentcast': _rc = _td
            elif _tn == 'market_stats': _ms = _td
            elif _tn == 'flood_zone': _flood = _td
            elif _tn in ('california_hazards', 'environmental'): _env = _td
            elif _tn == 'walk_score': _walk = _td
            elif _tn == 'school_ratings': _schools = _td
            elif _tn == 'permit_history': _permits = _td
            elif _tn == 'earthquake_history': _eq = _td
            elif _tn == 'air_quality': _air = _td

        def _safe_int(v, default=0):
            try: return int(v) if v is not None else default
            except (TypeError, ValueError): return default

        def _safe_float(v, default=0.0):
            try: return float(v) if v is not None else default
            except (TypeError, ValueError): return default

        _avm = _safe_int((_rc or {}).get('avm_price'))
        _avm_low = _safe_int((_rc or {}).get('avm_price_low'))
        _avm_high = _safe_int((_rc or {}).get('avm_price_high'))
        _comps = (_rc or {}).get('comparables') or []
        if not isinstance(_comps, list): _comps = []
        _dom = _safe_int((_ms or {}).get('average_days_on_market'))
        _listings = _safe_int((_ms or {}).get('total_listings'))

        result = {
            'analysis_depth': 'address_only',
            'property_address': address,
            'market_context': {
                'avm_price': _avm,
                'avm_price_low': _avm_low,
                'avm_price_high': _avm_high,
                'comparables_count': len(_comps),
                'average_days_on_market': _dom,
                'total_listings': _listings,
                'median_price_per_sqft': _safe_float((_ms or {}).get('median_price_per_sqft')),
            },
            'environmental': {
                'flood_zone': (_flood or {}).get('flood_zone') or 'Unknown',
                'flood_risk': (_flood or {}).get('flood_risk') or 'Unknown',
                'wildfire_risk': (_env or {}).get('wildfire_risk') or 'Unknown',
                'seismic_zone': (_env or {}).get('seismic_zone') or 'Unknown',
                'air_quality_index': _safe_int((_air or {}).get('aqi')),
            },
            'neighborhood': {
                'walk_score': _safe_int((_walk or {}).get('walk_score')),
                'transit_score': _safe_int((_walk or {}).get('transit_score')),
                'bike_score': _safe_int((_walk or {}).get('bike_score')),
                'school_rating': _safe_float((_schools or {}).get('average_rating')),
                'school_name': (_schools or {}).get('elementary_school') or '',
            },
            'permit_flags': (_permits or {}).get('flags') or [],
            'permit_count': _safe_int((_permits or {}).get('permit_count')),
            'recent_earthquakes': ((_eq or {}).get('recent_earthquakes') or [])[:3],
            'tools_succeeded': research_data.get('tools_succeeded', 0),
            'research_time_ms': research_data.get('research_time_ms', 0),
        }

        try:
            from funnel_tracker import track_from_request
            track_from_request('quick_check_complete', request, metadata={
                'avm': _avm,
                'tools': result['tools_succeeded'],
            })
        except Exception:
            pass

        logging.info(f"📍 Quick check done: AVM=${_avm:,}, {result['tools_succeeded']} tools, {result['research_time_ms']}ms")
        return jsonify(result)

    except Exception as e:
        import traceback
        logging.error(f"Quick check error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Property check failed. Please try again.'}), 500

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
        data = request.get_json(silent=True)
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

        # Circuit breaker — halt if over daily/monthly AI budget
        try:
            from ai_cost_tracker import is_ai_over_budget
            if is_ai_over_budget():
                logging.warning("🚨 Truth Check blocked: AI circuit breaker OPEN (over budget)")
                return jsonify({'error': 'Analysis service is temporarily at capacity. Please try again in a few hours.'}), 503
        except Exception:
            pass

        # Truth check uses PDF document API (Anthropic-specific) — retry on 529/503
        _truth_last_err = None
        for _truth_attempt in range(2):
            try:
                client = _anthropic.Anthropic(api_key=_api_key)
                break
            except Exception as _ce:
                _truth_last_err = _ce
                import time as _t; _t.sleep(2)
        else:
            return jsonify({'error': 'Analysis service temporarily unavailable. Please try again in a moment.'}), 503

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
        _truth_response_text = None
        _truth_last_err = None
        for _truth_attempt in range(2):
            try:
                response = client.messages.create(
                    model="claude-sonnet-4-6",
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
                _truth_response_text = response.content[0].text.strip()
                break
            except _anthropic.APIStatusError as _ae:
                _truth_last_err = _ae
                if _ae.status_code in (429, 500, 503, 529) and _truth_attempt == 0:
                    import time as _t; _t.sleep(3)
                    continue
                break  # non-retryable or second attempt — try OpenAI fallback

        # ── OpenAI fallback: use extracted PDF text if Anthropic failed ──────
        if _truth_response_text is None:
            _oai_key = os.environ.get("OPENAI_API_KEY", "")
            _extracted = _pdf_text_for_grounding or ""
            if _oai_key and len(_extracted) > 200:
                try:
                    import openai as _openai
                    _oai = _openai.OpenAI(api_key=_oai_key)
                    _oai_resp = _oai.chat.completions.create(
                        model="gpt-4o",
                        max_tokens=2000,
                        messages=[{
                            "role": "user",
                            "content": f"SELLER DISCLOSURE DOCUMENT:\n\n{_extracted[:12000]}\n\n{prompt}"
                        }]
                    )
                    _truth_response_text = (_oai_resp.choices[0].message.content or "").strip()
                    logging.info("✅ Truth-check: OpenAI GPT-4o fallback succeeded")
                except Exception as _oai_err:
                    logging.error(f"Truth-check OpenAI fallback failed: {_oai_err}")
            if _truth_response_text is None:
                raise RuntimeError(
                    f"All AI providers failed for truth-check. Last error: {_truth_last_err}"
                )
        # Alias so downstream code continues to work unchanged
        response = type("_R", (), {"content": [type("_C", (), {"text": _truth_response_text})()]})()
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
            model='claude-sonnet-4-6',
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
    Sends email to hello@getofferwise.ai with reply-to set to sender.
    No auth required — public endpoint.
    """
    try:
        data = request.get_json(silent=True)
        
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
        
        # Send to admin with reply-to set to the sender
        sent = send_email(
            to_email=ADMIN_EMAIL,
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



# ── 301 Redirects: CA-specific guides → nationwide equivalents (March 2026) ──
_GUIDE_REDIRECTS = {
    'california-seller-disclosure-guide':        'seller-disclosure-guide',
    'california-natural-hazard-disclosure':       'natural-hazard-disclosure',
    'california-home-inspection-requirements':    'home-inspection-requirements-by-state',
    'buying-house-california-inspection':         'first-time-homebuyer-inspection-guide',
    'as-is-home-sale-california':                 'as-is-home-sale',
    'first-time-homebuyer-inspection-checklist':  'first-time-homebuyer-inspection-guide',
}

@app.route('/guides/<path:guide_name>')
def guides_page(guide_name):
    """Individual guide pages — with 301 redirects for retired CA-specific URLs"""
    slug = guide_name.rstrip('.html').rstrip('/')
    # 301 redirect old CA-specific guides to nationwide equivalents
    if slug in _GUIDE_REDIRECTS:
        return redirect(f'/guides/{_GUIDE_REDIRECTS[slug]}', code=301)
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
        
        data = request.get_json(silent=True)
        plan = data.get('plan', 'bundle_5')

        # Inspector Pro — recurring subscription via Stripe Price ID
        if plan == 'inspector_pro':
            from auth_config import PRICING_TIERS
            price_id = PRICING_TIERS['inspector_pro']['stripe_price_id']
            if not price_id:
                return jsonify({'error': 'Inspector Pro checkout is not yet configured. Please contact support.'}), 400
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

        # Contractor subscriptions — Starter / Pro / Enterprise
        CONTRACTOR_PLANS = {
            'contractor_starter':    {'amount': 4900,  'name': 'Contractor Starter'},
            'contractor_pro':        {'amount': 9900,  'name': 'Contractor Pro'},
            'contractor_enterprise': {'amount': 19900, 'name': 'Contractor Enterprise'},
        }
        if plan in CONTRACTOR_PLANS:
            from auth_config import PRICING_TIERS
            tier = PRICING_TIERS.get(plan, {})
            price_id = tier.get('stripe_price_id', '')
            plan_info = CONTRACTOR_PLANS[plan]
            if not price_id:
                return jsonify({'error': f'{plan_info["name"]} is not yet available for purchase. Please contact support.'}), 400
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{'price': price_id, 'quantity': 1}],
                mode='subscription',
                success_url=url_for('payment_success', session_id='{CHECKOUT_SESSION_ID}', plan=plan, amount=plan_info['amount'], _external=True),
                cancel_url=url_for('for_contractors_landing', _external=True),
                client_reference_id=str(current_user.id),
                metadata={
                    'user_id': str(current_user.id),
                    'plan': plan,
                    'credits': 0,
                }
            )
            logging.info(f"✅ {plan_info['name']} checkout session: {checkout_session.id}")
            return jsonify({'sessionId': checkout_session.id})

        # Buyer subscription plans (monthly recurring)
        from auth_config import PRICING_TIERS
        BUYER_PLANS = ['buyer_starter', 'buyer_pro', 'buyer_unlimited']

        # Legacy one-time credit plans — redirect to equivalent subscription
        LEGACY_MAP = {
            'single':    'buyer_starter',
            'bundle_5':  'buyer_pro',
            'bundle_12': 'buyer_unlimited',
        }
        if plan in LEGACY_MAP:
            plan = LEGACY_MAP[plan]
            logging.info(f"Legacy plan redirected to subscription: {plan}")

        if plan not in BUYER_PLANS:
            return jsonify({'error': 'Invalid plan'}), 400

        tier = PRICING_TIERS.get(plan, {})
        price_id = tier.get('stripe_price_id', '')
        if not price_id:
            return jsonify({'error': f'{tier.get("name", plan)} is not yet configured. Please contact support.'}), 400

        logging.info(f"Creating subscription checkout: {tier['name']} ${tier['price']}/mo")

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            success_url=url_for('payment_success', session_id='{CHECKOUT_SESSION_ID}',
                                plan=plan, amount=tier['price'] * 100, _external=True),
            cancel_url=url_for('pricing', _external=True),
            client_reference_id=str(current_user.id),
            metadata={
                'user_id': str(current_user.id),
                'plan': plan,
                'credits': 0,
            }
        )

        logging.info(f"✅ Subscription checkout session created: {checkout_session.id}")
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
    except stripe.error.SignatureVerificationError as _sig_err:
        # Common causes:
        # 1. STRIPE_WEBHOOK_SECRET is wrong (test vs live key mismatch)
        # 2. Stripe retry arrived >5 minutes after original send (stale timestamp)
        # 3. Malicious probe of the endpoint
        # Check Stripe Dashboard → Webhooks → verify secret matches STRIPE_WEBHOOK_SECRET env var.
        logging.warning(f"⚠️ Stripe webhook signature invalid — check STRIPE_WEBHOOK_SECRET env var. "                        f"Sig header present: {bool(sig_header)}, Secret configured: {bool(webhook_secret)}")
        return jsonify({'error': 'Invalid signature'}), 400
    
    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']

        user_id = session['metadata'].get('user_id')
        credits = int(session['metadata'].get('credits', 0))
        plan = session['metadata'].get('plan')
        amount_total = session.get('amount_total', 0) / 100

        # API subscription — activate key before user check (user_id in metadata)
        if session['metadata'].get('type') == 'api_subscription':
            try:
                from models import APIKey
                import secrets as _sec
                uid = int(user_id or 0)
                api_plan = plan or 'api_starter'
                from auth_config import PRICING_TIERS
                tier_info = PRICING_TIERS.get(api_plan, {})
                mlimit = int(tier_info.get('monthly_limit', 500))
                raw_key = 'ow_live_' + _sec.token_urlsafe(32)
                prefix = raw_key[:12]
                ak = APIKey(
                    user_id=uid,
                    key_hash=_hash_api_key(raw_key),
                    key_prefix=prefix,
                    label=f"{tier_info.get('name','API')} — auto-activated",
                    tier=api_plan,
                    monthly_limit=mlimit,
                    price_per_call=0.0,
                    monthly_fee=float(tier_info.get('price', 0)),
                    billing_email=session.get('customer_email') or '',
                )
                db.session.add(ak)
                db.session.commit()
                # Email the key
                _api_user = User.query.get(uid)
                if _api_user:
                    try:
                        send_email(
                            to_email=_api_user.email,
                            subject='Your OfferWise API key is ready',
                            html_content=(
                                '<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:28px;border-radius:12px">'
                                f'<div style="font-size:20px;font-weight:800;color:#f59e0b;margin-bottom:12px">🔑 Your API Key</div>'
                                f'<p style="color:#94a3b8;margin-bottom:16px">Your OfferWise {tier_info.get("name","API")} subscription is active. Save this key — it will not be shown again.</p>'
                                f'<div style="background:#1e293b;border:1px solid rgba(96,165,250,0.3);border-radius:8px;padding:16px;font-family:monospace;font-size:13px;color:#60a5fa;word-break:break-all;margin-bottom:16px">{raw_key}</div>'
                                f'<p style="color:#94a3b8;font-size:13px">{mlimit} analyses/month · '
                                '<a href="https://www.getofferwise.ai/api/docs" style="color:#f97316">View API docs →</a></p>'
                                '</div>'
                            )
                        )
                    except Exception as _ke:
                        logging.warning(f'API key email failed: {_ke}')
                logging.info(f'✅ API key activated for user {uid}: {prefix}...')
            except Exception as _ae:
                logging.error(f'API key activation failed: {_ae}', exc_info=True)

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
                    track('purchase', source='stripe', user_id=int(user_id),
                          amount_usd=amount_total,
                          metadata={'plan': plan, 'credits': 0, 'amount': amount_total})
                except Exception:
                    pass

            # ── Contractor subscriptions ──────────────────────────
            elif plan in ('contractor_starter', 'contractor_pro', 'contractor_enterprise'):
                from auth_config import PRICING_TIERS
                tier_info = PRICING_TIERS.get(plan, {})
                contractor = Contractor.query.filter_by(email=user.email).first()
                if contractor:
                    contractor.plan = plan
                    contractor.status = 'active'
                    contractor.monthly_lead_limit = tier_info.get('monthly_lead_limit', -1)
                    contractor.stripe_customer_id = session.get('customer') or contractor.stripe_customer_id
                    contractor.subscription_id = session.get('subscription') or contractor.subscription_id
                    contractor.plan_activated_at = datetime.utcnow()
                    db.session.commit()
                    logging.info(f"🔧 {plan} activated for contractor {user.email}")

                    plan_labels = {
                        'contractor_starter':    ('Contractor Starter',    '$49/mo',  '5 leads/month, 3 ZIPs'),
                        'contractor_pro':        ('Contractor Pro',        '$99/mo',  'Unlimited leads, 10 ZIPs, priority matching'),
                        'contractor_enterprise': ('Contractor Enterprise', '$199/mo', 'Unlimited leads, statewide, featured placement'),
                    }
                    label, price, summary = plan_labels[plan]
                    try:
                        send_email(
                            to_email=user.email,
                            subject=f"You\'re on {label} — leads incoming",
                            html_content=(
                                '<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:28px;border-radius:12px;">'
                                f'<div style="font-size:22px;font-weight:800;margin-bottom:8px;">{label} is active 🔧</div>'
                                f'<div style="font-size:14px;color:#94a3b8;margin-bottom:20px;">Hi {contractor.name or "there"} — your subscription is live.</div>'
                                '<div style="padding:16px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:10px;margin-bottom:20px;">'
                                f'<div style="font-weight:700;color:#f97316;margin-bottom:6px;">{label} · {price}</div>'
                                f'<div style="font-size:13px;color:#94a3b8;line-height:1.7;">{summary}<br>'
                                'Leads delivered by email with buyer contact, repair type, and estimated cost.<br>'
                                '<strong style="color:#f1f5f9;">No referral fees. No commissions. No liability.</strong></div></div>'
                                '<div style="font-size:12px;color:#475569;margin-top:16px;">OfferWise makes no guarantee of lead quality, buyer readiness, or job outcomes. '
                                'This is a lead access subscription only. You contact the buyer directly.</div>'
                                '</div>'
                            )
                        )
                    except Exception as e:
                        logging.warning(f"Contractor subscription email failed: {e}")
                else:
                    logging.warning(f"Contractor plan paid but no Contractor record for email {user.email}")
                try:
                    from funnel_tracker import track
                    track('purchase', source='stripe', user_id=int(user_id),
                          amount_usd=amount_total,
                          metadata={'plan': plan, 'credits': 0, 'amount': amount_total})
                except Exception:
                    pass

            elif plan in ('buyer_starter', 'buyer_pro', 'buyer_unlimited'):
                # Buyer subscription activation
                from auth_config import PRICING_TIERS
                from datetime import timedelta as _td_sub
                tier = PRICING_TIERS.get(plan, {})
                user.subscription_plan = plan
                user.stripe_customer_id = session.get('customer') or user.stripe_customer_id
                user.stripe_subscription_id = session.get('subscription') or user.stripe_subscription_id
                user.subscription_status = 'active'
                user.analyses_this_month = 0
                user.analyses_reset_at = datetime.utcnow() + _td_sub(days=30)
                db.session.commit()
                logging.info(f"✅ Buyer subscription {plan} activated for user {user_id} ({user.email})")

                # Inspector referral attribution — mark buyer_converted on purchase
                try:
                    attributed_report = InspectorReport.query.filter_by(
                        user_id=user.id, buyer_registered=True,
                    ).order_by(InspectorReport.created_at.desc()).first()
                    if attributed_report and not attributed_report.buyer_converted:
                        attributed_report.buyer_converted = True
                        insp = Inspector.query.get(attributed_report.inspector_id)
                        if insp:
                            insp.total_buyers_converted = (insp.total_buyers_converted or 0) + 1
                            db.session.commit()
                            logging.info(f"Inspector #{insp.id} conversion: buyer {user.email} purchased {plan} (${amount_total})")
                except Exception as _ref_err:
                    logging.warning(f"Inspector referral attribution failed: {_ref_err}")

                plan_labels = {
                    'buyer_starter':   ('Starter', '$9/mo',  '10 analyses/month'),
                    'buyer_pro':       ('Pro',     '$19/mo', '30 analyses/month'),
                    'buyer_unlimited': ('Unlimited','$49/mo','Unlimited analyses'),
                }
                label, price, summary = plan_labels.get(plan, (plan, '', ''))
                try:
                    send_email(
                        to_email=user.email,
                        subject=f"You're on OfferWise {label} — you're ready to analyze",
                        html_content=(
                            f'<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:28px;border-radius:12px;">'
                            f'<div style="font-size:22px;font-weight:800;margin-bottom:8px;">OfferWise {label} is active ✅</div>'
                            f'<div style="font-size:14px;color:#94a3b8;margin-bottom:20px;">Hi {user.name or "there"} — your subscription is live. {summary}.</div>'
                            f'<div style="padding:16px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:10px;margin-bottom:20px;">'
                            f'<div style="font-weight:700;color:#f97316;margin-bottom:6px;">{label} · {price}</div>'
                            f'<div style="font-size:13px;color:#94a3b8;">Upload your inspection report and seller disclosure to get started. No credits needed — just analyze.</div></div>'
                            f'<a href="https://www.getofferwise.ai/app" style="display:inline-block;padding:12px 24px;background:linear-gradient(90deg,#f97316,#f59e0b);color:white;border-radius:10px;font-weight:700;text-decoration:none;">Analyze a Property →</a>'
                            f'</div>'
                        )
                    )
                except Exception as e:
                    logging.warning(f"Buyer subscription email failed: {e}")

                try:
                    from funnel_tracker import track
                    track('purchase', source='stripe', user_id=int(user_id),
                          amount_usd=amount_total,
                          metadata={'plan': plan, 'amount': amount_total})
                except Exception:
                    pass

            else:
                # Legacy credit purchase (kept for backward compat — no longer shown in UI)
                user.analysis_credits += credits
                user.stripe_customer_id = user.stripe_customer_id or session.get('customer')
                db.session.commit()
                logging.info(f"Legacy: added {credits} credits to user {user_id} for plan {plan}")

                # Inspector referral attribution — mark buyer_converted on purchase
                try:
                    attributed_report = InspectorReport.query.filter_by(
                        user_id=user.id, buyer_registered=True,
                    ).order_by(InspectorReport.created_at.desc()).first()
                    if attributed_report and not attributed_report.buyer_converted:
                        attributed_report.buyer_converted = True
                        insp = Inspector.query.get(attributed_report.inspector_id)
                        if insp:
                            insp.total_buyers_converted = (insp.total_buyers_converted or 0) + 1
                            db.session.commit()
                            logging.info(f"Inspector #{insp.id} ({insp.business_name}) conversion: buyer {user.email} purchased {credits} credits (${amount_total})")
                except Exception as _ref_err:
                    logging.warning(f"Inspector referral attribution failed (non-critical): {_ref_err}")
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
    
    # ── Subscription cancelled / expired → downgrade to free ──────────────
    elif event['type'] in ('customer.subscription.deleted', 'customer.subscription.updated'):
        sub = event['data']['object']
        customer_id = sub.get('customer')
        status = sub.get('status')  # 'canceled', 'unpaid', 'past_due', 'active', etc.

        # Handle buyer subscription downgrade
        if status in ('canceled', 'unpaid', 'past_due'):
            buyer = User.query.filter_by(stripe_customer_id=customer_id).first()
            if buyer and getattr(buyer, 'subscription_plan', 'free') in ('buyer_starter', 'buyer_pro', 'buyer_unlimited'):
                buyer.subscription_plan = 'free'
                buyer.subscription_status = status
                buyer.analyses_this_month = 0
                db.session.commit()
                logging.info(f"Buyer {buyer.email} downgraded to free — subscription {status}")
                try:
                    send_email(
                        to_email=buyer.email,
                        subject="Your OfferWise subscription has ended",
                        html_content=(
                            f'<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;">'
                            f'<div style="font-size:20px;font-weight:800;margin-bottom:8px;">Your subscription has ended</div>'
                            f'<p style="color:#94a3b8;">Hi {buyer.name or "there"} — your OfferWise subscription is no longer active. You can still use any referral credits you have earned.</p>'
                            f'<a href="https://www.getofferwise.ai/pricing" style="display:inline-block;padding:12px 24px;background:linear-gradient(90deg,#f97316,#f59e0b);color:white;border-radius:10px;font-weight:700;text-decoration:none;">Resubscribe →</a>'
                            f'</div>'
                        )
                    )
                except Exception: pass

        # Only act on terminal states
        if event['type'] == 'customer.subscription.deleted' or status in ('canceled', 'unpaid'):
            try:
                # Find inspector by stripe_customer_id (stored on User)
                user = User.query.filter_by(stripe_customer_id=customer_id).first()
                if user:
                    insp = Inspector.query.filter_by(user_id=user.id).first()
                    if insp and insp.plan == 'inspector_pro':
                        insp.plan = 'free'
                        insp.monthly_quota = 5
                        db.session.commit()
                        logging.info(f"⬇️  Inspector Pro cancelled → free for user {user.id} ({user.email})")
                        try:
                            send_email(
                                to_email=user.email,
                                subject="Your Inspector Pro subscription has ended",
                                html_content=(
                                    '<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:28px;border-radius:12px;">'
                                    '<div style="font-size:20px;font-weight:800;margin-bottom:8px;">Inspector Pro has ended</div>'
                                    f'<div style="font-size:14px;color:#94a3b8;margin-bottom:20px;">Hi {user.name or "there"} — your Inspector Pro subscription has been cancelled. '
                                    'You\'ve been moved back to the Free plan (5 analyses/month).</div>'
                                    '<a href="https://www.getofferwise.ai/for-inspectors#pricing" '
                                    'style="display:inline-block;padding:12px 24px;background:linear-gradient(90deg,#f97316,#f59e0b);'
                                    'color:white;border-radius:10px;font-weight:700;text-decoration:none;">Resubscribe →</a>'
                                    '</div>'
                                )
                            )
                        except Exception as e:
                            logging.warning(f"Inspector cancellation email failed: {e}")

                # Find contractor by stripe_customer_id
                contractor = Contractor.query.filter_by(stripe_customer_id=customer_id).first()
                if contractor and contractor.plan != 'free':
                    old_plan = contractor.plan
                    contractor.plan = 'free'
                    contractor.status = 'paused'
                    contractor.monthly_lead_limit = 0
                    contractor.subscription_id = None
                    db.session.commit()
                    logging.info(f"⬇️  Contractor {old_plan} cancelled → free for {contractor.email}")
                    try:
                        send_email(
                            to_email=contractor.email,
                            subject="Your OfferWise contractor subscription has ended",
                            html_content=(
                                '<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:28px;border-radius:12px;">'
                                '<div style="font-size:20px;font-weight:800;margin-bottom:8px;">Subscription ended</div>'
                                f'<div style="font-size:14px;color:#94a3b8;margin-bottom:20px;">Hi {contractor.name or "there"} — your contractor subscription has been cancelled. '
                                'Your account is paused and leads will not be sent until you resubscribe.</div>'
                                '<a href="https://www.getofferwise.ai/for-contractors#pricing" '
                                'style="display:inline-block;padding:12px 24px;background:linear-gradient(90deg,#f97316,#f59e0b);'
                                'color:white;border-radius:10px;font-weight:700;text-decoration:none;">Resubscribe →</a>'
                                '</div>'
                            )
                        )
                    except Exception as e:
                        logging.warning(f"Contractor cancellation email failed: {e}")

            except Exception as e:
                logging.error(f"Subscription cancellation handler failed: {e}", exc_info=True)

    # ── Invoice paid → reset monthly counters + B2B API revenue tracking ──
    elif event['type'] == 'invoice.paid':
        invoice = event['data']['object']
        customer_id = invoice.get('customer')
        amount_paid = (invoice.get('amount_paid') or 0) / 100

        try:
            # Reset monthly usage counters on subscription renewal
            user = User.query.filter_by(stripe_customer_id=customer_id).first()
            if user:
                insp = Inspector.query.filter_by(user_id=user.id).first()
                if insp and insp.plan == 'inspector_pro':
                    insp.monthly_used = 0
                    insp.quota_reset_at = datetime.utcnow()
                    db.session.commit()
                    logging.info(f"🔄 Inspector Pro monthly quota reset for {user.email}")

            contractor = Contractor.query.filter_by(stripe_customer_id=customer_id).first()
            if contractor:
                contractor.leads_sent_month = 0
                db.session.commit()
                logging.info(f"🔄 Contractor monthly lead count reset for {contractor.email}")

            # B2B API: reset monthly revenue counter on invoice paid
            from models import APIKey
            api_keys = APIKey.query.filter(
                APIKey.is_active == True,
                APIKey.billing_email == invoice.get('customer_email')
            ).all()
            for key in api_keys:
                if (key.revenue_month or 0) > 0:
                    logging.info(f"💰 B2B invoice paid: ${amount_paid:.2f} for key {key.key_prefix}... — resetting revenue_month")
                    key.revenue_month = 0
                    key.calls_month = 0
            db.session.commit()

        except Exception as e:
            logging.error(f"invoice.paid handler failed: {e}", exc_info=True)

    # ── Invoice payment failed → notify user ──────────────────────────────
    elif event['type'] == 'invoice.payment_failed':
        invoice = event['data']['object']
        customer_id = invoice.get('customer')
        attempt = invoice.get('attempt_count', 1)

        try:
            user = User.query.filter_by(stripe_customer_id=customer_id).first()
            email_to = None
            name_to = 'there'
            if user:
                email_to = user.email
                name_to = user.name or 'there'
            else:
                contractor = Contractor.query.filter_by(stripe_customer_id=customer_id).first()
                if contractor:
                    email_to = contractor.email
                    name_to = contractor.name or 'there'

            if email_to and attempt <= 2:  # Only email on first two failures
                send_email(
                    to_email=email_to,
                    subject="Action required: payment failed for your OfferWise subscription",
                    html_content=(
                        '<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:28px;border-radius:12px;">'
                        '<div style="font-size:20px;font-weight:800;margin-bottom:8px;color:#ef4444;">⚠️ Payment failed</div>'
                        f'<div style="font-size:14px;color:#94a3b8;margin-bottom:20px;">Hi {name_to} — we couldn\'t process your OfferWise subscription payment. '
                        'Please update your payment method to keep your subscription active.</div>'
                        '<a href="https://billing.stripe.com/p/login/test_28o3e0" '
                        'style="display:inline-block;padding:12px 24px;background:#ef4444;'
                        'color:white;border-radius:10px;font-weight:700;text-decoration:none;">Update Payment Method →</a>'
                        '<div style="margin-top:16px;font-size:12px;color:#475569;">Stripe will retry automatically. '
                        'Your subscription will be cancelled after multiple failed attempts.</div>'
                        '</div>'
                    )
                )
                logging.info(f"⚠️  Payment failed email sent to {email_to} (attempt {attempt})")
        except Exception as e:
            logging.error(f"invoice.payment_failed handler failed: {e}", exc_info=True)

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
        data = request.get_json(silent=True)
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
        data = request.get_json(silent=True)
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
        data = request.get_json(silent=True)
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
        data = request.get_json(silent=True)
        
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
        try:
            from funnel_tracker import track_from_request
            track_from_request('negotiation_doc_generated', request,
                               user_id=current_user.id if current_user.is_authenticated else None,
                               metadata={'doc_type': document_type})
        except Exception: pass
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


@app.route('/api/mcp/gmail/send-objection', methods=['POST'])
@login_required
def gmail_send_objection_letter():
    """
    Gmail MCP action: AI composes a negotiation objection letter and sends it
    to the buyer's realtor via Gmail — buyer never opens their email client.

    POST body:
        analysis_id   (int, required)
        to_email      (str, required) — realtor's email
        to_name       (str) — realtor's name
        buyer_name    (str)
        style         (str) 'firm' | 'balanced' | 'conciliatory'

    Returns:
        { success, message_id, subject, preview }
    """
    try:
        import anthropic
        data = request.get_json(silent=True) or {}
        analysis_id = data.get('analysis_id')
        to_email = (data.get('to_email') or '').strip()

        if not analysis_id:
            return jsonify({'success': False, 'error': 'analysis_id is required.'}), 400
        if not to_email or '@' not in to_email:
            return jsonify({'success': False, 'error': 'A valid realtor email address is required.'}), 400

        analysis_rec = Analysis.query.filter_by(
            id=analysis_id, user_id=current_user.id
        ).first()
        if not analysis_rec:
            return jsonify({'success': False, 'error': 'Analysis not found.'}), 404

        result = json.loads(analysis_rec.result_json or '{}')
        prop = Property.query.get(analysis_rec.property_id)
        address = prop.address if prop else ''
        asking_price = prop.price if prop else 0

        buyer_name = (data.get('buyer_name') or current_user.email or 'Buyer').strip()
        to_name = (data.get('to_name') or 'Agent').strip()
        style = data.get('style', 'balanced')

        # Extract key figures
        repair_estimate = result.get('repair_estimate', {})
        total_low = repair_estimate.get('total_low', 0)
        total_high = repair_estimate.get('total_high', 0)
        offer_strategy = result.get('offer_strategy', {})
        rec_offer = (
            offer_strategy.get('recommended_offer') or
            result.get('recommended_offer') or
            (asking_price * 0.97 if asking_price else 0)
        )
        risk_score = result.get('risk_score', {})
        deal_breakers = risk_score.get('deal_breakers', [])
        leverage_pts = (
            result.get('negotiation_strategy', {}).get('leverage_points') or
            result.get('negotiation_strategy', {}).get('talking_points') or []
        )

        tone_instruction = {
            'firm': 'Firm and assertive. State the position directly. No hedging.',
            'balanced': 'Professional and reasoned. Evidence-based. Collaborative in tone but clear on the ask.',
            'conciliatory': 'Warm and cooperative. Acknowledge the seller\'s position while making the ask clearly.',
        }.get(style, 'Professional and reasoned.')

        db_summary = '; '.join([
            db.get('issue', str(db)) if isinstance(db, dict) else str(db)
            for db in deal_breakers[:3]
        ]) or 'none critical'

        leverage_summary = '; '.join([
            lp.get('point', str(lp)) if isinstance(lp, dict) else str(lp)
            for lp in leverage_pts[:4]
        ]) or 'documented repair costs'

        prompt = f"""Draft a professional real estate negotiation email from a buyer to a seller's listing agent.

FROM: {buyer_name}
TO: {to_name} (listing agent)
PROPERTY: {address}
ASKING PRICE: ${asking_price:,.0f}
BUYER'S OFFER POSITION: ${rec_offer:,.0f}
DOCUMENTED REPAIR COSTS: ${total_low:,.0f}–${total_high:,.0f}
KEY DEAL BREAKERS: {db_summary}
LEVERAGE POINTS: {leverage_summary}
TONE: {tone_instruction}

Write a complete email (subject line + body) that:
1. Opens respectfully, references the property and inspection
2. States the buyer's offer or counter position clearly: ${rec_offer:,.0f}
3. Cites 2–3 specific documented findings as justification (use the leverage points above)
4. References the total repair cost range as basis for the price position
5. Closes with a clear call to action and timeline
6. Remains under 250 words — agents read fast

Format:
SUBJECT: [subject line here]

[email body here]"""

        from ai_client import get_ai_response as _get_ai
        composed = _get_ai(prompt, max_tokens=600).strip()

        # Parse subject and body
        subject = f"Re: {address} — Offer Position"
        body = composed
        if composed.startswith('SUBJECT:'):
            lines = composed.split('\n', 2)
            subject_line = lines[0].replace('SUBJECT:', '').strip()
            if subject_line:
                subject = subject_line
            body = '\n'.join(lines[2:]).strip() if len(lines) > 2 else composed

        # Send via Gmail MCP using Anthropic client with MCP server
        gmail_mcp_url = 'https://gmail.mcp.claude.com/mcp'
        try:
            mcp_client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))
            mcp_response = mcp_client.beta.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=200,
                mcp_servers=[{'type': 'url', 'url': gmail_mcp_url, 'name': 'gmail'}],
                messages=[{
                    'role': 'user',
                    'content': f'Send an email to {to_email} with subject "{subject}" and body:\n\n{body}\n\nPlease send this email now and confirm the message ID.'
                }],
                betas=['mcp-client-2025-04-04'],
            )
            # Extract message ID from MCP response
            mcp_text = ' '.join([
                blk.text for blk in mcp_response.content
                if hasattr(blk, 'text')
            ])
            gmail_sent = True
            message_id = None
            import re as _re
            id_match = _re.search(r'message[_\s]?id[:\s]+([A-Za-z0-9_\-]+)', mcp_text, _re.I)
            if id_match:
                message_id = id_match.group(1)
        except Exception as mcp_err:
            logging.warning(f"Gmail MCP send failed, falling back to Resend: {mcp_err}")
            gmail_sent = False
            message_id = None

        # Fallback: send via Resend if MCP unavailable
        if not gmail_sent:
            try:
                import resend
                resend.api_key = os.environ.get('RESEND_API_KEY', '')
                html_body = f"<pre style='font-family:sans-serif;white-space:pre-wrap;'>{body}</pre>"
                r = resend.Emails.send({
                    'from': os.environ.get('FROM_EMAIL', 'OfferWise <noreply@getofferwise.ai>'),
                    'reply_to': current_user.email,
                    'to': [to_email],
                    'subject': subject,
                    'html': html_body,
                })
                message_id = getattr(r, 'id', None)
                gmail_sent = True
            except Exception as resend_err:
                logging.error(f"Resend fallback also failed: {resend_err}")

        try:
            from ai_cost_tracker import log_ai_call
            log_ai_call(
                db.session, user_id=current_user.id,
                model='claude-sonnet-4-6', feature='gmail_objection_letter',
                input_tokens=0, output_tokens=0,
            )
        except Exception:
            pass

        return jsonify({
            'success': gmail_sent,
            'message_id': message_id,
            'subject': subject,
            'preview': body[:300],
            'full_body': body,
            'sent_to': to_email,
            'via': 'gmail_mcp' if gmail_sent else 'failed',
        })

    except Exception as e:
        logging.error(f"❌ Gmail MCP objection letter error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'An internal error occurred. Please try again.'}), 500


@app.route('/api/mcp/calendar/schedule-deadlines', methods=['POST'])
@login_required
def calendar_schedule_deadlines():
    """
    Google Calendar MCP action: reads escrow timeline from analysis and
    auto-creates calendar events for inspection response deadline,
    contingency removal, and close of escrow.

    POST body:
        analysis_id        (int, required)
        offer_accepted_date (str) ISO date — e.g. '2026-03-20'
        inspection_days    (int) default 10 — inspection contingency period
        response_days      (int) default 3  — seller response deadline after inspection
        contingency_days   (int) default 21 — contingency removal
        coe_days           (int) default 30 — close of escrow

    Returns:
        { success, events_created: [ {title, date, calendar_event_id} ] }
    """
    try:
        import anthropic
        data = request.get_json(silent=True) or {}
        analysis_id = data.get('analysis_id')
        if not analysis_id:
            return jsonify({'success': False, 'error': 'analysis_id is required.'}), 400

        analysis_rec = Analysis.query.filter_by(
            id=analysis_id, user_id=current_user.id
        ).first()
        if not analysis_rec:
            return jsonify({'success': False, 'error': 'Analysis not found.'}), 404

        prop = Property.query.get(analysis_rec.property_id)
        address = prop.address if prop else 'Your Property'

        from datetime import date, timedelta
        raw_date = data.get('offer_accepted_date') or date.today().isoformat()
        try:
            base = date.fromisoformat(raw_date)
        except ValueError:
            base = date.today()

        inspection_days  = int(data.get('inspection_days', 10))
        response_days    = int(data.get('response_days', 3))
        contingency_days = int(data.get('contingency_days', 21))
        coe_days         = int(data.get('coe_days', 30))

        events = [
            {
                'title': f'🔍 Inspection Deadline — {address}',
                'date': (base + timedelta(days=inspection_days)).isoformat(),
                'description': 'Inspection contingency period ends. All inspections must be completed by this date.',
                'reminder_hours': 48,
            },
            {
                'title': f'📋 Seller Response Deadline — {address}',
                'date': (base + timedelta(days=inspection_days + response_days)).isoformat(),
                'description': 'Deadline for seller to respond to Request for Repair. Review response with your agent.',
                'reminder_hours': 24,
            },
            {
                'title': f'📝 Contingency Removal — {address}',
                'date': (base + timedelta(days=contingency_days)).isoformat(),
                'description': 'All contingencies must be removed or waived. Confirm with your agent before signing.',
                'reminder_hours': 48,
            },
            {
                'title': f'🏠 Close of Escrow — {address}',
                'date': (base + timedelta(days=coe_days)).isoformat(),
                'description': 'Closing day. Confirm final walkthrough, wire transfer, and key handoff with your agent.',
                'reminder_hours': 72,
            },
        ]

        # Create events via Google Calendar MCP
        gcal_mcp_url = 'https://gcal.mcp.claude.com/mcp'
        created_events = []
        mcp_errors = []

        for evt in events:
            try:
                mcp_client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))
                mcp_response = mcp_client.beta.messages.create(
                    model='claude-sonnet-4-6',
                    max_tokens=200,
                    mcp_servers=[{'type': 'url', 'url': gcal_mcp_url, 'name': 'google-calendar'}],
                    messages=[{
                        'role': 'user',
                        'content': (
                            f'Create a Google Calendar event: title "{evt["title"]}", '
                            f'date {evt["date"]} (all-day), '
                            f'description "{evt["description"]}", '
                            f'add a reminder {evt["reminder_hours"]} hours before. '
                            f'Confirm the event ID after creating it.'
                        )
                    }],
                    betas=['mcp-client-2025-04-04'],
                )
                mcp_text = ' '.join([
                    blk.text for blk in mcp_response.content if hasattr(blk, 'text')
                ])
                import re as _re2
                eid_match = _re2.search(r'event[_\s]?id[:\s]+([A-Za-z0-9_@\-\.]+)', mcp_text, _re2.I)
                created_events.append({
                    'title': evt['title'],
                    'date': evt['date'],
                    'calendar_event_id': eid_match.group(1) if eid_match else None,
                    'created': True,
                })
            except Exception as evt_err:
                logging.warning(f"Calendar MCP failed for event '{evt['title']}': {evt_err}")
                mcp_errors.append(evt['title'])
                created_events.append({
                    'title': evt['title'],
                    'date': evt['date'],
                    'calendar_event_id': None,
                    'created': False,
                    'error': str(evt_err),
                })

        success_count = sum(1 for e in created_events if e['created'])

        return jsonify({
            'success': success_count > 0,
            'events_created': created_events,
            'success_count': success_count,
            'total': len(events),
            'address': address,
            'base_date': base.isoformat(),
        })

    except Exception as e:
        logging.error(f"❌ Calendar MCP schedule error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'An internal error occurred. Please try again.'}), 500


# ============================================================================
# ADMIN - DATABASE HEALTH
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

@app.errorhandler(403)
def forbidden_error(error):
    """Handle 403 - no traceback logging needed, this is expected behavior"""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Forbidden'}), 403
    return send_from_directory('static', '404.html'), 403

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
        admin_key = request.args.get('admin_key', '') or request.headers.get('X-Admin-Key', '')
        is_admin_req = admin_key and any(admin_key == k for k in [
            os.environ.get('ADMIN_KEY'), os.environ.get('TURK_ADMIN_KEY')
        ] if k)
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

@app.route('/signup')
def signup_redirect():
    """Redirect /signup → /login?signup (used by Google Ads and external links)."""
    ref = request.args.get('ref', '')
    dest = '/login?signup'
    if ref:
        dest += f'&ref={ref}'
    return redirect(dest, 301)


@app.route('/')
def index():
    """Landing page - captures referral codes and UTM params from URL"""
    # Capture referral code
    referral_code = request.args.get('ref')
    if referral_code:
        session['referral_code'] = referral_code.strip().upper()
        logging.info(f"🎁 Captured referral code from URL: {referral_code}")
    # Persist UTM params for signup attribution
    for _k in ('utm_source','utm_medium','utm_campaign','utm_content','utm_term'):
        _v = request.args.get(_k)
        if _v:
            session[_k] = _v
    # Track funnel visit
    try:
        from funnel_tracker import track_from_request
        track_from_request('visit', request)
    except Exception:
        pass

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

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/x-icon',
                                max_age=86400)


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
    """Return analytics/pixel IDs for client-side loading.
    Env vars needed:
      GA4_MEASUREMENT_ID        — Google Analytics 4 (e.g. G-XXXXXXXXXX)
      GOOGLE_ADS_CONVERSION_ID  — Google Ads account tag (e.g. AW-XXXXXXXXX)
      GOOGLE_ADS_SIGNUP_LABEL   — Conversion action label for signup (e.g. AbCdEfGhIj)
      REDDIT_PIXEL_ID           — Reddit Ads pixel ID (e.g. t2_xxxxxxxx)
    """
    return jsonify({
        'ga4_id':               os.environ.get('GA4_MEASUREMENT_ID', ''),
        'gads_id':              os.environ.get('GOOGLE_ADS_CONVERSION_ID', ''),
        'gads_signup_label':    os.environ.get('GOOGLE_ADS_SIGNUP_LABEL', ''),
        'gads_purchase_label':  os.environ.get('GOOGLE_ADS_PURCHASE_LABEL', ''),
        'reddit_pixel_id':      os.environ.get('REDDIT_PIXEL_ID', 'a2_ihx65g62d5n6'),
    })


@app.route('/api/config/new-signup', methods=['GET'])
def api_config_new_signup():
    """Returns {new_signup: true} once after account creation, then clears the flag.
    app.html reads this on load and fires conversion pixels if true.
    """
    is_new = bool(session.pop('new_signup', False))
    email = current_user.email if current_user.is_authenticated else ''
    return jsonify({'new_signup': is_new, 'email': email if is_new else ''})


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

# /auto-test-admin and /test-admin → handled by admin_routes.py


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

@app.route('/api/admin/users')
@api_admin_required
def admin_users_list():
    """Lightweight user list with signup source."""
    try:
        from models import GTMFunnelEvent
        from sqlalchemy import func as _fn
        users = User.query.order_by(User.created_at.desc()).limit(200).all()
        _sources = {}
        try:
            for ev in GTMFunnelEvent.query.filter(
                GTMFunnelEvent.stage == 'signup',
                GTMFunnelEvent.user_id.isnot(None)
            ).all():
                if ev.user_id not in _sources:
                    _sources[ev.user_id] = ev.source or 'direct'
        except Exception:
            pass
        _counts = dict(
            db.session.query(Property.user_id, _fn.count(Property.id))
            .group_by(Property.user_id).all()
        )
        result = [{
            'email': u.email,
            'analyses': _counts.get(u.id, 0),
            'purchased': bool(u.stripe_customer_id),
            'tier': getattr(u, 'subscription_plan', None) or u.tier or 'free',
            'signup_source': _sources.get(u.id, 'unknown'),
            'terms_accepted': bool(u.terms_accepted_at),
            'joined': u.created_at.isoformat() if u.created_at else None,
            'last_login': u.last_login.isoformat() if u.last_login else None,
        } for u in users]
        return jsonify({'users': result})
    except Exception:
        return jsonify({'users': []}), 500


@app.route('/api/admin/traffic')
@api_admin_required
def admin_traffic_sources():
    """Visit and signup counts by source for the last N days."""
    from models import GTMFunnelEvent
    from gtm.conversion_intel import _normalize_channel
    from sqlalchemy import func
    days = int(request.args.get('days', 30))
    since = datetime.utcnow() - timedelta(days=days)

    rows = db.session.query(
        GTMFunnelEvent.stage,
        GTMFunnelEvent.source,
        GTMFunnelEvent.medium,
        func.count(GTMFunnelEvent.id)
    ).filter(
        GTMFunnelEvent.created_at >= since,
        GTMFunnelEvent.stage.in_(['visit', 'signup'])
    ).group_by(GTMFunnelEvent.stage, GTMFunnelEvent.source, GTMFunnelEvent.medium).all()

    visits = {}
    signups = {}
    for stage, source, medium, cnt in rows:
        channel = _normalize_channel(source or '', medium or '')
        if stage == 'visit':
            visits[channel] = visits.get(channel, 0) + cnt
        elif stage == 'signup':
            signups[channel] = signups.get(channel, 0) + cnt

    return jsonify({
        'days': days,
        'visits': visits,
        'signups': signups,
        'total_visits': sum(visits.values()),
        'total_signups': sum(signups.values()),
    })


@app.route('/api/admin/consent-summary')
@api_admin_required
def admin_consent_summary():
    """Summary of legal agreement acceptance."""
    from models import ConsentRecord
    from sqlalchemy import func

    total_users = User.query.count()
    terms_accepted = User.query.filter(User.terms_accepted_at.isnot(None)).count()

    # Consent records by type
    by_type = dict(
        db.session.query(ConsentRecord.consent_type, func.count(ConsentRecord.id))
        .filter(ConsentRecord.revoked == False)
        .group_by(ConsentRecord.consent_type).all()
    )

    # Unique users with any consent record
    unique_consented = db.session.query(
        func.count(func.distinct(ConsentRecord.user_id))
    ).filter(ConsentRecord.revoked == False).scalar() or 0

    # Recent consents (last 10)
    recent = ConsentRecord.query.filter_by(revoked=False)\
        .order_by(ConsentRecord.consented_at.desc()).limit(10).all()
    recent_list = []
    for c in recent:
        u = User.query.get(c.user_id)
        recent_list.append({
            'email': u.email if u else '?',
            'type': c.consent_type,
            'version': c.consent_version,
            'date': c.consented_at.isoformat() if c.consented_at else None,
        })

    return jsonify({
        'total_users': total_users,
        'terms_accepted': terms_accepted,
        'terms_pct': round(terms_accepted / total_users * 100, 1) if total_users else 0,
        'unique_consented': unique_consented,
        'by_type': by_type,
        'recent': recent_list,
    })


@app.route('/api/admin/daily-activity')
@api_admin_required
def admin_daily_activity():

    """Lightweight endpoint: just 30 days of signups/analyses/logins."""
    try:
        now = datetime.utcnow()
        daily = []
        for days_ago in range(30):
            day = now - timedelta(days=days_ago)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            daily.append({
                'date': day.strftime('%m/%d'),
                'signups': User.query.filter(User.created_at >= day_start, User.created_at < day_end).count(),
                'analyses': Property.query.filter(Property.created_at >= day_start, Property.created_at < day_end).count(),
                'logins': User.query.filter(User.last_login >= day_start, User.last_login < day_end).count(),
            })
        daily.reverse()
        return jsonify({'daily': daily})
    except Exception:
        return jsonify({'error': 'Failed to load daily activity'}), 500


@app.route('/api/analytics')
@api_admin_required
def get_analytics():
    """Get analytics data for admin dashboard"""
    # Admin check handled by @api_admin_required decorator
    
    try:
        from sqlalchemy import func
        
        # User metrics — exclude persona/test accounts from all counts
        _test_domains = tuple(TEST_EMAIL_DOMAINS)
        def _excl_test(q):
            for d in _test_domains:
                q = q.filter(~User.email.endswith(d))
            return q
        total_users = _excl_test(User.query).count()
        
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
        active_7d = _excl_test(User.query.filter(User.last_login >= week_ago)).count()

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

        # Pre-fetch signup sources from funnel events
        _signup_sources = {}
        try:
            from models import GTMFunnelEvent
            _signup_events = GTMFunnelEvent.query.filter(
                GTMFunnelEvent.stage == 'signup',
                GTMFunnelEvent.user_id.isnot(None)
            ).all()
            for ev in _signup_events:
                if ev.user_id not in _signup_sources:
                    _signup_sources[ev.user_id] = {
                        'source': ev.source or 'direct',
                        'medium': ev.medium or '',
                    }
        except Exception:
            pass

        for user in all_users:
            user_usage = next((u for u in usage_stats if u.user_id == user.id), None)
            analyses_count = user_usage.total_analyses if user_usage and user_usage.total_analyses else 0
            _src = _signup_sources.get(user.id, {})
            all_users_data.append({
                'email': user.email,
                'analyses': analyses_count,
                'credits': user.analysis_credits or 0,
                'tier': user.tier or 'free',
                'purchased': bool(user.stripe_customer_id),
                'referral_code': getattr(user, 'referral_code', None) or '',
                'signup_source': _src.get('source', 'unknown'),
                'signup_medium': _src.get('medium', ''),
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
                'analyses': day_analyses,
                'count': day_analyses,
                'action': 'analysis run' if day_analyses == 1 else 'analyses run',
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
        data = request.get_json(silent=True) or {}
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

    is_admin_user = current_user.is_authenticated and current_user.email in ADMIN_EMAILS

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

    # Circuit breaker — bug fixer is non-essential, skip when over budget
    try:
        from ai_cost_tracker import is_ai_over_budget
        if is_ai_over_budget():
            logging.warning("🚨 Bug fixer blocked: AI circuit breaker OPEN (over budget)")
            return {"error": "AI analysis temporarily unavailable (budget limit reached)."}
    except Exception:
        pass

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
        from ai_client import get_ai_response as _get_ai
        response_text = _get_ai(prompt, max_tokens=2000)
        
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


@app.route('/api/market-refresh', methods=['POST'])
@login_required
def api_market_refresh():
    """Fetch fresh market data for a stored analysis and patch it back to DB."""
    try:
        data = request.get_json(silent=True) or {}
        property_id = data.get('property_id')
        address = (data.get('address') or '').strip()
        price = int(data.get('price') or 0)

        if not address or len(address) < 10 or price <= 0:
            return jsonify({'error': 'Invalid input'}), 400

        from property_research_agent import PropertyResearchAgent
        from market_intelligence import MarketIntelligenceEngine, apply_market_adjustment

        agent = PropertyResearchAgent()
        research_data = agent.research(address)
        if not research_data or not research_data.get('tool_results'):
            return jsonify({'market_applied': False, 'error': 'No research data'}), 200

        mi_obj = MarketIntelligenceEngine().from_research_data(research_data, price, address)
        if not mi_obj or mi_obj.data_quality == 'none':
            return jsonify({'market_applied': False, 'error': 'No market data'}), 200

        market_result = apply_market_adjustment(price, price, mi_obj)
        if not market_result.get('market_applied'):
            return jsonify({'market_applied': False}), 200

        market_context = {
            'market_applied': True,
            'market_temperature': market_result.get('market_temperature', ''),
            'buyer_leverage': market_result.get('buyer_leverage', ''),
            'estimated_value': market_result.get('estimated_value', 0),
            'avg_dom': getattr(mi_obj.market, 'average_days_on_market', 0) if mi_obj.market else 0,
            'asking_vs_avm_pct': market_result.get('asking_vs_avm_pct', 0),
            'comp_count': market_result.get('comp_count', 0),
            'comp_median_price': market_result.get('comp_median_price', 0),
            'comp_avg_dom': getattr(mi_obj, 'comp_avg_dom', 0),
            'comp_avg_price_per_sqft': getattr(mi_obj, 'comp_avg_price_per_sqft', 0),
            'asking_vs_comps_pct': getattr(mi_obj, 'asking_vs_comps_pct', 0),
            'market_adjustment_amount': market_result.get('market_adjustment_amount', 0),
            'market_rationale': market_result.get('rationale', ''),
            'avm_range_low': getattr(mi_obj, 'value_range_low', 0),
            'avm_range_high': getattr(mi_obj, 'value_range_high', 0),
            'zip_code': getattr(mi_obj.market, 'zip_code', '') if mi_obj.market else '',
            'total_listings': getattr(mi_obj.market, 'total_listings', 0) if mi_obj.market else 0,
            'median_price_per_sqft': getattr(mi_obj.market, 'median_price_per_sqft', 0) if mi_obj.market else 0,
            'market_intelligence': mi_obj.to_dict(),
        }

        # Persist back to DB so this never needs to run again
        if property_id:
            try:
                prop = Property.query.filter_by(id=property_id, user_id=current_user.id).first()
                if prop and prop.analysis:
                    stored = json.loads(prop.analysis.result_json or '{}')
                    if 'offer_strategy' not in stored:
                        stored['offer_strategy'] = {}
                    stored['offer_strategy']['market_context'] = market_context
                    stored['market_intelligence'] = mi_obj.to_dict()
                    stored.pop('_needs_market_refresh', None)
                    prop.analysis.result_json = json.dumps(stored)
                    db.session.commit()
                    logging.info(f"💾 Market data persisted to analysis for property {property_id}")
            except Exception as _pe:
                db.session.rollback()
                logging.warning(f"⚠️ Market persist failed: {_pe}")

        return jsonify(market_context)

    except Exception as e:
        logging.error(f"Market refresh error: {e}")
        return jsonify({'market_applied': False, 'error': 'Internal error'}), 500

@app.route('/api/admin/contractor-upgrade-francis', methods=['POST'])
def contractor_upgrade_francis():
    """One-time endpoint to upgrade francis to contractor_enterprise for testing."""
    try:
        from models import Contractor
        import datetime
        target_email = 'francis.kurupacheril@gmail.com'
        c = Contractor.query.filter_by(email=target_email).first()
        if not c:
            # Create contractor record if doesn't exist
            c = Contractor(
                email=target_email,
                name='Francis Kurupacheril',
                status='active',
                plan='contractor_enterprise',
                accepts_leads=True,
                available=True,
                trades='general',
                service_zips='',
                monthly_lead_limit=-1,
                plan_activated_at=datetime.datetime.utcnow(),
            )
            db.session.add(c)
            db.session.commit()
            return jsonify({'created': True, 'plan': c.plan, 'id': c.id})
        
        c.plan = 'contractor_enterprise'
        c.status = 'active'
        c.accepts_leads = True
        c.available = True
        c.monthly_lead_limit = -1
        if not c.trades:
            c.trades = 'general'
        c.plan_activated_at = datetime.datetime.utcnow()
        db.session.commit()
        return jsonify({'updated': True, 'plan': c.plan, 'id': c.id, 'email': c.email, 'trades': c.trades})
    except Exception as e:
        logging.error(f'Contractor upgrade error: {e}')
        return jsonify({'error': 'Internal server error'}), 500

# Run secondary initialization in a background thread AFTER gunicorn binds port
# This prevents the 5-minute deploy timeout caused by running 262 DB ops before port binding
import threading as _init_threading

def _deferred_init():
    """Secondary DB migrations and seeding — runs in background thread after startup."""
    import time as _init_time
    _init_time.sleep(3)  # Give gunicorn a moment to bind port and workers to stabilize
    with app.app_context():
        try:
            db.create_all()
            logging.info("Database initialized!")
        except Exception as _e:
            logging.warning(f"Deferred db.create_all error: {_e}")

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


# Deferred init thread removed — migrations now guarded by persistent disk stamp
# See _MIGRATION_STAMP_PATH logic in the with app.app_context() block above.
logging.info("✅ Startup complete — migration stamp guards prevent DDL locks on redeploy")


# ===== REPAIR COST ADMIN API =====


# GTM (Go-To-Market) — Routes extracted to gtm/routes.py blueprint
# ============================================================
from gtm.routes import init_gtm_blueprint
init_gtm_blueprint(app, db, admin_required, api_admin_required)

# Admin Routes — /api/admin/* and /admin/* pages
from admin_routes import init_admin_blueprint
init_admin_blueprint(app, db, login_required, admin_required, api_admin_required)

# Persona Routes
from inspector_routes    import init_inspector_blueprint
from contractor_routes   import init_contractor_blueprint
from user_routes         import init_user_blueprint
from agent_routes        import init_agent_blueprint
from negotiation_routes  import init_negotiation_blueprint

init_inspector_blueprint(app, db, login_required, admin_required)
init_contractor_blueprint(app, db, login_required, admin_required)
init_user_blueprint(app, db, login_required, admin_required)
init_agent_blueprint(app, db, login_required, admin_required)
init_negotiation_blueprint(app, db, login_required, admin_required)

def _seed_facebook_nextdoor_targets():
    """Seed Facebook groups and Nextdoor neighborhoods if not yet present."""
    from models import GTMTargetSubreddit

    fb_defaults = [
        # Only verified real groups — you can add more via the admin UI
        dict(name='Bay Area Real Estate', url='https://www.facebook.com/groups/bayarearealestategroup', priority=1, notes='Verified active group. High volume Bay Area RE discussion.'),
    ]
    nd_defaults = [
        # Nextdoor is hyperlocal by neighborhood — not city-wide.
        # These are real Nextdoor neighborhood pages for high-value Silicon Valley areas.
        # To post: you must have a verified address in or near each neighborhood.
        # Use "Share to nearby neighborhoods" to expand reach after posting.
        dict(name='San Jose, CA',        url='https://nextdoor.com/city/san--jose--ca/',       priority=1, notes='Broad SJ reach via city page. Use neighborhood posts + nearby share for real distribution.'),
        dict(name='Willow Glen, CA',     url='https://nextdoor.com/neighborhood/willow-glen--san-jose--ca/',   priority=1, notes='Affluent SJ neighborhood. High homeownership, very active RE discussions.'),
        dict(name='Almaden Valley, CA',  url='https://nextdoor.com/neighborhood/almaden-valley--san-jose--ca/', priority=2, notes='High-value SJ market. Strong buyer intent, inspection scrutiny common.'),
        dict(name='Los Gatos, CA',       url='https://nextdoor.com/city/los--gatos--ca/',       priority=2, notes='Luxury market, very high home values. Buyers ask lots of repair/negotiation questions.'),
        dict(name='Cupertino, CA',       url='https://nextdoor.com/city/cupertino--ca/',        priority=2, notes='Tech-heavy, high prices. Active homebuyer discussions.'),
        dict(name='Sunnyvale, CA',       url='https://nextdoor.com/city/sunnyvale--ca/',        priority=3, notes='Core Silicon Valley. High buyer intent.'),
        dict(name='Mountain View, CA',   url='https://nextdoor.com/city/mountain--view--ca/',   priority=3, notes='Tech hub, competitive market. Good fit for OfferWise.'),
        dict(name='Fremont, CA',         url='https://nextdoor.com/city/fremont--ca/',          priority=3, notes='East Bay. More affordable entry, growing buyer market.'),
        dict(name='Oakland, CA',         url='https://nextdoor.com/city/oakland--ca/',          priority=4, notes='East Bay urban market. Active buyer community.'),
        dict(name='Berkeley, CA',        url='https://nextdoor.com/city/berkeley--ca/',         priority=4, notes='High-income, high-price market. Buyers are analytically minded — good OfferWise fit.'),
    ]

    for platform, defaults in [('facebook', fb_defaults), ('nextdoor', nd_defaults)]:
        try:
            existing = {r.name: r for r in GTMTargetSubreddit.query.filter_by(platform=platform).all()}
            desired_names = {d['name'] for d in defaults}

            # Remove stale entries no longer in the desired list
            for name, record in existing.items():
                if name not in desired_names:
                    db.session.delete(record)

            # Upsert: add new, update changed
            for d in defaults:
                rec = existing.get(d['name'])
                if rec:
                    rec.url      = d.get('url', rec.url)
                    rec.priority = d.get('priority', rec.priority)
                    rec.notes    = d.get('notes', rec.notes)
                else:
                    db.session.add(GTMTargetSubreddit(platform=platform, **d))

            db.session.commit()
            logging.info(f"Synced {len(defaults)} {platform} community targets")
        except Exception as e:
            db.session.rollback()
            logging.warning(f"Sync {platform} error: {e}")


def _ensure_gtm_subreddits():
    """Seed default target subreddits + Facebook groups + Nextdoor if table is empty."""
    from models import GTMTargetSubreddit
    if GTMTargetSubreddit.query.count() > 0:
        # Seed FB/ND if not yet present
        _seed_facebook_nextdoor_targets()
        return
    defaults = [
        # Core CA homebuyer communities — highest intent
        dict(name='FirstTimeHomeBuyer',      platform='reddit', priority=1, notes='High volume, first-time buyers, very high intent'),
        dict(name='RealEstate',              platform='reddit', priority=2, notes='Broad RE discussion, large audience'),
        dict(name='homeowners',              platform='reddit', priority=3, notes='Existing owners, repair cost questions'),
        dict(name='REBubble',                platform='reddit', priority=4, notes='Market-aware buyers, inspection/offer questions'),
        dict(name='CaliforniaHousing',       platform='reddit', priority=1, notes='CA-specific — core target market'),
        dict(name='bayarea',                 platform='reddit', priority=1, notes='Bay Area housing discussions'),
        dict(name='SanJose',                 platform='reddit', priority=1, notes='San Jose — OfferWise home market'),
        dict(name='LosAngeles',              platform='reddit', priority=2, notes='LA housing discussions'),
        dict(name='sanfrancisco',            platform='reddit', priority=2, notes='SF housing market'),
        dict(name='personalfinance',         platform='reddit', priority=3, notes='Home buying advice seekers'),
        dict(name='Homebuyers',              platform='reddit', priority=2, notes='Active buyer community'),
        # BiggerPockets — homebuyer-specific forums
        dict(name='First-Time Home Buyer', platform='biggerpockets', priority=1,
             url='https://www.biggerpockets.com/forums/903',
             notes='Highest intent BP forum — first time buyers asking about inspections and offers'),
        dict(name='Real Estate Deal Analysis', platform='biggerpockets', priority=2,
             url='https://www.biggerpockets.com/forums/88',
             notes='Deal analysis forum — buyers asking about property condition and pricing'),
    ]
    for d in defaults:
        try:
            db.session.add(GTMTargetSubreddit(**d))
        except Exception:
            pass
    try:
        db.session.commit()
        logging.info(f"🎯 Seeded {len(defaults)} GTM target subreddits")
    except Exception as e:
        db.session.rollback()
        logging.warning(f"GTM subreddit seed error: {e}")


_INFRA_DEFAULT_VENDORS = [
    dict(name='Render',          category='hosting',  logo_emoji='🚀', notes='Web service + PostgreSQL DB'),
    dict(name='Anthropic',       category='ai',       logo_emoji='🤖', notes='API usage'),
    dict(name='Google Cloud',    category='services', logo_emoji='☁️',  notes='Ads + Cloud Run'),
    dict(name='Resend',          category='email',    logo_emoji='📧', notes='Transactional email'),
    dict(name='Sentry',          category='monitoring',logo_emoji='🚨', notes='Error tracking'),
    dict(name='GitHub',          category='dev',      logo_emoji='🐱', notes='Source control'),
    dict(name='RentCast',        category='data',     logo_emoji='🏠', notes='Market data API'),
    dict(name='Stripe',          category='payments', logo_emoji='💳', notes='Payment processing'),
]

def _ensure_infra_vendors():
    """Seed default vendors if the table is empty."""
    from models import InfraVendor
    if InfraVendor.query.count() == 0:
        for v in _INFRA_DEFAULT_VENDORS:
            db.session.add(InfraVendor(**v))
        db.session.commit()
DOCREPO_DISK_PATH  = os.environ.get('DOCREPO_PATH', '/var/data/docrepo')
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
    """Start recurring background tasks using APScheduler with SQLAlchemy job store.
    APScheduler persists jobs across Gunicorn restarts — threading.Timer did not.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.executors.pool import ThreadPoolExecutor
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

    # Use memory job store — jobs are recreated on every startup anyway,
    # and SQLAlchemy job store can't serialize nested function references.
    executors = {'default': ThreadPoolExecutor(max_workers=2)}
    job_defaults = {'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 300}

    scheduler = BackgroundScheduler(
        executors=executors,
        job_defaults=job_defaults,
        timezone='America/Los_Angeles'
    )

    def _on_job_error(event):
        logging.error(f"⚠️ Scheduler job error: {event.job_id} — {event.exception}")

    def _on_job_missed(event):
        logging.warning(f"⏰ Scheduler job missed: {event.job_id} (was due {event.scheduled_run_time})")

    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
    scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED)

    # ── Job definitions ──────────────────────────────────────────────────

    def _drip_job():
        with app.app_context():
            from drip_campaign import run_drip_scheduler
            stats = run_drip_scheduler(db.session)
            sent = stats.get('sent', 0)
            if sent > 0:
                logging.info(f"📧 Drip: sent={sent} checked={stats.get('checked', 0)}")

    def _ads_job():
        with app.app_context():
            # Google Ads sync
            from google_ads_sync import is_configured as gads_configured, sync_to_db as gads_sync
            if gads_configured():
                result = gads_sync(db.session)
                logging.info(f"💰 Google Ads sync: {result.get('status')} "
                             f"— {result.get('impressions', 0)} imp, "
                             f"${result.get('spend', 0)} spend")
            # Reddit Ads sync
            from reddit_ads_sync import is_configured as rads_configured, sync_to_db as rads_sync
            if rads_configured():
                result = rads_sync(db.session)
                logging.info(f"🟠 Reddit Ads sync: {result.get('status')} "
                             f"— {result.get('rows_synced', 0)} days, "
                             f"${result.get('spend', 0)} spend")

    def _reddit_job():
        with app.app_context():
            from reddit_poster import is_configured, post_next_approved
            if is_configured():
                result = post_next_approved(db.session)
                if result and 'error' not in result:
                    logging.info(f"📮 Reddit auto-posted: {result.get('title', '?')}")
                elif result and 'error' in result:
                    logging.warning(f"📮 Reddit post failed: {result['error']}")

    def _forum_scan_job():
        """Scan Reddit for relevant buyer threads, score with Claude, generate reply drafts."""
        with app.app_context():
            try:
                from gtm.forum_scanner import run_scan
                stats = run_scan(db.session, platform='all')
                scanned  = stats.get('posts_scanned', 0)
                drafts   = stats.get('drafts_created', 0)
                errors   = stats.get('errors', 0)
                if scanned > 0 or drafts > 0:
                    logging.info(f"🔍 Forum scan: scanned={scanned} drafts={drafts} errors={errors}")
                if stats.get('note'):
                    logging.warning(f"🔍 Forum scan note: {stats['note']}")
            except Exception as e:
                logging.error(f"Forum scan job error: {e}", exc_info=True)

    def _social_gen_job():
        """Generate today's Facebook + Nextdoor draft posts (daily at 7:30am PT)."""
        with app.app_context():
            try:
                from gtm.social_engine import generate_social_posts_for_date
                from models import GTMSubredditPost, Analysis
                from datetime import date as _date

                today      = _date.today()
                models_map = {'Analysis': Analysis}
                post_dicts = generate_social_posts_for_date(db.session, models_map, today)

                saved = 0
                for pd in post_dicts:
                    existing = GTMSubredditPost.query.filter_by(
                        platform=pd['platform'],
                        target_group=pd['target_group'],
                        scheduled_date=today,
                    ).first()
                    if existing:
                        continue
                    post = GTMSubredditPost(
                        title=pd['title'], body=pd['body'],
                        pillar=pd['pillar'], pillar_label=pd['pillar'],
                        flair='', data_summary='',
                        platform=pd['platform'], target_group=pd['target_group'],
                        scheduled_date=today, status='draft',
                        topic_key=pd.get('topic_key', ''),
                    )
                    db.session.add(post)
                    saved += 1

                db.session.commit()
                if saved:
                    logging.info(f"📱 Social gen: {saved} posts drafted (FB + Nextdoor)")
            except Exception as e:
                db.session.rollback()
                logging.error(f"Social gen job error: {e}", exc_info=True)

    def _content_gen_job():
        """Generate today's r/offerwiseAi content post and auto-approve if Reddit is configured."""
        with app.app_context():
            try:
                from gtm.content_engine import generate_daily_post
                from reddit_poster import is_configured as reddit_is_configured
                from models import GTMSubredditPost, Analysis
                from datetime import date as _date

                today = _date.today()

                # Skip if already generated today
                existing = GTMSubredditPost.query.filter_by(scheduled_date=today).first()
                if existing:
                    logging.debug(f"Content gen: post already exists for {today} (status={existing.status})")
                    return

                models_map = {"Analysis": Analysis}
                post_data  = generate_daily_post(db.session, models_map, today)

                # Auto-approve when Reddit is configured — _reddit_job will pick it up within the hour
                auto_status = 'approved' if reddit_is_configured() else 'draft'

                post = GTMSubredditPost(
                    title          = post_data['title'],
                    body           = post_data['body'],
                    pillar         = post_data['pillar'],
                    pillar_label   = post_data['pillar_label'],
                    flair          = post_data['flair'],
                    data_summary   = post_data.get('data_summary', ''),
                    scheduled_date = today,
                    status         = auto_status,
                    topic_key      = post_data.get('topic_key', ''),
                )
                db.session.add(post)
                db.session.commit()
                logging.info(
                    f"📝 Content gen: '{post_data['title'][:60]}' "
                    f"[{post_data['pillar']}] status={auto_status}"
                )
            except Exception as e:
                db.session.rollback()
                logging.error(f"Content gen job error: {e}", exc_info=True)


    def _facebook_content_job():
        """Generate today's Facebook group post (plain text, no markdown)."""
        with app.app_context():
            try:
                from gtm.content_engine import generate_post_for_platform, FACEBOOK_TARGET_GROUPS
                from models import GTMSubredditPost, Analysis
                from datetime import date as _date
                import random

                today = _date.today()
                if GTMSubredditPost.query.filter_by(scheduled_date=today, platform='facebook').first():
                    return

                group_name, _ = random.choice(FACEBOOK_TARGET_GROUPS)
                models_map = {'Analysis': Analysis}
                post_data  = generate_post_for_platform(db.session, models_map, 'facebook', group_name, today)

                post = GTMSubredditPost(
                    title=post_data['title'], body=post_data['body'],
                    pillar=post_data['pillar'], pillar_label=post_data['pillar_label'],
                    flair=post_data.get('flair', ''), data_summary=post_data.get('data_summary', ''),
                    scheduled_date=today, status='draft',
                    topic_key=post_data.get('topic_key', ''),
                    platform='facebook', target_group=group_name,
                )
                db.session.add(post)
                db.session.commit()
                logging.info(f"📘 Facebook post generated: '{post_data['title'][:60]}' → {group_name}")
            except Exception as e:
                db.session.rollback()
                logging.error(f'Facebook content job error: {e}', exc_info=True)

    def _nextdoor_content_job():
        """Generate today's Nextdoor neighborhood post (short plain text)."""
        with app.app_context():
            try:
                from gtm.content_engine import generate_post_for_platform, NEXTDOOR_NEIGHBORHOODS
                from models import GTMSubredditPost, Analysis
                from datetime import date as _date
                import random

                today = _date.today()
                if GTMSubredditPost.query.filter_by(scheduled_date=today, platform='nextdoor').first():
                    return

                neighborhood = random.choice(NEXTDOOR_NEIGHBORHOODS)
                models_map   = {'Analysis': Analysis}
                post_data    = generate_post_for_platform(db.session, models_map, 'nextdoor', neighborhood, today)

                post = GTMSubredditPost(
                    title=post_data['title'], body=post_data['body'],
                    pillar=post_data['pillar'], pillar_label=post_data['pillar_label'],
                    flair=post_data.get('flair', ''), data_summary=post_data.get('data_summary', ''),
                    scheduled_date=today, status='draft',
                    topic_key=post_data.get('topic_key', ''),
                    platform='nextdoor', target_group=neighborhood,
                )
                db.session.add(post)
                db.session.commit()
                logging.info(f"🏘️  Nextdoor post generated: '{post_data['title'][:60]}' → {neighborhood}")
            except Exception as e:
                db.session.rollback()
                logging.error(f'Nextdoor content job error: {e}', exc_info=True)


    def _lead_expiry_job():
        """Expire unclaimed leads older than 48h and notify affected buyers.
        Also auto-activates any 'new' leads that were created before auto-activation
        was added (one-time migration) and any stuck in 'new' status.
        """
        with app.app_context():
            from datetime import timedelta as _td_exp

            # Auto-activate any leads stuck in 'new' status → 'available'
            stuck_new = ContractorLead.query.filter_by(status='new').all()
            activated = 0
            for lead in stuck_new:
                lead.status = 'available'
                if not lead.available_at:
                    lead.available_at = datetime.utcnow()
                if not lead.expires_at:
                    lead.expires_at = datetime.utcnow() + _td_exp(hours=48)
                activated += 1
            if activated:
                db.session.commit()
                logging.info(f"✅ Auto-activated {activated} leads from 'new' → 'available'")

            cutoff = datetime.utcnow() - _td_exp(hours=48)
            stale = ContractorLead.query.filter(
                ContractorLead.status == 'available',
                ContractorLead.created_at < cutoff,
            ).all()
            expired = 0
            for lead in stale:
                lead.status = 'expired'
                expired += 1
                try:
                    send_email(
                        to_email=lead.user_email,
                        subject=f"Your {lead.repair_system} quote request — no contractors claimed yet",
                        html_content=f"""<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:24px;border-radius:12px;">
                      <div style="font-size:20px;font-weight:800;margin-bottom:8px;color:#f59e0b;">No contractors claimed your request yet</div>
                      <p style="color:#94a3b8;">Your request for a <strong style="color:#f1f5f9;">{lead.repair_system}</strong> contractor at {lead.property_address} didn't get claimed in 48 hours.</p>
                      <p style="color:#94a3b8;">We'll keep your request on file and notify you if a matching contractor joins your area.</p>
                    </div>"""
                    )
                except Exception: pass
            if expired:
                db.session.commit()
                logging.info(f"⏰ Lead expiry: {expired} leads expired and buyers notified")

    # ── Funnel drop-off alert ─────────────────────────────────────────────
    FUNNEL_THRESHOLDS = {
        'signup_to_analysis_started':   30.0,
        'analysis_started_to_purchase': 10.0,
        'visit_to_signup':               2.0,
    }

    def _funnel_alert_job():
        """Weekly funnel health check — emails admin if any stage drops below threshold."""
        with app.app_context():
            try:
                from gtm.conversion_intel import get_funnel_snapshot
                from models import FunnelEvent, GTMAdPerformance, GTMScanRun
                _models = {
                    'FunnelEvent': FunnelEvent,
                    'GTMAdPerformance': GTMAdPerformance,
                    'GTMScanRun': GTMScanRun,
                }
                snapshot = get_funnel_snapshot(db.session, _models, days=7)
                rates  = snapshot.get('conversion_rates', {})
                stages = snapshot.get('stages', {})
                source = snapshot.get('data_source', 'local')

                breached = []
                for key, threshold in FUNNEL_THRESHOLDS.items():
                    actual = rates.get(key)
                    if actual is not None and actual < threshold:
                        breached.append((key.replace('_', ' → '), actual, threshold))

                visits_7d    = stages.get('visit', 0)
                signups_7d   = stages.get('signup', 0)
                analyses_7d  = stages.get('analysis_complete', 0)
                purchases_7d = stages.get('purchase', 0)

                if breached:
                    breach_rows = ''.join(
                        '<tr><td style="padding:6px 0;color:#94a3b8;font-size:13px;">' + k + '</td>'
                        '<td style="padding:6px 0;"><strong style="color:#ef4444;">' + f'{v:.1f}%' + '</strong>'
                        ' <span style="color:#64748b;font-size:11px;">(min: ' + f'{t:.0f}%' + ')</span></td></tr>'
                        for k, v, t in breached
                    )
                    breach_block = (
                        '<div style="padding:14px;background:rgba(239,68,68,.08);border:1px solid'
                        ' rgba(239,68,68,.2);border-radius:10px;margin-bottom:16px;">'
                        '<div style="font-weight:700;color:#ef4444;margin-bottom:8px;">'
                        + f'⚠️ {len(breached)} stage{"" if len(breached)==1 else "s"} below threshold</div>'
                        + '<table style="width:100%;border-collapse:collapse;">' + breach_rows + '</table></div>'
                    )
                else:
                    breach_block = ''

                status_label = (
                    f'⚠️ {len(breached)} Alert{"s" if len(breached)!=1 else ""}'
                    if breached else '✅ All Clear'
                )

                rate_lines = ''.join(
                    '<div style="font-size:12px;color:#94a3b8;margin-bottom:3px;">'
                    + k.replace('_', ' → ') + ': <strong style="color:'
                    + ('#ef4444' if v < FUNNEL_THRESHOLDS.get(k, 999) else '#f1f5f9') + ';">'
                    + f'{v:.1f}%</strong></div>'
                    for k, v in rates.items() if v > 0
                )

                send_email(
                    to_email=ADMIN_EMAIL,
                    subject=f"📊 Weekly Funnel Report — {status_label} (last 7 days)",
                    html_content=(
                        '<div style="font-family:sans-serif;max-width:600px;background:#0f172a;color:#f1f5f9;padding:28px;border-radius:12px;">'
                        '<div style="font-size:20px;font-weight:800;margin-bottom:4px;">📊 Weekly Funnel Report</div>'
                        f'<div style="font-size:13px;color:#94a3b8;margin-bottom:20px;">Last 7 days · Data source: {source}</div>'
                        + breach_block +
                        '<table style="width:100%;border-collapse:collapse;">'
                        f'<tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Visits</td><td style="padding:7px 0;font-weight:700;">{visits_7d:,}</td></tr>'
                        f'<tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Signups</td><td style="padding:7px 0;font-weight:700;">{signups_7d:,}</td></tr>'
                        f'<tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Analyses completed</td><td style="padding:7px 0;font-weight:700;">{analyses_7d:,}</td></tr>'
                        f'<tr><td style="padding:7px 0;color:#94a3b8;font-size:13px;">Purchases / subscriptions</td><td style="padding:7px 0;font-weight:700;color:#22c55e;">{purchases_7d:,}</td></tr>'
                        '</table>'
                        '<div style="margin-top:16px;padding:12px;background:rgba(255,255,255,.04);border-radius:8px;">'
                        '<div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">Conversion rates (7d)</div>'
                        + rate_lines +
                        '</div>'
                        '<div style="margin-top:12px;font-size:11px;color:#475569;">Sent every Monday at 8am PT by OfferWise APScheduler</div>'
                        '</div>'
                    )
                )
                logging.info(f"📊 Funnel alert sent: {len(breached)} breach(es), signups={signups_7d}, analyses={analyses_7d}, purchases={purchases_7d}")
            except Exception as e:
                logging.error(f"❌ Funnel alert job failed: {e}", exc_info=True)

    # Remove stale jobs before re-adding (prevents duplicates on restart)
    for job_id in ['drip', 'ads_sync', 'reddit', 'lead_expiry', 'funnel_alert']:
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass

    # Drip: every 15 minutes
    scheduler.add_job(_drip_job,         'interval', minutes=15,  id='drip',         replace_existing=True)
    # Google Ads: every 6 hours
    scheduler.add_job(_ads_job,          'interval', hours=6,     id='ads_sync',     replace_existing=True)
    # Reddit: every hour
    scheduler.add_job(_reddit_job,       'interval', hours=1,     id='reddit',       replace_existing=True)
    # Lead expiry: daily at 2am PT
    scheduler.add_job(_lead_expiry_job,  'cron', hour=2,  minute=0, id='lead_expiry',  replace_existing=True)

    # Forum scanner — every 6h, staggered 30 min from ads sync to spread DB load
    scheduler.add_job(_forum_scan_job,   'interval', hours=6,   id='forum_scan',   replace_existing=True,
                      start_date='2026-01-01 00:30:00')

    # Content generator — daily at 7 AM PT, generates + auto-approves today's r/offerwiseAi post
    scheduler.add_job(_content_gen_job,  'cron', hour=7, minute=0, id='content_gen',  replace_existing=True)

    # Social content generator — daily at 7:30 AM PT, drafts FB + Nextdoor posts for manual posting
    scheduler.add_job(_social_gen_job,   'cron', hour=7, minute=30, id='social_gen',  replace_existing=True)
    # Funnel drop-off alert: every Monday at 8am PT
    scheduler.add_job(_funnel_alert_job, 'cron', day_of_week='mon', hour=8, minute=0, id='funnel_alert', replace_existing=True)

    # ── ML: Post-close survey sender (daily at 10am) ────────────────────
    def _survey_job():
        with app.app_context():
            try:
                from datetime import datetime, timedelta
                from survey_routes import send_post_close_survey
                from models import Analysis, Property, PostCloseSurvey
                import json as _json

                # Find analyses from 28-32 days ago that haven't been surveyed
                cutoff_start = datetime.utcnow() - timedelta(days=32)
                cutoff_end = datetime.utcnow() - timedelta(days=28)
                analyses = Analysis.query.filter(
                    Analysis.created_at.between(cutoff_start, cutoff_end),
                    Analysis.status == 'completed',
                    Analysis.user_id.isnot(None),
                ).all()

                sent = 0
                for a in analyses:
                    # Skip if already surveyed
                    existing = PostCloseSurvey.query.filter_by(analysis_id=a.id).first()
                    if existing:
                        continue
                    # Skip test accounts
                    user = User.query.get(a.user_id)
                    if not user or '@test.' in (user.email or '') or '@persona.' in (user.email or '') or user.email.endswith('@getofferwise.ai'):
                        continue
                    prop = Property.query.get(a.property_id)
                    if not prop:
                        continue

                    # Extract predictions from result
                    try:
                        result = _json.loads(a.result_json or '{}')
                        offer = result.get('offer_strategy', {})
                        repair_total = result.get('total_repair_cost', 0)
                        n_findings = len(result.get('findings', []))
                    except Exception:
                        offer = {}
                        repair_total = 0
                        n_findings = 0

                    send_post_close_survey(
                        user_id=user.id,
                        analysis_id=a.id,
                        property_address=prop.address or '',
                        predicted_offer_low=offer.get('conservative_offer'),
                        predicted_offer_high=offer.get('aggressive_offer'),
                        predicted_repair_total=repair_total,
                        predicted_findings_count=n_findings,
                    )
                    sent += 1
                    if sent >= 5:  # Cap at 5 per run to avoid spam
                        break

                if sent > 0:
                    logging.info(f"ML: sent {sent} post-close survey emails")
            except Exception as e:
                logging.warning(f"Survey job error: {e}")

    scheduler.add_job(_survey_job, 'cron', hour=10, minute=0, id='ml_survey', replace_existing=True)

    # ── ML Training Agent (daily at 3am PT) ──
    def _ml_agent_job():
        """Autonomous ML pipeline agent. Runs without human intervention.

        1. Crawl fresh external cost data (permits, HomeAdvisor, FEMA)
        2. Evaluate data sufficiency — skip if no meaningful new data
        3. Train all models
        4. Validate via inference tests
        5. Rollback if any model regressed significantly
        6. Log everything to MLAgentRun
        """
        try:
            with app.app_context():
                import os, shutil, json as _json, time as _time
                from models import MLFindingLabel, MLContradictionPair, MLTrainingRun, MLCostData, MLAgentRun

                agent_log = []
                t_start = _time.time()
                run = MLAgentRun(trigger='scheduled')

                def _alog(msg, level='info'):
                    agent_log.append({'t': round(_time.time() - t_start, 1), 'msg': msg, 'level': level})
                    logging.info(f'🤖 ML Agent: {msg}')

                _alog('ML Agent starting autonomous pipeline')

                # ── PHASE 1: CRAWL ──
                # v5.87.36 — two memory wins:
                #   1. violation_limit=2000 (was 50000) caps each violation/NFIP/
                #      Philly source. Sources are still ordered date DESC so we
                #      keep the recent rows. 25x reduction in peak crawl memory.
                #   2. Bulk-load existing content_hash values into a Python set
                #      ONCE up front, then check membership in O(1). Replaces
                #      what was a per-row SELECT against MLCostData (potentially
                #      hundreds of thousands of round-trips). Both faster and
                #      lets the crawled rows be dropped from memory sooner.
                crawl_added = 0
                crawl_scanned = 0
                try:
                    from cost_data_crawler import collect_all_external_cost_data
                    import hashlib
                    rows, stats = collect_all_external_cost_data(
                        permit_limit=300,
                        violation_limit=2000,
                    )
                    crawl_scanned = len(rows)

                    # Bulk-load existing hashes — one query, ~10MB set in memory.
                    # Way cheaper than 200K individual SELECTs.
                    existing_hashes = set()
                    try:
                        for (h,) in db.session.query(MLCostData.content_hash).yield_per(5000):
                            if h:
                                existing_hashes.add(h)
                    except Exception as load_err:
                        _alog(f'Phase 1 — Hash preload failed (falling back to per-row): {load_err}', 'warn')
                        existing_hashes = None

                    for row in rows:
                        h = hashlib.sha256(
                            (row['finding_text'][:200] + '|' + row['source'] + '|' + str(int(row['cost_mid']))).encode()
                        ).hexdigest()
                        # Set lookup if preload succeeded; fall back to query if not.
                        is_new = (h not in existing_hashes) if existing_hashes is not None \
                                 else (not MLCostData.query.filter_by(content_hash=h).first())
                        if is_new:
                            entry = MLCostData(
                                finding_text=row['finding_text'][:500],
                                category=row.get('category', 'general'),
                                severity=row.get('severity', 'moderate'),
                                cost_low=row.get('cost_low'), cost_high=row.get('cost_high'),
                                cost_mid=row['cost_mid'],
                                zip_code=row.get('zip_code', '')[:10],
                                source=row.get('source', 'unknown')[:50],
                                source_meta=_json.dumps(row.get('metadata', {})),
                                content_hash=h,
                            )
                            db.session.add(entry)
                            crawl_added += 1
                            # Add to the set so duplicates within this batch are also skipped
                            if existing_hashes is not None:
                                existing_hashes.add(h)
                    db.session.commit()
                    # Free the crawled rows + hash set as soon as the dedup is done
                    del rows
                    del existing_hashes
                    _alog(f'Phase 1 — Crawl: {crawl_added} new entries from {crawl_scanned} scanned')
                except Exception as crawl_err:
                    db.session.rollback()
                    _alog(f'Phase 1 — Crawl failed (non-fatal): {crawl_err}', 'warn')

                run.crawl_added = crawl_added
                run.crawl_scanned = crawl_scanned

                # ── PHASE 2: EVALUATE DATA SUFFICIENCY ──
                # v5.87.35 — stricter thresholds + staleness guard.
                #
                # Why bumped: at the 121K-row corpus size, retraining on +5
                # findings is wasteful — the marginal model improvement is
                # near zero and the OOM exposure on Render's 2GB tier is
                # real (PHASE 4's embedder + XGBoost + parent-process baseline
                # peaks above 2GB on training nights, triggering automatic
                # restarts in the early-AM window).
                #
                # The new rule:
                #   - First run ever (no last_run): always train.
                #   - Last training was 7+ days ago: train regardless of
                #     delta (model never goes truly stale even if crawlers
                #     run dry).
                #   - Otherwise: require 100+ new findings OR 50+ new costs.
                #
                # Manual trigger (admin "Run Now" button) bypasses Phase 2
                # entirely — see admin_routes.admin_ml_agent_run. This change
                # only affects the scheduled 3am cron path.
                current_findings = MLFindingLabel.query.count()
                current_pairs = MLContradictionPair.query.count()
                current_costs = MLCostData.query.count()
                last_run = MLTrainingRun.query.order_by(MLTrainingRun.created_at.desc()).first()

                run.data_findings = current_findings
                run.data_pairs = current_pairs
                run.data_costs = current_costs

                new_findings = current_findings - (last_run.fc_data_points or 0 if last_run else 0)
                new_costs = crawl_added

                # Threshold knobs — kept here near the call site for easy tuning.
                MIN_NEW_FINDINGS = 100   # was 5 in v5.87.34 and earlier
                MIN_NEW_COSTS    = 50    # was 10 in v5.87.34 and earlier
                MAX_DAYS_STALE   = 7     # train regardless if last run is older than this

                from datetime import datetime as _dt
                days_since_last = None
                if last_run and last_run.created_at:
                    days_since_last = (_dt.utcnow() - last_run.created_at).total_seconds() / 86400

                # Decision tree
                should_train = True
                skip_reason = None
                if last_run is None:
                    _alog('Phase 2 — First run ever, training from scratch')
                elif days_since_last is not None and days_since_last >= MAX_DAYS_STALE:
                    _alog(f'Phase 2 — Last training was {days_since_last:.1f} days ago (≥{MAX_DAYS_STALE}d staleness threshold), training regardless of delta')
                elif new_findings < MIN_NEW_FINDINGS and new_costs < MIN_NEW_COSTS:
                    should_train = False
                    skip_reason = (f'Insufficient new data (findings: +{new_findings}/{MIN_NEW_FINDINGS}, '
                                   f'costs: +{new_costs}/{MIN_NEW_COSTS}, '
                                   f'last train: {days_since_last:.1f}d ago)')

                if not should_train:
                    _alog(f'Phase 2 — Skip: {skip_reason}')
                    run.skipped_reason = skip_reason
                    run.elapsed_seconds = round(_time.time() - t_start, 1)
                    run.agent_log = _json.dumps(agent_log)
                    db.session.add(run)
                    db.session.commit()
                    return

                _alog(f'Phase 2 — Data sufficient: {current_findings} findings, {current_pairs} pairs, {current_costs} costs')

                # ── PHASE 3: BACKUP CURRENT MODELS ──
                base_dir = os.path.dirname(os.path.abspath(__file__))
                models_dir = os.path.join(base_dir, 'models')
                backup_dir = os.path.join(base_dir, 'models_backup')
                try:
                    if os.path.exists(backup_dir):
                        shutil.rmtree(backup_dir)
                    if os.path.exists(models_dir):
                        shutil.copytree(models_dir, backup_dir)
                    _alog('Phase 3 — Models backed up')
                except Exception as bk_err:
                    _alog(f'Phase 3 — Backup failed (non-fatal): {bk_err}', 'warn')

                # ── PHASE 4: TRAIN ──
                _alog('Phase 4 — Training started')
                results = None
                try:
                    from admin_routes import _execute_training
                    job = {'log': [], 'started_at': _time.time()}
                    results = _execute_training(job)
                    run.trained = True
                    _alog(f'Phase 4 — Training complete')
                except Exception as train_err:
                    _alog(f'Phase 4 — Training FAILED: {train_err}', 'error')
                    try:
                        if os.path.exists(backup_dir):
                            shutil.rmtree(models_dir)
                            shutil.move(backup_dir, models_dir)
                            _alog('Rolled back after training failure')
                    except Exception:
                        pass
                    run.elapsed_seconds = round(_time.time() - t_start, 1)
                    run.agent_log = _json.dumps(agent_log)
                    db.session.add(run)
                    db.session.commit()
                    return

                # Extract accuracies
                if results:
                    fc = results.get('Finding Classifier', {})
                    cd = results.get('Contradiction Detector', {})
                    rc = results.get('Repair Cost', {})
                    try: run.fc_acc = float(fc.get('category', '0').replace('%', ''))
                    except: pass
                    try: run.cd_acc = float(cd.get('accuracy', '0').replace('%', ''))
                    except: pass
                    try: run.rc_r2 = float(rc.get('r2', '0'))
                    except: pass

                # ── PHASE 5: VALIDATE ──
                regressed = False
                rollback_reason = ''
                if last_run and results:
                    fc = results.get('Finding Classifier', {})
                    cd = results.get('Contradiction Detector', {})

                    new_cd_acc = run.cd_acc or 0
                    old_cd_acc = last_run.cd_accuracy or 0
                    if old_cd_acc > 0 and new_cd_acc < old_cd_acc - 3:
                        rollback_reason = f'Contradiction regressed {new_cd_acc:.1f}% vs {old_cd_acc:.1f}%'
                        regressed = True

                    new_fc_cat = run.fc_acc or 0
                    old_fc_cat = last_run.fc_category_acc or 0
                    if old_fc_cat > 0 and new_fc_cat < old_fc_cat - 5:
                        rollback_reason = f'Finding classifier regressed {new_fc_cat:.1f}% vs {old_fc_cat:.1f}%'
                        regressed = True

                if regressed:
                    _alog(f'Phase 5 — REGRESSION: {rollback_reason}', 'error')
                    run.rolled_back = True
                    run.rollback_reason = rollback_reason
                    try:
                        if os.path.exists(backup_dir):
                            shutil.rmtree(models_dir)
                            shutil.move(backup_dir, models_dir)
                            from ml_inference import init_ml_inference
                            init_ml_inference(app_base_dir=base_dir)
                            _alog('Rollback complete — previous models restored')
                    except Exception as rb_err:
                        _alog(f'Rollback failed: {rb_err}', 'error')
                else:
                    _alog('Phase 5 — Validation passed')

                # ── PHASE 6: CLEANUP ──
                try:
                    if os.path.exists(backup_dir):
                        shutil.rmtree(backup_dir)
                except Exception:
                    pass

                run.elapsed_seconds = round(_time.time() - t_start, 1)
                run.agent_log = _json.dumps(agent_log)
                db.session.add(run)
                db.session.commit()
                _alog(f'ML Agent complete in {run.elapsed_seconds:.0f}s ✅', 'success')

        except Exception as e:
            logging.error(f'ML Agent fatal error: {e}', exc_info=True)

    scheduler.add_job(_ml_agent_job, 'cron', hour=3, minute=0,
                      id='ml_agent', replace_existing=True)

    # ── Agentic property monitoring jobs (v5.75.92) ──────────────────────────
    try:
        from agentic_monitor import register_monitoring_jobs
        register_monitoring_jobs(scheduler)
    except Exception as _ae:
        logging.warning(f"Agentic monitor registration failed: {_ae}")

    # v5.87.3: sweep any zombie ML ingestion jobs left over from a killed
    # worker (OOM, deploy restart, etc). Runs once on startup so the UI can
    # immediately accept new jobs without the operator needing to click
    # through "job X already running" errors for stale rows.
    try:
        with app.app_context():
            from models import MLIngestionJob
            swept = MLIngestionJob.sweep_stale()
            if swept:
                logging.info(f"✅ Swept {len(swept)} stale ingestion jobs on startup: {swept}")
    except Exception as _se:
        logging.warning(f"Stale job sweep failed (non-fatal): {_se}")

    # Market intel runs via agentic_monitor.register_monitoring_jobs() above
    # Upgrade RentCast to Foundation ($74/mo) when monthly analyses exceed 370

    scheduler.start()
    logging.info("✅ APScheduler started — drip(15m), ads(6h), reddit(1h), lead_expiry(daily 2am), funnel_alert(Mon 8am)")
    return scheduler


# Only start in production (not during testing or imports)
# The training subprocess skips the scheduler (no duplicate jobs) but still
# initializes ML inference because _execute_training needs the embedder loaded.
_scheduler = None
_is_training_subprocess = os.environ.get('OFFERWISE_TRAINING_SUBPROCESS') == '1'
if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' or not app.debug:
    if not _is_training_subprocess:
        try:
            _scheduler = _start_background_schedulers()
        except Exception as e:
            logging.warning(f"Could not start APScheduler: {e}")
    else:
        logging.info("🧠 Training subprocess: skipping APScheduler (gunicorn workers own the schedule)")
    # Initialize ML inference models (needed by both gunicorn workers and training subprocess)
    try:
        from ml_inference import init_ml_inference
        _ml_ready = init_ml_inference(app_base_dir=os.path.dirname(os.path.abspath(__file__)))
        if _ml_ready:
            logging.info("🧠 ML inference: models loaded and ready for hybrid routing")
        else:
            logging.info("🧠 ML inference: models not available — using Claude-only mode")
    except Exception as e:
        logging.warning(f"ML inference init skipped: {e}")
@app.route('/combo-matrix')
def combo_matrix():
    return send_from_directory('static', 'combo-matrix.html')


@app.route('/agentic-roadmap')
@login_required
def agentic_roadmap():
    return send_from_directory('static', 'agentic-roadmap.html')


@app.route('/investors')
def investors():
    """Investor overview — not linked from navigation, not indexed by search engines."""
    return send_from_directory('static', 'investors.html')


@app.route('/persona-matrix')
def persona_matrix():
    return send_from_directory('static', 'persona-matrix.html')


# ── B2B Onboarding Wizard Routes (v5.75.79) ─────────────────────────────────


@app.route('/api/watch', methods=['POST'])
@login_required
def create_property_watch():
    """
    Activate agentic monitoring for a property.
    Called automatically after a buyer completes an analysis, or manually.
    """
    from models import PropertyWatch
    data = request.get_json(silent=True) or {}
    address       = (data.get('address') or '').strip()
    asking_price  = data.get('asking_price') or data.get('price')
    analysis_id   = data.get('analysis_id')
    latitude      = data.get('latitude')
    longitude     = data.get('longitude')
    avm_at_analysis = data.get('avm_at_analysis')

    if not address:
        return jsonify({'error': 'address is required'}), 400

    # Deactivate any existing watch for this address+user
    existing = PropertyWatch.query.filter_by(
        user_id=current_user.id, address=address, is_active=True
    ).first()
    if existing:
        return jsonify({'success': True, 'watch_id': existing.id, 'already_exists': True})

    # Try to pull lat/lng from analysis JSON if not provided
    if (not latitude or not longitude) and analysis_id:
        try:
            analysis = Analysis.query.get(analysis_id)
            if analysis and analysis.result_json:
                rj = json.loads(analysis.result_json)
                rp = rj.get('research_profile', rj.get('property_context', {})) or {}
                latitude  = latitude  or rp.get('latitude')
                longitude = longitude or rp.get('longitude')
                if not avm_at_analysis:
                    avm_at_analysis = rp.get('avm_price') or rp.get('rentcast', {}).get('avm_price')
        except Exception as _e:
            logging.warning(f"Could not extract lat/lng from analysis: {_e}")

    # Parse optional deadline dates (ISO format YYYY-MM-DD)
    from datetime import date as _date
    def _parse_date(v):
        if not v: return None
        try: return _date.fromisoformat(str(v)[:10])
        except: return None

    watch = PropertyWatch(
        user_id        = current_user.id,
        analysis_id    = analysis_id,
        address        = address,
        asking_price   = float(asking_price) if asking_price else None,
        latitude       = float(latitude)  if latitude  else None,
        longitude      = float(longitude) if longitude else None,
        avm_at_analysis= float(avm_at_analysis) if avm_at_analysis else None,
        expires_at     = datetime.utcnow() + timedelta(days=45),
        offer_accepted_date         = _parse_date(data.get('offer_accepted_date')),
        inspection_contingency_date = _parse_date(data.get('inspection_contingency_date')),
        loan_contingency_date       = _parse_date(data.get('loan_contingency_date')),
        appraisal_contingency_date  = _parse_date(data.get('appraisal_contingency_date')),
        seller_response_deadline    = _parse_date(data.get('seller_response_deadline')),
        repair_completion_deadline  = _parse_date(data.get('repair_completion_deadline')),
        close_of_escrow_date        = _parse_date(data.get('close_of_escrow_date')),
    )
    db.session.add(watch)
    db.session.commit()

    logging.info(f"🔭 PropertyWatch created: {address} for user {current_user.email}")
    return jsonify({'success': True, 'watch_id': watch.id})


@app.route('/api/watch/<int:watch_id>', methods=['DELETE'])
@login_required
def deactivate_property_watch(watch_id):
    """Deactivate monitoring for a property."""
    from models import PropertyWatch
    watch = PropertyWatch.query.filter_by(id=watch_id, user_id=current_user.id).first()
    if not watch:
        return jsonify({'error': 'Watch not found'}), 404
    watch.is_active = False
    watch.deactivated_reason = 'manual'
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/watch/<int:watch_id>/deadlines', methods=['PATCH'])
@login_required
def update_watch_deadlines(watch_id):
    """
    Set or update escrow deadline dates for an active watch.
    Buyer calls this after offer is accepted to enable deadline monitoring.
    All dates are optional ISO format YYYY-MM-DD strings.
    """
    from models import PropertyWatch
    from datetime import date as _date

    watch = PropertyWatch.query.filter_by(id=watch_id, user_id=current_user.id).first()
    if not watch:
        return jsonify({'error': 'Watch not found'}), 404

    data = request.get_json(silent=True) or {}

    def _parse_date(v):
        if not v: return None
        try: return _date.fromisoformat(str(v)[:10])
        except: return None

    if 'offer_accepted_date'         in data: watch.offer_accepted_date         = _parse_date(data['offer_accepted_date'])
    if 'inspection_contingency_date' in data: watch.inspection_contingency_date = _parse_date(data['inspection_contingency_date'])
    if 'loan_contingency_date'       in data: watch.loan_contingency_date       = _parse_date(data['loan_contingency_date'])
    if 'appraisal_contingency_date'  in data: watch.appraisal_contingency_date  = _parse_date(data['appraisal_contingency_date'])
    if 'seller_response_deadline'    in data: watch.seller_response_deadline    = _parse_date(data['seller_response_deadline'])
    if 'repair_completion_deadline'  in data: watch.repair_completion_deadline  = _parse_date(data['repair_completion_deadline'])
    if 'close_of_escrow_date'        in data: watch.close_of_escrow_date        = _parse_date(data['close_of_escrow_date'])

    db.session.commit()
    logging.info(f"📅 Deadlines updated for watch {watch_id} by user {current_user.id}")
    return jsonify({'success': True, 'watch_id': watch_id})


@app.route('/api/watches', methods=['GET'])
@login_required
def list_property_watches():
    """List all active property watches for the current user."""
    from models import PropertyWatch
    watches = PropertyWatch.query.filter_by(
        user_id=current_user.id, is_active=True
    ).order_by(PropertyWatch.created_at.desc()).all()
    return jsonify({'watches': [{
        'id': w.id,
        'address': w.address,
        'asking_price': w.asking_price,
        'created_at': w.created_at.isoformat(),
        'expires_at': w.expires_at.isoformat() if w.expires_at else None,
        'last_comps_check': w.last_comps_check_at.isoformat() if w.last_comps_check_at else None,
        'last_earthquake_check': w.last_earthquake_check_at.isoformat() if w.last_earthquake_check_at else None,
        'offer_accepted_date':         w.offer_accepted_date.isoformat() if w.offer_accepted_date else None,
        'inspection_contingency_date': w.inspection_contingency_date.isoformat() if w.inspection_contingency_date else None,
        'loan_contingency_date':       w.loan_contingency_date.isoformat() if w.loan_contingency_date else None,
        'appraisal_contingency_date':  w.appraisal_contingency_date.isoformat() if w.appraisal_contingency_date else None,
        'seller_response_deadline':    w.seller_response_deadline.isoformat() if w.seller_response_deadline else None,
        'repair_completion_deadline':  w.repair_completion_deadline.isoformat() if w.repair_completion_deadline else None,
        'close_of_escrow_date':        w.close_of_escrow_date.isoformat() if w.close_of_escrow_date else None,
    } for w in watches]})


@app.route('/api/track/feature', methods=['POST'])
def track_feature_event():
    """
    Lightweight feature engagement tracker. Called from JS on key interactions.
    Works for both logged-in users (user_id) and anonymous sessions (session_id).

    POST body:
        feature     (str, required) — e.g. 'addendum_draft', 'repair_breakdown_open'
        action      (str)           — 'click' | 'open' | 'download' | 'copy' (default: 'click')
        analysis_id (int, optional)
        property_id (int, optional)
        meta        (obj, optional) — any extra context
    """
    try:
        data = request.get_json(silent=True) or {}
        feature = (data.get('feature') or '').strip()[:80]
        if not feature:
            return jsonify({'ok': False, 'error': 'feature is required'}), 400

        action      = (data.get('action') or 'click')[:40]
        analysis_id = data.get('analysis_id')
        property_id = data.get('property_id')
        meta        = data.get('meta')

        # Coerce IDs
        try: analysis_id = int(analysis_id) if analysis_id else None
        except (TypeError, ValueError): analysis_id = None
        try: property_id = int(property_id) if property_id else None
        except (TypeError, ValueError): property_id = None

        uid = current_user.id if current_user.is_authenticated else None
        sid = session.get('_id') or request.cookies.get('session')

        evt = FeatureEvent(
            user_id     = uid,
            session_id  = str(sid)[:64] if sid else None,
            feature     = feature,
            action      = action,
            analysis_id = analysis_id,
            property_id = property_id,
            meta        = json.dumps(meta) if meta else None,
        )
        db.session.add(evt)
        db.session.commit()
        return jsonify({'ok': True})

    except Exception as e:
        logging.warning(f"feature_event track error: {e}")
        db.session.rollback()
        return jsonify({'ok': False}), 500


@app.route('/api/paywall/reason', methods=['POST'])
def paywall_reason():
    """
    Record why a free user didn't convert at the paywall.
    Called from two surfaces:
      1. The one-click reason row below the unlock card (source='inline')
      2. The exit intent modal when cursor leaves the viewport (source='exit_intent')

    POST body:
        reason   (str) — 'not_ready' | 'price' | 'thinking' | 'not_useful'
        source   (str) — 'inline' | 'exit_intent'
        page     (str, optional) — pathname where it fired
        analysis_id (int, optional)
    """
    try:
        data = request.get_json(silent=True) or {}
        reason  = (data.get('reason') or '').strip()[:50]
        source  = (data.get('source') or 'inline').strip()[:30]
        page    = (data.get('page') or '').strip()[:200]
        analysis_id = data.get('analysis_id')

        valid_reasons = {'not_ready', 'price', 'thinking', 'not_useful'}
        if reason not in valid_reasons:
            return jsonify({'error': 'invalid reason'}), 400

        user_id = current_user.id if current_user.is_authenticated else None

        # Log to funnel_events table (reuse existing infrastructure)
        try:
            from models import FunnelEvent
            event = FunnelEvent(
                user_id=user_id,
                stage='paywall_reason',
                source=source,
                metadata=json.dumps({
                    'reason': reason,
                    'page': page,
                    'analysis_id': analysis_id,
                }),
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent', '')[:200],
            )
            db.session.add(event)
            db.session.commit()
        except Exception:
            # FunnelEvent may not exist — log to app logger instead
            logging.info(f"PAYWALL_REASON user={user_id} reason={reason} source={source} page={page}")

        return jsonify({'ok': True})
    except Exception as e:
        logging.error(f"paywall_reason error: {e}")
        return jsonify({'error': 'server error'}), 500


@app.route('/api/feedback/issue', methods=['POST'])
def submit_issue_feedback():
    """
    Record a buyer's verdict on a flagged repair issue.
    Works for authenticated and anonymous users.

    POST body:
        system       (str, required) — e.g. 'foundation'
        verdict      (str, required) — 'confirmed' | 'not_found' | 'partial'
        severity     (str, optional)
        description  (str, optional)
        analysis_id  (int, optional)
        property_id  (int, optional)
        buyer_note   (str, optional)
    """
    try:
        from models import IssueConfirmation
        data = request.get_json(silent=True) or {}

        system  = (data.get('system') or '').strip()[:80]
        verdict = (data.get('verdict') or '').strip()[:20]

        if not system or verdict not in ('confirmed', 'not_found', 'partial'):
            return jsonify({'ok': False, 'error': 'system and valid verdict required'}), 400

        uid = current_user.id if current_user.is_authenticated else None

        def _safe_int(v):
            try: return int(v)
            except: return None

        fb = IssueConfirmation(
            user_id     = uid,
            analysis_id = _safe_int(data.get('analysis_id')),
            property_id = _safe_int(data.get('property_id')),
            system      = system,
            severity    = (data.get('severity') or '')[:20] or None,
            description = (data.get('description') or '')[:500] or None,
            verdict     = verdict,
            buyer_note  = (data.get('buyer_note') or '')[:300] or None,
        )
        db.session.add(fb)
        db.session.commit()

        # Also track as a feature event for the dashboard
        try:
            fe = FeatureEvent(
                user_id    = uid,
                feature    = 'issue_feedback',
                action     = verdict,
                analysis_id= fb.analysis_id,
                property_id= fb.property_id,
                meta       = json.dumps({'system': system, 'severity': fb.severity}),
            )
            db.session.add(fe)
            db.session.commit()
        except Exception:
            pass

        return jsonify({'ok': True, 'id': fb.id})

    except Exception as e:
        db.session.rollback()
        logging.warning(f'Issue feedback error: {e}')
        return jsonify({'ok': False, 'error': 'An error occurred.'}), 500


@app.route('/api/alerts', methods=['GET'])
@login_required
def list_agent_alerts():
    """List recent agentic alerts for the current user."""
    from models import AgentAlert, PropertyWatch
    limit = min(int(request.args.get('limit', 20)), 50)
    # Get alert IDs via watch ownership
    watch_ids = [w.id for w in PropertyWatch.query.filter_by(user_id=current_user.id).all()]
    if not watch_ids:
        return jsonify({'alerts': []})
    alerts = AgentAlert.query.filter(
        AgentAlert.watch_id.in_(watch_ids)
    ).order_by(AgentAlert.created_at.desc()).limit(limit).all()
    return jsonify({'alerts': [{
        'id': a.id,
        'alert_type': a.alert_type,
        'severity': a.severity,
        'title': a.title,
        'body': a.body,
        'created_at': a.created_at.isoformat(),
        'read_at': a.read_at.isoformat() if a.read_at else None,
    } for a in alerts]})


@app.route('/api/alerts/<int:alert_id>/read', methods=['POST'])
@login_required
def mark_alert_read(alert_id):
    """Mark an alert as read."""
    from models import AgentAlert, PropertyWatch
    alert = AgentAlert.query.get(alert_id)
    if not alert:
        return jsonify({'error': 'Alert not found'}), 404
    watch = PropertyWatch.query.filter_by(id=alert.watch_id, user_id=current_user.id).first()
    if not watch:
        return jsonify({'error': 'Not authorised'}), 403
    alert.read_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})
