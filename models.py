"""
OfferWise Database Models
SQLite database with user authentication, property storage, and usage tracking
"""

from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

db = SQLAlchemy()

class User(UserMixin, db.Model):
    """User accounts with authentication"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255))
    password_hash = db.Column(db.String(255))  # For email/password auth
    
    # OAuth fields
    google_id = db.Column(db.String(255), unique=True, index=True)
    apple_id = db.Column(db.String(255), unique=True, index=True)
    facebook_id = db.Column(db.String(255), unique=True, index=True)
    github_id = db.Column(db.String(255), unique=True, index=True)
    auth_provider = db.Column(db.String(50))  # 'google', 'apple', 'facebook', 'github'
    
    # Subscription
    tier = db.Column(db.String(50), default='free')  # 'free', 'starter', 'professional', 'enterprise'
    stripe_customer_id = db.Column(db.String(255))
    stripe_subscription_id = db.Column(db.String(255))
    subscription_status = db.Column(db.String(50), default='active')  # 'active', 'cancelled', 'past_due'
    subscription_end_date = db.Column(db.DateTime)
    analysis_credits = db.Column(db.Integer, default=0)  # Pay-per-use credits
    
    # Buyer Preferences
    max_budget = db.Column(db.Integer, nullable=True)
    repair_tolerance = db.Column(db.String(50), nullable=True)  # 'Low', 'Moderate', 'High'
    biggest_regret = db.Column(db.Text, nullable=True)
    
    # Onboarding tracking - once completed, never redirect to onboarding again
    onboarding_completed = db.Column(db.Boolean, default=False)
    onboarding_completed_at = db.Column(db.DateTime)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    terms_accepted_at = db.Column(db.DateTime)  # When user accepted Terms of Service
    terms_version = db.Column(db.String(20), default='1.0')  # Track which version they accepted
    
    # Relationships
    properties = db.relationship('Property', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    usage_records = db.relationship('UsageRecord', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    consent_records = db.relationship('ConsentRecord', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    
    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Verify password"""
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)
    
    def get_tier_limits(self):
        """Get limits for current tier"""
        limits = {
            'free': {'properties_per_month': 3, 'storage_mb': 50},
            'starter': {'properties_per_month': 10, 'storage_mb': 200},
            'professional': {'properties_per_month': 50, 'storage_mb': 1000},
            'enterprise': {'properties_per_month': -1, 'storage_mb': -1}  # -1 = unlimited
        }
        return limits.get(self.tier, limits['free'])
    
    def get_current_usage(self):
        """Get current month's usage"""
        now = datetime.utcnow()
        start_of_month = datetime(now.year, now.month, 1)
        
        usage = UsageRecord.query.filter(
            UsageRecord.user_id == self.id,
            UsageRecord.month_start >= start_of_month
        ).first()
        
        if not usage:
            # Create new usage record for this month
            usage = UsageRecord(
                user_id=self.id,
                month_start=start_of_month,
                properties_analyzed=0
            )
            db.session.add(usage)
            db.session.commit()
        
        return usage
    
    def can_analyze_property(self):
        """Check if user can analyze another property"""
        limits = self.get_tier_limits()
        max_properties = limits['properties_per_month']
        
        if max_properties == -1:  # Unlimited
            return True
        
        usage = self.get_current_usage()
        return usage.properties_analyzed < max_properties
    
    def increment_usage(self):
        """Increment property analysis count"""
        usage = self.get_current_usage()
        usage.properties_analyzed += 1
        usage.last_analysis = datetime.utcnow()
        db.session.commit()


class Property(db.Model):
    """Properties analyzed by users"""
    __tablename__ = 'properties'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Property details
    address = db.Column(db.String(500), nullable=False)
    price = db.Column(db.Float)
    
    # Analysis status
    status = db.Column(db.String(50), default='pending')  # 'pending', 'completed', 'failed'
    analyzed_at = db.Column(db.DateTime)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    documents = db.relationship('Document', backref='property', lazy='dynamic', cascade='all, delete-orphan')
    analyses = db.relationship('Analysis', backref='property', lazy='dynamic', cascade='all, delete-orphan')


class Document(db.Model):
    """Uploaded documents (PDFs) for properties"""
    __tablename__ = 'documents'
    
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False, index=True)
    
    # Document details
    document_type = db.Column(db.String(50), nullable=False)  # 'seller_disclosure', 'inspection_report'
    filename = db.Column(db.String(500), nullable=False)
    file_path = db.Column(db.String(1000), nullable=False)
    file_size_bytes = db.Column(db.Integer)
    
    # Extracted content
    extracted_text = db.Column(db.Text)
    
    # Timestamps
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


class Analysis(db.Model):
    """Analysis results for properties"""
    __tablename__ = 'analyses'
    
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False, index=True)
    
    # Analysis results (stored as JSON)
    result_json = db.Column(db.Text, nullable=False)
    
    # Buyer profile used
    buyer_profile_json = db.Column(db.Text)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class UsageRecord(db.Model):
    """Track monthly usage per user"""
    __tablename__ = 'usage_records'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Usage tracking
    month_start = db.Column(db.DateTime, nullable=False, index=True)
    properties_analyzed = db.Column(db.Integer, default=0)
    last_analysis = db.Column(db.DateTime)
    
    # Unique constraint: one record per user per month
    __table_args__ = (
        db.UniqueConstraint('user_id', 'month_start', name='unique_user_month'),
    )


class MagicLink(db.Model):
    """Passwordless magic link tokens"""
    __tablename__ = 'magic_links'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    token = db.Column(db.String(255), unique=True, nullable=False, index=True)
    
    # Expiration
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    @staticmethod
    def create_link(email, expires_in_minutes=15):
        """Create a new magic link"""
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(minutes=expires_in_minutes)
        
        link = MagicLink(
            email=email,
            token=token,
            expires_at=expires_at
        )
        db.session.add(link)
        db.session.commit()
        
        return link
    
    def is_valid(self):
        """Check if link is still valid"""
        return not self.used and datetime.utcnow() < self.expires_at
    
    def mark_used(self):
        """Mark link as used"""
        self.used = True
        db.session.commit()


class ConsentRecord(db.Model):
    """
    Track user consent for legal disclaimers and terms.
    
    CRITICAL for legal protection - tracks:
    - What disclaimer/terms they consented to
    - Which version
    - When they consented
    - IP address for audit trail
    """
    __tablename__ = 'consent_records'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Consent details
    consent_type = db.Column(db.String(50), nullable=False)  # 'terms', 'privacy', 'disclaimer', 'analysis_disclaimer'
    consent_version = db.Column(db.String(20), nullable=False)  # Version of the text they consented to
    consent_text_hash = db.Column(db.String(64))  # SHA-256 hash of the exact text shown
    
    # Audit trail
    consented_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ip_address = db.Column(db.String(45))  # IPv4 or IPv6
    user_agent = db.Column(db.String(500))  # Browser/device info
    
    # Context
    analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id'))  # If consent was for specific analysis
    
    # Status
    revoked = db.Column(db.Boolean, default=False)
    revoked_at = db.Column(db.DateTime)
    
    def __repr__(self):
        return f'<ConsentRecord user={self.user_id} type={self.consent_type} version={self.consent_version}>'
    
    @staticmethod
    def has_current_consent(user_id, consent_type, required_version):
        """
        Check if user has valid consent for a specific type and version.
        
        Args:
            user_id: User ID
            consent_type: Type of consent ('disclaimer', 'terms', etc.)
            required_version: Minimum version required
            
        Returns:
            True if user has valid consent, False otherwise
        """
        consent = ConsentRecord.query.filter_by(
            user_id=user_id,
            consent_type=consent_type,
            revoked=False
        ).order_by(ConsentRecord.consented_at.desc()).first()
        
        if not consent:
            return False
        
        # Check if version is current
        return consent.consent_version >= required_version
    
    @staticmethod
    def record_consent(user_id, consent_type, consent_version, consent_text, ip_address=None, user_agent=None, analysis_id=None):
        """
        Record a new consent.
        
        Returns:
            ConsentRecord object
        """
        import hashlib
        
        # Hash the consent text for verification
        text_hash = hashlib.sha256(consent_text.encode('utf-8')).hexdigest()
        
        consent = ConsentRecord(
            user_id=user_id,
            consent_type=consent_type,
            consent_version=consent_version,
            consent_text_hash=text_hash,
            ip_address=ip_address,
            user_agent=user_agent,
            analysis_id=analysis_id
        )
        
        db.session.add(consent)
        db.session.commit()
        
        return consent
