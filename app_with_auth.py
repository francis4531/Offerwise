"""
OfferWise - Real Estate Intelligence Platform
Complete version with authentication, file storage, and pricing tiers
"""

import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, redirect, url_for, flash, session
from flask_cors import CORS
from flask_login import LoginManager, login_user, logout_user, current_user
from werkzeug.utils import secure_filename
import secrets

# Import models and authentication
from models import db, User, Property, Document, Analysis, MagicLink
from auth_config import (
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_DISCOVERY_URL,
    PRICING_TIERS, login_required, check_usage_limit
)

# Import intelligence modules
from offerwise_intelligence import OfferWiseIntelligence
from pdf_handler import PDFHandler

# Initialize Flask app
app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///offerwise.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# File upload configuration
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
ALLOWED_EXTENSIONS = {'pdf'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Initialize extensions
CORS(app)
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'serve_login'

# Initialize intelligence engine
intelligence = OfferWiseIntelligence()
pdf_handler = PDFHandler()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ============================================================================
# STATIC PAGE ROUTES
# ============================================================================

@app.route('/')
def serve_index():
    """Serve landing page"""
    if current_user.is_authenticated:
        return redirect(url_for('serve_dashboard'))
    return send_from_directory('static', 'index.html')

@app.route('/login')
def serve_login():
    """Serve login page"""
    if current_user.is_authenticated:
        return redirect(url_for('serve_dashboard'))
    return send_from_directory('static', 'login.html')

@app.route('/register')
def serve_register():
    """Serve registration page"""
    if current_user.is_authenticated:
        return redirect(url_for('serve_dashboard'))
    return send_from_directory('static', 'register.html')

@app.route('/dashboard')
@login_required
def serve_dashboard():
    """Serve user dashboard"""
    return send_from_directory('static', 'dashboard.html')

@app.route('/app')
@login_required
@check_usage_limit
def serve_app():
    """Serve main analysis app"""
    return send_from_directory('static', 'app.html')

@app.route('/pricing')
def serve_pricing():
    """Serve pricing page"""
    return send_from_directory('static', 'pricing.html')

# ============================================================================
# AUTHENTICATION API ROUTES
# ============================================================================

@app.route('/api/auth/register', methods=['POST'])
def register():
    """Register new user with email/password"""
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        name = data.get('name', '').strip()
        
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        
        # Check if user exists
        if User.query.filter_by(email=email).first():
            return jsonify({'error': 'Email already registered'}), 400
        
        # Create new user
        user = User(
            email=email,
            name=name,
            auth_provider='email',
            tier='free'
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        login_user(user)
        
        return jsonify({
            'success': True,
            'user': {
                'id': user.id,
                'email': user.email,
                'name': user.name,
                'tier': user.tier
            }
        })
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Login with email/password"""
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        
        user = User.query.filter_by(email=email).first()
        
        if not user or not user.check_password(password):
            return jsonify({'error': 'Invalid email or password'}), 401
        
        login_user(user, remember=True)
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'user': {
                'id': user.id,
                'email': user.email,
                'name': user.name,
                'tier': user.tier
            }
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/logout', methods=['POST'])
@login_required
def logout():
    """Logout current user"""
    logout_user()
    return jsonify({'success': True})

@app.route('/api/auth/google', methods=['POST'])
def google_login():
    """Login/Register with Google OAuth"""
    # TODO: Implement Google OAuth flow
    return jsonify({'error': 'Google OAuth not yet configured'}), 501

@app.route('/api/auth/magic-link', methods=['POST'])
def send_magic_link():
    """Send passwordless magic link"""
    # TODO: Implement magic link email
    return jsonify({'error': 'Magic link not yet configured'}), 501

# ============================================================================
# USER API ROUTES
# ============================================================================

@app.route('/api/user/me', methods=['GET'])
@login_required
def get_current_user():
    """Get current user info"""
    usage = current_user.get_current_usage()
    limits = current_user.get_tier_limits()
    
    return jsonify({
        'id': current_user.id,
        'email': current_user.email,
        'name': current_user.name,
        'tier': current_user.tier,
        'tier_info': PRICING_TIERS.get(current_user.tier, {}),
        'usage': {
            'properties_analyzed': usage.properties_analyzed,
            'limit': limits['properties_per_month'],
            'remaining': limits['properties_per_month'] - usage.properties_analyzed if limits['properties_per_month'] > 0 else -1
        }
    })

@app.route('/api/user/properties', methods=['GET'])
@login_required
def get_user_properties():
    """Get all properties for current user"""
    properties = Property.query.filter_by(user_id=current_user.id).order_by(Property.created_at.desc()).all()
    
    return jsonify({
        'properties': [{
            'id': p.id,
            'address': p.address,
            'price': p.price,
            'status': p.status,
            'created_at': p.created_at.isoformat(),
            'analyzed_at': p.analyzed_at.isoformat() if p.analyzed_at else None,
            'documents_count': p.documents.count(),
            'has_analysis': p.analyses.count() > 0
        } for p in properties]
    })

@app.route('/api/user/properties/<int:property_id>', methods=['GET'])
@login_required
def get_property(property_id):
    """Get specific property with analysis"""
    property = Property.query.filter_by(id=property_id, user_id=current_user.id).first()
    
    if not property:
        return jsonify({'error': 'Property not found'}), 404
    
    # Get latest analysis
    analysis = property.analyses.order_by(Analysis.created_at.desc()).first()
    
    return jsonify({
        'id': property.id,
        'address': property.address,
        'price': property.price,
        'status': property.status,
        'created_at': property.created_at.isoformat(),
        'analyzed_at': property.analyzed_at.isoformat() if property.analyzed_at else None,
        'documents': [{
            'id': d.id,
            'type': d.document_type,
            'filename': d.filename,
            'uploaded_at': d.uploaded_at.isoformat()
        } for d in property.documents],
        'analysis': json.loads(analysis.result_json) if analysis else None
    })

@app.route('/api/user/properties/<int:property_id>', methods=['DELETE'])
@login_required
def delete_property(property_id):
    """Delete a property and all associated data"""
    property = Property.query.filter_by(id=property_id, user_id=current_user.id).first()
    
    if not property:
        return jsonify({'error': 'Property not found'}), 404
    
    # Delete associated files
    for doc in property.documents:
        try:
            if os.path.exists(doc.file_path):
                os.remove(doc.file_path)
        except Exception as e:
            print(f"Error deleting file {doc.file_path}: {e}")
    
    db.session.delete(property)
    db.session.commit()
    
    return jsonify({'success': True})

# ============================================================================
# ANALYSIS API ROUTES
# ============================================================================

@app.route('/api/analyze', methods=['POST'])
@login_required
@check_usage_limit
def analyze_property():
    """Analyze property with uploaded documents"""
    try:
        # Get form data
        address = request.form.get('address')
        price = request.form.get('price')
        buyer_profile = request.form.get('buyer_profile')
        
        if not address:
            return jsonify({'error': 'Property address required'}), 400
        
        # Create property record
        property = Property(
            user_id=current_user.id,
            address=address,
            price=float(price) if price else None,
            status='pending'
        )
        db.session.add(property)
        db.session.flush()  # Get property ID
        
        # Handle file uploads
        seller_disclosure_file = request.files.get('seller_disclosure')
        inspection_report_file = request.files.get('inspection_report')
        
        documents = {}
        
        if seller_disclosure_file and allowed_file(seller_disclosure_file.filename):
            filename = secure_filename(f"{property.id}_seller_disclosure_{seller_disclosure_file.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            seller_disclosure_file.save(filepath)
            
            # Extract text
            extracted_text = pdf_handler.extract_text(filepath)
            
            doc = Document(
                property_id=property.id,
                document_type='seller_disclosure',
                filename=seller_disclosure_file.filename,
                file_path=filepath,
                file_size_bytes=os.path.getsize(filepath),
                extracted_text=extracted_text
            )
            db.session.add(doc)
            documents['seller_disclosure'] = extracted_text
        
        if inspection_report_file and allowed_file(inspection_report_file.filename):
            filename = secure_filename(f"{property.id}_inspection_report_{inspection_report_file.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            inspection_report_file.save(filepath)
            
            # Extract text
            extracted_text = pdf_handler.extract_text(filepath)
            
            doc = Document(
                property_id=property.id,
                document_type='inspection_report',
                filename=inspection_report_file.filename,
                file_path=filepath,
                file_size_bytes=os.path.getsize(filepath),
                extracted_text=extracted_text
            )
            db.session.add(doc)
            documents['inspection_report'] = extracted_text
        
        if not documents:
            return jsonify({'error': 'At least one document required'}), 400
        
        # Parse buyer profile
        buyer_data = json.loads(buyer_profile) if buyer_profile else {}
        
        # Run analysis
        result = intelligence.analyze(
            seller_disclosure=documents.get('seller_disclosure'),
            inspection_report=documents.get('inspection_report'),
            property_address=address,
            property_price=float(price) if price else None,
            buyer_profile=buyer_data
        )
        
        # Store analysis
        analysis = Analysis(
            property_id=property.id,
            result_json=json.dumps(result),
            buyer_profile_json=buyer_profile
        )
        db.session.add(analysis)
        
        # Update property status
        property.status = 'completed'
        property.analyzed_at = datetime.utcnow()
        
        # Increment usage
        current_user.increment_usage()
        
        db.session.commit()
        
        return jsonify(result)
    
    except Exception as e:
        db.session.rollback()
        print(f"Analysis error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ============================================================================
# HELPER FUNCTIONS (from original app.py)
# ============================================================================

def _format_offer_strategy(strategy_dict):
    """Format offer strategy for display"""
    if not isinstance(strategy_dict, dict):
        return str(strategy_dict)
    
    parts = []
    if 'recommended_offer' in strategy_dict:
        parts.append(f"Recommended Offer: ${strategy_dict['recommended_offer']:,}")
    if 'contingencies' in strategy_dict and isinstance(strategy_dict['contingencies'], dict):
        parts.append("\nContingencies:")
        for key, value in strategy_dict['contingencies'].items():
            parts.append(f"  • {key.replace('_', ' ').title()}: {value}")
    if 'scenarios' in strategy_dict and isinstance(strategy_dict['scenarios'], list):
        parts.append("\nScenarios:")
        for scenario in strategy_dict['scenarios'][:3]:
            if isinstance(scenario, dict):
                parts.append(f"  • {scenario.get('scenario', 'Scenario')}: ${scenario.get('offer', 'N/A')}")
    
    return '\n'.join(parts)

def _format_negotiation_strategy(strategy_dict):
    """Format negotiation strategy for display"""
    if not isinstance(strategy_dict, dict):
        return str(strategy_dict)
    
    parts = []
    if 'posture' in strategy_dict:
        parts.append(f"Posture: {strategy_dict['posture']}")
    if 'key_leverage_points' in strategy_dict:
        parts.append("\nLeverage Points:")
        points = strategy_dict['key_leverage_points']
        if isinstance(points, list):
            for point in points[:5]:
                parts.append(f"  • {point}")
    if 'suggested_approach' in strategy_dict:
        parts.append(f"\nApproach: {strategy_dict['suggested_approach']}")
    
    return '\n'.join(parts)

def _format_decision_framework(framework_dict):
    """Format decision framework for display"""
    if not isinstance(framework_dict, dict):
        return str(framework_dict)
    
    parts = []
    if 'recommendation' in framework_dict:
        parts.append(framework_dict['recommendation'])
    if 'key_decision_points' in framework_dict:
        points = framework_dict['key_decision_points']
        if isinstance(points, list):
            for point in points[:3]:
                parts.append(f"• {point}")
    
    return '\n'.join(parts)

# ============================================================================
# ADMIN ROUTES
# ============================================================================

@app.route('/api/admin/init-db', methods=['POST'])
def init_database():
    """Initialize database (development only)"""
    try:
        db.create_all()
        return jsonify({'success': True, 'message': 'Database initialized'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=True)
