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

# Import database models
from models import db, User, Property, Document, Analysis, UsageRecord, MagicLink, ConsentRecord
from auth_config import PRICING_TIERS
from legal_disclaimers import (
    get_disclaimer_text, 
    get_disclaimer_version, 
    get_all_disclaimers,
    ANALYSIS_DISCLAIMER_VERSION
)

# Stripe for payment processing
import stripe
# Stripe configuration
# Use environment variables, or fall back to test keys for development
STRIPE_TEST_SECRET = 'sk_test_51QaXIiRwZq9gHO0gQ5XqXvZE9W8j0hZYZJNs0K4gXXXXXXXXXXX'  # Replace with your test key
STRIPE_TEST_PUBLISHABLE = 'pk_test_51QaXIiRwZq9gHO0gXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX'  # Replace with your test key

stripe_secret = os.environ.get('STRIPE_SECRET_KEY', '')
stripe_publishable = os.environ.get('STRIPE_PUBLISHABLE_KEY', '')

# If no keys set, use test mode defaults in development
if not stripe_secret and os.environ.get('FLASK_ENV') == 'development':
    stripe_secret = STRIPE_TEST_SECRET
    stripe_publishable = STRIPE_TEST_PUBLISHABLE
    logging.warning("⚠️  Using default Stripe TEST keys. Set STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY in .env for your own keys.")
elif not stripe_secret:
    logging.error("❌ STRIPE_SECRET_KEY not set! Payments will not work.")

stripe.api_key = stripe_secret

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

# Initialize Flask app
app = Flask(__name__, static_folder='static')

# Apply ProxyFix for proper HTTPS detection behind reverse proxy (Render)
# This allows Flask to properly detect HTTPS from X-Forwarded-Proto header
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

CORS(app, supports_credentials=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production-' + secrets.token_hex(16))

# Database configuration - default to SQLite, optionally use PostgreSQL
database_url = os.environ.get('DATABASE_URL', 'sqlite:///offerwise.db')

# Handle Render's postgres:// URL (needs to be postgresql://)
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max - handles comprehensive disclosure packages
app.config['UPLOAD_FOLDER'] = 'uploads'

# Force HTTPS for OAuth and production
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.config['SESSION_COOKIE_SECURE'] = True  # Only send cookies over HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Initialize extensions
db.init_app(app)

# SECURITY: Rate limiting to prevent abuse
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["1000 per day", "200 per hour"],
    storage_uri="memory://",  # In-memory storage (no Redis needed)
    strategy="fixed-window"
)
logger.info("✅ Rate limiting enabled")

# SECURITY: Add security headers to all responses
@app.after_request
def set_security_headers(response):
    """Add security headers to prevent common attacks"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    # Only set HSTS in production (HTTPS)
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

# Create database tables and run health checks on startup
with app.app_context():
    try:
        # CRITICAL: Create all tables first (if they don't exist)
        logger.info("🔧 Creating database tables...")
        db.create_all()
        logger.info("✅ Database tables created/verified")
        
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
login_manager.login_view = 'login_page'
login_manager.login_message = 'Please log in to access this page.'

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
    print("⚠️  Apple OAuth not configured (missing APPLE_CLIENT_ID or APPLE_CLIENT_SECRET)")

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
    print("⚠️  Facebook OAuth not configured (missing FACEBOOK_CLIENT_ID or FACEBOOK_CLIENT_SECRET)")

# Initialize intelligence
parser = DocumentParser()
intelligence = OfferWiseIntelligence()
pdf_handler = PDFHandler()

# Initialize async PDF worker with memory-optimized settings
# Render free tier: 512MB RAM limit, so we use minimal workers
max_workers = int(os.environ.get('PDF_WORKER_THREADS', '2'))  # Reduced from 10 to 2
pdf_worker = initialize_worker(job_manager, pdf_handler, max_workers=max_workers)
logger.info(f"✅ Async PDF processing enabled with {max_workers} worker threads (memory-optimized)")

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

@app.route('/login')
def login_page():
    """Login page - OAuth only"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    return send_from_directory('static', 'login.html')

@app.route('/logout')
@login_required
def logout():
    """User logout - serves page that clears localStorage"""
    logout_user()
    # Serve logout page that clears localStorage before redirecting
    return send_from_directory('static', 'logout.html')

@app.route('/login/google')
def login_google():
    """Initiate Google OAuth login"""
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/google/callback')
def google_callback():
    """Handle Google OAuth callback"""
    try:
        # Get the token from Google
        token = google.authorize_access_token()
        
        # Get user info from Google
        user_info = token.get('userinfo')
        if not user_info:
            resp = google.get('https://www.googleapis.com/oauth2/v3/userinfo')
            user_info = resp.json()
        
        email = user_info.get('email')
        name = user_info.get('name')
        google_id = user_info.get('sub')
        
        if not email:
            flash('Could not get email from Google. Please try again.', 'error')
            return redirect(url_for('login_page'))
        
        # Check if user exists
        user = User.query.filter_by(email=email).first()
        
        if user:
            # Update Google ID if not set
            if not user.google_id:
                user.google_id = google_id
                user.auth_provider = 'google'
                db.session.commit()
        else:
            # Create new user
            user = User(
                email=email,
                name=name,
                google_id=google_id,
                auth_provider='google',
                tier='free',
                subscription_status='active',
                analysis_credits=1  # Give 1 free credit to new users
            )
            
            db.session.add(user)
            db.session.commit()
        
        # Log the user in
        login_user(user)
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        print(f"Google OAuth error: {e}")
        flash('An error occurred during Google login. Please try again.', 'error')
        return redirect(url_for('login_page'))

@app.route('/login/apple')
def login_apple():
    """Initiate Apple OAuth login"""
    if not apple:
        return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Apple Login - Configuration Required</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
                    color: #e2e8f0;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                    margin: 0;
                    padding: 20px;
                }
                .container {
                    max-width: 600px;
                    background: #1e293b;
                    border-radius: 16px;
                    padding: 40px;
                    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
                    border: 1px solid #334155;
                }
                h1 { color: #f1f5f9; margin-top: 0; }
                .message { background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 20px; margin: 20px 0; }
                .info { background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.3); border-radius: 8px; padding: 20px; margin: 20px 0; font-size: 14px; line-height: 1.6; }
                .button { display: inline-block; padding: 12px 24px; background: linear-gradient(135deg, #0ea5e9 0%, #06b6d4 100%); color: white; text-decoration: none; border-radius: 8px; font-weight: 600; margin-top: 20px; }
                code { background: #0f172a; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🍎 Apple Login Configuration Required</h1>
                <div class="message">
                    <strong>⚠️ Apple OAuth is not yet configured for this application.</strong>
                </div>
                <div class="info">
                    <p><strong>For the application administrator:</strong></p>
                    <p>To enable Apple login, please configure the following environment variables in your Render dashboard:</p>
                    <ul>
                        <li><code>APPLE_CLIENT_ID</code> - Your Apple Service ID</li>
                        <li><code>APPLE_CLIENT_SECRET</code> - Your Apple Private Key</li>
                    </ul>
                    <p><strong>Setup instructions:</strong></p>
                    <ol>
                        <li>Visit <a href="https://developer.apple.com" target="_blank" style="color: #60a5fa;">developer.apple.com</a></li>
                        <li>Create an App ID and Service ID</li>
                        <li>Configure "Sign in with Apple"</li>
                        <li>Generate a private key</li>
                        <li>Add credentials to Render environment variables</li>
                        <li>Redeploy the application</li>
                    </ol>
                </div>
                <a href="/login" class="button">← Back to Login</a>
            </div>
        </body>
        </html>
        ''')
    redirect_uri = url_for('apple_callback', _external=True)
    return apple.authorize_redirect(redirect_uri)

@app.route('/auth/apple/callback')
def apple_callback():
    """Handle Apple OAuth callback"""
    try:
        # Get the token from Apple
        token = apple.authorize_access_token()
        
        # Get user info from Apple
        user_info = token.get('userinfo')
        if not user_info:
            resp = apple.get('https://appleid.apple.com/auth/userinfo')
            user_info = resp.json()
        
        email = user_info.get('email')
        apple_id = user_info.get('sub')
        
        # Apple may not provide name on subsequent logins
        name = None
        if 'name' in user_info:
            name_obj = user_info.get('name', {})
            if isinstance(name_obj, dict):
                first = name_obj.get('firstName', '')
                last = name_obj.get('lastName', '')
                name = f"{first} {last}".strip()
        
        if not email:
            flash('Could not get email from Apple. Please try again.', 'error')
            return redirect(url_for('login_page'))
        
        # Check if user exists
        user = User.query.filter_by(email=email).first()
        
        if user:
            # Update Apple ID if not set
            if not user.apple_id:
                user.apple_id = apple_id
                if not user.auth_provider or user.auth_provider == 'email':
                    user.auth_provider = 'apple'
                db.session.commit()
        else:
            # Create new user
            user = User(
                email=email,
                name=name or email.split('@')[0],  # Fallback to email prefix if no name
                apple_id=apple_id,
                auth_provider='apple',
                tier='free',
                subscription_status='active',
                analysis_credits=1  # Give 1 free credit to new users
            )
            
            db.session.add(user)
            db.session.commit()
        
        # Log the user in
        login_user(user)
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        print(f"Apple OAuth error: {e}")
        flash('An error occurred during Apple login. Please try again.', 'error')
        return redirect(url_for('login_page'))

@app.route('/login/facebook')
def login_facebook():
    """Initiate Facebook OAuth login"""
    if not facebook:
        return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Facebook Login - Configuration Required</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
                    color: #e2e8f0;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                    margin: 0;
                    padding: 20px;
                }
                .container {
                    max-width: 600px;
                    background: #1e293b;
                    border-radius: 16px;
                    padding: 40px;
                    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
                    border: 1px solid #334155;
                }
                h1 { color: #f1f5f9; margin-top: 0; }
                .message { background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 20px; margin: 20px 0; }
                .info { background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.3); border-radius: 8px; padding: 20px; margin: 20px 0; font-size: 14px; line-height: 1.6; }
                .button { display: inline-block; padding: 12px 24px; background: linear-gradient(135deg, #0ea5e9 0%, #06b6d4 100%); color: white; text-decoration: none; border-radius: 8px; font-weight: 600; margin-top: 20px; }
                code { background: #0f172a; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
                a { color: #60a5fa; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📘 Facebook Login Configuration Required</h1>
                <div class="message">
                    <strong>⚠️ Facebook OAuth is not yet configured for this application.</strong>
                </div>
                <div class="info">
                    <p><strong>For the application administrator:</strong></p>
                    <p>To enable Facebook login, please configure the following environment variables in your Render dashboard:</p>
                    <ul>
                        <li><code>FACEBOOK_CLIENT_ID</code> - Your Facebook App ID</li>
                        <li><code>FACEBOOK_CLIENT_SECRET</code> - Your Facebook App Secret</li>
                    </ul>
                    <p><strong>Setup instructions:</strong></p>
                    <ol>
                        <li>Visit <a href="https://developers.facebook.com/apps" target="_blank">Facebook Developers</a></li>
                        <li>Create a new app or select existing app</li>
                        <li>Add "Facebook Login" product</li>
                        <li>Get App ID and App Secret from Settings → Basic</li>
                        <li>Configure Valid OAuth Redirect URIs to include: <code>https://your-app.onrender.com/auth/facebook/callback</code></li>
                        <li>Add credentials to Render environment variables</li>
                        <li>Redeploy the application</li>
                    </ol>
                    <p><strong>⚡ Quick Setup:</strong> In Render dashboard → Environment → Add:</p>
                    <ul>
                        <li><code>FACEBOOK_CLIENT_ID</code> = your_app_id_here</li>
                        <li><code>FACEBOOK_CLIENT_SECRET</code> = your_app_secret_here</li>
                    </ul>
                </div>
                <a href="/login" class="button">← Back to Login</a>
            </div>
        </body>
        </html>
        ''')
    redirect_uri = url_for('facebook_callback', _external=True)
    return facebook.authorize_redirect(redirect_uri)

@app.route('/auth/facebook/callback')
def facebook_callback():
    """Handle Facebook OAuth callback"""
    try:
        # Get the token from Facebook
        token = facebook.authorize_access_token()
        
        # Get user info from Facebook
        resp = facebook.get('me?fields=id,name,email')
        user_info = resp.json()
        
        email = user_info.get('email')
        name = user_info.get('name')
        facebook_id = user_info.get('id')
        
        if not email:
            flash('Could not get email from Facebook. Please try again.', 'error')
            return redirect(url_for('login_page'))
        
        # Check if user exists
        user = User.query.filter_by(email=email).first()
        
        if user:
            # Update Facebook ID if not set
            if not user.facebook_id:
                user.facebook_id = facebook_id
                if not user.auth_provider or user.auth_provider == 'email':
                    user.auth_provider = 'facebook'
                db.session.commit()
        else:
            # Create new user
            user = User(
                email=email,
                name=name,
                facebook_id=facebook_id,
                auth_provider='facebook',
                tier='free',
                subscription_status='active',
                analysis_credits=1  # Give 1 free credit to new users
            )
            
            db.session.add(user)
            db.session.commit()
        
        # Log the user in
        login_user(user)
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        print(f"Facebook OAuth error: {e}")
        flash('An error occurred during Facebook login. Please try again.', 'error')
        return redirect(url_for('login_page'))

# ============================================================================
# DASHBOARD & PROPERTY MANAGEMENT
# ============================================================================

@app.route('/api/user/credits')
@api_login_required  # Use API-friendly decorator
def get_user_credits():
    """Get current user's credit balance"""
    return jsonify({
        'credits': current_user.analysis_credits,
        'user_id': current_user.id,
        'email': current_user.email,
        'authenticated': True
    })

@app.route('/dashboard')
@login_required
def dashboard():
    """User dashboard"""
    # Get user's properties
    properties = Property.query.filter_by(user_id=current_user.id).order_by(Property.created_at.desc()).all()
    
    # Get usage stats
    usage = current_user.get_current_usage()
    limits = current_user.get_tier_limits()
    
    # Calculate storage used
    total_storage = db.session.query(db.func.sum(Document.file_size_bytes)).filter(
        Document.property_id.in_([p.id for p in properties])
    ).scalar() or 0
    storage_mb = total_storage / (1024 * 1024)
    
    return send_from_directory('static', 'dashboard.html')

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
    
    logging.info(f"✅ Retrieved analysis for property {property_id} with price ${property.price:,}")
    
    return jsonify(result_json)

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

@app.route('/api/properties/<int:property_id>', methods=['DELETE'])
@login_required
def delete_property(property_id):
    """Delete a property"""
    property = Property.query.filter_by(id=property_id, user_id=current_user.id).first_or_404()
    
    # Delete files
    property_folder = os.path.join(app.config['UPLOAD_FOLDER'], str(current_user.id), str(property.id))
    if os.path.exists(property_folder):
        import shutil
        shutil.rmtree(property_folder)
    
    db.session.delete(property)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Property deleted'})

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
def cancel_ocr():
    """Cancel ongoing OCR processing to save costs when user leaves"""
    # Use user ID if logged in, otherwise fallback to session ID
    if current_user.is_authenticated:
        progress_key = f"user_{current_user.id}"
    else:
        progress_key = session.get('_id', 'default')
    
    # Set cancellation flag
    if progress_key in ocr_progress:
        ocr_progress[progress_key]['cancelled'] = True
        ocr_progress[progress_key]['status'] = 'cancelled'
        logger.info(f"🛑 OCR cancellation requested for key '{progress_key}' - will stop Google Vision calls")
    else:
        logger.info(f"⚠️ No active OCR found for key '{progress_key}'")
    
    return jsonify({'success': True, 'message': 'Cancellation signal sent'})

@app.route('/api/upload-pdf', methods=['POST', 'OPTIONS'])
@api_login_required  # Use API-friendly decorator
@limiter.limit("30 per hour")  # SECURITY: Max 30 uploads per hour per user
def upload_pdf():
    """Upload PDF and queue for async processing"""
    if request.method == 'OPTIONS':
        return jsonify({'success': True})
    
    try:
        logger.info("📤 PDF upload started (async mode)")
        data = request.get_json()
        pdf_base64 = data.get('pdf_base64', '')
        filename = data.get('filename', 'document.pdf')
        
        # Remove data URL prefix if present
        if ',' in pdf_base64:
            pdf_base64 = pdf_base64.split(',')[1]
        
        # SECURITY: Validate size BEFORE decoding
        if len(pdf_base64) > 20_971_520:  # 20MB base64 = ~15MB actual
            return jsonify({'error': 'File too large (max 15MB)'}), 413
        
        # Decode PDF
        logger.info(f"Decoding PDF (base64 length: {len(pdf_base64)})")
        try:
            pdf_bytes = base64.b64decode(pdf_base64)
        except Exception as e:
            logger.error(f"Base64 decode failed: {e}")
            return jsonify({'error': 'Invalid file encoding'}), 400
        
        logger.info(f"PDF decoded: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024/1024:.2f} MB)")
        
        # SECURITY: Validate it's actually a PDF (check magic bytes)
        if not pdf_bytes.startswith(b'%PDF-'):
            logger.error("File is not a valid PDF (wrong magic bytes)")
            return jsonify({'error': 'Invalid PDF file'}), 400
        
        # SECURITY: Validate size after decoding too
        if len(pdf_bytes) > 15_728_640:  # 15MB
            return jsonify({'error': 'File too large (max 15MB)'}), 413
        
        # Create job
        job_id = job_manager.create_job(
            user_id=current_user.id,
            filename=filename,
            pdf_bytes=pdf_bytes
        )
        
        logger.info(f"✅ Job {job_id} created for user {current_user.id}: {filename}")
        
        # Queue for async processing
        pdf_worker.process_pdf_async(job_id)
        
        # Return immediately!
        # CRITICAL: Don't include page_count at all until processing completes
        return jsonify({
            'success': True,
            'job_id': job_id,
            'status': 'processing',
            'message': 'Upload complete! Processing document...',
            'poll_url': f'/api/jobs/{job_id}',
            'async': True,
            'processing': True
            # NO page_count field at all!
        })
        
    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True)
        return jsonify({'error': 'Upload failed', 'message': str(e)}), 500

@app.route('/api/jobs/<job_id>', methods=['GET'])
@api_login_required  # Use API-friendly decorator
@limiter.limit("100 per minute")  # Allow frequent polling
def get_job_status(job_id):
    """Get status of PDF processing job"""
    try:
        job = job_manager.get_job(job_id)
        
        if not job:
            return jsonify({'error': 'Job not found', 'status': 'error'}), 404
        
        # SECURITY: Only owner can check job status
        if job.user_id != current_user.id:
            logger.warning(f"🚫 User {current_user.id} tried to access job {job_id} owned by user {job.user_id}")
            return jsonify({'error': 'Unauthorized', 'status': 'error'}), 403
        
        # Check if job is taking too long (> 10 minutes = 600 seconds)
        if job.status == 'processing':
            from datetime import datetime
            elapsed = (datetime.now() - job.created_at).total_seconds()
            if elapsed > 600:
                logger.error(f"⏰ Job {job_id} has been processing for {elapsed:.0f}s - marking as failed")
                job.status = 'failed'
                job.error = 'Processing timeout - job took longer than 10 minutes'
                job_manager.update_job(job_id, status='failed', error=job.error)
        
        # Return job status as dict
        return jsonify(job.to_dict())
        
    except Exception as e:
        logger.error(f"Error getting job status for {job_id}: {e}", exc_info=True)
        # Always return JSON, even on error
        return jsonify({
            'error': 'Failed to get job status',
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/worker/stats', methods=['GET'])
@login_required
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
@login_required  # SECURITY: Require authentication
def ai_status_debug():
    """
    Debug endpoint to check AI helper status - ADMIN ONLY
    """
    # SECURITY: Only admins can access this
    # You'll need to add is_admin field to User model
    # For now, restrict to specific user IDs or disable entirely
    
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
        return jsonify({'error': str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
@api_login_required  # Use API-friendly decorator
@limiter.limit("20 per hour")  # SECURITY: Max 20 analyses per hour per user
def analyze_property():
    """Analyze a property (protected endpoint)"""
    
    # Check credits first (pay-per-use system)
    if current_user.analysis_credits <= 0:
        return jsonify({
            'error': 'No analysis credits',
            'message': 'You have no analysis credits remaining. Please purchase more credits to continue.',
            'credits_remaining': 0,
            'upgrade_url': url_for('pricing')
        }), 403
    
    # Check usage limits (tier-based)
    if not current_user.can_analyze_property():
        limits = current_user.get_tier_limits()
        return jsonify({
            'error': 'Monthly limit reached',
            'message': f'You have analyzed {limits["properties_per_month"]} properties this month. Please upgrade your plan.',
            'upgrade_url': url_for('pricing')
        }), 403
    
    # 🛡️ LEGAL PROTECTION: Verify user has consented to analysis disclaimer
    # NOTE: Consent is now collected in Settings/Onboarding, not here
    has_consent = ConsentRecord.has_current_consent(
        user_id=current_user.id,
        consent_type='analysis_disclaimer',
        required_version=ANALYSIS_DISCLAIMER_VERSION
    )
    
    if not has_consent:
        logging.warning(f"⚖️ User {current_user.id} analyzing without explicit consent - will prompt in settings")
        # Don't block - user will be prompted to consent in settings
        # We record the analysis but flag that consent should be obtained
    else:
        logging.info(f"✅ User {current_user.id} has valid consent for analysis")
    
    try:
        data = request.get_json()
        
        # 🚨 EMERGENCY DEBUG - LOG INCOMING REQUEST
        logging.error("=" * 80)
        logging.error("🚨 EMERGENCY DEBUG - ANALYZE ENDPOINT START")
        logging.error(f"Request data keys: {list(data.keys())}")
        logging.error(f"property_price from request: '{data.get('property_price', 'MISSING')}'")
        logging.error(f"property_address from request: '{data.get('property_address', 'MISSING')}'")
        if 'buyer_profile' in data:
            logging.error(f"buyer_profile max_budget: {data.get('buyer_profile', {}).get('max_budget', 'MISSING')}")
        
        # NEW: Check if job_id provided (async upload)
        job_id = data.get('job_id')
        if job_id:
            logging.info(f"📋 Analyze called with job_id: {job_id}")
        
        logging.error("=" * 80)
        
        # Extract data - accept both text and PDF formats
        property_address = data.get('property_address', '')
        
        # Robust price handling - accept both string and number
        raw_price = data.get('property_price', 0)
        try:
            if raw_price:
                property_price = int(float(raw_price))  # Handle both string "925000" and number 925000
                if property_price <= 0 or property_price > 100000000:
                    logging.warning(f"Invalid property price: {property_price}")
                    return jsonify({'error': 'Property price must be between $1 and $100M'}), 400
                logging.info(f"Property price parsed: ${property_price:,}")
            else:
                property_price = 0
                logging.warning("No property price provided")
                return jsonify({'error': 'Property price is required. Please provide a valid asking price.'}), 400
        except (ValueError, TypeError) as e:
            logging.error(f"Price parsing error: {e}, raw_price={raw_price}")
            return jsonify({'error': 'Invalid property price format'}), 400
        
        # Accept text format (from upload endpoint)
        seller_disclosure_text = data.get('seller_disclosure_text', '')
        inspection_report_text = data.get('inspection_report_text', '')
        
        # NEW: If job_id provided, get text from completed job
        if job_id and (not seller_disclosure_text or not inspection_report_text):
            job = job_manager.get_job(job_id)
            
            if not job:
                return jsonify({'error': 'Job not found', 'message': 'Upload job has expired or does not exist'}), 404
            
            # SECURITY: Verify job ownership
            if job.user_id != current_user.id:
                logging.warning(f"🚫 User {current_user.id} tried to analyze job {job_id} owned by {job.user_id}")
                return jsonify({'error': 'Unauthorized'}), 403
            
            # Check job status
            if job.status == 'failed':
                return jsonify({
                    'error': 'Document processing failed',
                    'message': job.error or 'Failed to process uploaded document'
                }), 400
            
            if job.status in ['queued', 'processing']:
                # Job still processing - return special status
                return jsonify({
                    'error': 'Document still processing',
                    'message': f'Please wait... {job.message}',
                    'status': job.status,
                    'progress': job.progress,
                    'total': job.total,
                    'job_id': job_id,
                    'retry_after': 2  # Seconds to wait before retrying
                }), 202  # 202 Accepted (processing)
            
            if job.status == 'complete' and job.result:
                # Use text from completed job
                document_text = job.result.get('text', '')
                
                # Determine which document type this is based on request
                doc_type = data.get('document_type', 'inspection')  # Default to inspection
                
                if doc_type == 'disclosure':
                    seller_disclosure_text = document_text
                    logging.info(f"✅ Using disclosure text from job {job_id} ({len(document_text)} chars)")
                else:
                    inspection_report_text = document_text
                    logging.info(f"✅ Using inspection text from job {job_id} ({len(document_text)} chars)")
            else:
                return jsonify({
                    'error': 'Job incomplete',
                    'message': 'Document processing has not completed successfully'
                }), 400
        
        # Also accept PDF format (legacy)
        disclosure_pdf = data.get('disclosure_pdf', '')
        inspection_pdf = data.get('inspection_pdf', '')
        
        buyer_profile_data = data.get('buyer_profile', {})
        
        # If PDFs provided, extract text
        if disclosure_pdf and not seller_disclosure_text:
            if ',' in disclosure_pdf:
                disclosure_pdf = disclosure_pdf.split(',')[1]
            pdf_bytes = base64.b64decode(disclosure_pdf)
            result = pdf_handler.extract_text_from_bytes(pdf_bytes)
            seller_disclosure_text = result.get('text', '') if isinstance(result, dict) else result
        
        if inspection_pdf and not inspection_report_text:
            if ',' in inspection_pdf:
                inspection_pdf = inspection_pdf.split(',')[1]
            pdf_bytes = base64.b64decode(inspection_pdf)
            result = pdf_handler.extract_text_from_bytes(pdf_bytes)
            inspection_report_text = result.get('text', '') if isinstance(result, dict) else result
        
        # Validate we have the required data
        if not seller_disclosure_text or not inspection_report_text:
            return jsonify({'error': 'Both disclosure and inspection reports required'}), 400
        
        # Create property record
        property = Property(
            user_id=current_user.id,
            address=property_address,
            price=property_price or buyer_profile_data.get('max_budget'),
            status='pending'
        )
        db.session.add(property)
        db.session.flush()  # Get property ID
        
        # Create property folder
        property_folder = os.path.join(app.config['UPLOAD_FOLDER'], str(current_user.id), str(property.id))
        os.makedirs(property_folder, exist_ok=True)
        
        # Save text files to disk (NOT database!)
        disclosure_path = os.path.join(property_folder, 'seller_disclosure.txt')
        with open(disclosure_path, 'w', encoding='utf-8') as f:
            f.write(seller_disclosure_text)
        
        inspection_path = os.path.join(property_folder, 'inspection_report.txt')
        with open(inspection_path, 'w', encoding='utf-8') as f:
            f.write(inspection_report_text)
        
        # Save document records WITHOUT extracted_text (major speed improvement!)
        disclosure_doc = Document(
            property_id=property.id,
            document_type='seller_disclosure',
            filename='seller_disclosure.txt',
            file_path=disclosure_path,
            file_size_bytes=len(seller_disclosure_text.encode('utf-8'))
            # extracted_text removed - read from file_path instead
        )
        db.session.add(disclosure_doc)
        
        inspection_doc = Document(
            property_id=property.id,
            document_type='inspection_report',
            filename='inspection_report.txt',
            file_path=inspection_path,
            file_size_bytes=len(inspection_report_text.encode('utf-8'))
            # extracted_text removed - read from file_path instead
        )
        db.session.add(inspection_doc)
        
        # Run analysis
        buyer_profile = BuyerProfile(
            max_budget=buyer_profile_data.get('max_budget', 0),
            repair_tolerance=buyer_profile_data.get('repair_tolerance', 'moderate'),
            ownership_duration=buyer_profile_data.get('ownership_duration', '3-7'),
            biggest_regret=buyer_profile_data.get('biggest_regret', ''),
            replaceability=buyer_profile_data.get('replaceability', 'somewhat_unique'),
            deal_breakers=buyer_profile_data.get('deal_breakers', [])
        )
        
        # CRITICAL: Initialize caching and confidence systems
        cache = AnalysisCache()
        confidence_scorer = ConfidenceScorer()
        
        # Generate cache key
        buyer_profile_dict = {
            'max_budget': buyer_profile_data.get('max_budget', 0),
            'repair_tolerance': buyer_profile_data.get('repair_tolerance', 'moderate'),
            'ownership_duration': buyer_profile_data.get('ownership_duration', '3-7'),
            'biggest_regret': buyer_profile_data.get('biggest_regret', ''),
            'replaceability': buyer_profile_data.get('replaceability', 'somewhat_unique'),
            'deal_breakers': buyer_profile_data.get('deal_breakers', [])
        }
        
        cache_key = cache.generate_cache_key(
            inspection_text=inspection_report_text,
            disclosure_text=seller_disclosure_text,
            asking_price=property_price or buyer_profile_data.get('max_budget', 0),
            buyer_profile=buyer_profile_dict
        )
        
        # Try to get cached result
        cached_result = cache.get(cache_key)
        
        if cached_result:
            # Cache hit - instant response
            logging.info(f"✅ Cache HIT - returning cached analysis for {property_address}")
            result_dict = cached_result
            
            # CRITICAL: Validate cached result has property_price (Bug #27 - old cache entries)
            if 'property_price' not in result_dict or result_dict.get('property_price', 0) == 0:
                logging.warning(f"⚠️ Cached result missing property_price - invalidating cache entry")
                # Invalidate this cache entry and re-run analysis
                cached_result = None
                result_dict = None
            else:
                logging.info(f"✅ Cached result validated with property_price: ${result_dict['property_price']:,}")
        
        if not cached_result:
            # Cache miss OR invalid cache - run full analysis
            logging.info(f"🔄 Cache MISS or invalid - running full analysis for {property_address}")
            
            # 🚨 EMERGENCY DEBUG - BEFORE INTELLIGENCE CALL
            price_to_use = property_price or buyer_profile_data.get('max_budget', 0)
            logging.error("=" * 80)
            logging.error("🚨 CALLING INTELLIGENCE MODULE")
            logging.error(f"property_price variable: {property_price}")
            logging.error(f"buyer_profile max_budget: {buyer_profile_data.get('max_budget', 0)}")
            logging.error(f"price_to_use: {price_to_use}")
            logging.error(f"price_to_use type: {type(price_to_use)}")
            logging.error("=" * 80)
            
            result = intelligence.analyze_property(
                seller_disclosure_text=seller_disclosure_text,
                inspection_report_text=inspection_report_text,
                property_price=price_to_use,
                buyer_profile=buyer_profile,
                property_address=property_address
            )
            
            # 🚨 EMERGENCY DEBUG - AFTER INTELLIGENCE CALL
            logging.error("=" * 80)
            logging.error("🚨 INTELLIGENCE MODULE RETURNED")
            logging.error(f"Result type: {type(result)}")
            if hasattr(result, 'offer_strategy'):
                logging.error(f"offer_strategy exists: {result.offer_strategy}")
                if hasattr(result.offer_strategy, 'recommended_offer'):
                    logging.error(f"recommended_offer: {result.offer_strategy.recommended_offer}")
            logging.error("=" * 80)
            
            # Convert PropertyAnalysis to JSON-serializable dict
            from dataclasses import asdict
            import datetime as dt
            from enum import Enum
            import numpy as np
            
            def convert_value(obj):
                """Convert a single value to JSON-serializable format"""
                if isinstance(obj, (dt.datetime, dt.date)):
                    return obj.isoformat()
                elif isinstance(obj, Enum):
                    return obj.value
                elif isinstance(obj, np.ndarray):
                    return obj.tolist()
                # Handle PropertyRiskDNA - use to_dict if available
                elif hasattr(obj, 'to_dict') and callable(obj.to_dict):
                    return obj.to_dict()
                # Handle other innovation objects
                elif hasattr(obj, '__dataclass_fields__'):
                    return asdict(obj, dict_factory=dict_factory)
                else:
                    return obj
            
            def dict_factory(fields):
                """Custom dict factory for asdict that handles Enums and datetimes"""
                return {k: convert_value(v) for k, v in fields}
            
            # Convert with custom factory
            result_dict = asdict(result, dict_factory=dict_factory)
            
            # Recursively clean any remaining objects
            def clean_dict(obj):
                # Import numpy for array checking
                import numpy as np
                
                if isinstance(obj, np.ndarray):
                    # Convert numpy arrays to lists
                    return obj.tolist()
                elif isinstance(obj, dict):
                    return {k: clean_dict(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [clean_dict(item) for item in obj]
                elif isinstance(obj, Enum):
                    return obj.value
                elif isinstance(obj, (dt.datetime, dt.date)):
                    return obj.isoformat()
                else:
                    return obj
            
            result_dict = clean_dict(result_dict)
        
        # Clean up category names (remove underscores, title case)
        if 'risk_score' in result_dict and 'category_scores' in result_dict['risk_score']:
            for cat in result_dict['risk_score']['category_scores']:
                if 'category' in cat and isinstance(cat['category'], str):
                    # Replace underscores with spaces and title case
                    cat['category'] = cat['category'].replace('_', ' & ').title()
        
        # Professional cleanup for detailed expert output
        import re
        if 'risk_score' in result_dict and 'deal_breakers' in result_dict['risk_score']:
            cleaned_breakers = []
            seen_issues = set()
            
            for breaker in result_dict['risk_score']['deal_breakers']:
                clean_text = breaker
                
                # STEP 1: Remove programmer/system artifacts
                
                # Remove severity prefixes at start
                clean_text = re.sub(r'^(CRITICAL|MAJOR|MODERATE|MINOR)\s*[:\-]?\s*', '', clean_text, flags=re.IGNORECASE)
                
                # Remove programmer variable names (words with underscores)
                clean_text = re.sub(r'\b[a-z]+_[a-z_]+\b', '', clean_text, flags=re.IGNORECASE)
                
                # Remove internal system data references
                clean_text = re.sub(r'(?:with\s+)?(?:risk\s+)?score\s+\d+/\d+', '', clean_text, flags=re.IGNORECASE)
                clean_text = re.sub(r'severity\s*:\s*\d+', '', clean_text, flags=re.IGNORECASE)
                
                # Remove ALL CAPS segments (even in middle of sentence)
                clean_text = re.sub(r'\b[A-Z][A-Z\s\-]{2,}[A-Z]\b\s*[\-:]?\s*', '', clean_text)
                
                # Remove separator artifacts
                clean_text = re.sub(r'[=\-]{3,}', '', clean_text)
                clean_text = re.sub(r'^[-•*]\s*', '', clean_text, flags=re.MULTILINE)
                
                # CRITICAL: Remove leading colons (often left after prefix removal)
                clean_text = re.sub(r'^\s*:\s*', '', clean_text)
                
                # STEP 2: Fix grammar issues
                
                # Fix common grammar errors
                clean_text = re.sub(r'\bdisclose\b(?!\w)', 'disclosed', clean_text, flags=re.IGNORECASE)
                clean_text = re.sub(r'\bobserve\b(?!\w)', 'observed', clean_text, flags=re.IGNORECASE)
                
                # NOTE: DO NOT add periods between lowercase and uppercase automatically
                # This breaks proper nouns like "Federal Pacific", "Foundation Structure", etc.
                
                # STEP 3: Clean up formatting (keep detailed content)
                clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                clean_text = re.sub(r'\s+([.,;:!?])', r'\1', clean_text)
                
                # Remove trailing incomplete fragments
                clean_text = re.sub(r',?\s+OR[.,]?\s*$', '', clean_text, flags=re.IGNORECASE)
                
                # Fix incomplete last words (but keep detailed explanations)
                words = clean_text.split()
                if words and len(words[-1].rstrip('.,;:!?')) <= 2:
                    last_word = words[-1].rstrip('.,;:!?').lower()
                    if last_word in ['ye', 't', 'or', 'in', 'on', 'at', 'to']:
                        words = words[:-1]
                        clean_text = ' '.join(words)
                
                # STEP 4: Quality filters (but keep detailed content)
                
                # Must be at least 50 characters (detailed enough)
                if len(clean_text) < 50:
                    continue
                
                # Must not be all caps
                if clean_text.isupper():
                    continue
                
                # Must not end with colon
                if clean_text.endswith(':'):
                    continue
                
                # Filter recommendations/advice (not actual issues)
                advice_patterns = [
                    r'^consider\s+',
                    r'^completion\s+',
                    r'^recommend',
                    r'^suggest',
                    r'^should\s+consider',
                    r'^advise',
                    r'^buyer\s+should'
                ]
                if any(re.search(pattern, clean_text, re.IGNORECASE) for pattern in advice_patterns):
                    continue
                
                # Filter vague/generic statements (but keep detailed ones)
                if len(clean_text) < 100:  # Only check if relatively short
                    vague_patterns = [
                        r'^issues?\s+(?:with|in|noted)',
                        r'^concerns?\s+(?:with|in|about)',
                        r'^problems?\s+(?:with|in|found)',
                        r'^defects?\s+(?:were|noted)',
                        r'the following',
                        r'items? (?:were )?found',
                        r'repairs? (?:are )?needed'
                    ]
                    if any(re.search(pattern, clean_text, re.IGNORECASE) for pattern in vague_patterns):
                        continue
                
                # Must mention specific components (not just meta-commentary)
                specific_components = [
                    'panel', 'breaker', 'wiring', 'electrical', 'circuit',
                    'roof', 'shingle', 'flashing', 'gutter', 'soffit',
                    'foundation', 'basement', 'crawl', 'slab', 'footing',
                    'plumbing', 'pipe', 'drain', 'sewer', 'water', 'leak',
                    'hvac', 'furnace', 'ac', 'heating', 'cooling', 'duct',
                    'window', 'door', 'wall', 'floor', 'ceiling',
                    'insulation', 'vapor', 'ventilation',
                    'structural', 'beam', 'joist', 'framing',
                    'crack', 'damage', 'corrosion', 'rust', 'mold', 'rot'
                ]
                has_component = any(comp in clean_text.lower() for comp in specific_components)
                if not has_component:
                    continue
                
                # STEP 4: Deduplicate
                # Extract key terms for comparison
                key_terms = re.sub(r'[^a-z0-9\s]', '', clean_text.lower())
                key_terms = ' '.join(sorted(set(key_terms.split())))[:80]
                
                # Check if similar to existing items
                is_duplicate = False
                for existing in seen_issues:
                    # Count common words
                    existing_words = set(existing.split())
                    new_words = set(key_terms.split())
                    common = existing_words & new_words
                    # If more than 60% overlap, it's a duplicate
                    if len(common) > 0.6 * min(len(existing_words), len(new_words)):
                        is_duplicate = True
                        break
                
                if is_duplicate:
                    continue
                
                seen_issues.add(key_terms)
                
                # STEP 5: Ensure professional formatting
                if clean_text and clean_text[0].islower():
                    clean_text = clean_text[0].upper() + clean_text[1:]
                
                if clean_text and not clean_text[-1] in '.!?':
                    if len(clean_text.split()) >= 5:
                        clean_text += '.'
                
                # STEP 6: Final validation - must be detailed enough
                word_count = len(clean_text.split())
                if word_count < 6:  # Too short to be informative
                    continue
                
                cleaned_breakers.append(clean_text)
                
                # Stop at 6 items
                if len(cleaned_breakers) >= 6:
                    break
            
            result_dict['risk_score']['deal_breakers'] = cleaned_breakers
        
        # Custom JSON encoder for any remaining datetime/enum objects
        class DateTimeEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, (dt.datetime, dt.date)):
                    return obj.isoformat()
                if isinstance(obj, Enum):
                    return obj.value
                return super().default(obj)
        
        # Save analysis
        analysis = Analysis(
            property_id=property.id,
            result_json=json.dumps(result_dict, cls=DateTimeEncoder),
            buyer_profile_json=json.dumps(buyer_profile_data)
        )
        db.session.add(analysis)
        
        # Update property
        property.status = 'completed'
        property.analyzed_at = datetime.utcnow()
        
        # Increment usage
        current_user.increment_usage()
        
        # Decrement analysis credits
        if current_user.analysis_credits > 0:
            current_user.analysis_credits -= 1
            logging.info(f"Decremented credits for user {current_user.id}. Remaining: {current_user.analysis_credits}")
        
        db.session.commit()
        
        # CRITICAL: Add property_price to result_dict BEFORE validation (Bug #40 - $N/A fix)
        # Validation needs this to correctly validate the recommended_offer
        result_dict['property_price'] = property_price or buyer_profile_data.get('max_budget', 0)
        result_dict['property_address'] = property_address
        logging.info(f"Added property_price to result_dict BEFORE validation: ${result_dict['property_price']:,}")
        
        # CRITICAL: Validate all output before sending to user
        try:
            result_dict = validate_analysis_output(result_dict)
            logging.info("Analysis output validated successfully")
        except ValidationError as e:
            logging.warning(f"Validation warning: {e}")
            # Continue even if validation has warnings
        
        # CRITICAL: Detect and flag special property types (Bug #34, #38, #39)
        result_dict = detect_and_flag_special_properties(
            result_dict,
            seller_disclosure_text,
            inspection_report_text
        )
        
        # CRITICAL: Calculate confidence score (transparency for users)
        if not cached_result:  # Only calculate if not from cache
            confidence = confidence_scorer.calculate(
                analysis=result_dict,
                input_data={
                    'inspection': inspection_report_text,
                    'disclosure': seller_disclosure_text
                }
            )
            result_dict['confidence'] = confidence
            logging.info(f"Confidence score: {confidence['score']:.1f}% ({confidence['level']})")
            
            # Cache the result for future identical queries
            cache.set(
                cache_key=cache_key,
                analysis=result_dict,
                property_address=property_address,
                asking_price=property_price or buyer_profile_data.get('max_budget', 0)
            )
            logging.info(f"💾 Cached analysis with property_price: ${result_dict['property_price']:,}")
        
        # CRITICAL: Ensure property metadata is in result (Bug #27 - $N/A display fix)
        # This applies to BOTH cached and non-cached results
        result_dict['property_id'] = property.id
        
        # 🚨 EMERGENCY DEBUG LOGGING
        logging.error("=" * 80)
        logging.error("🚨 EMERGENCY DEBUG - PROPERTY PRICE CHECK")
        logging.error(f"property_price variable: {property_price}")
        logging.error(f"property_price type: {type(property_price)}")
        logging.error(f"buyer_profile_data.get('max_budget'): {buyer_profile_data.get('max_budget')}")
        logging.error(f"property.price in DB: {property.price}")
        
        # Property price and address already added before validation
        # Just ensure they're still there (paranoid check)
        if 'property_price' not in result_dict or result_dict['property_price'] <= 0:
            logging.error("⚠️ property_price missing or 0 after all processing - forcing it")
            result_dict['property_price'] = property_price or buyer_profile_data.get('max_budget', 0)
            result_dict['property_address'] = property_address
        
        logging.error(f"✅ Final property_price in result_dict: ${result_dict.get('property_price', 0):,}")
        
        # Check what's actually in result_dict
        logging.error(f"result_dict['property_price'] = {result_dict.get('property_price', 'MISSING!')}")
        logging.error(f"result_dict keys: {list(result_dict.keys())}")
        
        # Check offer_strategy
        if 'offer_strategy' in result_dict:
            logging.error(f"offer_strategy keys: {list(result_dict['offer_strategy'].keys())}")
            logging.error(f"recommended_offer: {result_dict['offer_strategy'].get('recommended_offer', 'MISSING!')}")
        else:
            logging.error("❌ NO offer_strategy in result_dict!")
        
        logging.error("=" * 80)
        
        return jsonify(result_dict)
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# ============================================================================
# PRICING & SUBSCRIPTION
# ============================================================================

@app.route('/pricing')
def pricing():
    """Pricing page"""
    return send_from_directory('static', 'pricing.html')

@app.route('/sample-analysis')
def sample_analysis():
    """Sample analysis page"""
    return send_from_directory('static', 'sample-analysis.html')

@app.route('/settings')
@login_required
def settings():
    """User settings page"""
    return send_from_directory('static', 'settings.html')

@app.route('/debug')
@login_required
def debug_page():
    """Debug diagnostic page"""
    return send_from_directory('static', 'debug.html')

@app.route('/onboarding')
@login_required
def serve_onboarding():
    """Serve guided onboarding wizard"""
    return send_from_directory('static', 'onboarding.html')

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
            'error': str(e),
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
            'limits': limits,
            'can_analyze': can_analyze
        })
    except Exception as e:
        logging.error(f"❌ Error in /api/user endpoint: {e}")
        logging.exception(e)
        return jsonify({
            'error': str(e),
            'id': current_user.id if current_user else None,
            'email': current_user.email if current_user else 'Error',
            'tier': 'free',
            'name': 'User',
            'credits': 0
        }), 500

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
        if 'max_budget' in data:
            old_value = current_user.max_budget
            current_user.max_budget = int(data['max_budget'])
            logging.info(f"✏️  Updating max_budget: {old_value} → {current_user.max_budget}")
        
        if 'repair_tolerance' in data:
            old_value = current_user.repair_tolerance
            current_user.repair_tolerance = data['repair_tolerance']
            logging.info(f"✏️  Updating repair_tolerance: {old_value} → {current_user.repair_tolerance}")
        
        if 'biggest_regret' in data:
            old_value = current_user.biggest_regret
            current_user.biggest_regret = data['biggest_regret']
            logging.info(f"✏️  Updating biggest_regret: {old_value} → {current_user.biggest_regret}")
        
        logging.info(f"")
        logging.info(f"💾 COMMITTING TO DATABASE...")
        
        # Mark onboarding as completed (only first time)
        if not current_user.onboarding_completed:
            current_user.onboarding_completed = True
            current_user.onboarding_completed_at = datetime.utcnow()
            logging.info(f"✅ Onboarding completed for user: {current_user.email}")
        
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
            'message': f'Error saving preferences: {str(e)}'
        }), 500

@app.route('/api/user/debug-data', methods=['GET'])
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
                'total_documents': sum(len(Document.query.filter_by(property_id=p.id).all()) for p in properties),
                'total_analyses': sum(len(Analysis.query.filter_by(property_id=p.id).all()) for p in properties),
                'total_usage_records': len(usage_records),
                'total_consent_records': len(consent_records)
            }
        })
        
    except Exception as e:
        logging.error(f"Error getting debug data: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/delete', methods=['POST'])
@login_required
def delete_user_account():
    """Delete user account and all associated data"""
    user_id = current_user.id
    user_email = current_user.email
    
    logging.info(f"")
    logging.info(f"=" * 80)
    logging.info(f"🗑️ ACCOUNT DELETION STARTED")
    logging.info(f"=" * 80)
    logging.info(f"User Email: {user_email}")
    logging.info(f"User ID: {user_id}")
    logging.info(f"Current User Object: {current_user}")
    logging.info(f"")
    
    try:
        # STEP 1: Count everything BEFORE deletion
        properties_count = Property.query.filter_by(user_id=user_id).count()
        documents_count = db.session.query(Document).join(Property).filter(Property.user_id == user_id).count()
        analyses_count = db.session.query(Analysis).join(Property).filter(Property.user_id == user_id).count()
        usage_count = UsageRecord.query.filter_by(user_id=user_id).count()
        consent_count = ConsentRecord.query.filter_by(user_id=user_id).count()
        
        logging.info(f"📊 BEFORE DELETION - Database State:")
        logging.info(f"   Properties: {properties_count}")
        logging.info(f"   Documents: {documents_count}")
        logging.info(f"   Analyses: {analyses_count}")
        logging.info(f"   Usage Records: {usage_count}")
        logging.info(f"   Consent Records: {consent_count}")
        logging.info(f"")
        
        # STEP 2: Delete all properties and their nested data
        properties = Property.query.filter_by(user_id=user_id).all()
        logging.info(f"🏠 DELETING {len(properties)} PROPERTIES:")
        
        for i, prop in enumerate(properties, 1):
            logging.info(f"   Property {i}/{len(properties)}: ID={prop.id}, Address={prop.address}")
            
            # Delete documents for this property
            documents = Document.query.filter_by(property_id=prop.id).all()
            logging.info(f"      📄 Deleting {len(documents)} documents...")
            for doc in documents:
                logging.info(f"         - Document ID={doc.id}, Filename={doc.filename}")
                db.session.delete(doc)
            
            # Delete analyses for this property
            analyses = Analysis.query.filter_by(property_id=prop.id).all()
            logging.info(f"      🎯 Deleting {len(analyses)} analyses...")
            for analysis in analyses:
                logging.info(f"         - Analysis ID={analysis.id}, Created={analysis.created_at}")
                db.session.delete(analysis)
            
            # Delete the property itself
            logging.info(f"      🗑️ Deleting property ID={prop.id}")
            db.session.delete(prop)
        
        logging.info(f"")
        
        # STEP 3: Delete usage records
        usage_records = UsageRecord.query.filter_by(user_id=user_id).all()
        logging.info(f"📊 DELETING {len(usage_records)} USAGE RECORDS:")
        for record in usage_records:
            logging.info(f"   - UsageRecord ID={record.id}")
            db.session.delete(record)
        
        logging.info(f"")
        
        # STEP 4: Delete consent records
        consent_records = ConsentRecord.query.filter_by(user_id=user_id).all()
        logging.info(f"📝 DELETING {len(consent_records)} CONSENT RECORDS:")
        for record in consent_records:
            logging.info(f"   - ConsentRecord ID={record.id}, Type={record.consent_type}")
            db.session.delete(record)
        
        logging.info(f"")
        
        # STEP 5: Delete the user
        logging.info(f"👤 DELETING USER ACCOUNT:")
        logging.info(f"   User ID: {user_id}")
        logging.info(f"   Email: {user_email}")
        logging.info(f"   Auth Provider: {current_user.auth_provider}")
        db.session.delete(current_user)
        
        logging.info(f"")
        logging.info(f"💾 COMMITTING TO DATABASE...")
        
        # COMMIT - This is the critical step!
        db.session.commit()
        
        logging.info(f"✅ DATABASE COMMIT SUCCESSFUL")
        logging.info(f"")
        
        # STEP 6: Verify deletion
        logging.info(f"🔍 VERIFYING DELETION:")
        
        # Check if user still exists
        deleted_user = User.query.filter_by(id=user_id).first()
        if deleted_user:
            logging.error(f"   ❌ USER STILL EXISTS IN DATABASE! ID={deleted_user.id}")
            logging.error(f"   THIS IS A CRITICAL ERROR!")
        else:
            logging.info(f"   ✅ User successfully deleted from database")
        
        # Check properties
        remaining_properties = Property.query.filter_by(user_id=user_id).count()
        logging.info(f"   Remaining Properties: {remaining_properties} (should be 0)")
        
        # Check documents
        remaining_documents = db.session.query(Document).join(Property).filter(Property.user_id == user_id).count()
        logging.info(f"   Remaining Documents: {remaining_documents} (should be 0)")
        
        # Check analyses
        remaining_analyses = db.session.query(Analysis).join(Property).filter(Property.user_id == user_id).count()
        logging.info(f"   Remaining Analyses: {remaining_analyses} (should be 0)")
        
        logging.info(f"")
        
        # Log out the user
        logging.info(f"🚪 LOGGING OUT USER...")
        logout_user()
        
        logging.info(f"")
        logging.info(f"=" * 80)
        logging.info(f"✅ ACCOUNT DELETION COMPLETED SUCCESSFULLY")
        logging.info(f"=" * 80)
        logging.info(f"Summary:")
        logging.info(f"   - {properties_count} properties deleted")
        logging.info(f"   - {documents_count} documents deleted")
        logging.info(f"   - {analyses_count} analyses deleted")
        logging.info(f"   - {usage_count} usage records deleted")
        logging.info(f"   - {consent_count} consent records deleted")
        logging.info(f"   - 1 user account deleted")
        logging.info(f"=" * 80)
        logging.info(f"")
        
        return jsonify({
            'success': True,
            'message': 'Account and all data deleted successfully',
            'deleted': {
                'properties': properties_count,
                'documents': documents_count,
                'analyses': analyses_count,
                'usage_records': usage_count,
                'consent_records': consent_count
            }
        })
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"")
        logging.error(f"=" * 80)
        logging.error(f"❌ ACCOUNT DELETION FAILED")
        logging.error(f"=" * 80)
        logging.error(f"Error: {str(e)}")
        logging.error(f"Error Type: {type(e).__name__}")
        logging.exception(e)
        logging.error(f"=" * 80)
        logging.error(f"")
        return jsonify({
            'success': False,
            'message': f'Error deleting account: {str(e)}'
        }), 500
        logging.error(f"❌ Error deleting user account: {e}")
        return jsonify({
            'success': False,
            'message': f'Error deleting account: {str(e)}'
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
def delete_account():
    """Permanently delete user account and all associated data"""
    try:
        user_id = current_user.id
        user_email = current_user.email
        
        # Log the deletion request
        logging.info(f'User {user_email} (ID: {user_id}) requested account deletion')
        
        # Since User model has cascade='all, delete-orphan' for properties and usage_records,
        # deleting the user will automatically delete those relationships.
        # We only need to manually delete MagicLinks (which use email, not user_id)
        
        # Delete magic links by email
        MagicLink.query.filter_by(email=user_email).delete()
        
        # Delete the user (cascades will handle properties, documents, analyses, usage_records)
        user = User.query.get(user_id)
        if user:
            db.session.delete(user)
            
            # Commit the deletion
            db.session.commit()
            
            # Log out the user AFTER successful commit
            logout_user()
            
            logging.info(f'Successfully deleted account for user {user_email} (ID: {user_id})')
            
            return jsonify({
                'success': True,
                'message': 'Account successfully deleted'
            }), 200
        else:
            logging.error(f'User not found during deletion: {user_email} (ID: {user_id})')
            return jsonify({
                'success': False,
                'error': 'User account not found'
            }), 404
        
    except Exception as e:
        db.session.rollback()
        logging.error(f'Error deleting account for user {user_email if "user_email" in locals() else "unknown"}: {str(e)}')
        logging.exception(e)  # Log full stack trace
        return jsonify({
            'success': False,
            'error': f'An error occurred while deleting your account: {str(e)}'
        }), 500

@app.route('/privacy')
def privacy_policy():
    """Privacy Policy page"""
    return send_from_directory('static', 'privacy.html')

@app.route('/contact')
def contact():
    """Contact page"""
    return send_from_directory('static', 'contact.html')

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
        
        # Define pricing
        prices = {
            'single': {'amount': 1500, 'credits': 1, 'name': 'Single Analysis'},
            'bundle_5': {'amount': 5000, 'credits': 5, 'name': '5-Analysis Bundle'},
            'bundle_10': {'amount': 7500, 'credits': 10, 'name': '10-Analysis Bundle'}
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
            success_url=url_for('payment_success', session_id='{CHECKOUT_SESSION_ID}', _external=True),
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
            'message': str(e)
        }), 500
    except Exception as e:
        logging.error(f"❌ Error creating checkout session: {str(e)}")
        return jsonify({'error': str(e)}), 500

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
        
        # Get user and credits from metadata
        user_id = session['metadata'].get('user_id')
        credits = int(session['metadata'].get('credits', 0))
        plan = session['metadata'].get('plan')
        
        # Update user credits
        user = User.query.get(user_id)
        if user:
            user.analysis_credits += credits
            db.session.commit()
            
            logging.info(f"Added {credits} credits to user {user_id} for plan {plan}")
        else:
            logging.error(f"User {user_id} not found for webhook")
    
    return jsonify({'status': 'success'})

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
            consent_types = ['analysis_disclaimer', 'terms_of_service', 'privacy_policy']
            statuses = []
            
            for consent_type in consent_types:
                required_version = get_disclaimer_version(consent_type)
                if required_version:
                    has_consent = ConsentRecord.has_current_consent(
                        user_id=current_user.id,
                        consent_type=consent_type,
                        required_version=required_version
                    )
                    
                    statuses.append({
                        'consent_type': consent_type,
                        'required_version': required_version,
                        'has_consent': has_consent,
                        'display_name': consent_type.replace('_', ' ').title()
                    })
            
            return jsonify({
                'statuses': statuses,
                'needs_onboarding': False,  # NEVER redirect if completed once
                'onboarding_completed': True,
                'all_consented': all(s['has_consent'] for s in statuses),
                'has_preferences': True
            })
        
        # For NEW users who haven't completed onboarding yet
        consent_types = ['analysis_disclaimer', 'terms_of_service', 'privacy_policy']
        statuses = []
        
        for consent_type in consent_types:
            required_version = get_disclaimer_version(consent_type)
            if required_version:
                has_consent = ConsentRecord.has_current_consent(
                    user_id=current_user.id,
                    consent_type=consent_type,
                    required_version=required_version
                )
                
                statuses.append({
                    'consent_type': consent_type,
                    'required_version': required_version,
                    'has_consent': has_consent,
                    'display_name': consent_type.replace('_', ' ').title()
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
        return jsonify({'error': str(e)}), 500


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
        return jsonify({'error': str(e)}), 500


@app.route('/api/consent/record', methods=['POST'])
@login_required
def record_consent():
    """
    Record user consent for a specific disclaimer/terms.
    
    CRITICAL: This provides legal protection.
    Records exactly what they agreed to, when, and from where.
    """
    try:
        data = request.get_json()
        consent_type = data.get('consent_type')  # 'analysis_disclaimer', 'terms', 'privacy'
        analysis_id = data.get('analysis_id')  # Optional - if consenting for specific analysis
        
        if not consent_type:
            return jsonify({'error': 'consent_type required'}), 400
        
        # Get the current version and text
        consent_version = get_disclaimer_version(consent_type)
        consent_text = get_disclaimer_text(consent_type)
        
        if not consent_version or not consent_text:
            return jsonify({'error': 'Invalid consent type'}), 400
        
        # Get audit trail info
        ip_address = request.remote_addr
        user_agent = request.headers.get('User-Agent', '')
        
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
        
        logging.info(f"✅ Consent recorded: user={current_user.id} type={consent_type} version={consent_version}")
        
        return jsonify({
            'success': True,
            'consent_id': consent.id,
            'consented_at': consent.consented_at.isoformat()
        })
        
    except Exception as e:
        logging.error(f"❌ Error recording consent: {e}")
        return jsonify({'error': str(e)}), 500


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
        return jsonify({'error': str(e)}), 500


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
        return jsonify({'error': str(e)}), 500


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
        logging.error(f"❌ Error generating negotiation document: {e}")
        logging.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

# ============================================================================
# ADMIN - DATABASE HEALTH
# ============================================================================

@app.route('/api/admin/health-check', methods=['POST'])
@login_required
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
            'message': str(e)
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

# ============================================================================
# STATIC PAGES
# ============================================================================

@app.route('/')
def index():
    """Landing page"""
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

@app.route('/api/system-info', methods=['GET'])
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
    
    if not info['ocr_fully_available']:
        info['warning'] = 'OCR not fully available - scanned PDFs will fail'
        info['fix'] = 'Install system dependencies: apt-get install poppler-utils tesseract-ocr'
    
    return jsonify(info)

# Initialize database
with app.app_context():
    db.create_all()
    print("Database initialized!")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
