"""
OfferWise - Complete Application with Authentication and Storage
"""

from flask import Flask, request, jsonify, send_from_directory, render_template, redirect, url_for, flash, session, make_response
from flask_cors import CORS
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from authlib.integrations.flask_client import OAuth
import base64
import os
import json
import secrets
from datetime import datetime, timedelta

# Import intelligence modules
from document_parser import DocumentParser
from risk_scoring_model import BuyerProfile
from offerwise_intelligence import OfferWiseIntelligence
from pdf_handler import PDFHandler

# Import database models
from models import db, User, Property, Document, Analysis, UsageRecord, MagicLink
from auth_config import PRICING_TIERS

# Initialize Flask app
app = Flask(__name__, static_folder='static')
CORS(app, supports_credentials=True)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production-' + secrets.token_hex(16))

# Database configuration - default to SQLite, optionally use PostgreSQL
database_url = os.environ.get('DATABASE_URL', 'sqlite:///offerwise.db')

# Handle Render's postgres:// URL (needs to be postgresql://)
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max
app.config['UPLOAD_FOLDER'] = 'uploads'

# Initialize extensions
db.init_app(app)
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

# Initialize intelligence
parser = DocumentParser()
intelligence = OfferWiseIntelligence()
pdf_handler = PDFHandler()

# Create upload folder
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

@app.route('/register', methods=['GET', 'POST'])
def register():
    """User registration"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        name = data.get('name', '')
        
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        
        # Check if user exists
        if User.query.filter_by(email=email).first():
            return jsonify({'error': 'Email already registered'}), 400
        
        # Create user
        user = User(
            email=email,
            name=name,
            auth_provider='email',
            tier='free',
            subscription_status='active'
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        # Log them in
        login_user(user)
        
        return jsonify({
            'success': True,
            'message': 'Registration successful',
            'redirect': url_for('dashboard')
        })
    
    return send_from_directory('static', 'register.html')

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    """User login"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        
        user = User.query.filter_by(email=email).first()
        
        if not user or not user.check_password(password):
            return jsonify({'error': 'Invalid email or password'}), 401
        
        login_user(user, remember=data.get('remember', False))
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Login successful',
            'redirect': url_for('dashboard')
        })
    
    return send_from_directory('static', 'login.html')

@app.route('/logout')
@login_required
def logout():
    """User logout"""
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

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
                subscription_status='active'
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

# ============================================================================
# DASHBOARD & PROPERTY MANAGEMENT
# ============================================================================

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

@app.route('/api/upload-pdf', methods=['POST', 'OPTIONS'])
@login_required
def upload_pdf():
    """Upload and extract text from PDF"""
    if request.method == 'OPTIONS':
        return jsonify({'success': True})
    
    try:
        data = request.get_json()
        pdf_base64 = data.get('pdf_base64', '')
        
        # Remove data URL prefix if present
        if ',' in pdf_base64:
            pdf_base64 = pdf_base64.split(',')[1]
        
        # Decode and extract text
        pdf_bytes = base64.b64decode(pdf_base64)
        result = pdf_handler.extract_text_from_bytes(pdf_bytes)
        
        # Handle both dict and string returns
        if isinstance(result, dict):
            text = result.get('text', '')
            page_count = result.get('page_count', 0)
        else:
            text = result
            page_count = 0
        
        return jsonify({
            'success': True,
            'text': text,
            'page_count': page_count
        })
    
    except Exception as e:
        print(f"Upload error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
@login_required
def analyze_property():
    """Analyze a property (protected endpoint)"""
    
    # Check usage limits
    if not current_user.can_analyze_property():
        limits = current_user.get_tier_limits()
        return jsonify({
            'error': 'Monthly limit reached',
            'message': f'You have analyzed {limits["properties_per_month"]} properties this month. Please upgrade your plan.',
            'upgrade_url': url_for('pricing')
        }), 403
    
    try:
        data = request.get_json()
        
        # Extract data - accept both text and PDF formats
        property_address = data.get('property_address', '')
        property_price = data.get('property_price', 0)
        
        # Accept text format (from upload endpoint)
        seller_disclosure_text = data.get('seller_disclosure_text', '')
        inspection_report_text = data.get('inspection_report_text', '')
        
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
        
        # Save document records with text
        disclosure_doc = Document(
            property_id=property.id,
            document_type='seller_disclosure',
            filename='seller_disclosure.txt',
            file_path=os.path.join(property_folder, 'seller_disclosure.txt'),
            file_size_bytes=len(seller_disclosure_text.encode('utf-8')),
            extracted_text=seller_disclosure_text
        )
        db.session.add(disclosure_doc)
        
        inspection_doc = Document(
            property_id=property.id,
            document_type='inspection_report',
            filename='inspection_report.txt',
            file_path=os.path.join(property_folder, 'inspection_report.txt'),
            file_size_bytes=len(inspection_report_text.encode('utf-8')),
            extracted_text=inspection_report_text
        )
        db.session.add(inspection_doc)
        
        # Run analysis
        buyer_profile = BuyerProfile(
            max_budget=buyer_profile_data.get('max_budget', 0),
            repair_tolerance=buyer_profile_data.get('repair_tolerance', 'moderate'),
            transparency_weight=buyer_profile_data.get('transparency_weight', 1.0),
            biggest_regret=buyer_profile_data.get('biggest_regret', 'hidden_issues')
        )
        
        result = intelligence.analyze_property(
            disclosure_text=seller_disclosure_text,
            inspection_text=inspection_report_text,
            property_address=property_address,
            buyer_profile=buyer_profile
        )
        
        # Save analysis
        analysis = Analysis(
            property_id=property.id,
            result_json=json.dumps(result),
            buyer_profile_json=json.dumps(buyer_profile_data)
        )
        db.session.add(analysis)
        
        # Update property
        property.status = 'completed'
        property.analyzed_at = datetime.utcnow()
        
        # Increment usage
        current_user.increment_usage()
        
        db.session.commit()
        
        # Return result with property ID
        result['property_id'] = property.id
        return jsonify(result)
        
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

@app.route('/api/pricing')
def get_pricing():
    """Get pricing tiers"""
    return jsonify({'tiers': PRICING_TIERS})

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

# Initialize database
with app.app_context():
    db.create_all()
    print("Database initialized!")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
